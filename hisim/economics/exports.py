"""Exports and lifecycle KPIs (cost_spec.md §7.2, §7.3, §7.4).

All monetary figures are exported as min/avg/max: triplet objects in JSON, *_min/*_avg/*_max
column groups in CSV. New KPIs are namespaced per perspective and written to
`lifecycle_kpis.json` during the parallel phase (legacy KPI files stay byte-identical;
cost_module_issues.md #6).

**What this module owns**: turning an in-memory `EvaluationMatrix` into the machine-readable files
a webtool, the RenoVisor uploader, a spreadsheet or an archived-result reader consumes. It is
result *serialization*, not presentation — the human-facing renderings live in `reporting.py` and
`report_plots.py`, and the seam-4 import lint treats this module as one of their few permitted
imports precisely so the report's KPI table and `lifecycle_kpis.json` cannot disagree (they call
the same `build_lifecycle_kpi_entries`).

**What it deliberately does not own**: any derivation. Every number written here is read off the
result object or off `views.py`; an export never applies an annuity factor, never re-clamps a
subsidy, never sums what a view already totals (cost-spec-v2 W4.1 — "exports render, they do not
derive"). The one arithmetic left in the file is the discount factor applied per timeline row, and
that is a restatement of the entry's year, not a decision.

**The KPI naming scheme** (§7.3). New KPIs are namespaced by appending the perspective id in
parentheses to a name that already carries its unit in brackets, e.g.
``"Equivalent annual cost [EUR/a] (brownfield_net)"``, ``"Net present cost over 20 years [EUR]
(greenfield_gross)"``, ``"Subsidy DE_BEG_EM_HP_2024 [EUR] (brownfield_net)"``. The namespace is
what makes nine perspectives coexist in one flat KPI namespace without collision, and what lets a
consumer pick "the owner's monthly cost" rather than "a monthly cost". Every monetary KPI carries
its uncertainty band in the additive `KpiEntry.value_min` / `value_max` fields, with `value` itself
being the AVERAGE slot.

**These are NEW files only.** During the parallel phase the legacy KPI names, the legacy CSVs and
their values are untouched and remain the source of all published numbers; the lifecycle engine
writes `lifecycle_kpis.json` beside `all_kpis.json` rather than into it, and legacy KPIs stay plain
scalars. Both merge at the Phase-7 cutover (§10), after the parity evidence supports it — not
before.
"""

from __future__ import annotations

import csv
import json
import os
from typing import Any, Dict, List, Optional

from hisim.economics import views
from hisim.economics.results import EvaluationMatrix, VariantComparison
from hisim.economics.timeline import Actor, discount_factor
from hisim.postprocessing.kpi_computation.kpi_structure import KpiEntry, KpiTagEnumClass


class ExportFileNames:
    """Names of the result files the engine writes next to a simulation's results.

    All five are new files that no legacy code reads or writes (§10.0 rule 3), which is what keeps
    the parallel phase side-effect free. They are named constants because both the writers here and
    the readers in `serialization.py` and the CLI address them; the input-side names live in
    `serialization.SerializationFileNames`.
    """

    LIFECYCLE_COSTS_FILE_NAME = "lifecycle_costs.json"
    COMPONENT_COSTS_JSON_FILE_NAME = "component_costs.json"
    COMPONENT_COSTS_CSV_FILE_NAME = "component_costs.csv"
    CASH_FLOW_TIMELINE_FILE_NAME = "cash_flow_timeline.csv"
    LIFECYCLE_KPIS_FILE_NAME = "lifecycle_kpis.json"


