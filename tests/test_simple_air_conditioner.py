"""Tests for the simple air conditioner component and its hysteresis controller."""

import pandas as pd
import pytest

from hisim import component as cp
from hisim import loadtypes as lt
from hisim.components.simple_air_conditioner import (
    SimpleAirConditioner,
    SimpleAirConditionerConfig,
    SimpleAirConditionerController,
    SimpleAirConditionerControllerConfig,
    SimpleAirConditionerControllerState,
)
from hisim.postprocessing.kpi_computation.kpi_structure import KpiTagEnumClass
from hisim.simulationparameters import SimulationParameters


def _make_simulation_parameters() -> SimulationParameters:
    """Return a one-day SimulationParameters for unit tests.

    Uses 2021 so that cost-related methods (which call
    EmissionFactorsAndCostsForFuelsConfig.get_values_for_year) work if
    ever invoked from a test that uses this helper.
    """
    return SimulationParameters.one_day_only(2021, 60)


# ==============================================================================
# Controller unit tests
# ==============================================================================


def _wire_controller_inputs(controller: SimpleAirConditionerController, t_indoor_c: float) -> cp.SingleTimeStepValues:
    """Wire a fake indoor-temperature output into the controller and return a stsv.

    Creates a one-slot ``SingleTimeStepValues``, wires a fake
    ``ComponentOutput`` for the indoor air temperature to the controller's
    input channel, assigns the modulating-signal output index, populates
    ``stsv.values[0]`` with ``t_indoor_c``, and returns the ``stsv``.
    """
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(1)

    t_indoor_output = cp.ComponentOutput(
        "FakeBuilding",
        "TemperatureIndoorAir",
        lt.LoadTypes.TEMPERATURE,
        lt.Units.CELSIUS,
        component_id=cp.ComponentID("FakeBuilding"),
    )
    t_indoor_output.global_index = 0
    controller.indoor_air_temperature_channel.source_output = t_indoor_output
    controller.operation_modulating_signal_channel.global_index = 0
    stsv.values[0] = t_indoor_c

    return stsv


@pytest.mark.base
def test_controller_turns_on_when_temperature_above_upper_deadband() -> None:
    """Inside temp above setpoint + deadband (24.5 °C) commands cooling (-1.0)."""
    my_simulation_parameters = _make_simulation_parameters()
    config = SimpleAirConditionerControllerConfig.get_default_simple_air_conditioner_controller_config()
    controller = SimpleAirConditionerController(
        my_simulation_parameters=my_simulation_parameters,
        config=config,
    )

    stsv = _wire_controller_inputs(controller, t_indoor_c=25.0)  # above 24.5

    controller.i_restore_state()
    controller.i_simulate(0, stsv, False)

    assert stsv.values[0] == pytest.approx(-1.0)


@pytest.mark.base
def test_controller_turns_off_when_temperature_below_lower_deadband() -> None:
    """Inside temp below setpoint - deadband (23.5 °C) commands off (0.0)."""
    my_simulation_parameters = _make_simulation_parameters()
    config = SimpleAirConditionerControllerConfig.get_default_simple_air_conditioner_controller_config()
    controller = SimpleAirConditionerController(
        my_simulation_parameters=my_simulation_parameters,
        config=config,
    )

    stsv = _wire_controller_inputs(controller, t_indoor_c=23.0)  # below 23.5

    controller.i_restore_state()
    controller.i_simulate(0, stsv, False)

    assert stsv.values[0] == pytest.approx(0.0)


@pytest.mark.base
def test_controller_hysteresis_stays_on_within_deadband() -> None:
    """When previously cooling, a temp within the deadband keeps cooling on."""
    my_simulation_parameters = _make_simulation_parameters()
    config = SimpleAirConditionerControllerConfig.get_default_simple_air_conditioner_controller_config()
    controller = SimpleAirConditionerController(
        my_simulation_parameters=my_simulation_parameters,
        config=config,
    )
    # Set the controller to cooling state
    controller.state = SimpleAirConditionerControllerState(1)
    controller.i_save_state()

    stsv = _wire_controller_inputs(controller, t_indoor_c=24.0)  # within [23.5, 24.5]

    controller.i_restore_state()
    controller.i_simulate(0, stsv, False)

    assert stsv.values[0] == pytest.approx(-1.0)


