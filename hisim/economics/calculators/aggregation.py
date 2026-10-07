"""Discounting and aggregation: from the finished timeline to the KPIs (cost_spec.md §2.3, §3.4, §3.7, §4.3, §7.4).

It filters, discounts and pivots the allocated timeline and never creates cash flows: scoping to one actor's flows, NPV
and EAC (NPV times the annuity factor), pivots by category, component and payer, the nominal liquidity view and year-1
monthly figure, the cost per kWh of heat, and the per-subject breakdowns. The discounting arithmetic lives on
`CashFlowTimeline`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Dict, List, Optional

from hisim.economics.calculators.annualization import annualize
from hisim.economics.calculators.categories import EngineCategoryRules
from hisim.economics.calculators.subsidy_application import nominal_support_from_entries
from hisim.economics.carriers import validate_energy_attribution
from hisim.economics.facts import BillingDeterminants, ComponentCostFacts
from hisim.economics.parameters import EconomicParameters
from hisim.economics.perspectives import ActorScope
from hisim.economics.results import AnnualEnergyQuantities, ComponentCostBreakdown, LifecycleCo2Result
from hisim.economics.timeline import Actor, CashFlowTimeline, CostCategory, SubjectKind
from hisim.economics.uncertainty import UncertainValue


@dataclass
class TimelineAggregation:
    """The KPIs derived from one perspective's timeline, copied onto `LifecycleCostResult` (§3.7).

    Units: `total_npv_in_euro` and every `npv_by_*` value are euro bands discounted to year 0, cost-positive (a
    negative NPV means the variant earns money). `equivalent_annual_cost_in_euro` is the NPV times the VDI 2067-1
    annuity factor, in euro per year; `monthly_equivalent_cost_in_euro` is that over twelve.
    `annual_cost_series_nominal_in_euro` is undiscounted nominal euro per year 0..T (the liquidity view, §4.3) and
    `monthly_cost_year1_in_euro` its year-1 element over twelve. All are scoped to `scope_payer` except `npv_by_payer`,
    which covers every payer for the §6.5 zero-sum check.
    """

    #: Months per year: the one divisor behind every monthly figure the engine publishes — the
    #: "monthly cost" display figures of §4.3, the monthly burden view and the warm-rent change.
    #: The engine has no intra-year resolution, so this is a unit conversion of an annual figure
    #: and never a statement about seasonal profiles.
    MONTHS_PER_YEAR: ClassVar[float] = 12.0

    scope_payer: Actor
    total_npv_in_euro: UncertainValue
    equivalent_annual_cost_in_euro: UncertainValue
    monthly_equivalent_cost_in_euro: UncertainValue
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
    """Return the annualized energy volumes per carrier, for the result object (§3.6 rule 5).

    The plausibility panel and the report divide euros by these kWh to show effective prices, which exposes unit
    mix-ups. Volumes of the same carrier billed by several meters add up, as every other reading of those records does.
    The division uses the `guard_zero` divisor of `calculators/annualization.py`.

    Args:
        billing: The run's billing determinants per carrier over the simulated period.
        simulated_period_fraction: Simulated share of a year, dimensionless.

    Returns:
        Volumes in kWh per year keyed by `EnergyCarrier.value` (the carrier's timeline subject name); the same for
            every
        perspective.
    """
    quantities: Dict[str, AnnualEnergyQuantities] = {}
    for determinants in billing:
        carried = quantities.get(determinants.carrier.value)
        quantities[determinants.carrier.value] = AnnualEnergyQuantities(
            bought_in_kwh=(carried.bought_in_kwh if carried is not None else 0.0)
            + annualize(
                determinants.energy_bought_in_kwh, simulated_period_fraction, guard_zero=True
            ),
            sold_in_kwh=(carried.sold_in_kwh if carried is not None else 0.0)
            + annualize(
                determinants.energy_sold_in_kwh, simulated_period_fraction, guard_zero=True
            ),
        )
    return quantities


def annual_energy_attribution(
    attribution: Dict[str, Dict[str, float]], simulated_period_fraction: float
) -> Dict[str, Dict[str, float]]:
    """Return the per-subject energy attribution in kWh per year, annualized like the carrier totals.

    The household energy balance compares device columns against the metered carrier quantities, so both must use the
    same divisor.

    Args:
        attribution: Subject -> energy-balance role -> kWh over the simulated period, from `EvaluationInputs`.
        simulated_period_fraction: Simulated share of a year, dimensionless.

    Returns:
        The same shape in kWh per year; an empty map when the run has no per-component attribution.

    Raises:
        ValueError: If the map carries a negative quantity.
    """
    validate_energy_attribution(
        attribution, "annual_energy_attribution(EvaluationInputs.energy_attribution_by_subject_in_kwh)"
    )
    return {
        subject: {
            role: annualize(value, simulated_period_fraction, guard_zero=True)
            for role, value in by_role.items()
        }
        for subject, by_role in attribution.items()
    }


def build_breakdowns(
    scoped: CashFlowTimeline,
    facts_by_subject: Dict[str, ComponentCostFacts],
    co2_result: LifecycleCo2Result,
    interest: float,
    annuity: float,
    horizon: int,
) -> Dict[str, ComponentCostBreakdown]:
    """Return one `ComponentCostBreakdown` per subject, pivoted from the scoped timeline (§7.4 rule 1).

    Every breakdown filters the same timeline, so the per-subject NPVs sum exactly to the perspective's total and
    stacked bars add up. `investment_gross_in_euro` is the year-0 cost before support; `subsidies_nominal_in_euro` sums
    the subject's SUBSIDY entries undiscounted and `subsidies_npv_in_euro` discounts them. Carriers are subjects too: a
    carrier gets operational CO2, a component only embodied CO2.

    Args:
        scoped: The timeline filtered to the perspective's payer scope.
        facts_by_subject: Declared cost facts per component subject, for the asset class and KPI tag; carriers get
            None.
        co2_result: Finished CO2 accounting, for each subject's lifecycle mass in kg.
        interest: Nominal discount rate as a fraction.
        annuity: The VDI 2067-1 annuity factor for this horizon and rate, in 1/a.
        horizon: Observation period T in years; the nominal annual series has T + 1 entries.

    Returns:
        The breakdowns keyed by subject name, in the timeline's first-appearance order so charts and CSV rows are
            stable.
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
    """Derive the perspective's KPIs from the allocated timeline (§3.7).

    The last step of an evaluation and the only one that discounts. `npv_by_payer` is taken on the full timeline, so
    both sides of the landlord/tenant split are visible; everything else on the timeline scoped with
    `CashFlowTimeline.scoped_to`, the same scoping `explain` uses. The cost per kWh of heat is `NPV * annuity / annual
    heat`, an annual cost per annual kWh.

    Args:
        timeline: The finished, payer-allocated timeline.
        actor_scope: Whose flows the perspective reports; SYSTEM means all.
        facts_by_subject: Declared cost facts per component subject, passed to :func:`build_breakdowns`.
        co2_result: Finished CO2 accounting, passed to :func:`build_breakdowns`.
        parameters: Economic parameters; supply the interest rate, the horizon and the annuity factor.
        annual_heat_demand_in_kwh: Annual useful heat for the per-kWh figure; for a staged plan the equivalent annual
            heat of its horizon (`StagedEvaluator._equivalent_annual_heat`). None or zero suppresses the figure.

    Returns:
        A `TimelineAggregation`.
    """
    interest = parameters.interest_rate
    horizon = parameters.observation_period_in_years
    npv_by_payer = dict(timeline.npv_by(interest, lambda entry: entry.payer))

    # The perspective reports the scope actor's flows (SYSTEM = everything). One definition of
    # scoping, on the timeline itself, so `explain` cannot disagree with the KPI.
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

    equivalent_annual_cost = total_npv.scale(annuity)

    levelized = None
    if annual_heat_demand_in_kwh:
        levelized = total_npv.scale(annuity / annual_heat_demand_in_kwh)

    return TimelineAggregation(
        scope_payer=scope_actor,
        total_npv_in_euro=total_npv,
        equivalent_annual_cost_in_euro=equivalent_annual_cost,
        monthly_equivalent_cost_in_euro=equivalent_annual_cost.scale(1.0 / TimelineAggregation.MONTHS_PER_YEAR),
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
