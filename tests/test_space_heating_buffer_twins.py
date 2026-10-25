"""Every energy-system file with a space-heating buffer closes it on every step, and both ends of its circuits agree.

The buffer (``SimpleHotWaterStorage``) is a two-mode buffer (``roadmap/two_mode_buffer_spec.md``). Above the
part-load threshold (600 s) it is a start-of-step node: it publishes the temperature it starts the step at as the
return to its generators and as the supply to the heat distribution system, every circuit's heat is ``m c`` times the
difference between the entering stream and that temperature, and the buffer ends the step at its start temperature
plus the net heat over its heat capacity. At and below the threshold it is an iterated mixed node that publishes its
step mean instead, and both ends of a circuit agree once the step has converged. So on every step:

* the buffer's balance closes: the generators' heat, less the heat the distribution takes, less the standby loss,
  is the change of its stored heat, to a millionth of the step's largest term;
* the space heat the boiler or heat pump books is the heat the buffer received from it;
* the heat the distribution system delivers to the building is the heat the buffer gave to it.

Each file is simulated over a January day at 900 s (the start-of-step buffer), and the four twins of the golden gate
with a buffer also at 60 s (the iterated buffer). At 600 s, the threshold, the gas and the heat-pump twins show that
every part-load ratio is exactly 0 or 1 with the iterated buffer, so the heat pump's two tracks of its mode and the
boiler's held burner behave as without part load. Every run simulates a whole energy system and so runs in the
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

    #: The step length of the runs of every file, in s: the start-of-step buffer.
    SECONDS_PER_TIMESTEP: ClassVar[int] = 900

    #: The step length of the iterated buffer's runs, in s, the golden gate's.
    ITERATED_SECONDS_PER_TIMESTEP: ClassVar[int] = 60

    #: The twins of the golden gate with a buffer, one per generator kind, run at 60 s as well.
    ITERATED_TWINS: ClassVar[Tuple[str, ...]] = (
        "household_gas_building_sizer",
        "household_pellets_building_sizer",
        "household_heatpump_building_sizer",
        "household_heatpump_solar_thermal_building_sizer",
    )

    #: The step length at the part-load threshold, in s: the longest step of the iterated buffer.
    THRESHOLD_SECONDS_PER_TIMESTEP: ClassVar[int] = 600

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
    def run(
        cls, name: str, directory: str, seconds_per_timestep: int = SECONDS_PER_TIMESTEP
    ) -> Tuple[pd.DataFrame, str, str, str, str, str]:
        """Simulate ``energy_systems/<name>.energy_system.yaml`` over :attr:`WINTER_DAY` at a step length.

        Args:
            name: The file's name without ``.energy_system.yaml``.
            directory: The directory to write the files and results into.
            seconds_per_timestep: The step length, s; 900 s by default.

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
                    "seconds_per_timestep": seconds_per_timestep,
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


