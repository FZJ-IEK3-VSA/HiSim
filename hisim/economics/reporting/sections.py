"""Section builders of the HTML lifecycle report (cost_spec.md §7.2, §9.5), and the report CSS.

One function per section along the calculation chain: the primer, input audit, sources, assumptions, investment,
timeline, energy bill, CO2 and subsidies. The chart sections live in `sections_charts`; every section opens through
`scaffold._section_open` and `scaffold._explanation_html`, and the document is assembled in `assembly`.
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
from hisim.economics.results import AnywayBasisKinds, EvaluationMatrix, LifecycleCostResult
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
    _value_by_kind,
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

# ---------------------------------------------------------------------------- HTML report


def _how_to_read_section_html(context: _ChapterContext) -> str:
    """Render the primer: the three conventions every other section assumes.

    States discounting, the three worlds behind every `best_estimate [min | max]` band, and the sign rule for costs and
    credits, once at the top of the page. It has no number or chart and always renders.

    Args:
        context: The chapter this section is rendered into.

    Returns:
        The section.
    """
    return _section_open(ReportSections.HOW_TO_READ, context) + _explanation_html(
        ReportSections.HOW_TO_READ, context
    ) + "</section>"


def _color_declarations(prefix: str, colors: Sequence[str]) -> str:
    """Return the `--<prefix>0..--<prefix>N` CSS custom-property declarations of one theme's palette.

    Used for the eight display-group hues (`--g0..--g7`) and the ten steps of the sequential ramp (`--ramp0..--ramp9`).
    Generating them from the palette the charts and PNGs also read keeps the colours identical across renderers.

    Args:
        prefix: The custom-property prefix without dashes, `"g"` or `"ramp"`.
        colors: That palette's colours for one theme, in index order.

    Returns:
        The declarations as one line, e.g. ``--g0:#2a78d6; --g1:#1baf7a;``, each terminated.
    """
    return " ".join(f"--{prefix}{index}:{color};" for index, color in enumerate(colors))


def _neutral_color_declarations(
    chrome: Mapping[str, str], page: str, ink_2: str, baseline: str, border: str
) -> str:
    """Return one theme's neutral colour declarations, with the four shared chrome roles taken from the palette.

    The surface, ink, muted and grid roles come from `presentation_style.ChromeColors`, which the chart renderers also
    use, so an SVG gridline and its PNG companion match. The four neutrals only the HTML uses are arguments. The output
    is two lines exactly as the stylesheet needs them: the first continues the `:root { color-scheme: light dark;`
    line, the second has a two-space indent.

    Args:
        chrome: One theme of `ChromeColors`, `LIGHT` or `DARK`.
        page: Background behind the section cards.
        ink_2: Secondary text colour, for captions and definitions.
        baseline: Colour of chart baselines and of the table header rule.
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
    """The report stylesheet, inlined so the HTML is one self-contained file.

    The palette is declared as CSS custom properties on `:root` and redeclared under `@media (prefers-color-scheme:
    dark)`, so inline SVG charts follow the reader's theme. `--g0`..`--g7` (display-group hues) and
    `--ramp0`..`--ramp9` (the sequential ramp) are generated from `PresentationStyle` and `SequentialRamp`, and the
    shared chrome roles come from `presentation_style.ChromeColors`, so HTML, SVG and PNG colours agree. `--good`,
    `--warning` and `--critical` back the `.status.PASS`, `.status.WARN` and `.status.FAIL` classes of the plausibility
    panel.

    Chapters (`h2.chapter`, `p.chapter-intro`, `.chapter-tag`) are rule-topped headings between the section cards;
    sections are `<h3>` beneath them. The `dl`/`dt`/`dd` rules style the "Terms used here" lists in the same ink roles
    as headings and prose.
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
    """Return the words for where a resolved input row's price came from.

    A config override (with its cited source, or `NO SOURCE`), the matching database entry key, or `unresolved`. The
    precedence is decided in `input_audit.py` and written identically to `cost_audit.csv`; this only picks the words.
    """
    if row.origin_kind == OriginKind.ORIGIN_OVERRIDE:
        return f"override ({row.override_source or 'NO SOURCE'})"
    if row.origin_kind == OriginKind.ORIGIN_DATABASE:
        return str(row.entry_key)
    return "unresolved"


def _audit_section_html(audit: InputAuditReport, context: _ChapterContext) -> str:
    """Render the input audit: the declared facts and the prices they resolved to (§9.5).

    One row per priced fact with its size, resolved unit price and lifetime, where the price came from and any flags,
    built by `audit.build_input_audit` from the same rows as `cost_audit.csv`. A wiring mistake such as a 5000 kW heat
    pump shows here as an implausible size (`AuditThresholds` bounds sizes per unit). The unit price carries its basis
    (`input_audit.price_basis`), since a database row is euro per unit of size while an override is an absolute amount.
    The anyway-credit column states `share x basis = credit`, because the share is an input like a price. The sources
    table is appended.

    Args:
        audit: The resolved-input audit to render.
        context: The chapter this section is rendered into; supplies the anchor and decides whether the explanation is
            printed or linked.

    Returns:
        The section; never empty, since a run with no priced fact still has a sources table.
    """
    # A named template beats an f-string here: eight placeholders, three of them formatted,
    # and the row's shape stays readable as HTML.
    rows = [
        "<tr><td>{subject}</td><td>{cls}</td><td>{size:,.1f} {unit}</td><td>{price}</td>"  # pylint: disable=consider-using-f-string
        "<td>{life}</td><td>{share}</td><td>{origin}</td><td class=\"flag\">{flags}</td></tr>".format(
            subject=_esc(row.subject),
            cls=_esc(row.asset_class),
            size=row.size,
            unit=_esc(row.size_unit),
            price=_esc(_band_str(row.unit_price_in_euro, price_basis(row))),
            life=f"{row.lifetime_in_years:g} a" if row.lifetime_in_years else "-",
            # The Sowieso share is an input like the unit price, so it is audited here with the
            # cost it applies to, and the credit multiplies out.
            share=_anyway_credit_cell(row),
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


def anyway_credit_text(
    share: float, basis: Optional[float], credit: Optional[float] = None
) -> str:
    """Return one anyway credit's multiplication in the single spelling all three places print it in.

    An anyway credit is the cost the building would have paid anyway (§4.1). `share x basis = credit` appears in the
    input audit, in the cash-flow detail table and in the timeline caption. The detail table already shows the amount
    in its own column, so it passes `credit=None` and gets the factor and basis only. A basis of None or zero (a result
    without the basis) yields the share alone.

    Args:
        share: The Sowieso share the credit was computed at, as a fraction.
        basis: The cost that share was applied to, or None when the result records none.
        credit: The product, where the amount is not already on the row; None where it is.

    Returns:
        The text, safe for HTML since it is built from numbers only.
    """
    if not basis:
        return f"{share:.0%}"
    if credit is None:
        return f"{share:.0%} x {basis:,.0f} EUR"
    return f"{share:.0%} x {basis:,.0f} EUR = {credit:,.0f} EUR"


def _anyway_credit_cell(row: ResolvedInputRow) -> str:
    """Return one audit row's anyway credit as `share x basis = credit`, or the empty-cell dash.

    A row without a credit shows the dash used for absent values; a share recorded without a basis is shown alone.

    Args:
        row: The resolved input row.

    Returns:
        The cell text, safe for HTML since it is built from numbers only.
    """
    if row.anyway_share is None:
        return "-"
    basis = row.anyway_basis_in_euro
    product = row.anyway_share * basis if basis else None
    return anyway_credit_text(row.anyway_share, basis, product)


def _sources_table_html(audit: InputAuditReport) -> str:
    """Render the §3.10 source registry entries this evaluation cited, as resolved by the audit.

    Citation, kind, retrieval date and link per entry, so prices can be checked for currency. Collapsed by default;
    omitted when the audit resolved no sources.
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


