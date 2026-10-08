"""Derived views on a lifecycle cost result (cost_spec.md §2.4).

A view reshapes one evaluated `LifecycleCostResult` into the form one report figure needs: a per-year series, a
per-carrier bill, a payer x category pivot, a per-subject waterfall. Views are pure functions returning plain data,
with no formatting; they never re-run the engine or apply assumptions of their own, so every report reads one
definition of each figure. Figures the engine publishes itself (NPV, EAC, `npv_by_category`) stay on the result in
`results.py`.

Views read `result.scoped_timeline()`, the flows the perspective reports on, except `payer_category_npv_pivot`, which
needs every payer. Every present value goes through `timeline.discount_factor`. Display grouping of categories stays in
presentation, but the group sums come from `fold_categories` and `fold_category_matrix`.
"""

from __future__ import annotations

import enum
from collections.abc import Hashable
from dataclasses import dataclass, field
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Set,
    Tuple,
    TypeVar,
    Union,
)

from hisim.economics.calculators.aggregation import TimelineAggregation
from hisim.economics.calculators.financing_application import FinancingConstants
from hisim.economics.calculators.subsidy_application import nominal_support_from_entries
from hisim.economics.carriers import EnergyCarrier, EnergyFlowRole, bill_subjects
from hisim.economics.catalog_entries import CostDataError
from hisim.economics.numerics import bisect_root
from hisim.economics.results import (
    AnywayBasisKinds,
    EvaluationMatrix,
    LifecycleCostResult,
    ModernizationLevySummary,
    RateOrigin,
    ResolvedRate,
    VariantComparison,
    PaybackEnvelope,
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
    """Cost-category sets the views select on.

    These are selection sets of the engine's own categories ("what counts as a bill", "what moves money in year 0"),
    not display groups; the display grouping lives in `presentation_style.py`.
    """

    #: The categories that make up an energy bill; feed-in revenue is deliberately not one of them.
    #: The same object as the kernel's bill categories, so the report and the plausibility panel
    #: agree on what a bill is.
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
    """Return the display group of one category.

    The single lookup every folding view uses.

    Args:
        category: The category to place.
        mapping: The caller's category -> group mapping, usually `PresentationStyle.CATEGORY_TO_GROUP`.

    Returns:
        The group key `mapping` declares for `category`.

    Raises:
        CostDataError: If `mapping` declares no group for `category`; the message names the groups on offer.
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
    """Fold a category -> amount map onto groups, summing slot-wise.

    Example: `fold_categories(result.npv_by_category, PresentationStyle.CATEGORY_TO_GROUP)`. Grouping is a display
    concept, but the sums are computed here (§2.4).

    Args:
        values: Category -> amount in euro, as floats or `UncertainValue`s; folding keeps them nominal or discounted as
            given.
        mapping: Category -> group key; must cover every category in `values`.

    Returns:
        Group key -> summed amount, of the same type as the inputs; only groups that received a category appear.

    Raises:
        CostDataError: If `values` contains a category `mapping` does not declare.
    """
    folded: Dict[GroupKey, Any] = {}
    for category, amount in values.items():
        group = _display_group_of(category, mapping)
        folded[group] = folded[group] + amount if group in folded else amount
    return folded


def fold_category_matrix(
    matrix: List[Dict[CostCategory, Any]], mapping: Mapping[CostCategory, GroupKey]
) -> List[Dict[GroupKey, Any]]:
    """Apply `fold_categories` to each year of a year x category matrix.

    Used for the stacked annual cash-flow chart. Row order (the year index) is kept, and a year with no flows folds to
    an empty dict.
    """
    return [fold_categories(row, mapping) for row in matrix]


# ---------------------------------------------------------------------------- time series

def cumulative_discounted_cost_series(result: LifecycleCostResult) -> Dict[Slot, List[float]]:
    """Return the cumulative discounted cost per slot over years 0..T (index = year).

    A slot is one of the three band values (minimum, best estimate, maximum). The last point of each series is that
    slot's reported NPV; flows outside the horizon are excluded, as they are from the NPV.
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
    """Return nominal euros per (year, category) for years 0..T, index = year.

    The ungrouped input of the annual cash-flow chart; presentation folds it with `fold_category_matrix`. Nominal means
    undiscounted, the "what leaves the account in year N" view. Costs are positive and revenue/support negative.

    Args:
        result: The evaluated perspective.
        slot: Which band value to return; a stacked bar cannot show a band.
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
    """Interest, principal and outstanding balance of the loan per year 0..T (index = year), nominal (§4.4).

    All lists span the full horizon, zero-padded, so they index by year. The disbursement is a year-0
    `LOAN_DISBURSEMENT` flow shown in the investment waterfall; it is carried in `disbursement_in_euro` because the
    balance line starts from it. `outstanding_balance_in_euro` is the disbursement minus the cumulative principal
    repaid up to and including that year, so it matches the bars; a fully amortizing loan ends at zero within float
    tolerance.
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
        """Return the first year the outstanding balance reaches zero, or None while debt remains.

        The "loan-free" milestone of the milestone chart. It reads the balance series, not the loan term, so a term
        reaching past the horizon gives None. An unfinanced perspective also gives None. The tolerance is
        `ViewTolerances.BALANCE_EPSILON`.
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
    """Return the loan's interest/principal split per year (§4.4).

    Reads the `LOAN_INTEREST` and `LOAN_PRINCIPAL` entries of the scoped timeline instead of re-running the schedule in
    `financing.py`, so the chart shows the debt service the NPV was computed from. The outstanding balance is built
    from the same entries. Callers check `has_flows()` and skip the chart for a cash purchase.
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
    """Return the running total of operational CO2 in kg over the horizon (index = year), undiscounted (§3.8).

    A cumulative sum of `LifecycleCo2Result.operational_co2_by_year_in_kg`. Emissions are masses and are never
    discounted. The last element is the lifecycle operational total.
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

    All timeline entries sharing a year, a subject and a category, added up. It carries the nominal band as booked and
    the discounted best-estimate value, so a reader can check a year's discount factor by division.
    """

    year: int
    subject: str
    category: CostCategory
    nominal_in_euro: UncertainValue  # as booked, undiscounted, full min/best_estimate/max band
    discounted_best_estimate_in_euro: float  # BEST_ESTIMATE slot × discount_factor(interest, year)


@dataclass(frozen=True)
class TimelineDetailYear:
    """One year of the detail table: its rows and their subtotal.

    The totals cover exactly the rows in `rows`, so the table adds up on screen.
    """

    year: int
    rows: List[TimelineDetailRow]
    nominal_total_in_euro: UncertainValue  # sum of the rows' nominal bands, slot-wise
    discounted_total_best_estimate_in_euro: float  # that sum's BEST_ESTIMATE slot, discounted to year 0


def timeline_detail_rows(result: LifecycleCostResult) -> List[TimelineDetailYear]:
    """Return the §3.6 timeline as a verification table of (year, subject, category) rows with subtotals.

    Uses the scoped timeline. Duplicate cells are added up; cells that are zero in every slot within
    `ViewTolerances.DETAIL_ROW_EPSILON` are dropped as float noise. Rows within a year are ordered by their nominal
    best-estimate amount.
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
    """Return who pays which cost block, in present value (§6.5).

    Taken on the full timeline, because the zero-sum check needs every payer, and without the unallocated SYSTEM payer.
    Presentation folds the inner map onto display groups.
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
    """Annuitize one NPV over the observation period, giving its equivalent annual cost (EAC, §3.4).

    The EAC is the constant euro-per-year payment with the same present value over the horizon. The annuity factor
    comes from `result.parameters`, so every annuitized figure in a report uses the same rate and horizon as the
    headline KPI.
    """
    return npv.scale(result.parameters.annuity_factor())


def equivalent_annual_cost_by_category(
    result: LifecycleCostResult,
) -> Dict[CostCategory, UncertainValue]:
    """Return the equivalent annual cost per category of the whole perspective (§3.4).

    `npv_by_category` in euro per year; the categories still sum to the headline EAC.
    """
    return {
        category: equivalent_annual_cost_of(npv, result)
        for category, npv in result.npv_by_category.items()
    }


def subject_equivalent_annual_cost_by_category(
    result: LifecycleCostResult,
) -> Dict[str, Dict[CostCategory, UncertainValue]]:
    """Return the equivalent annual cost per subject and category, the `component_costs.csv` figure.

    A subject is one costed thing on the timeline, such as a heat pump. The export and any report showing this figure
    read this one definition.
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
    """One carrier's year-1 bill, its annual volume and the effective price they imply.

    A sanity check for the report: year-1 cost divided by volume must give a recognizable price (about 0.3 EUR/kWh for
    German household electricity, 0.10 for gas); a factor of 1000 or zero points to a unit mix-up or a missing tariff.
    The volume is in kWh for every carrier, fuels included, because per-ton and per-litre prices are converted when the
    price is resolved. Figures are nominal best-estimate euros, except `year_one_band_in_euro`, which keeps the full
    band.
    """

    carrier: str
    #: Year-1 amounts per category, BEST_ESTIMATE slot; includes the feed-in credit where it belongs
    #: to this carrier (see `carrier_year_one_bills`).
    by_category_in_euro: Dict[CostCategory, float]
    #: Sum of `by_category_in_euro` **without** feed-in revenue — what the energy actually cost.
    total_excluding_feed_in_in_euro: float
    #: Annualized bought volume in kilowatt-hours, for every carrier alike.
    annual_quantity_in_kwh: float
    #: `total_excluding_feed_in_in_euro / annual_quantity_in_kwh`, 0.0 for an unbilled carrier.
    effective_price_in_euro_per_kwh: float
    #: The carrier's own year-1 flows as a band (feed-in revenue excluded, whichever subject of
    #: `carriers.bill_subjects` it is booked under).
    year_one_band_in_euro: UncertainValue


def carrier_year_one_bills(result: LifecycleCostResult) -> Dict[str, CarrierYearOneBill]:
    """Return the year-1 bill per carrier with its implied effective price (§8).

    The carrier's revenue subject (`carriers.bill_subjects`, the feed-in one for electricity) is included in the
    carrier's category map, but not in `total_excluding_feed_in_in_euro`, the price numerator, nor in the band: a
    credit is not part of what a kWh costs.
    """
    bills: Dict[str, CarrierYearOneBill] = {}
    entries = [entry for entry in result.scoped_timeline().entries if entry.year == 1]
    for carrier, quantities in result.annual_energy_quantities_by_carrier.items():
        subjects = bill_subjects(carrier)
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
            entry.amount_in_euro
            for entry in entries
            if entry.subject in subjects and entry.category != CostCategory.FEED_IN_REVENUE
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
    """One subject's year-0 money movement: the investment waterfall steps and their net.

    Feeds the investment waterfall of report section 2: device + installation + planning + removal - subsidies - loan
    disbursement = net outflow. Only the `ViewCategories.YEAR_ZERO_CATEGORIES` are carried, in that order, and only
    when non-zero; subsidies and the disbursement are negative steps.
    """

    subject: str
    #: BEST_ESTIMATE-slot year-0 amounts, keyed by category, only the categories that are non-zero.
    by_category_in_euro: Dict[CostCategory, float] = field(default_factory=dict)

    @property
    def net_outflow_in_euro(self) -> float:
        """What actually leaves the payer's account in year 0 (subsidies and loans reduce it)."""
        return sum(self.by_category_in_euro.values())


def year_zero_build_up(result: LifecycleCostResult) -> Dict[str, YearZeroBuildUp]:
    """Return each subject's year-0 build-up: device + planning + removal - subsidies - loan = net.

    Reads the scoped timeline, so it agrees with the component table beneath the waterfall; a tenant perspective has no
    year-0 flows and so no build-up. Subjects with no year-0 flow are absent.
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
    """How far support covers one subject's gross investment, in nominal best-estimate euros.

    Used by the subsidy composition bars and the investment waterfall. `subsidy_in_euro` is clamped to the gross, so
    `share_of_gross` is always in [0, 1]; see `subsidy_share_of_gross`.
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
    """Return each subject's funded share of its year-0 gross investment, in nominal euros (§5.4).

    Nominal support can exceed the year-0 gross (support paid over several years, or attached to later investment), so
    the reported support is `min(subsidy, gross)`. This is the only place that clamp is applied; the unclamped figure
    is `ComponentCostBreakdown.subsidies_nominal_in_euro`. Subjects without a positive gross investment are absent.
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
    """Return each subject's year-0 gross investment minus nominal support, as a band.

    The "Net" column of the investment table. Unlike `SubsidyShare.net_in_euro` it is unclamped and slot-wise, so it
    can go negative when support exceeds the gross. Subjects without a positive gross investment are absent.
    """
    return {
        subject: breakdown.investment_gross_in_euro - breakdown.subsidies_nominal_in_euro
        for subject, breakdown in result.component_breakdowns.items()
        if breakdown.investment_gross_in_euro.maximum > 0
    }


def payer_npv_total(result: LifecycleCostResult) -> UncertainValue:
    """Return the sum of all payer NPVs, the system total the §6.5 zero-sum check reconciles against.

    Includes the unallocated SYSTEM payer. Report section 6b prints it above the payer bars, which must add up to it,
    because an allocation ruleset moves money between actors without creating or destroying any.
    """
    return UncertainValue.sum(result.npv_by_payer.values())


def scheme_display_names(result: LifecycleCostResult) -> Dict[str, str]:
    """Map every support id this result can show to the name a reader sees.

    Built from the awards of the result's own subsidy decisions, which captured the catalog's `display_name` at
    evaluation time, so a report renders without loading a catalog. Ids without an award get their labels from
    `SubsidySchemeLabels`, and unknown ids map to themselves, so no cell is ever empty. Its consumer is a subsidy
    Sankey view not built yet.

    Args:
        result: The evaluated perspective being rendered.

    Returns:
        `{scheme id: display name}`, always including the legacy-shim id and the empty string (unattributed support),
            so callers can look up `entry.subsidy_scheme_id or ""` directly.
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
    """Return the total nominal amount an award is worth (§5.4).

    The upfront amount plus the sum of any scheduled instalments, so a tax credit paid over N years is worth its
    instalments. `describe_award` is what renderers call; this is its euro half, for the KPI export and the chart data.

    Args:
        award: One entry of `SubsidyDecision.applied`.

    Returns:
        The nominal, undiscounted euro band the award pays in total.
    """
    return UncertainValue.sum([award.upfront_amount, *award.schedule_amounts])


@dataclass
class AwardPresentation:
    """One applied award reduced to what a reader needs to know about it (§5.4).

    The single source for the markdown decision list, the HTML decision cards and the awards table, so the three agree.
    `total_in_euro` is None exactly when the award carries no euro amount (loan terms, a per-kWh operational rate, a
    reduced VAT rate), because another calculator books its value; `payout_note` then states its terms. A loan's
    repayment grant is stated as a share, not in euros, because the solver values it on the gross measure cost while
    `calculators/financing_application` applies it to the loan principal, so there is no single euro figure.
    """

    scheme_id: str
    payout_kind: str
    total_in_euro: Optional[UncertainValue]
    payout_note: str
    caps_binding: Tuple[str, ...]
    #: The friendly name a reader sees; equal to `scheme_id` when the catalog had none.
    display_name: str = ""
    #: The multiplication that produced `total_in_euro`, as `rate x basis = amount`, or
    #: the empty string for an award whose form states no rate — a lump sum, a per-unit amount,
    #: loan terms, a VAT reduction — where `payout_note` already carries the form's own terms.
    arithmetic: str = ""
    #: What the eligible-cost ceiling did: "cap not binding", "capped at X EUR" or the empty
    #: string where the scheme declares no cap at all. Read from the solver's recorded decision
    #: data, never re-derived from the amount.
    cap_verdict: str = ""


def describe_award(award: SubsidyAward) -> AwardPresentation:
    """Return what an applied award is worth and how it is paid out, by payout kind (§5.2, §5.4).

    An upfront grant is worth its year-0 amount and needs no note; a tax credit is worth its instalments and says over
    how many years; loan terms, operational support and a VAT reduction have no euro amount and are described by their
    terms (rate and term, per-kWh rate and duration, reduced rate).

    Args:
        award: One entry of `SubsidyDecision.applied`.

    Returns:
        The renderable form; `total_in_euro` is None only for the kinds without a euro amount.
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
    """Return `rate x eligible basis = amount` for the two percentage forms, else "".

    Example: "20.0% x 30,000 EUR eligible basis = 6,000 EUR". The solver stores both factors on the award, so this only
    formats stored data. When a cumulation group's combined-rate cap or the EU state-aid overall cap cut the rate, each
    is named where it applied, e.g. "17.5% (of 20.0%, cut back by the cumulation group's combined-rate cap)". Lump-sum,
    per-unit, loan-terms and VAT forms return "", since `describe_award` states their terms.

    Args:
        award: The applied award; read for its rate, eligible basis and pre-cap rates.
        total: The award's value from `award_total_amount`.

    Returns:
        The arithmetic with the best-estimate value of both factors, or "".
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
    """Return what the eligible-cost ceiling did to this award, in the solver's terms.

    Below the ceiling support scales with spending; at the ceiling a more expensive measure earns the same euros. The
    solver records the ceiling and which slots it bound in; this states them.

    Args:
        award: The applied award.
        binding: The slots whose cap bound, if the caller already has them (as `describe_award` does); omitted, they
            are read off the award.

    Returns:
        "capped at X EUR eligible cost (slot, ...)", "cap not binding (X EUR eligible cost)", or "" when the scheme
            declares no cap.
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
    """Return the nominal support on the SUBSIDY entries of the scoped timeline, or None if there are none.

    Computed with `nominal_support_from_entries`, the same basis as the levy, so it includes support without a catalog
    award, operational support and scheduled instalments (§8). For a SYSTEM-scope perspective this is every SUBSIDY
    entry of the run; for an actor scope, the support that actor receives. None lets callers omit the KPI instead of
    publishing a zero. The per-subject counterpart is `ComponentCostBreakdown.subsidies_nominal_in_euro`; the per-award
    one is `award_total_amount`.
    """
    entries = [
        entry for entry in result.scoped_timeline().entries if entry.category == CostCategory.SUBSIDY
    ]
    if not entries:
        return None
    return nominal_support_from_entries(entries)


# ----- chart views: actor flows to event strip -----

class ViewTolerances:
    """The numeric tolerances the views apply, in one place.

    Two kinds: the reconciliation tolerances of the self-checking views (a Sankey whose transfers do not net to zero, a
    tornado whose bars do not sum to the band, an unbalanced sources-and-uses statement raise `CostDataError`), and
    `DETAIL_ROW_EPSILON`, the only threshold below which a view drops data, which suppresses float noise.
    """

    #: Absolute euro tolerance for the reconciliation checks (half a cent).
    RECONCILIATION_EPSILON = 0.005
    #: Amounts below this (in every slot) are dropped from the detail table as float noise; the
    #: same half a cent, named separately because dropping a row is not reconciling a total.
    DETAIL_ROW_EPSILON = 0.005
    #: Balance below this counts as repaid; guards `LoanAmortization.loan_free_year` against
    #: float residue.
    BALANCE_EPSILON = 0.01
    #: Relative tolerance for the kWh attribution check of the energy balance, where quantities
    #: are large enough that an absolute euro epsilon means nothing.
    QUANTITY_RELATIVE_EPSILON = 1e-6
    #: Relative tolerance on "the replacement arrived exactly when the life ran out". The
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
    """Fold items below `share` of the total into one replacement item per fold key.

    Shared by the Sankey's ribbon fold and the treemap's tile fold. Nothing is dropped: the replacement carries exactly
    what the folded items carried, so the reconciliation checks still hold.

    Args:
        items: The items to fold, in the order to keep them.
        amount_of: The magnitude of one item.
        share: Fraction of the total below which an item is folded.
        fold_key: The replacement an item folds into, e.g. the (source, target) pair for ribbons or the display group
            for tiles.
        fold_factory: Builds the replacement item from its fold key and total amount.

    Returns:
        `(kept, folded_count, folded_total)`: the surviving items followed by one replacement per fold key, how many
            items were folded, and the euros they carried.
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


# ============================================================================ actor flows

class FlowCounterparties:
    """The external counterparty of a cash flow per cost category, for the actor Sankey.

    The timeline names only the payer; this fixed mapping supplies the other end: "market" (contractors, vendors),
    "suppliers" (energy, operation, insurance), "state" (taxes, support), "bank" (the loan) and "grid operator"
    (feed-in revenue). An unmapped category raises `CostDataError` instead of landing in a default bucket. Transfers
    between actors are declared in `TRANSFER_CATEGORIES`, so one that fails to net out is reported as a defect.
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
    #: they net to zero across payers. This is the narrow, Sankey sense of
    #: "transfer" — both legs are on the timeline — and is deliberately *not*
    #: `StatementPartitions.SOCIETY_TRANSFER_CATEGORIES`, which is the wider macroeconomic sense.
    TRANSFER_CATEGORIES = frozenset({CostCategory.MODERNIZATION_LEVY})

    #: Ribbons smaller than this share of the gross flow volume are folded per node pair.
    SMALL_FLOW_SHARE = 0.005

    #: Label of the folded ribbon; named so the caption and the data agree on the wording.
    OTHER_LABEL = "other"

    @classmethod
    def counterparty_of(cls, category: CostCategory) -> str:
        """Return the counterparty node of one category.

        Raises `CostDataError` naming the category and this class if none is declared, since inventing a node would
        misattribute real money.
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

    `source` pays `target`: a cost becomes payer -> counterparty, support, revenue or a disbursement becomes
    counterparty -> payer, and `amount_in_euro` is always positive. `category` sets the ribbon's colour; it is None for
    the folded "other" ribbon.
    """

    source: str
    target: str
    amount_in_euro: float
    category: Optional[CostCategory] = None
    is_transfer: bool = False


@dataclass(frozen=True)
class ActorFlowMatrix:
    """Who pays whom over the whole horizon, as ribbons plus node columns.

    Nominal lifetime sums of the full allocated timeline in the best-estimate slot, so both legs of every transfer are
    visible (§6.5). `total_band` carries the grand total as a band for the title. `actor_flow_matrix` checks before
    returning that each actor's `net_by_actor()` equals the nominal sum of its timeline entries inside the horizon and
    that transfer ribbons net to zero.
    """

    flows: List[ActorFlow]
    #: Payer nodes, in the order they first appear on the timeline. Each gets a column of its own
    #: in the drawing; `actor_columns` decides in which order.
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
        """Return one column per internal party, ordered so transfers between them run left to right.

        The order is a topological sort of the transfer graph (each payer before its payee), with ties kept in the
        order payers first appear on the timeline, so the layout is deterministic. A tenant-to-landlord levy thus
        becomes an ordinary ribbon between adjacent columns. A cycle (A pays B, B pays A) has no such order; the
        remaining actors are appended in timeline order instead of raising.

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
        """Return outflows minus inflows per actor, the actor's nominal lifetime cost.

        Equals the nominal sum of the entries the timeline books on that payer; `actor_flow_matrix` checks this.
        """
        nets: Dict[str, float] = {actor: 0.0 for actor in self.actors}
        for flow in self.flows:
            if flow.source in nets:
                nets[flow.source] += flow.amount_in_euro
            if flow.target in nets:
                nets[flow.target] -= flow.amount_in_euro
        return nets


# ------------------------------------------------------------------ story chapters


@dataclass(frozen=True)
class StoryPerspectives:
    """Which evaluated perspectives belong to which chapter of the report.

    The report tells three stories: the owner lives in the house, the owner rents it out, and the economy-wide view.
    Classifying results here keeps renderers from matching perspective ids against strings. An empty list means the
    chapter is skipped, with its reason under the table of contents; an owner-occupied house has no landlord story.
    """

    owner: Tuple[LifecycleCostResult, ...]
    rented: Tuple[LifecycleCostResult, ...]
    society: Tuple[LifecycleCostResult, ...]


def has_macroeconomic_accounting(result: LifecycleCostResult) -> bool:
    """Return whether this perspective uses macroeconomic accounting, judged by what it books rather than by its id.

    Macroeconomic accounting (§4.5) books CO2 at its damage cost; no financial perspective books a `CO2_DAMAGE` flow.
    The full timeline is read, not `npv_by_category`: a macroeconomic perspective scoped to a landlord books the damage
    flow but its scoped pivot shows none of it.
    """
    return any(entry.category == CostCategory.CO2_DAMAGE for entry in result.timeline.entries)


def story_perspectives(results: Iterable[LifecycleCostResult]) -> StoryPerspectives:
    """Sort an evaluated matrix's perspectives into the three story chapters.

    Rules, in order: a perspective that books CO2 damage and is scoped to the whole system is the society story; one
    scoped to a landlord or tenant is the rented-out story; of the rest, the owner-occupied story takes owner-scoped
    perspectives and net ones (those that book support). Gross perspectives stay in the common chapter. If the run
    books no support at all, the leftovers become the owner story, preferring those scoped to an owner-occupier or the
    system. A macroeconomic perspective scoped to a party is told as that party's story, because the society statement
    is only defined for a SYSTEM-scoped result (see `perspective_statement`).

    Args:
        results: The evaluated perspectives, in bundle (rendering) order.

    Returns:
        The three lists, each in the input order.
    """
    evaluated = list(results)
    society: List[LifecycleCostResult] = []
    rented: List[LifecycleCostResult] = []
    rest: List[LifecycleCostResult] = []
    for result in evaluated:
        if has_macroeconomic_accounting(result) and result.scope_payer == Actor.SYSTEM:
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
    """Return whether any perspective of the run books a subsidy on its full timeline.

    Guards the owner chapter's fallback. It reads full timelines because the question is whether the run was supported
    at all, not what one perspective reports.
    """
    return any(
        entry.category == CostCategory.SUBSIDY
        for result in results
        for entry in result.timeline.entries
    )


# ------------------------------------------- party statements (landlord, owner, tenant, society)


class LandlordStatementCategories:
    """Which cost categories are cash and which are accounting credits, plus the statement row labels.

    Cash categories move money in the year they are booked: investment, replacements, maintenance, the levy, subsidies,
    feed-in revenue, loan flows. Accounting credits value something without money moving: the residual value at the
    horizon and the anyway credit (the avoided cost of a renovation the building needed regardless). A landlord
    perspective can look advantageous mainly through credits, so the statement keeps them apart. A category not named
    here counts as cash, which understates rather than overstates an advantage. `StatementPartitions` reuses
    `ACCOUNTING_CREDIT_CATEGORIES` and `LABELS` for the other parties.
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
    """How one party's NPV is split into two named sides.

    Every party the report states (owner-occupier, landlord, tenant, society) uses the same two-sided form, so the
    reconciliation (the sides sum to the NPV) has one implementation. `secondary_categories` are the second side's
    categories: the two accounting credits for household parties, the transfer categories for society.
    `secondary_is_transfer` makes the second side "money moving between parties" instead of "value, not payment".
    `labels` override `LandlordStatementCategories.LABELS`, e.g. the levy is income to the landlord and a payment to
    the tenant.
    """

    id: str
    party_label: str
    primary_label: str
    secondary_label: str
    secondary_categories: Tuple[CostCategory, ...]
    secondary_is_transfer: bool = False
    labels: Mapping[CostCategory, str] = field(default_factory=dict)

    def label_of(self, category: CostCategory) -> str:
        """Return the row label of one category, falling back to the shared label table."""
        if category in self.labels:
            return self.labels[category]
        return LandlordStatementCategories.LABELS.get(category, category.value)

    def is_secondary(self, category: CostCategory) -> bool:
        """Whether a category belongs on the second side of this partition."""
        return category in self.secondary_categories


class StatementPartitions:
    """The four party statements the report publishes: owner-occupier, landlord, tenant and society.

    The three household parties share the cash / accounting-credit split and differ in labels. Society's second side is
    the transfers. `TENANT` has an empty second side, because a tenant receives no credit in this ledger; a lower
    energy bill shows as a smaller cost line.
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
    """One row of a party statement: a category, its present value and its side.

    `npv_in_euro` keeps the report's sign convention (positive is a cost to the party, negative is money or value
    arriving). `is_accounting_credit` means the row is on the second side of its partition: the accounting credits for
    household parties, the transfers for society.
    """

    label: str
    category: CostCategory
    npv_in_euro: float
    is_accounting_credit: bool
    #: For a paired transfer row: the party that carries this half of the pair, or None for an
    #: ordinary row. The society statement renders both halves of every transfer so their sum is
    #: visibly zero, and the two halves are otherwise indistinguishable.
    payer: Optional[Actor] = None


@dataclass(frozen=True)
class IncomeRibbon:
    """One ribbon of the landlord income Sankey: money arriving at or leaving the landlord node.

    `source` pays `target`, and `amount_in_euro` is a positive magnitude. Unlike an `ActorFlow`, it runs between the
    landlord and one of his statement rows and carries the cash / accounting-credit split the renderer styles it by.
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
    """One party's NPV as a two-sided statement.

    Shows how much of a perspective's net position is cash and how much is book value (residual value, anyway credit),
    each side with its own subtotal. Under the society partition the sides are real resource cost and transfers.
    `perspective_statement` checks that `cash_subtotal + accounting_subtotal == net_position == total NPV` exactly. The
    side fields are named `cash_lines` and `accounting_lines` after the household case; `partition` carries the labels
    to print.
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
        """Return the landlord income Sankey as `IncomeRibbon`s, plus the net-position direction flag.

        Income (categories with a negative NPV) flows into the landlord node from the left, expenses leave to the
        right, and the leftover ribbon runs on to a node named for the net position. When the result is a net cost
        (positive NPV), the net position instead enters from the left as a source, and the flag says so. Only the
        landlord partition is drawn, because the nodes are labelled for the landlord.

        Returns:
            The ribbons (positive magnitudes), and True when the net-position ribbon is an inflow (a net cost).

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
    """Return the landlord's NPV split into money that moves and value that is only booked.

    `perspective_statement` with the landlord partition, kept as a named function because the landlord statement also
    carries an income Sankey.

    Args:
        result: The perspective's result; any result is accepted, but the report renders it only for the landlord
            chapter.

    Returns:
        The two-sided statement with both subtotals and the net position.

    Raises:
        CostDataError: If the two sides do not sum to the perspective's total NPV.
    """
    return perspective_statement(result, StatementPartitions.LANDLORD)


def perspective_statement(
    result: LifecycleCostResult, partition: StatementPartition
) -> PerspectiveStatement:
    """Return one party's NPV as a two-sided statement under the given partition.

    One pass over the perspective's `npv_by_category`, sorting each category onto a side and subtotalling; nothing is
    recomputed, so the sides add back to the headline NPV. A transfer partition (society) also lists both halves of
    every transfer pair from the full timeline, so the reader sees them cancel. That mixes the scoped and the full
    timeline, which only reconciles for a SYSTEM-scoped perspective.

    Args:
        result: The perspective to state.
        partition: Which two sides to split into and what to call them.

    Returns:
        The two-sided statement with both subtotals and the net position.

    Raises:
        CostDataError: If a transfer partition is asked for on a perspective that is not SYSTEM-scoped, or if the two
            sides do not sum to the total NPV.
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


def levy_transfer_reconciles(
    landlord: PerspectiveStatement, tenant: PerspectiveStatement
) -> Optional[float]:
    """Check that the landlord's levy income and the tenant's levy cost are the same amount.

    The modernization levy (the rent increase a landlord may charge after a renovation) is booked as a transfer pair:
    income in the landlord statement, cost in the tenant's. The two statements come from separately scoped timelines,
    so this confirms they agree. A party without a levy line counts as zero, so a levy booked on one side only is a
    mismatch.

    Args:
        landlord: The landlord's statement, under a partition that keeps the levy on a side.
        tenant: The tenant's statement from the same run.

    Returns:
        The tenant's levy present value (positive: what the tenant pays), or None when neither party books a levy.

    Raises:
        CostDataError: If the two figures differ by more than `ViewTolerances.RECONCILIATION_EPSILON`.
    """
    landlord_levy = _levy_line_npv(landlord)
    tenant_levy = _levy_line_npv(tenant)
    if landlord_levy is None and tenant_levy is None:
        return None
    landlord_amount = landlord_levy or 0.0
    tenant_amount = tenant_levy or 0.0
    if abs(tenant_amount + landlord_amount) > ViewTolerances.RECONCILIATION_EPSILON:
        raise CostDataError(
            f"The modernization levy does not reconcile between the two statements of this run: "
            f"the tenant of perspective {tenant.perspective_id!r} pays {tenant_amount:,.2f} EUR "
            f"while the landlord of perspective {landlord.perspective_id!r} receives "
            f"{-landlord_amount:,.2f} EUR in present value. The two are the halves of one booked "
            "transfer and have to cancel; a difference means the transfer leaked between the "
            "allocation and one of the two scoped timelines."
        )
    return tenant_amount


def _levy_line_npv(statement: PerspectiveStatement) -> Optional[float]:
    """Return the modernization levy line of one statement from either side, or None."""
    for line in list(statement.cash_lines) + list(statement.accounting_lines):
        if line.category == CostCategory.MODERNIZATION_LEVY:
            return line.npv_in_euro
    return None


def _transfer_statement_lines(
    result: LifecycleCostResult, partition: StatementPartition
) -> List[StatementLine]:
    """Return both halves of every transfer from the full timeline, so their sum is visibly zero.

    The society statement's second side. A transfer's two halves are booked on different payers, so the scoped view
    would show a cost, not a transfer. Lines are emitted per transfer category and payer, in payer order. On a
    macroeconomic result the list is empty, because that accounting removes transfers at source (§4.5).

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
    """Classify every timeline entry into a (source, target, amount) ribbon for the actor Sankey.

    Reads the full allocated timeline, since the chart shows the split between payers. Amounts are nominal lifetime
    sums of the best-estimate slot; the band goes into `total_band`. The counterparty comes from `FlowCounterparties`;
    the direction from the entry's sign (costs leave the payer, credits arrive); a category in `TRANSFER_CATEGORIES`
    (the modernization levy) becomes one payer-to-receiver ribbon. Before returning it checks that transfers net to
    zero and that each actor's net equals the nominal sum the timeline books on that payer.

    Raises:
        CostDataError: On a category without a declared counterparty, on transfers that do not net to zero or run
            between more than two parties, or on an actor whose ribbons do not net to its timeline sum.
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
    """Check every actor's ribbon net against the nominal sum the timeline books on that payer.

    The ribbons reshape the timeline without rounding, so any difference means money was drawn to the wrong end.

    Args:
        matrix: The assembled matrix.
        nominal_by_actor: Payer node -> nominal sum of that payer's entries inside the horizon.

    Raises:
        CostDataError: If any actor's net differs by more than `ViewTolerances.RECONCILIATION_EPSILON`.
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
    """Return the single payer-to-receiver ribbon of the declared transfers, checked to net to zero.

    The one declared transfer category is the modernization levy, booked as a matched pair (the tenant pays, the
    landlord receives). A run with a second payer or receiver is refused, because splitting the transfer between them
    would invent an allocation the engine never made.

    Args:
        transfer_net: Payer node -> nominal net of its transfer entries; positive for a payer, negative for a receiver.

    Returns:
        The one ribbon, or an empty list when the run books no transfer.

    Raises:
        CostDataError: If the transfers do not net to zero across payers, or if more than one payer or receiver
            appears.
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
    """Fold ribbons below `FlowCounterparties.SMALL_FLOW_SHARE` into one "other" ribbon per node pair.

    Returns the ribbons plus the folded count and euros for the caption. Each node pair's total is kept exactly.
    """
    return _fold_small(
        flows,
        lambda flow: flow.amount_in_euro,
        FlowCounterparties.SMALL_FLOW_SHARE,
        lambda flow: (flow.source, flow.target),
        lambda key, amount: ActorFlow(source=key[0], target=key[1], amount_in_euro=amount),
    )


# ============================================================================ liquidity fan

def band_zero_crossings(series_by_slot: Mapping[Any, List[float]]) -> Dict[Any, Optional[int]]:
    """Return the first non-negative year of each slot's series, or None where it never crosses.

    Calls `results.discounted_payback_year` per slot, so the chart and the printed payback year use the same crossing
    rule (year 0 excluded, first crossing only). Keys are the caller's: `Slot` members or the
    `"low"/"best_estimate"/"high"` strings of a `VariantComparison`.

    Returns:
        One crossing year per input key; None means no crossing within the horizon.
    """
    return {key: discounted_payback_year(series) for key, series in series_by_slot.items()}


def cumulative_nominal_cost_series(result: LifecycleCostResult) -> Dict[Slot, List[float]]:
    """Return the cumulative nominal cost per slot over years 0..T, the upper panel of the liquidity chart.

    A slot-wise running sum of `result.annual_cost_series_nominal_in_euro`; the last point is the slot's nominal
    lifetime cost and the highest point the deepest out-of-pocket position. Summing slot-wise is valid because each
    slot is one consistent world (all cheap, all expensive), not a confidence bound.
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
    """Return the year and amount of the deepest out-of-pocket position (best-estimate slot, nominal).

    The maximum of the cumulative nominal cost curve, where the most money has left the account; cost is positive.

    Returns:
        `(year, cumulative nominal cost in euro)`, or `(0, 0.0)` for an empty series.
    """
    curve = cumulative_nominal_cost_series(result)[Slot.BEST_ESTIMATE]
    if not curve:
        return 0, 0.0
    worst_year = max(range(len(curve)), key=lambda year: curve[year])
    return worst_year, curve[worst_year]


# ============================================================================ uncertainty attribution

class AttributionThresholds:
    """Cut-offs of the uncertainty attribution tornado.

    `TOP_N` is how many subjects get their own bar before the rest fold into one row; the fold keeps the sum exact.
    """

    #: Subjects shown individually; everything below is folded into one row.
    TOP_N = 10

    #: Label of the folded row, shared by the view and every caption that mentions it.
    FOLD_LABEL = "all other subjects"


@dataclass(frozen=True)
class AttributionRow:
    """One subject's contribution to the width of the total NPV band.

    `low_delta_in_euro` and `high_delta_in_euro` are the subject's NPV in the LOW and HIGH world minus its
    best-estimate NPV, signed. Revenue bands are mirrored on entry (`UncertainValue.as_revenue`), so `minimum` always
    means the LOW world and a revenue subject's bar straddles the axis like a cost subject's.
    """

    subject: str
    best_estimate_npv_in_euro: float
    low_delta_in_euro: float
    high_delta_in_euro: float
    #: True for the single folded row that carries every subject below the cut-off.
    is_fold: bool = False

    @property
    def width_in_euro(self) -> float:
        """Return how much of the total band width this subject accounts for; the sort key."""
        return self.high_delta_in_euro - self.low_delta_in_euro


def uncertainty_attribution(result: LifecycleCostResult) -> List[AttributionRow]:
    """Decompose the total NPV band into per-subject contributions (attribution, not sensitivity).

    Nothing is re-evaluated: all engine arithmetic is slot-wise, so the LOW and HIGH totals split exactly into
    `timeline.npv_by(subject)` on the scoped timeline. It is not a sensitivity analysis, since no input is varied. Rows
    are sorted by band width, descending, and those beyond `AttributionThresholds.TOP_N` fold into one row. The view
    checks `sum(low_delta) == total.minimum - total.best_estimate` and the same for the maximum.

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


# ============================================================================ comparison bridge

@dataclass(frozen=True)
class BridgeStep:
    """One floating bar of the comparison bridge: one display group's NPV delta.

    `group` is the caller's group key; `delta_in_euro` is variant minus reference in the best-estimate slot. Deltas
    carry no band, because `high(variant - reference)` is not `high(variant) - high(reference)`.
    """

    group: Any
    delta_in_euro: float


def comparison_bridge(
    reference: LifecycleCostResult,
    variant: LifecycleCostResult,
    mapping: Mapping[CostCategory, GroupKey],
) -> List[BridgeStep]:
    """Decompose the difference between the variant's and the reference's NPV by display group.

    Each group's best-estimate NPV in the variant minus that in the reference, in fixed group order so two reports can
    be read side by side; a group missing on one side counts as zero. It takes the two results because
    `VariantComparison` has no per-category deltas. The view checks that the steps sum to `variant.total_npv -
    reference.total_npv`.

    Raises:
        CostDataError: If the steps do not sum to the NPV delta, or if the caller's group keys cannot be ordered.
    """
    variant_groups = fold_categories(variant.npv_by_category, mapping)
    reference_groups = fold_categories(reference.npv_by_category, mapping)
    present: Set[Any] = set(variant_groups) | set(reference_groups)
    try:
        # Display-group indices sort into the fixed order every chart stacks them in.
        keys: List[Any] = sorted(present)
    except TypeError as error:
        # A mapping whose keys cannot be compared has no bar order, and two reports could then put
        # the same group in different places (IBCS). Naming the keys hands the caller the fix.
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


# ============================================================================ cost of credit

@dataclass(frozen=True)
class TotalCostOfCredit:
    """The consumer-credit disclosure of a financed perspective.

    "You borrow 50,000 and pay back 63,400" in four parts: principal, interest, fees and the repayment grant. Figures
    are nominal best-estimate euros, as a loan contract quotes them. `effective_annual_rate` is the internal rate of
    the loan's own flows (the Effektivzins); when it is None, `effective_annual_rate_note` says why, and the note is
    empty exactly when a rate is given. `fees_in_euro` is always zero, as the engine books no fee category yet.
    `unrepaid_principal_in_euro` is the principal repaid beyond the horizon.
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
        """Return the net cost of borrowing: interest + fees - grants."""
        return self.interest_in_euro + self.fees_in_euro - self.grants_in_euro


def total_cost_of_credit(result: LifecycleCostResult) -> TotalCostOfCredit:
    """Return the principal, interest, fees and grants of the perspective's loan, plus its effective rate.

    Reads the same scoped timeline entries as `loan_amortization_series`. The repayment grant (Tilgungszuschuss) is
    taken from the SUBSIDY entries booked under the financing subject and enters the rate as money received in year 0.
    The view checks that the loan entries plus the grant equal `net_cost_of_credit_in_euro` minus the unrepaid
    principal.

    Returns:
        The disclosure; `effective_annual_rate` is None when there is no loan, when the schedule reaches past the
            horizon, or when the grant exceeds the whole debt service, and the note names which of the last two.

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
    """Return the internal rate of the loan's flows: disbursement and grant in, debt service out.

    Solved by bisection (`numerics.bisect_root`) with the package's `discount_factor`. A fee-free, grant-free annual
    annuity returns its nominal rate; a grant lowers it. Two cases have no rate: a schedule reaching past the horizon
    (solving the truncated flows would give a meaningless rate, e.g. -23 % for a ten-year loan seen over four years),
    and a grant larger than the whole debt service (no rate in the search window [0, 5.0] solves it).

    Args:
        amortization: The loan's booked interest, principal and disbursement.
        grants_in_euro: The repayment grant, positive, received in year 0.

    Returns:
        `(rate, "")` with the rate as a fraction, or `(None, note)` with a printable reason.
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


# ============================================================================ event strip

class EventKinds(str, enum.Enum):
    """The three events on a component's lifetime strip: bought, replaced, or worth something at the horizon.

    The `str` mixin keeps members comparable with plain strings, so `event.kind == "residual"` works.
    """

    INVESTMENT = "investment"
    REPLACEMENT = "replacement"
    RESIDUAL = "residual"

    def __str__(self) -> str:
        """Return the kind's own word, so a label shows "residual" rather than "EventKinds.RESIDUAL"."""
        return self.value


@dataclass(frozen=True)
class LifecycleEvent:
    """One dated event on a component's strip: what happened, when, and for how much.

    Amounts are nominal best-estimate euros as booked: an investment is positive, a residual value negative.
    """

    year: int
    amount_in_euro: float
    kind: EventKinds


@dataclass(frozen=True)
class ServiceSpan:
    """One interval a component was in service, derived from the events the timeline booked.

    Spans come from booked events, not the catalog lifetime, so a mismatch with the database service life is visible. A
    span runs from an install or replacement year to the next event, or to the horizon for the last one.
    """

    start_year: int
    end_year: int


@dataclass(frozen=True)
class EventStripRow:
    """One component's lifetime lane: its purchases, replacements and residual value.

    The row checks its own shape so any builder produces a lane that draws correctly: a residual requires at least one
    investment or replacement event (§4.1); events are sorted by year; there is one span per event, starting at its
    year and running to the next event or, for the last one, the horizon.

    Raises:
        CostDataError: If any of these invariants is violated.
    """

    subject: str
    events: List[LifecycleEvent]
    spans: List[ServiceSpan]
    residual: Optional[LifecycleEvent] = None

    def __post_init__(self) -> None:
        """Check the three invariants stated in the class docstring."""
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
        """Return this row's year-0 investment, the sort key that puts the biggest asset first."""
        return sum(event.amount_in_euro for event in self.events if event.year == 0)


def component_event_strip(result: LifecycleCostResult) -> List[EventStripRow]:
    """Return when each component was bought, replaced and written down.

    One row per COMPONENT-kind subject of the scoped timeline, from the INVESTMENT, REPLACEMENT and RESIDUAL_VALUE
    entries in the best-estimate slot. Service spans are derived from these events, not from the database service life.
    Rows are sorted by year-0 investment, descending. Each event amount is a timeline entry, so a row sums to the
    subject's undiscounted `npv_by_component` figure.

    Raises:
        CostDataError: From `EventStripRow`, e.g. a subject with a residual-value credit but no charged investment or
            replacement.
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


# ----- chart views: treemap to equity build-up -----

# ============================================================================ treemap

class TileBasis(str, enum.Enum):
    """The two ways the cost treemap can show what something costs.

    A treemap has no negative areas, so credits cannot be drawn. GROSS shows the cost side only and states the excluded
    credits in the caption. NET_OF_CREDITS nets each subject's credits against its costs across display groups (a
    wall's subsidy and its investment sit in different groups, so netting per cell would change nothing) and clamps a
    subject whose credits exceed its costs to zero, disclosing it. Being an enum, an unknown basis is rejected rather
    than drawn as the wrong panel.
    """

    GROSS = "gross"
    NET_OF_CREDITS = "net"

    def __str__(self) -> str:
        """Return the basis's own word, so a caption does not show "TileBasis.GROSS"."""
        return self.value


class TreemapThresholds:
    """Cut-offs of the cost-structure treemap.

    Tiles below `SMALL_TILE_SHARE` of the whole treemap area are folded per group into one "other" tile, which the
    caption names.
    """

    #: Tiles below this share of the total area fold into one "other" tile per display group.
    SMALL_TILE_SHARE = 0.01

    #: Label of the folded tile.
    FOLD_LABEL = "other"


@dataclass(frozen=True)
class TreemapTile:
    """One rectangle of the treemap: a (display group, subject) cell and its area in euro.

    `area_in_euro` is never negative. `clamped_from_in_euro` is set only on the net-of-credits basis, for a subject
    whose credits reached its costs: it records the negative net (costs minus credits across groups) so the caption can
    disclose it; such a tile has zero area and is not drawn.
    """

    group: Any
    subject: str
    area_in_euro: float
    clamped_from_in_euro: Optional[float] = None
    is_fold: bool = False

    def __post_init__(self) -> None:
        """Check that the area is non-negative and that a clamped tile has zero area and a negative recorded net.

        The caption arithmetic (`areas - erased == net NPV`) relies on both.

        Raises:
            CostDataError: On a negative area, or on a clamped tile that is drawable or whose recorded net is not
                negative.
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
    """The treemap's tiles plus everything its caption discloses.

    Carries the gross cost NPV the tiles add up to, the credit total the gross variant leaves out and the net NPV they
    imply, so a reader can check `gross - credits == net` against the headline KPI. On the net basis,
    `clamped_total_in_euro` and the clamped tiles name the euros the clamp erased; the tile areas minus that total give
    the same net NPV.
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
        """Return the subjects whose negative net value was clamped to zero, for the caption to name."""
        return [tile for tile in self.tiles if tile.clamped_from_in_euro is not None]


def cost_structure_tiles(
    result: LifecycleCostResult,
    mapping: Mapping[CostCategory, GroupKey],
    basis: TileBasis = TileBasis.GROSS,
) -> CostStructureTiles:
    """Return the lifetime cost composition as (display group, subject) tiles on either basis.

    Pivots the scoped timeline by (subject, display group) in present value, best-estimate slot, and splits each cell
    into cost and credit by the sign of its entries, so a PV system's investment and revenue stay apart. On
    `TileBasis.GROSS` the area is the cost half and the credits go to `credit_total_in_euro`. On
    `TileBasis.NET_OF_CREDITS` each subject's credits shrink all its cost cells proportionally; subjects whose credits
    reach their costs clamp to zero and are disclosed. Tiles below `TreemapThresholds.SMALL_TILE_SHARE` fold into one
    "other" tile per group. The view checks `gross - credits == net_npv_in_euro ==
    result.total_npv_in_euro.best_estimate`, that the gross areas sum to `gross_cost_npv_in_euro`, and that the net
    areas minus `clamped_total_in_euro` give the net NPV.

    Raises:
        CostDataError: If `basis` is not a `TileBasis`, or if any of the checks above fails.
    """
    if not isinstance(basis, TileBasis):
        raise CostDataError(
            f"Unknown treemap basis {basis!r}: the cost structure is drawn either as "
            f"{TileBasis.GROSS.value!r} or as {TileBasis.NET_OF_CREDITS.value!r}. An unrecognised "
            "value used to fall through to the net branch, which drew a net panel under whatever "
            "heading the caller had in mind and disclosed the wrong thing."
        )

    def cell_of(item: CashFlowEntry) -> Tuple[Any, str]:
        """Return the (display group, subject) cell one entry belongs in."""
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
    """Apply each subject's credits to that subject's cost cells, across display groups.

    A subject's costs and credits rarely share a cell (insulation investment and its subsidy sit in different groups),
    so each subject's cost cells are scaled by `max(0, C - K) / C`, with C its costs and K its credits. Subjects whose
    credits reach their costs return as zero-area disclosure tiles carrying `C - K`, filed under the group of their
    largest cell.

    Returns:
        The per-cell net areas, and the disclosure tiles of the clamped subjects.
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
    """Return the display group a clamped subject is filed under: the group of its largest cell.

    Cost cells win over credit cells; a credit-only subject falls back to the group of its largest credit (the support
    group for a subsidy line).
    """
    for cells in (cost_cells, credit_cells):
        candidates = [(amount, group) for (group, cell_subject), amount in cells.items() if cell_subject == subject]
        if candidates:
            return max(candidates, key=lambda pair: pair[0])[1]
    raise CostDataError(f"Cannot place treemap subject {subject!r}: it has neither a cost nor a credit cell.")


def _fold_small_tiles(areas: Mapping[Tuple[Any, str], float]) -> Tuple[List[TreemapTile], int, float]:
    """Turn (group, subject) -> area into tiles, folding the small ones per group.

    Folding per group keeps every group's total exact; the threshold is a share of the whole treemap, as `_fold_small`
    measures it. Only positive areas arrive here; the caller appends the zero-area disclosure tiles afterwards so they
    are never folded.
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


# ============================================================================ swimlane

@dataclass(frozen=True)
class LaneEvent:
    """One dated marker on a swimlane: a disbursement, a payout or a milestone.

    `amount_in_euro` is None for a marker without an amount (the loan-free year), so the renderer prints no zero.
    """

    year: int
    label: str
    amount_in_euro: Optional[float] = None


@dataclass(frozen=True)
class LaneSpan:
    """One interval on a swimlane: a repayment period, a levy period or a payback range.

    `end_year` is None for an open-ended span, such as a payback range whose HIGH world never crosses zero within the
    horizon; the renderer draws an open arrow.
    """

    start_year: int
    end_year: Optional[int]
    label: str


@dataclass(frozen=True)
class Lane:
    """One labelled swimlane: its spans and its markers.

    `lifecycle_lanes` builds each lane once from finished lists; do not append to its lists afterwards.
    """

    name: str
    events: List[LaneEvent] = field(default_factory=list)
    spans: List[LaneSpan] = field(default_factory=list)

    def is_empty(self) -> bool:
        """Return True when the lane has nothing to draw, so the renderer can drop it and log the skip."""
        return not self.events and not self.spans


@dataclass(frozen=True)
class LifecycleLanes:
    """The one-page life of the renovation: assets, financing, support and milestones on one year axis.

    A composition only: each lane restates figures from other views (the component event strip, the amortization
    series, the band crossings), so it adds no new numbers.
    """

    horizon: int
    milestones: Lane
    assets: List[EventStripRow]
    financing: Lane
    support: Lane


def lifecycle_lanes(
    result: LifecycleCostResult, comparison: Optional[VariantComparison] = None
) -> LifecycleLanes:
    """Return assets, financing, support and milestones on one year axis.

    Delegates to other views so the swimlane cannot disagree with the charts it summarizes: `component_event_strip` for
    assets; `loan_amortization_series` for financing (disbursement, years with debt service, the loan-free year);
    `subsidy_decisions` and the MODERNIZATION_LEVY entries for support; band crossings and `worst_liquidity_position`
    for milestones.

    Args:
        result: The perspective to draw.
        comparison: The variant comparison, if any; the payback milestone is the range between the LOW and HIGH
            crossings of its savings curve and is absent without one.

    Returns:
        The four lane groups; empty lanes are returned empty, so the renderer can name what it skips.
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
        # The range by value: which world pays back first is not a property of its slot.
        envelope = PaybackEnvelope.of(
            band_zero_crossings(comparison.cumulative_discounted_savings_in_euro)
        )
        if envelope.earliest is not None:
            milestone_spans.append(
                LaneSpan(
                    start_year=envelope.earliest,
                    end_year=envelope.latest,
                    label="payback range (earliest to latest world)" if envelope.latest is not None
                    else "payback range (no payback in at least one world)",
                )
            )
    milestones = Lane(name="Milestones", events=milestone_events, spans=milestone_spans)
    return LifecycleLanes(
        horizon=horizon, milestones=milestones, assets=assets, financing=financing, support=support
    )


# ============================================================================ sources & uses

@dataclass(frozen=True)
class FundingNode:
    """One node of the sources-and-uses statement: a label and an amount in euro.

    Amounts are positive on both sides, so the balance is a plain equality of the two column totals. `category` is set
    where one exists, for colouring.
    """

    label: str
    amount_in_euro: float
    category: Optional[CostCategory] = None
    #: The use this source is tied to, when the booking names one — a subsidy scheme is awarded
    #: for a specific measure, and drawing it into an unrelated one would be a picture of a
    #: funding structure that does not exist. None for the untied sources (own capital, a loan
    #: taken against the investment as a whole).
    subject: Optional[str] = None
    #: The raw subsidy scheme id behind a support node, when there is one. `label` carries
    #: the friendly name a reader sees; this is what a reviewer greps the catalog with, and the
    #: renderers put it in the node's tooltip.
    scheme_id: Optional[str] = None


@dataclass(frozen=True)
class SourcesAndUses:
    """Where the year-0 money comes from and what it buys.

    The sources-and-uses statement (Mittelherkunft und Mittelverwendung) for year 0 in the best-estimate slot: subsidy
    schemes, loan disbursements and own capital on the left; gross investment per subject plus planning and removal on
    the right. `funding_sources_and_uses` checks that the sides balance.
    """

    sources: List[FundingNode]
    uses: List[FundingNode]
    gross_year_zero_investment_in_euro: float

    def total_sources_in_euro(self) -> float:
        """Return the sum of the left column, equal to the uses total by construction."""
        return sum(node.amount_in_euro for node in self.sources)

    def total_uses_in_euro(self) -> float:
        """Sum of the right column."""
        return sum(node.amount_in_euro for node in self.uses)

    def has_external_funding(self) -> bool:
        """Return True when anything but own capital funds year 0; the chart is skipped otherwise."""
        return any(node.category is not None for node in self.sources)

    def ribbons(self) -> List[Tuple[str, str, float]]:
        """Return (source label, use label, euros) ribbons whose widths fill both columns exactly.

        Two passes: a source tied to a subject (a subsidy awarded for one measure) first fills that use, up to what it
        still needs; the untied rest (own capital, the loan, an over-award) is spread over the remaining capacity in
        proportion to it. Both column totals are kept exactly.
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
    """Return year-0 funding sources against year-0 uses, balanced to the euro.

    Sources: one node per subsidy scheme, labelled with its display name; one node for all year-0 loan disbursements
    together, since the timeline ties a loan to no subject; and own capital as the balancing item. Uses: the gross
    year-0 investment per subject plus planning and removal as their own nodes. The view checks that sources, uses and
    the gross year-0 investment are equal.

    Raises:
        CostDataError: If own capital comes out negative (support plus debt exceed the investment), or if the two
            columns do not balance.
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


# ============================================================================ subject flows

@dataclass(frozen=True)
class SubjectGroupFlow:
    """One ribbon from a subject to a cost group, in present value.

    Costs and credits are separate ribbons, since a ribbon has no sign: `amount_in_euro` is always positive and
    `is_credit` says which side of the divider it belongs on.
    """

    subject: str
    group: Any
    amount_in_euro: float
    is_credit: bool


def subject_category_flows(
    result: LifecycleCostResult, mapping: Mapping[CostCategory, GroupKey]
) -> List[SubjectGroupFlow]:
    """Return what each subject costs and earns, split by display group.

    A subject x display-group pivot of the scoped timeline in present value (best-estimate slot), built with
    `CashFlowTimeline.npv_split_by` like the treemap, with costs and credits kept apart per entry. A subject's cost
    ribbons sum to its gross present cost, cost minus credit is its `npv_by_component` value, and a group's ribbons sum
    to the folded `npv_by_category`.

    Raises:
        CostDataError: If `mapping` declares no display group for a category on the timeline.
    """

    def cell_of(item: CashFlowEntry) -> Tuple[str, Any]:
        """Return the (subject, display group) cell one entry belongs in."""
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
    """The per-node sums behind the subject-to-group Sankey, as its labels and caption print them.

    Per subject the cost and the credit side separately, per group node its signed total. A node's drawn extent is
    costs plus credits stacked, which no table publishes, so these sums let labels, tooltip and caption print the same
    figures. `net_of` gives the subject's NPV as the breakdown table shows it.
    """

    costs_by_subject: Dict[str, float]
    credits_by_subject: Dict[str, float]
    signed_total_by_group: Dict[Tuple[Any, bool], float]

    def extent_of(self, subject: str) -> float:
        """The height the node is drawn at: costs and credits stacked, nothing netted."""
        return self.costs_by_subject.get(subject, 0.0) + self.credits_by_subject.get(subject, 0.0)

    def net_of(self, subject: str) -> float:
        """Return costs minus credits, the subject's net present value as the breakdown table prints it."""
        return self.costs_by_subject.get(subject, 0.0) - self.credits_by_subject.get(subject, 0.0)

    def widest_subject(self) -> Optional[str]:
        """Return the subject with the largest node, used as the caption's worked example."""
        if not self.costs_by_subject and not self.credits_by_subject:
            return None
        subjects = set(self.costs_by_subject) | set(self.credits_by_subject)
        return max(sorted(subjects), key=self.extent_of)


def subject_flow_margins(flows: Sequence[SubjectGroupFlow]) -> SubjectFlowMargins:
    """Sum the subject-to-group ribbons per node, giving the numbers the Sankey's labels state.

    Only the ribbons are read, so labels cannot disagree with the drawing: summed by subject (split by side) and by
    group node (signed, credit groups negative).

    Args:
        flows: The ribbons from `subject_category_flows`, in any order.

    Returns:
        The margins; a credit-only subject appears only in `credits_by_subject`.
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


# ============================================================================ energy balance

class EnergyBalanceLayout:
    """Node vocabulary and tolerances of the household electricity balance.

    Sources on the left, the house's electricity bus in the middle, sinks on the right, with the battery on both sides
    (discharging into the bus, charging from it); charge and discharge are not attributed to particular sources or
    loads, as the simulation does not. `MINIMUM_DEVICE_FLOWS` is the skip threshold: the two grid roles come from the
    meter in every run, so a balance needs device flows beyond them.
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
    """One terminal of the household energy balance: a quantity in kWh per year, a label and its money annotation.

    `annotation_in_euro` is the year-1 cost or revenue, set only on the two nodes that cross a billing boundary (grid
    import and export) and None elsewhere.
    """

    role: Optional[EnergyFlowRole]
    label: str
    quantity_in_kwh: float
    annotation_in_euro: Optional[float] = None


@dataclass(frozen=True)
class EnergyBalanceFlows:
    """The year-1 household electricity balance: sources, a bus and sinks, in kWh.

    The bus carries `bus_total_in_kwh`, the sum of both the sources and the sinks; any imbalance between the drawn
    terminals becomes an explicit `losses / unattributed` terminal. `self_consumption_share` is the share of PV
    generation used in the house and `self_sufficiency_share` the share of consumption not from the grid; both are None
    when their denominator is zero. `battery_round_trip_loss_in_kwh` is charge minus discharge, clamped at zero (more
    discharge than charge means charge carried over from the previous year). `unattributed_roles_in_kwh` lists role
    names this reader's `EnergyFlowRole` does not know; they have no side of the bus and are reported instead of
    dropped.
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
    """Read the energy attribution record once, returning the placeable roles and the rest.

    One pass keeps the two halves exhaustive and disjoint; `energy_balance_quantities` and
    `_unattributed_energy_roles_in_kwh` are its two projections.

    Args:
        result: The evaluated perspective.

    Returns:
        `(placeable, unplaceable)`: role -> annual kWh for roles `EnergyFlowRole` knows, and role name -> annual kWh
            for the rest. Zero quantities are dropped from both.
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
    """Return the result's per-subject energy record summed per balance role, in annual kWh.

    The subject dimension is dropped here (two PV arrays make one PV node). Only roles `EnergyFlowRole` knows appear;
    others are returned by `_unattributed_energy_roles_in_kwh` and carried on
    `EnergyBalanceFlows.unattributed_roles_in_kwh`, not absorbed into the residual node.
    """
    return _read_energy_attribution(result)[0]


def _unattributed_energy_roles_in_kwh(result: LifecycleCostResult) -> Dict[str, float]:
    """Return every attribution role name the balance cannot place, with its annual kWh.

    The complement of `energy_balance_quantities`. An unknown role has no known direction, so it cannot be a terminal,
    but it is reported rather than dropped.
    """
    return _read_energy_attribution(result)[1]


def has_energy_balance(result: LifecycleCostResult) -> bool:
    """Return whether the result has enough device flows to draw the household energy balance.

    Requires at least `MINIMUM_DEVICE_FLOWS` placeable device flows (the meter's own grid import and export do not
    count), and at least one placeable role from `EnergyBalanceLayout.SOURCE_ROLES` and one from `SINK_ROLES`, where
    the grid roles do count. A bus fed by nothing, or a meter feeding itself, is not drawn.
    """
    quantities = energy_balance_quantities(result)
    devices = [role for role in quantities if role not in EnergyBalanceLayout.METER_ROLES]
    if len(devices) < EnergyBalanceLayout.MINIMUM_DEVICE_FLOWS:
        return False
    return any(role in EnergyBalanceLayout.SOURCE_ROLES for role in quantities) and any(
        role in EnergyBalanceLayout.SINK_ROLES for role in quantities
    )


def energy_balance_flows(result: LifecycleCostResult) -> EnergyBalanceFlows:
    """Return where the house's electricity came from and where it went, in year-1 kWh.

    Sources (PV generation, grid import, battery discharge), the bus, and sinks (heat pump, household, grid export,
    battery charge). Money appears only as annotations on the two grid nodes, read from `carrier_year_one_bills`. The
    view checks that the grid nodes equal the bought and sold quantities of `annual_energy_quantities_by_carrier` (the
    meter the bills come from); the two sides of the bus balance because any remainder becomes a `losses /
    unattributed` terminal on the shorter side. Unknown roles go to `unattributed_roles_in_kwh` for the renderers to
    print.

    Args:
        result: The evaluated perspective; quantities come from `annual_energy_attribution_by_subject_in_kwh`, and
            callers check `has_energy_balance` first.

    Returns:
        The terminals, the bus total, the three derived figures the caption states and the roles that could not be
            placed.

    Raises:
        CostDataError: If the result carries no usable device flows, or if a grid node disagrees with the metered
            quantity.
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
    """Raise unless the balance's two grid nodes equal the metered carrier quantities.

    The import node is annotated with the year-1 electricity bill and the export node with the feed-in revenue, both
    computed from `annual_energy_quantities_by_carrier`; they must price the same quantity. Both figures come from the
    same meter columns and conversion, so the tolerance `QUANTITY_RELATIVE_EPSILON` (one millionth) only allows float
    residue. A record without grid nodes (an off-grid house) returns.

    Raises:
        CostDataError: If a grid node disagrees with the meter, or if grid nodes would be drawn for a result with no
            ELECTRICITY quantities.
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


# ============================================================================ wealth benchmark

class WealthBenchmarkGrid:
    """The interest-rate grid of the fixed-interest wealth benchmark.

    Sets the rates the trajectories are drawn for and thus the window in which a break-even rate can be reported; the
    view never extrapolates a break-even rate outside it.
    """

    #: 1 % to 10 % in 1 % steps: the range of savings rates a household actually compares against.
    RATES: Tuple[float, ...] = tuple(round(0.01 * step, 4) for step in range(1, 11))


@dataclass(frozen=True)
class WealthBenchmark:
    """Wealth advantage of renovating over banking the money, per interest rate.

    `series_by_rate[i][t]` is `W_i(t) = sum_{j<=t} d_j (1+i)^(t-j)`, with `d_j` the differential nominal flow of year j
    (reference minus variant); positive means the renovator is ahead of a household that did nothing and banked the
    difference at rate i. Interest is nominal and pre-tax, since capital-income tax is country-specific. The class
    checks `W_i(T) == (1+i)^T * NPV(i)` for every grid rate and the best-estimate parameter-rate line
    (`wealth_benchmark` checks the LOW and HIGH bands), and that the three rate-keyed collections share the same rates,
    every trajectory spans years 0..T and no break-even rate lies outside the grid.
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
        """Validate the shape and the future-value identity stated in the class docstring.

        Raises:
            CostDataError: On rates the three collections disagree on, a missing slot, a trajectory of the wrong
                length, a terminal that is not its series' last point, a broken future-value identity, or a break-even
                rate outside the grid.
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
        """Return the terminal wealth advantage at the evaluation's own discount rate.

        Agrees in sign with the comparison bridge's NPV delta: if renovating has the lower present cost, the renovator
        ends up richer.
        """
        return self.parameter_series_by_slot[Slot.BEST_ESTIMATE][-1]


def _check_future_value_identity(
    rate: float, horizon: int, terminal: float, flows: Sequence[float], what: str
) -> None:
    """Raise unless one benchmark trajectory ends at its present value carried forward.

    Checks `W_i(T) == (1+i)^T * NPV(i)`, with NPV recomputed through the package's `discount_factor`.

    Args:
        rate: The rate the trajectory was future-valued at.
        horizon: T, the last year of the series.
        terminal: `W_i(T)`, the trajectory's last point.
        flows: The differential nominal flow, index = year.
        what: The trajectory's name in the error, e.g. "grid rate 4 %".

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
    """Answer "should I just leave the money in the bank" as one series per interest rate.

    If the do-nothing household banks the unspent renovation money and the renovating one banks its annual savings,
    both at rate i, the wealth difference is the differential nominal cash flow future-valued at i. Computed for the
    `WealthBenchmarkGrid` rates plus the evaluation's own rate (with a LOW/HIGH band). It needs the two results, not a
    `VariantComparison`, because a rate grid needs the undiscounted per-year differential. The future-value identity is
    checked for the LOW and HIGH bands here and for the rest in `WealthBenchmark.__post_init__`.

    Raises:
        CostDataError: If the two results have different horizons, if either nominal series does not span its horizon,
            or if the future-value identity fails.
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
    """Return every rate inside the grid at which the terminal advantage changes sign.

    A list, not a single internal rate of return, because a differential series that changes sign more than once can
    have several. Crossings are linearly interpolated between grid points; nothing outside the grid is reported.
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


# ============================================================================ monthly burden

class BurdenCategories:
    """Which cost categories count as a monthly burden.

    Recurring flows are what a household budgets for: debt service, energy, maintenance and fixed operation, taxes and
    levies, minus recurring credits (feed-in revenue, a received levy). The year-0 financing event (investment,
    planning, removal, upfront support, loan disbursement) is excluded; it is shown in the funding statement.
    Replacements are capital expenditure and excluded as well: the `REPLACEMENT` set, which includes
    `REPLACEMENT_RESERVE` so a booked sinking fund is not counted twice, becomes the smoothed reserve line instead.
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


@dataclass(frozen=True)
class MonthlyBurden:
    """The monthly recurring cost per year plus the smoothed replacement reserve.

    `series` is the recurring burden the bars draw, year by year and slot-wise; `replacement_reserve_per_month` is the
    constant to set aside for the replacements the bars exclude, drawn as a dashed line. It is the replacement
    categories' NPV times the annuity factor (the factor of the headline EAC), divided by twelve, and zero when nothing
    is replaced.
    """

    series: List[UncertainValue]
    replacement_reserve_per_month: float


def monthly_burden_series(result: LifecycleCostResult) -> MonthlyBurden:
    """Return the recurring cost per month, year by year, plus the replacement reserve.

    The `BurdenCategories` recurring categories of the scoped timeline, slot-wise, divided by twelve; index = year.
    Replacements return as `replacement_reserve_per_month`, using `parameters.annuity_factor()`. `series[1]` is the
    recurring part of the monthly `monthly_cost_year1_in_euro`; twelve times the series is the recurring subset of
    `annual_cost_series_nominal_in_euro`; the tests check both and the reserve.
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
    reserve = replacement_npv * result.parameters.annuity_factor() / TimelineAggregation.MONTHS_PER_YEAR
    return MonthlyBurden(
        series=[value.scale(1.0 / TimelineAggregation.MONTHS_PER_YEAR) for value in per_year],
        replacement_reserve_per_month=reserve,
    )


def _recurring_entries_by_year(result: LifecycleCostResult) -> List[List[CashFlowEntry]]:
    """Return the scoped timeline's recurring flows, bucketed by year 0..T.

    The single selection of which categories count as a monthly burden and which years are on the axis. Both
    `monthly_burden_series` and `monthly_burden_by_group` build on it, so the stacked bars add up to the totals they
    fill; each divides by `TimelineAggregation.MONTHS_PER_YEAR` itself.

    Args:
        result: The perspective whose burden is drawn.

    Returns:
        One list per year, index = year; a year without recurring flows is an empty list, so the two views stay
            index-aligned.
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
    """Return the monthly burden split by display group, best-estimate slot, for the stacked bars.

    Uses the same selection as `monthly_burden_series` (`_recurring_entries_by_year`) and folds it onto the caller's
    groups. Row order is the year index; a year without recurring flows is an empty dict.

    Raises:
        CostDataError: If `mapping` declares no display group for a recurring category.
    """
    rows: List[Dict[CostCategory, float]] = []
    for entries in _recurring_entries_by_year(result):
        row: Dict[CostCategory, float] = {}
        for item in entries:
            row[item.category] = (
                row.get(item.category, 0.0)
                + item.amount_in_euro.best_estimate / TimelineAggregation.MONTHS_PER_YEAR
            )
        rows.append(row)
    return fold_category_matrix(rows, mapping)


# ============================================================================ equity build-up

@dataclass(frozen=True)
class AssetDebtSeries:
    """Book value, outstanding debt and the equity between them, per year, in the best-estimate slot.

    The book value is straight-line depreciation of every install and replacement the timeline charged, on the residual
    calculator's basis, so `book_value_in_euro[-1]` equals the booked residual-value credit. `debt_in_euro` is the
    outstanding balance clamped at zero (a negative balance is an overpayment, not debt), so `equity == book - debt`
    holds in every year. `underwater_intervals` lists each maximal run of years with negative equity as `(first,
    last)`, since equity can go negative, recover and go negative again; an empty list means never.
    `depreciation_life_by_subject` is the life each subject's last installation was depreciated over, derived from the
    booked events, not the catalog.
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
    """Return asset book value against outstanding debt, and the equity between them.

    Each charged install or replacement steps the book value up by its amount and then declines linearly to zero. The
    last event of a subject declines over the life derived from its residual credit (`residual = amount * (install +
    life - T) / life` solved for the life), or over the remaining horizon if it has no residual. An earlier event
    declines over the shorter of that life and the gap to its successor (`_write_off_span`). Debt is the clamped
    outstanding balance of `loan_amortization_series`. The view checks that the horizon book value equals the booked
    residual credit.

    Args:
        result: The perspective whose book value and debt are built.
        amortization: Its `loan_amortization_series`, if the caller already has it; derived here when omitted.

    Returns:
        The two series, the equity between them and the runs of negative equity.

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
    """Return how long one charged installation is written down over: its life, or until it is replaced.

    The last installation uses `life`; an earlier one the shorter of `life` and the years until its successor. A gap
    counts as shorter only beyond `ViewTolerances.DEPRECIATION_SPACING_RELATIVE_EPSILON`, because the engine re-invests
    at the rounded service life and the two values differ by float residue.

    Args:
        life: The subject's depreciation life, from `_depreciation_life`.
        event: The installation being written down.
        successor: The next event of the same subject, or None for the last one.

    Returns:
        The number of years to write the event off over, at least one.
    """
    if successor is None:
        return life
    gap = float(successor.year - event.year)
    if gap < life * (1.0 - ViewTolerances.DEPRECIATION_SPACING_RELATIVE_EPSILON):
        return max(gap, 1.0)
    return life


def _depreciation_life(last_event: LifecycleEvent, residual_in_euro: float, horizon: int) -> float:
    """Return the life the last charged installation is written down over, derived from the booking.

    Inverts the residual calculator's straight-line rule `residual = amount * (install + life - T) / life`, so the
    chart's endpoint equals the residual. Without a residual credit the installation depreciates over the years that
    remain, reproducing the zero the timeline booked. The result is at least one year. Earlier installations are capped
    by `_write_off_span`.
    """
    remaining_years = max(horizon - last_event.year, 0)
    ratio = residual_in_euro / last_event.amount_in_euro if last_event.amount_in_euro else 0.0
    if 0.0 < ratio < 1.0:
        return max(remaining_years / (1.0 - ratio), 1.0)
    return max(float(remaining_years), 1.0)


# ----- the causes the story chapters state -----

# ================================================ the assumptions behind the numbers


class AssumptionKinds(str, enum.Enum):
    """What kind of quantity an `AssumptionRow` carries, which decides how it is printed.

    Example spellings: a rate `3.00%`, a working price `0.2500 EUR/kWh`, an energy quantity `15,000 kWh/a`. The view
    returns numbers and the report section formats them. The physical unit is not part of the kind; it travels in the
    row's `unit` field.
    """

    PERCENT = "percent"
    YEARS = "years"
    YEAR = "year"
    FACTOR = "factor"
    EURO_PER_KWH = "euro_per_kwh"
    EURO_PER_YEAR = "euro_per_year"
    EURO_PER_TON = "euro_per_ton"
    KWH_PER_YEAR = "kwh_per_year"
    SQUARE_METERS = "square_meters"
    #: A number whose unit the view cannot know — a scenario override reaching into a data file,
    #: where no enumeration of fields exists. Printed with thousands separators and enough
    #: significant digits that a fractional per-kWh price survives beside a five-figure price.
    NUMBER = "number"
    #: Text that is already the value — the CO2 price scenario's name, not a quantity.
    PLAIN = "plain"


@dataclass(frozen=True)
class AssumptionRow:
    """One economic assumption: what it is, its value and where it came from.

    Plain data: `value` is the number, `unit` the symbol printed after it, `kind` how the number is spelled. `source`
    is a citation where the data layer has one (a database file, the country escalation defaults, a tariff contract
    id), or the literal "configuration" when the value is a run parameter.
    """

    group: str
    name: str
    value: Union[float, int, str]
    source: str
    kind: AssumptionKinds = AssumptionKinds.PLAIN
    #: The symbol printed after the formatted number (`EUR/kWh`, `a`, `per year`), or empty.
    unit: str = ""
    #: True for a value the engine computed from the others rather than read (the annuity
    #: factor), so the section can mark it as derived instead of implying it was configured.
    is_computed: bool = False


class AssumptionGroups:
    """The groups of the assumptions table, in print order.

    Shared by the section, the completeness tests and exports. The order follows how a reader rebuilds a number: the
    discounting frame, price movement, energy prices, the physical quantities per-unit figures divide by, and the CO2
    damage cost used by one chapter.
    """

    FRAME = "Calculation frame"
    ESCALATION = "Escalation rates"
    TARIFFS = "Energy tariffs"
    QUANTITIES = "Building quantities"
    SOCIETY = "Macroeconomic"
    ORDER = (FRAME, ESCALATION, TARIFFS, QUANTITIES, SOCIETY)
    #: The source text for a value that is a run parameter rather than reviewed data.
    CONFIGURATION_SOURCE = "configuration"


def economic_assumptions(results: Sequence[LifecycleCostResult]) -> List[AssumptionRow]:
    """Return every economic assumption this run was priced under, with value and source.

    Covers the interest rate, horizon and price basis year; the annuity factor they imply (marked as computed); every
    escalation rate that applied, with the step of the §3.2 fallback chain that produced it; the working price,
    standing charge and feed-in rate of every billed carrier; the building quantities per-unit figures divide by; and
    the CO2 damage cost when a perspective of the run priced one. Parameters come from `result.parameters`, resolved
    values from `result.assumptions`; a result without that record contributes no resolved rows.

    Args:
        results: The run's evaluated perspectives; values are read from the first, and the damage-cost row is added
            when any of them uses macroeconomic accounting (`has_macroeconomic_accounting`).

    Returns:
        The rows in `AssumptionGroups.ORDER`.

    Raises:
        CostDataError: If `results` is empty.
    """
    evaluated = list(results)
    if not evaluated:
        raise CostDataError(
            "The assumptions table states the causes of one run, and this call carries no "
            "evaluated perspective at all, so there is no interest rate, horizon or tariff to "
            "state — not even an empty table's worth."
        )
    result = evaluated[0]
    params = result.parameters
    configuration = AssumptionGroups.CONFIGURATION_SOURCE
    # An absent basis year is not a configured one: the engine falls back to the simulation year,
    # and citing "configuration" for it would credit the run with a decision nobody made. A result
    # that carries neither states that, rather than printing the word None as a year.
    basis_year = params.price_basis_year if params.price_basis_year is not None else result.simulation_year
    basis_source = configuration if params.price_basis_year is not None else "simulation year (default)"
    rows: List[AssumptionRow] = [
        AssumptionRow(AssumptionGroups.FRAME, "interest rate (discount rate)",
                      params.interest_rate, configuration, AssumptionKinds.PERCENT),
        AssumptionRow(AssumptionGroups.FRAME, "observation period",
                      params.observation_period_in_years, configuration,
                      AssumptionKinds.YEARS, "a"),
        AssumptionRow(AssumptionGroups.FRAME, "price basis year",
                      basis_year if basis_year is not None else "not stated",
                      basis_source if basis_year is not None else "neither configured nor simulated",
                      AssumptionKinds.YEAR if basis_year is not None else AssumptionKinds.PLAIN),
        AssumptionRow(AssumptionGroups.FRAME, "annuity factor",
                      params.annuity_factor(),
                      "computed from the interest rate and the horizon",
                      AssumptionKinds.FACTOR, is_computed=True),
    ]
    assumptions = result.assumptions
    if assumptions is not None:
        for label, rate in assumptions.escalation_rates.items():
            rows.append(
                AssumptionRow(
                    AssumptionGroups.ESCALATION,
                    _escalation_row_name(label),
                    rate.rate,
                    _rate_source(rate, configuration),
                    AssumptionKinds.PERCENT,
                    "per year",
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
                    tariff.working_price_in_euro_per_kwh.best_estimate,
                    source,
                    AssumptionKinds.EURO_PER_KWH,
                    "EUR/kWh",
                )
            )
            rows.append(
                AssumptionRow(
                    AssumptionGroups.TARIFFS,
                    f"{carrier}: standing charge",
                    tariff.standing_charge_in_euro_per_year.best_estimate,
                    source,
                    AssumptionKinds.EURO_PER_YEAR,
                    "EUR/a",
                )
            )
            if tariff.feed_in_rate_in_euro_per_kwh is not None:
                rows.append(
                    AssumptionRow(
                        AssumptionGroups.TARIFFS,
                        f"{carrier}: feed-in rate ({tariff.feed_in_kind.value})",
                        tariff.feed_in_rate_in_euro_per_kwh.best_estimate,
                        source,
                        AssumptionKinds.EURO_PER_KWH,
                        "EUR/kWh",
                    )
                )
    areas = result.reference_areas
    if areas.living_area_in_m2 is not None:
        rows.append(AssumptionRow(AssumptionGroups.QUANTITIES, "living area",
                                  areas.living_area_in_m2, configuration,
                                  AssumptionKinds.SQUARE_METERS, "m2"))
    if areas.heated_floor_area_in_m2 is not None:
        rows.append(AssumptionRow(AssumptionGroups.QUANTITIES, "heated floor area",
                                  areas.heated_floor_area_in_m2, configuration,
                                  AssumptionKinds.SQUARE_METERS, "m2"))
    heat_demand = assumptions.annual_heat_demand_in_kwh if assumptions is not None else None
    # `is not None`, not truthiness: a stored result may carry a declared zero demand, and dropping
    # the row would present that run as one that never declared a demand. For a staged plan the
    # figure is the equivalent annual heat of the horizon (`EconomicAssumptions`).
    if heat_demand is not None:
        rows.append(AssumptionRow(AssumptionGroups.QUANTITIES, "annual heat demand",
                                  heat_demand, configuration,
                                  AssumptionKinds.KWH_PER_YEAR, "kWh/a"))
    for carrier, quantities in result.annual_energy_quantities_by_carrier.items():
        rows.append(
            AssumptionRow(
                AssumptionGroups.QUANTITIES,
                f"{carrier}: energy bought (annualized)",
                quantities.bought_in_kwh,
                "simulation output",
                AssumptionKinds.KWH_PER_YEAR,
                "kWh/a",
            )
        )
        if quantities.sold_in_kwh:
            rows.append(
                AssumptionRow(
                    AssumptionGroups.QUANTITIES,
                    f"{carrier}: energy sold (annualized)",
                    quantities.sold_in_kwh,
                    "simulation output",
                    AssumptionKinds.KWH_PER_YEAR,
                    "kWh/a",
                )
            )
    if any(has_macroeconomic_accounting(evaluation) for evaluation in evaluated):
        rows.append(
            AssumptionRow(
                AssumptionGroups.SOCIETY,
                "CO2 damage cost (flat over the horizon)",
                params.co2_damage_cost_in_euro_per_ton,
                configuration,
                AssumptionKinds.EURO_PER_TON,
                "EUR/t",
            )
        )
    rows.append(
        AssumptionRow(
            AssumptionGroups.SOCIETY,
            "CO2 price scenario (path on the energy bill)",
            params.co2_price_scenario,
            configuration,
            AssumptionKinds.PLAIN,
        )
    )
    order = {group: index for index, group in enumerate(AssumptionGroups.ORDER)}
    return sorted(rows, key=lambda row: order.get(row.group, len(order)))


def _escalation_row_name(label: str) -> str:
    """Return the reader's name for one escalation-rate key (`energy:electricity` -> "energy: electricity")."""
    if ":" not in label:
        return f"{label} prices"
    kind, subject = label.split(":", 1)
    return f"{kind}: {subject}"


def _rate_source(rate: ResolvedRate, configuration: str) -> str:
    """Return the citation for one resolved escalation rate, by the step of the chain that produced it.

    A configured rate cites the run (`configuration`), a rate from the country defaults file cites that file's sources,
    and a rate that fell back to the general one says so.
    """
    if rate.origin == RateOrigin.COUNTRY_DEFAULTS and rate.source_ids:
        return ", ".join(rate.source_ids)
    if rate.origin == RateOrigin.COUNTRY_DEFAULTS:
        return "country escalation defaults"
    if rate.origin == RateOrigin.GENERAL_FALLBACK:
        return f"{configuration} (general fallback)"
    return configuration


# ============================================================ CO2 conversion factors


class Co2FactorKinds(str, enum.Enum):
    """The two kinds of CO2 factor row: an operational carrier row and an embodied device row.

    The kind decides which of the row's two products must be filled in and how the section words the row.
    """

    OPERATIONAL = "operational"
    EMBODIED = "embodied"


@dataclass(frozen=True)
class Co2FactorRow:
    """One line of the CO2 factors table: a mass and the multiplication that produced it.

    An operational row is a carrier: `factor_in_kg_per_unit` in kg per kWh times `quantity` (annual kWh bought) gives
    `annual_mass_in_kg`, repeated every year. An embodied row is a device: kg per size unit times the installed size
    gives `per_installation_in_kg`, charged `installations` times within the horizon. `total_in_kg` is the figure the
    chart draws; `__post_init__` checks that the multiplication produces it.
    """

    subject: str
    kind: Co2FactorKinds
    factor_in_kg_per_unit: float
    quantity: float
    quantity_unit: str
    total_in_kg: float
    annual_mass_in_kg: Optional[float] = None
    per_installation_in_kg: Optional[float] = None
    installations: int = 1

    def __post_init__(self) -> None:
        """Refuse a row whose multiplication does not produce its stated total.

        Raises:
            CostDataError: If the row carries neither or both of the two products, or if the product times
                `installations` misses `total_in_kg`.
        """
        is_operational = self.kind == Co2FactorKinds.OPERATIONAL
        product = self.annual_mass_in_kg if is_operational else self.per_installation_in_kg
        other = self.per_installation_in_kg if is_operational else self.annual_mass_in_kg
        expected_field = "annual_mass_in_kg" if is_operational else "per_installation_in_kg"
        if product is None or other is not None:
            raise CostDataError(
                f"A {self.kind.value} CO2 factor row states its mass as {expected_field}, and "
                f"{self.subject!r} carries annual_mass_in_kg={self.annual_mass_in_kg!r} and "
                f"per_installation_in_kg={self.per_installation_in_kg!r}. Exactly one of the two "
                "belongs on a row: the other product is a different multiplication."
            )
        expected = product * self.installations
        if abs(expected - self.total_in_kg) > max(
            ViewTolerances.RECONCILIATION_EPSILON, abs(self.total_in_kg) * 1e-9
        ):
            raise CostDataError(
                f"The CO2 factor row for {self.subject!r} does not multiply out: "
                f"{self.factor_in_kg_per_unit:g} kg/{self.quantity_unit} x "
                f"{self.quantity:,.2f} {self.quantity_unit} = {product:,.3f} kg x "
                f"{self.installations} = {expected:,.3f} kg, but the accounting published "
                f"{self.total_in_kg:,.3f} kg for it. The table states the mass as this "
                "multiplication, so the two have to be one figure."
            )


def co2_factor_rows(result: LifecycleCostResult) -> List[Co2FactorRow]:
    """Return the conversions behind every CO2 mass the report publishes.

    Per carrier: emission factor x annual kWh bought = mass per year, times the horizon = the carrier total. Per
    device: embodied factor x installed size = mass per installation, times the installations within the horizon.
    Factors are the ones the engine recorded while computing the masses, never a mass divided by a quantity. The CO2
    accumulator and `annual_energy_quantities_by_carrier` sum over the same billing records, and a carrier billed under
    two emission factors is refused earlier, so factor times kWh equals the mass exactly. A result without recorded
    factors yields no rows.

    Args:
        result: The perspective whose CO2 accounting is stated.

    Returns:
        Operational rows first, then embodied ones, each in the accounting's own order.

    Raises:
        CostDataError: From `Co2FactorRow`, when a row's multiplication does not reproduce the published mass.
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


# ----- captions -----

# =============================================== the scenario cube interface, named once


class ScenarioDefinitionView(Protocol):
    """The expanded definition of one scenario cell, as the report's captions read it.

    Presentation may not import `scenarios`, so these four protocols name exactly the attributes the report reads off a
    scenario cube; renaming one on `scenarios.Scenario` then fails the type check here instead of at render time.
    Members are read-only properties, because a mutable protocol attribute would be invariant and reject the cube's
    `Dict` and `List` fields.
    """

    @property
    def id(self) -> str:
        """The cell's id, the key of every per-scenario mapping the report prints."""

    @property
    def parameter_overrides(self) -> Mapping[str, Any]:
        """Dotted `EconomicParameters` path -> the value this cell was evaluated at."""

    @property
    def data_overlays(self) -> Mapping[str, Any]:
        """Dotted data-file path -> the value this cell overlaid the shipped data with."""


class KpiSpreadView(Protocol):
    """How far the headline KPI travelled across the cube, as the robustness table reads it."""

    @property
    def minimum(self) -> float:
        """The lowest value the KPI took over the evaluated cells."""

    @property
    def maximum(self) -> float:
        """The highest value the KPI took over the evaluated cells."""

    @property
    def spread(self) -> float:
        """`maximum - minimum`, the travel of the KPI over the cube."""


class ScenarioSetView(Protocol):
    """What the two caption views need: the cell definitions and which of them is the central one."""

    @property
    def base_id(self) -> str:
        """The id of the cell every swing is measured against."""

    @property
    def scenarios(self) -> Sequence[ScenarioDefinitionView]:
        """The expanded cell definitions, the base cell included."""


class ScenarioCubeView(ScenarioSetView, Protocol):
    """Everything the report's scenario section reads off an evaluated cube (§4.6)."""

    @property
    def results(self) -> Mapping[str, Mapping[str, LifecycleCostResult]]:
        """`[perspective][scenario]` -> the evaluated cell."""

    def equivalent_annual_cost_swings(self, perspective: str) -> Dict[str, float]:
        """Per-scenario EAC swing against the base cell, the base included as zero."""

    def equivalent_annual_cost_spreads(self) -> Mapping[str, KpiSpreadView]:
        """Min/max/spread of the headline KPI, per perspective."""


#: Distinguishes "this dict has no such key" from "this key is present and holds None", which the
#: central-value walk below must keep apart: the first may still have a rate recorded on the
#: result, the second is an optional parameter the engine resolves elsewhere.
_ABSENT: Any = object()


# ============================================= the heat-cost KPI as its own division


@dataclass(frozen=True)
class LevelizedHeatCostDerivation:
    """The heat-cost figure written out as its division: numerator, denominator and quotient.

    The engine publishes `system cost per unit of heat = equivalent annual cost / annual heat demand`. The numerator is
    the perspective's entire NPV, every subject included (PV and battery too), so on a multi-technology building it is
    what the whole installation costs per kWh of heat, which is why the KPI is not called a levelized cost of heat
    (`results.HeatCostNaming`). Dividing by the annuity factor equals dividing by the discounted sum of heat over the
    horizon, the form the LCOH literature uses; both forms are given. For a staged plan the recorded demand is already
    the equivalent annual heat of the horizon, so the same forms hold. Every figure is read off the result, not
    recomputed.
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
    #: True when the result stored no heat demand and the denominator was divided back out of the
    #: published figure instead. The division then reproduces the KPI by construction rather than
    #: as a check, and the caption has to say so — see `levelized_heat_cost_derivation`.
    demand_inferred_from_published: bool = False

    def stated_figures(self) -> Tuple[Any, ...]:
        """Return everything the caption states except the perspective's identity.

        The section prints one sentence when all perspectives share the same division and one line per perspective
        otherwise; this is the value it compares.
        """
        return (
            self.numerator_npv_in_euro,
            self.annuity_factor,
            self.equivalent_annual_cost_in_euro,
            self.annual_heat_demand_in_kwh,
            self.discounted_heat_in_kwh,
            self.levelized_cost_in_euro_per_kwh,
            self.attributed_subjects,
            self.demand_inferred_from_published,
        )


def levelized_heat_cost_derivation(
    result: LifecycleCostResult,
) -> Optional[LevelizedHeatCostDerivation]:
    """Return the division behind the published heat-cost KPI, or None when the run publishes none.

    The numerator is `total_npv_in_euro`, its annualization `equivalent_annual_cost_in_euro`, and the quotient is
    checked against `levelized_cost_of_heat_in_euro_per_kwh`. A stored result without a recorded heat demand still gets
    a derivation, with the demand divided back out of the published figure; it then sets
    `demand_inferred_from_published`, skips the check (which would be circular), and the caption says where the
    denominator came from.

    Args:
        result: The perspective to explain.

    Returns:
        The derivation, or None when the perspective publishes no heat-cost figure (no heat demand was declared).

    Raises:
        CostDataError: If the stated division does not reproduce the published figure.
    """
    published = result.levelized_cost_of_heat_in_euro_per_kwh
    if published is None:
        return None
    annuity = result.parameters.annuity_factor()
    equivalent_annual = result.equivalent_annual_cost_in_euro.best_estimate
    recorded = (
        result.assumptions.annual_heat_demand_in_kwh if result.assumptions is not None else None
    )
    inferred = not recorded
    if recorded:
        heat_demand = recorded
        quotient = equivalent_annual / heat_demand
        if abs(quotient - published.best_estimate) > ViewTolerances.RECONCILIATION_EPSILON:
            raise CostDataError(
                f"System cost per unit of heat does not reconcile for perspective "
                f"{result.perspective_id!r}: equivalent annual cost {equivalent_annual:,.2f} "
                f"EUR/a / {heat_demand:,.0f} kWh = {quotient:.4f} EUR/kWh, but the published "
                f"figure is {published.best_estimate:.4f} EUR/kWh."
            )
    else:
        if not published.best_estimate:
            return None
        heat_demand = equivalent_annual / published.best_estimate
    if not heat_demand:
        return None
    return LevelizedHeatCostDerivation(
        perspective_id=result.perspective_id,
        numerator_npv_in_euro=result.total_npv_in_euro.best_estimate,
        annuity_factor=annuity,
        equivalent_annual_cost_in_euro=equivalent_annual,
        annual_heat_demand_in_kwh=heat_demand,
        # The annuity factor is `i(1+i)^T / ((1+i)^T - 1)` or `1/T`, both strictly positive for
        # every parameter set `EconomicParameters.__post_init__` admits, so there is no zero to
        # guard against here.
        discounted_heat_in_kwh=heat_demand / annuity,
        levelized_cost_in_euro_per_kwh=published.best_estimate,
        attributed_subjects=tuple(
            dict.fromkeys(entry.subject for entry in result.scoped_timeline().entries)
        ),
        demand_inferred_from_published=inferred,
    )


def levelized_heat_cost_derivations(
    matrix: EvaluationMatrix,
) -> Dict[str, LevelizedHeatCostDerivation]:
    """Return one heat-cost derivation per perspective that publishes the figure, in matrix order.

    Each perspective's numerator is its own whole NPV, so a caption for one would mis-describe the others; the section
    decides whether they agree.

    Args:
        matrix: Every evaluated perspective.

    Returns:
        `{perspective id: derivation}`, omitting perspectives without the figure.

    Raises:
        CostDataError: If any perspective's division does not reproduce its published figure.
    """
    derivations: Dict[str, LevelizedHeatCostDerivation] = {}
    for perspective_id, result in matrix.results.items():
        derivation = levelized_heat_cost_derivation(result)
        if derivation is not None:
            derivations[perspective_id] = derivation
    return derivations


# ================================================== the anyway credit as a multiplication


@dataclass(frozen=True)
class AnywayCreditFact:
    """One booked anyway credit as share, basis and product, so `share x basis = credit` can be checked.

    The anyway credit is the avoided cost of work the building needed regardless. Its basis is either the avoided
    like-for-like replacement of the old asset or the non-energy share of the new measure's gross cost; `kind` says
    which, so the caption words it correctly.
    """

    subject: str
    share: float
    #: The cost the share was applied to, or None for a stored result without a recorded basis,
    #: in which case only the share can be stated.
    basis_in_euro: Optional[float]
    #: What that basis is, from `results.AnywayBasisKinds`; `UNRECORDED` for a stored result
    #: without a recorded kind, where the basis is known but its name is not.
    basis_kind: str
    #: `share x basis`, or None when there is no basis to multiply.
    credit_in_euro: Optional[float]


def anyway_credit_facts(result: LifecycleCostResult) -> Tuple[AnywayCreditFact, ...]:
    """Return every anyway credit this perspective booked, by subject, as a checkable product.

    Args:
        result: The perspective whose recorded shares, bases and kinds are read.

    Returns:
        One fact per credited subject, sorted by subject; empty for a run that credits nothing.
    """
    bases = result.anyway_basis_by_subject
    kinds = result.anyway_basis_kind_by_subject
    facts = []
    for subject, share in sorted(result.anyway_share_by_subject.items()):
        basis = bases.get(subject) or None
        facts.append(
            AnywayCreditFact(
                subject=subject,
                share=share,
                basis_in_euro=basis,
                basis_kind=kinds.get(subject, AnywayBasisKinds.UNRECORDED),
                credit_in_euro=share * basis if basis is not None else None,
            )
        )
    return tuple(facts)


def anyway_credit_facts_by_perspective(
    matrix: EvaluationMatrix,
) -> Dict[str, Tuple[AnywayCreditFact, ...]]:
    """Return the booked anyway credits of every perspective, in matrix order.

    A perspective without investment books no anyway credit, so credits are stated per perspective; the caption
    collapses them into one sentence only when they agree.

    Args:
        matrix: Every evaluated perspective.

    Returns:
        `{perspective id: facts}` for every perspective, including empty tuples.
    """
    return {
        perspective_id: anyway_credit_facts(result)
        for perspective_id, result in matrix.results.items()
    }


# ================================================ what each scenario row actually changed


class CentralCase(str, enum.Enum):
    """How the central case's value of a scenario field is known, or why it is not.

    A scenario row pairs the value a cell was evaluated at with the central case's value. The four members separate a
    known value from a dict key the run never configured, an optional parameter left unset, and a path that is not on
    `EconomicParameters` (a data-file field, the only case where "as shipped" is true). The section decides the
    wording; `_central_case` decides the member.
    """

    #: A value was read off the base cell's parameters and travels in `central_value`.
    VALUE = "value"
    #: The field is optional and unset, and the engine resolved it to the general escalation rate;
    #: that resolved rate is in `central_value` and the section says where it came from.
    RESOLVED_FROM_GENERAL = "resolved_from_general"
    #: A data overlay replaced shipped data outright — there is no central *parameter* to compare
    #: against, only the data files as they ship.
    AS_SHIPPED = "as_shipped"
    #: The field is not reachable on `EconomicParameters` (a price-entry field), or a dict key the
    #: run never configured and never recorded a resolved rate for. Nothing is claimed.
    NOT_RECORDED = "not_recorded"


@dataclass(frozen=True)
class ScenarioAssumption:
    """One field a scenario changed, with both of its values, as plain data.

    `kind` says how the numbers are spelled, `scenario_band` carries the min/best_estimate/max of a data overlay when
    there is one, and `key` is set only for a whole-dict override, which renders one line per key.
    """

    field_name: str
    #: The dict key this line is about, for a whole-dict override; empty for every other shape.
    key: str
    #: The value the cell was evaluated at — the best_estimate slot when `scenario_band` is set.
    scenario_value: Any
    #: `(min, best_estimate, max)` for a band overlay, None for a scalar value.
    scenario_band: Optional[Tuple[float, float, float]]
    central_case: CentralCase
    #: The central case's value; meaningful for `VALUE` and `RESOLVED_FROM_GENERAL` only.
    central_value: Any
    kind: AssumptionKinds


class ScenarioValueKinds:
    """Which quantity each scenario-addressable field carries, declared per field.

    Whether `0.05` prints as `5.00%` or `0.05` depends on the field, so it is declared rather than guessed from the
    name. `BY_PARAMETER` covers every `EconomicParameters` field (a test checks this). For data-overlay paths,
    `PERCENT_LEAVES` names the leaf fields that are fractions of one; everything else prints as a plain number.
    """

    BY_PARAMETER = {
        "observation_period_in_years": AssumptionKinds.YEARS,
        "interest_rate": AssumptionKinds.PERCENT,
        "general_price_escalation_rate": AssumptionKinds.PERCENT,
        "energy_price_escalation_rates": AssumptionKinds.PERCENT,
        "feed_in_escalation_rate": AssumptionKinds.PERCENT,
        "investment_price_escalation_rate": AssumptionKinds.PERCENT,
        "investment_price_escalation_rates": AssumptionKinds.PERCENT,
        "co2_price_scenario": AssumptionKinds.PLAIN,
        "co2_damage_cost_in_euro_per_ton": AssumptionKinds.EURO_PER_TON,
        "price_basis_year": AssumptionKinds.YEAR,
        "country": AssumptionKinds.PLAIN,
        "apply_subsidies": AssumptionKinds.PLAIN,
        "cost_database_path": AssumptionKinds.PLAIN,
        "subsidy_catalog_path": AssumptionKinds.PLAIN,
        "spread_escalation_rate": AssumptionKinds.PERCENT,
        "grid_fee_escalation_rate": AssumptionKinds.PERCENT,
        "anyway_threshold_years": AssumptionKinds.YEARS,
        "allow_counterfactual_billing": AssumptionKinds.PLAIN,
        # Per-carrier price terms in two units; not sweepable (`scenarios.ScenarioLimits`), so no
        # scenario row ever prints one, and the kind only completes the table.
        "energy_prices": AssumptionKinds.PLAIN,
    }
    #: Leaf names of *data-file* paths whose value is a fraction of one and reads as a percentage.
    PERCENT_LEAVES = ("co2_price_exposure", "tax_and_levy_share", "energy_related_cost_share")
    #: The optional parameters the engine resolves to `general_price_escalation_rate` when they
    #: are left unset (`calculators/energy.py`), so an axis on one has a central value after all.
    #: `spread_escalation_rate` is deliberately absent: it falls back to the *carrier's* rate,
    #: which is a different number per carrier and therefore not one central value.
    RESOLVED_FROM_GENERAL = ("grid_fee_escalation_rate",)


def scenario_assumptions(
    scenario_cube: Optional[ScenarioSetView], base_result: LifecycleCostResult
) -> Dict[str, Tuple[ScenarioAssumption, ...]]:
    """Return, per scenario id, the assumptions it changed with both values.

    A row labelled `interest=high` says what was varied but not to what. This pairs each override from the cube's
    expanded definitions with the central case's value, so the row can read "interest_rate 5.00% (central case:
    3.00%)".

    Args:
        scenario_cube: The evaluated cube, or None.
        base_result: The central cell's result, read for the values a scenario deviates from.

    Returns:
        `{scenario id: assumptions}`, empty for the base cell and for any scenario whose definition the cube did not
            keep.

    Raises:
        CostDataError: If a field declared as a fraction of one carries a value outside [-1, 1] (e.g. `5` meant as
            `0.05`).
    """
    if scenario_cube is None:
        return {}
    assumptions: Dict[str, Tuple[ScenarioAssumption, ...]] = {}
    for scenario in scenario_cube.scenarios:
        parts: List[ScenarioAssumption] = []
        for field_name, value in scenario.parameter_overrides.items():
            parts.extend(_scenario_assumptions_for(field_name, value, base_result, overlay=False))
        for field_name, value in scenario.data_overlays.items():
            parts.extend(_scenario_assumptions_for(field_name, value, base_result, overlay=True))
        if parts:
            assumptions[scenario.id] = tuple(parts)
    return assumptions


def _scenario_assumptions_for(
    field_name: str, value: Any, base_result: LifecycleCostResult, overlay: bool
) -> List[ScenarioAssumption]:
    """Return the line or lines one override states.

    A band (a data overlay in the §3.1 `min`/`best_estimate`/`max` syntax) is one line. A whole dict on a dict-typed
    field is the merge form `scenarios.apply_parameter_overrides` applies key by key, so it gives one line per key,
    each against that key's central value. Anything else is a scalar and one line.
    """
    if isinstance(value, dict):
        band = _value_band(value)
        if band is None:
            return [
                _one_assumption(
                    field_name,
                    _key_name(key),
                    f"{field_name}.{_key_name(key)}",
                    sub_value,
                    None,
                    base_result,
                    overlay,
                )
                for key, sub_value in value.items()
            ]
        return [_one_assumption(field_name, "", field_name, band[1], band, base_result, overlay)]
    return [_one_assumption(field_name, "", field_name, value, None, base_result, overlay)]


def _one_assumption(
    field_name: str,
    key: str,
    path: str,
    value: Any,
    band: Optional[Tuple[float, float, float]],
    base_result: LifecycleCostResult,
    overlay: bool,
) -> ScenarioAssumption:
    """Return one line's data: the changed value and the central case beside it.

    `field_name` and `key` are what the line reads as; `path` is the full dotted path the central value and the kind
    are looked up under.
    """
    kind = _scenario_value_kind(path)
    for component in band if band is not None else (value,):
        _refuse_non_fraction(path, component, kind)
    central_case, central_value = (
        (CentralCase.AS_SHIPPED, None) if overlay else _central_case(path, base_result)
    )
    if central_case in (CentralCase.VALUE, CentralCase.RESOLVED_FROM_GENERAL):
        _refuse_non_fraction(path, central_value, kind)
    return ScenarioAssumption(
        field_name=field_name,
        key=key,
        scenario_value=value,
        scenario_band=band,
        central_case=central_case,
        central_value=central_value,
        kind=kind,
    )


def _value_band(value: Mapping[str, Any]) -> Optional[Tuple[float, float, float]]:
    """Return `(min, best_estimate, max)` when this dict is the §3.1 band syntax, else None.

    Those three keys are what `UncertainValue.from_json` reads, and no dict-typed `EconomicParameters` field uses them,
    so they separate a band from the merge form.
    """
    keys = ("min", "best_estimate", "max")
    if not all(key in value for key in keys):
        return None
    return (float(value["min"]), float(value["best_estimate"]), float(value["max"]))


def _scenario_value_kind(field_name: str) -> AssumptionKinds:
    """Return which spelling one scenario-addressable path's value takes."""
    declared = ScenarioValueKinds.BY_PARAMETER.get(field_name.split(".", 1)[0])
    if declared is not None:
        return declared
    leaf = field_name.rsplit(".", 1)[-1]
    if leaf in ScenarioValueKinds.PERCENT_LEAVES or leaf.endswith(("rate", "rates", "share", "shares")):
        return AssumptionKinds.PERCENT
    return AssumptionKinds.NUMBER


