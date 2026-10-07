"""Result files and lifecycle KPIs written from an evaluated matrix (cost_spec.md §7.2, §7.3, §7.4).

Turns an `EvaluationMatrix` into the machine-readable files a webtool, the RenoVisor uploader, a spreadsheet or an
archive reader consumes. Every monetary figure is min/best_estimate/max: triplet objects in JSON,
`*_min`/`*_best_estimate`/`*_max` columns in CSV. Nothing is derived here; numbers are read from the result or
`views.py`. KPI names carry their unit in brackets and the perspective id in parentheses, e.g. `"Equivalent annual cost
[EUR/a] (brownfield_net)"`, so all perspectives share one flat KPI namespace. These files sit beside the legacy
outputs, which stay unchanged until the cutover (§10).
"""

from __future__ import annotations

import csv
import json
import os
from typing import Any, Dict, List, Mapping, Optional

from hisim.economics import views
from hisim.economics.provenance import ProvenanceLedger
from hisim.economics.results import EvaluationMatrix, HeatCostNaming, VariantComparison
from hisim.economics.timeline import Actor, discount_factor
from hisim.postprocessing.kpi_computation.kpi_structure import KpiEntry, KpiTagEnumClass


class ExportFileNames:
    """Names of the result files the engine writes next to a simulation's results.

    None of them is read or written by legacy code (§10.0 rule 3). The input-side names live in
    `serialization.SerializationFileNames`.
    """

    LIFECYCLE_COSTS_FILE_NAME = "lifecycle_costs.json"
    COMPONENT_COSTS_JSON_FILE_NAME = "component_costs.json"
    COMPONENT_COSTS_CSV_FILE_NAME = "component_costs.csv"
    CASH_FLOW_TIMELINE_FILE_NAME = "cash_flow_timeline.csv"
    LIFECYCLE_KPIS_FILE_NAME = "lifecycle_kpis.json"
    #: The ledger file. The same name as `serialization.SerializationFileNames.PROVENANCE_FILE_NAME`,
    #: which reads it back; it is written by an ordinary run and by the staged evaluator alike.
    PROVENANCE_FILE_NAME = "cost_provenance.json"


def write_lifecycle_costs_json(matrix: EvaluationMatrix, result_directory: str) -> str:
    """Write `lifecycle_costs.json`, the full typed `EvaluationMatrix` including subsidy audit trails.

    One entry per perspective with headline KPIs, pivots, per-component breakdowns, the CO2 result and the subsidy
    decisions. With `cash_flow_timeline.csv` it is what `serialization.read_results` reads back to render a report
    without a cost database (§7.2).

    Returns:
        The path written.
    """
    path = os.path.join(result_directory, ExportFileNames.LIFECYCLE_COSTS_FILE_NAME)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(matrix.to_json(), file, indent=2)
    return path


def write_component_costs(matrix: EvaluationMatrix, result_directory: str) -> List[str]:
    """Write `component_costs.json` and `.csv`, the per-component breakdowns for the frontend (§7.4).

    The JSON is the `{perspective: {subject: ComponentCostBreakdown}}` map; the CSV has one row per (perspective,
    subject, subject_kind, asset_class, category) with NPV, equivalent annual cost and year-1 nominal cost as
    min/best_estimate/max column groups, plus lifecycle CO2. Subjects include energy carriers as well as components,
    and subject NPVs sum to the perspective total per slot.

    Returns:
        The two paths written, JSON first.
    """
    json_path = os.path.join(result_directory, ExportFileNames.COMPONENT_COSTS_JSON_FILE_NAME)
    payload = {
        perspective: {subject: breakdown.to_json() for subject, breakdown in result.component_breakdowns.items()}
        for perspective, result in matrix.results.items()
    }
    with open(json_path, "w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2)

    csv_path = os.path.join(result_directory, ExportFileNames.COMPONENT_COSTS_CSV_FILE_NAME)
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
                "npv_best_estimate",
                "npv_max",
                "eac_min",
                "eac_best_estimate",
                "eac_max",
                "year1_nominal_min",
                "year1_nominal_best_estimate",
                "year1_nominal_max",
                "lifecycle_co2_kg",
            ]
        )
        for perspective, result in matrix.results.items():
            # The annuitized figures come from the view-model, not from an annuity factor applied here.
            equivalent_annual_costs = views.subject_equivalent_annual_cost_by_category(result)
            for subject, breakdown in result.component_breakdowns.items():
                year1 = (
                    breakdown.annual_cost_series_nominal_in_euro[1]
                    if len(breakdown.annual_cost_series_nominal_in_euro) > 1
                    else None
                )
                for category, npv in breakdown.npv_by_category.items():
                    eac = equivalent_annual_costs[subject][category]
                    writer.writerow(
                        [
                            perspective,
                            subject,
                            breakdown.subject_kind.value,
                            breakdown.asset_class.value if breakdown.asset_class else "",
                            category.value,
                            npv.minimum,
                            npv.best_estimate,
                            npv.maximum,
                            eac.minimum,
                            eac.best_estimate,
                            eac.maximum,
                            year1.minimum if year1 else "",
                            year1.best_estimate if year1 else "",
                            year1.maximum if year1 else "",
                            breakdown.lifecycle_co2_in_kg,
                        ]
                    )
    return [json_path, csv_path]


