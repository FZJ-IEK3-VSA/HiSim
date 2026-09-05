"""Tests for the P3 migration parity rig: what it covers, what it refuses and what it reports.

TEMPORARY — this module tests the rig of requirements R11 and is deleted together with it in P3's
last PR (R11.8, AC-P3.20).

The rig's own claim is that it can tell a reproduced setup from a changed one, so the tests that
matter run it for real rather than against a fake: one on a recorded twin as committed, one on the
same twin with a single configuration value changed, and one on the twin with a component renamed.
All use the cheapest setup that reaches parity today — an electrolyzer fed from a CSV profile,
four components, no weather and no load profile — so a real comparison over a whole simulated week
costs seconds rather than minutes.

That setup is also one of the seven whose KPI computation crashes (R11.4), which is why one fixture
answers both spec tests: T-21 asks that such a setup receive a structural verdict rather than an
error, and T-20 asks that an altered file fail and that the report name what moved.

Each test states the failure mode it catches.
"""

# clean

from __future__ import annotations

import math
from pathlib import Path
from typing import ClassVar, Optional

import pandas as pd
import pytest

from hisim.energy_system.parity import PortRenaming, ResultComparison
from hisim.simulationparameters import SimulationParameters

# The rig's shared names are imported through the checker's namespace on purpose: the scripts are
# importable both as ``p3_parity_*`` and as ``scripts.p3_parity_*``, and those are two separate
# module instances. With plain strings that was invisible; with the ``Verdict`` enum, comparing a
# member from one copy against a member from the other is always False, so the test must hold
# exactly the classes the checker itself resolved.
from scripts.p3_parity_check import (
    DeclaredPortRenamings,
    ParityChecker,
    ParityWindows,
    Report,
    RunOutcome,
    Tolerance,
    TripleInputs,
    TripleVerdict,
    Verdict,
    discover,
)
from scripts.p3_parity_matrix import MatrixPaths, build_matrix


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

    #: What the structural-change test renames that component to. Renaming is the structural
    #: mutation of choice because the twin stays buildable — the wire references are renamed with
    #: it — so the failure the test asserts comes from the wiring comparison, not from a crash.
    RENAMED_COMPONENT: ClassVar[str] = "RenamedTransformerAndRectifier"

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
def test_an_indicator_named_after_a_port_is_translated_too() -> None:
    """Catches the third comparison reporting a name the first two have already accounted for.

    A handful of key-performance indicators are named after a port rather than after a quantity —
    an energy management system publishes one "Priority for <port>" per participant — so their keys
    carry the very names the table exists to translate. Untranslated they read as two disjoint
    indicator sets with identical values, which hides whatever real difference might be in the same
    report. Everything else in a key has to survive untouched, or the translation would be
    inventing differences instead of removing them.
    """
    renaming = DeclaredPortRenamings.port_renaming()

    translated = renaming.apply_to_kpis(
        {
            "BUI1.Energy Management System.Priority for Input_Battery_AcBatteryPowerUsed_6": 3,
            "BUI1.Energy Management System.Priority for Input_PVSystem_ElectricityOutput_2": 1,
            "BUI1.Building.Total heating demand": 42.0,
        }
    )

    assert translated == {
        "BUI1.Energy Management System.Priority for AcBatteryPowerUsedFromBattery": 3,
        "BUI1.Energy Management System.Priority for ElectricityOutputFromPVSystem": 1,
        "BUI1.Building.Total heating demand": 42.0,
    }


@pytest.mark.base
def test_an_indicator_quoting_an_undeclared_port_still_fails_literally() -> None:
    """Catches a translation that guesses, which would absorb exactly the differences it must find.

    The table is a list of claims somebody made, and a port nobody declared has to keep comparing
    literally (C-P3.2). A name spelled inside a longer one must not be rewritten either, because
    the aggregator input names differ only by their trailing index.
    """
    renaming = DeclaredPortRenamings.port_renaming()

    translated = renaming.apply_to_kpis(
        {
            "BUI1.Energy Management System.Priority for Input_Nobody_Declared_This_3": 0,
            "BUI1.Energy Management System.Priority for Input_Battery_AcBatteryPowerUsed_60": 1,
        }
    )

    assert set(translated) == {
        "BUI1.Energy Management System.Priority for Input_Nobody_Declared_This_3",
        "BUI1.Energy Management System.Priority for Input_Battery_AcBatteryPowerUsed_60",
    }


@pytest.mark.base
def test_a_kpi_broken_setup_gets_a_structural_verdict(tmp_path: Path) -> None:
    """Catches a rig that turns a broken KPI layer into an exception instead of a verdict (T-21).

    Seven in-scope setups crash inside KPI computation after the simulation has finished. R11.4
    requires them to be covered anyway: the first two comparisons need no KPIs, so the triple must
    still report on the wiring and on every result column, and the third stage must say it was
    unavailable rather than raising. Since the 2026-09-05 amendment an unavailable stage fails the
    triple — a new KPI regression must not read green — so the verdict is a named failure, never
    an error and never a pass.
    """
    verdict = Rig.checker().check(Rig.triple(tmp_path)).verdict
    assert verdict.wiring == Verdict.OK
    assert verdict.results == Verdict.OK
    assert verdict.kpis == Verdict.UNAVAILABLE
    assert not verdict.passed
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


