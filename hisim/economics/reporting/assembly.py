"""Assembly of the HTML lifecycle report (cost_spec.md §7.2).

The remaining sections (perspectives, actors, scenarios, KPIs, components, checks,
variant comparison) and the two entry points `build_lifecycle_report_html` and
`write_lifecycle_report` that stitch every section into the final self-contained
document, in the order `scaffold.ReportSections.ORDER` declares. Split out of the former
single-module `reporting.py` (PR-3 review); the package `__init__` re-exports everything.
"""


from __future__ import annotations

import datetime
import os
from typing import Dict, List, Optional, Tuple

from hisim.economics import views
from hisim.economics.input_audit import InputAuditReport
from hisim.economics.plausibility import PlausibilityReport
from hisim.economics.presentation_style import PresentationStyle, group_name, group_of
from hisim.economics.results import EvaluationMatrix, LifecycleCostResult, VariantComparison
from hisim.economics.timeline import Actor
from hisim.economics.uncertainty import UncertainValue


from hisim.economics.reporting.summary import (
    ReportFileNames,
    _band_str,
    _degenerate_note,
    _fmt,
    _reference_result,
    all_bands_degenerate,
    render_plausibility_findings,
)
from hisim.economics.reporting.charts import (
    _CentredAxisFrame,
    _CentredRow,
    _ChartGeometry,
    _centred_axis_svg,
    _details,
    _esc,
    _legend_html,
    _payback_svg,
    _stacked_subject_svg,
    _table,
    _waterfall_svg,
    _whisker_svg,
)
from hisim.economics.reporting.scaffold import (
    ReportChapters,
    ReportSections,
    _ChapterContext,
    _chapter_open,
    _explanation_html,
    _not_drawn_html,
    _section_open,
    _table_of_contents_html,
)
from hisim.economics.reporting.sections import (
    _ReportCss,
    _audit_section_html,
    _co2_section_html,
    _energy_section_html,
    _how_to_read_section_html,
    _investment_section_html,
    _subsidy_section_html,
    _timeline_section_html,
)
from hisim.economics.reporting.sections_charts import (
    _actor_flow_section_html,
    _comparison_bridge_section_html,
    _component_events_section_html,
    _cost_of_credit_section_html,
    _energy_balance_section_html,
    _equity_section_html,
    _first_result_where,
    _has_year_zero_funding,
    _landlord_statement_section_html,
    _lifecycle_overview_section_html,
    _liquidity_section_html,
    _loan_section_html,
    _monthly_burden_section_html,
    _sources_uses_section_html,
    _subject_flows_section_html,
    _treemap_section_html,
    _uncertainty_section_html,
    _wealth_benchmark_section_html,
)


