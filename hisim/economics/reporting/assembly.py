"""Assembly of the HTML lifecycle report (cost_spec.md §7.2).

The remaining sections (perspectives, actors, scenarios, KPIs, components, checks,
variant comparison) and the two entry points `build_lifecycle_report_html` and
`write_lifecycle_report` that stitch every section into the final self-contained
document. Split out of the former single-module `reporting.py` (PR-3 review); the
package `__init__` re-exports everything.
"""


from __future__ import annotations

import datetime
import os
from typing import Dict, List, Optional, Tuple

from hisim.economics import views
from hisim.economics.input_audit import InputAuditReport
from hisim.economics.plausibility import PlausibilityReport
from hisim.economics.presentation_style import PresentationStyle, group_of
from hisim.economics.results import EvaluationMatrix, VariantComparison
from hisim.economics.uncertainty import UncertainValue



from hisim.economics.reporting.summary import (
    ReportFileNames,
    _band_str,
    _degenerate_note,
    _fmt,
    all_bands_degenerate,
    render_plausibility_findings,
)
from hisim.economics.reporting.charts import (
    _co2_section_html,
    _details,
    _esc,
    _legend_html,
    _payback_svg,
    _rect,
    _stacked_subject_svg,
    _svg_open,
    _table,
    _text,
    _waterfall_svg,
    _whisker_svg,
)
from hisim.economics.reporting.sections import (
    _ReportCss,
    _audit_section_html,
    _energy_section_html,
    _investment_section_html,
    _subsidy_section_html,
    _timeline_section_html,
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
        '<p class="sub">Equivalent annual cost with min/best_estimate/max whiskers. Sanity: gross &#8805; net; '
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
    deliberately; the min/best_estimate/max band varies the cost data within each scenario. They must not
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
        f"<section><h2>9 - Scenario analysis ({_esc(perspective_id)})</h2>"
        '<p class="sub">Equivalent annual cost swing per economic scenario vs. the base assumptions '
        "(red = more expensive, aqua = cheaper). Scenario axes vary rates and datapoints; the "
        "min/best_estimate/max bands vary the cost data within each scenario — two orthogonal uncertainty "
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
    `value` is the BEST_ESTIMATE slot and the band column is min | max, both stated in the caption
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
        "`value` is the BEST_ESTIMATE slot, the band column is min | max.</p>"
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
        f"<section><h2>8 - Variant comparison ({_esc(comparison.perspective_id)})</h2>"
        '<p class="sub">NPV delta by subject (variant - reference; red adds cost, aqua saves) and the '
        "cumulative discounted savings whose zero-crossing is the payback.</p>"
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
        f"CO2 scenario &#39;{_esc(params.co2_price_scenario)}&#39;. All money as best_estimate [min | max] "
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
