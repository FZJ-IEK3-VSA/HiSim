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

from collections.abc import Hashable
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple, TypeVar

from hisim.economics.calculators.financing_application import FinancingConstants
from hisim.economics.calculators.subsidy_application import nominal_support_from_entries
from hisim.economics.carriers import EnergyCarrier
from hisim.economics.catalog_entries import CostDataError
from hisim.economics.results import (
    LifecycleCostResult,
    ModernizationLevySummary,
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


class ViewThresholds:
    """Numeric cut-offs the view models apply.

    The one place where a view is allowed to drop data, and only ever to suppress float noise —
    values that are arithmetically zero but land at 1e-13 after a chain of discounting and slot
    arithmetic. Named rather than inlined so a reviewer can see the magnitude of what is being
    hidden (half a cent) and confirm it cannot swallow a real flow.
    """

    #: Amounts below this (in every slot) are dropped from the detail table as float noise.
    DETAIL_ROW_EPSILON = 0.005


#: Whatever presentation groups categories by — an index, a label, anything hashable. Generic so
#: a caller passing `Mapping[CostCategory, int]` gets a `Dict[int, ...]` back and stays typed.
GroupKey = TypeVar("GroupKey", bound=Hashable)


# ---------------------------------------------------------------------------- category folding

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
        KeyError: if `values` contains a category `mapping` does not declare.
    """
    folded: Dict[GroupKey, Any] = {}
    for category, amount in values.items():
        if category not in mapping:
            raise KeyError(f"No display group declared for cost category {category.value!r}.")
        group = mapping[category]
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
    dropped by `ViewThresholds.DETAIL_ROW_EPSILON` being absent from both — so the table always
    adds up on screen.
    """

    year: int
    rows: List[TimelineDetailRow]
    nominal_total_in_euro: UncertainValue  # sum of the rows' nominal bands, slot-wise
    discounted_total_best_estimate_in_euro: float  # that sum's BEST_ESTIMATE slot, discounted to year 0


def timeline_detail_rows(result: LifecycleCostResult) -> List[TimelineDetailYear]:
    """The §3.6 timeline as a verification table: (year, subject, category) with subtotals.

    Same scoping as the chart it sits under; duplicate cells are aggregated, and cells that are
    zero in every slot up to `ViewThresholds.DETAIL_ROW_EPSILON` are dropped as float noise. Rows within a year
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
            abs(value) < ViewThresholds.DETAIL_ROW_EPSILON
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
    """Tolerances the self-validating chart views reconcile against (visualization spec §5).

    The views added for the V1-V15 chart set do not merely re-shape the result, they *check*
    themselves: a Sankey whose transfer ribbons do not net to zero, a tornado whose bars do not
    sum to the band or a sources-and-uses statement that does not balance is a lie about the
    engine, so those views raise `CostDataError` instead of drawing. Every such comparison needs
    a tolerance, and they are collected here so a reviewer can see in one place how much
    disagreement counts as float noise (half a cent on a euro figure) rather than as a defect.
    """

    #: Absolute euro tolerance for the reconciliation checks (half a cent, as in the detail table).
    RECONCILIATION_EPSILON = 0.005
    #: Balance below this counts as repaid; guards `LoanAmortization.loan_free_year` against
    #: float residue.
    BALANCE_EPSILON = 0.01
    #: Relative tolerance for the kWh attribution check of the energy balance, where quantities
    #: are large enough that an absolute euro epsilon means nothing.
    QUANTITY_RELATIVE_EPSILON = 1e-6


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
    #: they net to zero across payers (fail-fast, D25).
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

    Reconciliation: `net_by_actor()` of an actor equals the nominal sum of that actor's scoped
    timeline, and the transfer ribbons net to zero across payers (validated in the view).
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

        The reconciliation handle: this must equal the nominal sum of the actor's scoped
        timeline, which is what makes the picture an accounting statement rather than an
        illustration.
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
    the remaining perspectives are used in that case.

    Args:
        results: The evaluated perspectives, in bundle order (the order they are rendered in).

    Returns:
        The three lists, each in the input's order.
    """
    society: List[LifecycleCostResult] = []
    rented: List[LifecycleCostResult] = []
    rest: List[LifecycleCostResult] = []
    for result in results:
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
    return StoryPerspectives(owner=owner or tuple(rest), rented=tuple(rented), society=tuple(society))


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
    #: why they reach the statement summing to zero.
    TRANSFER_CATEGORIES = (
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
        secondary_categories=TRANSFER_CATEGORIES,
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

    def income_flows(self) -> Tuple[Tuple[Tuple[str, str, float, bool, Optional[CostCategory]], ...], bool]:
        """The income-statement Sankey: `(source, target, amount, is_accounting_credit)` + a flag.

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

        Returns:
            The ribbons as `(source, target, amount, is_accounting_credit, category)` — the
            category so the renderer can hue a ribbon like the same money everywhere else, and
            `None` on the net-position ribbon, which is a result rather than a category — and
            `True` when the net-position ribbon is an inflow (a net cost) rather than the usual
            leftover outflow. Ribbon amounts are magnitudes; a Sankey ribbon has no sign.
        """
        node = LandlordStatementCategories.LANDLORD_NODE
        net_node = LandlordStatementCategories.NET_POSITION_NODE
        flows: List[Tuple[str, str, float, bool, Optional[CostCategory]]] = []
        for line in list(self.cash_lines) + list(self.accounting_lines):
            if line.npv_in_euro < 0:
                flows.append((line.label, node, -line.npv_in_euro, line.is_accounting_credit, line.category))
            elif line.npv_in_euro > 0:
                flows.append((node, line.label, line.npv_in_euro, line.is_accounting_credit, line.category))
        net_is_inflow = self.net_position_in_euro > 0
        if abs(self.net_position_in_euro) > ViewTolerances.RECONCILIATION_EPSILON:
            if net_is_inflow:
                flows.append((net_node, node, self.net_position_in_euro, False, None))
            else:
                flows.append((node, net_node, -self.net_position_in_euro, False, None))
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

    Args:
        result: The perspective to state; its `npv_by_category` and, for a transfer partition,
            its full `timeline`.
        partition: Which two sides to split into and what to call them.

    Returns:
        The two-sided statement with both subtotals and the net position.

    Raises:
        CostDataError: If the two sides do not sum to the perspective's total NPV. That can only
            happen if a category was dropped between the pivot and this split, which would make
            the statement a picture of a business case that is not the one being reported.
    """
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
    the §559e modernization levy today — is drawn payer to payer instead of as two external
    stubs; the view checks that those legs net to zero across all payers and raises if they do
    not, since a transfer that creates money is a defect in the allocation ruleset, not something
    to render.

    Reconciliation: per-actor net (outflows − inflows) equals the nominal sum of that actor's
    scoped timeline; the transfer ribbons net to zero.

    Raises:
        CostDataError: On a category with no declared counterparty, or on declared transfers that
            do not net to zero across payers.
    """
    horizon = result.parameters.observation_period_in_years
    ribbons: Dict[Tuple[str, str, Optional[CostCategory]], float] = {}
    transfer_net: Dict[str, float] = {}
    actors: List[str] = []
    for entry in result.timeline.entries:
        if not 0 <= entry.year <= horizon:
            continue
        actor = entry.payer.value
        if actor not in actors:
            actors.append(actor)
        amount = entry.amount_in_euro.best_estimate
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
    return ActorFlowMatrix(
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


def _transfer_ribbons(transfer_net: Dict[str, float]) -> List[ActorFlow]:
    """Payer-to-payer ribbons for the declared transfer categories, validated to net to zero.

    Splits the payers into the ones a transfer category leaves (positive net, the paying leg) and
    the ones it reaches (negative net, the receiving leg) and connects them, distributing
    proportionally when there is more than one of either — today there is exactly one of each
    (tenant pays, landlord receives), and the general form exists so a second transfer pair does
    not need new code.

    Raises:
        CostDataError: If the declared transfers do not net to zero across payers, i.e. if the
            allocation created or destroyed money.
    """
    total = sum(transfer_net.values())
    if abs(total) > ViewTolerances.RECONCILIATION_EPSILON:
        raise CostDataError(
            f"Declared inter-actor transfer categories do not net to zero across payers "
            f"(residual {total:,.2f} EUR): {transfer_net}. A transfer pair that does not cancel "
            "means the allocation ruleset created or destroyed money (§6.5)."
        )
    payers = {actor: value for actor, value in transfer_net.items() if value > 0}
    receivers = {actor: -value for actor, value in transfer_net.items() if value < 0}
    receiver_total = sum(receivers.values())
    flows: List[ActorFlow] = []
    for payer, paid in payers.items():
        for receiver, received in receivers.items():
            share = received / receiver_total if receiver_total else 0.0
            amount = paid * share
            if amount > ViewTolerances.RECONCILIATION_EPSILON:
                flows.append(
                    ActorFlow(
                        source=payer,
                        target=receiver,
                        amount_in_euro=amount,
                        category=CostCategory.MODERNIZATION_LEVY,
                        is_transfer=True,
                    )
                )
    return flows


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
    world minus its NPV in the BEST_ESTIMATE world — signed, and *not* absolute widths. For a
    mirrored revenue subject (feed-in, support) the LOW delta can be positive, which puts the
    whole bar on one side of the axis; that is correct and is what the caption explains.
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
        CostDataError: If the steps do not sum to the published NPV delta.
    """
    variant_groups = fold_categories(variant.npv_by_category, mapping)
    reference_groups = fold_categories(reference.npv_by_category, mapping)
    present: Set[Any] = set(variant_groups) | set(reference_groups)
    try:
        # Display-group indices sort into the fixed order every chart stacks them in; a mapping
        # whose keys are not orderable keeps first-appearance order instead, which is still fixed.
        keys: List[Any] = sorted(present)
    except TypeError:
        keys = [key for key in list(variant_groups) + list(reference_groups) if key in present]
        keys = list(dict.fromkeys(keys))
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
        The disclosure. `effective_annual_rate` is None when there is no loan at all or when the
        flow sequence has no sign change to solve for (a grant larger than the debt service).

    Raises:
        CostDataError: If the loan entries do not reconcile with the disclosure's parts.
    """
    amortization = loan_amortization_series(result)
    horizon = result.parameters.observation_period_in_years
    interest_total = sum(amortization.interest_in_euro)
    principal_repaid = sum(amortization.principal_in_euro)
    grants = -sum(
        entry.amount_in_euro.best_estimate
        for entry in result.scoped_timeline().entries
        if entry.category == CostCategory.SUBSIDY
        and entry.subject == FinancingConstants.FINANCING_SUBJECT
        and 0 <= entry.year <= horizon
    )
    disbursement = amortization.disbursement_in_euro
    nominal_loan_flows = sum(
        entry.amount_in_euro.best_estimate
        for entry in result.scoped_timeline().entries
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
    disclosure = TotalCostOfCredit(
        principal_in_euro=disbursement,
        interest_in_euro=interest_total,
        fees_in_euro=0.0,
        grants_in_euro=grants,
        unrepaid_principal_in_euro=disbursement - principal_repaid,
        effective_annual_rate=_effective_annual_rate(amortization, grants),
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
) -> Optional[float]:
    """Internal rate of the loan's own flows: disbursement and grant in, debt service out.

    The Effektivzins, found by bisection on the same `discount_factor` every other present value
    in the package uses — one flow sequence instead of a grid. For a fee-free, grant-free annuity
    with annual periods it returns the nominal rate exactly, which is the null test the spec asks
    for; a repayment grant strictly lowers it because the borrower received money without owing
    more.

    Returns:
        The rate as a fraction, or None when there is no loan or the sequence has no zero
        crossing inside the searched window (a grant exceeding the whole debt service).
    """
    if not amortization.has_flows() or amortization.disbursement_in_euro <= 0:
        return None
    inflow = amortization.disbursement_in_euro + grants_in_euro
    service = [
        interest + principal
        for interest, principal in zip(amortization.interest_in_euro, amortization.principal_in_euro)
    ]

    def present_value(rate: float) -> float:
        return inflow - sum(
            amount * discount_factor(rate, year) for year, amount in enumerate(service) if amount
        )

    low, high = -0.99, 5.0
    if present_value(low) * present_value(high) > 0:
        return None
    for _iteration in range(200):
        middle = (low + high) / 2.0
        if present_value(low) * present_value(middle) <= 0:
            high = middle
        else:
            low = middle
    return (low + high) / 2.0


# ============================================================================ V7 event strip

class EventKinds:
    """The three things that can happen to a component on its lifetime strip (V7).

    Named constants rather than an enum because they are compared and printed and nothing else;
    keeping them together states the vocabulary of the strip in one place — a component is
    bought, is replaced, or is worth something at the horizon, and nothing on the chart means
    anything else.
    """

    INVESTMENT = "investment"
    REPLACEMENT = "replacement"
    RESIDUAL = "residual"


@dataclass(frozen=True)
class LifecycleEvent:
    """One dated event on a component's strip: what happened, when, and for how much.

    Amounts are nominal BEST_ESTIMATE-slot euros as the timeline booked them, so an investment is
    positive and a residual value negative — the sign is the reader's cue that the residual is a
    credit, and the renderer does not flip it.
    """

    year: int
    amount_in_euro: float
    kind: str


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

    The row makes the tightened residual rule visible: `residual` may only be set when the row
    also has at least one investment or replacement event, because only an installation the
    timeline actually charged may be written down. `component_event_strip` validates that and
    raises, which turns a calculator-internal gate into a checked property of the output.
    """

    subject: str
    events: List[LifecycleEvent]
    spans: List[ServiceSpan]
    residual: Optional[LifecycleEvent] = None

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
        CostDataError: If a subject carries a residual-value credit without any investment or
            replacement the timeline charged (the package-A residual gate, as an output property).
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
        if residual is not None and not events:
            raise CostDataError(
                f"Subject {subject!r} carries a residual-value credit of "
                f"{residual.amount_in_euro:,.2f} EUR without any investment or replacement the "
                "timeline charged; only an installation charged inside the horizon may be "
                "written down (§4.1, review package A)."
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