def write_lifecycle_costs_json(matrix: EvaluationMatrix, result_directory: str) -> str:
    """`lifecycle_costs.json`: the full typed EvaluationMatrix incl. subsidy audit trails.

    The primary machine-readable result of a run and the richest of the exports: one entry per
    perspective with the headline KPIs, every pivot, the per-component breakdowns, the CO2 result
    and the §5 subsidy decisions, all monetary figures as min/avg/max triplets. It is what the
    webtool and the RenoVisor uploader consume, and — together with `cash_flow_timeline.csv` — what
    `serialization.read_results` reads back so a report can be rendered from an archived directory
    without a cost database (§7.2, W4.5).

    Returns:
        The path written.
    """
    path = os.path.join(result_directory, ExportFileNames.LIFECYCLE_COSTS_FILE_NAME)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(matrix.to_json(), file, indent=2)
    return path


def write_component_costs(matrix: EvaluationMatrix, result_directory: str) -> List[str]:
    """`component_costs.json` / `.csv`: per-component breakdowns for the frontend (§7.4).

    Answers "what does each component contribute to the total, in this perspective" — the data
    behind stacked bars per variant and component-level diffs between variants. The JSON is the
    typed `{perspective: {subject: ComponentCostBreakdown}}` map the webtool and the uploader
    consume; the CSV is the long format for ad-hoc analysis, one row per
    (perspective, subject, subject_kind, asset_class, category) with NPV, equivalent annual cost and
    year-1 nominal cost as min/avg/max column groups, plus lifecycle CO2.

    Two properties a reviewer should rely on: subjects cover components *and* energy carriers (the
    electricity bill is its own subject rather than smeared over consuming devices, §3.1), and the
    subject NPVs sum exactly to the perspective total per uncertainty slot — so a stacked chart
    always reconciles with the headline KPI.

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
            # The annuitized figures come from the view-model, not from an annuity factor
            # applied here (cost-spec-v2 W4.1: exports render, they do not derive).
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
    """`cash_flow_timeline.csv` in long format with timeline-entry ids for offline explain.

    The canonical timeline itself, unaggregated: one row per cash-flow entry with its year,
    category, subject, payer and optional subsidy scheme, in nominal *and* discounted form, each as
    a min/avg/max triple. Every published figure of a perspective is a filter or pivot of these
    rows, so this file is where a reviewer goes to check an aggregate by hand, and it is what
    `serialization.read_cash_flow_timelines` reads back to restore a full result.

    Each row carries `provenance_ids` — the ledger records behind the amount — which is what keeps
    an exported number explainable offline, from the archived directory alone, with no database and
    no rerun (§3.10, §7.2). The stored timeline is always the fully allocated one (all payers); a
    perspective's own scope is restored from its `scope_payer`.

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
                "nominal_avg",
                "nominal_max",
                "discounted_min",
                "discounted_avg",
                "discounted_max",
                "provenance_ids",
                # Appended by W4.5 so the stored timeline reloads faithfully; last column, so
                # anything reading this file positionally is unaffected.
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
                        entry.amount_in_euro.average,
                        entry.amount_in_euro.maximum,
                        discounted.minimum,
                        discounted.average,
                        discounted.maximum,
                        " ".join(str(record_id) for record_id in entry.provenance_ids),
                        entry.subject_kind.value,
                    ]
                )
    return path


