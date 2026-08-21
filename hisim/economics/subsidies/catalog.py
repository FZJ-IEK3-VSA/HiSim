"""Subsidy catalog schema and parsing (cost_spec.md §5.1-§5.2, §5.6).

Everything that turns a `subsidy_catalog/<COUNTRY>.json` file into typed objects:
conditions, the benefit-kind hierarchy, eligible-cost specs, `SubsidyScheme`,
questionnaire entries and the `SubsidyCatalog` loader itself. No evaluation happens
here — that lives in `assessment` and `solver`. Split out of the former single-module
`subsidies.py` (PR-3 review); the package `__init__` re-exports everything.
"""

from __future__ import annotations

import enum
import json
import os
from dataclasses import dataclass, field
from typing import (
    Any,
    Callable,
    ClassVar,
    Dict,
    Iterable,
    List,
    Optional,
    Tuple,
)

from hisim.economics.carriers import EnergyCarrier
from hisim.economics.database import SourceEntry, SourceRegistry
from hisim.economics.provenance import (
    ParameterOrigin,
    ParameterProvenance,
    ProvenanceLedger,
    ResolvedSource,
)
from hisim.economics.timeline import CostCategory
from hisim.loadtypes import ComponentType

from hisim.economics.subsidies.context import SubsidyContextFields, SubsidyDataError


@dataclass(frozen=True)
class Condition:
    """One node of the eligibility predicate tree (§5.3) — inert catalog payload.

    W2.3: the node carries data only. Parsing it lives in :func:`parse_condition` (data side,
    called by the catalog loader) and evaluating it in :func:`evaluate_condition` (engine side,
    below), so the catalog half of this module has no behavior an evaluator could diverge from.
    """

    #: The comparison operators a leaf may use, and what each one means.
    CONDITION_OPS: ClassVar[Dict[str, Callable[[Any, Any], bool]]] = {
        "==": lambda a, b: a == b,
        "!=": lambda a, b: a != b,
        "<": lambda a, b: a < b,
        "<=": lambda a, b: a <= b,
        ">": lambda a, b: a > b,
        ">=": lambda a, b: a >= b,
        "in": lambda a, b: a in b,
        "contains": lambda a, b: b in a,
        "exists": lambda a, b: a is not None,
    }

    kind: str  # "all" | "any" | "not" | "leaf"
    children: Tuple["Condition", ...] = ()
    fieldname: Optional[str] = None
    op: Optional[str] = None
    value: Any = None


def parse_condition(raw: dict, scheme_id: str) -> Condition:
    """Parses and validates a condition node with clear errors (data side).

    Recursively turns the catalog's ``{"all": [...]}`` / ``{"any": [...]}`` / ``{"not": {...}}`` /
    ``{"field": ..., "op": ..., "value": ...}`` JSON into the inert :class:`Condition` AST, checking
    as it goes that the operator is one of the nine allowed ones and that the field name is either
    part of the statically known vocabulary or a ``measure.*`` path (those address arbitrary
    ``technical_attributes`` keys and can only be checked when a measure is at hand). Validating at
    load time rather than at solve time is the whole point of the data-only predicate language:
    there is no Python ``eval`` anywhere, and a mistyped catalog fails with the scheme id in the
    message instead of quietly never matching.

    Args:
        raw: One condition node from the catalog JSON.
        scheme_id: Owning scheme, used only to make error messages actionable.

    Returns:
        The parsed node; children are parsed depth-first, so the returned tree is complete.

    Raises:
        SubsidyDataError: On an unknown operator, an unknown field name, or a node that is neither
            an ``all``/``any``/``not`` combinator nor a leaf.
    """
    if "all" in raw:
        return Condition(kind="all", children=tuple(parse_condition(child, scheme_id) for child in raw["all"]))
    if "any" in raw:
        return Condition(kind="any", children=tuple(parse_condition(child, scheme_id) for child in raw["any"]))
    if "not" in raw:
        return Condition(kind="not", children=(parse_condition(raw["not"], scheme_id),))
    if "field" in raw:
        fieldname = raw["field"]
        operator = raw.get("op")
        if operator not in Condition.CONDITION_OPS:
            raise SubsidyDataError(f"Scheme {scheme_id}: unknown op {operator!r} in condition on {fieldname!r}.")
        if not (fieldname in SubsidyContextFields.KNOWN_CONTEXT_FIELDS or fieldname.startswith("measure.")):
            raise SubsidyDataError(f"Scheme {scheme_id} references unknown field {fieldname!r}.")
        return Condition(kind="leaf", fieldname=fieldname, op=operator, value=raw.get("value"))
    raise SubsidyDataError(f"Scheme {scheme_id}: condition node {raw!r} is neither all/any/not nor a leaf.")


def referenced_fields(condition: Condition) -> List[str]:
    """All context fields referenced anywhere in the tree, in tree order (§5.7).

    Duplicates are kept: a field two schemes' conditions test twice weighs twice in the
    question-ordering heuristic, which is the established behavior.
    """
    if condition.kind == "leaf":
        return [condition.fieldname] if condition.fieldname else []
    names: List[str] = []
    for child in condition.children:
        names.extend(referenced_fields(child))
    return names


