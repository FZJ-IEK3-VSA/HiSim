"""Test for generic pv system."""

import dataclasses
import pathlib
from typing import Any, Callable, Dict, List, Optional, Tuple
import pytest
import yaml
from hisim import hydronics
from hisim import loadtypes as lt
from hisim import simulator as sim
from hisim.components import generic_boiler
from hisim.components.dual_circuit_system import HeatingMode
from hisim.components.generic_boiler import (
    GenericBoilerController,
    GenericBoilerControllerConfig,
)
from hisim.config import ComponentID, DisplayConfig, SizingContext
from hisim.simulationparameters import SimulationParameters


@pytest.mark.base
@pytest.mark.parametrize(
    [
        "operating_mode",
        "min_state_time",
        "water_temp_sh_in_celsius",
        "water_temp_dhw_in_celsius",
        "expected_mode",
    ],
    [
        (HeatingMode.OFF, 0, 65, 60, HeatingMode.OFF),
        (HeatingMode.OFF, 0, 40, 60, HeatingMode.SPACE_HEATING),
        (HeatingMode.OFF, 0, 40, 40, HeatingMode.DOMESTIC_HOT_WATER),
        (HeatingMode.OFF, 0, 65, 50, HeatingMode.DOMESTIC_HOT_WATER),
        (HeatingMode.OFF, 0, 0, 0, HeatingMode.DOMESTIC_HOT_WATER),
        (HeatingMode.SPACE_HEATING, 0, 60, 60, HeatingMode.OFF),
        (HeatingMode.DOMESTIC_HOT_WATER, 0, 60, 60, HeatingMode.OFF),
        (HeatingMode.SPACE_HEATING, 0, 60, 40, HeatingMode.DOMESTIC_HOT_WATER),
        (HeatingMode.DOMESTIC_HOT_WATER, 0, 40, 60, HeatingMode.SPACE_HEATING),
        (HeatingMode.OFF, 10, 0, 0, HeatingMode.DOMESTIC_HOT_WATER),
    ],
)
def test_determine_mode_returns_correct_operation_mode_for_temperature_and_time(
    operating_mode: HeatingMode,
    min_state_time: int,
    water_temp_sh_in_celsius: float,
    water_temp_dhw_in_celsius: float,
    expected_mode: str,
):
    """GIVEN."""
    testee = given_default_testee(
        {
            "minimum_runtime_in_seconds": min_state_time,
            "minimum_resting_time_in_seconds": min_state_time,
            "with_domestic_hot_water_preparation": True,
        }
    )
    testee.controller_mode = operating_mode
    testee.warm_water_temperature_aim_in_celsius = 60

    is_heating_season = True
    heating_flow_temperature = 55
    timestep = 5

    """ WHEN """
    _, _ = testee.determine_operating_mode(
        is_heating_season,
        water_temp_sh_in_celsius,
        water_temp_dhw_in_celsius,
        heating_flow_temperature,
        timestep,
    )

    """ THEN """
    assert testee.controller_mode == expected_mode


def given_default_testee(
    config_overwrite: Optional[Dict[str, Any]] = None,
) -> GenericBoilerController:
    """Create and configure default testee."""
    if config_overwrite is None:
        config_overwrite = {}
    simulationparameters = sim.SimulationParameters.full_year(year=2021, seconds_per_timestep=60)
    config = GenericBoilerControllerConfig.preset_on_off("OnOffBoilerController").resolve(
        SizingContext(maximal_thermal_power_in_watt=2500, minimal_thermal_power_in_watt=1000)
    )
    config.minimum_runtime_in_seconds = config_overwrite.get("minimum_runtime_in_seconds", 0)
    config.minimum_resting_time_in_seconds = config_overwrite.get("minimum_resting_time_in_seconds", 0)
    config.with_domestic_hot_water_preparation = config_overwrite.get(
        "with_domestic_hot_water_preparation",
        config.with_domestic_hot_water_preparation,
    )
    config.hysteresis_water_temperature_offset = 5
    testee = GenericBoilerController(
        simulationparameters,
        config,
        DisplayConfig(),
    )
    return testee


class FuelConstants:
    """What the boiler's two fuel constants are checked against, and with what.

    The numbers themselves are not repeated here: the point of the check is that the
    configuration and the component agree, so repeating a literal would only pin the
    ``PhysicsConfig`` table a second time and would pass even if the two derivations drifted
    apart. The building load is any load that sizes a boiler; nothing about the fuel depends
    on it.
    """

    #: A load big enough to size a real device, small enough to be a single-family home.
    HEATING_LOAD_IN_WATT: float = 8000.0

    #: One apartment, so the domestic-hot-water branch of the power law is exercised too.
    NUMBER_OF_APARTMENTS: float = 1.0


@pytest.mark.base
@pytest.mark.parametrize(
    "preset_name, energy_carrier, boiler_type",
    [
        ("preset_condensing_gas", lt.LoadTypes.GAS, generic_boiler.BoilerType.CONDENSING),
        ("preset_oil", lt.LoadTypes.OIL, generic_boiler.BoilerType.CONVENTIONAL),
    ],
)
def test_the_config_derives_the_fuel_constants_the_component_exposes(
    preset_name: str,
    energy_carrier: lt.LoadTypes,
    boiler_type: "generic_boiler.BoilerType",
) -> None:
    """The build-time derivation is the one the component runs on, for both boiler types.

    Failure mode caught: the derivation moving to ``GenericBoilerConfig`` (D-15) and drifting
    from what ``GenericBoiler.build`` sets, so the meter reading the contributed facts would
    account litres and kilograms the boiler beside it never burnt. Both boiler types are
    covered because the type is what picks the higher or the lower heating value.
    """
    config = getattr(generic_boiler.GenericBoilerConfig, preset_name)("Boiler").resolve(
        SizingContext(
            heating_load_in_watt=FuelConstants.HEATING_LOAD_IN_WATT,
            number_of_apartments=FuelConstants.NUMBER_OF_APARTMENTS,
        )
    )
    component = generic_boiler.GenericBoiler(
        config=config,
        my_simulation_parameters=SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60),
    )

    heating_value_in_kwh_per_liter, density_in_kg_per_m3 = generic_boiler.GenericBoilerConfig.fuel_constants(
        energy_carrier, boiler_type
    )

    assert component.heating_value_of_fuel_in_kwh_per_liter == heating_value_in_kwh_per_liter
    assert component.fuel_density_in_kg_per_m3 == density_in_kg_per_m3
    assert heating_value_in_kwh_per_liter is not None and density_in_kg_per_m3 is not None


@pytest.mark.base
def test_the_contributed_facts_carry_the_carrier_and_its_two_constants() -> None:
    """The boiler ships its fuel as sizing facts, with the values the component burns by.

    Failure mode caught: the contribution computing the constants a second way, or declaring
    a fact it does not return — the engine checks the names, but only a test checks that the
    values are the component's.
    """
    config = generic_boiler.GenericBoilerConfig.preset_condensing_gas("Boiler").resolve(
        SizingContext(
            heating_load_in_watt=FuelConstants.HEATING_LOAD_IN_WATT,
            number_of_apartments=FuelConstants.NUMBER_OF_APARTMENTS,
        )
    )
    component = generic_boiler.GenericBoiler(
        config=config,
        my_simulation_parameters=SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60),
    )

    contributions = generic_boiler.GenericBoilerConfig.SIZING_CONTRIBUTIONS
    assert len(contributions) == 1
    facts = contributions[0].compute(config, SizingContext())

    assert facts["energy_carrier"] is lt.LoadTypes.GAS
    assert facts["heating_value_of_fuel_in_kwh_per_liter"] == component.heating_value_of_fuel_in_kwh_per_liter
    assert facts["fuel_density_in_kg_per_m3"] == component.fuel_density_in_kg_per_m3


