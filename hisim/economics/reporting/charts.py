"""Inline-SVG primitives and the per-result charts of the HTML report (cost_spec.md §7.2).

The drawing layer: escaping, `_svg_open`/`_rect`/`_text` primitives, tables and
`<details>` blocks, and the charts built from them — annual flows, cumulative NPV,
waterfall, whiskers, stacked subjects, loan and payback, plus the shared builders of the
visualization set (the column Sankey, the xy line chart, the Gantt strip, the NPV bridge,
the attribution tornado and the cost-of-credit bar). Everything here renders from
already-computed results; no section layout, and nothing here reads `report_prose`. That
is what keeps this the bottom module of the package: `scaffold.py` borrows `_esc` and
`_details` from here, `sections.py` and `sections_charts.py` build on both, and
`assembly.py` stitches the document together. Split out of the former single-module
`reporting.py` (PR-3 review); the package `__init__` re-exports everything.
"""


from __future__ import annotations

import html
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from hisim.economics import views
from hisim.economics.presentation_style import (
    PresentationStyle,
    RibbonSegment,
    SankeyGeometry,
    SankeyLayout,
    group_name,
    sankey_node_boxes,
)
from hisim.economics.results import LifecycleCostResult, VariantComparison
from hisim.economics.uncertainty import Slot, UncertainValue


