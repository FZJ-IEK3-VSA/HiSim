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

import json
import math
from pathlib import Path
from typing import ClassVar, Optional, Set, Tuple

import pandas as pd
import pytest
import yaml

from hisim.config.channels import ResolvedDynamicConnection
from hisim.energy_system.parity import WiringParityHarness, WiringSnapshot
from hisim.simulationparameters import SimulationParameters
from hisim.simulator import Simulator

# The rig's shared names are imported through the checker's namespace on purpose: the scripts are
# importable both as ``p3_parity_*`` and as ``scripts.p3_parity_*``, and those are two separate
# module instances. With plain strings that was invisible; with the ``Verdict`` enum, comparing a
# member from one copy against a member from the other is always False, so the test must hold
# exactly the classes the checker itself resolved.
from scripts.p3_parity_check import (
    DeclaredPortRenamings,
    ParityChecker,
    ParitySide,
    ParityWindows,
    Report,
    RunOutcome,
    Tolerance,
    TripleInputs,
    TripleVerdict,
    Verdict,
    discover,
)
from scripts.p3_parity_check import main as parity_main
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

    #: The setup the staleness canary drives. It is the only in-scope setup that steers several
    #: participants through one energy manager and needs no load profile from the LoadProfileGenerator,
    #: so it exercises the counter-numbered dispatch names at a cost a base test can carry.
    CANARY: ClassVar[str] = "dynamic_components"

    #: The aggregator whose port names the canary checks. Every EMS setup grows its dispatch names
    #: through this one component, which is why one setup can stand in for all of them.
    CANARY_AGGREGATOR: ClassVar[str] = "L2EMSElectricityController"

    #: Every legacy port the canary setup's energy manager grows, as the table declares them. The
    #: numbers are the whole point, and the two halves now carry different ones: an input carries
    #: its insertion index, which still moves when anything before it is added or removed, while a
    #: dispatch output carries the weight it steers on, which is the setup's own and moves only
    #: when the setup re-weights a participant.
    CANARY_LEGACY_PORTS: ClassVar[Tuple[str, ...]] = (
        "Input_PVSystem_ElectricityOutput_2",
        "Input_Battery1_AcBatteryPowerUsed_3",
        "Input_Battery2_AcBatteryPowerUsed_4",
        "Input_CHP1_ElectricityOutput_5",
        "Input_CHP2_ElectricityOutput_6",
        "ElectricityTarget1",
        "ElectricityTarget2",
        "ElectricityTarget3",
        "ElectricityTarget4",
    )

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

    @classmethod
    def resolved_wiring(cls, simulator: Simulator) -> WiringSnapshot:
        """Takes a built simulator through the two steps that resolve its wiring, and snapshots it.

        A port name is only final once the automatic default connections have been applied, which
        happens in ``prepare_calculation``, and once every input has found its source, which happens
        in ``connect_all_components``. Those two are the beginning of ``run_all_timesteps`` and are
        run here without the timesteps that follow, because the names are what this test is about
        and simulating a week to read them would cost minutes rather than seconds.

        Args:
            simulator: A simulator whose components have been registered and wired.

        Returns:
            The canonical wiring snapshot of that simulator.
        """
        simulator.prepare_calculation()
        simulator.connect_all_components()
        return WiringSnapshot.from_simulator(simulator)

    @classmethod
    def canary_wiring(cls, work: Path) -> Tuple[WiringSnapshot, WiringSnapshot]:
        """Builds the canary setup both ways, far enough to name every port, and snapshots each.

        Both builds are given their own result and cache directory under the test's temporary
        directory, for the same reason the rig gives each side of a triple its own: a shared cache
        would let the second build read what the first one wrote.

        Args:
            work: Where the two builds may write; a test's own temporary directory.

        Returns:
            The Python setup's wiring and the recorded twin's wiring, in that order.
        """
        from hisim.energy_system.executor import build_energy_system  # noqa: PLC0415
        from hisim.hisim_main import initialize_from_python  # noqa: PLC0415

        legacy_parameters = ParityWindows.build(cls.WINDOW, work / "python", work / "python-cache")
        ParitySide.reset_singletons()
        legacy = cls.resolved_wiring(
            initialize_from_python(str(cls.SETUPS / f"{cls.CANARY}.py"), legacy_parameters, None)
        )

        declared_parameters = ParityWindows.build(cls.WINDOW, work / "declarative", work / "declarative-cache")
        ParitySide.reset_singletons()
        declarative = cls.resolved_wiring(
            build_energy_system(cls.ENERGY_SYSTEMS / f"{cls.CANARY}.energy_system.yaml", declared_parameters).simulator
        )
        return legacy, declarative

    @classmethod
    def twin_port_spellings(cls) -> Set[Tuple[str, str]]:
        """Every ``(aggregator, declarative port)`` pair a committed twin implies.

        The recorded ``*.energy_system.yaml`` twins are regenerated from live builds of the Python
        setups and held current by the ``energy-system-freshness`` gate, so their ``inputs:``
        blocks are the fleet's own statement of which participant feeds which aggregator. A feed's
        port names are not written out there — the format derives them from frozen templates — so
        this applies the very templates the resolver applies: ``<output>From<source>`` for the
        input an aggregator grows, and ``DispatchTo<source>_<input>`` for the dispatch output of a
        feed that names a target input. That yields the fleet's declarative port names without
        building a single setup, which is what lets a base test cross-check the whole table in
        milliseconds.

        This is the declarative half of each table entry. It replaced a collection over the v1
        ``*.scenario.json`` twins, which spelled the *legacy* half, when those files retired on
        2026-09-12; nothing committed carries a legacy aggregator port name any more, so an entry's
        legacy spelling is proven by the canary above and by the fleet workflow.

        The walk is structural rather than schema-bound: any mapping whose value carries an
        ``inputs`` list is a component, wherever the file nests it, so the collection survives a
        grouped file that puts components inside groups and variants.

        Returns:
            The set of ``(component name, derived port name)`` pairs found in all twins.
        """
        spellings: Set[Tuple[str, str]] = set()

        def collect(name: str, feeds: object) -> None:
            if not isinstance(feeds, list):
                return
            for feed in feeds:
                if not isinstance(feed, dict):
                    continue
                origin = feed.get("from")
                if not isinstance(origin, str) or "." not in origin:
                    continue
                source, _, output = origin.partition(".")
                spellings.add((name, ResolvedDynamicConnection.AGGREGATOR_INPUT_TEMPLATE.format(
                    source_output=output, source_name=source)))
                dispatch = feed.get("dispatch")
                target_input = dispatch.get("target_input") if isinstance(dispatch, dict) else None
                if isinstance(target_input, str):
                    spellings.add((name, ResolvedDynamicConnection.DISPATCH_OUTPUT_TEMPLATE.format(
                        source_name=source, target_input=target_input)))

        def walk(node: object) -> None:
            if isinstance(node, dict):
                for name, spec in node.items():
                    if isinstance(name, str) and isinstance(spec, dict):
                        collect(name, spec.get("inputs"))
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        for path in sorted(cls.ENERGY_SYSTEMS.glob("*.energy_system.yaml")):
            walk(yaml.safe_load(path.read_text(encoding="utf-8")))
        return spellings


