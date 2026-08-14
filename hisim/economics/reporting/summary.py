"""The markdown cost summary and the plausibility-panel rendering (cost_spec.md §7.2, §7.4).

`build_cost_summary_markdown`/`write_cost_summary` produce the reviewer-facing text
summary, and `render_plausibility_findings` turns the panel's findings into displayable
rows shared by the markdown, the HTML report and the bridge's log warnings. Split out of
the former single-module `reporting.py` (PR-3 review); the package `__init__` re-exports
everything, so `from hisim.economics.reporting import ...` is unchanged.
"""


from __future__ import annotations

import datetime
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from hisim.economics import views
from hisim.economics.plausibility import CheckIds, PlausibilityFinding, PlausibilityReport
from hisim.economics.presentation_style import PresentationStyle
from hisim.economics.results import EvaluationMatrix, VariantComparison
from hisim.economics.uncertainty import UncertainValue



class ReportFileNames:
    """Names of the report files written next to the results.

    Collected in one namespace because these names are part of the package's public output
    surface: `bridge.py` and the `report` CLI write them, the golden tests read them, and the
    README lists them among the files the engine adds without touching the legacy path (§10).
    They are plain names, not paths — the result directory is always supplied by the caller.
    """

    COST_SUMMARY_FILE_NAME = "cost_summary.md"
    LIFECYCLE_REPORT_FILE_NAME = "lifecycle_report.html"
    COMPARISON_REPORT_FILE_NAME = "variant_comparison_report.html"


def _fmt(value: float) -> str:
    """Compact euro formatting.

    The one place in the report where precision is chosen, and it is chosen by magnitude rather
    than fixed: cents below 100 EUR (a maintenance fee must be readable), whole euros up to
    100k, thousands above that (a 340k NPV printed to the cent is noise). This is the only
    arithmetic the seam-4 invariant leaves in this module besides SVG geometry — it rounds a
    number for display, it never derives one.

    Because it is a pure function of the value with no locale, width or context dependence, the
    same number always renders as the same string — which is what makes the golden markdown
    diffable at all. The flip side a reviewer should know: it discards precision, so two figures
    that differ below the printed digit are indistinguishable in the report, and the exports
    (`lifecycle_costs.json`, `cash_flow_timeline.csv`) carry the full values.
    """
    if abs(value) >= 100000:
        return f"{value / 1000:,.0f}k"
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    return f"{value:,.2f}" if abs(value) < 100 else f"{value:,.0f}"


def _band_str(band: Optional[UncertainValue], unit: str = "EUR") -> str:
    """`best_estimate [min | max] unit` rendering of a band.

    The house format for every monetary figure in both reports, so a reader learns to read one
    shape and knows that the bracketed pair is the §3.9 LOW/HIGH *envelope* (the coherent
    cheap and expensive worlds), not a confidence interval. An exact band collapses to a single
    figure rather than repeating itself three times, which is why a run on the 1:1-migrated
    legacy data reads as ordinary numbers — see `all_bands_degenerate`, which explains that
    absence to the reader instead of leaving it looking like a bug.

    A None band renders as `-`; the `unit` is appended verbatim and is the caller's way of
    distinguishing EUR from EUR/a, EUR/mo and EUR/kWh, which is otherwise the easiest thing to
    misread in a table of similar-looking numbers.
    """
    if band is None:
        return "-"
    if band.is_exact():
        return f"{_fmt(band.best_estimate)} {unit}"
    return f"{_fmt(band.best_estimate)} [{_fmt(band.minimum)} | {_fmt(band.maximum)}] {unit}"


# ---------------------------------------------------------------------------- plausibility (B)

@dataclass
class PlausibilityCheck:
    """One check **rendered** for the panel: the display row, all strings.

    The check itself is a `plausibility.PlausibilityFinding` produced engine-side (W4.2);
    this is only its presentation. Kept as a small record because both the markdown table and
    the HTML panel print the same four columns.
    """

    name: str
    status: str  # PASS | WARN | FAIL
    value: str
    expected: str
    detail: str = ""


