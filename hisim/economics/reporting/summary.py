"""The markdown cost summary and the plausibility-panel rendering (cost_spec.md §7.2, §7.4).

`build_cost_summary_markdown` and `write_cost_summary` produce `cost_summary.md`; `render_plausibility_findings` turns
plausibility findings into the display rows shared by the markdown, the HTML report and the bridge's log warnings.
"""


from __future__ import annotations

import datetime
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from hisim.economics import views
from hisim.economics.plausibility import CheckIds, CheckStatus, PlausibilityFinding, PlausibilityReport
from hisim.economics.presentation_style import PresentationStyle, group_name
from hisim.economics.report_prose import format_euro
from hisim.economics.results import (
    EvaluationMatrix,
    HeatCostNaming,
    LifecycleCostResult,
    VariantComparison,
)
from hisim.economics.uncertainty import UncertainValue


class ReportFileNames:
    """Names of the report files written next to the results.

    The `report` CLI writes them and the golden tests read them. They are plain file names; the caller supplies the
    directory.
    """

    COST_SUMMARY_FILE_NAME = "cost_summary.md"
    LIFECYCLE_REPORT_FILE_NAME = "lifecycle_report.html"


def _fmt(value: float) -> str:
    """Format a euro figure with the report's rounding rule.

    The rule is `report_prose.format_euro` (precision by magnitude: cents below 100 EUR, thousands above 100k), shared
    so the HTML report and the PNG captions print identical text. Rounding for display is the only arithmetic this
    module does.
    """
    return format_euro(value)


def _band_str(band: Optional[UncertainValue], unit: str = "EUR") -> str:
    """Render a band as `best_estimate [min | max] unit`.

    A band is an `UncertainValue` whose bracketed pair is the §3.9 envelope of the cheap and expensive worlds, not a
    confidence interval. An exact band collapses to a single figure, and None renders as `-`. `unit` is appended
    verbatim (EUR, EUR/a, EUR/mo, EUR/kWh).
    """
    if band is None:
        return "-"
    if band.is_exact():
        return f"{_fmt(band.best_estimate)} {unit}"
    return f"{_fmt(band.best_estimate)} [{_fmt(band.minimum)} | {_fmt(band.maximum)}] {unit}"


#: How each kind of assumption value is spelled: a rate to two decimals with a percent sign, a
#: working price to four, a euro figure and an energy quantity with thousands separators. The
#: assumptions table (`sections`) and the scenarios table (`assembly`) both print through it.
_ASSUMPTION_VALUE_FORMATS = {
    views.AssumptionKinds.PERCENT: "{value:.2%}",
    views.AssumptionKinds.YEARS: "{value:g}",
    views.AssumptionKinds.YEAR: "{value:g}",
    views.AssumptionKinds.FACTOR: "{value:.6f}",
    views.AssumptionKinds.EURO_PER_KWH: "{value:.4f}",
    views.AssumptionKinds.EURO_PER_YEAR: "{value:,.2f}",
    views.AssumptionKinds.EURO_PER_TON: "{value:,.2f}",
    views.AssumptionKinds.KWH_PER_YEAR: "{value:,.0f}",
    views.AssumptionKinds.SQUARE_METERS: "{value:,.1f}",
    views.AssumptionKinds.NUMBER: "{value:,.4g}",
    views.AssumptionKinds.PLAIN: "{value}",
}


def _value_by_kind(kind: "views.AssumptionKinds", value: object) -> str:
    """Spell one value in the conventional form for its kind.

    An unknown kind, or a value its format cannot take (a scenario name where a number is usual, e.g.
    `co2_price_scenario`), is printed as it stands rather than raising.

    Args:
        kind: The quantity kind, from `views.AssumptionKinds`.
        value: The number or text to spell.

    Returns:
        The value text, unescaped; the caller escapes it.
    """
    template = _ASSUMPTION_VALUE_FORMATS.get(kind, "{value}")
    try:
        return template.format(value=value)
    except (TypeError, ValueError):
        return str(value)


