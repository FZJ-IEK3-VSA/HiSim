""" Tests for the basic household system setup with simulation params and without. """
from pathlib import Path
import shutil

import pytest

from hisim import hisim_main
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim import utils
from tests.testing_utils import TestingUtils


BASIC_HOUSEHOLD_PATH: str = str(Path(__file__).resolve().parent.parent / "system_setups" / "basic_household.py")


@pytest.mark.extendedbase
@utils.measure_execution_time
def test_basic_household_with_simu_params() -> None:
    """Run the basic household setup for a single day with explicit SimulationParameters.

    Verifies that HiSim creates the result directory and writes the expected
    artifacts (finished.flag, component_connections.json, hisim_simulation.log)
    when called with a one-day SimulationParameters instance.
    """
    simulation_parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60 * 60)
    simulation_parameters.result_directory = TestingUtils.get_result_directory()
    shutil.rmtree(simulation_parameters.result_directory, ignore_errors=True)
    # hisim_main.main runs the simulation for its side effects and returns None.
    hisim_main.main(BASIC_HOUSEHOLD_PATH, simulation_parameters)
    result_directory = Path(simulation_parameters.result_directory)
    # The simulation should have (re)created its result directory.
    assert result_directory.is_dir()
    # HiSim writes "finished.flag" at the end of a successful run as the canonical
    # completion marker, and "component_connections.json" while wiring up components.
    assert (result_directory / "finished.flag").is_file()
    # "component_connections.json" is written while wiring up components; pinning
    # the specific file is more precise than globbing for any *.json.
    assert (result_directory / "component_connections.json").is_file()
    # The run should have produced at least one log artifact.
    assert (result_directory / "hisim_simulation.log").is_file()
    log.information(str(Path.cwd()))


@pytest.mark.extendedbase
@utils.measure_execution_time
def test_basic_household_without_simu_params(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the basic household setup without explicit SimulationParameters.

    Invokes HiSim with ``simulation_parameters=None`` so the default factory
    path is exercised. ``SimulationParameters.full_year_with_only_plots`` is
    monkeypatched to a fast one-day configuration, then the test verifies that
    the result directory is (re)created and the expected artifacts
    (finished.flag, component_connections.json, hisim_simulation.log) are
    written.

    Args:
        monkeypatch: Pytest fixture used to patch the default
            SimulationParameters factory onto a short one-day run.
    """

    # Capture the result directory that the patched factory hands to HiSim so the
    # assertions below can verify the artifacts landed where the test expects.
    captured_result_directory: list[str] = []

    def fast_default_parameters(
        cls: type[SimulationParameters],
        year: int,
        seconds_per_timestep: int,
    ) -> SimulationParameters:
        """Return a short simulation for this test while still exercising the no-params call path."""
        simulation_parameters = cls.one_day_only(year=year, seconds_per_timestep=max(seconds_per_timestep, 60 * 60))
        simulation_parameters.result_directory = TestingUtils.get_result_directory()
        shutil.rmtree(simulation_parameters.result_directory, ignore_errors=True)
        captured_result_directory.append(simulation_parameters.result_directory)
        return simulation_parameters

    monkeypatch.setattr(
        SimulationParameters,
        "full_year_with_only_plots",
        classmethod(fast_default_parameters),
    )
    simulation_parameters = None
    # hisim_main.main runs the simulation for its side effects and returns None.
    hisim_main.main(BASIC_HOUSEHOLD_PATH, simulation_parameters)
    assert captured_result_directory, "Patched SimulationParameters factory was never called."
    result_directory = Path(captured_result_directory[-1])
    # The simulation should have (re)created its result directory.
    assert result_directory.is_dir()
    # HiSim writes "finished.flag" at the end of a successful run as the canonical
    # completion marker, and "component_connections.json" while wiring up components.
    assert (result_directory / "finished.flag").is_file()
    # "component_connections.json" is written while wiring up components; pinning
    # the specific file is more precise than globbing for any *.json.
    assert (result_directory / "component_connections.json").is_file()
    # The run should have produced at least one log artifact.
    assert (result_directory / "hisim_simulation.log").is_file()
    log.information(str(Path.cwd()))
