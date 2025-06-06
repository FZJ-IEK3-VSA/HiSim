"""Test for generic dhw boiler."""

import pytest
from hisim.components import generic_hot_water_storage_modular
from hisim.components import generic_heat_source
from hisim.components import controller_l1_heatpump

from hisim import loadtypes as lt
from hisim import component as cp
from hisim.simulationparameters import SimulationParameters


@pytest.mark.base
def test_simple_bucket_boiler_state():
    """Test simple bucket state."""

    # simulation parameters
    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only(
        2017, seconds_per_timestep
    )

    # Boiler default config
    l1_config = controller_l1_heatpump.L1HeatPumpConfig.get_default_config_heat_source_controller_dhw(
        "HP Controller"
    )
    boiler_config = (
        generic_hot_water_storage_modular.StorageConfig.get_default_config_for_boiler()
    )
    boiler_config.volume = 200
    heater_config = generic_heat_source.HeatSourceConfig.get_default_config_waterheating(
        heating_system=lt.HeatingSystems.DISTRICT_HEATING,
        max_warm_water_demand_in_liter=200,
        scaling_factor_according_to_number_of_apartments=1,
        seconds_per_timestep=seconds_per_timestep,
    )

    # definition of outputs
    number_of_outputs = 5
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    # ===================================================================================================================
    # Set Boiler
    my_boiler = generic_hot_water_storage_modular.HotWaterStorage(
        config=boiler_config, my_simulation_parameters=my_simulation_parameters
    )
    my_heater = generic_heat_source.HeatSource(
        config=heater_config, my_simulation_parameters=my_simulation_parameters
    )
    # Set L1 Boiler Controller
    my_boiler_controller_l1 = controller_l1_heatpump.L1HeatPumpController(
        config=l1_config, my_simulation_parameters=my_simulation_parameters
    )

    # definition of hot water use
    ww_use = cp.ComponentOutput(
        "FakeWarmwaterUse", "WaterConsumption", lt.LoadTypes.WARM_WATER, lt.Units.LITER
    )

    # connection of in- and outputs

    my_boiler_controller_l1.storage_temperature_channel.source_output = (
        my_boiler.temperature_mean_channel
    )
    my_boiler.water_consumption_channel.source_output = ww_use
    my_boiler.thermal_power_delivered_channel.source_output = (
        my_heater.thermal_power_delivered_channel
    )
    my_heater.l1_heatsource_taget_percentage.source_output = (
        my_boiler_controller_l1.heat_pump_target_percentage_channel
    )

    # indexing of in- and outputs
    ww_use.global_index = 0
    my_boiler.temperature_mean_channel.global_index = 1
    my_heater.thermal_power_delivered_channel.global_index = 2
    my_boiler_controller_l1.heat_pump_target_percentage_channel.global_index = 3
    my_heater.fuel_delivered_channel.global_index = 4

    j = 60
    stsv.values[0] = 1

    # check if heat loss in boiler corresponds to heatloss originated from 1 l hot water use and u-value heat loss
    my_boiler.i_restore_state()
    my_boiler.i_simulate(j, stsv, False)

    my_boiler_controller_l1.i_restore_state()
    my_boiler_controller_l1.i_simulate(j, stsv, False)

    my_heater.i_restore_state()
    my_heater.i_simulate(j, stsv, False)

    my_boiler.i_restore_state()
    my_boiler.i_simulate(j, stsv, False)

    assert stsv.values[1] >= 59.6 and stsv.values[1] < 59.7

    # check if heater starts heating when temperature of boiler is too low
    stsv.values[0] = 0
    stsv.values[1] = 20

    my_boiler_controller_l1.i_restore_state()
    my_boiler_controller_l1.i_simulate(j, stsv, False)

    my_heater.i_restore_state()
    my_heater.i_simulate(j, stsv, False)

    my_boiler.i_restore_state()
    my_boiler.i_simulate(j, stsv, False)

    # check if heat loss in boiler corresponds to heatloss originated from 1 l hot water use and u-value heat loss
    assert stsv.values[2] == my_heater.config.thermal_power_in_watt * my_heater.config.efficiency

    # check if heater stops heating when temperature of boiler is too high
    stsv.values[0] = 0
    stsv.values[1] = 100

    my_boiler_controller_l1.i_restore_state()
    my_boiler_controller_l1.i_simulate(j, stsv, False)

    my_heater.i_restore_state()
    my_heater.i_simulate(j, stsv, False)

    my_boiler.i_restore_state()
    my_boiler.i_simulate(j, stsv, False)

    # check if heat loss in boiler corresponds to heatloss originated from 1 l hot water use and u-value heat loss
    assert stsv.values[2] == 0
