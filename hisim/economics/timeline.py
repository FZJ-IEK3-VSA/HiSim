"""The canonical cash-flow timeline (cost_spec.md §3.6).

One set of dated, categorized, payer-tagged cash flows per variant; every perspective, actor
view and KPI is a filter, allocation or discounting of this same timeline.

That is the load-bearing design decision of the whole engine (§3.1 principle 3). Total NPV, the
equivalent annual cost, NPV by category / component / payer, the nominal annual series, the monthly
figure, the actor split, the per-component stacks in the frontend — none of them is computed
separately. Each is a filter, a pivot or a discounting of the *same* list of `CashFlowEntry`
objects, which is why `sum(component NPVs) == perspective NPV` and `landlord + tenant == system`
hold by construction per uncertainty slot rather than by careful bookkeeping, and why `explain` can
trace any published number back to the entries that produced it.

**Sign convention, stated once for the whole package:** cost is positive, money arriving is
negative. A subsidy, a feed-in revenue, a residual value at the horizon, an anyway-cost credit and a
loan disbursement are all negative entries; investments, maintenance, energy bills and loan
service are positive. An NPV is therefore a net present *cost* — a lower number is better, and a
negative total means the variant nets money. Note the deliberate asymmetry documented on
`CategoryRules` below: "is this entry negative" and "is this parameter's band mirrored" are two
different questions with two different category sets (W3.7).

This module owns the timeline kernel — the categories, the payer enum, the entry and container
types, the sign contract and the one discount formula — and deliberately owns nothing that produces
entries. Building them is the calculators' job (`hisim/economics/calculators/`), allocating payers
is `actors.py`, and choosing which categories a given view drops is `perspectives.py` plus the
engine-side category sets in `calculators/categories.py`. Keeping the kernel free of those keeps it
importable from every layer, including presentation.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from hisim.economics.uncertainty import UncertainValue


class CostCategory(str, enum.Enum):
    """Categories of timeline entries (§3.6).

    The cost-type vocabulary of the engine: every cash flow is tagged with exactly one member, and
    that tag is what every downstream rule keys off — which flows a perspective drops, which are
    revenue-type for slot assembly, which sign they must carry, which subsidy scheme may count them
    as eligible cost, and which display group they stack into in the reports. Categories are
    finer-grained than a reader may expect (energy split into working / standing / CO2-price /
    capacity charge, loans into interest / principal / disbursement) precisely because those parts
    escalate at different rates and are treated differently by the macroeconomic and actor views.

    Adding a member is therefore not a cosmetic act: the sets on `CategoryRules`, the engine sets in
    `calculators/categories.py` and the display grouping in `presentation_style.py` all have to say
    what the new category means, and an unclassified one silently defaults to positive-signed,
    non-revenue and display group 0.
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


# The three sets below are read together with the engine-side category sets in
# `hisim/economics/calculators/categories.py`, which documents where they disagree (W3.3).
# They are defined here (and re-exported there) because `CostCategory` lives in this kernel
# module: defining them in the engine would make the kernel import the engine.