from hisim.economics.reporting.summary import _band_str, _fmt


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
    banded charts (caller passes a group colour and a heavier stroke). Both draw something only
    when `x1 != x2` — a degenerate call renders nothing, which is how `_whisker_svg`'s zero
    marker came to be invisible; a vertical rule is written out as a `<line>` by the charts that
    need one.
    """
    return f'<line x1="{x1:.1f}" y1="{y:.1f}" x2="{x2:.1f}" y2="{y:.1f}" stroke="{color}" stroke-width="{width}"/>'


#: One bar of a `_bar_row`: `(x, width, colour, tooltip, corner radius)` in user units. The row's
#: y and height are not part of it — they follow from the row, which is the point of the helper.
_Bar = Tuple[float, float, str, str, float]


def _bar_row(
    label: str,
    y: float,
    row_h: float,
    left: float,
    bars: Sequence[_Bar] = (),
    marks: Sequence[str] = (),
    value: Optional[Tuple[float, str, str]] = None,
    inset: float = 4.0,
    emphasis: bool = False,
    value_size: int = 10,
) -> List[str]:
    """One row of a horizontal bar chart: label, bars, extra marks, value label.

    Five charts in this package draw the same row — a right-aligned label ending just before the
    plot area, one or more bars inset vertically inside the row, and a figure printed past the end
    of the bar — and each of them used to hand-write the four offsets that make a row look like a
    row: the label at `left - 8` on the row's optical centre `y + row_h / 2 + 4`, the bar at
    `y + inset` with height `row_h - 2 * inset`, the value label on that same centre line. Five
    copies of those offsets is five chances for one chart to sit a pixel off the others, which is
    exactly the kind of drift nobody reports and everybody notices.

    Row *height* stays with the caller (`row_h`, and the `y += row_h` after each call), because it
    is the caller that knows how many rows it has and how tall its canvas must be.

    Args:
        label: The row's name, drawn right-aligned before the plot area.
        y: Top of the row in user units.
        row_h: Row height; the label and value sit on `y + row_h / 2 + 4`.
        left: Left edge of the plot area — the label ends 8 units before it.
        bars: `(x, width, colour, tooltip, rx)` per bar, drawn in order and inset vertically.
        marks: Pre-rendered SVG emitted after the bars, for marks that are not bars (the whisker
            and dot of a banded row).
        value: `(x, text, anchor)` of the figure printed next to the bar, or None for no figure.
        inset: Vertical gap between the row and its bars, which is what leaves a visible gap
            between adjacent rows.
        emphasis: Draws the label and value in the ink colour and bold — a total row.
        value_size: Font size of the value label.

    Returns:
        The row's SVG parts, in drawing order, for the caller to extend its parts list with.
    """
    ink = "var(--ink-1)"
    parts = [_text(left - 8, y + row_h / 2 + 4, label, 11, "end", ink if emphasis else "var(--ink-2)", bold=emphasis)]
    parts.extend(
        _rect(x, y + inset, width, row_h - 2 * inset, color, tooltip, rx) for x, width, color, tooltip, rx in bars
    )
    parts.extend(marks)
    if value is not None:
        value_x, value_text, value_anchor = value
        parts.append(
            _text(value_x, y + row_h / 2 + 4, value_text, value_size, value_anchor,
                  ink if emphasis else "var(--muted)", bold=emphasis)
        )
    return parts


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
    for index, (_label, categories) in enumerate(PresentationStyle.DISPLAY_GROUPS):
        group_total = group_npv.get(index)
        if group_total is None or not (group_total.best_estimate or group_total.minimum or group_total.maximum):
            continue
        rows.append([f"<b>{_esc(group_name(index))}</b>", f"<b>{_esc(_band_str(group_total))}</b>"])
        for category in categories:
            value = result.npv_by_category.get(category)
            if value is not None and (value.best_estimate or value.minimum or value.maximum):
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


def _legend_html(groups_present: List[int]) -> str:
    """Colour chips for exactly the display groups a chart actually drew.

    Takes group indices rather than names so the swatch and the label are read from the same
    `PresentationStyle` entry the chart coloured its marks with, which is what keeps legend and
    chart in step. Callers pass only the groups present in the data, so a legend never lists a
    colour the reader cannot find in the chart above it.
    """
    chips = "".join(
        f'<span class="chip"><span class="swatch" style="background:var(--g{index})"></span>'
        f"{_esc(group_name(index))}</span>"
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
    swallowing a thin one, and x-ticks are thinned by `horizon // 10`, which gives about ten
    labels at a 100-year horizon and fewer below it — a 20-year run is labelled every other year,
    a 5-year run every year — because integer division floors the step at 1.
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
            tooltip = f"year {year} - {group_name(index)}: {_fmt(value)} EUR"
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
        f'<polyline points="{points_of(cumulative[Slot.BEST_ESTIMATE])}" fill="none" stroke="var(--g0)" stroke-width="2">'
        f"<title>cumulative discounted cost (best-estimate slot)</title></polyline>"
    )
    parts.append(_text(left - 6, to_y(top_value) + 8, _fmt(top_value), 10, "end", "var(--muted)"))
    parts.append(_text(left - 6, to_y(0.0) + 4, "0", 10, "end", "var(--muted)"))
    parts.append(
        _text(left + horizon * step, to_y(cumulative[Slot.BEST_ESTIMATE][-1]) - 6,
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
        parts.extend(
            _bar_row(
                label=label,
                y=y,
                row_h=row_h,
                left=left,
                bars=[(x_from, bar_w, color, f"{label}: {_fmt(value)} EUR", 3)],
                value=(
                    x_from + bar_w + 6 if value >= 0 else x_from - 6,
                    f"{'+' if value >= 0 else ''}{_fmt(value)}",
                    "start" if value >= 0 else "end",
                ),
            )
        )
        cursor += value
        y += row_h
    parts.append(_hline(left, width - 10, y + 2, "var(--baseline)"))
    y += 8
    parts.extend(
        _bar_row(
            label=total_label,
            y=y,
            row_h=row_h,
            left=left,
            bars=[(left, abs(net) * scale, "var(--ink-1)", f"{total_label}: {_fmt(net)} EUR", 3)],
            value=(left + abs(net) * scale + 6, f"{_fmt(net)} EUR", "start"),
            emphasis=True,
            value_size=11,
        )
    )
    parts.append("</svg>")
    return "".join(parts)


def _whisker_svg(rows: List[Tuple[str, UncertainValue]], unit: str) -> str:
    """Dot-with-whiskers per row (perspective overview / any banded metric list).

    The report's standard way of showing a list of banded figures: a dot at the BEST_ESTIMATE slot and
    a bar from LOW to HIGH. It is generic on purpose — sections 6 (perspectives), 6b (payers)
    and 4 (per-carrier year-1 bills) all reduce to "labelled `UncertainValue`s on a common
    axis", and giving them one visual form means a reader learns the encoding once. The whiskers
    are the §3.9 *envelope* of two coherent worlds, not a statistical interval, so overlapping
    whiskers say nothing about significance.

    Geometry: the axis spans `min(0, smallest minimum)` to the largest maximum, so zero is
    always on the canvas and rows with credits (negative NPVs) read correctly against it; `to_x`
    maps value to pixel, labels sit to the right of each maximum, and the numeric band is
    printed next to the whisker so the chart is readable without hovering. When some row is
    negative, a vertical rule marks zero across the rows.
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
        parts.extend(
            _bar_row(
                label=label,
                y=y,
                row_h=row_h,
                left=left,
                marks=[
                    _hline(to_x(band.minimum), to_x(band.maximum), mid, "var(--g0)", 2),
                    f'<circle cx="{to_x(band.best_estimate):.1f}" cy="{mid:.1f}" r="5" fill="var(--g0)" '
                    f'stroke="var(--surface)" stroke-width="2">'
                    f"<title>{_esc(label)}: {_esc(_band_str(band, unit))}</title></circle>",
                ],
                value=(to_x(band.maximum) + 8, _band_str(band, unit), "start"),
            )
        )
        y += row_h
    if min_value < 0:
        # A vertical rule through every row, not a zero-length horizontal one: the marker exists to
        # say which side of zero a row sits on, and a line whose two x coordinates were identical
        # drew nothing at all — the axis was invisible in exactly the charts (payer NPVs, per-carrier
        # bills with a credit) that have negative rows and therefore need it.
        zero_x = to_x(0.0)
        parts.append(
            f'<line x1="{zero_x:.1f}" y1="4" x2="{zero_x:.1f}" y2="{height - 8}" stroke="var(--baseline)"/>'
        )
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
    between runs. Cost and credit rects are the same mark drawn on opposite sides of that line:
    the credit ones used to be written out inline so they could carry a `class="credit"` hook,
    which no rule in the shipped stylesheet ever matched — a second copy of `_rect`'s markup kept
    alive for a hook nobody styled.
    """
    breakdowns = list(result.component_breakdowns.values())
    if not breakdowns:
        return ""

    per_subject = {
        breakdown.subject: {
            index: band.best_estimate
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
        bars: List[_Bar] = []
        x_pos = zero_x
        x_neg = zero_x
        for index in range(len(PresentationStyle.DISPLAY_GROUPS)):
            value = values.get(index, 0.0)
            if not value:
                continue
            bar_w = abs(value) * scale
            tooltip = f"{breakdown.subject} - {group_name(index)}: {_fmt(value)} EUR NPV"
            if value > 0:
                bars.append((x_pos, max(bar_w - 1.5, 0.5), f"var(--g{index})", tooltip, 2))
                x_pos += bar_w
            else:
                x_neg -= bar_w
                bars.append((x_neg, max(bar_w - 1.5, 0.5), f"var(--g{index})", tooltip, 2))
        total = breakdown.total_npv_in_euro
        parts.extend(
            _bar_row(
                label=breakdown.subject,
                y=y,
                row_h=row_h,
                left=left,
                bars=bars,
                marks=[
                    _hline(to_x(total.minimum), to_x(total.maximum), mid, "var(--ink-1)", 1.5),
                    f'<circle cx="{to_x(total.best_estimate):.1f}" cy="{mid:.1f}" r="4" fill="var(--ink-1)" '
                    f'stroke="var(--surface)" stroke-width="1.5">'
                    f"<title>{_esc(breakdown.subject)} net NPV: {_esc(_band_str(total))}</title></circle>",
                ],
                value=(x_pos + 8, _band_str(total), "start"),
                inset=5,
            )
        )
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
        for name, slot in (("min", "low"), ("best_estimate", "best_estimate"), ("max", "high"))
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
    styles = {"best_estimate": ("var(--g0)", 2.5, ""), "min": ("var(--g0)", 1.2, ' stroke-dasharray="5 4"'),
              "max": ("var(--g0)", 1.2, ' stroke-dasharray="2 4"')}
    labels = {"best_estimate": "expected", "min": "optimistic (LOW world)", "max": "pessimistic (HIGH world)"}
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


# ------------------------------------------------------- shared chart builders of the V-set (E)
#
# The charts of the visualization extension reuse a handful of builders rather than each emitting
# its own SVG: a column Sankey (the actor flows and the statement income diagram), an xy line
# chart with optional bands (the cash curve, the outstanding balance), a Gantt strip (component
# lifetimes and the lifecycle overview), a two-sided tornado (uncertainty attribution) and the NPV
# bridge. Everything they draw comes from `views.py`; the code below is geometry only, in the same
# y-grows-downward user units as the older charts above, and it lays its rows out with `_bar_row`
# so a row of a bridge lines up with a row of the component breakdown three sections earlier.


class _ChartGeometry:
    """The pixel frame every chart of the visualization set is drawn in.

    One place for the canvas width and the margins, because the report stacks a dozen charts and
    a chart that sets its own plot area does not line up with the one above it. The margins are
    generous on the left for row labels (a subject name, an actor, a scheme id) and on the right
    for the value labels the charts print at the end of a line or a ribbon.
    """

    WIDTH = 860
    LEFT = 150
    RIGHT = 130
    TOP = 14
    BOTTOM = 30
    ROW_HEIGHT = 26
    #: Events closer than this many years share a label stack instead of overprinting.
    CLUSTER_YEARS = 3


def _net_stub_svg(
    geometry: SankeyGeometry,
    labels: Dict[str, str],
    node_pixels: Callable[[str], Tuple[float, float, float]],
    plot_w: float,
    plot_h: float,
    pixels_per_unit: float,
    stub_labels: Optional[Dict[str, str]] = None,
) -> List[str]:
    """The net-position stubs that close a node's deficient face (Q29 R7).

    Drawn as a short flat band off the face the ribbons do not fill, labelled with the signed
    amount, and deliberately unlike a ribbon: flat, muted and ending in mid-air rather than at
    another node, because it is a *position* and not a payment to anybody. A node that receives
    more than it passes on gets a `+` stub on its right face; one that pays out more than it takes
    in gets a `-` stub on its left, which is the leftover convention the statement income Sankey
    uses as well.

    Args:
        geometry: The layout `presentation_style.sankey_node_boxes` returned; its `net_stubs` are
            what this draws and its `boxes` decide which of them have a rectangle to hang off.
        labels: Node label per node key, for the stub's tooltip.
        node_pixels: The caller's node-to-user-units mapping, so both live in one coordinate
            system.
        plot_w: Width of the plot area in user units.
        plot_h: Height of the plot area in user units.
        pixels_per_unit: The diagram's one global unit scale, in user units per euro.
        stub_labels: The caller's own wording per node, overriding the geometric default.

    Returns:
        The stub rectangles and their labels, for the caller to extend its parts list with.
    """
    parts: List[str] = []
    for stub in geometry.net_stubs:
        if stub.node not in geometry.boxes:
            continue
        x, y, node_height = node_pixels(stub.node)
        stub_h = stub.amount * pixels_per_unit
        top = y + node_height - stub.anchor * plot_h - stub_h
        length = SankeyLayout.STUB_LENGTH * plot_w
        if stub.is_outgoing:
            x0 = x + SankeyLayout.NODE_WIDTH * plot_w
            text_x, anchor = x0 + length + 4, "start"
        else:
            x0, text_x, anchor = x - length, x - length - 4, "end"
        sign = "+" if stub.is_outgoing else "-"
        text = (stub_labels or {}).get(stub.node, f"net {sign}{_fmt(stub.amount)} EUR")
        tooltip = f"{labels.get(stub.node, stub.node)}: {text} ({stub.amount:,.2f} EUR unmatched)"
        parts.append(
            f'<rect class="net-stub" x="{x0:.1f}" y="{top:.1f}" width="{max(length, 0.1):.1f}" '
            f'height="{max(stub_h, 0.1):.1f}" fill="var(--muted)" fill-opacity="0.22" '
            f'stroke="var(--muted)" stroke-width="0.6" stroke-dasharray="2 2">'
            f"<title>{_esc(tooltip)}</title></rect>"
        )
        parts.append(_text(text_x, top + stub_h / 2 + 3, text, 9, anchor, "var(--muted)"))
    return parts


def _ribbon_title(
    labels: Dict[str, str],
    source: str,
    target: str,
    amount: float,
    ribbon_tooltips: Optional[List[str]],
    index: int,
) -> str:
    """The hover text of one ribbon: the caller's exact string, else the rounded default (Q28)."""
    if ribbon_tooltips is not None and index < len(ribbon_tooltips):
        return ribbon_tooltips[index]
    return f"{labels.get(source, source)} -> {labels.get(target, target)}: {_fmt(amount)}"


