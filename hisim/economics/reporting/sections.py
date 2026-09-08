"""Section builders of the HTML lifecycle report (cost_spec.md §7.2, §9.5).

One function per report section — input audit, sources, investment, timeline, energy and
the subsidy tables/cards (with the D28 content-key de-duplication) — plus the report CSS.
Assembly order and the document shell live in `assembly`. Split out of the former
single-module `reporting.py` (PR-3 review); the package `__init__` re-exports everything.
"""


from __future__ import annotations

from typing import List, Optional, Tuple

from hisim.economics import views
from hisim.economics.input_audit import InputAuditReport, OriginKind, ResolvedInputRow, price_basis
from hisim.economics.presentation_style import PresentationStyle, group_of
from hisim.economics.results import EvaluationMatrix, LifecycleCostResult
from hisim.economics.timeline import CostCategory
from hisim.economics.uncertainty import UncertainValue


from hisim.economics.reporting.summary import (
    _award_amount_str,
    _award_arithmetic_str,
    _band_str,
    _decisions_by_content,
    _fmt,
    _perspectives_note,
)
from hisim.economics.reporting.charts import (
    _annual_flow_svg,
    _Bar,
    _bar_row,
    _category_table,
    _cumulative_npv_svg,
    _details,
    _esc,
    _legend_html,
    _loan_svg,
    _svg_open,
    _table,
    _waterfall_svg,
    _whisker_svg,
)

# ---------------------------------------------------------------------------- HTML report (A)


def _group_color_declarations(colors: List[str]) -> str:
    """The `--g0..--gN` custom-property declarations of one theme, from the palette itself.

    The eight display-group hues have to appear in the stylesheet as well as in the palette the
    charts and the matplotlib companions read, and writing them out twice is how a group ends up
    one hue in the HTML and another in its PNG. Generating them means the palette is edited in one
    place; the output is byte-for-byte the hand-written line it replaces.

    Args:
        colors: A theme's group colours in group order.

    Returns:
        The declarations as one line, e.g. ``--g0:#2a78d6; --g1:#1baf7a;`` — each terminated, so
        the caller only adds the closing brace.
    """
    return " ".join(f"--g{index}:{color};" for index, color in enumerate(colors))


