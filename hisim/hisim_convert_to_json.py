""" HiSim converter from legacy python-based system setups to JSON-based configurations. """
# clean
import json
import re
import warnings
import importlib
from pathlib import Path
import sys
from datetime import datetime
from typing import List, Any, Optional, Dict, Tuple, cast, overload
# Third party imports
from pydantic import BaseModel, Field
import humps
from dotenv import load_dotenv
# First party imports
from hisim.components.controller_l2_district_energy_management_system import L2GenericDistrictEnergyManagementSystem
from hisim.components.controller_l2_energy_management_system import L2GenericEnergyManagementSystem
from hisim.components.loadprofilegenerator_utsp_connector import UtspLpgConnector
import hisim.simulator as sim
from hisim import log
from hisim.simulationparameters import SimulationParameters
import hisim.component as cp
import hisim.dynamic_component as dcp
from hisim.component import ConfigBase
from hisim.dynamic_component import DynamicComponent
from hisim.components.generic_car import GenericCarInformation

load_dotenv()


# Adapted from json_generator.py
class Component(BaseModel):
    """Represents a component in the scenario JSON."""

    component_full_classname: str
    configuration: Dict[Any, Any]
    config_full_classname: str
    inputs: list[Any]
    outputs: list[Any]
    connect_automatically: bool = True  # If all default connections are present


class Endpoint(BaseModel):
    """Represents an endpoint of a connection."""

    component_name: str
    field_name: str


class Connection(BaseModel):
    """Represents a connection between components in the scenario JSON."""

    source: Endpoint
    target: Endpoint


class Scenario(BaseModel):
    """Represents the scenario JSON."""

    name: str
    description: str
    multiple_buildings: bool = False
    components: list[Component] = Field(default_factory=list)
    connections: dict[str, Any] | list[Any] | None = None


def get_filtered_simulation_parameters(my_sim: sim.Simulator):
    """Gives the simulation parameters as a snake_case dict, with some fields excluded that are not strictly simulation parameters."""

    def export_filtered(obj, exclude: set[str]) -> dict:
        data = humps.decamelize(obj.to_dict())
        return {k: v for k, v in data.items() if k not in exclude}
    filtered = export_filtered(
        my_sim.get_simulation_parameters(),
        # Automatic camelCase conversion by dataclass-wizard
        {"multipleBuildings", "resultDirectory", "cacheDirPath", "surplusControl", "multiple_buildings", "result_directory", "cache_dir_path", "surplus_control"},
    )
    # Also export the post_processing_options as strings, instead of integers
    filtered["post_processing_options"] = [ppo.name for ppo in filtered["post_processing_options"]]
    return filtered


def write_standalone_simulation_json(my_sim: sim.Simulator, path="recent_simulation_parameters.json") -> None:
    """Write the simulation parameters of the given simulator to a JSON file."""

    if path.lower()[-5:] != ".json":
        path = path + ".json"
    log.information(f"Writing simulation parameters to JSON file {path}")

    with open(path, "w", encoding="utf-8") as f:
        # Interesting when later executing these JSONs:
        # - multiple_buildings is actually (only) part of the simulation parameters
        # - surplus_control is part of the household configuration, i.e., the dataclass that determines
        #   the exact behavior of the setup function (which household to create exactly) --> not needed anymore
        #   - the simulation parameter allows switching the car's surplus control
        filtered = get_filtered_simulation_parameters(my_sim)
        json.dump(filtered, f, indent=4)
        f.flush()
        f.close()


