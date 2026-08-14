"""Cost audit report and legacy-parity harness (cost_spec.md §9.5, §9.7).

The audit is the eager, tabular summary of the same ledger the `explain` API queries on
demand: review one table instead of 46 files. The parity harness compares the legacy path's
already-computed results (read-only, from its CSVs) against the new facts->engine path.

**This module is verification, not presentation** (cost-spec-v2 §2.4, W4.6). It computes by
design — that is the whole point of a parity harness — so it sits on the engine side of the
seam-4 lint and may import the database and the evaluator. What it must *not* do is import
`reporting`/`report_plots`: the flow goes the other way. `build_input_audit` resolves every
declared fact against the cost database **once**, into a typed `InputAuditReport`, which the
CSV writer here and the HTML report's input-audit section both merely render. Before W4.6 the
override precedence was implemented twice — a third time, counting the report — and the two
implementations disagreed in an edge case (see `ResolvedInputRow.origin_kind`).

**The two halves, and why they are in one file.** Both answer "is the new path telling the truth",
just about different things. `cost_audit.csv` audits the *inputs*: one row per declared cost
subject with its asset class, size, resolved unit price and band, lifetime, the origin and sources
of that price, the resulting gross investment and the subsidies with their binding caps. It is
where a mis-sized component, a kW/m² mix-up or an uncited override is caught — errors that leave
every downstream number arithmetically perfect and completely wrong (§9.5). `cost_parity_report.csv`
audits the *output*: legacy value vs. new value vs. delta, per component.

**The parity report is the evidence base for the cutover decision, and this is explicit.** During
the parallel phase the legacy `get_cost_capex`/`get_cost_opex` path remains the sole source of all
published numbers (§10.0 rule 5); the new engine runs beside it in shadow mode on every run where
both are active. This harness reads the legacy CSVs **read-only** — it never calls a legacy cost
method, because `get_cost_capex` mutates component configs and calling it again would corrupt the
very calculation it must leave bit-identical (§10.0 rule 4) — and diffs them against the facts the
new engine captured *before* the legacy path ran. The accumulated report across the golden scenario
suite and real RenoVisor runs is what §10 Phase 7 checks before removing the old implementation:
zero unexplained deltas is an exit criterion. Every remaining discrepancy is either a migration
mistake or a latent bug in the old code, and is documented in `cost_module_issues.md` rather than
silently fixed.
"""

from __future__ import annotations

import csv
import os
from typing import Any, Dict, List, Optional

import pandas as pd

from hisim import log
from hisim.economics.database import CostDatabase, CostDataError
from hisim.economics.evaluator import EvaluationInputs, effective_price_basis_year
from hisim.economics.input_audit import InputAuditReport, OriginKind, ResolvedInputRow
from hisim.economics.parameters import EconomicParameters
from hisim.economics.provenance import ResolvedSource
from hisim.economics.results import LifecycleCostResult
from hisim.economics.uncertainty import UncertainValue


class AuditFileNames:
    """Names of the audit files written next to the results.

    Both are new files nothing legacy reads (§10.0 rule 3): the input audit of §9.5 and the parity
    report of §9.7. They are the two files a reviewer opens first — one to check what went into the
    calculation, one to check whether it agrees with the path being replaced.
    """

    COST_AUDIT_FILE_NAME = "cost_audit.csv"
    PARITY_REPORT_FILE_NAME = "cost_parity_report.csv"


class AuditThresholds:
    """Bounds the audit flags declared facts against (§9.5).

    The audit's job includes catching wiring mistakes that are perfectly valid arithmetic, so it
    carries a small number of "this cannot be what you meant" bounds. They only ever produce a flag
    in the audit row — never an error, never a changed number — because the threshold is a heuristic
    and the engine's own validation (`ComponentCostFacts.__post_init__`, the resolution check) is
    what actually rejects impossible declarations.
    """

    #: Anything above this many units of a size is almost certainly a wiring mistake (§9.5).
    IMPLAUSIBLE_SIZE = 1e4


