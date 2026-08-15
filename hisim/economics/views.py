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

from dataclasses import dataclass, field
from typing import Any, Dict, Hashable, Iterable, List, Mapping, Optional, Sequence, Set, Tuple, TypeVar

from hisim.economics.calculators.subsidy_application import nominal_support_from_entries
from hisim.economics.carriers import EnergyCarrier, EnergyFlowRole
from hisim.economics.catalog_entries import CostDataError
from hisim.economics.results import (
    LifecycleCostResult,
    ModernizationLevySummary,
    RateOrigin,
    ResolvedRate,
)
from hisim.economics.subsidies import PayoutKind, SubsidyAward
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
    #: Balance below this counts as repaid; guards `loan_free_year` against float residue.
    BALANCE_EPSILON = 0.01
    #: Relative tolerance for the kWh attribution check of V12, where quantities are large.
    QUANTITY_RELATIVE_EPSILON = 1e-6


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
    result: LifecycleCostResult, slot: Slot = Slot.AVERAGE
) -> List[Dict[CostCategory, float]]:
    """Nominal euros per (year, category) for years 0..T — index = year.

    The un-grouped form of the annual cash-flow chart's input; presentation folds it onto its
    display groups with `fold_category_matrix`.

    Nominal means undiscounted: this is the liquidity view ("what leaves the account in year
    N"), the counterpart of `cumulative_discounted_cost_series` above it in the same report
    section. Package sign convention applies unchanged — costs positive, revenue/support
    negative — which is what puts credits below the axis in the chart. Only one slot is
    returned because a stacked bar cannot show a band; `slot` selects which of the three
    coherent worlds is drawn, and every caller in this package draws AVERAGE.

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

    `outstanding_balance_in_euro` was added for the V5 amortization panel and the V15 equity
    chart: it is the disbursement minus the cumulative principal repayments up to and including
    that year, so it reconciles with the plotted bars by construction rather than by a second
    schedule computation. Year 0 therefore carries the full disbursement, and a fully amortizing
    plan ends at zero within float tolerance.
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

        The "loan-free" milestone V9's financing lane draws. It reads the balance series rather
        than the plan's term so that a truncated schedule (a term reaching past the observation
        horizon) honestly reports None instead of a maturity the timeline never books.
        """
        for year, balance in enumerate(self.outstanding_balance_in_euro):
            if year > 0 and balance <= ViewTolerances.BALANCE_EPSILON:
                return year
        return None


def loan_amortization_series(
    result: LifecycleCostResult, slot: Slot = Slot.AVERAGE
) -> LoanAmortization:
    """The loan's interest/principal split per year (§4.4).

    Reads the `LOAN_INTEREST` and `LOAN_PRINCIPAL` entries off the scoped timeline — it does not
    re-run the schedule builder in `financing.py`, so what the chart shows is by construction
    the debt service the NPV was computed from. Callers use `LoanAmortization.has_flows()` to
    decide whether the perspective is financed at all and skip the chart when it is not, which
    is the common case (cash purchase).

    The outstanding-balance series is built here from the same entries (disbursement minus the
    running principal repayments), which is what keeps the balance line of the V5 panel and the
    debt line of the V15 equity chart from disagreeing with the bars drawn beside them.

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
    nominal band as it was booked, and the AVERAGE slot after discounting — because the two
    side by side are what lets a reviewer verify the discount factor of a given year by
    division, without leaving the table.
    """

    year: int
    subject: str
    category: CostCategory
    nominal_in_euro: UncertainValue  # as booked, undiscounted, full min/avg/max band
    discounted_average_in_euro: float  # AVERAGE slot × discount_factor(interest, year)


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
    discounted_total_average_in_euro: float  # that sum's AVERAGE slot, discounted to year 0


def timeline_detail_rows(result: LifecycleCostResult) -> List[TimelineDetailYear]:
    """The §3.6 timeline as a verification table: (year, subject, category) with subtotals.

    Same scoping as the chart it sits under; duplicate cells are aggregated, and cells that are
    zero in every slot up to `ViewThresholds.DETAIL_ROW_EPSILON` are dropped as float noise. Rows within a year
    are ordered by nominal AVERAGE amount, so the biggest credit and the biggest cost of a year
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
                    discounted_total_average_in_euro=total.average * discount_factor(interest, current),
                )
            )

    for (year, subject, category), amount in sorted(
        aggregated.items(), key=lambda item: (item[0][0], item[1].average)
    ):
        if all(
            abs(value) < ViewThresholds.DETAIL_ROW_EPSILON
            for value in (amount.average, amount.minimum, amount.maximum)
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
                discounted_average_in_euro=amount.average * discount_factor(interest, year),
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
    are nominal euros of the AVERAGE slot, except `year_one_band_in_euro`, which keeps the full
    band.
    """

    carrier: str
    #: Year-1 amounts per category, AVERAGE slot; includes the feed-in credit where it belongs
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
                    by_category.get(entry.category, 0.0) + entry.amount_in_euro.average
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
    #: AVERAGE-slot year-0 amounts, keyed by category, only the categories that are non-zero.
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
                entry.amount_in_euro.average for entry in year_zero if entry.category == category
            )
            if value:
                by_category[category] = value
        build_ups[subject] = YearZeroBuildUp(subject=subject, by_category_in_euro=by_category)
    return build_ups


@dataclass(frozen=True)
class SubsidyShare:
    """How far the support carries one subject's gross investment (AVERAGE slot, nominal).

    A "X % of this measure is funded" statement, used by the subsidy composition bars in the
    HTML report and by the matplotlib investment waterfall. Both parts are nominal (undiscounted)
    euros of the AVERAGE slot, because the question it answers is about the money on the invoice
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
        gross = breakdown.investment_gross_in_euro.average
        if gross <= 0:
            continue
        subsidy = breakdown.subsidies_nominal_in_euro.average
        shares[subject] = SubsidyShare(
            subject=subject, gross_in_euro=gross, subsidy_in_euro=min(subsidy, gross)
        )
    return shares


def investment_net_of_subsidies(result: LifecycleCostResult) -> Dict[str, UncertainValue]:
    """Per-subject year-0 gross investment minus nominal support, as a **band**.

    The "Net" column of the investment table (`reporting.py:976`), which is a different figure
    from `SubsidyShare.net_in_euro`: that one is the AVERAGE slot with the share clamp applied
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


class SubsidySchemeLabels:
    """The names of the support sources a timeline can carry that no catalog scheme covers (Q20).

    Two ids reach the report without ever having been a `SubsidyScheme`: the §10.1 legacy flat
    share, which is subsidy data carried in the *device* catalog for countries that have no
    subsidy catalog yet, and the fallback for a support entry that names no scheme at all. Both
    used to be printed raw — a reader of the Irish report saw a node called `LEGACY_FLAT` — and
    both deserve an honest label rather than an invented programme name: what the legacy shim
    models is a flat percentage with no scheme behind it, and the label says exactly that.
    """

    LEGACY_FLAT_ID = "LEGACY_FLAT"
    LEGACY_FLAT = "flat legacy support share (no catalog)"
    UNATTRIBUTED = "subsidy (unattributed)"


def scheme_display_names(result: LifecycleCostResult) -> Dict[str, str]:
    """Every support id this result can show, mapped to the name a reader sees (Q20).

    Built from the awards of the result's own subsidy decisions, because that is where the
    catalog's `display_name` was captured at evaluation time — a report is regularly rendered in
    a process that never loaded a catalog, so re-reading the data files here would be both a
    seam-4 violation and unreliable. Ids with no award (the legacy shim, an unattributed support
    entry) get their labels from `SubsidySchemeLabels`, and an id the mapping does not know maps
    to itself, so nothing ever renders as an empty cell.

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
    table has always shown it this way (`reporting.py:1190-1191`); stating the rule here keeps
    it from drifting away from the KPI beside it.

    The two halves are *added* rather than chosen between, so an award that one day carries both
    a year-0 payment and a schedule is worth both. Today no solver branch produces such an award
    — a `TaxCreditBenefit` leaves `upfront_amount` at zero and every other euro-valued benefit
    leaves `schedule_amounts` empty — so the sum equals the old either/or for every award that
    exists, and stays correct if that ever changes.
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

    The mapping from the flat `SubsidyAward` union onto the two fields a renderer needs. An
    upfront grant is worth its year-0 amount and needs no note; a tax credit is worth the sum of
    its instalments and says over how many years they arrive; loan terms, operational support and
    a VAT reduction have no euro amount of their own and are described by their terms — the
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
        cap_verdict=award_cap_verdict(award),
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

    Args:
        award: The applied award, read for its rate, its eligible basis and the pre-cap rate.
        total: The award's value as `award_total_amount` computed it, for the product.

    Returns:
        A short arithmetic string with the AVERAGE slot of both factors, or "".
    """
    if award.benefit_rate is None or award.eligible_basis_in_euro is None:
        return ""
    rate_text = f"{award.benefit_rate:.1%}"
    if award.benefit_rate_before_group_cap is not None:
        rate_text = (
            f"{rate_text} (of {award.benefit_rate_before_group_cap:.1%}, cut back by the "
            "cumulation group's combined-rate cap)"
        )
    return (
        f"{rate_text} x {award.eligible_basis_in_euro.average:,.0f} EUR eligible basis = "
        f"{total.average:,.0f} EUR"
    )