def _refuse_non_fraction(field_name: str, value: Any, kind: AssumptionKinds) -> None:
    """Refuse a percentage field whose value is not a fraction of one.

    The engine reads `interest_rate: 5` as a 500 % rate, so printing it as `5.00%` would state an assumption the run
    was not evaluated under.

    Raises:
        CostDataError: If `value` is a number outside [-1, 1] on a percentage field.
    """
    if kind is not AssumptionKinds.PERCENT:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return
    if -1.0 <= float(value) <= 1.0:
        return
    raise CostDataError(
        f"Scenario field {field_name!r} is a fraction of one (it is printed as a percentage), but "
        f"the value {value!r} is not a fraction: the engine reads it as "
        f"{float(value):.2%}. State it as a fraction, or declare the field's kind in "
        "views.ScenarioValueKinds."
    )


def _central_case(
    field_name: str, base_result: LifecycleCostResult
) -> Tuple[CentralCase, Any]:
    """Return the central case's value of one dotted path, or the reason there is none.

    Walks the path a scenario override writes to, over the base cell's parameters, so it compares against what was
    priced, not the defaults. It separates a key absent from a dict (the priced rate may still be recorded on the
    result), a key present and set to None, and a path not on `EconomicParameters` at all.
    """
    parts = field_name.split(".")
    current: Any = base_result.parameters
    for index, part in enumerate(parts):
        if isinstance(current, dict):
            match = next(
                (value for key, value in current.items() if _key_name(key) == part), _ABSENT
            )
            if match is _ABSENT:
                rate = _recorded_rate(parts, base_result)
                return (CentralCase.VALUE, rate) if rate is not None else (CentralCase.NOT_RECORDED, None)
            current = match
            continue
        if not hasattr(current, part):
            return (CentralCase.NOT_RECORDED, None)
        current = getattr(current, part)
        if current is None:
            if index == len(parts) - 1 and part in ScenarioValueKinds.RESOLVED_FROM_GENERAL:
                return (
                    CentralCase.RESOLVED_FROM_GENERAL,
                    base_result.parameters.general_price_escalation_rate,
                )
            return (CentralCase.NOT_RECORDED, None)
    return (CentralCase.VALUE, current)


