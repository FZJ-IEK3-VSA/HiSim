"""Tests for the off rule: what switching a group off removes from an energy-system file.

A group carries one flag, and turning it off has to remove three things together — the
group's components, every ``inputs`` item pointing at them and every ``sizing_sources``
reference to them — or the file left behind is not a system. This module tests each of the
three removals on the smallest file that shows it, plus the two properties the removal has to
have: it is idempotent, and it produces exactly the file an author would have written by hand
had they deleted the add-on instead of switching it off.

The identity property is checked mechanically over every group of every mockup, against a
deletion written independently in this module. That independence is the point of the test: a
hand-deletion that called the expander would only prove the expander equals itself.

Each test states the failure mode it catches, and the error tests assert the identifier and
the names in the message, because a rejection that does not say which line to edit only moves
the guessing to the author.
"""

# clean

from typing import ClassVar, Dict, List, Set, Tuple

import pytest

from hisim.energy_system import (
    ComponentEntry,
    EnergySystemErrorId,
    EnergySystemFile,
    EnergySystemFormatError,
    Group,
    SourceReference,
    dump_energy_system,
    enabled_component_names,
    expand_groups,
    parse_energy_system,
)
from hisim.energy_system.document import RawDocument
from hisim.energy_system.loader import EnergySystemReader
from tests.test_energy_system_loader import Mockups


class DanglingScalarReference(Exception):
    """Signals that a hand-deletion would leave a scalar sizing source without its provider.

    The by-hand deletion this module performs is the reference implementation the expander is
    compared against, so it has to reproduce the expander's one refusal as well as its
    removals. Raising a test-local exception rather than the production one keeps the two
    sides genuinely independent: the comparison asserts that both refuse, not that both use
    the same class.
    """


class Systems:
    """Builds the small documents the individual off-rule tests break or expand.

    A rule test needs a file that is valid except for the one thing under test, and writing
    each of them out in full would bury the interesting lines. The helpers here render a
    grouped skeleton whose group the test switches on or off, and parse inline documents
    written as text in the test that uses them.
    """

    #: An ungrouped consumer plus a one-component group it reads from in all three ways at
    #: once: a bare defaults item, a scalar sizing source and a list sizing source.
    GROUPED: ClassVar[str] = """
schema_version: 3
name: off rule under test
components:
  consumer:
    class: hisim.components.generic_boiler.GenericBoiler
    preset: condensing_gas
    inputs:
      - provider
      - other
    sizing_sources:
      heating_load_in_watt:
        - provider.heating_load_in_watt
        - other.heating_load_in_watt
  other:
    class: hisim.components.building.Building
    preset: standard
groups:
  extra:
    enabled: {enabled}
    components:
      provider:
        class: hisim.components.building.Building
        preset: standard
"""

    @classmethod
    def grouped(cls, *, enabled: bool) -> EnergySystemFile:
        """Parses the grouped skeleton with its one group switched on or off.

        Args:
            enabled: Whether the group is on.

        Returns:
            The parsed file, unvalidated, ready to be expanded.
        """
        return cls.parse(cls.GROUPED.format(enabled="true" if enabled else "false"))

    @classmethod
    def parse(cls, text: str) -> EnergySystemFile:
        """Parses one inline document without touching the filesystem.

        Args:
            text: The whole YAML document.

        Returns:
            The parsed file.
        """
        return EnergySystemReader.build(RawDocument.parse_text(text, "inline"), "inline")


def disable_group(model: EnergySystemFile, group_name: str) -> EnergySystemFile:
    """Returns the file with one group's flag flipped to off, changing nothing else.

    Args:
        model: The parsed file.
        group_name: The group to switch off.

    Returns:
        A copy of the file whose named group is disabled.
    """
    groups = {
        name: (group.model_copy(update={"enabled": False}) if name == group_name else group)
        for name, group in model.groups.items()
    }
    return model.model_copy(update={"groups": groups})


