"""Tests for the grouping pass: the three-state table, the decision, and the proof it produces.

The pass has a person in the middle of it, which is exactly what makes it worth testing hard: the
tool must ask a complete question and must refuse to answer it itself. So the tests here fall into
two halves. One half checks the question — that a component absent in one configuration, identical
in another and *wired differently* in a third is marked as three different things, because a
presence matrix would collapse the last two and the last one is the case variants exist for. The
other half checks the refusals: a difference nobody decided about, a configuration selecting an
option nobody created, a workbook that lost a decision on the round trip.

Everything runs on hand-written recordings rather than on real probe runs. A probe of a real setup
costs twenty seconds and proves nothing about the table that two small files cannot, and the whole
point of separating observation from judgement was that the judgement half is pure. The real fleet
run happens once, by hand, and is committed as ``energy_systems/*.grouping.yaml``.

Each test states the failure mode it catches.
"""

# clean

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
import dataclasses
from typing import Any, ClassVar, Dict

import pytest

from hisim.cli import main as cli_main
from hisim.energy_system.errors import EnergySystemRecordingError
from hisim.energy_system.groups import GroupExpander
from hisim.energy_system.loader import dump_energy_system, parse_energy_system
from hisim.energy_system.recording.session import RecordedFileWriter
from hisim.energy_system.recording.grouping import (
    Assignment,
    AssignmentKind,
    ConfigurationSelection,
    Grouping,
)
from hisim.energy_system.recording.grouping_checks import check_grouping
from hisim.energy_system.recording.grouping_io import dump_grouping, read_grouping
from hisim.energy_system.recording.grouping_report import CombinationSpace, GroupingReport
from hisim.energy_system.recording.matrix import CellState, EntryComparison, ProbeMatrix, ProbeRecording
from hisim.energy_system.recording.probes import ModuleConfigMaterialiser, ProbeList
from hisim.energy_system.recording.regrouping import ColumnRealizer, GroupedSystemBuilder
from hisim.energy_system.recording.workbook import write_workbook
from hisim.energy_system.recording.workbook_import import read_workbook


