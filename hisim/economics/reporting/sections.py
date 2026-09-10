"""Section builders of the HTML lifecycle report (cost_spec.md §7.2, §9.5).

One function per report section along the calculation chain — the primer that opens the
document, input audit, sources, the assumptions the run was priced under, investment, timeline,
energy bill, CO2 (with the table that states every mass as its own multiplication) and the
subsidy tables/cards (with the D28 content-key de-duplication) — plus the report CSS. The
sections of the visualization set
live beside them in `sections_charts`; every one of them, here and there, opens through
`scaffold._section_open` and `scaffold._explanation_html`, so its name, its anchor and its
authored explanation come from one place. Assembly order and the document shell live in
`assembly`. Split out of the former single-module `reporting.py` (PR-3 review); the package
`__init__` re-exports everything.
"""


from __future__ import annotations

import itertools
from typing import List, Mapping, Optional, Sequence, Tuple

from hisim.economics import views
from hisim.economics.input_audit import InputAuditReport, OriginKind, ResolvedInputRow, price_basis
from hisim.economics.presentation_style import (
    ChromeColors,
    PresentationStyle,
    SequentialRamp,
    group_of,
)
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
    _reference_result,
)
from hisim.economics.reporting.charts import (
    _annual_flow_svg,
    _Bar,
    _bar_row,
    _category_table,
    _cumulative_npv_svg,
    _details,
    _esc,
    _hline,
    _legend_html,
    _svg_open,
    _table,
    _text,
    _waterfall_svg,
    _whisker_svg,
)
from hisim.economics.reporting.scaffold import (
    ReportSections,
    _ChapterContext,
    _explanation_html,
    _section_open,
)

# ---------------------------------------------------------------------------- HTML report (A)


def _how_to_read_section_html(context: _ChapterContext) -> str:
    """The primer: the three conventions every other section assumes the reader knows.

    Discounting, the three complete worlds behind every `best_estimate [min | max]` band, and the
    sign rule that keeps costs and credits apart are stated once, at the top of the page, so no
    section has to re-derive them next to its own chart. It carries no number and no chart, which
    is why it is the one section built from nothing but its heading and its explanation block —
    and why it always renders: there is no input that could make it empty.

    It lives here, with the other prose sections, rather than in `scaffold.py` where the
    vocabulary it is built from lives: scaffold keeps the primitives, a section that opens a
    `<section>` and renders authored text is a section like any other.

    Args:
        context: The chapter this section is being rendered into.

    Returns:
        The section, always.
    """
    return _section_open(ReportSections.HOW_TO_READ, context) + _explanation_html(
        ReportSections.HOW_TO_READ, context
    ) + "</section>"


def _color_declarations(prefix: str, colors: Sequence[str]) -> str:
    """The `--<prefix>0..--<prefix>N` custom-property declarations of one theme, from a palette.

    Two palettes are declared this way: the eight display-group hues (`--g0..--g7`) and the ten
    steps of the sequential ramp the rate fan is drawn in (`--ramp0..--ramp9`). Both have to
    appear in the stylesheet as well as in the palette the charts and the matplotlib companions
    read, and writing them out twice is how a group ends up one hue in the HTML and another in its
    PNG. Generating them means each palette is edited in one place; the output is byte-for-byte
    the hand-written line it replaces.

    Args:
        prefix: The custom-property prefix, without dashes — `"g"` or `"ramp"`.
        colors: That palette's colours for one theme, in index order.

    Returns:
        The declarations as one line, e.g. ``--g0:#2a78d6; --g1:#1baf7a;`` — each terminated, so
        the caller only adds the closing brace.
    """
    return " ".join(f"--{prefix}{index}:{color};" for index, color in enumerate(colors))


