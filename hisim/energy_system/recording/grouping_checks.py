"""Holding a grouping decision against what the probes actually observed.

This is where the rule that nothing is ever inferred is enforced from the other side. The tool
supplies the three-state matrix and never fills a cell in; the person supplies the assignments; and
the only thing the machinery is allowed to do with the two together is to say that they do not
agree. Two of those disagreements are the ones the requirements name, and both are refusals rather
than repairs.

A component that differs between two configurations and carries no assignment is refused, naming
the row and the columns that differ: it cannot both stay an ordinary always-on component and
disagree with itself. A configuration selecting an option, or naming a group, that no assignment
ever created is refused too, because a table whose two sheets contradict each other has not made a
decision at all.

Three more checks are here for the same reason, and each of them catches a table that would produce
a grouped file failing its own byte-for-byte proof several minutes later: a decision about a
component no probe recorded, an ``override`` claimed for a component that is missing somewhere, and
a switch whose positions across the columns do not match where the component really was. Failing
early with the row named is worth more than failing late with a diff.
"""

# clean

from __future__ import annotations

from typing import Sequence, Tuple

from hisim.energy_system.errors import EnergySystemErrorId, EnergySystemRecordingError
from hisim.energy_system.recording.grouping import Assignment, AssignmentKind, Grouping
from hisim.energy_system.recording.matrix import ComponentRow, ProbeMatrix


