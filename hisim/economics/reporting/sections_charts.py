"""Report sections of the visualization set — the charts that answer a reader's own questions.

One function per chart of the visualization extension: the lifecycle overview the report opens
with, how year 0 is funded, who pays whom (the actor Sankey and the landlord's income statement
drawn as one), the cash curve, the loan and what credit costs, the household's energy balance,
the uncertainty drivers, the cost structure and the cost shapes, the equity build-up, the monthly
burden, the component lifetimes, the NPV bridge and the fixed-interest benchmark. They live
beside `sections.py` rather than inside it because the two halves are already ~800 lines each and
answer different questions — `sections.py` walks the calculation chain a reviewer checks, this
module answers what a reader came for.

Like every other section they open with `scaffold._explanation_html`, i.e. with the four
authored parts held in `report_prose.ReportProse` (rule 2.6): the report has to be understandable
by a reader who has never seen a Sankey or a bridge waterfall, and because the explanations are
part of the golden-tested HTML they are reviewed and frozen like any number. What stays in the
functions below is the run-specific half a golden-stable text cannot carry: the captions and
annotations that state this run's amounts and perspectives. A section that cannot be drawn at
all records its reason on the `_ChapterContext` and returns nothing; the document prints those
reasons under its table of contents, because a reader who notices a missing section is never the
person reading the log.

The per-party statements (owner, tenant, society), the chapter split they make possible and the
assumptions section are the one part of the set still to land; `scaffold.ReportSections.ORDER`
already names them, so an entry there without a builder here is expected.
"""


from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from hisim.economics import views
# The refusal type of the view layer, reached through `views` because that is the surface
# seam 4 opens to presentation (`tests/test_economics_import_lint.py`): a report that cannot
# tell which perspective it is drawing refuses with the same error a view would.
from hisim.economics.views import CostDataError
from hisim.economics.presentation_style import PresentationStyle, group_of
from hisim.economics.results import EvaluationMatrix, LifecycleCostResult, VariantComparison
from hisim.economics.timeline import CostCategory
from hisim.economics.uncertainty import Slot