#: Reader hints per check kind — prose, so it lives with the presentation. Checks whose hint
#: quotes numbers read them off the finding's `context`; see `_finding_detail`.
class _CheckHints:
    """Reader hints shown next to a flagged check, keyed by check id.

    A check can say a number is out of range; only prose can say what usually causes that, and
    "usually caused by" is editorial judgement rather than engine output, so it lives on the
    presentation side. Keyed by `CheckIds` so a check can be re-worded or re-scoped without
    orphaning its hint. Hints that need to quote figures cannot live in this static map and are
    built in `_finding_detail` from the finding's `context`; a check with no hint renders an
    empty note cell.
    """

    BY_CHECK_ID: Dict[str, str] = {
        CheckIds.CHECK_MAINTENANCE_RATIO: "a huge ratio usually means an absolute fee stored as a rate (issues #1)",
        CheckIds.CHECK_FLEXIBILITY_VALUE: (
            "the load was timed worse than a flat profile; the projection used 0 instead "
            "(controller signal or price series inverted?)"
        ),
    }


def _finding_detail(finding: PlausibilityFinding) -> str:
    """The reader hint shown next to a flagged check.

    Turns a finding into the "Note" column of the panel: the two checks whose hint is only
    useful with numbers in it (the effective price, which wants the bill and the quantity it
    came from; the band width, which wants the horizon) are formatted from the finding's
    `context` map, everything else falls back to the static hint in `_CheckHints`. The context
    keys read here are fixed per check id and documented at the producing call site in
    `plausibility.py`, which is why the lookups are unguarded — a missing key means the engine
    changed a check's contract without updating its renderer.
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


def _render_finding(finding: PlausibilityFinding) -> PlausibilityCheck:
    """Formats one typed finding into its panel row (no arithmetic beyond rounding).

    Each check kind has its own idea of what "value" and "expected" mean — a reconciliation
    reports a delta against zero, a band-ordering check has no single value at all and prints
    the band it rejected, a range check prints a figure against its bounds — so this dispatches
    on `check_id` rather than trying to format all findings uniformly. The dispatch is
    deliberately explicit and falls through to the generic range rendering, so a check kind
    added engine-side still renders sensibly instead of raising.

    The catch-all branch switches precision at 100 for the same reason `_fmt` does: a ratio of
    0.043 and an EAC of 12,400 EUR/m2a cannot share a format.
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
    low, high = finding.bounds if finding.bounds else (0.0, 0.0)
    return PlausibilityCheck(
        name=finding.name,
        status=finding.status,
        value=f"{value:,.3f} {finding.unit}" if abs(value) < 100 else f"{value:,.0f} {finding.unit}",
        expected=f"{low:g} - {high:g} {finding.unit}",
        detail=detail,
    )


def render_plausibility_findings(report: PlausibilityReport) -> List[PlausibilityCheck]:
    """The panel rows of a plausibility report, in report order.

    The rendering half of section 0, and the module's public entry point for it: both report
    formats and `bridge.py`'s log warnings go through here, so the panel a reader sees in the
    HTML, the table in `cost_summary.md` and the lines in the simulation log are the same rows
    with the same wording. Order is preserved exactly as `run_plausibility_checks` produced it
    (structural findings first, then magnitudes), which is what makes the golden panel stable.
    """
    return [_render_finding(finding) for finding in report.findings]


def all_bands_degenerate(matrix: EvaluationMatrix) -> bool:
    """True when every result band is exact (min = best_estimate = max).

    That is the expected state when the price basis year resolves to the 1:1-migrated legacy
    data (deliberately degenerate for parity, §10.1 Phase 1); banded AI-estimate data ships
    for 2026 and 2035. The reports surface this so missing whiskers read as a data property,
    not a bug.
    """
    return all(result.total_npv_in_euro.is_exact() for result in matrix.results.values())


