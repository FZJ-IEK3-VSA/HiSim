"""District heating test."""

from __future__ import annotations

import pytest

from hisim.components.generic_district_heating import (
    DistrictHeating,
    DistrictHeatingConfig,
    DistrictHeatingController,
    DistrictHeatingControllerConfig,
    HeatingMode,
)
from hisim import hydronics
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
        (False, 0, 20, 70, None, HeatingMode.SPACE_HEATING),
        (False, 0, 55, 25, None, HeatingMode.OFF),
        (False, 0, 40, 23, None, HeatingMode.OFF),
        (False, 20, 20, 25, None, HeatingMode.OFF),
        (True, 0, 15, 30, 30, HeatingMode.DOMESTIC_HOT_WATER),
        (True, 20, 15, 30, 30, HeatingMode.DOMESTIC_HOT_WATER),
        (True, 0, 40, 25, 50, HeatingMode.OFF),
        (True, 0, 25, 60, 50, HeatingMode.SPACE_HEATING),
        (True, 20, 25, 30, 50, HeatingMode.OFF),
    ],
)
def test_controller_determine_operating_mode(
    with_warm_water: bool,
    daily_avg_outside_temperature_deg_c: float,
    sh_current_water_temperature: float,
    sh_target_water_temperature: float,
    dhw_input_temperature: float | None,
    expected_mode: HeatingMode,
) -> None:
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
    (
        "connected_load_in_watt",
        "return_temperature_in_celsius",
        "lift_in_kelvin",
        "supply_temperature_set_in_celsius",
        "expected_power_in_watt",
        "expected_supply_temperature_in_celsius",
        "expected_mass_flow_in_kg_per_second",
    ),
    [
        # the set temperature is the return plus the lift: the supply reaches it
        (15000, 50, 20, 70, 3000, 70, 0.03588516746411483),
        (20000, 50, 20, 70, 4000, 70, 0.04784688995215311),
        # the return is the step mean, not the start temperature the controller added its lift to, so the set
        # temperature differs from the return plus the lift: the supply stops at the set temperature
        (15000, 52.5, 20, 70, 0.03588516746411483 * 4180 * 17.5, 70, 0.03588516746411483),
        (15000, 47.5, 20, 70, 0.03588516746411483 * 4180 * 22.5, 70, 0.03588516746411483),
        # a return above the set temperature: the supply stays at the return, nothing flows into the tank
        (15000, 71, 20, 70, 0, 71, 0.03588516746411483),
        # no lift asked for: the circuit idles at its return
        (15000, 70, 0, 70, 0, 70, 0),
        # the set temperature 120 °C lies beyond the connected load: the supply stops at return + 100 K
        (15000, 20, 100, 120, 15000, 120, 0.03588516746411483),
    ],
)
def test_the_hot_water_circuit_reaches_the_set_temperature_within_the_connected_load(
    connected_load_in_watt: float,
    return_temperature_in_celsius: float,
    lift_in_kelvin: float,
    supply_temperature_set_in_celsius: float,
    expected_power_in_watt: float,
    expected_supply_temperature_in_celsius: float,
    expected_mass_flow_in_kg_per_second: float,
) -> None:
    """A substation that supplied the return plus the lift rather than the set temperature would overshoot.

    The return is the tank's step mean while the controller adds its lift to the start temperature, so the two
    differ; the circuit must aim at the set temperature, within what the connected load carries at the pump's flow.
    """
    circuit = DistrictHeating.hot_water_circuit(
        return_temperature_in_celsius=return_temperature_in_celsius,
        lift_in_kelvin=lift_in_kelvin,
        supply_temperature_set_in_celsius=supply_temperature_set_in_celsius,
        connected_load_in_watt=connected_load_in_watt,
    )
    assert circuit.power_w == pytest.approx(expected_power_in_watt, rel=1e-12, abs=1e-9)
    assert circuit.t_supply_c == pytest.approx(expected_supply_temperature_in_celsius, rel=1e-15)
    assert circuit.mass_flow_kg_per_s == expected_mass_flow_in_kg_per_second
    assert circuit.power_w == hydronics.circuit_power_w(
        mass_flow_kg_per_s=circuit.mass_flow_kg_per_s,
        t_supply_c=circuit.t_supply_c,
        t_return_c=return_temperature_in_celsius,
    )


