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
    
    def __init__( self, smart_device_signal : int = 0, boiler_signal : int = 0, heat_pump_signal : int = 0 ):
        self.smart_device_signal = smart_device_signal
        self.boiler_signal = boiler_signal
        self.heat_pump_signal = heat_pump_signal
        
    def clone( self ):
        return ControllerSignal( smart_device_signal = self.smart_device_signal, boiler_signal = self.boiler_signal, heat_pump_signal = self.heat_pump_signal ) 
        
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

    # Inputs
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
        
        if my_simulation_parameters.system_config.smart_devices_included:
            pass
            # #Input
            # self.SmartApplianceStateC: cp.ComponentInput = self.add_input( self.ComponentName,
            #                                                                 self.SmartApplianceState,
            #                                                                 lt.LoadTypes.Any,
            #                                                                 lt.Units.Any,
            #                                                                 mandatory = False )
            # #Output
            # self.SmartApplianceSignalC: cp.ComponentOutput = self.add_output( self.ComponentName,
            #                                                                   self.SmartApplianceSignal,
            #                                                                   lt.LoadTypes.Any,
            #                                                                   lt.Units.Any )
            # self.add_default_connections( generic_smart_device_2.SmartDevice, self.get_smart_appliance_default_connections( ) )
            
        if my_simulation_parameters.system_config.boiler_included:
            pass
            # #Input
            # self.BoilerControllerStateC: cp.ComponentInput = self.add_input( self.ComponentName,
            #                                                                   self.BoilerControllerState,
            #                                                                   lt.LoadTypes.Any,
            #                                                                   lt.Units.Any,
            #                                                                   mandatory = False )
            # #Output
            # self.BoilerSignalC: cp.ComponentOutput = self.add_output( self.ComponentName,
            #                                                           self.BoilerSignal,
            #                                                           lt.LoadTypes.Any,
            #                                                           lt.Units.Any )
            # self.add_default_connections( generic_dhw_boiler.BoilerController, self.get_boiler_controller_default_connections( ) )
            
        # heatingchoice = my_simulation_parameters.system_config.heating_device_included
        # if heatingchoice == 'heat_pump':
        #     #inputs
        #     self.l1_HeatPumpSignalC: cp.ComponentInput = self.add_input(    self.ComponentName,
        #                                                                     self.l1_HeatPumpSignal,
        #                                                                     lt.LoadTypes.OnOff,
        #                                                                     lt.Units.binary,
        #                                                                     mandatory = True )
        #     #outputs
        #     self.l3_HeatPumpSignalC: cp.ComponentOutput = self.add_output(  self.ComponentName,
        #                                                                     self.l3_HeatPumpSignal,
        #                                                                     lt.LoadTypes.OnOff,
        #                                                                     lt.Units.binary )
            
        #     self.add_default_connections( controller_l1_generic_runtime.L1_Controller, self.get_l1_controller_default_connections( ) )
            # elif heatingchoice == 'oil_heater':
            #     self.add_default_connections( generic_oil_heater.OilHeaterController, self.get_oil_heater_controller_default_connections( ) )
        
    # def get_smart_appliance_default_connections( self ):
    #     log.information( "setting smart appliance default connections" )
    #     connections = [ ]
    #     smart_device_classname = generic_smart_device_2.SmartDevice.get_classname( )
    #     connections.append( cp.ComponentConnection( PredictiveController.SmartApplianceState, smart_device_classname, 
    #                                                 generic_smart_device_2.SmartDevice.SmartApplianceState ) )
    #     return connections
    
    # def get_boiler_controller_default_connections( self ):
    #     log.information( "setting boiler controller default connections" )
    #     connections = [ ]
    #     boiler_controller_classname = generic_dhw_boiler.BoilerController.get_classname( )
    #     connections.append( cp.ComponentConnection( PredictiveController.BoilerControllerState, boiler_controller_classname, 
    #                                                 generic_dhw_boiler.BoilerController.BoilerControllerState ) )
    #     return connections
    
    # def get_oil_heater_controller_default_connections( self ):
    #     log.information( "setting oil heater controller default connections" )
    #     connections = [ ]
    #     oil_heater_controller_classname = generic_oil_heater.OilHeaterController.get_classname( )
    #     connections.append( cp.ComponentConnection( PredictiveController.HeatingDeviceControllerState, oil_heater_controller_classname, 
    #                                                 generic_oil_heater.OilHeaterController.OilHeaterControllerState ) )
    #     return connections
    
    def get_l1_controller_default_connections( self ):
        log.information( "setting heat pump default connections in l3" ) 
        controller_classname = controller_l1_generic_runtime.L1_Controller.get_classname( )
        connections = [ ]
        connections.append( cp.ComponentConnection( L3_Controller.l1_HeatPumpSignal, controller_classname, 
                                                    controller_l1_generic_runtime.L1_Controller.l1_DeviceSignal ) )
        return connections

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

    def i_save_state(self):
        self.previous_signal = self.signal.clone( )

    def i_restore_state(self):
        self.signal= self.previous_signal.clone( )

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues):
        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues,  force_convergence: bool):
        
        demandforecast = self.simulation_repository.get_entry( loadprofilegenerator_connector.Occupancy.Electricity_Demand_Forecast_24h )
        priceinjectionforecast = self.simulation_repository.get_entry( generic_price_signal.PriceSignal.Price_Injection_Forecast_24h )
        pricepurchaseforecast = self.simulation_repository.get_entry( generic_price_signal.PriceSignal.Price_Purchase_Forecast_24h )
        
        #build total load
        if self.my_simulation_parameters.system_config.pv_included == True:
            pvforecast = self.simulation_repository.get_entry( generic_pv_system.PVSystem.PV_Forecast_24h )
            totalload = [ a - b for ( a, b ) in zip( demandforecast, pvforecast ) ]
        else:
            totalload = demandforecast
        
        #check smart appliance if available
        if self.my_simulation_parameters.system_config.smart_devices_included:
            pass
            
            # #get device state
            # devicestate = stsv.get_input_value( self.SmartApplianceStateC )
            
            # #see if device is controllable
            # if abs( devicestate ) < 2:
            #     #get forecast of device
            #     shiftableload = self.simulation_repository.get_entry( generic_smart_device_2.SmartDevice.ShiftableLoadForecast )
            #     steps = len( shiftableload )
                
            #     #calculate price and peak and get controller signal
            #     price_per_kWh, peak = price_and_peak( totalload[ : steps ], shiftableload, pricepurchaseforecast[ : steps ], priceinjectionforecast[ : steps ] )
            #     self.signal.smart_device_signal = self.decision_maker( price_per_kWh, peak )
                
            #     #update totalload for next device
            #     if devicestate == 2 or ( devicestate == 1 and self.signal.smart_device_signal == 1 ):
            #         totalload = [ a + b for ( a, b ) in zip( totalload[ : steps ], shiftableload ) ] + totalload[ steps : ]
            
            # else:
            #     #return untouched
            #     self.signal.smart_device_signal = 0
    
            # stsv.set_output_value( self.SmartApplianceSignalC, self.signal.smart_device_signal )
            
        #check boiler if available and elctricity driven
        if self.my_simulation_parameters.system_config.boiler_included == 'electricity' :
            pass
            
            # #get device state
            # devicestate = stsv.get_input_value( self.BoilerControllerStateC )
            
            # #get forecast of device
            # if devicestate != -2:
            #     shiftableload = self.simulation_repository.get_entry( generic_dhw_boiler.BoilerController.BoilerLoadForecast )
            #     steps = len( shiftableload )
                
            
            # #see if device is controllable
            # if abs( devicestate ) < 2:
                
            #     #calculate price and peak and get controller signal
            #     price_per_kWh, peak = price_and_peak( totalload[ : steps ], shiftableload, pricepurchaseforecast[ : steps ], priceinjectionforecast[ : steps ] )
            #     self.signal.boiler_signal = self.decision_maker( price_per_kWh, peak )
            
            # else:
            #     #return untouched
            #     self.signal.boiler_signal = 0
                
            # #update totalload for next device
            # if devicestate == 2 or ( abs( devicestate ) == 1 and self.signal.boiler_signal == 1 ) :
            #     totalload = [ a + b for ( a, b ) in zip( totalload[ : steps ], shiftableload ) ] + totalload[ steps : ]
    
            # stsv.set_output_value( self.BoilerSignalC, self.signal.boiler_signal )
        component_type = lt.ComponentType.HeatPump
        weight_counter = 1
        
        
        flag = False
            
        #try if input is available -> flag is False if not
        devicestate, flag = self.get_dynamic_input( stsv = stsv,
                                                    component_type = lt.ComponentType.HeatPump,
                                                    weight_counter = 1 )
        
        if flag == True:
            shiftableload = self.simulation_repository.get_entry( generic_heat_pump_modular.HeatPump.HeatPumpLoadForecast )
            steps = len( shiftableload )
            
            #calculate price and peak and get controller signal
            price_per_kWh, peak = price_and_peak( totalload[ : steps ], shiftableload, pricepurchaseforecast[ : steps ], priceinjectionforecast[ : steps ] )
            signal = self.decision_maker( price_per_kWh = price_per_kWh, peak = peak )
                
            #recompute base load if device was activated
            if devicestate == 1:
                totalload = [ a + b for ( a, b ) in zip( totalload[ : steps ], shiftableload ) ] + totalload[ steps : ]
                
            self.set_dynamic_output( stsv = stsv, 
                                     component_type = lt.ComponentType.HeatPump,
                                     weight_counter = 1,
                                     output_value = signal )
            
        # #set output value:         
        # for index, element in enumerate( self.MyComponentOutputs ): #loop over all outputs
        #     for tag in element.SourceTags: #loop over tags, one is lt.ComponentType, other is lt.InandOutputType
        #         if tag.__class__ == lt.ComponentType: #enter if tag is component type
        #             if tag == component_type and element.SourceWeight == weight_counter : #enter if ComponentType and sourceweight match
        #                 stsv.set_output_value( self.__getattribute__( element.SourceComponentClass ), signal ) 
        #                 break
        #             else:
        #                 continue
        #         else:
        #             continue
                

                        
        
        
        # if self.my_simulation_parameters.system_config.heating_device_included == 'heat_pump' :
            
        #     #get forecast of device
        #     # if self.my_simulation_parameters.system_config.heating_device_included == 'oil_heater':
        #     #     shiftableload = self.simulation_repository.get_entry( generic_oil_heater.OilHeaterController.OilHeaterLoadForecast )
        #     shiftableload = self.simulation_repository.get_entry( generic_heat_pump_modular.HeatPump.HeatPumpLoadForecast )
        #     steps = len( shiftableload )
            
        #     #calculate price and peak and get controller signal
        #     price_per_kWh, peak = price_and_peak( totalload[ : steps ], shiftableload, pricepurchaseforecast[ : steps ], priceinjectionforecast[ : steps ] )
        #     self.signal.heat_pump_signal = self.decision_maker( price_per_kWh, peak )
            
        #     stsv.set_output_value( self.l3_HeatPumpSignalC, self.signal.heat_pump_signal )
                            
        #     #get device state and update totalload for next device
        #     devicestate = stsv.get_input_value( self.l1_HeatPumpSignalC )
            
        #     # to do: add that to much chp-electricty is charged in Battery and doesnt go in to grid
        #     for index, element in enumerate(MyComponentOutputs):
        #         for tags in element.SourceTags:
        #             if tags.__class__ == lt.ComponentType and tags in component_type:
        #                 if element.SourceWeight == weight_counter:
        #                     # more electricity than needed
        #                     if tags ==lt.ComponentType.Battery:
        #                         stsv.set_output_value(self.__getattribute__(element.SourceComponentClass), demand)
        #                         break
        #                     elif tags ==lt.ComponentType.FuelCell:
        #                         if demand < 0:
        #                             stsv.set_output_value(self.__getattribute__(element.SourceComponentClass), -demand)
        #                         else:
        #                             stsv.set_output_value(self.__getattribute__(element.SourceComponentClass), 0)
        #                         break
        #         else:
        #             continue
        #         break
            
            
            
        #     if devicestate == 1:
        #         totalload = [ a + b for ( a, b ) in zip( totalload[ : steps ], shiftableload ) ] + totalload[ steps : ]