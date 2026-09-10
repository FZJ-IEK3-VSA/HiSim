"""Derived views on a lifecycle cost result (cost-spec-v2 §2.4, W4.1).

The presentation layer must never compute: every number a report shows has to exist in the
result object or be derived here, once, on the engine side. This module is that "once" — pure
functions of `LifecycleCostResult` returning plain data (dataclasses, dicts, lists of floats and
`UncertainValue`s), with no formatting, no colors and no HTML.

Each function names the presentation site it replaces; the line references are the ones the
spec's W4.1 inventory carries (`roadmap/cost-spec-v2.md` §2.4), i.e. the pre-W4 state of
`reporting.py`/`report_plots.py`.

**What a "view" is here, and where it stops.** A view is a *re-shaping* of one already-evaluated
`LifecycleCostResult` into the shape one report figure needs — a per-year series, a per-carrier
bill, a payer × category pivot, a per-subject waterfall. It never re-runs the engine, never
reaches for `EvaluationInputs`, the cost database or the evaluator, and never applies economic
assumptions of its own: everything it returns is a sum, a filter, a pivot or a discounting of
flows the evaluator already put on the timeline. The boundary against `results.py` is one of
*publication*, not of difficulty: figures the engine publishes about an evaluation (the NPV, the
EAC, `npv_by_category`, the per-subject `ComponentCostBreakdown`s, the comparison arithmetic of
`compare`) are fields and methods there and are read directly; figures that exist only because a
chart or a table asks for them live here, so that no report has to mint them itself and two
reports cannot mint them differently. Consumers are `reporting.py`, `report_plots.py` and
`exports.py` (the latter for `subject_equivalent_annual_cost_by_category` and
`total_subsidies_received`, so a written CSV/KPI and the report showing it share one
definition); the import-lint in `tests/test_economics_import_lint.py` keeps presentation on
this side of the line.

Two conventions worth stating once:

* **Scoping.** Views read `result.scoped_timeline()` — the flows the perspective reports on.
  The one exception is `payer_category_npv_pivot`, which is the view *of* the split and must see
  every payer. (`year_zero_build_up` was a second exception until package S4b; see its
  docstring for why it no longer is.)
* **Discounting.** Every present value goes through `timeline.discount_factor` (W4.3), never
  through a locally written `1/(1+i)**year`.

Display *grouping* (16 cost categories onto ~8 groups) stays a presentation concept, per the
spec's stated exception — but the group **sums** come from here: presentation passes its own
category→group mapping into `fold_categories` / `fold_category_matrix`.
"""

from __future__ import annotations

import enum
from collections.abc import Hashable
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple, TypeVar

from hisim.economics.calculators.financing_application import FinancingConstants
from hisim.economics.calculators.subsidy_application import nominal_support_from_entries
from hisim.economics.carriers import EnergyCarrier, EnergyFlowRole
from hisim.economics.catalog_entries import CostDataError
from hisim.economics.numerics import bisect_root
from hisim.economics.results import (
    LifecycleCostResult,
    ModernizationLevySummary,
    VariantComparison,
    discounted_payback_year,
)
from hisim.economics.subsidies import PayoutKind, SubsidyAward, SubsidySchemeLabels
from hisim.economics.timeline import (
    Actor,
    CashFlowEntry,
    CategoryRules,
    CostCategory,
    SubjectKind,
    discount_factor,
)
from hisim.economics.uncertainty import Slot, UncertainValue


class ViewCategories:
    """Category groupings the view models are built from.

    A namespace of the fixed `CostCategory` tuples the views below select on, kept here rather
    than inline so that "what counts as a bill" and "what counts as year-0 money movement" are
    stated once and can be reviewed as definitions rather than found in a filter expression.
    These are *selection* sets of the engine's own categories, not display groups — the
    presentation-side folding of 16 categories onto 8 coloured groups lives in
    `presentation_style.py` and reaches the sums only through `fold_categories`.
    """

    #: The categories that make up an energy bill; feed-in revenue is deliberately not one of them.
    #: Bound from the kernel so the report and the plausibility panel cannot disagree about what a
    #: bill is (review finding 14) — the same object, not a copy of the membership.
    BILL_CATEGORIES = CategoryRules.BILL_CATEGORIES

    #: The year-0 build-up steps, in the order the investment waterfall shows them (§4.1).
    YEAR_ZERO_CATEGORIES = (
        CostCategory.INVESTMENT,
        CostCategory.PLANNING,
        CostCategory.REMOVAL,
        CostCategory.SUBSIDY,
        CostCategory.LOAN_DISBURSEMENT,
    )


#: Whatever presentation groups categories by — an index, a label, anything hashable. Generic so
#: a caller passing `Mapping[CostCategory, int]` gets a `Dict[int, ...]` back and stays typed.
GroupKey = TypeVar("GroupKey", bound=Hashable)


# ---------------------------------------------------------------------------- category folding

def _display_group_of(category: CostCategory, mapping: Mapping[CostCategory, GroupKey]) -> GroupKey:
    """The display group of one category, or a located error naming the incomplete mapping.

    The single lookup every folding view in this module goes through, so that "the mapping does
    not cover this category" is one sentence rather than three. It raises `CostDataError` rather
    than the bare `KeyError` the fold used to let out: a `KeyError` surfacing from the middle of a
    report reads as an ordinary dictionary accident and is caught by generic handling on the way
    up, while the thing that actually happened is that a category -> group mapping is not total
    over the categories it was handed — a defect in the *presentation's* declaration, which the
    message names together with the groups on offer.

    Args:
        category: The category to place.
        mapping: The caller's category -> group mapping; presentation passes
            `PresentationStyle.CATEGORY_TO_GROUP`, which is deliberately total over the enum.

    Returns:
        The group key `mapping` declares for `category`.

    Raises:
        CostDataError: If `mapping` declares no group for `category`.
    """
    try:
        return mapping[category]
    except KeyError:
        raise CostDataError(
            f"No display group declared for cost category {category.value!r}; the mapping covers "
            f"{sorted(declared.value for declared in mapping)}. A category -> group mapping must "
            "be total over the categories it is asked to fold, so that no amount can land in a "
            "silent default bucket."
        ) from None


def fold_categories(
    values: Mapping[CostCategory, Any], mapping: Mapping[CostCategory, GroupKey]
) -> Dict[GroupKey, Any]:
    """Folds a category→amount map onto arbitrary groups, summing slot-wise.

    Amounts may be floats or `UncertainValue`s (both add). The `mapping` must cover every
    category present in `values`; a gap is a caller bug and raises rather than silently
    bucketing amounts into a default group.

    The presentation passes its own DISPLAY_GROUPS mapping in — grouping is a display concept,
    the sums are not (spec §2.4, stated exception).

    Args:
        values: category → amount, e.g. `result.npv_by_category` or one row of
            `nominal_annual_matrix_by_category`. Amounts are euros; whether they are nominal or
            discounted is whatever the caller passed in — folding does not change it.
        mapping: category → group key. Callers in this package pass
            `PresentationStyle.CATEGORY_TO_GROUP`, which is deliberately total over the enum.

    Returns:
        group key → summed amount, of the same type as the input amounts. Only groups that
        received at least one category appear.

    Raises:
        CostDataError: if `values` contains a category `mapping` does not declare — see
            `_display_group_of`, which is where the message is written.
    """
    folded: Dict[GroupKey, Any] = {}
    for category, amount in values.items():
        group = _display_group_of(category, mapping)
        folded[group] = folded[group] + amount if group in folded else amount
    return folded


def fold_category_matrix(
    matrix: List[Dict[CostCategory, Any]], mapping: Mapping[CostCategory, GroupKey]
) -> List[Dict[GroupKey, Any]]:
    """`fold_categories` applied per year of a year×category matrix.

    The stacked-bar form of the annual cash-flow chart: `nominal_annual_matrix_by_category`
    produces the raw matrix, this folds each year's row onto the display groups the chart has
    colours for. Row order is the year index and is preserved, so `result[year]` still means
    year `year`; an empty row (a year with no flows) folds to an empty dict rather than
    disappearing.
    """
    return [fold_categories(row, mapping) for row in matrix]


# ---------------------------------------------------------------------------- time series

def cumulative_discounted_cost_series(result: LifecycleCostResult) -> Dict[Slot, List[float]]:
    """Cumulative discounted cost per slot over years 0..T (index = year).

    The last point of each slot's series is that slot's reported NPV, which is what makes the
    chart's end label and the headline KPI agree by construction. Flows outside the horizon are
    not part of the reported NPV and are excluded here too.

    Replaces `reporting.py:686-701` (the cumulative-NPV chart's own discounting loop).
    """
    horizon = result.parameters.observation_period_in_years
    interest = result.parameters.interest_rate
    per_year: Dict[Slot, List[float]] = {slot: [0.0] * (horizon + 1) for slot in Slot}
    for entry in result.scoped_timeline().entries:
        if 0 <= entry.year <= horizon:
            factor = discount_factor(interest, entry.year)
            for slot in Slot:
                per_year[slot][entry.year] += entry.amount_in_euro.slot(slot) * factor
    series: Dict[Slot, List[float]] = {}
    for slot, values in per_year.items():
        running = 0.0
        curve: List[float] = []
        for value in values:
            running += value
            curve.append(running)
        series[slot] = curve
    return series


def nominal_annual_matrix_by_category(
    result: LifecycleCostResult, slot: Slot = Slot.BEST_ESTIMATE
) -> List[Dict[CostCategory, float]]:
    """Nominal euros per (year, category) for years 0..T — index = year.

    The un-grouped form of the annual cash-flow chart's input; presentation folds it onto its
    display groups with `fold_category_matrix`.

    Nominal means undiscounted: this is the liquidity view ("what leaves the account in year
    N"), the counterpart of `cumulative_discounted_cost_series` above it in the same report
    section. Package sign convention applies unchanged — costs positive, revenue/support
    negative — which is what puts credits below the axis in the chart. Only one slot is
    returned because a stacked bar cannot show a band; `slot` selects which of the three
    coherent worlds is drawn, and every caller in this package draws BEST_ESTIMATE.

    Replaces `reporting.py:640-646` and `report_plots.py:48-51`, which both accumulated
    per-group year totals while drawing.
    """
    horizon = result.parameters.observation_period_in_years
    matrix: List[Dict[CostCategory, float]] = [{} for _ in range(horizon + 1)]
    for entry in result.scoped_timeline().entries:
        if 0 <= entry.year <= horizon:
            row = matrix[entry.year]
            row[entry.category] = row.get(entry.category, 0.0) + entry.amount_in_euro.slot(slot)
    return matrix


@dataclass(frozen=True)
class LoanAmortization:
    """Interest, principal and outstanding balance per year 0..T (index = year), nominal (§4.4).

    The two components of a financed perspective's debt service, split so the report can stack
    them and a reviewer can see the shape an annuity loan has (falling interest, rising
    principal) or fails to have. All lists always span the full horizon, zero-padded, so they
    index by year directly and can be zipped with each other and with any other year series in
    this module. The disbursement itself is *not* one of the bar series — it is a year-0 flow of
    the `LOAN_DISBURSEMENT` category and shows up in the investment waterfall instead — but it
    is carried here as `disbursement_in_euro` because the balance line starts from it.

    `outstanding_balance_in_euro` is the disbursement minus the cumulative principal repayments
    up to and including that year, so it reconciles with the plotted bars by construction rather
    than by a second schedule computation. Year 0 therefore carries the disbursement *less any
    principal booked at year 0* — normally the full disbursement, since an annuity plan repays
    nothing in the year it is taken out, but a plan that books a year-0 repayment starts below it
    rather than above the sum of its own repayments. A fully amortizing plan ends at zero within
    float tolerance.
    """

    interest_in_euro: List[float]
    principal_in_euro: List[float]
    #: Disbursement at year 0 as a positive amount (the timeline books it negative).
    disbursement_in_euro: float = 0.0
    #: Debt still outstanding at the end of each year 0..T; index = year.
    outstanding_balance_in_euro: List[float] = field(default_factory=list)

    def has_flows(self) -> bool:
        """True when the perspective is financed at all."""
        return any(self.interest_in_euro) or any(self.principal_in_euro)

    def loan_free_year(self) -> Optional[int]:
        """First year the outstanding balance reaches zero, or None while debt remains.

        The "loan-free" milestone a lifecycle-milestone lane draws. It reads the balance series
        rather than the plan's term so that a truncated schedule (a term reaching past the
        observation horizon) honestly reports None instead of a maturity the timeline never books.
        The tolerance is `ViewTolerances.BALANCE_EPSILON`, which is why this method is defined
        here but documented with the chart views at the end of the module. An unfinanced
        perspective has no milestone rather than one in year 1: its balance series is all zeros,
        and reading a "loan-free" year off a loan that never existed would be a caption stating
        something that did not happen.
        """
        if not self.has_flows():
            return None
        for year, balance in enumerate(self.outstanding_balance_in_euro):
            if year > 0 and balance <= ViewTolerances.BALANCE_EPSILON:
                return year
        return None


def loan_amortization_series(
    result: LifecycleCostResult, slot: Slot = Slot.BEST_ESTIMATE
) -> LoanAmortization:
    """The loan's interest/principal split per year (§4.4).

    Reads the `LOAN_INTEREST` and `LOAN_PRINCIPAL` entries off the scoped timeline — it does not
    re-run the schedule builder in `financing.py`, so what the chart shows is by construction
    the debt service the NPV was computed from. Callers use `LoanAmortization.has_flows()` to
    decide whether the perspective is financed at all and skip the chart when it is not, which
    is the common case (cash purchase).

    The outstanding-balance series is built here from the same entries (disbursement minus the
    running principal repayments), which is what keeps a balance line and the bars drawn beside it
    from disagreeing.

    Replaces `reporting.py:517-526` (the amortization chart's accumulation loop).
    """
    horizon = result.parameters.observation_period_in_years
    interest_per_year = [0.0] * (horizon + 1)
    principal_per_year = [0.0] * (horizon + 1)
    disbursement = 0.0
    for entry in result.scoped_timeline().entries:
        if not 0 <= entry.year <= horizon:
            continue
        if entry.category == CostCategory.LOAN_INTEREST:
            interest_per_year[entry.year] += entry.amount_in_euro.slot(slot)
        elif entry.category == CostCategory.LOAN_PRINCIPAL:
            principal_per_year[entry.year] += entry.amount_in_euro.slot(slot)
        elif entry.category == CostCategory.LOAN_DISBURSEMENT:
            disbursement += -entry.amount_in_euro.slot(slot)
    balance: List[float] = []
    outstanding = disbursement
    for year in range(horizon + 1):
        outstanding -= principal_per_year[year]
        balance.append(outstanding)
    return LoanAmortization(
        interest_in_euro=interest_per_year,
        principal_in_euro=principal_per_year,
        disbursement_in_euro=disbursement,
        outstanding_balance_in_euro=balance,
    )


def cumulative_operational_co2_in_kg(result: LifecycleCostResult) -> List[float]:
    """Running total of operational CO2 over the horizon (index = year), undiscounted (§3.8).

    A plain cumulative sum of `LifecycleCo2Result.operational_co2_by_year_in_kg`, drawn as the
    curve under the CO2 bars in report section 4b. Emissions are masses, not money: they are
    never discounted (a kilogram in year 20 counts exactly like one in year 1), which is why
    this has no discount factor while its monetary sibling
    `cumulative_discounted_cost_series` does. The last element is the lifecycle operational
    total and is the figure the section's table prints.

    Replaces `reporting.py:584-588`.
    """
    cumulative: List[float] = []
    running = 0.0
    for value in result.lifecycle_co2_result.operational_co2_by_year_in_kg:
        running += value
        cumulative.append(running)
    return cumulative


# ---------------------------------------------------------------------------- detail table

@dataclass(frozen=True)
class TimelineDetailRow:
    """One (year, subject, category) cell of the cash-flow detail table.

    The finest grain the report ever shows: all timeline entries that share a year, a subject
    and a category, added up. It carries both readings of the same money on purpose — the
    nominal band as it was booked, and the BEST_ESTIMATE slot after discounting — because the two
    side by side are what lets a reviewer verify the discount factor of a given year by
    division, without leaving the table.
    """

    year: int
    subject: str
    category: CostCategory
    nominal_in_euro: UncertainValue  # as booked, undiscounted, full min/best_estimate/max band
    discounted_best_estimate_in_euro: float  # BEST_ESTIMATE slot × discount_factor(interest, year)


@dataclass(frozen=True)
class TimelineDetailYear:
    """One year of the detail table: its rows plus the subtotal printed under them.

    Groups the rows of a single year so the renderer can print a bold subtotal line without
    re-adding anything. The totals cover exactly the rows in `rows` — including the noise cells
    dropped by `ViewTolerances.DETAIL_ROW_EPSILON` being absent from both — so the table always
    adds up on screen.
    """

    year: int
    rows: List[TimelineDetailRow]
    nominal_total_in_euro: UncertainValue  # sum of the rows' nominal bands, slot-wise
    discounted_total_best_estimate_in_euro: float  # that sum's BEST_ESTIMATE slot, discounted to year 0


def timeline_detail_rows(result: LifecycleCostResult) -> List[TimelineDetailYear]:
    """The §3.6 timeline as a verification table: (year, subject, category) with subtotals.

    Same scoping as the chart it sits under; duplicate cells are aggregated, and cells that are
    zero in every slot up to `ViewTolerances.DETAIL_ROW_EPSILON` are dropped as float noise. Rows within a year
    are ordered by nominal BEST_ESTIMATE amount, so the biggest credit and the biggest cost of a year
    frame its block. The subtotals cover exactly the rows shown.

    Replaces `reporting.py:1086-1120`.
    """
    interest = result.parameters.interest_rate
    aggregated: Dict[Tuple[int, str, CostCategory], UncertainValue] = {}
    for entry in result.scoped_timeline().entries:
        key = (entry.year, entry.subject, entry.category)
        aggregated[key] = aggregated.get(key, UncertainValue.exact(0.0)) + entry.amount_in_euro
    years: List[TimelineDetailYear] = []
    current: Optional[int] = None
    rows: List[TimelineDetailRow] = []
    total = UncertainValue.exact(0.0)

    def flush() -> None:
        if current is not None:
            years.append(
                TimelineDetailYear(
                    year=current,
                    rows=rows,
                    nominal_total_in_euro=total,
                    discounted_total_best_estimate_in_euro=total.best_estimate * discount_factor(interest, current),
                )
            )

    for (year, subject, category), amount in sorted(
        aggregated.items(), key=lambda item: (item[0][0], item[1].best_estimate)
    ):
        if all(
            abs(value) < ViewTolerances.DETAIL_ROW_EPSILON
            for value in (amount.best_estimate, amount.minimum, amount.maximum)
        ):
            continue
        if year != current:
            flush()
            current, rows, total = year, [], UncertainValue.exact(0.0)
        total = total + amount
        rows.append(
            TimelineDetailRow(
                year=year,
                subject=subject,
                category=category,
                nominal_in_euro=amount,
                discounted_best_estimate_in_euro=amount.best_estimate * discount_factor(interest, year),
            )
        )
    flush()
    return years


# ---------------------------------------------------------------------------- pivots

def payer_category_npv_pivot(
    result: LifecycleCostResult,
) -> Dict[Actor, Dict[CostCategory, UncertainValue]]:
    """Who pays which cost block, in present value (§6.5).

    Taken on the **full** timeline (all payers must be visible for the zero-sum check) and
    without the unallocated SYSTEM payer. Presentation folds the inner map onto display groups.

    Replaces `reporting.py:1384-1396`.
    """
    interest = result.parameters.interest_rate
    pivot: Dict[Actor, Dict[CostCategory, UncertainValue]] = {}
    for entry in result.timeline.entries:
        if entry.payer == Actor.SYSTEM:
            continue
        bucket = pivot.setdefault(entry.payer, {})
        discounted = entry.amount_in_euro.scale(discount_factor(interest, entry.year))
        bucket[entry.category] = (
            bucket[entry.category] + discounted if entry.category in bucket else discounted
        )
    return pivot


def equivalent_annual_cost_of(npv: UncertainValue, result: LifecycleCostResult) -> UncertainValue:
    """One NPV annuitized over the observation period (§3.4) — the EAC of anything.

    Multiplies a present value in euro by the perspective's own annuity factor, giving the
    constant euro-per-year payment with the same present value over the horizon. It takes the
    factor from `result.parameters` rather than from an argument so that every annuitized figure
    in a report uses the same interest rate and the same horizon as the headline KPI beside it —
    the reason this exists as a shared helper instead of a multiplication at each call site.
    """
    return npv.scale(result.parameters.annuity_factor())


def equivalent_annual_cost_by_category(
    result: LifecycleCostResult,
) -> Dict[CostCategory, UncertainValue]:
    """Per-category equivalent annual cost of the whole perspective (§3.4).

    `npv_by_category` restated in euro per year, which is the unit most readers compare against
    a bill or a rent. Because annuitizing is a single multiplication, the categories still sum
    to the perspective's headline EAC exactly as their NPVs sum to its NPV.
    """
    return {
        category: equivalent_annual_cost_of(npv, result)
        for category, npv in result.npv_by_category.items()
    }


def subject_equivalent_annual_cost_by_category(
    result: LifecycleCostResult,
) -> Dict[str, Dict[CostCategory, UncertainValue]]:
    """Per-subject, per-category equivalent annual cost — the `component_costs.csv` figure.

    The two-level pivot behind the stacked-bar frontends: for every component subject, what its
    cost blocks are worth per year. It is the only view whose primary consumer is an export
    rather than a report, which is precisely why it lives here — the CSV and any report showing
    the same figure now read one definition.

    Replaces the annuity multiplication minted while writing the CSV (`exports.py:68-76`).
    """
    return {
        subject: {
            category: equivalent_annual_cost_of(npv, result)
            for category, npv in breakdown.npv_by_category.items()
        }
        for subject, breakdown in result.component_breakdowns.items()
    }


# ---------------------------------------------------------------------------- energy bills

@dataclass(frozen=True)
class CarrierYearOneBill:
    """One carrier's year-1 bill, its annual volume and the price those two imply.

    The record behind the report's most useful sanity check: dividing what a carrier cost in
    year 1 by how much of it was bought must give back a price a reader recognizes (roughly
    0.3 EUR/kWh for German household electricity, ~0.10 for gas). A factor of 1000, or a price
    of zero, means a unit mix-up or a missing tariff somewhere upstream, and it shows here
    before it shows anywhere else. That check only works because the denominator is kWh for
    *every* carrier, pellets and heating oil included — the per-ton and per-liter quotes of the
    data files are divided out when the price entry is resolved (D26), so a reader comparing two
    carriers is comparing two numbers of the same kind. Year 1 rather than year 0 because
    operating flows are booked over years 1..T while year 0 is the investment year; the figures
    are nominal euros of the BEST_ESTIMATE slot, except `year_one_band_in_euro`, which keeps the full
    band.
    """

    carrier: str
    #: Year-1 amounts per category, BEST_ESTIMATE slot; includes the feed-in credit where it belongs
    #: to this carrier (see `carrier_year_one_bills`).
    by_category_in_euro: Dict[CostCategory, float]
    #: Sum of `by_category_in_euro` **without** feed-in revenue — what the energy actually cost.
    total_excluding_feed_in_in_euro: float
    #: Annualized bought volume in kilowatt-hours, for every carrier alike (D26).
    annual_quantity_in_kwh: float
    #: `total_excluding_feed_in_in_euro / annual_quantity_in_kwh`, 0.0 for an unbilled carrier.
    effective_price_in_euro_per_kwh: float
    #: The carrier's own year-1 flows as a band (feed-in excluded — a different subject).
    year_one_band_in_euro: UncertainValue


def carrier_year_one_bills(result: LifecycleCostResult) -> Dict[str, CarrierYearOneBill]:
    """Year-1 bill decomposition per carrier with the implied effective price (§8).

    The feed-in subject is folded into the electricity bill's category map — that is where the
    report has always shown it — but never into `total_excluding_feed_in_in_euro`, the numerator
    of the price, nor into the band: a credit is not part of what a kWh costs.

    Replaces `reporting.py:1166-1192`.
    """
    bills: Dict[str, CarrierYearOneBill] = {}
    entries = [entry for entry in result.scoped_timeline().entries if entry.year == 1]
    for carrier, quantities in result.annual_energy_quantities_by_carrier.items():
        subjects = {carrier}
        if carrier == EnergyCarrier.ELECTRICITY.value:
            subjects.add(EnergyCarrier.ELECTRICITY_FEED_IN.value)
        by_category: Dict[CostCategory, float] = {}
        for entry in entries:
            if entry.subject in subjects:
                by_category[entry.category] = (
                    by_category.get(entry.category, 0.0) + entry.amount_in_euro.best_estimate
                )
        total = sum(
            value for category, value in by_category.items() if category != CostCategory.FEED_IN_REVENUE
        )
        quantity = quantities.bought_in_kwh
        band = UncertainValue.sum(
            entry.amount_in_euro for entry in entries if entry.subject == carrier
        )
        bills[carrier] = CarrierYearOneBill(
            carrier=carrier,
            by_category_in_euro=by_category,
            total_excluding_feed_in_in_euro=total,
            annual_quantity_in_kwh=quantity,
            effective_price_in_euro_per_kwh=total / quantity if quantity else 0.0,
            year_one_band_in_euro=band,
        )
    return bills


