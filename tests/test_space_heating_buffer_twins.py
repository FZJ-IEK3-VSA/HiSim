"""Every energy-system file with a space-heating buffer closes it on every step, and both ends of its circuits agree.

The buffer (``SimpleHotWaterStorage``) is a start-of-step node: it publishes the temperature it starts the step at as
the return to its generators and as the supply to the heat distribution system, every circuit's heat is ``m c`` times
the difference between the entering stream and that temperature, and the buffer ends the step at its start
temperature plus the net heat over its heat capacity. So on every step:

* the buffer's balance closes: the generators' heat, less the heat the distribution takes, less the standby loss,
  is the change of its stored heat, to a millionth of the step's largest term;
* the space heat the boiler or heat pump books is the heat the buffer received from it;
* the heat the distribution system delivers to the building is the heat the buffer gave to it.

Each file is simulated over a January day at 900 s. Every run simulates a whole energy system and so runs in the
``extendedbase2`` shard; only the check that every file with a buffer is listed stays in ``base``. Every file except
the car twin replaces its load-profile generator by the shipped predefined profile.
"""

from pathlib import Path
from typing import ClassVar, Dict, List, Tuple

import numpy as np
import pandas as pd
import pytest
import yaml

from hisim import hydronics
from hisim.calculation_scope import CalculationScope
from hisim.components.building.building import Building
from hisim.components.generic_boiler import GenericBoiler
from hisim.components.heat_distribution_system import HeatDistribution
from hisim.components.more_advanced_heat_pump_hplib import MoreAdvancedHeatPumpHPLib
from hisim.components.simple_water_storage import SimpleHotWaterStorage
from hisim.energy_system.executor import SimulationParametersReader, build_energy_system


