"""Test for generic pv system."""

import pathlib
from typing import Any, Dict, Optional
import pytest
import yaml
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
            "set_heating_threshold_outside_temperature_in_celsius": 15,
        }
    )
    testee.controller_mode = operating_mode
    testee.warm_water_temperature_aim_in_celsius = 60
    testee.config.hysteresis_water_temperature_offset = 5

    daily_avg_outside_temperature = 10
    heating_flow_temperature = 55
    timestep = 5

    """ WHEN """
    _, _ = testee.determine_operating_mode(
        daily_avg_outside_temperature,
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
    simulationparameters = sim.SimulationParameters.full_year(
        year=2021, seconds_per_timestep=60
    )
    config = GenericBoilerControllerConfig.preset_on_off("OnOffBoilerController").resolve(
        SizingContext(maximal_thermal_power_in_watt=2500, minimal_thermal_power_in_watt=1000)
    )
    config.minimum_runtime_in_seconds = config_overwrite.get(
        "minimum_runtime_in_seconds", 0
    )
    config.minimum_resting_time_in_seconds = config_overwrite.get(
        "minimum_resting_time_in_seconds", 0
    )
    config.with_domestic_hot_water_preparation = config_overwrite.get(
        "with_domestic_hot_water_preparation",
        config.with_domestic_hot_water_preparation,
    )
    config.set_heating_threshold_outside_temperature_in_celsius = config_overwrite.get(
        "set_heating_threshold_outside_temperature_in_celsius",
        config.set_heating_threshold_outside_temperature_in_celsius,
    )
    config.hysteresis_water_temperature_offset = 0
    testee = GenericBoilerController(
        simulationparameters,
        config,
        DisplayConfig(),
    )
    return testee


@pytest.mark.base
@pytest.mark.parametrize(
    [
        "daily_avg_outside_temperature_in_celsius",
        "set_heating_threshold_temperature_in_celsius",
        "expected_mode",
    ],
    [
        # Equality: exactly at threshold → "on" (cold enough for heating)
        (10.0, 10.0, "on"),
        # Below threshold → "on"
        (5.0, 10.0, "on"),
        # Above threshold → "off"
        (15.0, 10.0, "off"),
        # No threshold set → "on"
        (5.0, None, "on"),
        (15.0, None, "on"),
        # Equality with negative temperatures
        (0.0, 0.0, "on"),
        # Slightly above threshold
        (10.1, 10.0, "off"),
        # Slightly below threshold
        (9.9, 10.0, "on"),
    ],
)
def test_determine_summer_heating_mode_handles_equality_case(
    daily_avg_outside_temperature_in_celsius: float,
    set_heating_threshold_temperature_in_celsius: Optional[float],
    expected_mode: str,
):
    """Test determine_summer_heating_mode with emphasis on the equality boundary case.

    The original code used strict '>' and '<' comparisons, leaving an unreachable
    else branch that raised ValueError when temperatures were exactly equal.
    This test ensures the equality case returns 'on' (cold enough for heating).
    """
    from hisim.components.dual_circuit_system import DiverterValve

    result = DiverterValve.determine_summer_heating_mode(
        daily_avg_outside_temperature_in_celsius,
        set_heating_threshold_temperature_in_celsius,
    )
    assert result == expected_mode


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
    config = preset.resolve(
        SizingContext(
            maximal_thermal_power_in_watt=2500, minimal_thermal_power_in_watt=1000
        )
    )

    assert config.component_id.name == "X"
    assert config.is_modulating is expected_is_modulating
    assert config.minimum_runtime_in_seconds == expected_runtime
    assert config.minimum_resting_time_in_seconds == expected_resting
    assert config.set_temperature_difference_for_full_power == 5.0
    assert config.hysteresis_water_temperature_offset == 10.0
    assert config.set_heating_threshold_outside_temperature_in_celsius == 16.0
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
    twin_path = (
        pathlib.Path(__file__).resolve().parents[1] / "energy_systems" / twin_file_name
    )
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


def boiler_with_fake_inputs() -> Any:
    """A condensing gas boiler of 20 kW whose five inputs read fake outputs, the step values and the fakes.

    The fakes are, in order: control signal, operating mode, lift, space-heating return, hot-water return.
    """
    from hisim import component as cp  # pylint: disable=import-outside-toplevel
    from tests import functions_for_testing as fft  # pylint: disable=import-outside-toplevel

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


@pytest.mark.base
def test_the_hot_water_circuit_books_the_heat_its_water_carries_from_the_tanks_step_mean() -> None:
    """In hot-water mode the boiler supplies the tank's step mean plus the lift and books m c (T_sup - T_ret).

    20 kW at full signal, a 20 K lift and a 47.3 °C return: the boiler pumps P / (c 20 K) and supplies 67.3 °C, and
    the heat it books is the heat that water carries, which is its thermal power.
    """
    from hisim import hydronics  # pylint: disable=import-outside-toplevel

    boiler, stsv, fakes = boiler_with_fake_inputs()
    for fake, value in zip(fakes, [1.0, HeatingMode.DOMESTIC_HOT_WATER.value, 20.0, 35.0, 47.3]):
        stsv.set_output_value(fake, value)
    boiler.i_simulate(0, stsv, False)

    def output(channel: Any) -> float:
        return float(stsv.values[channel.global_index])

    mass_flow = output(boiler.water_output_mass_flow_dhw_channel)
    supply = output(boiler.water_output_temperature_dhw_channel)
    booked = output(boiler.thermal_output_power_dhw_channel)
    assert supply == pytest.approx(67.3)
    assert booked == hydronics.circuit_power_w(mass_flow, supply, 47.3)
    assert booked == pytest.approx(20000.0 * boiler.max_combustion_efficiency, rel=1e-12)
    assert output(boiler.energy_demand_dhw_channel) == pytest.approx(20000.0 * 900 / 3600)


@pytest.mark.base
def test_a_hot_water_charge_without_a_lift_does_not_fire() -> None:
    """With no lift the circuit moves no water: no heat, no fuel, no combustion loss on that step."""
    boiler, stsv, fakes = boiler_with_fake_inputs()
    for fake, value in zip(fakes, [1.0, HeatingMode.DOMESTIC_HOT_WATER.value, 0.0, 35.0, 71.0]):
        stsv.set_output_value(fake, value)
    boiler.i_simulate(0, stsv, False)
    for channel in (
        boiler.water_output_mass_flow_dhw_channel,
        boiler.thermal_output_power_dhw_channel,
        boiler.energy_demand_dhw_channel,
        boiler.combustion_heat_loss_channel,
    ):
        assert stsv.values[channel.global_index] == 0.0
    assert stsv.values[boiler.water_output_temperature_dhw_channel.global_index] == 71.0


@pytest.mark.base
def test_a_charge_above_the_maximal_flow_temperature_is_throttled_and_burns_what_its_heat_needs() -> None:
    """A 30 K lift on a 70 °C return stops at the 80 °C maximum; the heat follows from the flow, the fuel from it.

    The pump keeps the flow of the unthrottled charge, P_th / (c 30 K), so the water carries a third of P_th; the
    burner burns what that heat needs at its own efficiency (the inverse of the modulation law), and the loss is
    the fuel the heat does not take.
    """
    from hisim import hydronics  # pylint: disable=import-outside-toplevel

    boiler, stsv, fakes = boiler_with_fake_inputs()
    for fake, value in zip(fakes, [1.0, HeatingMode.DOMESTIC_HOT_WATER.value, 30.0, 35.0, 70.0]):
        stsv.set_output_value(fake, value)
    boiler.i_simulate(0, stsv, False)

    def output(channel: Any) -> float:
        return float(stsv.values[channel.global_index])

    mass_flow = output(boiler.water_output_mass_flow_dhw_channel)
    booked = output(boiler.thermal_output_power_dhw_channel)
    fuel = output(boiler.total_fuel_input_power_channel)
    assert output(boiler.water_output_temperature_dhw_channel) == 80.0
    assert booked == hydronics.circuit_power_w(mass_flow, 80.0, 70.0)
    assert booked == pytest.approx(20000.0 * boiler.max_combustion_efficiency / 3.0, rel=1e-12)
    assert fuel * boiler.combustion_efficiency_at_burner_power(fuel) == pytest.approx(booked, rel=1e-12)
    assert output(boiler.energy_demand_dhw_channel) == pytest.approx(fuel * 900 / 3600)
    assert output(boiler.combustion_heat_loss_channel) == pytest.approx(fuel - booked)


@pytest.mark.base
@pytest.mark.parametrize("thermal_power_in_watt", [500.0, 1200.0, 5000.0, 12000.0, 18000.0])
def test_the_fuel_law_inverts_the_modulation_law(thermal_power_in_watt: float) -> None:
    """Burning the returned fuel at its efficiency yields the asked heat, below the minimum at the minimum's."""
    boiler, _, _ = boiler_with_fake_inputs()
    fuel, efficiency = boiler.fuel_power_for_thermal_power(thermal_power_in_watt)
    assert efficiency == boiler.combustion_efficiency_at_burner_power(fuel)
    assert fuel * efficiency == pytest.approx(thermal_power_in_watt, rel=1e-12)