class Fork:
    """One tiny two-configuration system, written out twice, standing in for a real setup.

    The fixture is the smallest thing that exercises all three cell states at once. ``weather`` is
    in both worlds and says the same thing; ``meter`` is in both and is *wired* differently, which
    is the case the whole design turns on; ``battery`` is only in the first. A fourth component,
    ``pv``, is in both and differs only in a configuration value, which is the case a person is
    meant to call an override rather than structure.

    The two texts are complete energy-system files rather than fragments, because the pass compares
    files and a fragment would let a bug in the emitter hide.
    """

    #: The baseline world: a battery, an energy manager and a meter fed through it.
    BASELINE: ClassVar[str] = """
schema_version: 3
name: fork
components:
  weather:
    class: hisim.components.weather.Weather
    config:
      location: Aachen
  pv:
    class: hisim.components.generic_pv_system.PVSystem
    config:
      power_in_watt: 9000.0
    inputs:
      - weather
  meter:
    class: hisim.components.electricity_meter.ElectricityMeter
    config: {}
    inputs:
      - input: ElectricityInput
        from: battery.AcBatteryPowerUsed
  battery:
    class: hisim.components.advanced_battery_bslib.Battery
    config:
      capacity_in_kilowatt_hour: 18.0
"""

    #: The other world: no battery, and the meter wired straight to the array instead.
    NO_BATTERY: ClassVar[str] = """
schema_version: 3
name: fork
components:
  weather:
    class: hisim.components.weather.Weather
    config:
      location: Aachen
  pv:
    class: hisim.components.generic_pv_system.PVSystem
    config:
      power_in_watt: 4500.0
    inputs:
      - weather
  meter:
    class: hisim.components.electricity_meter.ElectricityMeter
    config: {}
    inputs:
      - input: ElectricityInput
        from: pv.ElectricityOutput
"""

    #: The probe list the two worlds stand for, as an author would write it.
    PROBES: ClassVar[str] = """
setup: system_setups/fork.py
defaults: tests.test_energy_system_grouping.Defaults.build
probes:
  - column: baseline
    description: the class defaults
  - column: no_battery
    module_config:
      energy_system_config_.use_battery_and_ems: false
"""

    #: Where the fixture pretends its probe list is committed, so that the workbook round trip
    #: carries a real path rather than the marker a list read from text gets.
    PROBES_PATH: ClassVar[str] = "energy_systems/fork.probes.yaml"

    @classmethod
    def probe_list(cls) -> ProbeList:
        """Reads the fixture's probe list.

        Returns:
            The list, its two columns in order, spelled as if it had been read from its file.
        """
        return dataclasses.replace(ProbeList.read(cls.PROBES), origin=cls.PROBES_PATH)

    @classmethod
    def matrix(cls) -> ProbeMatrix:
        """Builds the three-state table of the two hand-written worlds.

        Returns:
            The matrix, baseline first.
        """
        return ProbeMatrix.of(cls.probe_list(), cls.recordings())

    @classmethod
    def recordings(cls) -> Dict[str, ProbeRecording]:
        """The two worlds as probe recordings, text and model both.

        Returns:
            One recording per column.
        """
        return {
            column: cls.recording(column, text)
            for column, text in (("baseline", cls.BASELINE), ("no_battery", cls.NO_BATTERY))
        }

    @classmethod
    def recording(cls, column: str, text: str) -> ProbeRecording:
        """Wraps one written world as a recording.

        Args:
            column: The probe column it fills.
            text: The file's text.

        Returns:
            The recording, its text canonicalised so that a realization can be compared with it.
        """
        del column  # a recording is keyed by its column; the value itself no longer repeats it
        model = parse_energy_system(text)
        return ProbeRecording(text=dump_energy_system(model), model=model)

    @classmethod
    def decision(cls) -> Grouping:
        """The judgement a person would make about this fork.

        Returns:
            The meter in both options of one variant, the battery in one of them, the array an
            override, and the two columns positioned on the two options.
        """
        return Grouping(
            setup="system_setups/fork.py",
            probes="energy_systems/fork.probes.yaml",
            assignments=(
                Assignment("pv", AssignmentKind.OVERRIDE, note="the array size is a number, not a part"),
                Assignment("meter", AssignmentKind.VARIANT, "electricity_management"),
                Assignment("battery", AssignmentKind.VARIANT, "electricity_management", "with_battery"),
            ),
            configurations=(
                ConfigurationSelection("baseline", variants={"electricity_management": "with_battery"}),
                ConfigurationSelection("no_battery", variants={"electricity_management": "metered_directly"}),
            ),
            origin="fork.grouping.yaml",
        )


def state(matrix: ProbeMatrix, component: str, column: str) -> CellState:
    """Reads one cell of the matrix, insisting that the row is there at all.

    A missing row and a cell that says the wrong thing are different defects, and a test that
    reached through ``None`` would report the first as an attribute error rather than as the
    missing component it is.

    Args:
        matrix: The table to read.
        component: The row wanted.
        column: The probe column wanted.

    Returns:
        The cell's state.
    """
    row = matrix.row(component)
    assert row is not None, f"the matrix has no row for '{component}'"
    return row.states[column]


@pytest.mark.base
def test_the_matrix_tells_absent_identical_and_differing_apart() -> None:
    """T-16: catches a presence matrix pretending to be a three-state one.

    The meter is present in both configurations and wired differently in them, which is invisible
    to any check that only asks whether a component exists. If that cell ever reads ``=`` the whole
    reason variants exist has stopped being observable, and a person would never be asked about it.
    """
    matrix = Fork.matrix()

    assert matrix.columns == ("baseline", "no_battery")
    assert [row.component for row in matrix.rows] == ["weather", "pv", "meter", "battery"]
    assert state(matrix, "weather", "no_battery") is CellState.IDENTICAL
    assert state(matrix, "meter", "no_battery") is CellState.DIFFERENT
    assert state(matrix, "pv", "no_battery") is CellState.DIFFERENT
    assert state(matrix, "battery", "no_battery") is CellState.ABSENT
    assert state(matrix, "weather", "baseline") is CellState.IDENTICAL
    assert [row.component for row in matrix.decided_rows()] == ["pv", "meter", "battery"]