# ---------------------------------------------------------------------------- year 0 / subsidies

@dataclass(frozen=True)
class YearZeroBuildUp:
    """One subject's year-0 money movement: the waterfall steps and where they end up.

    Feeds the investment waterfall of report section 2, whose whole job is to make the chain
    "device + installation + planning + removal - subsidies - loan disbursement = net outflow"
    visible per component, so a binding subsidy cap or a component that was never priced can be
    spotted at a glance. Only the five `ViewCategories.YEAR_ZERO_CATEGORIES` are carried, in
    that order, and only where the amount is non-zero; sign follows the package convention, so
    subsidies and the loan disbursement appear as negative steps that pull the net down.
    """

    subject: str
    #: BEST_ESTIMATE-slot year-0 amounts, keyed by category, only the categories that are non-zero.
    by_category_in_euro: Dict[CostCategory, float] = field(default_factory=dict)

    @property
    def net_outflow_in_euro(self) -> float:
        """What actually leaves the payer's account in year 0 (subsidies and loans reduce it)."""
        return sum(self.by_category_in_euro.values())


def year_zero_build_up(result: LifecycleCostResult) -> Dict[str, YearZeroBuildUp]:
    """Per-subject year-0 build-up: device + planning + removal - subsidies - loan = net.

    Reads the **scoped** timeline, like every other view. W4.1 inherited the full timeline from
    the section this feeds (`reporting.py:1041-1054`) on the argument that year 0 is about what
    the measure costs, before the question of who carries it, and left the question open. It is
    settled the other way (package S4b): the same section's table is built from
    `component_breakdowns`, which the engine derives from the scoped timeline, so under an
    actor scope the waterfall and the table beneath it disagreed — a tenant perspective drew the
    landlord's full investment above an empty table. Measured on the S4b golden fixture, the two
    readings differ for exactly that case (a tenant scope now has no year-0 build-up at all, as
    it has no year-0 flows) and are identical for every other perspective, landlord included.

    Subjects with no year-0 flow are absent.
    """
    build_ups: Dict[str, YearZeroBuildUp] = {}
    scoped = result.scoped_timeline()
    for subject in result.component_breakdowns:
        year_zero = [
            entry for entry in scoped.entries if entry.year == 0 and entry.subject == subject
        ]
        if not year_zero:
            continue
        by_category: Dict[CostCategory, float] = {}
        for category in ViewCategories.YEAR_ZERO_CATEGORIES:
            value = sum(
                entry.amount_in_euro.best_estimate for entry in year_zero if entry.category == category
            )
            if value:
                by_category[category] = value
        build_ups[subject] = YearZeroBuildUp(subject=subject, by_category_in_euro=by_category)
    return build_ups


@dataclass(frozen=True)
class SubsidyShare:
    """How far the support carries one subject's gross investment (BEST_ESTIMATE slot, nominal).

    A "X % of this measure is funded" statement, used by the subsidy composition bars in the
    HTML report and by the matplotlib investment waterfall. Both parts are nominal (undiscounted)
    euros of the BEST_ESTIMATE slot, because the question it answers is about the money on the invoice
    rather than about present value. `subsidy_in_euro` is clamped to the gross so
    `share_of_gross` is always a fraction in [0, 1] — see `subsidy_share_of_gross` for why that
    clamp is a deliberate business rule and where the unclamped figure lives.
    """

    subject: str
    gross_in_euro: float
    #: Support **clamped to the gross** — see `subsidy_share_of_gross` for why.
    subsidy_in_euro: float

    @property
    def net_in_euro(self) -> float:
        """Gross minus the clamped support."""
        return self.gross_in_euro - self.subsidy_in_euro

    @property
    def share_of_gross(self) -> float:
        """Funded fraction in [0, 1]."""
        return self.subsidy_in_euro / self.gross_in_euro if self.gross_in_euro else 0.0


def subsidy_share_of_gross(result: LifecycleCostResult) -> Dict[str, SubsidyShare]:
    """Per-subject funded share of the year-0 gross investment (nominal euros, §5.4).

    **The clamp is a business rule, and this is its only home.** Nominal support can exceed the
    year-0 gross of a subject — a scheme paid out over several years, or support attached to a
    measure whose investment sits partly in later years — and a "share of gross" above 100 % is
    not a meaningful statement, so the reported support is `min(subsidy, gross)`. Until W4.1
    this clamp lived, undocumented, in two chart helpers (`reporting.py:1227` and
    `report_plots.py:93-96`), where nothing kept the two copies honest. Consumers that need the
    unclamped figure read `ComponentCostBreakdown.subsidies_nominal_in_euro`.

    Subjects without a positive gross investment are absent.
    """
    shares: Dict[str, SubsidyShare] = {}
    for subject, breakdown in result.component_breakdowns.items():
        gross = breakdown.investment_gross_in_euro.best_estimate
        if gross <= 0:
            continue
        subsidy = breakdown.subsidies_nominal_in_euro.best_estimate
        shares[subject] = SubsidyShare(
            subject=subject, gross_in_euro=gross, subsidy_in_euro=min(subsidy, gross)
        )
    return shares


def investment_net_of_subsidies(result: LifecycleCostResult) -> Dict[str, UncertainValue]:
    """Per-subject year-0 gross investment minus nominal support, as a **band**.

    The "Net" column of the investment table (`reporting.py:976`), which is a different figure
    from `SubsidyShare.net_in_euro`: that one is the BEST_ESTIMATE slot with the share clamp applied
    (a "how far does the support carry" statement), this one is the plain slot-wise difference
    of two reported bands and may go negative when support exceeds the year-0 gross. Subjects
    with no positive gross investment are absent — the table skips them.
    """
    return {
        subject: breakdown.investment_gross_in_euro - breakdown.subsidies_nominal_in_euro
        for subject, breakdown in result.component_breakdowns.items()
        if breakdown.investment_gross_in_euro.maximum > 0
    }


def payer_npv_total(result: LifecycleCostResult) -> UncertainValue:
    """Sum of all payer NPVs — the system total the §6.5 zero-sum check reconciles against.

    Includes the unallocated SYSTEM payer, so it is the whole allocated timeline's present
    value however the ruleset split it. Report section 6b prints it in the header above the
    payer whiskers, which is the visual form of the invariant: an allocation ruleset moves money
    between actors and may not create or destroy any, so the individual payer bars below it have
    to add up to this one number. Replaces `reporting.py:1306`.
    """
    return UncertainValue.sum(result.npv_by_payer.values())


def scheme_display_names(result: LifecycleCostResult) -> Dict[str, str]:
    """Every support id this result can show, mapped to the name a reader sees (Q20).

    Built from the awards of the result's own subsidy decisions, because that is where the
    catalog's `display_name` was captured at evaluation time — a report is regularly rendered in
    a process that never loaded a catalog, so re-reading the data files here would be both a
    seam-4 violation and unreliable. Ids with no award (the legacy shim, an unattributed support
    entry) get their labels from `SubsidySchemeLabels`, and an id the mapping does not know maps
    to itself, so nothing ever renders as an empty cell.

    It has no production caller in this slice of the cost stack: its consumer is the subsidy
    Sankey view of a later slice, which labels the nodes of a support flow diagram and is the
    reason the mapping has to cover ids that never were an award. Kept here rather than deferred
    with it, because the definition belongs beside the awards it is built from.

    Args:
        result: The evaluated perspective whose timeline and decisions are about to be rendered.

    Returns:
        `{scheme id: display name}`, always including the legacy-shim id and the unattributed
        key (the empty string), so callers can look up straight from a timeline entry's
        `subsidy_scheme_id or ""`.
    """
    names = {
        "": SubsidySchemeLabels.UNATTRIBUTED,
        SubsidySchemeLabels.LEGACY_FLAT_ID: SubsidySchemeLabels.LEGACY_FLAT,
    }
    for decision in result.subsidy_decisions:
        for award in decision.applied:
            names[award.scheme_id] = award.label
        for item in list(decision.rejected) + list(decision.undetermined):
            scheme_id = item.get("scheme_id")
            if scheme_id:
                names.setdefault(scheme_id, item.get("display_name") or scheme_id)
    return names


def award_total_amount(award: SubsidyAward) -> UncertainValue:
    """The amount an award is worth in total, nominal (§5.4).

    A scheduled payout (a tax credit spread over N years) is worth the sum of its instalments,
    not its — zero — upfront amount; every other kind is worth its upfront amount. The awards
    table has always shown it this way; stating the rule here keeps it from drifting away from
    the KPI beside it. `describe_award` is what renderers call — this is its euro half, kept
    separate because the KPI export and the chart data want the band without the prose.

    The two halves are *added* rather than chosen between, so an award that one day carries both
    a year-0 payment and a schedule is worth both. Today no solver branch produces such an award
    — a `TaxCreditBenefit` leaves `upfront_amount` at zero and every other euro-valued benefit
    leaves `schedule_amounts` empty — so the sum equals the old either/or for every award that
    exists, and stays correct if that ever changes.

    Args:
        award: One entry of `SubsidyDecision.applied`.

    Returns:
        The nominal, undiscounted euro band the award pays in total.
    """
    return UncertainValue.sum([award.upfront_amount, *award.schedule_amounts])


@dataclass
class AwardPresentation:
    """One applied award reduced to what a reader has to be told about it (§5.4).

    The renderers of the subsidy section — the markdown summary's decision list, the HTML decision
    cards and the awards table — used to each read `SubsidyAward` fields directly, and all three
    read `upfront_amount`. That is zero for three of the five payout kinds, so a §35c tax credit
    worth 2,060 EUR was printed as "0.00 EUR" on the card and dropped entirely from the markdown
    list (which filtered on a non-zero upfront amount), while the SUBSIDY category NPV beside it
    counted the money. This record is the single place where "what is this award worth, and how
    does it arrive" is decided, so the three renderings cannot disagree again.

    `total_in_euro` is None exactly when the award carries no euro amount at all — loan terms,
    an operational per-kWh rate, a reduced VAT rate — because their value depends on the
    financing plan or the energy flows and is booked by another calculator. Those awards are
    still *applied* and must still be listed, which is what `payout_note` is for: it names the
    terms instead of a euro band.

    A loan award's **repayment grant** is the one figure deliberately withheld even though euros
    for it exist: the solver values the forgiven share in its objective (`solver._support_value`,
    which is how a soft loan can win a combination at all), and `calculators/financing_application`
    later books it onto the timeline as a SUBSIDY entry. Those two are not the same number — §7 B3:
    the solver applies the share to the measure's gross cost, the calculator applies it to the loan
    *principal*, which the financing plan decides — so neither is a euro figure this award line can
    stand behind, and it states the share through `payout_note` instead. Resolving that
    disagreement is the trigger for showing the euros: once the award's own valuation is the amount
    the plan actually pays, the presentation can read it rather than pick one of two answers.
    """

    scheme_id: str
    payout_kind: str
    total_in_euro: Optional[UncertainValue]
    payout_note: str
    caps_binding: Tuple[str, ...]
    #: The friendly name a reader sees (Q20); equal to `scheme_id` when the catalog had none.
    display_name: str = ""
    #: The multiplication that produced `total_in_euro`, as `rate x basis = amount` (Q26 F8), or
    #: the empty string for an award whose form states no rate — a lump sum, a per-unit amount,
    #: loan terms, a VAT reduction — where `payout_note` already carries the form's own terms.
    arithmetic: str = ""
    #: What the eligible-cost ceiling did: "cap not binding", "capped at X EUR" or the empty
    #: string where the scheme declares no cap at all. Read from the solver's recorded decision
    #: data, never re-derived from the amount.
    cap_verdict: str = ""


def describe_award(award: SubsidyAward) -> AwardPresentation:
    """What an applied award is worth and how it is paid out, per payout kind (§5.2, §5.4).

    The mapping from the flat `SubsidyAward` union onto the fields a renderer needs. An upfront
    grant is worth its year-0 amount and needs no note; a tax credit is worth the sum of its
    instalments and says over how many years they arrive; loan terms, operational support and a
    VAT reduction have no euro amount of their own and are described by their terms — the
    interest rate and term they impose on the financing plan, the per-kWh rate and duration, the
    reduced rate — so that the reader sees an applied award rather than a silent gap.

    Args:
        award: One entry of `SubsidyDecision.applied`.

    Returns:
        The renderable form; `total_in_euro` is None only for the kinds that carry no euro amount.
    """
    total = award_total_amount(award)
    caps = tuple(slot for slot, bound in award.caps_binding_per_slot.items() if bound)
    note = ""
    if award.payout_kind == PayoutKind.TAX_CREDIT_SCHEDULE:
        note = f"tax credit paid over {len(award.schedule_amounts)} years"
    elif award.payout_kind == PayoutKind.OPERATIONAL:
        carrier = award.operational_carrier.value if award.operational_carrier is not None else "energy"
        note = (
            f"{award.operational_rate_per_kwh:.4f} EUR/kWh on {carrier} for "
            f"{award.operational_duration_years} years"
        )
    elif award.payout_kind == PayoutKind.LOAN_TERMS:
        terms = []
        if award.loan_interest_rate is not None:
            terms.append(f"{award.loan_interest_rate:.2%} interest")
        if award.loan_term_in_years is not None:
            terms.append(f"{award.loan_term_in_years} years term")
        if award.loan_repayment_grant_share is not None:
            terms.append(f"{award.loan_repayment_grant_share:.0%} repayment grant")
        note = "loan terms: " + (", ".join(terms) if terms else "inherited from the financing plan")
    elif award.payout_kind == PayoutKind.VAT_REDUCTION:
        rate = award.reduced_vat_rate
        note = f"reduced VAT rate {rate:.1%}" if rate is not None else "reduced VAT rate"
    quantified = award.payout_kind not in (
        PayoutKind.LOAN_TERMS, PayoutKind.OPERATIONAL, PayoutKind.VAT_REDUCTION
    ) or bool(total.maximum)
    return AwardPresentation(
        scheme_id=award.scheme_id,
        display_name=award.label,
        payout_kind=award.payout_kind.value,
        total_in_euro=total if quantified else None,
        payout_note=note,
        caps_binding=caps,
        arithmetic=award_arithmetic(award, total),
        cap_verdict=award_cap_verdict(award, caps),
    )


def award_arithmetic(award: SubsidyAward, total: UncertainValue) -> str:
    """`rate x eligible basis = amount` for the two percentage forms, else "" (Q26 F8).

    An award line that states only its euro amount cannot be checked: the reader cannot tell a
    9 % rate on a small basis from a 20 % rate that a ceiling cut back, and those are different
    conclusions about what a second measure would earn. The solver records both factors on the
    award, so the multiplication is a formatting of stored data rather than a re-derivation —
    which is what keeps it inside the seam-4 rule.

    The lump-sum, per-unit, loan-terms and VAT forms return the empty string on purpose: they
    have no rate, and `describe_award`'s `payout_note` already states their own terms.

    Two ceilings can each have cut the rate down, and both are named where they applied: a
    cumulation group's combined-rate cap and the EU state-aid overall cap. They compose — a rate
    that first lost the group's stack and then the state-aid ceiling reads as "17.5 % (of 20.0 %,
    …combined-rate cap) (of 17.5 %, …state-aid overall cap)" — because a reader who sees only the
    final rate cannot tell which limit is the binding one, and those imply different answers about
    what a second measure would earn.

    Args:
        award: The applied award, read for its rate, its eligible basis and the two pre-cap rates.
        total: The award's value as `award_total_amount` computed it, for the product.

    Returns:
        A short arithmetic string with the best-estimate slot of both factors, or "".
    """
    if award.benefit_rate is None or award.eligible_basis_in_euro is None:
        return ""
    rate_text = f"{award.benefit_rate:.1%}"
    if award.benefit_rate_before_group_cap is not None:
        rate_text = (
            f"{rate_text} (of {award.benefit_rate_before_group_cap:.1%}, cut back by the "
            "cumulation group's combined-rate cap)"
        )
    if award.benefit_rate_before_overall_cap is not None:
        rate_text = (
            f"{rate_text} (of {award.benefit_rate_before_overall_cap:.1%}, cut back by the "
            "state-aid overall cap)"
        )
    return (
        f"{rate_text} x {award.eligible_basis_in_euro.best_estimate:,.0f} EUR eligible basis = "
        f"{total.best_estimate:,.0f} EUR"
    )


def award_cap_verdict(award: SubsidyAward, binding: Optional[Tuple[str, ...]] = None) -> str:
    """What the eligible-cost ceiling did to this award, in the solver's own terms (Q26 F8).

    The second half of an award line a reader cannot otherwise reconstruct: below the ceiling the
    support scales with what was spent, at the ceiling it does not, and the same measure costing
    more would earn exactly the same euros. The solver records the ceiling and the per-slot
    binding flags; this states them.

    Args:
        award: The applied award.
        binding: The slots whose cap bound, when the caller already has them — `describe_award`
            computes exactly this tuple for `AwardPresentation.caps_binding`, so passing it
            through keeps the list from being derived twice from the same field. Omitted, it is
            read off the award.

    Returns:
        "capped at X EUR eligible cost (slot, ...)" naming the slots whose cap bound,
        "cap not binding (X EUR eligible cost)", or "" when the scheme declares no cap at all.
    """
    if award.eligible_basis_cap_in_euro is None:
        return ""
    slots = (
        binding
        if binding is not None
        else tuple(slot for slot, bound in award.caps_binding_per_slot.items() if bound)
    )
    if slots:
        return (
            f"capped at {award.eligible_basis_cap_in_euro:,.0f} EUR eligible cost "
            f"({', '.join(slots)})"
        )
    return f"cap not binding ({award.eligible_basis_cap_in_euro:,.0f} EUR eligible cost)"


def total_subsidies_received(result: LifecycleCostResult) -> Optional[UncertainValue]:
    """Nominal support carried by the SUBSIDY entries of the **scoped** timeline, or None.

    Owner decision D2 (cost-spec-v2 §8) unified this KPI with the W3.4 levy basis: both are
    `nominal_support_from_entries` over timeline SUBSIDY entries — nominal, undiscounted euros,
    complete by construction. It used to sum the solver's award amounts
    (`SubsidyDecision.applied[*].upfront_amount`) instead, which silently omitted every euro of
    support that reaches the timeline without a catalog award or without being upfront: the
    §10.1 legacy flat shim, operational support, and the instalments of a scheduled payout or a
    repayment grant. The per-subject counterpart is
    `ComponentCostBreakdown.subsidies_nominal_in_euro`; the per-award figure is
    `award_total_amount`.

    Scoping follows the module convention: the figure covers the flows the perspective reports
    on. For a SYSTEM-scope perspective that is *every* SUBSIDY entry of the run; for an actor
    scope it is the support that actor receives, so a tenant perspective reports the support
    allocated to the tenant and nothing of the landlord's.

    Returns None when the scoped timeline carries no SUBSIDY entry at all, so callers can omit
    the KPI entirely rather than publish a zero (the historical behaviour of the export).
    """
    entries = [
        entry for entry in result.scoped_timeline().entries if entry.category == CostCategory.SUBSIDY
    ]
    if not entries:
        return None
    return nominal_support_from_entries(entries)


# ----- 9/9 views, V1-V7 -----

class ViewTolerances:
    """The one namespace of numeric tolerances the views apply (visualization spec §5).

    Two kinds of number live here, and there is exactly one place for both. Most of the views
    added for the V1-V15 chart set do not merely re-shape the result, they *check* themselves: a
    Sankey whose transfer ribbons do not net to zero, a tornado whose bars do not sum to the band
    or a sources-and-uses statement that does not balance is a lie about the engine, so those
    views raise `CostDataError` instead of drawing, and every such comparison needs a tolerance.
    The other kind is `DETAIL_ROW_EPSILON`, the one point at which a view is allowed to *drop*
    data — and only ever to suppress float noise, values that are arithmetically zero but land at
    1e-13 after a chain of discounting and slot arithmetic.

    Collecting both here is the point: a reviewer sees in one place how much disagreement counts
    as float noise (half a cent on a euro figure) rather than as a defect, and how much may be
    hidden from a table. There used to be a second namespace, `ViewThresholds`, holding the
    dropping tolerance and claiming the same "one place" for itself; folding it in leaves one.
    """

    #: Absolute euro tolerance for the reconciliation checks (half a cent).
    RECONCILIATION_EPSILON = 0.005
    #: Amounts below this (in every slot) are dropped from the detail table as float noise; the
    #: same half a cent, named separately because dropping a row is not reconciling a total.
    DETAIL_ROW_EPSILON = 0.005
    #: Balance below this counts as repaid; guards `LoanAmortization.loan_free_year` against
    #: float residue.
    BALANCE_EPSILON = 0.01
    #: Relative tolerance for the kWh attribution check of the energy balance (the V11 view of
    #: the next slice is its reader), where quantities are large enough that an absolute euro
    #: epsilon means nothing.
    QUANTITY_RELATIVE_EPSILON = 1e-6
    #: Relative tolerance on "the replacement arrived exactly when the life ran out" (V15). The
    #: engine spaces replacements at the rounded service life and the depreciation life is
    #: recovered by inverting the residual formula, so the two agree to a few ulps and must be
    #: treated as agreeing; only a genuinely *early* replacement shortens a write-down.
    DEPRECIATION_SPACING_RELATIVE_EPSILON = 1e-9


#: Whatever a fold is folding — a Sankey ribbon, a treemap tile. Generic so `_fold_small` hands
#: back the caller's own item type rather than an untyped list.
FoldItem = TypeVar("FoldItem")


def _fold_small(
    items: Sequence[FoldItem],
    amount_of: Callable[[FoldItem], float],
    share: float,
    fold_key: Callable[[FoldItem], Any],
    fold_factory: Callable[[Any, float], FoldItem],
) -> Tuple[List[FoldItem], int, float]:
    """Folds items below `share` of the total into one replacement item per fold key.

    The one fold algorithm of the chart views, written once because the Sankey's ribbon fold and
    the treemap's tile fold are the same operation on different item types: measure every item,
    drop the ones carrying less than `share` of the whole, and put back a single replacement per
    fold key carrying exactly what they carried together. Nothing is ever dropped — the fold is a
    readability choice, never a silent cap — which is what lets the reconciliation invariants of
    the folding views survive it.

    Args:
        items: The items to fold, in the order the caller wants them kept.
        amount_of: The magnitude of one item; the fold threshold is `share` of their sum.
        share: Fraction of the total below which an item is folded.
        fold_key: Which replacement item an folded item belongs to — the (source, target) node
            pair for ribbons, the display group for tiles — so a fold never merges across the
            grouping the chart is built on.
        fold_factory: Builds the replacement item from its fold key and its total amount.

    Returns:
        `(kept, folded_count, folded_total)` — the surviving items followed by one replacement
        per fold key, how many items were folded, and the euros they carried.
    """
    total = sum(amount_of(item) for item in items)
    threshold = total * share
    kept: List[FoldItem] = []
    folded_amounts: Dict[Any, float] = {}
    folded_count = 0
    for item in items:
        amount = amount_of(item)
        if amount < threshold:
            key = fold_key(item)
            folded_amounts[key] = folded_amounts.get(key, 0.0) + amount
            folded_count += 1
            continue
        kept.append(item)
    for key, amount in folded_amounts.items():
        kept.append(fold_factory(key, amount))
    return kept, folded_count, sum(folded_amounts.values())


# ============================================================================ V1 actor flows

