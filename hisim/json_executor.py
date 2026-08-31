""" Helper module to set up components and connections in the simulator based on the scenario data from the JSON file. """
# clean
import importlib
import inspect
import re
import typing
from pathlib import Path
from typing import Any, cast
from hisim import log, utils, loadtypes as lt
from hisim import simulator
try:
    import humps
except ModuleNotFoundError:
    raise ModuleNotFoundError(
        "The 'humps' library is required for JSON-based scenario execution. "
        "Please install it with 'pip install -e .'."
    ) from None

__authors__: str = "Valentin Janser"
__credits__: list[str] = ["Noah Pflugradt", "Katharina Rieck"]
__maintainer__: str = "Valentin Janser"
__email__: str = "v.janser@fz-juelich.de"


__all__: list[str] = ["import_from_string", "setup_components_and_connections"]


def import_from_string(full_classname: str) -> type[Any]:
    """Import a class from a fully qualified dotted module path.

    Args:
        full_classname: Dotted path of the form ``module.submodule.ClassName``.

    Returns:
        The class object referenced by ``full_classname``.

    Raises:
        ValueError: If ``full_classname`` cannot be split into a module path
            and class name.
        ImportError: If the module cannot be imported, or if the named class
            does not exist within the imported module.
    """
    # This function was created with the help of ChatGPT

    try:
        module_path, class_name = full_classname.rsplit(".", 1)
    except ValueError as e:
        raise ValueError(f"Invalid class path: {full_classname}") from e

    try:
        module = importlib.import_module(module_path)
    except ModuleNotFoundError as e:
        raise ImportError(f"Could not import module '{module_path}'") from e

    try:
        return cast(type[Any], getattr(module, class_name))
    except AttributeError as e:
        raise ImportError(f"Module '{module_path}' has no class '{class_name}'") from e


def _get_default_config(config_class: type[Any]) -> Any:
    """Find and invoke the single get_default_* classmethod on config_class.

    Raises ValueError if no such method exists on the class itself or if
    multiple candidates are found (in which case an explicit configuration
    must be supplied in the scenario JSON).
    """
    candidates = [
        (name, method)
        for name, method in inspect.getmembers(config_class, predicate=inspect.ismethod)
        if "default" in name and name in config_class.__dict__
    ]

    if not candidates:
        raise ValueError(
            f"{config_class.__name__} has no 'get_default_*' classmethod. "
            "Please provide a 'configuration' block in the scenario JSON."
        )
    if len(candidates) > 1:
        names = ", ".join(name for name, _ in candidates)
        raise ValueError(
            f"{config_class.__name__} has multiple default config methods ({names}). "
            "Please provide an explicit 'configuration' block in the scenario JSON."
        )

    method_name, method = candidates[0]
    try:
        return method()
    except TypeError as e:
        raise ValueError(
            f"Failed to call {config_class.__name__}.{method_name}() with no arguments: {e}. "
            "Please provide a 'configuration' block in the scenario JSON."
        ) from e