def _perspective_section_html(matrix: EvaluationMatrix, context: _ChapterContext) -> str:
    """The perspectives section: equivalent annual cost across them, with bands and the table.

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

    Args:
        matrix: Every evaluated perspective; one whisker row and one table row each.
        context: The chapter this section is being rendered into.

    Returns:
        The section, always non-empty for a matrix with at least one perspective.
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

    Args:
        matrix: Every evaluated perspective; the unallocated ones are skipped.
        context: The chapter this section is being rendered into.

    Returns:
        The section, or the empty string when no perspective in the matrix is allocated.
    """
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
            _table(["Payer"] + [group_name(index) for index in group_indices], table_rows),
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

    The standard sensitivity picture: each scenario's equivalent annual cost minus the base
    scenario's, sorted by absolute magnitude so the assumptions the result is most sensitive to
    come first. Colour carries the direction (red for more expensive, aqua for cheaper) rather
    than the identity of the scenario, because the reader's question here is "which way and how
    far", not "which series is which".

    Geometry: a centred zero axis with the plot half-width scaled to the largest absolute swing,
    so the widest bar always fills its side; the base value is printed under the axis so the
    swings can be read as absolutes. The axis, the row walk and the footnote are
    `charts._centred_axis_svg`'s, shared with the uncertainty tornado, which is the same drawing
    with different numbers in it. Sorting happens here and only affects display — the swings
    themselves come from `ScenarioCube.equivalent_annual_cost_swings`.
    """
    if not rows:
        return ""
    row_h, left = 28, 250
    height = len(rows) * row_h + 26
    span = max(max(abs(swing) for _label, swing in rows), 1e-9)
    half_width = (_ChartGeometry.WIDTH - left - 120) / 2.0
    center = left + half_width
    scale = half_width / span
    drawn: List[_CentredRow] = []
    for label, swing in sorted(rows, key=lambda item: -abs(item[1])):
        color = "var(--g5)" if swing > 0 else "var(--g1)"
        x_from = center if swing >= 0 else center + swing * scale
        anchor_x = center + swing * scale + (6 if swing >= 0 else -6)
        drawn.append((
            label,
            [(x_from, abs(swing) * scale, color,
              f"{label}: {'+' if swing >= 0 else ''}{_fmt(swing)} EUR/a vs base", 3)],
            (anchor_x, f"{'+' if swing >= 0 else ''}{_fmt(swing)}", "start" if swing >= 0 else "end"),
        ))
    return _centred_axis_svg(
        drawn,
        _CentredAxisFrame(left=left, center=center, row_h=row_h, first_y=4.0, inset=5, text_size=10),
        height,
        f"base: {_fmt(base_value)} EUR/a",
    )


def _scenario_section_html(scenario_cube, matrix: EvaluationMatrix, context: _ChapterContext) -> str:
    """The scenarios section: a tornado of the headline KPI plus the full table (§4.6).

    Answers "how much of the conclusion survives the assumptions?" The tornado ranks the
    scenarios by how far they move the headline KPI, the all-scenarios table gives NPV, EAC and
    swing for each, and the robustness summary reports min/max/spread per perspective — the
    figure that says whether a ranking between two options holds across the whole scenario set
    or only under the base assumptions.

    The authored prose states the distinction reviewers most often miss: scenario axes and
    uncertainty bands are two *orthogonal* mechanisms (§4.6). A scenario varies rates and
    datapoints deliberately; the min/best_estimate/max band varies the cost data within each
    scenario. They must not be read as one interval, and the report never combines them.

    `scenario_cube` is taken untyped on purpose — presentation may render a cube but may not
    import the module that builds one (the seam-4 import rule), so it is duck-typed for
    `results`, `base_id`, `equivalent_annual_cost_swings` and `equivalent_annual_cost_spreads`.

    Args:
        scenario_cube: The evaluated cube, or None when the run computed none.
        matrix: Every evaluated perspective; the first is the one the tornado is drawn for.
        context: The chapter this section is being rendered into.

    Returns:
        The section, or the empty string when no cube was computed or the cube has no base
        result for the reference perspective.
    """
    if scenario_cube is None or not scenario_cube.results:
        return ""
    perspective_id = next(iter(matrix.results.keys()))
    per_scenario = scenario_cube.results.get(perspective_id)
    if not per_scenario or scenario_cube.base_id not in per_scenario:
        return ""
    base = per_scenario[scenario_cube.base_id]
    base_value = base.equivalent_annual_cost_in_euro.best_estimate
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
        "robustness summary across scenarios (EAC [EUR/a], BEST_ESTIMATE slot)",
        _table(["Perspective", "Min", "Max", "Spread"], robustness_rows),
    )
    return (
        _section_open(ReportSections.SCENARIOS, context, perspective_id)
        + _explanation_html(ReportSections.SCENARIOS, context)
        + "<p class='sub'>Full cube: scenario_cube.csv / scenario_cube.json.</p>"
        + _tornado_svg(rows, base_value)
        + "<details open><summary>all scenarios</summary><table>"
        "<tr><th>Scenario</th><th>NPV</th><th>Equivalent annual cost</th><th>Swing [EUR/a]</th></tr>"
        + table_rows + "</table></details>" + robustness + "</section>"
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
    `value` is the BEST_ESTIMATE slot and the band column is min | max, both stated in the caption
    because a KPI name alone does not say which slot it carries.

    Args:
        matrix: Every evaluated perspective; the KPI set is built from all of them.
        context: The chapter this section is being rendered into.

    Returns:
        The section, or the empty string when the matrix publishes no KPI entry.
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
        + "<p class='sub'>Published to lifecycle_kpis.json; <code>value</code> is the "
          "BEST_ESTIMATE slot, the band column is min | max.</p>"
        + _table(["KPI", "Value", "Band (min | max)", "Unit"], rows)
        + "</section>"
    )


def _components_section_html(matrix: EvaluationMatrix, context: _ChapterContext) -> str:
    """The component breakdown: per-subject stacked NPV bars per perspective (§7.4).

    Answers "which component actually drives the result, and does the sum of the parts equal the
    whole?" The diverging stacks put each subject's cost blocks right of zero and its credits
    (residual value, subsidies, feed-in, anyway credit) left, with a marker at the net NPV band,
    so `net = costs - credits` is geometry rather than a claim. The §7.4 reconciliation — the
    subject nets summing to the headline — is checked automatically in the plausibility panel;
    this is where a reader sees *why* it holds or which subject is responsible when it does not.

    One collapsible block per perspective, the first open, each with a legend restricted to the
    groups that perspective's breakdowns contain and a table repeating the same subjects with
    NPV, equivalent annual cost, year-0 investment, support and lifecycle CO2. Keeping the
    credits unnetted is deliberate: an expensive component with an equally large subsidy looks
    nothing like a cheap one, and a netted bar would hide the difference.

    Args:
        matrix: Every evaluated perspective; one block each.
        context: The chapter this section is being rendered into.

    Returns:
        The section, always non-empty for a matrix with at least one perspective.
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
    report?" It opens the analysis because a reviewer's time is better spent on the automated
    verdict than on rediscovering a unit mix-up by inspection, and the heading carries the count
    of flagged checks so that verdict is visible without scrolling. WARN means a magnitude left a
    deliberately generous range (usually a unit or a rate stored as an absolute); FAIL means a
    structural invariant is broken and the numbers below contradict each other.

    Rendering only — the checks themselves, their thresholds and their order are decided in
    `plausibility.py` from `cost_database/plausibility_checks.json`, and the same rows are
    re-used for the markdown table and for `bridge.py`'s log warnings (the bridge arrives with
    stack part 8/8). The reader hint in the Note column is the one part that lives on this side,
    since "what usually causes this" is editorial rather than computed.

    The status is written into a CSS class as well as into the cell, so it is escaped on both
    paths even though `PlausibilityCheck` now refuses anything but PASS/WARN/FAIL: an unescaped
    value interpolated into a quoted attribute is a markup injection waiting for the day the
    constraint is relaxed, and escaping the three legal spellings costs nothing.

    Args:
        plausibility: The report whose findings are rendered as the panel's rows.
        context: The chapter this section is being rendered into.

    Returns:
        The section, always non-empty; the verdict is in the heading.
    """
    checks = render_plausibility_findings(plausibility)
    rows = "".join(
        f"<tr><td><span class='status {_esc(check.status)}'>{_esc(check.status)}</span></td>"
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

    The NPV bridge beside it answers the same question decomposed by *cost group* rather than by
    subject, which is why the two are separate sections rather than two charts in one.

    Args:
        comparison: The variant-vs-reference comparison to state.
        context: The chapter this section is being rendered into.

    Returns:
        The section, always non-empty for a comparison that was computed at all.
    """
    steps: List[Tuple[str, float, str]] = []
    for subject, delta in sorted(comparison.npv_delta_by_subject.items(), key=lambda item: item[1].best_estimate):
        if abs(delta.best_estimate) < 0.005:
            continue
        color = "var(--g5)" if delta.best_estimate > 0 else "var(--g1)"
        steps.append((subject, delta.best_estimate, color))
    payback = comparison.discounted_payback_years
    payback_text = (
        f"best case {payback.get('low')} a, expected {payback.get('best_estimate')} a, "
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
        for subject, delta in sorted(comparison.npv_delta_by_subject.items(), key=lambda item: item[1].best_estimate)
    ]
    return (
        _section_open(ReportSections.COMPARISON, context, comparison.perspective_id)
        + _explanation_html(ReportSections.COMPARISON, context)
        + _waterfall_svg(steps, "Net NPV delta", comparison.npv_delta_in_euro.best_estimate)
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
    reference_result: Optional[LifecycleCostResult] = None,
) -> str:
    """The self-contained HTML report, sections along the calculation chain.

    The module's main entry point and the assembly of everything above: a header stating the
    run's parameters, a table of contents, every `_*_section_html` block in the order
    `ReportSections.ORDER` declares (the comparison pair only when comparing), and a footer
    telling the reader how to trace any figure back to its sources with
    `python -m hisim.economics explain`. The order is the calculation chain, not a menu: each
    section is placed where a mistake made upstream of it first becomes visible, which is why the
    contents are a navigation aid rather than the structure itself.

    The whole document is rendered as one chapter, `ReportChapters.THE_BUILDING`: splitting the
    story chapters apart needs the per-party statement sections, which land with the second half
    of the chart set, so until then every section carries the `building-` anchor prefix and the
    contents have a single top-level entry.

    The returned document is a **single file with no external references** — stylesheet inlined
    from `_ReportCss`, charts as inline SVG, tooltips native, no script and no font, image or
    CDN request — so it survives being mailed, archived beside the results or opened offline.
    Sections that have nothing to show return the empty string and vanish rather than rendering
    an empty box, which is why the list is concatenated blindly; each of them says so under the
    table of contents, so a gap in the page is never left to the reader to interpret. When every band is degenerate
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
        scenario_cube: Optional `ScenarioCube` (untyped by the seam-4 import rule); adds the
            scenarios section.
        reference_result: The comparison's baseline result. Needed by the two sections that
            decompose a comparison rather than restating it — the NPV bridge, which splits it by
            cost group, and the bank benchmark, which needs the per-year differential flows —
            because a `VariantComparison` publishes neither. Without it both are omitted and the
            omission is named under the contents like every other.

    Returns:
        The complete HTML document as one string.

    Raises:
        ValueError: If the matrix holds no evaluated perspective (see `_reference_result`).
    """
    reference = _reference_result(matrix)
    params = reference.parameters
    header = (
        f"<h1>Lifecycle cost report</h1><p class='sub'>Simulation year {reference.simulation_year}, "
        f"country {_esc(params.country)}, horizon {params.observation_period_in_years} a, "
        f"interest {params.interest_rate:.1%}, price basis {params.price_basis_year}, "
        f"CO2 scenario &#39;{_esc(params.co2_price_scenario)}&#39;. All money as best_estimate [min | max] "
        f"envelope bands (§3.9). Generated {datetime.date.today().isoformat()}.</p>"
    )
    if all_bands_degenerate(matrix):
        header += (
            "<section style='border-left:4px solid var(--warning)'><b>No uncertainty bands in "
            f"this run.</b> <span class='sub'>{_esc(_degenerate_note(matrix))}</span></section>"
        )
    context = _ChapterContext(chapter=ReportChapters.THE_BUILDING)
    document = _chapter_open(ReportChapters.THE_BUILDING) + "".join(
        _document_sections(matrix, plausibility, audit, comparison, scenario_cube,
                           reference_result, context)
    )
    footer = (
        "<footer>Every number is traceable: "
        "<code>python -m hisim.economics explain &lt;results_dir&gt; --value "
        f"\"{_esc(reference.perspective_id)}/total_npv_in_euro\"</code> — hisim.economics</footer>"
    )
    return (
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>Lifecycle cost report</title><style>{_ReportCss.CSS}</style></head>"
        f"<body><main>{header}{_table_of_contents_html(document)}"
        f"{_not_drawn_html(context.skipped)}{document}{footer}</main></body></html>"
    )


