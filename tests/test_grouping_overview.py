"""Tests for the generated grouping overview page: what it says, and that it stays what it says.

The page is a rendering of files that are already tested elsewhere, so nothing here re-checks a
grouping decision or a grouped file. What is tested is the three ways a generated document goes
wrong. It goes stale, which is why the committed page is compared byte for byte against what the
generator produces from the committed files today. It goes non-deterministic, which is why it is
rendered twice and the two texts are compared. And it quietly changes shape — an ordering that was
a property of one setup's files rather than of a stated rule, a diagram that grew a node per
component the moment a second setup appeared — which is why the ordering rule and the diagram's
structure are pinned on hand-built inputs with two variant groups rather than on the one setup the
repository has grouped so far.

The fleet denominator gets a test of its own for the same reason: it is the one number on the page
that is counted rather than read, and the three kinds of file it has to tell apart — a hand-written
exemplar, a flat twin, a grouped file — all live in one directory and all end in the same suffix.

Each test states the failure mode it catches.
"""

# clean

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Sequence

import pytest

from hisim.cli import main as cli_main
from hisim.energy_system.loader import load_energy_system
from hisim.energy_system.recording.grouping import Assignment, AssignmentKind, Grouping
from hisim.energy_system.recording.grouping_io import dump_grouping
from hisim.energy_system.recording.grouping_overview import (
    FleetCensus,
    GroupedSetup,
    Markdown,
    MermaidShape,
    OverviewPage,
    OverviewSweep,
    render_overview,
)
from hisim.energy_system.recording.probes import ProbeList
from hisim.energy_system.repository import RepositoryLayout


class Committed:
    """Where the real files this module reads live, found the way the command finds them.

    The tests run from wherever pytest was started, so nothing here may assume a working directory.
    The checkout is located from this test module itself, exactly as the sweep locates it from the
    generator module, so a test and the command it tests can never disagree about which repository
    they are looking at.
    """

    #: The directory the committed decisions, grouped files and flat twins share.
    DIRECTORY: ClassVar[str] = "energy_systems"

    @classmethod
    def root(cls) -> Path:
        """The checkout this test module lies in.

        Returns:
            The repository root.
        """
        return RepositoryLayout.root(Path(__file__))

    @classmethod
    def energy_systems(cls) -> Path:
        """The directory holding every committed file the page is rendered from.

        Returns:
            The repository's ``energy_systems/``.
        """
        return cls.root() / cls.DIRECTORY

    @classmethod
    def page(cls) -> Path:
        """The committed page itself.

        Returns:
            The path the command writes by default.
        """
        return cls.root() / OverviewPage.DEFAULT_OUTPUT