class FlowCounterparties:
    """Who is on the other side of a cash flow, per cost category (visualization spec §3, V1).

    The actor Sankey needs a *node* for every end of every ribbon, and the timeline names only
    one of the two: the payer. This table supplies the other one — the external counterparty the
    money goes to or comes from — as a fixed, reviewable mapping from cost category onto the five
    node labels the owner approved (Q3): "market" for contractors and vendors, "suppliers" for
    energy, operation and insurance, "state" for taxes and support, "bank" for the loan, and
    "grid operator" for feed-in revenue.

    Two properties make the mapping safe to trust. It is *declared*, not inferred: a category the
    table does not name raises `CostDataError` rather than landing in a default bucket, so a
    category added to the engine cannot silently disappear into an unlabelled ribbon. And
    inter-actor transfers are declared separately in `TRANSFER_CATEGORIES` instead of being
    detected by summing to zero at runtime — a transfer that fails to net out is then a reported
    defect rather than a pair of ribbons that quietly stopped being a transfer.
    """

    #: Contractors and vendors: the capital side, including the residual value written back.
    MARKET = "market"
    #: Energy, maintenance, fixed operation and insurance suppliers.
    SUPPLIERS = "suppliers"
    #: Taxes, carbon charges and public support.
    STATE = "state"
    #: The lender: disbursement in, debt service out.
    BANK = "bank"
    #: The buyer of exported electricity.
    GRID_OPERATOR = "grid operator"

    #: Cost category -> counterparty node. Total over the categories that are not transfers;
    #: anything missing is a defect and raises (see `counterparty_of`).
    BY_CATEGORY: Dict[CostCategory, str] = {
        CostCategory.INVESTMENT: MARKET,
        CostCategory.PLANNING: MARKET,
        CostCategory.REMOVAL: MARKET,
        CostCategory.REPLACEMENT: MARKET,
        CostCategory.RESIDUAL_VALUE: MARKET,
        CostCategory.ANYWAY_COST_CREDIT: MARKET,
        # The §4.2 sinking fund pre-funds a future purchase from the market; it is money set
        # aside rather than paid out, and the market end is where it is destined.
        CostCategory.REPLACEMENT_RESERVE: MARKET,
        CostCategory.MAINTENANCE: SUPPLIERS,
        CostCategory.FIXED_OPERATION: SUPPLIERS,
        CostCategory.ENERGY_WORKING: SUPPLIERS,
        CostCategory.ENERGY_STANDING: SUPPLIERS,
        CostCategory.ENERGY_CAPACITY_CHARGE: SUPPLIERS,
        CostCategory.ENERGY_CO2_PRICE: STATE,
        # A macroeconomic damage charge is not a payment at all; society is the counterparty, and
        # "state" is the node that stands for it here (the perspective that carries it says so).
        CostCategory.CO2_DAMAGE: STATE,
        CostCategory.SUBSIDY: STATE,
        CostCategory.LOAN_DISBURSEMENT: BANK,
        CostCategory.LOAN_INTEREST: BANK,
        CostCategory.LOAN_PRINCIPAL: BANK,
        CostCategory.FEED_IN_REVENUE: GRID_OPERATOR,
    }

    #: Categories booked as *matched pairs* between two payers by the allocation rulesets. They
    #: are drawn payer-to-payer instead of as two external stubs, and the view validates that
    #: they net to zero across payers (fail-fast, D25). This is the narrow, Sankey sense of
    #: "transfer" — both legs are on the timeline — and is deliberately *not*
    #: `StatementPartitions.SOCIETY_TRANSFER_CATEGORIES`, which is the wider macroeconomic sense.
    TRANSFER_CATEGORIES = frozenset({CostCategory.MODERNIZATION_LEVY})

    #: Ribbons smaller than this share of the gross flow volume are folded per node pair.
    SMALL_FLOW_SHARE = 0.005

    #: Label of the folded ribbon; named so the caption and the data agree on the wording.
    OTHER_LABEL = "other"

    @classmethod
    def counterparty_of(cls, category: CostCategory) -> str:
        """The counterparty node of one category, or `CostDataError` if none is declared.

        The fail-fast half of the taxonomy: an unmapped category means the Sankey would have to
        invent a node, and inventing one would silently misattribute real money. The message
        names the category and the class to extend, because that is the whole fix.
        """
        counterparty = cls.BY_CATEGORY.get(category)
        if counterparty is None:
            raise CostDataError(
                f"No actor-flow counterparty declared for cost category {category.value!r}; add it "
                "to views.FlowCounterparties.BY_CATEGORY (or to TRANSFER_CATEGORIES if it is an "
                "inter-actor transfer) before it can be drawn."
            )
        return counterparty


@dataclass(frozen=True)
class ActorFlow:
    """One ribbon of the actor Sankey: nominal euros moving from one node to another.

    Direction is already resolved — `source` pays `target` — so the renderer never has to look
    at a sign: an entry booked positive (a cost) becomes payer -> counterparty, a negative one
    (support, revenue, a disbursement) becomes counterparty -> payer, and `amount_in_euro` is
    always the positive magnitude. `category` is the flow's cost category, which the renderer
    turns into a display-group hue; it is None for the folded "other" ribbon, which has no single
    category left.
    """

    source: str
    target: str
    amount_in_euro: float
    category: Optional[CostCategory] = None
    is_transfer: bool = False


@dataclass(frozen=True)
class ActorFlowMatrix:
    """Who pays whom over the whole horizon, as ribbons plus the node columns (V1).

    Nominal lifetime sums of the **full** allocated timeline in the BEST_ESTIMATE slot, which is
    the only reading under which the §6.5 zero-sum property stays checkable: a scoped timeline
    would show one leg of every transfer and none of the counter-party's. `total_band` carries
    the grand total as a band so the title can state the uncertainty the ribbons themselves
    cannot.

    Reconciliation: `net_by_actor()` of an actor equals the nominal sum of the entries booked on
    that payer inside the horizon — its scoped timeline, for an actor-scoped perspective — and
    the transfer ribbons net to zero across payers. Both are validated in `actor_flow_matrix`
    before the matrix is returned, so an instance that exists reconciles.
    """

    flows: List[ActorFlow]
    #: Payer nodes, in the order they first appear on the timeline. Each gets a column of its own
    #: in the drawing; `actor_columns` decides in which order (Q23).
    actors: List[str]
    #: Counterparty nodes that appear as a ribbon source (left column).
    sources: List[str]
    #: Counterparty nodes that appear as a ribbon target (right column).
    sinks: List[str]
    #: Nominal lifetime total of the whole timeline, as a band, for the title.
    total_band: UncertainValue
    #: How many ribbons were folded into "other" and how many euros they carried.
    folded_ribbon_count: int = 0
    folded_amount_in_euro: float = 0.0

    def actor_columns(self) -> List[List[str]]:
        """One column per internal party, ordered so transfers between them run left to right.

        The layout decision (Q23), made here rather than in a renderer because it is a property
        of the flows and both renderers have to reach the same answer. Every actor used to share
        one middle column, which meant the tenant-to-landlord levy had nowhere to go: it was drawn
        as a band looping out of the column and back into it, a special case in both Sankey
        renderers and the only ribbon on the page that did not read left to right. Giving each
        party its own column removes the case entirely — the levy becomes an ordinary ribbon
        between two adjacent columns.

        The order is a topological sort of the transfer graph: an edge runs from each payer to each
        payee of a declared inter-actor transfer, and a payer's column is placed before its payee's.
        Ties — actors with no transfer between them, which is every pair in a run without a levy —
        keep the timeline order the payers first appeared in, so the layout is deterministic and a
        re-rendered report stays byte-identical. A cycle (A pays B, B pays A) has no topological
        order at all; the remaining actors are then appended in timeline order rather than raising,
        because a mutual transfer is a legitimate allocation and a picture whose columns are merely
        in an arbitrary order is much better than no picture.

        Returns:
            One single-actor column per party, left to right.
        """
        payers_of: Dict[str, Set[str]] = {actor: set() for actor in self.actors}
        for flow in self.flows:
            if flow.is_transfer and flow.source in payers_of and flow.target in payers_of:
                payers_of[flow.target].add(flow.source)
        ordered: List[str] = []
        remaining = list(self.actors)
        while remaining:
            ready = [actor for actor in remaining if not payers_of[actor] - set(ordered)]
            if not ready:  # a transfer cycle: keep the input order for what is left
                ready = remaining[:1]
            ordered.append(ready[0])
            remaining.remove(ready[0])
        return [[actor] for actor in ordered]

    def net_by_actor(self) -> Dict[str, float]:
        """Outflows minus inflows per actor — the actor's nominal lifetime cost.

        The reconciliation handle: this equals the nominal sum of the entries the timeline books
        on that payer, which is what makes the picture an accounting statement rather than an
        illustration. `actor_flow_matrix` checks it here rather than leaving it to a caption.
        """
        nets: Dict[str, float] = {actor: 0.0 for actor in self.actors}
        for flow in self.flows:
            if flow.source in nets:
                nets[flow.source] += flow.amount_in_euro
            if flow.target in nets:
                nets[flow.target] -= flow.amount_in_euro
        return nets


# ------------------------------------------------------------------ story chapters (Q24)


@dataclass(frozen=True)
class StoryPerspectives:
    """Which evaluated perspectives belong to which chapter of the report (owner decision Q24).

    The report tells three stories — the owner lives here, the owner rents it out, and what it
    means for the economy — and each is told with its own perspectives. Deciding which is which is
    a classification of the *results*, not a rendering choice, which is why it lives here: a
    renderer that picked perspectives by matching their id against strings would silently tell the
    wrong story for any bundle whose ids differ from the shipped ones.

    Each list may be empty, and an empty one means the chapter is skipped with a log line rather
    than rendered as an empty box: a run of an owner-occupied house genuinely has no landlord
    story, and inventing one would be worse than omitting it.
    """

    owner: Tuple[LifecycleCostResult, ...]
    rented: Tuple[LifecycleCostResult, ...]
    society: Tuple[LifecycleCostResult, ...]


def has_macroeconomic_accounting(result: LifecycleCostResult) -> bool:
    """Whether this perspective is the macroeconomic one, by what it books rather than by its id.

    CO2 at its damage cost is the structural signature of §4.5 accounting: no financial
    perspective books a `CO2_DAMAGE` flow, and the macroeconomic one always does whenever the
    building emits anything at all. Reading the timeline for it keeps presentation from having to
    know that the shipped bundle happens to call that perspective "macroeconomic".
    """
    return any(entry.category == CostCategory.CO2_DAMAGE for entry in result.timeline.entries)


def story_perspectives(results: Iterable[LifecycleCostResult]) -> StoryPerspectives:
    """Sorts an evaluated matrix's perspectives into the three story chapters (Q24).

    Three rules, applied in this order because the classes overlap at the edges. A perspective
    that books CO2 damage is the **society** story. One scoped to a landlord or a tenant is the
    **rented-out** story. Of what is left, the **owner-occupied** story takes the ones an owner
    would actually be shown: an explicitly owner-scoped perspective, or a net one — a perspective
    that books support, i.e. the after-subsidy view a household pays out of its own account. The
    gross perspectives stay out of it, because their whole purpose is the perspective-free "what
    does the technology cost" question the common chapter answers.

    A run with no support at all would leave the owner story empty by that rule, which would be
    wrong rather than honest — a cash purchase without subsidies is still an owner's story — so
    the leftovers are promoted in that one case, and the case is tested for rather than inferred
    from the owner list being empty. The difference matters for a bundle that pairs a gross
    system perspective with a rented-out pair: support *is* on the page, the owner rule correctly
    finds no owner perspective among the leftovers, and the chapter has to stay empty instead of
    being filled with the perspective-free gross view, whose whole purpose is the common chapter.
    When the fallback does fire it still prefers the owner-like leftovers — the ones scoped to an
    owner-occupier or to the system as a whole — over anything scoped to some other party.

    Args:
        results: The evaluated perspectives, in bundle order (the order they are rendered in).

    Returns:
        The three lists, each in the input's order.
    """
    evaluated = list(results)
    society: List[LifecycleCostResult] = []
    rented: List[LifecycleCostResult] = []
    rest: List[LifecycleCostResult] = []
    for result in evaluated:
        if has_macroeconomic_accounting(result):
            society.append(result)
        elif result.scope_payer in (Actor.LANDLORD, Actor.TENANT):
            rented.append(result)
        else:
            rest.append(result)
    owner = tuple(
        result for result in rest
        if result.scope_payer == Actor.OWNER_OCCUPIER
        or any(entry.category == CostCategory.SUBSIDY for entry in result.scoped_timeline().entries)
    )
    if not owner and not _run_books_support(evaluated):
        owner = tuple(
            result for result in rest
            if result.scope_payer in (Actor.OWNER_OCCUPIER, Actor.SYSTEM)
        ) or tuple(rest)
    return StoryPerspectives(owner=owner, rented=tuple(rented), society=tuple(society))


def _run_books_support(results: Sequence[LifecycleCostResult]) -> bool:
    """Whether any perspective of the run books a subsidy anywhere on its full timeline.

    The guard on the owner chapter's fallback. It reads the **full** timeline of every
    perspective rather than the scoped one, because the question is about the run — "was this
    renovation supported at all" — and not about what one perspective reports on: a landlord
    perspective's grant is support on the page even when the leftover gross perspective knows
    nothing of it.
    """
    return any(
        entry.category == CostCategory.SUBSIDY
        for result in results
        for entry in result.timeline.entries
    )


# ------------------------------------------- party statements (Q21 landlord, Q26 F4 the rest)


class LandlordStatementCategories:
    """Which cost categories are cash and which are accounting credits, plus the row labels (Q21).

    The whole content of a two-sided statement is this split, so it is data rather than a chain of
    `if`s inside a renderer. **Cash** categories move money through an account in the year they are
    booked: the investment and the replacements paid to contractors, the maintenance, the levy
    received from the tenant, the subsidies, the feed-in revenue, the loan flows. **Accounting
    credits** value something without any money moving — the residual worth of hardware at the
    horizon and the anyway credit for a renovation the building would have needed regardless. Both
    belong in a lifecycle NPV; only one of them pays bills, and a landlord perspective can look
    strongly advantageous on the strength of the half that does not.

    Anything the engine one day books onto a party that is not named here counts as cash, which is
    the conservative direction: an unclassified flow is then *understated* as an advantage rather
    than being silently promoted into the accounting half.

    The class keeps its Q21 name because it is the landlord statement's own vocabulary and the
    charts and tests address it by that name; the generalization to the owner, the tenant and
    society (Q26 F4) reuses `ACCOUNTING_CREDIT_CATEGORIES` and `LABELS` through
    `StatementPartitions` rather than restating them.
    """

    ACCOUNTING_CREDIT_CATEGORIES = (CostCategory.RESIDUAL_VALUE, CostCategory.ANYWAY_COST_CREDIT)
    #: Labels for the statement rows, so the table reads as a statement rather than as an enum
    #: dump. A category with no entry here falls back to its own name.
    LABELS = {
        CostCategory.INVESTMENT: "investment paid",
        CostCategory.PLANNING: "planning paid",
        CostCategory.REMOVAL: "removal of the old system",
        CostCategory.REPLACEMENT: "replacements paid",
        CostCategory.MAINTENANCE: "maintenance paid",
        CostCategory.FIXED_OPERATION: "fixed operation paid",
        CostCategory.MODERNIZATION_LEVY: "levy income (from the tenant)",
        CostCategory.SUBSIDY: "subsidies received",
        CostCategory.ENERGY_WORKING: "energy, working price",
        CostCategory.ENERGY_STANDING: "energy, standing charge",
        CostCategory.ENERGY_CO2_PRICE: "energy, CO2 price",
        CostCategory.ENERGY_CAPACITY_CHARGE: "energy, capacity charge",
        CostCategory.REPLACEMENT_RESERVE: "replacement reserve paid in",
        CostCategory.CO2_DAMAGE: "CO2 at damage cost",
        CostCategory.FEED_IN_REVENUE: "feed-in revenue",
        CostCategory.LOAN_INTEREST: "loan interest paid",
        CostCategory.LOAN_PRINCIPAL: "loan principal repaid",
        CostCategory.LOAN_DISBURSEMENT: "loan disbursed",
        CostCategory.RESIDUAL_VALUE: "residual value at the horizon",
        CostCategory.ANYWAY_COST_CREDIT: "anyway credit (avoided renovation)",
    }
    #: The node id of the party the income Sankey centres on, and of the terminal node the
    #: leftover ribbon runs into.
    LANDLORD_NODE = "landlord"
    NET_POSITION_NODE = "net position"


@dataclass(frozen=True)
class StatementPartition:
    """How one party's NPV is split into two named sides (owner decision Q26 F4).

    The generalization of the Q21 landlord statement: every party the report states — the
    owner-occupier, the landlord, the tenant, society — gets the same two-sided treatment, and the
    only thing that differs between them is *which* categories belong on the second side and what
    the two sides are called. Making that a value rather than four near-identical functions is
    what keeps the reconciliation invariant (the sides sum exactly to the perspective's NPV) a
    single implementation instead of four chances to get it wrong.

    Fields worth a word. `secondary_categories` are the categories of the second side — the two
    accounting credits for the household parties, the transfer categories for society.
    `secondary_is_transfer` switches the second side from "value, not payment" to "money that
    moves between parties without using resources", which the report renders paired and with an
    explicit zero-sum line. `labels` is consulted before `LandlordStatementCategories.LABELS`, so
    a party can rename a row that means something different from its side of the same flow: the
    levy is *income* to the landlord and a *payment* to the tenant.
    """

    id: str
    party_label: str
    primary_label: str
    secondary_label: str
    secondary_categories: Tuple[CostCategory, ...]
    secondary_is_transfer: bool = False
    labels: Mapping[CostCategory, str] = field(default_factory=dict)

    def label_of(self, category: CostCategory) -> str:
        """The row label of one category under this partition, falling back to the shared table."""
        if category in self.labels:
            return self.labels[category]
        return LandlordStatementCategories.LABELS.get(category, category.value)

    def is_secondary(self, category: CostCategory) -> bool:
        """Whether a category belongs on the second side of this partition."""
        return category in self.secondary_categories


class StatementPartitions:
    """The four party statements the report publishes (Q21 landlord, Q26 F4 the other three).

    One partition per chapter of the report. The three household parties share the cash /
    accounting-credit split — it is the same distinction between money that moved and value that
    was merely booked — and differ only in the row labels and in which flows their perspective
    carries at all. Society is the structurally different one: its second side is not a book value
    but the *transfers*, which are the whole point of the macroeconomic view.

    `TENANT` deliberately declares an empty second side. A tenant receives nothing back in this
    ledger, and the authored prose says so explicitly: where the renovation lowers the energy
    bill, the relief shows up as a smaller cost line, never as a credit. An empty side is
    therefore the honest partition rather than a missing feature, and the report renders its
    subtotal as the zero it is.
    """

    #: Categories that move money between parties without consuming resources (§4.5). Society's
    #: second side; the macroeconomic accounting removes every one of them at source, which is
    #: why they reach the statement summing to zero. The wider, macroeconomic sense of
    #: "transfer": `FlowCounterparties.TRANSFER_CATEGORIES` is the Sankey's narrow one, the
    #: single category whose *both* legs are booked on the timeline and can be drawn payer to
    #: payer — a subsidy or a feed-in tariff is a transfer to society without the state's leg
    #: ever appearing as an entry.
    SOCIETY_TRANSFER_CATEGORIES = (
        CostCategory.SUBSIDY,
        CostCategory.MODERNIZATION_LEVY,
        CostCategory.FEED_IN_REVENUE,
        CostCategory.ENERGY_CO2_PRICE,
    )

    LANDLORD = StatementPartition(
        id="landlord",
        party_label="landlord",
        primary_label="cash flows",
        secondary_label="accounting credits",
        secondary_categories=LandlordStatementCategories.ACCOUNTING_CREDIT_CATEGORIES,
    )
    OWNER = StatementPartition(
        id="owner",
        party_label="owner",
        primary_label="cash flows",
        secondary_label="accounting credits",
        secondary_categories=LandlordStatementCategories.ACCOUNTING_CREDIT_CATEGORIES,
        labels={
            # The two halves are separate rows rather than one netted figure: the prose speaks of
            # "the investment net of subsidies", and the honest way to show a net is to show both
            # numbers that make it.
            CostCategory.INVESTMENT: "investment paid (gross)",
            CostCategory.SUBSIDY: "subsidies received (deducted from the investment)",
            CostCategory.MODERNIZATION_LEVY: "modernization levy",
        },
    )
    TENANT = StatementPartition(
        id="tenant",
        party_label="tenant",
        primary_label="what the tenant pays",
        secondary_label="credits (none in this ledger)",
        secondary_categories=(),
        labels={
            CostCategory.MODERNIZATION_LEVY: "modernization levy (added to the rent)",
            CostCategory.ENERGY_WORKING: "energy, working price",
            CostCategory.ENERGY_STANDING: "energy, standing charge",
            CostCategory.ENERGY_CO2_PRICE: "energy, CO2 price",
            CostCategory.MAINTENANCE: "apportioned operating costs",
            CostCategory.FIXED_OPERATION: "apportioned fixed operation",
        },
    )
    SOCIETY = StatementPartition(
        id="society",
        party_label="society",
        primary_label="real resource costs",
        secondary_label="transfers",
        secondary_categories=SOCIETY_TRANSFER_CATEGORIES,
        secondary_is_transfer=True,
        labels={
            CostCategory.INVESTMENT: "hardware and installation",
            CostCategory.REPLACEMENT: "replacements",
            CostCategory.MAINTENANCE: "maintenance performed",
            CostCategory.RESIDUAL_VALUE: "residual value at the horizon",
            CostCategory.ANYWAY_COST_CREDIT: "anyway credit (avoided renovation)",
            CostCategory.CO2_DAMAGE: "CO2 at damage cost",
            CostCategory.SUBSIDY: "subsidies (transfer)",
            CostCategory.MODERNIZATION_LEVY: "modernization levy (transfer)",
            CostCategory.FEED_IN_REVENUE: "feed-in remuneration (transfer)",
            CostCategory.ENERGY_CO2_PRICE: "CO2 price on the bill (transfer)",
        },
    )


@dataclass(frozen=True)
class StatementLine:
    """One row of a party statement: a category, its present value and which side it is on.

    `npv_in_euro` keeps the report's sign convention — positive is a cost to the party, negative
    is money or value arriving — so a reader comparing this row against the perspectives table or
    the category pivot sees the same number with the same sign, not a re-signed one.

    `is_accounting_credit` says the row sits on the *second* side of its partition. For the three
    household parties that side is the accounting credits, which is what the flag is named after
    and what the landlord Sankey styles differently; for society it is the transfers, and the
    society statement labels the side itself rather than leaning on the flag's name.
    """

    label: str
    category: CostCategory
    npv_in_euro: float
    is_accounting_credit: bool
    #: For a paired transfer row: the party that carries this half of the pair, or None for an
    #: ordinary row. The society statement renders both halves of every transfer so their sum is
    #: visibly zero (Q26 F4), and the two halves are otherwise indistinguishable.
    payer: Optional[Actor] = None


@dataclass(frozen=True)
class IncomeRibbon:
    """One ribbon of the landlord income Sankey: money arriving at or leaving the landlord node.

    The named form of what used to be a five-tuple. Direction is already resolved — `source` pays
    `target` — and `amount_in_euro` is the positive magnitude, because a Sankey ribbon has no
    sign. It is deliberately *not* an `ActorFlow`: an actor-flow ribbon runs between a payer and
    an external counterparty and carries the transfer flag, while these ribbons run between the
    landlord and one of his own statement rows and carry the cash / accounting-credit split the
    renderer styles them by.
    """

    source: str
    target: str
    amount_in_euro: float
    is_accounting_credit: bool
    #: The category the ribbon's money belongs to, which the renderer turns into a display-group
    #: hue; None on the net-position ribbon, which is a result rather than a category.
    category: Optional[CostCategory] = None