def write_provenance_ledger(matrix: EvaluationMatrix, result_directory: str) -> Optional[str]:
    """`cost_provenance.json` (§3.10): one ledger per perspective (they share most records).

    The record of where every number came from: for each parameter the engine used, its origin
    (database entry, config override, scenario overlay, engine default, legacy shim), its value per
    slot and its source ids. Together with the `provenance_ids` on the timeline rows this is what
    makes any exported figure traceable back to a citation without re-running anything — the eager
    counterpart of the on-demand `explain` API.

    Returns:
        The path written, or None when no perspective carried a ledger (in which case no file is
        created at all, so a reader can tell "no provenance recorded" from "empty provenance").
    """
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
    """The new namespaced KPI set; every monetary KPI carries its uncertainty band.

    Builds the §7.3 KPI list from an evaluated matrix: per perspective the equivalent annual cost,
    the net present cost over the horizon, the year-1 monthly cost and the levelized cost of heat,
    plus one KPI per applied subsidy scheme and the perspective's total support; then the §6.5
    per-actor net present costs of `actor_kpi_entries` for whichever perspectives allocated flows
    to a payer; and, when a variant comparison is supplied, the NPV delta, the discounted payback
    and the warm-rent figures. Each
    name is suffixed with the perspective id in parentheses — that namespacing is what lets all nine
    perspectives coexist in one flat KPI space (see the module docstring).

    It exists as a separate function from `write_lifecycle_kpis` so the HTML report's KPI table and
    `lifecycle_kpis.json` are literally the same list; a KPI can therefore not appear in one and
    not the other. A KPI whose band is None is omitted entirely rather than published as zero.

    Two entries are deliberately *not* bands and are constructed by hand instead of via `add`: the
    discounted payback (whose low/high crossings are a bracket, carried in the description) and the
    warm-rent-neutral flag (a boolean rendered as a string, with the per-slot verdicts in the
    description).

    Args:
        matrix: The evaluated perspectives.
        comparison: Optional variant comparison against a reference run; adds the delta KPIs.

    Returns:
        The KPI entries, in perspective order.
    """
    entries: List[KpiEntry] = []

    def add(name: str, unit: str, band, description: Optional[str] = None) -> None:
        """Appends one banded KPI, or nothing at all when the figure does not exist.

        `value` is the AVERAGE slot and `value_min`/`value_max` the envelope (§3.9, §7.3). Skipping
        a None band is the deliberate contract: an absent KPI (no heat demand, hence no LCOH) must
        not be published as a zero.
        """
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
        for decision in result.subsidy_decisions:
            for award in decision.applied:
                if award.upfront_amount.maximum > 0:
                    add(
                        f"Subsidy {award.scheme_id} [EUR] ({perspective})",
                        "EUR",
                        award.upfront_amount,
                        description=decision.measure_subject,
                    )
        # The total is a view of the result, not a running sum kept while emitting KPIs (W4.1),
        # and since D2 it is the timeline-based nominal figure — so it is *not* the sum of the
        # per-scheme "Subsidy <id>" KPIs above wherever support reaches the timeline without an
        # upfront catalog award (flat shim, operational support, scheduled payouts).
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
    """Writes `lifecycle_kpis.json` (separate from all_kpis.json during the parallel phase).

    Serializes `build_lifecycle_kpi_entries` under a single "Lifecycle costs" group, keyed by KPI
    name. The separate file is the whole point of the parallel phase: legacy KPI names and values
    stay byte-identical in `all_kpis.json` whether or not this engine runs (§10.0 rule 3), and the
    two merge only at the Phase-7 cutover, when legacy KPIs also start carrying bands (§7.3).

    Args:
        matrix: The evaluated perspectives.
        result_directory: Where to write, normally next to the simulation's other results.
        comparison: Optional variant comparison, which adds the delta/payback/warm-rent KPIs.

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
    """Actor-level KPI entries (§6.5) for perspectives with payer allocations.

    One banded "Net present cost of <actor> [EUR] (<perspective>)" KPI per landlord / tenant /
    owner-occupier that the perspective's allocation ruleset actually produced, read straight off
    `npv_by_payer`; an actor with no allocation is skipped rather than reported as zero. The
    landlord and tenant figures sum with the owner's to the system NPV per slot (the §6 zero-sum
    invariant), so they can be published side by side without double counting — and because the
    KPI values are the payer bands verbatim, that invariant survives into the exported numbers.

    Part of `build_lifecycle_kpi_entries`, hence of both `lifecycle_kpis.json` and the report's
    KPI table. The entries are purely additive: they carry names no other KPI uses (no existing
    name, value or band changes), and a run whose perspectives allocate nothing to a payer adds
    none of them.
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
                    value=band.average,
                    value_min=band.minimum,
                    value_max=band.maximum,
                    tag=KpiTagEnumClass.COSTS,
                )
            )
    return entries
