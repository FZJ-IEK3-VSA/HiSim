"""Tests for exclusive variants: what selecting one option of a variant does to a file.

A variant names its options and selects exactly one, so the file can only ever describe one
world and the loader never has a constraint to solve. What has to hold is that the world it
describes is exactly the world an author would have written by hand: the selected option's
components at the top level, the variant block gone, and every reference into an option that
lost dropped the way a reference into a disabled group is dropped. That property is checked
mechanically over every option of every variant of every mockup, against the hand-written
equivalent the group tests already use — written from the rule and never through the expander,
so the two sides cannot agree by sharing a bug.

Around the identity property sit the checks that make it worth having. Both directions of the
mockup's metering variant are pinned, because "no EMS feed survives" and "no direct feed
survives" is the difference the whole construct was asked for and neither half is implied by
the other. Each of the five ways a variant can be written wrong is exercised on the smallest
document that shows it, asserting the catalogue identifier and the names in the message. And
one real build proves what a consumer of a record sees: the selection is resolved, so the
record carries no variants at all and the audit is the only place it is written down.
"""

# clean

from pathlib import Path
from typing import List, Tuple

import pytest

from hisim.energy_system import (
    EnergySystemErrorId,
    EnergySystemFile,
    EnergySystemFormatError,
    dump_energy_system,
    enabled_component_names,
    expand_groups,
    load_energy_system,
    parse_energy_system,
)
from hisim.cli import ExitCodes, main
from hisim.energy_system.audit import build_audit
from hisim.energy_system.model import Variant, VariantOption
from hisim.energy_system.record import realize
from tests.test_energy_system_groups import (
    DanglingScalarReference,
    hand_written_equivalent,
)
from tests.test_energy_system_loader import Mockups
from tests.test_energy_system_record import Fixtures


class Documents:
    """The smallest documents that show each way a variant can be written wrong.

    Every one of them is a valid file except for the single rule under test, so the rejection
    a test asserts can only come from that rule. They are kept as text rather than built
    through the models, because what is under test is what a file may say and an author writes
    text.
    """

    #: A well-formed building entry, so that every document below is a system rather than an
    #: empty file, and the rejections cannot be caused by a missing components block.
    BUILDING: str = """schema_version: 3
name: variant rule under test
components:
  building:
    class: hisim.components.building.Building
    preset: standard
"""

    #: One converted class, written out wherever a document needs a second component: once
    #: indented for a top-level entry, once for an entry inside an option.
    METER: str = "    class: hisim.components.electricity_meter.ElectricityMeter\n    preset: standard\n"
    OPTION_METER: str = (
        "            class: hisim.components.electricity_meter.ElectricityMeter\n"
        "            preset: standard\n"
    )

    #: A selection naming no option of its variant (R15.5, first).
    UNKNOWN_SELECTION: str = (
        BUILDING
        + """variants:
  metering:
    selected: with_battery
    options:
      with_ems:
        components: {}
      bare:
        components: {}
"""
    )

    #: A variant offering nothing to choose between (R15.5, second).
    EMPTY_OPTIONS: str = (
        BUILDING
        + """variants:
  metering:
    selected: bare
    options: {}
"""
    )

    #: One component name claimed by two different variants (R15.5, third).
    TWO_VARIANTS: str = (
        BUILDING
        + """variants:
  metering:
    selected: metered
    options:
      metered:
        components:
          meter:
"""
        + OPTION_METER
        + """      bare:
        components: {}
  billing:
    selected: billed
    options:
      billed:
        components:
          meter:
"""
        + OPTION_METER
        + """      unbilled:
        components: {}
"""
    )

    #: One component name written both at the top level and inside an option (R15.5, fourth).
    TOP_LEVEL_AND_OPTION: str = (
        BUILDING
        + "  meter:\n"
        + METER
        + """variants:
  metering:
    selected: metered
    options:
      metered:
        components:
          meter:
"""
        + OPTION_METER
        + """      bare:
        components: {}
"""
    )

    #: An entry inside the option nobody selects that says nothing about how it is
    #: configured, which is a defect in the file rather than in the world that runs.
    SILENT_OPTION_ENTRY: str = (
        BUILDING
        + """variants:
  metering:
    selected: metered
    options:
      metered:
        components:
          meter:
"""
        + OPTION_METER
        + """      bare:
        components:
          spare_meter:
            class: hisim.components.electricity_meter.ElectricityMeter
"""
    )

    #: A variant named after a component of the same file (R15.5, fifth).
    NAME_COLLISION: str = (
        BUILDING
        + """variants:
  building:
    selected: heavy
    options:
      heavy:
        components: {}
      light:
        components: {}
"""
    )


