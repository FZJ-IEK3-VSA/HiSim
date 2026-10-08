"""Report sections of the visualization set: the charts that answer a reader's own questions.

One builder per chart: lifecycle overview, year-0 funding, who pays whom, the four party statements, cash curve, loan
and cost of credit, energy balance, uncertainty drivers, cost structure and shapes, equity, monthly burden, component
lifetimes, NPV bridge and bank benchmark. Each opens with `scaffold._explanation_html` (the authored text of
`report_prose.ReportProse`) and adds the captions that state this run's figures. A section that cannot be drawn records
its reason on the `_ChapterContext` and returns nothing; the document lists those reasons. The four party statements
share one builder, `_statement_section_html`, since `views.StatementPartitions` carries each party's wording.
"""


from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from hisim.economics import views
# The refusal type of the view layer, reached through `views`, the surface presentation may
# import (`tests/test_economics_import_lint.py`).
from hisim.economics.views import CostDataError
from hisim.economics.presentation_style import PresentationStyle, SequentialRamp, group_of
# The two captions this module shares with `report_plots.py`: the payback sentence under the
# cash curve's lower panel and the treemap's disclosure. They are printed by both renderers,
# so they are authored once, in the module that holds the report's wording.
from hisim.economics.report_prose import payback_interval_sentence, treemap_disclosure
from hisim.economics.results import (
    EvaluationMatrix,
    LifecycleCostResult,
    VariantComparison,
    PaybackEnvelope,
)
from hisim.economics.timeline import CostCategory
from hisim.economics.uncertainty import Slot


from hisim.economics.reporting.summary import _band_str, _fmt
from hisim.economics.reporting.charts import (
    _attribution_tornado_svg,
    _bridge_svg,
    _ChartGeometry,
    _cost_of_credit_svg,
    _details,
    _esc,
    _gantt_svg,
    _loan_svg,
    _monthly_burden_svg,
    _sankey_svg,
    _table,
    _treemap_svg,
    _xy_lines_svg,
)
from hisim.economics.reporting.scaffold import (
    ReportSections,
    _ChapterContext,
    _explanation_html,
    _section_open,
)


#: One lane of `charts._gantt_svg`: `(label, spans, events, colour)`, where a span is `(start
#: year, end year or None for "to the horizon", span label)` and an event is `(year, label,
#: amount in euro or None)`. Built by the lifetimes strip and the lifecycle overview.
_GanttRow = Tuple[
    str,
    List[Tuple[int, Optional[int], str]],
    List[Tuple[int, str, Optional[float]]],
    str,
]


def _first_result_where(
    matrix: EvaluationMatrix, predicate: Callable[[LifecycleCostResult], bool]
) -> Optional[LifecycleCostResult]:
    """Return the first perspective of the matrix that satisfies a predicate, or None.

    Some charts need a perspective with particular data (two actors, a loan), so they pick the first that has it
    instead of the matrix's first perspective; the heading names the perspective shown.

    Args:
        matrix: Every evaluated perspective, in evaluation order.
        predicate: What the section needs of a result to be drawable.

    Returns:
        The first matching result, or None when no perspective qualifies.
    """
    for result in matrix.results.values():
        if predicate(result):
            return result
    return None


