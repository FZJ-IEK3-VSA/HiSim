"""Test for simple system setups."""

from pathlib import Path
import shutil
from typing import Iterator

import pytest

from hisim import hisim_main
from hisim.result_path_provider import ResultPathProviderSingleton
from hisim.simulationparameters import SimulationParameters
from hisim import utils

from tests.testing_utils import TestingUtils


REPO_ROOT = Path(__file__).resolve().parent.parent
SIMPLE_SYSTEM_SETUP_ONE_PATH = str(
    REPO_ROOT / "system_setups" / "simple_system_setup_one.py"
)


@pytest.fixture
def isolated_result_directory() -> Iterator[str]:
    """Provide a clean, deterministic, test-scoped result directory and tear it down afterwards.

    ``TestingUtils.get_result_directory`` configures the global
    :class:`ResultPathProviderSingleton` into ``RunMode.TEST`` and returns a path under
    ``<repo>/results/test/<test_name>`` (which is gitignored). The directory is removed
    before the test (so stale artefacts from a previous run cannot mask a regression) and
    again after the test -- also on failure -- so artefacts do not accumulate under
    ``results/``. The singleton is reset to its default ``RunMode.SINGLE`` state on both
    setup and teardown so the TEST-mode configuration cannot leak into sibling tests (e.g.
    ``test_second_system_setup``) that rely on the provider's default behaviour.

    A missing directory on a fresh run is expected and skipped; a directory that exists but
    cannot be removed surfaces a real error instead of being swallowed silently.
    """
    directory = TestingUtils.get_result_directory()
    # The simulator consumes the explicit ``result_directory`` set on the SimulationParameters
    # directly, so the (TEST-mode) provider is not consulted during the run. Reset it so the
    # global state does not leak while the test body runs.
    ResultPathProviderSingleton.reset()
    # Remove stale results from a previous run instead of silently inheriting them.
    if Path(directory).is_dir():
        shutil.rmtree(directory)
    try:
        yield directory
    finally:
        # Restore default global state and remove the directory even if the test failed.
        ResultPathProviderSingleton.reset()
        if Path(directory).is_dir():
            shutil.rmtree(directory)


@pytest.mark.system_setups
@utils.measure_execution_time
def test_first_system_setup(isolated_result_directory: str) -> None:
    """Run the first simple system setup and assert it produced a valid result.

    ``hisim_main.main`` runs the full simulation (time-stepping plus post-processing) and
    returns ``None`` on success; any failure propagates as an exception. Beyond merely
    "not raising", the test pins the concrete outcome: the configured result directory is
    created, the simulator writes its ``finished.flag`` after post-processing completes,
    and the plots-only post-processing emits at least one plot artifact in the configured
    figure format. This turns a silent no-op-pass into a real check that
    ``simple_system_setup_one`` actually produces a simulation result.

    ``one_day_only_with_only_plots`` enables only the line/carpet/single-day/monthly plot
    options (no CSV export, PDF report or KPI computation), so the plot files are the
    authoritative artefacts to assert on for this configuration.
    """
    path = SIMPLE_SYSTEM_SETUP_ONE_PATH

    sim_params = SimulationParameters.one_day_only_with_only_plots(
        year=2021, seconds_per_timestep=60
    )
    # Route results into the isolated, test-scoped directory provided by the fixture.
    sim_params.result_directory = isolated_result_directory

    result = hisim_main.main(path, sim_params)

    # main() returns None once the simulation and post-processing have completed; a
    # failure would have raised before reaching this point.
    assert result is None

    result_dir = Path(sim_params.result_directory)
    assert result_dir.is_dir(), f"Result directory was not created at {result_dir}"

    # The simulator writes finished.flag after post-processing completes successfully.
    finished_flag = result_dir / "finished.flag"
    assert finished_flag.is_file(), f"finished.flag missing in {result_dir}"

    # Plots are written with the SimulationParameters.figure_format extension (PNG by
    # default) into per-component subdirectories, so search recursively. At least one
    # plot file in the configured format must have been produced.
    figure_suffix = sim_params.figure_format.value
    plot_files = list(result_dir.rglob(f"*{figure_suffix}"))
    assert plot_files, f"No {figure_suffix} plot files were produced in {result_dir}"


@pytest.mark.system_setups
@utils.measure_execution_time
def test_second_system_setup():
    """Test second system setup."""
    path = "../system_setups/simple_system_setup_two.py"

    sim_params = SimulationParameters.one_day_only_with_only_plots(
        year=2021, seconds_per_timestep=60
    )
    hisim_main.main(path, sim_params)