class CategoryRules:
    """The semantic taxonomy over `CostCategory` — which categories mean what (§3.9, §4.2, §5.5).

    Four questions a consumer of the timeline keeps asking — is this revenue-type for band
    assembly, must it be negative, is it an investment flow, is it subsidy support — answered once,
    as data, so that no calculator, exporter or report re-derives them with a slightly different
    membership. Grouping them in a class rather than as loose module constants makes the taxonomy
    greppable and gives the W3.7 distinction between mirroring and sign a single place to be
    explained.

    The sets are read together with the engine-side sets of `calculators/categories.py`, which
    documents where the two disagree; they live here because `CostCategory` does, and defining them
    on the engine side would invert the kernel's import direction.
    """

    #: Categories whose parameters are revenue-type **for slot assembly** (§3.9): the optimistic
    #: world takes their band maximum, so the parameter band is mirrored with `as_revenue()`.
    #:
    #: W3.7: this is *not* the same question as "is the entry negative". Band mirroring and sign
    #: are two properties, and they do not coincide — see `NEGATIVE_SIGN_CATEGORIES` and
    #: `expected_sign` below. Conflating them is why sign validation could not be switched on.
    REVENUE_CATEGORIES = frozenset(
        {
            CostCategory.FEED_IN_REVENUE,
            CostCategory.SUBSIDY,
            CostCategory.RESIDUAL_VALUE,
            CostCategory.ANYWAY_COST_CREDIT,
        }
    )

    #: Categories whose entries are **negative-signed** — money arriving rather than leaving (§3.9).
    #:
    #: `REVENUE_CATEGORIES` plus LOAN_DISBURSEMENT: a disbursement is money arriving, but its band is
    #: *not* mirrored for slot assembly (it is a share of the year-0 investment band, so the world in
    #: which the investment is expensive is the world in which the loan is large). MODERNIZATION_LEVY
    #: is deliberately absent because its sign depends on the payer — see `expected_sign`.
    NEGATIVE_SIGN_CATEGORIES = frozenset(REVENUE_CATEGORIES | {CostCategory.LOAN_DISBURSEMENT})

    #: Categories dropped when a perspective excludes investment (OPERATING_ONLY, §4.2).
    #: The broadest of the investment-related sets: everything investment-*caused*, including the
    #: subsidies and loan flows that pay for it and the residual value and anyway credit netted
    #: against it, so what remains is the honest running cost — to which §4.2 adds a replacement
    #: reserve in place of the discarded replacement flows. Compare the two narrower engine sets in
    #: `calculators/categories.py`, whose module docstring tabulates where the three differ and why.
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

    #: The support flows a perspective with `subsidy_mode = NONE` reports nothing of (§5.5) — the
    #: gross half of the shipped gross/net perspective pair — and which MACROECONOMIC accounting
    #: also has none of, subsidies being transfers rather than resource costs (§4.5). Note that the
    #: evaluator realizes both by never *generating* the flows for such a perspective rather than by
    #: filtering with this set; it names the category the two rules are about.
    SUBSIDY_FLOW_CATEGORIES = frozenset({CostCategory.SUBSIDY})

    #: What an energy carrier's bill is made of (§8) — the four charge categories `apply_tariff`
    #: can produce for one carrier. Feed-in revenue is deliberately not one of them: it is money
    #: the export side earns, not part of what a bought kWh costs, and including it would net two
    #: different quantities into one "price".
    #:
    #: A tuple rather than a frozenset because its consumers render it in this order. It lives in
    #: the kernel so that the plausibility panel's effective-price check and report section 4 make
    #: the *same* statement about the same carrier; the two used to hold verbatim copies with
    #: nothing keeping them equal, so the panel could start validating a price the report does not
    #: show (review finding 14). `plausibility.PlausibilityCategories` and `views.ViewCategories`
    #: now alias this attribute.
    BILL_CATEGORIES = (
        CostCategory.ENERGY_WORKING,
        CostCategory.ENERGY_STANDING,
        CostCategory.ENERGY_CAPACITY_CHARGE,
        CostCategory.ENERGY_CO2_PRICE,
    )


def discount_factor(interest_rate: float, year: int) -> float:
    """1 / (1 + i)^year — **the** discount formula of the module (cost-spec-v2 W4.3).

    Every present value in `hisim/economics` routes through this function, either directly or
    via `CashFlowTimeline.npv`/`npv_by` and `EconomicParameters.discount_factor`, which both
    delegate here. It lives in the timeline kernel because that is the one module every layer
    (calculators, results, exports, reports) may already import; before W4.3 the same
    expression was written out in at least four places outside it.

    `year` is an offset from the year-0 investment date under the end-of-year convention of §4.1,
    not a calendar year, so year 0 yields exactly 1.0 and intra-year timing is out of scope. Rates
    are nominal by default, matching the nominal cash flows; a real-terms study simply supplies real
    rates throughout (§4.1). Negative interest rates are permitted and behave as expected — only
    i <= -1 is rejected, and that check sits on `EconomicParameters`.

    Args:
        interest_rate: Nominal calculation interest rate as a fraction (0.03 = 3 %).
        year: Whole years after year 0.

    Returns:
        The factor a nominal amount in that year is multiplied by to obtain its present value.
    """
    return 1.0 / ((1.0 + interest_rate) ** year)


class Actor(str, enum.Enum):
    """Payers of cash flows (§6.1).

    Tagging each entry with who actually pays it is what turns one timeline into the owner, landlord
    and tenant views without a second calculation: an allocation ruleset (`actors.py`) rewrites or
    splits entries onto these payers, and a perspective then simply filters (`scoped_to`). Because
    allocation only re-tags and splits, the zero-sum invariant — landlord + tenant (+ owner) NPV
    equals the SYSTEM NPV in every slot — holds structurally and is asserted in tests (§6.5).

    `SYSTEM` is both the "no allocation has happened yet" state every calculator emits and the total
    view a system-scope perspective reports; `scoped_to(SYSTEM)` therefore returns everything rather
    than the entries literally tagged SYSTEM.
    """

    SYSTEM = "system"  # before allocation / total view
    OWNER_OCCUPIER = "owner_occupier"
    LANDLORD = "landlord"
    TENANT = "tenant"