class _SankeyLabels:
    """How a Sankey node label and a ribbon are styled, for the amount lines of Q28 R6.

    A node label is drawn beside its rectangle, so two lines of it (the name and the amounts)
    only fit where the rectangle is at least as tall as the two baselines they occupy. Below that
    the amount line degrades to the node total alone, and the split stays in the node's tooltip —
    the Q28 rule that a small node may lose the breakdown but never the number.

    `COST_STYLE` and `CREDIT_STYLE` are here rather than inline in the path because they are the
    one place the cost/credit distinction is made visible: a ribbon has no sign, so it has to be
    carried structurally — a credit is outlined, translucent and dashed where a cost is solid.
    """

    #: Node height (user units) from which the full "costs X | credits -Y" line is drawn.
    MIN_HEIGHT_FOR_SPLIT = 20.0
    #: Font size of the amount line; one step below the name it sits under.
    AMOUNT_FONT_SIZE = 9
    #: Baseline offsets of the name and the amount line, measured from the node's vertical middle.
    NAME_OFFSET = -1.0
    AMOUNT_OFFSET = 9.0

    #: `(fill opacity, stroke width, extra stroke attributes)` of a ribbon, per kind of flow.
    COST_STYLE = (0.5, 0.5, "")
    CREDIT_STYLE = (0.18, 0.8, ' stroke-dasharray="4 3"')