def _award_amount_str(presentation: "views.AwardPresentation") -> str:
    """Describe what one applied award is worth in one phrase: band, terms, or both.

    Shared by the markdown decision list, the HTML decision cards and the awards table. An award with a euro amount
    reads as a band, followed by its payout note when it is not a plain year-0 grant ("tax credit paid over 3 years").
    An award without a euro amount (loan terms, an operating rate, a VAT reduction) reads as its terms alone, since its
    value is booked by the financing or energy calculators and "0 EUR" would be false.

    Args:
        presentation: The award as `views.describe_award` valued it.

    Returns:
        The phrase, without surrounding punctuation.
    """
    if presentation.total_in_euro is None:
        return presentation.payout_note or presentation.payout_kind
    band = _band_str(presentation.total_in_euro)
    return f"{band}, {presentation.payout_note}" if presentation.payout_note else band


def _award_arithmetic_str(presentation: "views.AwardPresentation") -> str:
    """Return the award's rate, basis and cap verdict as one appended phrase.

    The cap verdict tells a reader whether spending more would earn more or the measure has hit its ceiling. Both parts
    come from the solver's recorded decision via `views.describe_award`.

    Args:
        presentation: The award as `views.describe_award` valued it.

    Returns:
        The parenthesized phrase, or the empty string for a form that states no rate and no cap (its `payout_note`
            already carries its terms).
    """
    parts = [part for part in (presentation.arithmetic, presentation.cap_verdict) if part]
    return f" ({'; '.join(parts)})" if parts else ""


def _scheme_markdown(display_name: Optional[str], scheme_id: str) -> str:
    """Name a subsidy scheme for a human, with its raw id in a trailing parenthesis.

    Markdown has no hover, so the id is printed after the name; reviewers grep `cost_audit.csv` and the catalog for it.

    Args:
        display_name: The catalog's friendly name, or None/empty when it declares none.
        scheme_id: The raw id.

    Returns:
        "name (id)", or the bare id when there is no friendly name.
    """
    if not display_name or display_name == scheme_id:
        return scheme_id
    return f"{display_name} ({scheme_id})"


# ---------------------------------------------------------------------------- plausibility


#: The three statuses the panel can render. The markdown's icon table is keyed by them and the
#: HTML writes them into a CSS class (`.status.PASS` and its siblings).
PANEL_STATUSES = frozenset({CheckStatus.PASS, CheckStatus.WARN, CheckStatus.FAIL})


@dataclass
class PlausibilityCheck:
    """One plausibility finding rendered as a panel row: four display strings.

    The finding itself is a `plausibility.PlausibilityFinding`; the markdown table and the HTML panel print the same
    four columns from this record.
    """

    name: str
    status: str  # PASS | WARN | FAIL
    value: str
    expected: str
    detail: str = ""

    def __post_init__(self) -> None:
        """Refuse a status neither renderer can display.

        The markdown looks the status up in an icon map and the HTML writes it into a CSS class, so an unknown status
        would raise mid-report or render as an unstyled row.

        Raises:
            ValueError: If `status` is not PASS, WARN or FAIL.
        """
        if self.status not in PANEL_STATUSES:
            raise ValueError(
                f"Plausibility check '{self.name}' carries the status {self.status!r}, which the "
                f"panel cannot render. Expected one of {', '.join(sorted(PANEL_STATUSES))}."
            )


#: Reader hints per check kind. Checks whose hint quotes numbers read them from the finding's
#: `context`; see `_finding_detail`.
class _CheckHints:
    """Reader hints shown next to a flagged check, keyed by check id.

    Hints say what usually causes a flagged value; that is editorial prose, so it lives on the presentation side. Hints
    that quote figures are built in `_finding_detail` from the finding's `context`; a check without a hint gets an
    empty note cell.
    """

    BY_CHECK_ID: Dict[str, str] = {
        CheckIds.CHECK_MAINTENANCE_RATIO: "a huge ratio usually means an absolute fee stored as a rate (issues #1)",
        CheckIds.CHECK_FLEXIBILITY_VALUE: (
            "the load was timed worse than a flat profile; the projection used 0 instead "
            "(controller signal or price series inverted?)"
        ),
        CheckIds.CHECK_USEFUL_HEAT_WITHOUT_HOT_WATER: (
            "no hot-water source the cost engine recognizes, so the system cost per unit of heat "
            "divides by the rooms' heat only and reads too high by the hot water's share (hisim-4wlu)"
        ),
    }


