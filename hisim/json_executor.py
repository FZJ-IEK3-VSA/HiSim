""" Executes a json file in the simulator. """
# clean
import inspect
import os
from typing import List, Any, Dict
from inspect import isclass
from pkgutil import iter_modules
from pathlib import Path as PathlibPath
from importlib import import_module
from dataclasses import dataclass

from hisim.simulator import Simulator
from hisim import log
from hisim.json_generator import ConfigFile, ComponentEntry
from hisim.simulationparameters import SimulationParameters
from hisim import component as cp


@dataclass()
class ClassEntry:

    """Entry for a single class."""

    file_name: str
    module_name: str
    class_name: str
    module: Any


class JsonExecutor:

    """Class to read a json file created by the json generator and executre it."""

    def __init__(self, filename):
        """Initializes the JsonExecutor."""
        with open(filename, "r", encoding="utf-8") as filestream:
            json_str = filestream.read()
            self.my_obj: ConfigFile = ConfigFile.from_json(json_str)
            if self.my_obj.my_simulation_parameters is None:
                raise ValueError("No simulation parameters object was found.")
            self.my_simulation_parameters: SimulationParameters = (
                self.my_obj.my_simulation_parameters
            )

    def component_importer(self, needed_classes: List[str]) -> Dict[str, Any]:
        """Imports a config."""
        package_dir = os.path.join(PathlibPath(__file__).resolve().parent, "components")
        class_dict = {}
        for (_idx1, module_name, _idx2) in iter_modules([package_dir]):

            # import the module and iterate through its attributes
            module = import_module(f"hisim.components.{module_name}")

            for attribute_name in dir(module):
                attribute = getattr(module, attribute_name)
                if not isclass(attribute):
                    continue
                if attribute.__module__ != module.__name__:
                    # skipping because it's an import
                    continue

                # only import things that are actually needed
                full_class_name = attribute.__module__ + "." + attribute.__name__
                if full_class_name in needed_classes:
                    # Add the class to this package's variables
                    globals()[attribute_name] = attribute
                    class_dict[full_class_name] = attribute
        return class_dict

    def execute_all(self) -> None:
        """Executes the json and starts the simulation."""
        needed_classes = self.generate_list_of_needed_classes_for_import()
        class_dict = self.component_importer(needed_classes)

        my_simulation_parameters: SimulationParameters = self.my_simulation_parameters

        simulator: Simulator = Simulator(
            module_directory="json",
            setup_function="json_func",
            my_simulation_parameters=my_simulation_parameters,
            module_filename="json.py",
        )
        component_dict = {}
        for component_entry in self.my_obj.component_entries:
            component_instance = self.process_one_component_entry(
                component_entry, class_dict, my_simulation_parameters
            )
            simulator.add_component(component_instance)
            component_dict[component_entry.component_name] = component_instance
        for component_entry in self.my_obj.component_entries:
            if len(component_entry.default_connections) == 0:
                continue
            for component_name in component_entry.default_connections:
                dst_class: cp.Component = component_dict[component_entry.component_name]
                src_class = component_dict[component_name]
                dst_class.connect_only_predefined_connections(src_class)
        simulator.run_all_timesteps()

    def process_one_component_entry(
        self,
        component_entry: ComponentEntry,
        class_dict: Any,
        my_simulation_parameters: SimulationParameters,
    ) -> Any:
        """Processes a single component entry in the json."""
        my_class = class_dict[component_entry.component_full_classname]
        signature_component = inspect.signature(my_class)
        found_simulation_parameters = False
        found_config = False
        for parameter_name in signature_component.parameters:
            if parameter_name == "my_simulation_parameters":
                found_simulation_parameters = True
            elif parameter_name == "config":
                found_config = True
            else:
                log.warning(
                    "Found unclear parameter: "
                    + parameter_name
                    + " in component "
                    + component_entry.component_full_classname
                )
        if not found_simulation_parameters:
            raise ValueError(
                "The class "
                + component_entry.component_full_classname
                + " has no simulation parameters parameter"
            )
        if not found_config:
            raise ValueError(
                "The class "
                + component_entry.component_full_classname
                + " has no config parameter"
            )
        config_class = class_dict[component_entry.config_full_classname]

        config_instance = config_class.from_dict(component_entry.configuration)

        class_instance = my_class(
            config=config_instance, my_simulation_parameters=my_simulation_parameters
        )
        return class_instance

    def generate_list_of_needed_classes_for_import(self):
        """Generates a list of classes that need to be important from the object list."""
        needed_classes: List[str] = []
        for component_entry in self.my_obj.component_entries:
            needed_key = component_entry.component_full_classname
            needed_classes.append(needed_key)
            needed_config_key = component_entry.config_full_classname
            needed_classes.append(needed_config_key)
        return needed_classes
