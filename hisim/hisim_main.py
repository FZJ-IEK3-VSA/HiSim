""" Main module for HiSim: Starts the Simulator. """
# clean
import os
import warnings
import importlib
from pathlib import Path
import sys
from datetime import datetime
from typing import Optional, Any, cast
import argparse
from pydantic import TypeAdapter
from dotenv import load_dotenv
try:
    from hisim.postprocessingoptions import PostProcessingOptions
    from hisim.components.generic_car import GenericCarInformation
    import hisim.simulator as sim
    from hisim import log, utils
    from hisim import loadtypes as lt
    from hisim.simulationparameters import SimulationParameters
except ModuleNotFoundError:
    raise ModuleNotFoundError(
        "Could not import HiSim modules. "
        "It may not be installed in the current Python environment.\n\n"
        "If you already installed HiSim locally with 'pip install -e .', "
        "make sure you are using the same virtual environment/interpreter."
    ) from None
try:
    import humps
except ModuleNotFoundError:
    raise ModuleNotFoundError(
        "The 'humps' library is required for JSON-based scenario execution. "
        "Please install it with 'pip install pyhumps==3.8.0'."
    ) from None

load_dotenv()

__authors__ = "Valentin Janser"
__credits__ = ["Noah Pflugradt", "Katharina Rieck"]
__maintainer__ = "Valentin Janser"
__email__ = "v.janser@fz-juelich.de"


def initialize_from_python(
    path_to_module: str,
    my_simulation_parameters: Optional[SimulationParameters] = None,
    my_module_config: Optional[str] = None,
) -> sim.Simulator:
    """Initialize the simulator based on a Python household configuration and optional simulation parameters and module config."""

    function_in_module = "setup_function"

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

    return my_sim


def load_json_file(path_str: str) -> dict[str, Any]:
    """Load a JSON file and return it as a Python dict."""
    # This function was created with the help of ChatGPT

    path = Path(path_str).expanduser().resolve()

    try:
        with path.open("r", encoding="utf-8") as f:
            json_dict = TypeAdapter(dict[str, Any])
            data = json_dict.validate_json(f.read())
            return cast(dict[str, Any], data)
    except Exception as e:
        raise ValueError(f"Invalid JSON in file {path}: {e}") from e