def _finding_detail(finding: PlausibilityFinding) -> str:
    """Return the reader hint ("Note" column) for one finding.

    The effective-price and band-width hints are formatted from the finding's `context`; every other check uses its
    static hint in `_CheckHints`. The context keys are fixed per check id in `plausibility.py`, so a missing key
    raises.
    """
    if finding.check_id == CheckIds.CHECK_EFFECTIVE_PRICE:
        return (
            f"{_fmt(finding.context['year1_cost'])} EUR for {finding.context['quantity']:,.0f} "
            "kWh — catches unit mix-ups"
        )
    if finding.check_id == CheckIds.CHECK_BAND_WIDTH:
        return (
            f"over {int(finding.context['horizon_in_years'])} years; very wide bands usually "
            "mean a band typo in the data"
        )
    return _CheckHints.BY_CHECK_ID.get(finding.check_id, "")


# One return per check kind keeps each kind's formatting next to its kind.
def _render_finding(finding: PlausibilityFinding) -> PlausibilityCheck:  # pylint: disable=too-many-return-statements
    """Format one finding into its panel row, with no arithmetic beyond rounding.

    Dispatches on `check_id`, since each kind means something different by "value" and "expected": a reconciliation
    prints a delta against zero, a band-ordering check prints the rejected band, a range check a figure against its
    bounds. Unknown kinds fall through to the generic range rendering, whose precision switches at 100 so a ratio of
    0.043 and 12,400 EUR/m2a both read well.
    """
    detail = _finding_detail(finding)
    if finding.check_id == CheckIds.CHECK_RESULTS_PRESENT:
        return PlausibilityCheck(finding.name, finding.status, "0 perspectives", ">= 1", detail)
    if finding.check_id == CheckIds.CHECK_BAND_ORDERING:
        return PlausibilityCheck(finding.name, finding.status, str(finding.band), "min<=best_estimate<=max", detail)
    value = finding.value if finding.value is not None else 0.0
    if finding.check_id == CheckIds.CHECK_SUBJECTS_SUM_TO_TOTAL:
        return PlausibilityCheck(finding.name, finding.status, f"delta {_fmt(value)} EUR", "0", detail)
    if finding.check_id == CheckIds.CHECK_RESIDUAL_BELOW_PURCHASES:
        return PlausibilityCheck(
            finding.name,
            finding.status,
            f"{_fmt(value)} vs {_fmt(finding.context['purchases'])} EUR",
            "residual below discounted purchases",
            detail,
        )
    if finding.check_id == CheckIds.CHECK_SUBSIDIES_BELOW_BASIS:
        return PlausibilityCheck(
            finding.name,
            finding.status,
            f"{_fmt(value)} vs {_fmt(finding.context['basis'])} EUR",
            "support below its cost basis",
            detail,
        )
    if finding.check_id == CheckIds.CHECK_FLEXIBILITY_VALUE:
        return PlausibilityCheck(finding.name, finding.status, f"{_fmt(value)} EUR", ">= 0 EUR", detail)
    if finding.check_id == CheckIds.CHECK_USEFUL_HEAT_WITHOUT_HOT_WATER:
        return PlausibilityCheck(
            finding.name, finding.status, f"{value:,.0f} {finding.unit} rooms only", "rooms + hot water", detail
        )
    low, high = finding.bounds if finding.bounds else (0.0, 0.0)
    return PlausibilityCheck(
        name=finding.name,
        status=finding.status,
        value=f"{value:,.3f} {finding.unit}" if abs(value) < 100 else f"{value:,.0f} {finding.unit}",
        expected=f"{low:g} - {high:g} {finding.unit}",
        detail=detail,
    )


def render_plausibility_findings(report: PlausibilityReport) -> List[PlausibilityCheck]:
    """Return the panel rows of a plausibility report, in the order `run_plausibility_checks` produced them.

    Both report formats and `bridge.py`'s log warnings use this, so all three show the same rows with the same wording.
    """
    return [_render_finding(finding) for finding in report.findings]


