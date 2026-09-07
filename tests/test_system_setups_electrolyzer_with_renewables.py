"""Tests for the electrolyzer with renewables system setup."""

# clean

from pathlib import Path
import shutil
from typing import Iterator

import pytest

from hisim import hisim_main
from hisim.result_path_provider import ResultPathProviderSingleton
from hisim import utils

from tests.functions_for_testing import SetupTestParameters
from tests.testing_utils import TestingUtils


REPO_ROOT: Path = Path(__file__).resolve().parent.parent
ELECTROLYZER_SETUP_PATH: str = str(
    REPO_ROOT / "system_setups" / "electrolyzer_with_renewables.py"
)


@pytest.fixture(name="isolated_result_directory")
def fixture_isolated_result_directory() -> Iterator[str]:
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
def test_electrolyzer_with_renewables(isolated_result_directory: str) -> None:
    """Test the electrolyzer with renewables system setup for a single day, costs and KPIs included.

    Runs the system setup defined in ``system_setups/electrolyzer_with_renewables.py``
    using one-day simulation parameters (year=2021, 60 seconds per timestep) and verifies
    that the simulation not only completes without raising but actually produces its output
    artefacts in the results directory. ``hisim_main.main`` returns ``None`` on success, so
    without explicit assertions a silent no-op would still pass; pinning the result
    directory, the ``finished.flag`` completion marker and the simulation log turns this
    into a real smoke test of the full run.

    The parameters come from :class:`tests.functions_for_testing.SetupTestParameters`, which
    switches on COMPUTE_OPEX, COMPUTE_CAPEX and the two KPI options. That matters here
    specifically: those three run *before* the KPIs in post-processing, and until the
    transformer/rectifier and the electrolyzer had cost models a stock all-options run of this
    setup died in COMPUTE_OPEX before any KPI was computed. The cost tables asserted below are
    what catches that regression -- the log and the flag alone would not, because they are
    written even by a run whose cost stage was never asked to answer.
    """
    path = ELECTROLYZER_SETUP_PATH

    sim_params = SetupTestParameters.one_day_with_kpis(year=2021, seconds_per_timestep=60)
    # Route results into the isolated, test-scoped directory provided by the fixture so
    # that stale artefacts from a previous run cannot mask a regression and so the test
    # cleans up after itself.
    sim_params.result_directory = isolated_result_directory

    # main() returns None once the simulation and post-processing have completed; a
    # failure would have raised before reaching this point.
    hisim_main.main(path, sim_params)

    # The simulator creates the result directory while preparing the run and writes a
    # ``finished.flag`` file at the very end of a successful run (after post-processing),
    # so it is a reliable signal that the electrolyzer setup ran to completion.
    results_dir = Path(sim_params.result_directory)
    assert results_dir.is_dir(), f"Result directory was not created: {results_dir}"
    assert (results_dir / "finished.flag").is_file(), (
        f"finished.flag missing in results directory: {results_dir}"
    )
    # The simulator always writes a simulation log (hisim_simulation.log) via
    # log.logger.setup at the start of run_all_timesteps, so its presence confirms the run
    # produced concrete output artifacts rather than merely not crashing.
    assert (results_dir / "hisim_simulation.log").is_file(), (
        f"hisim_simulation.log missing in results directory: {results_dir}"
    )
    assert any(results_dir.iterdir()), (
        f"Result directory is empty: {results_dir}"
    )
    # The cost stages write one table each, and only if they were reached and answered. Both
    # devices have to appear by name: a component whose cost model went missing again would
    # either raise or drop out of the table, and this is what tells the two apart from a run
    # that merely finished.
    operational_costs = (results_dir / "operational_costs_co2_footprint.csv").read_text(encoding="utf-8")
    investment_costs = (results_dir / "investment_cost_co2_footprint.csv").read_text(encoding="utf-8")
    for table_name, table in (
        ("operational_costs_co2_footprint.csv", operational_costs),
        ("investment_cost_co2_footprint.csv", investment_costs),
    ):
        for component in ("StandardTransformerAndRectifier", "Electrolyzer"):
            assert component in table, f"{component} is missing from {table_name}"
    # WRITE_KPIS_TO_JSON is on, so the KPI stage after the cost stages ran too.
    assert (results_dir / "all_kpis.json").is_file(), (
        f"all_kpis.json missing in results directory: {results_dir}"
    )
