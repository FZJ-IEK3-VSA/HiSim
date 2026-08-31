"""Expansion: turning a file with switches into the one system that actually runs.

The format has two switches. A group is a named set of components carrying one flag, so that
an add-on — a photovoltaic string, a battery with its energy management, an electric vehicle
— can be switched off without deleting text and without keeping a second copy of the file. A
variant is an exclusive choice between named options, exactly one of which is live, so that
two alternative worlds — a house with an energy management system, a house with a bare meter
wired straight to every participant — can live in one file and wire the same component
differently. Both are resolved here, in one pass, because they raise the same question.

Switching something off has to be more than dropping the components, because the rest of the
file still points at them: a consumer lists them under ``inputs`` and a sizing line may name
one as the provider of a fact. This module removes all three together, so that what reaches
validation, configuration and resolution is one plain system with no switched-off remnants.

The rule the removal follows is deliberately asymmetric, and the asymmetry is the point. An
``inputs`` item pointing at a disabled component is *dropped*: an input is a wiring wish, and
a wish about something that no longer exists is simply not fulfilled. A *list* of sizing
sources shrinks, possibly to empty, because a list already means "however many of these
exist". A *scalar* sizing source, by contrast, is a decision the author made between several
providers, and silently deleting it would hand the choice back to the machine — so a scalar
reference into a disabled group is a hard error naming the consumer, the fact and the group.

Everything the expansion removed or shrank is collected in an :class:`ExpansionRecord`, which
is what lets a later stage show a reader why the running system differs from the file. The
expansion is pure — it builds a new file and never mutates the one handed in — and it is
idempotent, since expanding a file that has no disabled group and no variant left changes
nothing.

The two switches are treated differently in one respect, and the difference is deliberate.
Enabled groups survive as groups: they are the file's structure, not its scope, and dissolving
them would make the expanded file impossible to compare against the one on disk. A variant,
by contrast, does not survive at all — the selected option's components join the top level and
the block is gone — because a selection is a decision, not structure: once it is made there is
nothing left to switch, and nothing downstream of this module ever has to know that a variant
was there.
"""

# clean

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Set, Tuple

from hisim.energy_system.errors import EnergySystemErrorId, EnergySystemFormatError
from hisim.energy_system.model import (
    AnyInputItem,
    AnySizingSource,
    ComponentEntry,
    EnergySystemFile,
    Group,
    SourceReference,
)


@dataclass(frozen=True)
class Removal:
    """Why one component of the authored file is not part of the running system.

    A removed component is named in two places a reader has to be able to act on: the report
    of what the expansion took out, and the refusal of a scalar sizing line left without its
    provider. Both need the switch that removed it and the repair that would bring it back,
    and the two switches offer different repairs — a group is turned on, a variant selects a
    different option — so the phrasing travels with the removal instead of being reinvented
    at each message.
    """

    switch: str
    remedy: str


@dataclass(frozen=True)
class VariantSelection:
    """One variant's resolution: the option that won and the components it contributed.

    The record of an expansion has to state what a variant decided, because a realized record
    carries no variants at all — the selection is resolved into the top level — and a reader
    comparing the two files would otherwise find components that appear out of nowhere. The
    options that lost are named as well, since "which alternatives existed" is exactly what a
    reader of a run asks next.
    """

    variant: str
    selected: str
    components: Tuple[str, ...]
    rejected: Tuple[str, ...]


@dataclass(frozen=True)
class DroppedInputItem:
    """One ``inputs`` item removed because the component it drew from was switched off.

    The record keeps both ends and the position, because a reader asking "why is my meter
    not fed any more" needs the consumer, the vanished source and enough of the item to find
    the line again in the authored file. The kind is the item's own discriminator — bare
    defaults, an explicit wire or an aggregator feed — since the three read very differently
    in a report and a dropped feed is the one most likely to change results silently.
    """

    consumer: str
    source: str
    index: int
    item_kind: str


@dataclass(frozen=True)
class ShrunkSizingList:
    """One ``sizing_sources`` list that lost entries because their providers were switched off.

    A list is the only sizing shape that may lose members without an error, so the shrink is
    the one removal that has to be visible somewhere: a many-reader whose list quietly went
    from three providers to one still resolves, and the number it computes changes. Keeping
    the full before and after — not just the removed names — means the record reads as the
    diff it is, and an empty ``after`` is exactly the legal "no provider at all" case.
    """

    consumer: str
    fact: str
    before: Tuple[str, ...]
    after: Tuple[str, ...]

    @property
    def removed(self) -> Tuple[str, ...]:
        """The references that disappeared from the list, in the order they were written.

        Returns:
            The dotted references present in :attr:`before` and absent from :attr:`after`.
        """
        surviving = set(self.after)
        return tuple(reference for reference in self.before if reference not in surviving)


