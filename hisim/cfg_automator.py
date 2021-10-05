import json
import os
import ast
import component
import inspect
import shutil
import sys

from inspect import isclass
from pkgutil import iter_modules
from pathlib import Path as gg
from importlib import import_module

## IMPORT ALL COMPONENT CLASSES DYNAMICALLY
# DIRTY CODE. GIVE ME BETTER SUGGESTIONS

# iterate through the modules in the current package
package_dir = os.path.join(gg(__file__).resolve().parent, "components")

for (_, module_name, _) in iter_modules([package_dir]):

    # import the module and iterate through its attributes
    module = import_module(f"components.{module_name}")
    for attribute_name in dir(module):
        attribute = getattr(module, attribute_name)

        if isclass(attribute):
            # Add the class to this package's variables
            globals()[attribute_name] = attribute


from globals import HISIMPATH
import simulator

__authors__ = "Vitor Hugo Bellotto Zago"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"

def delete_all_results():
    directorypath = globals.HISIMPATH["results"]
    filelist = [f for f in os.listdir(directorypath)]
    for f in filelist:
        if os.path.isdir(os.path.join(directorypath, f)):
            shutil.rmtree(os.path.join(directorypath, f))

def get_subclasses(classname=None):
    """
    Return a list of all the children classes
    in this module from parent class classname


    """
    list_of_children = [cls.__name__ for cls in classname.__subclasses__()]
    return list_of_children

def get_default_args(obj, parameter=None):
    """
    Get the default argument of either a function or
    a class

    :param obj: a function or class
    :param parameter:
    :return: a dictionary or list of the arguments
    """
    if parameter == None:
        if inspect.isfunction(obj):
            signature = inspect.signature(obj)
            return {k: v.default for k, v in signature.parameters.items() if v.default is not inspect.Parameter.empty}
        if inspect.isclass(obj):
            #boring = dir(type('dummy', (object,), {}))
            #return [item
            #        for item in inspect.getmembers(obj)
            #        if item[0] not in boring]
            boring = dir(type('dummy', (object,), {}))
            return [item for item in inspect.getmembers(obj)]
    else:
        if inspect.isfunction(obj):
            signature = inspect.signature(obj)
            sig_dict = {k: v.default for k, v in signature.parameters.items() if v.default is not inspect.Parameter.empty}
            return sig_dict[parameter]
        if inspect.isclass(obj):
            #boring = dir(type('dummy', (object,), {}))
            #return [item
            #        for item in inspect.getmembers(obj)
            #        if item[0] not in boring]
            boring = dir(type('dummy', (object,), {}))
            return [item for item in inspect.getmembers(obj) if item[0] in parameter][0][1]

