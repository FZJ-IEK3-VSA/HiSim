from hisim import component as cp
from hisim.components.heat_pump_hplib import HeatPumpHplib
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters

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

    # Set stsv values for i_simulate   
    number_of_IOs = 11
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_IOs)

    # Create fake component outputs as inputs for simulation
    IO_1 = cp.ComponentOutput("I/O",
                              "I/O",
                              lt.LoadTypes.Any,
                              lt.Units.Any)
    IO_2 = cp.ComponentOutput("I/O",
                              "I/O",
                              lt.LoadTypes.Any,
                              lt.Units.Any)
    IO_3 = cp.ComponentOutput("I/O",
                              "I/O",
                              lt.LoadTypes.Any,
                              lt.Units.Any)
    IO_4 = cp.ComponentOutput("I/O",
                              "I/O",
                              lt.LoadTypes.Any,
                              lt.Units.Any)

    # Initialize component
    heatpump = HeatPumpHplib(model=model, group_id=group_id, t_in=t_in, t_out_val=t_out, p_th_set=p_th_set, my_simulation_parameters=simpars)

    heatpump.on_off_switch.SourceOutput = IO_1
    heatpump.t_in_primary.SourceOutput = IO_2
    heatpump.t_in_secondary.SourceOutput = IO_3
    heatpump.t_amb.SourceOutput = IO_4

    # Inputs
    IO_1.GlobalIndex = 0
    IO_2.GlobalIndex = 1
    IO_3.GlobalIndex = 2
    IO_4.GlobalIndex = 3

    # Inputs initialization
    stsv.values[0] = 1
    stsv.values[1] = -7
    stsv.values[2] = 47.0
    stsv.values[3] = -7 
     
    # Outputs
    heatpump.p_th.GlobalIndex = 4
    heatpump.p_el.GlobalIndex = 5
    heatpump.cop.GlobalIndex = 6
    heatpump.t_out.GlobalIndex = 7
    heatpump.m_dot.GlobalIndex = 8
    heatpump.time_on.GlobalIndex = 9 
    heatpump.time_off.GlobalIndex = 10     
    
    # Simulation
    heatpump.i_simulate(timestep=timestep, stsv=stsv, force_convergence=force_convergence)
    print(stsv.values)
    # Check
    assert p_th_set == stsv.values[4]
    assert 7074.0 == stsv.values[5]
    assert 1.41 == stsv.values[6]
    assert 52 == stsv.values[7]
    assert 0.476 == stsv.values[8]
    assert 60 == stsv.values[9]
    assert 0 == stsv.values[10]
