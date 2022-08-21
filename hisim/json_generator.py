""" Generates a Json file for the json executor. """

import copy
import inspect
import json
from typing import Dict, Any, Union, Optional

from hisim import component
from hisim import dynamic_component
from hisim import log
from hisim.utils import HISIMPATH


class ComponentsConnection:

    """ Represents a component connection for the json file. """

    def __init__(
        self,
        first_component: str,
        second_component: str,
        method: str = None,
        first_component_output: Optional[str] = None,
        second_component_input: Optional[str] = None,
    ) -> None:
        """ Initializes configuration for a component connection. """
        self.first_component = first_component
        self.second_component = second_component
        self.first_component_output = first_component_output
        self.second_component_input = second_component_input
        self.configuration: Dict[Any, Any]
        if method == "Automatic" or method is None:
            self.method = "Automatic"
            self.run_automatic()
        elif method == "Manual":
            self.method = method
            self.run_manual()

    def run_automatic(self) -> None:
        """ Run automatic config. """
        self.configuration = {
            "First Component": self.first_component,
            "Second Component": self.second_component,
            "Method": self.method,
        }

    def run_manual(self) -> None:
        """ Run manual config. """
        self.configuration = {
            "First Component": self.first_component,
            "Second Component": self.second_component,
            "Method": self.method,
            "First Component Output": self.first_component_output,  # noqa
            "Second Component Input": self.second_component_input,  # noqa
        }


class ComponentsGrouping:  # noqa: too-few-public-methods

    """ For making a components grouping configuration. """

    def __init__(
        self,
        component_name: str,
        operation: str,
        first_component: str,
        second_component: str,
        first_component_output: str,
        second_component_output: str,
    ) -> None:
        """ Initializes a configuration. """
        self.component_name = component_name
        self.operation = operation
        self.first_component = first_component
        self.second_component = second_component
        self.first_component_output = first_component_output
        self.second_component_output = second_component_output
        self.configuration = {
            "Component Name": self.component_name,
            "Operation": self.operation,
            "First Component": self.first_component,
            "Second Component": self.second_component,
            "First Component Output": self.first_component_output,
            "Second Component Output": self.second_component_output,
        }


