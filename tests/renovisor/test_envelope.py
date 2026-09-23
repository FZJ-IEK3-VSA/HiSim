"""T-ENV: the insulation arithmetic on known numbers, and the note that shows it.

Series resistance and nothing else (§4.3 of the calculation-request specification). The point of
these tests is that the numbers are checkable by eye: a 1.1 W/(m²·K) wall with 120 mm of a
material whose lambda is 0.0355 W/(m·K) becomes 1/(1/1.1 + 0.12/0.0355), and the mapping note
has to carry that division so a reader can redo it.
"""

import pytest

from hisim.renovisor.envelope import LayerNote, UValueComposer


@pytest.mark.base
class TestTheComposer:
    """``U_new = 1 / (1 / U_existing + sum of d/lambda)``, and nothing else."""

    def test_one_layer_on_a_known_wall(self) -> None:
        """A 1.1 W/(m2K) facade plus 120 mm of EPS at lambda 0.0355 (the mockup wall is 1.78 since 2026-09-19)."""
        resistance = UValueComposer.resistance(120, 0.0355)

        assert resistance == pytest.approx(0.12 / 0.0355)
        assert UValueComposer.compose(1.1, [resistance]) == pytest.approx(
            1.0 / (1.0 / 1.1 + 0.12 / 0.0355)
        )

    def test_two_layers_stack_and_the_order_does_not_change_the_answer(self) -> None:
        """Series resistances add, so a wall insulated twice is one wall with two layers."""
        first = UValueComposer.resistance(100, 0.035)
        second = UValueComposer.resistance(60, 0.022)

        forwards = UValueComposer.compose(2.4, [first, second])
        backwards = UValueComposer.compose(2.4, [second, first])

        assert forwards == pytest.approx(backwards)
        assert forwards == pytest.approx(1.0 / (1.0 / 2.4 + 0.1 / 0.035 + 0.06 / 0.022))

    def test_a_layer_always_lowers_the_u_value(self) -> None:
        """A positive resistance can only raise the total resistance, never lower it."""
        assert UValueComposer.compose(0.4, [UValueComposer.resistance(300, 0.04)]) < 0.4

    def test_no_layers_leaves_the_element_as_it_was(self) -> None:
        """An element no measure touched keeps exactly the U-value the request states."""
        assert UValueComposer.compose(0.7, []) == pytest.approx(0.7)

    @pytest.mark.parametrize(
        "existing, resistances",
        [(0.0, [1.0]), (-0.5, [1.0]), (1.0, [-1.0])],
    )
    def test_a_number_that_describes_no_wall_is_refused(self, existing: float, resistances: list) -> None:
        """A non-positive U-value or a negative resistance is a bug in a table, not a wall."""
        with pytest.raises(ValueError):
            UValueComposer.compose(existing, resistances)

    @pytest.mark.parametrize("thickness, conductivity", [(100, 0.0), (100, -0.03), (-10, 0.04)])
    def test_a_layer_that_describes_no_material_is_refused(
        self, thickness: int, conductivity: float
    ) -> None:
        """Lambda has to be positive and a thickness cannot be negative."""
        with pytest.raises(ValueError):
            UValueComposer.resistance(thickness, conductivity)


@pytest.mark.base
class TestTheNote:
    """The note carries the whole division, because it is the only place a reader can check it."""

    def test_the_note_contains_every_number_of_the_arithmetic(self) -> None:
        """The starting U-value, the thickness, the conductivity and the result, all in one line."""
        composed = UValueComposer.compose(1.1, [UValueComposer.resistance(120, 0.0355)])

        note = LayerNote.of(
            1.1, [("external_insulation", 120.0, "eps_rigid_board", 0.0355)], composed
        )

        assert "1.1 W/(m2K)" in note
        assert "external_insulation 120 mm of eps_rigid_board" in note
        assert "lambda 0.0355 W/mK" in note
        assert "1/(1/1.1 + 0.12/0.0355)" in note
        assert f"{composed:.4g}" in note

    def test_two_layers_are_both_named_and_both_in_the_division(self) -> None:
        """A wall with two measures on it says which measure put which layer on."""
        resistances = [UValueComposer.resistance(100, 0.035), UValueComposer.resistance(60, 0.022)]
        composed = UValueComposer.compose(2.4, resistances)

        note = LayerNote.of(
            2.4,
            [
                ("external_insulation", 100.0, "eps_rigid_board", 0.035),
                ("internal_dry_lining_insulation", 60.0, "pir", 0.022),
            ],
            composed,
        )

        assert "external_insulation" in note and "internal_dry_lining_insulation" in note
        assert "0.1/0.035 + 0.06/0.022" in note
