"""Tests for the report region, which postprocessing reads off the run's Weather component.

The region is report metadata -- the ``region`` field of the pyam export and of the webtool
result JSON. It used to travel through a process-wide singleton key the Weather wrote at
construction time; it now comes from the Weather component in the finished run, so these tests
pin the three answers ``region_of`` can give: no Weather, one Weather, and more than one.
"""

from __future__ import annotations

import datetime
from typing import List

import pandas as pd
import pytest

from hisim.component import Component
from hisim.component_wrapper import ComponentWrapper
from hisim.components.example_component import ExampleComponent, ExampleComponentConfig
from hisim.components.weather import LocationEnum, Weather, WeatherConfig
from hisim.postprocessing.postprocessing_datatransfer import PostProcessingDataTransfer
from hisim.postprocessing.postprocessing_main import region_of
from hisim.simulationparameters import SimulationParameters


SIMULATION_PARAMETERS = SimulationParameters(
    start_date=datetime.datetime(2021, 1, 1),
    end_date=datetime.datetime(2021, 1, 2),
    seconds_per_timestep=60,
    result_directory="",
    post_processing_options=[],
)


def _data_transfer(components: List[Component]) -> PostProcessingDataTransfer:
    """Build the smallest data transfer object ``region_of`` reads from."""
    return PostProcessingDataTransfer(
        results=pd.DataFrame(),
        all_outputs=[],
        simulation_parameters=SIMULATION_PARAMETERS,
        wrapped_components=[
            ComponentWrapper(component, is_cachable=False, connect_automatically=False)
            for component in components
        ],
        mode=1,
        setup_function="setup_function",
        module_filename="test_postprocessing_region",
        module_config=None,
        execution_time_in_s=0.0,
        results_monthly=None,
        results_hourly=None,
        results_cumulative=None,
        results_daily=None,
    )


def _weather(location: LocationEnum, name: str) -> Weather:
    """Build a Weather configured for one catalogue station."""
    config = WeatherConfig.get_default(location_entry=location, name=name)
    weather_component: Weather = Weather(my_simulation_parameters=SIMULATION_PARAMETERS, config=config)
    return weather_component


def _non_weather() -> ExampleComponent:
    """Build a component that is not a Weather, so the search cannot simply take the first one."""
    return ExampleComponent(
        my_simulation_parameters=SIMULATION_PARAMETERS,
        config=ExampleComponentConfig.get_default_example_component(),
    )


@pytest.mark.base
def test_region_is_the_weathers_configured_location() -> None:
    """A run with a Weather is reported under the location that Weather is configured for."""
    weather_component = _weather(LocationEnum.AACHEN, "Weather")
    ppdt = _data_transfer([_non_weather(), weather_component])

    assert region_of(ppdt) == weather_component.weather_config.location
    assert region_of(ppdt) != ""


@pytest.mark.base
def test_region_is_empty_without_a_weather() -> None:
    """A run with no Weather has no region, and gets the empty string the report falls back to."""
    assert region_of(_data_transfer([])) == ""
    assert region_of(_data_transfer([_non_weather()])) == ""


@pytest.mark.base
def test_region_of_two_weathers_is_the_first_one() -> None:
    """A district drawing on two stations is reported under the first Weather's location."""
    first = _weather(LocationEnum.AACHEN, "WeatherOne")
    second = _weather(LocationEnum.MANNHEIM, "WeatherTwo")
    assert first.weather_config.location != second.weather_config.location

    ppdt = _data_transfer([first, second])

    assert region_of(ppdt) == first.weather_config.location