# Adapted from json_generator.py
def convert_component_to_json(config: ConfigBase, component: cp.Component) -> Tuple[Component, list[Any], list[Any]]:
    """Converts a component to a JSON-compatible dictionary."""
    config_json_str = config.to_dict()
    config_json_str = humps.decamelize(config_json_str)
    # Some components have to be adapted "manually", e.g., replace absolute paths
    if config.get_main_classname() == "hisim.components.loadprofilegenerator_utsp_connector.UtspLpgConnector":
        config_json_str["result_dir_path"] = "<<utils.HISIMPATH['utsp_results']>>"
    elif config.get_main_classname() == "hisim.components.weather.Weather":
        dir_parts = Path(config_json_str["source_path"]).parts
        if "inputs" in dir_parts:
            idx = dir_parts.index("inputs")
            config_json_str["source_path"] = '<<utils.get_input_directory()>>\\' + str(Path(*dir_parts[idx + 1:]))
        else:
            log.warning(f"Could not find 'inputs' in absolute weather source path {config_json_str['source_path']}, leaving it unchanged in JSON output...")
    # Car information can be generated using Occupancy (see class GenericCarInformation)
    # However, then each car must be assigned to an occupancy
    elif config.get_main_classname() == "hisim.components.generic_car.Car":
        config_json_str["household_name"] = component.car_information_dict["household_name"]

    outs = []
    ins = []
    for out in component.outputs:
        if isinstance(component, DynamicComponent):
            matches = [
                dyn_out
                for dyn_out in component.my_component_outputs
                if dyn_out.source_output_field_name == out.field_name
            ]
            if len(matches) > 1:
                raise ValueError(
                    f"Multiple dynamic outputs found for field '{out.field_name}' in component '{component.component_name}'"
                )
            if len(matches) == 1:
                # Handle special case for EMS
                if isinstance(component, L2GenericEnergyManagementSystem):
                    if out.output_description in ["Target electricity for Occupancy. ",
                                                  "Target electricity for Heating Heat Pump. ",
                                                  "Target electricity for dhw heat pump.",
                                                  "Target electricity for electric heater space heating.",
                                                  "Target electricity for electric heater domestic hot water.",
                                                  "Target electricity for electric heater space heating.",
                                                  "Target electricity for solar thermal domestic hot water."]:
                        continue

                if isinstance(component, L2GenericDistrictEnergyManagementSystem):
                    if out.output_description in ["Target electricity for Occupancy. ",
                                                  "Target electricity for Heating Heat Pump. ",
                                                  "Target electricity for dhw heat pump."]:
                        continue

                # add_component_output has been used
                dyn_out = matches[0]
                # Extract source_output_name from the field_name
                match = re.match(r"^(.*?)(Output\d+)$", out.field_name)
                if not match:
                    raise ValueError(f"Invalid field_name format: {out.field_name}")
                son = match.group(1)
                outs.append({
                    "dynamic": True,
                    "source_output_name": son,
                    "source_tags": dyn_out.source_tags,
                    "source_load_type": dyn_out.source_load_type.value,
                    "source_unit": dyn_out.source_unit.value,
                    "source_weight": dyn_out.source_weight,
                    "output_description": out.output_description,
                    "source_component_class": dyn_out.source_component_class if dyn_out.source_component_class is not None else None,
                })
                continue

        # add_output has been used
        # => Does not have to be exported, since it is created automatically upon creation of the component

    for inp in component.inputs:
        if isinstance(component, DynamicComponent):
            matches = [
                dyn_inp
                for dyn_inp in component.my_component_inputs
                if dyn_inp.source_component_class == inp.field_name
            ]
            if len(matches) > 1:
                raise ValueError(
                    f"Multiple dynamic inputs found for field '{inp.field_name}' in component '{component.component_name}'"
                )
            if len(matches) == 1:
                # add_component_input(s)_and_connect has been used
                dyn_inp = matches[0]
                sco = dyn_inp.source_component_field_name

                # Extract source_object_name
                pattern = rf"^Input_(.*?)_{re.escape(sco)}_\d+$"
                match = re.match(pattern, inp.field_name)
                if not match:
                    raise ValueError(f"Label does not match expected format: {inp.field_name}")
                son = match.group(1)

                ins.append({
                    "dynamic": True,
                    "source_component_output": sco,
                    "source_object_name": son,
                    "source_load_type": inp.loadtype.value,
                    "source_unit": inp.unit.value,
                    "source_tags": dyn_inp.source_tags,
                    "source_weight": dyn_inp.source_weight,
                })
                continue

        # add_input has been used
        # => Does not have to be exported, since it is created automatically upon creation of the component

    component_entry = Component(
        component_full_classname=config.get_main_classname(),
        configuration=config_json_str,
        config_full_classname=config.get_config_classname(),
        inputs=ins,
        outputs=outs,
        connect_automatically=True,  # Update later based on connections
    )
    return component_entry, ins, outs