class BenefitKind(str, enum.Enum):
    """Tagged union of benefit kinds (§5.2).

    The *mechanism* a scheme uses to compute its support, surveyed from the schemes actually
    running in the EU (§5.1): a share of the eligible cost, a stackable bonus share, a fixed lump
    sum, an amount per unit of size, a multi-year tax credit, a reduced VAT rate, soft-loan terms,
    or a per-kWh operational payment. The tag selects the typed payload class
    (:attr:`BenefitTypes.BY_KIND`) that carries the mechanism's parameters, so adding a mechanism
    means adding a payload type here — adding a *programme* means editing a catalog file only.

    Note the deliberate distinction from :class:`PayoutKind`: a kind here says how much support is
    computed, a payout kind says when and in what form the money reaches the applicant.
    ``SHARE_OF_ELIGIBLE_COST`` and ``BONUS_SHARE`` share one payload type and differ only in
    intent — bonuses are the ones meant to stack on a base rate within a cumulation group.
    """

    SHARE_OF_ELIGIBLE_COST = "SHARE_OF_ELIGIBLE_COST"
    BONUS_SHARE = "BONUS_SHARE"
    LUMP_SUM = "LUMP_SUM"
    PER_UNIT = "PER_UNIT"
    TAX_CREDIT = "TAX_CREDIT"
    REDUCED_VAT = "REDUCED_VAT"
    SOFT_LOAN = "SOFT_LOAN"
    OPERATIONAL = "OPERATIONAL"


@dataclass(frozen=True)
class BenefitField:
    """One JSON key of a benefit payload: how it is named, converted and defaulted.

    The declarative description each benefit payload uses to state its JSON surface (its ``SPEC``),
    so that :meth:`Benefit.parse` can validate any payload generically — required keys present,
    unknown keys rejected, values convertible — instead of every kind hand-rolling its own parsing.
    Keeping the mapping declarative is what lets the loader name both the scheme and the offending
    key in an error message (W2.2).
    """

    key: str  # key in the catalog JSON
    name: str  # field name on the benefit dataclass
    convert: Callable[[Any], Any]
    required: bool = True
    default: Any = None


def _shares(raw: Any) -> Tuple[float, ...]:
    """Converts a JSON list of annual shares into a tuple of floats.

    The converter for :class:`TaxCreditBenefit`'s optional uneven payout schedule. It rejects
    strings and bytes explicitly because both are iterable and would otherwise be silently
    accepted character by character; the resulting `TypeError` is turned into a
    :class:`SubsidyDataError` naming the scheme by :meth:`Benefit.parse`.
    """
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Iterable):
        raise TypeError(f"expected a list of annual shares, got {raw!r}")
    return tuple(float(item) for item in raw)


@dataclass(frozen=True)
class Benefit:
    """Typed benefit payload of a scheme (§5.2, W2.2).

    The catalog JSON format is unchanged — ``benefit: {"kind": ..., <parameters>}`` — but the
    loader parses that dict into one of the frozen subclasses below, so a misspelled or missing
    parameter fails at load time naming the scheme and the key, instead of surfacing as a
    ``KeyError`` deep inside the cumulation solver.

    Subclasses declare their JSON surface in :attr:`SPEC`; :attr:`BenefitTypes.BY_KIND` maps each
    :class:`BenefitKind` to the payload type that implements it.
    """

    #: The payload's JSON keys, in documentation order.
    SPEC: ClassVar[Tuple[BenefitField, ...]] = ()

    @classmethod
    def parse(cls, raw: Dict[str, Any], scheme_id: str, kind: "BenefitKind") -> "Benefit":
        """Builds the payload from the catalog dict, rejecting missing/unknown/unparsable keys.

        The generic loader for every benefit kind, driven by the subclass's :attr:`SPEC`. Rejecting
        *unknown* keys as hard as missing ones is deliberate: a typo in a catalog would otherwise be
        dropped silently and the scheme would pay out at a default rate nobody intended.

        Args:
            raw: The ``benefit`` object from the catalog with its ``kind`` key already removed.
            scheme_id: Owning scheme, for error messages.
            kind: The declared benefit kind, likewise only used in error messages.

        Returns:
            An instance of the concrete payload class this was called on.

        Raises:
            SubsidyDataError: If a mandatory key is missing, an unknown key is present, or a value
                does not convert; the message always names the scheme and the key.
        """
        values: Dict[str, Any] = {}
        for spec in cls.SPEC:
            if spec.key in raw:
                try:
                    values[spec.name] = spec.convert(raw[spec.key])
                except (TypeError, ValueError, KeyError) as err:
                    raise SubsidyDataError(
                        f"Scheme {scheme_id}: benefit key {spec.key!r} of kind {kind.value} is not "
                        f"a valid {spec.convert.__name__ if hasattr(spec.convert, '__name__') else 'value'} "
                        f"({raw[spec.key]!r}): {err}."
                    ) from err
            elif spec.required:
                raise SubsidyDataError(
                    f"Scheme {scheme_id}: benefit of kind {kind.value} misses the mandatory key "
                    f"{spec.key!r} (expected keys: {[item.key for item in cls.SPEC]})."
                )
            else:
                values[spec.name] = spec.default
        unknown = sorted(set(raw) - {spec.key for spec in cls.SPEC})
        if unknown:
            raise SubsidyDataError(
                f"Scheme {scheme_id}: benefit of kind {kind.value} has unknown key(s) {unknown} "
                f"(expected keys: {[item.key for item in cls.SPEC]})."
            )
        return cls(**values)

    def value_estimate(self, gross_cost_in_euro: float, measure_size: float) -> float:
        """Rough upper bound of the support this benefit could unlock, for question ordering.

        The single definition of the *simplified* valuation (§5.7 pruning power): undiscounted,
        uncapped, on the gross measure cost. The exact, per-slot, capped valuation lives in the
        cumulation solver (:func:`_combination_awards`); both now read the same typed fields, so
        the two can no longer drift apart on key names or defaults.
        """
        del gross_cost_in_euro, measure_size  # unvalued kinds (loans, VAT, operational support)
        return 0.0