@dataclass(frozen=True)
class PerspectiveStatement:
    """One party's NPV as a two-sided statement (Q21 for the landlord, Q26 F4 for the rest).

    Answers the question the headline NPV cannot: a perspective can show a strongly negative net
    position — an advantage — while very little of it ever reaches a bank account, because the
    residual value of the hardware and the anyway credit for a renovation the building needed
    anyway are book entries, not payments. The statement states each side's bottom line before
    combining them. Under the society partition the same shape answers a different question: what
    of the result is real resource use and what is a transfer that nets to zero.

    Reconciliation, validated in :func:`perspective_statement`: `cash_subtotal +
    accounting_subtotal == net_position == the perspective's total NPV`. It is not new arithmetic
    — the split is by category over the same discounted timeline everything else in the report
    reads — which is exactly why the identity has to hold exactly rather than approximately.

    The two side fields keep the Q21 names (`cash_lines`, `accounting_lines`) because that is what
    they are for three of the four partitions and what the landlord Sankey and its tests address;
    `partition` carries the labels a renderer should print, so the society statement's sides are
    titled "real resource costs" and "transfers" without any renderer special-casing.
    """

    perspective_id: str
    cash_lines: Tuple[StatementLine, ...]
    accounting_lines: Tuple[StatementLine, ...]
    cash_subtotal_in_euro: float
    accounting_subtotal_in_euro: float
    net_position_in_euro: float
    #: The net position as the band the perspectives table publishes, for the caption.
    net_position_band: UncertainValue
    #: The levy summary of this perspective, when it has one — the caption's "X EUR/a, cap
    #: binding" clause reads it. None for a party with no rent increase.
    levy: Optional[ModernizationLevySummary] = None
    #: Which partition produced the two sides; supplies the side labels and the transfer flag.
    partition: StatementPartition = StatementPartitions.LANDLORD

    def income_flows(self) -> Tuple[Tuple[IncomeRibbon, ...], bool]:
        """The landlord income Sankey as `IncomeRibbon`s, plus the net-position direction flag.

        The earnings-Sankey convention (Q25): everything that arrives flows into the landlord node
        from the left, everything that is spent leaves to the right, and **the ribbon left over is
        the bottom line** — it runs on to a terminal node named for the net position. Because the
        report's sign convention is cost-positive, an income line is a category with a negative NPV
        and an expense line one with a positive NPV.

        Both signs of the net position are handled, which is the case the convention makes easy to
        get wrong. When the renovation is advantageous for the landlord (negative NPV, income
        exceeds expenses) the leftover leaves the landlord node to the right, like a profit. When
        it is a net cost (positive NPV) the picture is a loss: the missing money has to come from
        somewhere, so the net position enters from the *left* as a source and the flag says so.

        **Landlord only.** The node labels are the landlord's own (`LANDLORD_NODE`), so the
        picture claims a party the statement may not be about. A statement is callable under any
        of the four partitions, and drawing a tenant's or society's rows around a node labelled
        "landlord" would be a mislabelled picture rather than a missing feature — so this refuses
        instead. The tenant and society chapters state their sides as tables.

        Returns:
            The ribbons, and `True` when the net-position ribbon is an inflow (a net cost) rather
            than the usual leftover outflow. Ribbon amounts are magnitudes; a Sankey ribbon has
            no sign.

        Raises:
            CostDataError: If the statement was built under a partition other than the landlord's.
        """
        if self.partition.id != StatementPartitions.LANDLORD.id:
            raise CostDataError(
                f"The income Sankey is the landlord's picture, but this statement was built under "
                f"the {self.partition.id!r} partition: its ribbons would run into a node labelled "
                f"{LandlordStatementCategories.LANDLORD_NODE!r} while stating "
                f"{self.partition.party_label}'s rows. Render that party's statement as a table."
            )
        node = LandlordStatementCategories.LANDLORD_NODE
        net_node = LandlordStatementCategories.NET_POSITION_NODE
        flows: List[IncomeRibbon] = []
        for line in list(self.cash_lines) + list(self.accounting_lines):
            if line.npv_in_euro < 0:
                flows.append(IncomeRibbon(
                    source=line.label, target=node, amount_in_euro=-line.npv_in_euro,
                    is_accounting_credit=line.is_accounting_credit, category=line.category,
                ))
            elif line.npv_in_euro > 0:
                flows.append(IncomeRibbon(
                    source=node, target=line.label, amount_in_euro=line.npv_in_euro,
                    is_accounting_credit=line.is_accounting_credit, category=line.category,
                ))
        net_is_inflow = self.net_position_in_euro > 0
        if abs(self.net_position_in_euro) > ViewTolerances.RECONCILIATION_EPSILON:
            flows.append(
                IncomeRibbon(
                    source=net_node, target=node, amount_in_euro=self.net_position_in_euro,
                    is_accounting_credit=False,
                )
                if net_is_inflow
                else IncomeRibbon(
                    source=node, target=net_node, amount_in_euro=-self.net_position_in_euro,
                    is_accounting_credit=False,
                )
            )
        return tuple(flows), net_is_inflow


def landlord_statement(result: LifecycleCostResult) -> PerspectiveStatement:
    """The landlord's NPV split into money that moves and value that is merely booked (Q21).

    The Q21 statement under its own name: :func:`perspective_statement` with the landlord
    partition. It stays a named function because the landlord statement is the one that also
    carries an income Sankey and because three call sites and a test class address it directly.

    Args:
        result: The landlord perspective's result. Any result is accepted — the split is defined
            for every perspective — but only a landlord one is meaningful, and the report only
            renders it for that chapter.

    Returns:
        The two-sided statement with both subtotals and the net position.

    Raises:
        CostDataError: If the two sides do not sum to the perspective's total NPV.
    """
    return perspective_statement(result, StatementPartitions.LANDLORD)


def perspective_statement(
    result: LifecycleCostResult, partition: StatementPartition
) -> PerspectiveStatement:
    """One party's NPV as a two-sided statement, under the given partition (Q21, Q26 F4).

    One pass over `npv_by_category` of the perspective, sorting each category onto the first or
    the second side of `partition` and subtotalling both. No number is recomputed: the lines are
    the same present values the category pivot and the perspectives table publish, which is what
    lets the two sides be added back into the headline figure and checked against it — the whole
    reason the report may publish a decomposition at all (rule 2.9).

    A transfer partition (society) additionally renders **both halves of every transfer pair**,
    read from the full allocated timeline rather than the scoped one: a transfer is only visibly
    zero when the payer and the receiver are on the page together. The two halves cancel, so the
    reconciliation identity is unaffected — which is exactly the claim the society chapter makes
    and this is where it is checked rather than asserted.

    That mixed reading is also why a transfer partition has a precondition: the primary side is
    the perspective's *scoped* pivot while the transfer side is the *full* timeline, and the two
    are reconciled against the scoped total. The sum only closes when the two readings coincide,
    i.e. when the perspective is scoped to `Actor.SYSTEM` — which every macroeconomic perspective
    is. Applied to an actor-scoped result the statement would raise a reconciliation error that
    blamed a lost category for what is really a misuse, so the misuse is named up front instead.

    Args:
        result: The perspective to state; its `npv_by_category` and, for a transfer partition,
            its full `timeline`.
        partition: Which two sides to split into and what to call them.

    Returns:
        The two-sided statement with both subtotals and the net position.

    Raises:
        CostDataError: If a transfer partition is asked for on a perspective that is not
            SYSTEM-scoped, or if the two sides do not sum to the perspective's total NPV. The
            latter can only happen if a category was dropped between the pivot and this split,
            which would make the statement a picture of a business case that is not the one
            being reported.
    """
    if partition.secondary_is_transfer and result.scope_payer != Actor.SYSTEM:
        raise CostDataError(
            f"The {partition.id!r} statement reads the transfer side off the full timeline and the "
            f"resource side off the scoped one, so it is defined only for a SYSTEM-scoped "
            f"perspective; {result.perspective_id!r} is scoped to {result.scope_payer.value!r}. "
            "State that party with its own partition instead."
        )
    primary: List[StatementLine] = []
    secondary: List[StatementLine] = []
    for category, band in result.npv_by_category.items():
        if not band.best_estimate:
            continue
        is_secondary = partition.is_secondary(category)
        line = StatementLine(
            label=partition.label_of(category),
            category=category,
            npv_in_euro=band.best_estimate,
            is_accounting_credit=is_secondary,
        )
        (secondary if is_secondary else primary).append(line)
    if partition.secondary_is_transfer:
        secondary = _transfer_statement_lines(result, partition)
    primary_subtotal = sum(line.npv_in_euro for line in primary)
    secondary_subtotal = sum(line.npv_in_euro for line in secondary)
    net = primary_subtotal + secondary_subtotal
    total = result.total_npv_in_euro.best_estimate
    if abs(net - total) > ViewTolerances.RECONCILIATION_EPSILON:
        raise CostDataError(
            f"{partition.party_label.capitalize()} statement does not reconcile for perspective "
            f"{result.perspective_id!r}: {partition.primary_label} {primary_subtotal:,.2f} EUR + "
            f"{partition.secondary_label} {secondary_subtotal:,.2f} EUR = {net:,.2f} EUR, but the "
            f"perspective's NPV is {total:,.2f} EUR. The two sides are a partition of "
            "`npv_by_category`, so a difference means a category was lost."
        )
    return PerspectiveStatement(
        perspective_id=result.perspective_id,
        cash_lines=tuple(primary),
        accounting_lines=tuple(secondary),
        cash_subtotal_in_euro=primary_subtotal,
        accounting_subtotal_in_euro=secondary_subtotal,
        net_position_in_euro=net,
        net_position_band=result.total_npv_in_euro,
        levy=result.modernization_levy,
        partition=partition,
    )


def _transfer_statement_lines(
    result: LifecycleCostResult, partition: StatementPartition
) -> List[StatementLine]:
    """Both halves of every transfer, from the full timeline, so their sum is visibly zero (F4).

    The society statement's second side. It reads the **full** allocated timeline rather than the
    perspective's scoped pivot, because a transfer's two halves are booked on two different
    payers: the scoped view of one of them is not a transfer at all, it is a cost. Every declared
    transfer category is emitted per payer, in payer order, so a reader sees the tenant's payment
    beside the landlord's receipt and can add them to zero on the page.

    The macroeconomic accounting removes transfers at source (§4.5) — no subsidy, no feed-in
    revenue and no CO2 price is ever booked on that perspective — so on a macroeconomic result
    this returns an empty list, whose subtotal is the zero the statement prints. That is the
    honest rendering of "the transfers cancel": they never entered.

    Args:
        result: The perspective whose full timeline is read.
        partition: Supplies the transfer categories and their labels.

    Returns:
        One line per (category, payer) with a non-zero present value, in timeline order.
    """
    interest = result.parameters.interest_rate
    per_pair: Dict[Tuple[CostCategory, Actor], float] = {}
    for entry in result.timeline.entries:
        if entry.category not in partition.secondary_categories:
            continue
        key = (entry.category, entry.payer)
        per_pair[key] = per_pair.get(key, 0.0) + entry.amount_in_euro.best_estimate * discount_factor(
            interest, entry.year
        )
    lines: List[StatementLine] = []
    for (category, payer), value in per_pair.items():
        if abs(value) <= ViewTolerances.RECONCILIATION_EPSILON:
            continue
        lines.append(
            StatementLine(
                label=f"{partition.label_of(category)} — {payer.value}",
                category=category,
                npv_in_euro=value,
                is_accounting_credit=True,
                payer=payer,
            )
        )
    return lines


def actor_flow_matrix(result: LifecycleCostResult) -> ActorFlowMatrix:
    """Every timeline entry classified into a (source, target, amount) ribbon (V1).

    Reads `result.timeline` — the FULL allocated timeline, deliberately, because the chart's
    subject *is* the split between payers; the scoped timeline would draw a tenant paying a levy
    to nobody. Amounts are nominal (undiscounted) lifetime sums of the BEST_ESTIMATE slot (Q2): a
    banded Sankey is unreadable, so the band travels in `total_band` and is stated in the title
    instead.

    Three rules decide a ribbon. The counterparty comes from `FlowCounterparties`, declared per
    category and raising for anything unmapped. The direction comes from the entry's sign, so
    costs leave the payer and credits arrive. And a category declared in `TRANSFER_CATEGORIES` —
    the §559e modernization levy today — is drawn as one payer-to-receiver ribbon instead of as
    two external stubs; the view checks that those legs net to zero across all payers and raises
    if they do not, since a transfer that creates money is a defect in the allocation ruleset,
    not something to render, and it refuses a transfer running between more than two parties
    rather than inventing a split (see `_transfer_ribbons`).

    Reconciliation, both halves validated here: the transfer ribbons net to zero across payers,
    and each actor's net (outflows − inflows) equals the nominal sum of the entries the timeline
    books on that payer — which for an actor-scoped perspective is that actor's scoped timeline.
    The second check is what makes the picture an accounting statement rather than an
    illustration, and it catches what the first cannot: a ribbon drawn to the wrong end, a
    counterparty label that collides with a payer node, a fold that lost euros.

    Raises:
        CostDataError: On a category with no declared counterparty, on declared transfers that do
            not net to zero across payers, or on an actor whose ribbons do not net to what the
            timeline books on it.
    """
    horizon = result.parameters.observation_period_in_years
    ribbons: Dict[Tuple[str, str, Optional[CostCategory]], float] = {}
    transfer_net: Dict[str, float] = {}
    nominal_by_actor: Dict[str, float] = {}
    actors: List[str] = []
    for entry in result.timeline.entries:
        if not 0 <= entry.year <= horizon:
            continue
        actor = entry.payer.value
        if actor not in actors:
            actors.append(actor)
        amount = entry.amount_in_euro.best_estimate
        nominal_by_actor[actor] = nominal_by_actor.get(actor, 0.0) + amount
        if entry.category in FlowCounterparties.TRANSFER_CATEGORIES:
            transfer_net[actor] = transfer_net.get(actor, 0.0) + amount
            continue
        counterparty = FlowCounterparties.counterparty_of(entry.category)
        key = (
            (actor, counterparty, entry.category) if amount >= 0 else (counterparty, actor, entry.category)
        )
        ribbons[key] = ribbons.get(key, 0.0) + abs(amount)
    transfer_flows = _transfer_ribbons(transfer_net)
    matrix_flows = [
        ActorFlow(source=source, target=target, amount_in_euro=amount, category=category)
        for (source, target, category), amount in ribbons.items()
        if amount > ViewTolerances.RECONCILIATION_EPSILON
    ]
    matrix_flows, folded_count, folded_amount = _fold_small_flows(matrix_flows)
    matrix_flows.extend(transfer_flows)
    sources = [flow.source for flow in matrix_flows if flow.source not in actors]
    sinks = [flow.target for flow in matrix_flows if flow.target not in actors]
    matrix = ActorFlowMatrix(
        flows=matrix_flows,
        actors=actors,
        sources=list(dict.fromkeys(sources)),
        sinks=list(dict.fromkeys(sinks)),
        total_band=UncertainValue.sum(
            entry.amount_in_euro for entry in result.timeline.entries if 0 <= entry.year <= horizon
        ),
        folded_ribbon_count=folded_count,
        folded_amount_in_euro=folded_amount,
    )
    _validate_actor_nets(matrix, nominal_by_actor)
    return matrix


def _validate_actor_nets(matrix: ActorFlowMatrix, nominal_by_actor: Mapping[str, float]) -> None:
    """Checks every actor's ribbon net against the nominal sum the timeline books on that payer.

    The per-actor half of the V1 reconciliation, mirroring `_transfer_ribbons`' zero-sum check:
    the ribbons are a re-shaping of the timeline, so re-summing them per node has to give the
    timeline's own per-payer total back. A difference is never a rounding story — the ribbons are
    the same nominal amounts — it means money changed ends on the way into the picture.

    Args:
        matrix: The assembled matrix, ribbons and node columns.
        nominal_by_actor: Payer node -> nominal sum of that payer's entries inside the horizon.

    Raises:
        CostDataError: If any actor's net differs by more than
            `ViewTolerances.RECONCILIATION_EPSILON`.
    """
    nets = matrix.net_by_actor()
    mismatches = [
        f"{actor}: ribbons net to {nets.get(actor, 0.0):,.2f} EUR, the timeline books "
        f"{expected:,.2f} EUR"
        for actor, expected in nominal_by_actor.items()
        if abs(nets.get(actor, 0.0) - expected) > ViewTolerances.RECONCILIATION_EPSILON
    ]
    if mismatches:
        raise CostDataError(
            "The actor-flow ribbons do not reconcile with the timeline's per-payer nominal sums, "
            "so the Sankey would misattribute money: " + "; ".join(mismatches) + ". Every ribbon "
            "is one end of a timeline entry, so a difference means a ribbon was drawn to the "
            "wrong node — check `FlowCounterparties` for a counterparty label that collides with "
            "a payer's."
        )


def _transfer_ribbons(transfer_net: Dict[str, float]) -> List[ActorFlow]:
    """The single payer-to-payer ribbon of the declared transfers, validated to net to zero.

    `FlowCounterparties.TRANSFER_CATEGORIES` declares one category today, the §559e modernization
    levy, and it is booked as one matched pair: the tenant pays, the landlord receives. So the
    ribbon is that pair — one payer, one receiver, the whole net — and a run with a second payer
    or a second receiver is *refused* rather than drawn.

    The refusal replaces a proportional payer × receiver split that used to run here. With one
    payer and one receiver the split is the identity, so it was never exercised; with two of
    either it would have invented an allocation the engine never made — a tenant's levy spread
    over two landlords in proportion to what they received is an assumption, not a reading of the
    timeline. Restoring a split is the right upgrade when a second transfer category arrives, and
    it will then need the pair *the entries themselves* carry (per category and per subject)
    rather than a proportion derived from the nets.

    Args:
        transfer_net: Payer node -> nominal net of that payer's transfer entries; positive is a
            payer of the transfer, negative a receiver.

    Returns:
        The one ribbon, or an empty list when the run books no transfer at all.

    Raises:
        CostDataError: If the declared transfers do not net to zero across payers, i.e. if the
            allocation created or destroyed money; or if more than one payer or more than one
            receiver appears.
    """
    total = sum(transfer_net.values())
    if abs(total) > ViewTolerances.RECONCILIATION_EPSILON:
        raise CostDataError(
            f"Declared inter-actor transfer categories do not net to zero across payers "
            f"(residual {total:,.2f} EUR): {transfer_net}. A transfer pair that does not cancel "
            "means the allocation ruleset created or destroyed money (§6.5)."
        )
    payers = {
        actor: value for actor, value in transfer_net.items()
        if value > ViewTolerances.RECONCILIATION_EPSILON
    }
    receivers = {
        actor: -value for actor, value in transfer_net.items()
        if value < -ViewTolerances.RECONCILIATION_EPSILON
    }
    if len(payers) > 1 or len(receivers) > 1:
        raise CostDataError(
            f"The declared inter-actor transfers run between more than two parties — payers "
            f"{sorted(payers)}, receivers {sorted(receivers)} — and the actor Sankey draws one "
            "payer-to-receiver ribbon. Splitting the transfer across the parties would invent an "
            "allocation the timeline does not carry; give the ribbon builder the per-entry pairs "
            "before booking a second transfer category."
        )
    if not payers or not receivers:
        return []
    payer, paid = next(iter(payers.items()))
    receiver = next(iter(receivers))
    return [
        ActorFlow(
            source=payer,
            target=receiver,
            amount_in_euro=paid,
            category=CostCategory.MODERNIZATION_LEVY,
            is_transfer=True,
        )
    ]


def _fold_small_flows(flows: List[ActorFlow]) -> Tuple[List[ActorFlow], int, float]:
    """Folds ribbons below `FlowCounterparties.SMALL_FLOW_SHARE` into one "other" per node pair.

    A Sankey with fifty hairline ribbons is unreadable, but dropping them would be a silent cap,
    so the small ones are merged per (source, target) pair into a single categoryless ribbon and
    the count and total euros are returned for the caption to name. Folding preserves every node
    pair's total exactly, which is why the per-actor net reconciliation survives it.
    """
    return _fold_small(
        flows,
        lambda flow: flow.amount_in_euro,
        FlowCounterparties.SMALL_FLOW_SHARE,
        lambda flow: (flow.source, flow.target),
        lambda key, amount: ActorFlow(source=key[0], target=key[1], amount_in_euro=amount),
    )


# ============================================================================ V2 liquidity fan

def band_zero_crossings(series_by_slot: Mapping[Any, List[float]]) -> Dict[Any, Optional[int]]:
    """First non-negative year of each slot's series; None where it never crosses (V2).

    The band form of `results.discounted_payback_year`, and it *calls* that function per slot
    rather than re-implementing the crossing rule, so the fan's annotated interval and the
    printed payback year cannot disagree about what a crossing is (year 0 excluded, first
    crossing only). Keys are whatever the caller's series dict uses — `Slot` members for the
    view-side series, the `"low"/"best_estimate"/"high"` strings a `VariantComparison` carries.

    Returns:
        One crossing year per input key, None meaning "never within the horizon" — which is a
        real answer the fan annotates rather than omits.
    """
    return {key: discounted_payback_year(series) for key, series in series_by_slot.items()}


def cumulative_nominal_cost_series(result: LifecycleCostResult) -> Dict[Slot, List[float]]:
    """Cumulative *nominal* cost per slot over years 0..T — the liquidity fan's upper panel (V2).

    The undiscounted counterpart of `cumulative_discounted_cost_series`: a slot-wise running sum
    of `result.annual_cost_series_nominal_in_euro`, so the last point of each slot is that slot's
    nominal lifetime cost and the highest point is the deepest out-of-pocket position. Summing
    slot-wise is legitimate precisely because a slot is a coherent world — the LOW curve is "the
    whole horizon in the cheap world", not a lower confidence bound.

    It exists as a view rather than as a cumsum in the chart because a renderer that adds numbers
    is a renderer that can disagree with the engine (seam 4).
    """
    series: Dict[Slot, List[float]] = {}
    for slot in Slot:
        running = 0.0
        curve: List[float] = []
        for value in result.annual_cost_series_nominal_in_euro:
            running += value.slot(slot)
            curve.append(running)
        series[slot] = curve
    return series


def worst_liquidity_position(result: LifecycleCostResult) -> Tuple[int, float]:
    """Year and amount of the deepest out-of-pocket position (BEST_ESTIMATE slot, nominal) (V2).

    The annotation the nominal panel carries and the lifecycle-milestone lane restates: the
    maximum of the cumulative nominal cost curve, i.e. the point at which the most money has left
    the account and not come back. Cost is positive here (owner decision Q4), so "deepest" is the
    curve's maximum, and the reading is carried by the annotation rather than by the axis
    direction.

    Returns:
        `(year, cumulative nominal cost in euro)` — year 0 with 0.0 for an empty series.
    """
    curve = cumulative_nominal_cost_series(result)[Slot.BEST_ESTIMATE]
    if not curve:
        return 0, 0.0
    worst_year = max(range(len(curve)), key=lambda year: curve[year])
    return worst_year, curve[worst_year]


# ============================================================================ V3 attribution

class AttributionThresholds:
    """Cut-offs of the uncertainty attribution tornado (V3).

    Only one number, but it decides what a reader sees: how many subjects get their own bar
    before the rest are folded into a single row. The fold keeps the sum invariant exact, so the
    cut-off is a readability choice and never a hidden cap.
    """

    #: Subjects shown individually; everything below is folded into one row.
    TOP_N = 10

    #: Label of the folded row, shared by the view and every caption that mentions it.
    FOLD_LABEL = "all other subjects"


@dataclass(frozen=True)
class AttributionRow:
    """One subject's contribution to the width of the total NPV band (V3).

    `low_delta_in_euro` and `high_delta_in_euro` are the subject's own NPV in the LOW resp. HIGH
    world minus its NPV in the BEST_ESTIMATE world — signed, and *not* absolute widths.

    The sign of a revenue subject's LOW delta follows the band's orientation, and the orientation
    is already fixed by the time a flow reaches the timeline: a revenue-type amount enters through
    `UncertainValue.as_revenue`, which mirrors the band so that `minimum` always means "this
    entry in the LOW world" — for a revenue, the world where the *most* money arrives. A feed-in
    or support subject therefore has a negative LOW delta and a positive HIGH delta exactly like a
    cost subject, and its bar straddles the axis the same way. A positive LOW delta would mean an
    unmirrored band reached the timeline, which the entry's own min <= best <= max check forbids.
    """

    subject: str
    best_estimate_npv_in_euro: float
    low_delta_in_euro: float
    high_delta_in_euro: float
    #: True for the single folded row that carries every subject below the cut-off.
    is_fold: bool = False

    @property
    def width_in_euro(self) -> float:
        """How much of the total band's width this subject accounts for (the sort key)."""
        return self.high_delta_in_euro - self.low_delta_in_euro


def uncertainty_attribution(result: LifecycleCostResult) -> List[AttributionRow]:
    """Per-subject decomposition of the total NPV band (V3) — attribution, not sensitivity.

    Nothing is re-evaluated here. Because all engine arithmetic is slot-wise, the LOW and HIGH
    totals decompose *exactly* into per-subject contributions under the same assembly rules the
    total uses, and this view is that decomposition: `timeline.npv_by(subject)` on the scoped
    timeline, each subject's LOW and HIGH read against its own BEST_ESTIMATE. The chart title has
    to say "uncertainty attribution" rather than "sensitivity" for exactly this reason — no input
    was varied one at a time.

    Rows are sorted by band width descending, and everything below `AttributionThresholds.TOP_N`
    is folded into one row so that the bars still sum to the total.

    Reconciliation: `sum(low_delta) == total.minimum − total.best_estimate` and
    `sum(high_delta) == total.maximum − total.best_estimate`, including the folded row —
    validated here, not only in the tests, because a tornado whose bars do not sum to the total
    lies quietly.

    Raises:
        CostDataError: If the per-subject deltas do not sum to the total band's edges.
    """
    interest = result.parameters.interest_rate
    per_subject = result.scoped_timeline().npv_by(interest, lambda entry: entry.subject)
    rows = [
        AttributionRow(
            subject=subject,
            best_estimate_npv_in_euro=band.best_estimate,
            low_delta_in_euro=band.minimum - band.best_estimate,
            high_delta_in_euro=band.maximum - band.best_estimate,
        )
        for subject, band in per_subject.items()
    ]
    rows.sort(key=lambda row: row.width_in_euro, reverse=True)
    shown, folded = rows[: AttributionThresholds.TOP_N], rows[AttributionThresholds.TOP_N:]
    if folded:
        shown.append(
            AttributionRow(
                subject=AttributionThresholds.FOLD_LABEL,
                best_estimate_npv_in_euro=sum(row.best_estimate_npv_in_euro for row in folded),
                low_delta_in_euro=sum(row.low_delta_in_euro for row in folded),
                high_delta_in_euro=sum(row.high_delta_in_euro for row in folded),
                is_fold=True,
            )
        )
    total = result.total_npv_in_euro
    for name, actual, expected in (
        ("low", sum(row.low_delta_in_euro for row in shown), total.minimum - total.best_estimate),
        ("high", sum(row.high_delta_in_euro for row in shown), total.maximum - total.best_estimate),
    ):
        if abs(actual - expected) > ViewTolerances.RECONCILIATION_EPSILON:
            raise CostDataError(
                f"Uncertainty attribution does not reconcile with the total band: the {name} "
                f"deltas sum to {actual:,.2f} EUR but the total's {name} edge is {expected:,.2f} "
                "EUR away from its best estimate."
            )
    return shown


