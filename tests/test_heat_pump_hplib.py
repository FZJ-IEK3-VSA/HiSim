import component as cp
import components.heat_pump_hplib as hpl
import loadtypes as lt

def test_heat_pump_hplib():

    # Definitions for HeatPump init
    model: str = "Generic"
    group_id: int = 1
    t_in: float = -7
    t_out: float = 52
    p_th_set: float = 10000

    # Definitions for i_simulate
    timestep = 1
    seconds_per_timestep = 60
    force_convergence = False 

    # Set stsv values for i_simulate   
    number_of_IOs = 12
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
    heatpump = hpl.HeatPumpHplib(model=model, group_id=group_id, t_in=t_in, t_out=t_out, p_th_set=p_th_set)

    heatpump.mode.SourceOutput = IO_1
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
    heatpump.eer.GlobalIndex = 7
    heatpump.t_out.GlobalIndex = 8
    heatpump.m_dot.GlobalIndex = 9
    heatpump.time_on.GlobalIndex = 10 
    heatpump.time_off.GlobalIndex = 11     
    
    # Simulation
    heatpump.i_simulate(timestep=timestep, stsv=stsv, seconds_per_timestep=seconds_per_timestep, force_convergence=force_convergence)
    print(stsv.values)
    # Check
    assert p_th_set == stsv.values[4]
    assert 6850.73996774512 == stsv.values[5]
    assert 1.4596963316491838 == stsv.values[6]
    assert 0 == stsv.values[7]
    assert 52.0 == stsv.values[8]
    assert 0.47619047619047616 == stsv.values[9]
    assert 60 == stsv.values[10]
    assert 0 == stsv.values[11]