def award_cap_verdict(award: SubsidyAward) -> str:
    """What the eligible-cost ceiling did to this award, in the solver's own terms (Q26 F8).

    The second half of an award line a reader cannot otherwise reconstruct: below the ceiling the
    support scales with what was spent, at the ceiling it does not, and the same measure costing
    more would earn exactly the same euros. The solver records the ceiling and the per-slot
    binding flags; this states them.

    Args:
        award: The applied award.

    Returns:
        "capped at X EUR (slots: ...)", "cap not binding", or "" when the scheme declares no cap.
    """
    if award.eligible_basis_cap_in_euro is None:
        return ""
    binding = [slot for slot, bound in award.caps_binding_per_slot.items() if bound]
    if binding:
        return (
            f"capped at {award.eligible_basis_cap_in_euro:,.0f} EUR eligible cost "
            f"({', '.join(binding)})"
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

    Nominal lifetime sums of the **full** allocated timeline in the AVERAGE slot, which is the
    only reading under which the §6.5 zero-sum property stays checkable: a scoped timeline would
    show one leg of every transfer and none of the counter-party's. `total_band` carries the
    grand total as a band so the title can state the uncertainty the ribbons themselves cannot.

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
        """One column per internal party, ordered so transfers between them run left to right (Q23).

        The layout decision, made here rather than in a renderer because it is a property of the
        flows and both renderers have to reach the same answer. Every actor used to share one
        middle column, which meant the tenant-to-landlord levy had nowhere to go: it was drawn as a
        band looping out of the column and back into it, a special case in both Sankey renderers
        and the only ribbon on the page that did not read left to right. Giving each party its own
        column removes the case entirely — the levy becomes an ordinary ribbon between two adjacent
        columns.

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
    society = tuple(result for result in results if has_macroeconomic_accounting(result))
    rented = tuple(
        result for result in results
        if result not in society and result.scope_payer in (Actor.LANDLORD, Actor.TENANT)
    )
    rest = tuple(result for result in results if result not in society and result not in rented)
    owner = tuple(
        result for result in rest
        if result.scope_payer == Actor.OWNER_OCCUPIER
        or any(entry.category == CostCategory.SUBSIDY for entry in result.scoped_timeline().entries)
    )
    return StoryPerspectives(owner=owner or rest, rented=rented, society=society)


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
        if not band.average:
            continue
        is_secondary = partition.is_secondary(category)
        line = StatementLine(
            label=partition.label_of(category),
            category=category,
            npv_in_euro=band.average,
            is_accounting_credit=is_secondary,
        )
        (secondary if is_secondary else primary).append(line)
    if partition.secondary_is_transfer:
        secondary = _transfer_statement_lines(result, partition)
    primary_subtotal = sum(line.npv_in_euro for line in primary)
    secondary_subtotal = sum(line.npv_in_euro for line in secondary)
    net = primary_subtotal + secondary_subtotal
    total = result.total_npv_in_euro.average
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
        per_pair[key] = per_pair.get(key, 0.0) + entry.amount_in_euro.average * discount_factor(
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
    to nobody. Amounts are nominal (undiscounted) lifetime sums of the AVERAGE slot (Q2): a
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
        amount = entry.amount_in_euro.average
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
    volume = sum(flow.amount_in_euro for flow in flows)
    threshold = volume * FlowCounterparties.SMALL_FLOW_SHARE
    kept = [flow for flow in flows if flow.amount_in_euro >= threshold]
    small = [flow for flow in flows if flow.amount_in_euro < threshold]
    merged: Dict[Tuple[str, str], float] = {}
    for flow in small:
        key = (flow.source, flow.target)
        merged[key] = merged.get(key, 0.0) + flow.amount_in_euro
    for (source, target), amount in merged.items():
        kept.append(ActorFlow(source=source, target=target, amount_in_euro=amount, category=None))
    return kept, len(small), sum(flow.amount_in_euro for flow in small)


# ============================================================================ V2 liquidity fan

def band_zero_crossings(series_by_slot: Mapping[Any, List[float]]) -> Dict[Any, Optional[int]]:
    """First non-negative year of each slot's series; None where it never crosses (V2).

    The band form of `results.discounted_payback_year`, and it *calls* that function per slot
    rather than re-implementing the crossing rule, so the fan's annotated interval and the
    printed payback year cannot disagree about what a crossing is (year 0 excluded, first
    crossing only). Keys are whatever the caller's series dict uses — `Slot` members for the
    view-side series, the `"low"/"average"/"high"` strings a `VariantComparison` carries.

    Returns:
        One crossing year per input key, None meaning "never within the horizon" — which is a
        real answer the fan annotates rather than omits.
    """
    from hisim.economics.results import discounted_payback_year

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
    """Year and amount of the deepest out-of-pocket position (AVERAGE slot, nominal) (V2, V9).

    The annotation the nominal panel carries and the milestone V9 restates: the maximum of the
    cumulative nominal cost curve, i.e. the point at which the most money has left the account
    and not come back. Cost is positive here (owner decision Q4), so "deepest" is the curve's
    maximum, and the reading is carried by the annotation rather than by the axis direction.

    Returns:
        `(year, cumulative nominal cost in euro)` — year 0 with 0.0 for an empty series.
    """
    curve = cumulative_nominal_cost_series(result)[Slot.AVERAGE]
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
    world minus its NPV in the AVERAGE world — signed, and *not* absolute widths. For a
    mirrored revenue subject (feed-in, support) the LOW delta can be positive, which puts the
    whole bar on one side of the axis; that is correct and is what the caption explains.
    """

    subject: str
    average_npv_in_euro: float
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
    timeline, each subject's LOW and HIGH read against its own AVERAGE. The chart title has to
    say "uncertainty attribution" rather than "sensitivity" for exactly this reason — no input
    was varied one at a time.

    Rows are sorted by band width descending, and everything below `AttributionThresholds.TOP_N`
    is folded into one row so that the bars still sum to the total.

    Reconciliation: `sum(low_delta) == total.minimum − total.average` and
    `sum(high_delta) == total.maximum − total.average`, including the folded row — validated
    here, not only in the tests, because a tornado whose bars do not sum to the total lies
    quietly.

    Raises:
        CostDataError: If the per-subject deltas do not sum to the total band's edges.
    """
    interest = result.parameters.interest_rate
    per_subject = result.scoped_timeline().npv_by(interest, lambda entry: entry.subject)
    rows = [
        AttributionRow(
            subject=subject,
            average_npv_in_euro=band.average,
            low_delta_in_euro=band.minimum - band.average,
            high_delta_in_euro=band.maximum - band.average,
        )
        for subject, band in per_subject.items()
    ]
    rows.sort(key=lambda row: row.width_in_euro, reverse=True)
    shown, folded = rows[: AttributionThresholds.TOP_N], rows[AttributionThresholds.TOP_N:]
    if folded:
        shown.append(
            AttributionRow(
                subject=AttributionThresholds.FOLD_LABEL,
                average_npv_in_euro=sum(row.average_npv_in_euro for row in folded),
                low_delta_in_euro=sum(row.low_delta_in_euro for row in folded),
                high_delta_in_euro=sum(row.high_delta_in_euro for row in folded),
                is_fold=True,
            )
        )
    total = result.total_npv_in_euro
    for name, actual, expected in (
        ("low", sum(row.low_delta_in_euro for row in shown), total.minimum - total.average),
        ("high", sum(row.high_delta_in_euro for row in shown), total.maximum - total.average),
    ):
        if abs(actual - expected) > ViewTolerances.RECONCILIATION_EPSILON:
            raise CostDataError(
                f"Uncertainty attribution does not reconcile with the total band: the {name} "
                f"deltas sum to {actual:,.2f} EUR but the total's {name} edge is {expected:,.2f} "
                "EUR away from its average."
            )
    return shown


# ============================================================================ V4 bridge

@dataclass(frozen=True)
class BridgeStep:
    """One floating bar of the comparison bridge: a display group's NPV delta (V4).

    `group` is whatever key the caller's category mapping produces (the display-group index for
    every caller in this package), and `delta_in_euro` is variant minus reference in the AVERAGE
    slot. Deltas deliberately carry no band: `high(variant − reference)` is not
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
    same group's NPV in the reference, AVERAGE slot, in the fixed group order rather than sorted
    by magnitude — a bridge whose bars reorder between two reports cannot be read side by side
    (IBCS). A group present in only one variant folds in naturally, because a missing group is an
    explicit zero on that side.

    It takes the two *results* rather than the `VariantComparison` because the comparison object
    publishes deltas per subject and in total, never per category, and re-deriving a category
    split from subject deltas is not possible. The grouping mapping is passed in for the usual
    reason (`fold_categories`): grouping is a display concept, the sums are not.

    Reconciliation: `sum(step.delta) == variant.total_npv − reference.total_npv` in the AVERAGE
    slot, validated here.

    Raises:
        CostDataError: If the steps do not sum to the published NPV delta.
    """
    variant_groups = fold_categories(variant.npv_by_category, mapping)
    reference_groups = fold_categories(reference.npv_by_category, mapping)
    present = set(variant_groups) | set(reference_groups)
    try:
        # Display-group indices sort into the fixed order every chart stacks them in; a mapping
        # whose keys are not orderable keeps first-appearance order instead, which is still fixed.
        keys: List[Any] = sorted(present)  # type: ignore[type-var]
    except TypeError:
        keys = [key for key in list(variant_groups) + list(reference_groups) if key in present]
        keys = list(dict.fromkeys(keys))
    zero = UncertainValue.exact(0.0)
    steps = [
        BridgeStep(
            group=key,
            delta_in_euro=variant_groups.get(key, zero).average - reference_groups.get(key, zero).average,
        )
        for key in keys
    ]
    expected = variant.total_npv_in_euro.average - reference.total_npv_in_euro.average
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
    All figures are nominal (undiscounted) euros of the AVERAGE slot, because that is what a loan
    contract quotes; `effective_annual_rate` is the internal rate of the loan's own flow sequence
    (disbursement and grant in, debt service out), i.e. the Effektivzins a reader can compare
    with a bank's offer.

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
    from hisim.economics.calculators.financing_application import FinancingConstants

    amortization = loan_amortization_series(result)
    horizon = result.parameters.observation_period_in_years
    interest_total = sum(amortization.interest_in_euro)
    principal_repaid = sum(amortization.principal_in_euro)
    grants = -sum(
        entry.amount_in_euro.average
        for entry in result.scoped_timeline().entries
        if entry.category == CostCategory.SUBSIDY
        and entry.subject == FinancingConstants.FINANCING_SUBJECT
        and 0 <= entry.year <= horizon
    )
    disbursement = amortization.disbursement_in_euro
    nominal_loan_flows = sum(
        entry.amount_in_euro.average
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
    in the package uses — the machinery V13 uses for its break-even rate, applied to one flow
    sequence instead of a grid. For a fee-free, grant-free annuity with annual periods it returns
    the nominal rate exactly, which is the null test the spec asks for; a repayment grant strictly
    lowers it because the borrower received money without owing more.

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

    Amounts are nominal AVERAGE-slot euros as the timeline booked them, so an investment is
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
    REPLACEMENT and RESIDUAL_VALUE entries in the AVERAGE slot. Service spans are *derived from
    those events* — install or replacement to the next event, last one to the horizon — rather
    than from the database service life, so a component whose booked replacement interval
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
                    entry.amount_in_euro.average
                    for entry in entries
                    if entry.category == category and entry.year == year
                )
                events.append(LifecycleEvent(year=year, amount_in_euro=amount, kind=kind))
        residual_amount = sum(
            entry.amount_in_euro.average
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


# ============================================================================ V8 treemap

class TileBasis:
    """The two ways a treemap can answer "what does this cost" (V8, owner decision Q11).

    A treemap has no negative areas, so credits cannot be drawn — which leaves two honest
    options and no third one. GROSS shows the cost side only and states the excluded credits in
    the caption; NET_OF_CREDITS nets **per subject across display groups** and clamps a subject
    whose credits exceed its costs at zero, disclosing every clamped subject. Both are rendered
    side by side so the two readings can be compared on real evaluations before one is retired.

    Netting per subject rather than per cell is the only netting that changes anything: a wall's
    subsidy is booked in the support group while its investment is booked in the investment
    group, so a per-cell subtraction would find no credit in any cost cell and reproduce the
    gross panel exactly.
    """

    GROSS = "gross"
    NET_OF_CREDITS = "net"


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

    basis: str
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
    basis: str = TileBasis.GROSS,
) -> CostStructureTiles:
    """Lifetime cost composition as (display group -> subject) tiles, on either basis (V8).

    Pivots the scoped timeline by (subject, display group) in present value, AVERAGE slot, and
    splits each cell into its cost and credit halves — the split is by the *sign of the
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
    result.total_npv_in_euro.average`; the gross tile areas sum to `gross_cost_npv_in_euro`
    (fold included); and the net tile areas minus `clamped_total_in_euro` reproduce that same
    net NPV.

    Raises:
        CostDataError: If the tiles do not sum to the stated gross, if the net areas net of the
            disclosed erasure do not reproduce the net NPV, or if gross minus credits does not
            reproduce the published net NPV.
    """
    interest = result.parameters.interest_rate
    costs: Dict[Tuple[Any, str], float] = {}
    credits: Dict[Tuple[Any, str], float] = {}
    for entry in result.scoped_timeline().entries:
        amount = entry.amount_in_euro.average * discount_factor(interest, entry.year)
        if entry.category not in mapping:
            raise CostDataError(f"No display group declared for cost category {entry.category.value!r}.")
        key = (mapping[entry.category], entry.subject)
        if amount >= 0:
            costs[key] = costs.get(key, 0.0) + amount
        else:
            credits[key] = credits.get(key, 0.0) - amount
    gross_total = sum(costs.values())
    credit_total = sum(credits.values())
    net_total = result.total_npv_in_euro.average
    if abs(gross_total - credit_total - net_total) > ViewTolerances.RECONCILIATION_EPSILON:
        raise CostDataError(
            f"Cost-structure tiles do not reconcile with the published NPV: gross {gross_total:,.2f} "
            f"− credits {credit_total:,.2f} = {gross_total - credit_total:,.2f} EUR, but the "
            f"perspective's NPV is {net_total:,.2f} EUR."
        )
    if basis == TileBasis.GROSS:
        raw: Dict[Tuple[Any, str], float] = dict(costs)
        clamped_tiles: List[TreemapTile] = []
    else:
        raw, clamped_tiles = _net_cells_per_subject(costs, credits)
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
    costs: Mapping[Tuple[Any, str], float], credits: Mapping[Tuple[Any, str], float]
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
    for (_, subject), amount in costs.items():
        subject_costs[subject] = subject_costs.get(subject, 0.0) + amount
    for (_, subject), amount in credits.items():
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
                    group=_dominant_group(subject, costs, credits),
                    subject=subject,
                    area_in_euro=0.0,
                    clamped_from_in_euro=cost_total - credit_total,
                )
            )
            continue
        factor = (cost_total - credit_total) / cost_total
        for (group, cell_subject), amount in costs.items():
            if cell_subject == subject and amount:
                areas[(group, subject)] = amount * factor
    clamped_tiles.sort(key=lambda tile: tile.clamped_from_in_euro or 0.0)
    return areas, clamped_tiles


def _dominant_group(
    subject: str, costs: Mapping[Tuple[Any, str], float], credits: Mapping[Tuple[Any, str], float]
) -> Any:
    """The display group a clamped subject is filed under: the group of its largest cell.

    A clamped subject has no area, but it still needs a group so the disclosure can be coloured
    and grouped like everything else. Cost cells win over credit cells, because a subject that
    had costs belongs where the money was spent; a credit-only subject falls back to the group of
    its largest credit, which for a subsidy line is the support group.
    """
    for cells in (costs, credits):
        candidates = [(amount, group) for (group, cell_subject), amount in cells.items() if cell_subject == subject]
        if candidates:
            return max(candidates, key=lambda pair: pair[0])[1]
    raise CostDataError(f"Cannot place treemap subject {subject!r}: it has neither a cost nor a credit cell.")


def _fold_small_tiles(areas: Mapping[Tuple[Any, str], float]) -> Tuple[List[TreemapTile], int, float]:
    """Turns (group, subject) -> area into tiles, folding the small ones per group.

    The fold is per display group rather than global so that a group never disappears entirely:
    its small subjects collapse into one "other" tile that keeps the group's own total exact,
    which is what lets the sum invariant survive the readability cut. Only drawable (positive)
    areas reach this function; the net basis's zero-area disclosure tiles are appended by the
    caller so they are never folded away.
    """
    total = sum(areas.values())
    threshold = total * TreemapThresholds.SMALL_TILE_SHARE
    tiles: List[TreemapTile] = []
    folded: Dict[Any, float] = {}
    folded_count = 0
    for (group, subject), area in areas.items():
        if area < threshold:
            folded[group] = folded.get(group, 0.0) + area
            folded_count += 1
            continue
        tiles.append(TreemapTile(group=group, subject=subject, area_in_euro=area))
    for group, area in folded.items():
        tiles.append(
            TreemapTile(
                group=group, subject=TreemapThresholds.FOLD_LABEL, area_in_euro=area, is_fold=True
            )
        )
    tiles.sort(key=lambda tile: (-tile.area_in_euro, str(tile.subject)))
    return tiles, folded_count, sum(folded.values())


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
    """One labelled swimlane: its spans and its markers (V9)."""

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
    elsewhere — the asset rows are V7's, the financing lane reads V5's amortization series, the
    milestones read V2's crossings — which is what makes the overview safe. It introduces no new
    numbers and no new fields, only a shared year axis.
    """

    horizon: int
    milestones: Lane
    assets: List[EventStripRow]
    financing: Lane
    support: Lane


def lifecycle_lanes(
    result: LifecycleCostResult, comparison: Optional[Any] = None
) -> LifecycleLanes:
    """Assets, financing, support and milestones on one year axis (V9).

    Delegates rather than re-derives: `component_event_strip` supplies the asset rows,
    `loan_amortization_series` the financing lane (disbursement, the years carrying debt service,
    and the year the outstanding balance reaches zero), `subsidy_decisions` and the
    MODERNIZATION_LEVY entries the support lane, and `band_zero_crossings` plus
    `worst_liquidity_position` the milestones. Because the numbers come from those views, the
    swimlane cannot disagree with the detail charts it summarizes.

    Args:
        result: The perspective to draw.
        comparison: Optional `VariantComparison`. The payback milestone is a *range* between the
            LOW-world and HIGH-world crossings of its savings curve, so without a comparison
            there is no payback question to answer and the milestone is simply absent.

    Returns:
        The four lane groups. Empty lanes are returned empty rather than omitted, so the renderer
        can name every skip in its log line instead of silently drawing fewer lanes.
    """
    horizon = result.parameters.observation_period_in_years
    assets = component_event_strip(result)
    amortization = loan_amortization_series(result)
    financing = Lane(name="Financing")
    if amortization.has_flows():
        service_years = [
            year
            for year, (interest, principal) in enumerate(
                zip(amortization.interest_in_euro, amortization.principal_in_euro)
            )
            if interest or principal
        ]
        financing.events.append(
            LaneEvent(year=0, label="loan disbursement", amount_in_euro=amortization.disbursement_in_euro)
        )
        if service_years:
            financing.spans.append(
                LaneSpan(start_year=service_years[0], end_year=service_years[-1], label="debt service")
            )
            peak_year = max(
                service_years,
                key=lambda year: amortization.interest_in_euro[year] + amortization.principal_in_euro[year],
            )
            peak_amount = (
                amortization.interest_in_euro[peak_year] + amortization.principal_in_euro[peak_year]
            )
            financing.events.append(
                LaneEvent(year=peak_year, label="largest annual debt service", amount_in_euro=peak_amount)
            )
        loan_free = amortization.loan_free_year()
        if loan_free is not None:
            financing.events.append(LaneEvent(year=loan_free, label="loan-free"))
    support = Lane(name="Subsidies & levies")
    for decision in result.subsidy_decisions:
        for award in decision.applied:
            amount = award_total_amount(award).average
            if amount:
                support.events.append(
                    LaneEvent(year=0, label=f"{award.scheme_id} ({decision.measure_subject})",
                              amount_in_euro=amount)
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
            entry.amount_in_euro.average
            for entry in result.scoped_timeline().entries
            if entry.category == CostCategory.MODERNIZATION_LEVY and entry.year == levy_years[0]
        )
        direction = "paid" if annual > 0 else "received"
        support.spans.append(
            LaneSpan(
                start_year=levy_years[0],
                end_year=levy_years[-1],
                label=f"modernization levy {direction} ({abs(annual):,.0f} EUR/a)",
            )
        )
    milestones = Lane(name="Milestones")
    worst_year, worst_amount = worst_liquidity_position(result)
    milestones.events.append(
        LaneEvent(year=worst_year, label="deepest out-of-pocket", amount_in_euro=worst_amount)
    )
    residual_total = sum(
        entry.amount_in_euro.average
        for entry in result.scoped_timeline().entries
        if entry.category == CostCategory.RESIDUAL_VALUE
    )
    milestones.events.append(
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
            milestones.spans.append(
                LaneSpan(
                    start_year=first,
                    end_year=last,
                    label="payback range (LOW to HIGH world)" if last is not None
                    else "payback range (no payback in the HIGH world)",
                )
            )
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
    AVERAGE slot: subsidy schemes, loan disbursements and own capital on the left; the gross
    investment per subject plus planning and removal on the right. `funding_sources_and_uses`
    validates that the two sides balance, which is what makes this a statement rather than a
    picture.
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
        """(source label, use label, euros) pairs whose widths tile both columns exactly.

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

    Sources are one node per subsidy scheme (labelled with the scheme id the entries carry, which
    is where the `subsidy_scheme_id` dimension earns its keep — "state -> KfW 261 -> heat pump"
    reads very differently from one grey "subsidies" node), one node per loan disbursement, and
    own capital as the balancing item. Uses are the gross year-0 investment per subject plus the
    planning and removal categories as their own nodes.

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
            entry.amount_in_euro.average
            for entry in scoped
            if entry.subject == subject and entry.category == CostCategory.INVESTMENT
        )
        if amount:
            uses.append(
                FundingNode(label=subject, amount_in_euro=amount, category=CostCategory.INVESTMENT)
            )
    for category, label in ((CostCategory.PLANNING, "planning"), (CostCategory.REMOVAL, "removal")):
        amount = sum(entry.amount_in_euro.average for entry in scoped if entry.category == category)
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
            by_scheme[label] = by_scheme.get(label, 0.0) - entry.amount_in_euro.average
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
        entry.amount_in_euro.average
        for entry in scoped
        if entry.category == CostCategory.LOAN_DISBURSEMENT
    )
    if loans:
        sources.append(
            FundingNode(label="loan", amount_in_euro=loans, category=CostCategory.LOAN_DISBURSEMENT)
        )
    own_capital = gross - sum(node.amount_in_euro for node in sources)
    if own_capital < -ViewTolerances.RECONCILIATION_EPSILON:
        raise CostDataError(
            f"Year-0 funding sources exceed the gross investment by {-own_capital:,.2f} EUR "
            f"(gross {gross:,.2f} EUR, support and debt "
            f"{sum(node.amount_in_euro for node in sources):,.2f} EUR); own capital cannot be "
            "negative, so this is a data defect rather than a fully funded project."
        )
    sources.append(FundingNode(label="own capital", amount_in_euro=max(own_capital, 0.0)))
    statement = SourcesAndUses(
        sources=sources, uses=uses, gross_year_zero_investment_in_euro=gross
    )
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

    A subject × display-group pivot of the scoped timeline in present value (AVERAGE slot), with
    cost and credit contributions kept apart at entry level, so both margins of the pivot
    reconcile against tested result fields: a subject's cost ribbons sum to its gross present
    cost, cost minus credit is its `npv_by_component` entry, and a group's ribbons sum to the
    folded `npv_by_category`.

    Reconciliation is by construction (the same discounted entries, partitioned two ways) rather
    than by a check, because every ribbon here *is* one bucket of `npv_by`.
    """
    interest = result.parameters.interest_rate
    cells: Dict[Tuple[str, Any, bool], float] = {}
    for entry in result.scoped_timeline().entries:
        if entry.category not in mapping:
            raise CostDataError(f"No display group declared for cost category {entry.category.value!r}.")
        amount = entry.amount_in_euro.average * discount_factor(interest, entry.year)
        key = (entry.subject, mapping[entry.category], amount < 0)
        cells[key] = cells.get(key, 0.0) + abs(amount)
    return [
        SubjectGroupFlow(subject=subject, group=group, amount_in_euro=amount, is_credit=is_credit)
        for (subject, group, is_credit), amount in cells.items()
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

    `net_by_subject` is the difference, i.e. what the component breakdown publishes as the
    subject's NPV; the caption states it beside the extent so the two numbers a reader can find in
    a table (net) and on the chart (extent) are visibly the same data read two ways.
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
    costs: Dict[str, float] = {}
    credits: Dict[str, float] = {}
    by_group: Dict[Tuple[Any, bool], float] = {}
    for flow in flows:
        side = credits if flow.is_credit else costs
        side[flow.subject] = side.get(flow.subject, 0.0) + flow.amount_in_euro
        key = (flow.group, flow.is_credit)
        signed = -flow.amount_in_euro if flow.is_credit else flow.amount_in_euro
        by_group[key] = by_group.get(key, 0.0) + signed
    return SubjectFlowMargins(
        costs_by_subject=costs, credits_by_subject=credits, signed_total_by_group=by_group
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
    construction because the imbalance is booked as an explicit `losses / unattributed` terminal
    rather than absorbed silently.

    `self_consumption_share` is the share of the PV generation that did not leave the house, and
    `self_sufficiency_share` the share of the house's own consumption that did not come from the
    grid; both are None when their denominator is zero (no PV, or no attributed consumption)
    rather than being reported as a misleading zero. `battery_round_trip_loss_in_kwh` is the
    difference between what went into the battery and what came back out — the loss the caption
    names, so that the battery reading as a lossy pass-through is stated rather than inferred.
    """

    sources: List[EnergyBalanceNode]
    sinks: List[EnergyBalanceNode]
    bus_total_in_kwh: float
    self_consumption_share: Optional[float]
    self_sufficiency_share: Optional[float]
    battery_round_trip_loss_in_kwh: Optional[float]


def energy_balance_quantities(result: LifecycleCostResult) -> Dict[EnergyFlowRole, float]:
    """The result's per-subject energy record collapsed onto the balance roles, in annual kWh.

    The one place the subject dimension is dropped: the balance is a picture of the *house*, so
    two PV arrays are one PV generation node. Unknown role keys — a record written by a newer
    extraction than this reader knows — are ignored rather than raising, because an unrecognized
    flow can only make the balance's residual node bigger, which is visible, whereas refusing to
    draw would lose the chart entirely.
    """
    totals: Dict[EnergyFlowRole, float] = {}
    for by_role in result.energy_attribution_by_subject_in_kwh.values():
        for role_name, quantity in by_role.items():
            try:
                role = EnergyFlowRole(role_name)
            except ValueError:
                continue
            totals[role] = totals.get(role, 0.0) + quantity
    return {role: value for role, value in totals.items() if value}


def has_energy_balance(result: LifecycleCostResult) -> bool:
    """Whether the result carries enough device flows for the household energy balance.

    The skip predicate of decision Q16, and deliberately stricter than "is the field non-empty":
    a result whose only flows are the meter's own grid import and export carries no *device*
    information at all, and drawing a two-node diagram of the meter feeding itself was exactly the
    content-free stub the redesign retired. Two device flows is the floor — one source and one
    sink — below which the chart skips itself with a log line.
    """
    quantities = energy_balance_quantities(result)
    devices = [role for role in quantities if role not in EnergyBalanceLayout.METER_ROLES]
    return len(devices) >= EnergyBalanceLayout.MINIMUM_DEVICE_FLOWS


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
    hidden.

    Args:
        result: The evaluated perspective. Its `energy_attribution_by_subject_in_kwh` is the
            source of every quantity; `has_energy_balance` is the caller's skip check.

    Returns:
        The terminals, the bus total and the three derived figures the caption states.

    Raises:
        CostDataError: When the result carries no usable device flows (the located error the skip
            predicate exists to avoid), or when a grid node disagrees with the metered carrier
            quantity it must equal.
    """
    if not has_energy_balance(result):
        raise CostDataError(
            "The household energy balance needs at least "
            f"{EnergyBalanceLayout.MINIMUM_DEVICE_FLOWS} device flows in `LifecycleCostResult."
            "energy_attribution_by_subject_in_kwh`, which this result does not carry (a run "
            "serialized before the field existed, or a component set whose classes the adapter's "
            "`DeviceEnergySpecs` table does not know). Check `views.has_energy_balance` first."
        )
    quantities = energy_balance_quantities(result)
    _check_grid_nodes_against_the_meter(result, quantities)
    bills = carrier_year_one_bills(result)
    electricity = bills.get(EnergyCarrier.ELECTRICITY.value)
    annotations = {
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
            role=role, label=EnergyBalanceLayout.LABELS[role], quantity_in_kwh=quantities[role],
            annotation_in_euro=annotations.get(role),
        )
        for role in EnergyBalanceLayout.SOURCE_ROLES if quantities.get(role)
    ]
    sinks = [
        EnergyBalanceNode(
            role=role, label=EnergyBalanceLayout.LABELS[role], quantity_in_kwh=quantities[role],
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
    consumption = sum(
        quantities.get(role, 0.0) for role in EnergyBalanceLayout.CONSUMPTION_ROLES
    )
    imported = quantities.get(EnergyFlowRole.GRID_IMPORT, 0.0)
    charged = quantities.get(EnergyFlowRole.BATTERY_CHARGE, 0.0)
    discharged = quantities.get(EnergyFlowRole.BATTERY_DISCHARGE, 0.0)
    return EnergyBalanceFlows(
        sources=sources,
        sinks=sinks,
        bus_total_in_kwh=max(source_total, sink_total),
        self_consumption_share=(
            (generation - exported) / generation if generation > 0.0 else None
        ),
        self_sufficiency_share=(
            (consumption - imported) / consumption if consumption > 0.0 else None
        ),
        battery_round_trip_loss_in_kwh=charged - discharged if charged or discharged else None,
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
    """
    metered = result.annual_energy_quantities_by_carrier.get(EnergyCarrier.ELECTRICITY.value)
    if metered is None:
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
    discounting run backwards — and is validated in the view for every grid rate, which is what
    makes the verdict at the parameter rate provably the engine's own verdict.
    """

    rates: List[float]
    series_by_rate: Dict[float, List[float]]
    terminal_by_rate: Dict[float, float]
    parameter_rate: float
    parameter_series_by_slot: Dict[Slot, List[float]]
    #: Zero crossings of the terminal advantage *inside* the grid window, linearly interpolated.
    break_even_rates: List[float]
    #: Differential nominal flow per year (reference − variant), AVERAGE slot, index = year.
    differential_flow_in_euro: List[float]

    def terminal_at_parameter_rate(self) -> float:
        """Terminal wealth advantage at the evaluation's own discount rate.

        The number that has to agree in sign with the V4 bridge's NPV delta: if renovating has
        the lower present cost, the renovator ends up richer, and vice versa.
        """
        return self.parameter_series_by_slot[Slot.AVERAGE][-1]


def wealth_benchmark(
    reference: LifecycleCostResult, variant: LifecycleCostResult
) -> WealthBenchmark:
    """"Or should I just leave the money in the bank?" — as one differential series (V13).

    If the do-nothing household banks the unspent renovation money and the renovating household
    banks its annual savings, both at rate *i*, the wealth *difference* between the two
    strategies is a single series: the differential nominal cash flow, future-valued at *i*. That
    is what this view computes, for the grid on `WealthBenchmarkGrid` plus the evaluation's own
    parameter rate (the one line that also carries a LOW/HIGH band, slot-wise).

    It takes the two results rather than a `VariantComparison` because the comparison publishes
    the differential only as a discounted cumulative curve at the parameter rate, and a rate grid
    needs the undiscounted per-year differential — `annual_cost_series_nominal_in_euro` on both
    sides.

    Reconciliation: `W_i(T) == (1+i)^T · NPV(i)` for every grid rate, with `NPV(i)` recomputed
    from the same differential flows through the module's own `discount_factor` — validated here.

    Raises:
        CostDataError: If the future-value identity fails for any grid rate, which would mean the
            chart and the engine's discounting disagree.
    """
    horizon = variant.parameters.observation_period_in_years
    reference_series = reference.annual_cost_series_nominal_in_euro
    variant_series = variant.annual_cost_series_nominal_in_euro

    def differential(slot: Slot) -> List[float]:
        flows: List[float] = []
        for year in range(horizon + 1):
            left = reference_series[year].slot(slot) if year < len(reference_series) else 0.0
            right = variant_series[year].slot(slot) if year < len(variant_series) else 0.0
            flows.append(left - right)
        return flows

    def future_value_series(flows: List[float], rate: float) -> List[float]:
        series: List[float] = []
        for year in range(len(flows)):
            series.append(
                sum(flows[j] * (1.0 + rate) ** (year - j) for j in range(year + 1))
            )
        return series

    average_flows = differential(Slot.AVERAGE)
    series_by_rate = {rate: future_value_series(average_flows, rate) for rate in WealthBenchmarkGrid.RATES}
    terminal_by_rate = {rate: series[-1] for rate, series in series_by_rate.items()}
    for rate, terminal in terminal_by_rate.items():
        expected = ((1.0 + rate) ** horizon) * sum(
            flow * discount_factor(rate, year) for year, flow in enumerate(average_flows)
        )
        if abs(terminal - expected) > max(
            ViewTolerances.RECONCILIATION_EPSILON, abs(expected) * 1e-9
        ):
            raise CostDataError(
                f"Fixed-interest benchmark breaks the future-value identity at rate {rate:.0%}: "
                f"W(T) = {terminal:,.2f} EUR but (1+i)^T · NPV(i) = {expected:,.2f} EUR."
            )
    parameter_rate = variant.parameters.interest_rate
    parameter_series = {slot: future_value_series(differential(slot), parameter_rate) for slot in Slot}
    return WealthBenchmark(
        rates=list(WealthBenchmarkGrid.RATES),
        series_by_rate=series_by_rate,
        terminal_by_rate=terminal_by_rate,
        parameter_rate=parameter_rate,
        parameter_series_by_slot=parameter_series,
        break_even_rates=_terminal_zero_crossings(terminal_by_rate),
        differential_flow_in_euro=average_flows,
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

    **Replacement years are excluded too** (Q15 as revised on 2026-08-15, after the owner reviewed
    the rendered chart). A replacement is capital expenditure — the same economic object as the
    year-0 investment — so drawing it as a monthly burden while excluding year 0 was inconsistent,
    and no bank's monthly advisory shows replacement spikes; it smooths them into a maintenance
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

    Reconciliation: twelve times the year-1 value is the recurring part of
    `monthly_cost_year1_in_euro`, the series times twelve re-sums to the recurring subset of
    `annual_cost_series_nominal_in_euro`, and twelve times the reserve divided by the annuity
    factor gives the replacement categories' NPV back — all three checked in the tests, all three
    true by construction because this is a filter and a rescaling of the same entries.
    """
    horizon = result.parameters.observation_period_in_years
    per_year = [UncertainValue.exact(0.0) for _ in range(horizon + 1)]
    for entry in result.scoped_timeline().entries:
        if 0 <= entry.year <= horizon and entry.category in BurdenCategories.RECURRING:
            per_year[entry.year] = per_year[entry.year] + entry.amount_in_euro
    replacement_npv = sum(
        value.average
        for category, value in result.npv_by_category.items()
        if category in BurdenCategories.REPLACEMENT
    )
    reserve = (
        replacement_npv * result.parameters.annuity_factor() / BurdenCategories.MONTHS_PER_YEAR
    )
    return MonthlyBurden(
        series=[value.scale(1.0 / BurdenCategories.MONTHS_PER_YEAR) for value in per_year],
        replacement_reserve_per_month=reserve,
    )


def monthly_burden_by_group(
    result: LifecycleCostResult, mapping: Mapping[CostCategory, GroupKey]
) -> List[Dict[GroupKey, float]]:
    """The monthly burden split by display group, AVERAGE slot — the stack behind the bars (V14).

    The same filter as `monthly_burden_series`, folded onto the caller's display groups so the
    chart can stack the bars without adding anything itself. Row order is the year index, and a
    year with no recurring flow folds to an empty dict rather than disappearing, so the two views
    stay index-aligned.
    """
    horizon = result.parameters.observation_period_in_years
    rows: List[Dict[CostCategory, float]] = [{} for _ in range(horizon + 1)]
    for entry in result.scoped_timeline().entries:
        if 0 <= entry.year <= horizon and entry.category in BurdenCategories.RECURRING:
            row = rows[entry.year]
            row[entry.category] = (
                row.get(entry.category, 0.0)
                + entry.amount_in_euro.average / BurdenCategories.MONTHS_PER_YEAR
            )
    return fold_category_matrix(rows, mapping)


# ============================================================================ V15 equity build-up

@dataclass(frozen=True)
class AssetDebtSeries:
    """Book value, outstanding debt and the equity between them, per year (V15).

    Three year-indexed series in the AVERAGE slot plus the interval, if any, in which equity is
    negative — the "underwater" case a bank checks for. The book value is straight-line
    depreciation of every install and replacement the timeline *charged*, on the same basis the
    residual calculator uses, which is why `book_value_in_euro[-1]` equals the booked
    residual-value credit exactly; that endpoint identity is the chart's audit weight.

    `depreciation_life_by_subject` records the life each subject was depreciated over. It is
    *derived from the booked events* — the residual credit and the replacement spacing — never
    read from the catalog, for the same reason V7 derives its spans that way: the chart has to
    show what the timeline charged, so that a disagreement with the catalog is visible instead
    of being drawn away.
    """

    book_value_in_euro: List[float]
    debt_in_euro: List[float]
    equity_in_euro: List[float]
    residual_credit_in_euro: float
    depreciation_life_by_subject: Dict[str, float]
    underwater_interval: Optional[Tuple[int, int]] = None


def asset_debt_series(result: LifecycleCostResult) -> AssetDebtSeries:
    """Asset book value against outstanding debt, and the equity gap between them (V15).

    Book value is built from `component_event_strip`'s events: every charged install or
    replacement steps the curve up by its own amount and then declines linearly to zero over the
    subject's depreciation life. That life is derived from what the timeline booked — from the
    residual credit where there is one (`residual = amount × (install + life − T) / life`, the
    residual calculator's own formula solved for the life), otherwise from the horizon, so that a
    subject with no residual is fully written down at T. Debt is `loan_amortization_series`'s
    outstanding balance, reused rather than recomputed.

    Reconciliation: the book value at the horizon equals the booked residual credit (validated
    here), each install year steps the curve by exactly that event's charged amount, and equity
    is the plain difference of the two published series.

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
        last_event = row.events[-1]
        residual_amount = -row.residual.amount_in_euro if row.residual is not None else 0.0
        residual_total += residual_amount
        life = _depreciation_life(last_event, residual_amount, horizon)
        lives[row.subject] = life
        for event in row.events:
            for year in range(event.year, horizon + 1):
                remaining = max(0.0, 1.0 - (year - event.year) / life)
                book_value[year] += event.amount_in_euro * remaining
    amortization = loan_amortization_series(result)
    debt = amortization.outstanding_balance_in_euro or [0.0] * (horizon + 1)
    equity = [book - max(owed, 0.0) for book, owed in zip(book_value, debt)]
    if abs(book_value[horizon] - residual_total) > max(
        ViewTolerances.RECONCILIATION_EPSILON, abs(residual_total) * 1e-9
    ):
        raise CostDataError(
            f"Asset book value at the horizon is {book_value[horizon]:,.2f} EUR but the timeline "
            f"booked a residual credit of {residual_total:,.2f} EUR; the depreciation basis of "
            "the chart and of the residual calculator have diverged."
        )
    underwater_years = [year for year, value in enumerate(equity) if value < 0]
    return AssetDebtSeries(
        book_value_in_euro=book_value,
        debt_in_euro=list(debt),
        equity_in_euro=equity,
        residual_credit_in_euro=residual_total,
        depreciation_life_by_subject=lives,
        underwater_interval=(underwater_years[0], underwater_years[-1]) if underwater_years else None,
    )


def _depreciation_life(
    last_event: LifecycleEvent, residual_in_euro: float, horizon: int
) -> float:
    """The life the last charged installation is written down over, derived from the booking.

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


# ================================================ the assumptions behind the numbers (Q26 F2)


@dataclass(frozen=True)
class AssumptionRow:
    """One economic assumption: what it is, what it was, and where it came from (Q26 F2).

    The unit of the Assumptions section. `value` is already formatted as text because the rows
    are heterogeneous — a rate, a year, a count of years, a euro band, a kWh figure — and forcing
    them into one numeric type would either lose the band or lose the unit; everything else about
    the row stays data. `source` is a citation where the data layer has one (a database file, the
    country escalation defaults, a tariff contract id) and the literal "configuration" where the
    value is a run parameter, which is a statement rather than a placeholder: it says nobody
    reviewed this number, the run chose it.
    """

    group: str
    name: str
    value: str
    source: str
    #: True for a value the engine computed from the others rather than read (the annuity
    #: factor), so the section can mark it as derived instead of implying it was configured.
    is_computed: bool = False


class AssumptionGroups:
    """The groups the assumptions table is banded into, in the order it prints them.

    Named constants so the section, the tests that check the table's completeness and any future
    export agree on both the spelling and the order. The order is the order a reader reconstructs
    a number in: first the money-over-time frame, then how prices move, then what energy costs,
    then the physical quantities per-unit figures divide by, and last the macroeconomic shadow
    price that only one chapter uses.
    """

    FRAME = "Calculation frame"
    ESCALATION = "Escalation rates"
    TARIFFS = "Energy tariffs"
    QUANTITIES = "Building quantities"
    SOCIETY = "Macroeconomic"
    ORDER = (FRAME, ESCALATION, TARIFFS, QUANTITIES, SOCIETY)
    #: The source text for a value that is a run parameter rather than reviewed data.
    CONFIGURATION_SOURCE = "configuration"


def prices_co2_damage(result: LifecycleCostResult) -> bool:
    """Whether this perspective books the macroeconomic CO2 damage cost (Q26 F2/F3).

    A predicate rather than a report-side membership test, so the Assumptions section can state
    the damage-cost path when *some* perspective of the run priced one — the section itself
    renders on the reference perspective, which never does.
    """
    return CostCategory.CO2_DAMAGE in result.npv_by_category


def economic_assumptions(
    result: LifecycleCostResult, co2_damage_priced: bool = False
) -> List[AssumptionRow]:
    """Every economic assumption this evaluation ran on, with its value and its source (F2).

    The complete set of causes behind the report's consequences (rule 2.9): the interest rate,
    the horizon and the price basis year they discount over; the annuity factor they imply,
    marked as computed; every escalation rate that applied, with the step of the §3.2 fallback
    chain that produced it; the working price, standing charge and feed-in rate of every carrier
    billed; the building quantities the per-unit figures divide by; and the CO2 damage cost where
    the macroeconomic perspective priced one.

    Nothing here is a constant of the view. The parameter half comes from `result.parameters`, the
    resolved half from `result.assumptions`, which the evaluator filled from the cost database and
    `EvaluationInputs`; a result stored before that record existed simply contributes no resolved
    rows, and the section says which half is missing rather than inventing it.

    Args:
        result: The perspective whose assumptions are stated. Any perspective will do — the
            assumption set is a property of the run, not of the view — but the report states it
            once, on the reference perspective of the building chapter.
        co2_damage_priced: Whether any perspective of the run books the CO2 damage cost, which
            decides whether the damage-cost path belongs in the table. It is a property of the
            *run*, not of `result`, so the caller supplies it — see :func:`prices_co2_damage`.

    Returns:
        The rows in `AssumptionGroups.ORDER`, ready to be tabulated.
    """
    params = result.parameters
    configuration = AssumptionGroups.CONFIGURATION_SOURCE
    rows: List[AssumptionRow] = [
        AssumptionRow(AssumptionGroups.FRAME, "interest rate (discount rate)",
                      f"{params.interest_rate:.2%}", configuration),
        AssumptionRow(AssumptionGroups.FRAME, "observation period",
                      f"{params.observation_period_in_years} a", configuration),
        AssumptionRow(AssumptionGroups.FRAME, "price basis year",
                      str(params.price_basis_year if params.price_basis_year is not None
                          else result.simulation_year), configuration),
        AssumptionRow(AssumptionGroups.FRAME, "annuity factor",
                      f"{params.annuity_factor():.6f}",
                      "computed from the interest rate and the horizon", is_computed=True),
    ]
    assumptions = result.assumptions
    if assumptions is not None:
        for label, rate in assumptions.escalation_rates.items():
            rows.append(
                AssumptionRow(
                    AssumptionGroups.ESCALATION,
                    _escalation_row_name(label),
                    f"{rate.rate:.2%} per year",
                    _rate_source(rate, configuration),
                )
            )
        for carrier, tariff in assumptions.tariffs.items():
            source = (
                ", ".join(tariff.source_ids)
                if tariff.source_ids
                else (f"tariff contract {tariff.contract_id}" if not tariff.is_default_contract
                      else configuration)
            )
            contract_note = (
                "database price entry" if tariff.is_default_contract else tariff.contract_id
            )
            rows.append(
                AssumptionRow(
                    AssumptionGroups.TARIFFS,
                    f"{carrier}: working price ({contract_note})",
                    f"{tariff.working_price_in_euro_per_kwh.average:.4f} EUR/kWh",
                    source,
                )
            )
            rows.append(
                AssumptionRow(
                    AssumptionGroups.TARIFFS,
                    f"{carrier}: standing charge",
                    f"{tariff.standing_charge_in_euro_per_year.average:,.2f} EUR/a",
                    source,
                )
            )
            if tariff.feed_in_rate_in_euro_per_kwh is not None:
                rows.append(
                    AssumptionRow(
                        AssumptionGroups.TARIFFS,
                        f"{carrier}: feed-in rate ({tariff.feed_in_kind})",
                        f"{tariff.feed_in_rate_in_euro_per_kwh.average:.4f} EUR/kWh",
                        source,
                    )
                )
    areas = result.reference_areas
    if areas.living_area_in_m2 is not None:
        rows.append(AssumptionRow(AssumptionGroups.QUANTITIES, "living area",
                                  f"{areas.living_area_in_m2:,.1f} m2", configuration))
    if areas.heated_floor_area_in_m2 is not None:
        rows.append(AssumptionRow(AssumptionGroups.QUANTITIES, "heated floor area",
                                  f"{areas.heated_floor_area_in_m2:,.1f} m2", configuration))
    heat_demand = assumptions.annual_heat_demand_in_kwh if assumptions is not None else None
    if heat_demand:
        rows.append(AssumptionRow(AssumptionGroups.QUANTITIES, "annual heat demand",
                                  f"{heat_demand:,.0f} kWh/a", configuration))
    for carrier, quantities in result.annual_energy_quantities_by_carrier.items():
        rows.append(
            AssumptionRow(
                AssumptionGroups.QUANTITIES,
                f"{carrier}: energy bought (annualized)",
                f"{quantities.bought_in_kwh:,.0f} kWh/a",
                "simulation output",
            )
        )
        if quantities.sold_in_kwh:
            rows.append(
                AssumptionRow(
                    AssumptionGroups.QUANTITIES,
                    f"{carrier}: energy sold (annualized)",
                    f"{quantities.sold_in_kwh:,.0f} kWh/a",
                    "simulation output",
                )
            )
    if co2_damage_priced:
        rows.append(
            AssumptionRow(
                AssumptionGroups.SOCIETY,
                "CO2 damage cost (flat over the horizon)",
                f"{params.co2_damage_cost_in_euro_per_ton:,.2f} EUR/t",
                configuration,
            )
        )
    rows.append(
        AssumptionRow(
            AssumptionGroups.SOCIETY,
            "CO2 price scenario (path on the energy bill)",
            params.co2_price_scenario,
            configuration,
        )
    )
    order = {group: index for index, group in enumerate(AssumptionGroups.ORDER)}
    return sorted(rows, key=lambda row: order.get(row.group, len(order)))


def _escalation_row_name(label: str) -> str:
    """The reader's name for one escalation-rate key (`energy:electricity` -> "energy: electricity").

    The keys are stable identifiers chosen by the evaluator; this is the only place they become
    words, so a renamed key changes one line rather than every table that shows it.
    """
    if ":" not in label:
        return f"{label} prices"
    kind, subject = label.split(":", 1)
    return f"{kind}: {subject}"


def _rate_source(rate: ResolvedRate, configuration: str) -> str:
    """The citation for one resolved escalation rate, per the step of the chain that produced it.

    A configured rate cites the run (`configuration`), a rate from the country defaults file cites
    that file's registered sources, and a rate that fell through to the general one says so — the
    three are genuinely different claims about how reviewed the number is.
    """
    if rate.origin == RateOrigin.COUNTRY_DEFAULTS and rate.source_ids:
        return ", ".join(rate.source_ids)
    if rate.origin == RateOrigin.COUNTRY_DEFAULTS:
        return "country escalation defaults"
    if rate.origin == RateOrigin.GENERAL_FALLBACK:
        return f"{configuration} (general fallback)"
    return configuration


# ============================================================ CO2 conversion factors (Q26 F3)


@dataclass(frozen=True)
class Co2FactorRow:
    """One line of the CO2 factors table: a mass and the multiplication that produced it (F3).

    Two shapes in one record, because the table shows them side by side. An *operational* row is
    a carrier: `factor_in_kg_per_unit` is kg per kWh, `quantity` the annualized kWh bought, and
    `annual_mass_in_kg` their product — the mass that repeats every year. An *embodied* row is a
    device: the factor is kg per size unit, the quantity is the installed size, and
    `per_installation_in_kg` is their product, charged `installations` times within the horizon.

    `total_in_kg` is the figure the chart above the table draws, so the two are checkable against
    each other by eye, which is the whole purpose of the row.
    """

    subject: str
    kind: str
    factor_in_kg_per_unit: float
    quantity: float
    quantity_unit: str
    total_in_kg: float
    annual_mass_in_kg: Optional[float] = None
    per_installation_in_kg: Optional[float] = None
    installations: int = 1


class Co2FactorKinds:
    """The two kinds of CO2 factor row, named once for the view and its renderer."""

    OPERATIONAL = "operational"
    EMBODIED = "embodied"


def co2_factor_rows(result: LifecycleCostResult) -> List[Co2FactorRow]:
    """The conversions behind every CO2 mass the report publishes (Q26 F3, rule 2.9).

    Per carrier: emission factor x annualized kWh bought = the mass emitted per year, and that
    times the horizon is the carrier total the chart draws. Per device: embodied factor x
    installed size = the mass per installation, times the number of installations booked within
    the horizon. Both are read from what the engine recorded while it computed the masses — the
    price entry's factor and the device entry's per-unit figure — never re-derived by dividing a
    mass by a quantity, which would reproduce the mass whatever the factor was.

    A result stored before the factors were recorded contributes no rows, and the CO2 section then
    renders as it did before rather than showing a table of divisions.

    Args:
        result: The perspective whose CO2 accounting is stated.

    Returns:
        Operational rows first, then embodied ones, each in the accounting's own order.
    """
    co2 = result.lifecycle_co2_result
    horizon = result.parameters.observation_period_in_years
    rows: List[Co2FactorRow] = []
    for carrier, factor in co2.emission_factor_by_carrier_in_kg_per_kwh.items():
        quantities = result.annual_energy_quantities_by_carrier.get(carrier)
        bought = quantities.bought_in_kwh if quantities is not None else 0.0
        rows.append(
            Co2FactorRow(
                subject=carrier,
                kind=Co2FactorKinds.OPERATIONAL,
                factor_in_kg_per_unit=factor,
                quantity=bought,
                quantity_unit="kWh/a",
                annual_mass_in_kg=factor * bought,
                total_in_kg=co2.operational_co2_by_carrier_in_kg.get(carrier, 0.0),
                installations=horizon,
            )
        )
    for subject, basis in co2.embodied_basis_by_subject.items():
        rows.append(
            Co2FactorRow(
                subject=subject,
                kind=Co2FactorKinds.EMBODIED,
                factor_in_kg_per_unit=basis.factor_in_kg_per_unit,
                quantity=basis.size,
                quantity_unit=basis.size_unit,
                per_installation_in_kg=basis.per_installation_in_kg,
                installations=basis.installations,
                total_in_kg=co2.embodied_by_subject_in_kg.get(subject, 0.0),
            )
        )
    return rows


# ================================================ the levelized cost of heat, spelled out (F6)


@dataclass(frozen=True)
class LevelizedHeatCostDerivation:
    """The LCOH as its own division: numerator, denominator and the quotient (Q26 F6).

    The engine computes `LCOH = NPV x annuity factor / annual heat demand`, i.e. the perspective's
    equivalent annual cost per annual kilowatt hour of heat. Two things about that are invisible
    in the published figure and are what this record exists to state.

    First, the **attribution set**: there is none. The numerator is the perspective's *entire*
    NPV — every subject and every category the perspective books, the PV system and the battery
    included, not a heating-attributable subset — so on a multi-technology building the figure is
    "what the whole installation costs per kWh of heat delivered", which is a defensible number
    and not the one most readers assume. Second, the equivalent form of the denominator: dividing
    by the annuity factor is the same as dividing by the *discounted sum of heat*, the annual
    demand repeated over the horizon and discounted, which is the form the LCOH literature states.
    Both forms are given so a reader can reproduce the figure either way.
    """

    perspective_id: str
    numerator_npv_in_euro: float
    annuity_factor: float
    equivalent_annual_cost_in_euro: float
    annual_heat_demand_in_kwh: float
    discounted_heat_in_kwh: float
    levelized_cost_in_euro_per_kwh: float
    #: The subjects the numerator covers, in timeline order — the truthful statement of what is
    #: attributed to heat, which is everything the perspective books.
    attributed_subjects: Tuple[str, ...] = ()


def levelized_heat_cost_derivation(
    result: LifecycleCostResult,
) -> Optional[LevelizedHeatCostDerivation]:
    """The division behind the published LCOH, or None when the run publishes none (F6).

    Recomputes nothing the engine did not: the numerator is `total_npv_in_euro`, the annuity
    factor is the parameters' own, and the quotient is checked against the published
    `levelized_cost_of_heat_in_euro_per_kwh` — a mismatch would mean the caption is describing a
    different figure from the one the KPI table prints, which is exactly the failure the rule-2.9
    round is about.

    Args:
        result: The perspective to explain. Returns None when it has no LCOH (no heat demand was
            declared, so no figure was published).

    Returns:
        The derivation, or None.

    Raises:
        CostDataError: If the stated division does not reproduce the published LCOH.
    """
    published = result.levelized_cost_of_heat_in_euro_per_kwh
    heat_demand = (
        result.assumptions.annual_heat_demand_in_kwh if result.assumptions is not None else None
    )
    if published is None:
        return None
    annuity = result.parameters.annuity_factor()
    equivalent_annual = result.total_npv_in_euro.average * annuity
    if not heat_demand:
        # The heat demand is only on the result from Q26 F2 onward; without it the division can
        # still be stated backwards from the published figure, which is arithmetically the same
        # number and keeps an archived result explainable.
        heat_demand = equivalent_annual / published.average if published.average else 0.0
    if not heat_demand:
        return None
    quotient = equivalent_annual / heat_demand
    if abs(quotient - published.average) > ViewTolerances.RECONCILIATION_EPSILON:
        raise CostDataError(
            f"Levelized cost of heat does not reconcile for perspective "
            f"{result.perspective_id!r}: NPV {result.total_npv_in_euro.average:,.2f} EUR x annuity "
            f"{annuity:.6f} / {heat_demand:,.0f} kWh = {quotient:.4f} EUR/kWh, but the published "
            f"figure is {published.average:.4f} EUR/kWh."
        )
    return LevelizedHeatCostDerivation(
        perspective_id=result.perspective_id,
        numerator_npv_in_euro=result.total_npv_in_euro.average,
        annuity_factor=annuity,
        equivalent_annual_cost_in_euro=equivalent_annual,
        annual_heat_demand_in_kwh=heat_demand,
        discounted_heat_in_kwh=heat_demand / annuity if annuity else 0.0,
        levelized_cost_in_euro_per_kwh=published.average,
        attributed_subjects=tuple(
            dict.fromkeys(entry.subject for entry in result.scoped_timeline().entries)
        ),
    )


# ==================================================== what each scenario actually changed (F1)


def scenario_assumption_labels(scenario_cube, base_result: LifecycleCostResult) -> Dict[str, str]:
    """Per scenario id: the assumption it changed, with both values (Q26 F1, rule 2.9).

    A scenario row labelled `interest=high` says what was varied but not to what, and the swing
    beside it is then a number without a cause. This reads the cube's own expanded scenario
    definitions — the overrides `evaluate_cube` applied — and pairs each with the central case's
    value of the same field, so the row reads "interest rate 5.00 % (central 3.00 %)".

    The cube is taken untyped for the same reason the report takes it untyped: presentation may
    not import the module that builds one, and neither may this view need to. It is duck-typed
    for `scenarios` (the expanded definitions) alone.

    Args:
        scenario_cube: The evaluated cube, or None.
        base_result: The central cell's result, read for the parameter values a scenario deviates
            from.

    Returns:
        `{scenario id: assumption text}`, empty for the base cell and for any scenario whose
        definition the cube did not keep.
    """
    if scenario_cube is None:
        return {}
    labels: Dict[str, str] = {}
    for scenario in getattr(scenario_cube, "scenarios", []):
        parts = [
            _assumption_text(field_name, value, base_result, is_overlay=False)
            for field_name, value in getattr(scenario, "parameter_overrides", {}).items()
        ]
        parts.extend(
            _assumption_text(field_name, value, base_result, is_overlay=True)
            for field_name, value in getattr(scenario, "data_overlays", {}).items()
        )
        if parts:
            labels[scenario.id] = "; ".join(parts)
    return labels


class ScenarioAssumptionFormat:
    """How a scenario's changed value is written out (Q26 F1).

    Scenario axes address `EconomicParameters` fields by dotted path, and the values behind those
    paths are rates, counts of years, scenario names and — for a data overlay — a whole price
    band. `PERCENT_SUFFIXES` names the path stems whose values are fractions and therefore read as
    percentages; everything else is printed as it is stored, because inventing a unit for an
    unknown field would be a guess in a section whose entire purpose is that nothing is guessed.
    """

    PERCENT_SUFFIXES = ("rate", "rates", "share", "shares")
    #: What the central case is called when a data overlay replaced shipped data outright.
    AS_SHIPPED = "as shipped"


def _assumption_text(
    field_name: str, value: Any, base_result: LifecycleCostResult, is_overlay: bool
) -> str:
    """One scenario override as "<field> <scenario value> (central <central value>)" (F1)."""
    scenario_text = _format_assumption_value(field_name, value)
    if is_overlay:
        return f"{field_name} {scenario_text} (central {ScenarioAssumptionFormat.AS_SHIPPED})"
    central = _central_value(field_name, base_result)
    if central is None:
        return f"{field_name} {scenario_text} (central {ScenarioAssumptionFormat.AS_SHIPPED})"
    return f"{field_name} {scenario_text} (central {_format_assumption_value(field_name, central)})"


def _central_value(field_name: str, base_result: LifecycleCostResult) -> Any:
    """The central case's value of one dotted `EconomicParameters` path, or None when unset.

    Walks the same path a scenario override writes to, over the *base cell's* parameters, so the
    comparison is against what was actually priced rather than against the dataclass defaults. A
    dict path whose key is absent — an escalation rate that fell through to the country defaults
    — returns the resolved rate the result recorded when there is one, and None otherwise, which
    the caller renders as "as shipped".
    """
    parts = field_name.split(".")
    current: Any = base_result.parameters
    for index, part in enumerate(parts):
        if isinstance(current, dict):
            match = next((value for key, value in current.items() if _key_name(key) == part), None)
            if match is None:
                return _recorded_rate(parts, base_result)
            current = match
            continue
        if not hasattr(current, part):
            return None
        current = getattr(current, part)
        if current is None and index < len(parts) - 1:
            return None
    return current


def _recorded_rate(parts: List[str], base_result: LifecycleCostResult) -> Optional[float]:
    """The escalation rate the run resolved for a dict path the parameters do not state (F1).

    An axis on `energy_price_escalation_rates.ELECTRICITY` in a run that configured no explicit
    rate for electricity would otherwise be compared against nothing, when the run in fact priced
    electricity at the country defaults file's rate. That rate is on the result (Q26 F2), keyed
    by carrier, so the central value is knowable and is used.
    """
    assumptions = base_result.assumptions
    if assumptions is None or len(parts) != 2:
        return None
    prefixes = {
        "energy_price_escalation_rates": "energy",
        "investment_price_escalation_rates": "investment",
    }
    prefix = prefixes.get(parts[0])
    if prefix is None:
        return None
    for label, rate in assumptions.escalation_rates.items():
        if ":" not in label:
            continue
        kind, subject = label.split(":", 1)
        if kind == prefix and subject.upper() == parts[1].upper():
            return rate.rate
    return None


def _key_name(key: Any) -> str:
    """The name a dotted scenario path uses for a dict key (an enum's `name`, else its text)."""
    return getattr(key, "name", str(key))


def _format_assumption_value(field_name: str, value: Any) -> str:
    """A scenario value as text: a percentage for a rate path, a band for an overlay, else as-is."""
    if isinstance(value, dict):
        return "/".join(
            f"{key} {value[key]:,.0f}" for key in ("min", "avg", "max") if key in value
        ) or str(value)
    if isinstance(value, float) and field_name.split(".")[0].endswith(
        ScenarioAssumptionFormat.PERCENT_SUFFIXES
    ):
        return f"{value:.2%}"
    return str(value)


# ============================================ why a scenario row did not move at all (Q27 R2)


class ZeroSwingCauses:
    """What each scenario axis prices, so an inert axis can name its own cause (Q27 R2).

    A scenario row whose swing is exactly zero is the one row a reader cannot interpret: it looks
    either like a bug in the cube or like a reassuring result ("carbon prices do not matter here"),
    and neither is what it means. It means the axis moved a parameter that nothing in *this* run's
    timeline depends on. This namespace holds the two field paths whose inert case is diagnosable
    from a stored result, plus the honest fallback for every other one — the module refuses to
    guess a cause it cannot read off the data.
    """

    #: The CO2-price scenario axis; inert when no carrier books a carbon-price flow.
    CO2_PRICE_FIELD = "co2_price_scenario"
    #: The per-carrier energy escalation axis; inert when that carrier is not billed at all.
    ENERGY_ESCALATION_STEM = "energy_price_escalation_rates"
    #: The price-entry field whose zero value is the usual reason no carbon price is booked.
    EXPOSURE_PARAMETER = "co2_price_exposure"
    #: Said when the stored result cannot name the inert parameter. Deliberately not a guess.
    UNKNOWN = "no priced flow depends on this axis in this run"


def zero_swing_notes(
    scenario_cube, base_result: LifecycleCostResult, swings: Dict[str, float]
) -> Dict[str, str]:
    """Per scenario id with an exactly-zero swing: why that axis did nothing here (Q27 R2).

    The scenarios table publishes a swing per row; a `+0` row is read as either a bug or a
    finding, and it is neither. This derives the cause from the base cell's own timeline — which
    flows the run actually books — never from a table of known axes, so a run whose carbon price
    *is* priced gets no note and an axis this function cannot diagnose says so instead of
    inventing a reason.

    Only exact zeros qualify. A swing of a few cents is a real, tiny effect and must keep reading
    as one; rounding it into "inert" would be the same over-claim the note exists to prevent.

    Args:
        scenario_cube: The evaluated cube, or None. Duck-typed for `scenarios` and `base_id`, as
            everything presentation hands this module is.
        base_result: The base cell's result for the reference perspective — the timeline the
            causes are read from.
        swings: Per-scenario swing of the headline KPI, base included, as the table prints them.

    Returns:
        `{scenario id: cause}` for the zero-swing rows only; empty when the cube has none.
    """
    if scenario_cube is None:
        return {}
    base_id = getattr(scenario_cube, "base_id", None)
    notes: Dict[str, str] = {}
    for scenario in getattr(scenario_cube, "scenarios", []):
        if scenario.id == base_id or swings.get(scenario.id) != 0.0:
            continue
        fields = list(getattr(scenario, "parameter_overrides", {})) + list(
            getattr(scenario, "data_overlays", {})
        )
        causes = [cause for cause in (_inert_axis_cause(name, base_result) for name in fields) if cause]
        notes[scenario.id] = "; ".join(causes) if causes else ZeroSwingCauses.UNKNOWN
    return notes


def _inert_axis_cause(field_name: str, base_result: LifecycleCostResult) -> str:
    """The data-derived cause for one overridden field, or "" when it cannot be named (R2)."""
    if field_name == ZeroSwingCauses.CO2_PRICE_FIELD:
        return _co2_axis_cause(base_result)
    if field_name.split(".")[0] == ZeroSwingCauses.ENERGY_ESCALATION_STEM and "." in field_name:
        carrier = field_name.split(".", 1)[1]
        if not _carrier_is_billed(base_result, carrier):
            return f"the run books no {carrier.lower()} bill for the rate to escalate"
    return ""


def _co2_axis_cause(base_result: LifecycleCostResult) -> str:
    """Why a CO2-price axis is inert: no carbon-price flow is booked, and by which entries (R2).

    The engine books an `ENERGY_CO2_PRICE` entry only for a carrier whose price entry declares
    `co2_price_exposure > 0` (§3.5). So the absence of that category in the stored timeline *is*
    the zero exposure, and the carriers to name are the ones the run bills — read from the same
    timeline rather than from the database, which a stored result no longer has.
    """
    entries = base_result.timeline.entries
    if any(entry.category == CostCategory.ENERGY_CO2_PRICE for entry in entries):
        return ""
    carriers = sorted(
        {
            entry.subject
            for entry in entries
            if entry.subject_kind == SubjectKind.CARRIER and entry.category in ViewCategories.BILL_CATEGORIES
        }
    )
    if not carriers:
        return ""
    names = " and ".join(carrier.lower() for carrier in carriers)
    noun = "entry declares" if len(carriers) == 1 else "entries declare"
    return (
        f"no CO2-price flow is booked in this run — the {names} price {noun} no direct "
        f"CO2-price exposure ({ZeroSwingCauses.EXPOSURE_PARAMETER} = 0)"
    )


def _carrier_is_billed(base_result: LifecycleCostResult, carrier: str) -> bool:
    """Whether the run books any bill entry for a carrier named as a scenario path suffix (R2)."""
    return any(
        entry.subject_kind == SubjectKind.CARRIER
        and entry.subject.upper() == carrier.upper()
        and entry.category in ViewCategories.BILL_CATEGORIES
        for entry in base_result.timeline.entries
    )