@pytest.mark.base
def test_controller_hysteresis_stays_off_within_deadband() -> None:
    """When previously off, a temp within the deadband keeps cooling off."""
    my_simulation_parameters = _make_simulation_parameters()
    config = SimpleAirConditionerControllerConfig.get_default_simple_air_conditioner_controller_config()
    controller = SimpleAirConditionerController(
        my_simulation_parameters=my_simulation_parameters,
        config=config,
    )
    # Default state is 0 (off)

    stsv = _wire_controller_inputs(controller, t_indoor_c=24.0)  # within [23.5, 24.5]

    controller.i_restore_state()
    controller.i_simulate(0, stsv, False)

    assert stsv.values[0] == pytest.approx(0.0)


@pytest.mark.base
def test_controller_first_timestep_default_state_is_off() -> None:
    """The controller initialises in the off state (state == 0)."""
    my_simulation_parameters = _make_simulation_parameters()
    config = SimpleAirConditionerControllerConfig.get_default_simple_air_conditioner_controller_config()
    controller = SimpleAirConditionerController(
        my_simulation_parameters=my_simulation_parameters,
        config=config,
    )

    assert controller.state.state == 0
    assert controller.previous_state.state == 0


@pytest.mark.base
def test_controller_setpoint_default_value() -> None:
    """The default controller config has a 24 °C setpoint and 0.5 K deadband."""
    config = SimpleAirConditionerControllerConfig.get_default_simple_air_conditioner_controller_config()
    assert config.setpoint_temperature_c == 24.0
    assert config.deadband_k == 0.5


@pytest.mark.base
def test_controller_save_and_restore_state() -> None:
    """i_save_state / i_restore_state correctly round-trip the controller state."""
    my_simulation_parameters = _make_simulation_parameters()
    config = SimpleAirConditionerControllerConfig.get_default_simple_air_conditioner_controller_config()
    controller = SimpleAirConditionerController(
        my_simulation_parameters=my_simulation_parameters,
        config=config,
    )

    controller.state.state = 1
    controller.i_save_state()
    controller.state.state = 0
    controller.i_restore_state()
    assert controller.state.state == 1


# ==============================================================================
# Component unit tests
# ==============================================================================


def _make_component() -> SimpleAirConditioner:
    """Create a SimpleAirConditioner component and return it."""
    my_simulation_parameters = _make_simulation_parameters()
    config = SimpleAirConditionerConfig.get_default_simple_air_conditioner_config()
    return SimpleAirConditioner(
        my_simulation_parameters=my_simulation_parameters,
        config=config,
    )


def _wire_component_inputs(component: SimpleAirConditioner, t_out_c: float, t_in_c: float, modulation: float):
    """Wire fake temperature and modulation outputs into the component and return a stsv.

    Returns ``(stsv, index_map)`` where ``index_map`` maps output channel names to
    their global index in the stsv.
    """
    # 3 inputs + 6 outputs = 9 slots
    number_of_values = 9
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_values)

    t_out_output = cp.ComponentOutput(
        "FakeWeather",
        "TemperatureOutside",
        lt.LoadTypes.TEMPERATURE,
        lt.Units.CELSIUS,
        component_id=cp.ComponentID("FakeWeather"),
    )
    t_in_output = cp.ComponentOutput(
        "FakeBuilding",
        "TemperatureIndoorAir",
        lt.LoadTypes.TEMPERATURE,
        lt.Units.CELSIUS,
        component_id=cp.ComponentID("FakeBuilding"),
    )
    modulation_output = cp.ComponentOutput(
        "FakeController",
        "ModulatingPowerSignal",
        lt.LoadTypes.ANY,
        lt.Units.PERCENT,
        component_id=cp.ComponentID("FakeController"),
    )

    # Assign indices: 0=t_out, 1=t_in, 2=modulation, 3..8=outputs
    t_out_output.global_index = 0
    t_in_output.global_index = 1
    modulation_output.global_index = 2

    component.t_out_channel.source_output = t_out_output
    component.t_indoor_channel.source_output = t_in_output
    component.modulating_power_signal_channel.source_output = modulation_output

    component.thermal_power_generation_channel.global_index = 3
    component.thermal_energy_generation_channel.global_index = 4
    component.electrical_power_consumption_channel.global_index = 5
    component.electrical_energy_consumption_channel.global_index = 6
    component.cop_channel.global_index = 7
    component.running_state_channel.global_index = 8

    stsv.values[0] = t_out_c
    stsv.values[1] = t_in_c
    stsv.values[2] = modulation

    index_map = {
        "thermal_power": 3,
        "thermal_energy": 4,
        "electric_power": 5,
        "electric_energy": 6,
        "cop": 7,
        "running_state": 8,
    }
    return stsv, index_map


