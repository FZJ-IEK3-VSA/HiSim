"""The cost half of ``result.json``: that it is empty, and that it says where the money went.

Step 6 published a ``costs`` block read out of one run's cost-engine exports. Step 10 removed it:
a renovation is a plan over several years, the staged evaluator prices the whole of it into
``economics_result.json``, and keeping a second, single-state implementation of the money beside
it is exactly what the E-spec's "one implementation of the money" rule forbids.

So what is asserted here is the *statement of the absence*, which decision R8 makes a deliverable
in its own right: every contract cost field is in ``result.json["missing"]``, each with a reason
naming the document, the key inside it and the command that writes it; the one field that moved
nowhere says why instead; and the capability document and the translation map are generated from
the same rows, so a caller sees the split before the first payload exists.
"""

from typing import Tuple

import pytest

from hisim.renovisor.costs import CostBuilder, CostField, CostSchema, EconomicsDocument

pytestmark = pytest.mark.base


class TestTheCostBlockIsEmpty:
    """``result.json`` carries the KPI half of a calculation and nothing monetary."""

    def test_the_block_has_no_values_at_all(self) -> None:
        """A block with one figure left in it would be a second implementation of the money."""
        assert not CostBuilder().build().values

    def test_every_cost_field_is_listed_as_missing(self) -> None:
        """Decision R8: a field that is not published is named, never quietly left out."""
        block = CostBuilder().build()
        fields = [entry.field for entry in block.missing]

        assert fields == [f"{CostBuilder.MISSING_PREFIX}.{field.value}" for field in CostField]

    def test_the_order_is_the_payload_order(self) -> None:
        """Two runs of one request produce the same bytes (requirement R10)."""
        first = [entry.field for entry in CostBuilder().build().missing]
        second = [entry.field for entry in CostBuilder().build().missing]

        assert first == second


class TestTheReasonsSayWhereTheMoneyIs:
    """A reason a caller can act on: the document, the key inside it and how to produce it."""

    def test_a_moved_field_names_the_document_the_key_and_the_command(self) -> None:
        """Following the reason has to lead somewhere, not merely say "elsewhere"."""
        reason = CostBuilder.reason_for(CostField.NET_PRESENT_VALUE)

        assert EconomicsDocument.FILE_NAME in reason
        assert "plan.totals.npv_in_euro" in reason
        assert "python -m hisim.economics staged" in reason

    def test_the_payback_period_names_the_comparison_it_is_now_part_of(self) -> None:
        """It was absent for want of a second run; the staged plan always has its reference."""
        assert "comparison.discounted_payback_year" in CostBuilder.reason_for(CostField.PAYBACK)

    def test_the_property_value_figure_moved_nowhere_and_says_why(self) -> None:
        """The one cost field nothing anywhere produces: decision A13 records that no model exists."""
        reason = CostBuilder.reason_for(CostField.PROPERTY_VALUE)

        assert reason == CostBuilder.PROPERTY_VALUE_REASON
        assert "A13" in reason
        assert EconomicsDocument.FILE_NAME not in reason

    def test_every_field_but_that_one_has_a_key_in_the_document(self) -> None:
        """A cost field added without deciding where its figure lives now is a failing build."""
        addressed = set(EconomicsDocument.WHERE)
        expected = {field.value for field in CostField} - {CostField.PROPERTY_VALUE.value}

        assert addressed == expected


class TestTheSchemaRows:
    """What the capability document and the translation map show before any payload exists."""

    def test_there_is_one_row_per_cost_field_and_no_other(self) -> None:
        """The breakdown leaf of step 6 is gone with the block; the staged document carries it."""
        described: Tuple[str, ...] = tuple(row.field for row in CostSchema.rows())

        assert set(described) == {field.value for field in CostField}
        assert {row.block for row in CostSchema.rows()} == {CostBuilder.MISSING_PREFIX}

    def test_every_row_is_an_absent_one_with_its_reason_and_its_condition(self) -> None:
        """A row that still claimed a provenance would promise a figure the payload has not got."""
        for row in CostSchema.rows():
            assert row.provenance is None
            assert row.reason
            assert row.when

    def test_a_moved_row_names_what_produces_the_figure_now(self) -> None:
        """The source column points at the document and the key, so a reader can follow it."""
        rows = {row.field: row for row in CostSchema.rows()}

        assert EconomicsDocument.FILE_NAME in rows[CostField.INVESTMENT.value].source
        assert rows[CostField.INVESTMENT.value].when == CostSchema.MOVED_WHEN

    def test_the_property_value_row_names_no_source_at_all(self) -> None:
        """There is nothing to point at, and an invented source would be worse than none."""
        rows = {row.field: row for row in CostSchema.rows()}

        assert rows[CostField.PROPERTY_VALUE.value].source == ""
        assert rows[CostField.PROPERTY_VALUE.value].when == CostSchema.PROPERTY_VALUE_WHEN