def _degenerate_note(matrix: EvaluationMatrix) -> str:
    """The prose shown when `all_bands_degenerate` holds: why there are no whiskers.

    An absent uncertainty band looks like a broken feature, so both reports lead with an
    explanation instead: it is a property of the price basis year's data, and the note names the
    year in question and the two ways out (pick a banded basis year, or add bands to that year's
    entries as a data PR). Shared by the markdown and HTML headers so the two cannot drift.
    """
    reference = next(iter(matrix.results.values()))
    basis = reference.parameters.price_basis_year or reference.simulation_year
    return (
        f"All cost inputs resolved to exact values, so every min/best_estimate/max band is degenerate and "
        f"no uncertainty whiskers appear. Price basis year {basis} uses the 1:1-migrated legacy "
        f"data, which deliberately carries no bands (parity phase, cost_spec.md §10.1). Banded "
        f"data ships for 2026 and 2035 — set EconomicParameters.price_basis_year accordingly, "
        f"or add bands to the {basis} entries as a data PR."
    )


# ---------------------------------------------------------------------------- markdown (C)

def build_cost_summary_markdown(
    matrix: EvaluationMatrix,
    plausibility: PlausibilityReport,
    comparison: Optional[VariantComparison] = None,
) -> str:
    """`cost_summary.md`: compact, greppable, git-diffable (the §9.5 review workflow).

    The text sibling of the HTML report, and the artefact the data-review workflow actually runs
    on: golden scenarios keep a committed copy, so a PR that changes a price entry, a lifetime
    or a subsidy scheme shows up as an explicit line-level delta in the KPIs it moved instead of
    as silent drift (§9.5). Everything a reviewer needs to judge that delta is here — the run's
    parameters, the plausibility panel, one row per perspective, the cost structure and the
    per-subject figures, the subsidy decisions and, when comparing, the variant deltas.

    Diffability is what dictates the formatting choices below, which otherwise look arbitrary:
    rows follow the insertion order of `matrix.results` and `component_breakdowns` rather than
    being sorted by value, since a value-sorted table reorders itself whenever a number moves,
    turning a one-line change into a whole-table diff (the deliberate exception is the variant
    comparison's per-subject deltas, where the ranking *is* the message); figures go through
    `_fmt`/`_band_str`, whose
    rounding is a pure function of the value; zero-valued display groups are skipped so an
    unused category never appears and disappears; and the only run-dependent text is the
    generation date in the footer, which the golden test normalizes. Adding a timestamp, a
    duration or a path here would make every diff dirty.

    Args:
        matrix: The evaluated perspectives. Its first entry is the "reference" perspective whose
            cost structure and per-subject tables are shown; the perspective table covers all.
        plausibility: The panel from `run_plausibility_checks`, rendered as the first table.
        comparison: Optional variant-vs-reference comparison; adds the final section.

    Returns:
        The complete markdown document, newline-terminated.
    """
    checks = render_plausibility_findings(plausibility)
    reference = next(iter(matrix.results.values()))
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
    lines.append("| Perspective | NPV | Equivalent annual cost | Monthly (year 1) | LCOH |")
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
    for index, (group_name, _categories) in enumerate(PresentationStyle.DISPLAY_GROUPS):
        total = group_npv.get(index)
        if total is not None and (total.best_estimate or total.minimum or total.maximum):
            lines.append(f"| {group_name} | {_band_str(total)} |")
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
            applied = ", ".join(
                f"{award.scheme_id} ({_band_str(award.upfront_amount)})"
                for award in decision.applied
                if award.upfront_amount.maximum
            ) or "none"
            note = _perspectives_note(perspective_ids, matrix)
            lines.append(f"- **{decision.measure_subject}** ({note}): applied {applied}")
            for reject in decision.rejected:
                lines.append(f"  - rejected {reject['scheme_id']}: {reject['reason']}")
            for item in decision.undetermined:
                lines.append(f"  - undetermined {item['scheme_id']} (missing: {', '.join(item['missing_fields'])})")
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
        payback = comparison.discounted_payback_years
        lines.append(
            f"- Discounted payback [a]: best {payback.get('low')}, expected {payback.get('best_estimate')}, "
            f"worst {payback.get('high')} (None = never within horizon)"
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
    """Writes cost_summary.md.

    The thin filesystem wrapper around `build_cost_summary_markdown`: rendering and writing are
    separate so tests and the golden oracle can compare the document without a directory, while
    `bridge.py` and the `report` CLI get a one-call side effect. UTF-8 is explicit because the
    document contains non-ASCII text and the postprocessing may run under any locale.

    Args:
        matrix: Evaluated perspectives.
        plausibility: The panel to render at the top.
        result_directory: Directory to write into (the run's `results/`).
        comparison: Optional variant comparison section.

    Returns:
        The path written, for logging and for the caller's list of produced files.
    """
    path = os.path.join(result_directory, ReportFileNames.COST_SUMMARY_FILE_NAME)
    with open(path, "w", encoding="utf-8") as file:
        file.write(build_cost_summary_markdown(matrix, plausibility, comparison))
    return path


# ---------------------------------------------------------------------------- SVG helpers (A)
#
# Everything below draws charts by emitting SVG source directly. That is not a stylistic
# preference: the report must survive being mailed around, opened from a network share and
# archived next to the results, so it may not load a single external asset — no charting
# library, no web font, no image file. Hand-written inline SVG is the only way to get real
# charts under that rule, and it buys two further properties the report depends on: marks can
# be coloured with the CSS custom properties defined in `_ReportCss` (so charts follow the
# reader's light/dark theme, which a rasterized chart cannot), and a `<title>` child gives every
# mark a native browser tooltip with no JavaScript at all.
#
# **The coordinate convention**, which the chart builders assume everywhere and never restate:
# SVG user units are pixels of the `viewBox`, x grows right and **y grows downward** from the
# top-left origin. So a taller bar has a *smaller* y, a value axis is inverted relative to
# intuition, and every chart below computes a baseline y and subtracts. The `<text>` y is the
# glyph baseline, not the top of the text, which is why label positions carry a `+ 4`-ish
# nudge to sit optically centred on a row. Each chart derives its own `scale` (user units per
# euro) from the widest or tallest value it has to fit, guarded with a `1e-9` floor so an
# all-zero series cannot divide by zero.


def _decision_content_key(decision) -> Tuple:
    """Everything about a decision a reader of the report would notice.

    Two perspectives that reached the same conclusion about a measure should be reported once;
    two that reached different conclusions must both be reported. This key is where that line is
    drawn: the measure, and for every scheme the solver touched its id, its outcome and the detail
    that outcome carries — the amount and payout of an award, the reason of a rejection, the
    unanswered fields of an open question. Amounts are rounded to the cent so that float noise in
    the last digits cannot split one decision into two.

    Deliberately renderer-independent: the awards table shows an award's total while the decision
    card shows its upfront amount, and both are in the key, so the two renderings group the same
    perspectives and cannot disagree about which decisions are "the same".
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
    """The distinct subsidy decisions of the run, each with the perspectives that reached it.

    Perspectives differ in subsidy mode and installation context, so they do not have to agree
    about a measure — a net perspective applies what a gross one never asks for, and a brownfield
    context can fail an eligibility condition a greenfield one passes. Grouping by
    `_decision_content_key` rather than by measure name is what lets the report show every distinct
    outcome: the previous de-duplication kept the first perspective's decision per measure and
    dropped the rest unseen, which is at its worst exactly when it matters, a measure whose support
    depends on the view taken.

    Returns:
        `(decision, perspective_ids)` pairs in first-seen order, with the ids in matrix order.
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
    """How to name the perspectives sharing one decision, without listing five ids every time.

    The common case by far is that every perspective which decided anything decided the same
    thing, and spelling all of them out on each card would bury the case worth noticing — a
    decision only some perspectives reached. So a group covering all of them collapses to a phrase,
    and only a partial group is enumerated. The two phrasings are kept apart because "all
    perspectives" would be untrue on a run where a gross perspective produced no decision at all.
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