@pytest.mark.base
def test_component_cop_calculation() -> None:
    """COP = eta_carnot * T_in_K / (T_out_K - T_in_K) for a cooling command."""
    component = _make_component()
    # t_in=24°C, t_out=35°C, modulation=-1.0
    stsv, idx = _wire_component_inputs(component, t_out_c=35.0, t_in_c=24.0, modulation=-1.0)

    component.i_simulate(0, stsv, False)

    t_in_k = 24.0 + 273.15
    t_out_k = 35.0 + 273.15
    expected_cop = 0.3 * t_in_k / (t_out_k - t_in_k)
    assert expected_cop == pytest.approx(stsv.values[idx["cop"]], rel=1e-6, abs=1e-3)


@pytest.mark.base
def test_component_electrical_power() -> None:
    """Electrical power = nominal_cooling_power / COP_real."""
    component = _make_component()
    stsv, idx = _wire_component_inputs(component, t_out_c=35.0, t_in_c=24.0, modulation=-1.0)

    component.i_simulate(0, stsv, False)

    t_in_k = 24.0 + 273.15
    t_out_k = 35.0 + 273.15
    expected_cop = 0.3 * t_in_k / (t_out_k - t_in_k)
    expected_electric = 2000.0 / expected_cop
    assert expected_electric == pytest.approx(stsv.values[idx["electric_power"]], rel=1e-6, abs=1e-3)


@pytest.mark.base
def test_component_thermal_power_is_negative() -> None:
    """Thermal power is negative (cooling removes heat from the space)."""
    component = _make_component()
    stsv, idx = _wire_component_inputs(component, t_out_c=35.0, t_in_c=24.0, modulation=-1.0)

    component.i_simulate(0, stsv, False)

    assert stsv.values[idx["thermal_power"]] < 0
    assert stsv.values[idx["thermal_power"]] == pytest.approx(-2000.0, rel=1e-6, abs=1e-3)


@pytest.mark.base
def test_component_running_state_when_cooling() -> None:
    """Running state is 1 when actively cooling."""
    component = _make_component()
    stsv, idx = _wire_component_inputs(component, t_out_c=35.0, t_in_c=24.0, modulation=-1.0)

    component.i_simulate(0, stsv, False)

    assert stsv.values[idx["running_state"]] == 1


@pytest.mark.base
def test_component_state_off_all_outputs_zero() -> None:
    """When modulation is 0.0 (off), all power outputs are exactly 0.0 and COP is 0.0."""
    component = _make_component()
    stsv, idx = _wire_component_inputs(component, t_out_c=35.0, t_in_c=24.0, modulation=0.0)

    component.i_simulate(0, stsv, False)

    assert stsv.values[idx["thermal_power"]] == 0.0
    assert stsv.values[idx["thermal_energy"]] == 0.0
    assert stsv.values[idx["electric_power"]] == 0.0
    assert stsv.values[idx["electric_energy"]] == 0.0
    assert stsv.values[idx["cop"]] == 0.0
    assert stsv.values[idx["running_state"]] == 0