class TwoGroups:
    """A hand-built setup with two variant groups, which the real fleet does not have yet.

    The ordering rule and the diagram's shape are the two properties that would silently change the
    first time a second grouped setup appeared, and neither can be pinned on a repository holding
    one setup with one group: every ordering agrees on a single group, and a diagram of one group
    cannot show that groups are drawn in file order. So this fixture states two groups, each with
    two options, and puts a member on every position the rule distinguishes — assigned to a group as
    a whole, assigned to one option, and overridden in the shared section.

    The grouped file is written as text rather than built as a model because the page reads the
    order out of the document, and a model built by hand could not fail the way a re-emitted
    document can.
    """

    #: The grouped energy system: two shared components of which one is a knob, then two variant
    #: groups whose options each write out the member that survives them both.
    GROUPED: ClassVar[str] = """
schema_version: 3
name: two_groups
description: A setup with two variant groups.
components:
  weather:
    class: hisim.components.weather.Weather
    config:
      location: Aachen
  pv:
    class: hisim.components.generic_pv_system.PVSystem
    config:
      power_in_watt: 1000.0
variants:
  metering:
    selected: through_ems
    options:
      through_ems:
        components:
          meter:
            class: hisim.components.electricity_meter.ElectricityMeter
            config:
              source_weight: 0
          ems:
            class: hisim.components.controller_l2_energy_management_system.L2GenericEnergyManagementSystem
            config:
              strategy: optimize_own_consumption
      direct:
        components:
          meter:
            class: hisim.components.electricity_meter.ElectricityMeter
            config:
              source_weight: 1
  heating:
    selected: heat_pump
    options:
      heat_pump:
        components:
          emitter:
            class: hisim.components.heat_distribution_system.HeatDistributionSystem
            config:
              water_mass_flow_rate_in_kg_per_second: 0.5
          pump:
            class: hisim.components.more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLib
            config:
              model: Generic
      boiler:
        components:
          emitter:
            class: hisim.components.heat_distribution_system.HeatDistributionSystem
            config:
              water_mass_flow_rate_in_kg_per_second: 0.9
"""

    #: The probe list the decision points at, with one column per world the two groups make.
    PROBES: ClassVar[str] = """
setup: system_setups/two_groups.py
defaults: two_groups.Config.get_default
probes:
  - column: baseline
    description: >-
      The class defaults. Everything else in this list is one switch away from them.
  - column: no_ems
    description: A meter wired straight to every participant. Nothing else moves.
    module_config:
      config_.use_ems: false
  - column: boiler
    description: A boiler instead of a heat pump. The emitter survives and is rewired.
    module_config:
      config_.heater: Gas Boiler
"""

    #: The setup's own name, which its three files and its section heading all carry.
    STEM: ClassVar[str] = "two_groups"

    @classmethod
    def decision(cls, probes: str = "energy_systems/two_groups.probes.yaml") -> Grouping:
        """The grouping table of that setup, written in an order no renderer may keep.

        The assignments are deliberately listed in an order that is neither the grouped file's nor
        the rule's: if the page ever rendered the table in table order, this fixture would say so.

        Args:
            probes: Where the decision says its probe list is, relative to the root it is read
                against; a test writing the three files into a temporary directory names it there.

        Returns:
            The decision.
        """
        return Grouping(
            setup="system_setups/two_groups.py",
            probes=probes,
            assignments=(
                Assignment(component="pv", kind=AssignmentKind.OVERRIDE, note="A number a consumer picks."),
                Assignment(
                    component="pump",
                    kind=AssignmentKind.VARIANT,
                    name="heating",
                    option="heat_pump",
                    note="Only exists in the heat-pump world.",
                ),
                Assignment(
                    component="emitter",
                    kind=AssignmentKind.VARIANT,
                    name="heating",
                    note="Present either way and wired differently.",
                ),
                Assignment(
                    component="ems",
                    kind=AssignmentKind.VARIANT,
                    name="metering",
                    option="through_ems",
                    note="Only exists when the meter is wired through it.",
                ),
                Assignment(
                    component="meter",
                    kind=AssignmentKind.VARIANT,
                    name="metering",
                    note="Present in every configuration and wired differently.",
                ),
            ),
            configurations=(),
            origin="energy_systems/two_groups.grouping.yaml",
        )

    @classmethod
    def setup(cls) -> GroupedSetup:
        """The whole fixture as the page reads it.

        Returns:
            The grouped setup, its decision, probe list and grouped file all in memory.
        """
        return GroupedSetup(
            stem=cls.STEM,
            grouping=cls.decision(),
            probes=ProbeList.read(cls.PROBES),
            system=load_energy_system(cls.GROUPED),
        )

    @classmethod
    def commit(cls, directory: Path) -> None:
        """Writes the four files a grouped setup has into one directory.

        The flat twin is written too, because a grouped setup has one and the sweep has to drop it
        rather than list the setup a second time under its own name.

        Args:
            directory: Where the files go; it is also the root the decision's paths are read
                against, so the probe list is named without a directory of its own.
        """
        (directory / f"{cls.STEM}.probes.yaml").write_text(cls.PROBES, encoding="utf-8")
        (directory / f"{cls.STEM}{Grouping.SUFFIX}").write_text(
            dump_grouping(cls.decision(probes=f"{cls.STEM}.probes.yaml")), encoding="utf-8"
        )
        (directory / f"{cls.STEM}.grouped.energy_system.yaml").write_text(
            Headers.ORIGIN.format(stem=cls.STEM) + Headers.GROUPED_BY.format(stem=cls.STEM) + cls.GROUPED,
            encoding="utf-8",
        )
        (directory / f"{cls.STEM}.energy_system.yaml").write_text(
            Headers.twin(cls.STEM, "A setup with two variant groups.", ("weather", "pv")), encoding="utf-8"
        )