def _sankey_svg(
    columns: List[List[str]],
    ribbons: List[Tuple[str, str, float, str, bool]],
    labels: Dict[str, str],
    height: int = 360,
    tooltips: Optional[Dict[str, str]] = None,
    sublabels: Optional[Dict[str, Tuple[str, str]]] = None,
    ribbon_tooltips: Optional[List[str]] = None,
    stub_labels: Optional[Dict[str, str]] = None,
) -> str:
    """A column Sankey as inline SVG: node rectangles plus one Bezier ribbon per flow.

    The shared renderer of the actor-flow diagram and of the statement income diagram. Node
    placement comes from `presentation_style.sankey_node_boxes`, the same function the matplotlib
    companions use, so a node sits in the same place in both outputs. Ribbons leave a node's right
    face and arrive at the next node's left face in the order given, stacking on each face, so a
    node rectangle is exactly filled by the ribbons it carries.

    Every ribbon keeps **one width from end to end**, taken from the single global unit scale
    `sankey_node_boxes` returns (rule 2.7), and the ribbons on a node face stack to tile it
    exactly. Every flow travels between two *different* columns: since Q23 gave each internal
    party a column of its own, even an inter-actor transfer such as the §559e levy is an ordinary
    left-to-right ribbon, and the looping same-column band this used to draw is gone.

    Args:
        columns: Node keys per column, left to right.
        ribbons: `(source, target, amount, colour, is_credit)` per flow, in drawing order; a
            credit is drawn in `_SankeyLabels.CREDIT_STYLE`, and the sections that use it carry a
            legend saying so.
        labels: Visible label per node key; a key without one is labelled with itself.
        height: Canvas height in user units.
        tooltips: Hover text per node where the visible label is not the whole truth — a subsidy
            node reads as its friendly scheme name and carries the raw scheme id here (Q20).
        sublabels: `(full, compact)` amount line under a node's name; the full form is drawn where
            the node is tall enough for two baselines and the compact one — the node's total —
            everywhere else, with the full split remaining in the tooltip (Q28 R6).
        ribbon_tooltips: Replaces the rounded default hover text of the ribbon at the same index
            with an exact one, which is what makes a ribbon readable to the cent without printing
            cents on the canvas.
        stub_labels: The caller's own wording for a net-position stub (Q29 R7). It matters
            wherever the section already publishes that net under a sign convention of its own —
            the who-pays-whom chart states costs as positive, so a landlord who *gains* reads
            "net -88,032 EUR" there, and a stub inventing its own `+` beside that label would
            contradict the node it closes.

    Returns:
        The complete `<svg>` element as one string.
    """
    geometry = sankey_node_boxes(columns, [(s, t, a) for s, t, a, _c, _credit in ribbons])
    boxes = geometry.boxes
    plot_w = _ChartGeometry.WIDTH - _ChartGeometry.LEFT - _ChartGeometry.RIGHT
    plot_h = height - _ChartGeometry.TOP - _ChartGeometry.BOTTOM
    pixels_per_unit = geometry.unit_scale * plot_h

    def node_pixels(node: str) -> Tuple[float, float, float]:
        """(left x, top y, height) of a node in user units."""
        x, y, node_height = boxes[node]
        return (
            _ChartGeometry.LEFT + x * plot_w,
            _ChartGeometry.TOP + (1.0 - y - node_height) * plot_h,
            node_height * plot_h,
        )

    parts = _svg_open(_ChartGeometry.WIDTH, height)
    for index, (source, target, amount, color, is_credit) in enumerate(ribbons):
        if source not in boxes or target not in boxes:
            continue
        title = _esc(_ribbon_title(labels, source, target, amount, ribbon_tooltips, index))
        parts.extend(
            _ribbon_legs_svg(
                geometry.ribbon_segments[index], boxes, node_pixels,
                amount * pixels_per_unit, plot_h, plot_w, color, is_credit, title,
            )
        )
    parts.extend(
        _net_stub_svg(geometry, labels, node_pixels, plot_w, plot_h, pixels_per_unit, stub_labels)
    )
    for index, nodes in enumerate(columns):
        for node in nodes:
            if node not in boxes:
                continue
            x, y, node_height = node_pixels(node)
            parts.append(
                _rect(x, y, SankeyLayout.NODE_WIDTH * plot_w, node_height, "var(--ink-1)",
                      (tooltips or labels).get(node, labels.get(node, node)), rx=1)
            )
            parts.extend(
                _sankey_node_label(node, labels, sublabels, index, len(columns),
                                   (x, y, node_height), plot_w)
            )
    parts.append("</svg>")
    return "".join(parts)