def _assumption_value_text(row: views.AssumptionRow) -> str:
    """Return one assumption's value as the table prints it: the number, spelled by kind, then its unit.

    The digits come from `summary._value_by_kind`, shared with the scenarios table.

    Args:
        row: The row whose value cell is rendered.

    Returns:
        The value text, unescaped; the caller escapes it.
    """
    text = _value_by_kind(row.kind, row.value)
    return f"{text} {row.unit}" if row.unit else text


def _assumptions_section_html(matrix: EvaluationMatrix, context: _ChapterContext) -> str:
    """Render every economic assumption the run was priced under, with value and source.

    Placed after the input audit: the audit says which price each device resolved to, this says at which interest rate,
    horizon, escalation and tariff. Rows come from `views.economic_assumptions`; computed rows (such as the annuity
    factor) are marked, and a value with no data-layer source says `configuration`. The whole matrix is passed because
    the CO2 damage-cost row belongs in the table when any perspective applies it.

    Args:
        matrix: Every evaluated perspective; the table is stated on the reference one.
        context: The chapter this section is rendered into.

    Returns:
        The section, or the empty string when the run publishes no assumption.

    Raises:
        ValueError: If the matrix holds no evaluated perspective.
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
    """Render the year-0 investment build-up, one waterfall per subject.

    Each waterfall walks device + installation + planning + removal - subsidies - loan disbursement down to the net
    outflow, with a table of gross, support and net per subject. A binding subsidy cap shows as support short of the
    scheme's headline rate. Sunk cost (the written-off residual value of a replaced asset, §4.1) is a note, not a step,
    since it is excluded from the decision KPIs. Only subjects with a year-0 flow appear.

    Args:
        result: The perspective whose year 0 is drawn.
        context: The chapter this section is rendered into.

    Returns:
        The section; when nothing was bought in year 0 it carries a note saying so.
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
        # A run whose year 0 is empty (an operating-only perspective, or a measure whose whole
        # cost falls in a later year) still renders the section: a missing section would read
        # as a broken renderer rather than as the finding it is.
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
    """Render every flow behind the timeline chart as a verification table.

    One row per (year, subject, category) with its nominal band and discounted value. Rows, order, the float-noise
    cut-off and subtotals come from `views.timeline_detail_rows`; this adds the anyway credit's multiplication in its
    category cell.
    """
    rows: List[List[str]] = []
    shares = result.anyway_share_by_subject
    bases = result.anyway_basis_by_subject
    for detail_year in views.timeline_detail_rows(result):
        for row in detail_year.rows:
            # An anyway credit is `share x basis`, so the category cell of that row carries the
            # share and the basis, and the row multiplies out to the amount beside it.
            category = row.category.value
            if row.category == CostCategory.ANYWAY_COST_CREDIT and row.subject in shares:
                # No product: the amount is in this row's own Nominal cell, two columns right.
                factor = anyway_credit_text(shares[row.subject], bases.get(row.subject))
                category = f"{category} (anyway {factor})"
            rows.append(
                [
                    str(row.year),
                    _esc(row.subject),
                    _esc(category),
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
    """Render the cash-flow timeline: annual cash flows and cumulative discounted cost, per perspective (§3.6).

    Legend, nominal stacked bars, the cumulative discounted curve with the NPV label, the NPV-by-category table and the
    full year x subject x category detail table, so any bar can be traced to its flows. One collapsible block per
    perspective, the first open; each legend lists only the groups that perspective contains. Loan amortization is its
    own section.

    Args:
        matrix: Every evaluated perspective; each gets a block.
        context: The chapter this section is rendered into.

    Returns:
        The section; non-empty for a matrix with at least one perspective.
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
        + _anyway_share_caption(matrix)
        + "".join(blocks) + "</section>"
    )


#: The half of the anyway caption that is the same in both of its forms: the credit's timing and
#: what a share below 100 % means. Held as a constant so the collapsed and the per-perspective
#: sentence cannot come to explain the same share differently.
_ANYWAY_CAPTION_TAIL = (
    " (nominal, in the credit's own year). A share below 100 % means the measure was a "
    "first-time improvement, so only that fraction of it would have been spent without the "
    "renovation."
)


def _anyway_basis_word(facts: Sequence[views.AnywayCreditFact]) -> str:
    """Return the caption's name for the anyway-credit basis: its kind when all credits share one, else `basis`.

    All like-for-like credits read `like-for-like cost`; all coupled-measure credits read `non-energy share of the
    measure's gross cost`. A mix, or a stored result without kinds, reads `basis`, and each credit then names its own
    kind.
    """
    kinds = {fact.basis_kind for fact in facts if fact.basis_in_euro}
    return kinds.pop() if len(kinds) == 1 else AnywayBasisKinds.UNRECORDED


def _anyway_credit_list(facts: Sequence[views.AnywayCreditFact], name_kinds: bool) -> str:
    """Return one perspective's anyway credits, each as `subject share x basis = credit`."""
    parts = []
    for fact in facts:
        text = anyway_credit_text(fact.share, fact.basis_in_euro, fact.credit_in_euro)
        if name_kinds and fact.basis_in_euro:
            text = f"{text} ({_esc(fact.basis_kind)})"
        parts.append(f"{_esc(fact.subject)} {text}")
    return "; ".join(parts)


def _anyway_share_caption(matrix: EvaluationMatrix) -> str:
    """Return the caption line stating the Sowieso share and basis of each anyway credit in this run.

    The Sowieso share is the part of a measure's cost the building would have paid anyway, e.g. the repair share of a
    first-time improvement. The caption gives `share x basis = credit` per subject, the basis named by its kind
    (`results.AnywayBasisKinds`). It reads every perspective's credits and collapses to one sentence only when they are
    the same.

    Args:
        matrix: Every evaluated perspective; each contributes its own credits.

    Returns:
        The caption, or the empty string when no perspective books an anyway credit.
    """
    by_perspective = views.anyway_credit_facts_by_perspective(matrix)
    if not any(by_perspective.values()):
        return ""
    word = _anyway_basis_word([fact for facts in by_perspective.values() for fact in facts])
    name_kinds = word == AnywayBasisKinds.UNRECORDED
    if len(set(by_perspective.values())) == 1:
        facts = next(iter(by_perspective.values()))
        return (
            "<p class='sub'>Anyway credits in this run are booked at "
            f"<b>share x {word} = credit</b>: {_anyway_credit_list(facts, name_kinds)}"
            f"{_ANYWAY_CAPTION_TAIL}</p>"
        )
    lines = "".join(
        f"<br><b>{_esc(perspective_id)}</b> — "
        + (
            _anyway_credit_list(facts, name_kinds)
            if facts
            else "no anyway credit is booked"
        )
        for perspective_id, facts in by_perspective.items()
    )
    return (
        f"<p class='sub'>Anyway credits are booked at <b>share x {word} = credit</b>"
        f"{_ANYWAY_CAPTION_TAIL} The perspectives of this run do not book the same ones, so each "
        f"states its own:{lines}</p>"
    )


def _energy_section_html(result: LifecycleCostResult, context: _ChapterContext) -> str:
    """Render the energy bill: the year-1 cost per carrier with the implied effective price.

    Dividing a carrier's year-1 cost by the energy bought must give a recognizable price, so a Wh/kWh mix-up, a rate
    stored in cents or a missing time-of-use band shows at once; the plausibility panel checks the same figure.
    Whiskers show each carrier's year-1 band; the table gives quantity, cost, effective price and the split into
    working, standing, capacity and CO2-price parts. Feed-in revenue appears in the electricity components but is left
    out of the effective price (see `views.carrier_year_one_bills`).

    Args:
        result: The perspective whose year-1 bill is decomposed.
        context: The chapter this section is rendered into.

    Returns:
        The section, or the empty string when the run bought no energy.
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
    """Draw per measure the net cost (blue) and the subsidy (green): how much of each measure support covers.

    Segments and percentage come from `views.subsidy_share_of_gross`, including its `min(subsidy, gross)` clamp, so
    this chart and the matplotlib waterfall agree. It prefers a perspective with catalog decisions and falls back to
    any perspective with subsidy flows (an archived result without an award trail). The chosen perspective is printed
    above the chart, since perspectives differ in what support they apply. Empty when no perspective has support.
    """
    result = next(
        (res for res in matrix.results.values() if any(res.subsidy_decisions)), None
    )
    if result is None:
        # No catalog decisions (a result archived under the retired flat shim): use any
        # perspective with subsidy flows.
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
    """Return a scheme's display name as text with its raw id as the tooltip.

    Args:
        display_name: The catalog's display name, or None or empty when it declared none.
        scheme_id: The raw id; always the tooltip, and the visible text when there is no display name.

    Returns:
        An escaped `<span>` with the name as text and the id in its `title`.
    """
    name = display_name or scheme_id
    return f"<span title=\"{_esc(scheme_id)}\">{_esc(name)}</span>"


def _subsidy_awards_table(matrix: EvaluationMatrix) -> str:
    """Render all applied awards across measures: scheme, amount band, payout kind and binding caps (§5.4).

    The payout kind (upfront grant, repayment grant, scheduled tax credit) decides when money lands and so its present
    value; a cap binding only in HIGH explains an asymmetric band. Amounts come from `views.describe_award`, so a
    scheduled payout shows its instalment total and loan terms show their terms. Rows are de-duplicated by decision
    content (`_decisions_by_content`): perspectives that decided a measure the same way share a row. Empty when no
    award applied.
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
                # The multiplication and the ceiling verdict, so an amount can be checked
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
    """Render the subsidies section: the cumulation solver's audit trail per measure (§5.4).

    Each card lists what APPLIED (with the slots a cap bound in), what was REJECTED and why, and what is OPEN on an
    unanswered question, with the upper bound answering could unlock. There is one card per distinct decision
    (`_decisions_by_content`), naming the perspectives that share it. An award is worth `views.describe_award`'s total,
    as in the awards table and `cost_summary.md`. Support without a decision behind it (an archived result) is drawn
    with a note that it has no audit trail. Card headings are `<h4>`, below the section's `<h3>`.

    Args:
        matrix: Every evaluated perspective; their decisions are grouped by content.
        context: The chapter this section is rendered into.

    Returns:
        The section, or the empty string when there is neither a decision nor any support.
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
    # authored prose describes; support drawn without any card has no decision behind it (only
    # an archived result carries such support), and this note says which of the two it is.
    caption = "" if cards else (
        '<p class="sub">No subsidy catalog decision backs the support shown here. It comes from '
        "the retired flat legacy shim (cost_spec.md §10.1, retired 2026-09-24), which a run "
        "without a catalog no longer applies; an audit trail requires a catalog, see "
        "subsidy_catalog/.</p>"
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
    """Render the CO2 section: lifecycle emissions (§3.8), embodied and operational.

    Embodied emissions (blue, per component, booked at installation and every replacement) and operational emissions
    (orange, per carrier) share one axis. These are masses; neither the CO2 price nor the CO2 damage cost is added to
    them. The section shows sorted horizontal bars, a cumulative operational curve (a straight line, because emission
    factors are constant; the caption says so), a table whose Total row matches `total_co2_in_kg`, and the factors
    table. Emissions are not discounted.

    Args:
        matrix: Every evaluated perspective; the section is drawn from the reference one.
        context: The chapter this section is rendered into.

    Returns:
        The section, or the empty string when the run has no emissions data.

    Raises:
        ValueError: If the matrix holds no evaluated perspective.
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
    """Render the conversions behind every mass in the CO2 section.

    One row per carrier and per device with its factor, the quantity it multiplies and the product. Operational rows
    give the annual mass and the horizon total; embodied rows give the mass per installation and the number of
    installations, so a device replaced once shows its doubled mass.

    Args:
        result: The perspective whose CO2 accounting is spelled out.

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
