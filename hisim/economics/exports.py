"""Exports and lifecycle KPIs (cost_spec.md §7.2, §7.3, §7.4).

All monetary figures are exported as min/avg/max: triplet objects in JSON, *_min/*_avg/*_max
column groups in CSV. New KPIs are namespaced per perspective and written to
`lifecycle_kpis.json` during the parallel phase (legacy KPI files stay byte-identical;
cost_module_issues.md #6).
"""

from __future__ import annotations

import csv
import json
import os
from typing import Any, Dict, List, Optional

from hisim.economics.results import EvaluationMatrix, VariantComparison
from hisim.economics.timeline import Actor
from hisim.postprocessing.kpi_computation.kpi_structure import KpiEntry, KpiTagEnumClass

LIFECYCLE_COSTS_FILE_NAME = "lifecycle_costs.json"
COMPONENT_COSTS_JSON_FILE_NAME = "component_costs.json"
COMPONENT_COSTS_CSV_FILE_NAME = "component_costs.csv"
CASH_FLOW_TIMELINE_FILE_NAME = "cash_flow_timeline.csv"
LIFECYCLE_KPIS_FILE_NAME = "lifecycle_kpis.json"


def write_lifecycle_costs_json(matrix: EvaluationMatrix, result_directory: str) -> str:
    """`lifecycle_costs.json`: the full typed EvaluationMatrix incl. subsidy audit trails."""
    path = os.path.join(result_directory, LIFECYCLE_COSTS_FILE_NAME)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(matrix.to_json(), file, indent=2)
    return path


