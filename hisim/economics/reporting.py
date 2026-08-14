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
with the table it was drawn from. The HTML report numbers its sections along that chain — **0**
plausibility panel, **1** input audit, **2** year-0 investment waterfalls, **3** cash-flow
timeline, **4** year-1 energy bill, **4b** lifecycle CO2, **5** subsidy decisions, **6**
perspectives, **6b** actor split, **7** per-component stacks, **8** variant comparison, **9**
scenarios, **10** the lifecycle KPI table — and each `_*_section_html` function below documents
the question its section exists to answer. The most load-bearing of them is section 4: an
effective price of 300 EUR/kWh or 0.0003 EUR/kWh is the fastest detector of a unit mix-up
anywhere between the meter and the tariff, and it needs no domain knowledge to spot.

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
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from hisim.economics import views
from hisim.economics.input_audit import InputAuditReport, OriginKind, ResolvedInputRow
from hisim.economics.plausibility import CheckIds, PlausibilityFinding, PlausibilityReport
from hisim.economics.presentation_style import PresentationStyle, group_of
from hisim.economics.results import EvaluationMatrix, LifecycleCostResult, VariantComparison
from hisim.economics.timeline import CostCategory
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

    The rendering half of section 0, and the module's public entry point for it: both report
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
        if total is not None and (total.average or total.minimum or total.maximum):
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

    Shown inside section 3 only when the perspective is financed, and it answers a question the
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


