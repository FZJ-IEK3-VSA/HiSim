# Generic/Built-in
import numpy as np
import copy
import matplotlib
import seaborn
from math import pi

# Owned
import hisim.utils as utils
from hisim import component as cp
from hisim.loadtypes import LoadTypes, Units
from hisim.simulationparameters import SimulationParameters
from hisim.components.configuration import WarmWaterStorageConfig
from hisim.components.configuration import PhysicsConfig
from hisim.components.building import Building
from hisim.components.weather import Weather
from hisim.components import controller_l3_predictive
from hisim import log

seaborn.set(style='ticks')
font = {'family' : 'normal',
        'size'   : 24}

matplotlib.rc('font', **font)

__authors__ = "edited Johanna Ganglbauer"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"

class HeatPump(cp.Component):
    """
    Heat pump implementation. It does support a
    refrigeration cycle. Thermal output is delivered straight to
    the component object.

    Parameters
    ----------
    manufacturer : str
        Heat pump manufacturer
    name : str
        Heat pump model
    heating_season_begin : int, optional
        Day( julian day, number of day in year ), when heating season starts - and cooling season ends. The default is 270.
    heating_season_end : int, optional
        Day( julian day, number of day in year ), when heating season ends - and cooling season starts. The default is 150
    """
    # Inputs
    HeatPumpControllerState = "HeatPumpControllerState"
    TemperatureOutside = "TemperatureOutside"

    # Outputs
    ThermalEnergyDelivered = "ThermalEnergyDelivered"
    ElectricityOutput = "ElectricityOutput"
    HeatPumpPowerPotential = "HeatPumpPowerPotential"

    # Similar components to connect to:
    # 1. Weather
    # 2. HeatPumpController
    @utils.measure_execution_time
    def __init__( self,
                  my_simulation_parameters: SimulationParameters,
                  manufacturer : str = "Viessmann Werke GmbH & Co KG",
                  name : str ="Vitocal 300-A AWO-AC 301.B07",
                  heating_season_begin : int = 270,
                  heating_season_end : int = 150 ):
        super().__init__( "HeatPump", my_simulation_parameters = my_simulation_parameters )

        self.build( manufacturer, name, heating_season_begin, heating_season_end )

        # Inputs - Mandatories
        self.HeatPumpControllerStateC: cp.ComponentInput = self.add_input( self.ComponentName,
                                                                           self.HeatPumpControllerState,
                                                                           LoadTypes.Any,
                                                                           Units.Any,
                                                                           True )
        self.t_outC: cp.ComponentInput = self.add_input( self.ComponentName,
                                                         self.TemperatureOutside,
                                                         LoadTypes.Any,
                                                         Units.Celsius,
                                                         True )
        
        #Outputs
        self.thermal_energy_deliveredC: cp.ComponentOutput = self.add_output( self.ComponentName,
                                                                              self.ThermalEnergyDelivered,
                                                                              LoadTypes.Heating,
                                                                              Units.Watt )
        self.electricity_outputC: cp.ComponentOutput = self.add_output( self.ComponentName,
                                                                        self.ElectricityOutput,
                                                                        LoadTypes.Electricity,
                                                                        Units.Watt )
        if self.my_simulation_parameters.system_config.predictive == True:
            self.heat_pump_power_potentialC: cp.ComponentOutput = self.add_output( self.ComponentName,
                                                                          self.HeatPumpPowerPotential,
                                                                          LoadTypes.Electricity,
                                                                          Units.Watt )
            
        self.add_default_connections( Weather, self.get_weather_default_connections( ) )
        self.add_default_connections( HeatPumpController, self.get_controller_default_connections( ) )
        
    def get_weather_default_connections( self ):
        log.information("setting weather default connections in HeatPump")
        connections = [ ]
        weather_classname = Weather.get_classname( )
        connections.append( cp.ComponentConnection( HeatPump.TemperatureOutside, weather_classname, Weather.TemperatureOutside ) )
        return connections
    
    def get_controller_default_connections( self ):
        log.information("setting heat pump controller default connections in HeatPump")
        connections = [ ]
        controller_classname = HeatPumpController.get_classname( )
        connections.append( cp.ComponentConnection( HeatPump.HeatPumpControllerState, controller_classname, HeatPumpController.HeatPumpControllerState ) )
        return connections

    def build( self, manufacturer, name, heating_season_begin, heating_season_end ):

        # Retrieves heat pump from database - BEGIN
        heat_pumps_database = utils.load_smart_appliance("Heat Pump")

        heat_pump_found = False
        for heat_pump in heat_pumps_database:
            if heat_pump["Manufacturer"] == manufacturer and heat_pump["Name"] == name:
                heat_pump_found = True
                break

        if heat_pump_found == False:
            raise Exception("Heat pump model not registered in the database")

        # Interpolates COP data from the database
        self.cop_ref = []
        self.t_out_ref = []
        for heat_pump_cops in heat_pump['COP']:
            self.t_out_ref.append(float([*heat_pump_cops][0][1:].split("/")[0]))
            self.cop_ref.append(float([*heat_pump_cops.values()][0]))
        self.cop_coef = np.polyfit(self.t_out_ref, self.cop_ref, 1)

        self.max_heating_power = heat_pump['Nominal Heating Power A2/35'] * 1E3
        
        self.heating_season_begin = heating_season_begin * 24 * 3600 / self.my_simulation_parameters.seconds_per_timestep
        self.heating_season_end = heating_season_end * 24 * 3600 / self.my_simulation_parameters.seconds_per_timestep

        # Writes info to report
        self.write_to_report()

    def cal_cop( self, t_out ):
        return self.cop_coef[0] * t_out + self.cop_coef[1]

    def i_save_state(self):
        pass

    def i_restore_state(self):
        pass

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues):
        pass

    def write_to_report(self):
        lines = []
        lines.append("Name: {}".format("Heat Pump"))
        lines.append("Max power: {:4.0f} kW".format((self.max_heating_power)*1E-3))
        return lines

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues,  force_convergence: bool):
        
        # Inputs
        signal = stsv.get_input_value( self.HeatPumpControllerStateC )
        t_out = stsv.get_input_value( self.t_outC )
        
        #on/off
        if signal > 0:
            signal = 1
        else:
            signal = 0
            
        #cop
        cop = self.cal_cop( t_out )
        
        # write values for output time series
        #cooling season
        if timestep < self.heating_season_begin and timestep > self.heating_season_end:
            stsv.set_output_value( self.thermal_energy_deliveredC, - signal * self.max_heating_power )
        #heating season
        else:
            stsv.set_output_value( self.thermal_energy_deliveredC, signal * self.max_heating_power )
        
        stsv.set_output_value( self.electricity_outputC, signal * self.max_heating_power / cop )
        
        if self.my_simulation_parameters.system_config.predictive == True:
            stsv.set_output_value( self.heat_pump_power_potentialC, self.max_heating_power / cop )
        
        
