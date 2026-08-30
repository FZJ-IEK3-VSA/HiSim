"""Tests that an aggregator refuses to be fed the same source output twice.

A dynamic component sums one input per feed, so registering the same flow twice — once by hand in a
setup and once again through the default connections ``connect_automatically=True`` applies — counts
that flow twice and quietly corrupts every total built on it. That is exactly what
``household_gas_solar_thermal`` did with the occupancy's electricity until it was repaired, and the
only visible symptom was a KPI complaining that more electricity came from the grid than the
household consumed in total.

The tests here pin the three halves of the repair: the guard refuses a genuine duplicate and names
both registration sites so the reader knows which one to delete, it keeps accepting the ordinary
case of two different outputs of one source, and the repaired setup satisfies the energy balance
its own post-processing checks.
"""

# clean

import json
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from hisim import hisim_main
from hisim import loadtypes as lt
from hisim import utils
from hisim.components import electricity_meter
from hisim.components.loadprofilegenerator_utsp_connector import UtspLpgConnector
from hisim.dynamic_component import (
    DuplicateDynamicFeedError,
    DynamicComponentConnection,
)
from hisim.postprocessingoptions import PostProcessingOptions
from hisim.simulationparameters import SimulationParameters
from tests.testing_utils import TestingUtils


class DuplicateFeedFixtures:

    """Constants the duplicate-feed tests share, kept out of module scope.

    The tests all build one electricity meter and feed it from a named source, so the source name,
    its outputs and the tag list every household feed carries are written down once here instead of
    being repeated — and quietly drifting apart — in each test.
    """

    #: Instance name of the pretended source component. The add-API takes the source by name, so
    #: no real component has to be constructed for a wiring-level test.
    SOURCE_NAME: str = "UTSPConnector"

    #: The output whose double registration broke ``household_gas_solar_thermal``.
    ELECTRICITY_OUTPUT: str = "ElectricalPowerConsumption"

    #: A second, different output of the same source; feeding both is entirely ordinary.
    SECOND_OUTPUT: str = "WaterConsumption"

    #: The tag list a household electricity feed carries into a meter.
    CONSUMPTION_TAGS: tuple = (lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED,)

    #: The weight every meter feed uses.
    WEIGHT: int = 999


def _build_meter() -> electricity_meter.ElectricityMeter:
    """Builds a bare electricity meter to feed in the wiring-level tests.

    The meter is the aggregator every household setup routes its electricity through and the one
    the real duplicate occurred on, which makes it the honest subject here. Only construction is
    needed: the guard runs when a feed is registered, long before any timestep.

    Returns:
        A meter built with its default config and one-day simulation parameters.
    """
    my_simulation_parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60 * 15)
    return electricity_meter.ElectricityMeter(
        my_simulation_parameters=my_simulation_parameters,
        config=electricity_meter.ElectricityMeterConfig.get_electricity_meter_default_config(),
    )


def _occupancy_default_connection(output: str) -> DynamicComponentConnection:
    """Builds the default connection that ``connect_automatically=True`` would apply for one output.

    Handing this to :meth:`DynamicComponent.connect_with_dynamic_connections_list` is exactly what
    the simulator does when it resolves a target's default connections, so a test using it exercises
    the real automatic-connection path rather than an imitation of it.

    Args:
        output: Field name of the source output the default connection feeds.

    Returns:
        One default connection from the occupancy to whatever aggregator it is applied to.
    """
    return DynamicComponentConnection(
        source_component_class=UtspLpgConnector,
        source_class_name=UtspLpgConnector.get_classname(),
        source_component_field_name=output,
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        source_tags=list(DuplicateFeedFixtures.CONSUMPTION_TAGS),
        source_weight=DuplicateFeedFixtures.WEIGHT,
        source_instance_name=DuplicateFeedFixtures.SOURCE_NAME,
    )


@pytest.mark.base
@utils.measure_execution_time
def test_feeding_the_same_output_twice_raises_and_names_both_sites() -> None:
    """The explicit-then-automatic duplicate is refused, with both sites in the message.

    This is the defect verbatim: a setup hands the occupancy's electricity to the meter and then
    lets the meter's own default connection hand it over a second time. The message has to carry
    the aggregator, the source, the output and both registration sites, because the repair is
    always to delete one of the two and the reader cannot tell which without being told they exist.
    """
    my_electricity_meter = _build_meter()
    my_electricity_meter.add_component_input_and_connect(
        source_object_name=DuplicateFeedFixtures.SOURCE_NAME,
        source_component_output=DuplicateFeedFixtures.ELECTRICITY_OUTPUT,
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        source_tags=list(DuplicateFeedFixtures.CONSUMPTION_TAGS),
        source_weight=DuplicateFeedFixtures.WEIGHT,
    )

    with pytest.raises(DuplicateDynamicFeedError) as raised:
        my_electricity_meter.connect_with_dynamic_connections_list(
            dynamic_component_connections=[
                _occupancy_default_connection(DuplicateFeedFixtures.ELECTRICITY_OUTPUT)
            ]
        )

    message = str(raised.value)
    assert my_electricity_meter.component_name in message
    assert DuplicateFeedFixtures.SOURCE_NAME in message
    assert DuplicateFeedFixtures.ELECTRICITY_OUTPUT in message
    assert "explicitly in the system setup" in message
    assert "again through the default connections" in message
    # The refusal happens before any port is created, so the meter keeps exactly the one feed it
    # legitimately received rather than being left half-wired.
    assert len(my_electricity_meter.my_component_inputs) == 1


