"""Test json generator."""

import pytest

from hisim.components import loadprofilegenerator_utsp_connector
from hisim.components import weather
from hisim.components import building
from hisim.components import generic_pv_system
from hisim.simulationparameters import SimulationParameters
from hisim.json_generator import JsonConfigurationGenerator
from hisim.postprocessingoptions import PostProcessingOptions


@pytest.mark.base
def test_execute_json_generator():
    """Test execute json generator."""
    ex = ExampleConfig()
    ex.make_example_config()


class ExampleConfig:

    """Example config class."""

    def make_example_config(self):
        """Make example config."""
        jcg: JsonConfigurationGenerator = JsonConfigurationGenerator("TestModel")

        # basic simulation parameters
        my_simulation_parameters = SimulationParameters.january_only_with_only_plots(
            year=2021, seconds_per_timestep=60
        )
        my_simulation_parameters.post_processing_options.append(
            PostProcessingOptions.MAKE_NETWORK_CHARTS
        )
        jcg.set_simulation_parameters(my_simulation_parameters)

        # Occupancy
        my_occupancy_config = (
            loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig.get_default_utsp_connector_config())
        occ_entry = jcg.add_component(config=my_occupancy_config)

        # Weather
        my_weather_config = weather.WeatherConfig.get_default(
            weather.LocationEnum.AACHEN
        )
        weather_entry = jcg.add_component(config=my_weather_config)

        # Building
        building_config = (
            building.BuildingConfig.get_default_german_single_family_home()
        )
        building_entry = jcg.add_component(config=building_config)

        # PV
        pv_config = generic_pv_system.PVSystemConfig.get_default_pv_system()
        pv_entry = jcg.add_component(config=pv_config)

        jcg.add_default_connection(from_entry=weather_entry, to_entry=building_entry)
        jcg.add_default_connection(from_entry=occ_entry, to_entry=building_entry)
        jcg.add_default_connection(from_entry=weather_entry, to_entry=pv_entry)
        # jcg.add_manual_connection(from_entry=weather_entry, output_name=)
        jcg.save_to_json("cfg.json")