class MinimalWithAVariant:
    """The minimal mockup with its meter moved into a variant, as a buildable file.

    A record can only be written from a system that actually builds, and the one mockup every
    class of which is converted is the minimal household. Its meter is the component to move:
    nothing reads it, so the household still wires in the world without one, which is what
    lets the record and the audit be checked over a full build rather than over a parse.

    The file is derived from the mockup rather than copied into this module, so that a change
    to the mockup cannot leave a private copy behind to rot.
    """

    #: Name of the variant the meter is moved into.
    VARIANT: str = "metering"

    #: The option that keeps the meter, and the one that does without it.
    METERED: str = "metered"
    UNMETERED: str = "unmetered"

    @classmethod
    def model(cls) -> EnergySystemFile:
        """Builds the model of the derived file.

        Returns:
            The minimal mockup with its ``meter`` entry moved out of the top level and into
            the selected option of a two-option variant.
        """
        mockup = parse_energy_system(Mockups.path(Mockups.NAMES[0]))
        meter = mockup.components["meter"]
        variant = Variant(
            name=cls.VARIANT,
            selected=cls.METERED,
            options={
                cls.METERED: VariantOption(name=cls.METERED, components={meter.name: meter}),
                cls.UNMETERED: VariantOption(name=cls.UNMETERED, components={}),
            },
        )
        remaining = {name: entry for name, entry in mockup.components.items() if name != meter.name}
        return mockup.model_copy(update={"components": remaining, "variants": {cls.VARIANT: variant}})

    @classmethod
    def write(cls, directory: Path) -> Path:
        """Writes the derived file into a directory the test owns.

        Args:
            directory: Where the file goes; it is created if it does not exist.

        Returns:
            The path of the written file.
        """
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "uc1_with_a_variant.energy_system.yaml"
        path.write_text(dump_energy_system(cls.model()), encoding="utf-8")
        return path


def select_option(model: EnergySystemFile, variant_name: str, option_name: str) -> EnergySystemFile:
    """Returns the file with one variant's selection changed, and nothing else.

    Args:
        model: The parsed file.
        variant_name: The variant to re-select.
        option_name: The option it should resolve to.

    Returns:
        A copy of the file whose named variant selects the named option.
    """
    variants = {
        name: (variant.model_copy(update={"selected": option_name}) if name == variant_name else variant)
        for name, variant in model.variants.items()
    }
    return model.model_copy(update={"variants": variants})


def mockup_variant_triples() -> List[Tuple[str, str, str]]:
    """Lists every (mockup, variant, option) triple the identity property is checked over.

    Reading the triples off the mockups rather than hard-coding them means an option added to
    a mockup is covered without anyone remembering to extend the test.

    Returns:
        One triple per option of each variant of each mockup, mockups in canonical order.
    """
    triples: List[Tuple[str, str, str]] = []
    for name in Mockups.NAMES:
        model = parse_energy_system(Mockups.path(name))
        for variant_name, variant in model.variants.items():
            triples.extend((name, variant_name, option) for option in variant.options)
    return triples


def feeds_of(model: EnergySystemFile, consumer: str) -> List[str]:
    """Lists the sources one component takes its inputs from, in the order written.

    Args:
        model: The expanded file.
        consumer: The component whose input list is wanted.

    Returns:
        One source name per input item.
    """
    return [item.source for item in model.all_components()[consumer].inputs]


@pytest.mark.base
@pytest.mark.parametrize(("mockup", "variant", "option"), mockup_variant_triples())
def test_selecting_an_option_equals_writing_that_world_by_hand(mockup: str, variant: str, option: str) -> None:
    """Catches a selection producing something other than the hand-written alternative world.

    This is the property that makes a variant a way of writing two files in one rather than a
    semantic feature of its own: whichever option is selected, the system is exactly the one an
    author would have written with that option's components at the top level and the variant
    block deleted. Where the hand-written file cannot be completed — a scalar sizing line would
    be left without its provider — the expander must refuse for the same reason.
    """
    model = parse_energy_system(Mockups.path(mockup))
    already_off = {name for name, group in model.groups.items() if not group.enabled}
    try:
        expected = hand_written_equivalent(model, deleted_groups=already_off, selections={variant: option})
    except DanglingScalarReference:
        with pytest.raises(EnergySystemFormatError) as raised:
            expand_groups(select_option(model, variant, option))
        assert raised.value.error_id is EnergySystemErrorId.DISABLED_SIZING_SOURCE
        return

    expanded, _ = expand_groups(select_option(model, variant, option))
    assert dump_energy_system(expanded) == dump_energy_system(expected)


