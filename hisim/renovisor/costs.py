"""Why ``result.json`` carries no money, and where the money went instead.

Step 6 gave ``result.json`` a ``costs`` block: one state of one dwelling, priced over one horizon
with the whole investment in year 0, read out of the lifecycle cost engine's exports. Step 10
replaced it. A renovation is a *plan* — several states of the house over several years, each
stage's investment in its own year, each earlier stage's equipment ageing into the next — and one
run of one state cannot say what that costs. The staged evaluator can, and it writes
``economics_result.json`` (:mod:`hisim.economics.staged_document`), which carries the whole of the
money: the totals, the eight-group stacks, the per-subject build-up, the annual series, the
subsidies, the financing and the comparison against doing nothing.

The E-spec's rule — **one implementation of the money** — is therefore kept by removing the second
one rather than by keeping two in step. ``result.json`` is now the KPI half of the answer, and
every cost field of the contract appears in its ``missing`` list with the reason, because a field
that has moved is a different statement from a field nobody could produce, and decision R8 says
neither may be answered with a plausible zero.

What remains here is exactly that bookkeeping: the names of the contract's cost fields
(:class:`CostField`), the ``missing`` entries one calculation produces for them
(:class:`CostBuilder`), and the published shape of the block that the capability document and the
translation map show a caller before the first payload exists (:class:`CostSchema`). No figure is
read, no engine export is opened, and no price is estimated.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, ClassVar, Dict, Tuple

from hisim.renovisor.kpis import PayloadFieldRow
from hisim.renovisor.provenance import MissingField


class CostField(str, Enum):
    """The names the payload's ``costs`` block used, in HiSim spelling (decision C3).

    The block is gone; the names are not. They are what ``result.json["missing"]`` lists, what the
    capability document's ``results`` section publishes, and what a frontend looks for before it
    goes to ``economics_result.json`` instead. ``investment_breakdown`` is the one member that was
    never a contract field — it was the device/envelope split of decision Q23, which the staged
    document now carries as ``by_subject[]`` with a ``measure_id`` on every row.
    """

    INVESTMENT = "investment_costs_in_euro"
    ENERGY = "energy_costs_in_euro_per_year"
    MAINTENANCE = "maintenance_costs_in_euro_per_year"
    NET_PRESENT_VALUE = "net_present_value_in_euro"
    MONTHLY_TWENTY_YEARS = "monthly_net_cost_20y_in_euro"
    MONTHLY_TEN_YEARS = "monthly_net_cost_10y_in_euro"
    GRANT = "grant_in_euro"
    PAYBACK = "payback_period_in_years"
    PROPERTY_VALUE = "property_value_increase_in_percent"
    INVESTMENT_BREAKDOWN = "investment_breakdown"


class EconomicsDocument:
    """Where the money is, named once, so every reason and every schema row says the same thing.

    Three facts that would otherwise be spelled out at each of the places mentioning the split —
    the payload's ``missing`` reasons, the capability document's ``results`` section and the
    translation map's result pane. Naming them here is what sends a reader who follows any of the
    three to the same file, the same key and the same command.
    """

    #: The document the staged evaluator writes, and the whole of the money.
    FILE_NAME: ClassVar[str] = "economics_result.json"

    #: How it is produced: one invocation over the finished jobs of a plan.
    COMMAND: ClassVar[str] = (
        "python -m hisim.economics staged --stage <dir>:<from_year>:<label> ... --out "
        "economics_result.json"
    )

    #: Which key of it answers each cost field ``result.json`` no longer carries. Four keys are the
    #: figure itself; the other four point into a list whose rows carry it: ``plan.energy_year1[]``
    #: (one carrier's cost per row), ``plan.by_subject[]`` twice (one subject's maintenance, and
    #: one subject's investment for the breakdown, per row) and ``plan.subsidies[]`` (one scheme's
    #: award per row), so a reader sums or picks rows rather than reading one number. The
    #: twenty-year monthly figure is ``monthly_equivalent_cost_in_euro``, the equivalent annual
    #: cost over twelve — not ``monthly_cost_year1_in_euro``, which is year 1's cash and equals it
    #: only when the cost is flat (hisim-cyc.6); its horizon is stated in :attr:`CAVEATS`. Two
    #: :class:`CostField` members have no entry: ``property_value_increase_in_percent``, which
    #: moved nowhere because nothing anywhere produces it (decision A13), and
    #: ``monthly_net_cost_10y_in_euro``, which no key of a document evaluated over one horizon can
    #: answer (:attr:`NO_KEY`).
    WHERE: ClassVar[Dict[str, str]] = {
        CostField.INVESTMENT.value: "plan.totals.investment_year0_in_euro",
        CostField.ENERGY.value: "plan.energy_year1[].cost_in_euro",
        CostField.MAINTENANCE.value: "plan.by_subject[].maintenance_in_euro",
        CostField.NET_PRESENT_VALUE.value: "plan.totals.npv_in_euro",
        CostField.MONTHLY_TWENTY_YEARS.value: "plan.totals.monthly_equivalent_cost_in_euro",
        CostField.GRANT.value: "plan.subsidies[]",
        CostField.PAYBACK.value: "comparison.discounted_payback_year",
        CostField.INVESTMENT_BREAKDOWN.value: "plan.by_subject[]",
    }

    #: The fields the document does not answer at all, and why. A ten-year monthly figure is a
    #: different evaluation and not a different key: the staged document prices one plan over one
    #: horizon, so the figure exists only for a plan evaluated with ``horizon_years: 10``.
    NO_KEY: ClassVar[Dict[str, str]] = {
        CostField.MONTHLY_TEN_YEARS.value: (
            "the staged document is evaluated over one horizon; a ten-year figure needs a plan "
            "evaluated with `horizon_years: 10`"
        ),
    }

    #: What a key's figure depends on beyond the key itself, appended to its reason. The monthly
    #: figure is an annuity over the plan's horizon, so it answers the twenty-year question only
    #: for a plan evaluated over twenty years.
    CAVEATS: ClassVar[Dict[str, str]] = {
        CostField.MONTHLY_TWENTY_YEARS.value: (
            "the figure is over the document's `parameters.horizon_years` (20 in the backend's "
            "block); a plan evaluated over another horizon answers another question"
        ),
    }

    @classmethod
    def reason_for(cls, field_name: str) -> str:
        """The sentence ``result.json`` gives for one cost field it does not carry.

        Args:
            field_name: The :class:`CostField` value.

        Returns:
            One sentence naming the document and either the key inside it or why no key answers
            the field, with the key's :attr:`CAVEATS` entry where it has one.

        Raises:
            KeyError: If the field is in neither :attr:`WHERE` nor :attr:`NO_KEY`, which means a
                cost field was added without deciding where its figure lives now.
        """
        if field_name in cls.NO_KEY:
            return (
                f"the money is in {cls.FILE_NAME}, which does not carry this figure: "
                f"{cls.NO_KEY[field_name]}. Write the document with `{cls.COMMAND}`"
            )
        caveat = f"; {cls.CAVEATS[field_name]}" if field_name in cls.CAVEATS else ""
        return (
            f"the money is in {cls.FILE_NAME} ({cls.WHERE[field_name]}), which prices the whole "
            f"staged plan rather than this one job{caveat}; write it with `{cls.COMMAND}`"
        )


@dataclass(frozen=True)
class CostBlock:
    """What the cost half of one calculation contributes to ``result.json``.

    Args:
        values: The ``costs`` block. Always empty since step 10: ``result.json`` carries the KPI
            half of the answer and nothing monetary. The field is kept so the payload builder's
            two halves stay the same shape.
        missing: One entry per contract cost field, already prefixed with ``costs.``, each saying
            where that figure is instead.
    """

    values: Dict[str, Any]
    missing: Tuple[MissingField, ...]


class CostBuilder:
    """Lists the cost fields ``result.json`` does not carry, and why.

    It reads nothing and computes nothing: every cost field of the contract is absent from the
    payload by decision, so the whole of the builder is the reason each of them gives. Keeping it
    a class rather than a function keeps the payload builder's two halves symmetrical — one KPI
    builder, one cost builder, each returning values and ``missing`` — and gives the reasons one
    place to be reviewed.

    Example::

        block = CostBuilder().build()
        [entry.field for entry in block.missing]  # 'costs.investment_costs_in_euro', ...
    """

    #: The prefix every missing cost field carries in ``result.json["missing"]``.
    MISSING_PREFIX: ClassVar[str] = "costs"

    #: Why there is no property-value figure: decision A13 records that no model exists. It is
    #: the one cost field that did not move to ``economics_result.json``, because nothing
    #: anywhere produces it.
    PROPERTY_VALUE_REASON: ClassVar[str] = "no model for the value a renovation adds to a property (A13)"

    def build(self) -> CostBlock:
        """Return the empty ``costs`` block and one ``missing`` entry per cost field.

        Returns:
            The :class:`CostBlock`. Entries appear in the order of :class:`CostField`, so two
            runs of one request produce the same bytes (requirement R10).
        """
        missing = tuple(
            MissingField(field=f"{self.MISSING_PREFIX}.{field.value}", reason=self.reason_for(field))
            for field in CostField
        )
        return CostBlock(values={}, missing=missing)

    @classmethod
    def reason_for(cls, field: CostField) -> str:
        """The reason one cost field is absent from ``result.json``.

        Args:
            field: The cost field.

        Returns:
            The sentence its ``missing`` entry carries.
        """
        if field is CostField.PROPERTY_VALUE:
            return cls.PROPERTY_VALUE_REASON
        return EconomicsDocument.reason_for(field.value)


class CostSchema:
    """The published shape of the ``costs`` block, without running anything.

    The counterpart of :class:`hisim.renovisor.kpis.KpiSchema` for the money half of the payload:
    one row per :class:`CostField`, saying where the figure comes from and — since step 10 — that
    it does not come in ``result.json`` at all. The capability document's ``results`` section and
    the translation map both render these rows, so a frontend sees the split before the first
    payload exists rather than by finding a key missing.
    """

    #: Which block of ``result.json`` these rows describe; the prefix their ``missing`` entries
    #: carry.
    BLOCK: ClassVar[str] = CostBuilder.MISSING_PREFIX

    #: When every moved field is absent from ``result.json``: from step 10 onwards, always.
    MOVED_WHEN: ClassVar[str] = f"always absent; the money is in {EconomicsDocument.FILE_NAME}"

    #: When a field no document answers is absent: always, and no key will produce it.
    NO_KEY_WHEN: ClassVar[str] = "always absent; no document carries this figure"

    #: When the property-value figure is absent: always; decision A13 records that no model exists.
    PROPERTY_VALUE_WHEN: ClassVar[str] = "always absent (A13)"

    @classmethod
    def rows(cls) -> Tuple[PayloadFieldRow, ...]:
        """Return one row per field of the ``costs`` block, in payload order.

        Returns:
            One row per :class:`CostField`. Every row is an absent one: it names what produces
            the figure now and carries the sentence ``result.json["missing"]`` states, in place
            of a provenance it no longer has.
        """
        return tuple(
            PayloadFieldRow(
                cls.BLOCK,
                field.value,
                cls._produced_by(field),
                reason=CostBuilder.reason_for(field),
                when=cls._when(field),
            )
            for field in CostField
        )

    @classmethod
    def _when(cls, field: CostField) -> str:
        """When one cost field is absent from ``result.json``, for the row's condition column."""
        if field is CostField.PROPERTY_VALUE:
            return cls.PROPERTY_VALUE_WHEN
        if field.value in EconomicsDocument.NO_KEY:
            return cls.NO_KEY_WHEN
        return cls.MOVED_WHEN

    @classmethod
    def _produced_by(cls, field: CostField) -> str:
        """What produces one cost figure now, for the row's source column.

        Empty for the two fields nothing produces: the property-value increase, which no model
        anywhere computes (A13), and the ten-year monthly cost, which needs a second evaluation
        over a ten-year horizon rather than a key of this document.
        """
        if field is CostField.PROPERTY_VALUE or field.value in EconomicsDocument.NO_KEY:
            return ""
        return f"{EconomicsDocument.FILE_NAME}: {EconomicsDocument.WHERE[field.value]}"
