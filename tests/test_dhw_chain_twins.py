"""Every recorded twin's hot-water tank closes on every step, and both ends of its charging circuits agree.

The hot-water tank (``SimpleDHWStorage``) is a fully mixed node (hydronic coupling spec §4): on every step the heat its
charging circuits bring, minus the heat its tap draws and its standby loss, is the heat it stores,
``C (T_end - T0)``. It publishes its step mean ``T̄`` as the return temperature of each charging circuit, so the heat a
generator books for its hot-water circuit, ``m c (T_sup - T̄)``, is the heat the tank receives.

Each energy-system file with a hot-water tank is simulated at 900 s, a winter day, or the first twenty days of January
for a file with a solar collector (whose pump first runs on 20 January), and three things are checked:

* the tank's balance closes on every step, to a millionth of the step's largest term;
* on every step, the hot-water heat the boiler, heat pump, district-heating substation, electric heater or solar
  collector books equals the heat the tank received from that circuit, up to what the simulator's convergence
  tolerance leaves open (the tank's step mean is converged to about 1e-4 K, so the two ends may differ by ``m c``
  times that), and every circuit charges on some step;
* the iterations per step (hydronic coupling spec §6): no step of a file without a heat pump reaches the simulator's
  ``force_convergence`` (more than eleven passes). The heat-pump files are recorded, not asserted: their forced
  steps come from the energy manager, which switches its set-temperature raise on the sign of a surplus that
  includes the heat pump's own draw, so a float's flip turns it on and off within a step, and from controllers
  that decide once for a whole step, which a converged iteration cannot always reconcile with the plant's
  state; stage D resolves both. Every file's histogram is written to ``results/iteration_histogram/``.

Every run test simulates a whole energy system and so runs in the ``extendedbase2`` shard: the recorded twins, their
grouped twins, which RenoVisor translates from, and the composed files. Only the check that every file with a tank is
listed stays in ``base``. Every file except the car twin replaces its load-profile generator by the shipped
predefined profile, so no run needs the LoadProfileGenerator; the car twin needs the generator's driving profile and
reads the cached one.
"""

import json
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import pytest
import yaml

from hisim import hydronics
from hisim.components.generic_boiler import GenericBoiler
from hisim.components.generic_district_heating import DistrictHeating
from hisim.components.generic_electric_heating import ElectricHeating
from hisim.components.more_advanced_heat_pump_hplib import MoreAdvancedHeatPumpHPLib
from hisim.components.simple_water_storage import SimpleDHWStorage
from hisim.components.solar_thermal_system import SolarThermalSystem
from hisim.energy_system.executor import build_energy_system, SimulationParametersReader
from hisim.calculation_scope import CalculationScope