class Headers:
    """The three header shapes ``energy_systems/`` holds, as the census has to tell them apart.

    Only the header decides what a file is, so the bodies here are the smallest thing the reader
    accepts and every difference that matters is in the comment lines above them.
    """

    #: The recorder's own origin line, which is what makes a file a twin at all.
    ORIGIN: ClassVar[str] = (
        "# Recorded from system_setups/{stem}.py with energy_systems/one_day_15min.simulation.yaml "
        "by the HiSim energy-system recorder v1.\n"
    )

    #: The extra header line a grouped file carries, which is what keeps the census from counting
    #: a setup's second file as a second setup.
    GROUPED_BY: ClassVar[str] = (
        "# Grouped by {stem}.grouping.yaml from the probe configurations of {stem}.probes.yaml.\n"
    )

    #: A hand-authored energy system: no recorder line at all, so no twin of anything.
    HAND_WRITTEN: ClassVar[str] = "# The reference system of this directory.\nschema_version: 3\nname: exemplar\n"

    #: A flat twin, which is the only shape the census counts.
    FLAT: ClassVar[str] = ORIGIN.format(stem="flat") + "schema_version: 3\nname: flat\n"

    #: The grouped second file of a setup the flat twin already stands for.
    GROUPED: ClassVar[str] = (
        ORIGIN.format(stem="flat") + GROUPED_BY.format(stem="flat") + "schema_version: 3\nname: flat\n"
    )

    #: The class every component of a synthetic twin is given; the page never imports it, so any
    #: class the reader accepts will do and one is easier to read than five.
    COMPONENT_CLASS: ClassVar[str] = "hisim.components.electricity_meter.ElectricityMeter"

    @classmethod
    def twin(cls, stem: str, description: str, components: Sequence[str]) -> str:
        """Builds a whole recorded flat twin, header included.

        Args:
            stem: The setup's name, which the header and the body both carry.
            description: The one line the recorder copies out of the setup's docstring.
            components: The component names, in the order the twin should write them.

        Returns:
            The file's text.
        """
        body = ["schema_version: 3", f"name: {stem}", f"description: {description}", "components:"]
        for weight, name in enumerate(components):
            body.extend(
                [f"  {name}:", f"    class: {cls.COMPONENT_CLASS}", "    config:", f"      source_weight: {weight}"]
            )
        return cls.ORIGIN.format(stem=stem) + "\n".join(body) + "\n"


@pytest.mark.base
def test_the_committed_page_is_what_the_generator_produces_today() -> None:
    """Catches the committed page drifting away from the files it is a rendering of.

    This is the load-bearing test of the module. The page states counts, names and judgement notes
    that live in three other files, and every one of them can change without anybody thinking of
    the page; the only thing that keeps it true is that it is regenerated and compared, exactly as
    a recorded twin is.
    """
    generated = render_overview(Committed.energy_systems(), Committed.root())
    committed = Committed.page().read_text(encoding="utf-8")
    assert generated == committed, "re-render with 'hisim energy-system grouping overview'"


@pytest.mark.base
def test_rendering_the_page_twice_produces_the_same_bytes() -> None:
    """Catches a set or a dictionary whose iteration order leaks into the page.

    A page that differed between two renderings would fail the freshness test above at random,
    which is the worst way to learn about it, so the determinism is asserted on its own.
    """
    first = render_overview(Committed.energy_systems(), Committed.root())
    second = render_overview(Committed.energy_systems(), Committed.root())
    assert first == second


@pytest.mark.base
def test_the_assignments_table_follows_the_order_the_page_states_as_its_rule() -> None:
    """Catches a second grouped setup silently reordering the judgements.

    The page prints its ordering rule underneath the table, which is worth nothing unless the table
    obeys it. With two variant groups the rule has something to say that a single group cannot
    check: groups in the grouped file's order, group-scoped members before option-scoped ones,
    options in file order, and the overrides last in shared-section order.
    """
    rows = [assignment.component for assignment in TwoGroups.setup().assignment_rows()]
    assert rows == ["meter", "ems", "emitter", "pump", "pv"]


