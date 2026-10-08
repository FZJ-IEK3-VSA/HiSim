"""Cost audit table and legacy-parity harness (cost_spec.md §9.5, §9.7).

`cost_audit.csv` audits the inputs: one row per declared cost subject with asset class, size, resolved unit price and
band, lifetime, price origin and sources, gross investment and subsidies with binding caps; it catches mis-sized
components and unit mix-ups. `cost_parity_report.csv` compares the legacy `get_cost_capex` results, read only from
their CSVs, against the new engine per component; it is the evidence for the cutover decision (§10). This is
verification code on the engine side: it may import the database and evaluator, never the report modules.
"""

from __future__ import annotations

import csv
import enum
import os
from typing import Any, Dict, List, Optional, Union

import pandas as pd

from hisim import log
from hisim.economics.database import CostDatabase, CostDataError
from hisim.economics.evaluator import EvaluationInputs, effective_price_basis_year
from hisim.economics.input_audit import InputAuditReport, OriginKind, ResolvedInputRow, price_basis
from hisim.economics.parameters import EconomicParameters
from hisim.economics.provenance import ResolvedSource
from hisim.economics.results import LifecycleCostResult
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import Units


class AuditFileNames:
    """Names of the audit files written next to the results: the input audit (§9.5) and the parity report (§9.7)."""

    COST_AUDIT_FILE_NAME = "cost_audit.csv"
    PARITY_REPORT_FILE_NAME = "cost_parity_report.csv"


class AuditThresholds:
    """Size bounds above which the audit flags a declaration as a likely wiring mistake (§9.5).

    A flag is only a note in the audit row, never an error or a changed number. Bounds are per size unit and set where
    a residential declaration stops being believable: a thousand kW, kWh or m² is an apartment block, a hundred
    thousand litres a tank farm, ten thousand unitless devices a typo.
    """

    #: Size above which a declaration is almost certainly a wiring mistake, per size unit (§9.5).
    #: Keyed by the units `ComponentCostFacts.SUPPORTED_SIZE_UNITS` allows.
    IMPLAUSIBLE_SIZE_BY_UNIT: Dict[Units, float] = {
        Units.KILOWATT: 1e3,
        Units.KWH: 1e3,
        Units.SQUARE_METER: 1e3,
        Units.LITER: 1e5,
        Units.ANY: 1e4,
    }

    @classmethod
    def implausible_size(cls, size_unit: Units) -> float:
        """Return the size bound for one unit, falling back to the unitless bound for a unit without its own.

        Args:
            size_unit: The unit the declared size is stated in.

        Returns:
            The size above which the row is flagged.
        """
        return cls.IMPLAUSIBLE_SIZE_BY_UNIT.get(size_unit, cls.IMPLAUSIBLE_SIZE_BY_UNIT[Units.ANY])


