"""The canonical cash-flow timeline: dated, categorized, payer-tagged cash flows of one variant (cost_spec.md §3.6).

Every perspective, actor view and KPI is a filter, allocation or discounting of this one list of `CashFlowEntry`
objects, so `sum(component NPVs) == perspective NPV` and `landlord + tenant == system` hold by construction.

Sign convention for the whole package: cost is positive, money arriving is negative (subsidies, feed-in revenue,
residual value, anyway credits, loan disbursements). An NPV is therefore a net present cost; lower is better. This
module holds only the kernel types; the calculators in `hisim/economics/calculators/` produce the entries.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from hisim.economics.uncertainty import UncertainValue


class CostCategory(str, enum.Enum):
    """Categories of timeline entries (§3.6).

    Every cash flow carries exactly one category, and downstream rules key off it: which flows a perspective drops,
    which are revenue-type, which sign they must carry, which a subsidy may count as eligible cost, and which display
    group they stack into. Energy and loans are split finely (working, standing, CO2 price, capacity charge; interest,
    principal, disbursement) because the parts escalate differently. A new member must be added to the sets on
    `CategoryRules`, in `calculators/categories.py` and in `presentation_style.py`; otherwise it defaults to
    positive-signed, non-revenue and display group 0.
    """

    INVESTMENT = "INVESTMENT"
    PLANNING = "PLANNING"
    REMOVAL = "REMOVAL"
    REPLACEMENT = "REPLACEMENT"
    RESIDUAL_VALUE = "RESIDUAL_VALUE"
    MAINTENANCE = "MAINTENANCE"
    FIXED_OPERATION = "FIXED_OPERATION"
    ENERGY_WORKING = "ENERGY_WORKING"
    ENERGY_STANDING = "ENERGY_STANDING"
    ENERGY_CO2_PRICE = "ENERGY_CO2_PRICE"
    ENERGY_CAPACITY_CHARGE = "ENERGY_CAPACITY_CHARGE"
    FEED_IN_REVENUE = "FEED_IN_REVENUE"
    SUBSIDY = "SUBSIDY"
    LOAN_INTEREST = "LOAN_INTEREST"
    LOAN_PRINCIPAL = "LOAN_PRINCIPAL"
    LOAN_DISBURSEMENT = "LOAN_DISBURSEMENT"
    CO2_DAMAGE = "CO2_DAMAGE"  # macroeconomic only
    ANYWAY_COST_CREDIT = "ANYWAY_COST_CREDIT"  # avoided like-for-like replacement (§4.1)
    REPLACEMENT_RESERVE = "REPLACEMENT_RESERVE"  # sinking fund of the operating view (§4.2)
    MODERNIZATION_LEVY = "MODERNIZATION_LEVY"  # tenant pays, landlord receives (§6.4)


# The sets below are read together with the engine-side category sets in
# `hisim/economics/calculators/categories.py`. They live here because `CostCategory` does.

class CategoryRules:
    """Category sets that say what each `CostCategory` means (§3.9, §4.2, §5.5).

    Answers four questions once, as data: is a category revenue-type for band assembly, must its entries be negative,
    is it an investment flow, is it subsidy support. The engine-side sets in `calculators/categories.py` complement
    these; they live here because `CostCategory` does.
    """

    #: Categories whose parameters are revenue-type for band assembly (§3.9): the optimistic world takes their
    #: band maximum, so the band is mirrored with `as_revenue()`. Not the same as "the entry is negative"; see
    #: `NEGATIVE_SIGN_CATEGORIES` and `expected_sign`.
    REVENUE_CATEGORIES = frozenset(
        {
            CostCategory.FEED_IN_REVENUE,
            CostCategory.SUBSIDY,
            CostCategory.RESIDUAL_VALUE,
            CostCategory.ANYWAY_COST_CREDIT,
        }
    )

    #: Categories whose entries are negative-signed, money arriving rather than leaving (§3.9).
    #: `REVENUE_CATEGORIES` plus LOAN_DISBURSEMENT, whose band is not mirrored (it is a share of the year-0
    #: investment band). MODERNIZATION_LEVY is absent because its sign depends on the payer; see `expected_sign`.
    NEGATIVE_SIGN_CATEGORIES = frozenset(REVENUE_CATEGORIES | {CostCategory.LOAN_DISBURSEMENT})

    #: Categories dropped when a perspective excludes investment (OPERATING_ONLY, §4.2): everything caused by
    #: investment, including the subsidies and loan flows paying for it and the residual value and anyway credit
    #: netted against it. §4.2 adds a replacement reserve instead. `calculators/categories.py` has two narrower sets.
    INVESTMENT_CATEGORIES = frozenset(
        {
            CostCategory.INVESTMENT,
            CostCategory.PLANNING,
            CostCategory.REMOVAL,
            CostCategory.REPLACEMENT,
            CostCategory.RESIDUAL_VALUE,
            CostCategory.SUBSIDY,
            CostCategory.LOAN_INTEREST,
            CostCategory.LOAN_PRINCIPAL,
            CostCategory.LOAN_DISBURSEMENT,
            CostCategory.ANYWAY_COST_CREDIT,
        }
    )

    #: The support flows that a perspective with `subsidy_mode = NONE` and MACROECONOMIC accounting report none of
    #: (§5.5, §4.5). The evaluator never generates them for such a perspective; this set names the category.
    SUBSIDY_FLOW_CATEGORIES = frozenset({CostCategory.SUBSIDY})

    #: The four charge categories `apply_tariff` produces for one energy carrier's bill (§8). Feed-in revenue is
    #: not one of them: it is what exports earn, not part of what a bought kWh costs. A tuple because consumers
    #: render it in this order; `plausibility.PlausibilityCategories` and `views.ViewCategories` alias it.
    BILL_CATEGORIES = (
        CostCategory.ENERGY_WORKING,
        CostCategory.ENERGY_STANDING,
        CostCategory.ENERGY_CAPACITY_CHARGE,
        CostCategory.ENERGY_CO2_PRICE,
    )


def discount_factor(interest_rate: float, year: int) -> float:
    """Return 1 / (1 + i)^year, the discount factor every present value in the package uses.

    `CashFlowTimeline.npv`, `npv_by` and `EconomicParameters.discount_factor` all delegate here. `year` is an offset
    from year 0 under the end-of-year convention of §4.1, not a calendar year, so year 0 yields 1.0. Negative rates are
    allowed; `EconomicParameters` rejects i <= -1.

    Args:
        interest_rate: Nominal calculation interest rate as a fraction (0.03 = 3 %).
        year: Whole years after year 0.

    Returns:
        The factor that turns a nominal amount in that year into its present value.
    """
    return 1.0 / ((1.0 + interest_rate) ** year)


class Actor(str, enum.Enum):
    """Who pays a cash flow (§6.1).

    An allocation ruleset (`actors.py`) re-tags or splits entries onto these payers, and a perspective then filters
    with `scoped_to`. Since allocation only re-tags and splits, landlord + tenant (+ owner) NPV equals the SYSTEM NPV
    in every slot (§6.5). SYSTEM is both the tag every calculator emits before allocation and the total view, so
    `scoped_to(SYSTEM)` returns all entries.
    """

    SYSTEM = "system"  # before allocation / total view
    OWNER_OCCUPIER = "owner_occupier"
    LANDLORD = "landlord"
    TENANT = "tenant"


class SubjectKind(str, enum.Enum):
    """What a timeline subject refers to (§3.7).

    A subject is the free-form string naming who an entry belongs to: a simulation component or cost subject, or an
    `EnergyCarrier`. The per-subject breakdowns use this to look up an asset class for components and none for
    carriers.
    """

    COMPONENT = "COMPONENT"
    CARRIER = "CARRIER"


@dataclass(frozen=True)
class CashFlowEntry:
    """One dated, categorized, payer-tagged cash flow (§3.6).

    `amount_in_euro` is a band (an `UncertainValue` with minimum, best estimate and maximum slots, one per world of
    §3.9) in nominal euros of the entry's year: escalated, not discounted. Cost is positive, revenue and subsidy
    negative. `year` is used for discounting, `category` for perspective filters, `subject` and `subject_kind` for
    per-component pivots, `payer` for the actor split, and `provenance_ids` for `explain` (§3.10). The dataclass is
    frozen; `with_payer` and `scaled` return copies.
    """

    year: int
    amount_in_euro: UncertainValue
    category: CostCategory
    subject: str  # component name or carrier
    subject_kind: SubjectKind = SubjectKind.COMPONENT
    payer: Actor = Actor.SYSTEM
    subsidy_scheme_id: Optional[str] = None
    provenance_ids: Tuple[int, ...] = ()

    def with_payer(self, payer: Actor) -> "CashFlowEntry":
        """Return a copy assigned to another payer (§6).

        All other fields are kept, so re-tagging cannot change any total.
        """
        return replace(self, payer=payer)

    def scaled(self, factor: float) -> "CashFlowEntry":
        """Return a copy with the amount scaled by a non-negative share (§6).

        Splits a shared cost between landlord and tenant into entries whose shares sum to one. Scaling is
        slot-wise and rejects negative factors (see `UncertainValue.scale`), so a split never flips a sign.
        """
        return replace(self, amount_in_euro=self.amount_in_euro.scale(factor))


class SignExpectation:
    """The two sign expectations an entry can carry, as string constants for violation messages.

    Zero is allowed under both, so a subsidy that resolved to nothing is legitimate.
    """

    NON_NEGATIVE = "non-negative"
    NON_POSITIVE = "non-positive"


def expected_sign(entry: CashFlowEntry) -> str:
    """Return the sign the §3.9 convention requires of this entry in every slot.

    Cost is positive, money arriving is negative. Three cases:

    * `NEGATIVE_SIGN_CATEGORIES` must be non-positive (revenues, credits, loan disbursement);
    * MODERNIZATION_LEVY depends on the payer: the tenant pays the rent increase (non-negative), the landlord receives
      it (non-positive); an unallocated levy (payer SYSTEM) counts as the paying leg (§6.4);
    * everything else must be non-negative.

    This is a different question from `CategoryRules.REVENUE_CATEGORIES`, which says whose parameter bands are
    mirrored.
    """
    if entry.category == CostCategory.MODERNIZATION_LEVY:
        return SignExpectation.NON_POSITIVE if entry.payer == Actor.LANDLORD else SignExpectation.NON_NEGATIVE
    return (
        SignExpectation.NON_POSITIVE
        if entry.category in CategoryRules.NEGATIVE_SIGN_CATEGORIES
        else SignExpectation.NON_NEGATIVE
    )


@dataclass(frozen=True)
class SignViolation:
    """One entry whose sign disagrees with the §3.9 convention.

    Carries the offending entry, so the message shows its category, subject and amount; a violation usually means a
    calculator forgot `as_revenue()` or booked a credit under a cost category.
    """

    entry: CashFlowEntry
    expected_sign: str  # SignExpectation.NON_NEGATIVE / NON_POSITIVE, as decided by `expected_sign`

    def __str__(self) -> str:
        """A one-line description naming the entry and what was expected."""
        amount = self.entry.amount_in_euro
        return (
            f"{self.entry.category.value} entry for {self.entry.subject!r} in year "
            f"{self.entry.year} (payer {self.entry.payer.value}) should be "
            f"{self.expected_sign} but is ({amount.minimum}, {amount.best_estimate}, {amount.maximum})"
        )


def sign_violation(entry: CashFlowEntry) -> Optional[SignViolation]:
    """Check one entry against the §3.9 sign convention and return the violation, or None when it complies.

    All three slots must comply; a band straddling zero is a violation.
    """
    amount = entry.amount_in_euro
    slots = (amount.minimum, amount.best_estimate, amount.maximum)
    expected = expected_sign(entry)
    if expected == SignExpectation.NON_POSITIVE:
        if any(value > 0.0 for value in slots):
            return SignViolation(entry=entry, expected_sign=SignExpectation.NON_POSITIVE)
    elif any(value < 0.0 for value in slots):
        return SignViolation(entry=entry, expected_sign=SignExpectation.NON_NEGATIVE)
    return None


@dataclass
class CashFlowTimeline:
    """The canonical timeline of one variant under one perspective: an ordered list of `CashFlowEntry` objects.

    Offers filtering (`filtered`, `without_categories`, `scoped_to`), discounting (`npv`), pivots (`npv_by`) and the
    undiscounted yearly series (`nominal_annual_series`); methods return new timelines. Entries added through `add` and
    `extend` are sign-checked. Pass `validate=False` for synthetic timelines that carry arbitrary signs; derived
    timelines inherit the flag.
    """

    entries: List[CashFlowEntry] = field(default_factory=list)
    #: Whether `add`/`extend` enforce the §3.9 sign convention. Engine timelines leave this on.
    validate: bool = True

    def add(self, entry: CashFlowEntry) -> None:
        """Append an entry, sign-checking it first unless validation is off.

        Insertion order is kept and is the order exports and audits walk the timeline in.

        Raises:
            ValueError: If validation is on and the entry violates the §3.9 sign convention.
        """
        if self.validate:
            violation = sign_violation(entry)
            if violation is not None:
                raise ValueError(f"Timeline entry violates the §3.9 sign convention: {violation}")
        self.entries.append(entry)

    def sign_violations(self) -> List["SignViolation"]:
        """Return every entry whose sign disagrees with the §3.9 convention, without raising."""
        violations = [sign_violation(entry) for entry in self.entries]
        return [violation for violation in violations if violation is not None]

    def validate_signs(self) -> None:
        """Check the §3.9 sign convention over all entries at once.

        Needed only for timelines built with `entries=` in the constructor or with `validate=False`, since `add` and
        `extend` already check each entry. The rules are those of `expected_sign`.

        Raises:
            ValueError: If any entry violates the convention; the message names up to ten of them.
        """
        violations = self.sign_violations()
        if violations:
            details = "; ".join(str(violation) for violation in violations[:10])
            raise ValueError(
                f"{len(violations)} timeline entries violate the §3.9 sign convention: {details}"
            )

    def extend(self, entries: Iterable[CashFlowEntry]) -> None:
        """Append several entries in order, sign-checking each unless validation is off.

        Delegates to `add`; on a violation the entries before it stay appended.
        """
        for entry in entries:
            self.add(entry)

    def filtered(self, predicate: Callable[[CashFlowEntry], bool]) -> "CashFlowTimeline":
        """Return a new timeline with the entries matching the predicate.

        Entries are shared, not copied (they are frozen), and the `validate` flag is inherited.
        """
        return CashFlowTimeline(
            entries=[entry for entry in self.entries if predicate(entry)], validate=self.validate
        )

    def without_categories(self, categories: frozenset) -> "CashFlowTimeline":
        """Return a new timeline without the given categories, e.g. `INVESTMENT_CATEGORIES` for the operating view."""
        return self.filtered(lambda entry: entry.category not in categories)

    def scoped_to(self, payer: "Actor") -> "CashFlowTimeline":
        """Return the entries a perspective scoped to `payer` reports on; SYSTEM means all entries (§6).

        This is the one definition of perspective scoping; the aggregation calculator,
        `LifecycleCostResult.scoped_timeline` and `explain` all use it.
        """
        if payer == Actor.SYSTEM:
            return self
        return self.filtered(lambda entry: entry.payer == payer)

    def npv(self, interest_rate: float) -> UncertainValue:
        """Return the slot-wise net present value at the given discount rate.

        Each entry is discounted from its year to year 0 and summed per slot, giving a net present cost (lower is
        better, negative means the variant earns money). Times `EconomicParameters.annuity_factor()` it is the
        equivalent annual cost.

        Args:
            interest_rate: Nominal discount rate as a fraction, normally `EconomicParameters.interest_rate`.

        Returns:
            The discounted total as a band; an empty timeline yields exact zero.
        """
        total = UncertainValue.exact(0.0)
        for entry in self.entries:
            total = total + entry.amount_in_euro.scale(discount_factor(interest_rate, entry.year))
        return total

    def npv_by(
        self,
        interest_rate: float,
        key: Callable[[CashFlowEntry], Any],
    ) -> Dict[Any, UncertainValue]:
        """Return the slot-wise NPV pivoted by an arbitrary key, such as category, subject or payer.

        Every "NPV by ..." figure uses this, so the buckets always sum to `npv(interest_rate)` per slot (§7.4).

        Args:
            interest_rate: Nominal discount rate, as for `npv`.
            key: Maps an entry to its bucket; anything hashable works.

        Returns:
            Bucket -> discounted band, in first-appearance order.
        """
        result: Dict[Any, UncertainValue] = {}
        for entry in self.entries:
            discounted = entry.amount_in_euro.scale(discount_factor(interest_rate, entry.year))
            bucket = key(entry)
            result[bucket] = result.get(bucket, UncertainValue.exact(0.0)) + discounted
        return result

    def npv_split_by(
        self,
        interest_rate: float,
        key: Callable[[CashFlowEntry], Any],
    ) -> Tuple[Dict[Any, float], Dict[Any, float]]:
        """Return `npv_by` with each bucket split into its costs and its credits, best-estimate slot only.

        Treemaps and Sankey ribbons cannot draw negative values, so a subject that both costs and earns (a PV system
        with an investment and a feed-in revenue) needs two magnitudes rather than one netted figure. Entries are split
        by sign before summing. Callers that need bands use `npv_by`.

        Args:
            interest_rate: Nominal discount rate, as for `npv`.
            key: Maps an entry to its bucket; anything hashable works. An exception it raises propagates unchanged.

        Returns:
            `(costs, credits)`, each bucket -> positive euros in first-appearance order; `costs[b] - credits[b]` is the
                best-estimate `npv_by` figure. A bucket with only costs is absent from `credits` and vice versa.
        """
        cost_buckets: Dict[Any, float] = {}
        credit_buckets: Dict[Any, float] = {}
        for entry in self.entries:
            discounted = entry.amount_in_euro.best_estimate * discount_factor(interest_rate, entry.year)
            bucket = key(entry)
            side = cost_buckets if discounted >= 0.0 else credit_buckets
            side[bucket] = side.get(bucket, 0.0) + abs(discounted)
        return cost_buckets, credit_buckets

    def nominal_annual_series(self, horizon_years: int) -> List[UncertainValue]:
        """Return the undiscounted euros per year 0..T (index = year), the liquidity view (§4.3).

        The year-1 monthly figure is derived from it as annual / 12. Entries outside 0..T are ignored.

        Args:
            horizon_years: T; the list has T + 1 elements, index 0 being year 0.

        Returns:
            One band per year, exact zero for years without entries.
        """
        series = [UncertainValue.exact(0.0) for _ in range(horizon_years + 1)]
        for entry in self.entries:
            if 0 <= entry.year <= horizon_years:
                series[entry.year] = series[entry.year] + entry.amount_in_euro
        return series

    def subjects(self) -> List[str]:
        """Return all distinct subjects in first-appearance order.

        Breakdowns, stacked-bar exports and audit tables iterate this, so their row order stays stable across runs.
        """
        seen: Dict[str, None] = {}
        for entry in self.entries:
            seen.setdefault(entry.subject, None)
        return list(seen.keys())