class BufferTwins:
    """The energy-system files with a space-heating buffer, and how one winter day of each is run and read."""

    #: The repository's root.
    ROOT: ClassVar[Path] = Path(__file__).resolve().parents[1]

    #: The recorded twins with a buffer.
    TWINS: ClassVar[Tuple[str, ...]] = (
        "automatic_default_connections",
        "basic_household_only_heating",
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
    DERIVED_KINDS: ClassVar[Tuple[str, ...]] = ("grouped", "composed")

    #: The car twin reads the generator's driving profile, which a predefined profile does not carry.
    NEEDS_THE_LOAD_PROFILE_GENERATOR: ClassVar[Tuple[str, ...]] = ("household_heatpump_car_building_sizer",)

    #: A January day of 2021, on which every generator heats its buffer.
    WINTER_DAY: ClassVar[Tuple[str, str]] = ("2021-01-10T00:00:00", "2021-01-11T00:00:00")

    #: The step length of the runs, in s.
    SECONDS_PER_TIMESTEP: ClassVar[int] = 900

    #: Seconds per hour, to turn a power of the step into its energy in Wh.
    SECONDS_PER_HOUR: ClassVar[float] = 3600.0

    #: The simulator converges every output to 1e-4 between passes, so the two ends of a circuit, read in different
    #: passes, may differ by the circuit's ``m c`` times about this temperature.
    CIRCUIT_TEMPERATURE_TOLERANCE_IN_KELVIN: ClassVar[float] = 1e-3

    #: Per generator class, the output of its space-heating circuit's mass flow, in kg/s.
    SPACE_HEATING_MASS_FLOW: ClassVar[Dict[str, str]] = {
        GenericBoiler.get_classname(): GenericBoiler.WaterOutputMassFlowSh,
        MoreAdvancedHeatPumpHPLib.get_classname(): MoreAdvancedHeatPumpHPLib.MassFlowOutputSH,
    }

    #: How far below the room's start-of-step temperature a heated buffer may end, in K: the convergence tolerance.
    FLOOR_TOLERANCE_IN_KELVIN: ClassVar[float] = 1e-3

    #: Per generator class, the output of the space heat it books, in W.
    BOOKED_SPACE_HEATING_POWER: ClassVar[Dict[str, str]] = {
        GenericBoiler.get_classname(): GenericBoiler.ThermalPowerGenerationSh,
        MoreAdvancedHeatPumpHPLib.get_classname(): MoreAdvancedHeatPumpHPLib.ThermalOutputPowerSH,
    }

    @classmethod
    def derived_files(cls) -> List[str]:
        """Return the grouped twins and composed files of the recorded twins that exist."""
        return [
            f"{stem}.{kind}"
            for stem in cls.TWINS
            for kind in cls.DERIVED_KINDS
            if (cls.ROOT / "energy_systems" / f"{stem}.{kind}.energy_system.yaml").exists()
        ]

    @classmethod
    def run(cls, name: str, directory: str) -> Tuple[pd.DataFrame, str, str, str, str, str]:
        """Simulate ``energy_systems/<name>.energy_system.yaml`` over :attr:`WINTER_DAY` at 900 s.

        Returns:
            The result frame, the buffer's component name, the column prefixes ``"<component> - <field>"`` of the space
            heat its primary generator books and of that generator's space-heating mass flow, and the heat
            distribution system's and the building's component names.
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
        start, end = cls.WINTER_DAY
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
            simulator = build_energy_system(energy_system, parameters, assembly_resolver=None).simulator
            # read before the run: the simulator releases its components when it has finished
            components = [wrapped.my_component for wrapped in simulator.wrapped_components]
            (buffer,) = [component for component in components if isinstance(component, SimpleHotWaterStorage)]
            (distribution,) = [component for component in components if isinstance(component, HeatDistribution)]
            generator_name = buffer.water_mass_flow_rate_heat_generator_input_channel.src_object_name
            (generator,) = [component for component in components if component.component_name == generator_name]
            classname = type(generator).get_classname()
            booked_prefix = f"{generator_name} - {cls.BOOKED_SPACE_HEATING_POWER[classname]}"
            flow_prefix = f"{generator_name} - {cls.SPACE_HEATING_MASS_FLOW[classname]}"
            buffer_name = buffer.component_name
            (building,) = [component for component in components if isinstance(component, Building)]
            building_name = building.component_name
            distribution_name = distribution.component_name
            simulator.run_all_timesteps()
            results = simulator.results_data_frame
        return results, buffer_name, booked_prefix, flow_prefix, distribution_name, building_name

    @classmethod
    def circuit_tolerance_in_watt(cls, mass_flow_in_kg_per_second: np.ndarray) -> np.ndarray:
        """Return per step how far the two ends of a circuit may differ, in W: ``m c`` times the tolerance, plus 1 µW.

        Args:
            mass_flow_in_kg_per_second: The circuit's mass flow per step, in kg/s.

        Returns:
            The allowed difference per step, in W.
        """
        return (
            mass_flow_in_kg_per_second
            * hydronics.Water.SPECIFIC_HEAT_J_PER_KG_K
            * cls.CIRCUIT_TEMPERATURE_TOLERANCE_IN_KELVIN
            + 1e-6
        )

    @staticmethod
    def column(results: pd.DataFrame, prefix: str) -> np.ndarray:
        """Return the one column whose name starts with ``"<prefix> ["``."""
        matches = [name for name in results.columns if name.startswith(f"{prefix} [")]
        assert len(matches) == 1, (prefix, matches)
        values: np.ndarray = results[matches[0]].to_numpy(dtype=float)
        return values


def check_file(name: str, tmp_path_factory: pytest.TempPathFactory) -> None:
    """Run one file and check the buffer's closure and both of its circuits on every step."""
    results, buffer, booked_prefix, flow_prefix, distribution, building = BufferTwins.run(
        name, str(tmp_path_factory.mktemp(name.replace(".", "_")))
    )
    hours_per_step = BufferTwins.SECONDS_PER_TIMESTEP / BufferTwins.SECONDS_PER_HOUR
    primary_in_watt_hour = BufferTwins.column(
        results, f"{buffer} - {SimpleHotWaterStorage.ThermalEnergyFromHeatGenerator}"
    )
    secondary_in_watt_hour = BufferTwins.column(
        results, f"{buffer} - {SimpleHotWaterStorage.ThermalEnergyFromSecondaryHeatGenerator}"
    )
    distribution_in_watt_hour = BufferTwins.column(
        results, f"{buffer} - {SimpleHotWaterStorage.ThermalEnergyFromHeatDistribution}"
    )
    loss_in_watt_hour = (
        BufferTwins.column(results, f"{buffer} - {SimpleHotWaterStorage.StandbyHeatLoss}") * hours_per_step
    )
    stored_in_watt_hour = BufferTwins.column(
        results, f"{buffer} - {SimpleHotWaterStorage.ThermalEnergyIncreaseInStorage}"
    )
    residual_in_watt_hour = (
        primary_in_watt_hour
        + secondary_in_watt_hour
        + distribution_in_watt_hour
        - loss_in_watt_hour
        - stored_in_watt_hour
    )
    largest_in_watt_hour = np.maximum.reduce(
        [abs(primary_in_watt_hour), abs(distribution_in_watt_hour), abs(loss_in_watt_hour), abs(stored_in_watt_hour)]
    )
    assert np.all(np.abs(residual_in_watt_hour) <= 1e-6 * largest_in_watt_hour + 1e-9), float(
        np.max(np.abs(residual_in_watt_hour))
    )

    check_end_temperatures_without_each_circuit(
        results, buffer, primary_in_watt_hour, secondary_in_watt_hour, distribution_in_watt_hour
    )

    booked_in_watt = BufferTwins.column(results, booked_prefix)
    received_in_watt = BufferTwins.column(results, f"{buffer} - {SimpleHotWaterStorage.ThermalPowerFromHeatGenerator}")
    generator_tolerance_in_watt = BufferTwins.circuit_tolerance_in_watt(BufferTwins.column(results, flow_prefix))
    assert np.all(np.abs(booked_in_watt - received_in_watt) <= generator_tolerance_in_watt), float(
        np.max(np.abs(booked_in_watt - received_in_watt))
    )
    assert np.any(booked_in_watt > 0.0), "the generator never charged the buffer in the window"

    delivered_in_watt = BufferTwins.column(results, f"{distribution} - {HeatDistribution.ThermalPowerDelivered}")
    given_in_watt = BufferTwins.column(
        results, f"{buffer} - {SimpleHotWaterStorage.ThermalPowerConsumptionHeatDistribution}"
    )
    distribution_tolerance_in_watt = BufferTwins.circuit_tolerance_in_watt(
        BufferTwins.column(results, f"{distribution} - {HeatDistribution.WaterMassFlowHDS}")
    )
    assert np.all(np.abs(delivered_in_watt - given_in_watt) <= distribution_tolerance_in_watt), float(
        np.max(np.abs(delivered_in_watt - given_in_watt))
    )
    assert np.any(delivered_in_watt > 0.0), "the distribution never heated in the window"

    # above the part-load threshold the distribution circuit runs only the fraction that keeps the buffer at or above
    # the room's start-of-step temperature, so a step it heats in never ends the buffer below the room
    end_in_celsius = BufferTwins.column(
        results, f"{buffer} - {SimpleHotWaterStorage.WaterTemperatureAtEndOfStepInCelsius}"
    )
    room_in_celsius = BufferTwins.column(results, f"{building} - {Building.TemperatureIndoorAirAtStartOfStepInCelsius}")
    heating = delivered_in_watt > 0.0
    assert np.all(end_in_celsius[heating] >= room_in_celsius[heating] - BufferTwins.FLOOR_TOLERANCE_IN_KELVIN), float(
        np.min(end_in_celsius[heating] - room_in_celsius[heating])
    )


def check_end_temperatures_without_each_circuit(
    results: pd.DataFrame,
    buffer: str,
    primary_in_watt_hour: np.ndarray,
    secondary_in_watt_hour: np.ndarray,
    distribution_in_watt_hour: np.ndarray,
) -> None:
    """Each "end temperature without this circuit" lies below the end temperature by exactly that circuit's heat over C.

    The buffer's heat capacity follows from its published content and end temperature, ``C = 3600 E / T_end``.
    """
    end_in_celsius = BufferTwins.column(
        results, f"{buffer} - {SimpleHotWaterStorage.WaterTemperatureAtEndOfStepInCelsius}"
    )
    content_in_watt_hour = BufferTwins.column(results, f"{buffer} - {SimpleHotWaterStorage.ThermalEnergyInStorage}")
    heat_capacity_in_watt_hour_per_kelvin = content_in_watt_hour / end_in_celsius
    for field, heat_in_watt_hour in (
        (SimpleHotWaterStorage.WaterTemperatureAtEndOfStepWithoutHeatGeneratorInCelsius, primary_in_watt_hour),
        (
            SimpleHotWaterStorage.WaterTemperatureAtEndOfStepWithoutSecondaryHeatGeneratorInCelsius,
            secondary_in_watt_hour,
        ),
        (SimpleHotWaterStorage.WaterTemperatureAtEndOfStepWithoutHeatDistributionInCelsius, distribution_in_watt_hour),
    ):
        without_in_celsius = BufferTwins.column(results, f"{buffer} - {field}")
        change_in_watt_hour = (end_in_celsius - without_in_celsius) * heat_capacity_in_watt_hour_per_kelvin
        assert np.allclose(change_in_watt_hour, heat_in_watt_hour, rtol=1e-9, atol=1e-6), field


@pytest.mark.base
def test_every_energy_system_file_with_a_buffer_is_checked() -> None:
    """A new file with a space-heating buffer cannot escape the per-step checks."""
    listed = set(BufferTwins.TWINS) | set(BufferTwins.derived_files())
    found = set()
    for path in sorted((BufferTwins.ROOT / "energy_systems").glob("*.energy_system.yaml")):
        text = path.read_text(encoding="utf-8")
        has_buffer = SimpleHotWaterStorage.get_full_classname() in text or any(
            f"heating/{assembly}" in text
            for assembly in (
                "gas_condensing_boiler",
                "oil_boiler",
                "pellet_boiler",
                "wood_chip_boiler",
                "hydrogen_boiler",
                "air_source_heat_pump",
            )
        )
        if has_buffer:
            found.add(path.name[: -len(".energy_system.yaml")])
    assert found <= listed, sorted(found - listed)


@pytest.mark.extendedbase2
@pytest.mark.parametrize("name", BufferTwins.TWINS)
def test_the_recorded_twins_close_their_buffer_and_agree_on_both_circuits(
    name: str, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Every recorded twin with a buffer at 900 s: closure, the generator's circuit and the distribution's circuit."""
    check_file(name, tmp_path_factory)


@pytest.mark.extendedbase2
@pytest.mark.parametrize("name", BufferTwins.derived_files())
def test_the_grouped_twins_and_composed_files_close_their_buffer_and_agree_on_both_circuits(
    name: str, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """The same checks for the grouped twins RenoVisor translates from and for the composed files."""
    check_file(name, tmp_path_factory)
