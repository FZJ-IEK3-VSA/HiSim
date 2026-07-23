"""Typed result objects of the lifecycle cost engine (cost_spec.md §3.7, §3.8, §7).

CSV/JSON are export formats, never an internal API.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from hisim.economics.parameters import EconomicParameters
from hisim.economics.provenance import (
    ProvenanceLedger,
    ProvenanceReport,
    ProvenanceReportEntry,
    ResolvedSource,
)
from hisim.economics.subsidies import SubsidyDecision
from hisim.economics.timeline import Actor, CashFlowEntry, CashFlowTimeline, CostCategory, SubjectKind
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import ComponentType
from hisim.postprocessing.kpi_computation.kpi_structure import KpiTagEnumClass


@dataclass
class ComponentCostBreakdown:
    """Per-subject cost breakdown: a pure pivot of the canonical timeline (§3.7, §7.4)."""

    subject: str  # component name, or carrier for energy subjects
    subject_kind: SubjectKind
    asset_class: Optional[ComponentType]
    kpi_tag: Optional[KpiTagEnumClass]
    npv_by_category: Dict[CostCategory, UncertainValue]
    total_npv_in_euro: UncertainValue
    equivalent_annual_cost_in_euro: UncertainValue
    # Undiscounted display figures for "what does X cost to buy" views:
    investment_gross_in_euro: UncertainValue
    subsidies_in_euro: UncertainValue  # total support received for this subject
    annual_cost_series_nominal_in_euro: List[UncertainValue]
    lifecycle_co2_in_kg: float

    def to_json(self) -> dict:
        """Serialization for component_costs.json (§7.4)."""
        return {
            "subject": self.subject,
            "subject_kind": self.subject_kind.value,
            "asset_class": self.asset_class.value if self.asset_class else None,
            "kpi_tag": self.kpi_tag.value if self.kpi_tag else None,
            "npv_by_category": {category.value: value.to_json() for category, value in self.npv_by_category.items()},
            "total_npv_in_euro": self.total_npv_in_euro.to_json(),
            "equivalent_annual_cost_in_euro": self.equivalent_annual_cost_in_euro.to_json(),
            "investment_gross_in_euro": self.investment_gross_in_euro.to_json(),
            "subsidies_in_euro": self.subsidies_in_euro.to_json(),
            "annual_cost_series_nominal_in_euro": [value.to_json() for value in self.annual_cost_series_nominal_in_euro],
            "lifecycle_co2_in_kg": self.lifecycle_co2_in_kg,
        }


@dataclass
class LifecycleCo2Result:
    """Parallel, undiscounted CO2 accounting (§3.8).

    The CO2 *damage cost* (macroeconomic) and the CO2 *price* (a real cash flow) are distinct
    and must never be added together.
    """

    embodied_co2_in_kg: float = 0.0  # install + replacements, no discounting
    operational_co2_by_year_in_kg: List[float] = field(default_factory=list)  # index = year 1..T
    operational_co2_by_carrier_in_kg: Dict[str, float] = field(default_factory=dict)
    total_co2_in_kg: float = 0.0
    embodied_by_subject_in_kg: Dict[str, float] = field(default_factory=dict)

    def to_json(self) -> dict:
        """Serialization."""
        return {
            "embodied_co2_in_kg": self.embodied_co2_in_kg,
            "operational_co2_by_year_in_kg": self.operational_co2_by_year_in_kg,
            "operational_co2_by_carrier_in_kg": self.operational_co2_by_carrier_in_kg,
            "total_co2_in_kg": self.total_co2_in_kg,
            "embodied_by_subject_in_kg": self.embodied_by_subject_in_kg,
        }


@dataclass
class LifecycleCostResult:
    """The evaluation of one variant under one perspective (§3.7)."""

    perspective_id: str
    parameters: EconomicParameters
    total_npv_in_euro: UncertainValue  # net present cost over the horizon
    equivalent_annual_cost_in_euro: UncertainValue  # NPV x annuity factor — the headline KPI
    npv_by_category: Dict[CostCategory, UncertainValue]
    npv_by_component: Dict[str, UncertainValue]
    npv_by_payer: Dict[Actor, UncertainValue]
    component_breakdowns: Dict[str, ComponentCostBreakdown]
    annual_cost_series_nominal_in_euro: List[UncertainValue]  # liquidity view, year 0..T
    monthly_cost_year1_in_euro: Optional[UncertainValue]
    levelized_cost_of_heat_in_euro_per_kwh: Optional[UncertainValue]
    timeline: CashFlowTimeline
    lifecycle_co2_result: LifecycleCo2Result
    subsidy_decisions: List[SubsidyDecision] = field(default_factory=list)
    # Written-off residual book value of replaced assets, reported but excluded from
    # decision KPIs (§4.1):
    sunk_cost_written_off_in_euro: UncertainValue = field(default_factory=lambda: UncertainValue.exact(0.0))
    ledger: Optional[ProvenanceLedger] = None
    source_resolver: Optional[Dict[str, ResolvedSource]] = None
    # The payer this perspective reports on (§6). `timeline` always holds the FULL allocated
    # timeline (all payers, for the zero-sum invariant and payer pivots); consumers that
    # present "this perspective's flows" must filter by this payer — see `scoped_timeline()`.
    scope_payer: Actor = Actor.SYSTEM

    def scoped_timeline(self) -> CashFlowTimeline:
        """The flows this perspective actually reports on (filtered by `scope_payer`)."""
        if self.scope_payer == Actor.SYSTEM:
            return self.timeline
        return self.timeline.filtered(lambda entry: entry.payer == self.scope_payer)

    # ------------------------------------------------------------------ provenance (§3.10)

    def explain(self, value_path: str) -> ProvenanceReport:
        """Lineage of any result value: a filter of timeline entries plus their parameters.

        Accepted paths: ``total_npv_in_euro``, ``equivalent_annual_cost_in_euro``,
        ``npv_by_category[CATEGORY]``, ``npv_by_component[subject]``, ``npv_by_payer[actor]``.
        """
        entries = self._entries_for_path(value_path)
        value = self._value_for_path(value_path)
        report = ProvenanceReport(value_path=f"{self.perspective_id}/{value_path}", value=value)
        source_ids: List[str] = []
        for entry in entries:
            parameters = [self.ledger.get(record_id) for record_id in entry.provenance_ids] if self.ledger else []
            report.entries.append(
                ProvenanceReportEntry(
                    year=entry.year,
                    category=entry.category.value,
                    subject=entry.subject,
                    amount=entry.amount_in_euro,
                    parameters=parameters,
                )
            )
            for parameter in parameters:
                source_ids.extend(parameter.source_ids)
        if self.source_resolver:
            seen = set()
            for source_id in source_ids:
                if source_id in seen:
                    continue
                seen.add(source_id)
                if source_id.startswith("inline:"):
                    report.sources.append(
                        ResolvedSource(
                            source_id=source_id,
                            citation=source_id[len("inline:"):],
                            url=None,
                            publication_year=None,
                            retrieved=None,
                            kind="INLINE",
                        )
                    )
                elif source_id in self.source_resolver:
                    report.sources.append(self.source_resolver[source_id])
        return report

    def _entries_for_path(self, value_path: str) -> List[CashFlowEntry]:
        bracket = re.match(r"(\w+)\[(.+)\]$", value_path)
        if bracket:
            container, key = bracket.group(1), bracket.group(2)
            if container == "npv_by_category":
                category = CostCategory(key)
                return [entry for entry in self.timeline.entries if entry.category == category]
            if container == "npv_by_component":
                return [entry for entry in self.timeline.entries if entry.subject == key]
            if container == "npv_by_payer":
                actor = Actor(key)
                return [entry for entry in self.timeline.entries if entry.payer == actor]
            raise KeyError(f"Unknown result container {container!r} in {value_path!r}.")
        if value_path in ("total_npv_in_euro", "equivalent_annual_cost_in_euro", "monthly_cost_year1_in_euro"):
            return list(self.timeline.entries)
        raise KeyError(f"Unknown result value path {value_path!r}.")

    def _value_for_path(self, value_path: str) -> Optional[UncertainValue]:
        bracket = re.match(r"(\w+)\[(.+)\]$", value_path)
        if bracket:
            container, key = bracket.group(1), bracket.group(2)
            if container == "npv_by_category":
                return self.npv_by_category.get(CostCategory(key))
            if container == "npv_by_component":
                return self.npv_by_component.get(key)
            if container == "npv_by_payer":
                return self.npv_by_payer.get(Actor(key))
        attribute = getattr(self, value_path, None)
        return attribute if isinstance(attribute, UncertainValue) else None

    def to_json(self) -> dict:
        """Serialization for lifecycle_costs.json (without the ledger — stored separately)."""
        return {
            "perspective": self.perspective_id,
            "parameters": self.parameters.to_dict(),
            "total_npv_in_euro": self.total_npv_in_euro.to_json(),
            "equivalent_annual_cost_in_euro": self.equivalent_annual_cost_in_euro.to_json(),
            "npv_by_category": {category.value: value.to_json() for category, value in self.npv_by_category.items()},
            "npv_by_component": {subject: value.to_json() for subject, value in self.npv_by_component.items()},
            "npv_by_payer": {actor.value: value.to_json() for actor, value in self.npv_by_payer.items()},
            "annual_cost_series_nominal_in_euro": [value.to_json() for value in self.annual_cost_series_nominal_in_euro],
            "monthly_cost_year1_in_euro": self.monthly_cost_year1_in_euro.to_json()
            if self.monthly_cost_year1_in_euro
            else None,
            "levelized_cost_of_heat_in_euro_per_kwh": self.levelized_cost_of_heat_in_euro_per_kwh.to_json()
            if self.levelized_cost_of_heat_in_euro_per_kwh
            else None,
            "sunk_cost_written_off_in_euro": self.sunk_cost_written_off_in_euro.to_json(),
            "lifecycle_co2": self.lifecycle_co2_result.to_json(),
            "subsidy_decisions": [decision.to_json() for decision in self.subsidy_decisions],
            "component_breakdowns": {
                subject: breakdown.to_json() for subject, breakdown in self.component_breakdowns.items()
            },
        }


@dataclass
class EvaluationMatrix:
    """{perspective -> LifecycleCostResult} for one variant (§3.1)."""

    results: Dict[str, LifecycleCostResult] = field(default_factory=dict)

    def to_json(self) -> dict:
        """Serialization for lifecycle_costs.json."""
        return {perspective: result.to_json() for perspective, result in self.results.items()}


@dataclass
class VariantComparison:
    """Differential analysis of two variants (§3.7): the RenoVisor base-vs-measures case."""

    reference_id: str
    variant_id: str
    perspective_id: str
    npv_delta_in_euro: UncertainValue  # variant - reference, slot-wise
    equivalent_annual_cost_delta_in_euro: UncertainValue
    npv_delta_by_subject: Dict[str, UncertainValue]
    # Discounted payback per slot; each independently None-able ("never within horizon"):
    discounted_payback_years: Dict[str, Optional[int]] = field(default_factory=dict)
    warm_rent_change_per_month_in_euro: Optional[UncertainValue] = None
    warm_rent_neutral_per_slot: Dict[str, bool] = field(default_factory=dict)

    def to_json(self) -> dict:
        """Serialization."""
        return {
            "reference": self.reference_id,
            "variant": self.variant_id,
            "perspective": self.perspective_id,
            "npv_delta_in_euro": self.npv_delta_in_euro.to_json(),
            "equivalent_annual_cost_delta_in_euro": self.equivalent_annual_cost_delta_in_euro.to_json(),
            "npv_delta_by_subject": {subject: value.to_json() for subject, value in self.npv_delta_by_subject.items()},
            "discounted_payback_years": self.discounted_payback_years,
            "warm_rent_change_per_month_in_euro": self.warm_rent_change_per_month_in_euro.to_json()
            if self.warm_rent_change_per_month_in_euro
            else None,
            "warm_rent_neutral_per_slot": self.warm_rent_neutral_per_slot,
        }


def _subject_alignment_key(result: LifecycleCostResult, subject: str) -> str:
    """Aligns subjects across variants by (asset_class, subject) (§3.7)."""
    breakdown = result.component_breakdowns.get(subject)
    asset_class = breakdown.asset_class.value if breakdown and breakdown.asset_class else ""
    return f"{asset_class}|{subject}"


def compare(
    reference: LifecycleCostResult,
    variant: LifecycleCostResult,
    reference_id: str = "reference",
    variant_id: str = "variant",
) -> VariantComparison:
    """Differential NPV, differential annuity, discounted payback, warm-rent change (§3.7, §6.5).

    All deltas are slot-wise: reference and variant are compared within the same
    LOW/AVERAGE/HIGH world, so shared cost uncertainty cancels.
    """
    npv_delta = variant.total_npv_in_euro - reference.total_npv_in_euro
    eac_delta = variant.equivalent_annual_cost_in_euro - reference.equivalent_annual_cost_in_euro

    # Subject alignment with explicit zeros for one-sided subjects (§3.7).
    keys = {}
    for result in (reference, variant):
        for subject in result.npv_by_component:
            keys[_subject_alignment_key(result, subject)] = subject
    npv_delta_by_subject = {}
    zero = UncertainValue.exact(0.0)
    for _key, subject in sorted(keys.items()):
        reference_value = reference.npv_by_component.get(subject, zero)
        variant_value = variant.npv_by_component.get(subject, zero)
        npv_delta_by_subject[subject] = variant_value - reference_value

    # Discounted payback per slot: first year where cumulative discounted savings exceed the
    # differential investment (§3.7). Uses the full annual cash-flow difference.
    interest = variant.parameters.interest_rate
    horizon = variant.parameters.observation_period_in_years
    reference_series = reference.annual_cost_series_nominal_in_euro
    variant_series = variant.annual_cost_series_nominal_in_euro
    payback: Dict[str, Optional[int]] = {}
    for slot_name, getter in (
        ("low", lambda value: value.minimum),
        ("average", lambda value: value.average),
        ("high", lambda value: value.maximum),
    ):
        cumulative = 0.0
        result_year: Optional[int] = None
        for year in range(0, horizon + 1):
            reference_amount = getter(reference_series[year]) if year < len(reference_series) else 0.0
            variant_amount = getter(variant_series[year]) if year < len(variant_series) else 0.0
            cumulative += (reference_amount - variant_amount) / ((1.0 + interest) ** year)
            if year > 0 and cumulative >= 0 and result_year is None:
                result_year = year
        payback[slot_name] = result_year

    comparison = VariantComparison(
        reference_id=reference_id,
        variant_id=variant_id,
        perspective_id=variant.perspective_id,
        npv_delta_in_euro=npv_delta,
        equivalent_annual_cost_delta_in_euro=eac_delta,
        npv_delta_by_subject=npv_delta_by_subject,
        discounted_payback_years=payback,
    )

    # Warm-rent neutrality (§6.5): only meaningful for tenant-scope results.
    tenant_reference = reference.npv_by_payer.get(Actor.TENANT)
    tenant_variant = variant.npv_by_payer.get(Actor.TENANT)
    if tenant_reference is not None and tenant_variant is not None:
        annuity_factor = variant.parameters.annuity_factor()
        delta_per_month = (tenant_variant - tenant_reference).scale(annuity_factor / 12.0)
        comparison.warm_rent_change_per_month_in_euro = delta_per_month
        comparison.warm_rent_neutral_per_slot = {
            "low": delta_per_month.minimum <= 0,
            "average": delta_per_month.average <= 0,
            "high": delta_per_month.maximum <= 0,
        }
    return comparison