def write_cash_flow_timeline(matrix: EvaluationMatrix, result_directory: str) -> str:
    """Write `cash_flow_timeline.csv`: one row per timeline entry, nominal and discounted, as min/best_estimate/max.

    Each row has year, category, subject, payer, optional subsidy scheme and `provenance_ids`, so any figure can be
    checked and explained from the archived directory alone (§3.10, §7.2). The stored timeline is the fully allocated
    one (all payers); a perspective's scope is restored from its `scope_payer`.
    `serialization.read_cash_flow_timelines` reads it back.

    Returns:
        The path written.
    """
    path = os.path.join(result_directory, ExportFileNames.CASH_FLOW_TIMELINE_FILE_NAME)
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
                "nominal_best_estimate",
                "nominal_max",
                "discounted_min",
                "discounted_best_estimate",
                "discounted_max",
                "provenance_ids",
                # Last column, so anything reading this file positionally is unaffected.
                "subject_kind",
            ]
        )
        for perspective, result in matrix.results.items():
            interest = result.parameters.interest_rate
            for entry_id, entry in enumerate(result.timeline.entries):
                discounted = entry.amount_in_euro.scale(discount_factor(interest, entry.year))
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
                        entry.amount_in_euro.best_estimate,
                        entry.amount_in_euro.maximum,
                        discounted.minimum,
                        discounted.best_estimate,
                        discounted.maximum,
                        " ".join(str(record_id) for record_id in entry.provenance_ids),
                        entry.subject_kind.value,
                    ]
                )
    return path


def write_provenance_ledger(matrix: EvaluationMatrix, result_directory: str) -> Optional[str]:
    """Write `cost_provenance.json` with one ledger per perspective (§3.10).

    The ledger records, for every parameter used, its origin (database entry, config override, scenario overlay, engine
    default, legacy shim), its value per slot and its source ids.

    Returns:
        The path written, or None when no perspective carried a ledger; no file is created then.
    """
    return write_provenance_ledgers(
        {
            perspective: result.ledger
            for perspective, result in matrix.results.items()
            if result.ledger is not None
        },
        result_directory,
    )


def write_provenance_ledgers(
    ledgers: Mapping[str, ProvenanceLedger], result_directory: str
) -> Optional[str]:
    """Write `cost_provenance.json` from ledgers keyed by perspective id.

    Used by ordinary runs (`write_provenance_ledger`) and by the staged evaluator's CLI, which writes its single ledger
    under the plan's perspective id, so both files read the same way (`serialization.read_results`,
    `ProvenanceLedger.from_json`).

    Args:
        ledgers: Perspective id -> the ledger that perspective's entry ids point into.
        result_directory: Where the file goes; it must exist.

    Returns:
        The path written, or None when there is no ledger; no file is created then.
    """
    if not ledgers:
        return None
    payload = {perspective: ledger.to_json() for perspective, ledger in ledgers.items()}
    path = os.path.join(result_directory, ExportFileNames.PROVENANCE_FILE_NAME)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2)
    return path


# ---------------------------------------------------------------------- KPIs (§7.3)

