"""Test for generic pv system."""

from typing import Any, Dict, Optional
import pytest
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
    config.with_domestic_hot_water_preparation = True
    config.minimum_runtime_in_seconds = config_overwrite.get(
        "minimum_runtime_in_seconds", 0
    )
    config.minimum_resting_time_in_seconds = config_overwrite.get(
        "minimum_resting_time_in_seconds", 0
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