class _ReportCss:
    """The report stylesheet, inlined into the self-contained HTML.

    Inlined rather than linked because the report has to be a single file that works from a
    network share, an email attachment or an archive with no network at all — the same rule that
    forces the charts to be hand-written SVG. Everything the page needs is here; nothing is
    fetched.

    The whole palette is declared as CSS custom properties on `:root` and redeclared under
    `@media (prefers-color-scheme: dark)`, which is what makes the inline charts theme-aware: a
    bar filled with `var(--g3)` re-colours with the reader's system setting, something a
    rasterized chart cannot do. `--g0`..`--g7` are the eight display-group hues and are *generated*
    from `PresentationStyle.GROUP_COLORS_LIGHT` / `GROUP_COLORS_DARK` rather than transcribed, so a
    group keeps its colour across the HTML, its SVGs and the matplotlib PNGs by construction; the
    two lists were previously copied here by hand and could drift apart silently, and a hue that
    disagrees between an SVG and its PNG companion is a bug nobody reads as one. `--ink-*`,
    `--muted`, `--surface`, `--grid` and `--baseline`
    are the chrome roles, and `--good`/`--warning`/`--critical` back the `.status.PASS` /
    `.status.WARN` / `.status.FAIL` classes the plausibility panel emits from the finding status
    verbatim.
    """

    CSS = """
:root { color-scheme: light dark;
  --surface:#fcfcfb; --page:#f9f9f7; --ink-1:#0b0b0b; --ink-2:#52514e; --muted:#898781;
  --grid:#e1e0d9; --baseline:#c3c2b7; --border:rgba(11,11,11,0.10);
  --good:#0ca30c; --warning:#fab219; --critical:#d03b3b;
  """ + _group_color_declarations(PresentationStyle.GROUP_COLORS_LIGHT) + """ }
@media (prefers-color-scheme: dark) { :root {
  --surface:#1a1a19; --page:#0d0d0d; --ink-1:#ffffff; --ink-2:#c3c2b7; --muted:#898781;
  --grid:#2c2c2a; --baseline:#383835; --border:rgba(255,255,255,0.10);
  """ + _group_color_declarations(PresentationStyle.GROUP_COLORS_DARK) + """ } }
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
    """Spells out a resolved row's origin for this report (the audit decided the precedence).

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
    """Section 1: input audit, checking the declared facts and the resolved prices.

    Renders the rows `audit.build_input_audit` resolved; override precedence, the flags and the
    source list are decided there, once, and written to `cost_audit.csv` from the same rows.

    It comes first after the panel because everything downstream is a consequence of these
    numbers: one row per priced fact with its size, its resolved unit price and lifetime, where
    that price came from and any flags raised while resolving it. This is the §9.5 "review one
    table instead of 46 files" workflow — a config-wiring mistake such as a 5000 kW heat pump is
    an implausible size or price in this table long before it is a surprising NPV (and is flagged
    as such: `AuditThresholds` bounds a size per unit, 1,000 kW among them). The sources table is
    appended so the prices above can be checked for currency in the same place.

    The unit price carries its **basis** rather than a flat "EUR/unit", from the same
    `input_audit.price_basis` the CSV column uses: a database row states euro per unit of its size
    while an override states an absolute amount for the whole subject, and printing the two under
    one label made a per-kW figure and a total look like the same quantity.
    """
    # A named template beats an f-string here: seven placeholders, three of them formatted,
    # and the row's shape stays readable as HTML.
    rows = [
        "<tr><td>{subject}</td><td>{cls}</td><td>{size:,.1f} {unit}</td><td>{price}</td>"  # pylint: disable=consider-using-f-string
        "<td>{life}</td><td>{origin}</td><td class=\"flag\">{flags}</td></tr>".format(
            subject=_esc(row.subject),
            cls=_esc(row.asset_class),
            size=row.size,
            unit=_esc(row.size_unit),
            price=_esc(_band_str(row.unit_price_in_euro, price_basis(row))),
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
    """Every flow behind the timeline chart, as a verification table.

    One row per (year, subject, category) with its nominal band and discounted value — the
    §3.6 canonical timeline, laid out for checking.

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
                    _fmt(row.discounted_best_estimate_in_euro),
                ]
            )
        rows.append(
            [
                f"<b>{detail_year.year}</b>",
                "<b>year total</b>",
                "",
                f"<b>{_esc(_band_str(detail_year.nominal_total_in_euro))}</b>",
                f"<b>{_fmt(detail_year.discounted_total_best_estimate_in_euro)}</b>",
            ]
        )
    return _table(["Year", "Subject", "Category", "Nominal", "Discounted (best_estimate)"], rows)


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

    Which perspective won that selection is printed above the chart. A run's perspectives do not
    have to agree about support — a gross view applies nothing a net view applies — so an unlabelled
    composition invites the reader to take one perspective's funded share for the run's, and the
    selection rule above is not something a reader of the output can see.
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
        bars: List[_Bar] = [(left, net * scale, "var(--g0)",
                             f"{subject} - net cost after subsidies: {_fmt(net)} EUR", 2)]
        if subsidy > 0:
            bars.append((left + net * scale + 1.5, max(subsidy * scale - 1.5, 0.5), "var(--g3)",
                         f"{subject} - subsidies: {_fmt(subsidy)} EUR", 2))
        parts.extend(
            _bar_row(
                label=subject,
                y=y,
                row_h=row_h,
                left=left,
                bars=bars,
                value=(left + gross * scale + 6, f"{share.share_of_gross:.0%} funded", "start"),
            )
        )
        y += row_h
    parts.append("</svg>")
    return (
        f'<p class="sub">Composition drawn from perspective <b>{_esc(result.perspective_id)}</b>; '
        "perspectives can differ in what they apply.</p>"
        '<div class="legend"><span class="chip"><span class="swatch" style="background:var(--g0)"></span>'
        'net cost</span><span class="chip"><span class="swatch" style="background:var(--g3)"></span>'
        "subsidies</span></div>" + "".join(parts)
    )


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
    de-duplicated by decision *content* (`_decisions_by_content`), not by measure name:
    perspectives that awarded a measure the same way share one row, named in the "Perspectives"
    column, while a perspective that decided differently gets its own rows. Returns empty when no
    award applied anywhere.
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

    An award is worth `views.describe_award`'s total here, exactly as in the awards table below
    and in `cost_summary.md`. The card used to print the *upfront* amount instead, unlabelled,
    which is zero for a tax-credit schedule, an operational rate and loan terms, so a §35c credit
    worth 2,060 EUR appeared as "0.00 EUR" while the SUBSIDY category NPV beside it counted it.
    Three renderings of one audit trail may not disagree about what an award is worth.
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