@dataclass(frozen=True)
class ExpansionRecord:
    """Everything the off rule removed from one file, ready to be shown to a reader.

    The record answers the question a realized run raises for anyone comparing it with the
    authored file: which groups were off, which option each variant selected, which components
    therefore vanished, which input items were dropped with them and which sizing lists shrank.
    It is deliberately data and not prose, so the same record can be rendered into a log line,
    into an audit file or into an assertion in a test.

    An expansion that removed nothing produces an empty record rather than ``None``, which
    keeps every consumer of the record free of a case distinction.
    """

    disabled_groups: Tuple[str, ...] = ()
    selections: Tuple[VariantSelection, ...] = ()
    dropped_components: Tuple[str, ...] = ()
    dropped_input_items: Tuple[DroppedInputItem, ...] = ()
    shrunk_sizing_lists: Tuple[ShrunkSizingList, ...] = ()

    @property
    def is_empty(self) -> bool:
        """Whether the expansion changed nothing at all.

        Returns:
            ``True`` when no group was disabled, no variant was resolved and consequently
            nothing was removed.
        """
        return not (
            self.disabled_groups
            or self.selections
            or self.dropped_components
            or self.dropped_input_items
            or self.shrunk_sizing_lists
        )

    def describe(self) -> Tuple[str, ...]:
        """Renders the record as one human-readable line per removal.

        The lines are meant for a log or an audit block and name both ends of every removal,
        following the same rule the error messages follow: a reader must be able to act on
        the line without opening the file next to it.

        Returns:
            One line per disabled group, resolved variant, dropped item and shrunk list, in
            that order.
        """
        lines: List[str] = []
        for group_name in self.disabled_groups:
            members = ", ".join(name for name in self.dropped_components)
            lines.append(f"group '{group_name}' is disabled; components removed: {members or '<none>'}.")
        for selection in self.selections:
            contributed = ", ".join(selection.components) or "<none>"
            lines.append(
                f"variant '{selection.variant}' selected '{selection.selected}', which contributed "
                f"{contributed}; not selected: {', '.join(selection.rejected) or '<none>'}."
            )
        for item in self.dropped_input_items:
            lines.append(
                f"'{item.consumer}' lost its input item {item.index} from '{item.source}' "
                f"({item.item_kind}) with the component."
            )
        for shrink in self.shrunk_sizing_lists:
            lines.append(
                f"'{shrink.consumer}'.sizing_sources.{shrink.fact} shrank from "
                f"[{', '.join(shrink.before)}] to [{', '.join(shrink.after)}]."
            )
        return tuple(lines)