def _document_sections(
    matrix: EvaluationMatrix,
    plausibility: PlausibilityReport,
    audit: Optional[InputAuditReport],
    comparison: Optional[VariantComparison],
    scenario_cube,
    reference_result: Optional[LifecycleCostResult],
    context: _ChapterContext,
) -> List[str]:
    """Every section of the document, in `ReportSections.ORDER`.

    Split out of `build_lifecycle_report_html` because the document has to be *rendered* before
    its table of contents can be built — the contents list the sections that actually appeared,
    not the ones that might have — so the assembly and the shell around it are two steps rather
    than one expression.

    Most sections take the matrix's first perspective, which is the reference view of the run.
    Five cannot and use `_first_result_where` instead: the equity build-up needs a financed
    perspective, the who-pays-whom Sankey one with more than one payer, the funding statement one
    whose year 0 carries support or a loan, the energy balance one that carries device flows, and
    the landlord statement a landlord-scoped one. Each of them names the perspective it is showing
    in its own heading, and records a reason on the context when the run has none. The loan and
    cost-of-credit sections take the whole matrix, because they draw a block per financed
    perspective rather than picking one.

    The cash curve is the sixth: with a comparison it draws the perspective the comparison was
    computed *for*, not the matrix's first, because the payback sentence under it comes from that
    comparison and a payback drawn under somebody else's cash position is simply a wrong chart.
    The section refuses the mix rather than trusting this call site (§seam 4 is about numbers;
    this is the same argument about which result a number belongs to).

    Args:
        matrix: Evaluated perspectives.
        plausibility: The panel for the plausibility section.
        audit: Optional input audit.
        comparison: Optional variant comparison.
        scenario_cube: Optional scenario cube.
        reference_result: Optional baseline result for the NPV bridge.
        context: The chapter every section is rendered into; **mutated** as the explanations are
            recorded.

    Returns:
        The sections in page order; the empty ones are kept and concatenate to nothing.
    """
    reference = _reference_result(matrix)
    financed = _first_result_where(
        matrix, lambda result: views.loan_amortization_series(result).has_flows()
    ) or reference
    multi_actor = _first_result_where(
        matrix, lambda result: len({entry.payer for entry in result.timeline.entries}) > 1
    ) or reference
    funded = _first_result_where(matrix, _has_year_zero_funding) or reference
    attributed = _first_result_where(matrix, views.has_energy_balance) or reference
    landlord = _first_result_where(matrix, lambda result: result.scope_payer == Actor.LANDLORD)
    # The comparison names its own perspective; drawing the cash curve on any other one would
    # put its payback sentence under a stranger's curve.
    liquidity = matrix.results.get(comparison.perspective_id, reference) if comparison else reference
    sections = [
        _how_to_read_section_html(context),
        _lifecycle_overview_section_html(reference, comparison, context),
        _checks_section_html(plausibility, context),
        _audit_section_html(audit, context) if audit is not None else "",
        _investment_section_html(reference, context),
        _sources_uses_section_html(funded, context),
        _component_events_section_html(reference, context),
        _timeline_section_html(matrix, context),
        _liquidity_section_html(liquidity, comparison, context),
        _loan_section_html(matrix, context),
        _cost_of_credit_section_html(matrix, context),
        _energy_section_html(reference, context),
        _energy_balance_section_html(attributed, context),
        _co2_section_html(matrix, context),
        _subsidy_section_html(matrix, context),
        _perspective_section_html(matrix, context),
        _landlord_statement_section_html(landlord, context) if landlord is not None
        else context.skip(
            ReportSections.LANDLORD_STATEMENT,
            "No perspective of this run is scoped to the landlord, so there is no landlord "
            "business case to state.",
        ),
        _actor_section_html(matrix, context),
        _actor_flow_section_html(multi_actor, context),
        _uncertainty_section_html(reference, context),
        _components_section_html(matrix, context),
        _treemap_section_html(reference, context),
        _subject_flows_section_html(reference, context),
        _equity_section_html(financed, context),
        _scenario_section_html(scenario_cube, matrix, context),
        _monthly_burden_section_html(reference, context),
        _kpi_section_html(matrix, context),
    ]
    if comparison is not None:
        sections.append(_comparison_section_html(comparison, context))
        variant = matrix.results.get(comparison.perspective_id, reference)
        if reference_result is not None:
            sections.append(
                _comparison_bridge_section_html(reference_result, variant, comparison, context)
            )
            sections.append(_wealth_benchmark_section_html(reference_result, variant, context))
        else:
            sections.append(context.skip(
                ReportSections.NPV_BRIDGE,
                "The report was built with a comparison but without the reference result the "
                "bridge decomposes, so there is nothing to split by cost group.",
            ))
            sections.append(context.skip(
                ReportSections.BANK_BENCHMARK,
                "The report was built with a comparison but without the reference result the "
                "fixed-interest benchmark needs, so there are no per-year differential flows to "
                "compare against a savings account.",
            ))
    return sections