def _has_year_zero_funding(result: LifecycleCostResult) -> bool:
    """Return whether year 0 books a subsidy or a loan disbursement.

    A plain timeline scan rather than the funding view, which validates and raises; choosing a perspective must not
    depend on that validation.

    Args:
        result: The perspective to test.

    Returns:
        True when year 0 books a subsidy or a loan disbursement.
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
    """Return the lifecycle overview: assets, financing, support and milestones on one year axis.

    Every lane restates a chart further down on a shared axis and introduces no new numbers.

    Args:
        result: The perspective whose lanes are drawn.
        comparison: The variant comparison that supplies the payback milestone, or None; without one the milestone is
            absent and the caption says why.
        context: The chapter this section is being rendered into.

    Returns:
        The section, with one lane per asset plus the milestone, financing and support lanes.
    """
    lanes = views.lifecycle_lanes(result, comparison)
    rows: List[_GanttRow] = [(
        "Milestones",
        [(span.start_year, span.end_year, span.label) for span in lanes.milestones.spans],
        [(event.year, event.label, event.amount_in_euro) for event in lanes.milestones.events],
        "var(--baseline)",
    )]
    skipped: List[str] = []
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
    rows.extend(_event_strip_rows(lanes.assets))
    # An empty lane is stated beside the chart, not in the log, so the reader sees why a row is
    # missing. It is not a `context.skip` because the section itself is drawn.
    note = "" if not skipped else (
        f"<p class='sub'>The {_esc(' and '.join(skipped))} lane"
        f"{'s are' if len(skipped) > 1 else ' is'} empty for perspective "
        f"{_esc(result.perspective_id)} and {'are' if len(skipped) > 1 else 'is'} not drawn.</p>"
    )
    note += "" if comparison is not None else (
        "<p class='sub'>This run has no reference variant, so there is no payback milestone: "
        "payback is a statement about a difference between two variants.</p>"
    )
    return (
        _section_open(ReportSections.AT_A_GLANCE, context, result.perspective_id)
        + _explanation_html(ReportSections.AT_A_GLANCE, context) + note
        + _gantt_svg(rows, lanes.horizon) + "</section>"
    )


def _event_strip_rows(strips: List[views.EventStripRow]) -> List[_GanttRow]:
    """Return one Gantt lane per subject of an event strip, including the residual write-down marker.

    Shared by the lifetimes section and the asset lanes of the lifecycle overview, so the two always agree. The
    residual write-down is appended as an event because the view carries it separately.

    Args:
        strips: The rows `views.lifecycle_lanes` or `views.component_event_strip` returned.

    Returns:
        One `(label, spans, events, colour)` lane per subject, in the view's order.
    """
    rows: List[_GanttRow] = []
    for strip in strips:
        events: List[Tuple[int, str, Optional[float]]] = [
            (event.year, event.kind.value, event.amount_in_euro) for event in strip.events
        ]
        if strip.residual is not None:
            events.append((strip.residual.year, "residual", strip.residual.amount_in_euro))
        rows.append((
            strip.subject,
            [(span.start_year, span.end_year, "in service") for span in strip.spans],
            events,
            "var(--g4)",
        ))
    return rows


def _sources_uses_section_html(result: LifecycleCostResult, context: _ChapterContext) -> str:
    """Return the funding Sankey: how year 0 is paid for and what it buys, balanced to the euro.

    Subsidies are shown per scheme id ("state -> KfW 261 -> heat pump"). The view validates that both column totals are
    equal, so the caption states the balance as a fact.

    Args:
        result: The perspective whose year 0 is stated; the caller checks `_has_year_zero_funding`.
        context: The chapter this section is being rendered into.

    Returns:
        The section, or the empty string when year 0 is funded entirely from own capital.
    """
    statement = views.funding_sources_and_uses(result)
    if not statement.has_external_funding():
        return context.skip(
            ReportSections.FUNDING,
            f"Year 0 of perspective {result.perspective_id!r} is funded entirely from own "
            "capital, which the investment waterfall shows better than a one-ribbon Sankey.",
        )
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
    # The node reads as the scheme's friendly name and the raw id lives in the tooltip.
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


def _liquidity_section_html(
    result: LifecycleCostResult,
    comparison: Optional[VariantComparison],
    context: _ChapterContext,
) -> str:
    """Return the cash curve: the cumulative cash position as a fan, nominal above and discounted below.

    With a comparison the lower panel shows the cumulative discounted savings and the payback sentence; without one it
    shows the cumulative discounted cost, which ends at the NPV. A `VariantComparison` belongs to one perspective, so
    the section refuses to pair it with another perspective's cash curve.

    Args:
        result: The perspective whose liquidity is drawn; with a comparison it must be the comparison's perspective.
        comparison: The variant comparison whose savings curve carries the payback, or None.
        context: The chapter this section is being rendered into.

    Returns:
        The section, with both panels.

    Raises:
        CostDataError: If the comparison was computed for a different perspective than `result`.
    """
    if comparison is not None and comparison.perspective_id != result.perspective_id:
        raise CostDataError(
            f"The cash curve was asked to draw perspective {result.perspective_id!r} with the "
            f"comparison of perspective {comparison.perspective_id!r}: the payback sentence and "
            "the curve above it would then belong to two different parties. Pass the result the "
            "comparison was computed for, or no comparison at all."
        )
    nominal = views.cumulative_nominal_cost_series(result)
    years = list(range(len(nominal[Slot.BEST_ESTIMATE])))
    worst_year, worst_amount = views.worst_liquidity_position(result)
    nominal_svg = _xy_lines_svg(
        series=[("best-estimate scenario", list(zip(years, nominal[Slot.BEST_ESTIMATE])),
                 "var(--g0)", 2.2, "")],
        bands=[(list(zip(years, nominal[Slot.LOW])), list(zip(years, nominal[Slot.HIGH])), "var(--g0)")],
        y_label="cumulative nominal cost [EUR] - costs plotted upward",
        annotations=[(worst_year, worst_amount,
                      f"deepest out-of-pocket {_fmt(worst_amount)} EUR in year {worst_year}")],
    )
    if comparison is not None:
        curves = comparison.cumulative_discounted_savings_in_euro
        low = _savings_curve(curves, "low", comparison)
        best_estimate = _savings_curve(curves, "best_estimate", comparison)
        high = _savings_curve(curves, "high", comparison)
        crossings = views.band_zero_crossings(
            {"low": low, "best_estimate": best_estimate, "high": high}
        )
        lower_label = "cumulative discounted savings [EUR] (reference - variant)"
        envelope = PaybackEnvelope.of(crossings)
        payback_note = payback_interval_sentence(envelope.earliest, envelope.central, envelope.latest)
    else:
        discounted = views.cumulative_discounted_cost_series(result)
        low, best_estimate, high = (
            discounted[Slot.LOW], discounted[Slot.BEST_ESTIMATE], discounted[Slot.HIGH]
        )
        lower_label = "cumulative discounted cost [EUR] - ends at the NPV"
        payback_note = (
            "Without a reference variant there is no payback question, so the lower panel shows "
            "the cumulative discounted cost, whose end point is the reported NPV."
        )
    discounted_years = list(range(len(best_estimate)))
    discounted_svg = _xy_lines_svg(
        series=[("best-estimate scenario", list(zip(discounted_years, best_estimate)),
                 "var(--g0)", 2.2, "")],
        bands=[(list(zip(discounted_years, low)), list(zip(discounted_years, high)), "var(--g0)")],
        y_label=lower_label,
    )
    return (
        _section_open(ReportSections.CASH_CURVE, context, result.perspective_id)
        + _explanation_html(ReportSections.CASH_CURVE, context)
        + f"<p class='sub'>{_esc(payback_note)}</p>"
        + nominal_svg + discounted_svg + "</section>"
    )


def _savings_curve(
    curves: Dict[str, List[float]], slot: str, comparison: VariantComparison
) -> List[float]:
    """Return one band slot's cumulative savings curve, raising a named error when it is missing.

    Args:
        curves: The comparison's `cumulative_discounted_savings_in_euro`.
        slot: The slot key to read: `"low"`, `"best_estimate"` or `"high"`.
        comparison: The comparison the curves came from, named in the error.

    Returns:
        That slot's curve.

    Raises:
        CostDataError: If the comparison carries no curve for the slot.
    """
    if slot not in curves:
        raise CostDataError(
            f"The comparison of perspective {comparison.perspective_id!r} carries no "
            f"{slot!r} cumulative savings curve (it has {sorted(curves)}), so the cash curve "
            "cannot draw its band or state its payback."
        )
    return curves[slot]


def _uncertainty_section_html(result: LifecycleCostResult, context: _ChapterContext) -> str:
    """Return the uncertainty drivers: which subjects make the total NPV band as wide as it is.

    This attributes the existing band; nothing is re-evaluated, so it is not a sensitivity analysis, and the prose says
    so.

    Args:
        result: The perspective whose band is attributed.
        context: The chapter this section is being rendered into.

    Returns:
        The section, or the empty string when the total NPV band has no width.
    """
    total = result.total_npv_in_euro
    if total.is_exact():
        return context.skip(
            ReportSections.UNCERTAINTY_DRIVERS,
            f"The total NPV band of perspective {result.perspective_id!r} is degenerate, so "
            "there is no width to attribute to anything.",
        )
    rows = views.uncertainty_attribution(result)
    return (
        _section_open(ReportSections.UNCERTAINTY_DRIVERS, context, result.perspective_id)
        + _explanation_html(ReportSections.UNCERTAINTY_DRIVERS, context)
        + _attribution_tornado_svg(rows, total)
        + _details(
            "attribution table",
            _table(
                ["Subject", "NPV (best estimate)", "Optimistic delta", "Pessimistic delta"],
                [
                    [_esc(row.subject), _fmt(row.best_estimate_npv_in_euro),
                     _fmt(row.low_delta_in_euro), _fmt(row.high_delta_in_euro)]
                    for row in rows
                ],
            ),
        )
        + "</section>"
    )


def _actor_flow_section_html(result: LifecycleCostResult, context: _ChapterContext) -> str:
    """Return the who-pays-whom Sankey over the whole horizon, one column per party.

    Shows the levy, subsidies and energy bills crossing actor boundaries. Every internal party has its own column,
    ordered by `actor_columns`, so a payment between two of them (the §559e levy) runs left to right like every other
    ribbon.

    Args:
        result: The perspective whose flows are drawn; it needs at least two actor nodes.
        context: The chapter this section is being rendered into.

    Returns:
        The section, or the empty string for a perspective with a single actor.
    """
    matrix = views.actor_flow_matrix(result)
    if len(matrix.actors) < 2:
        return context.skip(
            ReportSections.WHO_PAYS_WHOM,
            f"Perspective {result.perspective_id!r} has {len(matrix.actors)} actor node(s), so "
            "one party pays everything and there is no who-pays-whom story to draw.",
        )
    nets = matrix.net_by_actor()

    def key(node: str, is_target: bool) -> str:
        """Return the node's key, namespaced by side so an actor and an external node never collide."""
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
    # One column per party, ordered by the view so payments between them run left to right;
    # external sources stay leftmost and external sinks rightmost, and a ribbon may skip columns.
    columns = (
        [[f"src:{node}" for node in matrix.sources]]
        + [[f"actor:{actor}" for actor in column] for column in matrix.actor_columns()]
        + [[f"snk:{node}" for node in matrix.sinks]]
    )
    return (
        _section_open(ReportSections.WHO_PAYS_WHOM, context, result.perspective_id)
        + _explanation_html(ReportSections.WHO_PAYS_WHOM, context)
        + "<p class='sub'>This chart shows the best-estimate scenario only; the band of the grand "
        f"total is {_esc(_band_str(matrix.total_band))}.</p>"
        # The stub closing an actor's face is labelled with the view's own net, in its sign
        # convention (cost positive), so it cannot contradict the node beside it.
        + _sankey_svg(
            columns, ribbons, labels,
            stub_labels={f"actor:{actor}": f"net {_fmt(net)} EUR" for actor, net in nets.items()},
        )
        + f"<p class='sub'>{matrix.folded_ribbon_count} ribbon(s) below 0.5 % of the flow volume, "
        f"carrying {_fmt(matrix.folded_amount_in_euro)} EUR in total, were folded into a single "
        "grey ribbon per node pair.</p></section>"
    )