def setup_components_and_connections(scenario_data: dict[str, Any], sim: simulator.Simulator, sim_params: simulator.SimulationParameters) -> None:
    """Set up components and connections in the simulator from JSON scenario data.

    Iterates the ``components`` list in ``scenario_data``, instantiating each
    component from its config class, registering inputs/outputs, and adding it
    to the simulator. Then wires components together according to the
    ``connections`` list. Special-case handling is applied for UTSP LPG
    connector, EV charge controller, Weather, and Car components (e.g.
    resolving path placeholders, pascal-casing JSON keys, generating car
    info dicts).

    The ``sim`` parameter was renamed from ``my_sim`` for consistency with the
    ``system_setups/`` convention, where setup functions receive the simulator
    as ``sim``. This is a breaking change for callers that passed it by keyword
    (``my_sim=...``); pass it positionally or use the new name ``sim``.

    Args:
        scenario_data: Parsed scenario JSON containing ``components`` and
            ``connections`` sections.
        sim: The simulator instance to populate with components. Formerly
            named ``my_sim``; renamed for convention consistency.
        sim_params: Simulation parameters forwarded to each component
            constructor.

    Returns:
        None. ``sim`` is mutated in place (components are added and
        connections wired via ``add_component`` / ``connect_input``). The
        caller should continue using the passed-in ``sim`` instance; no
        separate return value is provided.

    Raises:
        ValueError: If no components are defined in the scenario, if a
            component fails to initialize, if a Car component cannot find
            its associated UTSP connector, or if a connection entry is
            malformed (missing required keys).
    """

    component_dict: dict[str, Any] = {}

    # Build Components
    components = scenario_data.get("components", [])
    if not components:
        raise ValueError("No components defined in scenario")

    for component_def in components:
        component_class = import_from_string(component_def["component_full_classname"])
        if "config_full_classname" in component_def:
            config_class = import_from_string(component_def["config_full_classname"])
        else:
            try:
                hints = typing.get_type_hints(component_class.__init__)
                config_class = hints["config"]
            except (KeyError, TypeError, NameError) as e:
                raise ValueError(
                    f"Could not determine config class for {component_class.__name__}: {e}. "
                    "Please add 'config_full_classname' to the scenario JSON."
                ) from e
        try:
            config_dict = component_def.get("configuration") or {}

            if not config_dict:
                log.information(
                    f"No configuration provided for {component_class.__name__}; using default config."
                )
                config = _get_default_config(config_class)
            else:
                # Fill in the absolute paths we have filled with placeholders in JSON
                if component_def["component_full_classname"] == "hisim.components.loadprofilegenerator_utsp_connector.UtspLpgConnector":
                    if config_dict["result_dir_path"] == "<<utils.HISIMPATH['utsp_results']>>":
                        config_dict["result_dir_path"] = utils.HISIMPATH["utsp_results"]
                    else:
                        # This warning was generated by Copilot
                        log.warning(f"Unexpected value for result_dir_path in UtspLpgConnector config: {config_dict['result_dir_path']}.")
                    # UtspLpgConnectorConfig has values of type JsonReference, which need to be converted to pascal-case
                    config_dict["household"] = humps.pascalize(config_dict["household"])
                    config_dict["travel_route_set"] = humps.pascalize(config_dict["travel_route_set"])
                    config_dict["transportation_device_set"] = humps.pascalize(config_dict["transportation_device_set"])
                    config_dict["charging_station_set"] = humps.pascalize(config_dict["charging_station_set"])
                elif component_def["component_full_classname"] == "hisim.components.controller_l1_generic_ev_charge.L1Controller":
                    config_dict["charging_station_set"] = humps.pascalize(config_dict["charging_station_set"])
                elif component_def["component_full_classname"] == "hisim.components.weather.Weather":
                    if "<<utils.get_input_directory()>>" in config_dict["source_path"]:
                        config_dict["source_path"] = _resolve_input_directory_placeholder(
                            path_with_placeholder=config_dict["source_path"]
                        )
                        # log.information(f"Resolved weather source path to {config_dict['source_path']}.")
                    else:
                        # This warning was generated by Copilot
                        log.warning(f"Unexpected value for source_path in Weather config: {config_dict['source_path']}.")

                # Use the dataclasses_json from_dict method to get the ConfigBase instance
                config = config_class.from_dict(config_dict)

            component = component_class(
                config=config,
                my_simulation_parameters=sim_params,
            )
        except TypeError as e:
            raise ValueError(
                f"Failed to initialize component {component_class.__name__}: {e}"
            ) from e

        # Create inputs/outputs
        for input_def in component_def.get("inputs", []):
            if input_def["dynamic"]:
                component.add_component_input_and_connect(
                    source_component_output=input_def["source_component_output"],
                    source_object_name=input_def["source_object_name"],
                    source_load_type=lt.LoadTypes(input_def["source_load_type"]),
                    source_unit=lt.Units(input_def["source_unit"]),
                    source_tags=input_def["source_tags"],
                    source_weight=input_def["source_weight"],
                )
            else:
                component.add_input(
                    object_name=input_def["object_name"],
                    field_name=input_def["field_name"],
                    load_type=lt.LoadTypes(input_def["load_type"]),
                    unit=lt.Units(input_def["unit"]),
                    mandatory=input_def["mandatory"],
                )
        for output_def in component_def.get("outputs", []):
            if output_def["dynamic"]:
                component.add_component_output(
                    source_output_name=output_def["source_output_name"],
                    source_tags=output_def["source_tags"],
                    source_load_type=lt.LoadTypes(output_def["source_load_type"]),
                    source_unit=lt.Units(output_def["source_unit"]),
                    source_weight=output_def["source_weight"],
                    output_description=output_def["output_description"],
                    source_component_class=output_def.get("source_component_class", None),
                )
            else:
                component.add_output(
                    object_name=output_def["object_name"],
                    field_name=output_def["field_name"],
                    load_type=lt.LoadTypes(output_def["load_type"]),
                    unit=lt.Units(output_def["unit"]),
                    postprocessing_flag=output_def.get("postprocessing_flag", None),
                    sankey_flow_direction=output_def.get("sankey_flow_direction", None),
                    output_description=output_def.get("output_description", None),
                )

        sim.add_component(component, connect_automatically=component_def["connect_automatically"])
        component_dict[component.config.component_id.name] = component

    # Connect components
    for conn in scenario_data.get("connections", []):
        try:
            source_name = conn["source"]["component_name"]
            source_field = conn["source"]["field_name"]

            target_name = conn["target"]["component_name"]
            target_field = conn["target"]["field_name"]

            # Resolve components
            source_comp = component_dict.get(source_name)
            target_comp = component_dict.get(target_name)
            if source_comp is None:
                log.warning(f"Source component not found: {source_name}")
                continue
            if target_comp is None:
                log.warning(f"Target component not found: {target_name}")
                continue

            # Connect (analogous to Python-based setups)
            try:
                target_comp.connect_input(
                    target_field,
                    source_name,
                    source_field,
                )
                log.debug(
                    f"Added connection: {source_name}.{source_field} -> {target_name}.{target_field}"
                )

            except Exception as e:
                # Likely, fields named like Input_..._value_number do not actually have a corresponding input?!
                log.warning(f"Failed to connect {source_name}.{source_field} to {target_name}.{target_field}: {e}")

        except KeyError as e:
            raise ValueError(f"Malformed connection entry: missing {e}") from e


def _resolve_input_directory_placeholder(path_with_placeholder: str) -> str:
    """Resolve an input-directory placeholder while accepting JSON from any OS."""

    placeholder = "<<utils.get_input_directory()>>"
    relative_path = path_with_placeholder.replace(placeholder, "", 1).strip("\\/")
    relative_parts = [part for part in re.split(r"[\\/]+", relative_path) if part]
    return str(Path(utils.get_input_directory(), *relative_parts).resolve())