@pytest.mark.base
def test_a_reference_into_a_component_the_column_lacks_is_not_a_difference() -> None:
    """Catches a diff that asks a person about a row the format's own off rule already explains.

    A component listing a source that a configuration does not have loses that item in that
    configuration's recording, and turning a group off does exactly the same thing. Marking such a
    row ``≠`` would demand a decision about a component that needs none — in the real fleet that is
    the building, which lists the energy manager among its sources.
    """
    baseline = Fork.BASELINE.replace(
        "    config:\n      location: Aachen\n",
        "    config:\n      location: Aachen\n    inputs:\n      - battery\n",
        1,
    )
    recordings = dict(Fork.recordings())
    recordings["baseline"] = Fork.recording("baseline", baseline)
    matrix = ProbeMatrix.of(Fork.probe_list(), recordings)

    # weather exists in BOTH columns and differs only by its baseline-only reference to the
    # battery, which no_battery does not have — exactly the normalisation's case. Neutralising
    # EntryComparison.restricted would flip this cell to DIFFERENT.
    assert state(matrix, "weather", "no_battery") is CellState.IDENTICAL
    assert state(matrix, "weather", "baseline") is CellState.IDENTICAL
    assert "weather" not in [row.component for row in matrix.decided_rows()]


@pytest.mark.base
def test_a_differing_row_without_an_assignment_is_refused_by_name() -> None:
    """T-17 / AC-P3.14: catches a pass that quietly leaves an undecided difference at the top level.

    A component cannot both stay ungrouped and disagree with itself between two configurations. The
    refusal has to name the row and the column, because the person is looking at a hundred-row
    sheet and "the table is inconsistent" tells them nothing.
    """
    decision = Fork.decision()
    without_pv = decision.assignments[1:]

    with pytest.raises(EnergySystemRecordingError) as refusal:
        check_grouping(
            Grouping(
                setup=decision.setup,
                probes=decision.probes,
                assignments=without_pv,
                configurations=decision.configurations,
            ),
            Fork.matrix(),
        )

    assert "EF-R6" in str(refusal.value)
    assert "'pv'" in str(refusal.value)
    assert "no_battery" in str(refusal.value)


@pytest.mark.base
def test_a_configuration_naming_a_switch_nobody_created_is_refused() -> None:
    """Catches the two sheets of the table contradicting each other, which is EF-R7.

    A column may only stand where a switch the assignments created can stand. Without the check the
    contradiction would surface much later as a grouped file that reproduces nothing, and the
    message would be a diff rather than the name of the cell that is wrong.
    """
    decision = Fork.decision()
    wrong = Grouping(
        setup=decision.setup,
        probes=decision.probes,
        assignments=decision.assignments,
        configurations=(
            ConfigurationSelection(
                "baseline",
                variants={"electricity_management": "with_battery"},
                groups={"solar_thermal": True},
            ),
            decision.configurations[1],
        ),
    )

    with pytest.raises(EnergySystemRecordingError) as refusal:
        check_grouping(wrong, Fork.matrix())

    assert "EF-R7" in str(refusal.value)
    assert "solar_thermal" in str(refusal.value)


@pytest.mark.base
def test_an_assignment_naming_an_option_no_column_selects_is_refused() -> None:
    """Catches an option the file would offer that nothing was ever recorded in.

    Writing ``variant:x/y`` on a row when no configuration stands on ``y`` means the grouped file
    would carry a world nobody probed. The claim a grouped file makes reaches exactly as far as its
    probe list, so an option outside it is refused rather than written and quietly trusted.
    """
    decision = Fork.decision()
    wrong = Grouping(
        setup=decision.setup,
        probes=decision.probes,
        assignments=(
            *decision.assignments[:2],
            Assignment("battery", AssignmentKind.VARIANT, "electricity_management", "never_selected"),
        ),
        configurations=decision.configurations,
    )

    with pytest.raises(EnergySystemRecordingError) as refusal:
        check_grouping(wrong, Fork.matrix())

    assert "EF-R7" in str(refusal.value)
    assert "battery" in str(refusal.value)