# ============================================================================ V4 bridge

@dataclass(frozen=True)
class BridgeStep:
    """One floating bar of the comparison bridge: a display group's NPV delta (V4).

    `group` is whatever key the caller's category mapping produces (the display-group index for
    every caller in this package), and `delta_in_euro` is variant minus reference in the
    BEST_ESTIMATE slot. Deltas deliberately carry no band: `high(variant − reference)` is not
    `high(variant) − high(reference)`, so a whisker on a delta bar would be arithmetic that means
    nothing (spec §3 V4).
    """

    group: Any
    delta_in_euro: float


def comparison_bridge(
    reference: LifecycleCostResult,
    variant: LifecycleCostResult,
    mapping: Mapping[CostCategory, GroupKey],
) -> List[BridgeStep]:
    """Why the variant's NPV differs from the reference's, decomposed by display group (V4).

    The bridge between two published totals: each display group's NPV in the variant minus the
    same group's NPV in the reference, BEST_ESTIMATE slot, in the fixed group order rather than
    sorted by magnitude — a bridge whose bars reorder between two reports cannot be read side by
    side (IBCS). A group present in only one variant folds in naturally, because a missing group
    is an explicit zero on that side.

    It takes the two *results* rather than the `VariantComparison` because the comparison object
    publishes deltas per subject and in total, never per category, and re-deriving a category
    split from subject deltas is not possible. The grouping mapping is passed in for the usual
    reason (`fold_categories`): grouping is a display concept, the sums are not.

    Reconciliation: `sum(step.delta) == variant.total_npv − reference.total_npv` in the
    BEST_ESTIMATE slot, validated here.

    Raises:
        CostDataError: If the steps do not sum to the published NPV delta, or if the caller's
            group keys cannot be ordered — see below.
    """
    variant_groups = fold_categories(variant.npv_by_category, mapping)
    reference_groups = fold_categories(reference.npv_by_category, mapping)
    present: Set[Any] = set(variant_groups) | set(reference_groups)
    try:
        # Display-group indices sort into the fixed order every chart stacks them in.
        keys: List[Any] = sorted(present)
    except TypeError as error:
        # A mapping whose keys cannot be compared has no bar order, and the bar order is the
        # bridge's whole readability claim (IBCS): two reports drawn from mappings that happened
        # to iterate differently would put the same group in different places, which is exactly
        # the silent divergence this view exists to prevent. Falling back to first-appearance
        # order used to hide that; naming the keys hands the caller the fix.
        raise CostDataError(
            "The comparison bridge's group keys cannot be ordered, so its bars have no fixed "
            f"order: {sorted((type(key).__name__, repr(key)) for key in present)}. Give the "
            "category mapping keys of one orderable type (the display-group index every caller "
            "in this package passes)."
        ) from error
    zero = UncertainValue.exact(0.0)
    steps = [
        BridgeStep(
            group=key,
            delta_in_euro=(
                variant_groups.get(key, zero).best_estimate - reference_groups.get(key, zero).best_estimate
            ),
        )
        for key in keys
    ]
    expected = variant.total_npv_in_euro.best_estimate - reference.total_npv_in_euro.best_estimate
    actual = sum(step.delta_in_euro for step in steps)
    if abs(actual - expected) > ViewTolerances.RECONCILIATION_EPSILON:
        raise CostDataError(
            f"Comparison bridge does not reconcile: the steps sum to {actual:,.2f} EUR but the "
            f"published NPV delta is {expected:,.2f} EUR."
        )
    return steps


# ============================================================================ V5 cost of credit

@dataclass(frozen=True)
class TotalCostOfCredit:
    """The consumer-credit disclosure of a financed perspective (V5's companion panel).

    "You borrow 50,000 and pay back 63,400", in the four parts a loan document states: the
    principal, the interest it costs, any fees, and the repayment grant that comes back off it.
    All figures are nominal (undiscounted) euros of the BEST_ESTIMATE slot, because that is what a
    loan contract quotes; `effective_annual_rate` is the internal rate of the loan's own flow
    sequence (disbursement and grant in, debt service out), i.e. the Effektivzins a reader can
    compare with a bank's offer.

    The rate is `None` whenever the flows on the timeline do not define one, and
    `effective_annual_rate_note` then says why in a phrase the panel can print beside the "n/a" —
    "no rate" and "no rate *because the schedule reaches past the horizon*" are very different
    statements about a loan, and the second one is not a defect the reader should have to guess
    at. The note is empty exactly when a rate is given.

    `fees_in_euro` is structurally zero today: the engine books no loan fee category, and the
    field exists so that the disclosure is complete and a future fee flows straight in rather
    than being bolted onto the interest. `unrepaid_principal_in_euro` is the part of the
    disbursement whose repayment falls beyond the observation horizon — a truncated schedule,
    not a defect, but one the panel has to state or its total would look wrong.
    """

    principal_in_euro: float
    interest_in_euro: float
    fees_in_euro: float
    grants_in_euro: float
    unrepaid_principal_in_euro: float
    effective_annual_rate: Optional[float]
    #: Why there is no rate, for the panel to print; empty when `effective_annual_rate` is set.
    effective_annual_rate_note: str = ""

    @property
    def total_repaid_in_euro(self) -> float:
        """Principal repaid plus interest plus fees — what actually leaves the account."""
        return self.principal_in_euro - self.unrepaid_principal_in_euro + self.interest_in_euro + self.fees_in_euro

    @property
    def net_cost_of_credit_in_euro(self) -> float:
        """What borrowing cost, net of the repayment grant: interest + fees − grants."""
        return self.interest_in_euro + self.fees_in_euro - self.grants_in_euro


def total_cost_of_credit(result: LifecycleCostResult) -> TotalCostOfCredit:
    """Principal, interest, fees and grants of the perspective's loan, plus its effective rate.

    Read off the same scoped timeline entries `loan_amortization_series` stacks, so the panel and
    the bars beside it cannot disagree. The repayment grant is picked up from the SUBSIDY entries
    booked under the financing subject — that is where `calculators/financing_application.py`
    puts a Tilgungszuschuss — and it enters the effective-rate calculation as money received at
    year 0, which is exactly why a grant lowers the rate.

    Reconciliation: the nominal sum of every loan-category entry plus the repayment grant equals
    `net_cost_of_credit_in_euro` minus the unrepaid principal — validated here.

    Returns:
        The disclosure. `effective_annual_rate` is None when there is no loan at all, when the
        schedule reaches past the observation horizon, or when the repayment grant exceeds the
        whole debt service; `effective_annual_rate_note` names which of the last two it was.

    Raises:
        CostDataError: If the loan entries do not reconcile with the disclosure's parts.
    """
    amortization = loan_amortization_series(result)
    horizon = result.parameters.observation_period_in_years
    scoped = result.scoped_timeline().entries
    interest_total = sum(amortization.interest_in_euro)
    principal_repaid = sum(amortization.principal_in_euro)
    grants = -sum(
        entry.amount_in_euro.best_estimate
        for entry in scoped
        if entry.category == CostCategory.SUBSIDY
        and entry.subject == FinancingConstants.FINANCING_SUBJECT
        and 0 <= entry.year <= horizon
    )
    disbursement = amortization.disbursement_in_euro
    nominal_loan_flows = sum(
        entry.amount_in_euro.best_estimate
        for entry in scoped
        if 0 <= entry.year <= horizon
        and (
            entry.category in (
                CostCategory.LOAN_INTEREST, CostCategory.LOAN_PRINCIPAL, CostCategory.LOAN_DISBURSEMENT
            )
            or (
                entry.category == CostCategory.SUBSIDY
                and entry.subject == FinancingConstants.FINANCING_SUBJECT
            )
        )
    )
    rate, rate_note = _effective_annual_rate(amortization, grants)
    disclosure = TotalCostOfCredit(
        principal_in_euro=disbursement,
        interest_in_euro=interest_total,
        fees_in_euro=0.0,
        grants_in_euro=grants,
        unrepaid_principal_in_euro=disbursement - principal_repaid,
        effective_annual_rate=rate,
        effective_annual_rate_note=rate_note,
    )
    expected = disclosure.net_cost_of_credit_in_euro - disclosure.unrepaid_principal_in_euro
    if abs(nominal_loan_flows - expected) > ViewTolerances.RECONCILIATION_EPSILON:
        raise CostDataError(
            f"Total cost of credit does not reconcile with the timeline: the loan entries sum to "
            f"{nominal_loan_flows:,.2f} EUR nominal, the disclosure implies {expected:,.2f} EUR "
            "(interest + fees − grants − unrepaid principal)."
        )
    return disclosure


def _effective_annual_rate(
    amortization: LoanAmortization, grants_in_euro: float
) -> Tuple[Optional[float], str]:
    """Internal rate of the loan's own flows: disbursement and grant in, debt service out.

    The Effektivzins, found by bisection (`numerics.bisect_root`, shared with the scenario
    break-even) on the same `discount_factor` every other present value in the package uses — one
    flow sequence instead of a grid. For a fee-free, grant-free annuity with annual periods it
    returns the nominal rate exactly, which is the null test the spec asks for; a repayment grant
    strictly lowers it because the borrower received money without owing more.

    **Two cases have no rate, and both used to produce a wrong one.** A schedule whose term
    reaches past the observation horizon is only *partly* on the timeline: solving the truncated
    sequence prices a loan the borrower never took — a ten-year 4 % annuity seen at a four-year
    horizon looks like −23 %, because most of the repayment is missing. That is refused on the
    same test `LoanAmortization.loan_free_year` uses, unrepaid principal above the reconciliation
    epsilon. And the search window is non-negative, `[0, 5.0]`: a repayment grant larger than the
    whole debt service means the borrower paid back less than was received, for which no
    non-negative rate solves the sequence, and the old window's −0.99 end always produced a root
    because the present value there is hugely negative. Both cases return a note instead.

    Args:
        amortization: The loan's booked interest, principal and disbursement.
        grants_in_euro: The repayment grant, positive, received at year 0.

    Returns:
        `(rate, note)`. The rate is a fraction and the note is empty; or the rate is None and the
        note says why in a phrase a panel can print.
    """
    if not amortization.has_flows() or amortization.disbursement_in_euro <= 0:
        return None, ""
    unrepaid = amortization.disbursement_in_euro - sum(amortization.principal_in_euro)
    if unrepaid > ViewTolerances.RECONCILIATION_EPSILON:
        return None, "the loan term reaches past the observation horizon"
    inflow = amortization.disbursement_in_euro + grants_in_euro
    service = [
        interest + principal
        for interest, principal in zip(amortization.interest_in_euro, amortization.principal_in_euro)
    ]

    def present_value(rate: float) -> float:
        return inflow - sum(
            amount * discount_factor(rate, year) for year, amount in enumerate(service) if amount
        )

    rate = bisect_root(present_value, window=(0.0, 5.0), max_iterations=200)
    if rate is None:
        return None, "the repayment grant exceeds the debt service"
    return rate, ""


# ============================================================================ V7 event strip

class EventKinds(str, enum.Enum):
    """The three things that can happen to a component on its lifetime strip (V7).

    The vocabulary of the strip in one place: a component is bought, is replaced, or is worth
    something at the horizon, and nothing on the chart means anything else. It is an enum rather
    than a bag of string constants because `LifecycleEvent.kind` is typed with it, which is what
    makes "nothing else" a property the type checker holds rather than a sentence in a docstring;
    the `str` mixin keeps the members printable and comparable with the plain strings the
    renderers were written against, so `event.kind == "residual"` still means what it says.
    """

    INVESTMENT = "investment"
    REPLACEMENT = "replacement"
    RESIDUAL = "residual"

    def __str__(self) -> str:
        """The kind's own word, so a member printed into a label reads as the chart's vocabulary.

        Without this, `f"{kind}"` would render "EventKinds.INVESTMENT" — the enum's default —
        into a lane label, which is the one place the enum must not be visible.
        """
        return self.value


@dataclass(frozen=True)
class LifecycleEvent:
    """One dated event on a component's strip: what happened, when, and for how much.

    Amounts are nominal BEST_ESTIMATE-slot euros as the timeline booked them, so an investment is
    positive and a residual value negative — the sign is the reader's cue that the residual is a
    credit, and the renderer does not flip it.
    """

    year: int
    amount_in_euro: float
    kind: EventKinds


@dataclass(frozen=True)
class ServiceSpan:
    """One interval a component was in service, derived from the events the timeline booked.

    Derived from the *booked* events and never from the catalog lifetime: the chart shows what
    the timeline charged, and a span that disagrees with the database service life is exactly
    the mismatch a reviewer should be able to see. Spans run from an install or replacement year
    to the next event, or to the horizon for the last one.
    """

    start_year: int
    end_year: int


@dataclass(frozen=True)
class EventStripRow:
    """One component's lifetime lane: its purchases, its replacements and its residual (V7).

    The row checks its own shape, because every property the renderer relies on to draw a lane is
    a property of *this object* rather than of the loop that happened to build it:

    * a `residual` requires at least one investment or replacement event — only an installation
      the timeline actually charged may be written down (§4.1, review package A);
    * the events are sorted by year, which is the order the lane is drawn in;
    * the spans align to the events — one span per event, starting at its year, each running to
      the start of the next and the last to the horizon — so a lane's bars tile its lane without
      overlapping or leaving a hole between two events.

    `component_event_strip` builds rows that satisfy all three; the checks are here so that a
    second builder (a comparison strip, a webtool payload) cannot quietly produce a lane that
    draws wrongly, and so that the gate is a checked property of the output rather than a
    calculator-internal rule.

    Raises:
        CostDataError: If any of the three invariants is violated.
    """

    subject: str
    events: List[LifecycleEvent]
    spans: List[ServiceSpan]
    residual: Optional[LifecycleEvent] = None

    def __post_init__(self) -> None:
        """Enforces the three invariants stated in the class docstring."""
        if self.residual is not None and not self.events:
            raise CostDataError(
                f"Subject {self.subject!r} carries a residual-value credit of "
                f"{self.residual.amount_in_euro:,.2f} EUR without any investment or replacement "
                "the timeline charged; only an installation charged inside the horizon may be "
                "written down (§4.1, review package A)."
            )
        years = [event.year for event in self.events]
        if years != sorted(years):
            raise CostDataError(
                f"The events of subject {self.subject!r} are not in year order ({years}); the "
                "lane is drawn in this order and its spans are derived from it."
            )
        if len(self.spans) != len(self.events):
            raise CostDataError(
                f"Subject {self.subject!r} has {len(self.spans)} service span(s) for "
                f"{len(self.events)} event(s); a span starts at every event and only there."
            )
        for index, (event, span) in enumerate(zip(self.events, self.spans)):
            following = self.spans[index + 1].start_year if index + 1 < len(self.spans) else None
            if span.start_year != event.year or span.end_year < span.start_year or (
                following is not None and span.end_year != following
            ):
                raise CostDataError(
                    f"The service spans of subject {self.subject!r} do not align to its events: "
                    f"span {index} runs {span.start_year}..{span.end_year} for an event in year "
                    f"{event.year}. Spans run from an event to the next one, and the last to the "
                    "horizon."
                )

    @property
    def year_zero_investment_in_euro(self) -> float:
        """Year-0 investment of this row — the sort key that puts the biggest asset first."""
        return sum(event.amount_in_euro for event in self.events if event.year == 0)


def component_event_strip(result: LifecycleCostResult) -> List[EventStripRow]:
    """When each component was bought, replaced and written down (V7).

    One row per COMPONENT-kind subject of the scoped timeline, built from the INVESTMENT,
    REPLACEMENT and RESIDUAL_VALUE entries in the BEST_ESTIMATE slot. Service spans are *derived
    from those events* — install or replacement to the next event, last one to the horizon —
    rather than from the database service life, so a component whose booked replacement interval
    disagrees with the catalog is visible instead of being drawn as the catalog claims.

    Rows are sorted by year-0 investment descending, biggest asset first.

    Reconciliation: every event amount is a timeline entry of the named category, so the row
    sums equal the per-subject `npv_by_component` figures before discounting.

    Raises:
        CostDataError: From `EventStripRow`, whose invariants every row built here has to
            satisfy — most visibly the package-A residual gate: a subject carrying a
            residual-value credit without any investment or replacement the timeline charged.
    """
    horizon = result.parameters.observation_period_in_years
    by_subject: Dict[str, List[CashFlowEntry]] = {}
    for entry in result.scoped_timeline().entries:
        if entry.subject_kind != SubjectKind.COMPONENT or not 0 <= entry.year <= horizon:
            continue
        if entry.category in (
            CostCategory.INVESTMENT, CostCategory.REPLACEMENT, CostCategory.RESIDUAL_VALUE
        ):
            by_subject.setdefault(entry.subject, []).append(entry)
    rows: List[EventStripRow] = []
    for subject, entries in by_subject.items():
        events: List[LifecycleEvent] = []
        residual: Optional[LifecycleEvent] = None
        for category, kind in (
            (CostCategory.INVESTMENT, EventKinds.INVESTMENT),
            (CostCategory.REPLACEMENT, EventKinds.REPLACEMENT),
        ):
            years = sorted({entry.year for entry in entries if entry.category == category})
            for year in years:
                amount = sum(
                    entry.amount_in_euro.best_estimate
                    for entry in entries
                    if entry.category == category and entry.year == year
                )
                events.append(LifecycleEvent(year=year, amount_in_euro=amount, kind=kind))
        residual_amount = sum(
            entry.amount_in_euro.best_estimate
            for entry in entries
            if entry.category == CostCategory.RESIDUAL_VALUE
        )
        if residual_amount:
            residual = LifecycleEvent(
                year=horizon, amount_in_euro=residual_amount, kind=EventKinds.RESIDUAL
            )
        events.sort(key=lambda event: event.year)
        spans = [
            ServiceSpan(
                start_year=event.year,
                end_year=events[index + 1].year if index + 1 < len(events) else horizon,
            )
            for index, event in enumerate(events)
        ]
        rows.append(EventStripRow(subject=subject, events=events, spans=spans, residual=residual))
    rows.sort(key=lambda row: row.year_zero_investment_in_euro, reverse=True)
    return rows


# ----- 9/9 views, V8-V15 -----

# ============================================================================ V8 treemap

class TileBasis(str, enum.Enum):
    """The two ways a treemap can answer "what does this cost" (V8, owner decision Q11).

    A treemap has no negative areas, so credits cannot be drawn — which leaves two honest options
    and no third one. GROSS shows the cost side only and states the excluded credits in the
    caption; NET_OF_CREDITS nets **per subject across display groups** and clamps a subject whose
    credits exceed its costs at zero, disclosing every clamped subject. Both are rendered side by
    side so the two readings can be compared on real evaluations before one is retired.

    Netting per subject rather than per cell is the only netting that changes anything: a wall's
    subsidy is booked in the support group while its investment is booked in the investment
    group, so a per-cell subtraction would find no credit in any cost cell and reproduce the
    gross panel exactly.

    An enum rather than two string constants, because "there is no third option" is the whole
    claim: `cost_structure_tiles` used to route anything that was not `GROSS` to the net branch,
    so a typo drew a net panel under a gross heading and disclosed the wrong thing. The `str`
    mixin keeps the members comparable with the plain strings the renderers were written against,
    and `__str__` keeps a member printed into a label reading as the chart's own word.
    """

    GROSS = "gross"
    NET_OF_CREDITS = "net"

    def __str__(self) -> str:
        """The basis's own word, so a member interpolated into a caption is not "TileBasis.GROSS"."""
        return self.value


class TreemapThresholds:
    """Cut-offs of the cost-structure treemap (V8).

    A tile smaller than a few pixels carries no information and costs a label, so small tiles are
    folded per group. The threshold is relative to the whole treemap's area, and the fold is
    named in the caption, so nothing is capped silently.
    """

    #: Tiles below this share of the total area fold into one "other" tile per display group.
    SMALL_TILE_SHARE = 0.01

    #: Label of the folded tile.
    FOLD_LABEL = "other"


@dataclass(frozen=True)
class TreemapTile:
    """One rectangle of the treemap: a (display group, subject) cell and its area in euros.

    `area_in_euro` is what the rectangle encodes and is always non-negative — the whole point of
    `TileBasis`. `clamped_from_in_euro` is set only on the net-of-credits basis and records the
    negative net value the *subject* would have had (costs minus credits, summed across display
    groups), so the caption can disclose exactly which subjects were clamped and how many euros
    the clamping erased. A clamped tile carries zero area and is never drawn; it exists so the
    disclosure travels with the tiles instead of being recomputed by each renderer.
    """

    group: Any
    subject: str
    area_in_euro: float
    clamped_from_in_euro: Optional[float] = None
    is_fold: bool = False

    def __post_init__(self) -> None:
        """Enforces the two properties the class docstring states, so a renderer can rely on them.

        A treemap rectangle with a negative area is not a rectangle, and a clamped tile that
        carried area or a non-negative `clamped_from_in_euro` would be a disclosure of something
        that did not happen — the field records the *negative* net the subject would have had, and
        the tile itself is not drawn. Both are checked here rather than in the one builder that
        exists today, because the caption arithmetic (`areas − erased == net NPV`) is only sound
        while they hold.

        Raises:
            CostDataError: On a negative area, or on a clamped tile that is drawable or whose
                recorded net is not a credit balance.
        """
        if self.area_in_euro < 0.0:
            raise CostDataError(
                f"Treemap tile {self.subject!r} in group {self.group!r} has an area of "
                f"{self.area_in_euro:,.2f} EUR; a rectangle cannot encode a negative number, "
                "which is the whole reason `TileBasis` exists."
            )
        if self.clamped_from_in_euro is None:
            return
        if self.clamped_from_in_euro > 0.0 or self.area_in_euro != 0.0:
            raise CostDataError(
                f"Treemap tile {self.subject!r} discloses a clamp from "
                f"{self.clamped_from_in_euro:,.2f} EUR with an area of {self.area_in_euro:,.2f} "
                "EUR; a clamped subject is one whose credits reached its costs, so its recorded "
                "net is zero or negative and it carries no area."
            )


@dataclass(frozen=True)
class CostStructureTiles:
    """The treemap's tiles plus everything its caption has to disclose (V8).

    Carries both sides of the picture on purpose: the gross cost NPV the tiles add up to, the
    credit total the gross variant leaves out, and the net NPV the two imply — so a reader can
    check `gross − credits == net` against the headline KPI without leaving the caption. On the
    net basis, `clamped_total_in_euro` and the clamped tiles name the euros the clamp erased, and
    the tile areas minus that erased total reproduce the same net NPV — which is what makes the
    net panel a genuine second reading rather than a redrawn gross panel.
    """

    basis: TileBasis
    tiles: List[TreemapTile]
    gross_cost_npv_in_euro: float
    credit_total_in_euro: float
    net_npv_in_euro: float
    clamped_total_in_euro: float = 0.0
    folded_tile_count: int = 0
    folded_amount_in_euro: float = 0.0

    def clamped_tiles(self) -> List[TreemapTile]:
        """The subjects whose negative net value was clamped to zero — the caption's disclosure.

        These are the subjects whose credits reached or exceeded their costs, so they carry no
        area on the net basis; the renderers name them and the euros erased, because they are
        exactly the entries a reviewer should ask about.
        """
        return [tile for tile in self.tiles if tile.clamped_from_in_euro is not None]