@pytest.mark.base
def test_the_diagram_lists_members_inside_a_role_box_and_never_one_node_per_component() -> None:
    """Catches the diagram degenerating into a node per component.

    A picture with one box per component says nothing the assignments table does not say better and
    becomes unreadable at the size of a real household, so the bound is structural: the members are
    joined into one label, and the number of nodes follows the roles and the options rather than
    the components.
    """
    synthetic = TwoGroups.setup()
    committed = GroupedSetup.read(OverviewSweep.decisions(Committed.energy_systems())[0], Committed.root())
    for setup in (synthetic, committed):
        nodes = [line for line in MermaidShape.render(setup) if line.strip().endswith('"]')]
        assert len(nodes) == _role_count(setup), "one node per role and per option, never per component"
    assert _role_count(committed) < _component_count(committed)
    labels = [line for line in MermaidShape.render(synthetic) if line.strip().endswith('"]')]
    assert any("emitter<br/>pump" in line for line in labels), "members must be joined inside one label"


def _role_count(setup: GroupedSetup) -> int:
    """How many boxes a setup's diagram may hold: the roles and the options, and nothing else.

    Args:
        setup: The setup being drawn.

    Returns:
        One for the setup, one for each of the two shared boxes that has members, one per variant
        group and one per option of each.
    """
    return (
        1
        + bool(setup.fixed)
        + bool(setup.overrides)
        + len(setup.group_names)
        + sum(len(setup.options(group)) for group in setup.group_names)
    )


def _component_count(setup: GroupedSetup) -> int:
    """How many components the grouped file writes down, options included.

    Args:
        setup: The setup being drawn.

    Returns:
        The shared components plus every option's own.
    """
    return len(setup.shared) + sum(
        len(setup.option_members(group, option))
        for group in setup.group_names
        for option in setup.options(group)
    )


@pytest.mark.base
def test_the_fleet_denominator_counts_recorded_flat_twins_only(tmp_path: Path) -> None:
    """Catches the hand-written exemplar or a grouped file being counted as a recorded setup.

    All three files end in ``.energy_system.yaml`` and live in one directory, and only the header
    tells them apart, so a census that globbed on the suffix alone would report a fleet a third
    larger than the one that exists.
    """
    (tmp_path / "exemplar.energy_system.yaml").write_text(Headers.HAND_WRITTEN, encoding="utf-8")
    (tmp_path / "flat.energy_system.yaml").write_text(Headers.FLAT, encoding="utf-8")
    (tmp_path / "flat.grouped.energy_system.yaml").write_text(Headers.GROUPED, encoding="utf-8")
    twins = FleetCensus.flat_twins(tmp_path)
    assert [path.name for path in twins] == ["flat.energy_system.yaml"]


@pytest.mark.base
def test_a_setup_with_only_a_twin_gets_a_row_a_section_and_a_one_box_diagram(tmp_path: Path) -> None:
    """Catches the twenty-one ungrouped setups falling off the page again.

    The page's whole claim is that it says which setups have structure *yet*, which it cannot make
    while it lists only the ones that do. So a setup with nothing but a twin has to appear three
    times over — a row, a section and a diagram — and its section has to state its inventory without
    pretending to a judgement: no assignments table, no probe table, and one box rather than one node
    per component.
    """
    (tmp_path / "alpha.energy_system.yaml").write_text(
        Headers.twin("alpha", "The alpha household.", ("weather", "meter")), encoding="utf-8"
    )
    page = OverviewSweep.page(tmp_path, tmp_path).render()
    assert "| [`alpha`](#alpha) | *not grouped yet* | — | — | — |" in page
    assert "\n## `alpha`\n" in page
    assert 'Recorded from `system_setups/alpha.py`, described in the recorded twin as "The alpha' in page
    assert 'components["components (2)<br/>weather<br/>meter"]' in page
    assert "### Assignments" not in page and "### Probe configurations" not in page


