"""The gate of the condensing gas boiler (``assemblies_spec.md`` §13 step 5, D9): the composed file is its twin.

``energy_systems/household_gas_building_sizer.composed.energy_system.yaml`` — the site plus seven imports of
``energy_systems/assemblies/``, among them ``heating/gas_condensing_boiler`` and ``supply/gas_connection`` — built and
renamed must equal ``household_gas_building_sizer.energy_system.yaml``, the recorded twin of the Python setup, outside
exactly G7 and one neutral swap (:mod:`tests.assemblies.twin_gate`): the buffer before the DHW cylinder, which read
nothing from each other. The gas meter needs no swap: its import is numbered between the heat distribution and the
grid, where the twin evaluates it.
"""

from __future__ import annotations

from typing import Dict

import pytest

from hisim.energy_system.assemblies.twins import COMPOSED_TWINS
from tests.assemblies.twin_gate import (
    Run,
    TwinGate,
    check_columns,
    check_components,
    check_kpis,
    check_sequence,
    run_both,
)

GATE = TwinGate(
    COMPOSED_TWINS["household_gas_building_sizer"],
    neutral_swaps=(("DHWStorage", "SimpleHotWaterStorage"),),
    g7_component="Battery",
    g7_line={"pv_peak_power_in_watt": ["PVSystem.pv_peak_power_in_watt"]},
)


@pytest.fixture(scope="module", name="runs")
def fixture_runs(tmp_path_factory: pytest.TempPathFactory) -> Dict[str, Run]:
    """Both files run for one day, each in its own result directory."""
    return run_both(GATE, tmp_path_factory.mktemp("gas_twin_gate"))


@pytest.mark.assemblies
def test_the_composed_file_is_the_twin_outside_the_intended_differences(runs: Dict[str, Run]) -> None:
    """Catches any class, preset, config line, input item, feed tag, weight or dispatch the assemblies get wrong."""
    check_components(GATE, runs)


@pytest.mark.assemblies
def test_the_sequence_is_the_twins_up_to_the_neutral_swap(runs: Dict[str, Run]) -> None:
    """Catches an evaluation order other than the twin's, or a swap of two components that read each other."""
    check_sequence(GATE, runs)


@pytest.mark.assemblies
def test_every_result_column_of_one_day_is_the_twins(runs: Dict[str, Run]) -> None:
    """Catches a composed system that computes anything else than the twin, to the last bit."""
    check_columns(runs)


@pytest.mark.assemblies
def test_every_kpi_of_one_day_is_the_twins(runs: Dict[str, Run]) -> None:
    """Catches a KPI that differs from the twin's under the address rename."""
    check_kpis(runs)