@pytest.mark.base
def test_one_week_july_is_the_first_week_of_july() -> None:
    """Catches a summer window whose dates are not seven days, or not in July.

    The definition is kept while the rig's July window is fenced (R11.5 as amended), because the
    mid-year-start epic unfences it rather than writing it again, and a definition nobody runs is
    exactly the kind that drifts. What this pins is the dates alone: what a run over those dates
    currently simulates is January, which the fence tests below are about.
    """
    parameters = SimulationParameters.one_week_july(2021, 60)
    assert (parameters.start_date.month, parameters.start_date.day) == (7, 1)
    assert (parameters.end_date.month, parameters.end_date.day) == (7, 8)
    assert parameters.seconds_per_timestep == 60
    # The literal, not one_week_only's count: a length bug both factories share must still fail.
    assert parameters.timesteps == 7 * 24 * 60


@pytest.mark.base
def test_the_matrix_covers_every_recorded_setup_in_every_runnable_window() -> None:
    """Catches a dispatch that quietly covers less than the fleet.

    R11.7 asks for one table covering every triple, so a matrix that lost a setup or a runnable
    window would make the rig's table an incomplete claim. The window vocabulary is asserted
    against the runner's own list because the matrix script may not import HiSim and therefore
    carries a second copy of it, and the runnable subset is asserted too so the fence cannot be
    lifted on one side only.
    """
    assert tuple(MatrixPaths.WINDOWS) == ParityWindows.names()
    assert MatrixPaths.runnable_windows() == ParityWindows.runnable()
    covered = discover(None)
    assert covered, "no setup has a recorded twin, so the rig would cover nothing"
    include = build_matrix()["include"]
    assert len(include) == len(covered) * len(ParityWindows.runnable())
    for stem in covered:
        windows = {entry["window"] for entry in include if entry["setup"] == stem}
        assert windows == set(ParityWindows.runnable())