class ConfigurationGenerator:

    """ Generates a json configuration from a Python file.

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

    def __init__(self) -> None:
        """ Initializes a single simulation. """
        self.load_component_modules()
        self._simulation_parameters: Dict[str, Any] = {}
        self._components: Dict[Any, Any] = {}
        self._groupings: Dict[Any, Any] = {}
        self._connections: Dict[Any, Any] = {}
        self._parameters_range_studies: Dict[Any, Any] = {}
        self.data: Dict[Any, Any]

    def set_name(self, name_to_set: Any) -> Any:
        """ Sets the name of a module. """
        return name_to_set.__module__ + "." + name_to_set.__name__

    def load_component_modules(self) -> None:
        """ Load dynamically all classes implemented under the 'components' directory.

        With that said, the user does not have to import a recently implemented Component class in the cfg_automator module.
        """
        self.preloaded_components = {}

        def get_default_parameters_from_constructor(class_component: Any) -> Dict[Any, Any]:
            """ Get the default arguments of either a function or a class. """
            constructor_function_var = [
                item
                for item in inspect.getmembers(class_component)
                if item[0] in "__init__"
            ][0][1]
            sig = inspect.signature(constructor_function_var)
            return {
                k: v.default
                for k, v in sig.parameters.items()
                if v.default is not inspect.Parameter.empty
            }

        classname = component.Component
        component_class_children = [
            cls
            for cls in classname.__subclasses__()
            if cls != dynamic_component.DynamicComponent
        ]
        # component_class_children = [cls.__name__ for cls in classname.__subclasses__() if cls != component.DynamicComponent]

        for component_class in component_class_children:
            default_args = get_default_parameters_from_constructor(component_class)

            # Remove the simulation parameters of the list
            if "sim_params" in default_args:
                del default_args["sim_params"]

            # Save every component in the dictionary attribute
            self.preloaded_components[component_class] = default_args

    def add_simulation_parameters(self, my_simulation_parameters: Union[None, Dict[str, Any]] = None) -> None:
        """ Add the simulation parameters to the configuration JSON file list. """
        if my_simulation_parameters is None:
            log.debug("no simulation Parameters are added")
        else:
            self._simulation_parameters = my_simulation_parameters

    def add_component(self, user_components_name: Any) -> None:
        """ Add the component to the configuration JSON file list.

        It can read three types of arguments:

        String: the string should contain the name of a Component class implemented in the 'components' directory.
        In this case, the component object will be implemented in the setup function with the default values.

        List: the list of strings should contain the names of Component classes implemented in the 'components' director-y.
        In this case, the component objects will be implemented in the setup function with the default values.

        Dictionary: the dictionary containing at the first level the name of Component classes, and in the two level,
        the arguments. In this case, if any argument is not explicitly provided by the user, the default values are used
        instead.
        """
        if isinstance(user_components_name, list):
            for user_component_name in user_components_name:
                self._components[user_component_name] = self.preloaded_components[
                    user_component_name
                ]
            return
        if isinstance(user_components_name, dict):
            for user_component_name, parameters in user_components_name.items():
                if parameters.__class__ == dict:
                    self._components[user_component_name] = parameters
                    continue

                if str(user_component_name) in self._components:
                    # quick annd dirty solution. checks if maximum of 10 components of the same are added
                    for number in range(1, 9):
                        if (
                            str(user_component_name) + "_number" + str(number)
                            in self._components
                        ):
                            continue

                        self._components[
                            str(user_component_name) + "_number" + str(number)
                        ] = parameters.__dict__
                        break
                else:
                    self._components[str(user_component_name)] = parameters.__dict__
                    # self._components[user_component_name.__module__ +"."+ user_component_name.__name__] = parameters.__dict__
            return

        self._components[
            user_components_name.__module__
        ] = user_components_name.__doc__
        # self._components[user_components_name] = self.preloaded_components[user_components_name]

    def add_grouping(self, grouping_components: ComponentsGrouping) -> None:
        """ Add component grouping created out of the combination of previously created components.

        The Grouping component yields either a sum, subtraction or another operation combining multiple outputs of the previously
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
        self._groupings[
            grouping_components.component_name
        ] = grouping_components.configuration

    def add_connection(self, connection_components: Any) -> None:
        """ Add connections among the previously assigned components. Connections can be performed manually or automatically. """
        number_of_connections = len(self._connections)
        i_connection = number_of_connections + 1
        connection_name = f"Connection{i_connection}_{connection_components.first_component}_{connection_components.second_component}"
        self._connections[connection_name] = connection_components.configuration

    def add_paramater_range(self, parameter_range: Any) -> None:
        """ Adds a parameter range for a parameter study. """
        self._parameters_range_studies.update(parameter_range)

    def reset(self) -> None:
        """ Resets the entire thing. """
        self._simulation_parameters = {}
        self._components = {}
        self._groupings = {}
        self._connections = {}
        self._parameters_range_studies = {}

    def print_components(self) -> None:
        """ Prints all components. """
        log.trace(json.dumps(self._components, sort_keys=True, indent=4))

    def print_component(self, name: str) -> None:
        """ Prints a single component. """
        log.trace(json.dumps(self._components[name], sort_keys=True, indent=4))

    def dump(self) -> None:
        """ Dumps the entire config file as json. """
        self.data = {
            "SimulationParameters": self._simulation_parameters,
            "Components": self._components,
            "Groupings": self._groupings,
            "Connections": self._connections,
        }
        with open("" + HISIMPATH["cfg"], "w", encoding="utf-8") as filestream:
            json.dump(self.data, filestream, indent=4)

    def run_parameter_studies(self) -> None:
        """ Run a single parameter study. """
        for (
            component_class,
            parameter_name_and_range,
        ) in self._parameters_range_studies.items():
            parameters_range_studies_entry = copy.deepcopy(
                self._parameters_range_studies
            )
            if isinstance(parameter_name_and_range, dict):
                for parameter_name, _range in parameter_name_and_range.items():
                    cached_range = _range
                    for value in cached_range:
                        parameters_range_studies_entry[component_class][parameter_name] = value
                        self.add_component(parameters_range_studies_entry)
                        self.dump()
                        # self.run()
