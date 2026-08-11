"""Tests for the MPC controller config field names and output channel units.

Verifies that the physical-value variable renames in controller_mpc.py are
correct and that the BatteryEnergyContent output channel declares the
proper energy unit (WATT_HOUR, not WATT).
"""

# clean


import pytest

from hisim.components.controller_mpc import MpcController, MpcControllerConfig
from hisim.loadtypes import Units
from hisim.simulationparameters import SimulationParameters

# Non-zero thermal coefficients so statespace() does not divide by zero.
H_TR_W = 50.0
H_TR_MS = 300.0
H_TR_EM = 100.0
H_VE_ADJ = 40.0
H_TR_IS = 10.0
C_M = 1.5e7


def _make_mpc_config() -> MpcControllerConfig:
    """Builds an MpcControllerConfig with predictive=False to avoid sim repo deps."""
    return MpcControllerConfig(
        building_name="BUI1",
        name="MpcController",
        mpc_scheme="optimization_once_aday_only",
        min_comfort_temp_in_celsius=21.0,
        max_comfort_temp_in_celsius=23.0,
        optimizer_sampling_rate_in_min=15,
        initial_temperature_in_celsius=22.0,
        flexibility_element="basic_buidling_configuration",
        initial_state_of_charge=10 / 15,
        temp_forecast_in_celsius=[],
        phi_m_forecast_in_watt=[],
        phi_st_forecast_in_watt=[],
        phi_ia_forecast_in_watt=[],
        pv_forecast_yearly_in_watt=[],
        maximum_storage_capacity_in_watt_hour=15000.0,
        minimum_storage_capacity_in_watt_hour=0.0,
        maximum_charging_power_in_watt=5000.0,
        maximum_discharging_power_in_watt=5000.0,
        battery_efficiency=0.95,
        inverter_efficiency=0.95,
        temperature_forecast_24h_1min_in_celsius=[],
        phi_m_forecast_24h_1min_in_watt=[],
        phi_ia_forecast_24h_1min_in_watt=[],
        phi_st_forecast_24h_1min_in_watt=[],
        pv_forecast_24h_1min_in_watt=[],
        price_purchase_forecast_24h_1min_in_eur_per_kwh=[],
        price_injection_forecast_24h_1min_in_eur_per_kwh=[],
        optimal_cost_in_eur=[],
        revenues_in_eur=[],
        air_conditioning_electricity_in_watt=[],
        cost_optimal_temperature_set_point_in_celsius=[],
        pv2load_in_watt=[],
        electricity_from_grid_in_watt=[],
        electricity_to_grid_in_watt=[],
        battery_to_load_in_watt=[],
        pv_to_battery_timestep_in_watt=[],
        battery_power_flow_timestep_in_watt=[],
        battery_control_state=[],
        batt_energy_content_in_watt_hour_timestep=[],
        batt_soc_normalized_timestep=[],
        h_tr_w_in_watt_per_kelvin=H_TR_W,
        h_tr_ms_in_watt_per_kelvin=H_TR_MS,
        h_tr_em_in_watt_per_kelvin=H_TR_EM,
        h_ve_adj_in_watt_per_kelvin=H_VE_ADJ,
        h_tr_is_in_watt_per_kelvin=H_TR_IS,
        c_m_in_joule_per_kelvin=C_M,
        cop_coef=[0.0, 0.0],
        eer_coef=[0.0, 0.0],
        predictive=False,
        prediction_horizon_in_s=0,
    )


def _make_sim_params() -> SimulationParameters:
    """Builds a one-day SimulationParameters at 60-second resolution."""
    return SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)


@pytest.mark.base
def test_mpc_config_accepts_renamed_fields() -> None:
    """The MpcControllerConfig class must accept the unit-suffixed field names."""
    config = _make_mpc_config()
    assert config.optimizer_sampling_rate_in_min == 15
    assert not config.batt_energy_content_in_watt_hour_timestep
    assert not config.batt_soc_normalized_timestep


@pytest.mark.base
def test_battery_energy_content_channel_uses_watt_hour() -> None:
    """The BatteryEnergyContent output channel must declare Units.WATT_HOUR (energy), not WATT (power)."""
    config = _make_mpc_config()
    sim_params = _make_sim_params()
    controller = MpcController(
        my_simulation_parameters=sim_params,
        config=config,
    )
    channel = controller.batt_energy_content_in_watt_hour_channel
    assert channel.field_name == MpcController.BatteryEnergyContent
    assert channel.unit == Units.WATT_HOUR


@pytest.mark.base
def test_battery_soc_normalized_channel_is_separate() -> None:
    """The dimensionless BatterySoC channel must remain distinct from the energy-content channel."""
    config = _make_mpc_config()
    sim_params = _make_sim_params()
    controller = MpcController(
        my_simulation_parameters=sim_params,
        config=config,
    )
    assert controller.batt_soc_normalized_channel.field_name == MpcController.BatterySoC
    assert controller.batt_soc_normalized_channel is not controller.batt_energy_content_in_watt_hour_channel