@pytest.mark.base
def test_an_override_cannot_explain_a_component_that_is_missing() -> None:
    """Catches ``override`` being used as a catch-all for any row somebody does not want to think about.

    A value a consumer sets cannot make a component cease to exist, so the battery row is not an
    override however convenient that would be, and the refusal says so rather than producing a file
    that drops the battery from one world without saying where it went.
    """
    decision = Fork.decision()
    wrong = Grouping(
        setup=decision.setup,
        probes=decision.probes,
        assignments=(*decision.assignments[:2], Assignment("battery", AssignmentKind.OVERRIDE)),
        configurations=decision.configurations,
    )

    with pytest.raises(EnergySystemRecordingError) as refusal:
        check_grouping(wrong, Fork.matrix())

    assert "EF-R6" in str(refusal.value)
    assert "battery" in str(refusal.value)


@pytest.mark.base
def test_every_probe_column_equals_its_flat_recording_byte_for_byte() -> None:
    """T-19 / AC-P3.13: catches a grouped file that describes something other than what was recorded.

    This is the whole proof of the pass. Putting the grouped file's switches where a column stands
    and resolving it must produce that column's flat recording exactly — the same components, in the
    same order, wired the same way — and the baseline column is what makes the grouped file
    provably the flat twin with structure added.
    """
    matrix = Fork.matrix()
    decision = Fork.decision()
    check_grouping(decision, matrix)
    builder = GroupedSystemBuilder(decision, matrix)
    realizer = ColumnRealizer(builder.build(), builder)

    for column in matrix.columns:
        assert realizer.text(column, "") == matrix.recordings[column].text, column


@pytest.mark.base
def test_the_grouped_file_has_the_shape_the_assignments_describe() -> None:
    """Catches a builder that puts a component in the wrong place while still reproducing the columns.

    Reproduction alone is not enough: a file that put everything into one option of a variant could
    pass the byte check and be useless as a base file. The shape is asserted separately.
    """
    matrix = Fork.matrix()
    grouped = GroupedSystemBuilder(Fork.decision(), matrix).build()

    assert list(grouped.components) == ["weather", "pv"]
    assert not grouped.groups
    assert list(grouped.variants) == ["electricity_management"]
    variant = grouped.variants["electricity_management"]
    assert variant.selected == "with_battery"
    assert list(variant.options["with_battery"].components) == ["meter", "battery"]
    assert list(variant.options["metered_directly"].components) == ["meter"]


@pytest.mark.base
def test_the_knobs_name_exactly_what_the_file_does_not_determine() -> None:
    """AC-P3.16, first half: catches a report that hides how much a column had to be told.

    The grouped file states the baseline's array size, so the other column's size is a knob. If the
    knob list were empty the file would be claiming to determine a value it does not, and every
    column's byte-for-byte verdict would be worth less than it looks.
    """
    matrix = Fork.matrix()
    builder = GroupedSystemBuilder(Fork.decision(), matrix)

    knobs = builder.knobs()

    assert [(knob.component, knob.path) for knob in knobs] == [("pv", "config.power_in_watt")]
    assert knobs[0].stated == 9000.0
    assert knobs[0].values == {"no_battery": 4500.0}
    assert "4500.0" in knobs[0].describe()


@pytest.mark.base
def test_the_report_names_the_combinations_the_probe_list_never_exercised() -> None:
    """AC-P3.16, second half / R10.7: catches a file whose structure implies more than was probed.

    A probe list that toggles each fork on its own has tested no two forks together. The report has
    to say which corners of the space nobody visited, because the grouped file's switches otherwise
    read as an offer to combine them freely.
    """
    probe_list = ProbeList.read(
        """
setup: system_setups/fork.py
defaults: tests.test_energy_system_grouping.Defaults.build
probes:
  - column: baseline
  - column: no_battery
    module_config:
      energy_system_config_.use_battery_and_ems: false
  - column: half_pv
    module_config:
      energy_system_config_.share_of_maximum_pv_potential: 0.5
"""
    )

    untested = CombinationSpace.untested(probe_list)
    report = GroupingReport(setup="fork", grouped="fork.grouped.energy_system.yaml", untested=untested)
    printed = "\n".join(report.describe())

    assert len(untested) == 1
    assert dict(untested[0]) == {
        "energy_system_config_.use_battery_and_ems": False,
        "energy_system_config_.share_of_maximum_pv_potential": 0.5,
    }
    assert "never exercised (1)" in printed


@pytest.mark.base
def test_a_single_axis_probe_list_leaves_no_combination_untested() -> None:
    """Catches a report crying wolf about a setup that has exactly one fork.

    With one axis there is no pair of forks to combine, so every point of the space is a probe and
    the honest answer is that nothing is untested. A report that always listed something would train
    its reader to ignore it.
    """
    assert not CombinationSpace.untested(Fork.probe_list())


