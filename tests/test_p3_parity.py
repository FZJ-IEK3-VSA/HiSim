"""Tests for the P3 migration parity rig: what it covers, what it refuses and what it reports.

TEMPORARY — this module tests the rig of requirements R11 and is deleted together with it in P3's
last PR (R11.8, AC-P3.20).

The rig's own claim is that it can tell a reproduced setup from a broken one, so the two tests that
matter run it for real rather than against a fake: one on a recorded twin as committed, and one on
the same twin with a single configuration value changed. Both use the cheapest setup that reaches
parity today — an electrolyzer fed from a CSV profile, three components, no weather and no load
profile — so a real comparison over a whole simulated week costs seconds rather than minutes.

That setup is also one of the seven whose KPI computation crashes (R11.4), which is why one fixture
answers both spec tests: T-21 asks that such a setup receive a structural verdict rather than an
error, and T-20 asks that an altered file fail and that the report name what moved.

Each test states the failure mode it catches.
"""

# clean

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Optional

import pytest

from hisim.simulationparameters import SimulationParameters
from scripts.p3_parity_check import ParityChecker, discover
from scripts.p3_parity_matrix import MatrixPaths, build_matrix
from scripts.p3_parity_renamings import DeclaredPortRenamings
from scripts.p3_parity_runs import ParityWindows, TripleInputs
from scripts.p3_parity_verdicts import Report, Tolerance, Verdict


class Rig:
    """Where the rig's inputs live and which triple these tests drive it with.

    The paths are read from the repository rather than reconstructed, so that a test asserting
    "the matrix covers every recorded setup" keeps meaning that as setups come and go. The fixture
    setup is named once here because two tests use it, and the reason for the choice — the cheapest
    setup that reaches parity, and one of the seven whose KPI layer crashes — belongs beside the
    name rather than in each of them.
    """

    #: The repository root, from which every other path here is derived.
    ROOT: ClassVar[Path] = Path(__file__).resolve().parents[1]

    #: Where the Python setups live.
    SETUPS: ClassVar[Path] = ROOT / "system_setups"

    #: Where the recorded twins live.
    ENERGY_SYSTEMS: ClassVar[Path] = ROOT / "energy_systems"

    #: The setup both real tests drive the rig with.
    FIXTURE: ClassVar[str] = "electrolyzer_with_renewables"

    #: The window the real tests use. One is enough for them; covering both is the workflow's job.
    WINDOW: ClassVar[str] = "january"

    #: The configuration line the altered-file test moves, and what it moves it to. A transformer
    #: efficiency is chosen because it scales an output directly, so the failure is a moved number
    #: rather than a structural difference the rig would catch without comparing any values.
    ORIGINAL_LINE: ClassVar[str] = "efficiency: 0.95"
    ALTERED_LINE: ClassVar[str] = "efficiency: 0.9"

    #: The component whose result columns the altered value has to move.
    ALTERED_COMPONENT: ClassVar[str] = "StandardTransformerAndRectifier"

    @classmethod
    def triple(cls, work: Path, energy_system: Optional[Path] = None) -> TripleInputs:
        """Builds the triple the real tests run.

        Args:
            work: Where the two runs write; a test's own temporary directory.
            energy_system: The twin to compare against, defaulting to the committed one.

        Returns:
            The triple.
        """
        twin = energy_system or cls.ENERGY_SYSTEMS / f"{cls.FIXTURE}.energy_system.yaml"
        return TripleInputs(
            stem=cls.FIXTURE,
            window=cls.WINDOW,
            setup_path=cls.SETUPS / f"{cls.FIXTURE}.py",
            energy_system_path=twin,
            work_directory=work,
        )

    @classmethod
    def checker(cls) -> ParityChecker:
        """A checker configured exactly as the workflow configures it.

        Returns:
            A checker demanding exact equality and carrying the declared renamings.
        """
        return ParityChecker(Tolerance(), DeclaredPortRenamings.port_renaming())


@pytest.mark.base
def test_one_week_july_is_the_first_week_of_july() -> None:
    """Catches a summer window that is not seven days, or not in July.

    The whole point of the second window is that it measures a cooling device somewhere other than
    the annual minimum, so a set that silently stayed in January would defeat it without failing
    anything else.
    """
    parameters = SimulationParameters.one_week_july(2021, 60)
    assert (parameters.start_date.month, parameters.start_date.day) == (7, 1)
    assert (parameters.end_date.month, parameters.end_date.day) == (7, 8)
    assert parameters.seconds_per_timestep == 60
    assert parameters.timesteps == SimulationParameters.one_week_only(2021, 60).timesteps