def build_lifecycle_kpi_entries(
    matrix: EvaluationMatrix, comparison: Optional[VariantComparison] = None
) -> List[KpiEntry]:
    """Build the namespaced lifecycle KPI list; every monetary KPI carries its band (§7.3).

    Per perspective: equivalent annual cost, net present cost over the horizon, year-1 monthly cost, system cost per
    unit of heat, one KPI per applied subsidy scheme and the total support. Then the per-actor net present costs of
    `actor_kpi_entries` and, with a comparison, the NPV delta, discounted payback and warm-rent figures. The HTML
    report's KPI table and `lifecycle_kpis.json` both use this list. A KPI whose band is None is omitted, not published
    as zero. The discounted payback (a range across the three worlds, in the description) and the warm-rent-neutral
    flag (a boolean string) are not bands.

    Args:
        matrix: The evaluated perspectives.
        comparison: Optional variant comparison against a reference run; adds the delta KPIs.

    Returns:
        The KPI entries, in perspective order.
    """
    entries: List[KpiEntry] = []

    def add(name: str, unit: str, band, description: Optional[str] = None) -> None:
        """Append one banded KPI, or nothing when the band is None (e.g. no heat demand, hence no heat cost).

        `value` is the best-estimate slot, `value_min`/`value_max` the envelope (§3.9).
        """
        if band is None:
            return
        entries.append(
            KpiEntry(
                name=name,
                unit=unit,
                value=band.best_estimate,
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
            f"{HeatCostNaming.FULL} [EUR/kWh] ({perspective})",
            "EUR/kWh",
            result.levelized_cost_of_heat_in_euro_per_kwh,
        )
        for decision in result.subsidy_decisions:
            for award in decision.applied:
                # The award's total (upfront + instalments): a tax-credit schedule has a zero upfront
                # amount. Awards with no euro amount (loan terms, an operating rate) stay out;
                # `describe_award` decides which is which.
                presentation = views.describe_award(award)
                if presentation.total_in_euro is not None and presentation.total_in_euro.maximum > 0:
                    # The KPI reads as the scheme's friendly name with the raw catalog id beside it,
                    # so two schemes with the same display name cannot collide and the key can be
                    # grepped back to the catalog. The id is also in the description.
                    detail = f"{decision.measure_subject}; scheme {presentation.scheme_id}"
                    add(
                        f"Subsidy {presentation.display_name} ({presentation.scheme_id}) [EUR] "
                        f"({perspective})",
                        "EUR",
                        presentation.total_in_euro,
                        description=(
                            f"{detail}; {presentation.payout_note}"
                            if presentation.payout_note else detail
                        ),
                    )
        # The total is read from the result and is the timeline-based nominal figure, so it is not
        # the sum of the per-scheme "Subsidy <id>" KPIs above wherever support reaches the timeline
        # without an upfront catalog award (operational support, scheduled payouts, a loan's
        # repayment grant).
        add(
            f"Total subsidies received [EUR] ({perspective})",
            "EUR",
            views.total_subsidies_received(result),
            description="nominal support on the perspective's timeline (cost-spec-v2 §8, D2)",
        )
    entries.extend(actor_kpi_entries(matrix))
    if comparison is not None:
        add(
            f"Net present cost delta vs reference [EUR] ({comparison.perspective_id})",
            "EUR",
            comparison.npv_delta_in_euro,
        )
        payback = comparison.discounted_payback_envelope
        entries.append(
            KpiEntry(
                name=f"Discounted payback vs reference [a] ({comparison.perspective_id})",
                unit="a",
                value=payback.central,
                tag=KpiTagEnumClass.COSTS,
                description=f"range across the three worlds: earliest={payback.earliest}, "
                f"latest={payback.latest} (None = never within the horizon)",
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
                    value=str(comparison.warm_rent_neutral_per_slot.get("best_estimate", False)),
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
    """Write `lifecycle_kpis.json`: the `build_lifecycle_kpi_entries` list under a "Lifecycle costs" group.

    Kept separate from `all_kpis.json` so the legacy KPIs stay byte-identical whether or not this engine runs (§10.0
    rule 3).

    Args:
        matrix: The evaluated perspectives.
        result_directory: Where to write, normally next to the simulation's other results.
        comparison: Optional variant comparison, which adds the delta, payback and warm-rent KPIs.

    Returns:
        The path written.
    """
    entries = build_lifecycle_kpi_entries(matrix, comparison)
    path = os.path.join(result_directory, ExportFileNames.LIFECYCLE_KPIS_FILE_NAME)
    payload: Dict[str, Any] = {
        "Lifecycle costs": {entry.name: entry.to_dict() for entry in entries},
    }
    with open(path, "w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2)
    return path


def actor_kpi_entries(matrix: EvaluationMatrix) -> List[KpiEntry]:
    """Return one "Net present cost of <actor> [EUR] (<perspective>)" KPI per allocated payer (§6.5).

    Read from `npv_by_payer` for each landlord, tenant or owner-occupier the allocation produced; an actor without
    allocation is skipped. Landlord, tenant and owner sum to the system NPV per slot, so they can be published side by
    side.
    """
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
                    value=band.best_estimate,
                    value_min=band.minimum,
                    value_max=band.maximum,
                    tag=KpiTagEnumClass.COSTS,
                )
            )
    return entries
