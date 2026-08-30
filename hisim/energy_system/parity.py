"""The parity harness: comparing the wiring and the results of two ways of building a system.

The migration to declarative energy systems replaces, component by component, how a HiSim system
is assembled — first the executor, then the recorder that writes a Python setup out as a file, and
later the setups themselves. Every one of those steps has to be provably behaviour preserving, and
"provably" means two things here: the *resolved wiring* must be identical, and the *simulation
results* must be identical. Both comparisons live here as production helpers, so that every
migration change can assert them instead of re-implementing the diff in a test.

The wiring snapshot is taken from the same source of truth as the ``component_connections.json``
audit artifact: the ``src_object_name`` / ``src_field_name`` pair that
:meth:`hisim.component.Component.connect_input` writes onto every connected input. A snapshot
therefore describes exactly what the audit file records, sorted canonically so that two systems
assembled in different ways compare equal whenever they *are* equal. That same reading of a live
simulator is what the recorder observes, which is why the snapshot lives here rather than inside
the recorder: the rig and the recorder need one notion of "the wiring".

Snapshots must be taken before ``run_all_timesteps`` finishes, because the simulator drops its
component references at the end of a run.
"""

# clean

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Sequence, Tuple

import pandas as pd

from hisim.component import Component
from hisim.simulator import Simulator


@dataclass(frozen=True, order=True)
class ResolvedWire:
    """One resolved connection: which output of which component feeds which input of which.

    All four members are runtime component and port *names*, not file keys, because that is the
    level at which the simulator actually matches inputs to outputs and at which the audit artifact
    records them. Comparing names rather than object identity is what makes the same wire
    comparable between two independently built systems. The class is ordered so that a list of
    wires has a canonical sort, turning "the same wiring in a different order" into equality
    rather than into a spurious diff.
    """

    target_component: str
    target_input: str
    source_component: str
    source_output: str

    def describe(self) -> str:
        """Renders the wire in the ``Source.Output -> Target.Input`` spelling used in messages.

        Returns:
            A single-line description of the wire.
        """
        return f"{self.source_component}.{self.source_output} -> {self.target_component}.{self.target_input}"


@dataclass(frozen=True)
class WiringSnapshot:
    """The complete resolved wiring of one assembled system, in canonical order.

    A snapshot holds three things: the component names in registration order (which also fixes the
    result-frame column order), every resolved wire sorted canonically, and the inputs that ended
    up with no source. The unconnected set is part of the comparison on purpose — a migration that
    silently drops a wire into an optional input would otherwise compare equal on the wires alone.
    Snapshots are plain data and compare by value, so one can be stored as a recorded expectation
    in a test fixture and compared against a freshly built system later.
    """

    components: Tuple[str, ...]
    wires: Tuple[ResolvedWire, ...]
    unconnected_inputs: Tuple[Tuple[str, str], ...]

    @classmethod
    def from_simulator(cls, simulator: Simulator) -> "WiringSnapshot":
        """Extracts the resolved wiring of every component registered with a simulator.

        The snapshot must be taken while the simulator still holds its components, i.e. before
        ``run_all_timesteps`` returns, since that method clears every wrapper to free memory.

        Args:
            simulator: A simulator whose components have been added and wired.

        Returns:
            The canonical wiring snapshot of that simulator.
        """
        return cls.from_components([wrapper.my_component for wrapper in simulator.wrapped_components])

    @classmethod
    def from_components(cls, components: Sequence[Component]) -> "WiringSnapshot":
        """Extracts the resolved wiring from a bare component list, in registration order.

        Taking components rather than a simulator lets the harness snapshot a system that has been
        assembled but not yet registered anywhere, which is what the executor's own tests and the
        recorder need.

        Args:
            components: The components of the system, in registration order.

        Returns:
            The canonical wiring snapshot.
        """
        wires: List[ResolvedWire] = []
        unconnected: List[Tuple[str, str]] = []
        for component in components:
            for component_input in component.inputs:
                if component_input.src_object_name is None or component_input.src_field_name is None:
                    unconnected.append((component.component_name, component_input.field_name))
                    continue
                wires.append(
                    ResolvedWire(
                        target_component=component.component_name,
                        target_input=component_input.field_name,
                        source_component=component_input.src_object_name,
                        source_output=component_input.src_field_name,
                    )
                )
        return cls(
            components=tuple(component.component_name for component in components),
            wires=tuple(sorted(wires)),
            unconnected_inputs=tuple(sorted(unconnected)),
        )

    def wires_by_target(self) -> Dict[Tuple[str, str], ResolvedWire]:
        """Indexes the wires by the input they feed, which is unique by construction.

        Returns:
            A mapping of (target component, target input) to the wire feeding it.
        """
        return {(wire.target_component, wire.target_input): wire for wire in self.wires}