def cost_structure_tiles(
    result: LifecycleCostResult,
    mapping: Mapping[CostCategory, GroupKey],
    basis: TileBasis = TileBasis.GROSS,
) -> CostStructureTiles:
    """Lifetime cost composition as (display group -> subject) tiles, on either basis (V8).

    Pivots the scoped timeline by (subject, display group) in present value, BEST_ESTIMATE slot,
    and splits each cell into its cost and credit halves — the split is by the *sign of the
    contributing entries*, so a subject that both costs and earns (a PV system) keeps the two
    apart instead of being netted into a smaller cost.

    On `TileBasis.GROSS` the tile area is the cost half and the credit half is reported as
    `credit_total_in_euro` for the caption. On `TileBasis.NET_OF_CREDITS` the netting happens
    **per subject, across display groups** — a subject's whole credit total is applied to its
    whole cost total and the resulting shrink factor is spread proportionally over that subject's
    cost cells, so the group nesting survives while the areas actually move. Subjects whose
    credits reach or exceed their costs clamp to zero area and are disclosed with the euros the
    clamp erased. Tiles below `TreemapThresholds.SMALL_TILE_SHARE` are folded into one "other"
    tile per group.

    Reconciliation, validated here on both bases: `gross − credits == net_npv_in_euro ==
    result.total_npv_in_euro.best_estimate`; the gross tile areas sum to `gross_cost_npv_in_euro`
    (fold included); and the net tile areas minus `clamped_total_in_euro` reproduce that same
    net NPV.

    Raises:
        CostDataError: If `basis` is not a `TileBasis`, if the tiles do not sum to the stated
            gross, if the net areas net of the disclosed erasure do not reproduce the net NPV, or
            if gross minus credits does not reproduce the published net NPV.
    """
    if not isinstance(basis, TileBasis):
        raise CostDataError(
            f"Unknown treemap basis {basis!r}: the cost structure is drawn either as "
            f"{TileBasis.GROSS.value!r} or as {TileBasis.NET_OF_CREDITS.value!r}. An unrecognised "
            "value used to fall through to the net branch, which drew a net panel under whatever "
            "heading the caller had in mind and disclosed the wrong thing."
        )

    def cell_of(item: CashFlowEntry) -> Tuple[Any, str]:
        """The (display group, subject) cell one entry belongs in."""
        return (_display_group_of(item.category, mapping), item.subject)

    cost_cells, credit_cells = result.scoped_timeline().npv_split_by(
        result.parameters.interest_rate, cell_of
    )
    gross_total = sum(cost_cells.values())
    credit_total = sum(credit_cells.values())
    net_total = result.total_npv_in_euro.best_estimate
    if abs(gross_total - credit_total - net_total) > ViewTolerances.RECONCILIATION_EPSILON:
        raise CostDataError(
            f"Cost-structure tiles do not reconcile with the published NPV: gross {gross_total:,.2f} "
            f"− credits {credit_total:,.2f} = {gross_total - credit_total:,.2f} EUR, but the "
            f"perspective's NPV is {net_total:,.2f} EUR."
        )
    if basis == TileBasis.GROSS:
        raw: Dict[Tuple[Any, str], float] = dict(cost_cells)
        clamped_tiles: List[TreemapTile] = []
    else:
        raw, clamped_tiles = _net_cells_per_subject(cost_cells, credit_cells)
    tiles, folded_count, folded_amount = _fold_small_tiles(raw)
    tiles.extend(clamped_tiles)
    tiled_area = sum(tile.area_in_euro for tile in tiles)
    clamped_total = -sum(tile.clamped_from_in_euro or 0.0 for tile in clamped_tiles)
    if basis == TileBasis.GROSS and abs(tiled_area - gross_total) > ViewTolerances.RECONCILIATION_EPSILON:
        raise CostDataError(
            f"Cost-structure tiles sum to {tiled_area:,.2f} EUR but the gross cost NPV is "
            f"{gross_total:,.2f} EUR; the fold must preserve the total exactly."
        )
    if basis != TileBasis.GROSS and abs(tiled_area - clamped_total - net_total) > (
        ViewTolerances.RECONCILIATION_EPSILON
    ):
        raise CostDataError(
            f"Net-of-credits tiles sum to {tiled_area:,.2f} EUR and disclose {clamped_total:,.2f} "
            f"EUR erased by clamping, which nets to {tiled_area - clamped_total:,.2f} EUR, but the "
            f"perspective's net NPV is {net_total:,.2f} EUR; per-subject netting must be exact."
        )
    return CostStructureTiles(
        basis=basis,
        tiles=tiles,
        gross_cost_npv_in_euro=gross_total,
        credit_total_in_euro=credit_total,
        net_npv_in_euro=net_total,
        clamped_total_in_euro=clamped_total,
        folded_tile_count=folded_count,
        folded_amount_in_euro=folded_amount,
    )


def _net_cells_per_subject(
    cost_cells: Mapping[Tuple[Any, str], float], credit_cells: Mapping[Tuple[Any, str], float]
) -> Tuple[Dict[Tuple[Any, str], float], List[TreemapTile]]:
    """Applies each subject's credits to that subject's cost cells, across display groups.

    A subject's costs and its credits almost never share a cell — an insulation measure's
    investment is booked in the investment group while its subsidy is booked in the support
    group — so netting cell by cell would subtract nothing anywhere and the net panel would be a
    copy of the gross one. Netting per subject fixes that: the subject's shrink factor
    `max(0, C − K) / C` scales every one of its cost cells proportionally, which keeps the
    two-level group nesting intact while the areas genuinely move.

    Subjects whose credits reach or exceed their costs (a pure subsidy line, a PV system that
    earns more than it cost) cannot be drawn at all; they come back as zero-area disclosure tiles
    carrying the negative net `C − K`, filed under the display group of their largest cell.

    Returns:
        The per-cell net areas, and the disclosure tiles for the clamped subjects.
    """
    subject_costs: Dict[str, float] = {}
    subject_credits: Dict[str, float] = {}
    for (_, subject), amount in cost_cells.items():
        subject_costs[subject] = subject_costs.get(subject, 0.0) + amount
    for (_, subject), amount in credit_cells.items():
        subject_credits[subject] = subject_credits.get(subject, 0.0) + amount
    areas: Dict[Tuple[Any, str], float] = {}
    clamped_tiles: List[TreemapTile] = []
    for subject in sorted(set(subject_costs) | set(subject_credits)):
        cost_total = subject_costs.get(subject, 0.0)
        credit_total = subject_credits.get(subject, 0.0)
        if credit_total >= cost_total:
            if cost_total <= 0.0 and credit_total <= 0.0:
                continue
            clamped_tiles.append(
                TreemapTile(
                    group=_dominant_group(subject, cost_cells, credit_cells),
                    subject=subject,
                    area_in_euro=0.0,
                    clamped_from_in_euro=cost_total - credit_total,
                )
            )
            continue
        factor = (cost_total - credit_total) / cost_total
        for (group, cell_subject), amount in cost_cells.items():
            if cell_subject == subject and amount:
                areas[(group, subject)] = amount * factor
    clamped_tiles.sort(key=lambda tile: tile.clamped_from_in_euro or 0.0)
    return areas, clamped_tiles


def _dominant_group(
    subject: str,
    cost_cells: Mapping[Tuple[Any, str], float],
    credit_cells: Mapping[Tuple[Any, str], float],
) -> Any:
    """The display group a clamped subject is filed under: the group of its largest cell.

    A clamped subject has no area, but it still needs a group so the disclosure can be coloured
    and grouped like everything else. Cost cells win over credit cells, because a subject that
    had costs belongs where the money was spent; a credit-only subject falls back to the group of
    its largest credit, which for a subsidy line is the support group.
    """
    for cells in (cost_cells, credit_cells):
        candidates = [(amount, group) for (group, cell_subject), amount in cells.items() if cell_subject == subject]
        if candidates:
            return max(candidates, key=lambda pair: pair[0])[1]
    raise CostDataError(f"Cannot place treemap subject {subject!r}: it has neither a cost nor a credit cell.")


def _fold_small_tiles(areas: Mapping[Tuple[Any, str], float]) -> Tuple[List[TreemapTile], int, float]:
    """Turns (group, subject) -> area into tiles, folding the small ones per group.

    The fold key is the display group rather than nothing, so that a group never disappears
    entirely: its small subjects collapse into one "other" tile that keeps the group's own total
    exact, which is what lets the sum invariant survive the readability cut. The *threshold* is
    global — it is a share of the whole treemap, which is the area a reader's eye compares
    against — and that is exactly what `_fold_small` measures, so the algorithm is the shared one
    and only the ordering is decided here. Only drawable (positive) areas reach this function;
    the net basis's zero-area disclosure tiles are appended by the caller so they are never
    folded away.
    """
    tiles, folded_count, folded_amount = _fold_small(
        [TreemapTile(group=group, subject=subject, area_in_euro=area)
         for (group, subject), area in areas.items()],
        lambda tile: tile.area_in_euro,
        TreemapThresholds.SMALL_TILE_SHARE,
        lambda tile: tile.group,
        lambda group, area: TreemapTile(
            group=group, subject=TreemapThresholds.FOLD_LABEL, area_in_euro=area, is_fold=True
        ),
    )
    tiles.sort(key=lambda tile: (-tile.area_in_euro, str(tile.subject)))
    return tiles, folded_count, folded_amount


# ============================================================================ V9 swimlane

@dataclass(frozen=True)
class LaneEvent:
    """One dated marker on a swimlane: a disbursement, a payout, a milestone (V9).

    `amount_in_euro` is optional because not every milestone has one — "loan-free" is a year, not
    a sum — and the renderer prints the amount only where it exists rather than showing a zero
    that would read as a real figure.
    """

    year: int
    label: str
    amount_in_euro: Optional[float] = None


@dataclass(frozen=True)
class LaneSpan:
    """One interval on a swimlane: a repayment period, a levy period, a payback range (V9).

    `end_year` is None for an open-ended span — the payback range whose HIGH world never crosses
    zero inside the horizon — which the renderer draws with an open arrow and the caption states
    in words. Collapsing that case to "pays back at T" would be exactly the false precision the
    range bar exists to avoid.
    """

    start_year: int
    end_year: Optional[int]
    label: str


@dataclass(frozen=True)
class Lane:
    """One labelled swimlane: its spans and its markers (V9).

    Frozen, and meant as a finished object: `lifecycle_lanes` collects a lane's markers and spans
    into plain lists first and constructs the lane once from them. Appending to `events` after
    construction would have worked — a frozen dataclass freezes the *bindings*, not the lists they
    point at — but it makes the freeze a decoration rather than a guarantee, and a lane that is
    still being filled after it exists is exactly the state `is_empty` cannot answer for.
    """

    name: str
    events: List[LaneEvent] = field(default_factory=list)
    spans: List[LaneSpan] = field(default_factory=list)

    def is_empty(self) -> bool:
        """True when the lane has nothing to draw, so the renderer can drop it and log the skip."""
        return not self.events and not self.spans


@dataclass(frozen=True)
class LifecycleLanes:
    """The one-page life of the renovation: assets, financing, support and milestones (V9).

    A *composition*, not a computation: every lane restates a figure that exists in full
    elsewhere — the asset rows are the component event strip's, the financing lane reads the
    amortization series, the milestones read the band crossings — which is what makes the
    overview safe. It introduces no new numbers and no new fields, only a shared year axis.
    """

    horizon: int
    milestones: Lane
    assets: List[EventStripRow]
    financing: Lane
    support: Lane


def lifecycle_lanes(
    result: LifecycleCostResult, comparison: Optional[VariantComparison] = None
) -> LifecycleLanes:
    """Assets, financing, support and milestones on one year axis (V9).

    Delegates rather than re-derives: the component event strip supplies the asset rows,
    `loan_amortization_series` the financing lane (disbursement, the years carrying debt service,
    and the year the outstanding balance reaches zero), `subsidy_decisions` and the
    MODERNIZATION_LEVY entries the support lane, and the band crossings plus the worst liquidity
    position the milestones. Because the numbers come from those views, the swimlane cannot
    disagree with the detail charts it summarizes.

    Args:
        result: The perspective to draw.
        comparison: The variant comparison, if one exists. The payback milestone is a *range* between the
            LOW-world and HIGH-world crossings of its savings curve, so without a comparison
            there is no payback question to answer and the milestone is simply absent.

    Returns:
        The four lane groups. Empty lanes are returned empty rather than omitted, so the renderer
        can name every skip in its log line instead of silently drawing fewer lanes.
    """
    horizon = result.parameters.observation_period_in_years
    assets = component_event_strip(result)
    amortization = loan_amortization_series(result)
    financing_events: List[LaneEvent] = []
    financing_spans: List[LaneSpan] = []
    if amortization.has_flows():
        service_years = [
            year
            for year, (interest, principal) in enumerate(
                zip(amortization.interest_in_euro, amortization.principal_in_euro)
            )
            if interest or principal
        ]
        financing_events.append(
            LaneEvent(year=0, label="loan disbursement", amount_in_euro=amortization.disbursement_in_euro)
        )
        if service_years:
            financing_spans.append(
                LaneSpan(start_year=service_years[0], end_year=service_years[-1], label="debt service")
            )
            peak_year = max(
                service_years,
                key=lambda year: amortization.interest_in_euro[year] + amortization.principal_in_euro[year],
            )
            peak_amount = (
                amortization.interest_in_euro[peak_year] + amortization.principal_in_euro[peak_year]
            )
            financing_events.append(
                LaneEvent(year=peak_year, label="largest annual debt service", amount_in_euro=peak_amount)
            )
        loan_free = amortization.loan_free_year()
        if loan_free is not None:
            financing_events.append(LaneEvent(year=loan_free, label="loan-free"))
    financing = Lane(name="Financing", events=financing_events, spans=financing_spans)
    support_events: List[LaneEvent] = []
    support_spans: List[LaneSpan] = []
    for decision in result.subsidy_decisions:
        for award in decision.applied:
            amount = award_total_amount(award).best_estimate
            if amount:
                support_events.append(
                    LaneEvent(
                        year=0,
                        label=f"{award.scheme_id} ({decision.measure_subject})",
                        amount_in_euro=amount,
                    )
                )
    levy_years = sorted(
        {
            entry.year
            for entry in result.scoped_timeline().entries
            if entry.category == CostCategory.MODERNIZATION_LEVY and 0 <= entry.year <= horizon
        }
    )
    if levy_years:
        annual = sum(
            entry.amount_in_euro.best_estimate
            for entry in result.scoped_timeline().entries
            if entry.category == CostCategory.MODERNIZATION_LEVY and entry.year == levy_years[0]
        )
        direction = "paid" if annual > 0 else "received"
        support_spans.append(
            LaneSpan(
                start_year=levy_years[0],
                end_year=levy_years[-1],
                label=f"modernization levy {direction} ({abs(annual):,.0f} EUR/a)",
            )
        )
    support = Lane(name="Subsidies & levies", events=support_events, spans=support_spans)
    milestone_events: List[LaneEvent] = []
    milestone_spans: List[LaneSpan] = []
    worst_year, worst_amount = worst_liquidity_position(result)
    milestone_events.append(
        LaneEvent(year=worst_year, label="deepest out-of-pocket", amount_in_euro=worst_amount)
    )
    residual_total = sum(
        entry.amount_in_euro.best_estimate
        for entry in result.scoped_timeline().entries
        if entry.category == CostCategory.RESIDUAL_VALUE
    )
    milestone_events.append(
        LaneEvent(
            year=horizon,
            label="observation horizon",
            amount_in_euro=residual_total if residual_total else None,
        )
    )
    if comparison is not None:
        crossings = band_zero_crossings(comparison.cumulative_discounted_savings_in_euro)
        first = crossings.get("low")
        last = crossings.get("high")
        if first is not None:
            milestone_spans.append(
                LaneSpan(
                    start_year=first,
                    end_year=last,
                    label="payback range (LOW to HIGH world)" if last is not None
                    else "payback range (no payback in the HIGH world)",
                )
            )
    milestones = Lane(name="Milestones", events=milestone_events, spans=milestone_spans)
    return LifecycleLanes(
        horizon=horizon, milestones=milestones, assets=assets, financing=financing, support=support
    )


# ============================================================================ V10 sources & uses

@dataclass(frozen=True)
class FundingNode:
    """One node of the sources-and-uses statement: a label and an amount in euros (V10).

    Amounts are positive on both sides — a source and a use of the same 10,000 EUR are the same
    number seen from two ends — so the double-entry property is a plain equality of the two
    column totals rather than a sign convention the reader has to hold in their head.
    `category` is carried where one exists, so the renderer can hue the node like every other
    mark of the same money.
    """

    label: str
    amount_in_euro: float
    category: Optional[CostCategory] = None
    #: The use this source is tied to, when the booking names one — a subsidy scheme is awarded
    #: for a specific measure, and drawing it into an unrelated one would be a picture of a
    #: funding structure that does not exist. None for the untied sources (own capital, a loan
    #: taken against the investment as a whole).
    subject: Optional[str] = None
    #: The raw subsidy scheme id behind a support node, when there is one (Q20). `label` carries
    #: the friendly name a reader sees; this is what a reviewer greps the catalog with, and the
    #: renderers put it in the node's tooltip.
    scheme_id: Optional[str] = None


@dataclass(frozen=True)
class SourcesAndUses:
    """Where the year-0 money comes from and what it buys (V10).

    The project-finance statement ("Mittelherkunft und Mittelverwendung") for year 0 in the
    BEST_ESTIMATE slot: subsidy schemes, loan disbursements and own capital on the left; the
    gross investment per subject plus planning and removal on the right.
    `funding_sources_and_uses` validates that the two sides balance, which is what makes this a
    statement rather than a picture.
    """

    sources: List[FundingNode]
    uses: List[FundingNode]
    gross_year_zero_investment_in_euro: float

    def total_sources_in_euro(self) -> float:
        """Sum of the left column — equal to the uses total by construction."""
        return sum(node.amount_in_euro for node in self.sources)

    def total_uses_in_euro(self) -> float:
        """Sum of the right column."""
        return sum(node.amount_in_euro for node in self.uses)

    def has_external_funding(self) -> bool:
        """True when anything but own capital funds year 0 — the chart's skip condition."""
        return any(node.category is not None for node in self.sources)

    def ribbons(self) -> List[Tuple[str, str, float]]:
        """(source label, use label, euros) triples whose widths tile both columns exactly.

        The allocation the Sankey draws, decided here rather than in the renderer because it is a
        statement about the money and not about geometry. Two passes: a source that names a
        subject (a subsidy scheme awarded for one measure) fills that use first, up to what the
        use still needs; whatever is left — own capital, the loan, an over-award — is spread over
        the remaining capacity in proportion to it, since those sources genuinely are untied.

        Both column totals are preserved exactly, which is what keeps the double-entry property
        the view validated visible in the drawing.
        """
        capacity = {node.label: node.amount_in_euro for node in self.uses}
        pairs: List[Tuple[str, str, float]] = []
        remaining_source = {node.label: node.amount_in_euro for node in self.sources}
        for node in self.sources:
            if node.subject is None or node.subject not in capacity:
                continue
            amount = min(node.amount_in_euro, capacity[node.subject])
            if amount > 0:
                pairs.append((node.label, node.subject, amount))
                capacity[node.subject] -= amount
                remaining_source[node.label] -= amount
        open_capacity = sum(capacity.values())
        for node in self.sources:
            left = remaining_source[node.label]
            if left <= 0 or open_capacity <= 0:
                continue
            for use_label, still_needed in capacity.items():
                if still_needed <= 0:
                    continue
                pairs.append((node.label, use_label, left * still_needed / open_capacity))
        return pairs


def funding_sources_and_uses(result: LifecycleCostResult) -> SourcesAndUses:
    """Year-0 funding sources against year-0 uses, balanced to the euro (V10).

    Sources are one node per subsidy scheme (labelled with the display name the scheme carries,
    which is where the `subsidy_scheme_id` dimension earns its keep — "state -> KfW 261 -> heat
    pump" reads very differently from one grey "subsidies" node), **one** node carrying every
    year-0 loan disbursement together, and own capital as the balancing item. Debt is one node
    rather than one per disbursement because a loan is not tied to a measure the way an award is:
    the timeline records no subject for it that the Sankey could draw a ribbon to, so splitting it
    would produce several identically untied nodes that the allocation would then spread the same
    way. Uses are the gross year-0 investment per subject plus the planning and removal categories
    as their own nodes.

    A *negative* balancing item — support plus debt exceeding the gross investment — is a data
    defect rather than a rendering case, and raises.

    Reconciliation: sources total == uses total == gross year-0 investment, validated here and
    restated in the caption.

    Raises:
        CostDataError: If own capital comes out negative, or if the two columns do not balance.
    """
    scoped = [entry for entry in result.scoped_timeline().entries if entry.year == 0]
    uses: List[FundingNode] = []
    for subject in result.component_breakdowns:
        amount = sum(
            entry.amount_in_euro.best_estimate
            for entry in scoped
            if entry.subject == subject and entry.category == CostCategory.INVESTMENT
        )
        if amount:
            uses.append(FundingNode(label=subject, amount_in_euro=amount, category=CostCategory.INVESTMENT))
    for category, label in ((CostCategory.PLANNING, "planning"), (CostCategory.REMOVAL, "removal")):
        amount = sum(entry.amount_in_euro.best_estimate for entry in scoped if entry.category == category)
        if amount:
            uses.append(FundingNode(label=label, amount_in_euro=amount, category=category))
    gross = sum(node.amount_in_euro for node in uses)
    sources: List[FundingNode] = []
    names = scheme_display_names(result)
    by_scheme: Dict[str, float] = {}
    scheme_id_by_label: Dict[str, Optional[str]] = {}
    subject_by_scheme: Dict[str, Optional[str]] = {}
    for entry in scoped:
        if entry.category == CostCategory.SUBSIDY:
            scheme_id = entry.subsidy_scheme_id
            label = names.get(scheme_id or "", scheme_id or SubsidySchemeLabels.UNATTRIBUTED)
            by_scheme[label] = by_scheme.get(label, 0.0) - entry.amount_in_euro.best_estimate
            scheme_id_by_label[label] = scheme_id
            if label in subject_by_scheme and subject_by_scheme[label] != entry.subject:
                subject_by_scheme[label] = None  # awarded across measures: untie it
            else:
                subject_by_scheme[label] = entry.subject
    for label, amount in by_scheme.items():
        if amount:
            sources.append(
                FundingNode(
                    label=label,
                    amount_in_euro=amount,
                    category=CostCategory.SUBSIDY,
                    subject=subject_by_scheme.get(label),
                    scheme_id=scheme_id_by_label.get(label),
                )
            )
    loans = -sum(
        entry.amount_in_euro.best_estimate
        for entry in scoped
        if entry.category == CostCategory.LOAN_DISBURSEMENT
    )
    if loans:
        sources.append(FundingNode(label="loan", amount_in_euro=loans, category=CostCategory.LOAN_DISBURSEMENT))
    own_capital = gross - sum(node.amount_in_euro for node in sources)
    if own_capital < -ViewTolerances.RECONCILIATION_EPSILON:
        raise CostDataError(
            f"Year-0 funding sources exceed the gross investment by {-own_capital:,.2f} EUR "
            f"(gross {gross:,.2f} EUR, support and debt "
            f"{sum(node.amount_in_euro for node in sources):,.2f} EUR); own capital cannot be "
            "negative, so this is a data defect rather than a fully funded project."
        )
    sources.append(FundingNode(label="own capital", amount_in_euro=max(own_capital, 0.0)))
    statement = SourcesAndUses(sources=sources, uses=uses, gross_year_zero_investment_in_euro=gross)
    if (
        abs(statement.total_sources_in_euro() - statement.total_uses_in_euro())
        > ViewTolerances.RECONCILIATION_EPSILON
    ):
        raise CostDataError(
            f"Sources and uses do not balance: {statement.total_sources_in_euro():,.2f} EUR of "
            f"funding against {statement.total_uses_in_euro():,.2f} EUR of uses."
        )
    return statement


# ============================================================================ V11 subject flows

@dataclass(frozen=True)
class SubjectGroupFlow:
    """One ribbon from a subject to a cost group, in present value (V11).

    Cost and credit ribbons are separate records rather than one signed number: a Sankey ribbon
    has no sign, so a PV system's investment and its feed-in revenue are two ribbons of the same
    subject, one solid and one hatched, and nothing is netted. `amount_in_euro` is therefore
    always positive and `is_credit` says which side of the divider the ribbon belongs on.
    """

    subject: str
    group: Any
    amount_in_euro: float
    is_credit: bool


def subject_category_flows(
    result: LifecycleCostResult, mapping: Mapping[CostCategory, GroupKey]
) -> List[SubjectGroupFlow]:
    """What each subject causes, split by cost group and by sign (V11).

    A subject × display-group pivot of the scoped timeline in present value (BEST_ESTIMATE slot),
    with cost and credit contributions kept apart at entry level, so both margins of the pivot
    reconcile against tested result fields: a subject's cost ribbons sum to its gross present
    cost, cost minus credit is its `npv_by_component` entry, and a group's ribbons sum to the
    folded `npv_by_category`.

    Reconciliation is by construction (the same discounted entries, partitioned two ways) rather
    than by a check, because every ribbon here *is* one bucket of `npv_by` — literally so: the
    pivot is `CashFlowTimeline.npv_split_by`, the same one the treemap builds its cells with, so
    the two charts cannot disagree about what a subject cost.

    Raises:
        CostDataError: If `mapping` declares no display group for a category on the timeline.
    """

    def cell_of(item: CashFlowEntry) -> Tuple[str, Any]:
        """The (subject, display group) cell one entry belongs in."""
        return (item.subject, _display_group_of(item.category, mapping))

    cost_cells, credit_cells = result.scoped_timeline().npv_split_by(
        result.parameters.interest_rate, cell_of
    )
    return [
        SubjectGroupFlow(subject=subject, group=group, amount_in_euro=amount, is_credit=is_credit)
        for cells, is_credit in ((cost_cells, False), (credit_cells, True))
        for (subject, group), amount in cells.items()
        if amount > ViewTolerances.RECONCILIATION_EPSILON
    ]


