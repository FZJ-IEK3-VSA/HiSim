# -*- coding: utf-8 -*-
"""
Created on Tue Aug  9 09:06:13 2022

@author: m.alfouly
"""
#import numpy as np

# Owned
#from hisim.components.weather import Weather
from hisim import component as cp
from hisim.loadtypes import LoadTypes, Units
from hisim.simulationparameters import SimulationParameters
from hisim.components.building import Building
#from hisim.components.weather import Weather
#from hisim.components.loadprofilegenerator_connector import Occupancy
from hisim import log


class PIDState:
    """ Represents the current internal state of the PID. """

    def __init__(self,
                 integrator: float,
                 derivator: float):
        """ Initializes the state of the PID. """
        self.Integrator: float = integrator
        self.Derivator: float = derivator

    def clone(self):
        return PIDState(self.Integrator, self.Derivator)


class PIDController(cp.Component):
    # Inputs
    TemperatureMean = "Residence Temperature"  # uncontrolled temperature

    # ouput 
    ElectricityOutputPID = "ElectricityOutputPID"
    ThermalEnergyDelivered = "ThermalEnergyDelivered"
    error_pvalue = "error_p_value"
    error_dvalue = "error_d_value"
    error_ivalue = "error_i_value"
    error = "error_value"
    derivator = "derivator"
    integrator = "integrator"

    def __init__(self, my_simulation_parameters: SimulationParameters, ki: float = 1, kp: float = 1, kd: float =1) -> None:
        super().__init__("PIDController", my_simulation_parameters=my_simulation_parameters)
        self.build()
        # --------------------------------------------------
        # control saturation
        self.MV_min = 0
        self.MV_max = 5000
        self.ki = ki
        self.kp = kp
        self.kd = kd
        self.state = PIDState(integrator=0, derivator=0)
        self.previous_state = self.state.clone()

        self.t_mC: cp.ComponentInput = self.add_input(self.component_name,
                                                      self.TemperatureMean,
                                                      LoadTypes.TEMPERATURE,
                                                      Units.CELSIUS,
                                                      True)

        self.electric_power: cp.ComponentOutput = self.add_output(self.component_name,
                                                                  self.ElectricityOutputPID,
                                                                  LoadTypes.ELECTRICITY,
                                                                  Units.WATT)
        self.error_pvalue_output: cp.ComponentOutput = self.add_output(self.component_name,
                                                                       self.error_pvalue,
                                                                       LoadTypes.ELECTRICITY,
                                                                       Units.WATT)
        self.error_ivalue_output: cp.ComponentOutput = self.add_output(self.component_name,
                                                                       self.error_ivalue,
                                                                       LoadTypes.ELECTRICITY,
                                                                       Units.WATT)
        self.error_dvalue_output: cp.ComponentOutput = self.add_output(self.component_name,
                                                                       self.error_dvalue,
                                                                       LoadTypes.ELECTRICITY,
                                                                       Units.WATT)
        self.error_output: cp.ComponentOutput = self.add_output(self.component_name,
                                                                self.error,
                                                                LoadTypes.ELECTRICITY,
                                                                Units.WATT)

        self.derivator_output: cp.ComponentOutput = self.add_output(self.component_name,
                                                                self.derivator,
                                                                LoadTypes.ELECTRICITY,
                                                                Units.WATT)
        self.integrator_output: cp.ComponentOutput = self.add_output(self.component_name,
                                                             self.integrator,
                                                             LoadTypes.ELECTRICITY,
                                                             Units.WATT)

    def get_building_default_connections(self):
        log.information("setting building default connections in Heatpumpcontroller")
        connections = []
        building_classname = Building.get_classname()
        connections.append(
            cp.ComponentConnection(PIDController.TemperatureMean, building_classname, Building.TemperatureMean))
        # connections.append(cp.ComponentConnection(PIDController.SolarGainThroughWindows, building_classname,                                                  Building.SolarGainThroughWindows))

        return connections

    # def get_occupancy_default_connections(self):
    #     log.information("setting occupancy default connections")
    #     connections = []
    #     occupancy_classname = Occupancy.get_classname()
    #     connections.append(
    #         cp.ComponentConnection(PIDController.HeatingByResidents, occupancy_classname, Occupancy.HeatingByResidents))
    #     return connections
    #
    # def get_weather_default_connections(self):
    #     log.information("setting weather default connections")
    #     connections = []
    #     weather_classname = Weather.get_classname()
    #     connections.append(
    #         cp.ComponentConnection(PIDController.TemperatureOutside, weather_classname, Weather.TemperatureOutside))
    #     return connections

    def build(self):
        """ For calculating internal things and preparing the simulation. """
        # Sth
        pass

    def i_save_state(self):
        """ Saves the internal state at the beginning of each timestep. """
        self.previous_state = self.state.clone()

    def i_restore_state(self):
        """ Restores the internal state after each iteration. """
        self.state = self.previous_state.clone()

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """ Double check results after iteration. """
        pass

    def write_to_report(self):
        """ Logs the most important config stuff to the report. """
        lines = []
        lines.append("PID Controller")
        lines.append("Control algorithm of the Air conditioner is: PI \n")
        # lines.append(f"Controller Proportional gain is {self.Kp:4.2f} \n")
        # lines.append(f"Controller Integral gain is {self.Ki:4.2f} \n")
        return lines

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """ Core smulation function. """
        if force_convergence:
            return
        # Retrieve building temperature
        building_temperature_t_mc = stsv.get_input_value(self.t_mC)
        set_point: float = 22.0

        tau_r: float = 1.0
        tauI: float = tau_r * 1863
        Kplant: float = 1.8750 / 1000  # 0.15773831/(10000) #
        Kp: float = self.kp  # 2500   # (tau_r * Kplant)  # controller Proportional gain
        Ki: float = self.ki  # 0.2 #  Kp / tauI  # integral gain
        Kd: float = self.kd  #  200

        error = set_point - building_temperature_t_mc  # e(tk)
        p_value = Kp * error
        d_value = Kd * (error - self.state.Derivator)
        self.state.Derivator = error
        self.state.Integrator = self.state.Integrator + error

        limit = 5000
        if self.state.Integrator > limit:
            self.state.Integrator = limit
        elif self.state.Integrator < -limit:
            self.state.Integrator = -limit

        i_value = self.state.Integrator * Ki
        manipulated_variable = p_value + i_value + d_value

        stsv.set_output_value(self.error_pvalue_output, p_value)
        stsv.set_output_value(self.error_dvalue_output, d_value)
        stsv.set_output_value(self.error_ivalue_output, i_value)
        stsv.set_output_value(self.error_output, error)
        stsv.set_output_value(self.integrator_output, self.state.Integrator)
        stsv.set_output_value(self.derivator_output, self.state.Derivator)
        stsv.set_output_value(self.electric_power, manipulated_variable)

        # update state for next iteration
        #self.state.previous_value = building_temperature_t_mc

    def determine_conditions(self, current_temperature: float, set_point: float) -> str:
        """ For determining heating and cooling mode and implementing a dead zone. Currently disabled. """
        offset_in_degree_celsius = 0.5
        maximum_heating_set_temp = set_point + offset_in_degree_celsius
        minimum_cooling_set_temp = set_point - offset_in_degree_celsius

        mode = 'off'
        if mode == 'heating' and current_temperature > maximum_heating_set_temp:  # 23 22.5
            return 'off'
        if mode == 'cooling' and current_temperature < minimum_cooling_set_temp:  # 24 21.5
            mode = 'off'
        if mode == 'off':
            if current_temperature < set_point:  # 21
                mode = 'heating'
            if current_temperature > set_point:  # 26
                mode = 'cooling'
        return mode
