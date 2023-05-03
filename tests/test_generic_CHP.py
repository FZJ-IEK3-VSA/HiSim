# -*- coding: utf-8 -*-

"""Basic test of gas driven Combined heat and power.
"""

import pytest
from hisim import component as cp
from hisim import loadtypes as lt
from hisim.components import (
    generic_CHP,
    controller_l1_chp,
    generic_hot_water_storage_modular,
)
from hisim.simulationparameters import SimulationParameters
from tests import functions_for_testing as fft

@pytest.mark.base
def test_chp_system():
    seconds_per_timestep = 60
    thermal_power = 500  # thermal power in Watt
    my_simulation_parameters = SimulationParameters.one_day_only(2017, seconds_per_timestep)

    # configure and add chp
    chp_config = generic_CHP.CHPConfig.get_default_config_chp(thermal_power=thermal_power)
    chp_config.source_weight = count
    my_chp = generic_CHP.CHP(
        my_simulation_parameters=my_simulation_parameters, config=chp_config
        )
    
    # configure chp controller
    chp_controller_config = controller_l1_chp.L1CHPControllerConfig.get_default_config_with_buffer(name="CHP Controller", use=lt.LoadTypes.GAS)
    chp_controller_config.electricity_threshold = chp_config.p_el / 2
    my_chp_controller = controller_l1_chp.L1CHPController(
        my_simulation_parameters=my_simulation_parameters, config=chp_controller_config
        )

    my_chp_controller.connect_only_predefined_connections(my_boiler)
    my_chp_controller.connect_input(
        input_fieldname=my_chp_controller.BuildingTemperature, src_object_name=my_buffer.component_name, src_field_name=my_buffer.TemperatureMean
                                    )

    # connect chp with controller intputs and add it to simulation
    my_chp.connect_only_predefined_connections(my_chp_controller)


    # Set Fake Inputs
    electricity_target = cp.ComponentOutput('FakeElectricityTarget', "l2_ElectricityTarget", lt.LoadTypes.ELECTRICITY, lt.Units.WATT)
    hydrogensoc = cp.ComponentOutput('FakeH2SOC', 'HydrogenSOC', lt.LoadTypes.HYDROGEN, lt.Units.PERCENT)
    l2_devicesignal = cp.ComponentOutput('FakeHeatSignal', 'l2_DeviceSignal', lt.LoadTypes.ON_OFF, lt.Units.BINARY)

    number_of_outputs = fft.get_number_of_outputs([my_chp, my_chp_controller, electricity_target, hydrogensoc, l2_devicesignal])
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    my_chp_controller.ElectricityTargetC.source_output = electricity_target
    my_chp_controller.HydrogenSOCC.source_output = hydrogensoc
    my_chp_controller.l2_DeviceSignalC.source_output = l2_devicesignal
    my_chp.chp_onoff_signal_channel.source_output = my_chp_controller.L1DeviceSignalC

    # Add Global Index and set values for fake Inputs
    fft.add_global_index_of_components([my_chp, my_chp_controller, electricity_target, hydrogensoc, l2_devicesignal])

    # test if chp runs when hydrogen in storage and heat as well as electricity needed
    stsv.values[electricity_target.global_index] = 2.5e3
    stsv.values[hydrogensoc.global_index] = 50
    stsv.values[l2_devicesignal.global_index] = 1

    for t in range(int((my_chp_controller_config.min_idle_time / seconds_per_timestep) + 2)):
        my_chp_controller.i_simulate(t, stsv, False)
        my_chp.i_simulate(t, stsv, False)

    assert stsv.values[my_chp.thermal_power_output_channel.global_index] == 3000
    assert stsv.values[my_chp.electricity_output_channel.global_index] == 2000
    assert stsv.values[my_chp.fuel_consumption_channel.global_index] > 4e-5

    # #test if chp shuts down when too little hydrogen in storage and electricty as well as heat needed
    stsv.values[electricity_target.global_index] = 2.5e3
    stsv.values[hydrogensoc.global_index] = 0
    stsv.values[l2_devicesignal.global_index] = 1

    for tt in range(t, t + int((my_chp_controller_config.min_operation_time / seconds_per_timestep) + 2)):
        my_chp_controller.i_simulate(tt, stsv, False)
        my_chp.i_simulate(tt, stsv, False)

    assert stsv.values[my_chp.thermal_power_output_channel.global_index] == 0
    assert stsv.values[my_chp.electricity_output_channel.global_index] == 0
    assert stsv.values[my_chp.fuel_consumption_channel.global_index] == 0

    # test if chp shuts down when hydrogen is ok, electricity is needed, but heat not
    stsv.values[electricity_target.global_index] = 2.5e3
    stsv.values[hydrogensoc.global_index] = 50
    stsv.values[l2_devicesignal.global_index] = 1

    for ttt in range(tt, tt + int((my_chp_controller_config.min_idle_time / seconds_per_timestep) + 2)):
        my_chp_controller.i_simulate(ttt, stsv, False)
        my_chp.i_simulate(ttt, stsv, False)

    stsv.values[electricity_target.global_index] = 2.5e3
    stsv.values[hydrogensoc.global_index] = 50
    stsv.values[l2_devicesignal.global_index] = 0

    for it in range(ttt, ttt + int((my_chp_controller_config.min_operation_time / seconds_per_timestep) + 2)):
        my_chp_controller.i_simulate(it, stsv, False)
        my_chp.i_simulate(it, stsv, False)

    assert stsv.values[my_chp.thermal_power_output_channel.global_index] == 0
    assert stsv.values[my_chp.electricity_output_channel.global_index] == 0
    assert stsv.values[my_chp.fuel_consumption_channel.global_index] == 0

    # test if chp shuts down when hydrogen is ok, heat is needed, but electricity not
    stsv.values[electricity_target.global_index] = 2.5e3
    stsv.values[hydrogensoc.global_index] = 50
    stsv.values[l2_devicesignal.global_index] = 1

    for t in range(it, it + int((my_chp_controller_config.min_idle_time / seconds_per_timestep) + 2)):
        my_chp_controller.i_simulate(t, stsv, False)
        my_chp.i_simulate(t, stsv, False)

    stsv.values[electricity_target.global_index] = 0
    stsv.values[hydrogensoc.global_index] = 50
    stsv.values[l2_devicesignal.global_index] = 1

    for tt in range(t, t + int((my_chp_controller_config.min_operation_time / seconds_per_timestep) + 2)):
        my_chp_controller.i_simulate(tt, stsv, False)
        my_chp.i_simulate(tt, stsv, False)

    assert stsv.values[my_chp.thermal_power_output_channel.global_index] == 0
    assert stsv.values[my_chp.electricity_output_channel.global_index] == 0
    assert stsv.values[my_chp.fuel_consumption_channel.global_index] == 0