@dataclass(frozen=True)
class SubjectFlowMargins:
    """The two margins of the V11 pivot, as the node labels and the caption print them (Q28 R6).

    The cost-shapes Sankey drew ribbons with no amounts anywhere, so a node's extent — its costs
    *plus* the magnitude of its credits, stacked and never netted — was a quantity no table in the
    report publishes and no reader could reproduce. These are the sums behind that geometry: per
    subject the solid and the dashed side separately, per group node its signed total. They live
    on the view side because presentation may format numbers but may not derive them (seam 4), and
    because the node label, the tooltip and the caption must all print the same figure.

    `net_of` is the difference, i.e. what the component breakdown publishes as the subject's NPV;
    the caption states it beside the extent so the two numbers a reader can find in a table (net)
    and on the chart (extent) are visibly the same data read two ways.
    """

    costs_by_subject: Dict[str, float]
    credits_by_subject: Dict[str, float]
    signed_total_by_group: Dict[Tuple[Any, bool], float]

    def extent_of(self, subject: str) -> float:
        """The height the node is drawn at: costs and credits stacked, nothing netted."""
        return self.costs_by_subject.get(subject, 0.0) + self.credits_by_subject.get(subject, 0.0)

    def net_of(self, subject: str) -> float:
        """Costs minus credits — the subject's net present value, as the breakdown table prints it."""
        return self.costs_by_subject.get(subject, 0.0) - self.credits_by_subject.get(subject, 0.0)

    def widest_subject(self) -> Optional[str]:
        """The subject with the largest node, i.e. the one the caption uses as its worked example."""
        if not self.costs_by_subject and not self.credits_by_subject:
            return None
        subjects = set(self.costs_by_subject) | set(self.credits_by_subject)
        return max(sorted(subjects), key=self.extent_of)


def subject_flow_margins(flows: Sequence[SubjectGroupFlow]) -> SubjectFlowMargins:
    """Per-node sums of the V11 ribbons: the numbers the Sankey's labels state (Q28 R6).

    A pure re-aggregation of the ribbons the chart already draws, so a label can never disagree
    with the geometry beside it: the same list is summed by subject (split by side) and by group
    node (signed, credit groups negative), and nothing else is consulted.

    Args:
        flows: The ribbons from `subject_category_flows`, in any order.

    Returns:
        The margins; subjects with only credits appear in `credits_by_subject` alone, and
        `costs_by_subject.get(subject, 0.0)` is the honest zero for them.
    """
    cost_totals: Dict[str, float] = {}
    credit_totals: Dict[str, float] = {}
    by_group: Dict[Tuple[Any, bool], float] = {}
    for flow in flows:
        side = credit_totals if flow.is_credit else cost_totals
        side[flow.subject] = side.get(flow.subject, 0.0) + flow.amount_in_euro
        key = (flow.group, flow.is_credit)
        signed = -flow.amount_in_euro if flow.is_credit else flow.amount_in_euro
        by_group[key] = by_group.get(key, 0.0) + signed
    return SubjectFlowMargins(
        costs_by_subject=cost_totals,
        credits_by_subject=credit_totals,
        signed_total_by_group=by_group,
    )


# ============================================================================ V12 energy balance

class EnergyBalanceLayout:
    """The node vocabulary and the tolerances of the household energy balance (V12).

    One documented home for what the balance is made of, so neither the view nor a renderer has to
    decide it. The structure is the busbar every PV-monitoring dashboard draws: sources on the
    left, the house's electricity bus in the middle, sinks on the right — and the battery on both
    sides, discharging into the bus and charging out of it, which is what a pass-through *is* when
    only its two terminals are measured. Attributing the charge to a particular source or the
    discharge to a particular load would be an allocation the simulation never made, so it is not
    drawn.

    `MINIMUM_DEVICE_FLOWS` is the skip threshold of the Q16 decision: the two grid roles come from
    the meter, which every run has, so a balance that carries nothing else has no devices in it
    and must skip rather than draw a picture of a meter talking to itself.
    """

    SOURCE_ROLES = (
        EnergyFlowRole.PV_GENERATION,
        EnergyFlowRole.GRID_IMPORT,
        EnergyFlowRole.BATTERY_DISCHARGE,
    )
    SINK_ROLES = (
        EnergyFlowRole.HEAT_PUMP_ELECTRICITY,
        EnergyFlowRole.HOUSEHOLD_ELECTRICITY,
        EnergyFlowRole.GRID_EXPORT,
        EnergyFlowRole.BATTERY_CHARGE,
    )
    #: The roles the meter contributes; they do not count towards `MINIMUM_DEVICE_FLOWS`.
    METER_ROLES = (EnergyFlowRole.GRID_IMPORT, EnergyFlowRole.GRID_EXPORT)
    #: Roles that are consumption in the self-sufficiency sense — what the house actually used.
    CONSUMPTION_ROLES = (EnergyFlowRole.HEAT_PUMP_ELECTRICITY, EnergyFlowRole.HOUSEHOLD_ELECTRICITY)
    MINIMUM_DEVICE_FLOWS = 2

    LABELS = {
        EnergyFlowRole.PV_GENERATION: "PV generation",
        EnergyFlowRole.GRID_IMPORT: "grid import",
        EnergyFlowRole.BATTERY_DISCHARGE: "battery discharge",
        EnergyFlowRole.HEAT_PUMP_ELECTRICITY: "heat pump",
        EnergyFlowRole.HOUSEHOLD_ELECTRICITY: "household",
        EnergyFlowRole.GRID_EXPORT: "grid export",
        EnergyFlowRole.BATTERY_CHARGE: "battery charge",
    }
    BUS_LABEL = "house electricity"
    #: Where an imbalance between the two sides is booked. It is a node, not a rounding: losses
    #: and unattributed loads are real energy, and hiding them would make the balance close by
    #: construction and therefore prove nothing.
    RESIDUAL_LABEL = "losses / unattributed"
    #: An imbalance below this share of the larger side is float noise rather than a missing flow.
    BALANCE_RELATIVE_EPSILON = 1e-9


@dataclass(frozen=True)
class EnergyBalanceNode:
    """One terminal of the household energy balance: a quantity, a label and its money annotation.

    The unit is kWh per year throughout — the whole point of the Q16 redesign is that the diagram
    never changes unit mid-flight. `annotation_in_euro` is what that quantity *costs or earns* in
    year 1, filled only for the two nodes that cross a billing boundary (grid import, grid export)
    and None everywhere else, because no bill exists for electricity that never leaves the house.
    """

    role: Optional[EnergyFlowRole]
    label: str
    quantity_in_kwh: float
    annotation_in_euro: Optional[float] = None


@dataclass(frozen=True)
class EnergyBalanceFlows:
    """The year-1 household electricity balance: sources, a bus and sinks (V12).

    Everything the energy-balance Sankey draws and every number its caption states, in kWh. The
    two lists are the terminals; the bus in the middle carries `bus_total_in_kwh`, which is both
    the sum of the sources and the sum of the sinks — conservation holds at every node by
    construction because the imbalance between the drawn terminals is booked as an explicit
    `losses / unattributed` terminal rather than absorbed silently.

    `self_consumption_share` is the share of the PV generation that did not leave the house, and
    `self_sufficiency_share` the share of the house's own consumption that did not come from the
    grid; both are None when their denominator is zero (no PV, or no attributed consumption)
    rather than being reported as a misleading zero. `battery_round_trip_loss_in_kwh` is the
    difference between what went into the battery and what came back out — the loss the caption
    names, so that the battery reading as a lossy pass-through is stated rather than inferred. It
    is clamped at zero: a year in which the battery discharged more than it charged is a year that
    began with carried-over charge, and the surplus is energy stored *before* the measured year
    rather than energy the battery created. Reporting that as a negative loss would invite the
    reading "the battery gained energy", which is the one thing it certainly did not do.

    `unattributed_roles_in_kwh` is the honest remainder: role names the record carried that this
    reader's `EnergyFlowRole` vocabulary does not contain. They have no side of the bus, so they
    cannot become a terminal without inventing a direction — but they are not dropped either, and
    a caption that lists them tells a reader exactly how much energy the diagram is not showing.
    """

    sources: List[EnergyBalanceNode]
    sinks: List[EnergyBalanceNode]
    bus_total_in_kwh: float
    self_consumption_share: Optional[float]
    self_sufficiency_share: Optional[float]
    battery_round_trip_loss_in_kwh: Optional[float]
    #: Role name -> annual kWh for every attribution role the balance vocabulary does not know.
    unattributed_roles_in_kwh: Dict[str, float] = field(default_factory=dict)


def _read_energy_attribution(
    result: LifecycleCostResult,
) -> Tuple[Dict[EnergyFlowRole, float], Dict[str, float]]:
    """One walk of the attribution record, returning the placeable roles and the rest.

    The record is read twice by the balance — once for what can be drawn, once for what cannot —
    and the two readings are the same parse of the same dictionary with the two branches of one
    `try` swapped. Walking it once and returning both halves is what keeps them exhaustive and
    disjoint by construction: no role can be absent from both because a second loop was written
    with a slightly different condition. The two public functions below are the two projections
    of this one pass and keep their own names, because a caller asking "what can I draw" should
    not have to unpack a pair.

    Args:
        result: The evaluated perspective.

    Returns:
        `(placeable, unplaceable)` — role -> annual kWh for the roles this reader's
        `EnergyFlowRole` knows, and role *name* -> annual kWh for the rest. Zero-quantity roles
        are dropped from both, since a terminal carrying nothing is not a flow.
    """
    totals: Dict[EnergyFlowRole, float] = {}
    unknown: Dict[str, float] = {}
    for by_role in result.annual_energy_attribution_by_subject_in_kwh.values():
        for role_name, quantity in by_role.items():
            try:
                role = EnergyFlowRole(role_name)
            except ValueError:
                unknown[role_name] = unknown.get(role_name, 0.0) + quantity
                continue
            totals[role] = totals.get(role, 0.0) + quantity
    return (
        {role: value for role, value in totals.items() if value},
        {name: value for name, value in unknown.items() if value},
    )


def energy_balance_quantities(result: LifecycleCostResult) -> Dict[EnergyFlowRole, float]:
    """The result's per-subject energy record collapsed onto the balance roles, in annual kWh.

    The one place the subject dimension is dropped: the balance is a picture of the *house*, so
    two PV arrays are one PV generation node. Only role names this reader's `EnergyFlowRole`
    knows appear here — a record written by a newer extraction can carry others, and those are
    *not* silently absorbed by the residual node, which is computed from the drawn terminals
    alone. `_unattributed_energy_roles_in_kwh` is the other half of the same pass and collects
    them instead, and `energy_balance_flows` carries them out on
    `EnergyBalanceFlows.unattributed_roles_in_kwh`, so an unreadable role shrinks the diagram
    visibly rather than invisibly.
    """
    return _read_energy_attribution(result)[0]


def _unattributed_energy_roles_in_kwh(result: LifecycleCostResult) -> Dict[str, float]:
    """Every attribution role name the balance vocabulary cannot place, with its annual kWh.

    The complement of `energy_balance_quantities`, and the other projection of
    `_read_energy_attribution`. A role this reader does not know has no side of the busbar —
    drawing it as a source or as a sink would be a guess about direction that the stored record
    does not support — so it cannot become a terminal. What it must not do is disappear: it is
    real energy, and a balance that quietly drops it looks exactly like a balance that never had
    it.
    """
    return _read_energy_attribution(result)[1]


def has_energy_balance(result: LifecycleCostResult) -> bool:
    """Whether the result carries enough device flows for the household energy balance.

    The skip predicate of decision Q16, and deliberately stricter than "is the field non-empty":
    a result whose only flows are the meter's own grid import and export carries no *device*
    information at all, and drawing a two-node diagram of the meter feeding itself was exactly the
    content-free stub the redesign retired. `MINIMUM_DEVICE_FLOWS` device flows is the floor.
    Roles the vocabulary cannot place do not count: they are never drawn, so they cannot make a
    diagram worth drawing.

    The count alone is not enough, and the docstring promised more than the code checked: a
    busbar needs a side to come from and a side to go to, so the record must also place at least
    one `EnergyBalanceLayout.SOURCE_ROLES` role and at least one `SINK_ROLES` role. Two device
    flows that are both sinks (a heat pump and a household load with no generation and no import)
    draw a bus fed by nothing, whose entire content is then the residual terminal — the same
    content-free picture the device floor exists to refuse, arrived at from the other direction.
    The meter's own roles count towards *this* half of the test, because an all-electric house
    genuinely sourced from the grid is a balance worth drawing.
    """
    quantities = energy_balance_quantities(result)
    devices = [role for role in quantities if role not in EnergyBalanceLayout.METER_ROLES]
    if len(devices) < EnergyBalanceLayout.MINIMUM_DEVICE_FLOWS:
        return False
    return any(role in EnergyBalanceLayout.SOURCE_ROLES for role in quantities) and any(
        role in EnergyBalanceLayout.SINK_ROLES for role in quantities
    )


def energy_balance_flows(result: LifecycleCostResult) -> EnergyBalanceFlows:
    """Where the house's electricity came from and where it went, in year-1 kWh (V12).

    The pure-kWh redesign of decision Q16: sources (PV generation, grid import, battery
    discharge), the house's electricity bus, sinks (heat pump, household, grid export, battery
    charge). Money appears only as an annotation on the two grid nodes, read from
    `carrier_year_one_bills`, because the EUR/kWh basis (D26) makes that a one-line statement
    rather than a second unit flowing through the diagram.

    Reconciliation, all validated here rather than left to a test: the grid import and export
    nodes equal the bought and sold quantities of `annual_energy_quantities_by_carrier` — the same
    meter the bills are computed from — and the two sides of the bus balance exactly, because
    whatever they do not account for is booked as a `losses / unattributed` terminal on the
    shorter side. The battery is a pass-through whose round-trip loss is reported rather than
    hidden. Roles the vocabulary cannot place travel out on `unattributed_roles_in_kwh`, since
    they are outside the balance rather than inside its residual; the renderers print them, which
    is the only place a reader can act on them, and this module logs nothing.

    Args:
        result: The evaluated perspective. Its `annual_energy_attribution_by_subject_in_kwh` is the
            source of every quantity; `has_energy_balance` is the caller's skip check.

    Returns:
        The terminals, the bus total, the three derived figures the caption states and the roles
        the diagram could not place.

    Raises:
        CostDataError: When the result carries no usable device flows (the located error the skip
            predicate exists to avoid), or when a grid node disagrees with the metered carrier
            quantity it must equal.
    """
    if not has_energy_balance(result):
        raise CostDataError(
            "The household energy balance needs at least "
            f"{EnergyBalanceLayout.MINIMUM_DEVICE_FLOWS} device flows in `LifecycleCostResult."
            "annual_energy_attribution_by_subject_in_kwh`, one of them a source and one a sink, "
            "which this result does not carry (a run serialized before the field existed, a "
            "component set whose classes the adapter's `DeviceEnergySpecs` table does not know, "
            "or a record with nothing on one side of the electricity bus). Check "
            "`views.has_energy_balance` first."
        )
    quantities, unattributed = _read_energy_attribution(result)
    _check_grid_nodes_against_the_meter(result, quantities)
    bills = carrier_year_one_bills(result)
    electricity = bills.get(EnergyCarrier.ELECTRICITY.value)
    annotations: Dict[EnergyFlowRole, Optional[float]] = {
        EnergyFlowRole.GRID_IMPORT: (
            electricity.total_excluding_feed_in_in_euro if electricity else None
        ),
        EnergyFlowRole.GRID_EXPORT: (
            -sum(
                value
                for category, value in (electricity.by_category_in_euro.items() if electricity else [])
                if category == CostCategory.FEED_IN_REVENUE
            )
            if electricity else None
        ),
    }
    sources = [
        EnergyBalanceNode(
            role=role,
            label=EnergyBalanceLayout.LABELS[role],
            quantity_in_kwh=quantities[role],
            annotation_in_euro=annotations.get(role),
        )
        for role in EnergyBalanceLayout.SOURCE_ROLES if quantities.get(role)
    ]
    sinks = [
        EnergyBalanceNode(
            role=role,
            label=EnergyBalanceLayout.LABELS[role],
            quantity_in_kwh=quantities[role],
            annotation_in_euro=annotations.get(role),
        )
        for role in EnergyBalanceLayout.SINK_ROLES if quantities.get(role)
    ]
    source_total = sum(node.quantity_in_kwh for node in sources)
    sink_total = sum(node.quantity_in_kwh for node in sinks)
    residual = source_total - sink_total
    if abs(residual) > max(source_total, sink_total) * EnergyBalanceLayout.BALANCE_RELATIVE_EPSILON:
        node = EnergyBalanceNode(
            role=None, label=EnergyBalanceLayout.RESIDUAL_LABEL, quantity_in_kwh=abs(residual)
        )
        (sinks if residual > 0 else sources).append(node)
    generation = quantities.get(EnergyFlowRole.PV_GENERATION, 0.0)
    exported = quantities.get(EnergyFlowRole.GRID_EXPORT, 0.0)
    consumption = sum(quantities.get(role, 0.0) for role in EnergyBalanceLayout.CONSUMPTION_ROLES)
    imported = quantities.get(EnergyFlowRole.GRID_IMPORT, 0.0)
    charged = quantities.get(EnergyFlowRole.BATTERY_CHARGE, 0.0)
    discharged = quantities.get(EnergyFlowRole.BATTERY_DISCHARGE, 0.0)
    return EnergyBalanceFlows(
        sources=sources,
        sinks=sinks,
        bus_total_in_kwh=max(source_total, sink_total),
        self_consumption_share=((generation - exported) / generation if generation > 0.0 else None),
        self_sufficiency_share=((consumption - imported) / consumption if consumption > 0.0 else None),
        battery_round_trip_loss_in_kwh=max(charged - discharged, 0.0) if charged or discharged else None,
        unattributed_roles_in_kwh=unattributed,
    )


def _check_grid_nodes_against_the_meter(
    result: LifecycleCostResult, quantities: Dict[EnergyFlowRole, float]
) -> None:
    """Raises unless the balance's two grid nodes equal the metered carrier quantities.

    The reconciliation that makes the EUR annotations trustworthy: the import node is annotated
    with the year-1 electricity bill and the export node with the feed-in revenue, and both bills
    are computed from `annual_energy_quantities_by_carrier`. If the balance's own grid figures
    disagreed with those quantities the annotation would price a different number from the one it
    is written beside, which is the silent kind of wrong this module fails fast on (D25).

    **Why the tolerance is float noise and not a margin.** Since slice 3 the attribution's two
    grid roles and the billing determinants are summed from the same meter columns, converted by
    the same unit converter and annualized identically; the two figures are therefore the same
    arithmetic run twice, and the only difference they can legitimately show is the order the
    additions happened in. Anything larger is a defect — a second extraction path, a unit slip, a
    partial year — and widening this tolerance would hide exactly the class of bug it exists to
    catch. `QUANTITY_RELATIVE_EPSILON` is a millionth, which is float residue on a five-figure
    kWh total and nothing else.

    A *missing* electricity record is the same failure seen from the other side, and is refused
    rather than skipped: if the balance is about to draw grid nodes there is nothing to reconcile
    them against, so their euro annotations would be unchecked figures beside unchecked
    quantities. A record with no grid nodes at all (an off-grid house) has nothing to check and
    returns.

    Raises:
        CostDataError: If a grid node disagrees with the meter, or if grid nodes would be drawn
            for a result carrying no ELECTRICITY quantities at all.
    """
    metered = result.annual_energy_quantities_by_carrier.get(EnergyCarrier.ELECTRICITY.value)
    if metered is None:
        drawn = [role for role in EnergyBalanceLayout.METER_ROLES if quantities.get(role)]
        if drawn:
            raise CostDataError(
                "The energy balance would draw "
                + ", ".join(f"{role.value} ({quantities[role]:,.1f} kWh)" for role in drawn)
                + f", but the result carries no {EnergyCarrier.ELECTRICITY.value} entry in "
                "`annual_energy_quantities_by_carrier` to reconcile those nodes against, so the "
                "euro annotation beside each of them would price a quantity nothing checked "
                "(D25)."
            )
        return
    for role, expected, name in (
        (EnergyFlowRole.GRID_IMPORT, metered.bought_in_kwh, "bought"),
        (EnergyFlowRole.GRID_EXPORT, metered.sold_in_kwh, "sold"),
    ):
        actual = quantities.get(role, 0.0)
        tolerance = max(
            ViewTolerances.RECONCILIATION_EPSILON,
            abs(expected) * ViewTolerances.QUANTITY_RELATIVE_EPSILON,
        )
        if abs(actual - expected) > tolerance:
            raise CostDataError(
                f"The energy balance's {role.value} node is {actual:,.1f} kWh but the meter "
                f"{name} {expected:,.1f} kWh of electricity; the euro annotation beside that node "
                "would price a quantity the diagram does not show."
            )


# ============================================================================ V13 benchmark

class WealthBenchmarkGrid:
    """The interest-rate grid of the fixed-interest benchmark (V13).

    One tuning namespace for the chart's whole x-axis: which rates the trajectories are drawn
    for, and hence the window inside which a break-even rate can be reported at all. The window
    is deliberately closed — the view never extrapolates a break-even rate outside it, and the
    caption says "break-even rate(s) in the shown range" — because an internal rate of return
    found by extending a grid is a number nobody checked.
    """

    #: 1 % to 10 % in 1 % steps: the range of savings rates a household actually compares against.
    RATES: Tuple[float, ...] = tuple(round(0.01 * step, 4) for step in range(1, 11))