@pytest.mark.base
def test_the_selected_option_joins_the_top_level_and_the_variant_is_gone() -> None:
    """Catches a variant surviving expansion, which every later stage would have to know about.

    Nothing downstream of expansion — validation of the live set, class binding, sizing, wiring,
    the realized record — is written to handle a variant, so the pre-pass has to resolve it away
    completely rather than hand a smaller variant on.
    """
    model = parse_energy_system(Mockups.path("energy_system_mockup.yaml"))

    expanded, record = expand_groups(model)

    assert not expanded.variants
    assert [name for name in ("battery", "ems", "meter") if name in expanded.components] == [
        "battery",
        "ems",
        "meter",
    ]
    assert tuple(expanded.components)[-3:] == ("battery", "ems", "meter")
    assert set(enabled_component_names(expanded)) == set(expanded.all_components())
    assert len(record.selections) == 1
    selection = record.selections[0]
    assert (selection.variant, selection.selected) == ("electricity_management", "ems_and_battery")
    assert selection.components == ("battery", "ems", "meter")
    assert selection.rejected == ("direct_metering",)
    assert any("electricity_management" in line for line in record.describe())


@pytest.mark.base
def test_direct_metering_leaves_no_energy_management_behind() -> None:
    """Catches the option that has no EMS keeping a reference to one, or missing a direct feed.

    Both halves are the same rule seen from its two ends: the EMS and its battery vanish, and
    with them the four bare items that named the EMS, while the meter is the one written in this
    option and therefore reads every participant itself.
    """
    model = select_option(
        parse_energy_system(Mockups.path("energy_system_mockup.yaml")),
        "electricity_management",
        "direct_metering",
    )

    expanded, record = expand_groups(model)

    live = expanded.all_components()
    assert "ems" not in live and "battery" not in live
    assert feeds_of(expanded, "meter") == ["pv_south", "pv_east", "occupancy", "heat_pump", "heating_rod"]
    assert [item.source for entry in live.values() for item in entry.inputs if item.source == "ems"] == []
    assert {item.consumer for item in record.dropped_input_items if item.source == "ems"} == {
        "building",
        "hds_controller",
        "heat_pump_controller_sh",
        "heat_pump_controller_dhw",
    }


@pytest.mark.base
def test_ems_and_battery_leaves_no_direct_feed_behind() -> None:
    """Catches the two options' meters being merged rather than one of them replacing the other.

    The meter is written out in both options and the two versions are wired differently, so an
    expansion that let the losing option contribute anything would double-count the household:
    every participant would be measured directly *and* through the EMS total.
    """
    expanded, _ = expand_groups(parse_energy_system(Mockups.path("energy_system_mockup.yaml")))

    assert feeds_of(expanded, "meter") == ["ems"]
    assert feeds_of(expanded, "ems") == [
        "occupancy",
        "pv_south",
        "pv_east",
        "heat_pump",
        "heating_rod",
        "battery",
    ]


@pytest.mark.base
def test_a_selection_naming_no_option_lists_the_options_the_variant_has() -> None:
    """Catches a rejection that leaves the author guessing which names would have worked.

    A misspelled option name is the most likely mistake in a variant block, and the repair is one
    of a closed and usually short set, so not listing it would waste the one thing the loader
    knows and the author does not remember.
    """
    with pytest.raises(EnergySystemFormatError) as raised:
        load_energy_system(Documents.UNKNOWN_SELECTION)

    assert raised.value.error_id is EnergySystemErrorId.UNKNOWN_VARIANT_OPTION
    message = str(raised.value)
    assert "'metering'" in message and "with_battery" in message
    assert "with_ems" in message and "bare" in message


@pytest.mark.base
def test_a_variant_with_no_options_is_refused() -> None:
    """Catches an empty options mapping being read as a variant that decides nothing.

    A variant is a choice, and a choice between nothing is a block whose ``selected`` line can
    never be satisfied; accepting it would turn a typo in the indentation of the options into a
    system silently missing everything the variant was meant to contribute.
    """
    with pytest.raises(EnergySystemFormatError) as raised:
        load_energy_system(Documents.EMPTY_OPTIONS)

    assert raised.value.error_id is EnergySystemErrorId.EMPTY_VARIANT
    assert "'metering'" in str(raised.value)