def _statement_table_html(
    statement: views.PerspectiveStatement, side_labels: Optional[Tuple[str, str]] = None
) -> str:
    """Return the two-sided table every party statement shares.

    One row per category with its present value and side, then the two subtotals and the net position. The side column
    uses the partition's own labels (e.g. "real resource costs" / "transfers" for society). Subtotals are printed even
    when a side is empty, as an explicit zero.

    Args:
        statement: The partition `views.perspective_statement` returned.
        side_labels: Labels for the side column when they differ from the partition's own; the landlord statement
            passes "cash" and "accounting".

    Returns:
        The table, rows in the statement's own order.
    """
    partition = statement.partition
    primary_side, secondary_side = side_labels or (partition.primary_label, partition.secondary_label)
    rows = [
        [_esc(line.label), _fmt(line.npv_in_euro),
         secondary_side if line.is_accounting_credit else primary_side]
        for line in list(statement.cash_lines) + list(statement.accounting_lines)
    ]
    rows.append([
        f"<b>{_esc(partition.primary_label)}, subtotal</b>",
        f"<b>{_fmt(statement.cash_subtotal_in_euro)}</b>",
        f"<b>{_esc(primary_side)}</b>",
    ])
    rows.append([
        f"<b>{_esc(partition.secondary_label)}, subtotal</b>",
        f"<b>{_fmt(statement.accounting_subtotal_in_euro)}</b>",
        f"<b>{_esc(secondary_side)}</b>",
    ])
    rows.append([
        "<b>net position</b>", f"<b>{_fmt(statement.net_position_in_euro)}</b>", "<b>both sides</b>",
    ])
    return _table(["Item", "NPV [EUR]", "Side"], rows)


def _statement_caption(statement: views.PerspectiveStatement, lead: str = "", trail: str = "") -> str:
    """Return the caption stating the run's two subtotals under a party statement, in the partition's words.

    Args:
        statement: The partition `views.perspective_statement` returned.
        lead: Text opening the paragraph, inserted verbatim (it carries its own markup and escaping), e.g. the
            landlord's §559 levy verdict.
        trail: Text closing the paragraph, inserted verbatim, e.g. the landlord's note that only the cash half reaches
            an account.

    Returns:
        The caption paragraph.
    """
    partition = statement.partition
    return (
        f"<p class='sub'>{lead}{_esc(partition.primary_label.capitalize())} come to "
        f"<b>{_fmt(statement.cash_subtotal_in_euro)} EUR</b> and "
        f"{_esc(partition.secondary_label)} to "
        f"<b>{_fmt(statement.accounting_subtotal_in_euro)} EUR</b> in present value; together they "
        f"are the net position of {_esc(_band_str(statement.net_position_band))}.{trail}</p>"
    )