@pytest.mark.base
def test_component_delta_t_guard_zero_power_when_outside_not_hotter() -> None:
    """Delta-T guard: modulation=-1.0 but t_out <= t_in produces zero power and COP 0.0."""
    component = _make_component()
    # t_out == t_in (delta_t == 0, below epsilon)
    stsv, idx = _wire_component_inputs(component, t_out_c=24.0, t_in_c=24.0, modulation=-1.0)

    component.i_simulate(0, stsv, False)

    assert stsv.values[idx["thermal_power"]] == 0.0
    assert stsv.values[idx["electric_power"]] == 0.0
    assert stsv.values[idx["cop"]] == 0.0
    assert stsv.values[idx["running_state"]] == 0


@pytest.mark.base
def test_component_delta_t_guard_zero_power_when_outside_colder() -> None:
    """Delta-T guard: t_out < t_in (negative delta_t) produces zero power."""
    component = _make_component()
    stsv, idx = _wire_component_inputs(component, t_out_c=20.0, t_in_c=24.0, modulation=-1.0)

    component.i_simulate(0, stsv, False)

    assert stsv.values[idx["thermal_power"]] == 0.0
    assert stsv.values[idx["electric_power"]] == 0.0
    assert stsv.values[idx["cop"]] == 0.0


@pytest.mark.base
def test_component_energy_accumulation() -> None:
    """Thermal energy = power * seconds_per_timestep / 3.6e3."""
    component = _make_component()
    stsv, idx = _wire_component_inputs(component, t_out_c=35.0, t_in_c=24.0, modulation=-1.0)

    spt = component.my_simulation_parameters.seconds_per_timestep
    component.i_simulate(0, stsv, False)

    expected_energy = stsv.values[idx["thermal_power"]] * spt / 3.6e3
    assert expected_energy == pytest.approx(stsv.values[idx["thermal_energy"]], rel=1e-6, abs=1e-9)


@pytest.mark.base
def test_component_reproducibility() -> None:
    """Two identical i_simulate calls produce identical outputs."""
    component = _make_component()
    stsv1, idx1 = _wire_component_inputs(component, t_out_c=35.0, t_in_c=24.0, modulation=-1.0)
    stsv2, idx2 = _wire_component_inputs(component, t_out_c=35.0, t_in_c=24.0, modulation=-1.0)

    component.i_simulate(0, stsv1, False)
    component.i_simulate(0, stsv2, False)

    for key, value in idx1.items():
        assert stsv1.values[value] == stsv2.values[idx2[key]]


@pytest.mark.base
def test_component_force_convergence_returns_immediately() -> None:
    """When force_convergence is True, the component returns without changing outputs."""
    component = _make_component()
    stsv, idx = _wire_component_inputs(component, t_out_c=35.0, t_in_c=24.0, modulation=-1.0)

    component.i_simulate(0, stsv, True)

    # All outputs should remain at 0.0 (initialised by SingleTimeStepValues)
    for value in idx.values():
        assert stsv.values[value] == 0.0


@pytest.mark.base
def test_component_partial_modulation() -> None:
    """Modulation of -0.5 delivers half the nominal cooling power."""
    component = _make_component()
    stsv, idx = _wire_component_inputs(component, t_out_c=35.0, t_in_c=24.0, modulation=-0.5)

    component.i_simulate(0, stsv, False)

    assert stsv.values[idx["thermal_power"]] == pytest.approx(-1000.0, rel=1e-6, abs=1e-3)
    assert stsv.values[idx["running_state"]] == 1


@pytest.mark.base
def test_component_default_config_values() -> None:
    """The default component config has the expected nominal power, eta, and epsilon."""
    config = SimpleAirConditionerConfig.get_default_simple_air_conditioner_config()
    assert config.nominal_cooling_power_w == 2000.0
    assert config.eta_carnot == 0.3
    assert config.temperature_epsilon_k == 0.01