def _ribbon_legs_svg(
    segments: Sequence[RibbonSegment],
    boxes: Dict[str, Tuple[float, float, float]],
    node_pixels: Callable[[str], Tuple[float, float, float]],
    ribbon_h: float,
    plot_h: float,
    plot_w: float,
    color: str,
    is_credit: bool,
    title: str,
) -> List[str]:
    """One flow's Bezier bands: one leg per column gap it travels (Q29 R7).

    A ribbon that skips a column is routed through the corridor the layout reserved for it rather
    than drawn as one long curve across whatever block sits in the way, which is why a flow can
    be more than one path element. Every leg is emitted at the same `ribbon_h`, so the flow keeps
    one width from end to end however many legs it took.

    Args:
        segments: The legs `sankey_node_boxes` routed this flow through, in travel order.
        boxes: The layout's node boxes, to skip a leg whose ends were dropped.
        node_pixels: The caller's node-to-user-units mapping.
        ribbon_h: The flow's width in user units — the same for every leg.
        plot_h: Height of the plot area, which the layout's anchors are a fraction of.
        plot_w: Width of the plot area, which the node width is a fraction of.
        color: Fill and stroke colour of the band.
        is_credit: Draws the credit style (outlined, translucent, dashed) instead of the cost one.
        title: The already-escaped hover text, repeated on every leg of the flow.

    Returns:
        One `<path>` per leg, in travel order.
    """
    opacity, stroke_width, dash = (
        _SankeyLabels.CREDIT_STYLE if is_credit else _SankeyLabels.COST_STYLE
    )
    parts: List[str] = []
    for leg in segments:
        if leg.source not in boxes or leg.target not in boxes:
            continue
        source_x, source_y, source_h = node_pixels(leg.source)
        target_x, target_y, target_h = node_pixels(leg.target)
        # y grows downward here and upward in the layout, so an anchor measured from the node's
        # bottom becomes a distance from its top face read the other way round.
        y0 = source_y + source_h - leg.out_anchor * plot_h - ribbon_h
        y1 = target_y + target_h - leg.in_anchor * plot_h - ribbon_h
        x0 = source_x + SankeyLayout.NODE_WIDTH * plot_w
        control = (target_x - x0) * SankeyLayout.CURVATURE
        parts.append(
            f'<path d="M {x0:.1f},{y0:.1f} C {x0 + control:.1f},{y0:.1f} '
            f'{target_x - control:.1f},{y1:.1f} {target_x:.1f},{y1:.1f} '
            f'L {target_x:.1f},{y1 + ribbon_h:.1f} C {target_x - control:.1f},{y1 + ribbon_h:.1f} '
            f'{x0 + control:.1f},{y0 + ribbon_h:.1f} {x0:.1f},{y0 + ribbon_h:.1f} Z" '
            f'fill="{color}" fill-opacity="{opacity}" stroke="{color}" '
            f'stroke-width="{stroke_width}"{dash}>'
            f"<title>{title}</title></path>"
        )
    return parts


def _sankey_node_label(
    node: str,
    labels: Dict[str, str],
    sublabels: Optional[Dict[str, Tuple[str, str]]],
    index: int,
    column_count: int,
    box: Tuple[float, float, float],
    plot_w: float,
) -> List[str]:
    """One node's name, and its amount line where the caller supplied one (Q28 R6).

    Split out of `_sankey_svg` because where a label sits is a question of which column the node
    is in — outside the diagram for the first and the last, above the rectangle in between — and
    that branch plus the two-line/one-line degradation is the whole of it.

    Args:
        node: The node key being labelled.
        labels: Visible label per node key.
        sublabels: `(full, compact)` amount line per node, or None for no amount line.
        index: Index of the node's column.
        column_count: Number of columns, so the last one can be recognized.
        box: `(left x, top y, height)` of the node rectangle in user units.
        plot_w: Width of the plot area, which the node width is a fraction of.

    Returns:
        The one or two `<text>` elements of this node's label.
    """
    x, y, node_height = box
    label = labels.get(node, node)
    amounts = (sublabels or {}).get(node)
    middle = y + node_height / 2
    if index == 0:
        anchor_x, anchor = x - 6, "end"
    elif index == column_count - 1:
        anchor_x, anchor = x + SankeyLayout.NODE_WIDTH * plot_w + 6, "start"
    else:
        anchor_x, anchor = x + SankeyLayout.NODE_WIDTH * plot_w / 2, "middle"
    if amounts is None:
        if index in (0, column_count - 1):
            return [_text(anchor_x, middle + 4, label, 10, anchor)]
        return [_text(anchor_x, y - 4, label, 10, anchor, "var(--ink-1)")]
    full, compact = amounts
    amount_line = full if node_height >= _SankeyLabels.MIN_HEIGHT_FOR_SPLIT else compact
    return [
        _text(anchor_x, middle + _SankeyLabels.NAME_OFFSET, label, 10, anchor),
        _text(anchor_x, middle + _SankeyLabels.AMOUNT_OFFSET, amount_line,
              _SankeyLabels.AMOUNT_FONT_SIZE, anchor, "var(--muted)"),
    ]