def _recorded_rate(parts: List[str], base_result: LifecycleCostResult) -> Optional[float]:
    """Return the escalation rate the run resolved for a dict path the parameters do not state.

    Example: an axis on `energy_price_escalation_rates.ELECTRICITY` in a run without an explicit electricity rate
    compares against the country-default rate the run priced with, which the result records per carrier.
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
    """Return the name a dotted scenario path uses for a dict key: an enum's `name`, else its text."""
    return getattr(key, "name", str(key))


# ============================================== why an axis that moved nothing was inert


class ZeroSwingCauses:
    """What each scenario axis prices, so an axis with zero effect can name its cause.

    A zero swing means the axis moved a parameter nothing in this run's timeline depends on. This holds the two field
    paths whose zero case can be diagnosed from a stored result, and the text used when no cause can be named.
    """

    #: The CO2-price scenario axis; inert when no carrier books a carbon-price flow.
    CO2_PRICE_FIELD = "co2_price_scenario"
    #: The per-carrier energy escalation axis; inert when that carrier is not billed at all.
    ENERGY_ESCALATION_STEM = "energy_price_escalation_rates"
    #: Said when the booked flows explain nothing. A refusal, not a cause: one perspective's
    #: timeline cannot support a claim about every flow in the run.
    UNKNOWN = "no cause could be derived from the booked flows of this run"