def _neutral_color_declarations(
    chrome: Mapping[str, str], page: str, ink_2: str, baseline: str, border: str
) -> str:
    """One theme's neutral declarations, with the four shared chrome roles read from the palette.

    `presentation_style.ChromeColors` is the single source of the roles both renderers draw with —
    the surface a chart sits on, the ink its text is set in, the muted tone of secondary labels
    and the tone of the gridlines — and the stylesheet used to carry its own copy of those four
    hex values. Two copies is how an SVG gridline ends up a different grey from the gridline of
    its PNG companion, which is a defect nobody reads as one. The four neutrals only the HTML has
    stay arguments, because no chart draws them: the page behind the section cards, the secondary
    ink of the captions, the baseline rule and the card border.

    The output is byte-for-byte the two hand-written lines it replaces — the line break between
    them and the second line's two-space indent included — so the stylesheet inside the golden
    report does not move. The *first* line carries no indent of its own: it continues the
    literal's `:root { color-scheme: light dark;` line, and the two spaces in front of it are the
    literal's, not this function's.

    Args:
        chrome: One theme of `ChromeColors` — `LIGHT` or `DARK`.
        page: Background behind the section cards.
        ink_2: Secondary text colour, for captions and definitions.
        baseline: Colour of the chart baselines and of the table header rule.
        border: Border colour of a section card.

    Returns:
        The declarations as the two lines they occupy in the stylesheet.
    """
    return (
        f"--surface:{chrome['surface']}; --page:{page}; --ink-1:{chrome['ink']}; "
        f"--ink-2:{ink_2}; --muted:{chrome['muted']};\n"
        f"  --grid:{chrome['grid']}; --baseline:{baseline}; --border:{border};"
    )


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
    disagrees between an SVG and its PNG companion is a bug nobody reads as one. `--ramp0`..
    `--ramp9` are generated the same way from `SequentialRamp`: ten ordered steps for the one
    chart whose series are an ordered quantity rather than eight things to tell apart. `--ink-*`,
    `--muted`, `--surface`, `--grid` and `--baseline`
    are the chrome roles, and `--good`/`--warning`/`--critical` back the `.status.PASS` /
    `.status.WARN` / `.status.FAIL` classes the plausibility panel emits from the finding status
    verbatim. The four roles both renderers share — surface, ink, muted, grid — come from
    `presentation_style.ChromeColors` through `_neutral_color_declarations` for the same reason
    the group hues come from the palette; the neutrals only the HTML has are still written out
    here, because no chart draws them.

    The chapter rules (`h2.chapter`, `p.chapter-intro`, `.chapter-tag`) style the Q24 structure:
    a chapter is a rule-topped heading *between* the section cards rather than a card of its own,
    its authored lead-in sits in the same margin as the heading, and the small muted tag inside a
    section heading names the chapter that section is being read in. Sections are `<h3>` for the
    same reason — they sit under a chapter's `<h2>`, so the document outline is the chapter
    structure.

    The `dl`/`dt`/`dd` rules style the "Terms used here" definition lists of every section's
    explanation block. They deliberately reuse the existing roles — the term in `--ink-1` like a
    heading, the definition in `--ink-2` like the surrounding prose — so a disclosure a reader
    opens looks like the rest of the section rather than like a glossary pasted into it.
    """

    CSS = """
:root { color-scheme: light dark;
  """ + _neutral_color_declarations(
        ChromeColors.LIGHT, "#f9f9f7", "#52514e", "#c3c2b7", "rgba(11,11,11,0.10)"
    ) + """
  --good:#0ca30c; --warning:#fab219; --critical:#d03b3b;
  """ + _color_declarations("g", PresentationStyle.GROUP_COLORS_LIGHT) + """
  """ + _color_declarations("ramp", SequentialRamp.LIGHT) + """ }
@media (prefers-color-scheme: dark) { :root {
  """ + _neutral_color_declarations(
        ChromeColors.DARK, "#0d0d0d", "#c3c2b7", "#383835", "rgba(255,255,255,0.10)"
    ) + """
  """ + _color_declarations("g", PresentationStyle.GROUP_COLORS_DARK) + """
  """ + _color_declarations("ramp", SequentialRamp.DARK) + """ } }
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


