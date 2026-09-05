"""The second pass: assembling the grouped file, and proving it against every probe column.

Once a person has said what each observed difference means, the file that says it can be built
mechanically. Components nobody touched stay at the top level; a group is the components assigned
to it under the flag the baseline column gives it; a variant option is the entry each of its
components had in a configuration that selected that option, written out in full because two worlds
may wire the same component differently. That is all this module does with the decisions — it
applies them, it never makes one.

The interesting half is the proof. Every probe column is an assertion: put the file's switches where
that column stands, resolve it to a plain system, and the result must be the flat recording of that
column, byte for byte. No new simulations are needed for any of it, because the flat recordings
already exist from the probe pass, and the baseline column's assertion is the strong one — it says
the grouped file is the committed twin with structure added and nothing else changed.

One thing the file deliberately does not carry, and it has to be said out loud rather than
discovered. A value a consumer sets — the photovoltaic share is the standing case — is not
structure, so the grouped file states the baseline's value for it and the difference becomes a
*knob*: something the realization is handed rather than something the file determines. The knobs are
computed here, listed in the report, and applied when a column is realized, so what each column
proves is precisely "everything except these named values". A column with a long knob list proves
less than one with none, and the report says how many each has so that nobody has to guess.
"""

# clean

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Dict, Mapping, Optional, Tuple

from hisim.energy_system.emitter import EnergySystemEmitter
from hisim.energy_system.groups import GroupExpander
from hisim.energy_system.model import ComponentEntry, EnergySystemFile, Group, Variant, VariantOption
from hisim.energy_system.recording.grouping import AssignmentKind, Grouping
from hisim.energy_system.recording.matrix import DocumentDifference, EntryComparison, ProbeMatrix


@dataclass(frozen=True)
class Knob:
    """One value the grouped file does not determine, and what each column sets it to.

    A knob is the residue of the pass: after the groups and the variants have absorbed every
    difference that is about membership, whatever still differs between a column and the entry the
    file holds for that component is a value somebody chooses. Naming it, per component and per
    dotted path into the entry, is what turns "the file is a base file with knobs" from a claim into
    a list a reader can check against the consumers.
    """

    component: str
    path: str
    stated: Any
    values: Mapping[str, Any]

    def describe(self) -> str:
        """Renders the knob as one line of the report.

        Returns:
            The component and path, the value the file states, and the value of every column that
            sets it to something else.
        """
        settings = ", ".join(f"{column}={value!r}" for column, value in self.values.items())
        return f"{self.component}.{self.path}: the file states {self.stated!r}; {settings}."


