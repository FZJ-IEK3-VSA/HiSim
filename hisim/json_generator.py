""" Helper module to generate the JSON configurations. """
# clean
from __future__ import annotations
import dataclasses
import re
import json
from typing import List, Any, Dict, Tuple, cast, overload, TYPE_CHECKING
from pathlib import Path
# 3rd party imports
from pydantic import BaseModel, Field
import humps
# 1st party imports
from hisim import log
from hisim.postprocessingoptions import PostProcessingOptions
from hisim.components.controller_l2_energy_management_system import L2GenericEnergyManagementSystem
from hisim.components.loadprofilegenerator_utsp_connector import UtspLpgConnector
from hisim.components.generic_car import Car, GenericCarInformation
from hisim.component import ConfigBase
import hisim.component as cp
import hisim.dynamic_component as dcp
from hisim.dynamic_component import DynamicComponent
if TYPE_CHECKING:
    from hisim.simulator import Simulator

__authors__ = "Valentin Janser"
__credits__ = ["Noah Pflugradt", "Katharina Rieck"]
__maintainer__ = "Valentin Janser"
__email__ = "v.janser@fz-juelich.de"


# Adapted from old json_generator.py
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


def count_outputs_created_by_constructor(component: L2GenericEnergyManagementSystem) -> int:
    """Count the outputs the EMS builds for itself, before any system setup adds more.

    The EMS creates dynamic outputs from inside its own ``__init__``, one per
    ``get_default_connections_from_*`` helper. Those must not be written to the scenario
    JSON, because the JSON executor instantiates the component the same way and would end
    up with each of them twice. Outputs a system setup appended afterwards must be written,
    and they are exactly the ones whose ``OutputN`` index runs past this count.

    The count is measured rather than assumed: a pristine instance of the same class is
    built from the same config and its outputs are counted. ``Component.__init__`` only
    populates the instance -- it registers nothing globally -- so the throwaway instance is
    free of side effects. Hard-coding the number instead silently rotted once already, when
    retiring the modular DHW heat pump removed one of the EMS helpers and shifted every
    setup-added output down by one index.
    """
    pristine_instance = type(component)(
        my_simulation_parameters=component.my_simulation_parameters,
        config=component.ems_config,
    )
    return len(pristine_instance.outputs)