@pytest.mark.base
def test_the_workbook_round_trips_through_the_committed_file(tmp_path: Path) -> None:
    """T-18 / AC-P3.15: catches a workbook that loses a decision on the way out or back.

    The workbook is regenerable and the YAML is committed, so the identity that has to hold is
    yaml → workbook → yaml. If it ever fails, re-probing a setup silently discards judgements
    nobody will notice missing until a grouped file stops reproducing.
    """
    decision = Fork.decision()
    matrix = Fork.matrix()
    path = write_workbook(tmp_path / "fork.grouping.xlsx", matrix, Fork.probe_list(), decision)

    read_back = read_workbook(path)

    assert dump_grouping(read_back) == dump_grouping(decision)
    assert read_back.setup == decision.setup
    assert read_grouping(dump_grouping(read_back)).assignments == decision.assignments


@pytest.mark.base
def test_the_importer_refuses_a_workbook_whose_differing_row_was_left_empty(tmp_path: Path) -> None:
    """AC-P3.14, at the workbook: catches an import that accepts a table nobody finished.

    The same rule the second pass enforces against the real recordings is enforced here against the
    cells, because here the person still has the file open and the refusal is one edit away from
    being answered.
    """
    decision = Fork.decision()
    undecided = Grouping(
        setup=decision.setup,
        probes=decision.probes,
        assignments=decision.assignments[1:],
        configurations=decision.configurations,
    )
    path = write_workbook(tmp_path / "fork.grouping.xlsx", Fork.matrix(), Fork.probe_list(), undecided)

    with pytest.raises(EnergySystemRecordingError) as refusal:
        read_workbook(path)

    assert "EF-R6" in str(refusal.value)
    assert "'pv'" in str(refusal.value)


@pytest.mark.base
def test_a_probe_list_whose_first_entry_is_not_the_baseline_is_refused() -> None:
    """Catches the one mistake that would make the whole proof vacuous.

    The baseline column is recorded with no module configuration at all, which is what makes its
    flat file the committed twin. A first probe that changed something would compare the grouped
    file against a file nobody committed, and the pass would still say every column reproduced.
    """
    with pytest.raises(EnergySystemRecordingError) as refusal:
        ProbeList.read(
            """
setup: system_setups/fork.py
defaults: a.B.build
probes:
  - column: half_pv
    module_config:
      energy_system_config_.share_of_maximum_pv_potential: 0.5
"""
        )

    assert "EF-R9" in str(refusal.value)
    assert "baseline" in str(refusal.value)


@pytest.mark.base
def test_a_probe_overlay_naming_no_field_is_refused_rather_than_added() -> None:
    """Catches a misspelled knob turning a probe into a second copy of the baseline.

    A field that does not exist would be written into the configuration document, ignored by the
    setup, and produce a column identical to the baseline in every cell. That is the one failure
    the table cannot show, because it looks exactly like a proof.
    """
    probe_list = ProbeList.read(
        """
setup: system_setups/household_heatpump_building_sizer.py
defaults: hisim.building_sizer_utils.interface_configs.modular_household_config.ModularHouseholdConfig.get_default_config_for_household_heatpump
probes:
  - column: baseline
  - column: typo
    module_config:
      energy_system_config_.use_battery_and_emss: false
"""
    )

    with pytest.raises(EnergySystemRecordingError) as refusal:
        ModuleConfigMaterialiser.document(probe_list, probe_list.probes[1])

    assert "EF-R9" in str(refusal.value)
    assert "use_battery_and_emss" in str(refusal.value)


class Defaults:
    """The module-configuration defaults the Fork probe list names, small enough to see whole.

    The real setups name a ``ModularHouseholdConfig`` builder here; the fixture names this class so
    that materialiser tests need no heavy import and a default value is a fact of the test file.
    """

    @classmethod
    def build(cls) -> "Defaults.Config":
        """Builds the default configuration object the probe overlays are applied onto.

        Returns:
            The defaults, carrying one nested block with two fields.
        """
        return cls.Config()

    class Config:
        """The configuration value: one nested block, dumped the way a dataclass config would be."""

        def to_dict(self) -> Dict[str, Any]:
            """Dumps the defaults as the nested document an overlay path addresses.

            Returns:
                The document.
            """
            return {"energy_system_config_": {"use_battery_and_ems": True, "share_of_maximum_pv_potential": 1.0}}