@pytest.mark.base
def test_a_structurally_changed_recorded_file_fails_the_wiring_comparison(tmp_path: Path) -> None:
    """Catches a wiring comparison that cannot tell a rewired system from a reproduced one.

    The value-change test proves the numeric comparison; nothing else proves the *structural* one,
    and a wiring diff that silently reported everything as identical would leave every test green
    while defeating the rig's first comparison. Renaming a component (references included) keeps
    the twin buildable, so the run succeeds and the difference can only be caught by the wiring
    stage — which must fail the triple and name the component in the diff.
    """
    committed = (Rig.ENERGY_SYSTEMS / f"{Rig.FIXTURE}.energy_system.yaml").read_text(encoding="utf-8")
    assert Rig.ALTERED_COMPONENT in committed, "the fixture no longer carries the component this test renames"
    altered = tmp_path / f"{Rig.FIXTURE}.energy_system.yaml"
    altered.write_text(committed.replace(Rig.ALTERED_COMPONENT, Rig.RENAMED_COMPONENT), encoding="utf-8")

    verdict = Rig.checker().check(Rig.triple(tmp_path / "work", altered)).verdict
    assert verdict.wiring == Verdict.FAILED
    assert not verdict.passed
    assert Rig.RENAMED_COMPONENT in verdict.wire_diff


@pytest.mark.base
def test_a_column_nan_on_one_side_only_is_a_structural_problem() -> None:
    """Catches the exact comparison silently passing a run that produced NaN where the other did not.

    NaN propagates through every arithmetic reduction as "no deviation" — ``max(0.0, nan)`` keeps
    0.0 — so before this was a structural problem, a column NaN on one side and finite on the other
    was reported as zero deviation and the triple passed. That is the one difference an exact rig
    must never absorb, and no tolerance may absorb it either.
    """
    expected = pd.DataFrame({"X": [1.0, float("nan"), 3.0]})
    actual = pd.DataFrame({"X": [1.0, 5.0, 3.0]})

    comparison = ResultComparison.between(expected, actual)

    assert any("NaN on one side only" in problem for problem in comparison.structural_problems)
    assert not comparison.is_identical()


@pytest.mark.base
def test_identical_nan_columns_compare_equal() -> None:
    """Catches the comparison inventing a difference out of two identical NaN columns.

    A value both runs failed to produce is the same value: a recorded twin that reproduces its
    setup NaN for NaN must pass, in the frame comparison and under the exact KPI tolerance alike,
    or every setup with an undefined indicator would fail parity while being byte-identical.
    """
    frame = pd.DataFrame({"X": [float("nan"), 2.0]})

    comparison = ResultComparison.between(frame, frame.copy())

    assert not comparison.structural_problems
    assert comparison.max_absolute_deviation == 0.0
    assert Tolerance().accepts(float("nan"), float("nan"))
    assert not Tolerance().accepts(float("nan"), 1.0)


@pytest.mark.base
def test_a_kpi_nan_on_both_sides_passes_the_exact_comparison() -> None:
    """Catches the third comparison failing a KPI that both runs reproduced as NaN.

    ``nan != nan`` made the exact branch report such a pair as a difference while any non-zero
    tolerance accepted it (``equal_nan=True``), so the two modes disagreed about identical values.
    The rig's single notion of exact equality treats them as equal in both.
    """
    verdict = TripleVerdict(stem="s", window="january")

    Rig.checker().compare_kpis(
        RunOutcome(kpis={"BUI1.Battery.State of charge": float("nan")}),
        RunOutcome(kpis={"BUI1.Battery.State of charge": float("nan")}),
        verdict,
    )

    assert verdict.kpis == Verdict.OK


@pytest.mark.base
def test_a_differing_column_is_judged_at_every_row() -> None:
    """Catches the failure report probing the tolerance only at the worst-absolute row.

    Under a relative tolerance the out-of-tolerance row can be a low-magnitude one whose absolute
    deviation is unremarkable: here the large row is within one permille while the small row is off
    by nine percent. Judged at the worst-absolute row alone, the column would be skipped and the
    triple read TOLERATED with a worst-relative note far above its own allowance.
    """
    checker = ParityChecker(Tolerance(relative=0.01), DeclaredPortRenamings.port_renaming())
    expected = pd.DataFrame({"C": [1000.0, 1.0]})
    actual = pd.DataFrame({"C": [1001.0, 1.1]})

    columns = checker.differing_columns(expected, actual)

    assert [difference.column for difference in columns] == ["C"]
    assert columns[0].relative > 0.01
    assert math.isclose(columns[0].expected, 1.0)


@pytest.mark.base
def test_an_unavailable_stage_fails_its_triple_and_a_negative_tolerance_is_refused() -> None:
    """Catches the two verdict rules the 2026-09-05 review round pinned down.

    An UNAVAILABLE stage fails its triple (R11.4 as amended): a new KPI regression must not read
    green just because the crash also made the comparison impossible. And a negative tolerance is
    a typo, not a stricter run — accepted silently it would behave like exact equality while the
    report prints the nonsense value as if it had been measured against.
    """
    verdict = TripleVerdict(
        stem="s", window="january", wiring=Verdict.OK, results=Verdict.OK, kpis=Verdict.UNAVAILABLE
    )
    assert not verdict.passed
    with pytest.raises(ValueError):
        Tolerance(relative=-1.0)


@pytest.mark.base
def test_two_ports_renamed_onto_one_column_are_refused() -> None:
    """Catches a renaming table collapsing two result columns into one without an error.

    The KPI translation already refuses to map two indicators onto one key; the frame translation
    only refused a rename landing on an *untouched* column, so two renames landing on the same new
    name produced a frame with duplicate column labels that the comparison then mis-indexed. Both
    halves of the table now enforce the same invariant.
    """
    frame = pd.DataFrame({"Agg - PortA [Power - W]": [1.0], "Agg - PortB [Power - W]": [2.0]})
    renaming = PortRenaming(renamings={("Agg", "PortA"): "Same", ("Agg", "PortB"): "Same"})

    with pytest.raises(ValueError, match="onto"):
        renaming.apply_to_results(frame)