def check_file(
    name: str, tmp_path_factory: pytest.TempPathFactory, seconds_per_timestep: int = BufferTwins.SECONDS_PER_TIMESTEP
) -> None:
    """Run one file and check the buffer's closure and both of its circuits on every step.

    Above the part-load threshold (the start-of-step buffer) it also checks the end temperatures without each circuit
    and the distribution's floor; at and below it (the iterated buffer) that the end temperatures without a circuit
    carry the end temperature, as nothing reads them there.
    """
    results, buffer, booked_prefix, flow_prefix, distribution, building = BufferTwins.run(
        name, str(tmp_path_factory.mktemp(name.replace(".", "_"))), seconds_per_timestep
    )
    start_of_step_buffer = seconds_per_timestep > BufferTwins.THRESHOLD_SECONDS_PER_TIMESTEP
    hours_per_step = seconds_per_timestep / BufferTwins.SECONDS_PER_HOUR
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

    if start_of_step_buffer:
        check_end_temperatures_without_each_circuit(
            results, buffer, primary_in_watt_hour, secondary_in_watt_hour, distribution_in_watt_hour
        )
    else:
        check_end_temperatures_without_each_circuit_carry_the_end_temperature(results, buffer)

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

    if not start_of_step_buffer:
        return
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
    """Each "end temperature without this circuit" differs from the end temperature by that circuit's heat over C.

    It lies below the end temperature for a generator, which adds heat, and above it for the distribution, which takes
    heat out: a buffer that ends at 30 °C after the distribution took 2 K worth of heat would end at 32 °C without it.
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


def check_end_temperatures_without_each_circuit_carry_the_end_temperature(results: pd.DataFrame, buffer: str) -> None:
    """With the iterated buffer the three end temperatures without a circuit carry the end temperature (decision Q2)."""
    end_in_celsius = BufferTwins.column(
        results, f"{buffer} - {SimpleHotWaterStorage.WaterTemperatureAtEndOfStepInCelsius}"
    )
    for field in (
        SimpleHotWaterStorage.WaterTemperatureAtEndOfStepWithoutHeatGeneratorInCelsius,
        SimpleHotWaterStorage.WaterTemperatureAtEndOfStepWithoutSecondaryHeatGeneratorInCelsius,
        SimpleHotWaterStorage.WaterTemperatureAtEndOfStepWithoutHeatDistributionInCelsius,
    ):
        assert np.array_equal(BufferTwins.column(results, f"{buffer} - {field}"), end_in_celsius), field


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


@pytest.mark.extendedbase2
@pytest.mark.parametrize("name", BufferTwins.ITERATED_TWINS)
def test_at_60_s_the_twins_close_their_iterated_buffer_and_agree_on_both_circuits(
    name: str, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """The four twins of the golden gate with a buffer at 60 s, where the buffer is an iterated mixed node.

    Fails if a step of the iterated buffer does not close its balance, or if a generator or the distribution system
    booked a circuit on another temperature than the buffer's converged step mean.
    """
    check_file(name, tmp_path_factory, BufferTwins.ITERATED_SECONDS_PER_TIMESTEP)


@pytest.mark.extendedbase2
@pytest.mark.parametrize(
    "name, device_on_column",
    [
        (
            "household_gas_building_sizer",
            f"CondensingGasBoiler - {GenericBoiler.WaterOutputMassFlowSh}",
        ),
        (
            "household_heatpump_building_sizer",
            f"MoreAdvancedHeatPumpHPLib - {MoreAdvancedHeatPumpHPLib.MassFlowOutputSH}",
        ),
    ],
)
def test_at_the_threshold_every_part_load_ratio_is_0_or_1_and_1_while_the_generator_heats_its_buffer(
    name: str, device_on_column: str, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """At 600 s every part-load ratio, requested or actual, is exactly 0 or 1, and 1 in every step the generator charges.

    With the iterated buffer the part-load controls publish 1 while their device runs, so a generator never runs a
    fraction of a step. That is what keeps the heat pump's two tracks of its mode (its control track and the published
    one, which differ only for a space-heating step at a ratio of 0) and the boiler's held burner (a whole step) exactly
    as they were without part load. The heat pump's published state is space heating exactly in the steps it runs its
    space-heating ratio of 1.

    Fails if any ratio column holds a fraction, or if a generator charged its buffer at a ratio other than 1.
    """
    results, *_ = BufferTwins.run(name, str(tmp_path_factory.mktemp(name)), BufferTwins.THRESHOLD_SECONDS_PER_TIMESTEP)
    ratios = [
        column
        for column in results.columns
        if " - RequestedPartLoadRatio" in column or " - ActualPartLoadRatio" in column
    ]
    assert len(ratios) >= 3, ratios
    for column in ratios:
        values = results[column].to_numpy(dtype=float)
        assert np.all((values == 0.0) | (values == 1.0)), (column, sorted(set(values.tolist()))[:5])
    generator = device_on_column.split(" - ", 1)[0]
    actual_ratio = BufferTwins.column(results, f"{generator} - ActualPartLoadRatioSh")
    charging = BufferTwins.column(results, device_on_column) > 0.0
    assert np.any(charging), "the generator never charged the buffer in the window"
    assert np.all(actual_ratio[charging] == 1.0)
    if generator == "MoreAdvancedHeatPumpHPLib":
        published_space_heating = BufferTwins.column(results, f"{generator} - OnOffStateHeatpump") == 1.0
        assert np.array_equal(published_space_heating, actual_ratio == 1.0)
