"""Test for system setup dynamic component."""

from pathlib import Path
import shutil
from typing import Iterator

import pandas as pd
import pytest

from hisim import hisim_main
from hisim import utils
from hisim.result_path_provider import ResultPathProviderSingleton

from tests.functions_for_testing import SetupTestParameters
from tests.testing_utils import TestingUtils


REPO_ROOT: Path = Path(__file__).resolve().parent.parent
DYNAMIC_COMPONENTS_SETUP_PATH: str = str(REPO_ROOT / "system_setups" / "dynamic_components.py")
#: The runtime names the setup gives its two CHPs, which are also how they are written into the
#: "Component" column of both cost tables.
CHP_COMPONENT_NAMES: tuple[str, ...] = ("CHP1", "CHP2")


@pytest.fixture(name="isolated_result_directory")
def fixture_isolated_result_directory() -> Iterator[str]:
    """Provide a clean, deterministic, test-scoped result directory and tear it down afterwards.

    ``TestingUtils.get_result_directory`` configures the global
    :class:`ResultPathProviderSingleton` into ``RunMode.TEST`` and returns a path under
    ``<repo>/results/test/<test_name>`` (which is gitignored). The directory is removed before the
    test, so stale artefacts from a previous run cannot mask a regression, and again afterwards --
    also on failure -- so artefacts do not accumulate. The singleton is reset on both sides so the
    TEST-mode configuration cannot leak into sibling tests.

    Yields:
        str: the result directory the run writes into.
    """
    directory = TestingUtils.get_result_directory()
    # The simulator consumes the explicit ``result_directory`` set on the SimulationParameters
    # directly, so the (TEST-mode) provider is not consulted during the run. Reset it so the
    # global state does not leak while the test body runs.
    ResultPathProviderSingleton.reset()
    if Path(directory).is_dir():
        shutil.rmtree(directory)
    try:
        yield directory
    finally:
        ResultPathProviderSingleton.reset()
        if Path(directory).is_dir():
            shutil.rmtree(directory)


def _cost_row(table: pd.DataFrame, component_name: str, table_name: str) -> pd.Series:
    """Return the one row of a cost table that belongs to ``component_name``.

    The cost tables carry the component's runtime name -- and nothing else -- in their leading
    "Component" column, so the row is matched on equality rather than on a substring: the two CHPs
    are named CHP1 and CHP2, and a substring match on "CHP" would accept either of them for the
    other. The separator and total rows the writer appends carry no component name and simply do
    not match.

    Args:
        table: the parsed cost table.
        component_name: the runtime component name whose row is wanted.
        table_name: the file the table was read from, for the failure message.

    Returns:
        pd.Series: the single matching row, indexed by the table's column headers.
    """
    matching = table[table["Component"].astype(str).str.strip() == component_name]
    assert len(matching) == 1, (
        f"Expected exactly one {component_name} row in {table_name}, found {len(matching)}"
    )
    return matching.iloc[0]


@pytest.mark.extendedbase
@utils.measure_execution_time
def test_dynamic_components_system_setup(isolated_result_directory: str) -> None:
    """Test dynamic components system setup for a single day, costs and KPIs included.

    Runs the dynamic-components setup for one day and verifies that the simulation runs to
    completion and writes its outputs.

    The parameters come from :class:`tests.functions_for_testing.SetupTestParameters`, which
    switches on COMPUTE_OPEX, COMPUTE_CAPEX and the two KPI options. That matters here
    specifically: those three run *before* the KPIs in post-processing, and until the CHP had a
    cost model a stock all-options run of this setup died in COMPUTE_OPEX before any KPI was
    computed. The cost tables asserted below are what catches that regression -- the log and the
    flag alone would not, because they are written even by a run whose cost stage was never asked
    to answer.

    Args:
        isolated_result_directory: the test-scoped result directory from the fixture.
    """
    sim_params = SetupTestParameters.one_day_with_kpis(year=2021, seconds_per_timestep=60)
    # Route results into the isolated, test-scoped directory provided by the fixture so that
    # stale artefacts from a previous run cannot mask a regression and so the test cleans up
    # after itself.
    sim_params.result_directory = isolated_result_directory

    hisim_main.main(DYNAMIC_COMPONENTS_SETUP_PATH, sim_params)

    # `finished.flag` is written at the very end of a successful simulation run (see
    # Simulator.run_all_timesteps), so it is a reliable signal that the setup ran to completion.
    results_dir = Path(sim_params.result_directory)
    assert results_dir.is_dir(), f"Result directory was not created: {results_dir}"
    assert (results_dir / "finished.flag").is_file(), (
        f"finished.flag missing in results directory: {results_dir}"
    )
    assert (results_dir / "hisim_simulation.log").is_file(), (
        f"hisim_simulation.log missing in results directory: {results_dir}"
    )
    # The cost stages write one table each (semicolon-separated, one header row, the component
    # name in the leading "Component" column), and only if they were reached and answered. Both
    # CHPs have to appear by name and with an investment above zero: a component whose cost model
    # went missing again would either raise or drop out of the table, and one that answered zero
    # would understate the system total while still looking like an answer.
    operational_costs = pd.read_csv(results_dir / "operational_costs_co2_footprint.csv", sep=";")
    investment_costs = pd.read_csv(results_dir / "investment_cost_co2_footprint.csv", sep=";")
    for component in CHP_COMPONENT_NAMES:
        capex_row = _cost_row(investment_costs, component, "investment_cost_co2_footprint.csv")
        assert capex_row["Investment [EUR]"] > 0.0, (
            f"{component} reports no investment in investment_cost_co2_footprint.csv"
        )
        assert capex_row["Device CO2-footprint [kg]"] > 0.0, (
            f"{component} reports no device CO2 in investment_cost_co2_footprint.csv"
        )
        # The operating cost is asserted as a row that exists and is not negative rather than as a
        # positive number: whether these two CHPs are dispatched at all on the simulated day is the
        # energy management's decision, and a day on which they stayed off is a legitimate zero.
        opex_row = _cost_row(operational_costs, component, "operational_costs_co2_footprint.csv")
        assert opex_row["Costs of energy consumption [EUR]"] >= 0.0, (
            f"{component} reports a negative energy cost in operational_costs_co2_footprint.csv"
        )
        assert opex_row["Maintenance costs for simulated period [EUR]"] > 0.0, (
            f"{component} reports no maintenance cost in operational_costs_co2_footprint.csv"
        )
    # WRITE_KPIS_TO_JSON is on, so the KPI stage after the cost stages ran too.
    assert (results_dir / "all_kpis.json").is_file(), (
        f"all_kpis.json missing in results directory: {results_dir}"
    )
