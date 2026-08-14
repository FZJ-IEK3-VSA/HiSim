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
from typing import Any, Dict, Hashable, List, Mapping, Optional, Tuple, TypeVar

from hisim.economics.calculators.subsidy_application import nominal_support_from_entries
from hisim.economics.carriers import EnergyCarrier
from hisim.economics.results import LifecycleCostResult
from hisim.economics.subsidies import SubsidyAward
from hisim.economics.timeline import Actor, CategoryRules, CostCategory, discount_factor
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
    """Interest and principal per year 0..T (index = year), nominal euros (§4.4).

    The two components of a financed perspective's debt service, split so the report can stack
    them and a reviewer can see the shape an annuity loan has (falling interest, rising
    principal) or fails to have. Both lists always span the full horizon, zero-padded, so they
    index by year directly and can be zipped with each other and with any other year series in
    this module. The disbursement itself is *not* here — it is a year-0 flow of the
    `LOAN_DISBURSEMENT` category and shows up in the investment waterfall instead.
    """

    interest_in_euro: List[float]
    principal_in_euro: List[float]

    def has_flows(self) -> bool:
        """True when the perspective is financed at all."""
        return any(self.interest_in_euro) or any(self.principal_in_euro)


def loan_amortization_series(
    result: LifecycleCostResult, slot: Slot = Slot.BEST_ESTIMATE
) -> LoanAmortization:
    """The loan's interest/principal split per year (§4.4).

    Reads the `LOAN_INTEREST` and `LOAN_PRINCIPAL` entries off the scoped timeline — it does not
    re-run the schedule builder in `financing.py`, so what the chart shows is by construction
    the debt service the NPV was computed from. Callers use `LoanAmortization.has_flows()` to
    decide whether the perspective is financed at all and skip the chart when it is not, which
    is the common case (cash purchase).

    Replaces `reporting.py:517-526` (the amortization chart's accumulation loop).
    """
    horizon = result.parameters.observation_period_in_years
    interest_per_year = [0.0] * (horizon + 1)
    principal_per_year = [0.0] * (horizon + 1)
    for entry in result.scoped_timeline().entries:
        if not 0 <= entry.year <= horizon:
            continue
        if entry.category == CostCategory.LOAN_INTEREST:
            interest_per_year[entry.year] += entry.amount_in_euro.slot(slot)
        elif entry.category == CostCategory.LOAN_PRINCIPAL:
            principal_per_year[entry.year] += entry.amount_in_euro.slot(slot)
    return LoanAmortization(interest_in_euro=interest_per_year, principal_in_euro=principal_per_year)


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


def award_total_amount(award: SubsidyAward) -> UncertainValue:
    """The amount an award is worth in total, nominal (§5.4).

    A scheduled payout (a tax credit spread over N years) is worth the sum of its instalments,
    not its — zero — upfront amount; every other kind is worth its upfront amount. The awards
    table has always shown it this way (`reporting.py:1190-1191`); stating the rule here keeps
    it from drifting away from the KPI beside it.
    """
    return UncertainValue.sum(award.schedule_amounts) if award.schedule_amounts else award.upfront_amount


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
