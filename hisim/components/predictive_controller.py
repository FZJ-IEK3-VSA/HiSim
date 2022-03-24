# Generic/Built-in
from typing import Optional

#Owned
from hisim import component as cp
from hisim import loadtypes as lt
from hisim.components import occupancy
from hisim.components import pvs
from hisim.components import price_signal
from hisim.components import smart_device
from hisim.simulationparameters import SimulationParameters


__authors__ = "Johanna Ganglbauer"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"

class PredictiveController( cp.Component ):
    """
    Predictive controller. It takes data from the dictionary my_simulation_repository
    and decides if device should be activated or not. The predictive controller is a central
    contoller operating device by device following a predefined hierachy
    1. smart appliances
    2. boiler
    3. heating system
    

    Parameters
    --------------
    threshold_price: float
        Maximum price allowed for switch on
    threshold_peak: float or None
        Maximal peak allowed for switch on.
    """

    # Inputs
    SmartApplianceState = "SmartApplianceState"
    
    #Outputs
    SmartApplianceSignal = "SmartApplianceSignal"


    def __init__(self, my_simulation_parameters: SimulationParameters,
                       threshold_price : float = 25,
                       threshold_peak : Optional[ float ] = None ):
        super( ).__init__( "SmartDeviceController", my_simulation_parameters = my_simulation_parameters )
        self.build( threshold_price, threshold_peak )
        
        if my_simulation_parameters.system_config.smart_devices_included:
            #Input
            self.SmartApplianceStateC: cp.ComponentInput = self.add_input( self.ComponentName,
                                                                           self.SmartApplianceState,
                                                                           lt.LoadTypes.Any,
                                                                           lt.Units.Any,
                                                                           mandatory = False )
            #Output
            self.SmartApplianceSignalC: cp.ComponentOutput = self.add_output( self.ComponentName,
                                                                         self.SmartApplianceSignal,
                                                                         lt.LoadTypes.Any,
                                                                         lt.Units.Any )
            self.add_default_connections( smart_device.SmartDevice, self.get_smart_appliance_default_connections( ) )
        
    def get_smart_appliance_default_connections( self ):
        print("setting smart device controller default connections")
        connections = [ ]
        smart_device_classname = smart_device.SmartDevice.get_classname( )
        connections.append( cp.ComponentConnection( PredictiveController.SmartApplianceState, smart_device_classname, smart_device.SmartDevice.SmartApplianceState ) )
        return connections

    def build(self, threshold_price : float, threshold_peak : Optional[ float ], signal : int = 0 ):
        self.threshold_peak = threshold_peak
        self.threshold_price = threshold_price
        self.signal = signal
        self.previous_signal = signal

    def i_save_state(self):
        self.previous_signal = self.signal

    def i_restore_state(self):
        self.signal = self.previous_signal

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues):
        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues,  force_convergence: bool):
        
        #initialize signal
        self.signal = 0
        
        #get device state
        devicestate = stsv.get_input_value( self.SmartApplianceStateC )
        
        #see if device is controllable
        if abs( devicestate ) < 2:
            #get forecasts
            shiftableload = self.simulation_repository.get_entry( smart_device.SmartDevice.ShiftableLoadForecast )
            steps = len( shiftableload )
            
            pvforecast = self.simulation_repository.get_entry( pvs.PVSystem.PV_Forecast_24h )
            demandforecast = self.simulation_repository.get_entry( occupancy.Occupancy.Electricity_Demand_Forecast_24h )[ : steps ]
            priceinjectionforecast = self.simulation_repository.get_entry( price_signal.PriceSignal.Price_Injection_Forecast_24h )[ : steps ]
            pricepurchaseforecast = self.simulation_repository.get_entry( price_signal.PriceSignal.Price_Purchase_Forecast_24h )[ : steps ]
            
            #build total load
            if self.my_simulation_parameters.system_config.pv_included == True:
                totalload = [ a - b for ( a, b ) in zip( demandforecast, pvforecast ) ]
            else:
                totalload = demandforecast
                
            potentialload = [ a + b for ( a, b ) in zip( totalload, shiftableload ) ]
            
            #calculate price
            price_with = [ a * b for ( a, b ) in zip( potentialload, pricepurchaseforecast ) if a > 0 ] \
                + [ a * b for ( a, b ) in zip( potentialload, priceinjectionforecast ) if a < 0 ]
            price_without = [ a * b for ( a, b ) in zip( totalload, pricepurchaseforecast ) if a > 0 ] \
                + [ a * b for ( a, b ) in zip( totalload, priceinjectionforecast ) if a < 0 ]
            price_per_kWh = ( 3.6e6 / self.my_simulation_parameters.seconds_per_timestep ) * ( sum( price_with ) - sum( price_without ) ) / sum( shiftableload )
            print( price_per_kWh, self.threshold_price )
            
            #calculate peak
            peak = max( totalload )
            
            #make decision based on thresholds
            if ( ( not self.threshold_peak ) or peak < self.threshold_peak ) and price_per_kWh < self.threshold_price:
                self.signal = 1
            else:
                self.signal = -1 

        stsv.set_output_value( self.SmartApplianceSignalC, self.signal )