def write_lifecycle_report(
    matrix: EvaluationMatrix,
    plausibility: PlausibilityReport,
    result_directory: str,
    audit: Optional[InputAuditReport] = None,
    comparison: Optional[VariantComparison] = None,
    scenario_cube=None,
    reference_result: Optional[LifecycleCostResult] = None,
) -> str:
    """Writes the HTML report as `ReportFileNames.LIFECYCLE_REPORT_FILE_NAME`.

    The filesystem counterpart of `build_lifecycle_report_html`, kept separate for the same
    reason as the markdown pair: the golden oracle and the unit tests render without touching a
    directory, while the `report` CLI — and `bridge.py`, from stack part 8/8 — gets one call. The
    name is fixed rather than
    a parameter — a comparison is a *section* of this report, not a second document, so there was
    never a second name for a caller to pass.

    Args:
        matrix: Evaluated perspectives.
        plausibility: The panel for the plausibility section.
        result_directory: Directory to write into (the run's `results/`).
        audit: Optional input audit for the input-audit section.
        comparison: Optional variant comparison for the comparison section.
        scenario_cube: Optional scenario cube for the scenarios section.
        reference_result: The comparison's baseline, for the NPV bridge and the bank benchmark
            that decompose it; see `build_lifecycle_report_html`.

    Returns:
        The path written.
    """
    path = os.path.join(result_directory, ReportFileNames.LIFECYCLE_REPORT_FILE_NAME)
    with open(path, "w", encoding="utf-8") as file:
        file.write(
            build_lifecycle_report_html(
                matrix, plausibility, audit, comparison, scenario_cube, reference_result
            )
        )
    return path
