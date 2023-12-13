# """ Module to run a simulation from a json file instead of a python file. """
#
# import hisim.simulator as sim
#
# __authors__ = "Vitor Hugo Bellotto Zago"
# __copyright__ = "Copyright 2021, the House Infrastructure Project"
# __credits__ = ["Noah Pflugradt"]
# __license__ = "MIT"
# __version__ = "0.1"
# __maintainer__ = "Vitor Hugo Bellotto Zago"
# __email__ = "vitor.zago@rwth-aachen.de"
# __status__ = "development"
#
# # IMPORT ALL COMPONENT CLASSES DYNAMICALLY
# # DIRTY CODE. GIVE ME BETTER SUGGESTIONS
#
# # iterate through the modules in the current package
# from json_executor import JsonExecutor
#
#
#
# def basic_household_implicit(my_sim: sim.Simulator) -> None:
#     my_setup_function = JsonExecutor()
#     my_setup_function.build(my_sim)
# def component_importer(self) -> None:
#     """ Imports all the components once so that they can be found later. """
#     package_dir = os.path.join(PathlibPath(__file__).resolve().parent, "components")
#
#     for (_idx1, module_name, _idx2) in iter_modules([package_dir]):
#
#         # import the module and iterate through its attributes
#         module = import_module(f"hisim.components.{module_name}")
#         for attribute_name in dir(module):
#             attribute = getattr(module, attribute_name)
#
#             if isclass(attribute):
#                 # Add the class to this package's variables
#                 globals()[attribute_name] = attribute


# def find_all_component_class_children(self) -> Tuple[List[Any], List[str], List[str]]:
# """ Finds all children for a component class. """
#     classname = component.Component
#     component_file_children: List[str] = []
#     component_module: List[str] = []
#     component_class_children = [
#         cls.__module__ + "." + cls.__name__
#         for cls in classname.__subclasses__()
#         if cls != dynamic_component.DynamicComponent
#     ]
#     component_class_children_list: List[type[component.Component]] = [
#         cls
#         for cls in classname.__subclasses__()
#         if cls != dynamic_component.DynamicComponent
#     ]
#
#     for file_child in component_class_children_list:
#         component_module.append(file_child.__module__)
#         component_file_children.append(file_child.__name__)
#         component_file_children.append(file_child.__name__ + "Config")
#
#     # return component_class_children, component_file_children
#     return component_class_children, component_file_children, component_module


# class JsonExecutor:
#
#     """ Executes a Jsonfile as a simulation. """
#

#     def __init__(self) -> None:
#         """ Initializes the class. """
#         self.my_simulation_parameters: sim.SimulationParameters
#         file_path = str(HISIMPATH["cfg"])
#         if os.path.isfile(file_path):
#             with open(file_path, encoding="utf-8") as file:
#                 self.cfg = json.load(file)
#             # os.remove(""+HISIMPATH["cfg"])
#         else:
#             raise RuntimeError(f"File does not exist: {file_path}")
#         self.cfg_raw = copy.deepcopy(self.cfg)
#         self._components: List[Any] = []
#         self._connections: List[ComponentsConnection] = []
#         self._groupings: List[ComponentsGrouping] = []
#         # self.electricity_grids : List[ElectricityGrid] = []
#         # self.electricity_grid_consumption : List[ElectricityGrid] = []
#         self.component_class_children: List[str] = []
#         self.component_file_children: List[str] = []
#         self.component_module_list: List[str] = []
# #    def find_all_component_class_children(self) -> Tuple[List[Any], List[str], List[str]]:
#         """ Finds all children for a component class. """
#         classname = component.Component
#         component_file_children: List[str] = []
#         component_module: List[str] = []
#         component_class_children = [
#             cls.__module__ + "." + cls.__name__
#             for cls in classname.__subclasses__()
#             if cls != dynamic_component.DynamicComponent
#         ]
#         component_class_children_list: List[type[component.Component]] = [
#             cls
#             for cls in classname.__subclasses__()
#             if cls != dynamic_component.DynamicComponent
#         ]
#
#         for file_child in component_class_children_list:
#             component_module.append(file_child.__module__)
#             component_file_children.append(file_child.__name__)
#             component_file_children.append(file_child.__name__ + "Config")
#
#         # return component_class_children, component_file_children
#         return component_class_children, component_file_children, component_module