def add_component_to_scenario(scenario: Scenario, config: ConfigBase, component: cp.Component, my_sim: sim.Simulator) -> None:
    """Add a simulator component to the scenario JSON object."""

    component_entry, ins, outs = convert_component_to_json(config, component)
    if config.get_main_classname() == "hisim.components.generic_car.Car":
        # Handle special case for Car component, link to LPG connector
        for idx, comp in enumerate(scenario.components):
            if comp.component_full_classname == "hisim.components.loadprofilegenerator_utsp_connector.UtspLpgConnector":
                car_info = component.car_information_dict
                my_comp = cast(UtspLpgConnector, my_sim.wrapped_components[idx].my_component)  # For mypy, we know that this is an UtspLpgConnector
                new_gci = GenericCarInformation(my_occupancy_instance=my_comp).data_dict_for_car_component[car_info["household_name"]]

                if new_gci["time_resolution"] == car_info["time_resolution"] and \
                new_gci["car_location"] == car_info["car_location"] and new_gci["driven_meters"] == car_info["driven_meters"]:
                    # Cannot include car inside LPG connector, then not found for connections/inputs/outputs etc.
                    comp.configuration["cars"] = (comp.configuration.get("cars") or []) + [component_entry.configuration["name"]]
                    log.information(f"Added car information to LPG connector config for car {component.component_name}")

    # Always do:
    scenario.components.append(component_entry)
    log.information("Added component " + config.name + " with " + str(len(ins)) + " inputs and " + str(len(outs)) + " outputs")


def get_unique_connections(all_connections: List[Connection]) -> Tuple[List[Connection], set]:
    """Return only the unique connections from the list of all connections, and a set of seen generated keys for these connections."""

    unique_connections = []
    seen = set()
    for conn in all_connections:
        # Hashable key
        key = (
            conn.source.component_name,
            conn.source.field_name,
            conn.target.component_name,
            conn.target.field_name,
        )

        if key not in seen:
            seen.add(key)
            unique_connections.append(conn)

    return unique_connections, seen


# These 3 functions are needed separately due to mypy type-checking
@overload
def get_default_connection_dict(
    target_component: dcp.DynamicComponent,
) -> dict[str, list[dcp.DynamicComponentConnection]]:
    ...


@overload
def get_default_connection_dict(
    target_component: cp.Component,
) -> dict[str, list[cp.ComponentConnection]]:
    ...


def get_default_connection_dict(target_component):
    """Get the default connection dict of the target component."""

    if isinstance(target_component, dcp.DynamicComponent):
        return target_component.dynamic_default_connections
    if isinstance(target_component, cp.Component):
        return target_component.default_connections
    raise TypeError(f"Type {type(target_component)} of target_component should be Component or DynamicComponent.")


def compare_automatic_connections(target_default_connection_dict, source_component_list, target_component, seen_keys) -> bool:
    """Check whether the target component was/can be automatically connected, i.e., whether it features all default connections."""

    if (
        any(
            source_component.get_classname() in target_default_connection_dict
            for source_component in source_component_list
        )
        is False
    ):
        # connect_automatically does not connect anything
        log.information(f"Component {target_component.get_classname()} was not automatically connected: no source components found in default connections")
        return False

    # go through all registered components
    for source_component in source_component_list:
        source_component_classname = source_component.get_classname()

        # if the source components' classname is found in the target components' default connection dict, a connection is made
        if source_component_classname in target_default_connection_dict.keys():
            if isinstance(target_component, dcp.DynamicComponent):
                dynamic_connections = target_component.get_dynamic_default_connections(
                    source_component=source_component
                )

                # Check whether connection is present, if not, set connect_automatically to False and break inner loop
                for dynamic_connection in dynamic_connections:
                    # Hashable key
                    key = (
                        source_component.component_name,
                        dynamic_connection.source_component_field_name,
                        target_component.component_name,
                        dynamic_connection.source_component_field_name,
                    )
                    if key not in seen_keys:
                        log.information(f"DComponent {target_component.get_classname()} was not automatically connected: missing {dynamic_connection}")
                        return False

            if isinstance(target_component, cp.Component) and not isinstance(
                target_component, dcp.DynamicComponent
            ):
                connections = target_component.get_default_connections(source_component=source_component)
                for connection in connections:
                    # Hashable key
                    key = (
                        source_component.component_name,
                        connection.source_output_name,
                        target_component.component_name,
                        connection.target_input_name,
                    )
                    if key not in seen_keys:
                        log.information(f"Component {target_component.get_classname()} was not automatically connected: missing {key}")
                        return False
    return True