def _co2_section_html(matrix: EvaluationMatrix) -> str:
    """Section 4b: lifecycle CO2 (§3.8) — embodied vs. operational, per subject/carrier.

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
        "<section><h2>4b - Lifecycle CO2</h2>"
        '<p class="sub">Undiscounted, parallel to the money (§3.8): embodied emissions at install '
        "and each replacement (blue) and operational emissions per carrier (orange). The CO2 "
        "<i>price</i> (a cash flow) and the CO2 <i>damage cost</i> (macroeconomic) are separate "
        "and never added to these masses.</p>"
        + bars + line
        + _details("CO2 table [kg]", _table(["Subject / carrier", "Embodied", "Operational", "Total"], table_rows))
        + "</section>"
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

    The centrepiece of section 3 and the chart most likely to expose a modelling mistake at a
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
    that the label agrees with section 6.

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

    Used by two sections with the same shape of question — section 2 ("how does the year-0 gross
    become the net outflow") and section 8 ("how does each subject's delta add up to the total
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
    a bar from LOW to HIGH. It is generic on purpose — sections 6 (perspectives), 6b (payers)
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

    Section 7's chart, and the one that makes the §7.4 reconciliation checkable by eye: the
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


def _audit_section_html(audit: InputAuditReport) -> str:
    """Section 1: input audit — are the declared facts and resolved prices right?

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
        "<td>{life}</td><td>{origin}</td><td class=\"flag\">{flags}</td></tr>".format(
            subject=_esc(row.subject),
            cls=_esc(row.asset_class),
            size=row.size,
            unit=_esc(row.size_unit),
            price=_esc(_band_str(row.unit_price_in_euro, "EUR/unit")),
            life=f"{row.lifetime_in_years:g} a" if row.lifetime_in_years else "-",
            origin=_esc(_origin_label(row)),
            flags=_esc("; ".join(row.flags)),
        )
        for row in audit.rows
    ]
    return (
        "<section><h2>1 - Input audit</h2>"
        '<p class="sub">Every priced fact with its resolved unit price and origin. '
        "Wiring mistakes (wrong config field, missing source) surface here first (§9.5).</p>"
        "<table><tr><th>Subject</th><th>Asset class</th><th>Size</th><th>Unit price</th>"
        "<th>Lifetime</th><th>Origin</th><th>Flags</th></tr>" + "".join(rows) + "</table>"
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


def _investment_section_html(result: LifecycleCostResult) -> str:
    """Section 2: year-0 investment build-up waterfall per subject.

    Answers "is the money that leaves the account in year 0 the money this measure should
    cost?" — one waterfall per component walking device + installation + planning + removal
    - subsidies - loan disbursement down to the net outflow, plus a table of gross, support and
    net per subject. It sits directly after the input audit because that is the order the
    numbers are built in: section 1 shows the unit prices, this shows what they add up to, and
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
        "<section><h2>2 - Investment build-up (year 0)</h2>"
        '<p class="sub">Device + installation + planning + removal - subsidies = net outflow. '
        "Binding subsidy caps and missing components are visible here.</p>" + "".join(blocks)
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
    for detail_year in views.timeline_detail_rows(result):
        for row in detail_year.rows:
            rows.append(
                [
                    str(row.year),
                    _esc(row.subject),
                    _esc(row.category.value),
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


def _timeline_section_html(matrix: EvaluationMatrix) -> str:
    """Section 3: annual cash flows + cumulative discounted cost, per perspective.

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
        loan_chart = _loan_svg(result)
        if loan_chart:
            loan_chart = (
                "<p class='sub'>Financing (§4.4): loan amortization replacing the year-0 outflow:</p>"
                + loan_chart
            )
        body = (
            _legend_html(groups_present)
            + _annual_flow_svg(result)
            + "<p class='sub'>Cumulative discounted cost (separate axis — the horizon NPV):</p>"
            + _cumulative_npv_svg(result)
            + loan_chart
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
        "<section><h2>3 - Cash-flow timeline</h2>"
        '<p class="sub">Nominal flows per year, stacked by display group; costs above the axis, '
        "revenues/credits below. Check: replacement spikes at the right years, residual value at the "
        "horizon, believable energy escalation. Anyway-cost credits (§4.1) appear at the year the "
        "avoided like-for-like renovation would have occurred — the detail table below the chart "
        "attributes every flow.</p>" + "".join(blocks) + "</section>"
    )


def _energy_section_html(result: LifecycleCostResult) -> str:
    """Section 4: year-1 bill decomposition per carrier with implied effective prices.

    **The fastest unit-mix-up detector in the report**, and the reason it is placed this early:
    dividing what a carrier cost in year 1 by how much of it was bought must give back a price
    the reader recognizes, and no domain expertise is needed to see that 0.0003 or 312 EUR/kWh
    is wrong. A mistake anywhere between the meter, the annualization and the tariff — a Wh/kWh
    confusion, a rate stored in cents, a missing time-of-use band — lands on this one number,
    which is why the same figure is also checked automatically in section 0.

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
        "<section><h2>4 - Year-1 energy bill</h2>"
        '<p class="sub">Per carrier with the implied effective price (total / bought quantity) — '
        "the fastest unit-mix-up detector. Feed-in shows as negative.</p>"
        + _whisker_svg(rows, "EUR/a")
        + "<details><summary>decomposition table</summary><table>"
        "<tr><th>Carrier</th><th>Quantity [kWh/a]</th><th>Cost year 1 [EUR]</th>"
        "<th>Effective [EUR/kWh]</th>"
        "<th>Components</th></tr>" + "".join(detail_rows) + "</table></details></section>"
    )


def _subsidy_composition_svg(matrix: EvaluationMatrix) -> str:
    """Per measure: net cost (blue) + subsidy amount (green) — how far the support carries.

    The visual half of section 5, answering "what fraction of each measure does the support
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
                round(award.upfront_amount.average, 2),
                round(views.award_total_amount(award).average, 2),
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


def _subsidy_awards_table(matrix: EvaluationMatrix) -> str:
    """All awards across measures: scheme, amount band, payout kind, binding caps.

    The tabular form of the §5.4 audit trail: every applied award with what it is worth, how it
    is paid out (upfront grant, repayment grant, scheduled tax credit) and which uncertainty
    slots its cap bound in. The payout kind matters to a reviewer because it changes *when* the
    money lands and therefore its present value, and a cap that binds only in the HIGH slot
    explains an asymmetric band elsewhere in the report.

    Amounts come from `views.award_total_amount`, so a scheduled payout is shown as the sum of
    its instalments rather than as its zero upfront amount. Rows are de-duplicated by decision
    *content* (`_decisions_by_content`), not by measure name: perspectives that awarded a measure
    the same way share one row, named in the "Perspectives" column, while a perspective that
    decided differently gets its own rows. Returns empty when no award applied anywhere.
    """
    rows = []
    for decision, perspective_ids in _decisions_by_content(matrix):
        note = _perspectives_note(perspective_ids, matrix)
        for award in decision.applied:
            caps = ", ".join(slot for slot, bound in award.caps_binding_per_slot.items() if bound) or "-"
            amount = views.award_total_amount(award)
            rows.append([
                _esc(decision.measure_subject),
                _esc(award.scheme_id),
                _esc(_band_str(amount)),
                _esc(award.payout_kind.value),
                _esc(caps),
                _esc(note),
            ])
    if not rows:
        return ""
    return _details(
        "awards table (§5.4 audit trail)",
        _table(
            ["Measure", "Scheme", "Amount", "Payout", "Caps binding (slots)", "Perspectives"], rows
        ),
    )


def _subsidy_section_html(matrix: EvaluationMatrix) -> str:
    """Section 5: the cumulation solver's audit trail, rendered.

    Answers "why did this measure get this much support, and what did it miss?" — which is the
    question a subsidy engine has to be able to answer to be trusted at all. Each measure gets a
    card listing what APPLIED (with the slots any cap bound in), what was REJECTED and the
    reason, and what is still OPEN because a required questionnaire field is unanswered, with
    the upper bound the open questions could still unlock. That last line is the actionable one
    for a user: it quantifies what answering the questionnaire is worth.

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
            caps = [slot for slot, bound in award.caps_binding_per_slot.items() if bound]
            cap_note = f" — cap binding in {', '.join(caps)}" if caps else ""
            lines.append(
                f"<li><span class='status PASS'>APPLIED</span> {_esc(award.scheme_id)}: "
                f"{_esc(_band_str(award.upfront_amount))}{_esc(cap_note)}</li>"
            )
        for reject in decision.rejected:
            lines.append(
                f"<li><span class='status FAIL'>REJECTED</span> {_esc(reject['scheme_id'])}: "
                f"{_esc(reject['reason'])}</li>"
            )
        for item in decision.undetermined:
            lines.append(
                f"<li><span class='status WARN'>OPEN</span> {_esc(item['scheme_id'])}: "
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
    if cards:
        caption = (
            '<p class="sub">The cumulation solver&#39;s full audit trail (§5.4): what applied, '
            "what bound, what was rejected and why.</p>"
        )
    else:
        caption = (
            '<p class="sub">No subsidy catalog is active for this country — the flat legacy shim '
            "shares from the device entries apply (cost_spec.md §10.1; an audit trail requires a "
            "catalog, see subsidy_catalog/).</p>"
        )
    return (
        "<section><h2>5 - Subsidy decisions</h2>"
        + caption
        + composition
        + "".join(cards)
        + _subsidy_awards_table(matrix)
        + "</section>"
    )


def _perspective_section_html(matrix: EvaluationMatrix) -> str:
    """Section 6: equivalent annual cost across perspectives, with bands and the result table.

    Answers "is the perspective model itself behaving?" All perspectives on one axis make the
    orderings that must hold visible without arithmetic: a gross view sits above its net
    counterpart, operating-only below brownfield, and the macroeconomic row differs from the
    financial one only by transfers and CO2 damage. A violation of any of those points at the
    engine or the perspective bundle, not at the input data — which is why this section sits
    after the ones that validate inputs.

    The table beneath carries the four headline KPIs per perspective (NPV, equivalent annual
    cost, monthly cost in year 1, levelized cost of heat), each as a band, so a reader can pick
    the unit they think in. The sunk-cost column is added only when some perspective wrote off
    residual book value, and is marked "(info)": §4.1 reports it but keeps it out of the
    decision KPIs.
    """
    rows = [
        (perspective_id, result.equivalent_annual_cost_in_euro)
        for perspective_id, result in matrix.results.items()
    ]
    any_sunk = any(result.sunk_cost_written_off_in_euro.maximum > 0 for result in matrix.results.values())
    headers = ["Perspective", "NPV", "Equivalent annual cost", "Monthly (year 1)", "LCOH"]
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
        "<section><h2>6 - Perspectives at a glance</h2>"
        '<p class="sub">Equivalent annual cost with min/avg/max whiskers. Sanity: gross &#8805; net; '
        "operating &#8804; brownfield; macroeconomic differs only by transfers + CO2 damage.</p>"
        + _whisker_svg(rows, "EUR/a")
        + _table(headers, table_rows)
        + "</section>"
    )


def _actor_section_html(matrix: EvaluationMatrix) -> str:
    """Section 6b: who pays what — payer NPVs per allocated perspective (§6.5).

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
        "<section><h2>6b - Who pays what (actor split)</h2>"
        '<p class="sub">Landlord/tenant allocation per the DE_2024 ruleset: tenant pays energy and '
        "apportionable operation plus the modernization levy; landlord pays investment minus "
        "subsidies and receives the levy. Negative = net gain.</p>" + "".join(blocks) + "</section>"
    )


