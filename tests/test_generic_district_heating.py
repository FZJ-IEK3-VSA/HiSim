"""District heating test."""

import pytest

from hisim.components.generic_district_heating import (
    DistrictHeating,
    DistrictHeatingConfig,
    DistrictHeatingController,
    DistrictHeatingControllerConfig,
    HeatingMode,
)
from hisim import simulator as sim


@pytest.mark.base
@pytest.mark.parametrize(
    [
        "with_warm_water",
        "daily_avg_outside_temperature_deg_c",
        "sh_current_water_temperature",
        "sh_target_water_temperature",
        "dhw_input_temperature",
        "expected_mode",
    ],
    [
        (False, 0, 20, 25, None, HeatingMode.SPACE_HEATING),
        (False, 0, 25, 25, None, HeatingMode.OFF),
        (False, 0, 25, 23, None, HeatingMode.OFF),
        (False, 20, 20, 25, None, HeatingMode.OFF),
        (True, 0, 15, 30, 30, HeatingMode.DOMESTIC_HOT_WATER),
        (True, 20, 15, 30, 30, HeatingMode.DOMESTIC_HOT_WATER),
        (True, 0, 25, 25, 50, HeatingMode.OFF),
        (True, 0, 25, 30, 50, HeatingMode.SPACE_HEATING),
        (True, 20, 25, 30, 50, HeatingMode.OFF),
    ],
)
def test_controller_determine_operating_mode(
    with_warm_water,
    daily_avg_outside_temperature_deg_c,
    sh_current_water_temperature,
    sh_target_water_temperature,
    dhw_input_temperature,
    expected_mode,
):
    """Test determination of operating mode."""

    testee = given_default_controller_testee(with_warm_water=with_warm_water)

    testee.determine_operating_mode(
        daily_avg_outside_temperature_deg_c,
        sh_current_water_temperature,
        sh_target_water_temperature,
        dhw_input_temperature,
    )

    assert testee.controller_mode == expected_mode


@pytest.mark.base
@pytest.mark.parametrize(
    [
        "connected_load_w",
        "water_input_temperature_deg_c",
        "delta_temperature_needed_in_celsius",
        "expected_thermal_power_delivered_w",
        "expected_thermal_energy_delivered_in_watt_hour",
        "expected_water_output_temperature_deg_c",
        "expected_water_mass_flow_rate_in_kg_per_s",
    ],
    [
        (15000, 50, 20, 15000, 15000 / 60, 70, 0.17942583732057416),
        (20000, 50, 20, 20000, 20000 / 60, 70, 0.23923444976076555),
        (15000, 70, 0, 0, 0, 70, 0),
        (20000, 70, 0, 0, 0, 70, 0),
    ],
)
def test_component_get_dhw_outputs(
    connected_load_w,
    water_input_temperature_deg_c,
    delta_temperature_needed_in_celsius,
    expected_thermal_power_delivered_w,
    expected_thermal_energy_delivered_in_watt_hour,
    expected_water_output_temperature_deg_c,
    expected_water_mass_flow_rate_in_kg_per_s,
):
    """Test calculation of dhw outputs."""

    testee = given_default_component_testee()
    testee.config.connected_load_w = connected_load_w

    (
        thermal_power_delivered_w,
        thermal_energy_delivered_in_watt_hour,
        water_output_temperature_deg_c,
        water_mass_flow_rate_in_kg_per_s
    ) = testee._calculate_dhw_outputs(  # pylint: disable=protected-access
        water_input_temperature_deg_c,
        delta_temperature_needed_in_celsius,
    )

    assert thermal_power_delivered_w == expected_thermal_power_delivered_w
    assert thermal_energy_delivered_in_watt_hour == expected_thermal_energy_delivered_in_watt_hour
    assert water_output_temperature_deg_c == expected_water_output_temperature_deg_c
    assert water_mass_flow_rate_in_kg_per_s == expected_water_mass_flow_rate_in_kg_per_s


@pytest.mark.base
@pytest.mark.parametrize(
    [
        "connected_load_w",
        "water_input_temperature_deg_c",
        "delta_temperature_needed_in_celsius",
        "water_mass_flow_rate_in_kg_per_s",
        "expected_thermal_power_delivered_w",
        "expected_thermal_energy_delivered_in_watt_hour",
        "expected_water_output_temperature_deg_c",
    ],
    [
        (20000, 20, 5, 0.5, 0.5 * 4180 * 5, 0.5 * 4180 * 5 / 60, 25),
        (10000, 20, 5, 0.5, 10000, 10000 / 60, 24.784688995215312),  # max thermal power is limited
        (20000, 20, 0, 0.5, 0, 0, 20),
    ],
)
def test_component_get_space_heating_outputs(
    connected_load_w,
    water_input_temperature_deg_c,
    delta_temperature_needed_in_celsius,
    water_mass_flow_rate_in_kg_per_s,
    expected_thermal_power_delivered_w,
    expected_thermal_energy_delivered_in_watt_hour,
    expected_water_output_temperature_deg_c,
):
    """Test calculation of space heating outputs."""

    testee = given_default_component_testee()
    testee.config.connected_load_w = connected_load_w

    (
        thermal_power_delivered_w,
        thermal_energy_delivered_in_watt_hour,
        water_output_temperature_deg_c,
    ) = testee._calculate_space_heating_outputs(  # pylint: disable=protected-access
        water_mass_flow_rate_in_kg_per_s,
        delta_temperature_needed_in_celsius,
        water_input_temperature_deg_c,
    )

    assert thermal_power_delivered_w == expected_thermal_power_delivered_w
    assert thermal_energy_delivered_in_watt_hour == expected_thermal_energy_delivered_in_watt_hour
    assert water_output_temperature_deg_c == expected_water_output_temperature_deg_c


def given_default_controller_testee(
    with_warm_water: bool = False,
) -> DistrictHeatingController:
    """Create default controller testee."""

    simulation_parameters = sim.SimulationParameters.full_year(
        year=2021, seconds_per_timestep=60
    )
    config = DistrictHeatingControllerConfig.get_default_district_heating_controller_config(
        with_domestic_hot_water_preparation=with_warm_water
    )
    return DistrictHeatingController(simulation_parameters, config)


def given_default_component_testee(with_warm_water: bool = False) -> DistrictHeating:
    """Create default component testee."""

    simulation_parameters = sim.SimulationParameters.full_year(
        year=2021, seconds_per_timestep=60
    )
    config = DistrictHeatingConfig.get_default_district_heating_config(with_domestic_hot_water_preparation=with_warm_water)

    return DistrictHeating(simulation_parameters, config)