def zero_swing_notes(
    scenario_cube: Optional[ScenarioSetView],
    base_result: LifecycleCostResult,
    swings: Mapping[str, float],
) -> Dict[str, str]:
    """Return, per scenario with an exactly-zero swing, why that axis had no effect here.

    The cause is derived from the base cell's scoped timeline (which flows the perspective books), not from a table of
    axes, so a run that does price carbon gets no note and an axis that cannot be diagnosed says so. Only exact zeros
    qualify; the table prints small swings to three significant digits.

    Args:
        scenario_cube: The evaluated cube, or None.
        base_result: The base cell's result for the reference perspective.
        swings: Per-scenario swing of the headline KPI, base included.

    Returns:
        `{scenario id: cause}` for the zero-swing rows; empty when there are none.
    """
    if scenario_cube is None:
        return {}
    entries = base_result.scoped_timeline().entries
    # Both causes are questions about the same two observations, so they are made once for the
    # run rather than re-derived per row: which carriers this perspective is billed for, and
    # whether any carbon-price flow was booked at all.
    billed = _billed_carriers(base_result)
    co2_priced = any(entry.category == CostCategory.ENERGY_CO2_PRICE for entry in entries)
    notes: Dict[str, str] = {}
    for scenario in scenario_cube.scenarios:
        if scenario.id == scenario_cube.base_id or swings.get(scenario.id) != 0.0:
            continue
        fields = list(scenario.parameter_overrides) + list(scenario.data_overlays)
        causes = [
            cause
            for cause in (_inert_axis_cause(name, billed, co2_priced) for name in fields)
            if cause
        ]
        notes[scenario.id] = "; ".join(causes) if causes else ZeroSwingCauses.UNKNOWN
    return notes