def _tornado_svg(rows: List[Tuple[str, float]], base_value: float) -> str:
    """Diverging bars: per-scenario swing of the headline KPI vs. the base scenario.

    The standard sensitivity picture, drawn for section 9: each scenario's equivalent annual
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


def _scenario_section_html(scenario_cube, matrix: EvaluationMatrix) -> str:
    """Section 9: scenario analysis — tornado of the headline KPI plus the full table (§4.6).

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
    table_rows = "".join(
        f"<tr><td>{_esc(scenario_id)}</td>"
        f"<td>{_esc(_band_str(result.total_npv_in_euro))}</td>"
        f"<td>{_esc(_band_str(result.equivalent_annual_cost_in_euro, 'EUR/a'))}</td>"
        f"<td>{swings[scenario_id]:+,.0f}</td></tr>"
        for scenario_id, result in per_scenario.items()
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
        f"<section><h2>9 - Scenario analysis ({_esc(perspective_id)})</h2>"
        '<p class="sub">Equivalent annual cost swing per economic scenario vs. the base assumptions '
        "(red = more expensive, aqua = cheaper). Scenario axes vary rates and datapoints; the "
        "min/avg/max bands vary the cost data within each scenario — two orthogonal uncertainty "
        "mechanisms (§4.6). Full cube: scenario_cube.csv / scenario_cube.json.</p>"
        + _tornado_svg(rows, base_value)
        + "<details open><summary>all scenarios</summary><table>"
        "<tr><th>Scenario</th><th>NPV</th><th>Equivalent annual cost</th><th>Swing [EUR/a]</th></tr>"
        + table_rows + "</table></details>" + robustness + "</section>"
    )