# Adapted from old json_generator.py
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
            relative_input_path = Path(*dir_parts[idx + 1:]).as_posix()
            config_json_str["source_path"] = f"<<utils.get_input_directory()>>/{relative_input_path}"
        else:
            log.warning(f"Could not find 'inputs' in absolute weather source path {config_json_str['source_path']}, leaving it unchanged in JSON output...")
    # Car information can be generated using Occupancy (see class GenericCarInformation)
    # However, then each car must be assigned to an occupancy
    elif config.get_main_classname() == "hisim.components.generic_car.Car":
        config_json_str["household_name"] = cast(Car, component).car_information_dict["household_name"]

    outs = []
    ins = []
    # Used by the EMS special case inside the output loop below; see the comment there.
    number_of_outputs_built_by_constructor = (
        count_outputs_created_by_constructor(component)
        if isinstance(component, L2GenericEnergyManagementSystem)
        else 0
    )
    for out in component.outputs:
        if isinstance(component, DynamicComponent):
            output_matches = [
                dynamic_output
                for dynamic_output in component.my_component_outputs
                if dynamic_output.source_output_field_name == out.field_name
            ]
            if len(output_matches) > 1:
                raise ValueError(
                    f"Multiple dynamic outputs found for field '{out.field_name}' in component '{component.component_name}'"
                )
            if len(output_matches) == 1:
                match = re.search(r"Output(\d+)$", out.field_name)
                number = int(match.group(1)) if match else 100

                # Handle special case for EMS: its constructor already builds a batch of
                # dynamic outputs (one per `get_default_connections_from_*` helper), and the
                # JSON executor gets those back for free when it instantiates the component.
                # Re-declaring them here would duplicate them, so only the outputs a system
                # setup added on top of the constructor's belong in the file. The cut-off is
                # read off a pristine instance instead of hard-coded, because it shifts
                # whenever a default-connection helper is added or retired.
                if isinstance(component, L2GenericEnergyManagementSystem):
                    if number <= number_of_outputs_built_by_constructor:
                        continue

                # add_component_output has been used
                dynamic_output = output_matches[0]
                # Extract source_output_name from the field_name
                match = re.match(r"^(.*?)(Output\d+)$", out.field_name)
                if not match:
                    raise ValueError(f"Invalid field_name format: {out.field_name}")
                source_object_name = match.group(1)
                outs.append({
                    "dynamic": True,
                    "source_output_name": source_object_name,
                    "source_tags": dynamic_output.source_tags,
                    "source_load_type": dynamic_output.source_load_type.value,
                    "source_unit": dynamic_output.source_unit.value,
                    "source_weight": dynamic_output.source_weight,
                    "output_description": out.output_description,
                    "source_component_class": dynamic_output.source_component_class if dynamic_output.source_component_class is not None else None,
                })
                continue

        # add_output has been used
        # => Does not have to be exported, since it is created automatically upon creation of the component

    for inp in component.inputs:
        if isinstance(component, DynamicComponent):
            input_matches = [
                dynamic_input
                for dynamic_input in component.my_component_inputs
                if dynamic_input.source_component_class == inp.field_name
            ]
            if len(input_matches) > 1:
                raise ValueError(
                    f"Multiple dynamic inputs found for field '{inp.field_name}' in component '{component.component_name}'"
                )
            if len(input_matches) == 1:
                # add_component_input(s)_and_connect has been used
                dynamic_input = input_matches[0]
                source_component_field_name = dynamic_input.source_component_field_name

                # Extract source_object_name
                pattern = rf"^Input_(.*?)_{re.escape(source_component_field_name)}_\d+$"
                match = re.match(pattern, inp.field_name)
                if not match:
                    raise ValueError(f"Label does not match expected format: {inp.field_name}")
                source_object_name = match.group(1)

                ins.append({
                    "dynamic": True,
                    "source_component_output": source_component_field_name,
                    "source_object_name": source_object_name,
                    "source_load_type": inp.loadtype.value,
                    "source_unit": inp.unit.value,
                    "source_tags": dynamic_input.source_tags,
                    "source_weight": dynamic_input.source_weight,
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


def get_filtered_simulation_parameters(my_sim: "Simulator"):
    """Gives the simulation parameters as a snake_case dict, with some fields excluded that are not strictly simulation parameters."""
    params = my_sim.get_simulation_parameters()
    excluded = {"multiple_buildings", "result_directory", "cache_dir_path", "surplus_control"}
    filtered = {
        field.name: getattr(params, field.name)
        for field in dataclasses.fields(params)
        if field.name not in excluded
    }
    # JSON-portable spellings: datetimes as ISO strings, post-processing options by name
    # instead of by integer value (the field is annotated List[int] but holds enum members).
    filtered["start_date"] = params.start_date.isoformat()
    filtered["end_date"] = params.end_date.isoformat()
    filtered["post_processing_options"] = [
        PostProcessingOptions(ppo).name for ppo in params.post_processing_options
    ]
    return filtered


def write_standalone_simulation_json(my_sim: "Simulator", path="recent_simulation_parameters.json") -> None:
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


def add_component_to_scenario(scenario: Scenario, config: ConfigBase, component: cp.Component, my_sim: "Simulator") -> None:
    """Add a simulator component to the scenario JSON object."""

    component_entry, ins, outs = convert_component_to_json(config, component)
    if config.get_main_classname() == "hisim.components.generic_car.Car":
        # Handle special case for Car component, link to LPG connector
        for idx, comp in enumerate(scenario.components):
            if comp.component_full_classname == "hisim.components.loadprofilegenerator_utsp_connector.UtspLpgConnector":
                car_info = cast(Car, component).car_information_dict
                lpg_connector = cast(UtspLpgConnector, my_sim.wrapped_components[idx].my_component)  # For mypy, we know that this is an UtspLpgConnector
                new_car_info = GenericCarInformation(my_occupancy_instance=lpg_connector).data_dict_for_car_component[car_info["household_name"]]

                if new_car_info["time_resolution"] == car_info["time_resolution"] and \
                new_car_info["car_location"] == car_info["car_location"] and new_car_info["driven_meters"] == car_info["driven_meters"]:
                    # Cannot include car inside LPG connector, then not found for connections/inputs/outputs etc.
                    comp.configuration["cars"] = (
                        (comp.configuration.get("cars") or []) + [component_entry.configuration["component_id"]["name"]]
                    )
                    log.information(f"Added car information to LPG connector config for car {component.component_name}")

    # Always do:
    scenario.components.append(component_entry)
    log.debug(
        "Added component " + config.component_id.name + " with " + str(len(ins))
        + " inputs and " + str(len(outs)) + " outputs"
    )


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
        log.debug(f"Component {target_component.get_classname()} was not automatically connected: no source components found in default connections")
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
                        log.debug(f"Component {target_component.get_classname()} was not automatically connected: missing {dynamic_connection}")
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
                        log.debug(f"Component {target_component.get_classname()} was not automatically connected: missing {key}")
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
            for connection in unique_connections:
                if (
                    connection.source.component_name == source_component.component_name and
                    connection.source.field_name == dynamic_connection.source_component_field_name and
                    connection.target.component_name == target_component.component_name and
                    connection.target.field_name == dynamic_connection.source_component_field_name
                ):
                    unique_connections.remove(connection)
                    removed += 1

    elif isinstance(target_component, cp.Component):
        connections = target_component.get_default_connections(
            source_component=source_component
        )
        for default_connection in connections:
            for connection in unique_connections:
                if (
                    connection.source.component_name == source_component.component_name and
                    connection.source.field_name == default_connection.source_output_name and
                    connection.target.component_name == target_component.component_name and
                    connection.target.field_name == default_connection.target_input_name
                ):
                    unique_connections.remove(connection)
                    removed += 1

    else:
        # This error message was generated by Copilot.
        raise TypeError(
            f"Type {type(target_component)} of target_component should be Component or DynamicComponent."
        )

    return removed


def get_component_key_from_configuration(configuration: dict[str, Any]) -> str:
    """Derives the runtime component name from a serialized configuration block.

    A scenario JSON stores the component identity as a nested ``component_id`` object holding
    ``name``, ``building`` and ``unit``. This helper rebuilds the same runtime key that
    :py:attr:`hisim.component.ComponentID.key` produces for the live configuration, so that a
    scenario entry can be matched against an already-instantiated component without anyone
    having to take an existing key apart.
    """
    identity = configuration.get("component_id") or {}
    return cp.ComponentID(
        name=identity.get("name", ""),
        building=identity.get("building"),
        unit=identity.get("unit"),
    ).key


def remove_automatic_connections(my_sim: "Simulator", scenario: Scenario, unique_connections: List[Connection], seen_keys: set):
    """Remove all eligible automatic connections from unique_connections."""

    source_component_list = [wp.my_component for wp in my_sim.wrapped_components]
    removed = 0
    for target_component in source_component_list:
        target_scenario_components = [
            c for c in scenario.components
            if get_component_key_from_configuration(c.configuration) == target_component.component_name
        ]
        if len(target_scenario_components) != 1:
            raise ValueError(f"Expected to find exactly one component in scenario for component name {target_component.component_name}, "
                             f"but found {len(target_scenario_components)}. Available configs: {[c.configuration for c in scenario.components]}")
        target_scenario_component = target_scenario_components[0]

        target_default_connection_dict = get_default_connection_dict(target_component)

        # check if target component has any default connections
        if bool(target_default_connection_dict) is True:
            # Check whether the target component is connected automatically (i.e., all default connections' keys are present in the seen_keys)
            target_scenario_component.connect_automatically = compare_automatic_connections(
                target_default_connection_dict,
                source_component_list,
                target_component,
                seen_keys,
            )
        else:
            # There are no default connections
            log.debug(f"Component {target_component.get_classname()} was not automatically connected: no default connections present")
            target_scenario_component.connect_automatically = False

        if target_scenario_component.connect_automatically:
            # Remove the connections that will be done automatically
            # target_default_connection_dict is still correct for target_scenario_component
            for source_component in source_component_list:
                source_component_classname = source_component.get_classname()

                # if the source components' classname is found in the target components' default connection dict, find connection to delete
                if source_component_classname in target_default_connection_dict.keys():
                    removed += delete_connections(target_component, source_component, unique_connections)

    log.information(f"Removed {removed} automatic connections from JSON output.")


def write_standalone_scenario_json(module_filename: str, my_sim: "Simulator", desc: str, path: str) -> None:
    """Write the scenario JSON file based on the components and connections of the simulator."""

    # Get prettified name for the scenario from the module filename
    nice_name = module_filename.replace("_", " ").capitalize()

    scenario = Scenario(
        name=nice_name,
        description=desc,
        multiple_buildings=my_sim.get_simulation_parameters().multiple_buildings
    )
    log.information(f"Writing component configurations to JSON file {path}.")
    component_connections = []
    for component in my_sim.wrapped_components:
        add_component_to_scenario(scenario=scenario, config=component.my_component.config, component=component.my_component, my_sim=my_sim)

        for connection in component.my_component.log_connections:
            component_connections += [Connection(
                source=Endpoint(
                    component_name=connection["src_object_name"],
                    field_name=connection["src_field_name"],
                ),
                target=Endpoint(
                    component_name=component.my_component.component_name,
                    field_name=connection["input_fieldname"],
                ),
            )]

    all_connections = component_connections
    unique_connections, seen_keys = get_unique_connections(all_connections)

    remove_automatic_connections(my_sim=my_sim, scenario=scenario, unique_connections=unique_connections, seen_keys=seen_keys)

    scenario.connections = unique_connections
    if path.lower()[-5:] != ".json":
        path = path + ".json"
    log.information(f"Writing scenario parameters to JSON file {path}.")
    with open(path, "w", encoding="utf-8") as f:
        f.write(scenario.model_dump_json(indent=4))
        f.flush()
        f.close()
