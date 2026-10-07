"""Assembly of the HTML lifecycle report (cost_spec.md §7.2).

Holds the remaining sections (perspectives, actors, scenarios, KPIs, components, checks, variant comparison), the
chapter builders and the entry points `build_lifecycle_report_html` and `write_lifecycle_report`. The report is told as
chapters: the building on the gross basis, then one chapter per story (owner-occupied, rented, society), each rendering
the perspectives `views.story_perspectives` assigns to it through a chapter-scoped `scaffold._ChapterContext`. A
chapter or section with nothing to show is skipped and named with its reason under the table of contents.
"""


from __future__ import annotations

import datetime
import os
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from hisim.economics import views
from hisim.economics.input_audit import InputAuditReport
from hisim.economics.plausibility import PlausibilityReport
from hisim.economics.presentation_style import PresentationStyle, group_name, group_of
from hisim.economics.results import (
    EvaluationMatrix,
    HeatCostNaming,
    LifecycleCostResult,
    VariantComparison,
)
from hisim.economics.timeline import Actor
from hisim.economics.uncertainty import UncertainValue


from hisim.economics.reporting.summary import (
    ReportFileNames,
    _band_str,
    _degenerate_note,
    _fmt,
    _reference_result,
    _value_by_kind,
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
    _assumptions_section_html,
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
    _owner_statement_section_html,
    _society_statement_section_html,
    _sources_uses_section_html,
    _subject_flows_section_html,
    _tenant_statement_section_html,
    _treemap_section_html,
    _uncertainty_section_html,
    _wealth_benchmark_section_html,
)