class ConfigurationGenerator:
    SimulationParameters = {"year": 2019,
                            "seconds_per_timestep": 60,
                            "method": "full_year"}
    SimulationParametersDay = {"year": 2019,
                               "seconds_per_timestep": 60,
                               "method": "one_day_only"}
    Battery = {"manufacturer": "sonnen",
               "model": "sonnenBatterie 10 - 16,5 kWh",
               "soc": 10/15,
               "base": False}
    FlexibilityController = {"mode": 1}

    def __init__(self, set=None):
        self.data = {}
        self.order = []

        component_class_children = get_subclasses(component.Component)
        for component_class in component_class_children:
            sig = get_default_args(globals()[component_class], "__init__")
            default_args = get_default_args(sig)
            if "sim_params" in default_args:
                del default_args["sim_params"]
            setattr(self, component_class, default_args)

        self.Vehicle = {"manufacturer": "Tesla",
                   "model": "Model 3 v3",
                   "soc": 1.0,
                   "profile": "CH01"}
        self.EVCharger = {"manufacturer": "myenergi",
                         "name": "Wallbox ZAPPI 222TW"}
        self.EVChargerController = {"mode": 3}
        self.Battery = {"manufacturer": "sonnen",
                   "model": "sonnenBatterie 10 - 16,5 kWh",
                   "soc": 10 / 15,
                   "base": False}
        self.FlexibilityController = {"mode": 1}

        if set is None:
            self.add_base()

    def set_order(self, order):
        self.order = order

    def dump(self):
        self.data["Order"] = self.order
        with open(HISIMPATH["cfg"], "w") as f:
            json.dump(self.data, f, indent=4)

    def add_sim_param(self, custom=None):
        if custom is None:
            self.data["SimulationParameters"] = self.SimulationParameters
        else:
            self.data["SimulationParameters"] = self.SimulationParametersDay
        self.order.append("SimulationParameters")

    def add_weather(self):
        self.data["Weather"] = self.Weather
        self.order.append("Weather")

    def add_occupancy(self):
        self.data["Occupancy"] = self.Occupancy
        self.order.append("Occupancy")

    def add_pvs(self):
        self.data["PVSystem"] = self.PVSystem
        self.order.append("PVSystem")

    def add_building(self):
        self.data["Building"] = self.Building
        self.order.append("Building")

    def add_basic_setup(self):
        self.add_sim_param()
        self.add_weather()
        self.add_occupancy()
        self.add_pvs()
        self.add_building()

    def add_heat_pump(self, mode=None):
        self.data["HeatPumpController"] = self.HeatPumpController
        if mode is not None:
            self.data["HeatPumpController"]["mode"] = mode

        self.data["HeatPump"] = self.HeatPump
        self.order.append("HeatPump")

    def add_ev_charger(self, mode=None):
        self.data["EVChargerController"] = self.EVChargerController
        if mode is not None:
            self.data["EVChargerController"]["mode"] = mode

        self.data["Vehicle_Pure"] = self.Vehicle_Pure

        self.data["EVCharger"] = self.EVCharger
        self.order.append("EVCharger")

    def add_battery(self):
        self.data["Battery"] = self.Battery
        self.order.append("Battery")

    def add_dummy(self,
                  electricity=None,
                  heat=None,
                  capacity=None,
                  initial_temperature=None):
        if electricity is not None:
            self.Dummy["electricity"] = electricity
        if heat is not None:
            self.Dummy["heat"] = heat
        if capacity is not None:
            self.Dummy["capacity"] = capacity
        if initial_temperature is not None:
            self.Dummy["initial_temperature"] = initial_temperature
        self.data["Dummy"] = self.Dummy
        self.order.append("Dummy")

    def add_flexibility_controller(self, mode=None):
        self.data["FlexibilityController"] = self.FlexibilityController
        if mode is not None:
            self.data["FlexibilityController"]["mode"] = mode
        self.order.append("FlexibilityController")

    def add_thermal_energy_storage(self):
        self.data["ThermalEnergyStorage"] = 0
        self.order.append("ThermalEnergyStorage")

