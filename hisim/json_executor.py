""" Executes a json file in the simulator. """
import copy
import inspect
import json
import os
import sys
from typing import List, Any, Tuple, Dict
from inspect import isclass
from pkgutil import iter_modules
from pathlib import Path as PathlibPath
from importlib import import_module

from hisim import component
from hisim import dynamic_component
from hisim import simulator as sim
from hisim.json_generator import ComponentsConnection, ComponentsGrouping
from hisim.simulator import Simulator
from hisim.utils import HISIMPATH
from hisim import log


class JsonExecutor:

    """ Executes a Jsonfile as a simulation. """

    def component_importer(self) -> None:
        """ Imports a config. """
        package_dir = os.path.join(PathlibPath(__file__).resolve().parent, "components")

        for (_idx1, module_name, _idx2) in iter_modules([package_dir]):

            # import the module and iterate through its attributes
            module = import_module(f"hisim.components.{module_name}")
            for attribute_name in dir(module):
                attribute = getattr(module, attribute_name)

                if isclass(attribute):
                    # Add the class to this package's variables
                    globals()[attribute_name] = attribute

    def __init__(self) -> None:
        """ Initializes the class. """
        self.my_simulation_parameters: sim.SimulationParameters
        file_path = str(HISIMPATH["cfg"])
        if os.path.isfile(file_path):
            with open(file_path, encoding="utf-8") as file:
                self.cfg = json.load(file)
            # os.remove(""+HISIMPATH["cfg"])
        else:
            raise RuntimeError(f"File does not exist: {file_path}")
        self.cfg_raw = copy.deepcopy(self.cfg)
        self._components: List[Any] = []
        self._connections: List[ComponentsConnection] = []
        self._groupings: List[ComponentsGrouping] = []
        # self.electricity_grids : List[ElectricityGrid] = []
        # self.electricity_grid_consumption : List[ElectricityGrid] = []
        self.component_class_children: List[str] = []
        self.component_file_children: List[str] = []
        self.component_module_list: List[str] = []

    def build(self, my_sim: Simulator) -> None:
        """ Builds the parameters. """
        self.add_simulation_parameters(my_sim)
        (
            self.component_class_children,
            self.component_file_children,
            self.component_module_list,
        ) = self.find_all_component_class_children()
        for comp in self.cfg["Components"]:
            number_of_comp = ""
            if comp.__contains__("_number"):
                # quick annd dirty solution. checks if maximum of 10 components of the same are added
                for number in range(1, 9):
                    if comp.__contains__("_number" + str(number)):
                        comp = comp.replace("_number" + str(number), "")
                        number_of_comp = "_number" + str(number)

            if comp in self.component_class_children:
                self.add_component(comp, my_sim, number_of_comp)
        # for _grouping_key, grouping_value in self.cfg["Groupings"].items():
        #    self.add_grouping(grouping_value)

        for _connection_key, connection_value in self.cfg["Connections"].items():
            self.add_connection(connection_value)
        # self.add_configuration(my_sim)

    def add_simulation_parameters(self, my_sim: Simulator) -> Any:
        """ Adds a simulation parameters object. """
        # Timeline configuration
        method = self.cfg["SimulationParameters"]["method"]
        self.cfg["SimulationParameters"].pop("method", None)

        if method == "full_year":
            self.my_simulation_parameters = (
                sim.SimulationParameters.full_year_all_options(
                    **self.cfg["SimulationParameters"]
                )
            )
        elif method == "one_day_only":
            self.my_simulation_parameters = sim.SimulationParameters.one_day_only(
                **self.cfg["SimulationParameters"]
            )
        my_sim.set_simulation_parameters(self.my_simulation_parameters)

    def find_all_component_class_children(self) -> Tuple[List[Any], List[str], List[str]]:
        """ Finds all children for a component class. """
        classname = component.Component
        component_file_children: List[str] = []
        component_module: List[str] = []
        component_class_children = [
            cls.__module__ + "." + cls.__name__
            for cls in classname.__subclasses__()
            if cls != dynamic_component.DynamicComponent
        ]
        component_class_children_list: List[type[component.Component]] = [
            cls
            for cls in classname.__subclasses__()
            if cls != dynamic_component.DynamicComponent
        ]

        for file_child in component_class_children_list:
            component_module.append(file_child.__module__)
            component_file_children.append(file_child.__name__)
            component_file_children.append(file_child.__name__ + "Config")

        # return component_class_children, component_file_children
        return component_class_children, component_file_children, component_module

    def get_path(self, class_with_path: Any) -> str:
        """ Gets a class path. """
        mypath: str = class_with_path.__module__ + "." + class_with_path.__name__
        return mypath

    def add_component(self, full_class_path: str, my_sim: Simulator, number_of_comp: str, electricity_output: Any = None) -> None:
        """ Adds and initializes a component.

        # Save parameters of class
        # Retrieve class signature
        path_to_components = dirname(__file__) + "/components"
        list_of_all_components= glob.glob(join(path_to_components, "*.py"))
        stripped_list_of_all_components=[]
        seperater="HiSim"
        for component_path in list_of_all_components:
            stripped = component_path.split(seperater, 1)[1]
            stripped = stripped.replace("/", ".")
            stripped = stripped.replace(".py", "")
            stripped_list_of_all_components.append(stripped)
        """

        clsmembers: List[Any] = []
        full_instance_path = full_class_path + number_of_comp
        clsmembers = self.get_class_members_for_components(clsmembers, full_class_path)

        component_class_config_to_add, component_class_to_add, signature_component = self.initialize_component_classes(
            clsmembers, full_class_path)

        # Find if it has SimulationParameters and pass value
        for parameter_name in signature_component.parameters:
            if (
                # double check in case the type annotation is missing
                signature_component.parameters[parameter_name].annotation
                == component.SimulationParameters
                or parameter_name == "my_simulation_parameters"
            ):
                self.cfg["Components"][full_instance_path][
                    parameter_name
                ] = my_sim._simulation_parameters  # noqa: protected-access
        try:

            # self.cfg["Components"][comp].__delitem__("my_simulation_parameters")
            config_class = component_class_config_to_add.from_dict(
                self.cfg["Components"][full_instance_path]
            )
            self._components.append(
                component_class_to_add(
                    config=config_class,
                    my_simulation_parameters=self.my_simulation_parameters,
                )
            )

        except Exception as my_exception:  # noqa: broad-except
            log.debug(
                f"Adding Component {full_instance_path} resulted in a failure"
            )
            log.debug(f"Might be Missing :   {component_class_to_add} ")
            log.debug("Please, investigate implementation mistakes in this Component.")
            log.error(str(my_exception))
            sys.exit(1)
        # Add last listed component to Simulator object
        my_sim.add_component(self._components[-1])
        if electricity_output is not None:
            pass
            # ToDo: Implement electricity sum here.

    def initialize_component_classes(self, clsmembers, full_class_path):
        """ Initializes the component classes that were identified earlier. """
        component_class_to_add = None
        for _member_type, component_class in clsmembers:
            if self.get_path(component_class) == full_class_path:
                try:
                    component_class_to_add = component_class
                    signature_component = inspect.signature(component_class)
                except Exception as my_exception:
                    log.error(f"No relevant_component added. Investigate in Component: {full_class_path} ")
                    log.error(str(my_exception))
                    raise RuntimeError(
                        f"Could not find the class for the component {full_class_path}") from my_exception
            elif self.get_path(component_class) == full_class_path + "Config":
                try:
                    component_class_config_to_add = component_class
                except Exception as my_exception:
                    log.error(f"No relevant_component_config added. Investigate in Component: {full_class_path} ")
                    log.error(str(my_exception))
                    raise RuntimeError(
                        f"Could not find the config class for the component {full_class_path}") from my_exception
        if component_class_to_add is None:
            raise RuntimeError(
                f"Could not find the class for the component {full_class_path}"
            )
        if component_class_config_to_add is None:
            raise RuntimeError(
                f"Could not find the config class for the component {full_class_path}"
            )
        return component_class_config_to_add, component_class_to_add, signature_component

    def get_class_members_for_components(self, clsmembers, full_class_path):
        """ Gets the class members for components. """
        for component_to_check in self.component_class_children:
            try:
                if component_to_check == full_class_path:
                    # removes the last part (after the last dot) of the component string (the class name)
                    seperater = "."
                    stripped = ""
                    splitted_string = component_to_check.split(
                        seperater, component_to_check.count(".")
                    )
                    for i in range(component_to_check.count(".")):
                        if stripped == "":
                            stripped = splitted_string[i]
                        else:
                            stripped = stripped + "." + splitted_string[i]
                    clsmembers = [
                        (name, cls)
                        for name, cls in inspect.getmembers(
                            sys.modules[stripped], inspect.isclass
                        )
                        if cls.__module__ == stripped
                    ]
            except Exception as my_exception:  # noqa: broad-except
                # just continue
                log.trace(str(my_exception))
        if clsmembers is None:
            raise RuntimeError("No class members were found")
        return clsmembers

    # def add_grouping(self, grouping: Dict[Any, Any]) -> None:
        # """ Adds a component grouping. """
        # for my_component in self._components:
        #     if type(my_component).__name__ == grouping["Second Component"]:
        #         second_component = my_component
        #     elif type(my_component).__name__ == grouping["First Component"]:
        #         first_component = my_component
        # """
        # my_concatenated_component = CalculateOperation(name=grouping["Component Name"])
        # my_concatenated_component.connect_input(src_object_name=first_component.ComponentName,
        #                                         src_field_name=getattr(first_component, grouping["First Component Output"]))
        # my_concatenated_component.add_operation(operation=grouping["Operation"])
        # my_concatenated_component.connect_input(src_object_name=second_component.ComponentName,
        #                                         src_field_name=getattr(second_component, grouping["Second Component Output"]))
        # self._components.append(my_concatenated_component)
        # my_sim.add_component(my_concatenated_component)
        #
        # """

    def add_connection(self, connection: Dict[Any, Any]) -> None:
        """ Adds a connection to the simulation. """
        for my_component in self._components:
            component_name = my_component.component_name
            if hasattr(my_component, "source_weight"):
                if len(str(my_component.source_weight)) == 0:
                    pass
                else:
                    component_name = component_name[
                        : -len(str(my_component.source_weight))
                    ]
            if component_name == connection["Second Component"]:
                second_component = my_component
            elif component_name == connection["First Component"]:
                first_component = my_component

        if connection["Method"] == "Automatic":
            second_component.connect_similar_inputs(first_component)
        elif connection["Method"] == "Manual":
            try:
                second_component.connect_input(
                    input_fieldname=getattr(
                        second_component, connection["Second Component Input"]
                    ),
                    src_object_name=first_component.component_name,
                    src_field_name=getattr(
                        first_component, connection["First Component Output"]
                    ),
                )
            except Exception as my_exception:  # noqa: broad-except
                log.error(str(my_exception))
                log.debug("Incorrect Connection")

    # def add_configuration(self, my_sim: sim.Simulator) -> None:
    #    """ Adds a configuratation. """
    #    #my_sim.add_configuration(self.cfg_raw)
    #    #pass

    """
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
    """