# def add_component(self, full_class_path: str, my_sim: Simulator, number_of_comp: str, electricity_output: Any = None) -> None:
#     """ Adds and initializes a component.
#
#     # Save parameters of class
#     # Retrieve class signature
#     path_to_components = dirname(__file__) + "/components"
#     list_of_all_components= glob.glob(join(path_to_components, "*.py"))
#     stripped_list_of_all_components=[]
#     seperater="HiSim"
#     for component_path in list_of_all_components:
#         stripped = component_path.split(seperater, 1)[1]
#         stripped = stripped.replace("/", ".")
#         stripped = stripped.replace(".py", "")
#         stripped_list_of_all_components.append(stripped)
#     """
#
#     clsmembers: List[Any] = []
#     full_instance_path = full_class_path + number_of_comp
#     clsmembers = self.get_class_members_for_components(clsmembers, full_class_path)
#
#     component_class_config_to_add, component_class_to_add, signature_component = self.initialize_component_classes(
#         clsmembers, full_class_path)
#
#     # Find if it has SimulationParameters and pass value
#     for parameter_name in signature_component.parameters:
#         if (
#             # double check in case the type annotation is missing
#             signature_component.parameters[parameter_name].annotation
#             == component.SimulationParameters
#             or parameter_name == "my_simulation_parameters"
#         ):
#             self.cfg["Components"][full_instance_path][
#                 parameter_name
#             ] = my_sim._simulation_parameters  # noqa: protected-access
#     try:
#
#         # self.cfg["Components"][comp].__delitem__("my_simulation_parameters")
#         config_class = component_class_config_to_add.from_dict(
#             self.cfg["Components"][full_instance_path]
#         )
#         self._components.append(
#             component_class_to_add(
#                 config=config_class,
#                 my_simulation_parameters=self.my_simulation_parameters,
#             )
#         )
#
#     except Exception as my_exception:  # noqa: broad-except
#         log.debug(
#             f"Adding Component {full_instance_path} resulted in a failure"
#         )
#         log.debug(f"Might be Missing :   {component_class_to_add} ")
#         log.debug("Please, investigate implementation mistakes in this Component.")
#         log.error(str(my_exception))
#         sys.exit(1)
#     # Add last listed component to Simulator object
#     my_sim.add_component(self._components[-1])
#     if electricity_output is not None:
#         pass
#         # ToDo: Implement electricity sum here.