@pytest.mark.base
def test_a_configuration_inventing_a_variant_in_every_column_is_refused() -> None:
    """Catches the switch-validation validating a typo against itself.

    The known variants were once collected from assignments *and* configurations, so a variant name
    invented by every configuration entered the very set the guard checked against and a dead
    single-option switch landed silently in the committed file. Only an assignment can create a
    variant — a variant nobody assigned a component to has no members.
    """
    decision = Fork.decision()
    wrong = Grouping(
        setup=decision.setup,
        probes=decision.probes,
        assignments=decision.assignments,
        configurations=(
            ConfigurationSelection(
                "baseline", variants={"electricity_management": "with_battery", "ghost": "on"}
            ),
            ConfigurationSelection(
                "no_battery", variants={"electricity_management": "metered_directly", "ghost": "on"}
            ),
        ),
    )

    with pytest.raises(EnergySystemRecordingError) as refusal:
        check_grouping(wrong, Fork.matrix())

    assert "EF-R7" in str(refusal.value)
    assert "ghost" in str(refusal.value)


@pytest.mark.base
def test_a_group_flag_that_is_not_a_boolean_is_refused() -> None:
    """Catches a hand-written string flag silently switching a group on.

    ``bool("false")`` is ``True``, so a committed file edited by hand to say ``pv: "off"`` would
    read as an enabled group and only surface as a byte-for-byte non-reproduction minutes later.
    The reader accepts a real YAML boolean and nothing else, naming the flag it refused.
    """
    with pytest.raises(EnergySystemRecordingError) as refusal:
        read_grouping(
            """
setup: system_setups/fork.py
probes: energy_systems/fork.probes.yaml
configurations:
  baseline:
    groups:
      pv_switch: "false"
"""
        )

    assert "EF-R7" in str(refusal.value)
    assert "pv_switch" in str(refusal.value)


@pytest.mark.base
def test_a_probe_changing_only_default_values_is_refused() -> None:
    """Catches the vacuous-proof probe: an overlay that sets fields to what they already are.

    Such a column records the baseline a second time, every cell reads ``=`` and the table looks
    like a proof of a configuration that was never exercised. The misspelled-field refusal cannot
    catch it — the path is real — so the materialiser compares the finished document against the
    untouched defaults and refuses equality.
    """
    probe_list = ProbeList.read(
        """
setup: system_setups/fork.py
defaults: tests.test_energy_system_grouping.Defaults.build
probes:
  - column: baseline
  - column: no_op
    module_config:
      energy_system_config_.use_battery_and_ems: true
"""
    )

    with pytest.raises(EnergySystemRecordingError) as refusal:
        ModuleConfigMaterialiser.document(probe_list, probe_list.probes[1])

    assert "EF-R9" in str(refusal.value)
    assert "no_op" in str(refusal.value)


@pytest.mark.base
def test_a_probe_column_that_is_not_an_identifier_is_refused() -> None:
    """Catches a column name escaping the probe work directory.

    The column is interpolated into the recording's and the module-configuration file's names, so
    a ``/`` or a ``..`` would write outside the throwaway directory; group and variant names have
    carried the identifier rule from the start, and the column now gets the same one.
    """
    with pytest.raises(EnergySystemRecordingError) as refusal:
        ProbeList.read(
            """
setup: system_setups/fork.py
defaults: a.B.build
probes:
  - column: baseline
  - column: ../../escape
    module_config:
      energy_system_config_.use_battery_and_ems: false
"""
        )

    assert "EF-R9" in str(refusal.value)
    assert "escape" in str(refusal.value)


@pytest.mark.base
def test_a_matrix_without_the_baseline_recording_is_refused() -> None:
    """Catches a matrix silently re-baselining when the baseline recording is missing.

    Every cell's meaning turns on columns[0] being the committed twin; promoting the next column
    would flip the comparison rather than fail it. Unreachable through the probe runner, which
    raises on any failed probe, but the public constructor must hold the invariant itself.
    """
    recordings = dict(Fork.recordings())
    del recordings["baseline"]

    with pytest.raises(EnergySystemRecordingError) as refusal:
        ProbeMatrix.of(Fork.probe_list(), recordings)

    assert "EF-R9" in str(refusal.value)
    assert "baseline" in str(refusal.value)