@dataclass(frozen=True)
class WealthBenchmark:
    """Wealth advantage of renovating over banking the money, per interest rate (V13).

    `series_by_rate[i][t]` is `W_i(t) = Σ_{j<=t} d_j (1+i)^(t−j)` with `d_j` the differential
    nominal flow of year j (reference minus variant), so a positive value means the renovator is
    ahead of the household that did nothing and banked the difference at rate *i*. Interest is
    nominal and **pre-tax** (owner decision Q14): capital-income taxation is country-specific and
    the module is applied beyond Germany, so no tax law is baked in and the caption says so.

    The identity `W_i(T) == (1+i)^T · NPV(i)` ties the chart to the engine — future value is
    discounting run backwards — and is validated here for every grid rate *and* for the parameter
    rate, which is what makes the verdict at the parameter rate provably the engine's own
    verdict. The LOW and HIGH bands satisfy the same identity against their own differential
    flows; those flows are not fields of this object (only the BEST_ESTIMATE series is, since it
    is what the grid trajectories are built from), so `wealth_benchmark` checks the two bands
    where it still has them and this class checks everything its own fields can express.

    The shape checks come with it, because a chart cannot draw a trajectory whose rate it does not
    have an axis position for: the three rate-keyed collections carry exactly the same rates,
    every trajectory spans years 0..T like the differential flow it is built from, and no reported
    break-even rate falls outside the drawn window. Together they are what lets a renderer index
    `series_by_rate[rate]` and place `break_even_rates` without a guard of its own.
    """

    rates: List[float]
    series_by_rate: Dict[float, List[float]]
    terminal_by_rate: Dict[float, float]
    parameter_rate: float
    parameter_series_by_slot: Dict[Slot, List[float]]
    #: Zero crossings of the terminal advantage *inside* the grid window, linearly interpolated.
    break_even_rates: List[float]
    #: Differential nominal flow per year (reference − variant), BEST_ESTIMATE slot, index = year.
    differential_flow_in_euro: List[float]

    def __post_init__(self) -> None:
        """Validates the shape and the future-value identity stated in the class docstring.

        Raises:
            CostDataError: On a rate the three collections do not agree on, a missing slot, a
                trajectory of the wrong length, a terminal that is not its own series' last
                point, a broken future-value identity, or a break-even rate outside the grid.
        """
        if not self.differential_flow_in_euro:
            raise CostDataError(
                "The fixed-interest benchmark carries no differential flow at all, so there is "
                "no horizon to future-value over and nothing to draw."
            )
        horizon = len(self.differential_flow_in_euro) - 1
        if not set(self.rates) == set(self.series_by_rate) == set(self.terminal_by_rate):
            raise CostDataError(
                f"The fixed-interest benchmark draws rates {sorted(self.rates)} but carries "
                f"trajectories for {sorted(self.series_by_rate)} and terminals for "
                f"{sorted(self.terminal_by_rate)}; a chart cannot place a trajectory whose rate "
                "has no axis position, nor label an axis position that has no trajectory."
            )
        if set(self.parameter_series_by_slot) != set(Slot):
            raise CostDataError(
                f"The parameter-rate band carries slots {sorted(self.parameter_series_by_slot)} "
                f"rather than all of {sorted(slot.value for slot in Slot)}; the banded line is "
                "drawn from all three worlds or it is not a band."
            )
        trajectories: List[Tuple[str, List[float]]] = [
            (f"the {rate:.0%} trajectory", series) for rate, series in self.series_by_rate.items()
        ] + [
            (f"the {slot.value} band", series)
            for slot, series in self.parameter_series_by_slot.items()
        ]
        for label, series in trajectories:
            if len(series) != horizon + 1:
                raise CostDataError(
                    f"{label} of the fixed-interest benchmark spans {len(series)} year(s) but the "
                    f"differential flow spans {horizon + 1}; every line shares one year axis."
                )
        for rate, series in self.series_by_rate.items():
            if abs(series[-1] - self.terminal_by_rate[rate]) > ViewTolerances.RECONCILIATION_EPSILON:
                raise CostDataError(
                    f"The {rate:.0%} trajectory ends at {series[-1]:,.2f} EUR but its terminal is "
                    f"reported as {self.terminal_by_rate[rate]:,.2f} EUR; the end label and the "
                    "line it sits on are the same number."
                )
            _check_future_value_identity(
                rate, horizon, self.terminal_by_rate[rate], self.differential_flow_in_euro,
                f"grid rate {rate:.0%}",
            )
        _check_future_value_identity(
            self.parameter_rate,
            horizon,
            self.parameter_series_by_slot[Slot.BEST_ESTIMATE][-1],
            self.differential_flow_in_euro,
            f"the parameter rate {self.parameter_rate:.2%}",
        )
        outside = [
            rate for rate in self.break_even_rates if not min(self.rates) <= rate <= max(self.rates)
        ]
        if outside:
            raise CostDataError(
                f"The fixed-interest benchmark reports break-even rate(s) {outside} outside its "
                f"own grid {min(self.rates):.0%}..{max(self.rates):.0%}; an internal rate of "
                "return found by extending a grid is a number nobody checked."
            )

    def terminal_at_parameter_rate(self) -> float:
        """Terminal wealth advantage at the evaluation's own discount rate.

        The number that has to agree in sign with the comparison bridge's NPV delta: if
        renovating has the lower present cost, the renovator ends up richer, and vice versa.
        """
        return self.parameter_series_by_slot[Slot.BEST_ESTIMATE][-1]


def _check_future_value_identity(
    rate: float, horizon: int, terminal: float, flows: Sequence[float], what: str
) -> None:
    """Raises unless one benchmark trajectory ends where discounting run backwards says it must.

    `W_i(T) == (1+i)^T · NPV(i)` is the tie between the chart and the engine: the future value of
    a flow series at rate *i* is its present value carried forward, so a trajectory that ends
    anywhere else is drawn from arithmetic the engine does not do. The present value is recomputed
    through this module's own `discount_factor` rather than through a local `1/(1+i)**year`, which
    is the point — it is the engine's discounting the identity is checked against.

    Args:
        rate: The rate the trajectory was future-valued at.
        horizon: T, the last year of the series.
        terminal: `W_i(T)`, the trajectory's last point.
        flows: The differential nominal flow the trajectory was built from, index = year.
        what: How to name this trajectory in the error, e.g. "grid rate 4 %".

    Raises:
        CostDataError: If the two sides differ by more than float residue.
    """
    expected = ((1.0 + rate) ** horizon) * sum(
        flow * discount_factor(rate, year) for year, flow in enumerate(flows)
    )
    if abs(terminal - expected) > max(ViewTolerances.RECONCILIATION_EPSILON, abs(expected) * 1e-9):
        raise CostDataError(
            f"Fixed-interest benchmark breaks the future-value identity at {what}: "
            f"W(T) = {terminal:,.2f} EUR but (1+i)^T · NPV(i) = {expected:,.2f} EUR."
        )


def wealth_benchmark(reference: LifecycleCostResult, variant: LifecycleCostResult) -> WealthBenchmark:
    """The 'should I just leave the money in the bank' question, as one series (V13).

    If the do-nothing household banks the unspent renovation money and the renovating household
    banks its annual savings, both at rate *i*, the wealth *difference* between the two
    strategies is a single series: the differential nominal cash flow, future-valued at *i*. That
    is what this view computes, for the grid on `WealthBenchmarkGrid` plus the evaluation's own
    parameter rate (the one line that also carries a LOW/HIGH band, slot-wise).

    It takes the two results rather than a `VariantComparison` because the comparison publishes
    the differential only as a discounted cumulative curve at the parameter rate, and a rate grid
    needs the undiscounted per-year differential — `annual_cost_series_nominal_in_euro` on both
    sides.

    Reconciliation: `W_i(T) == (1+i)^T · NPV(i)` for every grid rate and for the parameter rate in
    all three worlds, with `NPV(i)` recomputed from the same differential flows through the
    module's own `discount_factor`. The grid rates and the BEST_ESTIMATE parameter line are
    checked by `WealthBenchmark.__post_init__`, which can express them from the object's own
    fields; the LOW and HIGH bands are checked here, where their differential flows still exist.

    The two results must share an observation horizon. A differential between series of different
    lengths is not a differential — the shorter side would contribute nothing for the years it
    does not have, which reads as "the reference costs nothing after year 10" rather than as "the
    reference was evaluated over ten years" — so a mismatch is refused instead of truncated.

    Raises:
        CostDataError: If the two results were evaluated over different horizons, if either
            nominal series does not span its own horizon, or if the future-value identity fails,
            which would mean the chart and the engine's discounting disagree.
    """
    horizon = variant.parameters.observation_period_in_years
    if reference.parameters.observation_period_in_years != horizon:
        raise CostDataError(
            "The fixed-interest benchmark compares two evaluations over different observation "
            f"periods: the reference runs {reference.parameters.observation_period_in_years} "
            f"year(s) and the variant {horizon}. Their differential flow would silently be the "
            "variant alone for the years only one of them covers."
        )
    reference_series = reference.annual_cost_series_nominal_in_euro
    variant_series = variant.annual_cost_series_nominal_in_euro
    for name, nominal in (("reference", reference_series), ("variant", variant_series)):
        if len(nominal) != horizon + 1:
            raise CostDataError(
                f"The {name}'s nominal annual series spans {len(nominal)} year(s) but its horizon "
                f"is {horizon}; the benchmark needs one flow per year of the shared axis."
            )

    def differential(slot: Slot) -> List[float]:
        return [
            reference_series[year].slot(slot) - variant_series[year].slot(slot)
            for year in range(horizon + 1)
        ]

    def future_value_series(flows: List[float], rate: float) -> List[float]:
        series: List[float] = []
        for year in range(len(flows)):
            series.append(sum(flows[j] * (1.0 + rate) ** (year - j) for j in range(year + 1)))
        return series

    flows_by_slot = {slot: differential(slot) for slot in Slot}
    best_estimate_flows = flows_by_slot[Slot.BEST_ESTIMATE]
    series_by_rate = {
        rate: future_value_series(best_estimate_flows, rate) for rate in WealthBenchmarkGrid.RATES
    }
    terminal_by_rate = {rate: series[-1] for rate, series in series_by_rate.items()}
    parameter_rate = variant.parameters.interest_rate
    parameter_series = {
        slot: future_value_series(flows_by_slot[slot], parameter_rate) for slot in Slot
    }
    for slot, series in parameter_series.items():
        _check_future_value_identity(
            parameter_rate, horizon, series[-1], flows_by_slot[slot],
            f"the parameter rate in the {slot.value} world",
        )
    return WealthBenchmark(
        rates=list(WealthBenchmarkGrid.RATES),
        series_by_rate=series_by_rate,
        terminal_by_rate=terminal_by_rate,
        parameter_rate=parameter_rate,
        parameter_series_by_slot=parameter_series,
        break_even_rates=_terminal_zero_crossings(terminal_by_rate),
        differential_flow_in_euro=best_estimate_flows,
    )


def _terminal_zero_crossings(terminal_by_rate: Mapping[float, float]) -> List[float]:
    """Every rate inside the grid at which the terminal advantage changes sign.

    Reported as a list rather than as "the" internal rate of return on purpose: a differential
    series that changes sign more than once can have several, and claiming a unique IRR for such
    a project is a standard finance mistake. Crossings are linearly interpolated between adjacent
    grid points, and nothing outside the grid window is ever reported.
    """
    rates = sorted(terminal_by_rate)
    crossings: List[float] = []
    for left, right in zip(rates, rates[1:]):
        low, high = terminal_by_rate[left], terminal_by_rate[right]
        if low == 0.0:
            crossings.append(left)
        elif low * high < 0:
            crossings.append(left + (right - left) * abs(low) / (abs(low) + abs(high)))
    if rates and terminal_by_rate[rates[-1]] == 0.0:
        crossings.append(rates[-1])
    return crossings


# ============================================================================ V14 monthly burden

class BurdenCategories:
    """What counts as a monthly burden, declared rather than inferred (V14, decision Q15 revised).

    The definitional heart of the chart, in one documented place. *Recurring* flows are what a
    household budgets for: debt service, energy, maintenance and fixed operation, taxes and
    levies, minus the recurring credits (feed-in revenue, a received levy). Everything to do with
    the year-0 financing event — the investment itself, planning, removal, the upfront support
    and the loan disbursement — is excluded: it is not a monthly burden, the funding statement
    shows it in full, and including it would put a 40,000 EUR "month" at the left edge of the
    axis.

    **Replacement years are excluded too** (Q15 as revised, after the owner reviewed the rendered
    chart). A replacement is capital expenditure — the same economic object as the year-0
    investment — so drawing it as a monthly burden while excluding year 0 was inconsistent, and
    no bank's monthly advisory shows replacement spikes; it smooths them into a maintenance
    reserve. `REPLACEMENT` therefore moves to the `REPLACEMENT` set below, whose equivalent annual
    cost becomes the reserve overlay line. `REPLACEMENT_RESERVE` joins it: where a perspective
    books an explicit sinking-fund payment, counting it in the bars *and* adding the derived
    reserve on top would charge the same replacement twice.
    """

    RECURRING = frozenset(
        {
            CostCategory.LOAN_INTEREST,
            CostCategory.LOAN_PRINCIPAL,
            CostCategory.ENERGY_WORKING,
            CostCategory.ENERGY_STANDING,
            CostCategory.ENERGY_CAPACITY_CHARGE,
            CostCategory.ENERGY_CO2_PRICE,
            CostCategory.MAINTENANCE,
            CostCategory.FIXED_OPERATION,
            CostCategory.MODERNIZATION_LEVY,
            CostCategory.FEED_IN_REVENUE,
        }
    )

    #: The capital events that leave the bars and come back as the reserve line — the display
    #: group "Replacements", named here as categories so the view never has to consult a display
    #: grouping to decide what a number *is*.
    REPLACEMENT = frozenset({CostCategory.REPLACEMENT, CostCategory.REPLACEMENT_RESERVE})

    #: Months a year, as the divisor it is: the engine has no intra-year resolution, so this is a
    #: unit conversion of an annual figure and never a statement about seasonal profiles.
    MONTHS_PER_YEAR = 12.0


@dataclass(frozen=True)
class MonthlyBurden:
    """The monthly recurring cost per year plus the smoothed replacement reserve (V14).

    Two figures that only make sense together after the Q15 revision: `series` is the recurring
    burden the bars draw, year by year and slot-wise, and `replacement_reserve_per_month` is the
    constant a prudent owner would put aside for the capital events those bars deliberately no
    longer contain. The chart draws the second as a dashed line above the first, which is why
    both travel in one object — a caller cannot pick up the bars and forget the reserve.

    The reserve is the equivalent annual cost of the replacement flows divided by twelve: the
    replacement categories' NPV multiplied by the parameters' annuity factor, the same capital
    recovery factor the headline EAC KPI uses. It is zero for an evaluation that books no
    replacement, in which case the chart omits the line rather than drawing a flat zero.
    """

    series: List[UncertainValue]
    replacement_reserve_per_month: float


def monthly_burden_series(result: LifecycleCostResult) -> MonthlyBurden:
    """Recurring cost per month, year by year, plus the replacement reserve (V14).

    The recurring categories of `BurdenCategories` off the scoped timeline, slot-wise, divided by
    twelve; index = year. Neither the year-0 financing event nor a replacement year appears,
    because neither owns a recurring category — the rule is stated once on the namespace class
    rather than as a year filter here. The replacements come back as
    `replacement_reserve_per_month`, the equivalent annual cost of the replacement-category NPV
    over twelve months, computed with `parameters.annuity_factor()` so it is the same smoothing
    the headline EAC applies to everything else.

    Reconciliation: `series[1]` is the recurring part of `monthly_cost_year1_in_euro` — the
    published field is *already* a monthly figure, so the two are compared directly and the
    difference between them is exactly year 1's non-recurring flows; the series times twelve
    re-sums to the recurring subset of `annual_cost_series_nominal_in_euro`; and twelve times the
    reserve divided by the annuity factor gives the replacement categories' NPV back. All three
    are checked in the tests, and all three are true by construction because this is a filter and
    a rescaling of the same entries.
    """
    horizon = result.parameters.observation_period_in_years
    per_year = [UncertainValue.exact(0.0) for _ in range(horizon + 1)]
    for year, entries in enumerate(_recurring_entries_by_year(result)):
        for item in entries:
            per_year[year] = per_year[year] + item.amount_in_euro
    replacement_npv = sum(
        value.best_estimate
        for category, value in result.npv_by_category.items()
        if category in BurdenCategories.REPLACEMENT
    )
    reserve = replacement_npv * result.parameters.annuity_factor() / BurdenCategories.MONTHS_PER_YEAR
    return MonthlyBurden(
        series=[value.scale(1.0 / BurdenCategories.MONTHS_PER_YEAR) for value in per_year],
        replacement_reserve_per_month=reserve,
    )


def _recurring_entries_by_year(result: LifecycleCostResult) -> List[List[CashFlowEntry]]:
    """The scoped timeline's recurring flows, bucketed by year and clamped to the horizon (V14).

    The one place V14's *selection* lives — which categories are a monthly burden and which years
    are on the axis. Both the total series and the per-group split are built from this, because
    they are drawn on top of each other: the stacked bars are the whiskered totals split by
    colour, and a split that filtered one category differently, or ran one year further, would
    produce a stack that does not add up to the bar it fills. The two callers each divide by
    `BurdenCategories.MONTHS_PER_YEAR` themselves, since one sums bands and the other sums
    best-estimate floats per category, but they select the same entries by construction.

    Args:
        result: The perspective whose burden is drawn.

    Returns:
        One list per year 0..T, index = year; a year with no recurring flow is an empty list
        rather than a missing row, so the two views stay index-aligned.
    """
    horizon = result.parameters.observation_period_in_years
    rows: List[List[CashFlowEntry]] = [[] for _ in range(horizon + 1)]
    for entry in result.scoped_timeline().entries:
        if 0 <= entry.year <= horizon and entry.category in BurdenCategories.RECURRING:
            rows[entry.year].append(entry)
    return rows


def monthly_burden_by_group(
    result: LifecycleCostResult, mapping: Mapping[CostCategory, GroupKey]
) -> List[Dict[GroupKey, float]]:
    """The monthly burden split by display group, BEST_ESTIMATE slot — the stack behind the bars.

    The same filter as `monthly_burden_series` (V14) — literally the same, through
    `_recurring_entries_by_year` — folded onto the caller's display groups so the chart can stack
    the bars without adding anything itself. Row order is the year index, and a year with no
    recurring flow folds to an empty dict rather than disappearing, so the two views stay
    index-aligned.

    Raises:
        CostDataError: If `mapping` declares no display group for a recurring category.
    """
    rows: List[Dict[CostCategory, float]] = []
    for entries in _recurring_entries_by_year(result):
        row: Dict[CostCategory, float] = {}
        for item in entries:
            row[item.category] = (
                row.get(item.category, 0.0)
                + item.amount_in_euro.best_estimate / BurdenCategories.MONTHS_PER_YEAR
            )
        rows.append(row)
    return fold_category_matrix(rows, mapping)


# ============================================================================ V15 equity build-up

@dataclass(frozen=True)
class AssetDebtSeries:
    """Book value, outstanding debt and the equity between them, per year (V15).

    Three year-indexed series in the BEST_ESTIMATE slot plus every interval in which equity is
    negative — the "underwater" case a bank checks for. The book value is straight-line
    depreciation of every install and replacement the timeline *charged*, on the same basis the
    residual calculator uses, which is why `book_value_in_euro[-1]` equals the booked
    residual-value credit exactly; that endpoint identity is the chart's audit weight.

    `debt_in_euro` is the outstanding balance **clamped at zero**. A negative balance is an
    overpayment artefact — a perspective whose booked principal repayments add up to more than its
    disbursement — and it is not debt: the loan-free rule already reads any balance at or below
    zero as repaid, so publishing the raw negative on the debt line while treating it as zero in
    the equity calculation would have made `equity == book − debt` false on exactly those years.
    With the clamp the identity holds everywhere, which is what lets the chart draw the gap
    between two lines instead of a third series.

    `underwater_intervals` is a list because negative equity can come and go: a loan drawn against
    a fast-depreciating asset can dip under, recover after a repayment, and dip again at the next
    replacement. One `(first, last)` pair spanning all of that would have claimed the recovery
    never happened, so each maximal run of negative-equity years is reported separately and an
    empty list means the equity never went negative.

    `depreciation_life_by_subject` records the life each subject's *last* installation was
    depreciated over. It is *derived from the booked events* — the residual credit and the
    replacement spacing — never read from the catalog, for the same reason the component event
    strip derives its spans that way: the chart has to show what the timeline charged, so that a
    disagreement with the catalog is visible instead of being drawn away.
    """

    book_value_in_euro: List[float]
    debt_in_euro: List[float]
    equity_in_euro: List[float]
    residual_credit_in_euro: float
    depreciation_life_by_subject: Dict[str, float]
    underwater_intervals: List[Tuple[int, int]] = field(default_factory=list)


def asset_debt_series(
    result: LifecycleCostResult, amortization: Optional[LoanAmortization] = None
) -> AssetDebtSeries:
    """Asset book value against outstanding debt, and the equity gap between them (V15).

    Book value is built from the component event strip's events: every charged install or
    replacement steps the curve up by its own amount and then declines linearly to zero over the
    span that installation was actually in service. For the **last** event of a subject that span
    is the depreciation life derived from what the timeline booked — from the residual credit
    where there is one (`residual = amount × (install + life − T) / life`, the residual
    calculator's own formula solved for the life), otherwise from the horizon, so that a subject
    with no residual is fully written down at T. For every **earlier** event it is the shorter of
    that life and the gap to its successor, because an installation that was replaced is off the
    books from the replacement year on (`_write_off_span`). Debt is `loan_amortization_series`'s
    outstanding balance, clamped at zero, reused rather than recomputed.

    Applying the last event's life to every event was the defect this shape fixes: a subject with
    an install and a replacement spaced closer together than that life kept book value from the
    superseded unit all the way to the horizon, so the endpoint overshot the residual and the
    check below refused to draw a chart that was correct in the engine. Engine timelines are
    unaffected — the calculator re-invests at exactly the service life and books a residual for
    the last unit only, so the gap *is* the derived life there and the curve is unchanged.

    Reconciliation: the book value at the horizon equals the booked residual credit (validated
    here), each install year steps the curve by exactly that event's charged amount, and equity
    is the plain difference of the two published series.

    Args:
        result: The perspective whose book value and debt are built.
        amortization: Its `loan_amortization_series`, when the caller already has it — the
            equity section reads it to decide whether the perspective is financed at all, and
            deriving the identical series a second line later is a second walk of the same
            timeline for numbers already in hand. Omitted, it is derived here as before.

    Returns:
        The two series, the equity between them and the runs where that equity is negative.

    Raises:
        CostDataError: If the horizon book value does not reproduce the booked residual credit.
    """
    horizon = result.parameters.observation_period_in_years
    rows = component_event_strip(result)
    book_value = [0.0] * (horizon + 1)
    lives: Dict[str, float] = {}
    residual_total = 0.0
    for row in rows:
        if not row.events:
            continue
        residual_amount = -row.residual.amount_in_euro if row.residual is not None else 0.0
        residual_total += residual_amount
        life = _depreciation_life(row.events[-1], residual_amount, horizon)
        lives[row.subject] = life
        for index, event in enumerate(row.events):
            successor = row.events[index + 1] if index + 1 < len(row.events) else None
            span = _write_off_span(life, event, successor)
            for year in range(event.year, horizon + 1):
                remaining = max(0.0, 1.0 - (year - event.year) / span)
                book_value[year] += event.amount_in_euro * remaining
    schedule = amortization if amortization is not None else loan_amortization_series(result)
    balance = schedule.outstanding_balance_in_euro or [0.0] * (horizon + 1)
    debt = [max(owed, 0.0) for owed in balance]
    equity = [book - owed for book, owed in zip(book_value, debt)]
    if abs(book_value[horizon] - residual_total) > max(
        ViewTolerances.RECONCILIATION_EPSILON, abs(residual_total) * 1e-9
    ):
        raise CostDataError(
            f"Asset book value at the horizon is {book_value[horizon]:,.2f} EUR but the timeline "
            f"booked a residual credit of {residual_total:,.2f} EUR; the depreciation basis of "
            "the chart and of the residual calculator have diverged."
        )
    underwater: List[Tuple[int, int]] = []
    for year, value in enumerate(equity):
        if value >= 0.0:
            continue
        if underwater and underwater[-1][1] == year - 1:
            underwater[-1] = (underwater[-1][0], year)
        else:
            underwater.append((year, year))
    return AssetDebtSeries(
        book_value_in_euro=book_value,
        debt_in_euro=debt,
        equity_in_euro=equity,
        residual_credit_in_euro=residual_total,
        depreciation_life_by_subject=lives,
        underwater_intervals=underwater,
    )


def _write_off_span(
    life: float, event: LifecycleEvent, successor: Optional[LifecycleEvent]
) -> float:
    """How long one charged installation is written down over: its life, or until it is replaced.

    The last installation of a subject writes down over `life`, the life `_depreciation_life`
    recovered from the residual booking. An earlier one writes down over the shorter of that life
    and the years until its successor, because the two things that can end a unit's book life are
    running out of life and being replaced, and the timeline records the second exactly.

    The comparison is relative rather than exact on purpose. The engine re-invests at the rounded
    service life, so the gap and the recovered life are the same number arrived at two ways and
    differ by float residue; treating that residue as an early replacement would shorten every
    engine-produced write-down by a few ulps and move a curve that is correct. Only a gap shorter
    than the life by more than `ViewTolerances.DEPRECIATION_SPACING_RELATIVE_EPSILON` counts.

    Args:
        life: The subject's depreciation life, from `_depreciation_life`.
        event: The installation being written down.
        successor: The next event on the same subject, or None for the last one.

    Returns:
        The number of years to write the event off over; never below one year, which guards a
        replacement booked in the year after its predecessor.
    """
    if successor is None:
        return life
    gap = float(successor.year - event.year)
    if gap < life * (1.0 - ViewTolerances.DEPRECIATION_SPACING_RELATIVE_EPSILON):
        return max(gap, 1.0)
    return life


def _depreciation_life(last_event: LifecycleEvent, residual_in_euro: float, horizon: int) -> float:
    """The life the last charged installation is written down over, derived from the booking.

    The *last* one, deliberately: this is the event the residual credit was computed for, so it is
    the only one whose life the booking determines. Earlier events borrow it as an upper bound and
    are cut short by their successor where the timeline replaced them sooner — see
    `_write_off_span`, which is where that rule lives.

    Inverts the residual calculator's own straight-line rule: it books
    `residual = amount × (install + life − T) / life`, so a subject with a residual credit
    determines its own life exactly, and the chart's endpoint then *is* that residual rather than
    merely agreeing with it. A subject with no residual was written off inside the horizon, so it
    depreciates over exactly the years that remain — which reproduces the zero the timeline
    booked. The one-year floor guards the degenerate case of an installation at the horizon.
    """
    remaining_years = max(horizon - last_event.year, 0)
    ratio = residual_in_euro / last_event.amount_in_euro if last_event.amount_in_euro else 0.0
    if 0.0 < ratio < 1.0:
        return max(remaining_years / (1.0 - ratio), 1.0)
    return max(float(remaining_years), 1.0)