@pytest.mark.base
def test_one_component_in_two_variants_is_refused() -> None:
    """Catches two exclusive choices both deciding the same component.

    Neither selection can resolve the other's, so whichever option wins the component would be
    defined twice; the file is asking for an override, which an option never is.
    """
    with pytest.raises(EnergySystemFormatError) as raised:
        load_energy_system(Documents.TWO_VARIANTS)

    assert raised.value.error_id is EnergySystemErrorId.COMPONENT_IN_TWO_VARIANTS
    message = str(raised.value)
    assert "'meter'" in message and "'metering'" in message and "billing" in message


@pytest.mark.base
def test_a_component_written_both_at_the_top_level_and_in_an_option_is_refused() -> None:
    """Catches the partial override an option is not allowed to be.

    An option states its components in full, so a component that also exists permanently would
    have two definitions with no rule saying which wins — the fallback semantics R15.2 rules out
    precisely because two options must be free to wire the same component differently.
    """
    with pytest.raises(EnergySystemFormatError) as raised:
        load_energy_system(Documents.TOP_LEVEL_AND_OPTION)

    assert raised.value.error_id is EnergySystemErrorId.DUPLICATE_NAME
    message = str(raised.value)
    assert "'meter'" in message and "ungrouped component" in message


@pytest.mark.base
def test_a_variant_named_after_a_component_is_refused() -> None:
    """Catches a variant taking a name a bare reference already resolves to.

    References in this format are bare names, so a variant sharing one with a component or a
    group would make ``- building`` ambiguous — the same reason two groups may not share a name.
    """
    with pytest.raises(EnergySystemFormatError) as raised:
        load_energy_system(Documents.NAME_COLLISION)

    assert raised.value.error_id is EnergySystemErrorId.DUPLICATE_NAME
    assert "'building'" in str(raised.value)


@pytest.mark.base
def test_a_record_of_a_run_with_a_variant_carries_no_variants(tmp_path: Path) -> None:
    """Catches a record that hands its reader a decision the run already made.

    A record is re-executed, and re-executing it must reproduce the run rather than resolve a
    choice again, so the selection is written out: the chosen option's components sit at the top
    level and the block is gone. The audit is then the only place the selection is recorded,
    which is exactly what a reader comparing the record with the authored file needs.
    """
    built = Fixtures.build(MinimalWithAVariant.write(tmp_path / "system"), tmp_path / "results")

    record = realize(built)
    audit = build_audit(built)

    assert not record.variants
    assert "meter" in record.components
    assert "variants" not in dump_energy_system(record)
    assert audit.variant_selections == (
        {
            "variant": MinimalWithAVariant.VARIANT,
            "selected": MinimalWithAVariant.METERED,
            "components": ["meter"],
            "rejected": [MinimalWithAVariant.UNMETERED],
        },
    )
    assert audit.to_document()["expansion"]["variant_selections"][0]["selected"] == MinimalWithAVariant.METERED


@pytest.mark.base
def test_the_unselected_world_still_has_to_be_a_well_formed_file() -> None:
    """Catches the losing option escaping the checks every entry has to pass.

    An option nobody selected today is selected by tomorrow's copy of the file, so a malformed
    entry in it is a defect now rather than later. It is also what lets the identity test trust
    the hand-written side of its comparison: both worlds are known to be files.
    """
    with pytest.raises(EnergySystemFormatError) as raised:
        load_energy_system(Documents.SILENT_OPTION_ENTRY)

    assert raised.value.error_id is EnergySystemErrorId.NO_CONFIGURATION_SOURCE
    assert "spare_meter" in str(raised.value)


@pytest.mark.base
def test_the_facts_report_names_both_switches_as_one_knob_surface(capsys) -> None:
    """Catches the report a consumer configures a run from omitting half of the knobs.

    Groups and variants are two constructs to an author and one surface to whoever runs the
    file, so the report lists both: the flag of every group, and the selected option of every
    variant with the alternatives after it. The heat-pump mockup is still refused by the
    class-bound stage, which is what makes this worth asserting — the knobs have to be printed
    before anything can fail, or the report would be useless for the file that needs it most.
    """
    code = main(["energy-system", "facts", str(Mockups.path("energy_system_mockup.yaml"))])
    printed = capsys.readouterr().out

    assert code == ExitCodes.FILE_REJECTED
    assert "knobs" in printed
    assert "groups.ev" in printed and "false" in printed
    assert "variants.electricity_management" in printed
    assert "ems_and_battery  (or direct_metering)" in printed