class GroupExpander:
    """Applies both switches of one file and collects what they removed.

    The expander is built for a single file, computes the set of removed component names once
    — the members of every disabled group plus the members of every option a variant did not
    select — and then rewrites every surviving entry against it. It is single-use and holds no
    state a caller should read directly; :func:`expand_groups` is the entry point, and the
    two results — the expanded file and the record — are what the rest of the package works
    with.

    Rewriting rather than mutating is what keeps the guarantee that the loaded file stays
    exactly as it was read, so a record writer can still emit the authored form next to the
    realized one.
    """

    def __init__(self, model: EnergySystemFile) -> None:
        """Prepares the expansion by collecting the components both switches take away.

        A name that a variant writes in several options is only removed when the selected
        option does not have it: the two entries describe the same component in two worlds,
        and the selected world's entry is the one that survives.

        Args:
            model: The parsed energy system, before any switch has been applied.
        """
        self.model = model
        self.disabled_groups: Tuple[str, ...] = tuple(
            name for name, group in model.groups.items() if not group.enabled
        )
        self.selections: Tuple[VariantSelection, ...] = tuple(
            VariantSelection(
                variant=name,
                selected=variant.selected,
                components=tuple(variant.selected_components()),
                rejected=tuple(option for option in variant.options if option != variant.selected),
            )
            for name, variant in model.variants.items()
        )
        surviving = set(model.all_components())
        self.dropped: Dict[str, Removal] = {}
        for group_name in self.disabled_groups:
            for component_name in model.groups[group_name].components:
                self.dropped[component_name] = Removal(
                    switch=f"group '{group_name}' disabled",
                    remedy=f"enable the group '{group_name}'",
                )
        for name, variant in model.variants.items():
            for option_name, option in variant.options.items():
                if option_name == variant.selected:
                    continue
                for component_name in option.components:
                    if component_name in surviving:
                        continue
                    self.dropped[component_name] = Removal(
                        switch=f"variant '{name}' left out by selecting '{variant.selected}'",
                        remedy=f"select the option '{option_name}' of the variant '{name}'",
                    )
        self.dropped_input_items: List[DroppedInputItem] = []
        self.shrunk_sizing_lists: List[ShrunkSizingList] = []

    def expand(self) -> Tuple[EnergySystemFile, ExpansionRecord]:
        """Builds the live system and the record of what was taken out of it.

        The selected option's components are appended to the top level in document order,
        which is where an author writing the same system without the variant would have put
        them, and the variants block is dropped: from here on nothing knows that a choice was
        ever offered.

        Returns:
            The expanded file — the same document with the disabled groups, the unselected
            options and every reference into either gone — and the record of the removals.

        Raises:
            EnergySystemFormatError: ``EF-42`` when a scalar ``sizing_sources`` reference
                names a component a switch took away, since that choice cannot be remade
                automatically.
        """
        components = {name: self._rewrite(entry) for name, entry in self.model.components.items()}
        for variant in self.model.variants.values():
            for name, entry in variant.selected_components().items():
                components[name] = self._rewrite(entry)
        groups: Dict[str, Group] = {}
        for group_name, group in self.model.groups.items():
            if group_name in self.disabled_groups:
                continue
            members = {name: self._rewrite(entry) for name, entry in group.components.items()}
            groups[group_name] = group.model_copy(update={"components": members})
        expanded = self.model.model_copy(
            update={"components": components, "groups": groups, "variants": {}}
        )
        self._assert_no_dangling_reference(expanded)
        record = ExpansionRecord(
            disabled_groups=self.disabled_groups,
            selections=self.selections,
            dropped_components=tuple(self.dropped),
            dropped_input_items=tuple(self.dropped_input_items),
            shrunk_sizing_lists=tuple(self.shrunk_sizing_lists),
        )
        return expanded, record

    def _rewrite(self, entry: ComponentEntry) -> ComponentEntry:
        """Returns one surviving entry with every reference into a removed component gone.

        Args:
            entry: A component that is part of the live set.

        Returns:
            The entry itself when it names nothing switched off or left out, or a copy
            without the dropped input items and with the shrunk sizing lists.

        Raises:
            EnergySystemFormatError: ``EF-42`` for a dangling scalar sizing reference.
        """
        if not self.dropped:
            return entry
        inputs = self._surviving_inputs(entry)
        sizing_sources = self._surviving_sizing_sources(entry)
        if inputs == entry.inputs and sizing_sources == dict(entry.sizing_sources):
            return entry
        return entry.model_copy(update={"inputs": inputs, "sizing_sources": sizing_sources})

    def _surviving_inputs(self, entry: ComponentEntry) -> Tuple[AnyInputItem, ...]:
        """Filters an entry's input list down to the items whose source still exists.

        The index recorded is the item's position in the *authored* list, because that is the
        position a reader counts when looking for the line, and a filtered list renumbers.

        Args:
            entry: The consuming component.

        Returns:
            The surviving items, in the order they were written.
        """
        surviving: List[AnyInputItem] = []
        for index, item in enumerate(entry.inputs):
            if item.source in self.dropped:
                self.dropped_input_items.append(
                    DroppedInputItem(
                        consumer=entry.name, source=item.source, index=index, item_kind=item.item_kind
                    )
                )
                continue
            surviving.append(item)
        return tuple(surviving)

    def _surviving_sizing_sources(self, entry: ComponentEntry) -> Dict[str, AnySizingSource]:
        """Rewrites an entry's sizing block, shrinking lists and rejecting dangling scalars.

        Args:
            entry: The consuming component.

        Returns:
            The block with every reference into a removed component gone; a fact whose list
            lost every member keeps an empty list, which is the format's explicit "nobody
            provides this".

        Raises:
            EnergySystemFormatError: ``EF-42`` when a scalar reference names a component a
                switch took away.
        """
        rewritten: Dict[str, AnySizingSource] = {}
        for fact, value in entry.sizing_sources.items():
            if isinstance(value, SourceReference):
                removal = self.dropped.get(value.component)
                if removal is not None:
                    raise self._dangling_scalar_error(entry.name, fact, value, removal)
                rewritten[fact] = value
                continue
            surviving = tuple(reference for reference in value if reference.component not in self.dropped)
            if len(surviving) != len(value):
                self.shrunk_sizing_lists.append(
                    ShrunkSizingList(
                        consumer=entry.name,
                        fact=fact,
                        before=tuple(reference.text for reference in value),
                        after=tuple(reference.text for reference in surviving),
                    )
                )
            rewritten[fact] = surviving
        return rewritten

    @classmethod
    def _dangling_scalar_error(
        cls, consumer: str, fact: str, reference: SourceReference, removal: Removal
    ) -> EnergySystemFormatError:
        """Builds the ``EF-42`` rejection of a scalar source left without its provider.

        The message names all three parties — the consumer, the fact and the switch that took
        the provider away — and offers the two repairs that exist, because the machine cannot
        pick between them: the author either wanted the provider in the running system, or
        wanted a different provider, and only the author knows which.

        Args:
            consumer: The component whose sizing line dangles.
            fact: The fact the line answers for.
            reference: The reference as written.
            removal: The switch that removed the referenced component, and its repair.

        Returns:
            The exception to raise.
        """
        return EnergySystemFormatError(
            EnergySystemErrorId.DISABLED_SIZING_SOURCE,
            f"components.{consumer}.sizing_sources.{fact}",
            f"'{consumer}' takes '{fact}' from '{reference.component}', which "
            f"{removal.switch}.",
            remedy=(
                f"Remove the line and let the remaining providers decide, name another "
                f"provider, or {removal.remedy}."
            ),
        )

    @classmethod
    def _assert_no_dangling_reference(cls, expanded: EnergySystemFile) -> None:
        """Verifies that nothing in the expanded file still names a component it lost.

        This is the expansion's own postcondition rather than a user-facing rule: every path
        that could leave a dangling name has already been handled above, so a violation here
        is a bug in this module and not a problem with the file. Checking it on every run —
        not only in a test — is cheap and keeps a future edit from quietly reintroducing the
        remnants the switches exist to remove.

        Args:
            expanded: The file the expansion produced.
        """
        names: Set[str] = set(expanded.all_components())
        for name, entry in expanded.all_components().items():
            for item in entry.inputs:
                assert item.source in names, f"expansion left '{name}' with an input from '{item.source}'"
            for fact, reference in entry.sizing_references():
                assert reference.component in names, (
                    f"expansion left '{name}'.sizing_sources.{fact} pointing at '{reference.component}'"
                )


