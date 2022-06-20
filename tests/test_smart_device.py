from hisim import component as cp
from hisim.components import generic_heat_pump
from hisim.components import generic_smart_device
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim import utils
from tests import functions_for_testing as fft

import csv

def test_smart_device():
    """
    Test time shifting for smart devices
    """
    #initialize simulation parameters
    mysim: SimulationParameters = SimulationParameters.full_year( year=2021,
                                                                 seconds_per_timestep = 60 ) 
    #read in first available smart device
    filepath = utils.HISIMPATH[ "smart_devices" ][ "device_collection" ] 
    
    with open( filepath, 'r' ) as f:
        i = 0
        formatreader = csv.reader( f, delimiter = ';' )
        for line in formatreader:
            if i > 1:
                device = line[ 0 ]
            i += 1
     
    #create smart_device        
    my_smart_device = generic_smart_device.SmartDevice( identifier = device, source_weight = 0, my_simulation_parameters = mysim )
    
    #get first activation and corrisponding profile from data (SmartDevice Class reads in data )
    activation = my_smart_device.latest_start[ 0 ]
    profile = my_smart_device.electricity_profile[ 0 ]

    #assign outputs correctly
    number_of_outputs = 1
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)
    my_smart_device.ElectricityOutputC.GlobalIndex = 0
    
    # Simulate and check that (a) device is activated at latest possible starting point, (b) device runs with the defined power profile
    my_smart_device.i_restore_state()
    for j in range( activation + len( profile ) ):
        my_smart_device.i_simulate( j, stsv, False )
        if j >= activation:
            assert stsv.values[ 0 ] == profile[ j - activation ]