# ==============================================================================
# Integration test: controller + component end-to-end
# ==============================================================================


@pytest.mark.base
def test_integration_controller_and_component_end_to_end() -> None:
    """End-to-end: controller reads indoor temp, commands modulation, component outputs powers.

    Scenario: indoor temp 26 °C (above 24.5 deadband), outdoor 35 °C.
    The controller should command -1.0, and the component should deliver
    negative thermal power (cooling) with a positive COP and positive electrical consumption.
    """
    my_simulation_parameters = _make_simulation_parameters()

    controller_config = SimpleAirConditionerControllerConfig.get_default_simple_air_conditioner_controller_config()
    controller = SimpleAirConditionerController(
        my_simulation_parameters=my_simulation_parameters,
        config=controller_config,
    )

    ac_config = SimpleAirConditionerConfig.get_default_simple_air_conditioner_config()
    ac = SimpleAirConditioner(
        my_simulation_parameters=my_simulation_parameters,
        config=ac_config,
    )

    # 1 controller output + 3 component inputs (t_out, t_in, modulation) + 6 component outputs = 10
    number_of_values = 10
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_values)

    # Fake temperature outputs
    t_out_output = cp.ComponentOutput(
        "FakeWeather",
        "TemperatureOutside",
        lt.LoadTypes.TEMPERATURE,
        lt.Units.CELSIUS,
        component_id=cp.ComponentID("FakeWeather"),
    )
    t_in_output = cp.ComponentOutput(
        "FakeBuilding",
        "TemperatureIndoorAir",
        lt.LoadTypes.TEMPERATURE,
        lt.Units.CELSIUS,
        component_id=cp.ComponentID("FakeBuilding"),
    )

    # Assign indices
    # 0 = controller output (modulation), 1 = t_out, 2 = t_in
    # 3..8 = component outputs
    t_out_output.global_index = 1
    t_in_output.global_index = 2
    controller.operation_modulating_signal_channel.global_index = 0

    # Wire controller: it reads indoor temp
    controller.indoor_air_temperature_channel.source_output = t_in_output

    # Wire component inputs
    ac.t_out_channel.source_output = t_out_output
    ac.t_indoor_channel.source_output = t_in_output
    ac.modulating_power_signal_channel.source_output = controller.operation_modulating_signal_channel

    # Component outputs
    ac.thermal_power_generation_channel.global_index = 3
    ac.thermal_energy_generation_channel.global_index = 4
    ac.electrical_power_consumption_channel.global_index = 5
    ac.electrical_energy_consumption_channel.global_index = 6
    ac.cop_channel.global_index = 7
    ac.running_state_channel.global_index = 8

    # Set temperatures: indoor 26 (above 24.5), outdoor 35
    stsv.values[1] = 35.0  # t_out
    stsv.values[2] = 26.0  # t_in

    # Simulate controller then component
    controller.i_restore_state()
    controller.i_simulate(0, stsv, False)

    # Controller should command cooling
    assert stsv.values[0] == pytest.approx(-1.0)

    ac.i_simulate(0, stsv, False)

    # Component should be cooling
    t_in_k = 26.0 + 273.15
    t_out_k = 35.0 + 273.15
    expected_cop = 0.3 * t_in_k / (t_out_k - t_in_k)
    assert stsv.values[7] == pytest.approx(expected_cop, rel=1e-6, abs=1e-3)
    assert stsv.values[3] == pytest.approx(-2000.0, rel=1e-6, abs=1e-3)
    assert stsv.values[5] == pytest.approx(2000.0 / expected_cop, rel=1e-6, abs=1e-3)
    assert stsv.values[8] == 1
    # Thermal power is negative (cooling)
    assert stsv.values[3] < 0
    # Electrical power is positive (consumption)
    assert stsv.values[5] > 0