def build_input_audit(
    inputs: EvaluationInputs,
    database: CostDatabase,
    parameters: EconomicParameters,
    result: Optional[LifecycleCostResult] = None,
) -> InputAuditReport:
    """Resolve every declared fact against the cost database once, into the typed input audit (§9.5).

    Per subject it determines which unit price applied and where it came from: a per-field config override (which wins
    whether or not a database entry exists), a database entry with its `valid_from_year` key and source ids, or
    nothing. It adds the gross investment and subsidy outcome from the evaluated result and flags a missing database
    entry, an override without `override_source`, and a size above `AuditThresholds.implausible_size`. The CSV writer
    and the HTML report both render this one report, and it is persisted so a report can be rebuilt without a cost
    database.

    Args:
        inputs: The declared facts, normally from `economic_inputs.json`.
        database: The cost database to resolve against; the price basis year comes from it and
            `inputs.simulation_year`.
        parameters: Economic parameters, for the country and an explicit price basis year.
        result: One evaluated perspective, supplying gross investment and subsidy decisions per subject. Without it the
            audit has origins, prices and flags but no amounts.

    Returns:
        An `InputAuditReport` with the price basis year, one row per declared subject and the §3.10 source registry
            entries the evaluation cited.
    """
    year = effective_price_basis_year(parameters, database, inputs.simulation_year)
    decisions_by_subject = (
        {decision.measure_subject: decision for decision in result.subsidy_decisions} if result else {}
    )
    rows: List[ResolvedInputRow] = []
    for subject_facts in inputs.cost_facts:
        facts = subject_facts.facts
        flags: List[str] = []
        entry = None
        try:
            entry = database.get_device_entry(facts.asset_class, year, parameters.country)
        except CostDataError:
            flags.append("no database entry")
        # A stated purchase price (a reader's quote) wins over the investment override, as it does
        # in the engine: it is what the year-0 purchase was priced at.
        stated = (
            facts.purchase_cost_override_in_euro
            if facts.purchase_cost_override_in_euro is not None
            else facts.investment_cost_override_in_euro
        )
        if stated is not None:
            origin_kind = OriginKind.ORIGIN_OVERRIDE
            unit_price: Optional[UncertainValue] = stated
            if not facts.override_source:
                flags.append("override without source")
        elif entry is not None:
            origin_kind, unit_price = OriginKind.ORIGIN_DATABASE, entry.specific_investment
        else:
            origin_kind, unit_price = OriginKind.ORIGIN_UNRESOLVED, None
        lifetime = facts.lifetime_override_in_years
        if lifetime is None and entry is not None:
            lifetime = entry.service_life_in_years
        if facts.size > AuditThresholds.implausible_size(facts.size_unit):
            flags.append(f"size {facts.size:,.0f} {facts.size_unit.value} looks implausible")
        breakdown = result.component_breakdowns.get(subject_facts.subject) if result else None
        decision = decisions_by_subject.get(subject_facts.subject)
        caps = {}
        if decision:
            for award in decision.applied:
                bound = [slot for slot, is_bound in award.caps_binding_per_slot.items() if is_bound]
                if bound:
                    caps[award.scheme_id] = bound
        rows.append(
            ResolvedInputRow(
                subject=subject_facts.subject,
                asset_class=facts.asset_class.value,
                size=facts.size,
                size_unit=facts.size_unit.value,
                origin_kind=origin_kind,
                override_source=facts.override_source,
                entry_key=entry.entry_key if entry is not None else None,
                source_ids=list(entry.source_ids) if entry is not None else [],
                unit_price_in_euro=unit_price,
                lifetime_in_years=lifetime,
                investment_gross_in_euro=breakdown.investment_gross_in_euro if breakdown else None,
                subsidies_nominal_in_euro=breakdown.subsidies_nominal_in_euro if breakdown else None,
                subsidy_scheme_ids=[award.scheme_id for award in decision.applied] if decision else [],
                caps_binding_by_scheme=caps,
                # The anyway (Sowieso) share behind this subject's anyway credit and the cost it
                # was applied to, so the audited credit is a multiplication the reader can check.
                anyway_share=(
                    result.anyway_share_by_subject.get(subject_facts.subject) if result else None
                ),
                anyway_basis_in_euro=(
                    result.anyway_basis_by_subject.get(subject_facts.subject) if result else None
                ),
                flags=flags,
            )
        )
    return InputAuditReport(price_basis_year=year, rows=rows, sources=_referenced_sources(database, result))


def _referenced_sources(
    database: CostDatabase, result: Optional[LifecycleCostResult]
) -> List[ResolvedSource]:
    """Return the §3.10 source entries this evaluation cited, resolved and sorted by id.

    Combines the cost database's referenced ids (`SourceRegistry.referenced_ids`) and the subsidy catalog's, found
    through the result's ledger and `source_resolver`.
    """
    referenced = set(database.sources.referenced_ids())
    resolver = dict(result.source_resolver or {}) if result is not None else {}
    if result is not None and result.ledger is not None:
        for record in result.ledger.records:
            referenced.update(source_id for source_id in record.source_ids if source_id in resolver)
    sources: List[ResolvedSource] = []
    for source_id in sorted(referenced):
        entry = database.sources.entries.get(source_id)
        resolved = entry.to_resolved() if entry is not None else resolver.get(source_id)
        if resolved is not None:
            sources.append(resolved)
    return sources


def _csv_origin(row: ResolvedInputRow) -> str:
    """Return the CSV's wording for a row's `origin_kind`.

    The wording is kept stable because `cost_audit.csv` is diffed on golden scenarios (§9.5). UNRESOLVED says so
    instead of claiming a database price.
    """
    if row.origin_kind == OriginKind.ORIGIN_OVERRIDE:
        return f"config override ({row.override_source or 'no source given'})"
    if row.origin_kind == OriginKind.ORIGIN_DATABASE:
        return f"database entry {row.entry_key}"
    return "unresolved - not priced"