@pytest.mark.base
@utils.measure_execution_time
def test_two_different_outputs_of_one_source_are_accepted() -> None:
    """Two different outputs of the same source stay perfectly legal.

    The identity of a feed is the pair (source, output), not the source alone: a heat pump reports
    its space-heating and its hot-water electricity on separate outputs and a meter wants both. If
    the guard keyed on the source it would break most of the fleet, so this pins that it does not.
    """
    my_electricity_meter = _build_meter()
    for output in (DuplicateFeedFixtures.ELECTRICITY_OUTPUT, DuplicateFeedFixtures.SECOND_OUTPUT):
        my_electricity_meter.add_component_input_and_connect(
            source_object_name=DuplicateFeedFixtures.SOURCE_NAME,
            source_component_output=output,
            source_load_type=lt.LoadTypes.ELECTRICITY,
            source_unit=lt.Units.WATT,
            source_tags=list(DuplicateFeedFixtures.CONSUMPTION_TAGS),
            source_weight=DuplicateFeedFixtures.WEIGHT,
        )

    assert len(my_electricity_meter.my_component_inputs) == 2
    fed_outputs = {feed.source_component_field_name for feed in my_electricity_meter.my_component_inputs}
    assert fed_outputs == {DuplicateFeedFixtures.ELECTRICITY_OUTPUT, DuplicateFeedFixtures.SECOND_OUTPUT}


@pytest.mark.base
@utils.measure_execution_time
def test_the_same_output_under_different_tags_is_still_refused() -> None:
    """Relabelling the second copy does not make it a different flow.

    The conservative reading the guard implements: tags say how an aggregator should classify a
    flow and the weight says how it should rank it, but a flow measured once is one flow however
    its two copies are labelled, so summing it twice is wrong either way. Pinning this keeps a
    later "but these carry different tags" from quietly reopening the hole.
    """
    my_electricity_meter = _build_meter()
    my_electricity_meter.add_component_input_and_connect(
        source_object_name=DuplicateFeedFixtures.SOURCE_NAME,
        source_component_output=DuplicateFeedFixtures.ELECTRICITY_OUTPUT,
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        source_tags=list(DuplicateFeedFixtures.CONSUMPTION_TAGS),
        source_weight=DuplicateFeedFixtures.WEIGHT,
    )

    with pytest.raises(DuplicateDynamicFeedError):
        my_electricity_meter.add_component_input_and_connect(
            source_object_name=DuplicateFeedFixtures.SOURCE_NAME,
            source_component_output=DuplicateFeedFixtures.ELECTRICITY_OUTPUT,
            source_load_type=lt.LoadTypes.ELECTRICITY,
            source_unit=lt.Units.WATT,
            source_tags=[lt.InandOutputType.ELECTRICITY_CONSUMPTION],
            source_weight=DuplicateFeedFixtures.WEIGHT - 1,
        )


@pytest.fixture(name="solar_thermal_result_directory")
def fixture_solar_thermal_result_directory() -> Iterator[str]:
    """Yields a fresh, isolated result directory for the solar-thermal balance test.

    Uses :meth:`TestingUtils.get_result_directory` so the run writes into its own deterministic,
    git-ignored directory under the project's results root, and removes it again on teardown so a
    failing run leaves nothing behind for the stray-file guard to find.

    Yields:
        The absolute path of the empty result directory.
    """
    result_directory = TestingUtils.get_result_directory()
    if Path(result_directory).is_dir():
        shutil.rmtree(result_directory)
    Path(result_directory).mkdir(parents=True, exist_ok=True)
    try:
        yield result_directory
    finally:
        shutil.rmtree(result_directory, ignore_errors=True)


@pytest.mark.base
@utils.measure_execution_time
def test_solar_thermal_household_satisfies_its_electricity_balance(solar_thermal_result_directory: str) -> None:
    """The repaired solar-thermal household draws no more from the grid than it consumes.

    While the occupancy's electricity entered the meter twice, the household's total consumption was
    counted once by the KPI layer and the grid import twice, so the relative grid demand exceeded
    100 % and post-processing refused the run outright. Asserting the KPI rather than merely that
    the run completes ties this test to the physical statement the fix restores.

    Args:
        solar_thermal_result_directory: Isolated directory the run writes its results into.
    """
    path = "../system_setups/household_gas_solar_thermal.py"
    my_simulation_parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60 * 15)
    my_simulation_parameters.result_directory = solar_thermal_result_directory
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_KPIS)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.WRITE_KPIS_TO_JSON)

    hisim_main.main(path, my_simulation_parameters)

    all_kpis_path = Path(solar_thermal_result_directory) / "all_kpis.json"
    assert all_kpis_path.is_file(), f"all_kpis.json was not written for {path}"
    with all_kpis_path.open("r", encoding="utf-8") as kpi_file:
        kpi_data = json.load(kpi_file)

    relative_demands = [
        entry["value"]
        for tag_dict in kpi_data.values()
        for kpi_entries in tag_dict.values()
        for entry in kpi_entries.values()
        if entry.get("name") == "Relative electricity demand from grid" and entry.get("value") is not None
    ]
    assert relative_demands, f"no relative grid-demand KPI was produced for {path}: {list(kpi_data)}"
    for relative_demand_in_percent in relative_demands:
        assert relative_demand_in_percent <= 100, (
            f"{path} draws {relative_demand_in_percent} % of its consumption from the grid, "
            "which means a consumer is still counted twice somewhere."
        )