class SmartHome:

    def __init__(self, function=None, mode=None):
        if os.path.isfile(HISIMPATH["cfg"]):
            with open(os.path.join(HISIMPATH["cfg"])) as file:
                cfg = json.load(file)
            self.cfg = cfg
        else:
            self.cfg = None
        self.function = function
        self.mode = mode
        self.electricity_grids : List[ElectricityGrid] = []
        self.electricity_grid_consumption : List[ElectricityGrid] = []

    def build(self, my_sim):
        if self.cfg is not None:
            for component in self.cfg["Order"]:

                if component == "SimulationParameters":
                    self.add_sim_param(my_sim)
                if component == "Weather":
                    #command = "self.add_{}(my_sim)".format(component.lower())
                    self.add_weather(my_sim)
                if component == "Occupancy":
                    self.add_occupancy(my_sim)
                if component == "PVSystem":
                    self.add_pvs(my_sim)
                if component == "Building":
                    self.add_building(my_sim)
                if component == "HeatPump":
                    self.add_heat_pump(my_sim)
                if component == "EVCharger":
                    self.add_ev_charger(my_sim)
                if component == "Battery":
                    self.add_battery(my_sim)
                if component == "FlexibilityController":
                    self.add_flexibility(my_sim)
                if component == "ThermalEnergyStorage":
                    self.add_thermal_energy_storage(my_sim)
                if component == "Dummy":
                    self.add_dummy(my_sim)
            self.close(my_sim)
        else:
            raise Exception("No configuration file!")

    def add_sim_param(self, my_sim):
        # Timeline configuration
        method = self.cfg["SimulationParameters"]["method"]
        self.cfg["SimulationParameters"].pop("method", None)
        if method == "full_year":
            self.time: simulator.SimulationParameters = simulator.SimulationParameters.full_year(**self.cfg["SimulationParameters"])
        elif method == "one_day_only":
            self.time: simulator.SimulationParameters = simulator.SimulationParameters.one_day_only(**self.cfg["SimulationParameters"])
        my_sim.set_parameters(self.time)

    def add_weather(self, my_sim):
        # Sets Weather
        self.weather = Weather(**self.cfg["Weather"])
        my_sim.add_component(self.weather)

    def add_occupancy(self, my_sim):
        # Sets Occupancy
        self.occupancy = Occupancy(**self.cfg["Occupancy"], sim_params=self.time)
        my_sim.add_component(self.occupancy)
        self.add_to_electricity_grid_consumption(my_sim, self.occupancy)

    def add_pvs(self, my_sim):
        # Sets PV System
        self.pvs = PVSystem(**self.cfg["PVSystem"], sim_params=self.time)
        self.pvs.connect_similar_inputs(self.weather)
        my_sim.add_component(self.pvs)

        # Sets base grid with PVSystem
        self.electricity_grids.append(ElectricityGrid(name="BaseloadAndPVSystem", grid=[self.occupancy, "Subtract", self.pvs]))
        my_sim.add_component(self.electricity_grids[-1])

    def add_building(self, my_sim):
        # Sets Residence
        self.building = Building(**self.cfg["Building"], sim_params=self.time)
        self.building.connect_similar_inputs([self.weather, self.occupancy])
        my_sim.add_component(self.building)

    def basic_setup(self, my_sim):
        self.add_sim_param(my_sim)
        self.add_weather(my_sim)
        self.add_occupancy(my_sim)
        self.add_pvs(my_sim)
        self.add_building(my_sim)

    def add_heat_pump(self, my_sim):
        # Sets Heat Pump
        self.heat_pump = HeatPump(**self.cfg["HeatPump"], sim_params=self.time)

        # Sets Heat Pump Controller
        self.heat_pump_controller = HeatPumpController(**self.cfg["HeatPumpController"])

        self.building.connect_similar_inputs([self.heat_pump])
        #my_sim.add_component(self.building)
        #self.dummy.connect_similar_inputs([self.heat_pump])

        self.heat_pump.connect_similar_inputs([self.weather, self.heat_pump_controller])
        my_sim.add_component(self.heat_pump)

        self.heat_pump_controller.connect_similar_inputs(self.building)
        #self.heat_pump_controller.connect_similar_inputs(self.dummy)
        self.heat_pump_controller.connect_electricity(self.electricity_grids[-1])
        my_sim.add_component(self.heat_pump_controller)

        self.add_to_electricity_grid_consumption(my_sim, self.heat_pump)
        self.add_to_electricity_grid(my_sim, self.heat_pump)

    def add_to_electricity_grid(self, my_sim, next_component, electricity_grid_label=None):
        n_consumption_components = len(self.electricity_grids)
        if electricity_grid_label is None:
            electricity_grid_label = "Load{}".format(n_consumption_components)
        if n_consumption_components == 0:
            list_components = [next_component]
        else:
            list_components = [self.electricity_grids[-1], "Sum", next_component]
        self.electricity_grids.append(ElectricityGrid(name=electricity_grid_label, grid=list_components))
        #self.electricity_grids.append(self.electricity_grids[-1]+next_component)
        my_sim.add_component(self.electricity_grids[-1])
        #if hasattr(next_component, "type"):
        #    if next_component.type == "Consumer":
        #        self.add_to_electricity_grid_consumption(my_sim, next_component)

    def add_to_electricity_grid_consumption(self, my_sim, next_component, electricity_grid_label = None):
        n_consumption_components = len(self.electricity_grid_consumption)
        if electricity_grid_label is None:
            electricity_grid_label = "Consumption{}".format(n_consumption_components)
        if n_consumption_components == 0:
            list_components = [next_component]
        else:
            list_components = [self.electricity_grid_consumption[-1], "Sum", next_component]
        self.electricity_grid_consumption.append(ElectricityGrid(name=electricity_grid_label, grid=list_components))
        my_sim.add_component(self.electricity_grid_consumption[-1])

    def add_ev_charger(self, my_sim):
        # Sets Electric Vehicle
        self.my_electric_vehicle = Vehicle_Pure(**self.cfg["Vehicle_Pure"])

        # Sets EV Charger Controller
        self.ev_charger_controller = EVChargerController(**self.cfg["EVChargerController"])

        # Sets EV Charger
        self.ev_charger = EVCharger(**self.cfg["EVCharger"],
                                    electric_vehicle=self.my_electric_vehicle,
                                    sim_params=self.time)

        #########################################################################################################
        self.ev_charger_controller.connect_electricity(self.electricity_grids[-1])
        self.ev_charger_controller.connect_similar_inputs(self.ev_charger)
        my_sim.add_component(self.ev_charger_controller)

        self.ev_charger.connect_electricity(self.electricity_grids[-1])
        self.ev_charger.connect_similar_inputs(self.ev_charger_controller)
        my_sim.add_component(self.ev_charger)

        self.add_to_electricity_grid_consumption(my_sim, self.ev_charger)
        self.add_to_electricity_grid(my_sim, self.ev_charger)

    def add_flexibility(self, my_sim):
        self.flexible_controller = Controller(**self.cfg["FlexibilityController"])
        self.controllable = Controllable()

        if int(self.cfg["FlexibilityController"]["mode"]) == 1:
            self.flexible_controller.connect_electricity(self.electricity_grids[0])
        else:
            self.flexible_controller.connect_electricity(self.electricity_grids[-1])
        my_sim.add_component(self.flexible_controller)

        self.controllable.connect_similar_inputs(self.flexible_controller)
        my_sim.add_component(self.controllable)

        self.add_to_electricity_grid_consumption(my_sim, self.controllable)
        self.add_to_electricity_grid(my_sim, self.controllable)

    def add_dummy(self, my_sim):

        self.dummy = Dummy(**self.cfg["Dummy"], sim_params=self.time)
        my_sim.add_component(self.dummy)

        self.add_to_electricity_grid_consumption(my_sim, self.dummy)
        self.add_to_electricity_grid(my_sim, self.dummy)

    def add_battery(self, my_sim):
        self.battery_controller = BatteryController()

        self.battery_controller.connect_electricity(self.electricity_grids[-1])
        my_sim.add_component(self.battery_controller)

        self.battery = Battery(**self.cfg["Battery"], sim_params=self.time)
        self.battery.connect_similar_inputs(self.battery_controller)
        self.battery.connect_electricity(self.electricity_grids[-1])
        my_sim.add_component(self.battery)

        self.add_to_electricity_grid_consumption(my_sim, self.battery)
        self.add_to_electricity_grid(my_sim, self.battery)

    def add_thermal_energy_storage(self, my_sim):
        wws_c = WarmWaterStorageConfig()
        self.tes = ThermalEnergyStorage2(component_name="MyStorage", config=wws_c, sim_params=self.time)

        # storage
        self.tes.connect_input(self.tes.HeatPump_ChargingSideInput_mass, self.heat_pump.ComponentName, self.heat_pump.WaterOutput_mass)
        self.tes.connect_input(self.tes.HeatPump_ChargingSideInput_temperature, self.heat_pump.ComponentName, self.heat_pump.WaterOutput_temperature)

        #self.tes.connect_input(self.tes.Heating_DischargingSideInput_mass, householdheatdemand.ComponentName, householdheatdemand.MassOutput)
        #self.tes.connect_input(self.tes.Heating_DischargingSideInput_temperature, householdheatdemand.ComponentName, householdheatdemand.TemperatureOutput)
        self.tes.connect_input(self.tes.WW_DischargingSideInput_mass, self.occupancy.ComponentName, self.occupancy.WW_MassOutput)
        self.tes.connect_input(self.tes.WW_DischargingSideInput_temperature, self.occupancy.ComponentName, self.occupancy.WW_TemperatureOutput)


        self.occupancy.connect_input(self.occupancy.WW_MassInput, self.tes.ComponentName, self.tes.WW_DischargingSideOutput_mass)
        self.occupancy.connect_input(self.occupancy.WW_TemperatureInput, self.tes.ComponentName, self.tes.WW_DischargingSideOutput_temperature)

        self.heat_pump.connect_input(self.heat_pump.WaterConsumption, self.occupancy.ComponentName, self.occupancy.WaterConsumption)
        self.heat_pump.connect_input(self.heat_pump.WaterInput_mass, self.tes.ComponentName, self.tes.HeatPump_ChargingSideOutput_mass)
        self.heat_pump.connect_input(self.heat_pump.WaterInput_temperature, self.tes.ComponentName, self.tes.HeatPump_ChargingSideOutput_temperature)

        my_sim.add_component(self.tes)

    def close(self, my_sim):
        my_last_grid = ElectricityGrid(name="Consumed", grid=[self.electricity_grid_consumption[-1]])
        my_sim.add_component(my_last_grid)
        my_final_grid_positive = ElectricityGrid(name="NotConveredConsumed", grid=[self.electricity_grids[-1]], signal="Positive")
        my_sim.add_component(my_final_grid_positive)

if __name__ == "__main__":
    delete_all_results()


    #component_class = get_subclasses(component.Component)
    #sig = get_default_args(globals()[component_class[3]], "__init__")
    #args = get_default_args(sig)
    #print(args)
    #print()
