import json
import os
import inspect
import copy
import sys
from inspect import isclass
from pkgutil import iter_modules
from pathlib import Path as gg
from importlib import import_module
import logging

import hisim.component as component
import hisim.simulator as sim
import hisim.hisim_main
from hisim.utils import HISIMPATH
import hisim.simulator

__authors__ = "Vitor Hugo Bellotto Zago"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"

#logging.basicConfig(level=#logging.INFO)

# IMPORT ALL COMPONENT CLASSES DYNAMICALLY
# DIRTY CODE. GIVE ME BETTER SUGGESTIONS

# iterate through the modules in the current package
package_dir = os.path.join(gg(__file__).resolve().parent, "components")

for (_, module_name, _) in iter_modules([package_dir]):

    # import the module and iterate through its attributes
    module = import_module(f"hisim.components.{module_name}")
    for attribute_name in dir(module):
        attribute = getattr(module, attribute_name)

        if isclass(attribute):
            # Add the class to this package's variables
            globals()[attribute_name] = attribute



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

    """
    todo Max:
    - 1. issue: SimulationParameters are hard coded in ConfigurationGenerator
        - implement function for adding SimulationParameter in CFG-Automator
          like a Component in basic_household


    - 2. issue: String-Call in basic_household for Simulation Setup:
                Name call of classes in basic_household has to be exactly the same than classname
                of component
        - import all components in basic_household and call class by real name
        - problem: how to handle generic components

        - factory functions -> desgin pattern

    - 3. issue: saved cfg-json is always named "cfg.json"
        - specify name of cfg with hashlib, so that multiple cfgs can be started at the same time
        - with saved hash can be checked, if a Simulation with same parameters has been done before

    Create a json file containing the configuration of the setup
    function to be run in HiSim. The configuration is done into
    4 parts:
        1) Setting the simulation parameters (add_simulation_parameters).
        2) Adding each Component (add_component). If the user does not define the arguments
        of the Component object, the default values are taken instead.
        3) Adding groupings (add_groupings). After adding the components, the user might need other components
        that are derived by a combination of single components. Groupings are components made of a combination
        of outputs of previously created components.
        4) Adding connections (add_connections). Connections between outputs and inputs of different components
        indicate to the simulator the flow of energy, mass or information.

    After all the configuration information has been provided, the user can:
        - Run a single simulation on the configuration file with the execution of 'dump' and 'run'.
        - Perform a parameter study, proving a range of a parameter used in the listed configuration. In this case,
        the 'dump' command does not have to be executed. The parameter study is performed with 'run_parameter_study',
        provided a dictionary with the range of the parameters.
    """
    SimulationParameters = {"year": 2019,
                            "seconds_per_timestep": 60*15,
                            "method": "full_year"}

    def __init__(self):
        self.load_component_modules()
        self._simulation_parameters = {}
        self._components = {}
        self._groupings = {}
        self._connections = {}
        self._parameters_range_studies = {}

    def load_component_modules(self):
        """
        Load dynamically all classes implemented under the
        'components' directory. With that said, the user
        does not have to import a recently implemented Component class
        in the cfg_automator module.

        """
        self.preloaded_components = {}
        def get_default_parameters_from_constructor(class_component):
            """
            Get the default arguments of either a function or
            a class

            :param obj: a function or class
            :param parameter:
            :return: a dictionary or list of the arguments
            """
            constructor_function_var = [item for item in inspect.getmembers(class_component) if item[0] in "__init__"][0][1]
            sig = inspect.signature(constructor_function_var)
            return {k: v.default for k, v in sig.parameters.items() if
                            v.default is not inspect.Parameter.empty}

        classname = component.Component
        component_class_children = [cls for cls in classname.__subclasses__() if cls != component.DynamicComponent]
        #component_class_children = [cls.__name__ for cls in classname.__subclasses__() if cls != component.DynamicComponent]

        for component_class in component_class_children:
            default_args = get_default_parameters_from_constructor(component_class)

            # Remove the simulation parameters of the list
            if "sim_params" in default_args:
                del default_args["sim_params"]

            # Save every component in the dictionary attribute
            self.preloaded_components[component_class] = default_args

    def add_simulation_parameters(self, my_simulation_parameters = None):
        """
        Add the simulation parameters to the configuration JSON file list.

        Parameters
        ----------
        my_simulation_parameters: contains the parameters for defining the simulation
        timeline.
        """
        if my_simulation_parameters is None:
            self._simulation_parameters = self.SimulationParameters
            #logging.info("Simulation parameters have been added to the configuration JSON file.")
        else:
            pass

    def add_component(self, user_components_name):
        """
        Add the component to the configuration JSON file list. It can read three types of arguments:

        String: the string should contain the name of a Component class implemented in the 'components' directory.
        In this case, the component object will be implemented in the setup function with the default values.

        List: the list of strings should contain the names of Component classes implemented in the 'components' director-y.
        In this case, the component objects will be implemented in the setup function with the default values.

        Dictionary: the dictionary containing at the first level the name of Component classes, and in the two level,
        the arguments. In this case, if any argument is not explicitly provided by the user, the default values are used
        instead.

        Parameters
        ----------
        user_components_name: string, list of strings, or a dictionary.
        """
        if isinstance(user_components_name, list):
            for user_component_name in user_components_name:
                self._components[user_component_name] = self.preloaded_components[user_component_name]
                #logging.info("Component {} has been added to the configuration JSON file.".format(user_component_name))
        elif isinstance(user_components_name, dict):
            for user_component_name, parameters in user_components_name.items():
                if user_component_name.__class__ == str:
                    self._components[user_component_name] = parameters
                else:

                    self._components[user_component_name.__module__ +"."+ user_component_name.__name__] = parameters.__dict__
                #logging.info("Component {} has been added to the configuration JSON file.".format(user_component_name))
        else:
            self._components[user_components_name.__module__] = user_components_name.__doc__
            #self._components[user_components_name] = self.preloaded_components[user_components_name]
            #logging.info("Component {} has been added to configuration JSON file.".format(user_components_name))

    def add_grouping(self, grouping_components: ComponentsGrouping):
        """
        Add component grouping created out of the combination of previously created components. The Grouping component
        yields either a sum, subtraction or another operation combining multiple outputs of the previously
        assigned components. Let a grouping component be the subtraction of a load profile in CSVLoader from PVSystem:

        (First Component)               (Second Component)                      (Operation)
        Name: CSVLoader                 Name: PVSystem                          Subtraction
        ComponentOutput: Output1        ComponentOutput: ElectricityOutput

        The grouping is set as follows:

        my_grouping = ComponentsGrouping(component_name="Sum_PVSystem_CSVLoader",
                                         operation="Subtract",
                                         first_component="CSVLoader",
                                         second_component="PVSystem",
                                         first_component_output="Output1",
                                         second_component_output="ElectricityOutput")
        ----------
        grouping_components: ComponentsGrouping
        """
        self._groupings[grouping_components.component_name] = grouping_components.configuration
        #logging.info("Component Grouping {} has been created.".format(grouping_components.component_name))

    def add_connection(self, connection_components: ComponentsConnection):
        """
        Add connections among the previously assigned components. Connections can be performed manually or
        automatically.


        Parameters
        ----------
        connection_components

        Returns
        -------

        """
        number_of_connections = len(self._connections)
        i_connection = number_of_connections + 1
        connection_name = "Connection{}_{}_{}".format(i_connection,
                                                      connection_components.first_component,
                                                      connection_components.second_component)
        self._connections[connection_name] = connection_components.configuration
        #logging.info("{} and {} have been connected ")

    def add_paramater_range(self, parameter_range):
        self._parameters_range_studies.update(parameter_range)

    def reset(self):
        self._simulation_parameters = {}
        self._components = {}
        self._groupings = {}
        self._connections = {}
        self._parameters_range_studies = {}

    def print_components(self):
        print(json.dumps(self._components, sort_keys=True, indent=4))

    def print_component(self, name):
        print(json.dumps(self._components[name], sort_keys=True, indent=4))

    def dump(self):

        self.data = {"SimulationParameters": self._simulation_parameters,
                     "Components": self._components,
                     "Groupings": self._groupings,
                     "Connections": self._connections}
        with open(""+HISIMPATH["cfg"], "w") as f:
            json.dump(self.data, f, indent=4)

    def run(self):
        hisim.main("cfg_automator", "basic_household_implicit")

    def run_parameter_studies(self):
        for component_class, parameter_name_and_range in self._parameters_range_studies.items():
            #logging.info("Performing parameter study of component {}".format(component))
            parameters_range_studies_entry = copy.deepcopy(self._parameters_range_studies)
            if isinstance(parameter_name_and_range, dict):
                for parameter_name, range in parameter_name_and_range.items():
                    cached_range = range
            for value in cached_range:
                parameters_range_studies_entry[component_class][parameter_name] = value
                self.add_component(parameters_range_studies_entry)
                self.dump()
                self.run()

    def run_cross_parameter_studies(self):
        pass