def delete_connections(target_component, source_component, unique_connections) -> int:
    """Delete the default connections of source_component to target_component from the unique_connections. Return the number of removed connections."""

    removed = 0
    if isinstance(target_component, dcp.DynamicComponent):
        dynamic_connections = target_component.get_dynamic_default_connections(
            source_component=source_component
        )
        for dynamic_connection in dynamic_connections:
            for c in unique_connections:
                if (
                    c.source.component_name == source_component.component_name and
                    c.source.field_name == dynamic_connection.source_component_field_name and
                    c.target.component_name == target_component.component_name and
                    c.target.field_name == dynamic_connection.source_component_field_name
                ):
                    unique_connections.remove(c)
                    removed += 1

    elif isinstance(target_component, cp.Component):
        connections = target_component.get_default_connections(
            source_component=source_component
        )
        for connection in connections:
            for c in unique_connections:
                if (
                    c.source.component_name == source_component.component_name and
                    c.source.field_name == connection.source_output_name and
                    c.target.component_name == target_component.component_name and
                    c.target.field_name == connection.target_input_name
                ):
                    unique_connections.remove(c)
                    removed += 1

    else:
        # This error message was generated by Copilot.
        raise TypeError(
            f"Type {type(target_component)} of target_component should be Component or DynamicComponent."
        )

    return removed


def remove_automatic_connections(my_sim: sim.Simulator, scenario: Scenario, unique_connections: List[Connection], seen_keys: set):
    """Remove all eligible automatic connections from unique_connections."""

    source_component_list = [wp.my_component for wp in my_sim.wrapped_components]
    removed = 0
    for target_component in source_component_list:
        tg_comps = [
            c for c in scenario.components if c.configuration.get("name") == target_component.component_name
        ]
        if len(tg_comps) != 1:
            raise ValueError(f"Expected to find exactly one component in scenario for component name {target_component.component_name}, but found {len(tg_comps)}")
        tg_comp = tg_comps[0]

        target_default_connection_dict = get_default_connection_dict(target_component)

        # check if target component has any default connections
        if bool(target_default_connection_dict) is True:
            # Check whether the target component is connected automatically (i.e., all default connections' keys are present in the seen_keys)
            tg_comp.connect_automatically = compare_automatic_connections(target_default_connection_dict, source_component_list, target_component, seen_keys)
        else:
            # There are no default connections
            log.information(f"Component {target_component.get_classname()} was not automatically connected: no default connections present")
            tg_comp.connect_automatically = False

        if tg_comp.connect_automatically:
            # Remove the connections that will be done automatically
            # target_default_connection_dict is still correct for tg_comp
            for source_component in source_component_list:
                source_component_classname = source_component.get_classname()

                # if the source components' classname is found in the target components' default connection dict, find connection to delete
                if source_component_classname in target_default_connection_dict.keys():
                    removed += delete_connections(target_component, source_component, unique_connections)

    log.information(f"Removed {removed} automatic connections from JSON output.")


def write_standalone_scenario_json(module_filename: str, my_sim: sim.Simulator, desc: str, path: str) -> None:
    """Write the scenario JSON file based on the components and connections of the simulator."""

    # Get prettified name for the scenario from the module filename
    nice_name = module_filename.replace("_", " ").capitalize()

    scenario = Scenario(
        name=nice_name,
        description=desc,
        multiple_buildings=my_sim.get_simulation_parameters().multiple_buildings
    )

    component_connections = []
    for component in my_sim.wrapped_components:
        add_component_to_scenario(scenario=scenario, config=component.my_component.config, component=component.my_component, my_sim=my_sim)

        for con in component.my_component.log_connections:
            component_connections += [Connection(
                source=Endpoint(
                    component_name=con["src_object_name"],
                    field_name=con["src_field_name"],
                ),
                target=Endpoint(
                    component_name=component.my_component.component_name,
                    field_name=con["input_fieldname"],
                ),
            )]

    all_connections = component_connections
    unique_connections, seen_keys = get_unique_connections(all_connections)

    remove_automatic_connections(my_sim=my_sim, scenario=scenario, unique_connections=unique_connections, seen_keys=seen_keys)

    scenario.connections = unique_connections
    if path.lower()[-5:] != ".json":
        path = path + ".json"
    log.information(f"Writing scenario parameters to JSON file {path}")
    with open(path, "w", encoding="utf-8") as f:
        f.write(scenario.model_dump_json(indent=4))
        f.flush()
        f.close()


def get_description_from_py(path_obj: Path) -> str:
    """Extract brief description from the first line of the system setup python file."""

    with open(path_obj, 'r', encoding="utf-8") as f:
        first_line = f.readline().strip()

    desc = first_line
    for quote_type in ['"""', "'''"]:
        if first_line.startswith(quote_type):
            desc = first_line.replace(quote_type, '').strip()
            break

    return desc


