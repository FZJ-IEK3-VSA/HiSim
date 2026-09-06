"""Tests for the parity harness's result comparison: what counts as identical and what does not.

The harness compares two ways of building the same system — the Python setup and its recorded twin
— on two questions: are they wired the same way, and do they compute the same numbers. Its
consumers, the rig that runs both halves of such a pair and asserts the answers, arrive in a later
change. What these tests pin is the promise those consumers will rely on: that the comparison says
"identical" only where the two runs really agree, and that everything it cannot compare is reported
as a structural problem rather than absorbed into a number.

That distinction is the whole risk. A comparison that quietly answers "no deviation" for two frames
it never actually compared — because a column was NaN on one side, because the rows are a different
hour of the year, because two renamed columns collapsed into one — passes every migration change
that breaks the results, which is the one thing the harness exists to prevent.

Each test states the failure mode it catches.
"""

# clean

from __future__ import annotations

from typing import ClassVar, Dict, Tuple

import numpy as np
import pandas as pd
import pytest

from hisim.energy_system.parity import PortRenaming, ResolvedWire, ResultComparison, WiringSnapshot


class Frames:
    """The two-column result frames these tests compare, and the names their columns carry.

    A result column is spelled ``"<component> - <port> [<load type> - <unit>]"``, and the renaming
    tests depend on that shape, so the names are built here from one template rather than typed out
    per test and drifting apart.
    """

    #: The component every synthetic result column belongs to.
    COMPONENT: ClassVar[str] = "Battery"

    #: How a result column is spelled: component, port, then the load type and unit suffix.
    COLUMN: ClassVar[str] = "{component} - {port} [Electricity - W]"

    #: The timestamps every synthetic frame is indexed by.
    TIMESTAMPS: ClassVar[Tuple[str, ...]] = ("2021-01-01 00:00", "2021-01-01 00:15", "2021-01-01 00:30")

    @classmethod
    def column(cls, port: str) -> str:
        """Spells the result column of one port.

        Args:
            port: The output port's name.

        Returns:
            The column name a result frame would carry for it.
        """
        return cls.COLUMN.format(component=cls.COMPONENT, port=port)

    @classmethod
    def frame(cls, values: Dict[str, Tuple[float, ...]], index: Tuple[str, ...] = TIMESTAMPS) -> pd.DataFrame:
        """Builds a result frame from one column of values per port.

        Args:
            values: The values of each port, keyed by port name.
            index: The timestamps to index the frame by.

        Returns:
            The frame, with the columns spelled as result columns.
        """
        return pd.DataFrame(
            {cls.column(port): list(column) for port, column in values.items()}, index=list(index)
        )


@pytest.mark.base
def test_a_value_missing_on_one_side_only_is_a_structural_problem_and_not_an_agreement() -> None:
    """Catches a NaN on one side being absorbed into a deviation of zero.

    Subtracting a number from a NaN gives a NaN, and a NaN loses every comparison it takes part in,
    so a maximum computed over such a column reports the deviation of the rows that did agree. Two
    runs where one produced no value at all would then compare identical, which is exactly the kind
    of difference the harness exists to find.
    """
    expected = Frames.frame({"Power": (1.0, 2.0, 3.0)})
    actual = Frames.frame({"Power": (1.0, float("nan"), 3.0)})

    comparison = ResultComparison.between(expected, actual)

    assert not comparison.is_identical()
    assert len(comparison.structural_problems) == 1
    assert Frames.column("Power") in comparison.structural_problems[0]
    assert "NaN on one side only" in comparison.structural_problems[0]


@pytest.mark.base
def test_a_value_missing_on_both_sides_is_the_same_value_and_leaves_the_runs_identical() -> None:
    """Catches the NaN rule being applied to the case where the two runs do agree.

    A row both runs left empty is a row they agree about, and reporting it would make every run
    carrying a legitimately undefined output — a division by a zero flow, a controller before its
    first decision — fail parity against itself.
    """
    values = (1.0, float("nan"), 3.0)
    comparison = ResultComparison.between(Frames.frame({"Power": values}), Frames.frame({"Power": values}))

    assert comparison.is_identical()
    assert comparison.structural_problems == []
    assert comparison.max_absolute_deviation == 0.0