@dataclass(frozen=True)
class ShareBenefit(Benefit):
    """A share of the eligible cost — base rate (SHARE_OF_ELIGIBLE_COST) or bonus (BONUS_SHARE).

    The workhorse mechanism of the EU programmes surveyed in §5.1 (BEG EM: 30 % base plus speed,
    income and efficiency bonuses). It is the only payload the cumulation solver stacks: schemes
    sharing a ``cumulation_group`` have their rates added and then scaled back to the group's
    ``combined_rate_cap`` (§5.4), which is why base rate and bonus need no structural distinction
    here. The rate applies to the *eligible* cost basis of its own scheme — categories, VAT basis,
    proration and cap are per scheme, so two stacked schemes may compute on different bases.
    """

    rate: float  # fraction of the eligible cost basis, e.g. 0.30

    SPEC: ClassVar[Tuple[BenefitField, ...]] = (BenefitField("rate", "rate", float),)

    def value_estimate(self, gross_cost_in_euro: float, measure_size: float) -> float:
        """Rate on the gross cost."""
        del measure_size
        return gross_cost_in_euro * self.rate


@dataclass(frozen=True)
class LumpSumBenefit(Benefit):
    """A fixed amount, clamped to the eligible cost basis when the scheme declares one.

    Models the fixed-grant programmes of §5.1 — Austria's "Raus aus Öl und Gas" boiler-replacement
    grant, income-banded MaPrimeRénov' amounts — where the support does not scale with what the
    measure cost. The amount is *exact* in all three uncertainty slots (a statutory number has no
    band), which is why a lump sum can be the binding constraint in one slot and not another once
    the overall cap scales it; see :func:`_scaled_to_cap`.
    """

    amount: float  # euro, year-0 nominal, exact in all slots

    SPEC: ClassVar[Tuple[BenefitField, ...]] = (BenefitField("amount", "amount", float),)

    def value_estimate(self, gross_cost_in_euro: float, measure_size: float) -> float:
        """The amount itself."""
        del gross_cost_in_euro, measure_size
        return self.amount


@dataclass(frozen=True)
class PerUnitBenefit(Benefit):
    """An amount per unit of measure size (EUR/kW, EUR/m², ... — the unit of the cost facts).

    Covers €/m² insulation grants and €/kWp PV programmes (§5.1). The unit is *not* stated in the
    catalog: the amount is multiplied by ``ComponentCostFacts.size``, so it silently inherits
    whatever ``size_unit`` the asset class declares — which is the same unit the device price is
    quoted in, and is enforced consistent by the pre-run resolution check. Like a lump sum the
    result is exact in all slots and clamped to the eligible-cost basis.
    """

    amount: float  # euro per unit of `ComponentCostFacts.size` (kW, m², liter, ...)

    SPEC: ClassVar[Tuple[BenefitField, ...]] = (BenefitField("amount", "amount", float),)

    def value_estimate(self, gross_cost_in_euro: float, measure_size: float) -> float:
        """Amount times measure size."""
        del gross_cost_in_euro
        return self.amount * measure_size


@dataclass(frozen=True)
class TaxCreditBenefit(Benefit):
    """A share of the eligible cost, paid out over `years` (optionally unevenly).

    Models income-tax deduction programmes — Italy's Ecobonus over ten installments, Germany's
    §35c EStG (shipped as 20 % over three unevenly split years) — where the *timing* of the payout
    materially changes the NPV and is therefore not a detail: the solver discounts each installment
    before comparing combinations. Unlike the share kinds this one does not stack additively in a
    cumulation group; §35c is expressed as mutually exclusive with the grant programmes via
    ``excludes``.
    """

    rate: float  # fraction of the eligible cost basis, spread over `years`
    years: int  # number of annual installments, starting in year 1
    annual_shares: Tuple[float, ...] = ()  # optional uneven split; must sum to 1 and match `years`

    SPEC: ClassVar[Tuple[BenefitField, ...]] = (
        BenefitField("rate", "rate", float),
        BenefitField("years", "years", int),
        BenefitField("annual_shares", "annual_shares", _shares, required=False, default=()),
    )

    def __post_init__(self) -> None:
        """Validates the payout schedule (§5.2): whole years, shares summing to 1."""
        if self.years < 1:
            raise SubsidyDataError(f"Tax credit benefit needs years >= 1, got {self.years}.")
        if self.annual_shares:
            if len(self.annual_shares) != self.years:
                raise SubsidyDataError(
                    f"Tax credit benefit declares {len(self.annual_shares)} annual_shares for "
                    f"{self.years} years — the two must agree."
                )
            if abs(sum(self.annual_shares) - 1.0) > 1e-9:
                raise SubsidyDataError(
                    f"Tax credit benefit annual_shares must sum to 1, got {sum(self.annual_shares)}."
                )

    def schedule_shares(self) -> Tuple[float, ...]:
        """Per-year shares of the total credit; even split unless the catalog says otherwise.

        Returns one share per installment year, in order, always summing to 1 (enforced in
        :meth:`__post_init__`). The cumulation solver multiplies the total credit by these to build
        the award's ``schedule_amounts``, which land on the timeline in years 1..N — so this is the
        function that decides how much of a tax credit survives discounting.
        """
        if self.annual_shares:
            return self.annual_shares
        return tuple(1.0 / self.years for _ in range(self.years))

    def value_estimate(self, gross_cost_in_euro: float, measure_size: float) -> float:
        """Rate on the gross cost (undiscounted, like the other kinds)."""
        del measure_size
        return gross_cost_in_euro * self.rate