def _perspective_section_html(matrix: EvaluationMatrix, context: _ChapterContext) -> str:
    """Render the perspectives section: equivalent annual cost across perspectives, with bands, and a table.

    Putting all perspectives on one axis makes the expected orderings visible: a gross view above its net counterpart,
    operating-only below brownfield, the macroeconomic row differing from the financial one only by transfers and CO2
    damage. A violation points at the engine or the perspective bundle. The table gives NPV, equivalent annual cost,
    monthly cost in year 1 and system cost per unit of heat per perspective, as bands. A sunk-cost column marked
    "(info)" appears only when some perspective wrote off residual book value, since §4.1 keeps it out of the decision
    KPIs.

    Args:
        matrix: Every evaluated perspective; one whisker row and one table row each.
        context: The chapter this section is rendered into.

    Returns:
        The section; non-empty for a matrix with at least one perspective.
    """
    rows = [
        (perspective_id, result.equivalent_annual_cost_in_euro)
        for perspective_id, result in matrix.results.items()
    ]
    any_sunk = any(result.sunk_cost_written_off_in_euro.maximum > 0 for result in matrix.results.values())
    headers = [
        "Perspective",
        "NPV",
        "Equivalent annual cost",
        "Monthly (year 1)",
        HeatCostNaming.COLUMN,
    ]
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
    """Render who pays what: payer NPVs per allocated perspective (§6.5).

    Each allocated perspective gets payer whiskers and a payer x cost-group table under a header printing
    `views.payer_npv_total`, the sum the payer bars must add up to (the zero-sum check of §6.5). The table shows which
    cost groups landed with whom, so an operating cost booked to the wrong party shows up as a group in the wrong row.
    The unallocated SYSTEM payer is left out of the rows, and perspectives with fewer than two real payers are skipped.

    Args:
        matrix: Every evaluated perspective; the unallocated ones are skipped.
        context: The chapter this section is rendered into.

    Returns:
        The section, or the empty string when no perspective is allocated (e.g. a plain owner-occupier run).
    """
    blocks = []
    for perspective_id, result in matrix.results.items():
        payers = {payer: band for payer, band in result.npv_by_payer.items() if payer != Actor.SYSTEM}
        # One payer is not a split, whether or not a SYSTEM residue happens to sit beside it: the
        # section's whole subject is who carries which share, and a single whisker over a
        # "payer NPVs sum to the system NPV" header states a zero-sum invariant about one number.
        if len(payers) < 2:
            continue
        rows = [(payer.value, band) for payer, band in payers.items()]
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
    """Draw diverging bars of each scenario's swing in the headline KPI against the base scenario.

    Each bar is a scenario's equivalent annual cost minus the base scenario's, sorted by absolute size so the most
    sensitive assumptions come first. Colour shows the direction (red more expensive, aqua cheaper). The zero axis is
    centred and scaled to the largest swing, and the base value is printed under it. The axis and footnote come from
    `charts._centred_axis_svg`, shared with the uncertainty tornado; the swings come from
    `ScenarioCube.equivalent_annual_cost_swings`.
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


class SwingFormat:
    """How a scenario's swing is printed, so a small swing does not read as no swing.

    At or above `WHOLE_EURO_FROM` whole euro are printed; below it, three significant digits, so a 0.42 EUR/a effect
    does not print as `+0`. Exactly zero prints as `+0`, the only value the zero-swing footnote is attached to.
    """

    #: At and above this magnitude a swing prints as whole euro, below it to three significant
    #: digits. 100 EUR/a is where the third significant digit is the euro digit, so the two
    #: spellings meet without a gap in precision.
    WHOLE_EURO_FROM = 100.0


def _swing_text(value: float) -> str:
    """Format one swing cell: whole euro for a large swing, three significant digits for a small one."""
    if abs(value) >= SwingFormat.WHOLE_EURO_FROM:
        return f"{value:+,.0f}"
    return f"{value:+,.3g}"


def _assumption_cell(
    assumptions: Dict[str, Tuple[views.ScenarioAssumption, ...]], scenario_id: str
) -> str:
    """Format what one scenario row changed, with both values, as the table prints it.

    The base row says it changed nothing rather than leaving the cell empty.
    """
    changed = assumptions.get(scenario_id)
    if not changed:
        return "central case — nothing changed"
    return "; ".join(_one_assumption_text(item) for item in changed)


class ScenarioAssumptionFormat:
    """The wording of the "what the central case had" half of a scenario row.

    Every phrasing has the shape `(central case: ...)`. The label matters because a field whose central value is itself
    named `central` (such as `co2_price_scenario`) would otherwise read `(central central)`.
    """

    #: Opens the central-case half of every label; one of the phrasings below follows it.
    LABEL = "central case"
    #: A data overlay replaced shipped data outright, so there is no central *parameter* at all.
    AS_SHIPPED = "as shipped"
    #: The field is not reachable on `EconomicParameters`, or is a dict key the run never
    #: configured and never resolved a rate for. Nothing is claimed about it.
    NOT_RECORDED = "not recorded"
    #: Follows a value the engine resolved from another parameter rather than read from this one.
    RESOLVED_FROM_GENERAL = "resolved from the general escalation rate"


def _one_assumption_text(item: views.ScenarioAssumption) -> str:
    """Format one changed field as `<field> <scenario value> (central case: <central value>)`.

    Digits are chosen by the same `_value_by_kind` the assumptions table uses. A band overlay is shown best estimate
    first with min and max in brackets, and a whole-dict override names its key beside the field.
    """
    name = f"{item.field_name} {item.key}" if item.key else item.field_name
    value = _value_by_kind(item.kind, item.scenario_value)
    if item.scenario_band is not None:
        low, _, high = item.scenario_band
        value = f"{value} [{_value_by_kind(item.kind, low)} | {_value_by_kind(item.kind, high)}]"
    central = _central_value_text(item)
    return f"{name} {value} ({ScenarioAssumptionFormat.LABEL}: {central})"


def _central_value_text(item: views.ScenarioAssumption) -> str:
    """Return what the central case had, or why it has nothing to state."""
    if item.central_case is views.CentralCase.AS_SHIPPED:
        return ScenarioAssumptionFormat.AS_SHIPPED
    if item.central_case is views.CentralCase.NOT_RECORDED:
        return ScenarioAssumptionFormat.NOT_RECORDED
    text = _value_by_kind(item.kind, item.central_value)
    if item.central_case is views.CentralCase.RESOLVED_FROM_GENERAL:
        return f"{text}, {ScenarioAssumptionFormat.RESOLVED_FROM_GENERAL}"
    return text


def _scenario_section_html(
    scenario_cube: Optional[views.ScenarioCubeView],
    matrix: EvaluationMatrix,
    context: _ChapterContext,
) -> str:
    """Render the scenarios section: a tornado of the headline KPI and the full scenario table (§4.6).

    The tornado ranks scenarios by how far they move the headline KPI; the table gives NPV, equivalent annual cost and
    swing per scenario; the robustness summary gives min, max and spread per perspective. Scenario axes and uncertainty
    bands are independent mechanisms (§4.6): a scenario varies rates deliberately, the band varies cost data within
    each scenario, and the report never combines them. Every row names the assumption it changed and both values, and a
    row whose swing is exactly zero carries a footnote saying why the axis was inert; both come from `views`. The cube
    is typed as `views.ScenarioCubeView`, because presentation may not import the module that builds one.

    Args:
        scenario_cube: The evaluated cube, or None when the run computed none.
        matrix: Every evaluated perspective; the tornado is drawn for the first.
        context: The chapter this section is rendered into.

    Returns:
        The section, or the empty string when there is no cube or it has no base result for the reference perspective.
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
    # What each scenario changed, with both values, read from the cube's own
    # expanded definitions, never from a table of names in this file.
    assumptions = views.scenario_assumptions(scenario_cube, base)
    # A row that did not move at all states why, from the base cell's own timeline.
    zero_swing = views.zero_swing_notes(scenario_cube, base, swings)
    markers = {scenario_id: index for index, scenario_id in enumerate(zero_swing, start=1)}
    table_rows = "".join(
        f"<tr><td>{_esc(scenario_id)}</td>"
        f"<td>{_esc(_assumption_cell(assumptions, scenario_id))}</td>"
        f"<td>{_esc(_band_str(result.total_npv_in_euro))}</td>"
        f"<td>{_esc(_band_str(result.equivalent_annual_cost_in_euro, 'EUR/a'))}</td>"
        f"<td>{_swing_text(swings[scenario_id])}"
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
        "robustness summary across scenarios (EAC [EUR/a], BEST_ESTIMATE slot)",
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
    """Render the KPIs section: the published lifecycle KPI set (§7.3) as a table with bands.

    Shows what downstream consumers see, so it is the output contract rather than a step in the derivation. The entries
    come from `exports.build_lifecycle_kpi_entries`, the function that writes `lifecycle_kpis.json`, so table and file
    agree. `value` is the BEST_ESTIMATE slot and the band column is min | max.

    Args:
        matrix: Every evaluated perspective; the KPI set is built from all of them.
        context: The chapter this section is rendered into.

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
        + _levelized_heat_cost_caption(matrix)
        + _table(["KPI", "Value", "Band (min | max)", "Unit"], rows)
        + "</section>"
    )


def _levelized_heat_cost_caption(matrix: EvaluationMatrix) -> str:
    """Write out the heat-cost figure as its division, including what the numerator covers.

    The system cost per unit of heat divides the perspective's entire NPV (every booked subject, PV and battery
    included) by the annual heat demand, so it is not the heating-related share readers tend to assume. The caption
    leads with the computed form, equivalent annual cost over annual heat demand, and gives the discounted-sum form of
    the LCOH literature as the equivalent second reading. Each perspective that publishes the figure divides a
    different NPV, so each gets its own line; the caption collapses to one sentence only when all divisions are the
    same.

    Args:
        matrix: Every evaluated perspective; each that publishes the figure is explained.

    Returns:
        The caption, or the empty string when no perspective publishes the figure (no heat demand declared).
    """
    derivations = views.levelized_heat_cost_derivations(matrix)
    if not derivations:
        return ""
    lead = f"<p class='sub'><b>{HeatCostNaming.FULL}, in full.</b> "
    if len({derivation.stated_figures() for derivation in derivations.values()}) == 1:
        return lead + _derivation_sentence(next(iter(derivations.values()))) + "</p>"
    lines = "".join(
        f"<br><b>{_esc(derivation.perspective_id)}</b> — {_derivation_division(derivation)}"
        f"{_subjects_counted_clause(derivation)}"
        for derivation in derivations.values()
    )
    return (
        lead
        + "Each perspective divides its own <i>whole</i> NPV, annualized with the annuity factor, "
        "by the heat it delivered; no heating-only attribution is applied, so every subject a "
        f"perspective books counts:{lines}</p>"
    )


def _derivation_sentence(derivation: views.LevelizedHeatCostDerivation) -> str:
    """Write one perspective's division as a full sentence, used when there is only one.

    The annual form first, then the equivalent discounted-sum form, then what the numerator covers.
    """
    inferred = _inferred_demand_note(derivation)
    return (
        f"{_fmt(derivation.equivalent_annual_cost_in_euro)} EUR/a &divide; "
        f"{derivation.annual_heat_demand_in_kwh:,.0f} kWh/a = "
        f"<b>{derivation.levelized_cost_in_euro_per_kwh:.4f} EUR/kWh</b> — equivalently NPV "
        f"&divide; discounted heat sum ({_fmt(derivation.numerator_npv_in_euro)} EUR &divide; "
        f"{derivation.discounted_heat_in_kwh:,.0f} kWh). The numerator is the <i>whole</i> NPV of "
        f"perspective {_esc(derivation.perspective_id)}, {_fmt(derivation.numerator_npv_in_euro)} "
        f"EUR, annualized with the annuity factor {derivation.annuity_factor:.6f} to "
        f"{_fmt(derivation.equivalent_annual_cost_in_euro)} EUR/a. No heating-only attribution is "
        f"applied: every subject the perspective books counts"
        f"{_namely_clause(derivation)}{inferred}"
    )


def _derivation_division(derivation: views.LevelizedHeatCostDerivation) -> str:
    """Write one perspective's division as a list line: both forms and nothing else.

    What the numerator covers is stated once above the list.
    """
    return (
        f"{_fmt(derivation.equivalent_annual_cost_in_euro)} EUR/a &divide; "
        f"{derivation.annual_heat_demand_in_kwh:,.0f} kWh/a = "
        f"<b>{derivation.levelized_cost_in_euro_per_kwh:.4f} EUR/kWh</b> (NPV "
        f"{_fmt(derivation.numerator_npv_in_euro)} EUR &divide; discounted heat sum "
        f"{derivation.discounted_heat_in_kwh:,.0f} kWh, annuity factor "
        f"{derivation.annuity_factor:.6f}).{_inferred_demand_note(derivation)}"
    )


def _namely_clause(derivation: views.LevelizedHeatCostDerivation) -> str:
    """Return the clause naming the subjects the numerator covers, or an empty string when there are none."""
    subjects = ", ".join(derivation.attributed_subjects)
    return f", namely {_esc(subjects)}." if subjects else "."


def _subjects_counted_clause(derivation: views.LevelizedHeatCostDerivation) -> str:
    """Return the subjects the numerator covers, ending a list line."""
    subjects = ", ".join(derivation.attributed_subjects)
    return f" Subjects counted: {_esc(subjects)}." if subjects else ""


def _inferred_demand_note(derivation: views.LevelizedHeatCostDerivation) -> str:
    """Return a note on where the heat demand came from when it was not recorded but derived back from the result."""
    if not derivation.demand_inferred_from_published:
        return ""
    return (
        " This result stored no heat demand, so the denominator is divided back out of the "
        "published figure: the division reproduces it by construction rather than checking it."
    )


def _components_section_html(matrix: EvaluationMatrix, context: _ChapterContext) -> str:
    """Render the component breakdown: per-subject stacked NPV bars per perspective (§7.4).

    Each subject's cost blocks go right of zero and its credits (residual value, subsidies, feed-in, anyway credit)
    left, with a marker at the net NPV band, so `net = costs - credits` is visible; credits are not netted, so an
    expensive component with a large subsidy looks different from a cheap one. One collapsible block per perspective
    (the first open), each with a legend of its own groups and a table of NPV, equivalent annual cost, year-0
    investment, support and lifecycle CO2 per subject.

    Args:
        matrix: Every evaluated perspective; one block each.
        context: The chapter this section is rendered into.

    Returns:
        The section; non-empty for a matrix with at least one perspective.
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
    """Render the plausibility panel, with the count of flagged checks in its heading.

    WARN means a magnitude left a generous expected range (often a unit or a rate stored as an absolute); FAIL means a
    structural invariant is broken. The checks, thresholds and order come from `plausibility.py` and
    `cost_database/plausibility_checks.json`; only the reader hint in the Note column is decided here. The status is
    escaped in both the CSS class and the cell.

    Args:
        plausibility: The report whose findings become the panel's rows.
        context: The chapter this section is rendered into.

    Returns:
        The section; never empty.
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
    """Render the comparison section: delta waterfall by subject and discounted payback curve.

    The waterfall attributes the total NPV delta to the subjects that caused it; its net bar is
    `comparison.npv_delta_in_euro`, read from the result rather than summed. Below it the discounted payback is given
    as text per slot (`None` meaning never within the horizon), as the cumulative discounted savings curve, and, when
    the comparison carries a tenancy, as the warm-rent change per month with its per-slot neutrality verdict (§6).
    Subjects with a delta below half a cent are left out of the waterfall but listed in the table. The NPV bridge, a
    separate section, splits the same delta by cost group.

    Args:
        comparison: The variant-vs-reference comparison to show.
        context: The chapter this section is rendered into.

    Returns:
        The section; never empty.
    """
    steps: List[Tuple[str, float, str]] = []
    for subject, delta in sorted(comparison.npv_delta_by_subject.items(), key=lambda item: item[1].best_estimate):
        if abs(delta.best_estimate) < 0.005:
            continue
        color = "var(--g5)" if delta.best_estimate > 0 else "var(--g1)"
        steps.append((subject, delta.best_estimate, color))
    payback = comparison.discounted_payback_envelope
    payback_text = (
        f"earliest {payback.earliest} a, expected {payback.central} a, "
        f"latest {payback.latest} a across the three worlds (None = never within the horizon)"
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
    scenario_cube: Optional[views.ScenarioCubeView] = None,
    reference_result: Optional[LifecycleCostResult] = None,
) -> str:
    """Build the self-contained HTML lifecycle report.

    The document has a header with the run's parameters, a two-level table of contents, the building chapter on the
    gross basis, the owner-occupied, rented and society chapters, the comparison block when there is a reference
    variant, and a footer pointing to `python -m hisim.economics explain`. `views.story_perspectives` assigns
    perspectives to chapters by what they book. Sections run along the calculation chain. Sections with nothing to show
    return the empty string and are named under the contents. When every band is degenerate the header explains why
    there are no whiskers.

    The output has no external references (inlined stylesheet, inline SVG, no script, font or network request), so it
    works offline. It is deterministic except for the generation date, which the golden test normalizes.

    Args:
        matrix: Evaluated perspectives; the first is the reference for the single-result sections of the building
            chapter and for the scenario base.
        plausibility: The plausibility panel.
        audit: Optional resolved-input audit; without it the input-audit section is omitted.
        comparison: Optional variant comparison; adds the comparison chapter and is handed to the story chapter that
            owns its perspective (`_comparison_for`).
        scenario_cube: Optional evaluated cube; adds the scenarios section to the building chapter.
        reference_result: The comparison's baseline result, needed by the NPV bridge and the bank benchmark; without it
            both are omitted and named under the contents.

    Returns:
        The complete HTML document.

    Raises:
        ValueError: If the matrix holds no evaluated perspective.
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
        f"<body><main>{header}{_table_of_contents_html(document)}"
        f"{_not_drawn_html(context.skipped)}{document}{footer}</main></body></html>"
    )