def initialize_from_json(
    scenario: str,
    simulation_parameters: str,
    path_to_module: str,
    delta: Optional[str],
) -> sim.Simulator:
    """Initialize the simulator based on JSON scenario and simulation parameters."""

    # Load JSON files
    scenario_data = load_json_file(scenario)
    # Missing in the following data: result_directory, surplus_control, cache_dir_path, multiple_buildings
    # -> Result Directory is set in prepare_simulation_directory function, called by run_all_timesteps
    # -> Cache Dir Path is filled by default in SimulationParameters
    # -> Surplus Control: see comment in hisim_convert_to_json.py
    sim_params_data = load_json_file(simulation_parameters)
    sim_params_data['multiple_buildings'] = scenario_data.get('multiple_buildings', False)
    sim_params_data['start_date'] = datetime.fromisoformat(sim_params_data['start_date'])
    sim_params_data['end_date'] = datetime.fromisoformat(sim_params_data['end_date'])
    sim_params_data['post_processing_options'] = [PostProcessingOptions[option] for option in sim_params_data.get('post_processing_options', [])]
    sim_params = SimulationParameters(**sim_params_data)
    sim_params.log_connections = True  # For easy post-processing (and debugging)

    # Normalize module path and resolve absolute path
    path_obj = Path(path_to_module).with_suffix(".json").resolve()
    # Get module name (filename without suffix)
    module_filename = path_obj.stem
    # Add parent directory to PYTHONPATH
    module_dir = path_obj.parent
    if module_dir.exists():
        sys.path.append(str(module_dir))
    else:
        raise ValueError(f"Directory of module does not exist: {module_dir}")

    my_sim = sim.Simulator(
        module_directory=module_dir,
        module_filename=module_filename,
        setup_function="setup_function",  # In JSON mode we do not use a setup function; but must not be None for post-processing
        my_module_config=None,
        my_simulation_parameters=sim_params,
    )  # type: ignore[no-any-return]

    # Build Components
    components = scenario_data.get("components", [])
    if not components:
        raise ValueError("No components defined in scenario")

    component_dict = {}

    def import_from_string(full_classname: str):
        """Import a class from a full module path string."""
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
            return getattr(module, class_name)
        except AttributeError as e:
            raise ImportError(f"Module '{module_path}' has no class '{class_name}'") from e

    for comp_def in components:
        component_class = import_from_string(comp_def["component_full_classname"])
        config_class = import_from_string(comp_def["config_full_classname"])
        try:
            config_dict = comp_def.get("configuration", {})
            # Fill in the absolute paths we have filled with placeholders in JSON
            if comp_def["component_full_classname"] == "hisim.components.loadprofilegenerator_utsp_connector.UtspLpgConnector":
                if config_dict["result_dir_path"] == "<<utils.HISIMPATH['utsp_results']>>":
                    config_dict["result_dir_path"] = utils.HISIMPATH["utsp_results"]
                else:
                    # This warning was generated by Copilot
                    log.warning(f"Unexpected value for result_dir_path in UtspLpgConnector config: {config_dict['result_dir_path']}.")
                # UtspLpgConnectorConfig has values of type JsonReference, which need to be converted to camel-case
                config_dict["household"] = humps.pascalize(config_dict["household"])
                config_dict["travel_route_set"] = humps.pascalize(config_dict["travel_route_set"])
                config_dict["transportation_device_set"] = humps.pascalize(config_dict["transportation_device_set"])
                config_dict["charging_station_set"] = humps.pascalize(config_dict["charging_station_set"])
            elif comp_def["component_full_classname"] == "hisim.components.weather.Weather":
                if "<<utils.get_input_directory()>>" in config_dict["source_path"]:
                    config_dict["source_path"] = config_dict["source_path"].replace("<<utils.get_input_directory()>>", utils.get_input_directory())
                else:
                    # This warning was generated by Copilot
                    log.warning(f"Unexpected value for source_path in Weather config: {config_dict['source_path']}.")

            # Use the JSONWizard from_dict method to get ConfigBase instance
            config = config_class.from_dict(config_dict)

            if comp_def["component_full_classname"] == "hisim.components.generic_car.Car":
                # We have to generate the car_info_dict
                car_info = None
                found = False
                for comp in my_sim.wrapped_components:
                    if comp.my_component.get_full_classname() == "hisim.components.loadprofilegenerator_utsp_connector.UtspLpgConnector":
                        if config_dict["name"] in comp.my_component.config.cars:
                            found = True
                            car_info = GenericCarInformation(comp.my_component).data_dict_for_car_component[config_dict["household_name"]]
                if not found:
                    raise ValueError(f"The car '{config_dict['name']}' was not associated with any UTSP connector.")
                component = component_class(
                    config=config,
                    my_simulation_parameters=sim_params,
                    data_dict_with_car_information=car_info,
                )
            else:
                component = component_class(
                    config=config,
                    my_simulation_parameters=sim_params,
                )
        except TypeError as e:
            raise ValueError(
                f"Failed to initialize component {component_class.__name__}: {e}"
            ) from e

        # Create inputs/outputs
        for input_def in comp_def.get("inputs", []):
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
        for output_def in comp_def.get("outputs", []):
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

        my_sim.add_component(component, connect_automatically=comp_def["connect_automatically"])
        component_dict[component.config.name] = component

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
                log.information(
                    f"Added connection: {source_name}.{source_field} -> {target_name}.{target_field}"
                )

            except Exception as e:
                # Likely, fields named like Input_..._value_number do not actually have a corresponding input?!
                log.warning(f"Failed to connect {source_name}.{source_field} to {target_name}.{target_field}: {e}")

        except KeyError as e:
            raise ValueError(f"Malformed connection entry: missing {e}") from e

    if delta:
        # delta_data = load_json_file(delta)
        log.warning("====================================================================")
        log.warning("== The delta file is currently not supported and will be ignored. ==")
        log.warning("====================================================================")

    return my_sim  # type: ignore[no-any-return]