def delete_groups_by_hand(model: EnergySystemFile, group_names: Set[str]) -> EnergySystemFile:
    """Deletes whole groups and every reference to them the way an author editing would.

    This is the independent reference implementation the expander is compared against, so it
    is written straight from the rule rather than from the expander's code: drop the groups,
    drop every input item naming one of their components, drop those components from every
    sizing list, and refuse when a scalar sizing source would be left without a provider.

    Several groups are deleted at once because a file may already carry a switched-off add-on
    of its own, and the comparison is only meaningful when both sides removed the same set.

    Args:
        model: The parsed file.
        group_names: The groups to delete.

    Returns:
        The file as it would read with those add-ons never written.

    Raises:
        DanglingScalarReference: When a surviving entry takes a fact from a component the
            deletion removes and named that component in a scalar source line.
    """
    removed = {member for name in group_names for member in model.groups[name].components}

    def rewrite(entry: ComponentEntry) -> ComponentEntry:
        """Returns one surviving entry with every reference into the deleted group gone."""
        inputs = tuple(item for item in entry.inputs if item.source not in removed)
        sources: Dict[str, object] = {}
        for fact, value in entry.sizing_sources.items():
            if isinstance(value, SourceReference):
                if value.component in removed:
                    raise DanglingScalarReference(f"{entry.name}.{fact} -> {value.component}")
                sources[fact] = value
            else:
                sources[fact] = tuple(item for item in value if item.component not in removed)
        return entry.model_copy(update={"inputs": inputs, "sizing_sources": sources})

    components = {name: rewrite(entry) for name, entry in model.components.items()}
    groups: Dict[str, Group] = {}
    for name, group in model.groups.items():
        if name in group_names:
            continue
        members = {member: rewrite(entry) for member, entry in group.components.items()}
        groups[name] = group.model_copy(update={"components": members})
    return model.model_copy(update={"components": components, "groups": groups})


def mockup_group_pairs() -> List[Tuple[str, str]]:
    """Lists every (mockup, group) pair the identity property is checked over.

    Reading the pairs off the mockups rather than hard-coding them means a group added to a
    mockup is covered without anyone remembering to extend the test.

    Returns:
        One pair per group of each mockup, mockups in their canonical order.
    """
    pairs: List[Tuple[str, str]] = []
    for name in Mockups.NAMES:
        model = parse_energy_system(Mockups.path(name))
        pairs.extend((name, group) for group in model.groups)
    return pairs


@pytest.mark.base
def test_an_enabled_group_keeps_every_component_and_reference() -> None:
    """Catches an expansion that dissolves or prunes a group that is switched on.

    Groups are structure, not scope: with the flag on, expansion must be a no-op down to the
    group itself, or a realized record could never be compared with the file it came from.
    """
    model = Systems.grouped(enabled=True)
    expanded, record = expand_groups(model)

    assert record.is_empty
    assert set(expanded.groups) == {"extra"}
    assert dump_energy_system(expanded) == dump_energy_system(model)


@pytest.mark.base
def test_a_disabled_group_takes_its_components_inputs_and_list_entries_with_it() -> None:
    """Catches an off rule that removes the components but leaves references pointing at them.

    All three removals are asserted at once because they are one rule: a file that keeps an
    input item or a list entry naming a component that no longer exists is not a system, and
    the failure would only surface much later as a wiring or a sizing error.
    """
    expanded, record = expand_groups(Systems.grouped(enabled=False))

    assert enabled_component_names(expanded) == ("consumer", "other")
    assert record.disabled_groups == ("extra",)
    assert record.dropped_components == ("provider",)
    assert [(item.consumer, item.source) for item in record.dropped_input_items] == [("consumer", "provider")]
    consumer = expanded.components["consumer"]
    assert [item.source for item in consumer.inputs] == ["other"]
    assert consumer.sizing_sources["heating_load_in_watt"] == (
        SourceReference(component="other", fact="heating_load_in_watt"),
    )


@pytest.mark.base
def test_a_shrinking_sizing_list_is_recorded_with_its_before_and_after() -> None:
    """Catches a list that shrinks silently, changing a many-reader's result without a trace.

    A list may legally lose members, so the only protection against a switched-off provider
    quietly halving a sum is that the shrink is written down where a reader of the run sees it.
    """
    _, record = expand_groups(Systems.grouped(enabled=False))

    assert len(record.shrunk_sizing_lists) == 1
    shrink = record.shrunk_sizing_lists[0]
    assert (shrink.consumer, shrink.fact) == ("consumer", "heating_load_in_watt")
    assert shrink.before == ("provider.heating_load_in_watt", "other.heating_load_in_watt")
    assert shrink.after == ("other.heating_load_in_watt",)
    assert shrink.removed == ("provider.heating_load_in_watt",)
    assert any("shrank from" in line for line in record.describe())


