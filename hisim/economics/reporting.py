"""Human-readable lifecycle cost reports (postprocessing option LIFECYCLE_COST_REPORT).

Follows the money along the calculation chain so results can be checked for plausibility:

1. plausibility panel (automated checks, thresholds in cost_database/plausibility_checks.json)
2. input audit (facts x database resolution)
3. investment build-up waterfalls (year 0)
4. annual cash-flow timeline + cumulative discounted cost
5. year-1 energy bill decomposition with implied effective prices
6. subsidy decision cards
7. perspective overview (EAC bands)
8. per-component breakdowns
9. variant comparison (delta waterfall by subject + discounted payback curve)

Outputs: `cost_summary.md` (diffable text), `lifecycle_report.html` (self-contained, inline
SVG, light/dark aware) and matplotlib PNGs (see `report_plots.py`).

**Who this is for.** The reader is a domain expert reviewing a run they did not produce, so the
report is organized as a chain of falsifiable questions rather than as a dashboard: each section
is placed where a mistake made upstream of it first becomes visible, and every chart is paired
with the table it was drawn from. Sections carry **mnemonic names, not numbers** (rule 2.8,
owner decision Q18) — the report used to interleave a legacy 0-to-10 scheme with the chart set's
V-numbers, and neither ran monotonically down the page; `ReportSections` holds the ratified names
and the order, and a table of contents at the top of the document provides the navigation the
numbering was supposed to. Each `_*_section_html` function below documents the question its
section exists to answer. The most load-bearing of them is the energy bill: an effective price of
300 EUR/kWh or 0.0003 EUR/kWh is the fastest detector of a unit mix-up anywhere between the meter
and the tariff, and it needs no domain knowledge to spot.

**Every section explains itself.** Under its heading, each section carries the same four parts
before anything else: what the chart *shows*, what it *adds* that no other section covers, a
disclosure defining every term of art it uses, and a disclosure explaining how its numbers are
calculated (`_explanation_html`). The report opens with the primer that states the three
conventions the rest leans on — discounting, the three worlds behind every band, and the sign
rule. The text of all of it lives in `report_prose.py`, authored and owner-reviewed as prose and
deliberately free of run-specific numbers; the run-specific half stays here, in the captions and
annotations each section emits around its chart, which is what keeps the prose golden-stable.

**Two outputs, two jobs.** The HTML is for reading a single run and is allowed to be rich
(collapsible details, tooltips, per-perspective tabs). The markdown summary is for *diffing*
runs: it is committed against golden scenarios so that a PR touching price data shows up as a
clean textual delta rather than as silent drift (§9.5). That is a real constraint on the
emitters below, not a style preference — the markdown carries fixed rounding, fixed row order,
one table per topic, and no run-dependent decoration, so that any line that moves moved because
a number moved. `tests/test_economics_report_goldens.py` byte-compares both outputs, normalizing
only the generation date.

**This module never computes** (cost-spec-v2 §2.4, the seam-4 invariant). Every displayed
number is read off the result object or off `views.py`; the only arithmetic left here is SVG
geometry — scales, bar heights, pixel coordinates — and the rounding inside `_fmt`. Discounting,
aggregation, category folding and the business rules that used to hide in chart helpers all live
engine-side. `tests/test_economics_import_lint.py` enforces the import half of that contract;
`tests/test_economics_report_goldens.py` pins the rendered output.
"""

from __future__ import annotations

import datetime
import html
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from hisim import log
from hisim.economics import views
from hisim.economics.input_audit import InputAuditReport, OriginKind, ResolvedInputRow
from hisim.economics.plausibility import CheckIds, PlausibilityFinding, PlausibilityReport
from hisim.economics.presentation_style import (
    PresentationStyle,
    SankeyLayout,
    group_of,
    sankey_node_boxes,
    squarified_layout,
)
from hisim.economics.report_prose import ReportProse
from hisim.economics.results import EvaluationMatrix, HeatCostNaming, LifecycleCostResult, VariantComparison
from hisim.economics.timeline import Actor, CostCategory
from hisim.economics.uncertainty import Slot, UncertainValue


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
    """`avg [min | max] unit` rendering of a band.

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
        return f"{_fmt(band.average)} {unit}"
    return f"{_fmt(band.average)} [{_fmt(band.minimum)} | {_fmt(band.maximum)}] {unit}"


def _award_amount_str(presentation: "views.AwardPresentation") -> str:
    """What one applied award is worth, in one phrase — band, terms, or both.

    The single rendering of `views.describe_award` shared by the markdown decision list, the HTML
    decision cards and the awards table, so the three cannot say different things about the same
    award again. An award with a euro amount reads as the house band format, followed by its
    payout note when the payout is not a plain year-0 grant ("tax credit paid over 3 years"); an
    award that carries no euro amount at all — loan terms, an operational rate, a VAT reduction —
    reads as its terms alone, because a "0 EUR" would be a false statement about an applied award
    whose value is booked by the financing or energy calculators.
    """
    if presentation.total_in_euro is None:
        return presentation.payout_note or presentation.payout_kind
    band = _band_str(presentation.total_in_euro)
    return f"{band}, {presentation.payout_note}" if presentation.payout_note else band


def _award_arithmetic_str(presentation: "views.AwardPresentation") -> str:
    """The award's own arithmetic and cap verdict, as one appended phrase (Q26 F8).

    An award amount that states no rate and no basis cannot be checked, and the cap verdict is the
    difference between "spending more would earn more" and "this measure has hit its ceiling" —
    the two conclusions a reader draws a plan from. Both come from the solver's recorded decision
    data via `views.describe_award`; this only joins them.

    Returns the empty string for a form that states no rate and declares no cap, which is where
    `payout_note` already carries the form's own terms.
    """
    parts = [part for part in (presentation.arithmetic, presentation.cap_verdict) if part]
    return f" ({'; '.join(parts)})" if parts else ""


def _scheme_html(display_name: Optional[str], scheme_id: str) -> str:
    """A subsidy scheme named for a human, with its raw id in the tooltip (owner decision Q20).

    The report used to print `DE_BEG_EM_HP_SPEED_2024` wherever a scheme appears, which is a
    database key, not a name: a reader could not tell a speed bonus from an income bonus without
    opening the catalog. The friendly name is now the visible text and the id moves into the
    `title` attribute, where it stays available to the one reader who needs it — the reviewer
    grepping `cost_audit.csv` or the catalog for that exact string.

    Args:
        display_name: The catalog's friendly name, or None/empty when it declared none.
        scheme_id: The raw id, always shown as the tooltip and used as the visible text when
            there is no friendly name (so an older catalog degrades to the previous behaviour).

    Returns:
        An escaped `<span>` with the name as text and the id as its tooltip.
    """
    name = display_name or scheme_id
    return f"<span title=\"{_esc(scheme_id)}\">{_esc(name)}</span>"


def _scheme_markdown(display_name: Optional[str], scheme_id: str) -> str:
    """The same thing for `cost_summary.md`, where a tooltip does not exist (Q20).

    Markdown has no hover, so the id follows the name in a trailing parenthesis instead. When the
    catalog declares no friendly name the id stands alone rather than being repeated twice.
    """
    if not display_name or display_name == scheme_id:
        return scheme_id
    return f"{display_name} ({scheme_id})"


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
        CheckIds.CHECK_MAINTENANCE_RATIO: "a huge ratio usually means an absolute fee stored as a rate",
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
        # Q27 R3: the note named only the defect. A wide band is at least as often the honest
        # shape of the inputs, and saying so keeps the check from reading as an accusation.
        return (
            f"over {int(finding.context['horizon_in_years'])} years; very wide bands can be a "
            "band typo in the data, or genuinely uncertain inputs (broad price bands, partial "
            "anyway shares) — check the uncertainty-drivers section for which subjects carry it"
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
        return PlausibilityCheck(finding.name, finding.status, str(finding.band), "min<=avg<=max", detail)
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

    The rendering half of the plausibility panel, and the module's public entry point for it: both report
    formats and `bridge.py`'s log warnings go through here, so the panel a reader sees in the
    HTML, the table in `cost_summary.md` and the lines in the simulation log are the same rows
    with the same wording. Order is preserved exactly as `run_plausibility_checks` produced it
    (structural findings first, then magnitudes), which is what makes the golden panel stable.
    """
    return [_render_finding(finding) for finding in report.findings]


def all_bands_degenerate(matrix: EvaluationMatrix) -> bool:
    """True when every result band is exact (min = avg = max).

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
        f"All cost inputs resolved to exact values, so every min/avg/max band is degenerate and "
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
        f"Monetary values as `avg [min | max]` (cost_spec.md §3.9)."
    )
    lines.append("")
    if all_bands_degenerate(matrix):
        lines.append(f"> **Note:** {_degenerate_note(matrix)}")
        lines.append("")
    lines.append(f"## {ReportSections.PLAUSIBILITY[1]}")
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
    lines.append(f"## {ReportSections.PERSPECTIVES[1]}")
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
    lines.append(f"## {ReportSections.COST_STRUCTURE[1]} ({reference.perspective_id})")
    lines.append("")
    lines.append("| Display group | NPV |")
    lines.append("|---|---|")
    group_npv = views.fold_categories(reference.npv_by_category, PresentationStyle.CATEGORY_TO_GROUP)
    for index, (group_name, _categories) in enumerate(PresentationStyle.DISPLAY_GROUPS):
        total = group_npv.get(index)
        if total is not None and (total.average or total.minimum or total.maximum):
            lines.append(f"| {group_name} | {_band_str(total)} |")
    lines.append("")
    lines.append(f"## {ReportSections.COMPONENT_BREAKDOWN[1]} ({reference.perspective_id})")
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
        lines.append(f"## {ReportSections.SUBSIDIES[1]}")
        lines.append("")
        for decision, perspective_ids in decisions:
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
        lines.append(f"## {ReportSections.COMPARISON[1]} ({comparison.perspective_id})")
        lines.append("")
        lines.append(f"- NPV delta (variant - reference): {_band_str(comparison.npv_delta_in_euro)}")
        lines.append(
            f"- Equivalent annual cost delta: {_band_str(comparison.equivalent_annual_cost_delta_in_euro, 'EUR/a')}"
        )
        payback = comparison.discounted_payback_years
        lines.append(
            f"- Discounted payback [a]: best {payback.get('low')}, expected {payback.get('average')}, "
            f"worst {payback.get('high')} (None = never within horizon)"
        )
        lines.append("")
        lines.append("| Subject | NPV delta |")
        lines.append("|---|---|")
        for subject, delta in sorted(
            comparison.npv_delta_by_subject.items(), key=lambda item: item[1].average
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

class _ReportStyle:
    """Inline style constants of the self-contained HTML/SVG output.

    SVG text does not inherit the document's CSS font stack, so every `<text>` element has to
    carry its own `font-family`; keeping that string here means the charts and the surrounding
    HTML stay in one typeface. Colours are deliberately *not* here — they are CSS custom
    properties resolved at render time (`var(--g0)`, `var(--ink-1)`), which is how the inline
    charts follow light/dark mode.
    """

    SVG_FONT = 'font-family="system-ui, -apple-system, Segoe UI, sans-serif"'


class ReportSections:
    """The mnemonic identity of every report section (rule 2.8, owner decision Q18).

    One `(anchor, name)` pair per section, and the order they appear on the page. The report used
    to interleave two numbering schemes — legacy sections 0 to 10 with 4b and 6b wedged in, plus
    the V-numbers of the chart set — neither of which ran monotonically down the document, so a
    reader could not use either to navigate. Names replace both; the V-numbers survive only as
    spec-internal identifiers, the way the decision log's D-numbers do.

    `ORDER` is what the table of contents iterates. It is a superset: a section that has nothing
    to show returns an empty string and then appears in neither the page nor the contents, which
    is why the contents are built from the rendered sections rather than from this list alone.

    `HOW_TO_READ` is the one section that carries no chart and no number: the primer that states
    the three conventions — discounting, the three worlds of the min/max band, and the sign rule —
    every other section then leans on. It is a section like the others rather than a preamble
    glued to the header so that it has an anchor, a contents entry and an explanation block built
    the same way as everywhere else.
    """

    HOW_TO_READ = ("how-to-read", ReportProse.PRIMER_SECTION_NAME)
    AT_A_GLANCE = ("at-a-glance", "At a glance")
    PLAUSIBILITY = ("plausibility", "Plausibility")
    INPUT_AUDIT = ("input-audit", "Input audit")
    ASSUMPTIONS = ("assumptions", "Assumptions")
    INVESTMENT_BUILD_UP = ("investment-build-up", "Investment build-up")
    FUNDING = ("funding", "Funding")
    LIFETIMES = ("lifetimes", "Lifetimes")
    CASH_FLOW_TIMELINE = ("cash-flow-timeline", "Cash-flow timeline")
    CASH_CURVE = ("cash-curve", "Cash curve")
    LOAN = ("loan", "Loan")
    COST_OF_CREDIT = ("cost-of-credit", "Cost of credit")
    ENERGY_BILL = ("energy-bill", "Energy bill")
    ENERGY_BALANCE = ("energy-balance", "Energy balance")
    CO2 = ("co2", "CO2")
    SUBSIDIES = ("subsidies", "Subsidies")
    PERSPECTIVES = ("perspectives", "Perspectives")
    LANDLORD_STATEMENT = ("landlord-statement", "Landlord statement")
    OWNER_STATEMENT = ("owner-statement", "Owner statement")
    TENANT_STATEMENT = ("tenant-statement", "Tenant statement")
    SOCIETY_STATEMENT = ("society-statement", "Society statement")
    WHO_PAYS_WHAT = ("who-pays-what", "Who pays what")
    WHO_PAYS_WHOM = ("who-pays-whom", "Who pays whom")
    UNCERTAINTY_DRIVERS = ("uncertainty-drivers", "Uncertainty drivers")
    COMPONENT_BREAKDOWN = ("component-breakdown", "Component breakdown")
    COST_STRUCTURE = ("cost-structure", "Cost structure")
    COST_SHAPES = ("cost-shapes", "Cost shapes")
    EQUITY_BUILD_UP = ("equity-build-up", "Equity build-up")
    SCENARIOS = ("scenarios", "Scenarios")
    MONTHLY_BURDEN = ("monthly-burden", "Monthly burden")
    KPIS = ("kpis", "KPIs")
    COMPARISON = ("comparison", "Comparison")
    NPV_BRIDGE = ("npv-bridge", "NPV bridge")
    BANK_BENCHMARK = ("bank-benchmark", "Bank benchmark")
    #: The one chart that lives with the audit outputs rather than in the report (Q9).
    LEDGER_HEATMAP = ("ledger-heatmap", "Ledger heatmap")

    ORDER = [
        HOW_TO_READ, AT_A_GLANCE, PLAUSIBILITY, INPUT_AUDIT, ASSUMPTIONS, INVESTMENT_BUILD_UP,
        FUNDING, LIFETIMES,
        CASH_FLOW_TIMELINE, CASH_CURVE, LOAN, COST_OF_CREDIT, ENERGY_BILL, ENERGY_BALANCE, CO2,
        SUBSIDIES, PERSPECTIVES, OWNER_STATEMENT, LANDLORD_STATEMENT, TENANT_STATEMENT,
        SOCIETY_STATEMENT, WHO_PAYS_WHAT, WHO_PAYS_WHOM,
        UNCERTAINTY_DRIVERS,
        COMPONENT_BREAKDOWN, COST_STRUCTURE, COST_SHAPES, EQUITY_BUILD_UP, SCENARIOS,
        MONTHLY_BURDEN, KPIS, COMPARISON, NPV_BRIDGE, BANK_BENCHMARK,
    ]


class ReportChapters:
    """The chapters the report is organized into (owner decision Q24, rule 2.8).

    The report used to be one flat sequence of sections that told three stories at once: a
    perspective-free part about what the technology costs, an owner-occupier part, a
    landlord/tenant part and a macroeconomic part, interleaved, with each section picking a
    perspective of its own. A reader following it end to end therefore switched stories several
    times per page without being told. The chapters make the switch explicit: a common part on the
    gross basis, then one chapter per question a reader actually has.

    A perspective-scoped section consequently renders **once per chapter**, on that chapter's own
    perspectives, so the same section name legitimately appears more than once in a document — and
    that is why anchors are chapter-prefixed (`owner-cash-curve`) and the contents are two-level.
    `COMPARISON` is deliberately not one of the three stories: it is the fourth block, present only
    when a reference variant exists, and it carries no authored intro because it answers a
    question about two runs rather than about one party.
    """

    THE_BUILDING = ("building", "The building")
    OWNER_OCCUPIED = ("owner", "Owner-occupied")
    RENTED_OUT = ("rented", "Rented out")
    SOCIETY = ("society", "Society")
    COMPARISON = ("vs-reference", "Comparison with the reference")

    #: The common part plus the three story chapters, in page order; each has an authored intro.
    STORY_ORDER = [THE_BUILDING, OWNER_OCCUPIED, RENTED_OUT, SOCIETY]
    ORDER = STORY_ORDER + [COMPARISON]
    #: Chapters that carry no authored lead-in — see the class docstring.
    WITHOUT_INTRO = (COMPARISON,)


@dataclass
class _ChapterContext:
    """Which chapter a section is being rendered into, and what has already been explained.

    Two jobs, both of which exist only because a section can now appear more than once. It makes
    the section's anchor unique by prefixing it with the chapter's own anchor, and it remembers
    where each section name's four-part explanation was rendered *first*, so the second and third
    occurrence link back to it instead of repeating a page of prose. Tripling the explanation
    weight of the report was the obvious failure mode of the chapter restructure: the prose is the
    longest part of most sections, and a reader who has just read it does not want it again three
    screens further down.

    `first_explained` is shared between the contexts of all chapters — it is the document's
    memory, not the chapter's — which is why this is a mutable object handed around rather than a
    value each chapter builds for itself.
    """

    chapter: Tuple[str, str]
    #: section name -> (anchor, chapter name) of the occurrence that carries the full explanation.
    first_explained: Dict[str, Tuple[str, str]] = field(default_factory=dict)

    def for_chapter(self, chapter: Tuple[str, str]) -> "_ChapterContext":
        """A context for another chapter, sharing this one's explanation memory."""
        return _ChapterContext(chapter=chapter, first_explained=self.first_explained)

    def anchor_of(self, section: Tuple[str, str]) -> str:
        """The chapter-prefixed anchor of a section in this chapter (`owner-cash-curve`)."""
        return f"{self.chapter[0]}-{section[0]}"


def _section_open(section: Tuple[str, str], context: _ChapterContext, subtitle: str = "") -> str:
    """Opening tag and heading of a report section, with its chapter-prefixed anchor.

    The single place a section's name reaches the page, so the heading, the anchor a table-of-
    contents link points at and the name a cross-reference in some other section's prose can
    never drift apart. `subtitle` is the perspective id (or any qualifier) the section applies
    to, shown in brackets after the name and escaped here so callers do not have to.

    The heading carries its chapter (Q24) because the same section name now appears in more than
    one of them: "Cash curve" alone would not tell a reader arriving by a contents link whether
    they are looking at the owner's liquidity or the landlord's. Sections are `<h3>` under the
    chapter's `<h2>`, so the document outline is the chapter structure.
    """
    _anchor, name = section
    tail = f" ({_esc(subtitle)})" if subtitle else ""
    chapter = f" <span class='chapter-tag'>{_esc(context.chapter[1])}</span>"
    return f"<section id=\"{context.anchor_of(section)}\"><h3>{_esc(name)}{tail}{chapter}</h3>"


def _chapter_open(chapter: Tuple[str, str]) -> str:
    """A chapter heading with its authored lead-in (Q24), or without one for the fourth block.

    Chapters are headings between the section cards rather than cards themselves: a chapter is not
    a thing to read, it is the answer to "which of the three questions do the next few sections
    answer". The lead-in comes from `ReportProse.CHAPTER_INTROS` verbatim, like every other word
    of explanation in the report.
    """
    anchor, name = chapter
    intro = (
        "" if chapter in ReportChapters.WITHOUT_INTRO
        else f"<p class='sub chapter-intro'>{ReportProse.to_html(ReportProse.for_chapter(name))}</p>"
    )
    return f"<h2 class='chapter' id=\"{anchor}\">{_esc(name)}</h2>{intro}"


def _explanation_html(section: Tuple[str, str], context: _ChapterContext) -> str:
    """The authored four-part explanation of one section — once per section name (Q24).

    Every section of the report opens the same way, so a reader who arrives at any of them by
    following a contents link is never missing context: two visible paragraphs — what the chart
    *shows* and what it *adds* that nothing else covers — then two collapsed disclosures, the
    definition list of every term of art the section uses and the account of how its numbers are
    calculated. The disclosures are collapsed because the report is read twice: once by someone
    who knows the vocabulary and wants the charts, once by someone who does not and needs the
    definitions to be one click away rather than in a glossary at the other end of the document.

    Since the chapter restructure a section name can appear in three chapters. The explanation is
    rendered at its **first** occurrence and every later one links back to it: the prose is the
    longest part of most sections, and printing "Cash curve" three times in full would triple the
    weight of the report for a reader who has just read it. The back-link names the chapter it
    points at, because "see above" is useless in a document this long.

    The text itself lives in `ReportProse` and is never assembled here — this function only makes
    HTML of it, so an editorial change is a change to one file of prose and the rendering cannot
    quietly reword anything. The two summary strings come from the same place for the same
    reason: `tests/test_economics_reporting.py` asserts on them section by section.

    Args:
        section: The `(anchor, name)` pair of `ReportSections`; the name is the prose key.
        context: The chapter being rendered; **mutated** — it records this section name as
            explained, so a later chapter links here instead of repeating the text.

    Returns:
        The two paragraphs and the two `<details>` blocks at the first occurrence, a one-line
        cross-reference paragraph at every later one.
    """
    name = section[1]
    already = context.first_explained.get(name)
    if already is not None:
        anchor, chapter_name = already
        return (
            "<p class='sub'>The same chart, read the same way: see the explanation under "
            f"<a href=\"#{anchor}\">{_esc(name)} &mdash; {_esc(chapter_name)}</a>.</p>"
        )
    context.first_explained[name] = (context.anchor_of(section), context.chapter[1])
    prose = ReportProse.for_section(name)
    terms = "".join(
        f"<dt><em>{ReportProse.to_html(term)}</em></dt>"
        f"<dd>{ReportProse.to_html(definition)}</dd>"
        for term, definition in prose.terms
    )
    calculation = "".join(
        f"<p class='sub'>{ReportProse.to_html(paragraph)}</p>" for paragraph in prose.calculation
    )
    return (
        f"<p class='sub'>{ReportProse.to_html(prose.shows)}</p>"
        f"<p class='sub'>{ReportProse.to_html(prose.adds)}</p>"
        + _details(ReportProse.TERMS_SUMMARY, f"<dl>{terms}</dl>")
        + _details(ReportProse.CALCULATION_SUMMARY, calculation)
    )


def _how_to_read_section_html(context: _ChapterContext) -> str:
    """The primer: the three conventions every other section assumes the reader knows.

    Discounting, the three complete worlds behind every `avg [min | max]` band, and the sign rule
    that keeps costs and credits apart are stated once, at the top of the page, so no section has
    to re-derive them next to its own chart. It carries no number and no chart, which is why it is
    the one section built from nothing but its heading and its explanation block — and why it
    always renders: there is no input that could make it empty.
    """
    return _section_open(ReportSections.HOW_TO_READ, context) + _explanation_html(
        ReportSections.HOW_TO_READ, context
    ) + "</section>"


def _table_of_contents_html(document: str) -> str:
    """Two-level contents: every chapter that rendered, with the sections inside it (Q24).

    Navigation is the whole reason the numbering existed, so removing the numbers means providing
    it properly. Both levels are built by asking the rendered document whether an anchor is in it,
    which means a section that skipped itself (no loans, no comparison, a degenerate band) and a
    chapter whose perspectives were absent are missing from the contents for free — an empty entry
    pointing at nothing would be worse than no numbering at all.

    The nesting is what makes the same section name appearing three times readable: "Cash curve"
    under Owner-occupied, under Rented out and under Society are three different charts of three
    different parties, and a flat list would show them as three identical words.
    """
    parts = ["<nav class='sub' style='margin:0 0 18px 0;line-height:1.9'>"]
    for chapter_anchor, chapter_name in ReportChapters.ORDER:
        # Sorted by where the section actually is on the page, not by `ReportSections.ORDER`: a
        # chapter arranges its sections in the order its story is told, and a contents list that
        # disagreed with the page would send a reader looking in the wrong place.
        present = [
            (document.index(f'id="{chapter_anchor}-{anchor}"'), anchor, name)
            for anchor, name in ReportSections.ORDER
            if f'id="{chapter_anchor}-{anchor}"' in document
        ]
        links = [
            f"<a href=\"#{chapter_anchor}-{anchor}\">{_esc(name)}</a>"
            for _position, anchor, name in sorted(present)
        ]
        if not links:
            continue
        parts.append(
            f"<div><a href=\"#{chapter_anchor}\"><b>{_esc(chapter_name)}</b></a>: "
            + " &middot; ".join(links) + "</div>"
        )
    parts.append("</nav>")
    return "".join(parts)


def _esc(text: str) -> str:
    """HTML-escapes any value on its way into the document, attributes included.

    Every subject name, carrier id, scheme id and rejection reason in the report comes from
    simulation configs and JSON data files, i.e. from outside this module, and lands inside
    markup or inside a quoted attribute. Escaping at the single point of insertion (`quote=True`
    covers the attribute case) is what keeps a component named `A & B` from silently breaking
    the page. Numbers formatted by `_fmt` cannot contain markup, which is why the numeric paths
    do not all route through here.
    """
    return html.escape(str(text), quote=True)


def _svg_open(width: int, height: int) -> List[str]:
    """Opens a responsive `<svg>` and returns it as the first element of the parts list.

    Every chart builder starts from this and appends its marks, so the return type is a list
    rather than a string: the builders accumulate parts and `"".join` them once at the end,
    which is both cheaper and easier to read than repeated concatenation. The element carries a
    `viewBox` in the chart's own user units together with `width="100%"` and a
    `max-width:{width}px`, so the drawing scales down on a narrow screen without any of the
    geometry below having to know the viewport, and `role="img"` announces it as a single
    graphic to assistive technology.
    """
    return [
        f'<svg viewBox="0 0 {width} {height}" width="100%" style="max-width:{width}px" '
        f'role="img" xmlns="http://www.w3.org/2000/svg">'
    ]


