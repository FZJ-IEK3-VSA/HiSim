"""Discounting and aggregation: timeline -> KPIs (cost-spec-v2 §2.3).

The §2.3 "discounting/aggregation" calculator, and the only place downstream of allocation.
It takes the finished, already-allocated timeline and derives every headline figure by
filtering, discounting and pivoting it — nothing here re-derives cash flows:

* **scoping** — a perspective reports one actor's flows; SYSTEM means all of them. The payer
  pivot is deliberately taken on the *full* timeline first (all payers must be visible for the
  §6.5 zero-sum check), everything else on the scoped one;
* **NPV and EAC** — present value at the interest rate, times the annuity factor (§3.4);
* **pivots** by category, component and payer;
* **the liquidity view** — nominal euros per year 0..T, and the year-1 monthly figure (§4.3);
* **LCOH** — annualized total cost per kWh of annual heat demand;
* **per-subject breakdowns** — the §7.4 pivot, one `ComponentCostBreakdown` per subject.

The discounting arithmetic itself lives on `CashFlowTimeline` (`npv`, `npv_by`,
`nominal_annual_series`) and is not duplicated here.

Realizes: cost_spec.md §3.4 (discounting, annuity), §3.7 (evaluation), §4.3 (liquidity view),
§7.4 (component breakdowns).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Dict, List, Optional

from hisim.economics.calculators.annualization import annualize
from hisim.economics.calculators.categories import EngineCategoryRules
from hisim.economics.calculators.subsidy_application import nominal_support_from_entries
from hisim.economics.facts import BillingDeterminants, ComponentCostFacts
from hisim.economics.parameters import EconomicParameters
from hisim.economics.perspectives import ActorScope
from hisim.economics.results import AnnualEnergyQuantities, ComponentCostBreakdown, LifecycleCo2Result
from hisim.economics.timeline import Actor, CashFlowTimeline, CostCategory, SubjectKind
from hisim.economics.uncertainty import UncertainValue


@dataclass
class TimelineAggregation:
    """Everything `LifecycleCostResult` needs that is derived from the timeline (§3.7).

    A plain transport record between :func:`aggregate_timeline` and `evaluator.evaluate`, which
    copies its fields onto the published `LifecycleCostResult` one by one. It exists so that the
    aggregation calculator has a single typed return value instead of a tuple of ten, and so that
    "which KPIs are derived from the timeline" is answerable by reading one dataclass.

    Units and conventions, since the field names alone do not say: `total_npv_in_euro` and every
    `npv_by_*` value are euro bands **discounted to year 0** at the run's interest rate, cost
    positive (lower is better; a negative NPV means the variant nets money);
    `equivalent_annual_cost_in_euro` is that NPV times the VDI 2067-1 annuity factor, in euro per
    year; `annual_cost_series_nominal_in_euro` is **undiscounted** nominal euro indexed by year
    0..T (the liquidity view, §4.3), and `monthly_cost_year1_in_euro` is its year-1 element over
    twelve. All are scoped to `scope_payer` except `npv_by_payer`, which deliberately covers every
    payer so the §6.5 zero-sum check has both sides.
    """

    #: Months per year, for the "monthly cost" display figure of §4.3.
    MONTHS_PER_YEAR: ClassVar[float] = 12.0

    scope_payer: Actor
    total_npv_in_euro: UncertainValue
    equivalent_annual_cost_in_euro: UncertainValue
    npv_by_category: Dict[CostCategory, UncertainValue]
    npv_by_component: Dict[str, UncertainValue]
    npv_by_payer: Dict[Actor, UncertainValue]
    component_breakdowns: Dict[str, ComponentCostBreakdown]
    annual_cost_series_nominal_in_euro: List[UncertainValue]
    monthly_cost_year1_in_euro: Optional[UncertainValue]
    levelized_cost_of_heat_in_euro_per_kwh: Optional[UncertainValue]


def annual_energy_quantities(
    billing: List[BillingDeterminants], simulated_period_fraction: float
) -> Dict[str, AnnualEnergyQuantities]:
    """Per-carrier annualized volumes for the result object (W4.2, §3.6 rule 5).

    Keyed by `EnergyCarrier.value`, i.e. by the timeline subject name of the carrier, so a
    consumer can join a bill against the carrier's cash flows without a second mapping.

    Annualization uses the `guard_zero` divisor of `calculators/annualization.py`: the two
    presentation sites this replaces (`reporting.py:231` and `:1189`, both
    ``max(fraction, 1e-9)``) are guarded, and the engine's own energy calculator has already
    rejected non-positive fractions by the time a result exists — so the guard only ever
    differs for fractions in ``(0, 1e-9)``, which no simulation produces (W3.5).

    It exists so that the *physical* context of an evaluation travels with the result: the
    plausibility panel and the report's year-1 energy bill divide euros by these kWh to show
    effective prices — the fastest way to catch a unit mix-up — and before W4.2 they had to reach
    back into `EvaluationInputs` to do it.

    Args:
        billing: The run's billing determinants per carrier, over the *simulated period*.
        simulated_period_fraction: Simulated share of a year, dimensionless.

    Returns:
        Annualized volumes in kWh per year, keyed by `EnergyCarrier.value`. Purely physical: no
        prices, no discounting, no perspective scoping — the same figures for every perspective.
    """
    quantities: Dict[str, AnnualEnergyQuantities] = {}
    for determinants in billing:
        quantities[determinants.carrier.value] = AnnualEnergyQuantities(
            bought_in_kwh=annualize(
                determinants.energy_bought_in_kwh, simulated_period_fraction, guard_zero=True
            ),
            sold_in_kwh=annualize(
                determinants.energy_sold_in_kwh, simulated_period_fraction, guard_zero=True
            ),
        )
    return quantities


def build_breakdowns(
    scoped: CashFlowTimeline,
    facts_by_subject: Dict[str, ComponentCostFacts],
    co2_result: LifecycleCo2Result,
    interest: float,
    annuity: float,
    horizon: int,
) -> Dict[str, ComponentCostBreakdown]:
    """The per-subject pivot of the canonical timeline (§7.4 rule 1).

    Subjects keep first-appearance order. `investment_gross_in_euro` is the year-0 gross figure
    *before* support; the support is reported separately, in both units and with the unit in the
    field name (W3.4): `subsidies_nominal_in_euro` sums the subject's SUBSIDY entries nominally
    — the unit the §6.4 levy basis deducts — and `subsidies_npv_in_euro` discounts them, so it
    equals `npv_by_category[SUBSIDY]` mirrored to positive. See `calculators/categories.py` for
    why the gross and financing category sets differ.

    This is the pivot every per-component frontend view rests on: because each breakdown is built
    by filtering the *same* scoped timeline, the per-subject NPVs sum exactly to the perspective's
    total, which is what makes stacked bars add up without a reconciliation step. Carriers appear
    alongside components — a subject is whatever the timeline says it is — and a subject's
    `subject_kind` decides whether operational CO2 is attributed to it (carriers) or only embodied
    CO2 (components).

    Args:
        scoped: The timeline already filtered to the perspective's payer scope.
        facts_by_subject: The declared cost facts per component subject, for the asset class and
            KPI tag columns; carriers are absent from it and get `None`.
        co2_result: Finished CO2 accounting, for the per-subject lifecycle mass in kg.
        interest: Nominal discount rate as a fraction.
        annuity: The VDI 2067-1 annuity factor for this horizon and rate, in 1/a.
        horizon: Observation period T in years, for the nominal annual series (T + 1 entries).

    Returns:
        One `ComponentCostBreakdown` per subject, keyed by subject name, in the timeline's
        first-appearance order — kept so chart series and CSV rows are stable across runs.
    """
    breakdowns: Dict[str, ComponentCostBreakdown] = {}
    for subject in scoped.subjects():
        subject_timeline = scoped.filtered(lambda entry, _subject=subject: entry.subject == _subject)
        npv_by_category = dict(subject_timeline.npv_by(interest, lambda entry: entry.category))
        total = subject_timeline.npv(interest)
        facts = facts_by_subject.get(subject)
        kind = SubjectKind.COMPONENT
        for entry in subject_timeline.entries:
            kind = entry.subject_kind
            break
        investment_gross = UncertainValue.sum(
            entry.amount_in_euro
            for entry in subject_timeline.entries
            if entry.year == 0 and entry.category in EngineCategoryRules.BREAKDOWN_INVESTMENT_GROSS_CATEGORIES
        )
        subsidies_nominal = nominal_support_from_entries(subject_timeline.entries)
        subsidies_npv = npv_by_category.get(CostCategory.SUBSIDY, UncertainValue.exact(0.0)).as_revenue()
        operational_co2 = (
            co2_result.operational_co2_by_carrier_in_kg.get(subject, 0.0) if kind == SubjectKind.CARRIER else 0.0
        )
        breakdowns[subject] = ComponentCostBreakdown(
            subject=subject,
            subject_kind=kind,
            asset_class=facts.asset_class if facts else None,
            kpi_tag=facts.kpi_tag if facts else None,
            npv_by_category=npv_by_category,
            total_npv_in_euro=total,
            equivalent_annual_cost_in_euro=total.scale(annuity),
            investment_gross_in_euro=investment_gross,
            subsidies_nominal_in_euro=subsidies_nominal,
            subsidies_npv_in_euro=subsidies_npv,
            annual_cost_series_nominal_in_euro=subject_timeline.nominal_annual_series(horizon),
            lifecycle_co2_in_kg=co2_result.embodied_by_subject_in_kg.get(subject, 0.0) + operational_co2,
        )
    return breakdowns


def aggregate_timeline(
    timeline: CashFlowTimeline,
    actor_scope: ActorScope,
    facts_by_subject: Dict[str, ComponentCostFacts],
    co2_result: LifecycleCo2Result,
    parameters: EconomicParameters,
    annual_heat_demand_in_kwh: Optional[float],
) -> TimelineAggregation:
    """Derives the perspective's KPIs from the allocated timeline (§3.7).

    The last step of an evaluation and the only one that discounts: `evaluator.evaluate` calls it
    after the timeline is complete and the allocation ruleset has assigned payers, and copies the
    result straight onto `LifecycleCostResult`. Everything it returns is a filter, a discounting
    or a pivot of one canonical timeline — no cash flow is created or re-derived here — which is
    the §3.1 principle that makes the published figures reconcile with each other by construction
    rather than by agreement between separate code paths.

    Two ordering decisions are worth checking. `npv_by_payer` is taken on the **full** timeline
    before scoping, because it exists to show the other side of the landlord/tenant split and the
    §6.5 zero-sum check needs every payer; everything else is taken on the scoped timeline via
    `CashFlowTimeline.scoped_to`, the single definition of scoping that `explain` also uses (§7
    B4). And LCOH is `NPV x annuity / annual heat demand`, i.e. an equivalent *annual* cost per
    annual kWh — not an NPV per lifetime kWh.

    Args:
        timeline: The finished, payer-allocated timeline for this perspective.
        actor_scope: Whose flows the perspective reports; SYSTEM means all of them.
        facts_by_subject: Declared cost facts per component subject, passed through to
            :func:`build_breakdowns`.
        co2_result: Finished CO2 accounting, passed through to :func:`build_breakdowns`.
        parameters: Economic parameters — supplies the interest rate, the horizon and the annuity
            factor.
        annual_heat_demand_in_kwh: Annual useful heat demand for the LCOH figure. `None` or zero
            suppresses it, since a system that delivers no heat has no levelized cost of heat.

    Returns:
        A `TimelineAggregation`; see its docstring for the units of each field.
    """
    interest = parameters.interest_rate
    horizon = parameters.observation_period_in_years
    npv_by_payer = dict(timeline.npv_by(interest, lambda entry: entry.payer))

    # The perspective reports the scope actor's flows (SYSTEM = everything). One definition of
    # scoping, on the timeline itself, so `explain` cannot disagree with the KPI (§7 B4).
    scope_actor = actor_scope.to_actor()
    scoped = timeline.scoped_to(scope_actor)

    total_npv = scoped.npv(interest)
    annuity = parameters.annuity_factor()
    npv_by_category = dict(scoped.npv_by(interest, lambda entry: entry.category))
    npv_by_component = dict(scoped.npv_by(interest, lambda entry: entry.subject))
    annual_series = scoped.nominal_annual_series(horizon)
    monthly_year1 = (
        annual_series[1].scale(1.0 / TimelineAggregation.MONTHS_PER_YEAR) if len(annual_series) > 1 else None
    )

    levelized = None
    if annual_heat_demand_in_kwh:
        levelized = total_npv.scale(annuity / annual_heat_demand_in_kwh)

    return TimelineAggregation(
        scope_payer=scope_actor,
        total_npv_in_euro=total_npv,
        equivalent_annual_cost_in_euro=total_npv.scale(annuity),
        npv_by_category=npv_by_category,
        npv_by_component=npv_by_component,
        npv_by_payer=npv_by_payer,
        component_breakdowns=build_breakdowns(
            scoped, facts_by_subject, co2_result, interest, annuity, horizon
        ),
        annual_cost_series_nominal_in_euro=annual_series,
        monthly_cost_year1_in_euro=monthly_year1,
        levelized_cost_of_heat_in_euro_per_kwh=levelized,
    )
