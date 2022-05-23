# -*- coding: utf-8 -*-
"""
Created on Tue Apr 26 12:59:48 2022

@author: Johanna
"""

# Generic/Built-in
from typing import Optional, List

#Owned
from hisim import log
from hisim import component as cp
from hisim import loadtypes as lt
from hisim.components import loadprofilegenerator_connector
from hisim.components import generic_pv_system
from hisim.components import generic_price_signal
from hisim.components import generic_smart_device_2
# from hisim.components import generic_dhw_boiler
from hisim.components import generic_district_heating
from hisim.components import controller_l1_generic_runtime
from hisim.components import generic_heat_pump_modular
from hisim.simulationparameters import SimulationParameters, SystemConfig

__authors__ = "Johanna Ganglbauer"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"

def price_and_peak( totalload : List, shiftableload : List, pricepurchaseforecast : List, priceinjectionforecast : List ):
    """calculate price per kWh of device which is activated and maximal load peak
    
    Parameters:
    -----------
    totalload : list
        Original load excluding device (demand is positive, surplus negative).
    shifableload : list
        Load of device which activation is tested.
    pricepurchaseforecast : list
        Forecast of the pricesignal for purchase.
    priceinjectionforecast : list
        Forecast of the pricesignal for injection.
    """
    
    #calculate load when device is switched on
    potentialload = [ a + b for ( a, b ) in zip( totalload, shiftableload ) ]
    
    #calculate price
    price_with = [ a * b for ( a, b ) in zip( potentialload, pricepurchaseforecast ) if a > 0 ] \
        + [ a * b for ( a, b ) in zip( potentialload, priceinjectionforecast ) if a < 0 ]
    price_without = [ a * b for ( a, b ) in zip( totalload, pricepurchaseforecast ) if a > 0 ] \
        + [ a * b for ( a, b ) in zip( totalload, priceinjectionforecast ) if a < 0 ]
    price_per_kWh = ( sum( price_with ) - sum( price_without ) ) / sum( shiftableload )
    
    #calculate peak
    peak = max( totalload )
    
    return price_per_kWh, peak

class ControllerSignal:
    """class to save predictive output signal from predictive controller
        -1 shut off device if possible
          0 evalueation is not needed
          1 turn on device if possible
    """
    
    def __init__( self, signal : List = [ ] ):
        self.signal = signal
        
    def clone( self ):
        return ControllerSignal( signal = self.signal ) 
        
class L3_Controller( cp.DynamicComponent ):
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

    # Inputs and Outputs
    MyComponentInputs: List[ cp.DynamicConnectionInput ] = []
    MyComponentOutputs: List[ cp.DynamicConnectionOutput ] = []

    def __init__(self, my_simulation_parameters: SimulationParameters,
                        threshold_price : float = 25,
                        threshold_peak : Optional[ float ] = None ):
        
        super( ).__init__(  my_component_inputs = self.MyComponentInputs,
                            my_component_outputs = self.MyComponentOutputs,
                            name = "L3Controller", 
                            my_simulation_parameters = my_simulation_parameters )
        
        self.build( threshold_price, threshold_peak )

    def build( self, threshold_price : float, threshold_peak : Optional[ float ] ):
        self.threshold_peak = threshold_peak
        self.threshold_price = threshold_price
        self.signal = ControllerSignal( )
        self.previous_signal = ControllerSignal( )
        
    def decision_maker( self, price_per_kWh, peak ):   
        if ( ( not self.threshold_peak ) or peak < self.threshold_peak ) and price_per_kWh < self.threshold_price:
            return 1
        else:
            return 0 

    def i_save_state( self ):
        self.previous_signal = self.signal.clone( )

    def i_restore_state( self ):
        self.signal= self.previous_signal.clone( )

    def i_doublecheck( self, timestep: int, stsv: cp.SingleTimeStepValues):
        pass

    def i_simulate( self, timestep: int, stsv: cp.SingleTimeStepValues,  force_convergence: bool ):
        
        totalload = self.simulation_repository.get_entry( loadprofilegenerator_connector.Occupancy.Electricity_Demand_Forecast_24h )
        priceinjectionforecast = self.simulation_repository.get_entry( generic_price_signal.PriceSignal.Price_Injection_Forecast_24h )
        pricepurchaseforecast = self.simulation_repository.get_entry( generic_price_signal.PriceSignal.Price_Purchase_Forecast_24h )
        
        
        #substract PV production from laod, if available
        for elem in self.simulation_repository.get_dynamic_component_weights( component_type = lt.ComponentType.PV ) :
            pvforecast = self.simulation_repository.get_dynamic_entry( component_type = lt.ComponentType.PV, source_weight = elem )
            totalload = [ a - b for ( a, b ) in zip( totalload, pvforecast ) ]      
        

        #initialize device signals
        signal = [ ]
        
        #loops over components -> also fixes hierachy in control
        for component_type in [ lt.ComponentType.Boiler, lt.ComponentType.HeatPump ]:
        
            devicestate = 0
            weight_counter = 1
            
            #loop over all source weights, breaks if one is missing
            while devicestate is not None:
                
                #try if input is available -> returns None if not
                devicestate = self.get_dynamic_input( stsv = stsv,
                                                      component_type = component_type,
                                                      weight_counter = weight_counter )
                print( timestep, component_type, weight_counter, devicestate )
                
                #control if input was available
                if devicestate is not None:
                    shiftableload = self.simulation_repository.get_dynamic_entry( component_type = component_type, source_weight = weight_counter )
                    steps = len( shiftableload )
                    
                    #calculate price and peak and get controller signal
                    price_per_kWh, peak = price_and_peak( totalload[ : steps ], shiftableload, pricepurchaseforecast[ : steps ], priceinjectionforecast[ : steps ] )
                    signal.append( self.decision_maker( price_per_kWh = price_per_kWh, peak = peak ) )
                        
                    #recompute base load if device was activated
                    if devicestate == 1:
                        totalload = [ a + b for ( a, b ) in zip( totalload[ : steps ], shiftableload ) ] + totalload[ steps : ]
                        
                    self.set_dynamic_output( stsv = stsv, 
                                             component_type = component_type,
                                             weight_counter = weight_counter,
                                             output_value = signal[ - 1 ] )
                #count up    
                weight_counter += 1
                
        self.signal = ControllerSignal( signal = signal )
            
       