def _statement_section_html(
    section: Tuple[str, str],
    result: LifecycleCostResult,
    partition: views.StatementPartition,
    context: _ChapterContext,
    no_flows_reason: str,
    note: Callable[[views.PerspectiveStatement], str],
    after_table: Callable[[views.PerspectiveStatement], str] = lambda _statement: "",
    side_labels: Optional[Tuple[str, str]] = None,
) -> str:
    """Return a party statement section: heading, explanation, the run's figures, the two-sided table.

    Shared by the owner, tenant, society and landlord statements; only the wording and the extras differ, and
    `views.StatementPartitions` carries the wording.

    Args:
        section: The `(anchor, name)` pair of `ReportSections` for this party.
        result: The perspective to state.
        partition: Which two sides to split it into, and what to call them.
        context: The chapter this section is being rendered into.
        no_flows_reason: The sentence recorded under "Not drawn for this run" when the perspective books nothing.
        note: Builds the paragraph(s) with this run's figures, between the explanation and the table.
        after_table: Builds anything drawn below the table; only the landlord uses it, for the Sankey.
        side_labels: Passed through to `_statement_table_html`.

    Returns:
        The section, or the empty string when the perspective books no flow at all (its reason recorded on the
            context).

    Raises:
        CostDataError: From `views.perspective_statement`, if the two sides do not sum to the perspective's NPV, or if
            a transfer partition is asked for on a scoped perspective.
    """
    statement = views.perspective_statement(result, partition)
    if not statement.cash_lines and not statement.accounting_lines:
        return context.skip(section, no_flows_reason)
    return (
        _section_open(section, context, result.perspective_id)
        + _explanation_html(section, context)
        + note(statement)
        + _statement_table_html(statement, side_labels)
        + after_table(statement)
        + "</section>"
    )


def _owner_statement_section_html(result: LifecycleCostResult, context: _ChapterContext) -> str:
    """Return the owner-occupier's two-sided statement.

    Splits the owner's NPV into money that moved (investment net of subsidies, bills, maintenance, replacements,
    feed-in, loan flows) and value that was only booked (residual value and the anyway credit, the avoided cost of a
    replacement that was due anyway). The caption states how much of the result is each. `views.perspective_statement`
    validates that the sides sum to the NPV.

    Args:
        result: The owner perspective to state.
        context: The chapter this section is being rendered into.

    Returns:
        The section, or the empty string for a perspective that books no flows at all.
    """
    return _statement_section_html(
        ReportSections.OWNER_STATEMENT,
        result,
        views.StatementPartitions.OWNER,
        context,
        f"Perspective {result.perspective_id!r} books no flows at all, so there is no owner "
        "position to state.",
        note=_statement_caption,
    )


def _tenant_statement_section_html(result: LifecycleCostResult, context: _ChapterContext) -> str:
    """Return the tenant's statement: everything paid because of the renovation, nothing received.

    Splits the tenant's cost into the modernization levy and the energy and apportioned operating costs. The credit
    side is empty and printed as zero; a lower energy bill appears as a smaller cost line. The caption calls the levy
    the counterpart of the landlord's levy income; `assembly._rented_chapter_html` checks that with
    `views.levy_transfer_reconciles` before either statement is drawn.

    Args:
        result: The tenant perspective to state.
        context: The chapter this section is being rendered into.

    Returns:
        The section, or the empty string for a tenant the allocation gave no flows.
    """
    return _statement_section_html(
        ReportSections.TENANT_STATEMENT,
        result,
        views.StatementPartitions.TENANT,
        context,
        f"The allocation assigned perspective {result.perspective_id!r} no flows at all, so "
        "there is no tenant position to state.",
        note=lambda statement: _statement_caption(statement) + _tenant_levy_note(statement),
    )


def _tenant_levy_note(statement: views.PerspectiveStatement) -> str:
    """Return the paragraph stating how much of the tenant's position is the levy.

    Args:
        statement: The tenant's statement.

    Returns:
        The paragraph.
    """
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
    return f"<p class='sub'>{levy_note}</p>"


def _society_statement_section_html(result: LifecycleCostResult, context: _ChapterContext) -> str:
    """Return the macroeconomic statement: real resource costs against transfers that cancel.

    Resource categories keep their values, transfers appear with both halves and a zero-sum line, and CO2 enters at its
    damage cost. On the shipped macroeconomic perspective the transfers are already removed (§4.5), so the transfer
    rows are zeros, and the caption says so. Only drawn for a SYSTEM-scoped perspective; `views.story_perspectives`
    keeps other scopes out of this chapter, since the view refuses them.

    Args:
        result: The macroeconomic perspective to state.
        context: The chapter this section is being rendered into.

    Returns:
        The section, or the empty string for a perspective that books no flows at all.
    """
    return _statement_section_html(
        ReportSections.SOCIETY_STATEMENT,
        result,
        views.StatementPartitions.SOCIETY,
        context,
        f"Perspective {result.perspective_id!r} books no flows at all, so there is no "
        "macroeconomic position to state.",
        note=lambda statement: _society_transfer_note(statement, result),
    )


def _society_transfer_note(
    statement: views.PerspectiveStatement, result: LifecycleCostResult
) -> str:
    """Return the paragraph stating what the transfer side came to and the CO2 damage cost used.

    Args:
        statement: The society statement being drawn.
        result: The perspective it was built from, for the damage cost and its parameter.

    Returns:
        The paragraph.
    """
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
    return f"<p class='sub'>{transfers}{damage_note}</p>"


def _landlord_statement_section_html(result: LifecycleCostResult, context: _ChapterContext) -> str:
    """Return the landlord's two-sided statement plus an income Sankey of the same numbers.

    The table states the cash side and the accounting side (residual value and the anyway credit) separately before
    combining them; the Sankey draws the same statement as an income statement. `views.landlord_statement` validates
    that the sides sum to the NPV. The levy verdict opens the caption and the Sankey closes the section.

    Args:
        result: The landlord perspective to state.
        context: The chapter this section is being rendered into.

    Returns:
        The section, or the empty string for a perspective that books no flows at all.
    """
    return _statement_section_html(
        ReportSections.LANDLORD_STATEMENT,
        result,
        views.StatementPartitions.LANDLORD,
        context,
        f"Perspective {result.perspective_id!r} books no flows at all, so there is no "
        "landlord business case to state.",
        note=_landlord_statement_caption,
        after_table=_landlord_statement_sankey,
        side_labels=("cash", "accounting"),
    )


