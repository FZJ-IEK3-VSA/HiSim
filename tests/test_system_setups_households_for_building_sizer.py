""" Tests for the household building-sizer system setups.

Each setup is run for a single day with the post-processing options the building
sizer relies on (OpEx/CapEx/KPI computation and writing the KPIs to JSON). Beyond
confirming that the simulation does not raise, every test also asserts that the
run actually produced its result artefacts in the result directory: the
``finished.flag`` completion marker and at least one ``*kpi*.json`` file written
by ``PostProcessingOptions.WRITE_KPIS_TO_JSON``.
"""
# clean
import os
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from hisim import hisim_main
from hisim import log
from hisim import utils
from hisim.postprocessingoptions import PostProcessingOptions
from hisim.simulationparameters import SimulationParameters
from tests.testing_utils import TestingUtils


def _make_building_sizer_simulation_parameters(result_directory: str) -> SimulationParameters:
    """Build one-day simulation parameters with the building-sizer post-processing options.

    The option set matches what the building sizer expects: scenario-evaluation
    outputs, OpEx/CapEx/KPI computation and writing the KPIs to JSON. Results are
    written to ``result_directory`` so each test can verify its own artefacts in
    isolation.
    """
    my_simulation_parameters = SimulationParameters.one_day_only(
        year=2024, seconds_per_timestep=60 * 15
    )
    my_simulation_parameters.result_directory = result_directory
    my_simulation_parameters.post_processing_options.append(
        PostProcessingOptions.PREPARE_OUTPUTS_FOR_SCENARIO_EVALUATION
    )
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_OPEX)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_CAPEX)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_KPIS)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.WRITE_KPIS_TO_JSON)
    return my_simulation_parameters


def _assert_kpi_artefacts_written(result_directory: str, path: str) -> None:
    """Assert the run produced its result artefacts (finished flag + KPI JSON)."""
    results_dir = Path(result_directory)
    assert results_dir.is_dir(), f"results directory was not created for {path}"
    assert (results_dir / "finished.flag").is_file(), (
        f"simulation did not write finished.flag for {path}"
    )
    kpi_files = list(results_dir.rglob("*kpi*.json"))
    assert len(kpi_files) >= 1, f"no KPI JSON file written for {path}"


@pytest.fixture
def building_sizer_result_directory() -> Iterator[str]:
    """Yield a fresh, isolated result directory for a building-sizer test.

    Uses :func:`TestingUtils.get_result_directory` so each test gets its own
    ``results/test/<test_name>`` directory (deterministic, git-ignored and inside
    the project's results root). The directory is removed again on teardown so no
    artefacts are left behind even when a test fails.
    """
    result_directory = TestingUtils.get_result_directory()
    if Path(result_directory).is_dir():
        shutil.rmtree(result_directory)
    Path(result_directory).mkdir(parents=True, exist_ok=True)
    try:
        yield result_directory
    finally:
        shutil.rmtree(result_directory, ignore_errors=True)


@pytest.mark.system_setups
@utils.measure_execution_time
def test_household_gas(building_sizer_result_directory: str) -> None:
    """Test household gas building sizer setup for a single day."""
    path = "../system_setups/household_gas_building_sizer.py"
    hisim_main.main(path, _make_building_sizer_simulation_parameters(building_sizer_result_directory))
    log.information(os.getcwd())
    _assert_kpi_artefacts_written(building_sizer_result_directory, path)


@pytest.mark.system_setups
@utils.measure_execution_time
def test_household_oil(building_sizer_result_directory: str) -> None:
    """Test household oil building sizer setup for a single day."""
    path = "../system_setups/household_oil_building_sizer.py"
    hisim_main.main(path, _make_building_sizer_simulation_parameters(building_sizer_result_directory))
    log.information(os.getcwd())
    _assert_kpi_artefacts_written(building_sizer_result_directory, path)


@pytest.mark.system_setups
@utils.measure_execution_time
def test_household_heatpump(building_sizer_result_directory: str) -> None:
    """Test household heat pump building sizer setup for a single day."""
    path = "../system_setups/household_heatpump_building_sizer.py"
    hisim_main.main(path, _make_building_sizer_simulation_parameters(building_sizer_result_directory))
    log.information(os.getcwd())
    _assert_kpi_artefacts_written(building_sizer_result_directory, path)