#     def build(self, my_sim: Simulator) -> None:
#         """ Builds the parameters. """
#         self.add_simulation_parameters(my_sim)
#         (
#             self.component_class_children,
#             self.component_file_children,
#             self.component_module_list,
#         ) = self.find_all_component_class_children()
#         for comp in self.cfg["Components"]:
#             number_of_comp = ""
#             if "_number" in comp:
#                 # quick and dirty solution. checks if maximum of 10 components of the same are added
#                 for number in range(1, 9):
#                     if "_number" + str(number) in comp:
#                         comp = comp.replace("_number" + str(number), "")
#                         number_of_comp = "_number" + str(number)
#
#             if comp in self.component_class_children:
#                 self.add_component(comp, my_sim, number_of_comp)
#         # for _grouping_key, grouping_value in self.cfg["Groupings"].items():
#         #    self.add_grouping(grouping_value)
#
#         for _connection_key, connection_value in self.cfg["Connections"].items():
#             self.add_connection(connection_value)
#         # self.add_configuration(my_sim)
#
#
#
#     def initialize_component_classes(self, clsmembers, full_class_path):
#         """ Initializes the component classes that were identified earlier. """
#         component_class_to_add = None
#         for _member_type, component_class in clsmembers:
#             if self.get_path(component_class) == full_class_path:
#                 try:
#                     component_class_to_add = component_class
#                     signature_component = inspect.signature(component_class)
#                 except Exception as my_exception:
#                     log.error(f"No relevant_component added. Investigate in Component: {full_class_path} ")
#                     log.error(str(my_exception))
#                     raise RuntimeError(
#                         f"Could not find the class for the component {full_class_path}") from my_exception
#             elif self.get_path(component_class) == full_class_path + "Config":
#                 try:
#                     component_class_config_to_add = component_class
#                 except Exception as my_exception:
#                     log.error(f"No relevant_component_config added. Investigate in Component: {full_class_path} ")
#                     log.error(str(my_exception))
#                     raise RuntimeError(
#                         f"Could not find the config class for the component {full_class_path}") from my_exception
#         if component_class_to_add is None:
#             raise RuntimeError(
#                 f"Could not find the class for the component {full_class_path}"
#             )
#         if component_class_config_to_add is None:
#             raise RuntimeError(
#                 f"Could not find the config class for the component {full_class_path}"
#             )
#         return component_class_config_to_add, component_class_to_add, signature_component
#
#     def get_class_members_for_components(self, clsmembers, full_class_path):
#         """ Gets the class members for components. """
#         for component_to_check in self.component_class_children:
#             try:
#                 if component_to_check == full_class_path:
#                     # removes the last part (after the last dot) of the component string (the class name)
#                     seperater = "."
#                     stripped = ""
#                     splitted_string = component_to_check.split(
#                         seperater, component_to_check.count(".")
#                     )
#                     for i in range(component_to_check.count(".")):
#                         if stripped == "":
#                             stripped = splitted_string[i]
#                         else:
#                             stripped = stripped + "." + splitted_string[i]
#                     clsmembers = [
#                         (name, cls)
#                         for name, cls in inspect.getmembers(
#                             sys.modules[stripped], inspect.isclass
#                         )
#                         if cls.__module__ == stripped
#                     ]
#             except Exception as my_exception:  # noqa: broad-except
#                 # just continue
#                 log.trace(str(my_exception))
#         if clsmembers is None:
#             raise RuntimeError("No class members were found")
#         return clsmembers
#
#     # def add_grouping(self, grouping: Dict[Any, Any]) -> None:
#         # """ Adds a component grouping. """
#         # for my_component in self._components:
#         #     if type(my_component).__name__ == grouping["Second Component"]:
#         #         second_component = my_component
#         #     elif type(my_component).__name__ == grouping["First Component"]:
#         #         first_component = my_component
#         # """
#         # my_concatenated_component = CalculateOperation(name=grouping["Component Name"])
#         # my_concatenated_component.connect_input(src_object_name=first_component.ComponentName,
#         #                                         src_field_name=getattr(first_component, grouping["First Component Output"]))
#         # my_concatenated_component.add_operation(operation=grouping["Operation"])
#         # my_concatenated_component.connect_input(src_object_name=second_component.ComponentName,
#         #                                         src_field_name=getattr(second_component, grouping["Second Component Output"]))
#         # self._components.append(my_concatenated_component)
#         # my_sim.add_component(my_concatenated_component)
#         #
#         # """
#
#     def add_connection(self, connection: Dict[Any, Any]) -> None:
#         """ Adds a connection to the simulation. """
#         for my_component in self._components:
#             component_name = my_component.component_name
#             if hasattr(my_component, "source_weight"):
#                 if len(str(my_component.source_weight)) == 0:
#                     pass
#                 else:
#                     component_name = component_name[
#                         : -len(str(my_component.source_weight))
#                     ]
#             if component_name == connection["Second Component"]:
#                 second_component = my_component
#             elif component_name == connection["First Component"]:
#                 first_component = my_component
#
#         if connection["Method"] == "Automatic":
#             second_component.connect_similar_inputs(first_component)
#         elif connection["Method"] == "Manual":
#             try:
#                 second_component.connect_input(
#                     input_fieldname=getattr(
#                         second_component, connection["Second Component Input"]
#                     ),
#                     src_object_name=first_component.component_name,
#                     src_field_name=getattr(
#                         first_component, connection["First Component Output"]
#                     ),
#                 )
#             except Exception as my_exception:  # noqa: broad-except
#                 log.error(str(my_exception))
#                 log.debug("Incorrect Connection")
#
#     # def add_configuration(self, my_sim: sim.Simulator) -> None:
#     #    """ Adds a configuratation. """
#     #    #my_sim.add_configuration(self.cfg_raw)
#     #    #pass
#
#     """
#     def add_to_electricity_grid(self, my_sim, next_component, electricity_grid_label=None):
#         n_consumption_components = len(self.electricity_grids)
#         if electricity_grid_label is None:
#             electricity_grid_label = "Load{}".format(n_consumption_components)
#         if n_consumption_components == 0:
#             list_components = [next_component]
#         else:
#             list_components = [self.electricity_grids[-1], "Sum", next_component]
#         self.electricity_grids.append(ElectricityGrid(name=electricity_grid_label, grid=list_components))
#         #self.electricity_grids.append(self.electricity_grids[-1]+next_component)
#         my_sim.add_component(self.electricity_grids[-1])
#         #if hasattr(next_component, "type"):
#         #    if next_component.type == "Consumer":
#         #        self.add_to_electricity_grid_consumption(my_sim, next_component)
#
#     def add_to_electricity_grid_consumption(self, my_sim, next_component, electricity_grid_label = None):
#         n_consumption_components = len(self.electricity_grid_consumption)
#         if electricity_grid_label is None:
#             electricity_grid_label = "Consumption{}".format(n_consumption_components)
#         if n_consumption_components == 0:
#             list_components = [next_component]
#         else:
#             list_components = [self.electricity_grid_consumption[-1], "Sum", next_component]
#         self.electricity_grid_consumption.append(ElectricityGrid(name=electricity_grid_label, grid=list_components))
#         my_sim.add_component(self.electricity_grid_consumption[-1])
#     """