def write_component_costs(matrix: EvaluationMatrix, result_directory: str) -> List[str]:
    """`component_costs.json` / `.csv`: per-component breakdowns for the frontend (§7.4)."""
    json_path = os.path.join(result_directory, COMPONENT_COSTS_JSON_FILE_NAME)
    payload = {
        perspective: {subject: breakdown.to_json() for subject, breakdown in result.component_breakdowns.items()}
        for perspective, result in matrix.results.items()
    }
    with open(json_path, "w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2)

    csv_path = os.path.join(result_directory, COMPONENT_COSTS_CSV_FILE_NAME)
    with open(csv_path, "w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file, delimiter=";")
        writer.writerow(
            [
                "perspective",
                "subject",
                "subject_kind",
                "asset_class",
                "category",
                "npv_min",
                "npv_avg",
                "npv_max",
                "eac_min",
                "eac_avg",
                "eac_max",
                "year1_nominal_min",
                "year1_nominal_avg",
                "year1_nominal_max",
                "lifecycle_co2_kg",
            ]
        )
        for perspective, result in matrix.results.items():
            annuity = result.parameters.annuity_factor()
            for subject, breakdown in result.component_breakdowns.items():
                year1 = (
                    breakdown.annual_cost_series_nominal_in_euro[1]
                    if len(breakdown.annual_cost_series_nominal_in_euro) > 1
                    else None
                )
                for category, npv in breakdown.npv_by_category.items():
                    eac = npv.scale(annuity)
                    writer.writerow(
                        [
                            perspective,
                            subject,
                            breakdown.subject_kind.value,
                            breakdown.asset_class.value if breakdown.asset_class else "",
                            category.value,
                            npv.minimum,
                            npv.average,
                            npv.maximum,
                            eac.minimum,
                            eac.average,
                            eac.maximum,
                            year1.minimum if year1 else "",
                            year1.average if year1 else "",
                            year1.maximum if year1 else "",
                            breakdown.lifecycle_co2_in_kg,
                        ]
                    )
    return [json_path, csv_path]


def write_cash_flow_timeline(matrix: EvaluationMatrix, result_directory: str) -> str:
    """`cash_flow_timeline.csv` in long format with timeline-entry ids for offline explain."""
    path = os.path.join(result_directory, CASH_FLOW_TIMELINE_FILE_NAME)
    with open(path, "w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file, delimiter=";")
        writer.writerow(
            [
                "perspective",
                "entry_id",
                "year",
                "category",
                "subject",
                "payer",
                "subsidy_scheme_id",
                "nominal_min",
                "nominal_avg",
                "nominal_max",
                "discounted_min",
                "discounted_avg",
                "discounted_max",
                "provenance_ids",
            ]
        )
        for perspective, result in matrix.results.items():
            interest = result.parameters.interest_rate
            for entry_id, entry in enumerate(result.timeline.entries):
                factor = 1.0 / ((1.0 + interest) ** entry.year)
                discounted = entry.amount_in_euro.scale(factor)
                writer.writerow(
                    [
                        perspective,
                        entry_id,
                        entry.year,
                        entry.category.value,
                        entry.subject,
                        entry.payer.value,
                        entry.subsidy_scheme_id or "",
                        entry.amount_in_euro.minimum,
                        entry.amount_in_euro.average,
                        entry.amount_in_euro.maximum,
                        discounted.minimum,
                        discounted.average,
                        discounted.maximum,
                        " ".join(str(record_id) for record_id in entry.provenance_ids),
                    ]
                )
    return path


def write_provenance_ledger(matrix: EvaluationMatrix, result_directory: str) -> Optional[str]:
    """`cost_provenance.json` (§3.10): one ledger per perspective (they share most records)."""
    payload = {}
    for perspective, result in matrix.results.items():
        if result.ledger is not None:
            payload[perspective] = result.ledger.to_json()
    if not payload:
        return None
    path = os.path.join(result_directory, "cost_provenance.json")
    with open(path, "w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2)
    return path


# ---------------------------------------------------------------------- KPIs (§7.3)

def build_lifecycle_kpi_entries(
    matrix: EvaluationMatrix, comparison: Optional[VariantComparison] = None
) -> List[KpiEntry]:
    """The new namespaced KPI set; every monetary KPI carries its uncertainty band."""
    entries: List[KpiEntry] = []

    def add(name: str, unit: str, band, description: Optional[str] = None) -> None:
        if band is None:
            return
        entries.append(
            KpiEntry(
                name=name,
                unit=unit,
                value=band.average,
                value_min=band.minimum,
                value_max=band.maximum,
                tag=KpiTagEnumClass.COSTS,
                description=description,
            )
        )

    for perspective, result in matrix.results.items():
        horizon = result.parameters.observation_period_in_years
        add(f"Equivalent annual cost [EUR/a] ({perspective})", "EUR/a", result.equivalent_annual_cost_in_euro)
        add(
            f"Net present cost over {horizon} years [EUR] ({perspective})",
            "EUR",
            result.total_npv_in_euro,
        )
        add(f"Monthly cost year 1 [EUR/month] ({perspective})", "EUR/month", result.monthly_cost_year1_in_euro)
        add(
            f"Levelized cost of heat [EUR/kWh] ({perspective})",
            "EUR/kWh",
            result.levelized_cost_of_heat_in_euro_per_kwh,
        )
        subsidies_total = None
        for decision in result.subsidy_decisions:
            for award in decision.applied:
                if award.upfront_amount.maximum > 0:
                    subsidies_total = (
                        award.upfront_amount if subsidies_total is None else subsidies_total + award.upfront_amount
                    )
                    add(
                        f"Subsidy {award.scheme_id} [EUR] ({perspective})",
                        "EUR",
                        award.upfront_amount,
                        description=decision.measure_subject,
                    )
        if subsidies_total is not None:
            add(f"Total subsidies received [EUR] ({perspective})", "EUR", subsidies_total)
    if comparison is not None:
        add(
            f"Net present cost delta vs reference [EUR] ({comparison.perspective_id})",
            "EUR",
            comparison.npv_delta_in_euro,
        )
        payback = comparison.discounted_payback_years.get("average")
        entries.append(
            KpiEntry(
                name=f"Discounted payback vs reference [a] ({comparison.perspective_id})",
                unit="a",
                value=payback,
                tag=KpiTagEnumClass.COSTS,
                description=f"band: low={comparison.discounted_payback_years.get('low')}, "
                f"high={comparison.discounted_payback_years.get('high')}",
            )
        )
        if comparison.warm_rent_change_per_month_in_euro is not None:
            add(
                f"Warm rent change [EUR/month] ({comparison.perspective_id})",
                "EUR/month",
                comparison.warm_rent_change_per_month_in_euro,
            )
            entries.append(
                KpiEntry(
                    name=f"Warm-rent neutral ({comparison.perspective_id})",
                    unit="-",
                    value=str(comparison.warm_rent_neutral_per_slot.get("average", False)),
                    tag=KpiTagEnumClass.COSTS,
                    description=f"per slot: {comparison.warm_rent_neutral_per_slot}",
                )
            )
    return entries


def write_lifecycle_kpis(
    matrix: EvaluationMatrix,
    result_directory: str,
    comparison: Optional[VariantComparison] = None,
) -> str:
    """Writes `lifecycle_kpis.json` (separate from all_kpis.json during the parallel phase)."""
    entries = build_lifecycle_kpi_entries(matrix, comparison)
    path = os.path.join(result_directory, LIFECYCLE_KPIS_FILE_NAME)
    payload: Dict[str, Any] = {
        "Lifecycle costs": {entry.name: entry.to_dict() for entry in entries},
    }
    with open(path, "w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2)
    return path


def write_actor_kpis_into(matrix: EvaluationMatrix) -> List[KpiEntry]:
    """Actor-level KPI entries (§6.5) for perspectives with payer allocations."""
    entries: List[KpiEntry] = []
    for perspective, result in matrix.results.items():
        for actor in (Actor.LANDLORD, Actor.TENANT, Actor.OWNER_OCCUPIER):
            band = result.npv_by_payer.get(actor)
            if band is None:
                continue
            entries.append(
                KpiEntry(
                    name=f"Net present cost of {actor.value} [EUR] ({perspective})",
                    unit="EUR",
                    value=band.average,
                    value_min=band.minimum,
                    value_max=band.maximum,
                    tag=KpiTagEnumClass.COSTS,
                )
            )
    return entries