def build_input_audit(
    inputs: EvaluationInputs,
    database: CostDatabase,
    parameters: EconomicParameters,
    result: Optional[LifecycleCostResult] = None,
) -> InputAuditReport:
    """Resolves every declared fact against the cost database, once (§9.5, W4.6).

    Walks the declared cost subjects and, per subject, answers the audit's central question: which
    unit price did this actually resolve to, and from where — a per-field config override (which
    wins whether or not a database entry exists), a cost-database entry with its `valid_from_year`
    key and source ids, or nothing at all. It then attaches the resulting gross investment and the
    subsidy outcome from the evaluated result, and flags what looks wrong: a missing database entry,
    an override without an `override_source`, an implausible size.

    "Once" is the design point (W4.6). The same question used to be answered independently by the
    CSV writer here and by the HTML report's input-audit section, and the two implementations
    disagreed — the report dropped an override's unit price whenever the asset class had no database
    entry. Both now render this one typed `InputAuditReport`, which is also persisted so a report
    can be rebuilt from an archived directory with no cost database present (W4.5).

    Args:
        inputs: The declared facts, normally straight from `economic_inputs.json`.
        database: The cost database to resolve against; the price basis year is derived from it
            and `inputs.simulation_year` via the one shared policy.
        parameters: Economic parameters — used for the country and an explicit price basis year.
        result: One evaluated perspective, supplying the gross investment and the subsidy decisions
            per subject. Optional: without it the audit still reports origins, prices and flags,
            just no resulting amounts.

    Returns:
        A typed `InputAuditReport` with the resolved price basis year, one row per declared subject
        and the §3.10 source registry entries this evaluation cited.
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
        if facts.investment_cost_override_in_euro is not None:
            origin_kind = OriginKind.ORIGIN_OVERRIDE
            unit_price: Optional[UncertainValue] = facts.investment_cost_override_in_euro
            if not facts.override_source:
                flags.append("override without source")
        elif entry is not None:
            origin_kind, unit_price = OriginKind.ORIGIN_DATABASE, entry.specific_investment
        else:
            origin_kind, unit_price = OriginKind.ORIGIN_UNRESOLVED, None
        lifetime = facts.lifetime_override_in_years
        if lifetime is None and entry is not None:
            lifetime = entry.service_life_in_years
        if facts.size > AuditThresholds.IMPLAUSIBLE_SIZE:
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
                flags=flags,
            )
        )
    return InputAuditReport(price_basis_year=year, rows=rows, sources=_referenced_sources(database, result))


def _referenced_sources(
    database: CostDatabase, result: Optional[LifecycleCostResult]
) -> List[ResolvedSource]:
    """The §3.10 registry entries this evaluation cited, resolved and sorted by id.

    Two registries feed it: the cost database's (whatever data files were touched, tracked by
    `SourceRegistry.referenced_ids`) and — via the result's ledger and `source_resolver` — the
    subsidy catalog's, whose ids could not be shown while subsidy provenance was fabricated as
    `inline:` pseudo-sources (W2.4).
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
    """The CSV's spelling of `origin_kind`.

    Formatting, not logic: the precedence decision was already made in `build_input_audit`, and each
    renderer only chooses words for it (the HTML report spells the same three outcomes its own way).
    The two resolved wordings are preserved verbatim from before the W4.6 refactor so that diffing
    `cost_audit.csv` across it shows no change — the file is reviewed as a diff on golden scenarios
    (§9.5), so churn in it is expensive. The third, UNRESOLVED, used to fall through to the bare
    string "database entry" and so reported an unpriceable component as if the database had priced
    it; it now says so, which is the one wording change this function has seen.
    """
    if row.origin_kind == OriginKind.ORIGIN_OVERRIDE:
        return f"config override ({row.override_source or 'no source given'})"
    if row.origin_kind == OriginKind.ORIGIN_DATABASE:
        return f"database entry {row.entry_key}"
    return "unresolved - not priced"


def _csv_price_basis(row: ResolvedInputRow) -> str:
    """What the row's three unit-price columns are measured in.

    The columns hold one number per slot but not one *kind* of number: a DATABASE row states the
    entry's specific investment, euro per unit of the declared size (EUR/kW, EUR/m², …), while an
    OVERRIDE row states an absolute euro amount for the whole subject, which is why the two are
    never multiplied by the size the same way downstream. Labelling both "Unit price" and nothing
    else made a per-kW figure and a total look like the same quantity in one column — this column
    says which one a reader is looking at, and an UNRESOLVED row has no price and says nothing.
    """
    if row.origin_kind == OriginKind.ORIGIN_OVERRIDE:
        return "EUR absolute (override)"
    if row.origin_kind == OriginKind.ORIGIN_DATABASE:
        return f"EUR/{row.size_unit} (database)"
    return ""