def _xy_lines_svg(
    series: List[Tuple[str, List[Tuple[float, float]], str, float, str]],
    bands: Optional[List[Tuple[List[Tuple[float, float]], List[Tuple[float, float]], str]]] = None,
    x_label: str = "year",
    y_label: str = "",
    annotations: Optional[List[Tuple[float, float, str]]] = None,
    height: int = 230,
) -> str:
    """An xy line chart with optional filled bands — the cash-curve fan, the loan balance.

    Each series is `(label, points, colour, stroke width, dash pattern)`; each band is a pair of
    point lists filled between them, which is how an uncertainty envelope and the gap between two
    curves are expressed with one primitive. The axes span everything given, always including
    zero so the sign of a curve is readable, and the zero line is drawn.

    Args:
        series: The lines to draw, each with its own colour, stroke width and dash pattern.
        bands: `(lower points, upper points, colour)` polygons filled under the lines.
        x_label: Axis caption printed at the bottom left.
        y_label: Axis caption printed at the top left; omitted when empty.
        annotations: `(x, y, text)` labels in data units, placed with a small offset — what
            carries the cash curve's "deepest out-of-pocket" marker.
        height: Canvas height in user units.

    Returns:
        The complete `<svg>` element, or the empty string when there is nothing to plot.
    """
    bands = bands or []
    annotations = annotations or []
    all_points = [point for _label, points, *_rest in series for point in points]
    for low, high, _color in bands:
        all_points.extend(low + high)
    if not all_points:
        return ""
    x_values = [point[0] for point in all_points]
    y_values = [point[1] for point in all_points]
    x_min, x_max = min(x_values), max(x_values)
    y_min, y_max = min(y_values + [0.0]), max(y_values + [0.0])
    plot_w = _ChartGeometry.WIDTH - _ChartGeometry.LEFT - _ChartGeometry.RIGHT
    plot_h = height - _ChartGeometry.TOP - _ChartGeometry.BOTTOM
    x_scale = plot_w / max(x_max - x_min, 1e-9)
    y_scale = plot_h / max(y_max - y_min, 1e-9)

    def to_x(value: float) -> float:
        """Data x to user units."""
        return _ChartGeometry.LEFT + (value - x_min) * x_scale

    def to_y(value: float) -> float:
        """Data y to user units, which grow downward."""
        return _ChartGeometry.TOP + (y_max - value) * y_scale

    parts = _svg_open(_ChartGeometry.WIDTH, height)
    parts.append(_hline(_ChartGeometry.LEFT, _ChartGeometry.WIDTH - _ChartGeometry.RIGHT + 20, to_y(0.0)))
    for low, high, color in bands:
        forward = " ".join(f"{to_x(x):.1f},{to_y(y):.1f}" for x, y in low)
        backward = " ".join(f"{to_x(x):.1f},{to_y(y):.1f}" for x, y in reversed(high))
        parts.append(
            f'<polygon points="{forward} {backward}" fill="{color}" opacity="0.16"></polygon>'
        )
    end_labels: List[Tuple[float, float, str]] = []
    for label, points, color, stroke, dash in series:
        if not points:
            continue
        drawn = " ".join(f"{to_x(x):.1f},{to_y(y):.1f}" for x, y in points)
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        parts.append(
            f'<polyline points="{drawn}" fill="none" stroke="{color}" stroke-width="{stroke}"'
            f"{dash_attr}><title>{_esc(label)}</title></polyline>"
        )
        end_labels.append((to_x(points[-1][0]) + 5, to_y(points[-1][1]) + 4, label))
    for x, y, label in _declutter_labels(end_labels):
        parts.append(_text(x, y, label, 9, "start", "var(--muted)"))
    for x, y, text in annotations:
        parts.append(_text(to_x(x) + 5, to_y(y) - 6, text, 9, "start", "var(--ink-1)"))
    # The axis carries three labels — top, bottom and zero — and drops either extreme when it *is*
    # zero, so an all-positive series does not print "0" twice on the same spot.
    if y_max:
        parts.append(_text(_ChartGeometry.LEFT - 6, to_y(y_max) + 8, _fmt(y_max), 9, "end", "var(--muted)"))
    if y_min:
        parts.append(_text(_ChartGeometry.LEFT - 6, to_y(y_min) + 4, _fmt(y_min), 9, "end", "var(--muted)"))
    parts.append(_text(_ChartGeometry.LEFT - 6, to_y(0.0) + 4, "0", 9, "end", "var(--muted)"))
    parts.append(_text(_ChartGeometry.LEFT, height - 6, x_label, 9, "start", "var(--muted)"))
    if y_label:
        parts.append(_text(_ChartGeometry.LEFT, _ChartGeometry.TOP - 2, y_label, 9, "start", "var(--muted)"))
    parts.append("</svg>")
    return "".join(parts)


def _declutter_labels(
    labels: List[Tuple[float, float, str]], minimum_spacing: float = 11.0
) -> List[Tuple[float, float, str]]:
    """Pushes end-of-line labels apart so none is written on top of another.

    A fan of trajectories, or three lines converging at the horizon, ends with all its labels
    within a few user units of each other, which renders as an unreadable smear. Sorting them by y
    and enforcing a minimum spacing downward keeps every label present — the alternative, dropping
    some, would silently hide which line is which.

    The shift is cosmetic and applies to the *label*, never to the line it names; a label that has
    moved still starts at the line's own end point horizontally, so the association stays visible.

    Args:
        labels: `(x, y, text)` of every end-of-line label, in user units.
        minimum_spacing: Smallest vertical gap two labels may end up with.

    Returns:
        The same labels, ordered by y, with the overlapping ones pushed down.
    """
    ordered = sorted(labels, key=lambda item: item[1])
    placed: List[Tuple[float, float, str]] = []
    for x, y, label in ordered:
        if placed and y - placed[-1][1] < minimum_spacing:
            y = placed[-1][1] + minimum_spacing
        placed.append((x, y, label))
    return placed


def _gantt_svg(
    rows: List[Tuple[str, List[Tuple[int, Optional[int], str]], List[Tuple[int, str, Optional[float]]], str]],
    horizon: int,
) -> str:
    """A swimlane/Gantt strip — the component lifetimes and the lifecycle overview.

    One row per lane: muted span bars, ticked event markers and vertical gridlines every five
    years. Rows go through `_bar_row`, so a lane label sits exactly where a subject label does in
    the bar charts above.

    Args:
        rows: `(lane label, spans, events, colour)` per lane, where a span is `(start year, end
            year or None for "to the horizon", span label)` and an event is `(year, label, amount
            in euro or None)`.
        horizon: Last year of the axis, in years from year 0.

    Returns:
        The complete `<svg>` element, or the empty string when there is no lane to draw.
    """
    if not rows:
        return ""
    height = len(rows) * _ChartGeometry.ROW_HEIGHT + 40
    plot_w = _ChartGeometry.WIDTH - _ChartGeometry.LEFT - _ChartGeometry.RIGHT
    scale = plot_w / max(horizon, 1)

    def to_x(year: float) -> float:
        """Year to user units."""
        return _ChartGeometry.LEFT + year * scale

    parts = _svg_open(_ChartGeometry.WIDTH, height)
    for year in range(0, horizon + 1, 5):
        parts.append(
            f'<line x1="{to_x(year):.1f}" y1="6" x2="{to_x(year):.1f}" y2="{height - 26}" '
            f'stroke="var(--grid)"/>'
        )
        parts.append(_text(to_x(year), height - 10, str(year), 9, "middle", "var(--muted)"))
    y = 10.0
    for label, spans, events, color in rows:
        bars: List[_Bar] = []
        for start, end, span_label in spans:
            end_year = end if end is not None else horizon
            bars.append((to_x(start), max((end_year - start) * scale, 2.0), color,
                         f"{span_label} ({start}-{end_year})", 3.0))
        parts.extend(
            _bar_row(label, y, _ChartGeometry.ROW_HEIGHT, _ChartGeometry.LEFT, bars,
                     marks=_gantt_event_marks(events, to_x, y), inset=5.0)
        )
        y += _ChartGeometry.ROW_HEIGHT
    parts.append("</svg>")
    return "".join(parts)