#
#
# class ComponentsConnection:
#
#     """ Represents a component connection for the json file. """
#
#     def __init__(
#         self,
#         first_component: str,
#         second_component: str,
#         method: str = None,
#         first_component_output: Optional[str] = None,
#         second_component_input: Optional[str] = None,
#     ) -> None:
#         """ Initializes configuration for a component connection. """
#         self.first_component = first_component
#         self.second_component = second_component
#         self.first_component_output = first_component_output
#         self.second_component_input = second_component_input
#         self.configuration: Dict[Any, Any]
#         if method == "Automatic" or method is None:
#             self.method = "Automatic"
#             self.run_automatic()
#         elif method == "Manual":
#             self.method = method
#             self.run_manual()
#
#     def run_automatic(self) -> None:
#         """ Run automatic config. """
#         self.configuration = {
#             "First Component": self.first_component,
#             "Second Component": self.second_component,
#             "Method": self.method,
#         }
#
#     def run_manual(self) -> None:
#         """ Run manual config. """
#         self.configuration = {
#             "First Component": self.first_component,
#             "Second Component": self.second_component,
#             "Method": self.method,
#             "First Component Output": self.first_component_output,  # noqa
#             "Second Component Input": self.second_component_input,  # noqa
#         }
#
#
# class ComponentsGrouping:  # noqa: too-few-public-methods
#
#     """ For making a components grouping configuration. """
#
#     def __init__(
#         self,
#         component_name: str,
#         operation: str,
#         first_component: str,
#         second_component: str,
#         first_component_output: str,
#         second_component_output: str,
#     ) -> None:
#         """ Initializes a configuration. """
#         self.component_name = component_name
#         self.operation = operation
#         self.first_component = first_component
#         self.second_component = second_component
#         self.first_component_output = first_component_output
#         self.second_component_output = second_component_output
#         self.configuration = {
#             "Component Name": self.component_name,
#             "Operation": self.operation,
#             "First Component": self.first_component,
#             "Second Component": self.second_component,
#             "First Component Output": self.first_component_output,
#             "Second Component Output": self.second_component_output,
#         }
#
#
# class ConfigurationGenerator:
#
#     """ Generates a json configuration from a Python file.
#
#     todo Max:
#     - 1. issue: SimulationParameters are hard coded in ConfigurationGenerator
#         - implement function for adding SimulationParameter in CFG-Automator
#           like a Component in basic_household
#
#
#     - 2. issue: String-Call in basic_household for Simulation Setup:
#                 Name call of classes in basic_household has to be exactly the same than classname
#                 of component
#         - import all components in basic_household and call class by real name
#         - problem: how to handle generic components
#
#         - factory functions -> desgin pattern
#
#     - 3. issue: saved cfg-json is always named "cfg.json"
#         - specify name of cfg with hashlib, so that multiple cfgs can be started at the same time
#         - with saved hash can be checked, if a Simulation with same parameters has been done before
#
#     Create a json file containing the configuration of the setup
#     function to be run in HiSim. The configuration is done into
#     4 parts:
#         1) Setting the simulation parameters (add_simulation_parameters).
#         2) Adding each Component (add_component). If the user does not define the arguments
#         of the Component object, the default values are taken instead.
#         3) Adding groupings (add_groupings). After adding the components, the user might need other components
#         that are derived by a combination of single components. Groupings are components made of a combination
#         of outputs of previously created components.
#         4) Adding connections (add_connections). Connections between outputs and inputs of different components
#         indicate to the simulator the flow of energy, mass or information.
#
#     After all the configuration information has been provided, the user can:
#         - Run a single simulation on the configuration file with the execution of 'dump' and 'run'.
#         - Perform a parameter study, proving a range of a parameter used in the listed configuration. In this case,
#         the 'dump' command does not have to be executed. The parameter study is performed with 'run_parameter_study',
#         provided a dictionary with the range of the parameters.
#     """
#
#     def __init__(self) -> None:
#         """ Initializes a single simulation. """
#         self.load_component_modules()
#         self._simulation_parameters: Dict[str, Any] = {}
#         self._components: Dict[Any, Any] = {}
#         self._groupings: Dict[Any, Any] = {}
#         self._connections: Dict[Any, Any] = {}
#         self._parameters_range_studies: Dict[Any, Any] = {}
#         self.data: Dict[Any, Any]
#
#     def set_name(self, name_to_set: Any) -> Any:
#         """ Sets the name of a module. """
#         return name_to_set.__module__ + "." + name_to_set.__name__
#
#     def load_component_modules(self) -> None:
#         """ Load dynamically all classes implemented under the 'components' directory.
#
#         With that said, the user does not have to import a recently implemented Component class in the cfg_automator module.
#         """
#         self.preloaded_components = {}
#
#         def get_default_parameters_from_constructor(class_component: Any) -> Dict[Any, Any]:
#             """ Get the default arguments of either a function or a class. """
#             constructor_function_var = [
#                 item
#                 for item in inspect.getmembers(class_component)
#                 if item[0] in "__init__"
#             ][0][1]
#             sig = inspect.signature(constructor_function_var)
#             return {
#                 k: v.default
#                 for k, v in sig.parameters.items()
#                 if v.default is not inspect.Parameter.empty
#             }
#
#         classname = component.Component
#         component_class_children = [
#             cls
#             for cls in classname.__subclasses__()
#             if cls != dynamic_component.DynamicComponent
#         ]
#         # component_class_children = [cls.__name__ for cls in classname.__subclasses__() if cls != component.DynamicComponent]
#
#         for component_class in component_class_children:
#             default_args = get_default_parameters_from_constructor(component_class)
#
#             # Remove the simulation parameters of the list
#             if "sim_params" in default_args:
#                 del default_args["sim_params"]
#
#             # Save every component in the dictionary attribute
#             self.preloaded_components[component_class] = default_args
#
#     def add_simulation_parameters(self, my_simulation_parameters: Union[None, Dict[str, Any]] = None) -> None:
#         """ Add the simulation parameters to the configuration JSON file list. """
#         if my_simulation_parameters is None:
#             log.debug("no simulation Parameters are added")
#         else:
#             self._simulation_parameters = my_simulation_parameters
#
#     def add_component(self, user_components_name: Any) -> None:
#         """ Add the component to the configuration JSON file list.
#
#         It can read three types of arguments:
#
#         String: the string should contain the name of a Component class implemented in the 'components' directory.
#         In this case, the component object will be implemented in the setup function with the default values.
#
#         List: the list of strings should contain the names of Component classes implemented in the 'components' director-y.
#         In this case, the component objects will be implemented in the setup function with the default values.
#
#         Dictionary: the dictionary containing at the first level the name of Component classes, and in the two level,
#         the arguments. In this case, if any argument is not explicitly provided by the user, the default values are used
#         instead.
#         """
#         if isinstance(user_components_name, list):
#             for user_component_name in user_components_name:
#                 self._components[user_component_name] = self.preloaded_components[
#                     user_component_name
#                 ]
#             return
#         if isinstance(user_components_name, dict):
#             for user_component_name, parameters in user_components_name.items():
#                 if parameters.__class__ == dict:
#                     self._components[user_component_name] = parameters
#                     continue
#
#                 if str(user_component_name) in self._components:
#                     # quick annd dirty solution. checks if maximum of 10 components of the same are added
#                     for number in range(1, 9):
#                         if (
#                             str(user_component_name) + "_number" + str(number)
#                             in self._components
#                         ):
#                             continue
#
#                         self._components[
#                             str(user_component_name) + "_number" + str(number)
#                         ] = parameters.__dict__
#                         break
#                 else:
#                     self._components[str(user_component_name)] = parameters.__dict__
#                     # self._components[user_component_name.__module__ +"."+ user_component_name.__name__] = parameters.__dict__
#             return
#
#         self._components[
#             user_components_name.__module__
#         ] = user_components_name.__doc__
#         # self._components[user_components_name] = self.preloaded_components[user_components_name]
#
#     def add_grouping(self, grouping_components: ComponentsGrouping) -> None:
#         """ Add component grouping created out of the combination of previously created components.
#
#         The Grouping component yields either a sum, subtraction or another operation combining multiple outputs of the previously
#         assigned components. Let a grouping component be the subtraction of a load profile in CSVLoader from PVSystem:
#
#         (First Component)               (Second Component)                      (Operation)
#         Name: CSVLoader                 Name: PVSystem                          Subtraction
#         ComponentOutput: Output1        ComponentOutput: ElectricityOutput
#
#         The grouping is set as follows:
#
#         my_grouping = ComponentsGrouping(component_name="Sum_PVSystem_CSVLoader",
#                                          operation="Subtract",
#                                          first_component="CSVLoader",
#                                          second_component="PVSystem",
#                                          first_component_output="Output1",
#                                          second_component_output="ElectricityOutput")
#         ----------
#         grouping_components: ComponentsGrouping
#         """
#         self._groupings[
#             grouping_components.component_name
#         ] = grouping_components.configuration
#
#     def add_connection(self, connection_components: Any) -> None:
#         """ Add connections among the previously assigned components. Connections can be performed manually or automatically. """
#         number_of_connections = len(self._connections)
#         i_connection = number_of_connections + 1
#         connection_name = f"Connection{i_connection}_{connection_components.first_component}_{connection_components.second_component}"
#         self._connections[connection_name] = connection_components.configuration
#
#     def add_paramater_range(self, parameter_range: Any) -> None:
#         """ Adds a parameter range for a parameter study. """
#         self._parameters_range_studies.update(parameter_range)
#
#     def reset(self) -> None:
#         """ Resets the entire thing. """
#         self._simulation_parameters = {}
#         self._components = {}
#         self._groupings = {}
#         self._connections = {}
#         self._parameters_range_studies = {}
#
#     def print_components(self) -> None:
#         """ Prints all components. """
#         log.trace(json.dumps(self._components, sort_keys=True, indent=4))
#
#     def print_component(self, name: str) -> None:
#         """ Prints a single component. """
#         log.trace(json.dumps(self._components[name], sort_keys=True, indent=4))
#
#     def dump(self) -> None:
#         """ Dumps the entire config file as json. """
#         self.data = {
#             "SimulationParameters": self._simulation_parameters,
#             "Components": self._components,
#             "Groupings": self._groupings,
#             "Connections": self._connections,
#         }
#         with open("" + HISIMPATH["cfg"], "w", encoding="utf-8") as filestream:
#             json.dump(self.data, filestream, indent=4)
#
#     def run_parameter_studies(self) -> None:
#         """ Run a single parameter study. """
#         for (
#             component_class,
#             parameter_name_and_range,
#         ) in self._parameters_range_studies.items():
#             parameters_range_studies_entry = copy.deepcopy(
#                 self._parameters_range_studies
#             )
#             if isinstance(parameter_name_and_range, dict):
#                 for parameter_name, _range in parameter_name_and_range.items():
#                     cached_range = _range
#                     for value in cached_range:
#                         parameters_range_studies_entry[component_class][parameter_name] = value
#                         self.add_component(parameters_range_studies_entry)
#                         self.dump()
#                         # self.run()