class SubjectKind(str, enum.Enum):
    """What a timeline subject refers to (§3.7).

    A timeline subject is a free-form string, and this says how to read it: the name of a simulation
    component or cost subject, or an `EnergyCarrier`. The per-subject breakdowns and the frontend
    stacks need the distinction to look up an asset class and KPI tag for components while leaving
    carriers without one — energy is billed per carrier, not per device.
    """

    COMPONENT = "COMPONENT"
    CARRIER = "CARRIER"


@dataclass(frozen=True)
class CashFlowEntry:
    """One dated, categorized, payer-tagged cash flow (§3.6).

    Sign convention: cost positive, revenue/subsidy negative. ``amount_in_euro`` carries the
    LOW/BEST_ESTIMATE/HIGH world values (§3.9).

    The atom of the whole engine: everything the calculators produce is a list of these, and every
    result, KPI and export cell is an aggregate of some subset of them. The five tag fields are what
    make that possible — `year` for discounting, `category` for the perspective filters and the
    breakdowns, `subject` (+ `subject_kind`) for the per-component pivot, `payer` for the actor
    split, and `provenance_ids` for `explain`, which unions the ledger records of the contributing
    entries rather than re-deriving anything (§3.10).

    Amounts are **nominal** euros in the entry's year — escalation has already been applied, and
    discounting has not; the timeline is stored undiscounted so that both the economic (NPV) and the
    liquidity (out-of-pocket per year) views can be read off the same data (§4.3). The dataclass is
    frozen, so `with_payer`/`scaled` return copies; entries are never mutated in place, which is
    what lets a single canonical timeline be shared by several derived views.
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
        """Copy with a different payer (allocation rulesets, §6).

        The re-tagging half of allocation: an entry produced as SYSTEM is assigned to whoever the
        ruleset says bears it, with everything else — year, amount, category, subject, provenance —
        carried over untouched, which is precisely why allocation cannot change any total.
        """
        return replace(self, payer=payer)

    def scaled(self, factor: float) -> "CashFlowEntry":
        """Copy with the amount scaled by a non-negative share (entry splitting, §6).

        The splitting half of allocation: a cost shared between landlord and tenant becomes two
        entries whose shares sum to one, so the zero-sum invariant is preserved by construction.
        Scaling is slot-wise and rejects negative factors (see `UncertainValue.scale`), so a split
        can never flip an entry's sign or invert its band.
        """
        return replace(self, amount_in_euro=self.amount_in_euro.scale(factor))


class SignExpectation:
    """The two sign expectations an entry can carry (W3.7).

    A tiny namespace of string constants rather than an enum, because these values are compared and
    printed in violation messages and nothing else. "Non-negative" and "non-positive" rather than
    "positive"/"negative" on purpose: a zero-valued entry (a subsidy that resolved to nothing, a
    replacement outside the horizon) is legitimate under either expectation.
    """

    NON_NEGATIVE = "non-negative"
    NON_POSITIVE = "non-positive"


def expected_sign(entry: CashFlowEntry) -> str:
    """The sign the §3.9 convention requires of this entry, in every slot (W3.7).

    The single authority on entry signs, consulted by `sign_violation` and therefore by every
    `add`/`extend` on a validating timeline. It exists because the convention cannot be expressed as
    one category set — the modernization levy is a transfer whose sign depends on which leg you are
    looking at — and a payer-aware predicate is what finally allowed sign validation to be switched
    on at all. Read it together with `CategoryRules`, whose `REVENUE_CATEGORIES` answers the
    *different* question of which parameter bands get mirrored.

    Cost is positive, money arriving is negative. Three cases:

    * `NEGATIVE_SIGN_CATEGORIES` — non-positive (revenues, credits, loan disbursement);
    * MODERNIZATION_LEVY — **payer-dependent**: the tenant *pays* the rent increase (positive),
      the landlord *receives* it (negative). It is one category carrying a transfer pair, so the
      category alone cannot decide (§6.4, §7 B11); a levy entry that has not been allocated yet
      (payer SYSTEM) is read as the paying leg;
    * everything else — non-negative.
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
    """One entry whose sign disagrees with its category's convention (W3.7).

    A reported violation rather than a bare exception message, so the batch checker can collect all
    of them and the caller can decide how to present them. Carrying the offending entry itself keeps
    the diagnosis specific: a violation almost always means a calculator forgot `as_revenue()` or
    booked a credit under a cost category, and both are visible from the entry's own fields.
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
    """Checks one entry against the §3.9 sign convention; None when it complies (W3.7).

    All three slots must satisfy the expectation, not just the best_estimate: a band that straddles zero
    means the entry is a cost in one world and a revenue in another, which no category in the
    taxonomy legitimately is and which would break the envelope reading of the totals. Returning an
    optional rather than raising lets `CashFlowTimeline.add` raise eagerly while
    `sign_violations()` collects the full list for a diagnostic.
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
    """The canonical timeline of one variant under one perspective.

    The container the §3.6 principle rests on: an ordered list of `CashFlowEntry` objects plus the
    handful of operations every consumer needs — filter (`filtered`, `without_categories`,
    `scoped_to`), discount (`npv`), pivot (`npv_by`) and the undiscounted liquidity view
    (`nominal_annual_series`). Nothing here interprets economics; the calculators build the entries
    and `results.py`/`exports.py` name the aggregates. The methods return *new* timelines rather
    than mutating, so the evaluator's timeline can be derived from repeatedly without copies
    diverging.

    Entries added through `add`/`extend` are sign-validated (W3.7). Construct with
    ``validate=False`` for synthetic timelines that deliberately carry arbitrary signs — a
    hand-written net cash-flow series booked under one neutral category, for instance. The flag
    is inherited by `filtered`/`without_categories` so a derived view keeps the same regime.
    """

    entries: List[CashFlowEntry] = field(default_factory=list)
    #: Whether `add`/`extend` enforce the §3.9 sign convention. Engine timelines leave this on.
    validate: bool = True

    def add(self, entry: CashFlowEntry) -> None:
        """Appends an entry, sign-validating it first unless validation is off (W3.7).

        Validating on insertion is what makes a sign mistake point at the calculator that made it
        instead of surfacing as an implausible KPI three layers later. Insertion order is preserved
        and is the order exports and audits walk the timeline in, so a run stays byte-reproducible.

        Raises:
            ValueError: If the entry violates the §3.9 sign convention and validation is on.
        """
        if self.validate:
            violation = sign_violation(entry)
            if violation is not None:
                raise ValueError(f"Timeline entry violates the §3.9 sign convention: {violation}")
        self.entries.append(entry)

    def sign_violations(self) -> List["SignViolation"]:
        """Every entry whose sign disagrees with the §3.9 sign convention (W3.7).

        The non-raising, batch form of the check: it reports the full list so a diagnostic can show
        a reviewer every offending entry at once instead of the first one. Used by
        `validate_signs()` and available to the plausibility panel and tests.
        """
        violations = [sign_violation(entry) for entry in self.entries]
        return [violation for violation in violations if violation is not None]

    def validate_signs(self) -> None:
        """Asserts the §3.9 sign convention over all entries at once (W3.7).

        `add`/`extend` enforce this entry by entry, so this is only needed for timelines built
        by passing `entries=` to the constructor, or to re-check one built with
        ``validate=False``. What is expected of which entry is decided by `expected_sign`:
        `NEGATIVE_SIGN_CATEGORIES` are non-positive (note it is *not* `REVENUE_CATEGORIES` —
        band mirroring and sign are two different properties, W3.7), MODERNIZATION_LEVY is
        payer-dependent, everything else is non-negative.

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
        """Appends entries, sign-validating each unless validation is off (W3.7).

        The bulk form calculators use to hand over a whole schedule (replacements, maintenance, a
        loan) at once. It delegates to `add`, so validation and ordering behave identically; a
        violation aborts partway, leaving the earlier entries in place — acceptable because a
        violation is a programming error that fails the run rather than something to recover from.
        """
        for entry in entries:
            self.add(entry)

    def filtered(self, predicate: Callable[[CashFlowEntry], bool]) -> "CashFlowTimeline":
        """New timeline with the entries matching the predicate.

        The generic building block behind every derived view, and the reason perspectives are cheap:
        producing the operating-only or tenant-scope timeline is a list comprehension over entries
        that already exist, not a second evaluation. Entries are shared, not copied — they are
        frozen — and the `validate` regime is inherited so a derived view keeps the same contract.
        """
        return CashFlowTimeline(
            entries=[entry for entry in self.entries if predicate(entry)], validate=self.validate
        )

    def without_categories(self, categories: frozenset) -> "CashFlowTimeline":
        """New timeline without the given categories.

        The category-filter form of `filtered`, used with the `CategoryRules` sets — dropping
        `INVESTMENT_CATEGORIES` for the operating view, dropping subsidy flows for a gross view.
        Stating the exclusion as a set keeps the perspective definitions declarative instead of
        spreading category conditionals through the calculators.
        """
        return self.filtered(lambda entry: entry.category not in categories)

    def scoped_to(self, payer: "Actor") -> "CashFlowTimeline":
        """The flows a perspective scoped to `payer` reports on; SYSTEM means everything (§6).

        The **single** definition of perspective scoping (§7 B4). The aggregation calculator
        derives every scoped KPI through it, `LifecycleCostResult.scoped_timeline` returns it,
        and `explain` filters it — three call sites that used to hold two implementations, which
        is how `explain("total_npv_in_euro")` came to report entries the KPI did not contain.
        """
        if payer == Actor.SYSTEM:
            return self
        return self.filtered(lambda entry: entry.payer == payer)

    def npv(self, interest_rate: float) -> UncertainValue:
        """Slot-wise net present value at the given discount rate.

        The headline aggregate: every entry discounted from its year to year 0 and summed within
        each of the three worlds, yielding a net present *cost* under the package sign convention
        (lower is better; negative means the variant nets money). Multiplied by
        `EconomicParameters.annuity_factor()` it becomes the equivalent annual cost. Because both
        discounting and summation are linear and act slot-wise, the LOW and HIGH results are the
        NPVs of coherent cheap and expensive worlds — not interval bounds computed after the fact.

        Args:
            interest_rate: Nominal discount rate as a fraction; normally
                `EconomicParameters.interest_rate`.

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
        """Slot-wise NPV pivot by an arbitrary key (category, subject, payer).

        Every "NPV by …" figure the engine publishes — by category, by component, by payer, by cost
        group in the reports — is this one function with a different key extractor. That is what
        guarantees the pivots reconcile: each is a partition of exactly the same discounted entries,
        so the buckets sum to `npv(interest_rate)` per slot by construction (§7.4), and no separate
        code path can drift from the headline total.

        Args:
            interest_rate: Nominal discount rate, as for `npv`.
            key: Extractor mapping an entry to its bucket; anything hashable works.

        Returns:
            Bucket -> discounted band, with buckets in first-appearance order.
        """
        result: Dict[Any, UncertainValue] = {}
        for entry in self.entries:
            discounted = entry.amount_in_euro.scale(discount_factor(interest_rate, entry.year))
            bucket = key(entry)
            result[bucket] = result.get(bucket, UncertainValue.exact(0.0)) + discounted
        return result

    def nominal_annual_series(self, horizon_years: int) -> List[UncertainValue]:
        """Nominal euros per year 0..T (index = year); the liquidity view (§4.3).

        The "can I afford this" counterpart of the NPV: undiscounted money in and out of pocket per
        year, from which the year-1 monthly figure is derived (annual / 12 — intra-year timing is
        out of scope). It matters most under financing, which barely moves the NPV but transforms
        the liquidity profile completely. Entries outside 0..T are silently ignored, so a timeline
        carrying flows beyond the horizon can be reported against a shorter one.

        Args:
            horizon_years: T; the returned list has T + 1 elements, index 0 being year 0.

        Returns:
            One band per year, exact zero for years with no entries.
        """
        series = [UncertainValue.exact(0.0) for _ in range(horizon_years + 1)]
        for entry in self.entries:
            if 0 <= entry.year <= horizon_years:
                series[entry.year] = series[entry.year] + entry.amount_in_euro
        return series

    def subjects(self) -> List[str]:
        """All distinct subjects in first-appearance order.

        The row set of every per-component view: breakdowns, the stacked-bar exports and the audit
        tables all iterate this so they cover exactly the subjects the timeline actually contains,
        components and carriers alike. First-appearance rather than sorted order is deliberate —
        it keeps chart series and CSV rows stable across runs and roughly in calculation order.
        """
        seen: Dict[str, None] = {}
        for entry in self.entries:
            seen.setdefault(entry.subject, None)
        return list(seen.keys())