class GroupedSystemBuilder:
    """Turns one grouping decision plus one probe matrix into the grouped energy-system file.

    The builder is a pure function of its two inputs, which is what lets the whole assembly be
    tested against hand-written recordings. It owns three decisions and no others: where each
    component's entry is taken from, what order the file writes things in, and which residual
    differences become knobs.

    Where an entry comes from follows the world it describes. A component at the top level or in a
    group is written as the baseline recorded it, because the file as written *is* the baseline
    column. A component inside a variant option is written as the first column selecting that
    option recorded it, because that is the only place that world was observed.
    """

    def __init__(self, grouping: Grouping, matrix: ProbeMatrix) -> None:
        """Prepares the builder for one setup.

        Args:
            grouping: The decisions a person made, already checked against the matrix.
            matrix: The three-state table and the recordings behind it.
        """
        self.grouping = grouping
        self.matrix = matrix
        self.baseline = matrix.recordings[matrix.baseline]

    def build(self) -> EnergySystemFile:
        """Assembles the grouped file.

        Returns:
            The model, carrying the ungrouped components in the baseline's registration order, one
            group per ``group:`` name and one variant per ``variant:`` name, with the baseline
            column's flag and selection.
        """
        model = self.baseline.model
        return model.model_copy(
            update={
                "components": self._top_level(),
                "groups": self._groups(),
                "variants": self._variants(),
            }
        )

    def _top_level(self) -> Dict[str, ComponentEntry]:
        """The components no switch owns, in the baseline's registration order.

        Returns:
            One entry per component whose assignment is ordinary or an override.
        """
        kept = {}
        for name, entry in self.baseline.entries().items():
            if self.grouping.assignment(name).kind in (AssignmentKind.ORDINARY, AssignmentKind.OVERRIDE):
                kept[name] = entry
        return kept

    def _groups(self) -> Dict[str, Group]:
        """The groups the assignments create, each under the baseline column's flag.

        Returns:
            One group per name, holding its members in table order.
        """
        groups = {}
        for name in self.grouping.group_names():
            groups[name] = Group(
                name=name,
                enabled=self.grouping.selection(self.matrix.baseline).groups.get(name, True),
                components=self._members(
                    self.grouping.members(AssignmentKind.GROUP, name), self.matrix.columns
                ),
            )
        return groups

    def _variants(self) -> Dict[str, Variant]:
        """The variants the assignments create, each resolved to what the baseline column selected.

        Returns:
            One variant per name, each option holding the entries the columns selecting it recorded.
        """
        variants = {}
        for name, options in self.grouping.variant_options().items():
            written = {}
            for option in options:
                written[option] = VariantOption(
                    name=option,
                    components=self._members(
                        self.grouping.members(AssignmentKind.VARIANT, name, option),
                        self._columns_selecting(name, option),
                    ),
                )
            variants[name] = Variant(
                name=name,
                selected=self.grouping.selection(self.matrix.baseline).variants.get(name, options[0]),
                options=written,
            )
        return variants

    def _members(self, components: Tuple[str, ...], columns: Tuple[str, ...]) -> Dict[str, ComponentEntry]:
        """Collects the entries of one switch's members, each from the first column that has it.

        Args:
            components: The component names the switch owns, in table order.
            columns: The columns whose world the switch is live in, in preference order.

        Returns:
            One entry per member that any of those columns recorded, in table order.
        """
        members: Dict[str, ComponentEntry] = {}
        for component in components:
            entry = self._first_entry(component, columns)
            if entry is not None:
                members[component] = entry
        return members

    def _columns_selecting(self, variant: str, option: str) -> Tuple[str, ...]:
        """The probe columns whose selection of one variant is one option.

        Args:
            variant: The variant's name.
            option: The option's name.

        Returns:
            The columns, in matrix order.
        """
        return tuple(
            column
            for column in self.matrix.columns
            if self.grouping.selection(column).variants.get(variant) == option
        )

    def _first_entry(self, component: str, columns: Tuple[str, ...]) -> Optional[ComponentEntry]:
        """The entry the first of these columns that has this component recorded for it.

        Args:
            component: The component wanted.
            columns: The columns to look in, in preference order.

        Returns:
            The entry, or ``None`` when none of the columns has the component at all.
        """
        for column in columns:
            entry = self.matrix.recordings[column].entries().get(component)
            if entry is not None:
                return entry
        return None

    def source_column(self, component: str, column: str) -> str:
        """Which column's recording the grouped file holds this component's entry from.

        A knob is the difference between that entry and the one the column being realized recorded,
        so the whole knob computation turns on this question and it is answered in one place.

        Args:
            component: The component in question.
            column: The column being realized.

        Returns:
            The column whose entry the file states for that component in the world ``column``
            selects.
        """
        assignment = self.grouping.assignment(component)
        if assignment.kind is AssignmentKind.VARIANT:
            option = self.grouping.selection(column).variants.get(assignment.name)
            candidates = self._columns_selecting(assignment.name, option or "")
        else:
            candidates = self.matrix.columns
        for candidate in candidates:
            if component in self.matrix.recordings[candidate].entries():
                return candidate
        return column

    def knobs(self) -> Tuple[Knob, ...]:
        """Every value the file does not determine, in table order.

        Returns:
            One knob per component and dotted path at which some column disagrees with the entry
            the file states for it.
        """
        collected: Dict[Tuple[str, str], Dict[str, Any]] = {}
        stated: Dict[Tuple[str, str], Any] = {}
        for row in self.matrix.rows:
            for column in row.present_in:
                for path, value in self.differences(row.component, column).items():
                    key = (row.component, path)
                    collected.setdefault(key, {})[column] = value
                    stated.setdefault(key, self._stated(row.component, column, path))
        return tuple(
            Knob(component=component, path=path, stated=stated[(component, path)], values=values)
            for (component, path), values in collected.items()
        )

    def differences(self, component: str, column: str) -> Dict[str, Any]:
        """The residual difference between the entry the file states and the one a column recorded.

        Args:
            component: The component in question.
            column: The column being realized.

        Returns:
            The dotted paths at which the two disagree, with the column's values; empty when the
            file already states exactly what the column recorded.
        """
        source = self.source_column(component, column)
        if source == column:
            return {}
        return self.matrix.differences(component, column, source=source)

    def _stated(self, component: str, column: str, path: str) -> Any:
        """The value the file states at one dotted path of one component's entry.

        Args:
            component: The component in question.
            column: A column whose world the entry is taken from.
            path: The dotted path into the entry document.

        Returns:
            The stated value, or the absence marker when the file's entry has no such path.
        """
        entry = self.matrix.recordings[self.source_column(component, column)].entries().get(component)
        block: Any = EntryComparison.document(entry) if entry is not None else {}
        for key in path.split(DocumentDifference.SEPARATOR):
            if not isinstance(block, Mapping) or key not in block:
                return DocumentDifference.ABSENT
            block = block[key]
        return block