def all_bands_degenerate(matrix: EvaluationMatrix) -> bool:
    """Return True when every perspective's total NPV band is exact (minimum = best estimate = maximum).

    Only `total_npv_in_euro` is examined; any band in the cost data propagates into it, so the answer means "the
    reports have no whiskers to draw". It holds when the price basis year resolves to the 1:1-migrated legacy data,
    which is exact on purpose (§10.1); banded data ships for 2026 and 2035.
    """
    return all(result.total_npv_in_euro.is_exact() for result in matrix.results.values())


def _reference_result(matrix: EvaluationMatrix) -> LifecycleCostResult:
    """Return the matrix's reference perspective, its first, refusing an empty matrix.

    Every report is built around this result: the header's run parameters, the single-result sections and the
    degenerate-band note. An empty matrix is reachable (every perspective filtered out, an upstream failure), so it
    gets a clear error.

    Args:
        matrix: The evaluated perspectives.

    Returns:
        The first result.

    Raises:
        ValueError: If the matrix holds no evaluated perspective.
    """
    reference = next(iter(matrix.results.values()), None)
    if reference is None:
        raise ValueError(
            "Cannot build a report: the evaluation matrix has no evaluated perspectives. Every "
            "report is built around a reference perspective, so there is nothing to render."
        )
    return reference


def _degenerate_note(matrix: EvaluationMatrix) -> str:
    """Return the note shown when `all_bands_degenerate` holds, explaining why there are no whiskers.

    It names the price basis year and the two remedies (choose a banded year, or add bands to that year's data). Shared
    by the markdown and HTML headers.
    """
    reference = _reference_result(matrix)
    basis = reference.parameters.price_basis_year or reference.simulation_year
    return (
        f"All cost inputs resolved to exact values, so every min/best_estimate/max band is degenerate and "
        f"no uncertainty whiskers appear. Price basis year {basis} uses the 1:1-migrated legacy "
        f"data, which deliberately carries no bands (parity phase, cost_spec.md §10.1). Banded "
        f"data ships for 2026 and 2035 — set EconomicParameters.price_basis_year accordingly, "
        f"or add bands to the {basis} entries as a data PR."
    )


# ---------------------------------------------------------------------------- markdown


