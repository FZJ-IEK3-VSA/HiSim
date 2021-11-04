import json
import os
import ast
import component
import inspect
import shutil
import sys
import loadtypes
import globals as gb
import numpy as np
from inspect import isclass
from pkgutil import iter_modules
from pathlib import Path as gg
from importlib import import_module

# IMPORT ALL COMPONENT CLASSES DYNAMICALLY
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

class ComponentsConnection:

    def __init__(self,
                 first_component,
                 second_component,
                 method=None):
        self.first_component = first_component
        self.second_component = second_component
        if method == "Automatic" or method is None:
            self.method = "Automatic"
            self.run()

    def run(self):
        self.configuration = {"First Component" : self.first_component,
                              "Second Component" : self.second_component,
                              "Method": self.method}

class ConfigurationGenerator:
    SimulationParameters = {"year": 2019,
                            "seconds_per_timestep": 60,
                            "method": "full_year"}

    def __init__(self, set=None):
        self.load_component_modules()

        self._simulation_parameters = {}
        self._components = {}
        self._connections = {}


    def load_component_modules(self):
        self.preloaded_components = {}
        def get_default_parameters_from_constructor(cls):
            """
            Get the default argument of either a function or
            a class

            :param obj: a function or class
            :param parameter:
            :return: a dictionary or list of the arguments
            """
            class_component = globals()[cls]
            constructor_function_var = [item for item in inspect.getmembers(class_component) if item[0] in "__init__"][0][1]
            sig = inspect.signature(constructor_function_var)
            return {k: v.default for k, v in sig.parameters.items() if
                            v.default is not inspect.Parameter.empty}

        classname = component.Component
        component_class_children = [cls.__name__ for cls in classname.__subclasses__()]

        for component_class in component_class_children:
            default_args = get_default_parameters_from_constructor(component_class)

            # Remove the simulation parameters of the list
            if "sim_params" in default_args:
                del default_args["sim_params"]

            # Save every component in the dictionary attribute
            self.preloaded_components[component_class] = default_args

    def add_simulation_parameters(self, my_simulation_parameters = None):
        if my_simulation_parameters is None:
            self._simulation_parameters = self.SimulationParameters
        else:
            pass

    def add_component(self, user_components_name):
        if isinstance(user_components_name, list):
            for user_component_name in user_components_name:
                self._components[user_component_name] = self.preloaded_components[user_component_name]
        elif isinstance(user_components_name, dict):
            for user_component_name, parameters in user_components_name.items():
                self._components[user_component_name] = parameters
        else:
            self._components[user_components_name] = self.preloaded_components[user_components_name]

    def add_connection(self, connection_components):
        number_of_connections = len(self._connections)
        i_connection = number_of_connections + 1
        connection_name = "Connection{}_{}_{}".format(i_connection,
                                                      connection_components.first_component,
                                                      connection_components.second_component)
        self._connections[connection_name] = connection_components.configuration

    def print_components(self):
        print(json.dumps(self._components, sort_keys=True, indent=4))

    def print_component(self, name):
        print(json.dumps(self._components[name], sort_keys=True, indent=4))

    def dump(self):
        self.data = {"SimulationParameters": self._simulation_parameters,
                     "Components": self._components,
                     "Connections": self._connections}
        with open(HISIMPATH["cfg"], "w") as f:
            json.dump(self.data, f, indent=4)