@pytest.mark.system_setups
@utils.measure_execution_time
def test_household_pellet_heating(building_sizer_result_directory: str) -> None:
    """Test household pellet heating building sizer setup for a single day."""
    path = "../system_setups/household_pellets_building_sizer.py"
    hisim_main.main(path, _make_building_sizer_simulation_parameters(building_sizer_result_directory))
    log.information(os.getcwd())
    _assert_kpi_artefacts_written(building_sizer_result_directory, path)


@pytest.mark.system_setups
@utils.measure_execution_time
def test_household_district_heating(building_sizer_result_directory: str) -> None:
    """Test household district heating building sizer setup for a single day."""
    path = "../system_setups/household_district_heating_building_sizer.py"
    hisim_main.main(path, _make_building_sizer_simulation_parameters(building_sizer_result_directory))
    log.information(os.getcwd())
    _assert_kpi_artefacts_written(building_sizer_result_directory, path)


@pytest.mark.system_setups
@utils.measure_execution_time
def test_household_wood_chips_heating(building_sizer_result_directory: str) -> None:
    """Test household wood chips heating building sizer setup for a single day."""
    path = "../system_setups/household_wood_chips_building_sizer.py"
    hisim_main.main(path, _make_building_sizer_simulation_parameters(building_sizer_result_directory))
    log.information(os.getcwd())
    _assert_kpi_artefacts_written(building_sizer_result_directory, path)


@pytest.mark.system_setups
@utils.measure_execution_time
def test_household_hydrogen_heating(building_sizer_result_directory: str) -> None:
    """Test household hydrogen boiler building sizer setup for a single day."""
    path = "../system_setups/household_hydrogen_boiler_building_sizer.py"
    hisim_main.main(path, _make_building_sizer_simulation_parameters(building_sizer_result_directory))
    log.information(os.getcwd())
    _assert_kpi_artefacts_written(building_sizer_result_directory, path)


@pytest.mark.system_setups
@utils.measure_execution_time
def test_household_electric_heating(building_sizer_result_directory: str) -> None:
    """Test household electric heating building sizer setup for a single day."""
    path = "../system_setups/household_electric_heating_building_sizer.py"
    hisim_main.main(path, _make_building_sizer_simulation_parameters(building_sizer_result_directory))
    log.information(os.getcwd())
    _assert_kpi_artefacts_written(building_sizer_result_directory, path)


@pytest.mark.system_setups
@utils.measure_execution_time
def test_household_gas_solar_thermal_heating(building_sizer_result_directory: str) -> None:
    """Test household gas with solar thermal building sizer setup for a single day."""
    path = "../system_setups/household_gas_solar_thermal_building_sizer.py"
    hisim_main.main(path, _make_building_sizer_simulation_parameters(building_sizer_result_directory))
    log.information(os.getcwd())
    _assert_kpi_artefacts_written(building_sizer_result_directory, path)


@pytest.mark.system_setups
@utils.measure_execution_time
def test_household_heatpump_solar_thermal_heating(building_sizer_result_directory: str) -> None:
    """Test household heat pump with solar thermal building sizer setup for a single day."""
    path = "../system_setups/household_heatpump_solar_thermal_building_sizer.py"
    hisim_main.main(path, _make_building_sizer_simulation_parameters(building_sizer_result_directory))
    log.information(os.getcwd())
    _assert_kpi_artefacts_written(building_sizer_result_directory, path)


@pytest.mark.system_setups
@utils.measure_execution_time
def test_household_heatpump_car(building_sizer_result_directory: str) -> None:
    """Test household heat pump with EV building sizer setup for a single day."""
    path = "../system_setups/household_heatpump_car_building_sizer.py"
    hisim_main.main(path, _make_building_sizer_simulation_parameters(building_sizer_result_directory))
    log.information(os.getcwd())
    _assert_kpi_artefacts_written(building_sizer_result_directory, path)