def expand_groups(model: EnergySystemFile) -> Tuple[EnergySystemFile, ExpansionRecord]:
    """Applies both switches and returns the system that actually runs, plus what it lost.

    This is the second stage of the lifecycle, between reading the document and checking it:
    every later stage — structural validation, class binding, configuration, sizing, wiring —
    sees only the live set, so no rule anywhere else has to know that groups exist and none
    of them ever meets a variant at all. In particular the sizing rule that a bare fact needs
    exactly one provider is evaluated over the live components alone, which is what makes
    switching an add-on off, or selecting the other option, a local edit instead of a change
    rippling through every consumer's source lines.

    The function is pure and idempotent: expanding an already expanded file returns an equal
    file and an empty record, because a file whose groups are all enabled and whose variants
    are resolved has nothing left to drop.

    Args:
        model: The parsed energy system, groups and variants included.

    Returns:
        A pair of the expanded file and the record of the removals.

    Raises:
        EnergySystemFormatError: ``EF-42`` when a scalar ``sizing_sources`` reference names a
            component a switch removed, naming the consumer, the fact and the switch.
    """
    return GroupExpander(model).expand()


def enabled_component_names(model: EnergySystemFile) -> Tuple[str, ...]:
    """Returns the names of the components that survive expansion, in document order.

    Several callers need the live set without needing the expanded file — a report listing
    what a run contains, a check comparing the file against a realized record — and computing
    it from the switches directly saves them the rewrite. Ungrouped components come first,
    then the members of each enabled group, then the components of each variant's selected
    option, all in document order.

    Args:
        model: The parsed energy system.

    Returns:
        The live component names.
    """
    names: List[str] = list(model.components)
    for group in model.groups.values():
        if group.enabled:
            names.extend(group.components)
    for variant in model.variants.values():
        names.extend(variant.selected_components())
    return tuple(names)