def write_cost_audit(audit: InputAuditReport, result_directory: str) -> str:
    """Write `cost_audit.csv`: one semicolon-separated row per declared subject (§9.5).

    Each row carries the declaration, the price origin and sources, unit price and gross investment as
    min/best_estimate/max, the lifetime, the applied subsidy schemes and which caps bound in which slot. "Price basis"
    precedes the unit-price columns because a database price is euro per size unit while an override is an absolute
    amount. The column set and wording are kept stable so data changes show up as a reviewable diff on golden
    scenarios.

    Args:
        audit: The resolved report from `build_input_audit`.
        result_directory: Where to write it, next to the run's other results.

    Returns:
        The path written.
    """
    path = os.path.join(result_directory, AuditFileNames.COST_AUDIT_FILE_NAME)
    rows: List[List[Any]] = []
    header = [
        "Subject",
        "Asset class",
        "Size",
        "Unit",
        "Investment origin",
        "Sources",
        "Price basis",
        "Unit price min",
        "Unit price best_estimate",
        "Unit price max",
        "Lifetime [a]",
        "Gross investment min [EUR]",
        "Gross investment best_estimate [EUR]",
        "Gross investment max [EUR]",
        "Subsidy schemes",
        "Subsidy min [EUR]",
        "Subsidy best_estimate [EUR]",
        "Subsidy max [EUR]",
        "Caps binding (slots)",
        "Anyway share",
        # The credit is share x basis; the basis is the second number a reader needs to check it.
        "Anyway basis [EUR]",
    ]
    for row in audit.rows:
        unit_price, gross, subsidy = (
            row.unit_price_in_euro, row.investment_gross_in_euro, row.subsidies_nominal_in_euro
        )
        rows.append(
            [
                row.subject,
                row.asset_class,
                row.size,
                row.size_unit,
                _csv_origin(row),
                " ".join(row.source_ids),
                price_basis(row),
                unit_price.minimum if unit_price else "",
                unit_price.best_estimate if unit_price else "",
                unit_price.maximum if unit_price else "",
                row.lifetime_in_years if row.lifetime_in_years is not None else "",
                gross.minimum if gross else "",
                gross.best_estimate if gross else "",
                gross.maximum if gross else "",
                " ".join(row.subsidy_scheme_ids),
                subsidy.minimum if subsidy else "",
                subsidy.best_estimate if subsidy else "",
                subsidy.maximum if subsidy else "",
                "; ".join(f"{scheme}:{','.join(slots)}" for scheme, slots in row.caps_binding_by_scheme.items()),
                row.anyway_share if row.anyway_share is not None else "",
                row.anyway_basis_in_euro if row.anyway_basis_in_euro is not None else "",
            ]
        )
    with open(path, "w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file, delimiter=";")
        writer.writerow(header)
        writer.writerows(rows)
    return path


class LegacyCsvStatus(enum.Enum):
    """Why a legacy cost CSV produced no table.

    ABSENT is normal for a run with `COMPUTE_CAPEX` off. UNREADABLE means the file exists but could not be parsed, a
    defect that must not read like a disabled check.
    """

    ABSENT = "absent"
    UNREADABLE = "unreadable"


def _read_legacy_csv(path: str) -> Union[pd.DataFrame, LegacyCsvStatus]:
    """Read one legacy cost CSV, read-only, or say why there is none (§10.0 rule 4).

    Failures are not fatal, since the parity report is diagnostic, but the reason reaches the caller.

    Args:
        path: The legacy CSV to read.

    Returns:
        The parsed table, or the `LegacyCsvStatus` saying why there is none.
    """
    if not os.path.isfile(path):
        return LegacyCsvStatus.ABSENT
    try:
        return pd.read_csv(path, sep=";")
    except (pd.errors.ParserError, OSError) as err:
        log.warning(f"Parity harness could not read {path}: {err}")
        return LegacyCsvStatus.UNREADABLE


def write_parity_report(
    inputs: EvaluationInputs,
    database: CostDatabase,
    parameters: EconomicParameters,
    result_directory: str,
) -> Optional[str]:
    """Write the shadow-mode parity report: legacy CSV values against the new engine (§9.7).

    Reads the legacy `investment_cost_co2_footprint.csv` (read-only) by component name and, per declared subject,
    compares the gross device investment and the investment for the simulated period (investment / lifetime * simulated
    fraction, the legacy annualization) in the best-estimate slot. A row is flagged `DISCREPANCY` when the delta
    exceeds 1 % of the legacy value, floored at one cent. A subject unknown to the legacy CSV is reported as `not in
    legacy CSV`; a subject with neither override nor database entry is skipped. Origin, unit price and lifetime are
    read from `build_input_audit`'s rows. Discrepancies are evidence of a migration mistake or a bug in the old path,
    not errors.

    The gross investment is recomputed with the legacy formula rather than taken from
    `ComponentCostBreakdown.investment_gross_in_euro`, which also includes planning and removal and so would differ as
    soon as those are non-zero.

    Args:
        inputs: The declared facts, read from `economic_inputs.json` so they predate the legacy run.
        database: Cost database to price the new path against.
        parameters: Economic parameters: country and price basis year.
        result_directory: Directory holding the legacy CSVs and receiving the report.

    Returns:
        The path of `cost_parity_report.csv`, or None when the legacy capex CSV is absent (COMPUTE_CAPEX off) or
            unreadable.
    """
    legacy_path = os.path.join(result_directory, "investment_cost_co2_footprint.csv")
    legacy_csv = _read_legacy_csv(legacy_path)
    if isinstance(legacy_csv, LegacyCsvStatus):
        if legacy_csv is LegacyCsvStatus.ABSENT:
            log.information("Parity report skipped: legacy capex CSV not present (COMPUTE_CAPEX off).")
        else:
            log.warning(
                f"Parity report skipped: the legacy capex CSV {legacy_path} exists but could not be "
                "read, so this run contributes no parity evidence. This is a defect, not a disabled "
                "option."
            )
        return None
    capex_df = legacy_csv
    path = os.path.join(result_directory, AuditFileNames.PARITY_REPORT_FILE_NAME)
    # Precedence, unit price and lifetime are read from the audit's rows rather than decided again,
    # so the parity report cannot disagree with the audit. No result is passed: parity compares
    # declarations with the legacy CSV.
    audit = build_input_audit(inputs, database, parameters)
    # Paired by subject, never by position, so a reordered or filtered audit cannot pair one
    # subject's origin with another's legacy figures.
    rows_by_subject = {row.subject: row for row in audit.rows}
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
        except (KeyError, TypeError, ValueError) as err:
            # Named, not swallowed: a renamed legacy column would otherwise turn every component into a
            # "not in legacy CSV" row.
            log.warning(
                f"Parity harness could not read the legacy capex row for component '{name}' in "
                f"{legacy_path}: {type(err).__name__}: {err}. The component is reported as not "
                "present in the legacy CSV."
            )
            continue
    if len(capex_df.index) and not legacy_by_component:
        log.warning(
            f"Parity harness parsed none of the {len(capex_df.index)} data rows of {legacy_path}: "
            "every component will be reported as not present in the legacy CSV, so the report "
            "compares nothing. The columns the harness reads are most likely no longer the ones "
            "the legacy path writes."
        )
    for subject_facts in inputs.cost_facts:
        facts = subject_facts.facts
        audit_row = rows_by_subject.get(subject_facts.subject)
        legacy = legacy_by_component.get(subject_facts.subject)
        if audit_row is None:
            log.warning(
                f"Parity harness has no input-audit row for the declared subject "
                f"'{subject_facts.subject}', so it has no new value to compare; the subject is "
                "left out of the parity report."
            )
            continue
        if audit_row.origin_kind == OriginKind.ORIGIN_UNRESOLVED or audit_row.unit_price_in_euro is None:
            continue
        if audit_row.origin_kind == OriginKind.ORIGIN_OVERRIDE:
            # `.scale(count)` exactly as the engine does it (calculators/context_resolution.py):
            # an override states the price of one device, and a subject may declare several.
            new_investment = audit_row.unit_price_in_euro.scale(float(facts.count)).best_estimate
        else:
            # The row says DATABASE, so this lookup is the one `build_input_audit` already made and
            # cannot fail. The entry is needed rather than the row's unit price because sizing is
            # the entry's own law (`investment_for_size`: absolute, power-law or linear).
            entry = database.get_device_entry(facts.asset_class, audit.price_basis_year, parameters.country)
            new_investment = entry.investment_for_size(facts.size).scale(float(facts.count)).best_estimate
        lifetime = audit_row.lifetime_in_years or 0.0
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
                ""
                if abs(delta) < 0.01 * max(1.0, abs(legacy["investment"]))
                else "DISCREPANCY (see cost_module_issues.md)",
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