class ColumnRealizer:
    """Puts one grouped file's switches where a column stands and writes out the plain system.

    Realizing is the format's own expansion followed by two steps the format does not have: enabled
    groups are dissolved into the top level, because a flat recording has no groups to compare
    against, and the column's knobs are applied. Both are part of what "the same system" means when
    a structured file and a flat recording are held against each other.

    The result is the document rather than the model, because the comparison is on bytes and going
    back through the model would only add a conversion that could differ from the one the recorder
    used.
    """

    #: The keys of an expanded file that the flat form must not carry.
    EMPTIED: ClassVar[Tuple[str, ...]] = ("groups", "variants")

    def __init__(self, grouped: EnergySystemFile, builder: GroupedSystemBuilder) -> None:
        """Prepares the realizer for one grouped file.

        Args:
            grouped: The file as it is committed, switches at the baseline column's positions.
            builder: The builder that produced it, asked for each column's knobs.
        """
        self.grouped = grouped
        self.builder = builder

    def flat_model(self, column: str) -> EnergySystemFile:
        """Resolves the grouped file at one column's switch positions into a plain system.

        Args:
            column: The probe column to realize.

        Returns:
            The expanded file with every surviving group dissolved into the top level, ordered the
            way that column's flat recording lists them. The blocks of the grouped file lose the
            setup's registration order — a group's members may have been registered between two
            ungrouped components — and the recording is the one place that order was observed, so
            the dissolved components are laid out by it; a component the recording does not have
            stays at the end, where the byte comparison will name it.
        """
        selection = self.builder.grouping.selection(column)
        switched = self.grouped.model_copy(
            update={
                "groups": {
                    name: group.model_copy(update={"enabled": selection.groups.get(name, group.enabled)})
                    for name, group in self.grouped.groups.items()
                },
                "variants": {
                    name: variant.model_copy(
                        update={"selected": selection.variants.get(name, variant.selected)}
                    )
                    for name, variant in self.grouped.variants.items()
                },
            }
        )
        expanded, _ = GroupExpander(switched).expand()
        dissolved: Dict[str, ComponentEntry] = dict(expanded.components)
        for group in expanded.groups.values():
            dissolved.update(group.components)
        observed = self.builder.matrix.recordings[column].entries()
        components: Dict[str, ComponentEntry] = {
            name: dissolved[name] for name in observed if name in dissolved
        }
        components.update({name: entry for name, entry in dissolved.items() if name not in components})
        return expanded.model_copy(update={"components": components, "groups": {}, "variants": {}})

    def document(self, column: str) -> Dict[str, Any]:
        """The realized document of one column, knobs applied.

        Args:
            column: The probe column to realize.

        Returns:
            The plain nested mapping the flat recording of that column should equal.
        """
        document = EnergySystemEmitter.to_document(self.flat_model(column))
        entries = document.get("components", {})
        for component in list(entries):
            differences = self.builder.differences(component, column)
            if differences:
                entries[component] = DocumentDifference.apply(entries[component], differences)
        return document

    def text(self, column: str, header: str) -> str:
        """The realized file of one column, ready to be compared with its flat recording.

        Args:
            column: The probe column to realize.
            header: The comment header that column's recording carries, computed the same way the
                recorder computes it rather than copied from the file being compared against.

        Returns:
            The whole text.
        """
        return header + EnergySystemEmitter.render(self.document(column))


def apply_grouping(grouping: Grouping, matrix: ProbeMatrix) -> EnergySystemFile:
    """Builds the grouped energy-system file one decision and one set of probe runs describe.

    Args:
        grouping: The decisions, already checked against the matrix.
        matrix: The three-state table and the recordings behind it.

    Returns:
        The grouped file, switches at the baseline column's positions.
    """
    return GroupedSystemBuilder(grouping, matrix).build()