class SetupFunction:

    def __init__(self):
        if os.path.isfile(""+HISIMPATH["cfg"]):
            with open(os.path.join(""+HISIMPATH["cfg"])) as file:
                self.cfg = json.load(file)
            #os.remove(""+HISIMPATH["cfg"])
        self.cfg_raw = copy.deepcopy(self.cfg)
        self._components = []
        self._connections = []
        self._groupings = []
        self.electricity_grids : List[ElectricityGrid] = []
        self.electricity_grid_consumption : List[ElectricityGrid] = []
        self.component_class_children=[]
    def build(self, my_sim):
        self.add_simulation_parameters(my_sim)
        self.component_class_children = self.find_all_component_class_children()
        for comp in self.cfg["Components"]:
            if comp in globals():
                if comp == "Weather":
                    continue
                self.add_component(comp, my_sim)
        for grouping_key, grouping_value in self.cfg["Groupings"].items():
            self.add_grouping(grouping_value, my_sim)
        for connection_key, connection_value in self.cfg["Connections"].items():
            self.add_connection(connection_value)
        self.add_configuration(my_sim)

    def add_simulation_parameters(self, my_sim):
        # Timeline configuration
        method = self.cfg["SimulationParameters"]["method"]
        self.cfg["SimulationParameters"].pop("method", None)
        if method == "full_year":
            self._simulation_parameters: sim.SimulationParameters = sim.SimulationParameters.full_year(**self.cfg["SimulationParameters"])
        elif method == "one_day_only":
            self._simulation_parameters: sim.SimulationParameters = sim.SimulationParameters.one_day_only(**self.cfg["SimulationParameters"])
        my_sim.set_parameters(self._simulation_parameters)


    def find_all_component_class_children(self ):
        classname = component.Component
        component_class_children = [cls for cls in classname.__subclasses__() if cls != component.DynamicComponent]
        return component_class_children
    def add_component(self, comp, my_sim, electricity_output=None):
        # Save parameters of class
        # Retrieve class signature
        signature = inspect.signature(globals()[comp])
        # Find if it has SimulationParameters and pass value
        for parameter_name in signature.parameters:
            if signature.parameters[parameter_name].annotation == component.SimulationParameters or parameter_name == "my_simulation_parameters":
                self.cfg["Components"][comp][parameter_name] = my_sim.SimulationParameters
        try:
            self._components.append(globals()[comp](**self.cfg["Components"][comp]))
        except Exception as e:
            print("Adding Component {} resulted in a failure".format(comp))
            print("Please, investigate implementation mistakes in this Component.")
            print(e)
            sys.exit(1)
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

    def add_configuration(self, my_sim: sim.Simulator):
        #my_sim.add_configuration(self.cfg_raw)
        pass

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

def basic_household_implicit(my_sim: sim.Simulator):
    my_setup_function = SetupFunction()
    my_setup_function.build(my_sim)