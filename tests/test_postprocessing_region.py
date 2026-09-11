"""Tests for the report region, which postprocessing reads off the run's Weather components.

The region is report metadata -- the ``region`` field of the pyam export and of the
scenario-evaluation config JSON. It used to travel through a process-wide singleton key the
Weather wrote at construction time; it now comes from the Weather components in the finished
run, so these tests pin the three answers ``region_of`` can give: no Weather, one Weather, and
more than one.
"""

from __future__ import annotations

import datetime
from typing import Iterator, List

import pytest

from hisim.component import Component
from hisim.component_wrapper import ComponentWrapper
from hisim.components.example_component import ExampleComponent
from hisim.components.weather import LocationEnum, Weather, WeatherConfig
from hisim.postprocessing.postprocessing_datatransfer import PostProcessingDataTransfer
from hisim.postprocessing.postprocessing_main import region_of
from tests import functions_for_testing as fft
from tests.postprocessing_option_test_framework import (
    PreparedPostProcessingCase,
    SETUP_MODULE_NAME,
    _clone_ppdt,
    _prepare_case,
)


@pytest.fixture(name="prepared_case", scope="module")
def prepared_case_fixture() -> Iterator[PreparedPostProcessingCase]:
    """Run the smallest system setup once, to borrow a finished run's data transfer object."""
    yield _prepare_case(
        setup_module_name=SETUP_MODULE_NAME,
        start_date=datetime.datetime(2021, 1, 1),
        end_date=datetime.datetime(2021, 1, 2),
        seconds_per_timestep=3600,
        test_name_prefix="postprocessing_region",
    )


def _data_transfer_reporting_on(
    case: PreparedPostProcessingCase, components: List[Component]
) -> PostProcessingDataTransfer:
    """Take the prepared run's data transfer object, reporting on these components instead."""
    return _clone_ppdt(
        case=case,
        simulation_parameters=case.ppdt.simulation_parameters,
        wrapped_components=[
            ComponentWrapper(component, is_cachable=False, connect_automatically=False)
            for component in components
        ],
    )


def _weather(case: PreparedPostProcessingCase, name: str, location: LocationEnum) -> Weather:
    """Build a Weather configured for one catalogue station."""
    # The config and component classes resolve to Any under the tests' mypy profile, so the
    # annotation is what pins the built component to a Weather.
    weather_component: Weather = Weather(
        my_simulation_parameters=case.ppdt.simulation_parameters,
        config=WeatherConfig.for_location(name, location=location),
    )
    return weather_component


def _non_weather(case: PreparedPostProcessingCase) -> ExampleComponent:
    """Build a component that is not a Weather, so the search cannot simply take the first one."""
    return ExampleComponent(
        my_simulation_parameters=case.ppdt.simulation_parameters,
        config=fft.sized_example_component_config(),
    )


@pytest.mark.base
def test_region_is_the_weathers_configured_location(prepared_case: PreparedPostProcessingCase) -> None:
    """A run with a Weather is reported under the location that Weather is configured for."""
    components: List[Component] = [
        _non_weather(prepared_case),
        _weather(prepared_case, "Weather", LocationEnum.AACHEN),
    ]

    assert region_of(_data_transfer_reporting_on(prepared_case, components)) == "Aachen"


@pytest.mark.base
def test_region_is_empty_without_a_weather(prepared_case: PreparedPostProcessingCase) -> None:
    """A run with no Weather has no region, and gets the empty string the report falls back to."""
    assert region_of(_data_transfer_reporting_on(prepared_case, [])) == ""
    assert region_of(_data_transfer_reporting_on(prepared_case, [_non_weather(prepared_case)])) == ""


@pytest.mark.base
def test_region_of_several_weathers_names_them_all(prepared_case: PreparedPostProcessingCase) -> None:
    """A district drawing on two stations is reported under both, joined in component order."""
    aachen = _weather(prepared_case, "WeatherOne", LocationEnum.AACHEN)
    madrid = _weather(prepared_case, "WeatherTwo", LocationEnum.MADRID)

    assert region_of(_data_transfer_reporting_on(prepared_case, [aachen, madrid])) == "Aachen / Madrid"
    assert region_of(_data_transfer_reporting_on(prepared_case, [madrid, aachen])) == "Madrid / Aachen"