@pytest.mark.base
def test_integration_controller_off_and_component_idle() -> None:
    """End-to-end: indoor temp 22 °C (below 23.5) commands off; component outputs zero."""
    my_simulation_parameters = _make_simulation_parameters()

    controller_config = SimpleAirConditionerControllerConfig.get_default_simple_air_conditioner_controller_config()
    controller = SimpleAirConditionerController(
        my_simulation_parameters=my_simulation_parameters,
        config=controller_config,
    )

    ac_config = SimpleAirConditionerConfig.get_default_simple_air_conditioner_config()
    ac = SimpleAirConditioner(
        my_simulation_parameters=my_simulation_parameters,
        config=ac_config,
    )

    number_of_values = 10
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_values)

    t_out_output = cp.ComponentOutput(
        "FakeWeather",
        "TemperatureOutside",
        lt.LoadTypes.TEMPERATURE,
        lt.Units.CELSIUS,
        component_id=cp.ComponentID("FakeWeather"),
    )
    t_in_output = cp.ComponentOutput(
        "FakeBuilding",
        "TemperatureIndoorAir",
        lt.LoadTypes.TEMPERATURE,
        lt.Units.CELSIUS,
        component_id=cp.ComponentID("FakeBuilding"),
    )

    t_out_output.global_index = 1
    t_in_output.global_index = 2
    controller.operation_modulating_signal_channel.global_index = 0

    controller.indoor_air_temperature_channel.source_output = t_in_output

    ac.t_out_channel.source_output = t_out_output
    ac.t_indoor_channel.source_output = t_in_output
    ac.modulating_power_signal_channel.source_output = controller.operation_modulating_signal_channel

    ac.thermal_power_generation_channel.global_index = 3
    ac.thermal_energy_generation_channel.global_index = 4
    ac.electrical_power_consumption_channel.global_index = 5
    ac.electrical_energy_consumption_channel.global_index = 6
    ac.cop_channel.global_index = 7
    ac.running_state_channel.global_index = 8

    # Indoor 22 (below 23.5), outdoor 30
    stsv.values[1] = 30.0
    stsv.values[2] = 22.0

    controller.i_restore_state()
    controller.i_simulate(0, stsv, False)
    assert stsv.values[0] == pytest.approx(0.0)

    ac.i_simulate(0, stsv, False)

    assert stsv.values[3] == 0.0
    assert stsv.values[5] == 0.0
    assert stsv.values[7] == 0.0
    assert stsv.values[8] == 0


# ==============================================================================
# Cost and KPI tests
# ==============================================================================


def _make_cost_component() -> SimpleAirConditioner:
    """Create a SimpleAirConditioner for cost/KPI tests (year 2021 has cost factors)."""
    my_simulation_parameters = SimulationParameters.one_day_only(2021, 60)
    config = SimpleAirConditionerConfig.get_default_simple_air_conditioner_config()
    return SimpleAirConditioner(
        my_simulation_parameters=my_simulation_parameters,
        config=config,
    )


def _build_postprocessing_results(component, electric_energy_wh, thermal_energy_wh):
    """Build all_outputs and a matching postprocessing_results DataFrame.

    Args:
        component: A SimpleAirConditioner instance.
        electric_energy_wh: List of Wh values for ElectricalEnergyConsumption.
        thermal_energy_wh: List of Wh values for ThermalEnergyDelivered (negative = cooling).

    Returns:
        (all_outputs, postprocessing_results) tuple.
    """
    all_outputs = list(component.outputs)
    n_timesteps = len(electric_energy_wh)
    data = {}
    for index, output in enumerate(all_outputs):
        if output.field_name == component.ElectricalEnergyConsumption:
            data[index] = electric_energy_wh
        elif output.field_name == component.ThermalEnergyDelivered:
            data[index] = thermal_energy_wh
        else:
            data[index] = [0.0] * n_timesteps
    return all_outputs, pd.DataFrame(data)