def write_cost_audit(audit: InputAuditReport, result_directory: str) -> str:
    """Writes cost_audit.csv: one row per component with origins, sources and bands (§9.5).

    Pure rendering of an already-resolved `InputAuditReport` — semicolon-separated, one row per
    declared subject, with the whole chain from declaration to money in one line: what was declared,
    where its price came from, which sources back it, the unit price and gross investment as
    min/avg/max, the lifetime, the applied subsidy schemes and which of their caps bound in which
    slot. This is the "review one table instead of 46 files" deliverable.

    "Unit price" is not one quantity across rows, which is why "Price basis" sits in front of those
    three columns: a database-priced row states euro per unit of its size, an override states an
    absolute euro amount, and reading the second as the first understates a subject by orders of
    magnitude. The column names the basis explicitly rather than leaving it to be inferred from the
    origin string.

    Its second job is being a *diff*: a PR that changes cost data produces a reviewable delta of
    this file on the golden scenario suite, so price updates surface as explicit changes rather than
    silent drift (§9.5). That is why the column set and the origin wording are kept stable.

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
                _csv_price_basis(row),
                unit_price.minimum if unit_price else "",
                unit_price.average if unit_price else "",
                unit_price.maximum if unit_price else "",
                row.lifetime_in_years if row.lifetime_in_years is not None else "",
                gross.minimum if gross else "",
                gross.average if gross else "",
                gross.maximum if gross else "",
                " ".join(row.subsidy_scheme_ids),
                subsidy.minimum if subsidy else "",
                subsidy.average if subsidy else "",
                subsidy.maximum if subsidy else "",
                "; ".join(f"{scheme}:{','.join(slots)}" for scheme, slots in row.caps_binding_by_scheme.items()),
            ]
        )
    with open(path, "w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file, delimiter=";")
        writer.writerow(header)
        writer.writerows(rows)
    return path


def _read_legacy_csv(path: str) -> Optional[pd.DataFrame]:
    """Reads one legacy cost CSV, or None when it is absent or unparseable.

    The single point at which this package touches legacy output, and it is strictly read-only
    (§10.0 rule 4). Every failure mode is non-fatal on purpose: the parity report is diagnostic
    evidence, and a missing or malformed legacy file must never turn into an error on a run whose
    legacy results are fine.
    """
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

    **Why the gross investment is recomputed here** (cost-spec-v2 W4.6 asked whether it could
    read `ComponentCostBreakdown.investment_gross_in_euro` instead; measured 2026-08-12: it
    cannot, and the two are not value-identical in general). The breakdown's gross is the sum
    of the year-0 INVESTMENT **plus PLANNING plus REMOVAL** entries of the *scoped* timeline;
    the legacy CSV column this diffs against is the bare device investment. They coincide on
    every shipped data file only because `planning_cost_in_euro` and `removal_cost_in_euro` are
    0 throughout — a data PR setting either would silently turn every row of this report into a
    false discrepancy. Recomputing the legacy formula is this harness's purpose, so the
    duplication stays, deliberately, and only here.

    **What it actually compares.** The legacy `investment_cost_co2_footprint.csv` is read (read-only,
    §10.0 rule 4) and indexed by component name; for every declared cost subject two figures are
    diffed against it — the gross device investment and the "investment for the simulated period"
    (investment / lifetime × simulated fraction, the legacy annualization). A row is flagged
    ``DISCREPANCY`` when the delta exceeds 1 % of the legacy value, floored at one cent so a
    near-zero legacy value does not flag on float noise. A subject the legacy CSV does not
    know at all is reported as ``not in legacy CSV`` rather than as a delta — during the parallel
    phase that usually means a component the legacy path never priced. Subjects that resolve to
    neither an override nor a database entry are skipped: there is no new value to compare.

    Because the migrated database entries are degenerate bands (min = avg = max), checking the
    AVERAGE slot checks all three.

    Args:
        inputs: The declared facts, read back from `economic_inputs.json` so they provably predate
            the legacy run that produced the CSVs.
        database: Cost database to price the new path against.
        parameters: Economic parameters; country and price basis year policy.
        result_directory: Directory holding the legacy CSVs and receiving the report.

    Returns:
        The path of `cost_parity_report.csv`, or None when the legacy capex CSV is absent (i.e.
        COMPUTE_CAPEX was off and there is nothing to compare against).
    """
    capex_df = _read_legacy_csv(os.path.join(result_directory, "investment_cost_co2_footprint.csv"))
    if capex_df is None:
        log.information("Parity report skipped: legacy capex CSV not present (COMPUTE_CAPEX off).")
        return None
    path = os.path.join(result_directory, AuditFileNames.PARITY_REPORT_FILE_NAME)
    year = effective_price_basis_year(parameters, database, inputs.simulation_year)
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