@pytest.mark.base
def test_the_july_window_is_fenced_out_of_every_default_dispatch() -> None:
    """Catches the fenced window creeping back into what a dispatch runs by default.

    A July triple compares two runs that both simulate January, because every profile-driven
    component indexes its profile from timestep 0 (R11.5 as amended). Until the mid-year-start epic
    fixes that, no default path may reach the window: not the matrix the discover job emits, not
    the checker's default window list, and not the runner's.
    """
    assert "july" in MatrixPaths.WINDOWS, "the definition is kept; only the running of it is fenced"
    assert "july" in MatrixPaths.FENCED_WINDOWS
    assert set(MatrixPaths.FENCED_WINDOWS) <= set(MatrixPaths.WINDOWS), (
        "a fenced window that is not a defined window is a typo the fence would silently ignore"
    )
    assert "july" not in MatrixPaths.runnable_windows()
    assert "july" not in ParityWindows.runnable()
    assert {entry["window"] for entry in build_matrix()["include"]} == {"january"}


@pytest.mark.base
def test_asking_for_the_fenced_window_is_refused_with_the_reason_and_the_pointer(tmp_path: Path) -> None:
    """Catches a fenced window being silently dropped, or refused without saying why.

    Silently running fewer triples than were asked for would report a green table over coverage
    nobody checked, and a bare "unknown window" would send whoever asked for July looking for a
    typo instead of at the defect. Every entry point is checked, because each of them is somebody's
    first contact with the fence, and the last of them refuses before a simulation is started. The
    build call gets throwaway directories: the refusal fires before they are used today, but the
    day the epic unfences July this test is edited, not allowed to write into the checkout.
    """
    for refused in (
        lambda: build_matrix(windows=["july"]),
        lambda: ParityWindows.build("july", tmp_path / "results", tmp_path / "cache"),
    ):
        with pytest.raises(ValueError, match="midyear_start_epic"):
            refused()

    with pytest.raises(ValueError, match="January's profiles"):
        build_matrix(windows=["january", "july"])

    with pytest.raises(SystemExit, match="midyear_start_epic"):
        parity_main(["--setup", Rig.FIXTURE, "--window", "july"])


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
        pairs[("L2EMSElectricityController", "LoadingPowerInputForBattery_6")]
        == "DispatchToBattery_LoadingPowerInput"
    )
    renaming = DeclaredPortRenamings.port_renaming()
    assert renaming.rename("ElectricityMeter", "SomethingNobodyDeclared") == "SomethingNobodyDeclared"


@pytest.mark.base
def test_the_table_still_spells_the_ports_the_dynamic_components_setup_actually_grows(tmp_path: Path) -> None:
    """Catches a renaming table whose legacy spellings no longer match the controller's build.

    Every legacy dynamic port name in the table carries a number the two paths do not agree on: an
    aggregator input carries its insertion index, and a dispatch output carries the source weight
    the aggregator steers that participant on. The dispatch half used to carry the aggregator's
    running output counter instead — the outputs it had already declared when the setup added the
    dispatch — which was not the setup's to control: an energy manager that gained or lost one
    declared output renumbered every dispatch output of every setup that used it. Exactly that
    once failed the whole fleet's dispatch at once, with a wiring difference that was nothing but
    a name; the table's own comment on the battery dispatch tells that story, and F-1 replaced the
    counter with the weight so that it cannot happen again.

    This is the canary for that class of failure, and it still earns its place: a weight is stable
    under unrelated edits but not under a setup re-weighting a participant, and an input index
    moves as freely as it ever did. One setup is enough for the dispatch half because the naming
    rule is the aggregator's, and this one steers four participants on four different weights
    through it; it also needs no load profile, so it costs a base test seconds instead of minutes.
    The per-setup insertion indices of the setups the canary does not build are outside its net;
    those are cross-checked against the recorded twins by the next test and verified
    against live builds only by the fleet workflow. The canary asserts both halves: that every
    legacy name the table declares for this setup is a port the Python build really has — a name
    nobody grows any more can never be exercised again — and that translating the Python wiring
    through the table yields precisely the twin's wiring, which is the claim the rig makes
    fleet-wide.
    """
    legacy, declarative = Rig.canary_wiring(tmp_path)
    grown = (
        {wire.target_input for wire in legacy.wires if wire.target_component == Rig.CANARY_AGGREGATOR}
        | {wire.source_output for wire in legacy.wires if wire.source_component == Rig.CANARY_AGGREGATOR}
        | {port for component, port in legacy.unconnected_inputs if component == Rig.CANARY_AGGREGATOR}
    )
    declared = DeclaredPortRenamings.pairs()

    for port in Rig.CANARY_LEGACY_PORTS:
        assert port in grown, f"'{Rig.CANARY}' no longer grows '{port}', so the table declares a name nobody uses"
        assert (Rig.CANARY_AGGREGATOR, port) in declared, f"the table stopped declaring '{port}'"

    diff = WiringParityHarness.compare(DeclaredPortRenamings.port_renaming().apply_to(legacy), declarative)
    assert diff.is_identical(), diff.describe()