def _landlord_statement_caption(statement: views.PerspectiveStatement) -> str:
    """Return the caption under the landlord statement: the levy, the cap verdict and the two subtotals.

    The levy clause says whether a statutory cap set the rent increase: below the cap the levy scales with spending, at
    the cap it does not.

    Args:
        statement: The partition `views.landlord_statement` returned.

    Returns:
        The caption paragraph.
    """
    levy = statement.levy
    if levy is None:
        levy_note = "This perspective books no modernization levy."
    elif levy.cap_binding_in_best_estimate and levy.cap_in_euro_per_m2_per_month is not None:
        levy_note = (
            f"Levy income {_fmt(levy.annual_amount_in_euro.best_estimate)} EUR per year, <b>set by "
            f"the §559 cap</b> of {levy.cap_in_euro_per_m2_per_month:,.2f} EUR/m²·month — not by the "
            "modernization cost, which would have supported more."
        )
    else:
        levy_note = (
            f"Levy income {_fmt(levy.annual_amount_in_euro.best_estimate)} EUR per year; the §559 "
            "cap does not bind, so the increase is set by the modernization cost."
        )
    if levy is not None:
        levy_note += _levy_world_verdicts(levy)
    return _statement_caption(
        statement,
        lead=f"{levy_note} ",
        trail=" A negative net position is an advantage on the stated basis, but only the cash "
              "half of it ever reaches an account.",
    )


def _levy_world_verdicts(levy: Any) -> str:
    """Return which mechanism set the levy in each of the three worlds.

    The caps apply per band slot, so the cap can bind in the expensive world and not in the cheap one. Identical
    verdicts collapse into one sentence.

    Args:
        levy: The perspective's `results.ModernizationLevySummary`; an empty `binding_mechanism_by_slot` (older stored
            results) adds nothing. Typed `Any` because `results` exposes it only through the statement.

    Returns:
        A sentence to append to the levy note, or the empty string.
    """
    verdicts = levy.binding_mechanism_by_slot
    if not verdicts:
        return ""
    slots = [slot for slot in (Slot.BEST_ESTIMATE, Slot.LOW, Slot.HIGH) if slot in verdicts]
    distinct = {verdicts[slot] for slot in slots}
    if len(distinct) == 1:
        return f" Binding mechanism in all three worlds: <b>{_esc(next(iter(distinct)))}</b>."
    stated = "; ".join(f"{slot.value} {_esc(verdicts[slot])}" for slot in slots)
    return f" Binding mechanism per world: <b>{stated}</b>."