def _kpi_section_html(matrix: EvaluationMatrix) -> str:
    """Section 10: the namespaced lifecycle KPI set (§7.3) as a table with bands.

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
        "<section><h2>10 - Lifecycle KPIs</h2>"
        '<p class="sub">The namespaced KPI set (§7.3) as published to lifecycle_kpis.json; '
        "`value` is the AVERAGE slot, the band column is min | max.</p>"
        + _table(["KPI", "Value", "Band (min | max)", "Unit"], rows)
        + "</section>"
    )


def _components_section_html(matrix: EvaluationMatrix) -> str:
    """Section 7: per-subject stacked NPV bars per perspective (§7.4).

    Answers "which component actually drives the result, and does the sum of the parts equal the
    whole?" The diverging stacks put each subject's cost blocks right of zero and its credits
    (residual value, subsidies, feed-in, anyway credit) left, with a marker at the net NPV band,
    so `net = costs - credits` is geometry rather than a claim. The §7.4 reconciliation — the
    subject nets summing to the headline — is checked automatically in section 0; this is where
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
        "<section><h2>7 - Per-component breakdown</h2>"
        '<p class="sub">Diverging stacks per subject: costs right of the zero line, credits '
        "(residual value, subsidies, feed-in, anyway credit) left — never added onto the cost "
        "side. The whisker + dot mark the net NPV band; net = costs - credits reconciles with "
        "the headline by construction (§7.4).</p>"
        + "".join(blocks) + "</section>"
    )