@dataclass(frozen=True)
class ReducedVatBenefit(Benefit):
    """A reduced VAT rate on the measure.

    **Unwired (§7 B7):** the resulting `PayoutKind.VAT_REDUCTION` award has no consumer anywhere
    in the engine — no VAT netting reads `reduced_vat_rate`. The kind is typed here so a catalog
    entry is at least well-formed; wiring it (or deleting it) is a separate fix-or-freeze call.
    """

    vat_rate: float

    SPEC: ClassVar[Tuple[BenefitField, ...]] = (BenefitField("vat_rate", "vat_rate", float),)


@dataclass(frozen=True)
class LoanTermsBenefit(Benefit):
    """Soft-loan terms: interest rate, term and an optional repayment grant (Tilgungszuschuss).

    A subsidized loan is not a cash grant but an *override of the financing plan* (§4.4): the award
    carries these terms, ``calculators/financing_application.py`` substitutes them into the
    :class:`~hisim.economics.financing.FinancingPlan`, and the benefit shows up as the interest the
    applicant does not pay. The optional repayment grant is the one part that is genuine support in
    year 0 — note §7 B3: the solver values it on the gross measure cost while financing applies it
    to the loan principal, which disagree whenever less than the full cost is financed (masked
    today because the shipped KfW rate is 0.0).
    """

    interest_rate: float  # nominal annual rate of the subsidized loan
    term: int  # years
    repayment_grant_rate: float = 0.0  # share of the principal written off (Tilgungszuschuss)

    SPEC: ClassVar[Tuple[BenefitField, ...]] = (
        BenefitField("interest_rate", "interest_rate", float),
        BenefitField("term", "term", int),
        BenefitField("repayment_grant_rate", "repayment_grant_rate", float, required=False, default=0.0),
    )


@dataclass(frozen=True)
class OperationalBenefit(Benefit):
    """A per-kWh payment on one carrier for a number of years (feed-in style support).

    The one benefit kind whose value depends on the *simulation* rather than on the investment:
    EEG-style feed-in remuneration and heat-generation premiums are paid per kWh actually sold (or,
    where no sold energy is recorded, bought — see :func:`_support_value`), so the engine can only
    value it once the meters have reported. Payments run for ``duration_years`` from year 1 and are
    truncated by the observation horizon when the flows are laid out; the rate is nominal and does
    not escalate, matching the fixed-for-20-years EEG contract convention (§8.5).
    """

    rate_per_kwh: float  # euro per kWh of the named carrier, nominal, fixed for the duration
    carrier: EnergyCarrier  # the carrier whose sold (or bought) energy is paid on
    duration_years: int  # payment years, counted from year 1

    SPEC: ClassVar[Tuple[BenefitField, ...]] = (
        BenefitField("rate_per_kwh", "rate_per_kwh", float),
        BenefitField("carrier", "carrier", EnergyCarrier),
        BenefitField("duration_years", "duration_years", int),
    )


class BenefitTypes:
    """The benefit payload type of each kind — the single dispatch table used by loader and engine.

    One table serves both directions: :func:`parse_benefit` uses it to pick the payload class for a
    catalog entry, and :meth:`SubsidyScheme.__post_init__` uses it to verify that a scheme built in
    Python pairs its declared kind with the matching payload. Having exactly one mapping is what
    lets the rest of the module narrow a payload by ``isinstance`` without defensive re-checks.
    """

    BY_KIND: Dict[BenefitKind, type] = {
        BenefitKind.SHARE_OF_ELIGIBLE_COST: ShareBenefit,
        BenefitKind.BONUS_SHARE: ShareBenefit,
        BenefitKind.LUMP_SUM: LumpSumBenefit,
        BenefitKind.PER_UNIT: PerUnitBenefit,
        BenefitKind.TAX_CREDIT: TaxCreditBenefit,
        BenefitKind.REDUCED_VAT: ReducedVatBenefit,
        BenefitKind.SOFT_LOAN: LoanTermsBenefit,
        BenefitKind.OPERATIONAL: OperationalBenefit,
    }


def parse_benefit(raw: Dict[str, Any], scheme_id: str) -> Tuple[BenefitKind, Benefit]:
    """Parses a catalog `benefit` object into its kind and typed payload (§5.2).

    The entry point the catalog loader uses for the ``benefit`` block: it reads the ``kind`` tag,
    looks the payload class up in :attr:`BenefitTypes.BY_KIND` and delegates the remaining keys to
    :meth:`Benefit.parse`. The pair it returns is stored on the scheme as-is, so from here on the
    engine works with typed fields and never with raw dictionary lookups (W2.2).

    Args:
        raw: The catalog's ``benefit`` object, including its ``kind`` key.
        scheme_id: Owning scheme, for error messages.

    Returns:
        The declared kind and its parsed, frozen payload.

    Raises:
        SubsidyDataError: If ``kind`` is missing or unknown, or the payload fails to parse.
    """
    if "kind" not in raw:
        raise SubsidyDataError(f"Scheme {scheme_id}: benefit misses the mandatory key 'kind'.")
    try:
        kind = BenefitKind(raw["kind"])
    except ValueError as err:
        raise SubsidyDataError(
            f"Scheme {scheme_id}: unknown benefit kind {raw['kind']!r} "
            f"(known kinds: {[item.value for item in BenefitKind]})."
        ) from err
    payload = {key: value for key, value in raw.items() if key != "kind"}
    return kind, BenefitTypes.BY_KIND[kind].parse(payload, scheme_id, kind)  # type: ignore[attr-defined]