def build_cost_summary_markdown(
    matrix: EvaluationMatrix,
    plausibility: PlausibilityReport,
    comparison: Optional[VariantComparison] = None,
) -> str:
    """Build `cost_summary.md`, the compact, diffable text report for checking data changes (§9.5).

    Golden scenarios keep a committed copy, so a data change shows up as line-level deltas in the KPIs it moved. It
    holds the run parameters, the plausibility panel, one row per perspective, the cost structure, per-subject figures,
    subsidy decisions and, when comparing, the variant deltas.

    For diffability, rows keep the insertion order of `matrix.results` and `component_breakdowns` (except the variant
    comparison's per-subject deltas, which are ranked), rounding depends only on the value, zero-valued display groups
    are skipped, and the only run-dependent text is the generation date in the footer, which the golden test
    normalizes.

    Args:
        matrix: The evaluated perspectives. The first is the reference whose cost structure and per-subject tables are
            shown; the perspective table covers all.
        plausibility: The panel from `run_plausibility_checks`, rendered as the first table.
        comparison: Optional variant-vs-reference comparison; adds the final section.

    Returns:
        The complete markdown document, newline-terminated.

    Raises:
        ValueError: If the matrix holds no evaluated perspective.
    """
    checks = render_plausibility_findings(plausibility)
    reference = _reference_result(matrix)
    params = reference.parameters
    lines: List[str] = []
    lines.append("# Lifecycle cost summary")
    lines.append("")
    lines.append(
        f"Simulation year {reference.simulation_year}, country {params.country}, "
        f"horizon {params.observation_period_in_years} a, interest {params.interest_rate:.1%}, "
        f"price basis {params.price_basis_year}. "
        f"Monetary values as `best_estimate [min | max]` (cost_spec.md §3.9)."
    )
    lines.append("")
    if all_bands_degenerate(matrix):
        lines.append(f"> **Note:** {_degenerate_note(matrix)}")
        lines.append("")
    lines.append("## Plausibility checks")
    lines.append("")
    lines.append("| Status | Check | Value | Expected |")
    lines.append("|---|---|---|---|")
    icon = {"PASS": "OK", "WARN": "WARN(!)", "FAIL": "FAIL(!!)"}
    for check in checks:
        lines.append(f"| {icon[check.status]} | {check.name} | {check.value} | {check.expected} |")
    failed = [check for check in checks if check.status != "PASS"]
    if failed:
        lines.append("")
        for check in failed:
            if check.detail:
                lines.append(f"- **{check.name}**: {check.detail}")
    lines.append("")
    lines.append("## Perspectives")
    lines.append("")
    lines.append(
        f"| Perspective | NPV | Equivalent annual cost | Monthly (year 1) | {HeatCostNaming.COLUMN} |"
    )
    lines.append("|---|---|---|---|---|")
    for perspective_id, result in matrix.results.items():
        lines.append(
            f"| {perspective_id} | {_band_str(result.total_npv_in_euro)} "
            f"| {_band_str(result.equivalent_annual_cost_in_euro, 'EUR/a')} "
            f"| {_band_str(result.monthly_cost_year1_in_euro, 'EUR/mo')} "
            f"| {_band_str(result.levelized_cost_of_heat_in_euro_per_kwh, 'EUR/kWh')} |"
        )
    lines.append("")
    lines.append(f"## Cost structure ({reference.perspective_id})")
    lines.append("")
    lines.append("| Display group | NPV |")
    lines.append("|---|---|")
    group_npv = views.fold_categories(reference.npv_by_category, PresentationStyle.CATEGORY_TO_GROUP)
    for index in range(len(PresentationStyle.DISPLAY_GROUPS)):
        total = group_npv.get(index)
        if total is not None and (total.best_estimate or total.minimum or total.maximum):
            lines.append(f"| {group_name(index)} | {_band_str(total)} |")
    lines.append("")
    lines.append(f"## Per subject ({reference.perspective_id})")
    lines.append("")
    lines.append("| Subject | NPV | Year-0 investment | Subsidies |")
    lines.append("|---|---|---|---|")
    for subject, breakdown in reference.component_breakdowns.items():
        lines.append(
            f"| {subject} | {_band_str(breakdown.total_npv_in_euro)} "
            f"| {_band_str(breakdown.investment_gross_in_euro)} "
            f"| {_band_str(breakdown.subsidies_nominal_in_euro)} |"
        )
    decisions = _decisions_by_content(matrix)
    if decisions:
        lines.append("")
        lines.append("## Subsidy decisions")
        lines.append("")
        for decision, perspective_ids in decisions:
            # Every applied award at its total amount: a scheduled tax credit has a zero upfront
            # amount, so filtering on the upfront amount would print "applied none".
            # `views.describe_award` is the source the awards table and decision cards read too.
            applied = ", ".join(
                f"{_scheme_markdown(presentation.display_name, presentation.scheme_id)} "
                f"({_award_amount_str(presentation)}{_award_arithmetic_str(presentation)})"
                for presentation in (views.describe_award(award) for award in decision.applied)
            ) or "none"
            note = _perspectives_note(perspective_ids, matrix)
            lines.append(f"- **{decision.measure_subject}** ({note}): applied {applied}")
            for reject in decision.rejected:
                name = _scheme_markdown(reject.get("display_name"), reject["scheme_id"])
                lines.append(f"  - rejected {name}: {reject['reason']}")
            for item in decision.undetermined:
                name = _scheme_markdown(item.get("display_name"), item["scheme_id"])
                lines.append(f"  - undetermined {name} (missing: {', '.join(item['missing_fields'])})")
            if decision.undetermined_upper_bound_in_euro > 0:
                lines.append(
                    f"  - answering the open questions could unlock up to "
                    f"{_fmt(decision.undetermined_upper_bound_in_euro)} EUR"
                )
    if comparison is not None:
        lines.append("")
        lines.append(f"## Variant comparison ({comparison.perspective_id})")
        lines.append("")
        lines.append(f"- NPV delta (variant - reference): {_band_str(comparison.npv_delta_in_euro)}")
        lines.append(
            f"- Equivalent annual cost delta: {_band_str(comparison.equivalent_annual_cost_delta_in_euro, 'EUR/a')}"
        )
        payback = comparison.discounted_payback_envelope
        lines.append(
            f"- Discounted payback [a]: earliest {payback.earliest}, expected {payback.central}, "
            f"latest {payback.latest} across the three worlds (None = never within horizon)"
        )
        lines.append("")
        lines.append("| Subject | NPV delta |")
        lines.append("|---|---|")
        for subject, delta in sorted(
            comparison.npv_delta_by_subject.items(), key=lambda item: item[1].best_estimate
        ):
            lines.append(f"| {subject} | {_band_str(delta)} |")
    lines.append("")
    lines.append(
        f"_Generated {datetime.date.today().isoformat()} by hisim.economics; "
        "trace any value with `python -m hisim.economics explain`._"
    )
    return "\n".join(lines) + "\n"