def _gantt_event_marks(
    events: List[Tuple[int, str, Optional[float]]],
    to_x: Callable[[float], float],
    y: float,
) -> List[str]:
    """The dated tick marks of one Gantt lane, labelled only where they do not collide.

    Every event keeps its marker and its tooltip; only the printed text is dropped for an event
    within `_ChartGeometry.CLUSTER_YEARS` of the last labelled one, which is what keeps a year 0
    carrying five awards readable without hiding any of them.

    Args:
        events: `(year, label, amount in euro or None)` per event, in any order.
        to_x: The lane's year-to-user-units mapping.
        y: Top of the lane in user units.

    Returns:
        The tick lines and the surviving labels, in drawing order.
    """
    parts: List[str] = []
    last_labelled: Optional[int] = None
    for year, event_label, amount in sorted(events, key=lambda item: item[0]):
        tooltip = event_label if amount is None else f"{event_label}: {_fmt(amount)} EUR"
        parts.append(
            f'<line x1="{to_x(year):.1f}" y1="{y + 2:.1f}" x2="{to_x(year):.1f}" '
            f'y2="{y + _ChartGeometry.ROW_HEIGHT - 4:.1f}" stroke="var(--ink-1)" '
            f'stroke-width="2"><title>year {year} - {_esc(tooltip)}</title></line>'
        )
        if last_labelled is None or year - last_labelled > _ChartGeometry.CLUSTER_YEARS:
            text = event_label if amount is None else f"{event_label} {_fmt(amount)}"
            parts.append(_text(to_x(year) + 4, y + 10, text, 8, "start", "var(--muted)"))
            last_labelled = year
    return parts


def _bridge_svg(
    anchors: Tuple[Tuple[str, UncertainValue], Tuple[str, UncertainValue]],
    steps: List[Tuple[str, float, str]],
) -> str:
    """The NPV bridge: two anchor bars with their bands and the floating deltas between them.

    A bridge is not the same shape as the report's older waterfall, which starts at zero and
    walks a staircase of contributions: here the two *anchors* are absolute NPVs and the bars
    between them float at wherever the running total has got to. The axis is therefore fitted
    over the whole excursion of that running total (and always includes zero), which is what
    keeps a large negative first step on the canvas.

    The anchors carry a min/max whisker and are drawn as emphasized rows; the delta bars
    deliberately carry no whisker, because the band of a difference is not the difference of the
    bands.

    Args:
        anchors: `(label, band)` of the reference and of the variant, in that order.
        steps: `(label, delta in euro, colour)` per contribution, in drawing order.

    Returns:
        The complete `<svg>` element.
    """
    (base_label, base_band), (variant_label, variant_band) = anchors
    cursors = [base_band.best_estimate]
    for _label, delta, _color in steps:
        cursors.append(cursors[-1] + delta)
    span_min = min([0.0, base_band.minimum, variant_band.minimum] + cursors)
    span_max = max([0.0, base_band.maximum, variant_band.maximum] + cursors)
    height = (len(steps) + 2) * _ChartGeometry.ROW_HEIGHT + 24
    plot_w = _ChartGeometry.WIDTH - _ChartGeometry.LEFT - _ChartGeometry.RIGHT
    scale = plot_w / max(span_max - span_min, 1e-9)

    def to_x(value: float) -> float:
        """Euro to user units."""
        return _ChartGeometry.LEFT + (value - span_min) * scale

    def anchor_row(label: str, band: UncertainValue, position: float) -> List[str]:
        """One absolute NPV bar with its band, drawn from the zero line."""
        return _bar_row(
            label, position, _ChartGeometry.ROW_HEIGHT, _ChartGeometry.LEFT,
            bars=[(min(to_x(0.0), to_x(band.best_estimate)),
                   abs(to_x(band.best_estimate) - to_x(0.0)), "var(--ink-1)",
                   f"{label}: {_band_str(band)}", 3.0)],
            marks=[_hline(to_x(band.minimum), to_x(band.maximum),
                          position + _ChartGeometry.ROW_HEIGHT / 2, "var(--muted)", 1.4)],
            value=(to_x(band.maximum) + 6, _band_str(band), "start"),
            emphasis=True,
        )

    parts = _svg_open(_ChartGeometry.WIDTH, height)
    y = 6.0
    parts.extend(anchor_row(base_label, base_band, y))
    y += _ChartGeometry.ROW_HEIGHT
    cursor = base_band.best_estimate
    for label, delta, color in steps:
        start = min(cursor, cursor + delta)
        cursor += delta
        parts.extend(
            _bar_row(
                label, y, _ChartGeometry.ROW_HEIGHT, _ChartGeometry.LEFT,
                bars=[(to_x(start), abs(delta) * scale, color, f"{label}: {_fmt(delta)} EUR", 3.0)],
                marks=[
                    f'<line x1="{to_x(cursor):.1f}" y1="{y + 5:.1f}" x2="{to_x(cursor):.1f}" '
                    f'y2="{y + _ChartGeometry.ROW_HEIGHT + 5:.1f}" stroke="var(--grid)"/>'
                ],
                value=(
                    to_x(start) + abs(delta) * scale + 6 if delta >= 0 else to_x(start) - 6,
                    f"{'+' if delta >= 0 else ''}{_fmt(delta)}",
                    "start" if delta >= 0 else "end",
                ),
                inset=5.0,
            )
        )
        y += _ChartGeometry.ROW_HEIGHT
    parts.extend(anchor_row(variant_label, variant_band, y))
    parts.append(
        f'<line x1="{to_x(0.0):.1f}" y1="2" x2="{to_x(0.0):.1f}" y2="{height - 18}" '
        f'stroke="var(--baseline)"/>'
    )
    parts.append("</svg>")
    return "".join(parts)