@pytest.mark.base
def test_get_cost_capex_returns_expected_values() -> None:
    """CAPEX returns the hardcoded investment cost, CO2 footprint, and lifetime."""
    my_simulation_parameters = SimulationParameters.one_day_only(2021, 60)
    config = SimpleAirConditionerConfig.get_default_simple_air_conditioner_config()
    capex = SimpleAirConditioner.get_cost_capex(config, my_simulation_parameters)

    assert capex.capex_investment_cost_in_euro == 1500.0
    assert capex.device_co2_footprint_in_kg == 100.0
    assert capex.lifetime_in_years == 15
    assert capex.kpi_tag == KpiTagEnumClass.AIR_CONDITIONER
    # Per-period values scale by duration ratio (1 day / 365 days)
    seconds_per_year = 365 * 24 * 60 * 60
    duration_ratio = my_simulation_parameters.duration.total_seconds() / seconds_per_year
    assert capex.capex_investment_cost_for_simulated_period_in_euro == pytest.approx((1500.0 / 15) * duration_ratio)
    assert capex.device_co2_footprint_for_simulated_period_in_kg == pytest.approx((100.0 / 15) * duration_ratio)


@pytest.mark.base
def test_get_cost_opex_computes_electricity_consumption() -> None:
    """OPEX computes electricity cost and CO2 from the ElectricalEnergyConsumption output."""
    component = _make_cost_component()
    # 500 Wh per timestep for 2 timesteps = 1000 Wh = 1.0 kWh
    electric_energy_wh = [500.0, 500.0]
    # -2000 Wh per timestep (cooling) for 2 timesteps
    thermal_energy_wh = [-2000.0, -2000.0]
    all_outputs, postprocessing_results = _build_postprocessing_results(
        component, electric_energy_wh, thermal_energy_wh
    )

    opex = component.get_cost_opex(all_outputs=all_outputs, postprocessing_results=postprocessing_results)

    # Electricity consumption: round(sum(500 + 500) * 1e-3, 1) = 1.0 kWh
    assert opex.total_consumption_in_kwh == pytest.approx(1.0)
    assert opex.loadtype == lt.LoadTypes.ELECTRICITY
    assert opex.kpi_tag == KpiTagEnumClass.AIR_CONDITIONER
    # Electricity cost = 1.0 kWh * 0.3005 EUR/kWh (DE 2021)
    assert opex.opex_energy_cost_in_euro == pytest.approx(1.0 * 0.3005)
    # CO2 = 1.0 kWh * 0.41 kg/kWh (DE 2021)
    assert opex.co2_footprint_in_kg == pytest.approx(1.0 * 0.41)


@pytest.mark.base
def test_get_cost_opex_zero_consumption_when_no_energy() -> None:
    """OPEX returns zero consumption when all energy values are zero."""
    component = _make_cost_component()
    all_outputs, postprocessing_results = _build_postprocessing_results(
        component, electric_energy_wh=[0.0, 0.0], thermal_energy_wh=[0.0, 0.0]
    )

    opex = component.get_cost_opex(all_outputs=all_outputs, postprocessing_results=postprocessing_results)

    assert opex.total_consumption_in_kwh == pytest.approx(0.0)
    assert opex.opex_energy_cost_in_euro == pytest.approx(0.0)


@pytest.mark.base
def test_get_component_kpi_entries() -> None:
    """KPI entries include electricity consumption, cooling energy, CAPEX, and OPEX."""
    component = _make_cost_component()
    electric_energy_wh = [500.0, 500.0]
    thermal_energy_wh = [-2000.0, -2000.0]
    all_outputs, postprocessing_results = _build_postprocessing_results(
        component, electric_energy_wh, thermal_energy_wh
    )

    kpi_entries = component.get_component_kpi_entries(
        all_outputs=all_outputs, postprocessing_results=postprocessing_results
    )

    kpi_by_name = {entry.name: entry for entry in kpi_entries}

    # Electricity consumption KPI = 1.0 kWh
    assert "Electrical energy consumption" in kpi_by_name
    assert kpi_by_name["Electrical energy consumption"].value == pytest.approx(1.0)
    assert kpi_by_name["Electrical energy consumption"].unit == "kWh"

    # Thermal cooling energy KPI = round(sum(-2000, -2000) * 1e-3, 1) = -4.0 kWh
    assert "Thermal energy delivered - cooling" in kpi_by_name
    assert kpi_by_name["Thermal energy delivered - cooling"].value == pytest.approx(-4.0)

    # CAPEX investment cost KPI = 1500.0 EUR
    assert "CAPEX - Investment cost" in kpi_by_name
    assert kpi_by_name["CAPEX - Investment cost"].value == 1500.0

    # OPEX electricity costs KPI = 1.0 * 0.3005 = 0.3005 EUR
    assert "OPEX - Electricity costs" in kpi_by_name
    assert kpi_by_name["OPEX - Electricity costs"].value == pytest.approx(1.0 * 0.3005)