@dataclass(frozen=True)
class PortRenaming:
    """A translation table between the port names two builds give to the same connection.

    Wiring parity is normally a plain equality of names, and for every static port it stays that
    way. Dynamic ports are the exception: the imperative add-API names an aggregator's dynamic
    input after its insertion order (``Input_<source>_<field>_<n>``) and its dynamic outputs after
    a running counter (``LoadingPowerInputForBattery_Output15``), while the declarative path
    derives both from the frozen templates of the format. The two names denote the same wire, so
    comparing them literally would report a difference where there is none — and dropping the
    comparison would hide a real one. This class makes the translation explicit and reviewable: a
    change states, port by port, which legacy name it claims corresponds to which declarative name,
    and everything it does not list must still match literally. :meth:`apply_to` rewrites a
    snapshot, :meth:`apply_to_results` a result frame and :meth:`apply_to_kpis` the indicator keys
    that quote a port name, so one table governs all three.

    Keys are ``(component name, port name in the source build)``; the component name is part of the
    key because port names are only unique per component.
    """

    #: Separator between component and port in a result column name, mirroring
    #: :meth:`hisim.component.ComponentOutput.get_pretty_name`. Used to rewrite column names
    #: without having to reconstruct the load type and unit suffix.
    COLUMN_SEPARATOR: ClassVar[str] = " - "

    #: Character that begins the load type and unit suffix of a result column name. Matching it
    #: keeps a rename of ``Foo`` from also hitting a column named ``FooBar``.
    COLUMN_SUFFIX_START: ClassVar[str] = " ["

    renamings: Mapping[Tuple[str, str], str]

    def rename(self, component_name: str, port_name: str) -> str:
        """Translates one port name, returning it unchanged when the table does not list it.

        Args:
            component_name: Runtime name of the component the port belongs to.
            port_name: The port name in the build being translated.

        Returns:
            The corresponding name in the other build, or the input name.
        """
        return self.renamings.get((component_name, port_name), port_name)

    def apply_to(self, snapshot: WiringSnapshot) -> WiringSnapshot:
        """Rewrites every port name of a snapshot through the table.

        Both ends of every wire are translated, since an aggregator appears as the target of its
        participants' forward wires and as the source of its dispatch wires. The unconnected inputs
        are translated as well, so that a renamed port that is unconnected on both sides still
        compares equal.

        Args:
            snapshot: The snapshot to translate, typically the legacy one.

        Returns:
            A new snapshot in the other build's naming, canonically sorted again.
        """
        wires = [
            ResolvedWire(
                target_component=wire.target_component,
                target_input=self.rename(wire.target_component, wire.target_input),
                source_component=wire.source_component,
                source_output=self.rename(wire.source_component, wire.source_output),
            )
            for wire in snapshot.wires
        ]
        unconnected = [
            (component_name, self.rename(component_name, port_name))
            for component_name, port_name in snapshot.unconnected_inputs
        ]
        return WiringSnapshot(
            components=snapshot.components,
            wires=tuple(sorted(wires)),
            unconnected_inputs=tuple(sorted(unconnected)),
        )

    def apply_to_results(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Rewrites the result columns of the renamed outputs, leaving the rest untouched.

        Result columns are ``"<component> - <port> [<load type> - <unit>]"``, so a rename is a
        substitution on the middle part only. The suffix is matched too, which keeps a rename of a
        port from also hitting a differently named port that happens to start with it.

        Args:
            frame: The result frame to translate, typically the legacy one.

        Returns:
            A frame with the renamed columns, in unchanged column order.

        Raises:
            ValueError: If a renaming would produce a column name the frame already has, which
                would silently drop one of the two.
        """
        columns = [str(column) for column in frame.columns]
        mapping: Dict[str, str] = {}
        for (component_name, port_name), new_name in self.renamings.items():
            prefix = f"{component_name}{self.COLUMN_SEPARATOR}{port_name}{self.COLUMN_SUFFIX_START}"
            replacement = f"{component_name}{self.COLUMN_SEPARATOR}{new_name}{self.COLUMN_SUFFIX_START}"
            for column in columns:
                if column.startswith(prefix):
                    mapping[column] = replacement + column[len(prefix):]
        clashes = sorted(set(mapping.values()) & (set(columns) - set(mapping)))
        if clashes:
            raise ValueError(
                f"The port renaming would produce column names the frame already carries: "
                f"{clashes}. Two different outputs cannot be renamed onto one column."
            )
        return frame.rename(columns=mapping)

    def apply_to_kpis(self, kpis: Mapping[str, Any]) -> Dict[str, Any]:
        """Rewrites the renamed port names where a key-performance-indicator name quotes one.

        Some indicators are named after a port rather than after a quantity: an energy management
        system publishes one "Priority for <port>" per participant, so its indicator keys carry the
        aggregator input names verbatim. Those are the very names this table exists to translate,
        and leaving them alone would report a difference in the third comparison that the first two
        have already accounted for — with identical values on both sides.

        The substitution is by port name alone, because an indicator key carries the component's
        display tag rather than its runtime name and there is nothing to match the other half of
        the table's key against. That is only safe while one legacy port name means one declarative
        name across the whole table, so a table that disagrees with itself is refused rather than
        applied to whichever entry came first. Matching is on whole words, so a name is never
        rewritten inside a longer one.

        Args:
            kpis: The flattened indicators to translate, typically the legacy run's.

        Returns:
            A new mapping with the renamed keys, values untouched.

        Raises:
            ValueError: If one legacy port name is declared to mean two different declarative
                names, or if a rename would land on a key the mapping already carries.
        """
        by_port = self._renamings_by_port()
        translated: Dict[str, Any] = {}
        for name, value in kpis.items():
            renamed = self._rename_within(name, by_port)
            if renamed in translated:
                raise ValueError(
                    f"The port renaming would map two indicators onto the key '{renamed}'; two "
                    "different ports cannot be renamed onto one indicator."
                )
            translated[renamed] = value
        return translated

    def _renamings_by_port(self) -> List[Tuple[str, str]]:
        """Flattens the table to port names alone, longest first, refusing an ambiguous one.

        Returns:
            The pairs of legacy and declarative port name, longest legacy name first so that a
            name is never rewritten by a shorter one that happens to be spelled inside it.

        Raises:
            ValueError: If one legacy port name is declared to mean two different things.
        """
        flattened: Dict[str, str] = {}
        for (component_name, port_name), new_name in self.renamings.items():
            previous = flattened.get(port_name)
            if previous is not None and previous != new_name:
                raise ValueError(
                    f"The port name '{port_name}' is declared to mean both '{previous}' and "
                    f"'{new_name}' (on '{component_name}'), so an indicator key quoting it cannot "
                    "be translated."
                )
            flattened[port_name] = new_name
        return sorted(flattened.items(), key=lambda pair: (-len(pair[0]), pair[0]))

    @classmethod
    def _rename_within(cls, text: str, by_port: Sequence[Tuple[str, str]]) -> str:
        """Replaces every whole-word occurrence of a renamed port inside one string.

        Args:
            text: The indicator key to translate.
            by_port: The flattened table, longest legacy name first.

        Returns:
            The key with every renamed port rewritten.
        """
        for legacy, declarative in by_port:
            text = re.sub(rf"\b{re.escape(legacy)}\b", declarative, text)
        return text


@dataclass
class WiringDiff:
    """The difference between two wiring snapshots, in a form a test can print verbatim.

    The diff distinguishes the three failure modes a migration actually produces: a component that
    appears on only one side, a wire that appears on only one side, and — the subtle one — an input
    both sides connect but to *different* sources. Keeping them apart means the message says what
    went wrong instead of dumping two sorted lists for the reader to compare.
    """

    #: Fields of this dataclass that carry a difference. Listed as class-scoped data so that
    #: :meth:`is_identical` and :meth:`describe` cannot drift apart when a category is added.
    DIFFERENCE_FIELDS: ClassVar[Tuple[str, ...]] = (
        "missing_components",
        "extra_components",
        "missing_wires",
        "extra_wires",
        "rewired_inputs",
        "unconnected_only_in_expected",
        "unconnected_only_in_actual",
    )

    missing_components: Tuple[str, ...] = ()
    extra_components: Tuple[str, ...] = ()
    component_order_differs: bool = False
    missing_wires: Tuple[ResolvedWire, ...] = ()
    extra_wires: Tuple[ResolvedWire, ...] = ()
    rewired_inputs: Tuple[Tuple[str, str, str, str], ...] = ()
    unconnected_only_in_expected: Tuple[Tuple[str, str], ...] = ()
    unconnected_only_in_actual: Tuple[Tuple[str, str], ...] = ()

    def is_identical(self) -> bool:
        """Reports whether the two snapshots describe the same system.

        Component *order* is reported separately and does not by itself make the snapshots unequal
        here, because a caller that only cares about wiring topology should not have to filter it
        out; the accompanying description always mentions it.

        Returns:
            ``True`` when no difference category holds an entry.
        """
        return not any(getattr(self, name) for name in self.DIFFERENCE_FIELDS)

    def describe(self) -> str:
        """Builds a human-readable report of every difference found.

        Returns:
            A multi-line description, or a single line stating that the snapshots agree.
        """
        if self.is_identical() and not self.component_order_differs:
            return "wiring snapshots are identical"
        lines: List[str] = []
        if self.missing_components:
            lines.append(f"components missing from the actual system: {list(self.missing_components)}")
        if self.extra_components:
            lines.append(f"components only in the actual system: {list(self.extra_components)}")
        if self.component_order_differs:
            lines.append("the components are registered in a different order")
        lines.extend(f"wire missing from the actual system: {wire.describe()}" for wire in self.missing_wires)
        lines.extend(f"wire only in the actual system: {wire.describe()}" for wire in self.extra_wires)
        for component_name, input_name, expected_source, actual_source in self.rewired_inputs:
            lines.append(
                f"input {component_name}.{input_name} is fed by '{expected_source}' in the "
                f"expected system but by '{actual_source}' in the actual one"
            )
        lines.extend(f"input {name}.{port} is unconnected only in the expected system"
                     for name, port in self.unconnected_only_in_expected)
        lines.extend(f"input {name}.{port} is unconnected only in the actual system"
                     for name, port in self.unconnected_only_in_actual)
        return "\n".join(lines)


class WiringParityHarness:
    """Compares two assembled systems and reports precisely where their wiring differs.

    The harness is the workhorse of the migration: every change to how a system is assembled
    asserts that the wiring it produces is identical to the wiring the previous path produced. Both
    inputs may be simulators, bare component lists or recorded snapshots, so the same comparison
    covers "Python setup vs recorded file", "old executor vs new executor" and "today's build vs a
    golden snapshot checked into the repository". All methods are classmethods; there is no state.
    """

    @classmethod
    def compare(cls, expected: WiringSnapshot, actual: WiringSnapshot) -> WiringDiff:
        """Diffs two wiring snapshots.

        Args:
            expected: The reference system's snapshot, e.g. from the Python setup.
            actual: The system under test, e.g. built from the recorded file.

        Returns:
            A diff object; call :meth:`WiringDiff.is_identical` to decide pass or fail.
        """
        expected_components = set(expected.components)
        actual_components = set(actual.components)
        expected_by_target = expected.wires_by_target()
        actual_by_target = actual.wires_by_target()

        rewired: List[Tuple[str, str, str, str]] = []
        for key in sorted(set(expected_by_target) & set(actual_by_target)):
            expected_wire = expected_by_target[key]
            actual_wire = actual_by_target[key]
            if expected_wire != actual_wire:
                expected_source = f"{expected_wire.source_component}.{expected_wire.source_output}"
                actual_source = f"{actual_wire.source_component}.{actual_wire.source_output}"
                rewired.append((key[0], key[1], expected_source, actual_source))

        expected_unconnected = set(expected.unconnected_inputs)
        actual_unconnected = set(actual.unconnected_inputs)
        return WiringDiff(
            missing_components=tuple(sorted(expected_components - actual_components)),
            extra_components=tuple(sorted(actual_components - expected_components)),
            component_order_differs=expected.components != actual.components,
            missing_wires=tuple(sorted(set(expected.wires) - set(actual.wires))),
            extra_wires=tuple(sorted(set(actual.wires) - set(expected.wires))),
            rewired_inputs=tuple(rewired),
            unconnected_only_in_expected=tuple(sorted(expected_unconnected - actual_unconnected)),
            unconnected_only_in_actual=tuple(sorted(actual_unconnected - expected_unconnected)),
        )

    @classmethod
    def compare_simulators(cls, expected: Simulator, actual: Simulator) -> WiringDiff:
        """Snapshots two simulators and diffs them in one call.

        Args:
            expected: The reference simulator.
            actual: The simulator under test.

        Returns:
            The wiring diff between the two.
        """
        return cls.compare(WiringSnapshot.from_simulator(expected), WiringSnapshot.from_simulator(actual))


@dataclass
class ResultComparison:
    """The numeric outcome of comparing two simulation result frames.

    Wiring parity proves the two systems are connected the same way; this proves they compute the
    same numbers. The comparison reports the worst absolute and relative deviation together with
    the column and timestamp where it occurred, because a migration that changes results by 1e-9 in
    one column is a very different finding from one that changes them everywhere. Structural
    problems — different columns, different row counts — are collected separately and make the
    comparison fail regardless of the numeric tolerance.
    """

    #: Relative deviation below which two runs count as numerically identical. Identical configs on
    #: identical wiring must reproduce bit-for-bit up to floating point summation order, so the
    #: tolerance is deliberately at the noise floor rather than at an engineering tolerance.
    DEFAULT_RELATIVE_TOLERANCE: ClassVar[float] = 1e-12

    compared_columns: int = 0
    compared_rows: int = 0
    max_absolute_deviation: float = 0.0
    max_relative_deviation: float = 0.0
    worst_column: Optional[str] = None
    worst_timestamp: Optional[str] = None
    structural_problems: List[str] = field(default_factory=list)

    def is_identical(self, relative_tolerance: float = DEFAULT_RELATIVE_TOLERANCE) -> bool:
        """Reports whether the two result frames agree within the given relative tolerance.

        Args:
            relative_tolerance: Largest relative deviation still considered identical.

        Returns:
            ``True`` when there is no structural problem and the worst relative deviation is within
            the tolerance.
        """
        return not self.structural_problems and self.max_relative_deviation <= relative_tolerance

    def describe(self) -> str:
        """Builds a one-paragraph report of the comparison, suitable for an assertion message.

        Returns:
            A multi-line description including the worst deviation and where it occurred.
        """
        lines = [
            f"compared {self.compared_columns} output columns over {self.compared_rows} timesteps",
            f"max absolute deviation: {self.max_absolute_deviation:.6g}",
            f"max relative deviation: {self.max_relative_deviation:.6g}",
        ]
        if self.worst_column is not None:
            lines.append(f"worst column: '{self.worst_column}' at {self.worst_timestamp}")
        lines.extend(f"structural problem: {problem}" for problem in self.structural_problems)
        return "\n".join(lines)

    @classmethod
    def between(cls, expected: pd.DataFrame, actual: pd.DataFrame) -> "ResultComparison":
        """Compares two result frames column by column and returns the worst deviation.

        Columns are matched by name rather than by position, so a run that produces the same
        outputs in a different order still compares numerically; the order difference itself is
        reported as a structural problem, because it changes every result file downstream.

        Args:
            expected: The reference result frame, e.g. ``Simulator.results_data_frame``.
            actual: The result frame of the system under test.

        Returns:
            The comparison outcome.
        """
        comparison = cls()
        expected_columns = [str(name) for name in expected.columns]
        actual_columns = [str(name) for name in actual.columns]
        missing = [name for name in expected_columns if name not in actual_columns]
        extra = [name for name in actual_columns if name not in expected_columns]
        if missing:
            comparison.structural_problems.append(f"columns missing from the actual results: {missing}")
        if extra:
            comparison.structural_problems.append(f"columns only in the actual results: {extra}")
        if not missing and not extra and expected_columns != actual_columns:
            comparison.structural_problems.append("the result columns are in a different order")
        if len(expected.index) != len(actual.index):
            comparison.structural_problems.append(
                f"different number of timesteps: {len(expected.index)} vs {len(actual.index)}"
            )
            return comparison
        cls._compare_columns(comparison, expected, actual, [n for n in expected_columns if n in actual_columns])
        return comparison

    @classmethod
    def _compare_columns(
        cls,
        comparison: "ResultComparison",
        expected: pd.DataFrame,
        actual: pd.DataFrame,
        shared: Sequence[str],
    ) -> None:
        """Fills in the numeric half of a comparison over the columns both frames carry.

        Split out of :meth:`between` so that the structural checks and the numeric sweep read as
        two separate steps: the first decides whether a comparison is meaningful at all, and only
        this one touches the values.

        Args:
            comparison: The comparison being filled in; modified in place.
            expected: The reference frame.
            actual: The frame under test.
            shared: The column names present in both, in the reference frame's order.
        """
        comparison.compared_columns = len(shared)
        comparison.compared_rows = len(expected.index)
        for name in shared:
            left = expected[name].to_numpy(dtype=float)
            right = actual[name].to_numpy(dtype=float)
            absolute = abs(left - right)
            scale = pd.Series(abs(left)).combine(pd.Series(abs(right)), max).to_numpy(dtype=float)
            relative = absolute / pd.Series(scale).replace(0.0, 1.0).to_numpy(dtype=float)
            position = int(relative.argmax()) if relative.size else 0
            if relative.size and relative[position] > comparison.max_relative_deviation:
                comparison.max_relative_deviation = float(relative[position])
                comparison.worst_column = name
                comparison.worst_timestamp = str(expected.index[position])
            if absolute.size:
                comparison.max_absolute_deviation = max(comparison.max_absolute_deviation, float(absolute.max()))
