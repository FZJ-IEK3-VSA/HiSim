# Generic/Built-in
import numpy as np
import copy
import matplotlib
import seaborn
from math import pi
from dataclasses import dataclass
from dataclasses_json import dataclass_json
from typing import Optional, List, Any

# Owned
import hisim.utils as utils
import hisim.loadtypes as lt
from hisim import component as cp
from hisim.simulationparameters import SimulationParameters
from hisim.components.controller_l2_energy_management_system import L2GenericEnergyManagementSystem
from hisim.components.weather import Weather
from hisim.components import controller_l1_heatpump
from hisim import log

__authors__ = "edited Johanna Ganglbauer"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"


@dataclass_json
@dataclass
class HeatPumpConfig:
    name: str
    source_weight: int
    parameter_string: str
    manufacturer: str
    device_name: str
    power_th: float
    cooling_considered: bool
    heating_season_begin: Optional[int]
    heating_season_end: Optional[int]

    def __init__(self, name: str, source_weight: int, manufacturer: str, device_name: str, power_th: float, cooling_considered: bool,
                 heating_season_begin: Optional[int], heating_season_end: Optional[int]):
        self.name = name
        self.source_weight = source_weight
        self.manufacturer = manufacturer
        self.device_name = device_name
        self.power_th = power_th
        self.cooling_considered = cooling_considered
        self.heating_season_begin = heating_season_begin
        self.heating_season_end = heating_season_end
        
    @staticmethod
    def get_default_config_heating() -> Any:
        config = HeatPumpConfig(name='HeatingHeatPump', source_weight=1, manufacturer="Viessmann Werke GmbH & Co KG",
                                device_name="Vitocal 300-A AWO-AC 301.B07", power_th=6200, cooling_considered=True, heating_season_begin=270,
                                heating_season_end=150)
        return config

    @staticmethod
    def get_default_config_waterheating() -> Any:
        config = HeatPumpConfig(name='DHWHeatPump', source_weight=1, manufacturer="Viessmann Werke GmbH & Co KG",
                                device_name="Vitocal 300-A AWO-AC 301.B07", power_th=3000, cooling_considered=False, heating_season_begin=None,
                                heating_season_end=None)
        return config

    @staticmethod
    def get_default_config_heating_electric() -> Any:
        config = HeatPumpConfig(name='HeatingHeatingRod', source_weight=1, manufacturer="dummy", device_name="HeatingRod", power_th=6200,
                                cooling_considered=False, heating_season_begin=None, heating_season_end=None)
        return config

    @staticmethod
    def get_default_config_waterheating_electric() -> Any:
        config = HeatPumpConfig(name='DHWHeatingRod', source_weight=1, manufacturer="dummy", device_name="HeatingRod", power_th=3000,
                                cooling_considered=False, heating_season_begin=None, heating_season_end=None)
        return config


class ModularHeatPumpState:
    """
    This data class saves the state of the heat pump.
    """

    def __init__(self, state: float = 0, timestep: int = -1):
        self.state: float = state
        self.timestep = timestep

    def clone(self):
        return ModularHeatPumpState(state=self.state, timestep=self.timestep)