def _attribution_tornado_svg(rows: List[views.AttributionRow], total: UncertainValue) -> str:
    """The uncertainty tornado: each subject's LOW and HIGH deltas around the best-estimate NPV.

    Bars run left for a negative delta and right for a positive one from a zero axis that *is*
    the total best-estimate NPV. A mirrored revenue subject can have a positive LOW delta, which
    puts its whole bar on one side — correct, and the section's prose says so, because it looks
    like a bug the first time.

    Args:
        rows: The attribution `views.uncertainty_attribution` returned, in its own order.
        total: The total NPV band the deltas attribute, printed under the axis.

    Returns:
        The complete `<svg>` element, or the empty string when there is nothing to attribute.
    """
    if not rows:
        return ""
    height = len(rows) * _ChartGeometry.ROW_HEIGHT + 30
    span = max(
        max(abs(row.low_delta_in_euro), abs(row.high_delta_in_euro)) for row in rows
    ) or 1.0
    plot_w = _ChartGeometry.WIDTH - _ChartGeometry.LEFT - _ChartGeometry.RIGHT
    zero_x = _ChartGeometry.LEFT + plot_w / 2
    scale = (plot_w / 2) / span
    parts = _svg_open(_ChartGeometry.WIDTH, height)
    parts.append(
        f'<line x1="{zero_x:.1f}" y1="4" x2="{zero_x:.1f}" y2="{height - 20}" stroke="var(--baseline)"/>'
    )
    y = 6.0
    for row in rows:
        bars: List[_Bar] = [
            (zero_x + min(delta, 0.0) * scale, abs(delta) * scale, color,
             f"{row.subject}: {_fmt(delta)} EUR", 2.0)
            for delta, color in ((row.low_delta_in_euro, "var(--g0)"),
                                 (row.high_delta_in_euro, "var(--g5)"))
            if delta
        ]
        parts.extend(
            _bar_row(
                row.subject, y, _ChartGeometry.ROW_HEIGHT, _ChartGeometry.LEFT, bars,
                value=(zero_x + max(row.high_delta_in_euro, 0.0) * scale + 6,
                       f"{_fmt(row.low_delta_in_euro)} | {_fmt(row.high_delta_in_euro)}", "start"),
                inset=6.0, value_size=9,
            )
        )
        y += _ChartGeometry.ROW_HEIGHT
    parts.append(
        _text(zero_x, height - 6, f"total band {_band_str(total)}", 9, "middle", "var(--muted)")
    )
    parts.append("</svg>")
    return "".join(parts)


def _cost_of_credit_svg(credit: views.TotalCostOfCredit) -> str:
    """The loan's companion panel: one stacked bar of principal, interest, fees and the grant.

    The consumer-credit disclosure a loan document carries on its first page — "you borrow 50,000
    and pay back 63,400" — as a single bar, with the repayment grant on a row of its own below
    because it is money coming back rather than a smaller cost.

    Args:
        credit: The decomposition `views.total_cost_of_credit` returned.

    Returns:
        The complete `<svg>` element, or the empty string when nothing was actually repaid.
    """
    segments = [
        ("principal", credit.principal_in_euro - credit.unrepaid_principal_in_euro, "var(--g0)"),
        ("interest", credit.interest_in_euro, "var(--g5)"),
        ("fees", credit.fees_in_euro, "var(--g2)"),
    ]
    segments = [segment for segment in segments if segment[1]]
    if not segments:
        return ""
    height, left, row_h = 90, 150, 34
    span = max(sum(value for _label, value, _color in segments) + credit.grants_in_euro, 1e-9)
    scale = (_ChartGeometry.WIDTH - left - 150) / span
    bars: List[_Bar] = []
    marks: List[str] = []
    cursor = float(left)
    for label, value, color in segments:
        bars.append((cursor, value * scale, color, f"{label}: {_fmt(value)} EUR", 2.0))
        if value * scale > 60:
            marks.append(_text(cursor + 6, 33, f"{label} {_fmt(value)}", 9, "start", "var(--surface)"))
        cursor += value * scale
    parts = _svg_open(_ChartGeometry.WIDTH, height)
    parts.extend(
        _bar_row("you repay", 12.0, row_h, left, bars, marks,
                 value=(cursor + 8, f"total {_fmt(credit.total_repaid_in_euro)} EUR", "start"),
                 emphasis=True)
    )
    if credit.grants_in_euro:
        parts.extend(
            _bar_row(
                "grant back", 50.0, row_h, left,
                bars=[(float(left), credit.grants_in_euro * scale, "var(--g3)",
                       f"repayment grant: {_fmt(credit.grants_in_euro)} EUR", 2.0)],
                value=(left + credit.grants_in_euro * scale + 8,
                       f"-{_fmt(credit.grants_in_euro)} EUR", "start"),
                inset=8.0, value_size=9,
            )
        )
    parts.append("</svg>")
    return "".join(parts)