def _landlord_statement_sankey(statement: views.PerspectiveStatement) -> str:
    """Return the landlord statement as an income Sankey: income left, expenses right, the leftover is the result.

    Income ribbons enter the landlord node from the left, expenses leave to the right, and the remainder runs into a
    terminal node labelled with the net position. Accounting credits use the credit style (outlined, translucent,
    dashed). When the renovation is a net cost the net position enters from the left instead, and the caption says
    which way round it is.

    Args:
        statement: The partition `views.landlord_statement` returned.

    Returns:
        The diagram and its legend, or the empty string when the statement has no flow to draw.
    """
    flows, net_is_inflow = statement.income_flows()
    if not flows:
        return ""
    node = views.LandlordStatementCategories.LANDLORD_NODE
    net_node = views.LandlordStatementCategories.NET_POSITION_NODE
    incomes = [ribbon.source for ribbon in flows if ribbon.target == node]
    expenses = [ribbon.target for ribbon in flows if ribbon.source == node]
    ribbons = [
        (f"in:{ribbon.source}" if ribbon.target == node else f"mid:{ribbon.source}",
         f"mid:{ribbon.target}" if ribbon.target == node else f"out:{ribbon.target}",
         ribbon.amount_in_euro,
         f"var(--g{group_of(ribbon.category)})" if ribbon.category is not None else "var(--muted)",
         ribbon.is_accounting_credit)
        for ribbon in flows
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


def _loan_section_html(matrix: EvaluationMatrix, context: _ChapterContext) -> str:
    """Return the debt service per year, one block per financed perspective of the chapter.

    An annuity shows falling interest against rising principal, an interest-only loan a final bullet. Rendered per
    story chapter over that chapter's own perspectives.

    Args:
        matrix: The chapter's perspectives; one block each, in matrix order.
        context: The chapter this section is being rendered into.

    Returns:
        The section, or the empty string when nobody in this chapter borrows.
    """
    blocks = []
    for perspective_id, result in matrix.results.items():
        chart = _loan_svg(result)
        if not chart:
            continue
        blocks.append(f"<p class='sub'><b>{_esc(perspective_id)}</b></p>" + chart)
    if not blocks:
        return context.skip(
            ReportSections.LOAN,
            "No perspective of this chapter's story carries loan flows: every purchase in it is "
            "a cash purchase.",
        )
    return (
        _section_open(ReportSections.LOAN, context)
        + _explanation_html(ReportSections.LOAN, context)
        + "".join(blocks) + "</section>"
    )


def _effective_rate_text(credit: views.TotalCostOfCredit) -> str:
    """Return the effective annual rate (Effektivzins) as the panel prints it.

    When the rate is None, `TotalCostOfCredit.effective_annual_rate_note` gives the reason when known (e.g. a term past
    the horizon, a repayment grant larger than the debt service), printed in brackets after "n/a".

    Args:
        credit: The disclosure to render the rate cell of.

    Returns:
        The formatted percentage, or "n/a" followed by the view's reason when it has one.
    """
    if credit.effective_annual_rate is not None:
        return f"{credit.effective_annual_rate:.2%}"
    if credit.effective_annual_rate_note:
        return f"n/a ({credit.effective_annual_rate_note})"
    return "n/a"


def _cost_of_credit_section_html(matrix: EvaluationMatrix, context: _ChapterContext) -> str:
    """Return the total cost of credit and the effective annual rate, one block per financed perspective.

    Built from the same amortization series as the loan section, over the same perspectives of the chapter; each block
    names its perspective.

    Args:
        matrix: The chapter's perspectives; the financed ones get a block, in matrix order.
        context: The chapter this section is being rendered into.

    Returns:
        The section, or the empty string when nobody in this chapter borrows.
    """
    blocks = []
    for perspective_id, result in matrix.results.items():
        amortization = views.loan_amortization_series(result)
        if not amortization.has_flows():
            continue
        blocks.append(
            f"<p class='sub'><b>{_esc(perspective_id)}</b></p>"
            + _cost_of_credit_block_html(result, amortization)
        )
    if not blocks:
        return context.skip(
            ReportSections.COST_OF_CREDIT,
            "No perspective of this chapter's story carries loan flows, so there is no credit "
            "to price here.",
        )
    return (
        _section_open(ReportSections.COST_OF_CREDIT, context)
        + _explanation_html(ReportSections.COST_OF_CREDIT, context)
        + "".join(blocks) + "</section>"
    )


def _cost_of_credit_block_html(
    result: LifecycleCostResult, amortization: views.LoanAmortization
) -> str:
    """Return one financed perspective's credit disclosure: the rate, the stacked bar, the table and the balance.

    Args:
        result: The financed perspective to disclose.
        amortization: Its amortization series, already read by the caller.

    Returns:
        The block's HTML.
    """
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
    rate = _effective_rate_text(credit)
    unrepaid = (
        f"<p class='sub'>{_fmt(credit.unrepaid_principal_in_euro)} EUR of the principal falls due "
        "after the observation horizon and is therefore not in the bar.</p>"
        if abs(credit.unrepaid_principal_in_euro) > 0.005 else ""
    )
    return (
        f"<p class='sub'>Effective annual rate: <b>{_esc(rate)}</b>.</p>" + unrepaid
        + _cost_of_credit_svg(credit)
        + _table(
            ["Principal", "Interest", "Fees", "Repayment grant", "Total repaid", "Effective rate"],
            [[
                _fmt(credit.principal_in_euro), _fmt(credit.interest_in_euro), _fmt(credit.fees_in_euro),
                _fmt(credit.grants_in_euro), _fmt(credit.total_repaid_in_euro), _esc(rate),
            ]],
        )
        + balance_svg
    )


def _component_events_section_html(result: LifecycleCostResult, context: _ChapterContext) -> str:
    """Return the lifetimes strip: when each component is bought, replaced and written down.

    A residual marker on a row with no purchase before it is visibly a defect.

    Args:
        result: The perspective whose components are drawn.
        context: The chapter this section is being rendered into.

    Returns:
        The section, or the empty string for an evaluation without component subjects.
    """
    rows = views.component_event_strip(result)
    if not rows:
        return context.skip(
            ReportSections.LIFETIMES,
            f"Perspective {result.perspective_id!r} has no component subjects to put on a row "
            "(a carriers-only evaluation).",
        )
    horizon = result.parameters.observation_period_in_years
    return (
        _section_open(ReportSections.LIFETIMES, context, result.perspective_id)
        + _explanation_html(ReportSections.LIFETIMES, context)
        + _gantt_svg(_event_strip_rows(rows), horizon) + "</section>"
    )


def _comparison_bridge_section_html(
    reference: LifecycleCostResult,
    variant: LifecycleCostResult,
    comparison: VariantComparison,
    context: _ChapterContext,
) -> str:
    """Return the NPV bridge: why the variant's NPV differs from the reference's, by cost group.

    The net figure printed is the comparison's own `npv_delta_in_euro`, not a recomputed difference; the steps must sum
    to it.

    Args:
        reference: The baseline result the comparison was computed against.
        variant: The result being compared to it.
        comparison: The comparison itself, which publishes the net difference.
        context: The chapter this section is being rendered into.

    Returns:
        The section, with the two anchor bands and the deltas between them.
    """
    steps = views.comparison_bridge(reference, variant, PresentationStyle.CATEGORY_TO_GROUP)
    bridge_steps = [
        (PresentationStyle.DISPLAY_GROUPS[step.group][0], step.delta_in_euro, f"var(--g{step.group})")
        for step in steps
        if abs(step.delta_in_euro) >= 0.005
    ]
    delta = comparison.npv_delta_in_euro.best_estimate
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
        "(variant minus reference, best-estimate scenario).</p></section>"
    )