@pytest.mark.base
def test_a_sizing_list_may_shrink_all_the_way_to_empty() -> None:
    """Catches an expansion that treats an emptied list as a dangling reference.

    An empty list is the format's explicit "nobody provides this fact", so a list whose last
    provider is switched off must become that rather than an error — otherwise switching an
    add-on off would force an edit in every consumer that ever listed it.
    """
    model = Systems.parse(
        """
schema_version: 3
name: emptied list
components:
  consumer:
    class: hisim.components.generic_boiler.GenericBoiler
    preset: condensing_gas
    sizing_sources:
      heating_load_in_watt:
        - provider.heating_load_in_watt
groups:
  extra:
    enabled: false
    components:
      provider:
        class: hisim.components.building.Building
        preset: standard
"""
    )
    expanded, record = expand_groups(model)

    assert expanded.components["consumer"].sizing_sources["heating_load_in_watt"] == ()
    assert record.shrunk_sizing_lists[0].after == ()


@pytest.mark.base
def test_a_scalar_sizing_source_into_a_disabled_group_is_refused() -> None:
    """Catches an off rule that silently deletes a decision the author made deliberately.

    A scalar source line exists because more than one component could provide the fact, so
    dropping it would hand the choice back to the machine — and the machine would either pick
    a different provider or fail much later with a message about a fact, not about a group.
    """
    model = Systems.parse(
        """
schema_version: 3
name: dangling scalar
components:
  consumer:
    class: hisim.components.generic_boiler.GenericBoiler
    preset: condensing_gas
    sizing_sources:
      heating_load_in_watt: provider.heating_load_in_watt
groups:
  extra:
    enabled: false
    components:
      provider:
        class: hisim.components.building.Building
        preset: standard
"""
    )
    with pytest.raises(EnergySystemFormatError) as raised:
        expand_groups(model)

    assert raised.value.error_id is EnergySystemErrorId.DISABLED_SIZING_SOURCE
    message = str(raised.value)
    assert "consumer" in message and "provider" in message and "'extra'" in message
    assert "heating_load_in_watt" in message


@pytest.mark.base
@pytest.mark.parametrize("name", Mockups.NAMES)
def test_expanding_a_mockup_twice_changes_nothing_the_second_time(name: str) -> None:
    """Catches an expansion that is not idempotent and therefore not a fixed point.

    Every later stage sees the expanded file, and a record writer emits it, so an expansion
    that keeps changing its own output would make a re-executed record differ from the record
    it was written from.
    """
    once, _ = expand_groups(parse_energy_system(Mockups.path(name)))
    twice, record = expand_groups(once)

    assert record.is_empty
    assert dump_energy_system(twice) == dump_energy_system(once)


@pytest.mark.base
@pytest.mark.parametrize("name", Mockups.NAMES)
def test_expanding_a_mockup_leaves_the_loaded_file_untouched(name: str) -> None:
    """Catches an expansion that mutates the file it was handed instead of building a new one.

    The authored form has to survive the run: a record writer emits it next to the realized
    one, and a caller may expand the same file twice with different groups switched off.
    """
    model = parse_energy_system(Mockups.path(name))
    before = dump_energy_system(model)
    expand_groups(model)

    assert dump_energy_system(model) == before


@pytest.mark.base
@pytest.mark.parametrize(("mockup", "group"), mockup_group_pairs())
def test_disabling_a_group_equals_deleting_it_by_hand(mockup: str, group: str) -> None:
    """Catches an off rule that produces something other than the hand-written file.

    This is the property that makes groups safe to use: an author switching an add-on off gets
    exactly the system they would have written without it, so a group can never introduce a
    difference of its own. Where the hand deletion cannot be completed — a scalar source line
    would be left without its provider — the expander must refuse for the same reason.
    """
    model = parse_energy_system(Mockups.path(mockup))
    already_off = {name for name, other in model.groups.items() if not other.enabled}
    try:
        expected = delete_groups_by_hand(model, already_off | {group})
    except DanglingScalarReference:
        with pytest.raises(EnergySystemFormatError) as raised:
            expand_groups(disable_group(model, group))
        assert raised.value.error_id is EnergySystemErrorId.DISABLED_SIZING_SOURCE
        return

    expanded, _ = expand_groups(disable_group(model, group))
    assert dump_energy_system(expanded) == dump_energy_system(expected)