class ControllerState:
    """
    This data class saves the state of the controller.
    """

    def __init__( self, state : int = 0, timestep_of_last_action : int = -999 ):
        self.state = state
        self.timestep_of_last_action = timestep_of_last_action
        
    def clone( self ):
        return ControllerState( state = self.state, timestep_of_last_action = self.timestep_of_last_action )


class HeatPumpController( cp.Component ):
    
    """
    Heat Pump Controller. It takes data from other
    components and sends signal to the heat pump for
    activation or deactivation.

    Parameters
    --------------
    T_min_heating: float, optional
        Minimum comfortable temperature for residents during heating period, in °C. The default is 19 °C.
    T_max_heating: float, optional
        Minimum comfortable temperature for residents during heating period, in °C. The default is 23 °C.
    T_min_cooling: float, optional
        Minimum comfortable temperature for residents during cooling period, in °C. The default is 23 °C.
    T_max_cooling: float, optional
        Minimum comfortable temperature for residents during cooling period, in °C. The default is 26 °C.
    min_running_time: int, optional
        Minimal running time of device, in seconds. The default is 3600 seconds.
    min_idle_time : int, optional
        Minimal off time of device, in seconds. The default is 900 seconds.
    heating_season_begin : int, optional
        Day( julian day, number of day in year ), when heating season starts - and cooling season ends. The default is 270.
    heating_season_end : int, optional
        Day( julian day, number of day in year ), when heating season ends - and cooling season starts. The default is 150
    """
    # Inputs
    TemperatureMean = "ResidenceTemperature"
    HeatPumpPowerPotential = "HeatPumpPowerPotential"
    HeatPumpSignal = "HeatPumpSignal"

    # Outputs
    HeatPumpControllerState = "HeatPumpControllerState"
    
    #Forecasts
    HeatPumpLoadForecast = "HeatPumpLoadForecast"

    # Similar components to connect to:
    # 1. Building
    @utils.measure_execution_time
    def __init__( self, 
                  my_simulation_parameters : SimulationParameters,
                  T_min_heating : float = 19.0,
                  T_max_heating : float = 23.0,
                  T_min_cooling : float = 23.0,
                  T_max_cooling : float = 26.0,
                  min_operation_time : int = 3600,
                  min_idle_time : int = 900,
                  heating_season_begin : int = 270,
                  heating_season_end : int = 150 ):
        super().__init__( "HeatPumpController", my_simulation_parameters = my_simulation_parameters )
        self.build( T_min_heating, T_max_heating, T_min_cooling, T_max_cooling, min_operation_time, 
                    min_idle_time, heating_season_begin, heating_season_end )

        self.t_mC: cp.ComponentInput = self.add_input( self.ComponentName,
                                                       self.TemperatureMean,
                                                       LoadTypes.Temperature,
                                                       Units.Celsius,
                                                       mandatory = True )
        if self.my_simulation_parameters.system_config.predictive == True:  
            self.HeatPumpPowerPotentialC : cp.ComponentInput = self.add_input( self.ComponentName,
                                                                      self.HeatPumpPowerPotential,
                                                                      LoadTypes.Electricity,
                                                                      Units.Watt,
                                                                      mandatory = False )
            self.HeatPumpSignalC : cp.ComponentInput = self.add_input( self.ComponentName,
                                                                       self.HeatPumpSignal,
                                                                       LoadTypes.Any,
                                                                       Units.Any,
                                                                       mandatory = False )
            self.add_default_connections( HeatPump, self.get_heat_pump_default_connections( ) )
            self.add_default_connections( controller_l3_predictive.PredictiveController, self.get_predictive_controller_default_connections( ))
        self.HeatPumpControllerStateC: cp.ComponentOutput = self.add_output( self.ComponentName,
                                                                              self.HeatPumpControllerState,
                                                                              LoadTypes.Any,
                                                                              Units.Any)
        
        self.add_default_connections( Building, self.get_building_default_connections( ) )
        
    def get_building_default_connections( self ):
        log.information("setting building default connections in Heatpumpcontroller")
        connections = [ ]
        building_classname = Building.get_classname( )
        connections.append( cp.ComponentConnection( HeatPumpController.TemperatureMean, building_classname, Building.TemperatureMean ) )
        return connections
    
    def get_heat_pump_default_connections( self ):
        log.information("setting heat pump default connections in Heatpumpcontroller")
        connections = [ ]
        heat_pump_classname = HeatPump.get_classname( )
        connections.append( cp.ComponentConnection( HeatPumpController.HeatPumpPowerPotential, heat_pump_classname, HeatPump.HeatPumpPowerPotential ) )
        return connections
    
    def get_predictive_controller_default_connections( self ):
        log.information("setting predictive controller default connections in Heatpumpcontroller")
        connections = [ ]
        predictive_controller_classname = controller_l3_predictive.PredictiveController.get_classname( )
        connections.append( cp.ComponentConnection( HeatPumpController.HeatPumpSignal, predictive_controller_classname, controller_l3_predictive.PredictiveController.HeatingDeviceSignal ) )
        return connections

    def build( self, T_min_heating, T_max_heating, T_min_cooling, T_max_cooling, min_operation_time, min_idle_time, heating_season_begin, heating_season_end ):
        
        self.T_min_heating = T_min_heating
        self.T_max_heating = T_max_heating
        self.T_min_cooling = T_min_cooling
        self.T_max_cooling = T_max_cooling
        self.on_time = int( min_operation_time / self.my_simulation_parameters.seconds_per_timestep )
        self.off_time = int( min_idle_time / self.my_simulation_parameters.seconds_per_timestep )
        self.heating_season_begin = heating_season_begin * 24 * 3600 / self.my_simulation_parameters.seconds_per_timestep
        self.heating_season_end = heating_season_end * 24 * 3600 / self.my_simulation_parameters.seconds_per_timestep
        
        self.state = ControllerState( )
        self.previous_state = ControllerState( )
        
    def activation( self, timestep, soft = True ):
        self.state.state = 2
        self.state.timestep_of_last_action = timestep
        #violently access previous timestep to avoid oscillation between 0 and 1 (decision is based on decision of previous time step)
        self.previous_state = self.state.clone( )

    def deactivation( self, timestep, soft = True ):
        self.state.state = -2
        self.state.timestep_of_last_action = timestep 
        #violently access previous timestep to avoid oscillation between 0 and 1 (decision is based on decision of previous time step)
        self.previous_state = self.state.clone( )

    def i_save_state(self):
        self.previous_state = self.state.clone( )

    def i_restore_state(self):
        self.state = self.previous_state.clone( )

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues):
        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues,  force_convergence: bool):
        # check demand, and change state of self.has_heating_demand, and self._has_cooling_demand
        if force_convergence:
            pass
        
        #continue running or being off if that is necesary
        if ( self.state.state == 2 and timestep < self.state.timestep_of_last_action + self.on_time ) or \
             ( self.state.state == -2 and timestep < self.state.timestep_of_last_action + self.off_time ):
            pass
        
        else:
            # Retrieves inputs
            T_control = stsv.get_input_value( self.t_mC )
            
            #check out during cooling season
            if timestep < self.heating_season_begin and timestep > self.heating_season_end:
                #on off control based on temperature limits
                if T_control > self.T_max_cooling:
                    #start cooling if temperature exceeds upper limit
                    if self.state.state <= 0:
                        self.activation( timestep )
    
                elif T_control < self.T_min_cooling:
                    #stop cooling if temperature goes below lower limit
                    if self.state.state >= 0:
                        self.deactivation( timestep )
                        
                else:
                    #continue working if other is not defined 
                    if self.state.state > 0:
                        self.state.state = 1
                    if self.state.state < 0:
                        self.state.state = -1
            
            #check out during heating season
            else:
                #on off control based on temperature limits
                if T_control > self.T_max_heating:
                    #stop heating if temperature exceeds upper limit
                    if self.state.state >= 0:
                        self.deactivation( timestep = timestep, soft = False )
    
                elif T_control < self.T_min_heating:
                    #start heating if temperature goes below lower limit
                    if self.state.state <= 0:
                        self.activation( timestep = timestep, soft = False )

                else:
                    #continue working if other is not defined
                    if self.state.state > 0:
                        self.state.state = 1
                    if self.state.state < 0:
                        self.state.state = -1
        
            if self.my_simulation_parameters.system_config.predictive:
                P_on = stsv.get_input_value( self.HeatPumpPowerPotentialC )
                #put forecast into dictionary
                if self.state.state > 0:
                    self.simulation_repository.set_entry( self.HeatPumpLoadForecast, [ P_on ] * max( 1, self.on_time - timestep + self.state.timestep_of_last_action ) )
                else:
                    self.simulation_repository.set_entry( self.HeatPumpLoadForecast, [ P_on ] * self.on_time )
                    
                #read in signal and modify state if recommended
                devicesignal = stsv.get_input_value( self.HeatPumpSignalC )
                if self.state.state == 1 and devicesignal == -1:
                    self.deactivation( timestep )
                    
                elif self.state.state == -1 and devicesignal == 1:
                    self.activation( timestep )
        
        stsv.set_output_value( self.HeatPumpControllerStateC, self.state.state )

    def prin1t_outpu1t(self, t_m, state):
        log.information("==========================================")
        log.information("T m: {}".format(t_m))
        log.information("State: {}".format(state))