class PayoutKind(str, enum.Enum):
    """How benefits map to timeline entries (§5.2).

    The second half of the benefit model: :class:`BenefitKind` says how much support is computed,
    this says *when and in what form* it arrives, which is what the timeline needs. The separation
    matters because the two are not in bijection — the same 30 % share can be paid as a year-0
    grant in one country and as a multi-year tax deduction in another, and NPV distinguishes them
    sharply. A scheme declares its payout in the catalog's ``payout.kind`` and defaults to
    ``UPFRONT_GRANT``; ``calculators/subsidy_application.py`` is the sole interpreter.

    The mapping it implements: ``UPFRONT_GRANT`` → one negative SUBSIDY entry at year 0;
    ``TAX_CREDIT_SCHEDULE`` → one entry per scheduled year 1..N, truncated at the horizon;
    ``OPERATIONAL`` → one entry per year of the award's duration, valued on the annualized energy;
    ``LOAN_TERMS`` → no entry of its own, it overrides the financing plan (§4.4);
    ``VAT_REDUCTION`` → typed but deliberately unwired, see :class:`ReducedVatBenefit` (§7 B7).
    """

    UPFRONT_GRANT = "UPFRONT_GRANT"
    TAX_CREDIT_SCHEDULE = "TAX_CREDIT_SCHEDULE"
    LOAN_TERMS = "LOAN_TERMS"
    OPERATIONAL = "OPERATIONAL"
    VAT_REDUCTION = "VAT_REDUCTION"


@dataclass
class EligibleCostSpec:
    """Which cost categories count, capped and prorated how (§5.2).

    Defines a scheme's *cost basis* — the euro figure its rate, lump sum or credit is computed on —
    and it is per scheme, not per measure: two schemes stacking on the same heat pump may count
    different categories or cap at different ceilings. Four dials, all data: which timeline
    categories count (investment, planning, removal by default — the removal of the old boiler is
    fundable, its maintenance is not), whether the basis is gross or net of VAT, whether mixed-use
    buildings are prorated down to their residential share, and a per-dwelling-unit ceiling.
    :func:`_eligible_cost_basis` is the one place that applies all four, in that order.
    """

    categories: List[CostCategory] = field(
        default_factory=lambda: [CostCategory.INVESTMENT, CostCategory.PLANNING, CostCategory.REMOVAL]
    )
    cap_per_dwelling_unit_in_euro: List[float] = field(default_factory=list)  # tiered; empty = uncapped
    basis: str = "GROSS"  # GROSS | NET (of VAT)
    proration: str = "NONE"  # NONE | RESIDENTIAL_SHARE

    def cap_for_units(self, dwelling_units: int) -> Optional[float]:
        """Total eligible-cost cap; tier list, last value repeated for further units.

        Programmes cap the eligible cost per dwelling unit in descending tiers — the shipped BEG
        entry is 30 k€ for the first unit, 15 k€ for the next five and 8 k€ for every further one —
        so the building-wide ceiling is the tiered sum, not a flat multiple. Repeating the last
        tier for all remaining units is the standard "and further units" clause of those directives.

        Args:
            dwelling_units: Number of dwelling units in the building; values below 1 count as 1.

        Returns:
            The total cap in euro, or ``None`` when the scheme declares no cap at all — which the
            caller must distinguish from a cap of 0.
        """
        if not self.cap_per_dwelling_unit_in_euro:
            return None
        total = 0.0
        for unit_index in range(max(1, dwelling_units)):
            tier = min(unit_index, len(self.cap_per_dwelling_unit_in_euro) - 1)
            total += self.cap_per_dwelling_unit_in_euro[tier]
        return total


@dataclass
class SubsidyScheme:
    """One scheme of the catalog (§5.2).

    The in-memory image of one funding programme: where and when it applies, what it applies to,
    who qualifies, what it pays, on which cost basis, and how it interacts with other schemes.
    Everything a reviewer needs to check a euro amount against the directive is on this object,
    including the mandatory ``legal_basis`` and ``url`` — a scheme is a STATUTE-kind source in the
    sense of §3.10, which is why an unsourced one is refused at load time.

    Schemes are inert: they are read by :meth:`SubsidyCatalog.candidate_schemes` (jurisdiction and
    validity pre-filter), by :func:`assess_schemes` (eligibility) and by the cumulation solver
    (stacking, exclusions, valuation). Note that ``cumulation_group`` is a free-text *label*, not a
    reference to anything, whereas every id in ``excludes`` must name a real scheme — the data-file
    CI checks the latter.
    """

    id: str
    country: str
    region: Optional[str]  # NUTS code; None = nationwide, matches any applicant region
    valid_from: str  # ISO date; only the year is used for the validity test
    valid_to: Optional[str]  # ISO date, None = open-ended
    legal_basis: str  # mandatory: the directive/statute this encodes
    url: str  # mandatory: where that text can be read
    asset_classes: List[ComponentType]
    measure_kinds: List[str]  # INSTALL | REPLACE
    eligibility: Condition
    benefit_kind: BenefitKind
    benefit: Benefit  # typed, kind-specific parameters (W2.2)
    eligible_cost: EligibleCostSpec
    cumulation_group: Optional[str]  # label; schemes sharing it stack additively (share kinds)
    combined_rate_cap: Optional[float]  # cap on the group's summed rate, e.g. 0.70
    excludes: List[str]  # scheme ids this one cannot be combined with (symmetric in effect)
    payout_kind: PayoutKind
    source_ids: Tuple[str, ...] = ()  # registry ids; mandatory for catalog-loaded schemes (W2.4)

    def __post_init__(self) -> None:
        """Keeps `benefit_kind` and the typed payload in sync (W2.2)."""
        expected = BenefitTypes.BY_KIND[self.benefit_kind]
        if not isinstance(self.benefit, expected):
            raise SubsidyDataError(
                f"Scheme {self.id}: benefit kind {self.benefit_kind.value} needs a "
                f"{expected.__name__}, got {type(self.benefit).__name__}."
            )

    def applies_to(self, asset_class: ComponentType, measure_kind: str) -> bool:
        """Whether the scheme covers the measure at all.

        The cheapest of the pre-filters: a scheme only enters the candidate set for an asset class
        it names and a measure kind (INSTALL / REPLACE) it funds. The distinction matters because
        several programmes — the BEG speed bonus is the obvious one — exist precisely to reward
        *replacing* a working fossil system and must not pay for a new-build installation.
        """
        return asset_class in self.asset_classes and measure_kind in self.measure_kinds