def _audit_section_html(audit: InputAuditReport, context: _ChapterContext) -> str:
    """The input audit: the declared facts and the prices they resolved to.

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

    Args:
        audit: The resolved-input audit to render.
        context: The chapter this section is being rendered into; it supplies the anchor and
            decides whether the authored explanation is printed or linked to.

    Returns:
        The section, always non-empty — a run with no priced fact still has a sources table.
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
        _section_open(ReportSections.INPUT_AUDIT, context)
        + _explanation_html(ReportSections.INPUT_AUDIT, context)
        + "<table><tr><th>Subject</th><th>Asset class</th><th>Size</th><th>Unit price</th>"
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


#: How each kind of assumption value is spelled, in the conventional form for its quantity: a
#: rate to two decimals with a percent sign, a working price to four, a euro figure and an energy
#: quantity with thousands separators. The view returns the numbers and this decides their
#: digits — the formatting half of the seam `views.AssumptionRow` sits on.
_ASSUMPTION_VALUE_FORMATS = {
    views.AssumptionKinds.PERCENT: "{value:.2%}",
    views.AssumptionKinds.YEARS: "{value:g}",
    views.AssumptionKinds.YEAR: "{value:g}",
    views.AssumptionKinds.FACTOR: "{value:.6f}",
    views.AssumptionKinds.EURO_PER_KWH: "{value:.4f}",
    views.AssumptionKinds.EURO_PER_YEAR: "{value:,.2f}",
    views.AssumptionKinds.EURO_PER_TON: "{value:,.2f}",
    views.AssumptionKinds.KWH_PER_YEAR: "{value:,.0f}",
    views.AssumptionKinds.SQUARE_METERS: "{value:,.1f}",
    views.AssumptionKinds.PLAIN: "{value}",
}


def _assumption_value_text(row: views.AssumptionRow) -> str:
    """One assumption's value as the table prints it: the number, spelled by kind, then its unit.

    The whole of the formatting the assumptions table needs, in one place, so a row's digits are
    decided here rather than in the view that computes it. An unknown kind falls back to printing
    the value as it stands, which is what a new kind would want before anyone has decided how it
    reads — and is never silently empty.

    Args:
        row: The row to render the value cell of.

    Returns:
        The value text, unescaped — the caller escapes it like every other cell.
    """
    template = _ASSUMPTION_VALUE_FORMATS.get(row.kind, "{value}")
    text = template.format(value=row.value)
    return f"{text} {row.unit}" if row.unit else text


def _assumptions_section_html(matrix: EvaluationMatrix, context: _ChapterContext) -> str:
    """Every economic assumption the run was priced under, with value and source (Q26 F2).

    The boundary between input and result, placed directly after the input audit because that is
    where a reader has just finished checking *what* was priced and needs to know *under which
    assumptions*. The audit answers "which price did this device resolve to"; this answers "at
    what interest rate, over what horizon, with which escalation and which tariff" — the causes
    behind every consequence the rest of the report draws (rule 2.9).

    Rows come from `views.economic_assumptions`, which reads the parameters and the assumption
    record the evaluator resolved; nothing here is a literal, and the values arrive as *numbers* —
    the digits each of them is printed with are decided here, by `_assumption_value_text`. The
    computed rows are marked as such, so the annuity factor is not mistaken for something somebody
    chose, and a value with no data-layer source states `configuration`, which is a statement about
    its provenance rather than a blank.

    The whole matrix is passed on rather than one perspective because the assumption set is a
    property of the *run*: every row but one is identical across the evaluated perspectives, and
    the exception — the CO2 damage cost — belongs in the table when any of them applies it, which
    the view now decides for itself instead of taking a boolean from here.

    Args:
        matrix: Every evaluated perspective; the table is stated on the reference one and the
            damage-cost row follows from the whole run.
        context: The chapter this section is being rendered into.

    Returns:
        The section, or the empty string when the run publishes no assumption at all.

    Raises:
        ValueError: If the matrix holds no evaluated perspective (see `_reference_result`).
    """
    result = _reference_result(matrix)
    rows = views.economic_assumptions(list(matrix.results.values()))
    if not rows:
        return ""
    grouped = []
    # One pass over rows that already arrive in `AssumptionGroups.ORDER`: the band header and the
    # rows under it come out of the same walk, where re-filtering the whole list per group made
    # the table's shape depend on two orderings agreeing.
    for group, in_group in itertools.groupby(rows, key=lambda row: row.group):
        grouped.append([f"<b>{_esc(group)}</b>", "", ""])
        grouped.extend(
            [
                _esc(row.name) + (" <span class='sub'>(computed)</span>" if row.is_computed else ""),
                _esc(_assumption_value_text(row)),
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
    """The year-0 investment build-up, one waterfall per subject.

    Answers "is the money that leaves the account in year 0 the money this measure should
    cost?" — one waterfall per component walking device + installation + planning + removal
    - subsidies - loan disbursement down to the net outflow, plus a table of gross, support and
    net per subject. It sits directly after the input audit because that is the order the
    numbers are built in: the audit shows the unit prices, this shows what they add up to, and
    a component that is missing or priced from the wrong field is visible in both.

    A binding subsidy cap shows here as support that stops short of the scheme's headline rate,
    and a financed perspective shows the loan disbursement cancelling most of the outflow. Sunk
    cost (the written-off residual book value of a replaced asset, §4.1) is appended as a note
    rather than as a step, because it is reported but deliberately excluded from the decision
    KPIs. Only subjects with a year-0 flow appear.

    Args:
        result: The perspective whose year 0 is drawn.
        context: The chapter this section is being rendered into.

    Returns:
        The section — always rendered, carrying the "nothing was bought in year 0" note instead of
        the waterfalls when no subject has a year-0 flow, because that is itself worth stating.
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
        # "Always rendered" means this case too: a run whose year 0 is empty — an operating-only
        # perspective, or a measure whose whole cost falls in a later year — used to drop the
        # section, and a missing section reads as a broken renderer rather than as the finding it
        # is. The waterfalls are what is absent, not the answer.
        blocks.append(
            "<p class='sub'>Nothing was bought in year 0: no subject of this perspective books an "
            "investment, planning, removal, subsidy or loan flow in the first year, so there is no "
            "build-up to walk down. The costs this run does carry are in the sections below.</p>"
        )
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
    table = (
        _details(
            "investment table",
            _table(["Subject", "Gross investment", "Subsidies", "Net"], table_rows),
        )
        if table_rows else ""
    )
    return (
        _section_open(ReportSections.INVESTMENT_BUILD_UP, context, "year 0")
        + _explanation_html(ReportSections.INVESTMENT_BUILD_UP, context) + "".join(blocks)
        + table
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


def _timeline_section_html(matrix: EvaluationMatrix, context: _ChapterContext) -> str:
    """The cash-flow timeline: annual cash flows + cumulative discounted cost, per perspective.

    Answers "does the money arrive in the right years, and does the year-by-year story add up to
    the headline NPV?" This is the report's view of the §3.6 canonical timeline, and it is
    assembled as a chain a reviewer can walk down: legend, nominal stacked bars, the discounted
    cumulative curve with the NPV label, then the NPV-by-category table and finally the full
    year × subject × category detail table, so any bar in the chart can be resolved to the
    individual flows behind it.

    The loan amortization used to be a third chart in this block and is now the Loan section of
    its own (Q18): debt service is a separate story from the timeline it replaces, and the
    cost-of-credit panel beside it is the second half of that story rather than a footnote to
    this one.

    One collapsible block per perspective, the first open, because the same timeline read under
    different scopes is exactly what makes an actor split or a subsidy mode comprehensible. The
    legend lists only the display groups this perspective's scoped timeline actually contains.

    Args:
        matrix: Every evaluated perspective; each gets a block.
        context: The chapter this section is being rendered into.

    Returns:
        The section, always non-empty for a matrix with at least one perspective.
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
        + "".join(blocks) + "</section>"
    )


def _energy_section_html(result: LifecycleCostResult, context: _ChapterContext) -> str:
    """The energy bill: the year-1 decomposition per carrier with implied effective prices.

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

    Args:
        result: The perspective whose year-1 bill is decomposed.
        context: The chapter this section is being rendered into.

    Returns:
        The section, or the empty string when the run bought no energy at all.
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


def _subsidy_section_html(matrix: EvaluationMatrix, context: _ChapterContext) -> str:
    """The subsidies section: the cumulation solver's audit trail, rendered.

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

    A card heading is an `<h4>`: sections are `<h3>` under their chapter's `<h2>` since the
    mnemonic naming, so a card inside one has to sit a level below it or the document outline
    reads as two sections where there is one.

    Args:
        matrix: Every evaluated perspective; their decisions are grouped by content.
        context: The chapter this section is being rendered into.

    Returns:
        The section, or the empty string when there is neither a decision nor any support to
        draw.
    """
    cards = []
    for decision, perspective_ids in _decisions_by_content(matrix):
        note = _perspectives_note(perspective_ids, matrix)
        lines = [
            f"<h4 style='font-size:13px;margin:12px 0 4px'>{_esc(decision.measure_subject)} "
            f"<span style='font-weight:400;color:var(--muted)'>({_esc(note)})</span></h4><ul>"
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
    # The two states of this section: with a catalog the cards below are the audit trail the
    # authored prose describes, without one the flat legacy shim applies and this note says which
    # of the two the reader is looking at.
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


def _co2_section_html(matrix: EvaluationMatrix, context: _ChapterContext) -> str:
    """The CO2 section: lifecycle emissions (§3.8), embodied vs. operational.

    Answers "does the emissions accounting behave like the money does, and are the two kept
    apart?" Embodied emissions (blue, keyed by component subject, booked at installation and at
    every replacement) and operational emissions (orange, keyed by energy carrier) are drawn on
    one axis so their relative size is visible, since for a well-insulated building with a heat
    pump the embodied share stops being negligible. The section's authored prose states the
    separation the spec insists on: these are *masses*, and neither the CO2 price (a cash flow)
    nor the CO2 damage cost (a macroeconomic charge) is ever added to them.

    Three views of the same figures: sorted horizontal bars, a cumulative operational curve over
    the horizon (flat-sloped, because v1 holds emission factors constant — the caption says so;
    revisit both the slope and that caption when a time-varying emission-factor path ships, spec
    §3.8), and a table whose Total row closes against `total_co2_in_kg`. Emissions are never
    discounted, so unlike the money charts this one has no present-value counterpart.

    It lives here rather than with the SVG primitives it draws with: it is a section, and every
    section is assembled in this module so `charts.py` stays free of `report_prose` and can
    remain the layer `scaffold.py` builds on.

    Args:
        matrix: Every evaluated perspective; the section is drawn from the reference one.
        context: The chapter this section is being rendered into.

    Returns:
        The section, or the empty string when the run has no emissions data at all.

    Raises:
        ValueError: If the matrix holds no evaluated perspective (see `_reference_result`); the
            section is drawn from the reference one.
    """
    result = _reference_result(matrix)
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
        parts.extend(
            _bar_row(
                label=subject,
                y=y,
                row_h=row_h,
                left=left,
                bars=[(left, value * scale, color,
                       f"{subject} ({kind}): {value:,.0f} kg CO2 over the horizon", 3)],
                value=(left + value * scale + 6, f"{value:,.0f} kg", "start"),
            )
        )
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

    Args:
        result: The perspective whose CO2 accounting the table spells out.

    Returns:
        The disclosure, or the empty string when the result records no factor.
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
        table_rows.append([_esc(row.subject), _esc(row.kind.value), arithmetic, over_horizon])
    return _details(
        "CO2 factors — every mass as its own multiplication",
        _table(["Subject / carrier", "Kind", "Factor x quantity", "Over the horizon"], table_rows),
        open_by_default=True,
    )