@pytest.mark.base
def test_a_scalar_sizing_reference_into_an_absent_component_is_dropped() -> None:
    """Catches the reference normalisation treating the two sizing_sources shapes differently.

    A list of sizing references dropped vanished providers while a single dotted reference was
    kept, so a row could read ``≠`` purely because of a reference the format's own off rule
    explains. Latent — recordings carry no sizing_sources — but the comparison must not depend on
    that staying true.
    """
    present = {"weather": object()}
    document = {
        "class": "x.Y",
        "sizing_sources": {
            "listed": ["weather.Other", "battery.Fact"],
            "scalar_gone": "battery.Fact",
            "scalar_kept": "weather.Other",
        },
    }

    restricted = EntryComparison.restricted(document, present)

    assert restricted["sizing_sources"]["listed"] == ["weather.Other"]
    assert restricted["sizing_sources"]["scalar_gone"] is None
    assert restricted["sizing_sources"]["scalar_kept"] == "weather.Other"


@pytest.mark.base
def test_an_assignment_that_would_not_round_trip_is_refused_at_construction() -> None:
    """Catches an assignment whose written form parses back as a different assignment.

    ``:`` and ``/`` are the separators of the written form with no escaping, so a name carrying one
    would survive the trip to the sheet and come back meaning something else. Such a value is a
    programming error, refused where it is constructed rather than where it is next read.
    """
    with pytest.raises(ValueError):
        Assignment("x", AssignmentKind.VARIANT, "ems/high", "opt")
    with pytest.raises(ValueError):
        Assignment("x", AssignmentKind.GROUP, "g", "an_option")
    with pytest.raises(ValueError):
        Assignment("x", AssignmentKind.VARIANT, "")


@pytest.mark.base
def test_a_group_registered_between_ungrouped_components_still_reproduces_byte_for_byte() -> None:
    """Catches the realizer laying dissolved components out in block order instead of observed order.

    The grouped file's blocks lose the registration order — here the boiler group's member was
    registered between the weather and the battery — and the expansion appends variant members
    before group members, so ordering by the blocks can never reproduce an interleaved recording.
    The realizer orders the dissolved components by the recording it is being proven against.
    """
    baseline_text = """
schema_version: 3
name: interleaved
components:
  weather:
    class: hisim.components.weather.Weather
    config:
      location: Aachen
  boiler:
    class: hisim.components.generic_boiler.GenericBoiler
    config:
      power_in_watt: 5000.0
  battery:
    class: hisim.components.advanced_battery_bslib.Battery
    config:
      capacity_in_kilowatt_hour: 18.0
  meter:
    class: hisim.components.electricity_meter.ElectricityMeter
    config: {}
"""
    bare_text = """
schema_version: 3
name: interleaved
components:
  weather:
    class: hisim.components.weather.Weather
    config:
      location: Aachen
  meter:
    class: hisim.components.electricity_meter.ElectricityMeter
    config: {}
"""
    probe_list = dataclasses.replace(
        ProbeList.read(
            """
setup: system_setups/interleaved.py
defaults: tests.test_energy_system_grouping.Defaults.build
probes:
  - column: baseline
  - column: bare
    module_config:
      energy_system_config_.use_battery_and_ems: false
"""
        ),
        origin="energy_systems/interleaved.probes.yaml",
    )
    recordings = {
        "baseline": Fork.recording("baseline", baseline_text),
        "bare": Fork.recording("bare", bare_text),
    }
    matrix = ProbeMatrix.of(probe_list, recordings)
    decision = Grouping(
        setup="system_setups/interleaved.py",
        probes="energy_systems/interleaved.probes.yaml",
        assignments=(
            Assignment("boiler", AssignmentKind.GROUP, "heating"),
            Assignment("battery", AssignmentKind.VARIANT, "storage", "with_battery"),
        ),
        configurations=(
            ConfigurationSelection(
                "baseline", groups={"heating": True}, variants={"storage": "with_battery"}
            ),
            ConfigurationSelection("bare", groups={"heating": False}, variants={"storage": "none"}),
        ),
        origin="interleaved.grouping.yaml",
    )
    check_grouping(decision, matrix)
    builder = GroupedSystemBuilder(decision, matrix)
    realizer = ColumnRealizer(builder.build(), builder)

    for column in matrix.columns:
        assert realizer.text(column, "") == matrix.recordings[column].text, column