@pytest.mark.base
def test_the_matrix_covers_every_recorded_setup_in_both_windows() -> None:
    """Catches a dispatch that quietly covers less than the fleet.

    R11.5 asks for both windows on every triple and R11.7 for one table covering all of them, so a
    matrix that lost a setup or a window would make the rig's table an incomplete claim. The
    windows are asserted against the runner's own list because the matrix script may not import
    HiSim and therefore carries a second copy of them.
    """
    assert tuple(MatrixPaths.WINDOWS) == ParityWindows.names()
    covered = discover(None)
    assert covered, "no setup has a recorded twin, so the rig would cover nothing"
    include = build_matrix()["include"]
    assert len(include) == len(covered) * len(ParityWindows.names())
    for stem in covered:
        windows = {entry["window"] for entry in include if entry["setup"] == stem}
        assert windows == set(ParityWindows.names())


@pytest.mark.base
def test_the_renaming_table_declares_one_meaning_per_legacy_port() -> None:
    """Catches a renaming table that claims one legacy port means two different declarative ports.

    The table is the rig's only licence to call two differently named ports the same wire, so a
    contradiction inside it would silently decide which of two claims wins. The pass-through case
    is asserted too, because a port nobody declared has to keep failing literally (C-P3.2).
    """
    pairs = DeclaredPortRenamings.pairs()
    assert pairs, "the table declares nothing, so every aggregator port would fail literally"
    assert pairs[("ElectricityMeter", "Input_PVSystem_ElectricityOutput_0")] == "ElectricityOutputFromPVSystem"
    assert (
        pairs[("L2EMSElectricityController", "LoadingPowerInputForBattery_Output15")]
        == "DispatchToBattery_LoadingPowerInput"
    )
    renaming = DeclaredPortRenamings.port_renaming()
    assert renaming.rename("ElectricityMeter", "SomethingNobodyDeclared") == "SomethingNobodyDeclared"


@pytest.mark.base
def test_a_kpi_broken_setup_gets_a_structural_verdict(tmp_path: Path) -> None:
    """Catches a rig that turns a broken KPI layer into a parity failure or an exception (T-21).

    Seven in-scope setups crash inside KPI computation after the simulation has finished. R11.4
    requires them to be covered anyway: the first two comparisons need no KPIs, so the triple must
    still report on the wiring and on every result column, and the third stage must say it was
    unavailable rather than failing or raising.
    """
    verdict = Rig.checker().check(Rig.triple(tmp_path)).verdict
    assert verdict.wiring == Verdict.OK
    assert verdict.results == Verdict.OK
    assert verdict.kpis == Verdict.UNAVAILABLE
    assert verdict.passed
    assert any("KPI stage unavailable" in note for note in verdict.notes)


@pytest.mark.base
def test_an_altered_recorded_file_fails_and_names_what_moved(tmp_path: Path) -> None:
    """Catches a rig that cannot tell a reproduced setup from a changed one (T-20).

    AC-P3.18 asks that changing one configuration value in a recorded file make its triple fail and
    that the report name the columns or KPIs that moved. The comparison is exact, so nothing about
    the size of the change can hide it; what this test guards is that the failure is *reported* in
    terms a reader can act on rather than as a bare non-zero exit.
    """
    committed = (Rig.ENERGY_SYSTEMS / f"{Rig.FIXTURE}.energy_system.yaml").read_text(encoding="utf-8")
    assert Rig.ORIGINAL_LINE in committed, "the fixture no longer carries the value this test moves"
    altered = tmp_path / f"{Rig.FIXTURE}.energy_system.yaml"
    altered.write_text(committed.replace(Rig.ORIGINAL_LINE, Rig.ALTERED_LINE, 1), encoding="utf-8")

    verdict = Rig.checker().check(Rig.triple(tmp_path / "work", altered)).verdict
    assert not verdict.passed
    assert verdict.wiring == Verdict.OK, "changing a value must not disturb the wiring"
    assert verdict.results == Verdict.FAILED
    moved = [difference.column for difference in verdict.differences.columns]
    assert moved, "the report named no column, so nobody could act on the failure"
    assert any(Rig.ALTERED_COMPONENT in column for column in moved)
    report = Report.failure(verdict, Tolerance())
    assert "result columns that differ" in report
    assert Rig.ALTERED_COMPONENT in report