def run_simulation(my_sim: sim.Simulator, path_to_module: Optional[str]) -> None:
    """Runs the simulation (for both Python-based and JSON-based executions)."""

    # Print components and their inputs/outputs
    for comp in my_sim.wrapped_components:
        log.information(f"Component {comp.my_component.component_name} has inputs "
                        f"{[input.full_name for input in comp.component_inputs]} and"
                        f" outputs {[output.full_name for output in comp.component_outputs]}")

    log.information("#################################")
    log.information(f"Starting simulation of {path_to_module}")
    starttime = datetime.now()
    starting_date_time_str = starttime.strftime("%d-%b-%Y %H:%M:%S")
    log.information(f"Start @ {starting_date_time_str}")
    log.information("#################################")

    # Perform simulation throughout the defined timeline
    my_sim.run_all_timesteps()

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
        my_sim.put_log_files_into_result_path()
    # sometimes when running many simulations at once this leads to errors, so ignore
    except Exception:
        pass


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for HiSim execution."""

    # Adapted from README from https://github.com/FZJ-IEK3-VSA/HiSim
    description = (
        "ETHOS.HiSim --- Household Infrastructure and Building Simulator\n\n"
        "ETHOS.HiSim allows simulating and analyzing household scenarios "
        "and building systems, integrating load profiles generation of "
        "electricity consumption/generation, heating demand, "
        "and smart strategies of modern components, such as heat pump, "
        "battery, electric vehicle or thermal energy storage."
    )

    parser = argparse.ArgumentParser(
        prog="python hisim_main.py",
        description=description,
        formatter_class=argparse.RawTextHelpFormatter,
    )

    parser.add_argument(
        "inputs",
        nargs="+",
        help=(
            "JSON mode:\n"
            "  <scenario_params.json> <simulation_params.json> [scenario_delta.json]\n\n"
            "Legacy Python mode:\n"
            "  <module.py> [module_config]"
        ),
    )

    return parser.parse_args()


def validate_args(args: argparse.Namespace):
    """Check the provided command-line arguments for validity and determine execution mode."""

    inputs = args.inputs

    if inputs[0].endswith(".py"):
        if len(inputs) > 2:
            raise ValueError(
                "The legancy Python mode accepts at most 2 arguments:\n"
                "  <module.py> [module_config]"
            )

        module_file = inputs[0]
        module_config = inputs[1] if len(inputs) == 2 else None

        if not os.path.isfile(module_file):
            raise FileNotFoundError(f"Python module not found: {module_file}")

        return {
            "mode": "python",
            "module_file": module_file,
            "module_config": module_config,
        }

    if inputs[0].endswith(".json"):
        if len(inputs) < 2:
            raise ValueError(
                "The JSON mode requires at least 2 files:\n"
                "  <scenario.json> <simulation_params.json> [delta.json]"
            )
        if len(inputs) > 3:
            raise ValueError(
                "The JSON mode accepts at most 3 files:\n"
                "  <scenario.json> <simulation_params.json> [delta.json]"
            )

        for f in inputs:
            if not f.endswith(".json"):
                raise ValueError(f"Invalid file in JSON mode (must be .json): {f}")
            if not os.path.isfile(f):
                raise FileNotFoundError(f"File not found: {f}")

        return {
            "mode": "json",
            "scenario": inputs[0],
            "simulation": inputs[1],
            "delta": inputs[2] if len(inputs) == 3 else None,
        }

    raise ValueError(
        "First argument must be either:\n"
        "  - a Python file (*.py) for legacy Python mode, or\n"
        "  - a JSON file (*.json) for JSON mode"
    )


def main_cli():
    """Main function for command-line execution of HiSim, supporting both Python-based and JSON-based scenarios."""

    try:
        args = parse_args()
        config = validate_args(args)

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

        # Dispatching logic
        if config["mode"] == "python":
            print(f"Calling setup_function from {config['module_file']}")
            my_sim = initialize_from_python(
                path_to_module=config["module_file"],
                my_module_config=config["module_config"],
            )
            ptm = config["module_file"]

        elif config["mode"] == "json":
            print(f"Running simulation of scenario {config['scenario']} with simulation parameters {config['simulation']}"
                  + (f" and delta {config['delta']}" if config["delta"] else ""))
            my_sim = initialize_from_json(
                scenario=config["scenario"],
                simulation_parameters=config["simulation"],
                path_to_module=config["scenario"],
                delta=config["delta"],
            )
            ptm = config["scenario"]

        run_simulation(my_sim, path_to_module=ptm)

    except Exception as e:
        raise e


def main(path_to_module: str, my_simulation_parameters: Optional[SimulationParameters] = None, my_module_config: Optional[str] = None) -> None:
    """Main function only needed for legacy functionality, such as system setup tests."""

    my_sim = initialize_from_python(
        path_to_module=path_to_module,
        my_simulation_parameters=my_simulation_parameters,
        my_module_config=my_module_config,
    )
    run_simulation(my_sim, path_to_module=path_to_module)


if __name__ == "__main__":
    main_cli()