class DhwTwins:
    """The energy-system files with a hot-water tank, and how one winter day of each is run and read."""

    #: The repository's root.
    ROOT = Path(__file__).resolve().parents[1]

    #: The recorded twins, one per setup with a hot-water tank (run in ``extendedbase2``).
    TWINS: Tuple[str, ...] = (
        "automatic_default_connections",
        "household_district_heating_building_sizer",
        "household_electric_heating_building_sizer",
        "household_gas_building_sizer",
        "household_gas_solar_thermal",
        "household_gas_solar_thermal_building_sizer",
        "household_heatpump_building_sizer",
        "household_heatpump_car_building_sizer",
        "household_heatpump_solar_thermal_building_sizer",
        "household_hydrogen_boiler_building_sizer",
        "household_oil_building_sizer",
        "household_pellets_building_sizer",
        "household_wood_chips_building_sizer",
    )

    #: The kinds of derived file run beside the recorded twins: the grouped twins and the composed files.
    DERIVED_KINDS: Tuple[str, ...] = ("grouped", "composed")

    #: The car twin reads the generator's driving profile, which a predefined profile does not carry.
    NEEDS_THE_LOAD_PROFILE_GENERATOR: Tuple[str, ...] = ("household_heatpump_car_building_sizer",)

    #: A January day of 2021 at 900 s, the day the heat-pump flow-booking test runs as well.
    WINTER_DAY: Tuple[str, str] = ("2021-01-10T00:00:00", "2021-01-11T00:00:00")

    #: The step length of the runs, s.
    SECONDS_PER_TIMESTEP: int = 900

    #: Per generator class, the output of its booked hot-water heat flow (W).
    BOOKED_DHW_POWER: Dict[str, str] = {
        GenericBoiler.get_classname(): GenericBoiler.ThermalOutputPowerDhw,
        MoreAdvancedHeatPumpHPLib.get_classname(): MoreAdvancedHeatPumpHPLib.ThermalOutputPowerDHW,
        DistrictHeating.get_classname(): DistrictHeating.ThermalOutputDhwPower,
        ElectricHeating.get_classname(): ElectricHeating.ThermalOutputDhwPower,
        SolarThermalSystem.get_classname(): SolarThermalSystem.ThermalPowerOutput,
    }

    #: Per generator class, the output of its hot-water circuit's mass flow (kg/s).
    DHW_MASS_FLOW: Dict[str, str] = {
        GenericBoiler.get_classname(): GenericBoiler.WaterOutputMassFlowDhw,
        MoreAdvancedHeatPumpHPLib.get_classname(): MoreAdvancedHeatPumpHPLib.MassFlowOutputDHW,
        DistrictHeating.get_classname(): DistrictHeating.WaterOutputDhwMassFlowRate,
        ElectricHeating.get_classname(): ElectricHeating.WaterOutputDhwMassFlowRate,
        SolarThermalSystem.get_classname(): SolarThermalSystem.WaterMassFlowOutput,
    }

    #: The first twenty days of January, for a file with a solar collector: its pump first runs on 20 January.
    SOLAR_WINDOW: Tuple[str, str] = ("2021-01-01T00:00:00", "2021-01-21T00:00:00")

    #: The tank converges its step mean to about the simulator's tolerance, 1e-4 K; the two ends of a circuit may
    #: differ by the circuit's ``m c`` times this.
    CIRCUIT_TEMPERATURE_TOLERANCE_IN_KELVIN: float = 1e-3

    @classmethod
    def derived_files(cls) -> List[str]:
        """The grouped twins and composed files of the recorded twins that exist."""
        return [
            f"{stem}.{kind}"
            for stem in cls.TWINS
            for kind in cls.DERIVED_KINDS
            if (cls.ROOT / "energy_systems" / f"{stem}.{kind}.energy_system.yaml").exists()
        ]

    @classmethod
    @lru_cache(maxsize=None)
    def run(cls, name: str, directory: str) -> Tuple[pd.DataFrame, Tuple[int, ...], Tuple[bool, ...], Dict[str, str]]:
        """Simulate ``energy_systems/<name>.energy_system.yaml`` at 900 s over its window (:meth:`window`).

        Returns:
            The result frame, the passes and the ``force_convergence`` flag of every step, and per charging circuit
            of the tank (``"primary"``, ``"secondary"``) the ``"<component> - <output>"`` column prefix of the
            generator's booked hot-water heat flow, for the circuits with a flow source.
        """
        source = cls.ROOT / "energy_systems" / f"{name}.energy_system.yaml"
        model = yaml.safe_load(source.read_text(encoding="utf-8"))
        if name.split(".")[0] not in cls.NEEDS_THE_LOAD_PROFILE_GENERATOR:
            for entry in model["components"].values():
                if str(entry.get("class", "")).endswith("UtspLpgConnector"):
                    entry.setdefault("config", {})["data_acquisition_mode"] = "USE_PREDEFINED_PROFILE"
        work = Path(directory)
        energy_system = work / f"{name.replace('.', '_')}.energy_system.yaml"
        energy_system.write_text(yaml.safe_dump(model, sort_keys=False), encoding="utf-8")
        parameters_path = work / "winter_day.simulation.yaml"
        start, end = cls.window(name)
        parameters_path.write_text(
            yaml.safe_dump(
                {
                    "start_date": start,
                    "end_date": end,
                    "seconds_per_timestep": cls.SECONDS_PER_TIMESTEP,
                    "country": "DE",
                    "logging_level": 3,
                    "post_processing_options": [],
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        results_directory = work / "results"
        with CalculationScope.open(label=str(energy_system), run_directory=str(results_directory)):
            parameters = SimulationParametersReader.read(parameters_path)
            parameters.result_directory = str(results_directory)
            built = build_energy_system(energy_system, parameters, assembly_resolver=None)
            simulator = built.simulator
            passes: List[int] = []
            forced: List[bool] = []
            process_one_timestep = simulator.process_one_timestep

            def counting(step, stsv):  # the iteration count the simulator returns per step (§6)
                result = process_one_timestep(step, stsv)
                passes.append(result[1])
                forced.append(bool(result[2]))
                return result

            setattr(simulator, "process_one_timestep", counting)
            # read before the run: the simulator releases its components when it has finished
            circuits = cls.circuit_generators(simulator)
            simulator.run_all_timesteps()
            results = simulator.results_data_frame
        return results, tuple(passes), tuple(forced), circuits

    @classmethod
    def window(cls, name: str) -> Tuple[str, str]:
        """The simulated window: :attr:`SOLAR_WINDOW` for a file with a solar collector, :attr:`WINTER_DAY` else."""
        text = (cls.ROOT / "energy_systems" / f"{name}.energy_system.yaml").read_text(encoding="utf-8")
        has_collector = SolarThermalSystem.get_full_classname() in text or "solar_thermal" in name
        return cls.SOLAR_WINDOW if has_collector else cls.WINTER_DAY

    @classmethod
    def circuit_generators(cls, simulator) -> Dict[str, str]:
        """Per charging circuit of the tank, the column prefix of its generator's booked hot-water heat flow."""
        (tank,) = [
            wrapped.my_component
            for wrapped in simulator.wrapped_components
            if isinstance(wrapped.my_component, SimpleDHWStorage)
        ]
        circuits = {}
        for circuit, channel in (
            ("primary", tank.water_mass_flow_rate_heat_generator_input_channel),
            ("secondary", tank.water_mass_flow_rate_secondary_heat_generator_input_channel),
        ):
            if channel.src_object_name is None:
                continue
            generator = [
                wrapped.my_component
                for wrapped in simulator.wrapped_components
                if wrapped.my_component.component_name == channel.src_object_name
            ][0]
            circuits[circuit] = f"{channel.src_object_name}|{type(generator).get_classname()}"
        return circuits

    @staticmethod
    def column(results: pd.DataFrame, component: str, field: str) -> np.ndarray:
        """One output's values, by component name and field name."""
        matches = [name for name in results.columns if name.startswith(f"{component} - {field} [")]
        assert len(matches) == 1, (component, field, matches)
        values: np.ndarray = results[matches[0]].to_numpy(dtype=float)
        return values

    @classmethod
    def tank_name(cls, results: pd.DataFrame) -> str:
        """The tank's component name: the one component with a ``ThermalEnergyUnmetDHWInWattHour`` output."""
        marker = f" - {SimpleDHWStorage.ThermalEnergyUnmetDHWInWattHour} ["
        owners = [name.split(" - ")[0] for name in results.columns if marker in name]
        assert len(owners) == 1, owners
        return str(owners[0])


def every_file_with_a_tank_is_listed() -> None:
    """Fail when an energy-system file builds a hot-water tank and is neither a listed twin nor derived from one."""
    listed = set(DhwTwins.TWINS) | set(DhwTwins.derived_files())
    found = set()
    for path in sorted((DhwTwins.ROOT / "energy_systems").glob("*.energy_system.yaml")):
        text = path.read_text(encoding="utf-8")
        if SimpleDHWStorage.get_full_classname() in text or "dhw/indirect_cylinder" in text:
            found.add(path.name[: -len(".energy_system.yaml")])
    assert found <= listed, sorted(found - listed)


@pytest.mark.base
def test_every_energy_system_file_with_a_tank_is_checked() -> None:
    """A new file with a hot-water tank cannot escape the per-step checks."""
    every_file_with_a_tank_is_listed()


def check_tank_closure(results: pd.DataFrame) -> None:
    """The tank's balance closes on every step to a millionth of the step's largest term."""
    tank = DhwTwins.tank_name(results)
    seconds_per_timestep = DhwTwins.SECONDS_PER_TIMESTEP
    primary_in_watt_hour = DhwTwins.column(results, tank, SimpleDHWStorage.ThermalEnergyFromHeatGenerator)
    secondary_in_watt_hour = DhwTwins.column(results, tank, SimpleDHWStorage.ThermalEnergyFromSecondaryHeatGenerator)
    tap_in_watt_hour = DhwTwins.column(results, tank, SimpleDHWStorage.ThermalEnergyConsumptionDHW)
    loss_in_watt_hour = DhwTwins.column(results, tank, SimpleDHWStorage.StandbyHeatLoss) * seconds_per_timestep / 3600.0
    stored_in_watt_hour = DhwTwins.column(results, tank, SimpleDHWStorage.ThermalEnergyIncreaseInStorage)
    residual_in_watt_hour = (
        primary_in_watt_hour + secondary_in_watt_hour + tap_in_watt_hour - loss_in_watt_hour - stored_in_watt_hour
    )
    largest_in_watt_hour = np.maximum.reduce(
        [
            abs(primary_in_watt_hour),
            abs(secondary_in_watt_hour),
            abs(tap_in_watt_hour),
            abs(loss_in_watt_hour),
            abs(stored_in_watt_hour),
        ]
    )
    assert np.all(np.abs(residual_in_watt_hour) <= 1e-6 * largest_in_watt_hour + 1e-9), float(
        np.max(np.abs(residual_in_watt_hour))
    )
    # the tap drew on some step of the day, so the check covered the valve
    assert np.any(tap_in_watt_hour < 0.0)


def check_circuits(results: pd.DataFrame, circuits: Dict[str, str]) -> int:
    """Every charging circuit: the generator books the heat the tank received; returns how many were checked."""
    tank = DhwTwins.tank_name(results)
    received_field = {
        "primary": SimpleDHWStorage.ThermalPowerFromHeatGenerator,
        "secondary": SimpleDHWStorage.ThermalPowerFromSecondaryHeatGenerator,
    }
    checked = 0
    for circuit, owner in circuits.items():
        component, classname = owner.split("|")
        booked_in_watt = DhwTwins.column(results, component, DhwTwins.BOOKED_DHW_POWER[classname])
        received_in_watt = DhwTwins.column(results, tank, received_field[circuit])
        mass_flow_in_kg_per_second = DhwTwins.column(results, component, DhwTwins.DHW_MASS_FLOW[classname])
        tolerance_in_watt = (
            mass_flow_in_kg_per_second
            * hydronics.WATER_SPECIFIC_HEAT_J_PER_KG_K
            * DhwTwins.CIRCUIT_TEMPERATURE_TOLERANCE_IN_KELVIN
        )
        difference_in_watt = np.abs(booked_in_watt - received_in_watt)
        assert np.all(difference_in_watt <= tolerance_in_watt + 1e-6), (circuit, float(np.max(difference_in_watt)))
        assert np.any(booked_in_watt > 0.0), f"the {circuit} circuit never charged in the window"
        checked += 1
    return checked


def record_histogram(name: str, passes: Tuple[int, ...], forced: Tuple[bool, ...]) -> Dict[str, object]:
    """Write the file's iterations-per-step histogram to ``results/iteration_histogram/<name>.json``."""
    summary: Dict[str, object] = {
        "file": f"energy_systems/{name}.energy_system.yaml",
        "seconds_per_timestep": DhwTwins.SECONDS_PER_TIMESTEP,
        "period": list(DhwTwins.window(name)),
        "steps": len(passes),
        "mean_passes": float(np.mean(passes)),
        "max_passes": int(max(passes)),
        "steps_at_force_convergence": int(sum(forced)),
        "histogram": {str(count): number for count, number in sorted(Counter(passes).items())},
    }
    directory = DhwTwins.ROOT / "results" / "iteration_histogram"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', name)}.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    return summary


def check_file(name: str, tmp_path_factory: pytest.TempPathFactory) -> None:
    """Run one file and apply the three checks."""
    results, passes, forced, circuits = DhwTwins.run(name, str(tmp_path_factory.mktemp(name.replace(".", "_"))))
    check_tank_closure(results)
    assert check_circuits(results, circuits) >= 1, circuits
    classnames = {owner.split("|")[1] for owner in circuits.values()}
    summary = record_histogram(name, passes, forced)
    if MoreAdvancedHeatPumpHPLib.get_classname() not in classnames:
        assert summary["steps_at_force_convergence"] == 0, summary


@pytest.mark.extendedbase2
@pytest.mark.parametrize("name", DhwTwins.TWINS)
def test_the_recorded_twins_close_their_tank_and_agree_on_every_circuit(
    name: str, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Every recorded twin at 900 s: closure, circuit agreement and the iteration histogram."""
    check_file(name, tmp_path_factory)


@pytest.mark.extendedbase2
@pytest.mark.parametrize("name", DhwTwins.derived_files())
def test_the_grouped_twins_and_composed_files_close_their_tank_and_agree_on_every_circuit(
    name: str, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """The same checks for the grouped twins RenoVisor translates from and for the composed files."""
    check_file(name, tmp_path_factory)