@pytest.mark.base
def test_two_result_columns_renamed_onto_one_name_are_refused_rather_than_collapsed() -> None:
    """Catches a renaming table silently dropping one of two columns it maps together.

    ``DataFrame.rename`` is happy to produce two columns of the same name, and the comparison that
    follows matches columns by name: one of the two would simply stop being compared. A table
    claiming two legacy ports mean one declarative port is a mistake in the table, so it is
    reported as one.
    """
    frame = Frames.frame({"First": (1.0, 2.0, 3.0), "Second": (4.0, 5.0, 6.0)})
    renaming = PortRenaming(
        renamings={
            (Frames.COMPONENT, "First"): "Merged",
            (Frames.COMPONENT, "Second"): "Merged",
        }
    )

    with pytest.raises(ValueError) as failure:
        renaming.apply_to_results(frame)

    assert "Merged" in str(failure.value)


@pytest.mark.base
def test_two_inputs_of_one_component_renamed_onto_one_name_are_refused() -> None:
    """Catches a renaming table making one wire of a snapshot disappear before the diff runs.

    A snapshot is compared through its wires indexed by the input they feed, so two inputs renamed
    onto one name leave one wire in the index and the other nowhere. The diff would then report the
    two systems as agreeing about an input that one of them wires differently.
    """
    snapshot = WiringSnapshot(
        components=("Battery", "Controller"),
        wires=(
            ResolvedWire("Battery", "First", "Controller", "OutA"),
            ResolvedWire("Battery", "Second", "Controller", "OutB"),
        ),
        unconnected_inputs=(),
    )
    renaming = PortRenaming(
        renamings={("Battery", "First"): "Merged", ("Battery", "Second"): "Merged"}
    )

    with pytest.raises(ValueError) as failure:
        renaming.apply_to(snapshot)

    assert "Merged" in str(failure.value)


@pytest.mark.base
def test_two_frames_of_equal_length_over_different_timesteps_are_not_compared_numerically() -> None:
    """Catches two runs of different periods being subtracted row by row.

    Rows are matched by position, which is only meaningful while both frames carry the same index.
    Two runs of the same length that started an hour apart would produce a number for every row,
    and every one of those numbers would be a comparison of two unrelated timesteps — most likely
    a large deviation reported in the wrong place, and occasionally a small one reported nowhere.
    """
    expected = Frames.frame({"Power": (1.0, 2.0, 3.0)})
    actual = Frames.frame(
        {"Power": (1.0, 2.0, 3.0)},
        index=("2021-01-01 00:00", "2021-01-01 01:00", "2021-01-01 02:00"),
    )

    comparison = ResultComparison.between(expected, actual)

    assert not comparison.is_identical()
    assert len(comparison.structural_problems) == 1
    assert "different timesteps" in comparison.structural_problems[0]
    assert comparison.compared_columns == 0


@pytest.mark.base
def test_numerically_negligible_noise_against_a_zero_reference_is_not_a_full_deviation() -> None:
    """Catches a column that is zero in one run reporting a 100% deviation over nothing.

    A column whose reference value is exactly zero has no scale to divide by, so the comparison
    divides by one — and a run differing by 1e-15 of floating-point summation noise would be
    reported against a reference of the same size as a complete disagreement. The absolute
    deviation is still reported truthfully, which is what a reader would check.
    """
    zero = Frames.frame({"Power": (0.0, 0.0, 0.0)})
    noise = Frames.frame({"Power": (0.0, 1e-15, 0.0)})
    real = Frames.frame({"Power": (0.0, 5.0, 0.0)})

    negligible = ResultComparison.between(zero, noise)
    substantial = ResultComparison.between(zero, real)

    assert negligible.is_identical()
    assert negligible.max_absolute_deviation == 1e-15
    assert not substantial.is_identical()
    assert substantial.max_absolute_deviation == 5.0


@pytest.mark.base
def test_the_worst_relative_deviation_is_reported_with_the_column_and_the_timestep_it_is_in() -> None:
    """Catches the vectorized sweep losing track of where the worst deviation actually sits.

    The whole value of the numeric half is that it says *where* two runs diverge: a migration that
    changes one column by a part in a thousand is a finding, and one that changes every column is a
    different finding. Reporting a correct maximum against the wrong column or timestamp would send
    the reader to the wrong component.
    """
    expected = Frames.frame({"Steady": (10.0, 10.0, 10.0), "Drifting": (100.0, 200.0, 400.0)})
    actual = Frames.frame({"Steady": (10.0, 10.0, 10.0), "Drifting": (100.0, 200.0, 404.0)})

    comparison = ResultComparison.between(expected, actual)

    assert comparison.structural_problems == []
    assert comparison.compared_columns == 2
    assert comparison.compared_rows == 3
    assert comparison.max_absolute_deviation == 4.0
    assert np.isclose(comparison.max_relative_deviation, 4.0 / 404.0)
    assert comparison.worst_column == Frames.column("Drifting")
    assert comparison.worst_timestamp == Frames.TIMESTAMPS[2]
    assert not comparison.is_identical()