@pytest.mark.base
def test_the_committed_grouped_sizer_realizes_the_committed_twin() -> None:
    """Catches drift between the three committed grouping artifacts and the committed flat twin.

    The grouped heat-pump-sizer file's whole claim is that, at its committed switch positions, it
    is the flat twin with structure added. The full per-column proof needs live probe runs, but the
    baseline half of it is a pure file computation: expand the committed grouped file, lay the
    components out in the twin's own order, and the emitted body must equal the committed twin's
    body byte for byte.
    """
    root = Path(__file__).resolve().parents[1]
    grouped = parse_energy_system(root / "energy_systems/household_heatpump_building_sizer.grouped.energy_system.yaml")
    twin_path = root / "energy_systems/household_heatpump_building_sizer.energy_system.yaml"
    twin = parse_energy_system(twin_path)

    expanded, _ = GroupExpander(grouped).expand()
    dissolved = dict(expanded.components)
    for group in expanded.groups.values():
        dissolved.update(group.components)
    ordered = {name: dissolved[name] for name in twin.all_components() if name in dissolved}
    ordered.update({name: entry for name, entry in dissolved.items() if name not in ordered})
    flat = expanded.model_copy(update={"components": ordered, "groups": {}, "variants": {}})

    _, twin_body = RecordedFileWriter.split(twin_path.read_text(encoding="utf-8"))
    assert dump_energy_system(flat) == twin_body


@pytest.mark.base
def test_a_workbook_without_its_provenance_properties_is_refused(tmp_path: Path) -> None:
    """Catches an import silently guessing which setup a workbook belongs to.

    The writer always records the setup and the probe list in the workbook's description property;
    a workbook without them was re-saved by a tool that strips document properties, and a guessed
    path would commit a decision whose provenance nobody stated — wrong only later, at
    ``record --grouping``, after the commit.
    """
    path = write_workbook(tmp_path / "fork.grouping.xlsx", Fork.matrix(), Fork.probe_list(), Fork.decision())
    from openpyxl import load_workbook  # noqa: PLC0415 - the test tampers the way a tool would

    workbook = load_workbook(path)
    workbook.properties.description = None
    workbook.save(path)

    with pytest.raises(EnergySystemRecordingError) as refusal:
        read_workbook(path)

    assert "EF-R7" in str(refusal.value)
    assert "setup" in str(refusal.value)


@pytest.mark.base
def test_the_grouping_probe_verb_drives_the_shared_recorder_child(monkeypatch: pytest.MonkeyPatch) -> None:
    """Catches the CLI wiring between the grouping verbs and the child recorder drifting.

    Every probe is recorded through this repository's own command line so that a probe and a hand
    recording cannot diverge; nothing but this test pins the argument vector that contract turns
    on. The child is stubbed to fail, so the test also proves a failing probe surfaces as the
    refusal exit code rather than as a traceback.
    """
    captured: Dict[str, Any] = {}

    def fake_run(arguments, **kwargs):  # noqa: ANN001, ANN003 - mirror subprocess.run loosely
        del kwargs  # the stub reads the argument vector and nothing else
        captured.setdefault("argv", list(arguments))
        return subprocess.CompletedProcess(args=arguments, returncode=1, stdout="", stderr="stubbed refusal")

    monkeypatch.setattr("hisim.energy_system.recording.probe_session.subprocess.run", fake_run)

    exit_code = cli_main(
        [
            "energy-system",
            "grouping",
            "probe",
            "system_setups/household_heatpump_building_sizer.py",
            "energy_systems/household_heatpump_building_sizer.probes.yaml",
        ]
    )

    assert exit_code != 0
    argv = captured["argv"]
    assert argv[0] == sys.executable
    assert argv[1:5] == ["-m", "hisim.cli", "energy-system", "record"]
    assert argv[5].endswith("household_heatpump_building_sizer.py")
    assert "--out" in argv