def main(
    path_to_module: str,
    my_simulation_parameters: Optional[SimulationParameters] = None,
    my_module_config: Optional[str] = None,
) -> None:
    """Core function."""
    # Suppress warnings (e.g., from pvlib)
    warnings.filterwarnings("ignore")

    # Delete old log files
    logging_default_path = Path(log.LOGGING_DEFAULT_PATH)
    if logging_default_path.exists() and logging_default_path.is_dir():
        for file in logging_default_path.iterdir():
            try:
                file.unlink()
            except Exception:
                log.information("Logging default file could not be removed. This can occur when more than one simulation run simultaneously.")

    # Logging simulation start
    function_in_module = "setup_function"
    log.information("#################################")
    log.information(f"Starting simulation of {path_to_module}")
    starttime = datetime.now()
    starting_date_time_str = starttime.strftime("%d-%b-%Y %H:%M:%S")
    log.information(f"Start @ {starting_date_time_str}")
    log.profile(f"{path_to_module} {function_in_module} Start @ {starting_date_time_str}")
    log.information("#################################")

    # Normalize module path and resolve absolute path
    path_obj = Path(path_to_module).with_suffix(".py").resolve()

    # Get module name (filename without suffix)
    module_filename = path_obj.stem

    # Add parent directory to PYTHONPATH
    module_dir = path_obj.parent
    if module_dir.exists():
        sys.path.append(str(module_dir))
    else:
        raise ValueError(f"Directory of module does not exist: {module_dir}")

    # Final check and import
    if not path_obj.is_file():
        raise ValueError(f"Python script {module_filename}.py could not be found at {path_obj}")

    # Make setup function executable
    targetmodule = importlib.import_module(module_filename)

    # Initialize simulator based on setup function
    my_sim: sim.Simulator = sim.Simulator(
        module_directory=str(module_dir),
        module_filename=module_filename,
        setup_function=function_in_module,
        my_simulation_parameters=my_simulation_parameters,
        my_module_config=my_module_config,
    )

    # Build method
    model_init_method = getattr(targetmodule, function_in_module)
    # Pass setup function to simulator
    model_init_method(my_sim, my_simulation_parameters)
    # Write the simulation parameters now, then alter them to include advanced logging
    write_standalone_simulation_json(my_sim, path=f"{module_dir}/{module_filename}.simulation.json")

    my_simulation_parameters = my_sim.get_simulation_parameters()
    my_simulation_parameters.log_connections = True

    # Other name due to mypy no-redef
    my_sim2: sim.Simulator = sim.Simulator(
        module_directory=str(module_dir),
        module_filename=module_filename,
        setup_function=function_in_module,
        my_simulation_parameters=my_simulation_parameters,
        my_module_config=my_module_config,
    )
    model_init_method(my_sim2, my_simulation_parameters)

    if my_sim2.my_module_config is not None:
        log.warning(f"Module config is not None but not exported to JSON: {my_sim2.my_module_config}")
    # The config dictionary is already part of the components (under "configuration")
    # if len(my_sim2.config_dictionary) > 0:
    #     log.warning(f"Config dictionary is not empty but not exported to JSON: {my_sim2.config_dictionary}")

    # Do not run the simulation
    my_sim2.prepare_calculation()
    my_sim2.connect_all_components()

    desc = get_description_from_py(path_obj)

    write_standalone_scenario_json(module_filename=module_filename, my_sim=my_sim2, desc=desc, path=f"{module_dir}/{module_filename}.scenario.json")

    log.information("#################################")
    endtime = datetime.now()
    starting_date_time_str = endtime.strftime("%d-%b-%Y %H:%M:%S")
    log.information("finished @ " + starting_date_time_str)
    log.profile("finished @ " + starting_date_time_str)
    log.profile("duration: " + str((endtime - starttime).total_seconds()))
    log.information("#################################")
    log.information("")

    # At the end put new logging files into result directory
    try:
        my_sim2.put_log_files_into_result_path()
    # sometimes when running many simulations at once this leads to errors, so ignore
    except Exception:
        pass


if __name__ == "__main__":
    if len(sys.argv) < 2:
        log.information("HiSim converter needs at least one argument.")
        sys.exit(1)
    FILE_NAME = sys.argv[1]
    FUNCTION_NAME = "setup_function"
    if len(sys.argv) == 2:
        log.information("calling " + FUNCTION_NAME + " from " + FILE_NAME)
        main(path_to_module=FILE_NAME)
    if len(sys.argv) == 3:
        MODULE_CONFIG = sys.argv[2]
        log.information("calling " + FUNCTION_NAME + " from " + FILE_NAME + " with module config " + MODULE_CONFIG)
        main(
            path_to_module=FILE_NAME,
            my_module_config=MODULE_CONFIG,
        )
