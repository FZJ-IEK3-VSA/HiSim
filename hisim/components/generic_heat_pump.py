# Generic/Built-in
import numpy as np
import copy
import matplotlib
import seaborn
from math import pi
from typing import List, Any, Optional
# Owned
import hisim.utils as utils
from hisim.components.weather import Weather
from hisim import component as cp
from hisim.loadtypes import LoadTypes, Units
from hisim.simulationparameters import SimulationParameters
#from hisim.components.extended_storage import WaterSlice
#from hisim.components.configuration import WarmWaterStorageConfig
#from hisim.components.configuration import PhysicsConfig
from hisim.components.building import Building
from hisim.components.weather import Weather
from hisim import log

seaborn.set(style='ticks')
font = {'family' : 'normal',
        'size'   : 24}

matplotlib.rc('font', **font)

__authors__ = "Vitor Hugo Bellotto Zago"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"

class GenericHeatPumpState:

    def __init__(self,
                 start_timestep: int=0,
                 thermal_energy_delivered: float = 0.0,
                 cop:float = 1.0,
                 cycle_number:Optional[int]=None) -> None:
        self.start_timestep=start_timestep
        self.thermal_energy_delivered = thermal_energy_delivered
        self.cycle_number = cycle_number
        if thermal_energy_delivered == 0.0:
            self.activation = 0
            self.heating = 0.0
            self.cooling = 0.0
            self.cop = 1.0
            self.electricity_in = abs(self.thermal_energy_delivered / self.cop)
        elif self.thermal_energy_delivered > 0.0:
            self.activation = -1
            self.heating = self.thermal_energy_delivered
            self.cooling = 0.0
            self.cop = cop
            self.electricity_in = abs(self.thermal_energy_delivered / self.cop)
        elif self.thermal_energy_delivered < 0.0:
            self.activation = 1
            self.heating = 0
            self.cooling = self.thermal_energy_delivered
            self.cop = cop
            self.electricity_in = abs(self.thermal_energy_delivered / self.cop)
        else:
            raise Exception("Impossible Heat Pump State.")
            
    def clone(self) -> Any:
        return GenericHeatPumpState(self.start_timestep, self.thermal_energy_delivered, self.cop, self.cycle_number)