@pytest.mark.base
def test_district_heating_has_no_heating_value_and_no_fuel_density() -> None:
    """A carrier that burns nothing ships ``None`` for both constants, not a stand-in number.

    Failure mode caught: district heating inheriting whatever the neighbouring setup happened
    to pass — today's setups hand a district-heating meter the *oil* constants — so its
    consumption would be reported as litres of a fuel nobody burnt (D-15).
    """
    assert generic_boiler.GenericBoilerConfig.fuel_constants(
        lt.LoadTypes.DISTRICTHEATING, generic_boiler.BoilerType.CONDENSING
    ) == (None, None)

    config = generic_boiler.GenericBoilerConfig(
        component_id=ComponentID(name="DistrictHeatingBoiler"),
        energy_carrier=lt.LoadTypes.DISTRICTHEATING,
        boiler_type=generic_boiler.BoilerType.CONDENSING,
        minimal_thermal_power_in_watt=0.0,
        maximal_thermal_power_in_watt=FuelConstants.HEATING_LOAD_IN_WATT,
    )
    contributions = generic_boiler.GenericBoilerConfig.SIZING_CONTRIBUTIONS
    assert len(contributions) == 1
    facts = contributions[0].compute(config, SizingContext())

    assert facts["heating_value_of_fuel_in_kwh_per_liter"] is None
    assert facts["fuel_density_in_kg_per_m3"] is None
    assert facts["energy_carrier"] is lt.LoadTypes.DISTRICTHEATING


@pytest.mark.base
@pytest.mark.parametrize(
    ["preset_name", "expected_is_modulating", "expected_runtime", "expected_resting"],
    [
        ("preset_modulating", True, 1800, 1800),
        ("preset_on_off", False, 0, 0),
    ],
)
def test_controller_presets_reproduce_the_factories_they_replaced(
    preset_name: str,
    expected_is_modulating: bool,
    expected_runtime: float,
    expected_resting: float,
) -> None:
    """The two presets still resolve to the literals the four deleted factories wrote.

    Failure mode caught: a preset default drifting from the factory it replaced. The
    ``get_default_modulating_…`` / ``get_default_on_off_…`` factories are gone with no shim,
    so nothing else in the repository states what a boiler controller is supposed to start
    from; a silent edit to a field default here would move eleven call sites at once.
    The power band is AUTO in both presets and arrives from the boiler through the
    ``SizingContext``, the same two numbers the factories took as their first arguments.
    """
    preset = getattr(GenericBoilerControllerConfig, preset_name)("X")
    config = preset.resolve(SizingContext(maximal_thermal_power_in_watt=2500, minimal_thermal_power_in_watt=1000))

    assert config.component_id.name == "X"
    assert config.is_modulating is expected_is_modulating
    assert config.minimum_runtime_in_seconds == expected_runtime
    assert config.minimum_resting_time_in_seconds == expected_resting
    assert config.set_temperature_difference_for_full_power == 5.0
    assert config.hysteresis_water_temperature_offset == 10.0
    assert config.secondary_mode is False
    assert config.with_domestic_hot_water_preparation is False
    assert config.maximal_thermal_power_in_watt == 2500
    assert config.minimal_thermal_power_in_watt == 1000


@pytest.mark.base
@pytest.mark.parametrize(
    ["twin_file_name", "controller_key", "expected_runtime", "expected_resting"],
    [
        (
            "household_pellets_building_sizer.energy_system.yaml",
            "PelletBoilerController",
            1800.0,
            900.0,
        ),
        (
            "household_wood_chips_building_sizer.energy_system.yaml",
            "WoodChipBoilerController",
            3600.0,
            1800.0,
        ),
    ],
)
def test_solid_fuel_setups_keep_the_timings_of_their_deleted_factories(
    twin_file_name: str,
    controller_key: str,
    expected_runtime: float,
    expected_resting: float,
) -> None:
    """Pellet and wood chip boilers still cycle on the timings their own factories carried.

    Failure mode caught: a solid-fuel override dropped from a setup. ``get_default_pellet_…``
    set 30 and 15 minutes and ``get_default_wood_chip_…`` 60 and 30; both are now plain field
    overrides on ``preset_on_off``, and an override is far easier to lose than a factory name.
    The recorded twin is read rather than the setup because the energy-system freshness gate
    ties the two together, so pinning the twin pins the setup that recorded it.
    """
    twin_path = pathlib.Path(__file__).resolve().parents[1] / "energy_systems" / twin_file_name
    twin = yaml.safe_load(twin_path.read_text(encoding="utf-8"))

    controller = twin["components"][controller_key]
    assert controller["class"] == "hisim.components.generic_boiler.GenericBoilerController"
    assert controller["preset"] == "on_off"
    assert controller["config"]["minimum_runtime_in_seconds"] == expected_runtime
    assert controller["config"]["minimum_resting_time_in_seconds"] == expected_resting