def write_cost_summary(
    matrix: EvaluationMatrix,
    plausibility: PlausibilityReport,
    result_directory: str,
    comparison: Optional[VariantComparison] = None,
) -> str:
    """Write `cost_summary.md` into the result directory, as UTF-8.

    Args:
        matrix: Evaluated perspectives.
        plausibility: The panel to render at the top.
        result_directory: Directory to write into (the run's `results/`).
        comparison: Optional variant comparison section.

    Returns:
        The path written.
    """
    path = os.path.join(result_directory, ReportFileNames.COST_SUMMARY_FILE_NAME)
    with open(path, "w", encoding="utf-8") as file:
        file.write(build_cost_summary_markdown(matrix, plausibility, comparison))
    return path


# ---------------------------------------------------------------------------- subsidy decisions


def _decision_content_key(decision) -> Tuple:
    """Return a key holding everything about a subsidy decision a reader would notice.

    Perspectives with the same key are reported once. The key holds the measure and, per scheme the solver touched, its
    id, outcome and detail: an award's amount and payout, a rejection's reason, an open question's unanswered fields.
    Amounts are rounded to the cent so float noise cannot split a decision. The upfront amount is keyed beside the
    total, since an upfront grant and a scheduled tax credit of the same size are different decisions.
    """
    return (
        decision.measure_subject,
        tuple(
            (
                award.scheme_id,
                "APPLIED",
                round(award.upfront_amount.best_estimate, 2),
                round(views.award_total_amount(award).best_estimate, 2),
                award.payout_kind.value,
                tuple(slot for slot, bound in award.caps_binding_per_slot.items() if bound),
            )
            for award in decision.applied
        ),
        tuple((reject["scheme_id"], "REJECTED", reject["reason"]) for reject in decision.rejected),
        tuple(
            (item["scheme_id"], "OPEN", tuple(item["missing_fields"]))
            for item in decision.undetermined
        ),
        round(decision.undetermined_upper_bound_in_euro, 2),
    )


def _decisions_by_content(matrix: EvaluationMatrix) -> List[Tuple]:
    """Return the distinct subsidy decisions of the run, each with the perspectives that reached it.

    Perspectives can disagree about a measure (a net perspective applies what a gross one never asks for; a brownfield
    context can fail a condition a greenfield one passes), so decisions are grouped by `_decision_content_key`, not by
    measure name.

    Returns:
        `(decision, perspective_ids)` pairs in first-seen order, ids in matrix order.
    """
    grouped: Dict[Tuple, Tuple] = {}
    for perspective_id, result in matrix.results.items():
        for decision in result.subsidy_decisions:
            key = _decision_content_key(decision)
            if key in grouped:
                grouped[key][1].append(perspective_id)
            else:
                grouped[key] = (decision, [perspective_id])
    return list(grouped.values())


def _perspectives_note(perspective_ids: List[str], matrix: EvaluationMatrix) -> str:
    """Name the perspectives sharing one decision: a phrase when it is all of them, else the ids.

    "All perspectives with subsidy decisions" covers every perspective that decided something; a partial group is
    listed by id.
    """
    deciding = [
        perspective_id
        for perspective_id, result in matrix.results.items()
        if result.subsidy_decisions
    ]
    if len(perspective_ids) < len(deciding) or len(deciding) < 2:
        return ", ".join(perspective_ids)
    return "all perspectives" if len(deciding) == len(matrix.results) else (
        "all perspectives with subsidy decisions"
    )