@pytest.mark.base
def test_get_component_kpi_entries_cooling_sums_only_negative() -> None:
    """Cooling energy KPI sums only negative (cooling) thermal energy values."""
    component = _make_cost_component()
    # Mix of negative (cooling) and positive (should be ignored) thermal energy
    electric_energy_wh = [500.0, 500.0]
    thermal_energy_wh = [-2000.0, 1000.0]  # only -2000 counts
    all_outputs, postprocessing_results = _build_postprocessing_results(
        component, electric_energy_wh, thermal_energy_wh
    )

    kpi_entries = component.get_component_kpi_entries(
        all_outputs=all_outputs, postprocessing_results=postprocessing_results
    )

    kpi_by_name = {entry.name: entry for entry in kpi_entries}
    # Only the -2000 Wh value is summed: round(-2000 * 1e-3, 1) = -2.0 kWh
    assert kpi_by_name["Thermal energy delivered - cooling"].value == pytest.approx(-2.0)


@pytest.mark.base
def test_controller_get_cost_capex_returns_default() -> None:
    """Controller CAPEX returns the default (zero-cost) dataclass."""
    my_simulation_parameters = _make_simulation_parameters()
    config = SimpleAirConditionerControllerConfig.get_default_simple_air_conditioner_controller_config()
    capex = SimpleAirConditionerController.get_cost_capex(config, my_simulation_parameters)

    assert capex.capex_investment_cost_in_euro == 0
    assert capex.device_co2_footprint_in_kg == 0


@pytest.mark.base
def test_controller_get_cost_opex_returns_default() -> None:
    """Controller OPEX returns the default (zero-cost) dataclass."""
    controller = SimpleAirConditionerController(
        my_simulation_parameters=_make_simulation_parameters(),
        config=SimpleAirConditionerControllerConfig.get_default_simple_air_conditioner_controller_config(),
    )
    opex = controller.get_cost_opex(all_outputs=[], postprocessing_results=None)
    assert opex.total_consumption_in_kwh == pytest.approx(0.0)


@pytest.mark.base
def test_controller_get_component_kpi_entries_returns_empty() -> None:
    """Controller KPI entries list is empty (no direct KPIs)."""
    controller = SimpleAirConditionerController(
        my_simulation_parameters=_make_simulation_parameters(),
        config=SimpleAirConditionerControllerConfig.get_default_simple_air_conditioner_controller_config(),
    )
    kpi_entries = controller.get_component_kpi_entries(all_outputs=[], postprocessing_results=None)
    assert not kpi_entries


# ==============================================================================
# Modulation clamping test
# ==============================================================================


@pytest.mark.base
def test_component_clamps_modulation_above_one() -> None:
    """A modulation signal below -1.0 is clamped to full capacity (not 150%)."""
    component = _make_component()
    # modulation = -1.5 should be clamped to -1.0 (full capacity)
    stsv, idx = _wire_component_inputs(component, t_out_c=35.0, t_in_c=24.0, modulation=-1.5)

    component.i_simulate(0, stsv, False)

    # Thermal power should be -2000.0 (nominal), not -3000.0
    assert stsv.values[idx["thermal_power"]] == pytest.approx(-2000.0, rel=1e-6, abs=1e-3)
    assert stsv.values[idx["running_state"]] == 1