@pytest.mark.base
@pytest.mark.parametrize(
    ["runtime_in_seconds", "resting_time_in_seconds", "warned_fields"],
    [
        (600, 1800, ["minimum_runtime_in_seconds"]),
        (900, 300, ["minimum_resting_time_in_seconds"]),
        (1800, 900, []),
        (0, 0, []),
    ],
)
def test_a_minimum_time_shorter_than_one_timestep_warns_once_and_a_whole_one_does_not(
    runtime_in_seconds: float,
    resting_time_in_seconds: float,
    warned_fields: list,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches a minimum time that rounds down to zero timesteps without a word, or a warning for a whole one.

    At 900 s per timestep the controller counts 600 s or 300 s as zero timesteps, so that field has no effect;
    900 s and 1800 s are whole timesteps, and zero is no minimum at all.
    """
    warnings: list = []
    monkeypatch.setattr(generic_boiler.log, "warning", warnings.append)
    config = GenericBoilerControllerConfig.preset_on_off("OnOffBoilerController").resolve(
        SizingContext(maximal_thermal_power_in_watt=2500, minimal_thermal_power_in_watt=1000)
    )
    config.minimum_runtime_in_seconds = runtime_in_seconds
    config.minimum_resting_time_in_seconds = resting_time_in_seconds
    controller = GenericBoilerController(
        SimulationParameters.one_day_only(year=2021, seconds_per_timestep=900), config, DisplayConfig()
    )
    assert [field_name for field_name in warned_fields if any(field_name in text for text in warnings)] == warned_fields
    assert len(warnings) == len(warned_fields)
    assert controller.minimum_runtime_in_timesteps == int(runtime_in_seconds // 900)
    assert controller.minimum_resting_time_in_timesteps == int(resting_time_in_seconds // 900)


@pytest.mark.base
@pytest.mark.parametrize("field_name", ["minimum_runtime_in_seconds", "minimum_resting_time_in_seconds"])
@pytest.mark.parametrize("seconds", [-900.0, float("nan")])
def test_a_negative_or_non_finite_minimum_time_is_refused(field_name: str, seconds: float) -> None:
    """Catches a negative minimum time that rounds to no minimum without a word; the message names field and value."""
    config = GenericBoilerControllerConfig.preset_on_off("OnOffBoilerController")
    with pytest.raises(ValueError, match=rf"needs {field_name} to be a finite time of at least 0 s, got {seconds!r} s"):
        overrides: Dict[str, Any] = {field_name: seconds}
        dataclasses.replace(config, **overrides)


class BoilerStep:
    """One step of a 20 kW condensing gas boiler whose inputs read fake outputs, a namespace of helpers."""

    #: The order of the fakes: control signal, operating mode, lift, space-heating return, hot-water return, the
    #: controller's hot-water supply set temperature, its hot-water and space-heating part-load ratios, and the
    #: control signal and lift of a space-heating charge in the rest of a hot-water step.
    INPUT_ORDER = (
        "control_signal",
        "operating_mode",
        "lift",
        "space_heating_return",
        "hot_water_return",
        "set",
        "ratio",
        "space_heating_ratio",
        "remainder_signal",
        "remainder_lift",
    )

    #: The hot-water set temperature a step uses unless the test names one, in °C: the 80 °C maximal flow
    #: temperature itself, so that only the maximum limits the supply.
    UNLIMITING_SET_TEMPERATURE_IN_CELSIUS = 80.0

    @staticmethod
    def build() -> Tuple[Any, Any, List[Any]]:
        """Return the boiler, the step values and its fakes in :attr:`INPUT_ORDER`."""
        from hisim import component as cp  # pylint: disable=import-outside-toplevel  # keeps the test module light
        from tests import functions_for_testing as fft  # pylint: disable=import-outside-toplevel  # as above

        parameters = SimulationParameters.one_day_only(2021, 900)
        config = generic_boiler.GenericBoilerConfig.preset_condensing_gas("Boiler")
        config.maximal_thermal_power_in_watt = 20000.0
        config.minimal_thermal_power_in_watt = 2000.0
        boiler = generic_boiler.GenericBoiler(parameters, config)
        channels = [
            (boiler.control_signal_channel, lt.LoadTypes.ANY, lt.Units.PERCENT),
            (boiler.operating_mode_channel, lt.LoadTypes.ANY, lt.Units.ANY),
            (boiler.temperature_delta_channel, lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS),
            (boiler.water_input_temperature_sh_channel, lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS),
            (boiler.water_input_temperature_dhw_channel, lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS),
            (boiler.supply_temperature_set_for_dhw_in_celsius_channel, lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS),
            (boiler.dhw_part_load_command.ratio_channel, lt.LoadTypes.ANY, lt.Units.FRACTION),
            (boiler.space_heating_part_load_command.ratio_channel, lt.LoadTypes.ANY, lt.Units.FRACTION),
            (boiler.control_signal_for_space_heating_remainder_channel, lt.LoadTypes.ANY, lt.Units.PERCENT),
            (boiler.temperature_delta_for_space_heating_remainder_channel, lt.LoadTypes.TEMPERATURE, lt.Units.KELVIN),
        ]
        fakes = []
        for number, (channel, load_type, unit) in enumerate(channels):
            fake = cp.ComponentOutput(
                f"Fake{number}", f"Fake{number}", load_type, unit, component_id=ComponentID(f"Fake{number}")
            )
            channel.source_output = fake
            fakes.append(fake)
        stsv = cp.SingleTimeStepValues(fft.get_number_of_outputs([*fakes, boiler]))
        fft.add_global_index_of_components([*fakes, boiler])
        return boiler, stsv, fakes

    @staticmethod
    def run(**inputs: float) -> Tuple[Any, Callable[[Any], float]]:
        """Step a fresh boiler once with these inputs (see :attr:`INPUT_ORDER`), return it and a reader of outputs.

        A missing return is 0 °C, a missing set temperature :attr:`UNLIMITING_SET_TEMPERATURE_IN_CELSIUS`, a missing
        part-load ratio 1, the whole step.
        """
        boiler, stsv, fakes = BoilerStep.build()
        values = {
            "set": BoilerStep.UNLIMITING_SET_TEMPERATURE_IN_CELSIUS,
            "ratio": 1.0,
            "space_heating_ratio": 1.0,
            **inputs,
        }
        for name, fake in zip(BoilerStep.INPUT_ORDER, fakes):
            stsv.set_output_value(fake, values.get(name, 0.0))
        boiler.i_simulate(0, stsv, False)

        def output(channel: Any) -> float:
            """Return the value the boiler set on this output channel."""
            return float(stsv.values[channel.global_index])

        return boiler, output


@pytest.mark.base
def test_the_hot_water_circuit_books_the_heat_its_water_carries_from_the_tanks_step_mean() -> None:
    """A boiler that booked another heat than its water carries would unbalance the hot-water circuit.

    20 kW at full signal, a 20 K lift and a 47.3 °C return: the boiler pumps P / (c 20 K) and supplies 67.3 °C, and
    the heat it books is the heat that water carries, which is its thermal power.
    """
    boiler, output = BoilerStep.run(
        control_signal=1.0,
        operating_mode=HeatingMode.DOMESTIC_HOT_WATER.value,
        lift=20.0,
        space_heating_return=35.0,
        hot_water_return=47.3,
    )
    mass_flow_in_kg_per_second = output(boiler.water_output_mass_flow_dhw_channel)
    supply_temperature_in_celsius = output(boiler.water_output_temperature_dhw_channel)
    booked_power_in_watt = output(boiler.thermal_output_power_dhw_channel)
    assert supply_temperature_in_celsius == pytest.approx(67.3)
    assert booked_power_in_watt == hydronics.circuit_power_w(
        mass_flow_kg_per_s=mass_flow_in_kg_per_second, t_supply_c=supply_temperature_in_celsius, t_return_c=47.3
    )
    assert booked_power_in_watt == pytest.approx(20000.0 * boiler.max_combustion_efficiency, rel=1e-12)
    assert output(boiler.energy_demand_dhw_channel) == pytest.approx(20000.0 * 900 / 3600)
    assert output(boiler.total_fuel_input_power_channel) == 20000.0


@pytest.mark.base
@pytest.mark.parametrize("mode", [HeatingMode.DOMESTIC_HOT_WATER, HeatingMode.SPACE_HEATING])
def test_a_charge_without_a_lift_does_not_fire(mode: HeatingMode) -> None:
    """A circuit without a lift moves no water: a boiler that booked heat or fuel on it would bill heat nobody got.

    Space heating with a lift of 0 used to book the burner's full heat at zero flow.
    """
    boiler, output = BoilerStep.run(
        control_signal=1.0, operating_mode=mode.value, lift=0.0, space_heating_return=35.0, hot_water_return=71.0
    )
    for channel in (
        boiler.water_output_mass_flow_dhw_channel,
        boiler.thermal_output_power_dhw_channel,
        boiler.energy_demand_dhw_channel,
        boiler.water_output_mass_flow_sh_channel,
        boiler.thermal_output_power_sh_channel,
        boiler.energy_demand_sh_channel,
        boiler.combustion_heat_loss_channel,
        boiler.total_fuel_input_power_channel,
    ):
        assert output(channel) == 0.0, channel.field_name
    assert output(boiler.water_output_temperature_dhw_channel) == 71.0
    assert output(boiler.water_output_temperature_sh_channel) == 35.0


@pytest.mark.base
def test_an_idle_boiler_burns_no_fuel() -> None:
    """An off boiler that reported its minimum burner power as fuel would show fuel burnt while idle."""
    boiler, output = BoilerStep.run(
        control_signal=0.0,
        operating_mode=HeatingMode.OFF.value,
        lift=0.0,
        space_heating_return=35.0,
        hot_water_return=50.0,
    )
    assert output(boiler.total_fuel_input_power_channel) == 0.0
    assert output(boiler.combustion_heat_loss_channel) == 0.0
    assert output(boiler.water_output_temperature_sh_channel) == 35.0
    assert output(boiler.water_output_temperature_dhw_channel) == 50.0


@pytest.mark.base
def test_a_charge_above_the_maximal_flow_temperature_is_throttled_and_burns_what_its_heat_needs() -> None:
    """A throttled charge that burnt its commanded fuel would bill heat its water does not carry.

    A 30 K lift on a 70 °C return stops at the 80 °C maximum. The pump keeps the flow of the unthrottled charge,
    P_th / (c 30 K), so the water carries a third of P_th. The burner cycles at the power it was commanded to, full
    power here, so the fuel is that heat over the efficiency at full power, and the loss is the fuel the heat does
    not take.
    """
    boiler, output = BoilerStep.run(
        control_signal=1.0,
        operating_mode=HeatingMode.DOMESTIC_HOT_WATER.value,
        lift=30.0,
        space_heating_return=35.0,
        hot_water_return=70.0,
    )
    mass_flow_in_kg_per_second = output(boiler.water_output_mass_flow_dhw_channel)
    booked_power_in_watt = output(boiler.thermal_output_power_dhw_channel)
    fuel_power_in_watt = output(boiler.total_fuel_input_power_channel)
    assert output(boiler.water_output_temperature_dhw_channel) == 80.0
    assert booked_power_in_watt == hydronics.circuit_power_w(
        mass_flow_kg_per_s=mass_flow_in_kg_per_second, t_supply_c=80.0, t_return_c=70.0
    )
    assert booked_power_in_watt == pytest.approx(20000.0 * boiler.max_combustion_efficiency / 3.0, rel=1e-12)
    assert fuel_power_in_watt == pytest.approx(booked_power_in_watt / boiler.burner.efficiency_at(20000.0), rel=1e-12)
    assert fuel_power_in_watt == pytest.approx(20000.0 / 3.0, rel=1e-12)
    assert output(boiler.energy_demand_dhw_channel) == pytest.approx(fuel_power_in_watt * 900 / 3600)
    assert output(boiler.combustion_heat_loss_channel) == pytest.approx(fuel_power_in_watt - booked_power_in_watt)


@pytest.mark.base
def test_a_hot_water_charge_stops_at_the_controllers_set_temperature() -> None:
    """A charge that passed the controller's 70 °C target would end a whole step past it at coarse steps.

    A 20 K lift on a 60 °C return supplies 70 °C. The flow stays that of the full 20 K charge, so the water carries
    half the commanded heat, and the fuel is that heat over the efficiency at the commanded power. Below the set
    temperature nothing is throttled.
    """
    boiler, output = BoilerStep.run(
        control_signal=1.0,
        operating_mode=HeatingMode.DOMESTIC_HOT_WATER.value,
        lift=20.0,
        space_heating_return=35.0,
        hot_water_return=60.0,
        set=70.0,
    )
    booked_power_in_watt = output(boiler.thermal_output_power_dhw_channel)
    assert output(boiler.water_output_temperature_dhw_channel) == 70.0
    assert booked_power_in_watt == hydronics.circuit_power_w(
        mass_flow_kg_per_s=output(boiler.water_output_mass_flow_dhw_channel), t_supply_c=70.0, t_return_c=60.0
    )
    assert booked_power_in_watt == pytest.approx(20000.0 * boiler.max_combustion_efficiency / 2.0, rel=1e-12)
    assert output(boiler.total_fuel_input_power_channel) == pytest.approx(10000.0, rel=1e-12)

    _, output = BoilerStep.run(
        control_signal=1.0,
        operating_mode=HeatingMode.DOMESTIC_HOT_WATER.value,
        lift=20.0,
        space_heating_return=35.0,
        hot_water_return=45.0,
        set=70.0,
    )
    assert output(boiler.water_output_temperature_dhw_channel) == pytest.approx(65.0)
    assert output(boiler.total_fuel_input_power_channel) == pytest.approx(20000.0, rel=1e-12)


@pytest.mark.base
def test_the_space_heating_circuit_is_throttled_at_the_maximal_flow_temperature_only() -> None:
    """A space-heating charge capped at the hot-water set temperature would starve the heating; only 80 °C caps it."""
    boiler, output = BoilerStep.run(
        control_signal=1.0,
        operating_mode=HeatingMode.SPACE_HEATING.value,
        lift=20.0,
        space_heating_return=65.0,
        hot_water_return=50.0,
        set=70.0,
    )
    assert output(boiler.water_output_temperature_sh_channel) == 80.0
    assert output(boiler.water_output_mass_flow_dhw_channel) == 0.0
    assert output(boiler.water_output_temperature_dhw_channel) == 50.0


class PumpCase:
    """The pump of :class:`BoilerStep`'s boiler, a namespace for the pump tests' expected values."""

    #: The largest flow of a 20 kW boiler at 0.9 over the default 5 K: 18000 / (4180 * 5) = 0.861 kg/s.
    MAXIMAL_MASS_FLOW_IN_KG_PER_SECOND = 20000.0 * 0.9 / (hydronics.Water.SPECIFIC_HEAT_J_PER_KG_K * 5.0)

    #: Per circuit, the operating mode and the return inputs of a step: space heating on a 35 °C buffer return, hot
    #: water on a 50 °C tank return.
    CIRCUITS: Dict[str, Tuple[HeatingMode, float, float]] = {
        "space heating": (HeatingMode.SPACE_HEATING, 35.0, 50.0),
        "hot water": (HeatingMode.DOMESTIC_HOT_WATER, 35.0, 50.0),
    }

    @staticmethod
    def outputs_of(boiler: Any, circuit: str) -> Tuple[Any, Any, Any]:
        """Return the circuit's mass-flow, supply-temperature and thermal-power outputs."""
        if circuit == "space heating":
            return (
                boiler.water_output_mass_flow_sh_channel,
                boiler.water_output_temperature_sh_channel,
                boiler.thermal_output_power_sh_channel,
            )
        return (
            boiler.water_output_mass_flow_dhw_channel,
            boiler.water_output_temperature_dhw_channel,
            boiler.thermal_output_power_dhw_channel,
        )


@pytest.mark.base
@pytest.mark.parametrize("circuit", sorted(PumpCase.CIRCUITS))
@pytest.mark.parametrize(
    ("control_signal", "burner_power_in_watt", "heat_in_watt"), [(1.0, 20000.0, 18000.0), (0.05, 2000.0, 1200.0)]
)
def test_a_lift_close_to_zero_runs_the_pumps_largest_flow_and_books_the_burners_heat(
    circuit: str, control_signal: float, burner_power_in_watt: float, heat_in_watt: float
) -> None:
    """A boiler that sized its flow to a lift close to 0 K pumped hundreds of kg/s and booked heat no store got.

    The pellet and wood-chip twins' on/off controller asks for full power on a lift that shrinks to a few mK while
    the buffer reaches its set temperature: the boiler pumped 958 kg/s on the year's mean and booked 51.7 MWh of
    space heating for 11.0 MWh delivered (hisim-component-physics-4g9.63). The pump moves at most
    :attr:`PumpCase.MAXIMAL_MASS_FLOW_IN_KG_PER_SECOND`, so a 0.01 K lift is raised to the rise the heat gives at that
    flow: 5 K at full power (18000 W), 1/3 K at the 2 kW minimum (1200 W at 0.6). The heat the boiler books is the
    heat that flow carries, which is the burner's heat, and the fuel the commanded power. The expected heats are
    written out by hand, not read from the burner's efficiency law, so a fault in that law fails here too.
    """
    mode, space_heating_return_in_celsius, hot_water_return_in_celsius = PumpCase.CIRCUITS[circuit]
    boiler, output = BoilerStep.run(
        control_signal=control_signal,
        operating_mode=mode.value,
        lift=0.01,
        space_heating_return=space_heating_return_in_celsius,
        hot_water_return=hot_water_return_in_celsius,
    )
    mass_flow_channel, supply_channel, power_channel = PumpCase.outputs_of(boiler, circuit)
    is_space_heating = mode == HeatingMode.SPACE_HEATING
    return_in_celsius = space_heating_return_in_celsius if is_space_heating else hot_water_return_in_celsius
    assert output(mass_flow_channel) == pytest.approx(PumpCase.MAXIMAL_MASS_FLOW_IN_KG_PER_SECOND, rel=1e-12)
    assert output(supply_channel) == pytest.approx(
        return_in_celsius
        + heat_in_watt / (hydronics.Water.SPECIFIC_HEAT_J_PER_KG_K * PumpCase.MAXIMAL_MASS_FLOW_IN_KG_PER_SECOND),
        rel=1e-12,
    )
    assert output(power_channel) == hydronics.circuit_power_w(
        mass_flow_kg_per_s=output(mass_flow_channel), t_supply_c=output(supply_channel), t_return_c=return_in_celsius
    )
    assert output(power_channel) == pytest.approx(heat_in_watt, rel=1e-12)
    assert output(boiler.total_fuel_input_power_channel) == burner_power_in_watt


@pytest.mark.base
def test_a_lift_the_pump_carries_is_kept() -> None:
    """A boiler that raised every lift would overshoot the supply a modulating controller asks for.

    At half signal the burner turns 10 kW into 10000 * 0.7333 = 7333 W (0.6 at 2 kW rising linearly to 0.9 at
    20 kW, written out by hand), which the pump's largest flow carries over 2.04 K. A 3 K lift is above that, so the
    boiler runs it: the supply is the return plus 3 K, and the flow carries the heat over that lift.
    """
    boiler, output = BoilerStep.run(
        control_signal=0.5,
        operating_mode=HeatingMode.SPACE_HEATING.value,
        lift=3.0,
        space_heating_return=35.0,
        hot_water_return=50.0,
    )
    heat_in_watt = 10000.0 * (0.6 + (10000.0 - 2000.0) * (0.9 - 0.6) / (20000.0 - 2000.0))
    assert output(boiler.water_output_temperature_sh_channel) == 38.0
    assert output(boiler.water_output_mass_flow_sh_channel) == pytest.approx(
        heat_in_watt / (hydronics.Water.SPECIFIC_HEAT_J_PER_KG_K * 3.0), rel=1e-12
    )
    assert output(boiler.water_output_mass_flow_sh_channel) < PumpCase.MAXIMAL_MASS_FLOW_IN_KG_PER_SECOND


@pytest.mark.base
@pytest.mark.parametrize(
    ("control_signal", "expected_power_in_watt"),
    [(0.0, 2000.0), (0.05, 2000.0), (0.1, 2000.0), (0.5, 10000.0), (1.0, 20000.0)],
)
def test_the_burner_never_runs_below_its_minimal_power(control_signal: float, expected_power_in_watt: float) -> None:
    """A burner commanded below its band would burn less than it can modulate down to."""
    burner = generic_boiler.BurnerModulation(
        minimal_power_in_watt=2000.0, maximal_power_in_watt=20000.0, minimal_efficiency=0.6, maximal_efficiency=0.9
    )
    assert burner.commanded_power_in_watt(control_signal) == expected_power_in_watt


@pytest.mark.base
@pytest.mark.parametrize(
    ("burner_power_in_watt", "expected_efficiency"), [(1000.0, 0.6), (2000.0, 0.6), (11000.0, 0.75), (20000.0, 0.9)]
)
def test_the_combustion_efficiency_runs_linearly_across_the_band(
    burner_power_in_watt: float, expected_efficiency: float
) -> None:
    """An efficiency off its line, or below the minimum below the band, would bill a wrong fuel for the same heat."""
    burner = generic_boiler.BurnerModulation(
        minimal_power_in_watt=2000.0, maximal_power_in_watt=20000.0, minimal_efficiency=0.6, maximal_efficiency=0.9
    )
    assert burner.efficiency_at(burner_power_in_watt) == pytest.approx(expected_efficiency, rel=1e-12)


@pytest.mark.base
def test_a_burner_with_a_single_power_burns_at_its_minimal_efficiency() -> None:
    """A band of one power has no slope; dividing by its width would fail or return nonsense."""
    burner = generic_boiler.BurnerModulation(
        minimal_power_in_watt=10000.0, maximal_power_in_watt=10000.0, minimal_efficiency=0.85, maximal_efficiency=0.95
    )
    assert burner.efficiency_at(10000.0) == 0.85


@pytest.mark.base
def test_the_controller_states_its_hot_water_set_temperature() -> None:
    """A controller that published another set temperature than it charges to would cap the boiler elsewhere.

    The set temperature is the 60 °C warm-water aim plus the hysteresis, 70 °C with the default hysteresis.
    """
    from hisim import component as cp  # pylint: disable=import-outside-toplevel  # keeps the test module light
    from tests import functions_for_testing as fft  # pylint: disable=import-outside-toplevel  # as above

    config = GenericBoilerControllerConfig.preset_modulating("BoilerController").resolve(
        SizingContext(maximal_thermal_power_in_watt=20000, minimal_thermal_power_in_watt=2000)
    )
    config.with_domestic_hot_water_preparation = True
    controller = GenericBoilerController(SimulationParameters.one_day_only(2021, 900), config, DisplayConfig())
    fakes = []
    for component_input in controller.inputs:
        fake = cp.ComponentOutput(
            "Fake", component_input.field_name, lt.LoadTypes.ANY, lt.Units.ANY, component_id=ComponentID("Fake")
        )
        component_input.source_output = fake
        fakes.append(fake)
    fft.add_global_index_of_components([*fakes, controller])
    stsv = cp.SingleTimeStepValues(fft.get_number_of_outputs([*fakes, controller]))
    controller.i_simulate(0, stsv, False)
    set_temperature_in_celsius = stsv.values[controller.supply_temperature_set_for_dhw_in_celsius_channel.global_index]
    assert set_temperature_in_celsius == 60.0 + config.hysteresis_water_temperature_offset
    assert set_temperature_in_celsius == controller.hot_water_supply_temperature_set_in_celsius


@pytest.mark.base
@pytest.mark.parametrize(
    ("eff_th_min", "eff_th_max"),
    [
        (0.0, 0.9),  # no heat from a throttled step: the fuel divides by zero
        (-0.1, 0.9),
        (0.6, 0.0),
        (float("nan"), 0.9),
        (0.6, float("inf")),
    ],
)
def test_a_boiler_with_impossible_combustion_efficiencies_is_refused(eff_th_min: float, eff_th_max: float) -> None:
    """An efficiency of 0 or less, or one that is not finite, would divide by zero or book fuel without heat."""
    with pytest.raises(ValueError, match="eff_th_m"):
        generic_boiler.GenericBoilerConfig(
            component_id=ComponentID(name="Boiler"),
            energy_carrier=lt.LoadTypes.GAS,
            boiler_type=generic_boiler.BoilerType.CONDENSING,
            eff_th_min=eff_th_min,
            eff_th_max=eff_th_max,
        )


@pytest.mark.base
@pytest.mark.parametrize(("eff_th_min", "eff_th_max"), [(0.6, 0.9), (0.9, 0.9), (0.01, 1.0)])
def test_a_boiler_with_positive_combustion_efficiencies_is_accepted(eff_th_min: float, eff_th_max: float) -> None:
    """The refusal must keep the edges ``eff_th_min == eff_th_max`` and ``eff_th_max == 1``, which real boilers use."""
    config = generic_boiler.GenericBoilerConfig(
        component_id=ComponentID(name="Boiler"),
        energy_carrier=lt.LoadTypes.GAS,
        boiler_type=generic_boiler.BoilerType.CONDENSING,
        eff_th_min=eff_th_min,
        eff_th_max=eff_th_max,
    )
    assert (config.eff_th_min, config.eff_th_max) == (eff_th_min, eff_th_max)


@pytest.mark.base
@pytest.mark.parametrize("maximal_flow_temperature_in_celsius", [0.0, -10.0, float("nan"), float("inf")])
def test_a_boiler_without_a_finite_positive_maximal_flow_temperature_is_refused(
    maximal_flow_temperature_in_celsius: float,
) -> None:
    """A maximum below every return would leave the space heating idle without a message."""
    with pytest.raises(ValueError, match="maximal_flow_temperature_in_celsius"):
        generic_boiler.GenericBoilerConfig(
            component_id=ComponentID(name="Boiler"),
            energy_carrier=lt.LoadTypes.GAS,
            boiler_type=generic_boiler.BoilerType.CONDENSING,
            maximal_flow_temperature_in_celsius=maximal_flow_temperature_in_celsius,
        )


@pytest.mark.base
@pytest.mark.parametrize("minimal_lift_in_kelvin", [0.0, -5.0, float("nan"), float("inf")])
def test_a_boiler_without_a_finite_positive_minimal_lift_is_refused(minimal_lift_in_kelvin: float) -> None:
    """A minimal lift of 0 K would leave the pump's largest flow unbounded, and an infinite one would stop the flow."""
    with pytest.raises(ValueError, match="minimal_lift_at_maximal_power_in_kelvin"):
        generic_boiler.GenericBoilerConfig(
            component_id=ComponentID(name="Boiler"),
            energy_carrier=lt.LoadTypes.GAS,
            boiler_type=generic_boiler.BoilerType.CONDENSING,
            minimal_lift_at_maximal_power_in_kelvin=minimal_lift_in_kelvin,
        )


@pytest.mark.base
@pytest.mark.parametrize("maximal_power_in_watt", [0.0, -1000.0, float("nan")])
def test_a_boiler_without_a_finite_positive_maximal_power_is_refused_when_built(maximal_power_in_watt: float) -> None:
    """A boiler of 0 W has a pump of no largest flow, which divided by zero at the first step the boiler fired.

    The power is sizable, so the config may hold ``AUTO`` until it is resolved; the pump refuses the resolved value
    when the boiler is built.
    """
    config = generic_boiler.GenericBoilerConfig.preset_condensing_gas("Boiler")
    config.maximal_thermal_power_in_watt = maximal_power_in_watt
    config.minimal_thermal_power_in_watt = 0.0
    with pytest.raises(ValueError, match="maximal_thermal_power_in_watt"):
        generic_boiler.GenericBoiler(SimulationParameters.one_day_only(2021, 900), config)


@pytest.mark.base
@pytest.mark.parametrize(
    "preset",
    [
        generic_boiler.GenericBoilerConfig.preset_condensing_gas,
        generic_boiler.GenericBoilerConfig.preset_condensing_gas_12kw,
        generic_boiler.GenericBoilerConfig.preset_oil,
        generic_boiler.GenericBoilerConfig.preset_oil_12kw,
        generic_boiler.GenericBoilerConfig.preset_pellets,
        generic_boiler.GenericBoilerConfig.preset_wood_chips,
        generic_boiler.GenericBoilerConfig.preset_hydrogen,
    ],
)
def test_every_boiler_preset_passes_the_config_checks(
    preset: Callable[[str], generic_boiler.GenericBoilerConfig],
) -> None:
    """A preset that the efficiency or flow-temperature checks refused could not be built at all."""
    config = preset("Boiler")
    assert 0.0 < config.eff_th_min <= config.eff_th_max <= 1.0  # the presets also keep the stricter physical bounds
    assert config.maximal_flow_temperature_in_celsius > 0.0


class BurnerSequence:
    """A boiler of one fuel on fake inputs, stepped through a sequence of commands the way the simulator steps it.

    Every step commits the previous step's state (``i_save_state``), then runs two passes that each start from it
    (``i_restore_state`` and ``i_simulate``), so a decision that depended on an earlier pass of the same step shows
    as a difference between the passes.
    """

    #: The boiler's maximal burner power, in W; a pellet or wood-chip burner modulates down to a twelfth of it.
    MAXIMAL_POWER_IN_WATT = 12000.0

    #: The space-heating buffer's return, in °C, and the hot-water tank's.
    SPACE_HEATING_RETURN_IN_CELSIUS = 35.0
    HOT_WATER_RETURN_IN_CELSIUS = 50.0

    #: The command that asks an on/off burner to heat the buffer: full signal, space heating, a 5 K lift.
    ASK_TO_BURN: Tuple[float, float, float] = (1.0, float(HeatingMode.SPACE_HEATING.value), 5.0)
    #: The command that asks it to stop while its controller stays in space heating: the buffer is above its set
    #: temperature, so the lift is negative.
    ASK_TO_STOP_IN_SPACE_HEATING: Tuple[float, float, float] = (1.0, float(HeatingMode.SPACE_HEATING.value), -2.0)
    #: The command of a controller that switched off.
    ASK_TO_STOP: Tuple[float, float, float] = (0.0, float(HeatingMode.OFF.value), 0.0)

    @classmethod
    def fuel_burnt_per_step(
        cls,
        preset: Callable[[str], generic_boiler.GenericBoilerConfig],
        seconds_per_timestep: int,
        commands: List[Tuple[float, float, float]],
        space_heating_ratios: Optional[List[float]] = None,
    ) -> Tuple[Any, List[float], List[float], List[float]]:
        """Step a fresh boiler of ``preset`` through ``commands``; return it, its fuel power, buffer heat and hot water.

        Args:
            preset: The boiler's preset, resolved to :attr:`MAXIMAL_POWER_IN_WATT`.
            seconds_per_timestep: The step length, in s.
            commands: Per step, the control signal, the operating mode's value and the lift in K.
            space_heating_ratios: Per step, the space-heating part-load ratio the controller commands; 1, the whole
                step, in every step if not given.

        Returns:
            The boiler, and per step its fuel power, its space-heating heat and its hot-water heat, each in W.
        """
        from hisim import component as cp  # pylint: disable=import-outside-toplevel  # keeps the test module light
        from tests import functions_for_testing as fft  # pylint: disable=import-outside-toplevel  # as above

        config = preset("Boiler").resolve(
            SizingContext(heating_load_in_watt=cls.MAXIMAL_POWER_IN_WATT / 1.1, number_of_apartments=1)
        )
        parameters = SimulationParameters.one_day_only(2021, seconds_per_timestep)
        boiler = generic_boiler.GenericBoiler(parameters, config)
        channels = [
            (boiler.control_signal_channel, lt.LoadTypes.ANY, lt.Units.PERCENT),
            (boiler.operating_mode_channel, lt.LoadTypes.ANY, lt.Units.ANY),
            (boiler.temperature_delta_channel, lt.LoadTypes.TEMPERATURE, lt.Units.KELVIN),
            (boiler.water_input_temperature_sh_channel, lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS),
            (boiler.water_input_temperature_dhw_channel, lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS),
            (boiler.supply_temperature_set_for_dhw_in_celsius_channel, lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS),
            (boiler.dhw_part_load_command.ratio_channel, lt.LoadTypes.ANY, lt.Units.FRACTION),
            (boiler.space_heating_part_load_command.ratio_channel, lt.LoadTypes.ANY, lt.Units.FRACTION),
            (boiler.control_signal_for_space_heating_remainder_channel, lt.LoadTypes.ANY, lt.Units.PERCENT),
            (boiler.temperature_delta_for_space_heating_remainder_channel, lt.LoadTypes.TEMPERATURE, lt.Units.KELVIN),
        ]
        fakes = []
        for number, (channel, load_type, unit) in enumerate(channels):
            fake = cp.ComponentOutput(
                f"Fake{number}", f"Fake{number}", load_type, unit, component_id=ComponentID(f"Fake{number}")
            )
            channel.source_output = fake
            fakes.append(fake)
        fft.add_global_index_of_components([*fakes, boiler])
        fuel_power_in_watt: List[float] = []
        space_heating_heat_in_watt: List[float] = []
        hot_water_heat_in_watt: List[float] = []
        ratios = space_heating_ratios if space_heating_ratios is not None else [1.0] * len(commands)
        for timestep, (control_signal, operating_mode, lift_in_kelvin) in enumerate(commands):
            boiler.i_save_state()
            passes = []
            for _ in range(2):
                stsv = cp.SingleTimeStepValues(fft.get_number_of_outputs([*fakes, boiler]))
                for fake, value in zip(
                    fakes,
                    (
                        control_signal,
                        operating_mode,
                        lift_in_kelvin,
                        cls.SPACE_HEATING_RETURN_IN_CELSIUS,
                        cls.HOT_WATER_RETURN_IN_CELSIUS,
                        70.0,
                        1.0,
                        ratios[timestep],
                        0.0,
                        0.0,
                    ),
                ):
                    stsv.set_output_value(fake, value)
                boiler.i_restore_state()
                boiler.i_simulate(timestep, stsv, False)
                passes.append(list(stsv.values))
            assert passes[0] == passes[1], f"the second pass of step {timestep} decided differently"
            fuel_power_in_watt.append(float(stsv.values[boiler.total_fuel_input_power_channel.global_index]))
            space_heating_heat_in_watt.append(float(stsv.values[boiler.thermal_output_power_sh_channel.global_index]))
            hot_water_heat_in_watt.append(float(stsv.values[boiler.thermal_output_power_dhw_channel.global_index]))
        return boiler, fuel_power_in_watt, space_heating_heat_in_watt, hot_water_heat_in_watt

    @staticmethod
    def lit_steps(fuel_power_in_watt: List[float]) -> List[bool]:
        """Return per step whether the burner burnt fuel."""
        return [power > 0.0 for power in fuel_power_in_watt]


@pytest.mark.base
@pytest.mark.parametrize(
    ("preset", "minimum_firing_time_in_seconds", "minimum_pause_in_seconds"),
    [
        (generic_boiler.GenericBoilerConfig.preset_pellets, 1800, 900),
        (generic_boiler.GenericBoilerConfig.preset_wood_chips, 3600, 1800),
    ],
)
@pytest.mark.parametrize("seconds_per_timestep", [60, 900])
def test_a_wood_burner_burns_its_minimum_firing_time_and_rests_its_minimum_pause(
    preset: Callable[[str], generic_boiler.GenericBoilerConfig],
    minimum_firing_time_in_seconds: int,
    minimum_pause_in_seconds: int,
    seconds_per_timestep: int,
) -> None:
    """A pellet or wood-chip burner that stopped whenever its controller asked lit and went out about once a minute.

    The on/off controller keeps its space-heating mode while the buffer sits near its set temperature and asks for
    fire whenever the buffer is below it: the pellet twin's burner started 425 times a day in a January week at 60 s,
    for 1.25 minutes each. The burner now burns for its minimum firing time once lit (30 minutes for pellets, 60 for
    wood chips) although asked to stop after one step, sends that heat to the buffer, and then stays out for its
    minimum pause (15 and 30 minutes) although asked to burn at once. The sequence is: one step asked to burn, then
    asked to stop until the minimum firing time is over, then asked to burn again.
    """
    firing_steps = minimum_firing_time_in_seconds // seconds_per_timestep
    pause_steps = minimum_pause_in_seconds // seconds_per_timestep
    commands = (
        [BurnerSequence.ASK_TO_BURN]
        + [BurnerSequence.ASK_TO_STOP_IN_SPACE_HEATING] * (firing_steps - 1)
        + [BurnerSequence.ASK_TO_STOP]
        + [BurnerSequence.ASK_TO_BURN] * pause_steps
    )
    boiler, fuel_power_in_watt, space_heating_heat_in_watt, _ = BurnerSequence.fuel_burnt_per_step(
        preset, seconds_per_timestep, commands
    )
    assert BurnerSequence.lit_steps(fuel_power_in_watt) == [True] * firing_steps + [False] * pause_steps + [True]
    # held lit in space heating at the commanded full power, the buffer taking the heat at the pump's largest flow;
    # at full power the burner runs at its maximal efficiency
    assert fuel_power_in_watt[1] == pytest.approx(BurnerSequence.MAXIMAL_POWER_IN_WATT)
    maximal_power_in_watt = BurnerSequence.MAXIMAL_POWER_IN_WATT
    assert space_heating_heat_in_watt[1] == pytest.approx(maximal_power_in_watt * boiler.config.eff_th_max)
    assert boiler.burner_times == generic_boiler.BurnerTimes(
        minimum_firing_time_in_seconds=minimum_firing_time_in_seconds,
        minimum_pause_in_seconds=minimum_pause_in_seconds,
    )


@pytest.mark.base
def test_a_burner_held_lit_after_its_controller_switched_off_burns_at_its_minimal_power_into_the_buffer() -> None:
    """A held burner whose controller sends no fire burns at its minimal power, a twelfth of the maximal one.

    The controller's off command carries a control signal of 0, which commands the burner's minimal power; the heat
    goes to the space-heating circuit and no hot water is charged.
    """
    commands = [BurnerSequence.ASK_TO_BURN, BurnerSequence.ASK_TO_STOP]
    boiler, fuel_power_in_watt, space_heating_heat_in_watt, hot_water_heat_in_watt = BurnerSequence.fuel_burnt_per_step(
        generic_boiler.GenericBoilerConfig.preset_pellets, 900, commands
    )
    minimal_power_in_watt = BurnerSequence.MAXIMAL_POWER_IN_WATT / 12
    assert fuel_power_in_watt[1] == pytest.approx(minimal_power_in_watt)
    # at its minimal power the burner runs at its minimal efficiency
    assert space_heating_heat_in_watt[1] == pytest.approx(minimal_power_in_watt * boiler.config.eff_th_min)
    assert hot_water_heat_in_watt[1] == 0.0


@pytest.mark.base
def test_a_held_burner_burns_its_whole_step_although_its_controller_commands_no_space_heating_part_load() -> None:
    """A burner held lit is not part-loaded: it burns the whole step even when the commanded ratio is 0.

    At 900 s a pellet burner that fired one step and whose controller then switched off may receive a space-heating
    part-load ratio of 0 above 600 s. If the held firing applied that ratio, the burner would burn nothing, go out and
    break its 30-minute minimum firing time. It burns the second step in full, at least at its minimal power, and
    reports a space-heating ratio run of 1.
    """
    commands = [BurnerSequence.ASK_TO_BURN, BurnerSequence.ASK_TO_STOP]
    boiler, fuel_power_in_watt, space_heating_heat_in_watt, _ = BurnerSequence.fuel_burnt_per_step(
        generic_boiler.GenericBoilerConfig.preset_pellets, 900, commands, space_heating_ratios=[1.0, 0.0]
    )
    minimal_power_in_watt = BurnerSequence.MAXIMAL_POWER_IN_WATT / 12
    assert fuel_power_in_watt[1] == pytest.approx(minimal_power_in_watt)
    assert space_heating_heat_in_watt[1] == pytest.approx(minimal_power_in_watt * boiler.config.eff_th_min)
    assert boiler.burner_state.is_lit


@pytest.mark.base
@pytest.mark.parametrize(
    "preset",
    [
        generic_boiler.GenericBoilerConfig.preset_condensing_gas,
        generic_boiler.GenericBoilerConfig.preset_oil,
        generic_boiler.GenericBoilerConfig.preset_hydrogen,
    ],
)
def test_a_modulating_burner_stops_and_lights_when_asked(
    preset: Callable[[str], generic_boiler.GenericBoilerConfig],
) -> None:
    """A gas, oil or hydrogen burner has no minimum times: holding it would move every modulating twin's results."""
    commands = [
        BurnerSequence.ASK_TO_BURN,
        BurnerSequence.ASK_TO_STOP_IN_SPACE_HEATING,
        BurnerSequence.ASK_TO_BURN,
        BurnerSequence.ASK_TO_STOP,
        BurnerSequence.ASK_TO_BURN,
    ]
    _, fuel_power_in_watt, _, _ = BurnerSequence.fuel_burnt_per_step(preset, 60, commands)
    assert BurnerSequence.lit_steps(fuel_power_in_watt) == [True, False, True, False, True]


@pytest.mark.base
@pytest.mark.parametrize(
    ("seconds", "seconds_per_timestep", "expected_seconds"),
    [(1800, 60, 1800), (1800, 900, 1800), (1500, 900, 900), (600, 900, 0), (1800, 3600, 0)],
)
def test_a_minimum_burner_time_rounds_down_to_whole_steps(
    seconds: float, seconds_per_timestep: float, expected_seconds: float
) -> None:
    """A minimum time rounded up would stretch a burn or a pause beyond its configured length at a coarse step.

    The controller rounds its own minimum times down to whole steps; the burner counts its times the same way, so
    30 minutes have no effect at hourly steps.
    """
    assert generic_boiler.BurnerTimes.whole_steps_in_seconds(seconds, seconds_per_timestep) == expected_seconds


@pytest.mark.base
@pytest.mark.parametrize(
    ("energy_carrier", "seconds_per_timestep", "expected_names"),
    [
        (lt.LoadTypes.PELLETS, 60.0, []),
        (lt.LoadTypes.PELLETS, 900.0, []),
        (lt.LoadTypes.PELLETS, 1200.0, ["minimum pause"]),
        (lt.LoadTypes.PELLETS, 3600.0, ["minimum firing time", "minimum pause"]),
        (lt.LoadTypes.WOOD_CHIPS, 3600.0, ["minimum pause"]),
        (lt.LoadTypes.GAS, 3600.0, []),
    ],
)
def test_a_burner_names_the_minimum_times_shorter_than_one_step(
    energy_carrier: lt.LoadTypes, seconds_per_timestep: float, expected_names: List[str]
) -> None:
    """A minimum time shorter than one step rounds down to no minimum; the boiler warns about each such time.

    Without the warning a pellet burner at hourly steps would light and go out as asked, with nothing to tell why
    its 30 minutes of minimum firing time had no effect. A gas burner has no minimum times, so nothing to warn about.
    """
    burner_times = generic_boiler.BurnerTimes.of_fuel(energy_carrier)
    assert burner_times.times_shorter_than_a_step(seconds_per_timestep) == expected_names


@pytest.mark.base
def test_a_burner_with_a_minimum_firing_time_and_no_minimal_power_is_refused() -> None:
    """A held burner burns at least its minimal power; at 0 W it carries no heat, and its circuit divided by 0.

    A pellet burner held lit after its controller switched off is commanded a signal of 0, which is its minimal
    power. With a minimal power of 0 W the pump's lift was 0 K and the flow ``P / (c lift)`` raised a
    ZeroDivisionError at the first held step; the boiler now refuses the configuration when it is built.
    """
    config = generic_boiler.GenericBoilerConfig.preset_pellets("Boiler").resolve(
        SizingContext(heating_load_in_watt=BurnerSequence.MAXIMAL_POWER_IN_WATT / 1.1, number_of_apartments=1)
    )
    config = dataclasses.replace(config, minimal_thermal_power_in_watt=0.0)
    with pytest.raises(ValueError, match="minimal_thermal_power_in_watt must be above 0"):
        generic_boiler.GenericBoiler(SimulationParameters.one_day_only(2021, 900), config)


@pytest.mark.base
def test_a_space_heating_charge_at_part_load_carries_the_ratio_of_its_flow_heat_and_fuel() -> None:
    """A space-heating charge at a quarter of the step pumps, carries and burns a quarter, at the full-load supply.

    20 kW at full signal, a 10 K lift and a 35 °C return: at the ratio 0.25 the boiler publishes a quarter of the
    whole step's flow at the same supply, a quarter of its heat and fuel, and reports 0.25 for space heating and 0 for
    hot water.
    """
    full_boiler, full = BoilerStep.run(
        control_signal=1.0, operating_mode=HeatingMode.SPACE_HEATING.value, lift=10.0, space_heating_return=35.0
    )
    boiler, part = BoilerStep.run(
        control_signal=1.0,
        operating_mode=HeatingMode.SPACE_HEATING.value,
        lift=10.0,
        space_heating_return=35.0,
        space_heating_ratio=0.25,
    )
    assert part(boiler.water_output_temperature_sh_channel) == full(full_boiler.water_output_temperature_sh_channel)
    for name in (
        "water_output_mass_flow_sh_channel",
        "thermal_output_power_sh_channel",
        "total_fuel_input_power_channel",
    ):
        assert part(getattr(boiler, name)) == pytest.approx(0.25 * full(getattr(full_boiler, name)), rel=1e-12), name
    assert part(boiler.space_heating_part_load_command.ratio_run_channel) == 0.25
    assert part(boiler.dhw_part_load_command.ratio_run_channel) == 0.0


@pytest.mark.base
def test_the_rest_of_a_hot_water_step_charges_the_buffer_and_both_fuels_add_up() -> None:
    """A hot-water charge at 0.6 of the step and a space-heating charge at 0.4 fire in one step; their fuels add up.

    The hot-water charge runs as alone; the space-heating charge runs at the remainder's signal and lift, as a
    space-heating step would at its ratio; the boiler's fuel power is the sum of both, and it reports both ratios.
    """
    _, hot_water_alone = BoilerStep.run(
        control_signal=1.0,
        operating_mode=HeatingMode.DOMESTIC_HOT_WATER.value,
        lift=20.0,
        hot_water_return=45.0,
        ratio=0.6,
        space_heating_ratio=0.0,
    )
    _, space_heating_alone = BoilerStep.run(
        control_signal=1.0,
        operating_mode=HeatingMode.SPACE_HEATING.value,
        lift=10.0,
        space_heating_return=35.0,
        space_heating_ratio=0.4,
    )
    boiler, both = BoilerStep.run(
        control_signal=1.0,
        operating_mode=HeatingMode.DOMESTIC_HOT_WATER.value,
        lift=20.0,
        hot_water_return=45.0,
        space_heating_return=35.0,
        ratio=0.6,
        space_heating_ratio=0.4,
        remainder_signal=1.0,
        remainder_lift=10.0,
    )
    assert both(boiler.thermal_output_power_dhw_channel) == pytest.approx(
        hot_water_alone(boiler.thermal_output_power_dhw_channel)
    )
    assert both(boiler.thermal_output_power_sh_channel) == pytest.approx(
        space_heating_alone(boiler.thermal_output_power_sh_channel)
    )
    assert both(boiler.total_fuel_input_power_channel) == pytest.approx(
        hot_water_alone(boiler.total_fuel_input_power_channel)
        + space_heating_alone(boiler.total_fuel_input_power_channel)
    )
    assert both(boiler.dhw_part_load_command.ratio_run_channel) == 0.6
    assert both(boiler.space_heating_part_load_command.ratio_run_channel) == 0.4


@pytest.mark.base
def test_a_hot_water_step_without_a_remainder_command_leaves_the_buffer_alone() -> None:
    """Without the remainder's signal and lift a space-heating ratio in a hot-water step fires nothing."""
    boiler, output = BoilerStep.run(
        control_signal=1.0,
        operating_mode=HeatingMode.DOMESTIC_HOT_WATER.value,
        lift=20.0,
        hot_water_return=45.0,
        ratio=0.6,
        space_heating_ratio=0.4,
    )
    assert output(boiler.thermal_output_power_sh_channel) == 0.0
    assert output(boiler.space_heating_part_load_command.ratio_run_channel) == 0.0