def _billed_carriers(result: LifecycleCostResult) -> Tuple[str, ...]:
    """Return the carriers this perspective books a bill entry for, upper-cased and sorted.

    Both zero-swing causes use it: a carbon-price axis names the billed carriers without a CO2-price entry, and an
    escalation axis checks whether its carrier is billed at all.
    """
    return tuple(
        sorted(
            {
                entry.subject.upper()
                for entry in result.scoped_timeline().entries
                if entry.subject_kind == SubjectKind.CARRIER
                and entry.category in ViewCategories.BILL_CATEGORIES
            }
        )
    )


def _inert_axis_cause(field_name: str, billed: Tuple[str, ...], co2_priced: bool) -> str:
    """Return the observed cause for one overridden field, or "" when it cannot be named."""
    if field_name == ZeroSwingCauses.CO2_PRICE_FIELD:
        return _co2_axis_cause(billed, co2_priced)
    if field_name.split(".")[0] == ZeroSwingCauses.ENERGY_ESCALATION_STEM and "." in field_name:
        carrier = field_name.split(".", 1)[1]
        if carrier.upper() not in billed:
            return f"the run books no {carrier.lower()} bill for the rate to escalate"
    return ""


def _co2_axis_cause(billed: Tuple[str, ...], co2_priced: bool) -> str:
    """Return why a CO2-price axis has no effect: no carbon-price flow is booked, beside which bills.

    States only the observation. The engine books `ENERGY_CO2_PRICE` only for carriers with `co2_price_exposure > 0`
    (§3.5), but a stored result has no price entry to confirm that, so the note does not name it.
    """
    if co2_priced or not billed:
        return ""
    names = " and ".join(carrier.lower() for carrier in billed)
    return f"the stored timeline books no CO2-price entry for any carrier, {names} included"