@pytest.mark.base
def test_every_declared_declarative_port_is_implied_by_a_committed_twin() -> None:
    """Catches a table entry that claims a declarative port the fleet does not grow.

    The canary above builds one setup live, so it can only vouch for that setup's ports; the
    entries of every other setup would otherwise be guarded only by the manually dispatched fleet
    workflow. This test closes that gap statically: every declarative name the table promises must
    be a port some committed twin's feed implies, because the twins are recorded from live builds
    and the resolver derives these names from them by frozen template. A typo in a newly authored
    entry fails here immediately, and an entry gone stale fails as soon as the twins are
    re-recorded — cheaper and earlier than the fleet workflow, though only that workflow proves an
    entry against a live build.

    The legacy half of each entry was cross-checked the same way against the v1 ``.scenario.json``
    twins until those retired; it is now proven by the canary and by the fleet workflow.
    """
    implied = Rig.twin_port_spellings()
    assert implied, "no committed twin declares any feed, so the cross-check checks nothing"
    for key, declarative in DeclaredPortRenamings.pairs().items():
        assert (key[0], declarative) in implied, (
            f"the table claims '{key[0]}.{key[1]}' is '{key[0]}.{declarative}', but no committed twin "
            "grows that port — either the entry has a typo, or the fleet stopped growing the feed "
            "and the entry is dead"
        )


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
def test_a_kpi_broken_setup_gets_a_structural_verdict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Catches a rig that turns a broken KPI layer into an exception instead of a verdict (T-21).

    Setups used to crash inside KPI computation after the simulation had finished — seven of them
    when this test was written — and R11.4 requires them to be covered anyway: the first two
    comparisons need no KPIs, so the triple must still report on the wiring and on every result
    column, and the third stage must say it was unavailable rather than raising. The real crashes
    heal as components gain their KPI entries — the fixture's transformer got its own, which is
    the point of the rule — so the crash is synthesized here at the very seam a real one hits: the
    postprocessor's KPI step, raising after the run produced its results. Since the 2026-09-05
    amendment an unavailable stage fails the triple — a new KPI regression must not read green —
    so the verdict is a named failure, never an error and never a pass.
    """
    from hisim.postprocessing.postprocessing_main import PostProcessor  # noqa: PLC0415

    def crash_in_kpi_computation(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("synthesized KPI crash: a component's KPI entries divide by zero")

    monkeypatch.setattr(PostProcessor, "compute_kpis_and_write_to_report_and_to_ppdt", crash_in_kpi_computation)

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
def test_the_exact_tolerance_treats_nan_for_nan_as_equal_and_nan_for_a_number_as_not() -> None:
    """Catches the rig's own tolerance disagreeing with the frame comparison about NaN.

    A value both runs failed to produce is the same value, and a value only one run failed to
    produce can never be absorbed: the frame comparison's half of both rules is pinned in
    ``tests/test_energy_system_parity.py`` beside the comparison itself, and this is the KPI
    tolerance's half, so the two modes cannot come to disagree about identical values again.
    """
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
def test_summarizing_a_dispatch_exits_nonzero_unless_every_collected_triple_reached_parity(
    tmp_path: Path,
) -> None:
    """Catches the summary job reading green over a dispatch that did not reach parity.

    The workflow's summary step is a pipeline — the table is teed into the step summary — so the
    only thing that can still turn the job red is the checker's own exit code surviving the pipe.
    That is what `set -o pipefail` under an explicitly requested bash is there for, and it protects
    nothing if the ``--summarize`` path itself stops returning 1. Both failing shapes are pinned:
    a triple that missed parity, and a dispatch whose verdict directory is empty — a run that
    covered nothing must never read as a pass. The verdict files are written the way the run path
    writes them, ``json.dumps`` over ``TripleVerdict.to_json()`` into a per-triple file under the
    collection directory, so a change to the verdict's JSON shape reaches this test too.
    """

    def dispatch(name: str, *verdicts: TripleVerdict) -> Path:
        directory = tmp_path / name
        for verdict in verdicts:
            written = directory / f"p3-parity-verdict-{verdict.stem}-{verdict.window}"
            written.mkdir(parents=True, exist_ok=True)
            (written / "verdict.json").write_text(
                json.dumps([verdict.to_json()], indent=2), encoding="utf-8"
            )
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    reached = TripleVerdict(
        stem=Rig.FIXTURE, window="january", wiring=Verdict.OK, results=Verdict.OK, kpis=Verdict.OK
    )
    missed = TripleVerdict(
        stem="other", window="january", wiring=Verdict.OK, results=Verdict.FAILED, kpis=Verdict.OK
    )

    assert parity_main(["--summarize", str(dispatch("parity", reached))]) == 0
    assert parity_main(["--summarize", str(dispatch("one_missed", reached, missed))]) == 1
    assert parity_main(["--summarize", str(dispatch("nothing_collected"))]) == 1
