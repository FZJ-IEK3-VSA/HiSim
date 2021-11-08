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
                 method=None,
                 first_component_output=None,
                 second_component_input=None):
        self.first_component = first_component
        self.second_component = second_component
        self.first_component_output = first_component_output
        self.second_component_input = second_component_input
        if method == "Automatic" or method is None:
            self.method = "Automatic"
            self.run_automatic()
        elif method == "Manual":
            self.method = method
            self.run_manual()

    def run_automatic(self):
        self.configuration = {"First Component" : self.first_component,
                              "Second Component" : self.second_component,
                              "Method": self.method}
    def run_manual(self):
        self.configuration = {"First Component" : self.first_component,
                              "Second Component" : self.second_component,
                              "Method": self.method,
                              "First Component Output" : self.first_component_output,
                              "Second Component Input" : self.second_component_input}

class ComponentsGrouping:

    def __init__(self,
                 component_name,
                 operation,
                 first_component,
                 second_component,
                 first_component_output,
                 second_component_output):
        self.component_name = component_name
        self.operation = operation
        self.first_component = first_component
        self.second_component = second_component
        self.first_component_output = first_component_output
        self.second_component_output = second_component_output
        self.run()

    def run(self):
        self.configuration = {"Component Name": self.component_name,
                              "Operation": self.operation,
                              "First Component": self.first_component,
                              "Second Component": self.second_component,
                              "First Component Output": self.first_component_output,
                              "Second Component Output": self.second_component_output}

class ConfigurationGenerator:
    SimulationParameters = {"year": 2019,
                            "seconds_per_timestep": 60,
                            "method": "full_year"}

    def __init__(self, set=None):
        self.load_component_modules()
        self._simulation_parameters = {}
        self._components = {}
        self._groupings = {}
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

    def add_grouping(self, grouping_components):
        self._groupings[grouping_components.component_name] = grouping_components.configuration

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
                     "Groupings": self._groupings,
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
        self._groupings = []
        self.electricity_grids : List[ElectricityGrid] = []
        self.electricity_grid_consumption : List[ElectricityGrid] = []

    def build(self, my_sim):
        self.add_simulation_parameters(my_sim)
        for comp in self.cfg["Components"]:
            if comp in globals():
                self.add_component(comp, my_sim)
        for grouping_key, grouping_value in self.cfg["Groupings"].items():
            self.add_grouping(grouping_value, my_sim)
        for connection_key, connection_value in self.cfg["Connections"].items():
            self.add_connection(connection_value)

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
            if signature.parameters[parameter_name].annotation == component.SimulationParameters or parameter_name == "my_simulation_parameters":
                self.cfg["Components"][comp][parameter_name] = my_sim.SimulationParameters
        self._components.append(globals()[comp](**self.cfg["Components"][comp]))
        # Add last listed component to Simulator object
        my_sim.add_component(self._components[-1])
        if electricity_output is not None:
            pass
            #ToDo: Implement electricity sum here.

    def add_grouping(self, grouping, my_sim):
        for component in self._components:
            if type(component).__name__ == grouping["Second Component"]:
                second_component = component
            elif type(component).__name__ == grouping["First Component"]:
                first_component = component

        my_concatenated_component = CalculateOperation(name=grouping["Component Name"])
        my_concatenated_component.connect_input(src_object_name=first_component.ComponentName,
                                                src_field_name=getattr(first_component, grouping["First Component Output"]))
        my_concatenated_component.add_operation(operation=grouping["Operation"])
        my_concatenated_component.connect_input(src_object_name=second_component.ComponentName,
                                                src_field_name=getattr(second_component, grouping["Second Component Output"]))
        self._components.append(my_concatenated_component)
        my_sim.add_component(my_concatenated_component)

    def add_connection(self, connection):
        first_component = None
        second_component = None

        for component in self._components:
            if component.ComponentName == connection["Second Component"]:
                second_component = component
            elif component.ComponentName == connection["First Component"]:
                first_component = component

        if connection["Method"] == "Automatic":
            second_component.connect_similar_inputs(first_component)
        elif connection["Method"] == "Manual":
            try:
                second_component.connect_input(input_fieldname=getattr(second_component, connection["Second Component Input"]),
                                               src_object_name=first_component.ComponentName,
                                               src_field_name=getattr(first_component, connection["First Component Output"]))
            except ValueError:
                print("Incorrect Connection")

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