class SetupFunction:

    def __init__(self):
        if os.path.isfile(HISIMPATH["cfg"]):
            with open(os.path.join(HISIMPATH["cfg"])) as file:
                self.cfg = json.load(file)
        self._components = []
        self._connections = []
        self.electricity_grids : List[ElectricityGrid] = []
        self.electricity_grid_consumption : List[ElectricityGrid] = []

    def build(self, my_sim):
        self.add_simulation_parameters(my_sim)
        for comp in self.cfg["Components"]:
            if comp in globals():
                self.add_component(comp, my_sim)
        for connection_key, connection_value in self.cfg["Connections"].items():
            self.add_connection(connection_value, my_sim)

    def add_simulation_parameters(self, my_sim):
        # Timeline configuration
        method = self.cfg["SimulationParameters"]["method"]
        self.cfg["SimulationParameters"].pop("method", None)
        if method == "full_year":
            self._simulation_parameters: simulator.SimulationParameters = simulator.SimulationParameters.full_year(**self.cfg["SimulationParameters"])
        elif method == "one_day_only":
            self._simulation_parameters: simulator.SimulationParameters = simulator.SimulationParameters.one_day_only(**self.cfg["SimulationParameters"])
        my_sim.set_parameters(self._simulation_parameters)

    def add_component(self, comp, my_sim, electricity_output=None):
        # Save parameters of class
        # Retrieve class signature
        signature = inspect.signature(globals()[comp])
        # Find if it has SimulationParameters and pass value
        for parameter_name in signature.parameters:
            if signature.parameters[parameter_name].annotation == component.SimulationParameters:
                self.cfg["Components"][comp][parameter_name] = my_sim.SimulationParameters
        self._components.append(globals()[comp](**self.cfg["Components"][comp]))
        # Add last listed component to Simulator object
        my_sim.add_component(self._components[-1])
        if electricity_output is not None:
            pass
            #self.add_to_electricity_grid_consumption(my_sim, self.occupancy)

    def add_connection(self, connection, my_sim):
        for component in self._components:
            if type(component).__name__ == connection["Second Component"]:
                second_component = component
            elif type(component).__name__ == connection["First Component"]:
                first_component = component

        if connection["Method"] == "Automatic":
            second_component.connect_similar_inputs(first_component)



    def add_occupancy(self, my_sim):
        # Sets Occupancy
        self.occupancy = Occupancy(**self.cfg["Occupancy"])
        my_sim.add_component(self.occupancy)
        self.add_to_electricity_grid_consumption(my_sim, self.occupancy)

    def add_pvs(self, my_sim):
        # Sets PV System
        self.pvs = PVSystem(**self.cfg["PVSystem"], sim_params=self.time)
        self.pvs.connect_similar_inputs(self.weather)
        my_sim.add_component(self.pvs)

        # Sets base grid with PVSystem
        #self.electricity_grids.append(ElectricityGrid(name="BaseloadAndPVSystem", grid=[self.occupancy, "Subtract", self.pvs]))
        #my_sim.add_component(self.electricity_grids[-1])


    def add_building(self, my_sim):
        # Sets Residence
        self.building = Building(**self.cfg["Building"], sim_params=self.time)
        self.building.connect_similar_inputs([self.weather, self.occupancy])
        my_sim.add_component(self.building)

    def basic_setup(self, my_sim):
        self.add_sim_param(my_sim)
        self.add_csv_load_power(my_sim)
        self.add_weather(my_sim)
        #self.add_occupancy(my_sim)
        self.add_pvs(my_sim)
        #self.add_building(my_sim)

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

    def add_csv_load_power(self,my_sim):
        self.csv_load_power_demand = CSVLoader(component_name="csv_load_power",
                                          csv_filename="Lastprofile/SOSO/Orginal/EFH_Bestand_TRY_5_Profile_1min.csv",
                                          column=0,
                                          loadtype=loadtypes.LoadTypes.Electricity,
                                          unit=loadtypes.Units.Watt,
                                          column_name="power_demand",
                                          simulation_parameters=my_sim,
                                          multiplier=6)
        my_sim.add_component(self.csv_load_power_demand)

    def add_controller(self,my_sim):

        self.controller = Controller()
        self.controller.connect_input(self.controller.ElectricityToOrFromBatteryReal,
                                    self.advanced_battery.ComponentName,
                                    self.advanced_battery.ACBatteryPower)
        self.controller.connect_input(self.controller.ElectricityConsumptionBuilding,
                                    self.csv_load_power_demand.ComponentName,
                                    self.csv_load_power_demand.Output1)
        self.controller.connect_input(self.controller.ElectricityOutputPvs,
                                    self.pvs.ComponentName,
                                    self.pvs.ElectricityOutput)
        my_sim.add_component(self.controller)

        self.advanced_battery.connect_input(self.advanced_battery.LoadingPowerInput,
                                            self.controller.ComponentName,
                                            self.controller.ElectricityToOrFromBatteryTarget)

    def close(self):
        pass