class ModularHeatPump(cp.Component):
    """
    Heat pump implementation. It does support a refrigeration cycle. The heatpump_modular differs to heatpump in (a) minumum run- and
    idle time are given in seconds (not in time steps), (b) the season for heating and cooling is explicitly separated by days of the year.
    This is mostly done to avoid heating and cooling at the same day in spring and autum with PV surplus available. (c) heat pump modular needs
    a generic_controller_l1_runtime signal. The run time is not controlled in the component itself but in the controller.
    
    STILL TO BE DONE: implement COP for cooling period. At the moment it cools with heating efficiencies.

    Parameters
    ----------
    manufacturer : str
        Heat pump manufacturer
    device_name : str
        Heat pump model
    heating_season_begin : int, optional
        Day( julian day, number of day in year ), when heating season starts - and cooling season ends. The default is 270.
    heating_season_end : int, optional
        Day( julian day, number of day in year ), when heating season ends - and cooling season starts. The default is 150
    name : str, optional
        Name of heatpump within simulation. The default is 'HeatPump'
    source_weight : int, optional
        Weight of component, relevant if there is more than one heat pump, defines hierachy in control. The default is 1.
        
    """
    # Inputs
    TemperatureOutside = "TemperatureOutside"
    L1DeviceSignal = "L1DeviceSignal"
    l1_RunTimeSignal = 'l1_RunTimeSignal'
    ems_flexible_electricity = "EMS Modulating Signal"

    # Outputs
    ThermalPowerDelivered = "ThermalPowerDelivered"
    ElectricityOutput = "ElectricityOutput"
    PowerModifier = "PowerModifier"

    # Similar components to connect to:
    # 1. HeatPump l1 controller
    # 2. HeatPump l2 controller
    # 3. HeatPump l3 controller ( optional )

    @utils.measure_execution_time
    def __init__(self, config: HeatPumpConfig, my_simulation_parameters: SimulationParameters):

        super().__init__(name=config.name + '_w' + str(config.source_weight), my_simulation_parameters=my_simulation_parameters)
        self.config = config
        self.build(config)

        if my_simulation_parameters.surplus_control:
            postprocessing_flag = [lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED]
        else:
            postprocessing_flag = [lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED]

        # Inputs - Mandatories
        self.TemperatureOutsideC: cp.ComponentInput = self.add_input(self.component_name, self.TemperatureOutside, lt.LoadTypes.ANY, lt.Units.CELSIUS,
                                                                     mandatory=True)

        self.EMS_Flexible_ElectricityC: cp.ComponentInput = self.add_input(self.component_name, self.ems_flexible_electricity,
                                                                           lt.LoadTypes.ELECTRICITY, lt.Units.WATT, mandatory=False)

        self.L1HeatControllerTargetPercentage: cp.ComponentInput = self.add_input(self.component_name, self.L1DeviceSignal, lt.LoadTypes.ANY, lt.Units.PERCENT,
                                                                 mandatory=True)

        # Outputs
        self.ThermalPowerDeliveredC: cp.ComponentOutput = self.add_output(object_name=self.component_name, field_name=self.ThermalPowerDelivered,
                                                                          load_type=lt.LoadTypes.HEATING, unit=lt.Units.WATT,
                                                                          postprocessing_flag=[lt.InandOutputType.HEAT_TO_BUFFER])
        self.ElectricityOutputC: cp.ComponentOutput = self.add_output(object_name=self.component_name, field_name=self.ElectricityOutput,
                                                                      load_type=lt.LoadTypes.ELECTRICITY, unit=lt.Units.WATT,
                                                                      postprocessing_flag=postprocessing_flag)

        self.PowerModifierChannel: cp.ComponentOutput = self.add_output(object_name=self.component_name, field_name=self.PowerModifier,
                                                                      load_type=lt.LoadTypes.ANY, unit=lt.Units.ANY,postprocessing_flag=[])

        self.add_default_connections(Weather, self.get_weather_default_connections())
        self.add_default_connections(controller_l1_heatpump.L1HeatPumpController, self.get_l1_heatpump_controller_default_connections())
        self.add_default_connections(L2GenericEnergyManagementSystem, self.get_ems_default_connections())

    def get_weather_default_connections(self):
        log.information("setting weather default connections in HeatPump")
        connections = []
        weather_classname = Weather.get_classname()
        connections.append(cp.ComponentConnection(ModularHeatPump.TemperatureOutside, weather_classname, Weather.TemperatureOutside))
        return connections

    def get_ems_default_connections(self):
        log.information("setting weather default connections in HeatPump")
        connections = []
        ems_classname = L2GenericEnergyManagementSystem.get_classname()
        connections.append(
            cp.ComponentConnection(ModularHeatPump.ems_flexible_electricity, ems_classname, L2GenericEnergyManagementSystem.FlexibleElectricity))
        return connections

    def get_l1_heatpump_controller_default_connections(self):
        log.information("setting l1 default connections in HeatPump")
        connections = []
        controller_classname = controller_l1_heatpump.L1HeatPumpController.get_classname()
        connections.append(cp.ComponentConnection(ModularHeatPump.L1DeviceSignal, controller_classname,
                                                  controller_l1_heatpump.L1HeatPumpController.HeatControllerTargetPercentage))
        return connections

    def i_prepare_simulation(self) -> None:
        """ Prepares the simulation. """
        pass

    def build(self, config):
        self.name = config.name
        self.source_weight = config.source_weight
        self.manufacturer = config.manufacturer
        self.devicename = config.device_name

        # Retrieves heat pump from database - BEGIN
        heat_pumps_database = utils.load_smart_appliance("Heat Pump")

        heat_pump_found = False
        for heat_pump in heat_pumps_database:
            if heat_pump["Manufacturer"] == config.manufacturer and heat_pump["Name"] == config.device_name:
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
        self.power_th = config.power_th
        self.cooling_considered = config.cooling_considered
        if self.cooling_considered:
            self.heating_season_begin = config.heating_season_begin * 24 * 3600 / self.my_simulation_parameters.seconds_per_timestep
            self.heating_season_end = config.heating_season_end * 24 * 3600 / self.my_simulation_parameters.seconds_per_timestep

        self.state = ModularHeatPumpState()
        self.previous_state = ModularHeatPumpState()

        # Writes info to report
        self.write_to_report()

    def cal_cop(self, t_out: float) -> float:
        val: float = self.cop_coef[0] * t_out + self.cop_coef[1]
        return val

    def i_save_state(self) -> None:
        self.previous_state = self.state.clone()

    def i_restore_state(self) -> None:
        self.state = self.previous_state.clone()

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        pass

    def write_to_report(self) -> List[str]:
        lines: List[str] = []
        lines.append("Name: {}".format(self.name + str(self.source_weight)))
        lines.append("Manufacturer: {}".format(self.name))
        lines.append("Max power: {:4.0f} kW".format((self.power_th) * 1E-3))
        return lines

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:

        # Inputs
        target_percentage = stsv.get_input_value(self.L1HeatControllerTargetPercentage)

        T_outside: float = stsv.get_input_value(self.TemperatureOutsideC)
        cop = self.cal_cop(T_outside)
        electric_power = self.power_th / cop

        # calculate modulation
        if target_percentage > 0:
            power_modifier = target_percentage
        if target_percentage == 0:
            power_modifier = 0
        if target_percentage < 0:
            flexible_electricity = stsv.get_input_value(self.EMS_Flexible_ElectricityC)
            power_modifier = flexible_electricity / electric_power

        if power_modifier > 1:
            power_modifier = 1

        stsv.set_output_value(self.ThermalPowerDeliveredC, self.power_th * power_modifier)
        stsv.set_output_value(self.PowerModifierChannel, power_modifier)

        stsv.set_output_value(self.ElectricityOutputC,  electric_power * power_modifier)

        # #put forecast into dictionary  # if self.my_simulation_parameters.predictive_control:  #     #only in first timestep  #     if self.state.timestep + 1 == timestep:  #         self.state.timestep += 1  #         self.previous_state.timestep += 1  #         runtime = stsv.get_input_value( self.l1_RunTimeSignalC )

        #         self.simulation_repository.set_dynamic_entry(component_type = lt.ComponentType.HEAT_PUMP, source_weight = self.source_weight, entry =[self.power_th / cop] * runtime)