class GenericHeatPump(cp.Component):
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
    min_operation_time : int, optional
        Minimum time duration that the heat pump operates under one cycle, in seconds. The default is 3600.
    min_idle_time : int, optional
        Minimum time duration that the heat pump has to stay idle, in seconds. The default is 900.
    """
    # Inputs
    State = "State"
    TemperatureOutside = "TemperatureOutside"
    WaterConsumption = "WaterConsumption"
    WaterInput_mass = "WaterInput_mass"                             # kg/s
    WaterInput_temperature = "WaterInput_temperature"               # °C

    # Outputs
    #WaterOutput_mass = "WaterOutput_mass"                           # kg/s
    #WaterOutput_temperature = "WaterOutput_temperature"             # °C
    #WastedEnergyMaxTemperature = "Wasted Energy Max Temperature"    # W

    ThermalEnergyDelivered = "ThermalEnergyDelivered"
    Heating = "Heating"
    Cooling = "Cooling"
    ElectricityOutput = "ElectricityOutput"
    NumberOfCycles = "NumberOfCycles"

    # Similar components to connect to:
    # 1. Weather
    # 2. HeatPumpController
    @utils.measure_execution_time
    def __init__(self,
                 my_simulation_parameters: SimulationParameters,
                 manufacturer : str = "Viessmann Werke GmbH & Co KG",
                 name : str ="Vitocal 300-A AWO-AC 301.B07",
                 min_operation_time : int = 60 * 60,
                 min_idle_time : int = 15 * 60) -> None:
        super().__init__("HeatPump", my_simulation_parameters=my_simulation_parameters)

        self.build(manufacturer, name, min_operation_time, min_idle_time)

        self.number_of_cycles = 0
        self.number_of_cycles_previous = copy.deepcopy(self.number_of_cycles)
        self.state = GenericHeatPumpState(start_timestep=int(0), cycle_number=0)
        self.previous_state = self.state.clone( )



        # Inputs - Mandatories
        self.stateC: cp.ComponentInput = self.add_input(self.component_name,
                                                        self.State,
                                                        LoadTypes.ANY,
                                                        Units.ANY,
                                                        True)
        self.t_outC: cp.ComponentInput = self.add_input(self.component_name,
                                                        self.TemperatureOutside,
                                                        LoadTypes.ANY,
                                                        Units.CELSIUS,
                                                        True)
        # Inputs - Not Mandatories
        self.water_loadC: cp.ComponentInput = self.add_input(self.component_name,
                                                             self.WaterConsumption,
                                                             LoadTypes.VOLUME,
                                                             Units.LITER,
                                                             False)
        self.water_input_mass: cp.ComponentInput = self.add_input(self.component_name,
                                                                  self.WaterInput_mass,
                                                                  LoadTypes.WARM_WATER,
                                                                  Units.KG_PER_SEC,
                                                                  False)
        self.water_input_temperature: cp.ComponentInput = self.add_input(self.component_name,
                                                                         self.WaterInput_temperature,
                                                                         LoadTypes.WARM_WATER,
                                                                         Units.CELSIUS,
                                                                         False)



        # Outputs
        #self.water_output_mass: cp.ComponentOutput = self.add_output(self.ComponentName,
        #                                                          self.WaterOutput_mass,
        #                                                          LoadTypes.WarmWater,
        #                                                          Units.kg_per_sec)
        #self.water_output_temperature: cp.ComponentOutput = self.add_output(self.ComponentName,
        #                                                                 self.WaterOutput_temperature,
        #ä                                                                      LoadTypes.WarmWater,
        #                                                                 Units.Celsius)
        #self.wasted_energy_max_temperature: cp.ComponentOutput = self.add_output(self.ComponentName,
        #                                                                      self.WastedEnergyMaxTemperature,
        #                                                                      LoadTypes.WarmWater,
        #                                                                      Units.Watt)

        self.thermal_energy_deliveredC: cp.ComponentOutput = self.add_output(self.component_name,
                                                                             self.ThermalEnergyDelivered,
                                                                             LoadTypes.HEATING,
                                                                             Units.WATT)

        self.heatingC: cp.ComponentOutput = self.add_output(self.component_name,
                                                            self.Heating,
                                                            LoadTypes.HEATING,
                                                            Units.WATT)

        self.coolingC: cp.ComponentOutput = self.add_output(self.component_name,
                                                            self.Cooling,
                                                            LoadTypes.COOLING,
                                                            Units.WATT)

        self.electricity_outputC: cp.ComponentOutput = self.add_output(self.component_name,
                                                                       self.ElectricityOutput,
                                                                       LoadTypes.ELECTRICITY,
                                                                       Units.WATT)

        self.number_of_cyclesC : cp.ComponentOutput = self.add_output(self.component_name,
                                                                      self.NumberOfCycles,
                                                                      LoadTypes.ANY,
                                                                      Units.ANY)
        
        self.add_default_connections( Weather, self.get_weather_default_connections( ) )
        self.add_default_connections( HeatPumpController, self.get_controller_default_connections( ) )
        
    def get_weather_default_connections( self ) -> List[cp.ComponentConnection]:
        log.information("setting weather default connections in HeatPump")
        connections = [ ]
        weather_classname = Weather.get_classname( )
        connections.append(cp.ComponentConnection(GenericHeatPump.TemperatureOutside, weather_classname, Weather.TemperatureOutside))
        return connections
    
    def get_controller_default_connections( self )  -> List[cp.ComponentConnection]:
        log.information("setting controller default connections in HeatPump")
        connections = [ ]
        controller_classname = HeatPumpController.get_classname( )
        connections.append(cp.ComponentConnection(GenericHeatPump.State, controller_classname, HeatPumpController.State))
        return connections
    def i_prepare_simulation(self) -> None:
        """ Prepares the simulation. """
        pass
    def build( self, manufacturer: str, name: str, min_operation_time: float, min_idle_time:float )  -> None:
        # Simulation parameters

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
        #self.max_heating_power = 11 * 1E3
        self.max_cooling_power = - self.max_heating_power
        # Retrieves heat pump from database - END

        # Sets the power variation restrictions
        # Default values: 15 minutes to full power
        # Used only for non-clocked heat pump
        self.max_heating_power_var = self.max_heating_power * self.my_simulation_parameters.seconds_per_timestep / 900
        self.max_cooling_power_var = - self.max_heating_power * self.my_simulation_parameters.seconds_per_timestep / 900

        # Sets the time operation restricitions
        self.min_operation_time = min_operation_time / self.my_simulation_parameters.seconds_per_timestep
        self.min_idle_time = min_idle_time / self.my_simulation_parameters.seconds_per_timestep

        # Writes info to report
        self.write_to_report()

        # Applies correction due to timestep
        self.set_time_correction()
        #self.set_time_correction(self.time_correction_factor)

    def set_time_correction(self, factor: float = 1) -> None:
        if factor == 1:
            self.HasBeenConverted = False
        if self.HasBeenConverted is True:
            raise Exception("It has been already converted!")
        self.max_heating_power *= factor
        self.max_cooling_power *= factor
        self.max_heating_power_var *= factor
        self.max_cooling_power_var *= factor
        if factor != 1:
            self.HasBeenConverted = True


    def cal_cop(self, t_out:float) -> float:
        val:float = self.cop_coef[0] * t_out + self.cop_coef[1]
        return val

    def i_save_state(self)->None:
        self.previous_state = self.state.clone()
        self.number_of_cycles_previous = self.number_of_cycles

    def i_restore_state(self) -> None:
        self.state = self.previous_state.clone() # copy.deepcopy(self.previous_state)
        self.number_of_cycles = self.number_of_cycles_previous

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        pass

    def write_to_report(self) -> List[str]:
        lines: List[str] = []
        lines.append("Name: {}".format("Heat Pump"))
        lines.append("Max power: {:4.0f} kW".format((self.max_heating_power)*1E-3))
        lines.append("Max power var: {:4.0f}".format(self.max_heating_power_var))
        #lines = []
        #lines.append([self.ComponentName,""])
        #lines.append(["Max power:","{:4.2f}".format(self.max_heating_power)])
        #lines.append(["Max power var:","{:4.2f}".format(self.max_heating_power_var)])
        return lines

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues,  force_convergence: bool) -> None:
        # Inputs
        stateC = stsv.get_input_value(self.stateC)
        t_out = stsv.get_input_value(self.t_outC)
        #log.information("State: {}, Temperature: {}".format(stateC, t_out))
        #log.information("State of Activation: {}".format(self.state.activation))
        #log.information("Timestep special: {}".format(self.state.start_timestep + self.min_idle_time))
        # Calculation

        ## Calculation.ThermalEnergyStorage
        ## ToDo: Implementation with Thermal Energy Storage - BEGIN
        #if self.water_loadC.SourceOutput is not None:
        #    if stsv.get_input_value(self.water_loadC) != 0:
        #        control_signal = 1
        #    else:
        #        control_signal = 0
        #    # Inputs
        #    water_input_mass_sec = stsv.get_input_value(self.water_input_mass)
        #    water_input_mass = water_input_mass_sec
        #    water_input_temperature = stsv.get_input_value(self.water_input_temperature)

        #    mass_flow_max = self.max_heating_power / (4180 * 25)  # kg/s ## -> ~0.07

        #    if control_signal == 1 and (water_input_mass == 0 and water_input_temperature == 0):
        #        """first iteration"""
        #        water_input_temperature = 40
        #        water_input_mass = mass_flow_max

        #    if control_signal == 1:
        #        volume_flow_gasheater = water_input_mass / PhysicsConfig.water_density
        #        ws = WaterSlice(WarmWaterStorageConfig.tank_diameter, (4 * volume_flow_gasheater) / (pi * WarmWaterStorageConfig.tank_diameter ** 2), water_input_temperature)
        #        ws_output, wasted_energy_max_temperature, thermal_output = self.process_thermal(ws)
        #    else:
        #        height_flow_gasheater = 0
        #        water_input_temperature = 0
        #        ws = WaterSlice(WarmWaterStorageConfig.tank_diameter, height_flow_gasheater, water_input_temperature)
        #        ws_output = ws
        #        wasted_energy_max_temperature = 0

        #    ws_output_mass = ws_output.mass
        #    ws_output_temperature = ws_output.temperature

        #    # Mass is consistent
        #    stsv.set_output_value(self.water_output_mass, ws_output_mass)
        #    stsv.set_output_value(self.water_output_temperature, ws_output_temperature)
        #    stsv.set_output_value(self.wasted_energy_max_temperature, wasted_energy_max_temperature)
        ## ToDo: Implementation with Thermal Energy Storage - END


        ## Calculation.ThermalEnergyDelivery
        ### Heat Pump is on
        if self.state.activation != 0:
            number_of_cycles = self.state.cycle_number
            # Checks if the minimum running time has been reached
            if timestep >= self.state.start_timestep + self.min_operation_time and stateC == 0:
                self.state = GenericHeatPumpState(start_timestep=timestep,
                                                  cycle_number=number_of_cycles)

            stsv.set_output_value(self.thermal_energy_deliveredC, self.state.thermal_energy_delivered)
            stsv.set_output_value(self.heatingC, self.state.heating)
            stsv.set_output_value(self.coolingC, self.state.cooling)
            stsv.set_output_value(self.electricity_outputC, self.state.electricity_in)
            stsv.set_output_value(self.number_of_cyclesC, self.number_of_cycles)
            return

        ### Heat Pump is Off
        if stateC != 0 and (timestep >= self.state.start_timestep + self.min_idle_time):
            self.number_of_cycles = self.number_of_cycles + 1
            number_of_cycles = self.number_of_cycles
            if stateC == 1:
            #if stsv.get_input_value(self.stateC) > 0:
                self.state = GenericHeatPumpState(start_timestep=timestep,
                                                  thermal_energy_delivered = self.max_heating_power,
                                                  cop=self.cal_cop(t_out),
                                                  cycle_number=number_of_cycles)
            else:
                self.state = GenericHeatPumpState(start_timestep=timestep,
                                                  thermal_energy_delivered = self.max_cooling_power,
                                                  cop=self.cal_cop(t_out),
                                                  cycle_number = number_of_cycles)

        #log.information(self.state.thermal_energy_delivered)
        # Outputs
        stsv.set_output_value(self.thermal_energy_deliveredC, self.state.thermal_energy_delivered)
        stsv.set_output_value(self.heatingC, self.state.heating)
        stsv.set_output_value(self.coolingC, self.state.cooling)
        stsv.set_output_value(self.electricity_outputC, self.state.electricity_in)
        stsv.set_output_value(self.number_of_cyclesC, self.number_of_cycles)

    def process_thermal(self, ws_in: float) -> None:
        pass
        #temperature_max = 55
        #heat_capacity = PhysicsConfig.water_specific_heat_capacity
        #thermal_energy_to_add = self.max_heating_power
        #ws_out_mass = ws_in.mass
        #try:
        #    ws_out_temperature = ws_in.temperature + thermal_energy_to_add / (heat_capacity * ws_out_mass)
        #except ZeroDivisionError:
        #    log.information(heat_capacity)
        #    log.information(ws_out_mass)
        #    log.information(ws_in.mass)
        #    raise ValueError
        #wasted_energy = 0
        #if ws_out_temperature > temperature_max:
        #    delta_T = ws_out_temperature - temperature_max
        #    wasted_energy = (delta_T * ws_out_mass * PhysicsConfig.water_specific_heat_capacity)
        #    ws_out_temperature = temperature_max
        #ws_out_enthalpy = ws_in.enthalpy + thermal_energy_to_add
        #ws_in.change_slice_parameters(new_temperature=ws_out_temperature, new_enthalpy=ws_out_enthalpy, new_mass=ws_out_mass)
        #return ws_in, wasted_energy, thermal_energy_to_add


class HeatPumpController(cp.Component):
    """
    Heat Pump Controller. It takes data from other
    components and sends signal to the heat pump for
    activation or deactivation.

    Parameters
    --------------
    t_air_heating: float
        Minimum comfortable temperature for residents
    t_air_cooling: float
        Maximum comfortable temperature for residents
    offset: float
        Temperature offset to compensate the hysteresis
        correction for the building temperature change
    mode : int
        Mode index for operation type for this heat pump
    """
    # Inputs
    TemperatureMean = "Residence Temperature"
    ElectricityInput = "ElectricityInput"

    # Outputs
    State = "State"

    # Similar components to connect to:
    # 1. Building
    @utils.measure_execution_time
    def __init__(self,
                 my_simulation_parameters: SimulationParameters,
                 t_air_heating: float = 18.0,
                 t_air_cooling: float = 26.0,
                 offset: float = 0.0,
                 mode:int=1) -> None:
        super().__init__("HeatPumpController", my_simulation_parameters=my_simulation_parameters)
        self.build(t_air_cooling=t_air_cooling,
                   t_air_heating=t_air_heating,
                   offset=offset,
                   mode=mode)

        self.t_mC: cp.ComponentInput = self.add_input(self.component_name,
                                                      self.TemperatureMean,
                                                      LoadTypes.TEMPERATURE,
                                                      Units.CELSIUS,
                                                      True)
        self.electricity_inputC : cp.ComponentInput= self.add_input(self.component_name,
                                                                    self.ElectricityInput,
                                                                    LoadTypes.ELECTRICITY,
                                                                    Units.WATT,
                                                                    False)
        self.stateC: cp.ComponentOutput = self.add_output(self.component_name,
                                                          self.State,
                                                          LoadTypes.ANY,
                                                          Units.ANY)
        
        self.add_default_connections( Building, self.get_building_default_connections( ) )
        
    def get_building_default_connections( self ) -> List[cp.ComponentConnection]:
        log.information("setting building default connections in Heatpumpcontroller")
        connections = [ ]
        building_classname = Building.get_classname( )
        connections.append( cp.ComponentConnection( HeatPumpController.TemperatureMean, building_classname, Building.TemperatureMean ) )
        return connections

    def build(self, t_air_heating:float, t_air_cooling:float, offset:float, mode: float) -> None:
        # Sth
        self.controller_heatpumpmode = "off"
        self.previous_heatpump_mode = self.controller_heatpumpmode

        # Configuration
        self.t_set_heating = t_air_heating
        self.t_set_cooling = t_air_cooling
        self.offset = offset

        self.mode = mode
    def i_prepare_simulation(self) -> None:
        """ Prepares the simulation. """
        pass
    def i_save_state(self ) -> None:
        self.previous_heatpump_mode = self.controller_heatpumpmode

    def i_restore_state(self)  -> None:
        self.controller_heatpumpmode = self.previous_heatpump_mode

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues)  -> None:
        pass
    def write_to_report(self) -> List[str]:
        lines = []
        lines.append("Heat Pump Controller")
        # todo: add more useful stuff here
        lines.append("tbd")
        return lines
    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues,  force_convergence: bool) -> None:
        # check demand, and change state of self.has_heating_demand, and self._has_cooling_demand
        if force_convergence:
            pass
        else:
            # Retrieves inputs
            t_m_old = stsv.get_input_value(self.t_mC)
            electricity_input = stsv.get_input_value(self.electricity_inputC)

            if self.mode == 1:
                self.conditions(t_m_old)
            elif self.mode == 2:
                self.smart_conditions(t_m_old, electricity_input)

        if self.controller_heatpumpmode == 'heating':
            state = 1
        if self.controller_heatpumpmode == 'cooling':
            state = -1
        if self.controller_heatpumpmode == 'off':
            state = 0
        stsv.set_output_value(self.stateC, state)

    def conditions(self, set_temp: float) -> None:
        maximum_heating_set_temp = self.t_set_heating + self.offset
        minimum_heating_set_temp = self.t_set_heating
        minimum_cooling_set_temp = self.t_set_cooling - self.offset
        maximum_cooling_set_temp = self.t_set_cooling

        if self.controller_heatpumpmode == 'heating':  # and daily_avg_temp < 15:
            if set_temp > maximum_heating_set_temp:  # 23
                self.controller_heatpumpmode = 'off'
                return
        if self.controller_heatpumpmode == 'cooling':
            if set_temp < minimum_cooling_set_temp: # 24
                self.controller_heatpumpmode = 'off'
                return
        if self.controller_heatpumpmode == 'off':
            #if pvs_surplus > ? and air_temp < minimum_heating_air + 2:
            if set_temp < minimum_heating_set_temp:  # 21
                self.controller_heatpumpmode = 'heating'
                return
            if set_temp > maximum_cooling_set_temp:  # 26
                self.controller_heatpumpmode = 'cooling'
                return

    def smart_conditions(self, set_temp: float, electricity_input: float) -> None:
        smart_offset_upper = 3
        smart_offset_lower = 0.5
        maximum_heating_set_temp = self.t_set_heating + self.offset
        if electricity_input < 0:
            maximum_heating_set_temp += smart_offset_upper
        # maximum_heating_set_temp = self.t_set_heating
        minimum_heating_set_temp = self.t_set_heating
        if electricity_input < 0:
            minimum_heating_set_temp += smart_offset_lower
        minimum_cooling_set_temp = self.t_set_cooling - self.offset
        # minimum_cooling_set_temp = self.t_set_cooling
        maximum_cooling_set_temp = self.t_set_cooling

        if self.controller_heatpumpmode == 'heating':  # and daily_avg_temp < 15:
            if set_temp > maximum_heating_set_temp:  # 23
                self.controller_heatpumpmode = 'off'
                return
        if self.controller_heatpumpmode == 'cooling':
            if set_temp < minimum_cooling_set_temp:  # 24
                self.controller_heatpumpmode = 'off'
                return
        if self.controller_heatpumpmode == 'off':
            # if pvs_surplus > ? and air_temp < minimum_heating_air + 2:
            if set_temp < minimum_heating_set_temp:  # 21
                self.controller_heatpumpmode = 'heating'
                return
            if set_temp > maximum_cooling_set_temp:  # 26
                self.controller_heatpumpmode = 'cooling'
                return

        #if timestep >= 60*24*30*3 and timestep <= 60*24*30*9:
        #    state = 0

        #log.information("Final state: {}\n".format(state))

    def prin1t_outpu1t(self, t_m:float, state: Any) -> None:
        log.information("==========================================")
        log.information("T m: {}".format(t_m))
        log.information("State: {}".format(state))

