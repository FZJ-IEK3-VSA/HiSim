"""Cost audit report and legacy-parity harness (cost_spec.md §9.5, §9.7).

The audit is the eager, tabular summary of the same ledger the `explain` API queries on
demand: review one table instead of 46 files. The parity harness compares the legacy path's
already-computed results (read-only, from its CSVs) against the new facts->engine path.
"""

from __future__ import annotations

import csv
import os
from typing import Any, Dict, List, Optional

import pandas as pd

from hisim import log
from hisim.economics.database import CostDatabase, CostDataError
from hisim.economics.evaluator import EvaluationInputs
from hisim.economics.parameters import EconomicParameters
from hisim.economics.results import LifecycleCostResult

COST_AUDIT_FILE_NAME = "cost_audit.csv"
PARITY_REPORT_FILE_NAME = "cost_parity_report.csv"


def write_cost_audit(
    inputs: EvaluationInputs,
    database: CostDatabase,
    parameters: EconomicParameters,
    result: LifecycleCostResult,
    result_directory: str,
) -> str:
    """Writes cost_audit.csv: one row per component with origins, sources and bands (§9.5)."""
    path = os.path.join(result_directory, COST_AUDIT_FILE_NAME)
    year = parameters.price_basis_year or inputs.simulation_year
    rows: List[List[Any]] = []
    header = [
        "Subject",
        "Asset class",
        "Size",
        "Unit",
        "Investment origin",
        "Sources",
        "Unit price min",
        "Unit price avg",
        "Unit price max",
        "Lifetime [a]",
        "Gross investment min [EUR]",
        "Gross investment avg [EUR]",
        "Gross investment max [EUR]",
        "Subsidy schemes",
        "Subsidy min [EUR]",
        "Subsidy avg [EUR]",
        "Subsidy max [EUR]",
        "Caps binding (slots)",
    ]
    decisions_by_subject = {decision.measure_subject: decision for decision in result.subsidy_decisions}
    for subject_facts in inputs.cost_facts:
        facts = subject_facts.facts
        origin = "database entry"
        sources = ""
        unit_price = None
        lifetime: Optional[float] = facts.lifetime_override_in_years
        if facts.investment_cost_override_in_euro is not None:
            origin = f"config override ({facts.override_source or 'no source given'})"
            unit_price = facts.investment_cost_override_in_euro
        try:
            entry = database.get_device_entry(facts.asset_class, year, parameters.country)
            if unit_price is None:
                unit_price = entry.specific_investment
            if lifetime is None:
                lifetime = entry.service_life_in_years
            sources = " ".join(entry.source_ids)
            if facts.investment_cost_override_in_euro is None:
                origin = f"database entry {entry.entry_key}"
        except CostDataError:
            pass
        breakdown = result.component_breakdowns.get(subject_facts.subject)
        gross = breakdown.investment_gross_in_euro if breakdown else None
        subsidy = breakdown.subsidies_in_euro if breakdown else None
        decision = decisions_by_subject.get(subject_facts.subject)
        schemes = " ".join(award.scheme_id for award in decision.applied) if decision else ""
        caps = ""
        if decision:
            caps = "; ".join(
                f"{award.scheme_id}:{','.join(slot for slot, bound in award.caps_binding_per_slot.items() if bound)}"
                for award in decision.applied
                if any(award.caps_binding_per_slot.values())
            )
        rows.append(
            [
                subject_facts.subject,
                facts.asset_class.value,
                facts.size,
                facts.size_unit.value,
                origin,
                sources,
                unit_price.minimum if unit_price else "",
                unit_price.average if unit_price else "",
                unit_price.maximum if unit_price else "",
                lifetime if lifetime is not None else "",
                gross.minimum if gross else "",
                gross.average if gross else "",
                gross.maximum if gross else "",
                schemes,
                subsidy.minimum if subsidy else "",
                subsidy.average if subsidy else "",
                subsidy.maximum if subsidy else "",
                caps,
            ]
        )
    with open(path, "w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file, delimiter=";")
        writer.writerow(header)
        writer.writerows(rows)
    return path


def _read_legacy_csv(path: str) -> Optional[pd.DataFrame]:
    if not os.path.isfile(path):
        return None
    try:
        return pd.read_csv(path, sep=";")
    except (pd.errors.ParserError, OSError) as err:
        log.warning(f"Parity harness could not read {path}: {err}")
        return None


def write_parity_report(
    inputs: EvaluationInputs,
    database: CostDatabase,
    parameters: EconomicParameters,
    result_directory: str,
) -> Optional[str]:
    """Shadow-mode parity: legacy CSV values vs the new facts->engine path (§9.7).

    Parity is checked against the AVERAGE slot with legacy-equivalent formulas
    (investment / lifetime * simulated fraction). Discrepancies are evidence, not errors:
    each is either a migration mistake or a latent legacy bug (documented in
    cost_module_issues.md), and this report is the primary input for the cutover decision.
    """
    capex_df = _read_legacy_csv(os.path.join(result_directory, "investment_cost_co2_footprint.csv"))
    if capex_df is None:
        log.information("Parity report skipped: legacy capex CSV not present (COMPUTE_CAPEX off).")
        return None
    path = os.path.join(result_directory, PARITY_REPORT_FILE_NAME)
    year = parameters.price_basis_year or inputs.simulation_year
    fraction = inputs.simulated_period_fraction
    rows: List[List[Any]] = []
    legacy_by_component: Dict[str, Dict[str, float]] = {}
    for _, row in capex_df.iterrows():
        name = str(row.get("Component", ""))
        try:
            legacy_by_component[name] = {
                "investment": float(row["Investment [EUR]"]),
                "lifetime": float(row["Lifetime [Years]"]),
                "investment_period": float(row["Investment for simulated period [EUR]"]),
            }
        except (KeyError, TypeError, ValueError):
            continue
    for subject_facts in inputs.cost_facts:
        facts = subject_facts.facts
        legacy = legacy_by_component.get(subject_facts.subject)
        try:
            entry = database.get_device_entry(facts.asset_class, year, parameters.country)
        except CostDataError:
            entry = None
        if facts.investment_cost_override_in_euro is not None:
            new_investment = facts.investment_cost_override_in_euro.average
        elif entry is not None:
            new_investment = entry.investment_for_size(facts.size).average * facts.count
        else:
            continue
        lifetime = facts.lifetime_override_in_years or (entry.service_life_in_years if entry else 0.0)
        new_period = new_investment / lifetime * fraction if lifetime else 0.0
        if legacy is None:
            rows.append([subject_facts.subject, "investment", "", new_investment, "", "not in legacy CSV"])
            continue
        delta = new_investment - legacy["investment"]
        rows.append(
            [
                subject_facts.subject,
                "investment",
                legacy["investment"],
                round(new_investment, 2),
                round(delta, 2),
                "" if abs(delta) < 0.01 * max(1.0, abs(legacy["investment"])) else "DISCREPANCY (see cost_module_issues.md)",
            ]
        )
        delta_period = new_period - legacy["investment_period"]
        rows.append(
            [
                subject_facts.subject,
                "investment_for_simulated_period",
                legacy["investment_period"],
                round(new_period, 2),
                round(delta_period, 2),
                "" if abs(delta_period) < 0.01 * max(1.0, abs(legacy["investment_period"])) else "DISCREPANCY",
            ]
        )
    with open(path, "w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file, delimiter=";")
        writer.writerow(["Component", "Figure", "Legacy value", "New value", "Delta", "Note"])
        writer.writerows(rows)
    return path