@pytest.mark.base
def test_the_fleet_table_puts_the_grouped_setups_first_and_the_rest_alphabetically(tmp_path: Path) -> None:
    """Catches the fleet table's order becoming whatever the directory listing happened to be.

    The order is stated in a footnote on the table, so it has to be a rule and not an accident. It
    cannot be observed on the real directory, which holds one grouped setup whose name sorts in the
    middle of the rest, so it is pinned on a directory built to disagree with every other order: a
    grouped setup whose name sorts last, and two twins written in reverse alphabetical order.
    """
    TwoGroups.commit(tmp_path)
    for stem in ("zeta", "alpha"):
        (tmp_path / f"{stem}.energy_system.yaml").write_text(
            Headers.twin(stem, f"The {stem} household.", ("weather",)), encoding="utf-8"
        )
    page = OverviewSweep.page(tmp_path, tmp_path)
    assert [setup.stem for setup in page.grouped] == ["two_groups"]
    assert [setup.stem for setup in page.ungrouped] == ["alpha", "zeta"]
    assert page.recorded == 3, "the grouped setup's own twin is counted once and listed once"
    rendered = page.render()
    positions = [rendered.index(f"[`{stem}`](#{stem})") for stem in ("two_groups", "alpha", "zeta")]
    assert positions == sorted(positions)


@pytest.mark.base
def test_a_setup_name_anchors_the_way_github_anchors_it() -> None:
    """Catches the fleet table linking to fragments no renderer creates.

    Every setup name carries underscores and every heading is a code span, so the two things the
    derivation has to get right are that an underscore survives and that a backtick does not. A link
    to a fragment that does not exist fails silently in a browser, which is why it is pinned here
    rather than left to be noticed.
    """
    assert Markdown.anchor("`household_heatpump_building_sizer`") == "household_heatpump_building_sizer"
    assert Markdown.link("`two_groups`", "`two_groups`") == "[`two_groups`](#two_groups)"
    assert Markdown.anchor("Probe configurations") == "probe-configurations"


@pytest.mark.base
def test_a_judgement_note_appears_on_the_page_unfolded_and_in_full() -> None:
    """Catches a note being truncated to its first sentence or left with its YAML line wrapping.

    The reasoning is the part of a grouping decision a reviewer most needs and the part a table most
    easily loses, so the committed page carries the whole sentence and the wrapping the committed
    YAML puts in it does not reach the page.
    """
    page = Committed.page().read_text(encoding="utf-8")
    assert (
        "Present in every configuration and wired differently: fed by the energy manager's grid "
        "balance when there is one, and by every participant directly when there is not. A group "
        "can add and remove a component but cannot rewire one that survives, so this is the row "
        "that forces a variant. Written out in full in both options."
    ) in page


@pytest.mark.base
def test_the_sweep_takes_the_committed_decisions_in_sorted_file_name_order() -> None:
    """Catches the page's sections being ordered by whatever the filesystem listed first.

    One decision is committed today, so the order cannot be observed on the real directory; what
    can be observed is that the sweep sorts rather than trusting the glob, which is the property
    that will hold when there are five.
    """
    decisions = OverviewSweep.decisions(Committed.energy_systems())
    assert [path.name for path in decisions] == sorted(path.name for path in decisions)
    assert decisions and all(path.name.endswith(Grouping.SUFFIX) for path in decisions)


@pytest.mark.base
def test_the_command_writes_the_page_where_it_asks_for_it(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Catches the verb being wired up but writing nothing, or writing somewhere else.

    The committed artefact has to come out of the real command rather than out of a test helper, so
    the command is driven here with the same words a person types, and what it wrote is compared
    against what the generator produces.
    """
    path = tmp_path / "overview.md"
    assert cli_main(["energy-system", "grouping", "overview", "--out", str(path)]) == 0
    assert path.read_text(encoding="utf-8") == render_overview(Committed.energy_systems(), Committed.root())
    assert str(path) in capsys.readouterr().out


@pytest.mark.base
def test_naming_no_grouping_verb_reports_the_verbs_that_exist(capsys: pytest.CaptureFixture[str]) -> None:
    """Catches the new verb being reachable but missing from the usage line people read.

    A workflow command whose usage line lists two of its three verbs teaches the wrong workflow, so
    the line is asserted rather than left to whoever edited the dispatch table last.
    """
    assert cli_main(["energy-system", "grouping"]) != 0
    assert "{probe,import,overview}" in capsys.readouterr().err