def _rect(x: float, y: float, w: float, h: float, color: str, tooltip: str, rx: float = 0.0) -> str:
    """A mark with a native tooltip and the 2px surface gap handled by the caller.

    The workhorse of every bar chart here. `x`/`y` are the mark's **top-left** corner in user
    units (y grows downward), `color` is normally a CSS variable so the bar re-colours with the
    theme, and `rx` rounds the corners. The `<title>` child is the tooltip: browsers show it on
    hover natively, which is how the report gets per-mark values without any script.

    Width and height are floored at 0.1 rather than clamped at 0, so a segment that is real but
    sub-pixel still renders as a hairline instead of vanishing — a bar chart that silently drops
    small contributions would be misleading. The visual gap between adjacent bars is *not* done
    here; callers subtract it from the width or height they pass in, which is why they pass
    `bar_w - 2` and similar.
    """
    return (
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(w, 0.1):.1f}" height="{max(h, 0.1):.1f}" '
        f'fill="{color}" rx="{rx}"><title>{_esc(tooltip)}</title></rect>'
    )


def _text(x: float, y: float, content: str, size: int = 11, anchor: str = "start",
          color: str = "var(--ink-2)", bold: bool = False) -> str:
    """One SVG text label, escaped and in the report's typeface.

    `y` is the glyph **baseline**, not the top of the line box, and `anchor` chooses which end
    of the string sits at `x` (`start`, `middle` or `end`) — the two facts that explain the
    otherwise cryptic offsets in the chart builders: row labels are drawn at `left - 8` with
    `anchor="end"` so they end just before the plot area, and vertically at
    `row_middle + 4` so a ~11px glyph sits optically centred on its row.
    """
    weight = ' font-weight="600"' if bold else ""
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" text-anchor="{anchor}" '
        f'fill="{color}" {_ReportStyle.SVG_FONT}{weight}>{_esc(content)}</text>'
    )


def _hline(x1: float, x2: float, y: float, color: str = "var(--baseline)", width: float = 1.0) -> str:
    """A horizontal rule in user units — a chart's zero line, or a whisker between two values.

    Two unrelated jobs share one primitive because both are a straight segment at a constant y:
    axis chrome (default colour `--baseline`, hairline) and the min-to-max whisker of the
    banded charts (caller passes a group colour and a heavier stroke). It is also used
    degenerately, with `x1 == x2`, to mark a point.
    """
    return f'<line x1="{x1:.1f}" y1="{y:.1f}" x2="{x2:.1f}" y2="{y:.1f}" stroke="{color}" stroke-width="{width}"/>'


def _table(headers: List[str], rows: List[List[str]]) -> str:
    """A plain result table; all cell values must already be strings (and escaped).

    Every chart in the report is paired with the table it was drawn from, and this renders them
    all, so the styling in `_ReportCss` reaches each one. The escaping contract is inverted
    compared to `_text`: cells are inserted **verbatim**, so a caller can emit `<b>` for a total
    row or an `<a>` for a source link, and must therefore run any external string through
    `_esc` itself. Ragged rows are not checked — a row shorter than the headers simply renders
    with fewer cells.
    """
    head = "".join(f"<th>{header}</th>" for header in headers)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f"<table><tr>{head}</tr>{body}</table>"


def _details(summary: str, content: str, open_by_default: bool = False) -> str:
    """Wraps content in a native collapsible `<details>` block.

    The report's answer to being both an overview and a full audit trail: charts and headline
    tables stay visible, while the underlying detail (the cash-flow table with one row per
    year × subject × category, the sources registry, the awards table) is one click away and
    does not have to be paged or paginated. Native `<details>` keeps that behaviour scriptless,
    printable and searchable. `summary` is inserted verbatim, so callers escape it themselves.
    """
    open_attr = " open" if open_by_default else ""
    return f"<details{open_attr}><summary>{summary}</summary>{content}</details>"


def _category_table(result: LifecycleCostResult) -> str:
    """NPV by display group and by raw cost category — the §3.7 result table.

    The table under the timeline chart, and the place where the display grouping is undone
    again: each of the eight coloured groups is printed in bold with its total, and the raw
    `CostCategory` members that make it up are indented underneath. That two-level shape is
    what lets a reviewer move between the chart's vocabulary (eight colours) and the engine's
    (sixteen-plus categories) without a lookup, and it is where a category landing in an
    unexpected group would show up.

    Groups and categories whose value is zero in every slot are skipped, so the table shows what
    this run actually produced rather than the full taxonomy. The group sums come from
    `views.fold_categories` — presentation supplies only the mapping.
    """
    rows = []
    group_npv = views.fold_categories(result.npv_by_category, PresentationStyle.CATEGORY_TO_GROUP)
    for index, (group_name, categories) in enumerate(PresentationStyle.DISPLAY_GROUPS):
        group_total = group_npv.get(index)
        if group_total is None or not (group_total.average or group_total.minimum or group_total.maximum):
            continue
        rows.append([f"<b>{_esc(group_name)}</b>", f"<b>{_esc(_band_str(group_total))}</b>"])
        for category in categories:
            value = result.npv_by_category.get(category)
            if value is not None and (value.average or value.minimum or value.maximum):
                rows.append([f"&nbsp;&nbsp;{_esc(category.value)}", _esc(_band_str(value))])
    return _table(["Category", "NPV"], rows)