from hisim.economics.reporting.summary import _band_str, _fmt
from hisim.economics.reporting.charts import (
    _attribution_tornado_svg,
    _bridge_svg,
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
#: amount in euro or None)`. Named here because two sections build these rows — the lifetimes
#: strip and the lifecycle overview — and the tuple is long enough that spelling it twice is how
#: the two drift apart.
_GanttRow = Tuple[
    str,
    List[Tuple[int, Optional[int], str]],
    List[Tuple[int, str, Optional[float]]],
    str,
]


def _first_result_where(
    matrix: EvaluationMatrix, predicate: Callable[[LifecycleCostResult], bool]
) -> Optional[LifecycleCostResult]:
    """The first perspective of the matrix that satisfies a predicate, or None.

    Most single-result sections of this report render the matrix's *first* perspective, which is
    the reference view of the run. Several charts of the visualization set cannot: the actor
    Sankey needs a perspective that actually has two actors, the loan panel and the cost of
    credit need a financed one. Rendering them for the first perspective would drop them from
    every run whose first row happens to be an unallocated cash view — even though the run
    evaluated a landlord and a financed row right below it. Picking the first perspective that
    *has* the data keeps the section, and the heading names which perspective it is showing, so
    nothing is ambiguous.

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
    """Whether year 0 carries support or a loan — the cheap predicate behind the funding chart.

    Deliberately a scan of the timeline rather than a call to a view that assembles the funding
    picture: such a view validates and raises, and choosing which perspective to draw must not
    depend on a validation that is only meaningful once the perspective has been chosen. It
    belongs with `_first_result_where`, the other half of the same "pick a perspective that has
    the data" pattern.

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
    """At a glance: assets, financing, support and milestones on one year axis.

    Answers "what happens when, over the life of this renovation" — what was built, how it is
    financed, what support and obligations run alongside it, and when the project pays off. It is
    the report's first figure because it is the only one that shows the whole story at once; every
    lane is a compressed restatement of a chart further down, which is also what makes it safe, as
    it introduces no new numbers and only a shared axis.

    Args:
        result: The perspective whose lanes are drawn.
        comparison: The variant comparison whose band crossings carry the payback milestone, or
            None — payback is a statement about a difference between two variants, so without one
            the milestone is absent and the caption says why.
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
    rows.extend(_asset_lane_rows(lanes.assets))
    # An empty lane is stated beside the chart rather than in the log, for the same reason a
    # skipped section is stated under the contents: the reader who notices the gap is looking at
    # the drawing, not at the process output. It is not a `context.skip` because the section
    # itself *is* drawn — one row of it is missing, which the reader can only see here.
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


def _asset_lane_rows(assets: List[views.EventStripRow]) -> List[_GanttRow]:
    """One Gantt lane per asset of the overview, residual marker included.

    The same rows the lifetimes section draws, which is the point of the overview: it restates
    charts that exist in full elsewhere on a shared axis, so a lane that disagrees with the strip
    further down would be a defect rather than a second opinion. The residual write-down is
    appended as an event because the view carries it beside the events rather than among them —
    it is not something that was *done* to the asset, it is what the horizon did to its book
    value.

    Args:
        assets: The asset rows `views.lifecycle_lanes` collected.

    Returns:
        One `(label, spans, events, colour)` lane per asset, in the view's order.
    """
    rows: List[_GanttRow] = []
    for asset in assets:
        events: List[Tuple[int, str, Optional[float]]] = [
            (event.year, event.kind.value, event.amount_in_euro) for event in asset.events
        ]
        if asset.residual is not None:
            events.append((asset.residual.year, "residual", asset.residual.amount_in_euro))
        rows.append((
            asset.subject,
            [(span.start_year, span.end_year, "in service") for span in asset.spans],
            events,
            "var(--g4)",
        ))
    return rows


def _sources_uses_section_html(result: LifecycleCostResult, context: _ChapterContext) -> str:
    """Funding: how year 0 is paid for and what it buys, balanced to the euro.

    Answers the first question a bank or a funding advisor asks, and it is the one place the
    subsidy scheme id earns its keep: "state -> KfW 261 -> heat pump" reads very differently from
    one grey subsidies node. The two column totals are equal by construction — the view validates
    the double entry before this runs — so the caption can state the balance as a fact.

    Args:
        result: The perspective whose year 0 is stated; `_has_year_zero_funding` is the caller's
            skip check.
        context: The chapter this section is being rendered into.

    Returns:
        The section, or the empty string when year 0 is funded entirely from own capital — the
        investment waterfall shows that case better than a one-ribbon Sankey would.
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


def _liquidity_section_html(
    result: LifecycleCostResult,
    comparison: Optional[VariantComparison],
    context: _ChapterContext,
) -> str:
    """The cash curve: the cumulative cash position as a fan, nominal above and discounted below.

    Answers "how deep does this go, and when do I get it back": the running out-of-pocket
    position with its uncertainty band, so the worst year and the payback both read as ranges
    rather than as single numbers.

    Without a reference variant there is no payback question at all, and the lower panel shows
    the cumulative discounted cost instead — whose end point is the reported NPV, which is what
    makes the panel worth keeping in a single-variant run.

    **One perspective, one story.** With a comparison the whole section — the heading, both
    panels and the payback sentence — is one perspective's, and it is the comparison's own: a
    `VariantComparison` is computed for exactly one perspective, so a payback sentence drawn from
    it under some other perspective's cash curve is two runs' figures presented as one. The
    section refuses that combination rather than rendering it, because it is invisible in the
    output: both halves are plausible, they just belong to different parties.

    Args:
        result: The perspective whose liquidity is drawn; with a comparison it must be the one
            the comparison was computed for.
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
        payback_note = _payback_interval_prose(
            crossings["low"], crossings["best_estimate"], crossings["high"]
        )
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
    """One slot's cumulative savings curve, named in the refusal when it is not there.

    The three curves are the whole lower panel: the band is drawn from two of them and the
    payback sentence reads a crossing off each. A missing slot used to be a `KeyError` from a
    subscript deep inside the section, or — worse, had anyone reached for `.get` — a silently
    flat curve that reads as "no savings in that world". Naming the slot and the comparison turns
    it into a sentence a reader of the traceback can act on.

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


def _payback_interval_prose(
    low: Optional[int], best_estimate: Optional[int], high: Optional[int]
) -> str:
    """The payback sentence of the cash curve's lower panel, with the open end spelled out.

    Says "never within the horizon" in words rather than omitting the statement, which is the
    failure mode this wording exists to prevent: an absent annotation reads as "did not pay back"
    to one reader and as "not computed" to another. All three worlds are consulted, because a
    sentence built from two of them cannot say where the answer actually lands.

    **Which world is which.** Savings are reference minus variant, so the slot with the *larger*
    savings pays back *earlier*: the HIGH savings slot is the optimistic world and the LOW one
    the pessimistic. The sentence used to have those two the other way round, which inverted the
    conclusion — a reader was told the pessimistic case paid back first.

    Args:
        low: The zero-crossing year of the LOW savings curve — the pessimistic world — or None.
        best_estimate: The crossing of the central world, or None.
        high: The crossing of the HIGH savings curve — the optimistic world — or None.

    Returns:
        One sentence naming the interval, or saying that there is none.
    """
    if low is None and best_estimate is None and high is None:
        return "The investment does not pay back within the horizon in any of the three worlds."
    if low is None and best_estimate is None:
        return (
            f"Payback lands in year {high} in the optimistic world only; in the central and the "
            "pessimistic world the curve never reaches zero within the horizon."
        )
    return (
        f"Payback lands in {_payback_year_prose(best_estimate)} in the central world, between "
        f"{_payback_year_prose(high)} (optimistic) and {_payback_year_prose(low)} (pessimistic)."
    )


def _payback_year_prose(year: Optional[int]) -> str:
    """One world's crossing as it is read aloud: "year 12", or that it never crossed."""
    return f"year {year}" if year is not None else "never within the horizon"


def _uncertainty_section_html(result: LifecycleCostResult, context: _ChapterContext) -> str:
    """The uncertainty drivers: which subjects make the total NPV band as wide as it is.

    Answers "which inputs are worth arguing about". It is an *attribution* of the existing band,
    not a sensitivity analysis — nothing is re-evaluated — and the prose says so, because the
    chart looks exactly like an OAT tornado and would otherwise be read as one.

    Args:
        result: The perspective whose band is attributed.
        context: The chapter this section is being rendered into.

    Returns:
        The section, or the empty string when the total NPV band is degenerate and there is no
        width to attribute.
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
    """Who pays whom over the whole horizon, one column per party.

    Answers the question a landlord/tenant case makes unavoidable: the levy, the subsidy and the
    energy bills all cross actor boundaries, and until now that structure was visible only as
    rows of a pivot table.

    Since Q23 every internal party has a column of its own, ordered by `actor_columns` so that a
    payment between two of them — the §559e levy — runs left to right like every other ribbon
    instead of looping out of a shared column and back into it.

    Args:
        result: The perspective whose flows are drawn; it needs at least two actor nodes.
        context: The chapter this section is being rendered into.

    Returns:
        The section, or the empty string for a perspective with a single actor — there is no
        who-pays-whom story in a view where one party pays everything.
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
        """The node's key, namespaced by side so an actor and an external node never collide."""
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
        + "<p class='sub'>This chart shows the best-estimate scenario only; the band of the grand "
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

    Args:
        result: The landlord perspective to state.
        context: The chapter this section is being rendered into.

    Returns:
        The section, or the empty string for a perspective that books no flows at all.
    """
    statement = views.landlord_statement(result)
    if not statement.cash_lines and not statement.accounting_lines:
        return context.skip(
            ReportSections.LANDLORD_STATEMENT,
            f"Perspective {result.perspective_id!r} books no flows at all, so there is no "
            "landlord business case to state.",
        )
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
    return (
        f"<p class='sub'>{levy_note} Cash flows come to "
        f"<b>{_fmt(statement.cash_subtotal_in_euro)} EUR</b> and accounting credits to "
        f"<b>{_fmt(statement.accounting_subtotal_in_euro)} EUR</b> in present value; together they "
        f"are the net position of {_esc(_band_str(statement.net_position_band))}. A negative net "
        "position is an advantage on the stated basis, but only the cash half of it ever reaches "
        "an account.</p>"
    )


def _levy_world_verdicts(levy: Any) -> str:
    """Which mechanism set the levy in each of the three worlds (owner decision Q26 F5).

    The caps are applied per slot, so "the cap binds" is a best-estimate-slot statement that can
    be false in the cheap world and true in the expensive one — two economically different answers
    to the question a landlord actually asks, which is whether spending more would raise the rent.
    The ruleset records the verdict per world; this states them, and collapses them into one
    sentence when all three agree, because three identical clauses read as a defect.

    Args:
        levy: The `results.ModernizationLevySummary` of the perspective; its
            `binding_mechanism_by_slot` is empty for a result stored before the verdicts were
            recorded, and nothing is added. Untyped because `results` publishes it only through
            the statement this caption is built from.

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
    """The debt service per year, one block per financed perspective.

    Answers "what does the loan actually look like over time" — falling interest against rising
    principal for an annuity, a bullet spike for interest-only. It is its own section rather than
    a chart buried in the cash-flow timeline because the loan is a separate story from the
    timeline it replaces, and because the cost-of-credit section beside it is the second half of
    the same question.

    Args:
        matrix: The perspectives to draw; one block each, in matrix order.
        context: The chapter this section is being rendered into.

    Returns:
        The section, or the empty string when nobody in this bundle borrows — a run of cash
        purchases produces no section at all rather than an empty box.
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
            "No evaluated perspective carries loan flows: every purchase in this bundle is a "
            "cash purchase.",
        )
    return (
        _section_open(ReportSections.LOAN, context)
        + _explanation_html(ReportSections.LOAN, context)
        + "".join(blocks) + "</section>"
    )


def _effective_rate_text(credit: views.TotalCostOfCredit) -> str:
    """The Effektivzins as the panel prints it, including why there is none.

    A loan whose flows do not define a rate is not the same statement as a loan the report forgot
    to price, and "n/a" alone leaves the reader unable to tell the two apart.
    `TotalCostOfCredit.effective_annual_rate_note` carries the short reason — a term reaching past
    the observation horizon, a repayment grant larger than the debt service — and it is non-empty
    exactly when the rate is None and the view knows why, so it is printed in brackets beside the
    "n/a" rather than dropped.

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
    """The loan's companion: the total cost of credit and the effective annual rate.

    Answers the question every loan document answers on its first page — "what does borrowing
    this money actually cost me" — from the same amortization series the debt-service chart in
    the loan section stacks.

    One block per financed perspective, over exactly the set the loan section draws, and each
    block names the perspective it belongs to. It used to disclose the *first* financed
    perspective only, which is a silent half-answer in the common bundle where a gross and a net
    view are both financed: the reader saw one effective rate, with nothing to say that a second
    one existed and differed.

    Args:
        matrix: The perspectives to disclose; the financed ones get a block, in matrix order.
        context: The chapter this section is being rendered into.

    Returns:
        The section, or the empty string when nobody in this bundle borrows.
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
            "No evaluated perspective carries loan flows, so there is no credit to price.",
        )
    return (
        _section_open(ReportSections.COST_OF_CREDIT, context)
        + _explanation_html(ReportSections.COST_OF_CREDIT, context)
        + "".join(blocks) + "</section>"
    )


def _cost_of_credit_block_html(
    result: LifecycleCostResult, amortization: views.LoanAmortization
) -> str:
    """One financed perspective's disclosure: the rate, the stacked bar, the table, the balance.

    Split from the section itself because the section is now a loop over the financed
    perspectives and the block is what one iteration of it renders — a per-perspective block that
    is half section and half chart is exactly the shape that grows a second, divergent copy.

    Args:
        result: The financed perspective to disclose.
        amortization: Its amortization series, already read by the caller to select it.

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
    """The lifetimes strip: when each component is bought, replaced and written down.

    Answers the *schedule* question per device, and makes the residual-value rule auditable: a
    residual marker on a row with no purchase before it is a defect you can see.

    Args:
        result: The perspective whose components are stripped out.
        context: The chapter this section is being rendered into.

    Returns:
        The section, or the empty string for a carriers-only evaluation with no component
        subject to put on a row.
    """
    rows = views.component_event_strip(result)
    if not rows:
        return context.skip(
            ReportSections.LIFETIMES,
            f"Perspective {result.perspective_id!r} has no component subjects to put on a row "
            "(a carriers-only evaluation).",
        )
    horizon = result.parameters.observation_period_in_years
    gantt_rows: List[_GanttRow] = []
    for row in rows:
        events: List[Tuple[int, str, Optional[float]]] = [
            (event.year, event.kind.value, event.amount_in_euro) for event in row.events
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


def _comparison_bridge_section_html(
    reference: LifecycleCostResult,
    variant: LifecycleCostResult,
    comparison: VariantComparison,
    context: _ChapterContext,
) -> str:
    """The NPV bridge: why the variant's NPV differs from the reference's, by cost group.

    Answers the decision question one level deeper than the total does: not "is it cheaper" but
    "what makes it cheaper", which is what a reader needs to judge whether the answer rests on
    one assumption or on many.

    The net figure under the chart is the comparison's own `npv_delta_in_euro`, printed rather
    than recomputed: the report used to subtract the two totals here, which is the engine's
    arithmetic done a second time in the renderer (seam 4) and free to disagree with the delta
    every other section of the report quotes. The steps still have to *sum* to it — that is the
    reconciliation the bridge exists for, and reading two published numbers to check they agree
    is not computing either of them.

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
    """The bank benchmark: renovating against banking the money at 1-10 % interest.

    Answers the question every homeowner actually asks, and it turns the discount rate from an
    opaque parameter into something a reader can interrogate: the rate at which the terminal
    advantage crosses zero is the return the renovation has to beat. The upper panel is the whole
    rate fan over time with the evaluation's own parameter rate drawn heavier and banded; the
    lower one is the terminal advantage against the rate, whose zero crossings are the break-even
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
    ramp = PresentationStyle.GROUP_COLORS_LIGHT
    series: List[Tuple[str, List[Tuple[float, float]], str, float, str]] = [
        (f"{rate:.0%}", _points(years, benchmark.series_by_rate[rate]), ramp[index % len(ramp)],
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
    """A year-indexed series as the `(x, y)` point list `charts._xy_lines_svg` takes.

    The benchmark draws twelve series and two band edges off the same year axis, and writing the
    zip out at each of them is how one of them ends up plotted against a different axis than its
    neighbours.

    Args:
        years: The x values, in plotting order.
        values: The y values, index-aligned with `years`.

    Returns:
        The points, truncated to the shorter of the two lists.
    """
    return [(float(year), value) for year, value in zip(years, values)]


def _treemap_section_html(result: LifecycleCostResult, context: _ChapterContext) -> str:
    """Cost structure: the composition of lifetime cost, gross and net of credits side by side.

    Answers "what is this made of" in an area encoding that survives a glance. Both bases are
    rendered because a treemap cannot draw a credit and neither variant alone is the whole truth
    (owner decision Q11): the gross panel states the credits it leaves out, the net panel states
    the subjects whose credits exceeded their costs and were clamped to nothing.

    Args:
        result: The perspective whose composition is drawn.
        context: The chapter this section is being rendered into.

    Returns:
        The section, with both panels and the disclosure each of them owes.
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
        captions.append(_treemap_caption(tiles, basis))
    return (
        _section_open(ReportSections.COST_STRUCTURE, context, result.perspective_id)
        + _explanation_html(ReportSections.COST_STRUCTURE, context)
        + f"<div style='display:flex;gap:14px;flex-wrap:wrap'>{''.join(panels)}</div>"
        + f"<p class='sub'>{' '.join(_esc(caption) for caption in captions)}</p></section>"
    )


def _treemap_caption(tiles: views.CostStructureTiles, basis: views.TileBasis) -> str:
    """What one treemap panel has to disclose about the areas it could not draw.

    A treemap has no negative area, so each basis hides something different and has to say what:
    the gross panel hides the credits, the net panel hides the subjects whose credits exceeded
    their costs and were clamped to zero. Naming the clamped subjects is the point — they are
    exactly the entries a reviewer should ask about, and a panel that merely came out smaller
    would not tell anyone which ones they are.

    Args:
        tiles: The tiles and disclosures `views.cost_structure_tiles` returned for this basis.
        basis: The `views.TileBasis` the panel was drawn on.

    Returns:
        The disclosure as plain text; the caller escapes it.
    """
    if basis == views.TileBasis.GROSS:
        return (
            f"The gross panel leaves out {_fmt(tiles.credit_total_in_euro)} EUR of credits "
            f"(support, feed-in revenue, residual value); {tiles.folded_tile_count} tile(s) "
            "below 1 % of the area were folded into an 'other' tile per group."
        )
    clamped = tiles.clamped_tiles()
    names = ", ".join(
        f"{tile.subject} ({_fmt(tile.clamped_from_in_euro or 0.0)} EUR)" for tile in clamped
    ) or "none"
    return (
        "The net panel applies each subject's credits to that subject's own cost tiles across all "
        "groups — a wall's subsidy shrinks the wall — and clamps at zero the subjects whose "
        f"credits exceed their costs, erasing {_fmt(tiles.clamped_total_in_euro)} EUR in "
        f"{len(clamped)} subject(s): {names}. Those are exactly the entries worth asking about."
    )


def _subject_flows_section_html(result: LifecycleCostResult, context: _ChapterContext) -> str:
    """Cost shapes: which subject causes which *kind* of cost, cost and credit kept apart.

    Answers the technology-comparison question a composition chart structurally hides: a gas
    boiler is cheap to install and expensive to run, a heat pump the reverse, and PV feeds revenue
    back. Cost and credit ribbons are separate flows rather than one netted number, which is why a
    subject's node is taller than the net figure the component breakdown publishes — the caption
    states that arithmetic on this run's widest block.

    Args:
        result: The perspective whose flows are drawn.
        context: The chapter this section is being rendered into.

    Returns:
        The section, or the empty string for a single-subject perspective — there is no
        cross-link story in a chart with one row, and the treemap covers that case.
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
    short for two lines; the tooltip always carries the full split to the cent, so a degraded
    label loses the breakdown and never the number.

    Args:
        margins: The per-node sums `views.subject_flow_margins` returned.
        labels: The visible label per node key, which the group tooltips quote.
        groups_cost: The display groups carrying cost ribbons.
        groups_credit: The display groups carrying credit ribbons.

    Returns:
        The `(full, compact)` amount line per node and the hover text per node, in the shapes
        `charts._sankey_svg` takes them.
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
    block is the two stacked — is authored prose and is printed above by `_explanation_html`. This
    caption is the arithmetic of that rule for *this* run, on the largest block, because the
    complaint the round is answering ("the chart does not add up") is only answered by numbers a
    reader can look up in the breakdown table two sections earlier.

    Args:
        margins: The per-node sums `views.subject_flow_margins` returned.

    Returns:
        The caption paragraph, or the empty string when there is no subject to work through.
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
    """Energy balance: where the house's electricity came from and where it went, in year-1 kWh.

    Answers the question an energy-system tool exists to answer, in the picture every PV dashboard
    already shows its owner: sources on the left, the house's electricity bus in the middle, sinks
    on the right, with money only as an annotation on the two nodes that cross a billing boundary.
    Whatever the drawn terminals do not account for is an explicit `losses / unattributed` node
    rather than a silent imbalance.

    Args:
        result: The perspective whose year-1 balance is drawn.
        context: The chapter this section is being rendered into.

    Returns:
        The section, or the empty string when the result carries fewer than two device flows — a
        meter talking to itself is not a balance.
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
    """The derived figures of the balance, plus everything the diagram could not place.

    Self-consumption and self-sufficiency are the two shares a PV owner reads a balance for, and
    the battery's round-trip loss is what makes it a lossy pass-through rather than a store. The
    two remainders are stated for the same reason they exist at all: the residual terminal is
    energy the drawn devices do not account for, and `unattributed_roles_in_kwh` is energy whose
    role name this reader's vocabulary cannot place at all. The latter has no side of the bus, so
    it cannot become a terminal without inventing a direction — but a diagram quietly missing it
    looks exactly like a diagram that never had it, which is why it is named here instead.

    Args:
        flows: The balance `views.energy_balance_flows` returned.

    Returns:
        The caption paragraph.
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
    return (
        f"<p class='sub'>{_esc('; '.join(shares))}.{_esc(battery)}{_esc(residual_prose)}"
        f"{_esc(unplaced_prose)}</p>"
    )


def _monthly_burden_section_html(result: LifecycleCostResult, context: _ChapterContext) -> str:
    """Monthly burden: what this costs per month, year by year — the unit households budget in.

    Answers the cash curve's question in the lay reader's unit. The definitional decision — every
    capital event excluded, the replacements smoothed into a reserve line — is stated in the
    authored prose, because a monthly figure whose scope is unstated is the easiest number in the
    report to misread.

    Args:
        result: The perspective whose recurring burden is drawn.
        context: The chapter this section is being rendered into.

    Returns:
        The section, or the empty string when the perspective books no month at all.
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


def _year_spans(intervals: List[Tuple[int, int]]) -> str:
    """Name a list of maximal year runs the way a caption would read them aloud.

    `views.asset_debt_series` reports every maximal run of negative equity, because a dip, a
    recovery and a second dip are three facts and one (first, last) pair would have claimed the
    recovery never happened. A single-year run is named as one year rather than as a range from
    itself to itself.

    Args:
        intervals: The (first year, last year) pairs, in order; assumed non-empty by the caller.

    Returns:
        The runs as plain text, e.g. "years 3-7 and 12-14"; the caller escapes it.
    """
    spans = [
        f"year {start}" if start == end else f"years {start}-{end}" for start, end in intervals
    ]
    if len(spans) == 1:
        return spans[0]
    return ", ".join(spans[:-1]) + " and " + spans[-1]


def _equity_section_html(result: LifecycleCostResult, context: _ChapterContext) -> str:
    """Equity build-up: asset book value against outstanding debt — the lender's solvency picture.

    Answers "how much of the installation do I own", and carries audit weight beyond that: the
    book-value line is the same depreciation basis the residual calculator uses, so a defect in it
    is visible along the whole curve rather than only at the horizon, where the two are checked
    against each other.

    Args:
        result: The perspective whose book value and debt are drawn.
        context: The chapter this section is being rendered into.

    Returns:
        The section, or the empty string for an unfinanced perspective — with no debt line there
        is no gap to draw and no solvency story to tell.
    """
    amortization = views.loan_amortization_series(result)
    if not amortization.has_flows():
        return context.skip(
            ReportSections.EQUITY_BUILD_UP,
            f"Perspective {result.perspective_id!r} is unfinanced, so there is no debt line and "
            "no gap story to draw.",
        )
    series = views.asset_debt_series(result)
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