def _checks_section_html(plausibility: PlausibilityReport) -> str:
    """Section 0: the plausibility panel.

    Answers, before anything else is read, "is there a reason not to trust the rest of this
    report?" It is section 0 because a reviewer's time is better spent on the automated verdict
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
        f"<section><h2>0 - Plausibility panel — {headline}</h2>"
        '<p class="sub">Automated ratio and invariant checks (thresholds: '
        "cost_database/plausibility_checks.json). WARN = outside the generous range, FAIL = structural.</p>"
        f"<table><tr><th></th><th>Check</th><th>Value</th><th>Expected</th><th>Note</th></tr>{rows}</table></section>"
    )


def _comparison_section_html(comparison: VariantComparison) -> str:
    """Section 8/D: delta waterfall by subject + discounted payback curve.

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
        f"<section><h2>8 - Variant comparison ({_esc(comparison.perspective_id)})</h2>"
        '<p class="sub">NPV delta by subject (variant - reference; red adds cost, aqua saves) and the '
        "cumulative discounted savings whose zero-crossing is the payback.</p>"
        + _waterfall_svg(steps, "Net NPV delta", comparison.npv_delta_in_euro.average)
        + _details("delta table (best-case | expected | worst-case, §3.9 envelope)",
                   _table(["Subject", "NPV delta"], delta_rows))
        + f"<p class='sub'>Discounted payback: {_esc(payback_text)}</p>"
        + _payback_svg(comparison)
        + warm_rent
        + "</section>"
    )


def build_lifecycle_report_html(
    matrix: EvaluationMatrix,
    plausibility: PlausibilityReport,
    audit: Optional[InputAuditReport] = None,
    comparison: Optional[VariantComparison] = None,
    scenario_cube=None,
) -> str:
    """The self-contained HTML report, sections along the calculation chain.

    The module's main entry point and the assembly of everything above: a header stating the
    run's parameters, the twelve-odd `_*_section_html` blocks in the fixed order 0, 1, 2, 3, 4,
    4b, 5, 6, 6b, 7, 9, 10 (plus 8 when comparing), and a footer telling the reader how to trace
    any figure back to its sources with `python -m hisim.economics explain`. The order is the
    calculation chain, not a menu: each section is placed where a mistake made upstream of it
    first becomes visible.

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
        plausibility: The panel rendered as section 0.
        audit: Optional resolved-input audit; section 1 is omitted without it.
        comparison: Optional variant-vs-reference comparison; appends section 8.
        scenario_cube: Optional `ScenarioCube` (untyped by the seam-4 import rule); adds
            section 9.

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
    sections = [
        _checks_section_html(plausibility),
        _audit_section_html(audit) if audit is not None else "",
        _investment_section_html(reference),
        _timeline_section_html(matrix),
        _energy_section_html(reference),
        _co2_section_html(matrix),
        _subsidy_section_html(matrix),
        _perspective_section_html(matrix),
        _actor_section_html(matrix),
        _components_section_html(matrix),
        _scenario_section_html(scenario_cube, matrix),
        _kpi_section_html(matrix),
    ]
    if comparison is not None:
        sections.append(_comparison_section_html(comparison))
    footer = (
        "<footer>Every number is traceable: "
        "<code>python -m hisim.economics explain &lt;results_dir&gt; --value "
        f"\"{_esc(reference.perspective_id)}/total_npv_in_euro\"</code> — hisim.economics</footer>"
    )
    return (
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>Lifecycle cost report</title><style>{_ReportCss.CSS}</style></head>"
        f"<body><main>{header}{''.join(sections)}{footer}</main></body></html>"
    )


def write_lifecycle_report(
    matrix: EvaluationMatrix,
    plausibility: PlausibilityReport,
    result_directory: str,
    audit: Optional[InputAuditReport] = None,
    comparison: Optional[VariantComparison] = None,
    file_name: str = ReportFileNames.LIFECYCLE_REPORT_FILE_NAME,
    scenario_cube=None,
) -> str:
    """Writes the HTML report.

    The filesystem counterpart of `build_lifecycle_report_html`, kept separate for the same
    reason as the markdown pair: the golden oracle and the unit tests render without touching a
    directory, while `bridge.py` and the `report` CLI get one call. `file_name` is a parameter
    rather than a constant so a second report can be written beside the first under
    `ReportFileNames.COMPARISON_REPORT_FILE_NAME` without overwriting it.

    Args:
        matrix: Evaluated perspectives.
        plausibility: The panel for section 0.
        result_directory: Directory to write into (the run's `results/`).
        audit: Optional input audit for section 1.
        comparison: Optional variant comparison for section 8.
        file_name: Output file name; defaults to `lifecycle_report.html`.
        scenario_cube: Optional scenario cube for section 9.

    Returns:
        The path written.
    """
    path = os.path.join(result_directory, file_name)
    with open(path, "w", encoding="utf-8") as file:
        file.write(
            build_lifecycle_report_html(matrix, plausibility, audit, comparison, scenario_cube)
        )
    return path
