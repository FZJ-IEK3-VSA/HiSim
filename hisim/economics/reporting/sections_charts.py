"""Report sections of the visualization set — the charts that answer a reader's own questions.

One function per chart of the visualization extension that this slice carries: who pays whom
(the actor Sankey and the landlord's income statement drawn as one), the cash curve, the
uncertainty drivers, the NPV bridge, the loan and what credit costs, and the component
lifetimes. They live beside `sections.py` rather than inside it because the two halves are
already ~800 lines each and answer different questions — `sections.py` walks the calculation
chain a reviewer checks, this module answers what a reader came for.

Like every other section they open with `scaffold._explanation_html`, i.e. with the four
authored parts held in `report_prose.ReportProse` (rule 2.6): the report has to be understandable
by a reader who has never seen a Sankey or a bridge waterfall, and because the explanations are
part of the golden-tested HTML they are reviewed and frozen like any number. What stays in the
functions below is the run-specific half a golden-stable text cannot carry: the captions and
annotations that state this run's amounts, perspectives and skip reasons.

The sections of the second half of the set — the cost structure treemap, the funding and
energy-balance Sankeys, the monthly burden, the equity build-up, the wealth benchmark and the
lifecycle overview — arrive with the view functions they read, which are not in `views.py` yet.
"""


from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from hisim import log
from hisim.economics import views
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
    _sankey_svg,
    _table,
    _xy_lines_svg,
)
from hisim.economics.reporting.scaffold import (
    ReportSections,
    _ChapterContext,
    _explanation_html,
    _section_open,
)


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
    depend on a validation that is only meaningful once the perspective has been chosen.

    The funding section that selects its perspective with this arrives with the second half of
    the chart set; the predicate is here already because it belongs with `_first_result_where`,
    the other half of the same "pick a perspective that has the data" pattern, and
    `tests/test_economics_sections_a.py` pins it.

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

    Args:
        result: The perspective whose liquidity is drawn.
        comparison: The variant comparison whose savings curve carries the payback, or None.
        context: The chapter this section is being rendered into.

    Returns:
        The section, with both panels.
    """
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
        crossings = views.band_zero_crossings(curves)
        low, best_estimate, high = curves["low"], curves["best_estimate"], curves["high"]
        lower_label = "cumulative discounted savings [EUR] (reference - variant)"
        payback_note = _payback_interval_prose(crossings)
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


def _payback_interval_prose(crossings: Dict[Any, Optional[int]]) -> str:
    """The payback sentence of the cash curve's lower panel, with the open end spelled out.

    Says "no payback in the pessimistic world within the horizon" in words rather than omitting
    the statement, which is the failure mode this wording exists to prevent: an absent annotation
    reads as "did not pay back" to one reader and as "not computed" to another.

    Args:
        crossings: The zero-crossing year per slot as `views.band_zero_crossings` returns it,
            None meaning "never within the horizon".

    Returns:
        One sentence naming the interval, or saying that there is none.
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
        log.information(
            f"Actor-flow Sankey skipped for perspective {result.perspective_id!r}: it has "
            f"{len(matrix.actors)} actor node(s), so there is no who-pays-whom story to draw."
        )
        return ""
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


def _cost_of_credit_section_html(result: LifecycleCostResult, context: _ChapterContext) -> str:
    """The loan's companion: the total cost of credit and the effective annual rate.

    Answers the question every loan document answers on its first page — "what does borrowing
    this money actually cost me" — from the same amortization series the debt-service chart in
    the loan section stacks.

    Args:
        result: The financed perspective to disclose.
        context: The chapter this section is being rendered into.

    Returns:
        The section, or the empty string for a cash purchase with no loan flows.
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
    rate = _effective_rate_text(credit)
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
    context: _ChapterContext,
) -> str:
    """The NPV bridge: why the variant's NPV differs from the reference's, by cost group.

    Answers the decision question one level deeper than the total does: not "is it cheaper" but
    "what makes it cheaper", which is what a reader needs to judge whether the answer rests on
    one assumption or on many.

    Args:
        reference: The baseline result the comparison was computed against.
        variant: The result being compared to it.
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
    delta = variant.total_npv_in_euro.best_estimate - reference.total_npv_in_euro.best_estimate
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
