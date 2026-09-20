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

from typing import Any, Mapping, Tuple

import pytest

from hisim.economics.__main__ import StagedCli
from hisim.economics.staged_document import StagedDocument
from hisim.renovisor.costs import CostBuilder, CostField, CostSchema, EconomicsDocument

pytestmark = pytest.mark.base


def _resolves(schema: Mapping[str, Any], key: str) -> bool:
    """Whether one dotted document key names a field the shipped JSON Schema declares.

    The keys the ``missing`` reasons point at are read by a person and by a frontend, so what has
    to be true of them is that the field exists — not that the sentence quoting them was written.
    ``[]`` in a key means "each element of this array", which is how the document spells a figure
    that is one row per subject or per carrier.

    Args:
        schema: The parsed ``economics_result.schema.json``.
        key: A dotted key such as ``plan.totals.npv_in_euro`` or ``plan.by_subject[]``.

    Returns:
        Whether the key resolves to a declared property.
    """
    node: Any = _dereferenced(schema, schema)
    for step in key.split("."):
        name, array = (step[:-2], True) if step.endswith("[]") else (step, False)
        properties = node.get("properties") if isinstance(node, Mapping) else None
        if not isinstance(properties, Mapping) or name not in properties:
            return False
        node = _dereferenced(schema, properties[name])
        if array:
            if node.get("type") != "array":
                return False
            node = _dereferenced(schema, node.get("items", {}))
    return True


def _dereferenced(schema: Mapping[str, Any], node: Any) -> Any:
    """One schema node with a local ``$ref`` followed, or the node itself.

    The document's schema factors its repeated shapes into ``$defs`` — one ``evaluation`` serves
    both the reference and the plan — so walking a key means following those references. Only the
    local ``#/...`` form is resolved, which is the only form the shipped schema uses.

    Args:
        schema: The whole schema, the references are relative to.
        node: The node to dereference.

    Returns:
        The referenced node, or ``node`` when it is not a reference.
    """
    seen = 0
    while isinstance(node, Mapping) and isinstance(node.get("$ref"), str) and seen < 10:
        target: Any = schema
        for step in node["$ref"].lstrip("#/").split("/"):
            if not isinstance(target, Mapping) or step not in target:
                return {}
            target = target[step]
        node = target
        seen += 1
    return node


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

    def test_a_moved_field_names_a_key_the_document_schema_really_has(self) -> None:
        """Following the reason has to lead somewhere, which is checked against the schema.

        The reason points at a dotted key of ``economics_result.json``; asserting that the string
        appears in the sentence proves only that the sentence was written. What a caller needs is
        that the key *resolves*, so every entry of the ``WHERE`` map is walked through the shipped
        JSON Schema here -- a key naming a field the document does not have then fails the build
        instead of sending a frontend to a null.
        """
        schema = StagedDocument.schema()
        for field_name, key in EconomicsDocument.WHERE.items():
            assert _resolves(schema, key), f"{field_name} points at {key!r}, which the schema has not got"
            assert key in CostBuilder.reason_for(CostField(field_name))

    def test_the_command_that_writes_the_document_is_the_one_the_cli_offers(self) -> None:
        """A reason naming a subcommand nobody registered would send a caller nowhere."""
        reason = CostBuilder.reason_for(CostField.NET_PRESENT_VALUE)

        assert EconomicsDocument.FILE_NAME in reason
        assert EconomicsDocument.COMMAND.split()[:4] == ["python", "-m", "hisim.economics", "staged"]
        assert "--stage" in EconomicsDocument.COMMAND and "--out" in EconomicsDocument.COMMAND
        assert StagedCli.DEFAULT_PERSPECTIVE
        assert EconomicsDocument.COMMAND in reason

    def test_the_twenty_year_monthly_figure_says_the_annuity_and_the_division(self) -> None:
        """It is the annual annuity divided by twelve, not the year-1 cash flow.

        The map used to point it at ``monthly_cost_year1_in_euro``, which is what the plan
        actually pays in its first year and equals the annuity only when the cost is flat. A
        caller following the reason then read a different concept with the same unit.
        """
        reason = CostBuilder.reason_for(CostField.MONTHLY_TWENTY_YEARS)

        assert EconomicsDocument.WHERE[CostField.MONTHLY_TWENTY_YEARS.value] == (
            "plan.totals.equivalent_annual_cost_in_euro"
        )
        assert "divided by twelve" in reason

    def test_the_ten_year_monthly_figure_names_no_key_and_says_why(self) -> None:
        """One document is one horizon; a ten-year figure is a second evaluation, not a key."""
        reason = CostBuilder.reason_for(CostField.MONTHLY_TEN_YEARS)

        assert CostField.MONTHLY_TEN_YEARS.value not in EconomicsDocument.WHERE
        assert "horizon_years: 10" in reason

    def test_the_payback_period_names_the_comparison_it_is_now_part_of(self) -> None:
        """It was absent for want of a second run; the staged plan always has its reference."""
        assert "comparison.discounted_payback_year" in CostBuilder.reason_for(CostField.PAYBACK)

    def test_the_property_value_figure_moved_nowhere_and_says_why(self) -> None:
        """The one cost field nothing anywhere produces: decision A13 records that no model exists."""
        reason = CostBuilder.reason_for(CostField.PROPERTY_VALUE)

        assert reason == CostBuilder.PROPERTY_VALUE_REASON
        assert "A13" in reason
        assert EconomicsDocument.FILE_NAME not in reason

    def test_every_cost_field_is_decided_one_way_or_the_other(self) -> None:
        """A cost field added without deciding where its figure lives now is a failing build.

        Three answers exist and every field has exactly one: a key of the staged document, a
        statement that no key answers it (the ten-year monthly cost, which needs a second
        evaluation), or the property-value field, which no model anywhere produces.
        """
        decided = set(EconomicsDocument.WHERE) | set(EconomicsDocument.NO_KEY)
        expected = {field.value for field in CostField} - {CostField.PROPERTY_VALUE.value}

        assert decided == expected
        assert not set(EconomicsDocument.WHERE) & set(EconomicsDocument.NO_KEY)


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