def _loan_svg(result: LifecycleCostResult) -> str:
    """Loan amortization: interest vs. principal per year (§4.4).

    Shown in the loan section only when the perspective is financed, and it answers a question the
    cash-flow chart cannot: of the money leaving the account each year, how much is repayment
    and how much is the cost of borrowing. An annuity loan has a characteristic shape — constant
    total, interest falling as principal rises — and a chart that does not have it means the
    financing plan is not what the reviewer thinks it is. Returns the empty string for an
    unfinanced perspective, which is the common case, so the caller can concatenate it blindly.

    Geometry: bars are principal-first from the baseline upward with interest stacked on top, so
    the total bar height is the year's debt service. `scale` maps euros to pixels off the peak
    total year, `bar_w` divides the plot width over the horizon, and the x-axis is labelled
    every `horizon // 10` years to keep the ticks readable at any horizon. The interest segment
    gets a `max(..., 0.5)` floor so a nearly-repaid final year still shows a visible sliver.
    """
    horizon = result.parameters.observation_period_in_years
    amortization = views.loan_amortization_series(result)
    interest_per_year = amortization.interest_in_euro
    principal_per_year = amortization.principal_in_euro
    if not amortization.has_flows():
        return ""
    peak = max(i + p for i, p in zip(interest_per_year, principal_per_year))
    width, height, left, top, bottom = 860, 180, 70, 14, 28
    scale = (height - top - bottom) / max(peak, 1e-9)
    bar_w = (width - left - 20) / (horizon + 1)
    parts = _svg_open(width, height)
    parts.append(_hline(left, width - 10, height - bottom))
    for year in range(horizon + 1):
        x = left + year * bar_w
        principal_h = principal_per_year[year] * scale
        interest_h = interest_per_year[year] * scale
        base_y = height - bottom
        if principal_per_year[year]:
            parts.append(_rect(x + 1, base_y - principal_h, bar_w - 2, principal_h - 1, "var(--g0)",
                               f"year {year} - principal: {_fmt(principal_per_year[year])} EUR"))
        if interest_per_year[year]:
            parts.append(_rect(x + 1, base_y - principal_h - interest_h, bar_w - 2, max(interest_h - 1, 0.5),
                               "var(--g1)", f"year {year} - interest: {_fmt(interest_per_year[year])} EUR"))
        if year % max(1, horizon // 10) == 0:
            parts.append(_text(x + bar_w / 2, height - 12, str(year), 10, "middle", "var(--muted)"))
    parts.append(_text(left - 6, top + 8, _fmt(peak), 10, "end", "var(--muted)"))
    parts.append("</svg>")
    return (
        '<div class="legend"><span class="chip"><span class="swatch" style="background:var(--g0)"></span>'
        'principal</span><span class="chip"><span class="swatch" style="background:var(--g1)"></span>'
        "interest</span></div>" + "".join(parts)
    )


def _co2_section_html(matrix: EvaluationMatrix, context: _ChapterContext) -> str:
    """The CO2 section: lifecycle CO2 (§3.8) — embodied vs. operational, per subject/carrier.

    Answers "does the emissions accounting behave like the money does, and are the two kept
    apart?" Embodied emissions (blue, keyed by component subject, booked at installation and at
    every replacement) and operational emissions (orange, keyed by energy carrier) are drawn on
    one axis so their relative size is visible, since for a well-insulated building with a heat
    pump the embodied share stops being negligible. The section's caption states the separation
    the spec insists on: these are *masses*, and neither the CO2 price (a cash flow) nor the CO2
    damage cost (a macroeconomic charge) is ever added to them.

    Three views of the same figures: sorted horizontal bars, a cumulative operational curve over
    the horizon (flat-sloped, because v1 holds emission factors constant — the caption says so),
    and a table whose Total row closes against `total_co2_in_kg`. Emissions are never discounted,
    so unlike the money charts this one has no present-value counterpart. Renders as the empty
    string when the run has no emissions data at all.
    """
    result = next(iter(matrix.results.values()))
    co2 = result.lifecycle_co2_result
    embodied = dict(co2.embodied_by_subject_in_kg)
    operational = dict(co2.operational_co2_by_carrier_in_kg)
    if not embodied and not operational:
        return ""
    # Viz 1: horizontal bars per subject/carrier (embodied blue, operational orange).
    entries = [(subject, value, "embodied") for subject, value in embodied.items() if value] + [
        (carrier, value, "operational") for carrier, value in operational.items() if value
    ]
    entries.sort(key=lambda item: -item[1])
    width, row_h, left = 860, 26, 220
    height = len(entries) * row_h + 12
    peak = max((value for _s, value, _k in entries), default=1.0)
    scale = (width - left - 130) / max(peak, 1e-9)
    parts = _svg_open(width, height)
    y = 4.0
    for subject, value, kind in entries:
        color = "var(--g0)" if kind == "embodied" else "var(--g7)"
        parts.append(_text(left - 8, y + row_h / 2 + 4, subject, 11, "end"))
        parts.append(_rect(left, y + 4, value * scale, row_h - 8, color,
                           f"{subject} ({kind}): {value:,.0f} kg CO2 over the horizon", rx=3))
        parts.append(_text(left + value * scale + 6, y + row_h / 2 + 4, f"{value:,.0f} kg", 10,
                           "start", "var(--muted)"))
        y += row_h
    parts.append("</svg>")
    bars = "".join(parts)
    # Viz 2: cumulative operational CO2 over the years.
    cumulative = views.cumulative_operational_co2_in_kg(result)
    line = ""
    if cumulative and cumulative[-1] > 0:
        width2, height2, left2, top2, bottom2 = 860, 120, 70, 10, 24
        scale2 = (height2 - top2 - bottom2) / max(cumulative[-1], 1e-9)
        step = (width2 - left2 - 20) / max(len(cumulative) - 1, 1)
        points = " ".join(
            f"{left2 + index * step:.1f},{height2 - bottom2 - value * scale2:.1f}"
            for index, value in enumerate(cumulative)
        )
        line_parts = _svg_open(width2, height2)
        line_parts.append(_hline(left2, width2 - 10, height2 - bottom2))
        line_parts.append(
            f'<polyline points="{points}" fill="none" stroke="var(--g7)" stroke-width="2">'
            f"<title>cumulative operational CO2</title></polyline>"
        )
        line_parts.append(_text(left2 + (len(cumulative) - 1) * step, height2 - bottom2 - cumulative[-1] * scale2 - 6,
                                f"{cumulative[-1]:,.0f} kg operational", 10, "end", "var(--ink-1)"))
        line_parts.append("</svg>")
        line = ("<p class='sub'>Cumulative operational CO2 over the horizon "
                "(constant emission factors in v1, §3.8):</p>" + "".join(line_parts))
    table_rows = [
        [_esc(subject), f"{value:,.0f}", "-", f"{value:,.0f}"] for subject, value in embodied.items() if value
    ] + [
        [_esc(carrier), "-", f"{value:,.0f}", f"{value:,.0f}"] for carrier, value in operational.items() if value
    ]
    operational_total = cumulative[-1] if cumulative else 0.0
    table_rows.append(["<b>Total</b>", f"<b>{co2.embodied_co2_in_kg:,.0f}</b>",
                       f"<b>{operational_total:,.0f}</b>", f"<b>{co2.total_co2_in_kg:,.0f}</b>"])
    return (
        _section_open(ReportSections.CO2, context)
        + _explanation_html(ReportSections.CO2, context)
        + bars + line
        + _co2_factors_table(result)
        + _details("CO2 table [kg]", _table(["Subject / carrier", "Embodied", "Operational", "Total"], table_rows))
        + "</section>"
    )


def _co2_factors_table(result: LifecycleCostResult) -> str:
    """The conversions behind every mass in the section above (Q26 F3, rule 2.9).

    One row per carrier and per device, each stating its factor, the quantity it multiplies and
    the product — so every bar in the chart is one visible multiplication away from its inputs
    instead of a number to be trusted. Operational rows carry the annual mass and the horizon
    total; embodied rows carry the mass per installation and how many installations the horizon
    booked, which is where a device replaced once inside the horizon shows its doubled mass.

    Empty for a result stored before the factors were recorded, in which case the section renders
    exactly as it did before rather than dividing masses by quantities to invent a factor.
    """
    rows = views.co2_factor_rows(result)
    if not rows:
        return ""
    table_rows = []
    for row in rows:
        if row.kind == views.Co2FactorKinds.OPERATIONAL:
            arithmetic = (
                f"{row.factor_in_kg_per_unit:,.4f} kg/kWh x {row.quantity:,.0f} kWh/a = "
                f"{row.annual_mass_in_kg or 0.0:,.0f} kg/a"
            )
            over_horizon = f"x {row.installations} a = {row.total_in_kg:,.0f} kg"
        else:
            arithmetic = (
                f"{row.factor_in_kg_per_unit:,.2f} kg/{_esc(row.quantity_unit)} x "
                f"{row.quantity:,.2f} {_esc(row.quantity_unit)} = "
                f"{row.per_installation_in_kg or 0.0:,.0f} kg per installation"
            )
            over_horizon = (
                f"x {row.installations} installation(s) = {row.total_in_kg:,.0f} kg"
            )
        table_rows.append([_esc(row.subject), _esc(row.kind), arithmetic, over_horizon])
    return _details(
        "CO2 factors — every mass as its own multiplication",
        _table(["Subject / carrier", "Kind", "Factor x quantity", "Over the horizon"], table_rows),
        open_by_default=True,
    )


def _legend_html(groups_present: List[int]) -> str:
    """Colour chips for exactly the display groups a chart actually drew.

    Takes group indices rather than names so the swatch and the label are read from the same
    `PresentationStyle` entry the chart coloured its marks with, which is what keeps legend and
    chart in step. Callers pass only the groups present in the data, so a legend never lists a
    colour the reader cannot find in the chart above it.
    """
    chips = "".join(
        f'<span class="chip"><span class="swatch" style="background:var(--g{index})"></span>'
        f"{_esc(PresentationStyle.DISPLAY_GROUPS[index][0])}</span>"
        for index in groups_present
    )
    return f'<div class="legend">{chips}</div>'


def _annual_flow_svg(result: LifecycleCostResult) -> str:
    """Stacked bars per year by display group (nominal, negatives below the axis).

    The centrepiece of the cash-flow timeline and the chart most likely to expose a modelling mistake at a
    glance: replacements have to spike at the component lifetimes, the residual-value credit has
    to appear at the horizon, energy has to grow smoothly at the escalation rate, and year 0 has
    to carry the investment. Nominal (undiscounted) on purpose — this is the liquidity view, and
    the discounted counterpart is the curve drawn immediately below it.

    Geometry: costs and credits have *separate* baselines (`y_pos` growing upward from the zero
    line, `y_neg` downward) and are never netted, so a year with both shows both. The zero line
    sits `max_pos * scale` below the top, which places it wherever the positive/negative split
    requires instead of at a fixed height; one shared `scale` covers `max_pos + max_neg` so the
    two halves stay comparable. Each segment is shortened by up to 1px
    (`bar_h - min(1.0, bar_h * 0.3)`) to leave a hairline between stacked groups without
    swallowing a thin one, and x-ticks are thinned to about ten labels whatever the horizon.
    """
    horizon = result.parameters.observation_period_in_years
    per_year: List[Dict[int, float]] = views.fold_category_matrix(
        views.nominal_annual_matrix_by_category(result), PresentationStyle.CATEGORY_TO_GROUP
    )
    max_pos = max((sum(v for v in year.values() if v > 0) for year in per_year), default=1.0)
    max_neg = max((-sum(v for v in year.values() if v < 0) for year in per_year), default=0.0)
    width, height, left, top, bottom = 860, 300, 70, 16, 34
    plot_h = height - top - bottom
    scale = (plot_h) / max(max_pos + max_neg, 1e-9)
    zero_y = top + max_pos * scale
    bar_w = (width - left - 20) / (horizon + 1)
    parts = _svg_open(width, height)
    parts.append(_hline(left, width - 10, zero_y))
    for year, groups in enumerate(per_year):
        x = left + year * bar_w
        y_pos, y_neg = zero_y, zero_y
        for index in range(len(PresentationStyle.DISPLAY_GROUPS)):
            value = groups.get(index, 0.0)
            if not value:
                continue
            bar_h = abs(value) * scale
            tooltip = f"year {year} - {PresentationStyle.DISPLAY_GROUPS[index][0]}: {_fmt(value)} EUR"
            if value > 0:
                y_pos -= bar_h
                parts.append(_rect(x + 1, y_pos, bar_w - 2, bar_h - min(1.0, bar_h * 0.3), f"var(--g{index})", tooltip))
            else:
                parts.append(_rect(x + 1, y_neg, bar_w - 2, bar_h - min(1.0, bar_h * 0.3), f"var(--g{index})", tooltip))
                y_neg += bar_h
        if year % max(1, horizon // 10) == 0:
            parts.append(_text(x + bar_w / 2, height - 14, str(year), 10, "middle", "var(--muted)"))
    parts.append(_text(left - 6, zero_y + 4, "0", 10, "end", "var(--muted)"))
    parts.append(_text(left - 6, top + 10, _fmt(max_pos), 10, "end", "var(--muted)"))
    if max_neg:
        parts.append(_text(left - 6, height - bottom, f"-{_fmt(max_neg)}", 10, "end", "var(--muted)"))
    parts.append(_text(width - 10, height - 14, "year", 10, "end", "var(--muted)"))
    parts.append("</svg>")
    return "".join(parts)


def _cumulative_npv_svg(result: LifecycleCostResult) -> str:
    """Cumulative discounted cost over the horizon, with its min/max uncertainty band.

    Own axis (never dual-axis); the shaded band is the slot-wise LOW/HIGH envelope, so the
    final point matches the reported NPV band exactly.

    This is where the report ties the year-by-year story to the headline number: the curve's end
    point is labelled with the NPV band and is, by construction, the same figure the perspective
    table prints, because both come from the same discounted series in
    `views.cumulative_discounted_cost_series`. A reviewer's check here is the shape — a steep
    year-0 step for the investment, a steady operating slope, visible replacement steps — and
    that the label agrees with the perspectives section.

    Geometry: `to_y` inverts value to pixel (larger value, smaller y) against a scale spanning
    `low_value..top_value`, where `low_value` is clamped to at most 0 so the zero line is always
    on the canvas even for an all-positive series. The band polygon is the LOW series drawn
    left-to-right followed by the HIGH series drawn right-to-left, which closes it into a filled
    ribbon; it is skipped entirely when the run has no bands.
    """
    horizon = result.parameters.observation_period_in_years
    cumulative = views.cumulative_discounted_cost_series(result)
    top_value = max(max(series) for series in cumulative.values())
    low_value = min(0.0, min(min(series) for series in cumulative.values()))
    width, height, left, top, bottom = 860, 150, 70, 12, 26
    scale = (height - top - bottom) / max(top_value - low_value, 1e-9)
    step = (width - left - 20) / max(horizon, 1)

    def to_y(value: float) -> float:
        return top + (top_value - value) * scale

    def points_of(series: List[float]) -> str:
        return " ".join(f"{left + year * step:.1f},{to_y(value):.1f}" for year, value in enumerate(series))

    parts = _svg_open(width, height)
    parts.append(_hline(left, width - 10, to_y(0.0)))
    band = result.total_npv_in_euro
    if not band.is_exact():
        # min series forward, max series backward -> closed band polygon.
        forward = points_of(cumulative[Slot.LOW])
        backward = " ".join(
            f"{left + year * step:.1f},{to_y(value):.1f}"
            for year, value in reversed(list(enumerate(cumulative[Slot.HIGH])))
        )
        parts.append(
            f'<polygon points="{forward} {backward}" fill="var(--g0)" opacity="0.15">'
            f"<title>cumulative discounted cost, min/max envelope</title></polygon>"
        )
    parts.append(
        f'<polyline points="{points_of(cumulative[Slot.AVERAGE])}" fill="none" stroke="var(--g0)" stroke-width="2">'
        f"<title>cumulative discounted cost (average slot)</title></polyline>"
    )
    parts.append(_text(left - 6, to_y(top_value) + 8, _fmt(top_value), 10, "end", "var(--muted)"))
    parts.append(_text(left - 6, to_y(0.0) + 4, "0", 10, "end", "var(--muted)"))
    parts.append(
        _text(left + horizon * step, to_y(cumulative[Slot.AVERAGE][-1]) - 6,
              f"NPV {_band_str(band)}", 11, "end", "var(--ink-1)", bold=True)
    )
    parts.append("</svg>")
    return "".join(parts)


def _waterfall_svg(steps: List[Tuple[str, float, str]], total_label: str, net: float) -> str:
    """Horizontal waterfall: (label, signed value, color-var) steps ending in a net bar.

    `net` is passed in rather than summed from the steps: it is a *result* figure (the year-0
    net outflow, the NPV delta), and the steps may legitimately not add up to it — the
    comparison waterfall drops sub-cent subjects. Summing it here is §7 B8's second half.

    Used by two sections with the same shape of question — the investment build-up ("how does the
    year-0 gross become the net outflow") and the comparison ("how does each subject's delta add up to the total
    NPV delta") — which is why it takes an abstract list of steps and a total label rather than
    knowing about either. Each step is drawn where the running `cursor` leaves off, so the bars
    form a staircase and a reader can follow the money left to right; steps are drawn in the
    order given, and that order is the caller's editorial choice.

    Geometry: one row per step plus a separated total row, `scale` fitted so the sum of the
    steps' absolute values (or the net, whichever is larger) spans the plot width. A negative
    step is drawn from `cursor + value` to `cursor`, i.e. leftward, and gets its value label on
    the left so the text never overlaps the bar.
    """
    width, row_h, left = 860, 26, 220
    height = (len(steps) + 2) * row_h + 10
    span = max(sum(abs(value) for _l, value, _c in steps), abs(net), 1e-9)
    scale = (width - left - 120) / span
    parts = _svg_open(width, height)
    cursor = 0.0
    y = 6.0
    for label, value, color in steps:
        x_from = left + min(cursor, cursor + value) * scale
        bar_w = abs(value) * scale
        parts.append(_text(left - 8, y + row_h / 2 + 4, label, 11, "end"))
        parts.append(_rect(x_from, y + 4, bar_w, row_h - 8, color, f"{label}: {_fmt(value)} EUR", rx=3))
        parts.append(
            _text(x_from + bar_w + 6 if value >= 0 else x_from - 6, y + row_h / 2 + 4,
                  f"{'+' if value >= 0 else ''}{_fmt(value)}", 10,
                  "start" if value >= 0 else "end", "var(--muted)")
        )
        cursor += value
        y += row_h
    parts.append(_hline(left, width - 10, y + 2, "var(--baseline)"))
    y += 8
    parts.append(_text(left - 8, y + row_h / 2 + 4, total_label, 11, "end", "var(--ink-1)", bold=True))
    parts.append(
        _rect(left, y + 4, abs(net) * scale, row_h - 8, "var(--ink-1)", f"{total_label}: {_fmt(net)} EUR", rx=3)
    )
    parts.append(_text(left + abs(net) * scale + 6, y + row_h / 2 + 4, f"{_fmt(net)} EUR", 11, "start",
                       "var(--ink-1)", bold=True))
    parts.append("</svg>")
    return "".join(parts)


def _whisker_svg(rows: List[Tuple[str, UncertainValue]], unit: str) -> str:
    """Dot-with-whiskers per row (perspective overview / any banded metric list).

    The report's standard way of showing a list of banded figures: a dot at the AVERAGE slot and
    a bar from LOW to HIGH. It is generic on purpose — the perspectives, who-pays-what
    and 4 (per-carrier year-1 bills) all reduce to "labelled `UncertainValue`s on a common
    axis", and giving them one visual form means a reader learns the encoding once. The whiskers
    are the §3.9 *envelope* of two coherent worlds, not a statistical interval, so overlapping
    whiskers say nothing about significance.

    Geometry: the axis spans `min(0, smallest minimum)` to the largest maximum, so zero is
    always on the canvas and rows with credits (negative NPVs) read correctly against it; `to_x`
    maps value to pixel, labels sit to the right of each maximum, and the numeric band is
    printed next to the whisker so the chart is readable without hovering.
    """
    width, row_h, left = 860, 30, 220
    height = len(rows) * row_h + 30
    max_value = max((band.maximum for _l, band in rows), default=1.0)
    min_value = min(0.0, min((band.minimum for _l, band in rows), default=0.0))
    scale = (width - left - 110) / max(max_value - min_value, 1e-9)

    def to_x(value: float) -> float:
        return left + (value - min_value) * scale

    parts = _svg_open(width, height)
    y = 8.0
    for label, band in rows:
        mid = y + row_h / 2
        parts.append(_text(left - 8, mid + 4, label, 11, "end"))
        parts.append(_hline(to_x(band.minimum), to_x(band.maximum), mid, "var(--g0)", 2))
        parts.append(
            f'<circle cx="{to_x(band.average):.1f}" cy="{mid:.1f}" r="5" fill="var(--g0)" '
            f'stroke="var(--surface)" stroke-width="2">'
            f"<title>{_esc(label)}: {_esc(_band_str(band, unit))}</title></circle>"
        )
        parts.append(_text(to_x(band.maximum) + 8, mid + 4, _band_str(band, unit), 10, "start", "var(--muted)"))
        y += row_h
    if min_value < 0:
        parts.append(_hline(to_x(0.0), to_x(0.0), 4, "var(--baseline)"))
    parts.append("</svg>")
    return "".join(parts)


def _stacked_subject_svg(result: LifecycleCostResult) -> str:
    """Per-subject diverging stacked bars by display group (§7.4).

    Costs stack RIGHT of the zero line, credits (residual value, subsidies, feed-in, anyway
    credit) stack LEFT — never summed onto the cost side. The whisker + dot mark the net NPV
    band on the same signed axis, so `net = costs - credits` is visible geometry.

    The component-breakdown chart, and the one that makes the §7.4 reconciliation checkable by eye: the
    per-subject net markers must add up to the headline NPV of the perspective, and a subject
    whose credits visibly outweigh its costs (a PV system, say) sits left of zero. Drawing
    credits as their own stack rather than netting them into the cost bar is the whole point —
    a component whose gross cost is large and whose subsidy is nearly as large looks very
    different from one that was cheap to begin with, and a netted bar hides that difference.

    Geometry: `pos_span`/`neg_span` are the widest cost and credit stacks, each additionally
    widened to cover the net band's maximum/minimum so the whisker can never be drawn off the
    canvas; the zero line is placed `neg_span * scale` from the left, which is why it moves
    between runs. Credit rects are emitted inline rather than through `_rect` so they can carry
    a `class="credit"` hook for styling; the shipped stylesheet does not currently use it.
    """
    breakdowns = list(result.component_breakdowns.values())
    if not breakdowns:
        return ""

    per_subject = {
        breakdown.subject: {
            index: band.average
            for index, band in views.fold_categories(
                breakdown.npv_by_category, PresentationStyle.CATEGORY_TO_GROUP
            ).items()
        }
        for breakdown in breakdowns
    }
    pos_span = max(
        (sum(v for v in values.values() if v > 0) for values in per_subject.values()), default=1.0
    )
    neg_span = max(
        (-sum(v for v in values.values() if v < 0) for values in per_subject.values()), default=0.0
    )
    pos_span = max(pos_span, max((b.total_npv_in_euro.maximum for b in breakdowns), default=0.0), 1e-9)
    neg_span = max(neg_span, -min((b.total_npv_in_euro.minimum for b in breakdowns), default=0.0), 0.0)
    width, row_h, left = 860, 30, 220
    height = len(breakdowns) * row_h + 26
    scale = (width - left - 140) / max(pos_span + neg_span, 1e-9)
    zero_x = left + neg_span * scale

    def to_x(value: float) -> float:
        return zero_x + value * scale

    parts = _svg_open(width, height)
    parts.append(
        f'<line x1="{zero_x:.1f}" y1="2" x2="{zero_x:.1f}" y2="{height - 18}" stroke="var(--baseline)"/>'
    )
    y = 4.0
    for breakdown in breakdowns:
        mid = y + row_h / 2
        values = per_subject[breakdown.subject]
        parts.append(_text(left - 8, mid + 4, breakdown.subject, 11, "end"))
        x_pos = zero_x
        x_neg = zero_x
        for index in range(len(PresentationStyle.DISPLAY_GROUPS)):
            value = values.get(index, 0.0)
            if not value:
                continue
            bar_w = abs(value) * scale
            tooltip = f"{breakdown.subject} - {PresentationStyle.DISPLAY_GROUPS[index][0]}: {_fmt(value)} EUR NPV"
            if value > 0:
                parts.append(
                    _rect(x_pos, y + 5, max(bar_w - 1.5, 0.5), row_h - 10, f"var(--g{index})", tooltip, rx=2)
                )
                x_pos += bar_w
            else:
                x_neg -= bar_w
                parts.append(
                    f'<rect class="credit" x="{x_neg:.1f}" y="{y + 5:.1f}" width="{max(bar_w - 1.5, 0.5):.1f}" '
                    f'height="{row_h - 10:.1f}" fill="var(--g{index})" rx="2">'
                    f"<title>{_esc(tooltip)}</title></rect>"
                )
        total = breakdown.total_npv_in_euro
        parts.append(_hline(to_x(total.minimum), to_x(total.maximum), mid, "var(--ink-1)", 1.5))
        parts.append(
            f'<circle cx="{to_x(total.average):.1f}" cy="{mid:.1f}" r="4" fill="var(--ink-1)" '
            f'stroke="var(--surface)" stroke-width="1.5">'
            f"<title>{_esc(breakdown.subject)} net NPV: {_esc(_band_str(total))}</title></circle>"
        )
        parts.append(_text(x_pos + 8, mid + 4, _band_str(total), 10, "start", "var(--muted)"))
        y += row_h
    parts.append(_text(zero_x, height - 6, "credits left | costs right of 0; whisker + dot = net NPV band",
                       9, "middle", "var(--muted)"))
    parts.append("</svg>")
    return "".join(parts)


def _payback_svg(comparison: VariantComparison) -> str:
    """The comparison's payback curve per slot; its zero-crossing is the printed payback year.

    The curve is `comparison.cumulative_discounted_savings_in_euro` (W4.4) — the same array
    `discounted_payback_years` was derived from, so the drawing and the number cannot disagree.
    """
    series = {
        name: comparison.cumulative_discounted_savings_in_euro[slot]
        for name, slot in (("min", "low"), ("avg", "average"), ("max", "high"))
        if slot in comparison.cumulative_discounted_savings_in_euro
    }
    if not series:
        return ""
    horizon = max(len(values) for values in series.values()) - 1
    top_value = max(max(values) for values in series.values())
    low_value = min(min(values) for values in series.values())
    width, height, left, top, bottom = 860, 240, 70, 14, 28
    scale = (height - top - bottom) / max(top_value - low_value, 1e-9)
    step = (width - left - 20) / max(horizon, 1)

    def to_y(value: float) -> float:
        return top + (top_value - value) * scale

    parts = _svg_open(width, height)
    parts.append(_hline(left, width - 10, to_y(0.0), "var(--baseline)"))
    styles = {"avg": ("var(--g0)", 2.5, ""), "min": ("var(--g0)", 1.2, ' stroke-dasharray="5 4"'),
              "max": ("var(--g0)", 1.2, ' stroke-dasharray="2 4"')}
    labels = {"avg": "expected", "min": "optimistic (LOW world)", "max": "pessimistic (HIGH world)"}
    for slot_name, values in series.items():
        color, stroke_width, dash = styles[slot_name]
        points = " ".join(f"{left + year * step:.1f},{to_y(value):.1f}" for year, value in enumerate(values))
        parts.append(
            f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="{stroke_width}"{dash}>'
            f"<title>cumulative discounted savings - {labels[slot_name]}</title></polyline>"
        )
        parts.append(_text(left + horizon * step + 4, to_y(values[-1]) + 4, labels[slot_name].split(" ")[0], 9,
                           "start", "var(--muted)"))
    for year in range(0, horizon + 1, max(1, horizon // 10)):
        parts.append(_text(left + year * step, height - 10, str(year), 10, "middle", "var(--muted)"))
    parts.append(_text(left - 6, to_y(0.0) + 4, "0", 10, "end", "var(--muted)"))
    parts.append(_text(left - 6, to_y(top_value) + 8, _fmt(top_value), 10, "end", "var(--muted)"))
    parts.append("</svg>")
    return "".join(parts)


# ------------------------------------------------------- shared chart builders of the V-set (E)
#
# The fifteen charts of the visualization extension reuse five builders rather than each
# emitting its own SVG: a column Sankey (V1, V10, V11, V12), an xy line chart with optional
# bands (V2, V13, V15), a Gantt strip (V7, V9), a treemap (V8) and a two-sided tornado (V3).
# Everything they draw comes from `views.py`; the code below is geometry only, in the same
# y-grows-downward user units as the older charts above.

class _ChartGeometry:
    """The pixel frame every chart of the visualization set is drawn in.

    One place for the canvas width and the margins, because the report stacks a dozen charts and
    a chart that sets its own plot area does not line up with the one above it. The margins are
    generous on the left for row labels (a subject name, an actor, a scheme id) and on the right
    for the value labels the charts print at the end of a line or a ribbon.
    """

    WIDTH = 860
    LEFT = 150
    RIGHT = 130
    TOP = 14
    BOTTOM = 30
    ROW_HEIGHT = 26
    #: Events closer than this many years share a label stack instead of overprinting (V7, V9).
    CLUSTER_YEARS = 3


def _net_stub_svg(
    geometry: Any,
    labels: Dict[str, str],
    node_pixels: Any,
    plot_w: float,
    plot_h: float,
    pixels_per_unit: float,
    stub_labels: Optional[Dict[str, str]] = None,
) -> List[str]:
    """The net-position stubs that close a node's deficient face (Q29 R7).

    Drawn as a short flat band off the face the ribbons do not fill, labelled with the signed
    amount, and deliberately unlike a ribbon: flat, muted and ending in mid-air rather than at
    another node, because it is a *position* and not a payment to anybody. A node that receives
    more than it passes on gets a `+` stub on its right face; one that pays out more than it takes
    in gets a `−` stub on its left, which is the same leftover convention the landlord income
    Sankey already uses.
    """
    parts: List[str] = []
    for stub in geometry.net_stubs:
        if stub.node not in geometry.boxes:
            continue
        x, y, node_height = node_pixels(stub.node)
        stub_h = stub.amount * pixels_per_unit
        top = y + node_height - stub.anchor * plot_h - stub_h
        length = SankeyLayout.STUB_LENGTH * plot_w
        if stub.is_outgoing:
            x0 = x + SankeyLayout.NODE_WIDTH * plot_w
            text_x, anchor = x0 + length + 4, "start"
        else:
            x0, text_x, anchor = x - length, x - length - 4, "end"
        sign = "+" if stub.is_outgoing else "-"
        text = (stub_labels or {}).get(stub.node, f"net {sign}{_fmt(stub.amount)} EUR")
        tooltip = f"{labels.get(stub.node, stub.node)}: {text} ({stub.amount:,.2f} EUR unmatched)"
        parts.append(
            f'<rect class="net-stub" x="{x0:.1f}" y="{top:.1f}" width="{max(length, 0.1):.1f}" '
            f'height="{max(stub_h, 0.1):.1f}" fill="var(--muted)" fill-opacity="0.22" '
            f'stroke="var(--muted)" stroke-width="0.6" stroke-dasharray="2 2">'
            f"<title>{_esc(tooltip)}</title></rect>"
        )
        parts.append(_text(text_x, top + stub_h / 2 + 3, text, 9, anchor, "var(--muted)"))
    return parts


def _ribbon_title(
    labels: Dict[str, str],
    source: str,
    target: str,
    amount: float,
    ribbon_tooltips: Optional[List[str]],
    index: int,
) -> str:
    """The hover text of one ribbon: the caller's exact string, else the rounded default (Q28)."""
    if ribbon_tooltips is not None and index < len(ribbon_tooltips):
        return ribbon_tooltips[index]
    return f"{labels.get(source, source)} -> {labels.get(target, target)}: {_fmt(amount)}"


class _SankeyLabels:
    """How much room a Sankey node label needs, for the amount lines of Q28 R6.

    A node label is drawn beside its rectangle, so two lines of it (the name and the amounts)
    only fit where the rectangle is at least as tall as the two baselines they occupy. Below that
    the amount line degrades to the node total alone, and the split stays in the node's tooltip —
    the Q28 rule that a small node may lose the breakdown but never the number.
    """

    #: Node height (px) from which the full "costs X | credits -Y" line is drawn.
    MIN_HEIGHT_FOR_SPLIT = 20.0
    #: Font size of the amount line; one step below the name it sits under.
    AMOUNT_FONT_SIZE = 9
    #: Baseline offsets of the name and the amount line, measured from the node's vertical middle.
    NAME_OFFSET = -1.0
    AMOUNT_OFFSET = 9.0


def _sankey_svg(
    columns: List[List[str]],
    ribbons: List[Tuple[str, str, float, str, bool]],
    labels: Dict[str, str],
    height: int = 360,
    tooltips: Optional[Dict[str, str]] = None,
    sublabels: Optional[Dict[str, Tuple[str, str]]] = None,
    ribbon_tooltips: Optional[List[str]] = None,
    stub_labels: Optional[Dict[str, str]] = None,
) -> str:
    """A column Sankey as inline SVG: node rectangles plus one Bézier ribbon per flow.

    The shared renderer of V1, V10, V11 and V12. Node placement comes from
    `presentation_style.sankey_node_boxes`, the same function the matplotlib companions use, so
    a node sits in the same place in both outputs. Ribbons leave a node's right face and arrive
    at the next node's left face in the order given, stacking on each face, so a node rectangle
    is exactly filled by the ribbons it carries.

    Credit ribbons (the `bool` of each tuple) are drawn outlined and translucent where cost
    ribbons are solid: a ribbon has no sign, so the distinction has to be structural rather than
    left to colour, and the sections that use it carry a two-line legend saying so.

    Every ribbon keeps **one width from end to end**, taken from the single global unit scale
    `sankey_node_boxes` returns (rule 2.7), and the ribbons on a node face stack to tile it
    exactly. Every flow travels between two *different* columns: since Q23 gave each internal
    party a column of its own, even an inter-actor transfer such as the §559e levy is an ordinary
    left-to-right ribbon, and the looping same-column band this used to draw is gone.

    `tooltips` overrides the hover text of individual nodes where the visible label is not the
    whole truth — a subsidy node reads as its friendly scheme name and carries the raw scheme id
    in its tooltip (Q20). Nodes it does not name keep their label as the tooltip.

    `sublabels` adds the amount line under a node's name as `(full, compact)`; the full form is
    drawn where the node is tall enough for two baselines and the compact one — the node's total —
    everywhere else, with the full split remaining in the tooltip (Q28 R6). `ribbon_tooltips`
    replaces the rounded default hover text of the ribbon at the same index with an exact one,
    which is what makes a ribbon readable to the cent without printing cents on the canvas.

    `stub_labels` gives a net-position stub (Q29 R7) the caller's own wording for a node. It
    matters wherever the section already publishes that net under a sign convention of its own —
    the who-pays-whom chart states costs as positive, so a landlord who *gains* reads "net
    -88,032 EUR" there, and a stub inventing its own `+` beside that label would contradict the
    node it closes. Without an override the stub states the geometric imbalance, `+` when more
    arrives than leaves.
    """
    geometry = sankey_node_boxes(columns, [(s, t, a) for s, t, a, _c, _credit in ribbons])
    boxes = geometry.boxes
    plot_w = _ChartGeometry.WIDTH - _ChartGeometry.LEFT - _ChartGeometry.RIGHT
    plot_h = height - _ChartGeometry.TOP - _ChartGeometry.BOTTOM
    pixels_per_unit = geometry.unit_scale * plot_h

    def node_pixels(node: str) -> Tuple[float, float, float]:
        """(left x, top y, height) of a node in user units."""
        x, y, node_height = boxes[node]
        return (
            _ChartGeometry.LEFT + x * plot_w,
            _ChartGeometry.TOP + (1.0 - y - node_height) * plot_h,
            node_height * plot_h,
        )

    parts = _svg_open(_ChartGeometry.WIDTH, height)
    for index, (source, target, amount, color, is_credit) in enumerate(ribbons):
        if source not in boxes or target not in boxes:
            continue
        ribbon_h = amount * pixels_per_unit
        title = _esc(_ribbon_title(labels, source, target, amount, ribbon_tooltips, index))
        # Q29 R7: one leg per column gap, so a column-skipping ribbon travels its corridor
        # instead of crossing whatever block sits in the way.
        for leg in geometry.ribbon_segments[index]:
            if leg.source not in boxes or leg.target not in boxes:
                continue
            source_x, source_y, source_h = node_pixels(leg.source)
            target_x, target_y, target_h = node_pixels(leg.target)
            # y grows downward here and upward in the layout, so an anchor measured from the
            # node's bottom becomes a distance from its top face read the other way round.
            y0 = source_y + source_h - leg.out_anchor * plot_h - ribbon_h
            y1 = target_y + target_h - leg.in_anchor * plot_h - ribbon_h
            x0 = source_x + SankeyLayout.NODE_WIDTH * plot_w
            control = (target_x - x0) * SankeyLayout.CURVATURE
            parts.append(
                f'<path d="M {x0:.1f},{y0:.1f} C {x0 + control:.1f},{y0:.1f} '
                f'{target_x - control:.1f},{y1:.1f} {target_x:.1f},{y1:.1f} '
                f'L {target_x:.1f},{y1 + ribbon_h:.1f} C {target_x - control:.1f},{y1 + ribbon_h:.1f} '
                f'{x0 + control:.1f},{y0 + ribbon_h:.1f} {x0:.1f},{y0 + ribbon_h:.1f} Z" '
                f'fill="{color}" fill-opacity="{0.18 if is_credit else 0.5}" stroke="{color}" '
                f'stroke-width="{0.8 if is_credit else 0.5}"'
                f'{" stroke-dasharray=\"4 3\"" if is_credit else ""}>'
                f"<title>{title}</title></path>"
            )
    parts.extend(
        _net_stub_svg(geometry, labels, node_pixels, plot_w, plot_h, pixels_per_unit, stub_labels)
    )
    for index, nodes in enumerate(columns):
        for node in nodes:
            if node not in boxes:
                continue
            x, y, node_height = node_pixels(node)
            parts.append(
                _rect(x, y, SankeyLayout.NODE_WIDTH * plot_w, node_height, "var(--ink-1)",
                      (tooltips or labels).get(node, labels.get(node, node)), rx=1)
            )
            label = labels.get(node, node)
            amounts = (sublabels or {}).get(node)
            middle = y + node_height / 2
            if index == 0:
                anchor_x, anchor = x - 6, "end"
            elif index == len(columns) - 1:
                anchor_x, anchor = x + SankeyLayout.NODE_WIDTH * plot_w + 6, "start"
            else:
                anchor_x, anchor = x + SankeyLayout.NODE_WIDTH * plot_w / 2, "middle"
            if amounts is None:
                if index == 0 or index == len(columns) - 1:
                    parts.append(_text(anchor_x, middle + 4, label, 10, anchor))
                else:
                    parts.append(_text(anchor_x, y - 4, label, 10, anchor, "var(--ink-1)"))
                continue
            full, compact = amounts
            amount_line = full if node_height >= _SankeyLabels.MIN_HEIGHT_FOR_SPLIT else compact
            parts.append(_text(anchor_x, middle + _SankeyLabels.NAME_OFFSET, label, 10, anchor))
            parts.append(
                _text(anchor_x, middle + _SankeyLabels.AMOUNT_OFFSET, amount_line,
                      _SankeyLabels.AMOUNT_FONT_SIZE, anchor, "var(--muted)")
            )
    parts.append("</svg>")
    return "".join(parts)


def _xy_lines_svg(
    series: List[Tuple[str, List[Tuple[float, float]], str, float, str]],
    bands: Optional[List[Tuple[List[Tuple[float, float]], List[Tuple[float, float]], str]]] = None,
    x_label: str = "year",
    y_label: str = "",
    annotations: Optional[List[Tuple[float, float, str]]] = None,
    height: int = 230,
) -> str:
    """An xy line chart with optional filled bands — V2's fan, V13's trajectories, V15's gap.

    Each series is `(label, points, colour, stroke width, dash pattern)`; each band is a pair of
    point lists filled between them, which is how both an uncertainty envelope and V15's equity
    gap are expressed with one primitive. The axes span everything given, always including zero
    so the sign of a curve is readable, and the zero line is drawn.

    Annotations are `(x, y, text)` in data units and are placed with a small offset, which is
    what carries V2's "deepest out-of-pocket" and V13's break-even labels.
    """
    bands = bands or []
    annotations = annotations or []
    all_points = [point for _label, points, *_rest in series for point in points]
    for low, high, _color in bands:
        all_points.extend(low + high)
    if not all_points:
        return ""
    x_values = [point[0] for point in all_points]
    y_values = [point[1] for point in all_points]
    x_min, x_max = min(x_values), max(x_values)
    y_min, y_max = min(min(y_values), 0.0), max(max(y_values), 0.0)
    plot_w = _ChartGeometry.WIDTH - _ChartGeometry.LEFT - _ChartGeometry.RIGHT
    plot_h = height - _ChartGeometry.TOP - _ChartGeometry.BOTTOM
    x_scale = plot_w / max(x_max - x_min, 1e-9)
    y_scale = plot_h / max(y_max - y_min, 1e-9)

    def to_x(value: float) -> float:
        return _ChartGeometry.LEFT + (value - x_min) * x_scale

    def to_y(value: float) -> float:
        return _ChartGeometry.TOP + (y_max - value) * y_scale

    parts = _svg_open(_ChartGeometry.WIDTH, height)
    parts.append(_hline(_ChartGeometry.LEFT, _ChartGeometry.WIDTH - _ChartGeometry.RIGHT + 20, to_y(0.0)))
    for low, high, color in bands:
        forward = " ".join(f"{to_x(x):.1f},{to_y(y):.1f}" for x, y in low)
        backward = " ".join(f"{to_x(x):.1f},{to_y(y):.1f}" for x, y in reversed(high))
        parts.append(
            f'<polygon points="{forward} {backward}" fill="{color}" opacity="0.16"></polygon>'
        )
    end_labels: List[Tuple[float, float, str]] = []
    for label, points, color, stroke, dash in series:
        if not points:
            continue
        drawn = " ".join(f"{to_x(x):.1f},{to_y(y):.1f}" for x, y in points)
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        parts.append(
            f'<polyline points="{drawn}" fill="none" stroke="{color}" stroke-width="{stroke}"'
            f"{dash_attr}><title>{_esc(label)}</title></polyline>"
        )
        end_labels.append((to_x(points[-1][0]) + 5, to_y(points[-1][1]) + 4, label))
    for x, y, label in _declutter_labels(end_labels):
        parts.append(_text(x, y, label, 9, "start", "var(--muted)"))
    for x, y, text in annotations:
        parts.append(_text(to_x(x) + 5, to_y(y) - 6, text, 9, "start", "var(--ink-1)"))
    # The axis carries three labels — top, bottom and zero — and drops either extreme when it *is*
    # zero, so an all-positive series does not print "0" twice on the same spot.
    if y_max:
        parts.append(_text(_ChartGeometry.LEFT - 6, to_y(y_max) + 8, _fmt(y_max), 9, "end", "var(--muted)"))
    if y_min:
        parts.append(_text(_ChartGeometry.LEFT - 6, to_y(y_min) + 4, _fmt(y_min), 9, "end", "var(--muted)"))
    parts.append(_text(_ChartGeometry.LEFT - 6, to_y(0.0) + 4, "0", 9, "end", "var(--muted)"))
    parts.append(_text(_ChartGeometry.LEFT, height - 6, x_label, 9, "start", "var(--muted)"))
    if y_label:
        parts.append(_text(_ChartGeometry.LEFT, _ChartGeometry.TOP - 2, y_label, 9, "start", "var(--muted)"))
    parts.append("</svg>")
    return "".join(parts)


def _declutter_labels(
    labels: List[Tuple[float, float, str]], minimum_spacing: float = 11.0
) -> List[Tuple[float, float, str]]:
    """Pushes end-of-line labels apart so none is written on top of another.

    A fan of ten trajectories (V13) or three lines converging at the horizon (V15) ends with all
    its labels within a few pixels of each other, which renders as an unreadable smear. Sorting
    them by y and enforcing a minimum spacing downward keeps every label present — the
    alternative, dropping some, would silently hide which line is which.

    The shift is cosmetic and applies to the *label*, never to the line it names; a label that has
    moved still starts at the line's own end point horizontally, so the association stays visible.
    """
    ordered = sorted(labels, key=lambda item: item[1])
    placed: List[Tuple[float, float, str]] = []
    for x, y, label in ordered:
        if placed and y - placed[-1][1] < minimum_spacing:
            y = placed[-1][1] + minimum_spacing
        placed.append((x, y, label))
    return placed


def _gantt_svg(
    rows: List[Tuple[str, List[Tuple[int, Optional[int], str]], List[Tuple[int, str, Optional[float]]], str]],
    horizon: int,
) -> str:
    """A swimlane/Gantt strip — V7's component lifetimes and V9's lifecycle overview.

    One row per lane: muted span bars, ticked event markers and vertical gridlines every five
    years. Event labels are printed only where they do not collide (events within
    `_ChartGeometry.CLUSTER_YEARS` of an already-labelled one keep their marker and their
    tooltip but lose their text), so a year 0 carrying five subsidy awards stays readable and
    nothing is actually hidden — the tooltip has it.
    """
    if not rows:
        return ""
    height = len(rows) * _ChartGeometry.ROW_HEIGHT + 40
    plot_w = _ChartGeometry.WIDTH - _ChartGeometry.LEFT - _ChartGeometry.RIGHT
    scale = plot_w / max(horizon, 1)

    def to_x(year: float) -> float:
        return _ChartGeometry.LEFT + year * scale

    parts = _svg_open(_ChartGeometry.WIDTH, height)
    for year in range(0, horizon + 1, 5):
        parts.append(
            f'<line x1="{to_x(year):.1f}" y1="6" x2="{to_x(year):.1f}" y2="{height - 26}" '
            f'stroke="var(--grid)"/>'
        )
        parts.append(_text(to_x(year), height - 10, str(year), 9, "middle", "var(--muted)"))
    y = 10.0
    for label, spans, events, color in rows:
        middle = y + _ChartGeometry.ROW_HEIGHT / 2
        parts.append(_text(_ChartGeometry.LEFT - 8, middle + 4, label, 10, "end"))
        for start, end, span_label in spans:
            end_year = end if end is not None else horizon
            parts.append(
                _rect(to_x(start), y + 5, max((end_year - start) * scale, 2.0),
                      _ChartGeometry.ROW_HEIGHT - 12, color, f"{span_label} ({start}-{end_year})", rx=3)
            )
        last_labelled: Optional[int] = None
        for year, event_label, amount in sorted(events, key=lambda item: item[0]):
            tooltip = event_label if amount is None else f"{event_label}: {_fmt(amount)} EUR"
            parts.append(
                f'<line x1="{to_x(year):.1f}" y1="{y + 2:.1f}" x2="{to_x(year):.1f}" '
                f'y2="{y + _ChartGeometry.ROW_HEIGHT - 4:.1f}" stroke="var(--ink-1)" '
                f'stroke-width="2"><title>year {year} - {_esc(tooltip)}</title></line>'
            )
            if last_labelled is None or year - last_labelled > _ChartGeometry.CLUSTER_YEARS:
                text = event_label if amount is None else f"{event_label} {_fmt(amount)}"
                parts.append(_text(to_x(year) + 4, y + 10, text, 8, "start", "var(--muted)"))
                last_labelled = year
        y += _ChartGeometry.ROW_HEIGHT
    parts.append("</svg>")
    return "".join(parts)


def _treemap_svg(tiles: List[Tuple[str, float, str]], height: int = 240) -> str:
    """A squarified treemap — V8's cost structure, one panel per basis.

    Tiles are `(label, area, colour)`; the layout comes from
    `presentation_style.squarified_layout`, the same function the matplotlib companion uses, so
    the two panels are not two different pictures of the same numbers. A label is printed only
    where the tile can hold it; every tile carries its full label and value as a tooltip, which
    is what the HTML variant has over the PNG.
    """
    drawable = [tile for tile in tiles if tile[1] > 0]
    if not drawable:
        return ""
    width = _ChartGeometry.WIDTH // 2 - 20
    parts = _svg_open(width, height)
    layout = squarified_layout([tile[1] for tile in drawable], 0.0, 0.0, float(width), float(height))
    for (label, area, color), (x, y, tile_w, tile_h) in zip(drawable, layout):
        parts.append(_rect(x + 1, y + 1, max(tile_w - 2, 0.5), max(tile_h - 2, 0.5), color,
                           f"{label}: {_fmt(area)} EUR", rx=2))
        if tile_w > 70 and tile_h > 26:
            parts.append(_text(x + 6, y + 16, label, 9, "start", "var(--surface)"))
            parts.append(_text(x + 6, y + 28, _fmt(area), 9, "start", "var(--surface)"))
    parts.append("</svg>")
    return "".join(parts)


def _bridge_svg(
    anchors: Tuple[Tuple[str, UncertainValue], Tuple[str, UncertainValue]],
    steps: List[Tuple[str, float, str]],
) -> str:
    """V4's bridge: two anchor bars with their bands and the floating deltas between them.

    A bridge is not the same shape as the report's older waterfall, which starts at zero and
    walks a staircase of contributions: here the two *anchors* are absolute NPVs and the bars
    between them float at wherever the running total has got to. The axis is therefore fitted
    over the whole excursion of that running total (and always includes zero), which is what
    keeps a large negative first step on the canvas.

    The anchors carry a min/max whisker; the delta bars deliberately do not, because the band of
    a difference is not the difference of the bands.
    """
    (base_label, base_band), (variant_label, variant_band) = anchors
    cursors = [base_band.average]
    for _label, delta, _color in steps:
        cursors.append(cursors[-1] + delta)
    span_min = min([0.0, base_band.minimum, variant_band.minimum] + cursors)
    span_max = max([0.0, base_band.maximum, variant_band.maximum] + cursors)
    height = (len(steps) + 2) * _ChartGeometry.ROW_HEIGHT + 24
    plot_w = _ChartGeometry.WIDTH - _ChartGeometry.LEFT - _ChartGeometry.RIGHT
    scale = plot_w / max(span_max - span_min, 1e-9)

    def to_x(value: float) -> float:
        return _ChartGeometry.LEFT + (value - span_min) * scale

    parts = _svg_open(_ChartGeometry.WIDTH, height)
    y = 6.0

    def anchor_row(label: str, band: UncertainValue, position: float) -> None:
        """One absolute NPV bar with its band, drawn from the zero line."""
        middle = position + _ChartGeometry.ROW_HEIGHT / 2
        parts.append(_text(_ChartGeometry.LEFT - 8, middle + 4, label, 11, "end", "var(--ink-1)", bold=True))
        left_x = min(to_x(0.0), to_x(band.average))
        parts.append(
            _rect(left_x, position + 4, abs(to_x(band.average) - to_x(0.0)),
                  _ChartGeometry.ROW_HEIGHT - 8, "var(--ink-1)",
                  f"{label}: {_band_str(band)}", rx=3)
        )
        parts.append(_hline(to_x(band.minimum), to_x(band.maximum), middle, "var(--muted)", 1.4))
        parts.append(_text(to_x(band.maximum) + 6, middle + 4, _band_str(band), 10, "start", "var(--muted)"))

    anchor_row(base_label, base_band, y)
    y += _ChartGeometry.ROW_HEIGHT
    cursor = base_band.average
    for label, delta, color in steps:
        middle = y + _ChartGeometry.ROW_HEIGHT / 2
        parts.append(_text(_ChartGeometry.LEFT - 8, middle + 4, label, 11, "end"))
        start = min(cursor, cursor + delta)
        parts.append(
            _rect(to_x(start), y + 5, abs(delta) * scale, _ChartGeometry.ROW_HEIGHT - 10, color,
                  f"{label}: {_fmt(delta)} EUR", rx=3)
        )
        parts.append(
            _text(to_x(start) + abs(delta) * scale + 6 if delta >= 0 else to_x(start) - 6,
                  middle + 4, f"{'+' if delta >= 0 else ''}{_fmt(delta)}", 10,
                  "start" if delta >= 0 else "end", "var(--muted)")
        )
        cursor += delta
        parts.append(
            f'<line x1="{to_x(cursor):.1f}" y1="{y + 5:.1f}" x2="{to_x(cursor):.1f}" '
            f'y2="{y + _ChartGeometry.ROW_HEIGHT + 5:.1f}" stroke="var(--grid)"/>'
        )
        y += _ChartGeometry.ROW_HEIGHT
    anchor_row(variant_label, variant_band, y)
    parts.append(
        f'<line x1="{to_x(0.0):.1f}" y1="2" x2="{to_x(0.0):.1f}" y2="{height - 18}" '
        f'stroke="var(--baseline)"/>'
    )
    parts.append("</svg>")
    return "".join(parts)


def _attribution_tornado_svg(rows: List[views.AttributionRow], total: UncertainValue) -> str:
    """V3's two-sided bars: each subject's LOW and HIGH deltas around the average NPV.

    Bars run left for a negative delta and right for a positive one from a zero axis that *is*
    the total average NPV. A mirrored revenue subject can have a positive LOW delta, which puts
    its whole bar on one side — correct, and the section's prose says so, because it looks like a
    bug the first time.
    """
    if not rows:
        return ""
    height = len(rows) * _ChartGeometry.ROW_HEIGHT + 30
    span = max(
        max(abs(row.low_delta_in_euro), abs(row.high_delta_in_euro)) for row in rows
    ) or 1.0
    plot_w = _ChartGeometry.WIDTH - _ChartGeometry.LEFT - _ChartGeometry.RIGHT
    zero_x = _ChartGeometry.LEFT + plot_w / 2
    scale = (plot_w / 2) / span
    parts = _svg_open(_ChartGeometry.WIDTH, height)
    parts.append(
        f'<line x1="{zero_x:.1f}" y1="4" x2="{zero_x:.1f}" y2="{height - 20}" stroke="var(--baseline)"/>'
    )
    y = 6.0
    for row in rows:
        middle = y + _ChartGeometry.ROW_HEIGHT / 2
        parts.append(_text(_ChartGeometry.LEFT - 8, middle + 4, row.subject, 10, "end"))
        for delta, color in ((row.low_delta_in_euro, "var(--g0)"), (row.high_delta_in_euro, "var(--g5)")):
            if not delta:
                continue
            x_from = zero_x + min(delta, 0.0) * scale
            parts.append(
                _rect(x_from, y + 5, abs(delta) * scale, _ChartGeometry.ROW_HEIGHT - 12, color,
                      f"{row.subject}: {_fmt(delta)} EUR", rx=2)
            )
        parts.append(
            _text(zero_x + max(row.high_delta_in_euro, 0.0) * scale + 6, middle + 4,
                  f"{_fmt(row.low_delta_in_euro)} | {_fmt(row.high_delta_in_euro)}", 9, "start",
                  "var(--muted)")
        )
        y += _ChartGeometry.ROW_HEIGHT
    parts.append(
        _text(zero_x, height - 6, f"total band {_band_str(total)}", 9, "middle", "var(--muted)")
    )
    parts.append("</svg>")
    return "".join(parts)


def _monthly_burden_svg(result: LifecycleCostResult) -> str:
    """V14's stacked monthly bars with whiskers on the total — the household's own unit.

    Same geometry as the annual cash-flow chart (separate baselines above and below zero, never
    netted), on the monthly recurring figures from `views.monthly_burden_series` and
    `views.monthly_burden_by_group`. The whiskers are the min/max band of the monthly *total*,
    this chart's one banded mark.

    The capital events the bars exclude return as a dashed overlay segment per year, drawn at that
    year's recurring total plus the replacement reserve: the line is what the month costs once the
    sinking fund for the replacements is paid into, which is the figure a bank would quote.
    """
    burden = views.monthly_burden_series(result)
    totals = burden.series
    per_group = views.monthly_burden_by_group(result, PresentationStyle.CATEGORY_TO_GROUP)
    if not totals:
        return ""
    reserve = burden.replacement_reserve_per_month
    horizon = len(totals) - 1
    max_pos = max([sum(v for v in row.values() if v > 0) for row in per_group] +
                  [value.maximum for value in totals] +
                  [value.average + reserve for value in totals] + [1.0])
    max_neg = max([-sum(v for v in row.values() if v < 0) for row in per_group] +
                  [-min(value.minimum, 0.0) for value in totals] + [0.0])
    width, height, left, top, bottom = _ChartGeometry.WIDTH, 260, 70, 16, 30
    plot_h = height - top - bottom
    scale = plot_h / max(max_pos + max_neg, 1e-9)
    zero_y = top + max_pos * scale
    bar_w = (width - left - 20) / (horizon + 1)
    parts = _svg_open(width, height)
    parts.append(_hline(left, width - 10, zero_y))
    for year, groups in enumerate(per_group):
        x = left + year * bar_w
        y_pos, y_neg = zero_y, zero_y
        for index in range(len(PresentationStyle.DISPLAY_GROUPS)):
            value = groups.get(index, 0.0)
            if not value:
                continue
            bar_h = abs(value) * scale
            tooltip = (
                f"year {year} - {PresentationStyle.DISPLAY_GROUPS[index][0]}: {_fmt(value)} EUR/month"
            )
            if value > 0:
                y_pos -= bar_h
                parts.append(_rect(x + 1, y_pos, bar_w - 2, bar_h, f"var(--g{index})", tooltip))
            else:
                parts.append(_rect(x + 1, y_neg, bar_w - 2, bar_h, f"var(--g{index})", tooltip))
                y_neg += bar_h
        band = totals[year]
        if not band.is_exact():
            centre = x + bar_w / 2
            parts.append(
                f'<line x1="{centre:.1f}" y1="{zero_y - band.maximum * scale:.1f}" '
                f'x2="{centre:.1f}" y2="{zero_y - band.minimum * scale:.1f}" stroke="var(--ink-1)" '
                f'stroke-width="1"><title>year {year} total: {_esc(_band_str(band, "EUR/month"))}'
                f"</title></line>"
            )
        if year % max(1, horizon // 10) == 0:
            parts.append(_text(x + bar_w / 2, height - 12, str(year), 9, "middle", "var(--muted)"))
    if reserve:
        for year, band in enumerate(totals):
            x = left + year * bar_w
            line_y = zero_y - (band.average + reserve) * scale
            parts.append(
                f'<line x1="{x + 1:.1f}" y1="{line_y:.1f}" x2="{x + bar_w - 1:.1f}" '
                f'y2="{line_y:.1f}" stroke="var(--ink-1)" stroke-width="1.4" '
                f'stroke-dasharray="5 3"><title>year {year} with replacement reserve: '
                f"{_fmt(band.average + reserve)} EUR/month (of which {_fmt(reserve)} reserve)"
                f"</title></line>"
            )
        parts.append(
            _text(left + 4, top + 10, f"— — with replacement reserve (+{_fmt(reserve)} EUR/month)",
                  9, "start", "var(--muted)")
        )
    parts.append(_text(left - 6, top + 10, _fmt(max_pos), 9, "end", "var(--muted)"))
    parts.append(_text(left - 6, zero_y + 4, "0", 9, "end", "var(--muted)"))
    parts.append(_text(width - 10, height - 12, "year", 9, "end", "var(--muted)"))
    parts.append("</svg>")
    return "".join(parts)


def _cost_of_credit_svg(credit: views.TotalCostOfCredit) -> str:
    """V5's companion panel: one stacked bar of principal, interest, fees and the grant credit.

    The consumer-credit disclosure a loan document carries — "you borrow 50,000 and pay back
    63,400" — as a single bar, with the repayment grant drawn as a credit segment to the left of
    zero because it is money coming back rather than a smaller cost.
    """
    segments = [
        ("principal", credit.principal_in_euro - credit.unrepaid_principal_in_euro, "var(--g0)"),
        ("interest", credit.interest_in_euro, "var(--g5)"),
        ("fees", credit.fees_in_euro, "var(--g2)"),
    ]
    segments = [segment for segment in segments if segment[1]]
    if not segments:
        return ""
    height, left, bar_h = 90, 150, 26
    span = max(sum(value for _label, value, _color in segments) + credit.grants_in_euro, 1e-9)
    scale = (_ChartGeometry.WIDTH - left - 150) / span
    parts = _svg_open(_ChartGeometry.WIDTH, height)
    cursor = float(left)
    parts.append(_text(left - 8, 30, "you repay", 10, "end"))
    for label, value, color in segments:
        parts.append(_rect(cursor, 16, value * scale, bar_h, color, f"{label}: {_fmt(value)} EUR", rx=2))
        if value * scale > 60:
            parts.append(_text(cursor + 6, 33, f"{label} {_fmt(value)}", 9, "start", "var(--surface)"))
        cursor += value * scale
    parts.append(_text(cursor + 8, 33, f"total {_fmt(credit.total_repaid_in_euro)} EUR", 10, "start",
                       "var(--ink-1)", bold=True))
    if credit.grants_in_euro:
        parts.append(_text(left - 8, 68, "grant back", 10, "end"))
        parts.append(
            _rect(left, 54, credit.grants_in_euro * scale, 18, "var(--g3)",
                  f"repayment grant: {_fmt(credit.grants_in_euro)} EUR", rx=2)
        )
        parts.append(
            _text(left + credit.grants_in_euro * scale + 8, 68,
                  f"-{_fmt(credit.grants_in_euro)} EUR", 9, "start", "var(--muted)")
        )
    parts.append("</svg>")
    return "".join(parts)


# ---------------------------------------------------------------------------- HTML report (A)

class _ReportCss:
    """The report stylesheet, inlined into the self-contained HTML.

    Inlined rather than linked because the report has to be a single file that works from a
    network share, an email attachment or an archive with no network at all — the same rule that
    forces the charts to be hand-written SVG. Everything the page needs is here; nothing is
    fetched.

    The whole palette is declared as CSS custom properties on `:root` and redeclared under
    `@media (prefers-color-scheme: dark)`, which is what makes the inline charts theme-aware: a
    bar filled with `var(--g3)` re-colours with the reader's system setting, something a
    rasterized chart cannot do. `--g0`..`--g7` are the eight display-group hues and mirror
    `PresentationStyle.GROUP_COLORS_LIGHT` / `GROUP_COLORS_DARK` index for index, so a group
    keeps its colour across the HTML, its SVGs and the matplotlib PNGs — if one list is edited,
    this one has to move with it. `--ink-*`, `--muted`, `--surface`, `--grid` and `--baseline`
    are the chrome roles, and `--good`/`--warning`/`--critical` back the `.status.PASS` /
    `.status.WARN` / `.status.FAIL` classes the plausibility panel emits from the finding status
    verbatim.

    The chapter rules (`h2.chapter`, `p.chapter-intro`, `.chapter-tag`) style the Q24 structure:
    a chapter is a rule-topped heading *between* the section cards rather than a card of its own,
    its authored lead-in sits in the same margin as the heading, and the small muted tag inside a
    section heading names the chapter that section is being read in — needed because the same
    section name now appears in more than one chapter.

    The `dl`/`dt`/`dd` rules style the "Terms used here" definition lists of every section's
    explanation block. They deliberately reuse the existing roles — the term in `--ink-1` like a
    heading, the definition in `--ink-2` like the surrounding prose — so a disclosure a reader
    opens looks like the rest of the section rather than like a glossary pasted into it.
    """

    CSS = """
:root { color-scheme: light dark;
  --surface:#fcfcfb; --page:#f9f9f7; --ink-1:#0b0b0b; --ink-2:#52514e; --muted:#898781;
  --grid:#e1e0d9; --baseline:#c3c2b7; --border:rgba(11,11,11,0.10);
  --good:#0ca30c; --warning:#fab219; --critical:#d03b3b;
  --g0:#2a78d6; --g1:#1baf7a; --g2:#eda100; --g3:#008300; --g4:#4a3aa7; --g5:#e34948; --g6:#e87ba4; --g7:#eb6834; }
@media (prefers-color-scheme: dark) { :root {
  --surface:#1a1a19; --page:#0d0d0d; --ink-1:#ffffff; --ink-2:#c3c2b7; --muted:#898781;
  --grid:#2c2c2a; --baseline:#383835; --border:rgba(255,255,255,0.10);
  --g0:#3987e5; --g1:#199e70; --g2:#c98500; --g3:#008300; --g4:#9085e9; --g5:#e66767; --g6:#d55181; --g7:#d95926; } }
body { font-family: system-ui, -apple-system, "Segoe UI", sans-serif; background: var(--page);
  color: var(--ink-1); margin: 0; padding: 24px; }
main { max-width: 960px; margin: 0 auto; }
section { background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
  padding: 18px 22px; margin-bottom: 18px; overflow-x: auto; }
h1 { font-size: 22px; } h2 { font-size: 16px; margin: 2px 0 10px; }
h3 { font-size: 15px; margin: 2px 0 10px; }
h2.chapter { font-size: 19px; margin: 26px 4px 4px; padding-top: 10px;
  border-top: 2px solid var(--baseline); }
p.chapter-intro { margin: 0 4px 12px; max-width: 62em; }
.chapter-tag { color: var(--muted); font-weight: 400; font-size: 12px; margin-left: 6px; }
p.sub { color: var(--ink-2); font-size: 13px; margin-top: 0; }
table { border-collapse: collapse; font-size: 12.5px; width: 100%; }
th { text-align: left; color: var(--muted); font-weight: 600; border-bottom: 1px solid var(--baseline);
  padding: 4px 10px 4px 0; }
td { padding: 4px 10px 4px 0; border-bottom: 1px solid var(--grid);
  font-variant-numeric: tabular-nums; }
.legend { display: flex; flex-wrap: wrap; gap: 12px; font-size: 12px; color: var(--ink-2); margin: 6px 0 10px; }
.chip { display: inline-flex; align-items: center; gap: 5px; }
.swatch { width: 10px; height: 10px; border-radius: 3px; display: inline-block; }
.status { font-weight: 600; font-size: 12px; }
.status.PASS { color: var(--good); } .status.WARN { color: var(--warning); } .status.FAIL { color: var(--critical); }
details { margin-top: 8px; } summary { cursor: pointer; color: var(--ink-2); font-size: 13px; }
details > p.sub:first-of-type { margin-top: 8px; }
dl { margin: 8px 0 2px; font-size: 13px; color: var(--ink-2); }
dt { color: var(--ink-1); font-weight: 600; margin-top: 7px; }
dd { margin: 1px 0 0 16px; }
.flag { color: var(--critical); font-weight: 600; }
footer { color: var(--muted); font-size: 12px; margin: 10px 4px; }
"""


def _origin_label(row: ResolvedInputRow) -> str:
    """This report's spelling of a resolved row's origin (the audit decided the precedence).

    Answers "where did this price actually come from" in one cell of the input-audit table:
    a config override (with the source it cited, or a loud `NO SOURCE`), the database entry key
    that matched, or `unresolved`. The precedence between those is an engine decision made once
    in `input_audit.py` and written identically to `cost_audit.csv`; this only chooses the words,
    so a reviewer comparing the HTML table with the CSV sees the same origin either way.
    """
    if row.origin_kind == OriginKind.ORIGIN_OVERRIDE:
        return f"override ({row.override_source or 'NO SOURCE'})"
    if row.origin_kind == OriginKind.ORIGIN_DATABASE:
        return str(row.entry_key)
    return "unresolved"


def _audit_section_html(audit: InputAuditReport, context: _ChapterContext) -> str:
    """The input audit: are the declared facts and resolved prices right?

    Renders the rows `audit.build_input_audit` resolved; override precedence, the flags and the
    source list are decided there, once, and written to `cost_audit.csv` from the same rows.

    It comes first after the panel because everything downstream is a consequence of these
    numbers: one row per priced fact with its size, its resolved unit price and lifetime, where
    that price came from and any flags raised while resolving it. This is the §9.5 "review one
    table instead of 46 files" workflow — a config-wiring mistake such as a 5000 kW heat pump is
    an implausible size or price in this table long before it is a surprising NPV. The sources
    table is appended so the prices above can be checked for currency in the same place.
    """
    rows = [
        "<tr><td>{subject}</td><td>{cls}</td><td>{size:,.1f} {unit}</td><td>{price}</td>"
        "<td>{life}</td><td>{share}</td><td>{origin}</td><td class=\"flag\">{flags}</td></tr>".format(
            subject=_esc(row.subject),
            cls=_esc(row.asset_class),
            size=row.size,
            unit=_esc(row.size_unit),
            price=_esc(_band_str(row.unit_price_in_euro, "EUR/unit")),
            life=f"{row.lifetime_in_years:g} a" if row.lifetime_in_years else "-",
            # Q22: the Sowieso share is an input of the same standing as the unit price — it
            # decides how much of a measure the counterfactual pays for — so it is audited here.
            # Q26 F7: the share and the cost it applies to, so the credit multiplies out here.
            share=(
                f"{row.anyway_share:.0%} x {row.anyway_basis_in_euro:,.0f} EUR = "
                f"{row.anyway_share * row.anyway_basis_in_euro:,.0f} EUR"
                if row.anyway_share is not None and row.anyway_basis_in_euro
                else (f"{row.anyway_share:.0%}" if row.anyway_share is not None else "-")
            ),
            origin=_esc(_origin_label(row)),
            flags=_esc("; ".join(row.flags)),
        )
        for row in audit.rows
    ]
    return (
        _section_open(ReportSections.INPUT_AUDIT, context)
        + _explanation_html(ReportSections.INPUT_AUDIT, context)
        + "<table><tr><th>Subject</th><th>Asset class</th><th>Size</th><th>Unit price</th>"
        "<th>Lifetime</th><th>Anyway credit (share x basis)</th><th>Origin</th><th>Flags</th></tr>"
        + "".join(rows) + "</table>"
        + _sources_table_html(audit)
        + "</section>"
    )


def _sources_table_html(audit: InputAuditReport) -> str:
    """The §3.10 source registry entries this evaluation cited, as resolved by the audit.

    The bibliography of the run: which registry entries the numbers above actually came from,
    with citation, kind, retrieval date and link. It exists because §3.10 forbids unsourced
    datapoints, and a report that shows prices without saying where they are from cannot be
    reviewed for currency — a reader spotting a 2019 retrieval date on an energy price knows to
    distrust the bill section. Collapsed by default (it is reference material, not a finding)
    and omitted entirely when the audit resolved no sources.
    """
    if not audit.sources:
        return ""
    rows = [
        [
            _esc(resolved.source_id),
            _esc(resolved.citation),
            _esc(resolved.kind or "-"),
            _esc(resolved.retrieved or "-"),
            f'<a href="{_esc(resolved.url)}">link</a>' if resolved.url else "-",
        ]
        for resolved in audit.sources
    ]
    return _details(
        f"sources used ({len(rows)} registry entries, §3.10)",
        _table(["Id", "Citation", "Kind", "Retrieved", "Url"], rows),
    )


def _assumptions_section_html(
    result: LifecycleCostResult, context: _ChapterContext, co2_damage_priced: bool = False
) -> str:
    """Every economic assumption the run was priced under, with value and source (Q26 F2).

    The boundary between input and result, placed directly after the input audit because that is
    where a reader has just finished checking *what* was priced and needs to know *under which
    assumptions*. The audit answers "which price did this device resolve to"; this answers "at
    what interest rate, over what horizon, with which escalation and which tariff" — the causes
    behind every consequence the rest of the report draws (rule 2.9).

    Rows come from `views.economic_assumptions`, which reads the parameters and the assumption
    record the evaluator resolved; nothing here is a literal. The computed rows are marked as
    such, so the annuity factor is not mistaken for something somebody chose, and a value with no
    data-layer source states `configuration`, which is a statement about its provenance rather
    than a blank.
    """
    rows = views.economic_assumptions(result, co2_damage_priced=co2_damage_priced)
    if not rows:
        return ""
    grouped = []
    for group in views.AssumptionGroups.ORDER:
        in_group = [row for row in rows if row.group == group]
        if not in_group:
            continue
        grouped.append([f"<b>{_esc(group)}</b>", "", ""])
        grouped.extend(
            [
                _esc(row.name) + (" <span class='sub'>(computed)</span>" if row.is_computed else ""),
                _esc(row.value),
                _esc(row.source),
            ]
            for row in in_group
        )
    missing = (
        ""
        if result.assumptions is not None else
        "<p class='sub'>This result was stored before the resolved assumption record existed, so "
        "the escalation rates and tariff terms are not available here; the calculation frame and "
        "the building quantities below are complete.</p>"
    )
    return (
        _section_open(ReportSections.ASSUMPTIONS, context, result.perspective_id)
        + _explanation_html(ReportSections.ASSUMPTIONS, context)
        + missing
        + "<p class='sub'>Every figure elsewhere in this report is one of these values, escalated, "
          "discounted or divided. A source of <code>configuration</code> means the run chose the "
          "value rather than reading it from reviewed data.</p>"
        + _table(["Assumption", "Value", "Source"], grouped)
        + "</section>"
    )


def _investment_section_html(result: LifecycleCostResult, context: _ChapterContext) -> str:
    """The investment build-up: year-0 waterfall per subject.

    Answers "is the money that leaves the account in year 0 the money this measure should
    cost?" — one waterfall per component walking device + installation + planning + removal
    - subsidies - loan disbursement down to the net outflow, plus a table of gross, support and
    net per subject. It sits directly after the input audit because that is the order the
    numbers are built in: the input audit shows the unit prices, this shows what they add up to, and
    a component that is missing or priced from the wrong field is visible in both.

    A binding subsidy cap shows here as support that stops short of the scheme's headline rate,
    and a financed perspective shows the loan disbursement cancelling most of the outflow. Sunk
    cost (the written-off residual book value of a replaced asset, §4.1) is appended as a note
    rather than as a step, because it is reported but deliberately excluded from the decision
    KPIs. Only subjects with a year-0 flow appear; the section renders empty when none do.
    """
    blocks = []
    build_ups = views.year_zero_build_up(result)
    net_of_subsidies = views.investment_net_of_subsidies(result)
    for subject in result.component_breakdowns:
        build_up = build_ups.get(subject)
        if build_up is None:
            continue
        steps: List[Tuple[str, float, str]] = []
        for category, label in (
            (CostCategory.INVESTMENT, "Device + installation"),
            (CostCategory.PLANNING, "Planning"),
            (CostCategory.REMOVAL, "Removal of old device"),
            (CostCategory.SUBSIDY, "Subsidies"),
            (CostCategory.LOAN_DISBURSEMENT, "Loan disbursement"),
        ):
            value = build_up.by_category_in_euro.get(category)
            if value:
                steps.append((label, value, f"var(--g{group_of(category)})"))
        if steps:
            blocks.append(f"<h3 style='font-size:13px;margin:14px 0 2px'>{_esc(subject)}</h3>")
            blocks.append(_waterfall_svg(steps, "Net year-0 outflow", build_up.net_outflow_in_euro))
    if not blocks:
        return ""
    table_rows = []
    for subject, net in net_of_subsidies.items():
        breakdown = result.component_breakdowns[subject]
        table_rows.append(
            [
                _esc(subject),
                _esc(_band_str(breakdown.investment_gross_in_euro)),
                _esc(_band_str(breakdown.subsidies_nominal_in_euro)),
                _esc(_band_str(net)),
            ]
        )
    sunk = result.sunk_cost_written_off_in_euro
    sunk_note = ""
    if sunk.maximum > 0:
        sunk_note = (
            f"<p class='sub'>Written-off residual book value of replaced assets (sunk cost, §4.1 — "
            f"reported, excluded from decision KPIs): <b>{_esc(_band_str(sunk))}</b></p>"
        )
    return (
        _section_open(ReportSections.INVESTMENT_BUILD_UP, context, "year 0")
        + _explanation_html(ReportSections.INVESTMENT_BUILD_UP, context) + "".join(blocks)
        + _details("investment table", _table(["Subject", "Gross investment", "Subsidies", "Net"], table_rows))
        + sunk_note + "</section>"
    )


def _timeline_detail_table(result: LifecycleCostResult) -> str:
    """Every flow behind the timeline chart: (year, subject, category) with nominal band and
    discounted value — the §3.6 canonical timeline as a verification table.

    Rows, ordering, the float-noise cut-off and the subtotals all come from
    `views.timeline_detail_rows`; this only lays them out.
    """
    rows: List[List[str]] = []
    shares = result.anyway_share_by_subject
    bases = result.anyway_basis_by_subject
    for detail_year in views.timeline_detail_rows(result):
        for row in detail_year.rows:
            # Q22: an anyway credit is `share x like-for-like cost`, so the category cell of that
            # one row carries the share it was computed at. Without it the table states a credit
            # whose basis the reader cannot reconstruct from anything else on the page. Q26 F7
            # adds the basis itself, so the row multiplies out to the amount beside it.
            category = row.category.value
            if row.category == CostCategory.ANYWAY_COST_CREDIT and row.subject in shares:
                basis = bases.get(row.subject)
                category = (
                    f"{category} (anyway {shares[row.subject]:.0%} x {basis:,.0f} EUR)"
                    if basis else f"{category} (anyway share {shares[row.subject]:.0%})"
                )
            rows.append(
                [
                    str(row.year),
                    _esc(row.subject),
                    _esc(category),
                    _esc(_band_str(row.nominal_in_euro)),
                    _fmt(row.discounted_average_in_euro),
                ]
            )
        rows.append(
            [
                f"<b>{detail_year.year}</b>",
                "<b>year total</b>",
                "",
                f"<b>{_esc(_band_str(detail_year.nominal_total_in_euro))}</b>",
                f"<b>{_fmt(detail_year.discounted_total_average_in_euro)}</b>",
            ]
        )
    return _table(["Year", "Subject", "Category", "Nominal", "Discounted (avg)"], rows)


def _timeline_section_html(matrix: EvaluationMatrix, context: _ChapterContext) -> str:
    """The cash-flow timeline: annual cash flows + cumulative discounted cost, per perspective.

    Answers "does the money arrive in the right years, and does the year-by-year story add up to
    the headline NPV?" This is the report's view of the §3.6 canonical timeline, and it is
    assembled as a chain a reviewer can walk down: legend, nominal stacked bars, the discounted
    cumulative curve with the NPV label, the loan chart when financed, then the NPV-by-category
    table and finally the full year × subject × category detail table, so any bar in the chart
    can be resolved to the individual flows behind it.

    One collapsible block per perspective, the first open, because the same timeline read under
    different scopes is exactly what makes an actor split or a subsidy mode comprehensible. The
    legend lists only the display groups this perspective's scoped timeline actually contains.
    """
    blocks = []
    for index, (perspective_id, result) in enumerate(matrix.results.items()):
        groups_present = sorted(
            {group_of(entry.category) for entry in result.scoped_timeline().entries}
        )
        body = (
            _legend_html(groups_present)
            + _annual_flow_svg(result)
            + "<p class='sub'>Cumulative discounted cost (separate axis — the horizon NPV):</p>"
            + _cumulative_npv_svg(result)
            + _details("NPV by cost category", _category_table(result))
            + _details(
                "cash-flow detail table (year x subject x category — every flow behind the chart)",
                _timeline_detail_table(result),
            )
        )
        open_attr = " open" if index == 0 else ""
        blocks.append(
            f"<details{open_attr}><summary><b>{_esc(perspective_id)}</b> — "
            f"NPV {_esc(_band_str(result.total_npv_in_euro))}</summary>{body}</details>"
        )
    return (
        _section_open(ReportSections.CASH_FLOW_TIMELINE, context)
        + _explanation_html(ReportSections.CASH_FLOW_TIMELINE, context)
        + _anyway_share_caption(next(iter(matrix.results.values())))
        + "".join(blocks) + "</section>"
    )


def _anyway_share_caption(result: LifecycleCostResult) -> str:
    """The run-specific line stating at which Sowieso share each anyway credit was computed (Q22).

    The prose explains what an anyway share *is*; this says what it *was* here, which is the half
    a reader cannot get anywhere else on the page. A share below 100 % is the interesting case —
    it means the measure was a first-time improvement and only the repair share of it was a cost
    the building would have caused regardless — so the sentence names it per subject rather than
    averaging it away. Empty when the run credits nothing, which is most runs.
    """
    shares = result.anyway_share_by_subject
    if not shares:
        return ""
    bases = result.anyway_basis_by_subject
    parts = []
    for subject, share in sorted(shares.items()):
        basis = bases.get(subject)
        if basis:
            # Q26 F7: the full multiplication, with the base the ledger recorded — a share beside
            # a credit does not let a reader check either of them.
            parts.append(
                f"{_esc(subject)} {share:.0%} x {basis:,.0f} EUR = {share * basis:,.0f} EUR"
            )
        else:
            parts.append(f"{_esc(subject)} {share:.0%}")
    stated = "; ".join(parts)
    return (
        "<p class='sub'>Anyway credits in this run are booked at "
        f"<b>share x like-for-like cost = credit</b>: {stated} (nominal, in the credit's own "
        "year). A share below 100 % means the measure was a first-time improvement, so only that "
        "fraction of it would have been spent without the renovation.</p>"
    )


def _energy_section_html(result: LifecycleCostResult, context: _ChapterContext) -> str:
    """The energy bill: year-1 decomposition per carrier with implied effective prices.

    **The fastest unit-mix-up detector in the report**, and the reason it is placed this early:
    dividing what a carrier cost in year 1 by how much of it was bought must give back a price
    the reader recognizes, and no domain expertise is needed to see that 0.0003 or 312 EUR/kWh
    is wrong. A mistake anywhere between the meter, the annualization and the tariff — a Wh/kWh
    confusion, a rate stored in cents, a missing time-of-use band — lands on this one number,
    which is why the same figure is also checked automatically in the plausibility panel.

    The whiskers show each carrier's year-1 flows as a band; the collapsible table gives the
    quantity, the cost, the implied effective price and the split into working, standing,
    capacity and CO2-price components. Feed-in revenue appears as a negative contribution in the
    electricity carrier's component list but is deliberately excluded from the price numerator
    and the band — a credit is not part of what a kWh costs (see `views.carrier_year_one_bills`).
    """
    bills = views.carrier_year_one_bills(result)
    if not bills:
        return ""
    rows: List[Tuple[str, UncertainValue]] = []
    detail_rows = []
    for carrier, bill in bills.items():
        rows.append(
            (f"{carrier} ({bill.annual_quantity_in_kwh:,.0f} kWh/a)", bill.year_one_band_in_euro)
        )
        breakdown = ", ".join(
            f"{category.value}: {_fmt(value)}" for category, value in bill.by_category_in_euro.items()
        )
        detail_rows.append(
            f"<tr><td>{_esc(carrier)}</td><td>{bill.annual_quantity_in_kwh:,.0f}</td>"
            f"<td>{_fmt(bill.total_excluding_feed_in_in_euro)}</td>"
            f"<td><b>{bill.effective_price_in_euro_per_kwh:,.3f}</b></td>"
            f"<td>{_esc(breakdown)}</td></tr>"
        )
    return (
        _section_open(ReportSections.ENERGY_BILL, context, "year 1")
        + _explanation_html(ReportSections.ENERGY_BILL, context)
        + _whisker_svg(rows, "EUR/a")
        + "<details><summary>decomposition table</summary><table>"
        "<tr><th>Carrier</th><th>Quantity [kWh/a]</th><th>Cost year 1 [EUR]</th>"
        "<th>Effective [EUR/kWh]</th>"
        "<th>Components</th></tr>" + "".join(detail_rows) + "</table></details></section>"
    )


def _subsidy_composition_svg(matrix: EvaluationMatrix) -> str:
    """Per measure: net cost (blue) + subsidy amount (green) — how far the support carries.

    The visual half of the subsidies section, answering "what fraction of each measure does the support
    actually cover?" — the number a homeowner asks for and the one a scheme's headline
    percentage rarely equals once caps and eligible-cost rules bite. Both segments and the
    printed percentage come from `views.subsidy_share_of_gross`, including its `min(subsidy,
    gross)` clamp, so this chart and the matplotlib investment waterfall cannot disagree.

    Perspective selection is the fiddly part: it prefers a perspective that carries catalog
    decisions, and falls back to any perspective with subsidy flows, which is what makes the
    chart appear for the §10.1 legacy flat shim (support with no award trail behind it). Renders
    empty when no perspective has support at all.
    """
    result = next(
        (res for res in matrix.results.values() if any(res.subsidy_decisions)), None
    )
    if result is None:
        # No catalog decisions (e.g. the flat shim): use any perspective with subsidy flows.
        result = next(
            (
                res
                for res in matrix.results.values()
                if any(b.subsidies_nominal_in_euro.maximum > 0 for b in res.component_breakdowns.values())
            ),
            None,
        )
    if result is None:
        return ""
    shares = list(views.subsidy_share_of_gross(result).values())
    if not shares or not any(share.subsidy_in_euro for share in shares):
        return ""
    width, row_h, left = 860, 26, 220
    height = len(shares) * row_h + 10
    peak = max(share.gross_in_euro for share in shares)
    scale = (width - left - 150) / max(peak, 1e-9)
    parts = _svg_open(width, height)
    y = 4.0
    for share in shares:
        subject, gross, subsidy, net = (
            share.subject, share.gross_in_euro, share.subsidy_in_euro, share.net_in_euro
        )
        parts.append(_text(left - 8, y + row_h / 2 + 4, subject, 11, "end"))
        parts.append(_rect(left, y + 4, net * scale, row_h - 8, "var(--g0)",
                           f"{subject} - net cost after subsidies: {_fmt(net)} EUR", rx=2))
        if subsidy > 0:
            parts.append(_rect(left + net * scale + 1.5, y + 4, max(subsidy * scale - 1.5, 0.5), row_h - 8,
                               "var(--g3)", f"{subject} - subsidies: {_fmt(subsidy)} EUR", rx=2))
        parts.append(_text(left + gross * scale + 6, y + row_h / 2 + 4,
                           f"{share.share_of_gross:.0%} funded", 10, "start", "var(--muted)"))
        y += row_h
    parts.append("</svg>")
    return (
        '<div class="legend"><span class="chip"><span class="swatch" style="background:var(--g0)"></span>'
        'net cost</span><span class="chip"><span class="swatch" style="background:var(--g3)"></span>'
        "subsidies</span></div>" + "".join(parts)
    )


def _decision_content_key(decision) -> Tuple:
    """Everything about a decision a reader of the report would notice.

    Two perspectives that reached the same conclusion about a measure should be reported once;
    two that reached different conclusions must both be reported. This key is where that line is
    drawn: the measure, and for every scheme the solver touched its id, its outcome and the detail
    that outcome carries — the amount and payout of an award, the reason of a rejection, the
    unanswered fields of an open question. Amounts are rounded to the cent so that float noise in
    the last digits cannot split one decision into two.

    Deliberately renderer-independent: the upfront amount, the award total and the payout note the
    three renderings can show are all in the key, so they group the same perspectives and cannot
    disagree about which decisions are "the same". The payout note carries the terms of the awards
    that have no euro amount — loan interest rate and term, operational rate and duration — which
    are otherwise invisible to the key and would collapse two genuinely different loan offers.
    """
    return (
        decision.measure_subject,
        tuple(
            (
                award.scheme_id,
                "APPLIED",
                round(award.upfront_amount.average, 2),
                round(views.award_total_amount(award).average, 2),
                award.payout_kind.value,
                views.describe_award(award).payout_note,
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


def _subsidy_awards_table(matrix: EvaluationMatrix) -> str:
    """All awards across measures: scheme, amount band, payout kind, binding caps.

    The tabular form of the §5.4 audit trail: every applied award with what it is worth, how it
    is paid out (upfront grant, repayment grant, scheduled tax credit) and which uncertainty
    slots its cap bound in. The payout kind matters to a reviewer because it changes *when* the
    money lands and therefore its present value, and a cap that binds only in the HIGH slot
    explains an asymmetric band elsewhere in the report.

    Amounts come from `views.describe_award`, so a scheduled payout is shown as the sum of its
    instalments rather than as its zero upfront amount, and an award with no euro amount of its
    own (loan terms, an operational rate) is shown by its terms rather than as a zero. Rows are
    de-duplicated by decision
    *content* (`_decisions_by_content`), not by measure name: perspectives that awarded a measure
    the same way share one row, named in the "Perspectives" column, while a perspective that
    decided differently gets its own rows. Returns empty when no award applied anywhere.
    """
    rows = []
    for decision, perspective_ids in _decisions_by_content(matrix):
        note = _perspectives_note(perspective_ids, matrix)
        for award in decision.applied:
            presentation = views.describe_award(award)
            rows.append([
                _esc(decision.measure_subject),
                _scheme_html(presentation.display_name, presentation.scheme_id),
                _esc(_award_amount_str(presentation)),
                # Q26 F8: the multiplication and the ceiling verdict, so an amount can be checked
                # against the rate and the basis that produced it.
                _esc("; ".join(
                    part for part in (presentation.arithmetic, presentation.cap_verdict) if part
                ) or "-"),
                _esc(presentation.payout_kind),
                _esc(", ".join(presentation.caps_binding) or "-"),
                _esc(note),
            ])
    if not rows:
        return ""
    return _details(
        "awards table (§5.4 audit trail)",
        _table(
            ["Measure", "Scheme", "Amount", "Arithmetic", "Payout", "Caps binding (slots)",
             "Perspectives"],
            rows,
        ),
    )


def _subsidy_section_html(matrix: EvaluationMatrix, context: _ChapterContext) -> str:
    """The subsidies section: the cumulation solver's audit trail, rendered.

    Answers "why did this measure get this much support, and what did it miss?" — which is the
    question a subsidy engine has to be able to answer to be trusted at all. Each measure gets a
    card listing what APPLIED (with the slots any cap bound in), what was REJECTED and the
    reason, and what is still OPEN because a required questionnaire field is unanswered, with
    the upper bound the open questions could still unlock. That last line is the actionable one
    for a user: it quantifies what answering the questionnaire is worth.

    An award is worth `views.describe_award`'s total, not its upfront amount: the card used to
    print `upfront_amount`, which is zero for a tax-credit schedule, an operational rate and loan
    terms, so a §35c credit worth 2,060 EUR appeared as "0.00 EUR" while the SUBSIDY category NPV
    beside it counted it.

    One card per *distinct* decision, not per measure (`_decisions_by_content`): perspectives that
    decided a measure identically share a card and are named in its heading, and a perspective that
    decided differently — a different subsidy mode, a different installation context — gets a card
    of its own next to it. Before this the first perspective to mention a measure won and the rest
    were dropped without a trace, so the section was silently incomplete precisely on the measures
    whose support depends on the view taken.

    When no catalog ships for the run's country there are no decisions to show, and the section
    substitutes a note that the §10.1 legacy flat shim is doing the work instead — an audit trail
    requires a catalog. The section is omitted entirely only when there is neither a decision nor
    any support to draw.
    """
    cards = []
    for decision, perspective_ids in _decisions_by_content(matrix):
        note = _perspectives_note(perspective_ids, matrix)
        lines = [
            f"<h3 style='font-size:13px;margin:12px 0 4px'>{_esc(decision.measure_subject)} "
            f"<span style='font-weight:400;color:var(--muted)'>({_esc(note)})</span></h3><ul>"
        ]
        for award in decision.applied:
            presentation = views.describe_award(award)
            cap_note = (
                f" — cap binding in {', '.join(presentation.caps_binding)}"
                if presentation.caps_binding else ""
            )
            lines.append(
                f"<li><span class='status PASS'>APPLIED</span> "
                f"{_scheme_html(presentation.display_name, presentation.scheme_id)}: "
                f"{_esc(_award_amount_str(presentation))}"
                f"{_esc(_award_arithmetic_str(presentation))}{_esc(cap_note)}</li>"
            )
        for reject in decision.rejected:
            lines.append(
                f"<li><span class='status FAIL'>REJECTED</span> "
                f"{_scheme_html(reject.get('display_name'), reject['scheme_id'])}: "
                f"{_esc(reject['reason'])}</li>"
            )
        for item in decision.undetermined:
            lines.append(
                f"<li><span class='status WARN'>OPEN</span> "
                f"{_scheme_html(item.get('display_name'), item['scheme_id'])}: "
                f"missing {_esc(', '.join(item['missing_fields']))}</li>"
            )
        if decision.undetermined_upper_bound_in_euro > 0:
            lines.append(
                f"<li><b>Answering the open questions could unlock up to "
                f"{_fmt(decision.undetermined_upper_bound_in_euro)} EUR.</b></li>"
            )
        lines.append("</ul>")
        cards.append("".join(lines))
    composition = _subsidy_composition_svg(matrix)
    if not cards and not composition:
        return ""
    # The two states of this section: with a catalog the cards below are the audit trail, without
    # one the flat legacy shim applies and the state note says which of the two the reader has.
    caption = "" if cards else (
        '<p class="sub">No subsidy catalog is active for this country — the flat legacy shim '
        "shares from the device entries apply (cost_spec.md §10.1; an audit trail requires a "
        "catalog, see subsidy_catalog/).</p>"
    )
    return (
        _section_open(ReportSections.SUBSIDIES, context)
        + _explanation_html(ReportSections.SUBSIDIES, context)
        + caption
        + composition
        + "".join(cards)
        + _subsidy_awards_table(matrix)
        + "</section>"
    )


def _perspective_section_html(matrix: EvaluationMatrix, context: _ChapterContext) -> str:
    """The perspectives section: equivalent annual cost across perspectives, with bands and the table.

    Answers "is the perspective model itself behaving?" All perspectives on one axis make the
    orderings that must hold visible without arithmetic: a gross view sits above its net
    counterpart, operating-only below brownfield, and the macroeconomic row differs from the
    financial one only by transfers and CO2 damage. A violation of any of those points at the
    engine or the perspective bundle, not at the input data — which is why this section sits
    after the ones that validate inputs.

    The table beneath carries the four headline KPIs per perspective (NPV, equivalent annual
    cost, monthly cost in year 1, system cost per unit of heat), each as a band, so a reader can pick
    the unit they think in. The sunk-cost column is added only when some perspective wrote off
    residual book value, and is marked "(info)": §4.1 reports it but keeps it out of the
    decision KPIs.
    """
    rows = [
        (perspective_id, result.equivalent_annual_cost_in_euro)
        for perspective_id, result in matrix.results.items()
    ]
    any_sunk = any(result.sunk_cost_written_off_in_euro.maximum > 0 for result in matrix.results.values())
    headers = ["Perspective", "NPV", "Equivalent annual cost", "Monthly (year 1)", HeatCostNaming.COLUMN]
    if any_sunk:
        headers.append("Sunk cost (info)")
    table_rows = []
    for perspective_id, result in matrix.results.items():
        row = [
            _esc(perspective_id),
            _esc(_band_str(result.total_npv_in_euro)),
            _esc(_band_str(result.equivalent_annual_cost_in_euro, "EUR/a")),
            _esc(_band_str(result.monthly_cost_year1_in_euro, "EUR/mo")),
            _esc(_band_str(result.levelized_cost_of_heat_in_euro_per_kwh, "EUR/kWh")),
        ]
        if any_sunk:
            row.append(_esc(_band_str(result.sunk_cost_written_off_in_euro)))
        table_rows.append(row)
    return (
        _section_open(ReportSections.PERSPECTIVES, context)
        + _explanation_html(ReportSections.PERSPECTIVES, context)
        + _whisker_svg(rows, "EUR/a")
        + _table(headers, table_rows)
        + "</section>"
    )


def _actor_section_html(matrix: EvaluationMatrix, context: _ChapterContext) -> str:
    """Who pays what: payer NPVs per allocated perspective (§6.5).

    Answers "does the landlord/tenant split move money between actors without creating or
    destroying any?" Each allocated perspective gets payer whiskers plus a payer × cost-group
    table, under a header printing `views.payer_npv_total` — the sum the individual bars must
    add up to, which is the §6.5 zero-sum invariant in visual form. The cost-group table is the
    interesting half for a reviewer of the DE_2024 ruleset: it shows *which* blocks landed with
    whom, so an apportionable operating cost booked to the wrong side is visible as a group in
    the wrong row rather than as a total that is merely surprising.

    The unallocated SYSTEM payer is filtered out of the rows (it is the residue, not an actor),
    and perspectives that were never allocated are skipped entirely — recognized by having fewer
    than two real payers while carrying a SYSTEM entry. The section disappears when no
    perspective in the matrix is allocated, which is the case for a plain owner-occupier run.
    """
    from hisim.economics.timeline import Actor

    blocks = []
    for perspective_id, result in matrix.results.items():
        payers = {payer: band for payer, band in result.npv_by_payer.items() if payer != Actor.SYSTEM}
        if len(payers) < 2 and Actor.SYSTEM in result.npv_by_payer:
            continue  # unallocated (system-scope) perspective
        rows = [(payer.value, band) for payer, band in payers.items()]
        if not rows:
            continue
        system_total = views.payer_npv_total(result)
        # Payer x display-group table: which cost blocks land with whom.
        payer_categories: Dict[str, Dict[int, UncertainValue]] = {
            payer.value: views.fold_categories(by_category, PresentationStyle.CATEGORY_TO_GROUP)
            for payer, by_category in views.payer_category_npv_pivot(result).items()
        }
        group_indices = sorted({index for bucket in payer_categories.values() for index in bucket})
        table_rows = []
        for payer_name, bucket in payer_categories.items():
            table_rows.append(
                [f"<b>{_esc(payer_name)}</b>"]
                + [_esc(_band_str(bucket[index])) if index in bucket else "-" for index in group_indices]
            )
        payer_table = _details(
            "payer x cost-group table (NPV)",
            _table(["Payer"] + [PresentationStyle.DISPLAY_GROUPS[index][0] for index in group_indices], table_rows),
        )
        blocks.append(
            f"<details open><summary><b>{_esc(perspective_id)}</b> — payer NPVs sum to the system NPV "
            f"({_esc(_band_str(system_total))}, zero-sum invariant §6.5)</summary>"
            + _whisker_svg(rows, "EUR") + payer_table + "</details>"
        )
    if not blocks:
        return ""
    return (
        _section_open(ReportSections.WHO_PAYS_WHAT, context)
        + _explanation_html(ReportSections.WHO_PAYS_WHAT, context)
        + "".join(blocks) + "</section>"
    )


def _tornado_svg(rows: List[Tuple[str, float]], base_value: float) -> str:
    """Diverging bars: per-scenario swing of the headline KPI vs. the base scenario.

    The standard sensitivity picture, drawn for the scenarios section: each scenario's equivalent annual
    cost minus the base scenario's, sorted by absolute magnitude so the assumptions the result
    is most sensitive to come first. Colour carries the direction (red for more expensive, aqua
    for cheaper) rather than the identity of the scenario, because the reader's question here is
    "which way and how far", not "which series is which".

    Geometry: a centred zero axis with the plot half-width scaled to the largest absolute swing,
    so the widest bar always fills its side; the base value is printed under the axis so the
    swings can be read as absolutes. Sorting happens here and only affects display — the swings
    themselves come from `ScenarioCube.equivalent_annual_cost_swings`.
    """
    if not rows:
        return ""
    width, row_h, left = 860, 28, 250
    height = len(rows) * row_h + 26
    span = max(max(abs(swing) for _label, swing in rows), 1e-9)
    center = left + (width - left - 120) / 2.0
    scale = (width - left - 120) / 2.0 / span
    parts = _svg_open(width, height)
    parts.append(f'<line x1="{center}" y1="4" x2="{center}" y2="{height - 20}" stroke="var(--baseline)"/>')
    y = 4.0
    for label, swing in sorted(rows, key=lambda item: -abs(item[1])):
        mid = y + row_h / 2
        color = "var(--g5)" if swing > 0 else "var(--g1)"
        x_from = center if swing >= 0 else center + swing * scale
        parts.append(_text(left - 8, mid + 4, label, 11, "end"))
        parts.append(_rect(x_from, y + 5, abs(swing) * scale, row_h - 10, color,
                           f"{label}: {'+' if swing >= 0 else ''}{_fmt(swing)} EUR/a vs base", rx=3))
        anchor_x = center + swing * scale + (6 if swing >= 0 else -6)
        parts.append(_text(anchor_x, mid + 4, f"{'+' if swing >= 0 else ''}{_fmt(swing)}", 10,
                           "start" if swing >= 0 else "end", "var(--muted)"))
        y += row_h
    parts.append(_text(center, height - 6, f"base: {_fmt(base_value)} EUR/a", 10, "middle", "var(--muted)"))
    parts.append("</svg>")
    return "".join(parts)


def _scenario_section_html(scenario_cube, matrix: EvaluationMatrix, context: _ChapterContext) -> str:
    """The scenarios section: tornado of the headline KPI plus the full table (§4.6).

    Answers "how much of the conclusion survives the assumptions?" The tornado ranks the
    scenarios by how far they move the headline KPI, the all-scenarios table gives NPV, EAC and
    swing for each, and the robustness summary reports min/max/spread per perspective — the
    figure that says whether a ranking between two options holds across the whole scenario set
    or only under the base assumptions.

    The caption states the distinction reviewers most often miss: scenario axes and uncertainty
    bands are two *orthogonal* mechanisms (§4.6). A scenario varies rates and datapoints
    deliberately; the min/avg/max band varies the cost data within each scenario. They must not
    be read as one interval, and the report never combines them.

    `scenario_cube` is taken untyped on purpose — presentation may render a cube but may not
    import the module that builds one (the seam-4 import rule), so it is duck-typed for
    `results`, `base_id`, `equivalent_annual_cost_swings` and `equivalent_annual_cost_spreads`.
    The section disappears when no cube was computed, or when the cube has no base result for
    the reference perspective.
    """
    if scenario_cube is None or not scenario_cube.results:
        return ""
    perspective_id = next(iter(matrix.results.keys()))
    per_scenario = scenario_cube.results.get(perspective_id)
    if not per_scenario or scenario_cube.base_id not in per_scenario:
        return ""
    base = per_scenario[scenario_cube.base_id]
    base_value = base.equivalent_annual_cost_in_euro.average
    swings = scenario_cube.equivalent_annual_cost_swings(perspective_id)
    rows = [
        (scenario_id, swing)
        for scenario_id, swing in swings.items()
        if scenario_id != scenario_cube.base_id
    ]
    # Q26 F1: what each scenario actually changed, with both values — read from the cube's own
    # expanded definitions, never from a table of names in this file.
    assumptions = views.scenario_assumption_labels(scenario_cube, base)
    # Q27 R2: a row that did not move at all states why, from the base cell's own timeline.
    zero_swing = views.zero_swing_notes(scenario_cube, base, swings)
    markers = {scenario_id: index for index, scenario_id in enumerate(zero_swing, start=1)}
    table_rows = "".join(
        f"<tr><td>{_esc(scenario_id)}</td>"
        f"<td>{_esc(assumptions.get(scenario_id, 'central case — nothing changed'))}</td>"
        f"<td>{_esc(_band_str(result.total_npv_in_euro))}</td>"
        f"<td>{_esc(_band_str(result.equivalent_annual_cost_in_euro, 'EUR/a'))}</td>"
        f"<td>{swings[scenario_id]:+,.0f}"
        + (f"<sup>{markers[scenario_id]}</sup>" if scenario_id in markers else "")
        + "</td></tr>"
        for scenario_id, result in per_scenario.items()
    )
    footnotes = "".join(
        f"<p class='sub'><sup>{markers[scenario_id]}</sup> <b>{_esc(scenario_id)}</b> — "
        f"swing is exactly zero: {_esc(note)}.</p>"
        for scenario_id, note in zero_swing.items()
    )
    # Robustness summary (§4.6): min/max/spread of the headline KPI per perspective.
    robustness_rows = [
        [_esc(pid), f"{spread.minimum:,.0f}", f"{spread.maximum:,.0f}", f"{spread.spread:,.0f}"]
        for pid, spread in scenario_cube.equivalent_annual_cost_spreads().items()
    ]
    robustness = _details(
        "robustness summary across scenarios (EAC [EUR/a], AVERAGE slot)",
        _table(["Perspective", "Min", "Max", "Spread"], robustness_rows),
    )
    return (
        _section_open(ReportSections.SCENARIOS, context, perspective_id)
        + _explanation_html(ReportSections.SCENARIOS, context)
        + "<p class='sub'>Full cube: scenario_cube.csv / scenario_cube.json.</p>"
        + _tornado_svg(rows, base_value)
        + "<details open><summary>all scenarios</summary><table>"
        "<tr><th>Scenario</th><th>Assumption (scenario value, central value)</th><th>NPV</th>"
        "<th>Equivalent annual cost</th><th>Swing [EUR/a]</th></tr>"
        + table_rows + "</table>" + footnotes + "</details>" + robustness + "</section>"
    )


def _kpi_section_html(matrix: EvaluationMatrix, context: _ChapterContext) -> str:
    """The KPIs section: the namespaced lifecycle KPI set (§7.3) as a table with bands.

    Answers "what exactly will downstream consumers see?" — the section prints the published KPI
    set verbatim, so the reviewer who has just followed the calculation chain can confirm that
    what leaves the engine matches what they were shown. It is last for that reason: it is the
    output contract, not a step in the derivation.

    The entries come from `exports.build_lifecycle_kpi_entries`, i.e. the same function that
    writes `lifecycle_kpis.json`, which is what makes it impossible for the table and the file to
    disagree; that import is one of the explicitly allowed presentation→engine-output imports.
    `value` is the AVERAGE slot and the band column is min | max, both stated in the caption
    because a KPI name alone does not say which slot it carries.
    """
    from hisim.economics.exports import build_lifecycle_kpi_entries

    entries = build_lifecycle_kpi_entries(matrix)
    if not entries:
        return ""
    rows = []
    for entry in entries:
        value = f"{entry.value:,.2f}" if isinstance(entry.value, (int, float)) else _esc(str(entry.value))
        band = (
            f"{entry.value_min:,.2f} | {entry.value_max:,.2f}"
            if entry.value_min is not None and entry.value_max is not None
            else "-"
        )
        rows.append([_esc(entry.name), value, band, _esc(entry.unit)])
    return (
        _section_open(ReportSections.KPIS, context)
        + _explanation_html(ReportSections.KPIS, context)
        + "<p class='sub'>Published to lifecycle_kpis.json; <code>value</code> is the AVERAGE "
          "slot, the band column is min | max.</p>"
        + _levelized_heat_cost_caption(next(iter(matrix.results.values())))
        + _table(["KPI", "Value", "Band (min | max)", "Unit"], rows)
        + "</section>"
    )


def _levelized_heat_cost_caption(result: LifecycleCostResult) -> str:
    """The heat-cost figure written out as its own division, attribution set included (Q26 F6).

    The published system cost per unit of heat is a quotient of two figures the report shows
    nowhere else in that form, and readers reliably assume a third thing about the numerator: that
    it is the heating-attributable share of the cost. It is not. The engine divides the
    perspective's *entire* NPV — every subject it books, the PV system and the battery included —
    by the annual heat demand, so the figure answers "what does this whole installation cost per
    kWh of heat delivered". Saying that plainly is the point of the caption; the KPI's name says
    it too since Q27 R1, but a name cannot carry the attribution set.

    The caption leads with the division as it is actually computed — equivalent annual cost over
    annual heat demand (Q27 R4) — and gives the discounted-sum form, the one the LCOH literature
    states, as the equivalent second reading. Both are exact; leading with the annual form means
    the first two numbers a reader sees are two the report already published.

    Empty when the run publishes no such figure (no heat demand was declared).
    """
    derivation = views.levelized_heat_cost_derivation(result)
    if derivation is None:
        return ""
    subjects = ", ".join(derivation.attributed_subjects)
    return (
        f"<p class='sub'><b>{HeatCostNaming.FULL}, in full.</b> "
        f"{_fmt(derivation.equivalent_annual_cost_in_euro)} EUR/a &divide; "
        f"{derivation.annual_heat_demand_in_kwh:,.0f} kWh/a = "
        f"<b>{derivation.levelized_cost_in_euro_per_kwh:.4f} EUR/kWh</b> — equivalently NPV "
        f"&divide; discounted heat sum ({_fmt(derivation.numerator_npv_in_euro)} EUR &divide; "
        f"{derivation.discounted_heat_in_kwh:,.0f} kWh). The numerator is the <i>whole</i> NPV of "
        f"perspective {_esc(derivation.perspective_id)}, {_fmt(derivation.numerator_npv_in_euro)} "
        f"EUR, annualized with the annuity factor {derivation.annuity_factor:.6f} to "
        f"{_fmt(derivation.equivalent_annual_cost_in_euro)} EUR/a. No heating-only attribution is "
        f"applied: every subject the perspective books counts, namely {_esc(subjects)}.</p>"
    )


def _components_section_html(matrix: EvaluationMatrix, context: _ChapterContext) -> str:
    """The component breakdown: per-subject stacked NPV bars per perspective (§7.4).

    Answers "which component actually drives the result, and does the sum of the parts equal the
    whole?" The diverging stacks put each subject's cost blocks right of zero and its credits
    (residual value, subsidies, feed-in, anyway credit) left, with a marker at the net NPV band,
    so `net = costs - credits` is geometry rather than a claim. The §7.4 reconciliation — the
    subject nets summing to the headline — is checked automatically in the plausibility panel; this is where
    a reader sees *why* it holds or which subject is responsible when it does not.

    One collapsible block per perspective, the first open, each with a legend restricted to the
    groups that perspective's breakdowns contain and a table repeating the same subjects with
    NPV, equivalent annual cost, year-0 investment, support and lifecycle CO2. Keeping the
    credits unnetted is deliberate: an expensive component with an equally large subsidy looks
    nothing like a cheap one, and a netted bar would hide the difference.
    """
    blocks = []
    for index, (perspective_id, result) in enumerate(matrix.results.items()):
        groups_present = sorted(
            {group_of(category) for b in result.component_breakdowns.values() for category in b.npv_by_category}
        )
        subject_rows = [
            [
                _esc(subject),
                _esc(_band_str(breakdown.total_npv_in_euro)),
                _esc(_band_str(breakdown.equivalent_annual_cost_in_euro, "EUR/a")),
                _esc(_band_str(breakdown.investment_gross_in_euro)),
                _esc(_band_str(breakdown.subsidies_nominal_in_euro)),
                f"{breakdown.lifecycle_co2_in_kg:,.0f}",
            ]
            for subject, breakdown in result.component_breakdowns.items()
        ]
        subject_table = _details(
            "subject table",
            _table(
                ["Subject", "NPV", "Equivalent annual cost", "Year-0 investment", "Subsidies", "Lifecycle CO2 [kg]"],
                subject_rows,
            ),
        )
        open_attr = " open" if index == 0 else ""
        blocks.append(
            f"<details{open_attr}><summary><b>{_esc(perspective_id)}</b></summary>"
            + _legend_html(groups_present) + _stacked_subject_svg(result) + subject_table + "</details>"
        )
    return (
        _section_open(ReportSections.COMPONENT_BREAKDOWN, context)
        + _explanation_html(ReportSections.COMPONENT_BREAKDOWN, context)
        + "".join(blocks) + "</section>"
    )


def _checks_section_html(plausibility: PlausibilityReport, context: _ChapterContext) -> str:
    """The plausibility panel.

    Answers, before anything else is read, "is there a reason not to trust the rest of this
    report?" It opens the analysis because a reviewer's time is better spent on the automated verdict
    than on rediscovering a unit mix-up by inspection, and the heading carries the count of
    flagged checks so that verdict is visible without scrolling. WARN means a magnitude left a
    deliberately generous range (usually a unit or a rate stored as an absolute); FAIL means a
    structural invariant is broken and the numbers below contradict each other.

    Rendering only — the checks themselves, their thresholds and their order are decided in
    `plausibility.py` from `cost_database/plausibility_checks.json`, and the same rows are
    re-used for the markdown table and for `bridge.py`'s log warnings. The reader hint in the
    Note column is the one part that lives on this side, since "what usually causes this" is
    editorial rather than computed.
    """
    checks = render_plausibility_findings(plausibility)
    rows = "".join(
        f"<tr><td><span class='status {check.status}'>{check.status}</span></td>"
        f"<td>{_esc(check.name)}</td><td>{_esc(check.value)}</td><td>{_esc(check.expected)}</td>"
        f"<td>{_esc(check.detail)}</td></tr>"
        for check in checks
    )
    n_bad = sum(1 for check in checks if check.status != "PASS")
    headline = "all checks passed" if n_bad == 0 else f"{n_bad} check(s) need a look"
    return (
        _section_open(ReportSections.PLAUSIBILITY, context, headline)
        + _explanation_html(ReportSections.PLAUSIBILITY, context)
        + f"<table><tr><th></th><th>Check</th><th>Value</th><th>Expected</th><th>Note</th></tr>{rows}"
        "</table></section>"
    )


def _comparison_section_html(comparison: VariantComparison, context: _ChapterContext) -> str:
    """The comparison section (§D): delta waterfall by subject + discounted payback curve.

    Answers the only question that is actually a decision: "is the variant worth it compared to
    the reference, and when does it pay back?" The waterfall attributes the total NPV delta to
    the subjects that caused it — a heat pump adding investment, an energy bill giving it back —
    so a reader can see whether a favourable total rests on one component or on many. Its net
    bar is `comparison.npv_delta_in_euro`, read from the result rather than summed from the
    steps, which is the second half of the §7 B8 fix.

    Below it, the discounted payback is given three ways: as text for the three slots (with
    `None` meaning "never within the horizon", a real and common answer), as the cumulative
    discounted savings curve whose zero-crossing *is* that year, and — when the comparison
    carries a tenancy — as the warm-rent change per month with its per-slot neutrality verdict,
    which is the §6 question of whether the modernization is neutral for the tenant.

    Subjects whose delta is below half a cent are dropped from the waterfall as float noise;
    the delta table below it lists every subject, so nothing is hidden.
    """
    steps: List[Tuple[str, float, str]] = []
    for subject, delta in sorted(comparison.npv_delta_by_subject.items(), key=lambda item: item[1].average):
        if abs(delta.average) < 0.005:
            continue
        color = "var(--g5)" if delta.average > 0 else "var(--g1)"
        steps.append((subject, delta.average, color))
    payback = comparison.discounted_payback_years
    payback_text = (
        f"best case {payback.get('low')} a, expected {payback.get('average')} a, "
        f"worst case {payback.get('high')} a (None = never within the horizon)"
    )
    warm_rent = ""
    if comparison.warm_rent_change_per_month_in_euro is not None:
        warm_rent = (
            f"<p>Warm rent change: <b>{_esc(_band_str(comparison.warm_rent_change_per_month_in_euro, 'EUR/month'))}</b>"
            f" — neutral per slot: {_esc(str(comparison.warm_rent_neutral_per_slot))}</p>"
        )
    delta_rows = [
        [_esc(subject), _esc(_band_str(delta))]
        for subject, delta in sorted(comparison.npv_delta_by_subject.items(), key=lambda item: item[1].average)
    ]
    return (
        _section_open(ReportSections.COMPARISON, context, comparison.perspective_id)
        + _explanation_html(ReportSections.COMPARISON, context)
        + _waterfall_svg(steps, "Net NPV delta", comparison.npv_delta_in_euro.average)
        + _details("delta table (best-case | expected | worst-case, §3.9 envelope)",
                   _table(["Subject", "NPV delta"], delta_rows))
        + f"<p class='sub'>Discounted payback: {_esc(payback_text)}</p>"
        + _payback_svg(comparison)
        + warm_rent
        + "</section>"
    )


# ------------------------------------------------------- sections of the visualization set (F)
#
# One section per chart of the visualization extension, V6 excepted — it lives with the audit
# outputs (owner decision Q9). Like every other section these open with `_explanation_html`, i.e.
# with the four authored parts held in `ReportProse` (rule 2.6): the report has to be
# understandable by a reader who has never seen a Sankey or a bridge waterfall, and because the
# explanations are part of the golden-tested HTML they are reviewed and frozen like any number.
# What stays in the functions below is the run-specific half a golden-stable text cannot carry:
# the captions and annotations that state this run's amounts, perspectives and skip reasons.

def _first_result_where(matrix: EvaluationMatrix, predicate) -> Optional[LifecycleCostResult]:
    """The first perspective of the matrix that satisfies a predicate, or None.

    Most single-result sections of this report render the matrix's *first* perspective, which is
    the reference view of the run. Four charts of the visualization set cannot: the actor Sankey
    needs a perspective that actually has two actors, the loan panel and the equity chart need a
    financed one, the energy-to-money chart needs one carrying an energy attribution. Rendering
    them for the first perspective would drop them from every run whose first row happens to be
    an unallocated cash view — even though the run evaluated a landlord and a financed row right
    below it. Picking the first perspective that *has* the data keeps the section, and the
    heading names which perspective it is showing, so nothing is ambiguous.
    """
    for result in matrix.results.values():
        if predicate(result):
            return result
    return None


def _has_year_zero_funding(result: LifecycleCostResult) -> bool:
    """Whether year 0 carries support or a loan — the cheap predicate behind V10's perspective.

    Deliberately a scan of the timeline rather than a call to `funding_sources_and_uses`: the
    view validates and raises, and choosing which perspective to draw must not depend on a
    validation that is only meaningful once the perspective has been chosen.
    """
    return any(
        entry.year == 0
        and entry.category in (CostCategory.SUBSIDY, CostCategory.LOAN_DISBURSEMENT)
        for entry in result.scoped_timeline().entries
    )


def _lifecycle_overview_section_html(
    result: LifecycleCostResult,
    comparison: Optional[VariantComparison],
    context: _ChapterContext,
) -> str:
    """V9: the opening figure — assets, financing, support and milestones on one year axis.

    Answers "what happens when, over the life of this renovation": what was built, how it is
    financed, what support and obligations run alongside it, and when the project pays off. It is
    the report's first figure because it is the only one that shows the whole story at once; every
    lane is a compressed restatement of a chart further down, which is also what makes it safe —
    it introduces no new numbers, only a shared axis.
    """
    lanes = views.lifecycle_lanes(result, comparison)
    rows: List[Tuple[str, List[Tuple[int, Optional[int], str]], List[Tuple[int, str, Optional[float]]], str]] = []
    skipped: List[str] = []
    rows.append((
        "Milestones",
        [(span.start_year, span.end_year, span.label) for span in lanes.milestones.spans],
        [(event.year, event.label, event.amount_in_euro) for event in lanes.milestones.events],
        "var(--baseline)",
    ))
    for lane, color in ((lanes.financing, "var(--g0)"), (lanes.support, "var(--g3)")):
        if lane.is_empty():
            skipped.append(lane.name)
            continue
        rows.append((
            lane.name,
            [(span.start_year, span.end_year, span.label) for span in lane.spans],
            [(event.year, event.label, event.amount_in_euro) for event in lane.events],
            color,
        ))
    for asset in lanes.assets:
        asset_events: List[Tuple[int, str, Optional[float]]] = [
            (event.year, event.kind, event.amount_in_euro) for event in asset.events
        ]
        if asset.residual is not None:
            asset_events.append((asset.residual.year, "residual", asset.residual.amount_in_euro))
        rows.append((
            asset.subject,
            [(span.start_year, span.end_year, "in service") for span in asset.spans],
            asset_events,
            "var(--g4)",
        ))
    if skipped:
        log.information(
            f"Lifecycle overview: lane(s) {', '.join(skipped)} are empty for perspective "
            f"{result.perspective_id!r} and are not drawn."
        )
    note = "" if comparison is not None else (
        "<p class='sub'>This run has no reference variant, so there is no payback milestone: "
        "payback is a statement about a difference between two variants.</p>"
    )
    return (
        _section_open(ReportSections.AT_A_GLANCE, context, result.perspective_id)
        + _explanation_html(ReportSections.AT_A_GLANCE, context) + note
        + _gantt_svg(rows, lanes.horizon) + "</section>"
    )


def _liquidity_section_html(
    result: LifecycleCostResult,
    comparison: Optional[VariantComparison],
    context: _ChapterContext,
) -> str:
    """V2: the cumulative cash position as a fan, nominal above and discounted below.

    Answers "how deep does this go, and when do I get it back": the running out-of-pocket
    position with its uncertainty band, so the worst year and the payback both read as ranges
    rather than as single numbers.
    """
    nominal = views.cumulative_nominal_cost_series(result)
    years = list(range(len(nominal[Slot.AVERAGE])))
    worst_year, worst_amount = views.worst_liquidity_position(result)
    nominal_svg = _xy_lines_svg(
        series=[("average scenario", list(zip(years, nominal[Slot.AVERAGE])), "var(--g0)", 2.2, "")],
        bands=[(list(zip(years, nominal[Slot.LOW])), list(zip(years, nominal[Slot.HIGH])), "var(--g0)")],
        y_label="cumulative nominal cost [EUR] - costs plotted upward",
        annotations=[(worst_year, worst_amount,
                      f"deepest out-of-pocket {_fmt(worst_amount)} EUR in year {worst_year}")],
    )
    if comparison is not None:
        curves = comparison.cumulative_discounted_savings_in_euro
        crossings = views.band_zero_crossings(curves)
        low, average, high = curves["low"], curves["average"], curves["high"]
        lower_label = "cumulative discounted savings [EUR] (reference - variant)"
        payback_note = _payback_interval_prose(crossings)
    else:
        discounted = views.cumulative_discounted_cost_series(result)
        low, average, high = discounted[Slot.LOW], discounted[Slot.AVERAGE], discounted[Slot.HIGH]
        lower_label = "cumulative discounted cost [EUR] - ends at the NPV"
        payback_note = (
            "Without a reference variant there is no payback question, so the lower panel shows "
            "the cumulative discounted cost, whose end point is the reported NPV."
        )
    discounted_years = list(range(len(average)))
    discounted_svg = _xy_lines_svg(
        series=[("average scenario", list(zip(discounted_years, average)), "var(--g0)", 2.2, "")],
        bands=[(list(zip(discounted_years, low)), list(zip(discounted_years, high)), "var(--g0)")],
        y_label=lower_label,
    )
    return (
        _section_open(ReportSections.CASH_CURVE, context, result.perspective_id)
        + _explanation_html(ReportSections.CASH_CURVE, context)
        + f"<p class='sub'>{_esc(payback_note)}</p>"
        + nominal_svg + discounted_svg + "</section>"
    )


def _payback_interval_prose(crossings: Dict[Any, Optional[int]]) -> str:
    """The payback sentence of V2's lower panel, with the open end spelled out.

    Says "no payback in the pessimistic world within the horizon" in words rather than omitting
    the statement, which is the failure mode this wording exists to prevent: an absent annotation
    reads as "did not pay back" to one reader and as "not computed" to another.
    """
    low, high = crossings.get("low"), crossings.get("high")
    if low is None:
        return "The investment does not pay back within the horizon in any of the three worlds."
    if high is None:
        return (
            f"Payback starts in year {low} in the optimistic world; in the pessimistic world the "
            "curve never reaches zero within the horizon."
        )
    return f"Payback lands between year {low} (optimistic world) and year {high} (pessimistic world)."


def _uncertainty_section_html(result: LifecycleCostResult, context: _ChapterContext) -> str:
    """V3: which subjects make the total NPV band as wide as it is.

    Answers "which inputs are worth arguing about". It is an *attribution* of the existing band,
    not a sensitivity analysis — nothing is re-evaluated — and the prose says so, because the
    chart looks exactly like an OAT tornado and would otherwise be read as one.
    """
    total = result.total_npv_in_euro
    if total.is_exact():
        log.information(
            f"Uncertainty attribution skipped for perspective {result.perspective_id!r}: the "
            "total NPV band is degenerate, so there is no width to attribute."
        )
        return ""
    rows = views.uncertainty_attribution(result)
    return (
        _section_open(ReportSections.UNCERTAINTY_DRIVERS, context, result.perspective_id)
        + _explanation_html(ReportSections.UNCERTAINTY_DRIVERS, context)
        + _attribution_tornado_svg(rows, total)
        + _details(
            "attribution table",
            _table(
                ["Subject", "NPV (average)", "Optimistic delta", "Pessimistic delta"],
                [
                    [_esc(row.subject), _fmt(row.average_npv_in_euro),
                     _fmt(row.low_delta_in_euro), _fmt(row.high_delta_in_euro)]
                    for row in rows
                ],
            ),
        )
        + "</section>"
    )


def _actor_flow_section_html(result: LifecycleCostResult, context: _ChapterContext) -> str:
    """V1: who pays whom over the whole horizon, one column per party.

    Answers the question a landlord/tenant case makes unavoidable: the levy, the subsidy and the
    energy bills all cross actor boundaries, and until now that structure was visible only as
    rows of a pivot table.

    Since Q23 every internal party has a column of its own, ordered by `actor_columns` so that a
    payment between two of them — the §559e levy — runs left to right like every other ribbon
    instead of looping out of a shared column and back into it.
    """
    matrix = views.actor_flow_matrix(result)
    if len(matrix.actors) < 2:
        log.information(
            f"Actor-flow Sankey skipped for perspective {result.perspective_id!r}: it has "
            f"{len(matrix.actors)} actor node(s), so there is no who-pays-whom story to draw."
        )
        return ""
    nets = matrix.net_by_actor()

    def key(node: str, is_target: bool) -> str:
        if node in matrix.actors:
            return f"actor:{node}"
        return f"snk:{node}" if is_target else f"src:{node}"

    ribbons = [
        (key(flow.source, False), key(flow.target, True), flow.amount_in_euro,
         f"var(--g{group_of(flow.category)})" if flow.category is not None else "var(--muted)", False)
        for flow in sorted(matrix.flows, key=lambda item: -item.amount_in_euro)
    ]
    labels = {f"actor:{actor}": f"{actor} (net {_fmt(nets[actor])} EUR)" for actor in matrix.actors}
    labels.update({f"src:{node}": node for node in matrix.sources})
    labels.update({f"snk:{node}": node for node in matrix.sinks})
    # Q23: one column per party, ordered by the view so payments between them run left to right;
    # external sources stay leftmost and external sinks rightmost, and a ribbon may skip columns.
    columns = (
        [[f"src:{node}" for node in matrix.sources]]
        + [[f"actor:{actor}" for actor in column] for column in matrix.actor_columns()]
        + [[f"snk:{node}" for node in matrix.sinks]]
    )
    return (
        _section_open(ReportSections.WHO_PAYS_WHOM, context, result.perspective_id)
        + _explanation_html(ReportSections.WHO_PAYS_WHOM, context)
        + "<p class='sub'>This chart shows the average scenario only; the band of the grand "
        f"total is {_esc(_band_str(matrix.total_band))}.</p>"
        # Q29 R7: the stub that closes an actor's face is labelled with the view's own net, in the
        # view's own sign convention (cost positive), so it cannot contradict the node beside it.
        + _sankey_svg(
            columns, ribbons, labels,
            stub_labels={f"actor:{actor}": f"net {_fmt(net)} EUR" for actor, net in nets.items()},
        )
        + f"<p class='sub'>{matrix.folded_ribbon_count} ribbon(s) below 0.5 % of the flow volume, "
        f"carrying {_fmt(matrix.folded_amount_in_euro)} EUR in total, were folded into a single "
        "grey ribbon per node pair.</p></section>"
    )


def _loan_section_html(matrix: EvaluationMatrix, context: _ChapterContext) -> str:
    """The debt service per year, one block per financed perspective (V5).

    Answers "what does the loan actually look like over time" — falling interest against rising
    principal for an annuity, a bullet spike for interest-only. It is its own section rather than
    a chart buried in the cash-flow timeline because the loan is a separate story from the
    timeline it replaces, and because the cost-of-credit section beside it is the second half of
    the same question.

    Perspectives without loan flows contribute nothing, and a bundle in which nobody borrows
    produces no section at all rather than an empty box.
    """
    blocks = []
    for perspective_id, result in matrix.results.items():
        chart = _loan_svg(result)
        if not chart:
            continue
        blocks.append(f"<p class='sub'><b>{_esc(perspective_id)}</b></p>" + chart)
    if not blocks:
        log.information(
            "Loan section skipped: no evaluated perspective carries loan flows (every purchase "
            "in this bundle is a cash purchase)."
        )
        return ""
    return (
        _section_open(ReportSections.LOAN, context)
        + _explanation_html(ReportSections.LOAN, context)
        + "".join(blocks) + "</section>"
    )


def _cost_of_credit_section_html(result: LifecycleCostResult, context: _ChapterContext) -> str:
    """V5's companion panel: the total cost of credit and the effective annual rate.

    Answers the question every loan document answers on its first page — "what does borrowing
    this money actually cost me" — from the same amortization series the debt-service chart in
    the loan section stacks.
    """
    amortization = views.loan_amortization_series(result)
    if not amortization.has_flows():
        log.information(
            f"Total cost of credit skipped for perspective {result.perspective_id!r}: it is a "
            "cash purchase with no loan flows."
        )
        return ""
    credit = views.total_cost_of_credit(result)
    years = list(range(len(amortization.outstanding_balance_in_euro)))
    balance_svg = _xy_lines_svg(
        series=[
            ("outstanding balance", list(zip(years, amortization.outstanding_balance_in_euro)),
             "var(--g0)", 2.2, ""),
            ("annual debt service", list(zip(years, [
                interest + principal
                for interest, principal in zip(amortization.interest_in_euro, amortization.principal_in_euro)
            ])), "var(--g5)", 1.6, "5 4"),
        ],
        y_label="EUR (nominal) - one axis for both, so the early years look small on purpose",
        height=200,
    )
    rate = (
        f"{credit.effective_annual_rate:.2%}" if credit.effective_annual_rate is not None else "n/a"
    )
    unrepaid = (
        f"<p class='sub'>{_fmt(credit.unrepaid_principal_in_euro)} EUR of the principal falls due "
        "after the observation horizon and is therefore not in the bar.</p>"
        if abs(credit.unrepaid_principal_in_euro) > 0.005 else ""
    )
    return (
        _section_open(ReportSections.COST_OF_CREDIT, context, result.perspective_id)
        + _explanation_html(ReportSections.COST_OF_CREDIT, context)
        + f"<p class='sub'>Effective annual rate: <b>{_esc(rate)}</b>.</p>" + unrepaid
        + _cost_of_credit_svg(credit)
        + _table(
            ["Principal", "Interest", "Fees", "Repayment grant", "Total repaid", "Effective rate"],
            [[
                _fmt(credit.principal_in_euro), _fmt(credit.interest_in_euro), _fmt(credit.fees_in_euro),
                _fmt(credit.grants_in_euro), _fmt(credit.total_repaid_in_euro), _esc(rate),
            ]],
        )
        + balance_svg + "</section>"
    )


def _component_events_section_html(result: LifecycleCostResult, context: _ChapterContext) -> str:
    """V7: when each component is bought, replaced and written down.

    Answers the *schedule* question per device, and makes the residual-value rule auditable: a
    residual marker on a row with no purchase before it is a defect you can see.
    """
    rows = views.component_event_strip(result)
    if not rows:
        log.information(
            f"Component event strip skipped for perspective {result.perspective_id!r}: it has no "
            "component subjects (a carriers-only evaluation)."
        )
        return ""
    horizon = result.parameters.observation_period_in_years
    gantt_rows: List[
        Tuple[str, List[Tuple[int, Optional[int], str]], List[Tuple[int, str, Optional[float]]], str]
    ] = []
    for row in rows:
        events: List[Tuple[int, str, Optional[float]]] = [
            (event.year, event.kind, event.amount_in_euro) for event in row.events
        ]
        if row.residual is not None:
            events.append((row.residual.year, "residual", row.residual.amount_in_euro))
        gantt_rows.append((
            row.subject,
            [(span.start_year, span.end_year, "in service") for span in row.spans],
            events,
            "var(--g4)",
        ))
    return (
        _section_open(ReportSections.LIFETIMES, context, result.perspective_id)
        + _explanation_html(ReportSections.LIFETIMES, context)
        + _gantt_svg(gantt_rows, horizon) + "</section>"
    )


def _treemap_section_html(result: LifecycleCostResult, context: _ChapterContext) -> str:
    """V8: the composition of lifetime cost, gross and net of credits side by side.

    Answers "what is this made of" in an area encoding that survives a glance. Both bases are
    rendered because a treemap cannot draw a credit and neither variant alone is the whole truth
    (owner decision Q11).
    """
    panels: List[str] = []
    captions: List[str] = []
    for basis, headline in (
        (views.TileBasis.GROSS, "gross cost"), (views.TileBasis.NET_OF_CREDITS, "net of credits")
    ):
        tiles = views.cost_structure_tiles(result, PresentationStyle.CATEGORY_TO_GROUP, basis)
        drawable = sorted(
            [tile for tile in tiles.tiles if tile.area_in_euro > 0],
            key=lambda tile: (tile.group, -tile.area_in_euro),
        )
        total = sum(tile.area_in_euro for tile in drawable)
        panels.append(
            f"<div><p class='sub'><b>{headline}: {_fmt(total)} EUR</b> "
            f"(net NPV {_fmt(tiles.net_npv_in_euro)} EUR)</p>"
            + _treemap_svg([
                (f"{PresentationStyle.DISPLAY_GROUPS[tile.group][0]} - {tile.subject}",
                 tile.area_in_euro, f"var(--g{tile.group})")
                for tile in drawable
            ])
            + "</div>"
        )
        if basis == views.TileBasis.GROSS:
            captions.append(
                f"The gross panel leaves out {_fmt(tiles.credit_total_in_euro)} EUR of credits "
                f"(support, feed-in revenue, residual value); {tiles.folded_tile_count} tile(s) "
                "below 1 % of the area were folded into an 'other' tile per group."
            )
        else:
            clamped = tiles.clamped_tiles()
            names = ", ".join(
                f"{tile.subject} ({_fmt(tile.clamped_from_in_euro or 0.0)} EUR)" for tile in clamped
            ) or "none"
            captions.append(
                f"The net panel applies each subject's credits to that subject's own cost tiles "
                f"across all groups — a wall's subsidy shrinks the wall — and clamps at zero the "
                f"subjects whose credits exceed their costs, erasing "
                f"{_fmt(tiles.clamped_total_in_euro)} EUR in {len(clamped)} subject(s): {names}. "
                f"Those are exactly the entries worth asking about."
            )
    return (
        _section_open(ReportSections.COST_STRUCTURE, context, result.perspective_id)
        + _explanation_html(ReportSections.COST_STRUCTURE, context)
        + f"<div style='display:flex;gap:14px;flex-wrap:wrap'>{''.join(panels)}</div>"
        + f"<p class='sub'>{_esc(' ')}{' '.join(_esc(caption) for caption in captions)}</p></section>"
    )


def _sources_uses_section_html(result: LifecycleCostResult, context: _ChapterContext) -> str:
    """V10: how year 0 is funded and what it buys, balanced to the euro.

    Answers the first question a bank or a funding advisor asks, and the one place the subsidy
    scheme id earns its keep: "state -> KfW 261 -> heat pump" reads very differently from one
    grey subsidies node.
    """
    statement = views.funding_sources_and_uses(result)
    if not statement.has_external_funding():
        log.information(
            f"Sources-and-uses Sankey skipped for perspective {result.perspective_id!r}: year 0 "
            "is funded entirely from own capital, which the investment waterfall shows better."
        )
        return ""
    color_by_source = {
        node.label: f"var(--g{group_of(node.category)})" if node.category is not None else "var(--muted)"
        for node in statement.sources
    }
    ribbons = [
        (f"src:{source}", f"use:{use}", amount, color_by_source[source], False)
        for source, use, amount in statement.ribbons()
        if amount > 0
    ]
    labels = {f"src:{node.label}": f"{node.label} ({_fmt(node.amount_in_euro)} EUR)"
              for node in statement.sources}
    labels.update({f"use:{node.label}": f"{node.label} ({_fmt(node.amount_in_euro)} EUR)"
                   for node in statement.uses})
    # Q20: the node reads as the scheme's friendly name and the raw id lives in the tooltip.
    tooltips = dict(labels)
    tooltips.update({
        f"src:{node.label}": f"{labels[f'src:{node.label}']} — scheme id {node.scheme_id}"
        for node in statement.sources
        if node.scheme_id
    })
    columns = [
        [f"src:{node.label}" for node in statement.sources],
        [f"use:{node.label}" for node in statement.uses],
    ]
    return (
        _section_open(ReportSections.FUNDING, context, result.perspective_id)
        + _explanation_html(ReportSections.FUNDING, context)
        + f"<p class='sub'>Checked here: sources {_fmt(statement.total_sources_in_euro())} EUR = "
        f"uses {_fmt(statement.total_uses_in_euro())} EUR = gross year-0 investment "
        f"{_fmt(statement.gross_year_zero_investment_in_euro)} EUR.</p>"
        + _sankey_svg(columns, ribbons, labels, height=300, tooltips=tooltips) + "</section>"
    )


def _subject_flows_section_html(result: LifecycleCostResult, context: _ChapterContext) -> str:
    """V11: which subject causes which *kind* of cost, cost and credit ribbons kept apart.

    Answers the technology-comparison question a composition chart structurally hides: a gas
    boiler is cheap to install and expensive to run, a heat pump the reverse, and PV feeds
    revenue back.
    """
    flows = views.subject_category_flows(result, PresentationStyle.CATEGORY_TO_GROUP)
    subjects = sorted({flow.subject for flow in flows})
    if len(subjects) < 2:
        log.information(
            f"Subject-to-group Sankey skipped for perspective {result.perspective_id!r}: it has "
            f"{len(subjects)} subject(s), so there is no cross-link story; the treemap covers it."
        )
        return ""
    groups_cost = sorted({flow.group for flow in flows if not flow.is_credit})
    groups_credit = sorted({flow.group for flow in flows if flow.is_credit})
    ordered = sorted(flows, key=lambda item: -item.amount_in_euro)
    ribbons = [
        (f"sub:{flow.subject}", f"grp:{flow.group}:{int(flow.is_credit)}", flow.amount_in_euro,
         f"var(--g{flow.group})", flow.is_credit)
        for flow in ordered
    ]
    labels = {f"sub:{subject}": subject for subject in subjects}
    labels.update({
        f"grp:{group}:0": PresentationStyle.DISPLAY_GROUPS[group][0] for group in groups_cost
    })
    labels.update({
        f"grp:{group}:1": f"{PresentationStyle.DISPLAY_GROUPS[group][0]} (credit)"
        for group in groups_credit
    })
    columns = [
        [f"sub:{subject}" for subject in subjects],
        [f"grp:{group}:0" for group in groups_cost] + [f"grp:{group}:1" for group in groups_credit],
    ]
    # Q28 R6: every node states the amounts its extent is made of, every ribbon its exact euros.
    margins = views.subject_flow_margins(flows)
    sublabels, tooltips = _subject_flow_node_labels(margins, labels, groups_cost, groups_credit)
    ribbon_tooltips = [
        f"{flow.subject} -> {labels[f'grp:{flow.group}:{int(flow.is_credit)}']}: "
        f"{flow.amount_in_euro:,.2f} EUR {'credit' if flow.is_credit else 'cost'}"
        for flow in ordered
    ]
    return (
        _section_open(ReportSections.COST_SHAPES, context, result.perspective_id)
        + _explanation_html(ReportSections.COST_SHAPES, context)
        + _subject_flow_caption(margins)
        + _sankey_svg(columns, ribbons, labels, height=340, tooltips=tooltips,
                      sublabels=sublabels, ribbon_tooltips=ribbon_tooltips)
        + "</section>"
    )


def _subject_flow_node_labels(
    margins: views.SubjectFlowMargins,
    labels: Dict[str, str],
    groups_cost: List[Any],
    groups_credit: List[Any],
) -> Tuple[Dict[str, Tuple[str, str]], Dict[str, str]]:
    """Amount lines and hover texts for the cost-shapes nodes (Q28 R6).

    A subject node states both sides of what its extent is made of ("costs X | credits -Y", the
    credit half omitted when there is none) and a group node its signed total, negative for the
    credit groups so the sign a reader is looking for is on the label rather than only in the
    dashing. The compact form of each pair is the node's own total, drawn where the node is too
    short for two lines; the tooltip always carries the full split to the cent, so the degraded
    label loses the breakdown and never the number.
    """
    sublabels: Dict[str, Tuple[str, str]] = {}
    tooltips: Dict[str, str] = {}
    for subject in sorted(set(margins.costs_by_subject) | set(margins.credits_by_subject)):
        cost = margins.costs_by_subject.get(subject, 0.0)
        credit = margins.credits_by_subject.get(subject, 0.0)
        if not credit:
            full, compact = f"costs {_fmt(cost)}", _fmt(cost)
        elif not cost:
            full, compact = f"credits -{_fmt(credit)}", f"-{_fmt(credit)}"
        else:
            full = f"costs {_fmt(cost)} | credits -{_fmt(credit)}"
            compact = _fmt(margins.extent_of(subject))
        sublabels[f"sub:{subject}"] = (full, compact)
        tooltips[f"sub:{subject}"] = (
            f"{subject}: costs {cost:,.2f} EUR, credits -{credit:,.2f} EUR, "
            f"block {margins.extent_of(subject):,.2f} EUR, net {margins.net_of(subject):,.2f} EUR"
        )
    for group in groups_cost:
        node = f"grp:{group}:0"
        total = margins.signed_total_by_group.get((group, False), 0.0)
        sublabels[node] = (_fmt(total), _fmt(total))
        tooltips[node] = f"{labels[node]}: {total:,.2f} EUR"
    for group in groups_credit:
        node = f"grp:{group}:1"
        total = margins.signed_total_by_group.get((group, True), 0.0)
        sublabels[node] = (_fmt(total), _fmt(total))
        tooltips[node] = f"{labels[node]}: {total:,.2f} EUR"
    return sublabels, tooltips


def _subject_flow_caption(margins: views.SubjectFlowMargins) -> str:
    """The reconciliation the cost-shapes blocks need, on the run's own widest block (Q28 R6).

    The rule — solid side is the component breakdown's cost column, dashed side its credits, the
    block is the two stacked — is authored prose and is printed above by `_explanation_html`.
    This caption is the arithmetic of that rule for *this* run, on the largest block, because the
    complaint the round is answering ("the chart does not add up") is only answered by numbers a
    reader can look up in the breakdown table two sections earlier.
    """
    subject = margins.widest_subject()
    if subject is None:
        return ""
    cost = margins.costs_by_subject.get(subject, 0.0)
    credit = margins.credits_by_subject.get(subject, 0.0)
    return (
        f"<p class='sub'><b>How a block reconciles.</b> {_esc(subject)}: {_fmt(cost)} EUR of cost "
        f"and {_fmt(credit)} EUR of credit, stacked into a block of "
        f"{_fmt(margins.extent_of(subject))} EUR. The component breakdown publishes their "
        f"difference, {_fmt(margins.net_of(subject))} EUR net; no table publishes the stacked "
        "extent, which is why the block is larger than either figure. Hover any ribbon for its "
        "exact amount.</p>"
    )


def _energy_balance_section_html(result: LifecycleCostResult, context: _ChapterContext) -> str:
    """V12: where the house's electricity came from and where it went, in year-1 kWh.

    Answers the question an energy-system tool exists to answer, in the picture every PV
    dashboard already shows its owner, and is skipped entirely when the run carries no device
    flows rather than drawn as a meter talking to itself.
    """
    if not views.has_energy_balance(result):
        log.information(
            f"Household energy balance skipped for perspective {result.perspective_id!r}: the "
            "result carries fewer than two device energy flows (a run from before the field "
            "existed, or a component set whose classes the adapter does not know); the meter's "
            "own grid import and export are not device flows."
        )
        return ""
    flows = views.energy_balance_flows(result)
    bus = "bus"
    ribbons: List[Tuple[str, str, float, str, bool]] = [
        (f"src:{node.label}", bus, node.quantity_in_kwh, "var(--g5)", False)
        for node in flows.sources
    ]
    ribbons += [
        (bus, f"snk:{node.label}", node.quantity_in_kwh, "var(--g5)", False)
        for node in flows.sinks
    ]
    labels = {bus: f"{views.EnergyBalanceLayout.BUS_LABEL} ({flows.bus_total_in_kwh:,.0f} kWh/a)"}
    for prefix, nodes in (("src", flows.sources), ("snk", flows.sinks)):
        for node in nodes:
            money = (
                f" = {_fmt(node.annotation_in_euro)} EUR"
                if node.annotation_in_euro is not None else ""
            )
            labels[f"{prefix}:{node.label}"] = (
                f"{node.label} {node.quantity_in_kwh:,.0f} kWh/a{money}"
            )
    columns = [
        [f"src:{node.label}" for node in flows.sources],
        [bus],
        [f"snk:{node.label}" for node in flows.sinks],
    ]
    shares = []
    if flows.self_consumption_share is not None:
        shares.append(f"self-consumption {flows.self_consumption_share:.0%} of the PV generation")
    if flows.self_sufficiency_share is not None:
        shares.append(f"self-sufficiency {flows.self_sufficiency_share:.0%} of what the house used")
    battery = (
        f" The battery is a pass-through: {_fmt(flows.battery_round_trip_loss_in_kwh)} kWh/a of "
        "what went in did not come back out, which is its round-trip loss."
        if flows.battery_round_trip_loss_in_kwh else ""
    )
    residual = next(
        (node for node in flows.sources + flows.sinks
         if node.label == views.EnergyBalanceLayout.RESIDUAL_LABEL), None
    )
    residual_prose = (
        f" {residual.quantity_in_kwh:,.0f} kWh/a are not attributed to any of the devices above "
        "and are shown as their own node rather than quietly balanced away."
        if residual is not None else ""
    )
    return (
        _section_open(ReportSections.ENERGY_BALANCE, context, result.perspective_id)
        + _explanation_html(ReportSections.ENERGY_BALANCE, context)
        + f"<p class='sub'>{_esc('; '.join(shares))}.{_esc(battery)}{_esc(residual_prose)}</p>"
        + _sankey_svg(columns, ribbons, labels, height=320) + "</section>"
    )


def _comparison_bridge_section_html(
    reference: LifecycleCostResult,
    variant: LifecycleCostResult,
    context: _ChapterContext,
) -> str:
    """V4: why the variant's NPV differs from the reference's, decomposed by cost group.

    Answers the decision question one level deeper than the total does: not "is it cheaper" but
    "what makes it cheaper", which is what a reader needs to judge whether the answer rests on
    one assumption or on many.
    """
    steps = views.comparison_bridge(reference, variant, PresentationStyle.CATEGORY_TO_GROUP)
    bridge_steps = [
        (PresentationStyle.DISPLAY_GROUPS[step.group][0], step.delta_in_euro, f"var(--g{step.group})")
        for step in steps
        if abs(step.delta_in_euro) >= 0.005
    ]
    delta = variant.total_npv_in_euro.average - reference.total_npv_in_euro.average
    return (
        _section_open(ReportSections.NPV_BRIDGE, context, variant.perspective_id)
        + _explanation_html(ReportSections.NPV_BRIDGE, context)
        + f"<p class='sub'>Anchor bands: {_esc(_band_str(reference.total_npv_in_euro))} reference, "
        f"{_esc(_band_str(variant.total_npv_in_euro))} variant.</p>"
        + _bridge_svg(
            (
                (f"reference: {reference.perspective_id}", reference.total_npv_in_euro),
                (f"variant: {variant.perspective_id}", variant.total_npv_in_euro),
            ),
            bridge_steps,
        )
        + f"<p class='sub'>Net NPV difference: <b>{_esc(_fmt(delta))} EUR</b> "
        "(variant minus reference, average scenario).</p></section>"
    )


def _wealth_benchmark_section_html(
    reference: LifecycleCostResult,
    variant: LifecycleCostResult,
    context: _ChapterContext,
) -> str:
    """V13: renovate, or leave the money in the bank at 1-10 % interest?

    Answers the question every homeowner actually asks, and turns the discount rate from an
    opaque parameter into something the reader can interrogate: the rate at which the terminal
    advantage crosses zero is the return the renovation has to beat.
    """
    benchmark = views.wealth_benchmark(reference, variant)
    years = list(range(len(benchmark.differential_flow_in_euro)))
    ramp = PresentationStyle.GROUP_COLORS_LIGHT
    series: List[Tuple[str, List[Tuple[float, float]], str, float, str]] = [
        (f"{rate:.0%}",
         [(float(year), value) for year, value in zip(years, benchmark.series_by_rate[rate])],
         ramp[index % len(ramp)], 1.0, "3 3")
        for index, rate in enumerate(benchmark.rates)
    ]
    parameter = benchmark.parameter_series_by_slot
    series.append((
        f"parameter rate {benchmark.parameter_rate:.1%}",
        list(zip(years, parameter[Slot.AVERAGE])), "var(--ink-1)", 2.4, "",
    ))
    trajectories = _xy_lines_svg(
        series=series,
        bands=[(list(zip(years, parameter[Slot.LOW])), list(zip(years, parameter[Slot.HIGH])),
                "var(--g0)")],
        y_label="advantage of renovating [EUR] - above zero means renovating is ahead",
        height=250,
    )
    terminal = _xy_lines_svg(
        series=[("terminal advantage",
                 [(rate * 100, benchmark.terminal_by_rate[rate]) for rate in benchmark.rates],
                 "var(--g0)", 2.2, "")],
        x_label="interest rate [%] (nominal, pre-tax)",
        y_label="advantage at the end of the horizon [EUR]",
        annotations=[(crossing * 100, 0.0, f"break-even {crossing:.1%}")
                     for crossing in benchmark.break_even_rates],
        height=210,
    )
    crossings = (
        ", ".join(f"{crossing:.1%}" for crossing in benchmark.break_even_rates)
        or "none inside the 1-10 % window shown"
    )
    return (
        _section_open(ReportSections.BANK_BENCHMARK, context, variant.perspective_id)
        + _explanation_html(ReportSections.BANK_BENCHMARK, context)
        + f"<p class='sub'>Break-even rate in this run: {_esc(crossings)}.</p>"
        + trajectories + terminal + "</section>"
    )


def _statement_table_html(statement: views.PerspectiveStatement) -> str:
    """The two-sided table every party statement shares (Q21, Q26 F4).

    One row per category with its present value and the side it sits on, then the two subtotals
    and the net position. The side column is labelled from the statement's own partition, so the
    society statement reads "real resource costs" / "transfers" where the household ones read
    "cash" / "accounting", without this function knowing which party it is drawing.

    The subtotals are printed even when a side is empty — the tenant's credit side always is —
    because a stated zero is the answer to "where is my credit side?" and a missing row is not.
    """
    partition = statement.partition
    rows = [
        [_esc(line.label), _fmt(line.npv_in_euro),
         partition.secondary_label if line.is_accounting_credit else partition.primary_label]
        for line in list(statement.cash_lines) + list(statement.accounting_lines)
    ]
    rows.append([
        f"<b>{_esc(partition.primary_label)}, subtotal</b>",
        f"<b>{_fmt(statement.cash_subtotal_in_euro)}</b>",
        f"<b>{_esc(partition.primary_label)}</b>",
    ])
    rows.append([
        f"<b>{_esc(partition.secondary_label)}, subtotal</b>",
        f"<b>{_fmt(statement.accounting_subtotal_in_euro)}</b>",
        f"<b>{_esc(partition.secondary_label)}</b>",
    ])
    rows.append([
        "<b>net position</b>", f"<b>{_fmt(statement.net_position_in_euro)}</b>", "<b>both sides</b>",
    ])
    return _table(["Item", "NPV [EUR]", "Side"], rows)


def _owner_statement_section_html(result: LifecycleCostResult, context: _ChapterContext) -> str:
    """The owner-occupier's two-sided statement (owner decision Q26 F4, rule 2.9).

    The owner rows of the perspectives table were single numbers until here. This decomposes them
    into the money that moved — investment net of subsidies, bills, maintenance, replacements,
    feed-in revenue and the loan flows where the financed view applies — and the value that was
    merely booked: the residual worth of the hardware and the anyway credit. The caption states
    how much of the result is each, because a strongly negative owner NPV carried by book value
    is a different proposition from the same figure carried by cash.

    Every figure comes from `views.perspective_statement`, which validates that the two sides sum
    to the perspective's NPV before this runs, so the table and the headline cannot disagree.
    """
    statement = views.perspective_statement(result, views.StatementPartitions.OWNER)
    if not statement.cash_lines and not statement.accounting_lines:
        log.information(
            f"Owner statement skipped for perspective {result.perspective_id!r}: it books no "
            "flows at all, so there is no position to state."
        )
        return ""
    return (
        _section_open(ReportSections.OWNER_STATEMENT, context, result.perspective_id)
        + _explanation_html(ReportSections.OWNER_STATEMENT, context)
        + _statement_caption(statement)
        + _statement_table_html(statement)
        + "</section>"
    )


def _tenant_statement_section_html(result: LifecycleCostResult, context: _ChapterContext) -> str:
    """The tenant's statement: everything paid because of the renovation, nothing received (F4).

    The tenant's side of the rented-out story, decomposed into the levy set by law and the energy
    and apportioned operating costs set by physics and prices — the split the fairness question
    turns on. The credit side is deliberately empty and its subtotal is printed as the zero it is:
    a tenant receives nothing back in this ledger, and a lower energy bill shows up as a smaller
    cost line rather than as income.

    The caption additionally checks the levy against the landlord statement's levy income, since
    the pair is booked as equal halves and a difference between the two sections would mean the
    transfer had leaked.
    """
    statement = views.perspective_statement(result, views.StatementPartitions.TENANT)
    if not statement.cash_lines and not statement.accounting_lines:
        log.information(
            f"Tenant statement skipped for perspective {result.perspective_id!r}: the allocation "
            "assigned this tenant no flows at all."
        )
        return ""
    levy_line = next(
        (line for line in statement.cash_lines
         if line.category == CostCategory.MODERNIZATION_LEVY),
        None,
    )
    levy_note = (
        f"The modernization levy accounts for <b>{_fmt(levy_line.npv_in_euro)} EUR</b> of the "
        f"tenant's position, the energy and operating costs for the rest; the levy is the exact "
        "counterpart of the landlord statement's levy income."
        if levy_line is not None else
        "This tenant pays no modernization levy, so the whole position is energy and apportioned "
        "operating cost."
    )
    return (
        _section_open(ReportSections.TENANT_STATEMENT, context, result.perspective_id)
        + _explanation_html(ReportSections.TENANT_STATEMENT, context)
        + _statement_caption(statement)
        + f"<p class='sub'>{levy_note}</p>"
        + _statement_table_html(statement)
        + "</section>"
    )


def _society_statement_section_html(result: LifecycleCostResult, context: _ChapterContext) -> str:
    """The macroeconomic statement: real resources against transfers that cancel (Q26 F4).

    The proof of what "macroeconomic" means rather than the word: the resource categories keep
    their values, the transfers appear with both halves and an explicit zero-sum line, and CO2
    enters at its damage cost rather than at any price a household pays. On the shipped
    macroeconomic perspective every transfer has already been removed at source (§4.5), so the
    transfer rows are the zeros that statement makes checkable — which is stated in the caption
    rather than left as an empty side.
    """
    statement = views.perspective_statement(result, views.StatementPartitions.SOCIETY)
    if not statement.cash_lines and not statement.accounting_lines:
        log.information(
            f"Society statement skipped for perspective {result.perspective_id!r}: it books no "
            "flows at all."
        )
        return ""
    transfers = (
        "The macroeconomic accounting removes every transfer at source — no subsidy, no feed-in "
        "remuneration, no CO2 price and no levy is booked in this view — so the transfer side "
        "sums to <b>0.00 EUR</b> and the net position is real resource use alone."
        if not statement.accounting_lines else
        "Each transfer appears with both of its halves, so the side sums to "
        f"<b>{_fmt(statement.accounting_subtotal_in_euro)} EUR</b>."
    )
    damage = result.npv_by_category.get(CostCategory.CO2_DAMAGE)
    damage_note = (
        f" CO2 enters as a cost of {_esc(_band_str(damage))} at a damage cost of "
        f"{result.parameters.co2_damage_cost_in_euro_per_ton:,.2f} EUR/t, flat over the horizon "
        "(see <i>Assumptions</i>)."
        if damage is not None else
        " This view books no CO2 damage cost."
    )
    return (
        _section_open(ReportSections.SOCIETY_STATEMENT, context, result.perspective_id)
        + _explanation_html(ReportSections.SOCIETY_STATEMENT, context)
        + f"<p class='sub'>{transfers}{damage_note}</p>"
        + _statement_table_html(statement)
        + "</section>"
    )


def _statement_caption(statement: views.PerspectiveStatement) -> str:
    """The run's own two subtotals under a party statement, in the partition's own words.

    The prose says what the two sides *are*; this says what they came to here, which is the half a
    reader cannot get from anywhere else on the page.
    """
    partition = statement.partition
    return (
        f"<p class='sub'>{_esc(partition.primary_label.capitalize())} come to "
        f"<b>{_fmt(statement.cash_subtotal_in_euro)} EUR</b> and "
        f"{_esc(partition.secondary_label)} to "
        f"<b>{_fmt(statement.accounting_subtotal_in_euro)} EUR</b> in present value; together they "
        f"are the net position of {_esc(_band_str(statement.net_position_band))}.</p>"
    )


def _landlord_statement_section_html(result: LifecycleCostResult, context: _ChapterContext) -> str:
    """The landlord's two-sided statement plus the income Sankey of the same numbers (Q21, Q25).

    Answers the question the landlord row of the perspectives table cannot: a strongly negative
    net position reads as a gain, but part of it is the residual book value of the hardware and
    the avoided cost of a renovation the building needed anyway — value, not income. The table
    states the cash side and the accounting side separately before combining them, and the Sankey
    below it draws the same statement the way an income statement is drawn: what arrives from the
    left, what leaves to the right, and the ribbon left over is the bottom line.

    Every number comes from `views.landlord_statement`, which validates that the two sides sum to
    the perspective's NPV before this ever runs, so the table, the picture and the headline figure
    cannot disagree.
    """
    statement = views.landlord_statement(result)
    if not statement.cash_lines and not statement.accounting_lines:
        log.information(
            f"Landlord statement skipped for perspective {result.perspective_id!r}: it books no "
            "flows at all, so there is no business case to state."
        )
        return ""
    rows = [
        [_esc(line.label), _fmt(line.npv_in_euro),
         "accounting" if line.is_accounting_credit else "cash"]
        for line in list(statement.cash_lines) + list(statement.accounting_lines)
    ]
    rows.append([
        "<b>cash flows, subtotal</b>", f"<b>{_fmt(statement.cash_subtotal_in_euro)}</b>", "<b>cash</b>",
    ])
    rows.append([
        "<b>accounting credits, subtotal</b>",
        f"<b>{_fmt(statement.accounting_subtotal_in_euro)}</b>",
        "<b>accounting</b>",
    ])
    rows.append([
        "<b>net position</b>", f"<b>{_fmt(statement.net_position_in_euro)}</b>", "<b>both sides</b>",
    ])
    return (
        _section_open(ReportSections.LANDLORD_STATEMENT, context, result.perspective_id)
        + _explanation_html(ReportSections.LANDLORD_STATEMENT, context)
        + _landlord_statement_caption(statement)
        + _table(["Item", "NPV [EUR]", "Side"], rows)
        + _landlord_statement_sankey(statement)
        + "</section>"
    )


def _landlord_statement_caption(statement: views.PerspectiveStatement) -> str:
    """The run's own figures under the landlord statement: levy, cap verdict, the two subtotals.

    The prose says what the two sides *are*; this says what they came to here, which is the half a
    reader cannot get from anywhere else. The levy clause states whether a statutory ceiling
    decided the rent increase, because below the cap the levy scales with what was spent and at
    the cap it does not — the same renovation costing more would then produce the identical
    increase, which is a completely different business case and invisible in the amount itself.
    """
    levy = statement.levy
    if levy is None:
        levy_note = "This perspective books no modernization levy."
    elif levy.cap_binding and levy.cap_in_euro_per_m2_per_month is not None:
        levy_note = (
            f"Levy income {_fmt(levy.annual_amount_in_euro.average)} EUR per year, <b>set by the "
            f"§559 cap</b> of {levy.cap_in_euro_per_m2_per_month:,.2f} EUR/m²·month — not by the "
            "modernization cost, which would have supported more."
        )
    else:
        levy_note = (
            f"Levy income {_fmt(levy.annual_amount_in_euro.average)} EUR per year; the §559 cap "
            "does not bind, so the increase is set by the modernization cost."
        )
    if levy is not None:
        levy_note += _levy_world_verdicts(levy)
    return (
        f"<p class='sub'>{levy_note} Cash flows come to "
        f"<b>{_fmt(statement.cash_subtotal_in_euro)} EUR</b> and accounting credits to "
        f"<b>{_fmt(statement.accounting_subtotal_in_euro)} EUR</b> in present value; together they "
        f"are the net position of {_esc(_band_str(statement.net_position_band))}. A negative net "
        "position is an advantage on the stated basis, but only the cash half of it ever reaches "
        "an account.</p>"
    )


def _levy_world_verdicts(levy) -> str:
    """Which mechanism set the levy in each of the three worlds (owner decision Q26 F5).

    The caps are applied per slot, so "the cap binds" is an average-slot statement that can be
    false in the cheap world and true in the expensive one — two economically different answers
    to the question a landlord actually asks, which is whether spending more would raise the rent.
    The ruleset records the verdict per world; this states them, and collapses them into one
    sentence when all three agree, because three identical clauses read as a defect.

    Args:
        levy: The `ModernizationLevySummary` of the perspective; its `binding_mechanism_by_slot`
            is empty for a result stored before the verdicts were recorded, and nothing is added.

    Returns:
        A sentence to append to the levy note, or the empty string.
    """
    verdicts = levy.binding_mechanism_by_slot
    if not verdicts:
        return ""
    slots = [slot for slot in ("average", "low", "high") if slot in verdicts]
    distinct = {verdicts[slot] for slot in slots}
    if len(distinct) == 1:
        return f" Binding mechanism in all three worlds: <b>{_esc(next(iter(distinct)))}</b>."
    stated = "; ".join(f"{slot} {_esc(verdicts[slot])}" for slot in slots)
    return f" Binding mechanism per world: <b>{stated}</b>."


def _landlord_statement_sankey(statement: views.PerspectiveStatement) -> str:
    """The statement as an income Sankey: income left, expenses right, the leftover is the result.

    The earnings-statement convention (Q25). Income ribbons arrive at the landlord node from the
    left and expense ribbons leave it to the right; whatever is left over runs on into a terminal
    node labelled with the net position, so the bottom line is a ribbon rather than a number
    somebody has to add up. Accounting credits are drawn in the report's credit style — outlined,
    translucent and dashed where cash is solid — so the split the table states is visible in the
    picture rather than only stated beside it.

    Both signs are handled: when the renovation is a net cost for the landlord the leftover cannot
    leave, so the net position enters from the left instead, and the caption says which way round
    the drawing is.
    """
    flows, net_is_inflow = statement.income_flows()
    if not flows:
        return ""
    node = views.LandlordStatementCategories.LANDLORD_NODE
    net_node = views.LandlordStatementCategories.NET_POSITION_NODE
    incomes = [source for source, target, _amount, _credit, _category in flows if target == node]
    expenses = [target for source, target, _amount, _credit, _category in flows if source == node]
    ribbons = [
        (f"in:{source}" if target == node else f"mid:{source}",
         f"mid:{target}" if target == node else f"out:{target}",
         amount,
         f"var(--g{group_of(category)})" if category is not None else "var(--muted)",
         is_credit)
        for source, target, amount, is_credit, category in flows
    ]
    labels = {f"in:{name}": name for name in incomes}
    labels.update({f"out:{name}": name for name in expenses})
    labels[f"mid:{node}"] = node
    net_label = f"{net_node} ({_fmt(abs(statement.net_position_in_euro))} EUR)"
    labels[f"in:{net_node}"] = net_label
    labels[f"out:{net_node}"] = net_label
    columns = [
        [f"in:{name}" for name in incomes],
        [f"mid:{node}"],
        [f"out:{name}" for name in expenses],
    ]
    direction = (
        "The renovation is a net cost here, so the net position is drawn entering from the left: "
        "the missing money has to come from somewhere."
        if net_is_inflow else
        "The ribbon leaving on the right with no destination of its own is the net position - the "
        "bottom line of the statement, in the earnings-statement convention."
    )
    return (
        _sankey_svg(columns, ribbons, labels, height=320)
        + "<p class='sub'>Dashed, translucent ribbons are the accounting credits; solid ribbons "
        f"are cash. {_esc(direction)}</p>"
    )


def _monthly_burden_section_html(result: LifecycleCostResult, context: _ChapterContext) -> str:
    """V14: what this costs per month, year by year — the unit households budget in.

    Answers the cash curve's question in the lay reader's unit. The definitional decision (every
    capital event excluded, replacements smoothed into a reserve line) is stated in the prose,
    because a monthly figure whose scope is unstated is the easiest number in the report to
    misread.
    """
    burden = views.monthly_burden_series(result)
    if not burden.series:
        return ""
    year_one = burden.series[1] if len(burden.series) > 1 else burden.series[0]
    reserve = burden.replacement_reserve_per_month
    reserve_prose = (
        f"The dashed reserve line is at {_esc(_fmt(reserve))} EUR/month."
        if reserve else
        "This evaluation books no replacement, so there is no reserve line."
    )
    return (
        _section_open(ReportSections.MONTHLY_BURDEN, context, result.perspective_id)
        + _explanation_html(ReportSections.MONTHLY_BURDEN, context)
        + f"<p class='sub'>Year 1 is {_esc(_band_str(year_one, 'EUR/month'))}. {reserve_prose}</p>"
        + _monthly_burden_svg(result) + "</section>"
    )


def _equity_section_html(result: LifecycleCostResult, context: _ChapterContext) -> str:
    """V15: asset book value against outstanding debt — the lender's solvency picture.

    Answers "how much of the installation do I own", and carries audit weight beyond that: the
    book-value line is the same depreciation basis the residual calculator uses, so a defect in
    it is visible along the whole curve rather than only at the horizon.
    """
    amortization = views.loan_amortization_series(result)
    if not amortization.has_flows():
        log.information(
            f"Equity build-up chart skipped for perspective {result.perspective_id!r}: it is "
            "unfinanced, so there is no debt line and no gap story."
        )
        return ""
    series = views.asset_debt_series(result)
    years = list(range(len(series.book_value_in_euro)))
    underwater = (
        f"Equity is negative between year {series.underwater_interval[0]} and year "
        f"{series.underwater_interval[1]}: the debt exceeds the book value there, which is the "
        "interval a lender looks for."
        if series.underwater_interval is not None else
        "Equity stays positive over the whole horizon."
    )
    chart = _xy_lines_svg(
        series=[
            ("asset book value", list(zip(years, series.book_value_in_euro)), "var(--g4)", 2.2, ""),
            ("outstanding debt", list(zip(years, series.debt_in_euro)), "var(--g0)", 2.2, ""),
            ("equity", list(zip(years, series.equity_in_euro)), "var(--g1)", 1.4, "5 4"),
        ],
        bands=[(list(zip(years, series.debt_in_euro)), list(zip(years, series.book_value_in_euro)),
                "var(--g1)")],
        y_label="EUR (nominal) - book value, debt and the equity between them",
        height=240,
    )
    return (
        _section_open(ReportSections.EQUITY_BUILD_UP, context, result.perspective_id)
        + _explanation_html(ReportSections.EQUITY_BUILD_UP, context)
        + f"<p class='sub'>At the horizon the book value equals the residual value credited in "
        f"the results ({_fmt(series.residual_credit_in_euro)} EUR). {underwater}</p>"
        + chart + "</section>"
    )


def build_lifecycle_report_html(
    matrix: EvaluationMatrix,
    plausibility: PlausibilityReport,
    audit: Optional[InputAuditReport] = None,
    comparison: Optional[VariantComparison] = None,
    scenario_cube=None,
    reference_result: Optional[LifecycleCostResult] = None,
) -> str:
    """The self-contained HTML report, sections along the calculation chain.

    The module's main entry point and the assembly of everything above: a header stating the
    run's parameters, a table of contents, every `_*_section_html` block in the order
    `ReportSections.ORDER` declares (the comparison trio only when comparing), and a footer
    telling the reader how to trace any figure back to its sources with
    `python -m hisim.economics explain`. The order is the calculation chain, not a menu: each
    section is placed where a mistake made upstream of it first becomes visible, which is why
    the contents are a navigation aid rather than the structure itself.

    The returned document is a **single file with no external references** — stylesheet inlined
    from `_ReportCss`, charts as inline SVG, tooltips native, no script and no font, image or
    CDN request — so it survives being mailed, archived beside the results or opened offline.
    Sections that have nothing to show return the empty string and vanish rather than rendering
    an empty box, which is why the list is concatenated blindly. When every band is degenerate
    the header carries the `_degenerate_note` explanation, so missing whiskers read as a
    property of the price data rather than as a broken feature.

    Rendering is deterministic given the inputs except for the generation date, which is why the
    golden test normalizes exactly that and byte-compares the rest.

    Args:
        matrix: Evaluated perspectives; the first is the reference used for the single-result
            sections (investment, energy bill, CO2) and for the scenario section's base.
        plausibility: The panel rendered as the plausibility section.
        audit: Optional resolved-input audit; the input-audit section is omitted without it.
        comparison: Optional variant-vs-reference comparison; appends the comparison sections.
        scenario_cube: Optional `ScenarioCube` (untyped by the seam-4 import rule); adds
            the scenarios section.
        reference_result: The comparison's baseline result. Needed by the two charts that
            decompose a comparison rather than restating it — the V4 bridge (by cost group) and
            the V13 fixed-interest benchmark (per-year differential flows) — because a
            `VariantComparison` publishes neither. Without it those two sections are omitted and
            the omission is logged.

    Returns:
        The complete HTML document as one string.
    """
    reference = next(iter(matrix.results.values()))
    params = reference.parameters
    header = (
        f"<h1>Lifecycle cost report</h1><p class='sub'>Simulation year {reference.simulation_year}, "
        f"country {_esc(params.country)}, horizon {params.observation_period_in_years} a, "
        f"interest {params.interest_rate:.1%}, price basis {params.price_basis_year}, "
        f"CO2 scenario &#39;{_esc(params.co2_price_scenario)}&#39;. All money as avg [min | max] "
        f"envelope bands (§3.9). Generated {datetime.date.today().isoformat()}.</p>"
    )
    if all_bands_degenerate(matrix):
        header += (
            "<section style='border-left:4px solid var(--warning)'><b>No uncertainty bands in "
            f"this run.</b> <span class='sub'>{_esc(_degenerate_note(matrix))}</span></section>"
        )
    stories = views.story_perspectives(list(matrix.results.values()))
    context = _ChapterContext(chapter=ReportChapters.THE_BUILDING)
    parts = [
        _chapter_open(ReportChapters.THE_BUILDING),
        _building_chapter_html(matrix, plausibility, audit, comparison, scenario_cube, context),
    ]
    parts.extend(_owner_chapter_html(stories, comparison, context))
    parts.extend(_rented_chapter_html(stories, comparison, context))
    parts.extend(_society_chapter_html(stories, comparison, context))
    parts.extend(_comparison_chapter_html(matrix, comparison, reference_result, context))
    footer = (
        "<footer>Every number is traceable: "
        "<code>python -m hisim.economics explain &lt;results_dir&gt; --value "
        f"\"{_esc(reference.perspective_id)}/total_npv_in_euro\"</code> — hisim.economics</footer>"
    )
    document = "".join(parts)
    return (
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>Lifecycle cost report</title><style>{_ReportCss.CSS}</style></head>"
        f"<body><main>{header}{_table_of_contents_html(document)}{document}{footer}</main></body></html>"
    )


def _building_chapter_html(
    matrix: EvaluationMatrix,
    plausibility: PlausibilityReport,
    audit: Optional[InputAuditReport],
    comparison: Optional[VariantComparison],
    scenario_cube,
    context: _ChapterContext,
) -> str:
    """The common part: what the technology costs, before asking whose money it is (rule 2.8).

    Everything on the gross / perspective-free basis, in the order the numbers are built in —
    inputs, then what they add up to, then the flows over the years, then the physics and the
    emissions, then where the uncertainty sits, and finally the perspectives table as the bridge
    into the story chapters. The sections that need *a* perspective take the matrix's first, which
    is the reference view of the run, exactly as before the restructure.

    Scenarios sits here rather than in a story chapter: the spec's chapter table does not assign
    it, and a sensitivity sweep over the base perspective is a statement about the priced inputs,
    not about a party.
    """
    reference = next(iter(matrix.results.values()))
    attributed = _first_result_where(matrix, views.has_energy_balance) or reference
    return "".join([
        # The primer first: discounting, the three worlds and the sign rule, stated once for
        # every section that follows.
        _how_to_read_section_html(context),
        # V9 then opens the analysis: the one-page overview a renovation report starts with (Q1).
        _lifecycle_overview_section_html(reference, comparison, context),
        _checks_section_html(plausibility, context),
        _audit_section_html(audit, context) if audit is not None else "",
        # Q26 F2: the causes, directly after the audit of what was priced and before the first
        # figure that is computed from them.
        _assumptions_section_html(
            reference,
            context,
            co2_damage_priced=_first_result_where(matrix, views.prices_co2_damage) is not None,
        ),
        _investment_section_html(reference, context),
        _component_events_section_html(reference, context),
        _timeline_section_html(matrix, context),
        _energy_section_html(reference, context),
        _energy_balance_section_html(attributed, context),
        _co2_section_html(matrix, context),
        _subsidy_section_html(matrix, context),
        _uncertainty_section_html(reference, context),
        _components_section_html(matrix, context),
        _treemap_section_html(reference, context),
        _subject_flows_section_html(reference, context),
        _scenario_section_html(scenario_cube, matrix, context),
        _perspective_section_html(matrix, context),
        _kpi_section_html(matrix, context),
    ])


def _sub_matrix(results: List[LifecycleCostResult]) -> EvaluationMatrix:
    """An `EvaluationMatrix` of just these results, for a chapter that owns only some of them.

    The matrix-shaped section builders (the loan panel, who-pays-what) render one block per
    perspective they are given, so restricting a chapter to its own story is a matter of handing
    them a smaller matrix rather than teaching each of them a filter.
    """
    return EvaluationMatrix(results={result.perspective_id: result for result in results})


def _comparison_for(
    results: Sequence[LifecycleCostResult], comparison: Optional[VariantComparison]
) -> Optional[VariantComparison]:
    """The comparison, but only for the chapter whose story it is actually about.

    A `VariantComparison` is computed for *one* perspective, so handing it to a chapter that does
    not contain that perspective would draw the owner's payback under the landlord's cash curve.
    The chapters that do not own it show their cumulative discounted cost instead, which is what
    the cash curve does without a reference anyway.
    """
    if comparison is None:
        return None
    owns = any(result.perspective_id == comparison.perspective_id for result in results)
    return comparison if owns else None


def _owner_chapter_html(
    stories: views.StoryPerspectives,
    comparison: Optional[VariantComparison],
    context: _ChapterContext,
) -> List[str]:
    """The owner-occupied story: how a household pays for this and lives with it (rule 2.8).

    Funding, the cash curve, the loan and what it costs, the monthly burden, the equity build-up
    and who pays whom — all on the net / owner perspectives `views.story_perspectives` selected,
    i.e. the after-subsidy views a household actually pays out of its own account. Each section
    still picks the perspective that *has* its subject matter (a loan chart needs a financed one),
    but now only from within this chapter's story.
    """
    if not stories.owner:
        log.information(
            "Owner-occupied chapter skipped: no perspective of this run tells an owner's story "
            "(neither an owner-scoped nor a support-carrying one)."
        )
        return []
    chapter = context.for_chapter(ReportChapters.OWNER_OCCUPIED)
    owner_matrix = _sub_matrix(list(stories.owner))
    lead = stories.owner[0]
    financed = _first_result_where(
        owner_matrix, lambda result: views.loan_amortization_series(result).has_flows()
    ) or lead
    funded = _first_result_where(owner_matrix, _has_year_zero_funding) or lead
    multi_actor = _first_result_where(
        owner_matrix, lambda result: len({entry.payer for entry in result.timeline.entries}) > 1
    ) or lead
    return [
        _chapter_open(ReportChapters.OWNER_OCCUPIED),
        "".join([
            # Q26 F4: the statement opens the chapter for the same reason the landlord's opens
            # the rented one — it is what makes every figure after it readable. It is rendered on
            # the financed view where the run has one, because the loan flows belong on the cash
            # side of an owner's statement; without financing that is the chapter's lead anyway.
            _owner_statement_section_html(financed, chapter),
            _sources_uses_section_html(funded, chapter),
            _liquidity_section_html(lead, _comparison_for(stories.owner, comparison), chapter),
            _loan_section_html(owner_matrix, chapter),
            _cost_of_credit_section_html(financed, chapter),
            _monthly_burden_section_html(lead, chapter),
            _equity_section_html(financed, chapter),
            _actor_flow_section_html(multi_actor, chapter),
        ]),
    ]


def _rented_chapter_html(
    stories: views.StoryPerspectives,
    comparison: Optional[VariantComparison],
    context: _ChapterContext,
) -> List[str]:
    """The rented-out story: the landlord's business case and the tenant's monthly reality.

    Rendered only when the allocation actually produced landlord or tenant perspectives; a run of
    an owner-occupied house has no rented story and the chapter is skipped with a log line rather
    than drawn empty. The landlord statement opens it because it is the section that separates the
    landlord's cash from the landlord's book value, which is what makes every figure after it
    readable.
    """
    if not stories.rented:
        log.information(
            "Rented-out chapter skipped: the allocation produced no landlord or tenant "
            "perspective, so this run has no rented story to tell."
        )
        return []
    chapter = context.for_chapter(ReportChapters.RENTED_OUT)
    rented_matrix = _sub_matrix(list(stories.rented))
    landlord = next(
        (result for result in stories.rented if result.scope_payer == Actor.LANDLORD),
        stories.rented[0],
    )
    tenant = next(
        (result for result in stories.rented if result.scope_payer == Actor.TENANT),
        stories.rented[0],
    )
    return [
        _chapter_open(ReportChapters.RENTED_OUT),
        "".join([
            _landlord_statement_section_html(landlord, chapter),
            _tenant_statement_section_html(tenant, chapter),
            _actor_section_html(rented_matrix, chapter),
            _actor_flow_section_html(landlord, chapter),
            _monthly_burden_section_html(tenant, chapter),
            _liquidity_section_html(landlord, _comparison_for(stories.rented, comparison), chapter),
        ]),
    ]


def _society_chapter_html(
    stories: views.StoryPerspectives,
    comparison: Optional[VariantComparison],
    context: _ChapterContext,
) -> List[str]:
    """The macroeconomic story: transfers cancel, CO2 enters at its damage cost (rule 2.8).

    Two sections only, because that is all the perspective supports: the cash curve of the
    resource cost over time and who pays whom once the transfers between the parties have netted
    themselves out. Skipped with a log line when the bundle evaluated no macroeconomic
    perspective.
    """
    if not stories.society:
        log.information(
            "Society chapter skipped: this run evaluated no macroeconomic perspective, so there "
            "is no view in which transfers cancel and CO2 is priced at its damage cost."
        )
        return []
    chapter = context.for_chapter(ReportChapters.SOCIETY)
    macro = stories.society[0]
    return [
        _chapter_open(ReportChapters.SOCIETY),
        "".join([
            _society_statement_section_html(macro, chapter),
            _liquidity_section_html(macro, _comparison_for(stories.society, comparison), chapter),
            _actor_flow_section_html(macro, chapter),
        ]),
    ]


def _comparison_chapter_html(
    matrix: EvaluationMatrix,
    comparison: Optional[VariantComparison],
    reference_result: Optional[LifecycleCostResult],
    context: _ChapterContext,
) -> List[str]:
    """The fourth block: the three sections that only exist when there is a reference variant.

    Not one of the three stories — it answers a question about two runs rather than about one
    party — which is why it carries no authored lead-in and sits at the end.
    """
    if comparison is None:
        return []
    chapter = context.for_chapter(ReportChapters.COMPARISON)
    reference = next(iter(matrix.results.values()))
    variant = matrix.results.get(comparison.perspective_id, reference)
    blocks = [_comparison_section_html(comparison, chapter)]
    if reference_result is not None:
        # V4 and V13 need both results, not just the published deltas: the bridge splits by
        # cost group and the benchmark needs the per-year differential flows.
        blocks.append(_comparison_bridge_section_html(reference_result, variant, chapter))
        blocks.append(_wealth_benchmark_section_html(reference_result, variant, chapter))
    else:
        log.information(
            "Comparison bridge and fixed-interest benchmark skipped: the report was built "
            "with a comparison but without the reference result they decompose."
        )
    return [_chapter_open(ReportChapters.COMPARISON), "".join(blocks)]


def write_lifecycle_report(
    matrix: EvaluationMatrix,
    plausibility: PlausibilityReport,
    result_directory: str,
    audit: Optional[InputAuditReport] = None,
    comparison: Optional[VariantComparison] = None,
    file_name: str = ReportFileNames.LIFECYCLE_REPORT_FILE_NAME,
    scenario_cube=None,
    reference_result: Optional[LifecycleCostResult] = None,
) -> str:
    """Writes the HTML report.

    The filesystem counterpart of `build_lifecycle_report_html`, kept separate for the same
    reason as the markdown pair: the golden oracle and the unit tests render without touching a
    directory, while `bridge.py` and the `report` CLI get one call. `file_name` is a parameter
    rather than a constant so a second report can be written beside the first under
    `ReportFileNames.COMPARISON_REPORT_FILE_NAME` without overwriting it.

    Args:
        matrix: Evaluated perspectives.
        plausibility: The panel for the plausibility section.
        result_directory: Directory to write into (the run's `results/`).
        audit: Optional input audit for the input-audit section.
        comparison: Optional variant comparison for the comparison section.
        file_name: Output file name; defaults to `lifecycle_report.html`.
        scenario_cube: Optional scenario cube for the scenarios section.
        reference_result: The comparison's baseline, for the two charts that decompose it (V4,
            V13); see `build_lifecycle_report_html`.

    Returns:
        The path written.
    """
    path = os.path.join(result_directory, file_name)
    with open(path, "w", encoding="utf-8") as file:
        file.write(
            build_lifecycle_report_html(
                matrix, plausibility, audit, comparison, scenario_cube, reference_result
            )
        )
    return path