def _wealth_benchmark_section_html(
    reference: LifecycleCostResult,
    variant: LifecycleCostResult,
    context: _ChapterContext,
) -> str:
    """Return the bank benchmark: renovating against banking the money at 1-10 % interest.

    The upper panel shows the advantage over time for each rate, with the evaluation's own discount rate drawn heavier
    and banded; the lower panel shows the terminal advantage against the rate, whose zero crossings are the break-even
    rates the caption states.

    Args:
        reference: The baseline result the differential flows are taken against.
        variant: The result being compared to it; the section is headed with its perspective.
        context: The chapter this section is being rendered into.

    Returns:
        The section, with both panels.
    """
    benchmark = views.wealth_benchmark(reference, variant)
    years = list(range(len(benchmark.differential_flow_in_euro)))
    if len(benchmark.rates) > len(SequentialRamp.LIGHT):
        raise CostDataError(
            f"The benchmark carries {len(benchmark.rates)} rates but the sequential ramp declares "
            f"{len(SequentialRamp.LIGHT)} steps, so the last lines of the fan would be drawn in a "
            "CSS variable the stylesheet does not define and would come out black. Extend "
            "`presentation_style.SequentialRamp` alongside `views.WealthBenchmarkGrid.RATES`."
        )
    # The ten rates are one ordered quantity, so they use the sequential ramp the stylesheet
    # declares from `SequentialRamp` (1 % lightest, 10 % darkest) rather than the eight-colour
    # group palette, which would repeat colours. The variables also re-resolve in dark mode.
    series: List[Tuple[str, List[Tuple[float, float]], str, float, str]] = [
        (f"{rate:.0%}", _points(years, benchmark.series_by_rate[rate]), f"var(--ramp{index})",
         1.0, "3 3")
        for index, rate in enumerate(benchmark.rates)
    ]
    parameter = benchmark.parameter_series_by_slot
    series.append((
        f"parameter rate {benchmark.parameter_rate:.1%}",
        _points(years, parameter[Slot.BEST_ESTIMATE]), "var(--ink-1)", 2.4, "",
    ))
    trajectories = _xy_lines_svg(
        series=series,
        bands=[(_points(years, parameter[Slot.LOW]), _points(years, parameter[Slot.HIGH]),
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


def _points(years: List[int], values: List[float]) -> List[Tuple[float, float]]:
    """Return a year-indexed series as the `(x, y)` point list `charts._xy_lines_svg` takes.

    A length mismatch is refused instead of truncated by `zip`, which would draw a curve ending at the wrong year.

    Args:
        years: The x values, in plotting order.
        values: The y values, index-aligned with `years`.

    Returns:
        The points, one per year.

    Raises:
        CostDataError: If the two lists are not the same length.
    """
    if len(years) != len(values):
        raise CostDataError(
            f"A chart series carries {len(values)} value(s) for {len(years)} year(s); plotting "
            "the overlap would draw a curve that ends at the wrong year without looking like it "
            "does. Both come from the perspective's observation period, so they cannot "
            "legitimately differ."
        )
    return [(float(year), value) for year, value in zip(years, values)]


def _treemap_section_html(result: LifecycleCostResult, context: _ChapterContext) -> str:
    """Return the cost structure: treemaps of lifetime cost, gross and net of credits, side by side.

    A treemap cannot draw a credit, so both bases are shown: the gross panel states the credits it leaves out, the net
    panel the subjects clamped to zero because their credits exceed their costs. A basis with no positive area is
    replaced by a sentence saying so.

    Args:
        result: The perspective whose composition is drawn.
        context: The chapter this section is being rendered into.

    Returns:
        The section, or the empty string when neither basis has a positive area.
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
        if not drawable:
            captions.append(
                f"There is no {headline} panel: on this basis every subject's credits reach its "
                "costs, so no tile has an area to draw."
            )
            continue
        total = sum(tile.area_in_euro for tile in drawable)
        panels.append(
            f"<div><p class='sub'><b>{headline}: {_fmt(total)} EUR</b> "
            f"(net NPV {_fmt(tiles.net_npv_in_euro)} EUR)</p>"
            + _treemap_svg(
                [
                    (f"{PresentationStyle.DISPLAY_GROUPS[tile.group][0]} - {tile.subject}",
                     tile.area_in_euro, f"var(--g{tile.group})")
                    for tile in drawable
                ],
                # Two panels across the chart column, with the flex gap between them.
                width=_ChartGeometry.WIDTH // 2 - 20,
            )
            + "</div>"
        )
        captions.append(treemap_disclosure(tiles, basis))
    if not panels:
        return context.skip(
            ReportSections.COST_STRUCTURE,
            f"Neither basis has a positive area for perspective {result.perspective_id!r}: every "
            "subject's credits reach its costs, and a treemap has no negative tile to draw them "
            "with.",
        )
    return (
        _section_open(ReportSections.COST_STRUCTURE, context, result.perspective_id)
        + _explanation_html(ReportSections.COST_STRUCTURE, context)
        + f"<div style='display:flex;gap:14px;flex-wrap:wrap'>{''.join(panels)}</div>"
        + f"<p class='sub'>{' '.join(_esc(caption) for caption in captions)}</p></section>"
    )


def _subject_flows_section_html(result: LifecycleCostResult, context: _ChapterContext) -> str:
    """Return the cost shapes: which subject causes which kind of cost, with cost and credit kept apart.

    E.g. a gas boiler is cheap to install and expensive to run, a heat pump the reverse. Cost and credit ribbons are
    separate flows, so a subject's node is taller than its net figure; the caption works this through on the widest
    block.

    Args:
        result: The perspective whose flows are drawn.
        context: The chapter this section is being rendered into.

    Returns:
        The section, or the empty string for a single-subject perspective.
    """
    flows = views.subject_category_flows(result, PresentationStyle.CATEGORY_TO_GROUP)
    subjects = sorted({flow.subject for flow in flows})
    if len(subjects) < 2:
        return context.skip(
            ReportSections.COST_SHAPES,
            f"Perspective {result.perspective_id!r} has {len(subjects)} subject(s), so there is "
            "no cross-link story to draw; the cost structure treemap covers that case.",
        )
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
    # Every node states the amounts its extent is made of, every ribbon its exact euros.
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
    """Return the amount lines and hover texts for the cost-shapes Sankey nodes.

    A subject node states "costs X | credits -Y" (the credit half omitted when zero), a group node its signed total,
    negative for credit groups. The compact form is the node's total, used where the node is too short for two lines;
    the tooltip always carries the full split to the cent.

    Args:
        margins: The per-node sums `views.subject_flow_margins` returned.
        labels: The visible label per node key, which the group tooltips quote.
        groups_cost: The display groups carrying cost ribbons.
        groups_credit: The display groups carrying credit ribbons.

    Returns:
        The `(full, compact)` amount line per node and the hover text per node, as `charts._sankey_svg` takes them.
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
    for groups, suffix, is_credit in ((groups_cost, "0", False), (groups_credit, "1", True)):
        for group in groups:
            node = f"grp:{group}:{suffix}"
            total = margins.signed_total_by_group.get((group, is_credit), 0.0)
            sublabels[node] = (_fmt(total), _fmt(total))
            tooltips[node] = f"{labels[node]}: {total:,.2f} EUR"
    return sublabels, tooltips


def _subject_flow_caption(margins: views.SubjectFlowMargins) -> str:
    """Return the caption working through the cost-shapes arithmetic on the run's widest block.

    States that the block's solid side is the component breakdown's cost column and its dashed side the credits, with
    this run's numbers, so a reader can match them against the breakdown table.

    Args:
        margins: The per-node sums `views.subject_flow_margins` returned.

    Returns:
        The caption paragraph, or the empty string when there is no subject.
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
    """Return the energy balance: where the house's electricity came from and went, in year-1 kWh.

    Sources on the left, the house's electricity bus in the middle, sinks on the right, with money annotated only on
    the two nodes that cross a billing boundary. Energy the drawn terminals do not account for goes to an explicit
    `losses / unattributed` node.

    Args:
        result: The perspective whose year-1 balance is drawn.
        context: The chapter this section is being rendered into.

    Returns:
        The section, or the empty string when the result carries fewer than two device flows.
    """
    if not views.has_energy_balance(result):
        return context.skip(
            ReportSections.ENERGY_BALANCE,
            f"Perspective {result.perspective_id!r} carries fewer than two device energy flows "
            "(a run from before the field existed, or a component set whose classes the adapter "
            "does not know); the meter's own grid import and export are not device flows.",
        )
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
            labels[f"{prefix}:{node.label}"] = f"{node.label} {node.quantity_in_kwh:,.0f} kWh/a{money}"
    columns = [
        [f"src:{node.label}" for node in flows.sources],
        [bus],
        [f"snk:{node.label}" for node in flows.sinks],
    ]
    return (
        _section_open(ReportSections.ENERGY_BALANCE, context, result.perspective_id)
        + _explanation_html(ReportSections.ENERGY_BALANCE, context)
        + _energy_balance_caption(flows)
        + _sankey_svg(columns, ribbons, labels, height=320) + "</section>"
    )


def _energy_balance_caption(flows: views.EnergyBalanceFlows) -> str:
    """Return the derived figures of the energy balance and everything the diagram could not place.

    States self-consumption, self-sufficiency and the battery's round-trip loss, the residual terminal, and the energy
    in `unattributed_roles_in_kwh` whose role name has no side of the bus and is therefore not drawn.

    Args:
        flows: The balance `views.energy_balance_flows` returned.

    Returns:
        The caption paragraph, or the empty string when there is nothing to state.
    """
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
    unplaced = flows.unattributed_roles_in_kwh
    unplaced_prose = (
        f" A further {sum(unplaced.values()):,.0f} kWh/a carry role name(s) this balance has no "
        f"side for ({', '.join(sorted(unplaced))}) and are outside the diagram altogether, not "
        "inside the node above."
        if unplaced else ""
    )
    stated = "; ".join(shares)
    text = (f"{stated}." if stated else "") + battery + residual_prose + unplaced_prose
    return f"<p class='sub'>{_esc(text.lstrip())}</p>" if text.strip() else ""


def _monthly_burden_section_html(result: LifecycleCostResult, context: _ChapterContext) -> str:
    """Return the monthly burden: what this costs per month, year by year.

    Capital events are excluded and replacements are smoothed into a reserve line; the authored prose states that
    scope. A perspective with no recurring cost in any month of any world is skipped (e.g. one that books only the
    year-0 investment and its residual credit); the reserve alone does not count.

    Args:
        result: The perspective whose recurring burden is drawn.
        context: The chapter this section is being rendered into.

    Returns:
        The section, or the empty string when the perspective books no recurring month at all.
    """
    burden = views.monthly_burden_series(result)
    recurring = any(
        value.best_estimate or value.minimum or value.maximum for value in burden.series
    )
    if not recurring:
        return context.skip(
            ReportSections.MONTHLY_BURDEN,
            f"Perspective {result.perspective_id!r} books no recurring cost in any month of any "
            "of the three worlds, so there is no monthly burden to draw — everything it carries "
            "is capital, which the investment and cash-flow sections show.",
        )
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
        + _monthly_burden_svg(result, burden) + "</section>"
    )


def _year_spans(intervals: List[Tuple[int, int]]) -> str:
    """Name a list of year runs as a caption would read them, e.g. "years 3-7 and 12-14".

    `views.asset_debt_series` reports every maximal run of negative equity; a one-year run is named as a single year.

    Args:
        intervals: The (first year, last year) pairs, in order; the caller ensures it is non-empty.

    Returns:
        The runs as plain text; the caller escapes it.
    """
    spans = [
        f"year {start}" if start == end else f"years {start}-{end}" for start, end in intervals
    ]
    if len(spans) == 1:
        return spans[0]
    return ", ".join(spans[:-1]) + " and " + spans[-1]


def _equity_section_html(matrix: EvaluationMatrix, context: _ChapterContext) -> str:
    """Return the equity build-up: asset book value against outstanding debt.

    The book value uses the same depreciation basis as the residual calculator, so a defect shows along the whole
    curve. The first financed perspective of the chapter is drawn, like the loan and cost-of-credit sections; its
    amortization is passed on to `views.asset_debt_series`.

    Args:
        matrix: The chapter's perspectives; the first financed one is drawn.
        context: The chapter this section is being rendered into.

    Returns:
        The section, or the empty string when this chapter has no financed perspective.
    """
    result = _first_result_where(
        matrix, lambda candidate: views.loan_amortization_series(candidate).has_flows()
    )
    if result is None:
        return context.skip(
            ReportSections.EQUITY_BUILD_UP,
            "No perspective of this chapter's story is financed, so there is no debt line and "
            "no gap story to draw.",
        )
    amortization = views.loan_amortization_series(result)
    series = views.asset_debt_series(result, amortization)
    years = list(range(len(series.book_value_in_euro)))
    underwater = (
        f"Equity is negative in {_year_spans(series.underwater_intervals)}: the debt exceeds the "
        "book value there, which is what a lender looks for."
        if series.underwater_intervals else
        "Equity stays positive over the whole horizon."
    )
    chart = _xy_lines_svg(
        series=[
            ("asset book value", _points(years, series.book_value_in_euro), "var(--g4)", 2.2, ""),
            ("outstanding debt", _points(years, series.debt_in_euro), "var(--g0)", 2.2, ""),
            ("equity", _points(years, series.equity_in_euro), "var(--g1)", 1.4, "5 4"),
        ],
        bands=[(_points(years, series.debt_in_euro), _points(years, series.book_value_in_euro),
                "var(--g1)")],
        y_label="EUR (nominal) - book value, debt and the equity between them",
        height=240,
    )
    return (
        _section_open(ReportSections.EQUITY_BUILD_UP, context, result.perspective_id)
        + _explanation_html(ReportSections.EQUITY_BUILD_UP, context)
        + "<p class='sub'>At the horizon the book value equals the residual value credited in "
        f"the results ({_fmt(series.residual_credit_in_euro)} EUR). {_esc(underwater)}</p>"
        + chart + "</section>"
    )