@pytest.mark.base
@pytest.mark.parametrize(
    [
        "connected_load_in_w",
        "water_input_temperature_deg_c",
        "delta_temperature_needed_in_celsius",
        "water_mass_flow_rate_in_kg_per_s",
        "expected_thermal_power_delivered_in_w",
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
    connected_load_in_w: float,
    water_input_temperature_deg_c: float,
    delta_temperature_needed_in_celsius: float,
    water_mass_flow_rate_in_kg_per_s: float,
    expected_thermal_power_delivered_in_w: float,
    expected_thermal_energy_delivered_in_watt_hour: float,
    expected_water_output_temperature_deg_c: float,
) -> None:
    """Test calculation of space heating outputs."""

    testee = given_default_component_testee()
    testee.config.connected_load_in_w = connected_load_in_w

    circuit = testee._calculate_space_heating_outputs(  # pylint: disable=protected-access  # the space-heating law
        water_mass_flow_rate_in_kg_per_s,
        delta_temperature_needed_in_celsius,
        water_input_temperature_deg_c,
        available_load_in_w=connected_load_in_w
    )

    assert circuit.thermal_power_in_watt == expected_thermal_power_delivered_in_w
    assert circuit.thermal_energy_in_watt_hour == expected_thermal_energy_delivered_in_watt_hour
    assert circuit.supply_temperature_in_celsius == expected_water_output_temperature_deg_c


def given_default_controller_testee(
    with_warm_water: bool = False,
) -> DistrictHeatingController:
    """Create default controller testee."""

    simulation_parameters = sim.SimulationParameters.full_year(
        year=2021, seconds_per_timestep=60
    )
    config = DistrictHeatingControllerConfig.preset_standard("DistrictHeatingController")
    config.with_domestic_hot_water_preparation = with_warm_water
    config.set_heating_threshold_outside_temperature_in_celsius = 16.0
    return DistrictHeatingController(simulation_parameters, config)


def given_default_component_testee(with_warm_water: bool = False) -> DistrictHeating:
    """Create default component testee."""

    simulation_parameters = sim.SimulationParameters.full_year(
        year=2021, seconds_per_timestep=60
    )
    config = DistrictHeatingConfig.preset_standard("DistrictHeating")
    config.with_domestic_hot_water_preparation = with_warm_water
    config.connected_load_in_w = 20000.0

    return DistrictHeating(simulation_parameters, config)


def step_with_fake_inputs(component: object, values: dict) -> dict:
    """Step ``component`` once with every input reading a fake output set from ``values`` by field name.

    Args:
        component: The component to step.
        values: Input values by field name; a missing one is 0.

    Returns:
        Every output of the component by field name.
    """
    from hisim import component as cp  # pylint: disable=import-outside-toplevel  # keeps the test module light
    from hisim import loadtypes as lt  # pylint: disable=import-outside-toplevel  # as above
    from hisim.config import ComponentID  # pylint: disable=import-outside-toplevel  # as above
    from tests import functions_for_testing as fft  # pylint: disable=import-outside-toplevel  # as above

    fakes = []
    for component_input in component.inputs:  # type: ignore[attr-defined]  # any component
        fake = cp.ComponentOutput(
            "Fake", component_input.field_name, lt.LoadTypes.ANY, lt.Units.ANY, component_id=ComponentID("Fake")
        )
        component_input.source_output = fake
        fakes.append(fake)
    fft.add_global_index_of_components([*fakes, component])
    stsv = cp.SingleTimeStepValues(fft.get_number_of_outputs([*fakes, component]))
    for fake in fakes:
        stsv.values[fake.global_index] = values.get(fake.field_name, 0.0)
    component.i_simulate(0, stsv, False)  # type: ignore[attr-defined]  # any component
    return {output.field_name: stsv.values[output.global_index] for output in component.outputs}  # type: ignore[attr-defined]  # any component


@pytest.mark.base
@pytest.mark.parametrize("tank_temperature_in_celsius", [45.0, 65.0])
def test_the_controller_sets_the_tank_s_start_temperature_plus_the_lift_it_asks_for(
    tank_temperature_in_celsius: float,
) -> None:
    """A set temperature other than the tank's start temperature plus the lift would charge to the wrong target.

    Without a request the lift is 0 and the set temperature the start temperature itself, not a 0 °C marker.
    """
    controller = given_default_controller_testee(with_warm_water=True)
    outputs = step_with_fake_inputs(
        controller,
        {
            DistrictHeatingController.WaterTemperatureInputFromWarmWaterStorage: tank_temperature_in_celsius,
            DistrictHeatingController.DailyAverageOutsideTemperature: 20.0,
            DistrictHeatingController.WaterTemperatureInputFromHeatDistributionSystem: 30.0,
            DistrictHeatingController.HeatingFlowTemperatureFromHeatDistributionSystem: 30.0,
        },
    )
    lift_in_kelvin = outputs[DistrictHeatingController.DeltaTemperatureNeededForDHW]
    assert outputs[DistrictHeatingController.SupplyTemperatureSetForDHWInCelsius] == (
        tank_temperature_in_celsius + lift_in_kelvin
    )


@pytest.mark.base
def test_a_controller_without_hot_water_has_no_set_temperature_output() -> None:
    """A set temperature output without a tank would have to publish a made-up value; it is not declared at all."""
    controller = given_default_controller_testee(with_warm_water=False)
    assert DistrictHeatingController.SupplyTemperatureSetForDHWInCelsius not in [
        output.field_name for output in controller.outputs
    ]
