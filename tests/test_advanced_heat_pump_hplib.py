from hisim import component as cp
from hisim.components.advanced_heat_pump_hplib import  HeatPumpHplib
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from hisim import log
from tests import functions_for_testing as fft


def test_heat_pump_hplib():

    # Definitions for HeatPump init
    model: str = "Generic"
    group_id: int = 1
    t_in: float = -7
    t_out: float = 52
    p_th_set: float = 10000
    simpars = SimulationParameters.one_day_only(2017,60)
    # Definitions for i_simulate
    timestep = 1
    seconds_per_timestep = 60
    force_convergence = False 

    # Create fake component outputs as inputs for simulation
    on_off_switch = cp.ComponentOutput("Fake_on_off_switch",
                              "Fake_on_off_switch",
                              lt.LoadTypes.Any,
                              lt.Units.Any)
    t_in_primary = cp.ComponentOutput("Fake_t_in_primary",
                              "Fake_t_in_primary",
                              lt.LoadTypes.Any,
                              lt.Units.Any)
    t_in_secondary = cp.ComponentOutput("Fake_t_in_secondary",
                              "Fake_t_in_secondary",
                              lt.LoadTypes.Any,
                              lt.Units.Any)
    t_amb = cp.ComponentOutput("Fake_t_amb",
                              "Fake_t_amb",
                              lt.LoadTypes.Any,
                              lt.Units.Any)

    # Initialize component
    heatpump = HeatPumpHplib(model=model, group_id=group_id, t_in=t_in, t_out_val=t_out, p_th_set=p_th_set, my_simulation_parameters=simpars)

    number_of_outputs = fft.get_number_of_outputs([on_off_switch,t_in_primary,t_in_secondary,t_amb,heatpump])
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    heatpump.on_off_switch.SourceOutput = on_off_switch
    heatpump.t_in_primary.SourceOutput = t_in_primary
    heatpump.t_in_secondary.SourceOutput = t_in_secondary
    heatpump.t_amb.SourceOutput = t_amb

    # Add Global Index and set values for fake Inputs
    fft.add_global_index_of_components([on_off_switch,t_in_primary,t_in_secondary,t_amb,heatpump])
    stsv.values[on_off_switch.GlobalIndex] = 1
    stsv.values[t_in_primary.GlobalIndex] = -7
    stsv.values[t_in_secondary.GlobalIndex] = 47.0
    stsv.values[t_amb.GlobalIndex] = -7


    # Simulation
    heatpump.i_simulate(timestep=timestep, stsv=stsv, force_convergence=force_convergence)
    log.information(str(stsv.values))
    # Check
    assert p_th_set == stsv.values[heatpump.p_th.GlobalIndex]
    assert 7074.0 == stsv.values[heatpump.p_el.GlobalIndex]
    assert 1.41 == stsv.values[heatpump.cop.GlobalIndex]
    assert t_out == stsv.values[heatpump.t_out.GlobalIndex]
    assert 0.476 == stsv.values[heatpump.m_dot.GlobalIndex]
    assert 60 == stsv.values[heatpump.time_on.GlobalIndex]
    assert 0 == stsv.values[heatpump.time_off.GlobalIndex]