def scheme_context_fields(scheme: SubsidyScheme) -> List[str]:
    """Every context field a scheme depends on (§5.7) — the one definition of that set.

    Two sources: the eligibility conditions, and the eligible-cost spec, which needs the
    residential share when it prorates and the dwelling-unit count when it caps per unit.
    Question derivation and question-coverage validation both read this, so a new implied
    dependency is added once.
    """
    names = list(referenced_fields(scheme.eligibility))
    if scheme.eligible_cost.proration == "RESIDENTIAL_SHARE":
        names.append("building.residential_share")
    if scheme.eligible_cost.cap_per_dwelling_unit_in_euro:
        names.append("building.dwelling_units")
    return names


@dataclass
class QuestionEntry:
    """One localized question of the questionnaire catalog (§5.7).

    The presentation half of a context field: how to ask a user for it, in every supported
    language, with the answer options and the help text that explains why it matters. Entries live
    in ``subsidy_catalog/questions_<COUNTRY>.json`` keyed by the context field they fill, and the
    data-file CI (§9.6) fails if any field referenced by a shipped scheme has no entry — that check
    is what keeps the questionnaire complete by construction rather than by review.
    """

    fieldname: str  # the context field this answer fills, e.g. "building.heritage_status"
    answer_kind: str  # BOOLEAN | CHOICE | NUMBER | YEAR | INCOME_BAND
    question: Dict[str, str]  # language code -> question text (de, en)
    options: List[str] = field(default_factory=list)  # CHOICE: the admissible raw values
    option_labels: Dict[str, Dict[str, str]] = field(default_factory=dict)  # language -> value -> label
    help_text: Dict[str, str] = field(default_factory=dict)  # language -> explanatory text
    unit: Optional[str] = None  # NUMBER: the unit to display, e.g. "m2"


@dataclass
class Question:
    """A question to ask, with the schemes that made it necessary ("asked because").

    What :func:`required_questions` returns: a catalog entry plus the derived justification for
    asking it. ``asked_because`` lets a frontend show *why* a question appears and can never go
    stale relative to the catalog, since it is computed from the conditions rather than curated;
    ``pruning_power_in_euro`` is the ordering key that puts the most consequential question first,
    so a user who stops answering early has still answered the questions that mattered most.
    """

    entry: QuestionEntry
    asked_because: List[str] = field(default_factory=list)  # scheme ids
    # Upper bound of support the answer could unlock (ordering heuristic, §5.7):
    pruning_power_in_euro: float = 0.0