def _building_chapter_html(
    matrix: EvaluationMatrix,
    plausibility: PlausibilityReport,
    audit: Optional[InputAuditReport],
    comparison: Optional[VariantComparison],
    scenario_cube: Optional[views.ScenarioCubeView],
    context: _ChapterContext,
) -> str:
    """Render the building chapter: what the technology costs before asking whose money it is.

    Everything on the gross basis, in the order the numbers are built: inputs, totals, flows over the years, physics
    and emissions, uncertainty, scenarios, and the perspectives table leading into the story chapters. Sections that
    need one perspective take the matrix's first.

    Args:
        matrix: Every evaluated perspective.
        plausibility: The plausibility panel.
        audit: Optional input audit; without it the audit section is omitted.
        comparison: Optional variant comparison, for the overview's payback milestone.
        scenario_cube: Optional scenario cube for the scenarios section.
        context: The building chapter's context; mutated as explanations are recorded.

    Returns:
        The chapter's sections in page order.

    Raises:
        ValueError: If the matrix holds no evaluated perspective.
    """
    reference = _reference_result(matrix)
    attributed = _first_result_where(matrix, views.has_energy_balance) or reference
    return "".join([
        # The primer first: discounting, the three worlds and the sign rule, stated once for
        # every section that follows.
        _how_to_read_section_html(context),
        # The one-page overview a renovation report starts with opens the analysis.
        _lifecycle_overview_section_html(reference, comparison, context),
        _checks_section_html(plausibility, context),
        _audit_section_html(audit, context) if audit is not None else "",
        # The causes, directly after the audit of what was priced and before the first
        # figure that is computed from them.
        _assumptions_section_html(matrix, context),
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
    """Return an `EvaluationMatrix` of just these results, for a chapter that owns only some perspectives.

    Args:
        results: The chapter's perspectives, in rendering order.

    Returns:
        A matrix of exactly those results, keyed by perspective id.
    """
    return EvaluationMatrix(results={result.perspective_id: result for result in results})


def _is_financed(result: LifecycleCostResult) -> bool:
    """Whether this perspective borrows; the test the three financing sections share.

    Uses `loan_amortization_series(...).has_flows()`, the same reading the sections do.

    Args:
        result: The perspective to test.

    Returns:
        True when the perspective carries loan flows.
    """
    return views.loan_amortization_series(result).has_flows()


def _scoped_to(actor: Actor) -> Callable[[LifecycleCostResult], bool]:
    """Return a `_first_result_where` predicate for "this perspective reports on that party".

    The rented chapter uses it to find its landlord and tenant by scope and to notice when one is missing, so one
    party's flows are never shown under the other's heading.

    Args:
        actor: The scope the perspective must report on.

    Returns:
        The predicate.
    """
    return lambda result: result.scope_payer == actor


def _comparison_for(
    results: Sequence[LifecycleCostResult], comparison: Optional[VariantComparison]
) -> Optional[VariantComparison]:
    """Return the comparison only for the chapter that contains the perspective it was computed for.

    A comparison is computed for one perspective; other chapters show their cumulative discounted cost instead.

    Args:
        results: The chapter's perspectives.
        comparison: The run's comparison, or None.

    Returns:
        The comparison when this chapter carries its perspective, else None.
    """
    if comparison is None:
        return None
    owns = any(result.perspective_id == comparison.perspective_id for result in results)
    return comparison if owns else None


def _result_for(
    results: Sequence[LifecycleCostResult],
    comparison: Optional[VariantComparison],
    lead: LifecycleCostResult,
) -> LifecycleCostResult:
    """Return the chapter's result the comparison was computed for, or `lead` without a comparison.

    The counterpart of `_comparison_for`: the cash curve draws only the comparison's own perspective, so the pair
    handed to the section must agree.

    Args:
        results: The chapter's perspectives.
        comparison: The comparison this chapter owns, as `_comparison_for` returned it, or None.
        lead: The perspective drawn when there is no comparison.

    Returns:
        The result the comparison was computed for, or `lead`.
    """
    if comparison is None:
        return lead
    return next(
        (result for result in results if result.perspective_id == comparison.perspective_id),
        lead,
    )


def _owner_chapter_html(
    stories: views.StoryPerspectives,
    comparison: Optional[VariantComparison],
    context: _ChapterContext,
) -> List[str]:
    """Render the owner-occupied chapter: how a household pays for the system and lives with it.

    Funding, the cash curve, the loan and its cost, the monthly burden, the equity build-up and who pays whom, on the
    owner perspectives `views.story_perspectives` selected. Each section picks the perspective within the chapter that
    has its subject (a loan chart needs a financed one).

    Args:
        stories: The story lists; this chapter renders `stories.owner`.
        comparison: The run's comparison, passed on only when this chapter owns its perspective.
        context: The document's chapter context; a chapter copy is derived and the shared explanation memory is
            mutated.

    Returns:
        The chapter heading and sections, or an empty list when the run has no owner story.
    """
    if not stories.owner:
        return context.skip_chapter(
            ReportChapters.OWNER_OCCUPIED,
            "No perspective of this run tells an owner's story (neither an owner-scoped nor a "
            "support-carrying one).",
        )
    chapter = context.for_chapter(ReportChapters.OWNER_OCCUPIED)
    owner_matrix = _sub_matrix(list(stories.owner))
    lead = stories.owner[0]
    financed = _first_result_where(owner_matrix, _is_financed) or lead
    funded = _first_result_where(owner_matrix, _has_year_zero_funding) or lead
    multi_actor = _first_result_where(
        owner_matrix, lambda result: len({entry.payer for entry in result.timeline.entries}) > 1
    ) or lead
    owner_comparison = _comparison_for(stories.owner, comparison)
    return [
        _chapter_open(ReportChapters.OWNER_OCCUPIED),
        "".join([
            # The statement opens the chapter for the same reason the landlord's opens
            # the rented one — it is what makes every figure after it readable. It is rendered on
            # the financed view where the run has one, because the loan flows belong on the cash
            # side of an owner's statement; without financing that is the chapter's lead anyway.
            _owner_statement_section_html(financed, chapter),
            _sources_uses_section_html(funded, chapter),
            _liquidity_section_html(
                _result_for(stories.owner, owner_comparison, lead), owner_comparison, chapter
            ),
            _loan_section_html(owner_matrix, chapter),
            _cost_of_credit_section_html(owner_matrix, chapter),
            _monthly_burden_section_html(lead, chapter),
            _equity_section_html(owner_matrix, chapter),
            _actor_flow_section_html(multi_actor, chapter),
        ]),
    ]


def _rented_chapter_html(
    stories: views.StoryPerspectives,
    comparison: Optional[VariantComparison],
    context: _ChapterContext,
) -> List[str]:
    """Render the rented chapter: the landlord's business case and the tenant's monthly cost.

    Rendered only when the allocation produced landlord or tenant perspectives. The landlord statement opens it,
    separating the landlord's cash from book value. Each party's sections are guarded on that party's own perspective,
    so a bundle with only a tenant view shows the tenant's side and names the landlord's sections as skipped. When both
    parties are present, `views.levy_transfer_reconciles` checks that the tenant's levy line equals the landlord's levy
    income before either section is drawn.

    Args:
        stories: The story lists; this chapter renders `stories.rented`.
        comparison: The run's comparison, passed on only when this chapter owns its perspective.
        context: The document's chapter context; the shared explanation memory is mutated.

    Returns:
        The chapter heading and sections, or an empty list when nothing was rented out.

    Raises:
        CostDataError: If both parties are present and their halves of the modernization levy do not cancel.
    """
    if not stories.rented:
        return context.skip_chapter(
            ReportChapters.RENTED_OUT,
            "The allocation produced no landlord and no tenant perspective, so this run has no "
            "rented story to tell.",
        )
    chapter = context.for_chapter(ReportChapters.RENTED_OUT)
    rented_matrix = _sub_matrix(list(stories.rented))
    landlord = _first_result_where(rented_matrix, _scoped_to(Actor.LANDLORD))
    tenant = _first_result_where(rented_matrix, _scoped_to(Actor.TENANT))
    if landlord is not None and tenant is not None:
        views.levy_transfer_reconciles(
            views.landlord_statement(landlord),
            views.perspective_statement(tenant, views.StatementPartitions.TENANT),
        )
    rented_comparison = _comparison_for(stories.rented, comparison)
    lead = landlord or tenant or stories.rented[0]
    absent_landlord = (
        "No perspective of this run reports on the landlord, so this chapter tells the tenant's "
        "side of the tenancy alone."
    )
    absent_tenant = (
        "No perspective of this run reports on the tenant, so this chapter tells the landlord's "
        "side of the tenancy alone."
    )
    return [
        _chapter_open(ReportChapters.RENTED_OUT),
        "".join([
            _landlord_statement_section_html(landlord, chapter) if landlord is not None
            else chapter.skip(ReportSections.LANDLORD_STATEMENT, absent_landlord),
            _tenant_statement_section_html(tenant, chapter) if tenant is not None
            else chapter.skip(ReportSections.TENANT_STATEMENT, absent_tenant),
            _actor_section_html(rented_matrix, chapter),
            _actor_flow_section_html(landlord, chapter) if landlord is not None
            else chapter.skip(
                ReportSections.WHO_PAYS_WHOM,
                f"{absent_landlord} The flow diagram is drawn on the landlord, the party whose "
                "ribbons run to and from every other one.",
            ),
            _monthly_burden_section_html(tenant, chapter) if tenant is not None
            else chapter.skip(
                ReportSections.MONTHLY_BURDEN,
                f"{absent_tenant} The monthly burden is the tenant's rent and bill.",
            ),
            _liquidity_section_html(
                _result_for(stories.rented, rented_comparison, lead),
                rented_comparison,
                chapter,
            ),
            _loan_section_html(rented_matrix, chapter),
            _cost_of_credit_section_html(rented_matrix, chapter),
            _equity_section_html(rented_matrix, chapter),
        ]),
    ]


def _society_chapter_html(
    stories: views.StoryPerspectives,
    comparison: Optional[VariantComparison],
    context: _ChapterContext,
) -> List[str]:
    """Render the society chapter: the macroeconomic view, where transfers cancel and CO2 enters at its damage cost.

    Three sections: the statement, the cash curve of the resource cost, and who pays whom after transfers net out.
    Financing sections are not offered, because a macroeconomic view books no debt service; they belong to the owner
    and rented chapters. A perspective belongs here only if it books CO2 damage and reports on the whole system; a
    landlord-scoped macroeconomic view goes to the rented chapter.

    Args:
        stories: The story lists; this chapter renders `stories.society`.
        comparison: The run's comparison, passed on only when this chapter owns its perspective.
        context: The document's chapter context; the shared explanation memory is mutated.

    Returns:
        The chapter heading and sections, or an empty list when no system-scoped perspective books CO2 damage.
    """
    if not stories.society:
        return context.skip_chapter(
            ReportChapters.SOCIETY,
            # Both halves of the classification, because both can be the reason: a run may have
            # no macroeconomic view at all, or one that is scoped to a party and is therefore
            # told as that party's story (see `views.story_perspectives`).
            "This run evaluated no system-scoped perspective that books CO2 damage cost, so "
            "there is no society story to tell.",
        )
    chapter = context.for_chapter(ReportChapters.SOCIETY)
    macro = stories.society[0]
    society_comparison = _comparison_for(stories.society, comparison)
    return [
        _chapter_open(ReportChapters.SOCIETY),
        "".join([
            _society_statement_section_html(macro, chapter),
            _liquidity_section_html(
                _result_for(stories.society, society_comparison, macro),
                society_comparison,
                chapter,
            ),
            _actor_flow_section_html(macro, chapter),
        ]),
    ]


def _comparison_chapter_html(
    matrix: EvaluationMatrix,
    comparison: Optional[VariantComparison],
    reference_result: Optional[LifecycleCostResult],
    context: _ChapterContext,
) -> List[str]:
    """Render the comparison block: the sections that exist only when there is a reference variant.

    It compares two runs rather than describing one party, so it has no lead-in and comes last.

    Args:
        matrix: Evaluated perspectives; the comparison's own is looked up in it.
        comparison: The run's comparison, or None.
        reference_result: The comparison's baseline result; without it the two sections that decompose it are skipped
            with their reasons.
        context: The document's chapter context; the shared explanation memory is mutated.

    Returns:
        The chapter heading and sections, or an empty list without a comparison.

    Raises:
        ValueError: If the matrix holds no evaluated perspective.
    """
    if comparison is None:
        return []
    chapter = context.for_chapter(ReportChapters.COMPARISON)
    reference = _reference_result(matrix)
    variant = matrix.results.get(comparison.perspective_id, reference)
    blocks = [_comparison_section_html(comparison, chapter)]
    if reference_result is not None:
        # The bridge and the benchmark need both results, not just the published deltas: the
        # bridge splits by cost group and the benchmark needs the per-year differential flows.
        blocks.append(
            _comparison_bridge_section_html(reference_result, variant, comparison, chapter)
        )
        blocks.append(_wealth_benchmark_section_html(reference_result, variant, chapter))
    else:
        chapter.skip(
            ReportSections.NPV_BRIDGE,
            "The report was built with a comparison but without the reference result the bridge "
            "decomposes, so there is nothing to split by cost group.",
        )
        chapter.skip(
            ReportSections.BANK_BENCHMARK,
            "The report was built with a comparison but without the reference result the "
            "fixed-interest benchmark needs, so there are no per-year differential flows to "
            "compare against a savings account.",
        )
    return [_chapter_open(ReportChapters.COMPARISON), "".join(blocks)]


def write_lifecycle_report(
    matrix: EvaluationMatrix,
    plausibility: PlausibilityReport,
    result_directory: str,
    audit: Optional[InputAuditReport] = None,
    comparison: Optional[VariantComparison] = None,
    scenario_cube: Optional[views.ScenarioCubeView] = None,
    reference_result: Optional[LifecycleCostResult] = None,
) -> str:
    """Write the HTML report to `ReportFileNames.LIFECYCLE_REPORT_FILE_NAME` in the result directory.

    The file counterpart of `build_lifecycle_report_html`, which tests render without touching a directory; the
    `report` CLI and `bridge.py` call this. The PNG companions are written separately by
    `report_plots.write_report_plots`, one set per perspective.

    Args:
        matrix: Evaluated perspectives.
        plausibility: The plausibility panel.
        result_directory: Directory to write into (the run's `results/`).
        audit: Optional input audit.
        comparison: Optional variant comparison.
        scenario_cube: Optional scenario cube.
        reference_result: The comparison's baseline, for the NPV bridge and the bank benchmark.

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
