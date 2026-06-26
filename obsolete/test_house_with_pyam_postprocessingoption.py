"""  Basic household system setup adapted for pyam postprocessing test. """

# clean
import shutil
from pathlib import Path
from typing import Optional

import pytest

import pandas as pd
import hisim.simulator as sim
from hisim.simulator import SimulationParameters
from hisim.components import loadprofilegenerator_utsp_connector
from hisim.components import weather
from hisim.components import generic_pv_system
from hisim.components import building
from hisim.components import generic_heat_pump
from hisim import postprocessingoptions
from hisim.result_path_provider import ResultPathProviderSingleton, SortingOptionEnum

__authors__ = "Vitor Hugo Bellotto Zago, Noah Pflugradt"
__copyright__ = "Copyright 2022, FZJ-IEK-3"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "1.0"
__maintainer__ = "Noah Pflugradt"
__status__ = "development"

# PATH and FUNC needed to build simulator, PATH is fake
PATH = "../system_setups/household_for_pyam_test.py"


@pytest.mark.extendedbase
def test_house_with_pyam(
    my_simulation_parameters: Optional[SimulationParameters] = None,
) -> None:  # noqa: PLR0915
    """Basic household system setup.

    This setup function emulates an household including the basic components. Here the residents have their
    electricity and heating needs covered by the photovoltaic system and the heat pump.

    - Simulation Parameters
    - Components
        - Occupancy (Residents' Demands)
        - Weather
        - Photovoltaic System
        - Building
        - Heat Pump
    """

    # =================================================================================================================================
    # Set System Parameters

    # Set Simulation Parameters
    year = 2021
    seconds_per_timestep = 60 * 60

    # Set Heat Pump Controller
    temperature_air_heating_in_celsius = 19.0
    temperature_air_cooling_in_celsius = 24.0
    temperature_offset = 0.5
    hp_mode = 2

    # =================================================================================================================================

    # Build Simulation Parameters
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.one_day_only_with_only_plots(
            year=year, seconds_per_timestep=seconds_per_timestep
        )
    my_simulation_parameters.post_processing_options.append(
        postprocessingoptions.PostProcessingOptions.PREPARE_OUTPUTS_FOR_SCENARIO_EVALUATION
    )

    # this part is copied from hisim_main
    path_to_be_added = str(Path(PATH).resolve().parent)

    my_sim: sim.Simulator = sim.Simulator(
        module_directory=path_to_be_added,
        my_simulation_parameters=my_simulation_parameters,
        module_filename="household_for_pyam_test",
    )
    my_sim.set_simulation_parameters(my_simulation_parameters)

    # Build Results Path
    ResultPathProviderSingleton().set_important_result_path_information(
        module_directory=my_sim.module_directory,
        model_name=my_sim.module_filename,
        variant_name="pyam_test",
        sorting_option=SortingOptionEnum.FLAT,
        scenario_hash_string=None,
    )

    # =================================================================================================================================
    # Build Components

    # Build Building
    my_building_config = building.BuildingConfig.get_default_german_single_family_home()

    my_building = building.Building(
        config=my_building_config, my_simulation_parameters=my_simulation_parameters
    )
    # Occupancy
    my_occupancy_config = (
        loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig.get_default_utsp_connector_config())
    my_occupancy = loadprofilegenerator_utsp_connector.UtspLpgConnector(
        config=my_occupancy_config, my_simulation_parameters=my_simulation_parameters
    )

    # Build Weather
    my_weather_config = weather.WeatherConfig.get_default(
        location_entry=weather.LocationEnum.AACHEN
    )
    my_weather = weather.Weather(
        config=my_weather_config, my_simulation_parameters=my_simulation_parameters
    )

    # Build PV
    my_photovoltaic_system_config = (
        generic_pv_system.PVSystemConfig.get_default_pv_system()
    )
    my_photovoltaic_system = generic_pv_system.PVSystem(
        config=my_photovoltaic_system_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Heat Pump Controller
    my_heat_pump_controller = generic_heat_pump.GenericHeatPumpController(
        config=generic_heat_pump.GenericHeatPumpControllerConfig(
            building_name="BUI1",
            name="GenericHeatPumpController",
            temperature_air_heating_in_celsius=temperature_air_heating_in_celsius,
            temperature_air_cooling_in_celsius=temperature_air_cooling_in_celsius,
            offset=temperature_offset,
            mode=hp_mode,
        ),
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Heat Pump
    my_heat_pump = generic_heat_pump.GenericHeatPump(
        config=generic_heat_pump.GenericHeatPumpConfig.get_default_generic_heat_pump_config(),
        my_simulation_parameters=my_simulation_parameters,
    )

    # =================================================================================================================================
    # Connect Component Inputs with Outputs

    my_photovoltaic_system.connect_only_predefined_connections(my_weather)

    my_building.connect_only_predefined_connections(my_weather, my_occupancy)

    my_building.connect_input(
        my_building.ThermalPowerDelivered,
        my_heat_pump.component_name,
        my_heat_pump.ThermalPowerDelivered,
    )

    my_heat_pump_controller.connect_only_predefined_connections(my_building)

    my_heat_pump.connect_only_predefined_connections(
        my_weather, my_heat_pump_controller
    )
    my_heat_pump.get_default_connections_heatpump_controller()
    # =================================================================================================================================
    # Add Components to Simulation Parameters

    my_sim.add_component(my_occupancy)
    my_sim.add_component(my_weather)
    my_sim.add_component(my_photovoltaic_system)
    my_sim.add_component(my_building)
    my_sim.add_component(my_heat_pump_controller)
    my_sim.add_component(my_heat_pump)

    # Remove any stale scenario-evaluation output from a previous run of this
    # test so that the assertions below genuinely verify *this* run's output and
    # cannot pass against leftover files should the postprocessing silently become
    # a no-op.  The result directory path is deterministic within a single process
    # (the singleton's datetime_string is fixed at import time), so re-running the
    # test in the same session would otherwise reuse the same directory.
    _stale_result_dir = ResultPathProviderSingleton().get_result_directory_name()
    if _stale_result_dir is not None:
        _stale_scenario_eval_dir = Path(_stale_result_dir) / "result_data_for_scenario_evaluation"
        if _stale_scenario_eval_dir.exists():
            shutil.rmtree(_stale_scenario_eval_dir)

    my_sim.run_all_timesteps()

    # =================================================================================================================================
    # Verify the PREPARE_OUTPUTS_FOR_SCENARIO_EVALUATION postprocessing actually produced
    # its expected pyam-style output artifacts. Without these assertions the option could
    # silently become a no-op and this test would still pass.
    # =================================================================================================================================
    # The simulation writes its artifacts to the directory reported by
    # ResultPathProviderSingleton. SimulationParameters.result_directory starts empty and is
    # only populated from the singleton inside run_all_timesteps(), so reading the singleton
    # directly is the authoritative source (see simulator.prepare_simulation_directory).
    result_directory_name = ResultPathProviderSingleton().get_result_directory_name()
    assert result_directory_name is not None, (
        "ResultPathProviderSingleton did not return a result directory after the simulation ran."
    )
    result_dir = Path(result_directory_name)
    scenario_eval_dir = result_dir / "result_data_for_scenario_evaluation"
    assert scenario_eval_dir.is_dir(), (
        f"Scenario-evaluation output directory was not created at {scenario_eval_dir}."
    )

    # The scenario-evaluation CSVs are named "<resolution>_<duration_in_days>_days.csv".
    # The simulation uses one_day_only_with_only_plots, so duration.days == 1 here.
    # (timedelta.days truncates sub-day durations to 0; the naming pattern assumes a
    # duration of at least one day, which holds for the one-day simulation above.)
    duration_days = my_simulation_parameters.duration.days
    expected_csv_paths = {
        resolution: scenario_eval_dir / f"{resolution}_{duration_days}_days.csv"
        for resolution in ("hourly", "daily", "monthly", "yearly")
    }
    for resolution, csv_path in expected_csv_paths.items():
        assert csv_path.is_file(), (
            f"Scenario-evaluation {resolution} CSV was not produced at {csv_path}."
        )

    # Time-series CSVs (hourly/daily/monthly) follow the pyam long format with
    # columns: model, scenario, region, variable, unit, time, value.
    # Verified against the actual output of PrepareResultsForScenarioEvaluation
    # (see postprocessing_main.iterate_over_results_and_add_values_to_dict).
    # A set comparison is used so the test is not brittle to column reordering.
    expected_timeseries_columns = {
        "model", "scenario", "region", "variable", "unit", "time", "value",
    }
    for resolution in ("hourly", "daily", "monthly"):
        df = pd.read_csv(expected_csv_paths[resolution])
        assert not df.empty, f"Scenario-evaluation {resolution} CSV is empty."
        assert set(df.columns) == expected_timeseries_columns, (
            f"Scenario-evaluation {resolution} CSV has unexpected columns: "
            f"{list(df.columns)}, expected (in any order) {sorted(expected_timeseries_columns)}."
        )

    # The yearly CSV uses 'year' instead of 'time' (verified against the
    # simple_dict_cumulative_data structure in prepare_results_for_scenario_evaluation).
    expected_yearly_columns = {
        "model", "scenario", "region", "variable", "unit", "year", "value",
    }
    yearly_df = pd.read_csv(expected_csv_paths["yearly"])
    assert not yearly_df.empty, "Scenario-evaluation yearly CSV is empty."
    assert set(yearly_df.columns) == expected_yearly_columns, (
        f"Scenario-evaluation yearly CSV has unexpected columns: "
        f"{list(yearly_df.columns)}, expected (in any order) {sorted(expected_yearly_columns)}."
    )

    # The model name is derived from the module filename and the region from the weather location.
    assert (yearly_df["model"] == f"HiSim_{my_sim.module_filename}").all(), (
        f"Unexpected model name in scenario-evaluation output: "
        f"{sorted(set(yearly_df['model']))}."
    )
    # The region comes from the weather location set via SingletonSimRepository
    # (see weather.py: set_entry(LOCATION, weather_config.location)).  my_weather_config.location
    # is a plain str (the first element of the LocationEnum value tuple, e.g. "Aachen" for
    # LocationEnum.AACHEN), which is exactly the value the postprocessing writes to the CSV's
    # "region" column, so a direct equality comparison is correct.
    assert (yearly_df["region"] == my_weather_config.location).all(), (
        f"Unexpected region in scenario-evaluation output: "
        f"{sorted(set(yearly_df['region']))}."
    )

    # The scenario-evaluation folder also contains the standalone config JSONs.
    assert (scenario_eval_dir / "simulation.json").is_file(), (
        "simulation.json was not produced in the scenario-evaluation directory."
    )
    assert (scenario_eval_dir / "scenario.json").is_file(), (
        "scenario.json was not produced in the scenario-evaluation directory."
    )
