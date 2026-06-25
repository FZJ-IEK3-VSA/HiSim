""" Tests for the household system setup with advanced heat pump, hot water storage, heat distribution, PV, battery, and EMS. """
# clean
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
HOUSEHOLD_SETUP_PATH = str(
    REPO_ROOT / "system_setups" / "household_with_advanced_hp_hws_hds_pv_battery_ems.py"
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
    setup and teardown so the TEST-mode configuration cannot leak into sibling tests that
    rely on the provider's default behaviour.

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
def test_household_with_advanced_hp_hws_hds_pv_battery_ems(
    isolated_result_directory: str,
) -> None:
    """Single day.

    Runs a one-day simulation of the advanced household configuration (heat pump, hot
    water storage, heat distribution system, PV, battery, and energy management system)
    and asserts that the simulation completed and wrote its output artifacts to the
    results directory.
    """
    path = HOUSEHOLD_SETUP_PATH

    mysimpar = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    # Route results into the isolated, test-scoped directory provided by the fixture so
    # that stale artefacts from a previous run cannot mask a regression and so the test
    # cleans up after itself.
    mysimpar.result_directory = isolated_result_directory

    # hisim_main.main returns None once the simulation and post-processing have completed;
    # a failure would have raised before reaching this point.
    hisim_main.main(path, mysimpar)

    # The simulator populates ``result_directory`` while preparing the run (it is already
    # set above, but confirming it survived the run guards against accidental resets) and
    # writes a ``finished.flag`` file once all timesteps and post-processing are complete.
    results_dir = Path(mysimpar.result_directory)
    assert results_dir.is_dir(), f"Results directory was not created: {results_dir}"
    assert (results_dir / "finished.flag").is_file(), (
        f"finished.flag missing in results directory: {results_dir}"
    )
    # The simulator always writes a simulation log (hisim_simulation.log) via
    # log.logger.setup at the start of run_all_timesteps, so its presence confirms the run
    # produced concrete output artifacts rather than merely not crashing.
    # Note: CSV/JSON exports are gated behind post-processing options
    # (EXPORT_TO_CSV / WRITE_KPIS_TO_JSON / ...) that SimulationParameters.one_day_only
    # does not enable, so they cannot be asserted on here.
    assert (results_dir / "hisim_simulation.log").is_file(), (
        f"hisim_simulation.log missing in results directory: {results_dir}"
    )