class SubsidyCatalog:
    """One country catalog plus the question catalog and source registry.

    The loaded, validated content of ``subsidy_catalog/<COUNTRY>.json`` and its companion
    ``questions_<COUNTRY>.json``: every scheme of that jurisdiction, the localized questionnaire
    entries, the country-level state-aid ceiling and the ``sources.json`` entries the schemes cite.
    It is the sole entry point to catalog data — the engine below never opens a file — and is
    attached to an evaluation through ``EconomicParameters.subsidy_catalog_path``; where no path is
    set, no catalog is loaded at all and the §10.1 legacy flat shim applies instead.

    A catalog can also be built in Python (tests, worked examples), in which case it carries no
    registry and its schemes record ``IN_MEMORY_DEFINITION`` provenance rather than citing sources.
    """

    #: Default on-disk location of the shipped subsidy catalogs — `hisim/subsidy_catalog`,
    #: three levels up from this file now that `subsidies` is a package.
    DEFAULT_PATH: ClassVar[str] = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "subsidy_catalog"
    )

    def __init__(
        self,
        schemes: List[SubsidyScheme],
        questions: Dict[str, QuestionEntry],
        snapshot_date: Optional[str],
        overall_cap_share: Optional[float],
        base_path: str,
        country: str,
        sources: Optional[Dict[str, SourceEntry]] = None,
    ) -> None:
        """Built by :meth:`load`.

        `sources` are the registry entries the loader resolved this catalog's `source_ids`
        against (W2.4). A catalog built in Python (tests, worked examples) passes none: its
        schemes then mint `IN_MEMORY_DEFINITION` provenance instead of citing registry ids.
        """
        self.schemes = schemes
        self.questions = questions
        self.snapshot_date = snapshot_date
        self.overall_cap_share = overall_cap_share
        self.base_path = base_path
        self.country = country
        #: Resolved `sources.json` entries by id — the resolution `load` used to be thrown away
        #: (W2.4), which is why the evaluator had to fabricate `inline:` ids.
        self.sources: Dict[str, SourceEntry] = dict(sources or {})

    @classmethod
    def load(cls, country: str, base_path: Optional[str] = None) -> "SubsidyCatalog":
        """Loads and validates `<base_path>/<COUNTRY>.json` plus `questions_<COUNTRY>.json`.

        Everything the catalog schema can be checked for statically is checked here, so that a
        malformed programme is a load error naming the scheme rather than a wrong euro amount much
        later: ``legal_basis``, ``url``, ``benefit`` and ``source_ids`` are mandatory, the benefit
        payload is parsed into its typed form (§5.2, W2.2), the eligibility tree is parsed and its
        field names checked against the context vocabulary (§5.3), and every cited source id is
        resolved against ``sources.json`` and kept for the provenance ledger (W2.4). The question
        file is optional at this level — its *completeness* is a CI check (§9.6), not a load error,
        so a catalog under development still loads.

        Args:
            country: ISO country code selecting both file names.
            base_path: Directory holding the catalog; defaults to the shipped
                :attr:`DEFAULT_PATH`.

        Returns:
            The loaded catalog, with resolved source entries attached.

        Raises:
            SubsidyDataError: If no catalog file exists for the country, or any scheme violates the
                schema (missing mandatory field, unknown benefit kind or asset class, unparsable
                condition, no source ids).
        """
        base = base_path or SubsidyCatalog.DEFAULT_PATH
        catalog_path = os.path.join(base, f"{country}.json")
        if not os.path.isfile(catalog_path):
            raise SubsidyDataError(f"No subsidy catalog for country {country!r} at {catalog_path}.")
        with open(catalog_path, encoding="utf-8") as file:
            raw = json.load(file)
        sources_path = os.path.join(base, "sources.json")
        registry = SourceRegistry.load(sources_path) if os.path.isfile(sources_path) else None
        resolved_sources: Dict[str, SourceEntry] = {}
        schemes = []
        for item in raw.get("schemes", []):
            scheme_id = item.get("id", "<missing id>")
            for mandatory in ("legal_basis", "url"):
                if not item.get(mandatory):
                    raise SubsidyDataError(f"Scheme {scheme_id}: mandatory field {mandatory!r} missing (§5.2).")
            if "benefit" not in item:
                raise SubsidyDataError(f"Scheme {scheme_id}: mandatory field 'benefit' missing (§5.2).")
            benefit_kind, benefit = parse_benefit(dict(item["benefit"]), scheme_id)
            eligible_raw = item.get("eligible_cost", {})
            cumulation = item.get("cumulation", {})
            source_ids = tuple(item.get("source_ids", []))
            if not source_ids:
                raise SubsidyDataError(
                    f"Scheme {scheme_id}: source_ids are mandatory for catalog schemes — an "
                    "unsourced scheme is not admissible (§3.10, W2.4)."
                )
            if registry is not None:
                for entry in registry.resolve(source_ids, f"scheme {scheme_id}"):
                    resolved_sources[entry.source_id] = entry
            schemes.append(
                SubsidyScheme(
                    id=scheme_id,
                    country=item["jurisdiction"]["country"],
                    region=item["jurisdiction"].get("region"),
                    valid_from=item.get("valid_from", "1900-01-01"),
                    valid_to=item.get("valid_to"),
                    legal_basis=item["legal_basis"],
                    url=item["url"],
                    asset_classes=[
                        _component_type(name, scheme_id) for name in item["applies_to"]["asset_classes"]
                    ],
                    measure_kinds=list(item["applies_to"].get("measure_kinds", ["INSTALL", "REPLACE"])),
                    eligibility=parse_condition(item.get("eligibility", {"all": []}), scheme_id),
                    benefit_kind=benefit_kind,
                    benefit=benefit,
                    eligible_cost=EligibleCostSpec(
                        categories=[
                            CostCategory(cat)
                            for cat in eligible_raw.get("categories", ["INVESTMENT", "PLANNING", "REMOVAL"])
                        ],
                        cap_per_dwelling_unit_in_euro=list(eligible_raw.get("cap_per_dwelling_unit_in_euro", [])),
                        basis=eligible_raw.get("basis", "GROSS"),
                        proration=eligible_raw.get("proration", "NONE"),
                    ),
                    cumulation_group=cumulation.get("group"),
                    combined_rate_cap=cumulation.get("combined_rate_cap"),
                    excludes=list(cumulation.get("excludes", [])),
                    payout_kind=PayoutKind(item.get("payout", {}).get("kind", "UPFRONT_GRANT")),
                    source_ids=source_ids,
                )
            )
        questions = {}
        questions_path = os.path.join(base, f"questions_{country}.json")
        if os.path.isfile(questions_path):
            with open(questions_path, encoding="utf-8") as file:
                questions_raw = json.load(file)
            for item in questions_raw.get("questions", []):
                questions[item["field"]] = QuestionEntry(
                    fieldname=item["field"],
                    answer_kind=item["answer_kind"],
                    question=item["question"],
                    options=item.get("options", []),
                    option_labels=item.get("option_labels", {}),
                    help_text=item.get("help", {}),
                    unit=item.get("unit"),
                )
        return cls(
            schemes=schemes,
            questions=questions,
            snapshot_date=raw.get("catalog_snapshot_date"),
            overall_cap_share=raw.get("overall_cap_share"),
            base_path=base,
            country=country,
            sources=resolved_sources,
        )

    # ------------------------------------------------------------------ provenance (§3.10, W2.4)

    def scheme_by_id(self, scheme_id: str) -> Optional[SubsidyScheme]:
        """The scheme with that id, or None (awards carry ids, not scheme objects).

        A :class:`SubsidyAward` deliberately references its scheme by id so that decisions stay
        serializable into the audit trail; this is how a consumer gets back to the scheme's legal
        basis, most importantly ``calculators/subsidy_application.py`` when it mints an award's
        provenance record.
        """
        return next((scheme for scheme in self.schemes if scheme.id == scheme_id), None)

    def resolved_sources(self, scheme: SubsidyScheme) -> List[SourceEntry]:
        """Registry entries backing one scheme — empty for in-memory catalogs.

        Returns the ``sources.json`` entries this catalog resolved for the scheme's ``source_ids``
        at load time, skipping any id the registry did not contain. An empty result is meaningful
        rather than an error: it identifies a scheme defined in Python, which has no registry
        behind it and is recorded as :attr:`ParameterOrigin.IN_MEMORY_DEFINITION`.
        """
        return [self.sources[source_id] for source_id in scheme.source_ids if source_id in self.sources]

    def source_resolver(self) -> Dict[str, ResolvedSource]:
        """The catalog's registry entries in the report representation (§3.10).

        Hands the subsidy registry to the result object, which merges it with the cost database's
        registry — the two id spaces are disjoint — so that a subsidy flow explained through the
        provenance ledger resolves to a real citation instead of a bare id. Consumed by the
        "sources used" table of the report and by the ``explain`` CLI.
        """
        return {source_id: entry.to_resolved() for source_id, entry in self.sources.items()}

    def provenance_for_scheme(
        self, scheme: SubsidyScheme, ledger: ProvenanceLedger, value: Any
    ) -> int:
        """Records the ledger entry backing one award of `scheme` (W2.4).

        A catalog-loaded scheme cites the registry ids its `sources.json` resolution produced at
        load time, so `explain` reaches the legal text a subsidy came from; the scheme's own
        `legal_basis` and `url` — which are scheme *fields*, not registry entries — ride along in
        `detail`. A scheme defined in Python (tests, worked examples) has no registry behind it
        and is recorded as :attr:`ParameterOrigin.IN_MEMORY_DEFINITION`, which is honest about
        having no source, instead of the `inline:` pseudo-ids this replaced.
        """
        detail = f"{scheme.legal_basis} <{scheme.url}>"
        if scheme.source_ids:
            return ledger.record(
                ParameterProvenance(
                    parameter=f"subsidy.{scheme.id}",
                    value=value,
                    origin=ParameterOrigin.DATABASE_ENTRY,
                    data_file=f"subsidy_catalog/{self.country}.json#{scheme.id}",
                    source_ids=scheme.source_ids,
                    detail=detail,
                )
            )
        return ledger.record(
            ParameterProvenance(
                parameter=f"subsidy.{scheme.id}",
                value=value,
                origin=ParameterOrigin.IN_MEMORY_DEFINITION,
                data_file=None,
                source_ids=(),
                detail=detail,
            )
        )

    def candidate_schemes(
        self, asset_class: ComponentType, measure_kind: str, region: Optional[str], year: int
    ) -> List[SubsidyScheme]:
        """Pre-filter by jurisdiction, asset class and validity (§5.7).

        The first stage of every subsidy computation: it narrows the whole catalog to the schemes
        that *could* apply to one measure before any context is looked at, which keeps the
        exponential cumulation solver working on a handful of schemes and keeps the question
        derivation from asking about programmes the case can never reach. A scheme with no region
        is nationwide and matches any applicant; a regional scheme matches only its own region, and
        an applicant who did not state a region is offered regional schemes too (the eligibility
        conditions can still reject them). Validity is tested on the *year* parsed from the ISO
        dates against the price basis year, i.e. against the economic "today" rather than the
        possibly historical weather year of the simulation.

        Args:
            asset_class: The measure's asset class.
            measure_kind: "INSTALL" or "REPLACE".
            region: The applicant's region key, or None if unstated.
            year: The year scheme validity is tested against.

        Returns:
            The candidate schemes, in catalog file order — which is also the order the cumulation
            solver enumerates them in and therefore its tie-break order.
        """
        result = []
        for scheme in self.schemes:
            if not scheme.applies_to(asset_class, measure_kind):
                continue
            if scheme.region is not None and region is not None and scheme.region != region:
                continue
            valid_from_year = int(scheme.valid_from[:4])
            valid_to_year = int(scheme.valid_to[:4]) if scheme.valid_to else 9999
            if not valid_from_year <= year <= valid_to_year:
                continue
            result.append(scheme)
        return result


def _component_type(name: str, scheme_id: str) -> ComponentType:
    """Resolves a catalog asset-class string to its `ComponentType`, by enum name or value.

    Accepting both spellings keeps catalog files readable while tolerating the enum's value
    strings; anything else is a data error rather than a silently unfunded asset class, which is
    why it raises instead of returning None (a scheme that quietly applies to nothing would look
    exactly like a scheme whose conditions failed).
    """
    for member in ComponentType:
        if name in (member.name, member.value):
            return member
    raise SubsidyDataError(f"Scheme {scheme_id}: unknown asset class {name!r}.")


# =========================================================================== engine
# Everything below consumes the catalog data above: the applicant/building context conditions
# resolve against, the condition evaluator, the eligibility assessment and the cumulation
# solver. Nothing above this line reads a context or evaluates anything (§2.5: the data half
# becomes `economics/data/`, this half `economics/engine/`; the cut is prepared, the physical
# move is deliberately left to the package split so imports churn once, not twice).
