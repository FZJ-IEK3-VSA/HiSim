""" Generates a Json file for the json executor. """

from typing import Any, Optional, List, Type, Dict
from dataclasses import dataclass, field
from dataclass_wizard import JSONWizard

from hisim import utils
from hisim import log
from hisim.component import ConfigBase
from hisim.simulationparameters import SimulationParameters


@dataclass
class ConnectionEntry(JSONWizard):

    """ Represents a connection between two components. """

    src_component_name: str
    src_output_name: str
    dst_input_name: str


@dataclass
class ComponentEntry(JSONWizard):

    """ Entry for a single component. """

    component_full_classname: str
    component_name: str
    configuration: Dict[Any, Any]
    config_full_classname: str
    # config_class_module: str
    default_connections: List[str]
    manual_connections: List[ConnectionEntry]


@dataclass
class ConfigFile(JSONWizard):

    """ Contains all the data that goes into the json config file. """

    system_name: str
    component_entries: List[ComponentEntry] = field(default_factory=list)
    my_simulation_parameters: Optional[SimulationParameters] = None


class JsonConfigurationGenerator:

    """ Class to generate Json config files that can be used to start calculations without Python. """

    def __init__(self, name: str) -> None:
        """ Initializes the configuration generator. """
        self.config_file: ConfigFile = ConfigFile(system_name=name)

    def set_simulation_parameters(self, my_simulation_parameter: SimulationParameters) -> None:
        """ Sets the simulation parameters. """
        self.config_file.my_simulation_parameters = my_simulation_parameter

    def add_component(self, config: Type[ConfigBase]) -> ComponentEntry:
        """ Adds a component and returns a component entry. """
        # component_type_str = config.get_configured_classname() #.__name__
        config_json_str = config.to_dict()
        # config_class = config.__class__.__qualname__
        # config_module = type(config).__module__
        component_entry = ComponentEntry(component_full_classname=config.get_main_classname(),
                                         component_name=config.name,
                                         configuration=config_json_str,
                                         config_full_classname=config.get_config_classname(),
                                         default_connections=[],
                                         manual_connections=[])
        self.config_file.component_entries.append(component_entry)
        log.information("Added component " + config.name)
        return component_entry

    def add_default_connection(self, from_entry: ComponentEntry, to_entry: ComponentEntry) -> None:
        """ Adds a default connection to another component. """
        to_entry.default_connections.append(from_entry.component_name)

    def add_manual_connection(self, from_entry: ComponentEntry, output_name: str, to_entry: ComponentEntry, input_name: str) -> None:
        """ Adds a manual connection entry for connecting a single input. """
        my_connection = ConnectionEntry(from_entry.component_name, output_name, input_name)
        to_entry.manual_connections.append(my_connection)

    def save_to_json(self, filename: str) -> None:
        """ Saves a configuration to a json file. """
        with open(filename, "w", encoding="utf-8") as filestream:
            mystr = self.config_file.to_json(indent=4) # , cls=utils.MyJsonEncoder
            filestream.write(mystr)