class GroupingCheck:
    """Holds a decision against what was observed and refuses the ways the two can contradict.

    This is where C-P3.6 is enforced. The tool supplies the matrix and never fills a cell in; the
    person supplies the assignments; and the only thing the machinery is allowed to do with the two
    together is to say that they do not agree. Every refusal names the row and the columns
    involved, because a message that only says "the table is inconsistent" leaves the reader to
    re-derive the diff the tool already has.
    """

    @classmethod
    def check(cls, grouping: Grouping, matrix: ProbeMatrix) -> None:
        """Runs every consistency rule over one decision and one matrix.

        Args:
            grouping: The person's decisions.
            matrix: The observation they were made against.

        Raises:
            EnergySystemRecordingError: ``EF-R6`` for a difference nobody assigned and ``EF-R7``
                for a decision naming something that does not exist or contradicting the matrix.
        """
        for row in matrix.decided_rows():
            cls._check_row(grouping.assignment(row.component), row, grouping, matrix)
        cls._check_known_components(grouping, matrix)
        cls._check_selections(grouping, matrix)

    @classmethod
    def _check_row(
        cls, assignment: Assignment, row: ComponentRow, grouping: Grouping, matrix: ProbeMatrix
    ) -> None:
        """Checks one non-uniform row against the decision made about it.

        Args:
            assignment: The decision.
            row: The row it is about.
            grouping: The whole decision, for the switch positions.
            matrix: The observation, for the message.

        Raises:
            EnergySystemRecordingError: ``EF-R6`` when nothing was said or when ``override`` was
                said about a component that is missing somewhere; ``EF-R7`` when a group's flags
                or a variant's selections do not match where the component actually is.
        """
        if assignment.kind is AssignmentKind.ORDINARY:
            raise cls._unassigned(row, matrix)
        if assignment.kind is AssignmentKind.OVERRIDE and row.absent_in:
            raise EnergySystemRecordingError(
                EnergySystemErrorId.GROUPING_UNASSIGNED_DIFFERENCE,
                f"{grouping.origin}:assignments.{row.component}",
                f"'{row.component}' is not there at all in {', '.join(row.absent_in)}, which no "
                "value a consumer sets can explain.",
                remedy="Assign the row to a group or to one option of a variant instead.",
            )
        if assignment.kind is AssignmentKind.GROUP:
            expected = tuple(
                column
                for column in matrix.columns
                if grouping.selection(column).groups.get(assignment.name, False)
            )
            if row.present_in != expected:
                raise cls._mismatch(grouping, row, f"group '{assignment.name}'", expected, row.present_in)
        if assignment.kind is AssignmentKind.VARIANT:
            expected = cls._variant_columns(assignment, grouping, matrix)
            if row.present_in != expected:
                raise cls._mismatch(grouping, row, f"variant '{assignment.name}'", expected, row.present_in)

    @classmethod
    def _variant_columns(cls, assignment: Assignment, grouping: Grouping, matrix: ProbeMatrix) -> Tuple[str, ...]:
        """The columns a variant assignment claims its component exists in.

        Args:
            assignment: The variant assignment.
            grouping: The whole decision, for the selections.
            matrix: The observation, for the column order.

        Returns:
            The columns whose selection of that variant includes this component.
        """
        return tuple(
            column
            for column in matrix.columns
            if assignment.option is None
            or grouping.selection(column).variants.get(assignment.name) == assignment.option
        )

    @classmethod
    def _check_known_components(cls, grouping: Grouping, matrix: ProbeMatrix) -> None:
        """Refuses an assignment about a component no probe ever recorded.

        Args:
            grouping: The decisions.
            matrix: The observation.

        Raises:
            EnergySystemRecordingError: ``EF-R7`` naming the component and the rows that exist.
        """
        known = tuple(row.component for row in matrix.rows)
        for assignment in grouping.assignments:
            if assignment.component not in known:
                raise EnergySystemRecordingError(
                    EnergySystemErrorId.GROUPING_UNKNOWN_OPTION,
                    f"{grouping.origin}:assignments.{assignment.component}",
                    f"no probe recorded a component called '{assignment.component}'.",
                    alternatives=known,
                    alternatives_label="components",
                    offending_value=assignment.component,
                )

    @classmethod
    def _check_selections(cls, grouping: Grouping, matrix: ProbeMatrix) -> None:
        """Refuses a configuration that names a switch or an option no assignment created.

        Args:
            grouping: The decisions.
            matrix: The observation, for the columns that must all be covered.

        Raises:
            EnergySystemRecordingError: ``EF-R7`` naming what was selected and what exists.
        """
        groups, variants = grouping.group_names(), grouping.variant_options()
        for column in matrix.columns:
            selection = grouping.selection(column)
            for name in selection.groups:
                if name not in groups:
                    raise cls._unknown(grouping, column, "group", name, groups)
            for name, option in selection.variants.items():
                if name not in variants:
                    raise cls._unknown(grouping, column, "variant", name, tuple(variants))
                if option not in variants[name]:
                    raise cls._unknown(grouping, column, f"option of '{name}'", option, variants[name])
            for name in groups:
                if name not in selection.groups:
                    raise cls._missing(grouping, column, "group", name)
            for name in variants:
                if name not in selection.variants:
                    raise cls._missing(grouping, column, "variant", name)

    @classmethod
    def _unassigned(cls, row: ComponentRow, matrix: ProbeMatrix) -> EnergySystemRecordingError:
        """Builds the refusal of a row nobody decided about; the caller raises it.

        Args:
            row: The row.
            matrix: The observation, for the baseline column's name.

        Returns:
            The exception, naming the row and every column that disagrees with the baseline.
        """
        differing = row.differs_in or row.absent_in
        state = "differs from" if row.differs_in else "is missing against"
        return EnergySystemRecordingError(
            EnergySystemErrorId.GROUPING_UNASSIGNED_DIFFERENCE,
            f"components.{row.component}",
            f"'{row.component}' {state} the baseline '{matrix.baseline}' in "
            f"{', '.join(differing)}, and the table says nothing about what that means.",
            alternatives=("", "override", "group:<name>", "variant:<name>", "variant:<name>/<option>"),
            alternatives_label="assignments",
            remedy=(
                "A component cannot both stay ungrouped and disagree with itself between two "
                "configurations. Write the decision in the row's 'assignment' cell."
            ),
        )

    @classmethod
    def _mismatch(
        cls,
        grouping: Grouping,
        row: ComponentRow,
        switch: str,
        expected: Sequence[str],
        actual: Sequence[str],
    ) -> EnergySystemRecordingError:
        """Builds the refusal of a switch whose positions do not match where the component is.

        Args:
            grouping: The decision, for the location.
            row: The row in question.
            switch: How the message names the switch.
            expected: The columns the switch positions imply.
            actual: The columns the component is really in.

        Returns:
            The exception.
        """
        return EnergySystemRecordingError(
            EnergySystemErrorId.GROUPING_UNKNOWN_OPTION,
            f"{grouping.origin}:assignments.{row.component}",
            f"the {switch} is live in [{', '.join(expected) or 'no column'}] but '{row.component}' "
            f"was recorded in [{', '.join(actual) or 'no column'}].",
            remedy="Correct the configurations sheet or move the row to the switch it really follows.",
        )

    @classmethod
    def _unknown(
        cls, grouping: Grouping, column: str, what: str, name: str, alternatives: Sequence[str]
    ) -> EnergySystemRecordingError:
        """Builds the refusal of a configuration naming something no assignment created.

        Args:
            grouping: The decision, for the location.
            column: The column that named it.
            what: The kind of thing named.
            name: What it named.
            alternatives: What exists instead.

        Returns:
            The exception.
        """
        return EnergySystemRecordingError(
            EnergySystemErrorId.GROUPING_UNKNOWN_OPTION,
            f"{grouping.origin}:configurations.{column}",
            f"the {what} '{name}' is not one the assignments create.",
            alternatives=alternatives,
            alternatives_label="names",
            offending_value=name,
        )

    @classmethod
    def _missing(cls, grouping: Grouping, column: str, what: str, name: str) -> EnergySystemRecordingError:
        """Builds the refusal of a column that leaves one switch unpositioned.

        Every column has to say where every switch stands, because a column is a claim about the
        whole system and a switch nobody positioned makes the claim unprovable rather than partial.

        Args:
            grouping: The decision, for the location.
            column: The column that says nothing about the switch.
            what: Whether a group or a variant is meant.
            name: The switch's name.

        Returns:
            The exception.
        """
        return EnergySystemRecordingError(
            EnergySystemErrorId.GROUPING_UNKNOWN_OPTION,
            f"{grouping.origin}:configurations.{column}.{what}s",
            f"the column '{column}' does not say where the {what} '{name}' stands.",
            remedy=f"Give every column a position for every {what} the assignments create.",
        )


def check_grouping(grouping: Grouping, matrix: ProbeMatrix) -> None:
    """Refuses a decision that contradicts what the probes observed.

    Args:
        grouping: The person's decisions.
        matrix: The three-state table they were made against.

    Raises:
        EnergySystemRecordingError: ``EF-R6`` for an unassigned difference, ``EF-R7`` for a
            decision naming something that does not exist.
    """
    GroupingCheck.check(grouping, matrix)
