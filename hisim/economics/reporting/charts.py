"""Inline-SVG primitives and the per-result charts of the HTML report (cost_spec.md §7.2).

Holds escaping, the `_svg_open`/`_rect`/`_text` primitives, tables, `<details>` blocks and the charts built from them,
plus the shared builders of the visualization set (column Sankey, xy lines, Gantt strip, treemap, NPV bridge, tornado,
monthly burden, cost of credit). It renders already-computed results and is the bottom module of the `reporting`
package: `scaffold.py`, `sections.py`, `sections_charts.py` and `assembly.py` build on it.
"""


from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from hisim.economics import views
from hisim.economics.presentation_style import (
    PresentationStyle,
    RibbonSegment,
    SankeyGeometry,
    SankeyLayout,
    group_name,
    sankey_node_boxes,
    squarified_layout,
)
from hisim.economics.results import LifecycleCostResult, VariantComparison
from hisim.economics.uncertainty import Slot, UncertainValue


from hisim.economics.reporting.summary import _band_str, _fmt


class _ReportStyle:
    """Inline style constants of the self-contained HTML/SVG output.

    SVG text does not inherit the document's CSS font, so every `<text>` element carries the font family kept here.
    Colours are not here: they are CSS custom properties (`var(--g0)`) so the charts follow light and dark mode.
    """

    SVG_FONT = 'font-family="system-ui, -apple-system, Segoe UI, sans-serif"'


def _esc(text: str) -> str:
    """HTML-escape a value on its way into the document, attributes included (`quote=True`).

    Subject names, carrier ids and scheme ids come from configs and data files, so a component named `A & B` must not
    break the page. Numbers formatted by `_fmt` contain no markup and need no escaping.
    """
    return html.escape(str(text), quote=True)


def _svg_open(width: int, height: int) -> List[str]:
    """Open a responsive `<svg>` and return it as the first element of a parts list.

    Chart builders append their marks to the list and join it once at the end. The element has a `viewBox` in the
    chart's user units, `width="100%"` and `max-width:{width}px`, so it scales down on narrow screens, and `role="img"`
    for assistive technology.
    """
    return [
        f'<svg viewBox="0 0 {width} {height}" width="100%" style="max-width:{width}px" '
        f'role="img" xmlns="http://www.w3.org/2000/svg">'
    ]


def _rect(x: float, y: float, w: float, h: float, color: str, tooltip: str, rx: float = 0.0) -> str:
    """Return one SVG `<rect>` mark with a native hover tooltip.

    `x`/`y` are the top-left corner in user units (y grows downward); `color` is normally a CSS variable; `rx` rounds
    the corners. Width and height are floored at 0.1, so a real sub-pixel segment still shows as a hairline. The gap
    between adjacent bars is the caller's: it passes e.g. `bar_w - 2`.
    """
    return (
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(w, 0.1):.1f}" height="{max(h, 0.1):.1f}" '
        f'fill="{color}" rx="{rx}"><title>{_esc(tooltip)}</title></rect>'
    )


def _text(x: float, y: float, content: str, size: int = 11, anchor: str = "start",
          color: str = "var(--ink-2)", bold: bool = False) -> str:
    """Return one SVG text label, escaped and in the report's typeface.

    `y` is the glyph baseline, and `anchor` (`start`, `middle`, `end`) chooses which end of the string sits at `x`.
    That is why row labels are drawn at `left - 8` with `anchor="end"` and at `row_middle + 4`.
    """
    weight = ' font-weight="600"' if bold else ""
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" text-anchor="{anchor}" '
        f'fill="{color}" {_ReportStyle.SVG_FONT}{weight}>{_esc(content)}</text>'
    )


def _hline(x1: float, x2: float, y: float, color: str = "var(--baseline)", width: float = 1.0) -> str:
    """Return a horizontal line in user units: a chart's zero line or a min-to-max whisker.

    Axis lines use the default `--baseline` colour and a hairline; whiskers pass a group colour and a heavier stroke. A
    call with `x1 == x2` draws nothing visible; charts that need a vertical rule write their own `<line>`.
    """
    return f'<line x1="{x1:.1f}" y1="{y:.1f}" x2="{x2:.1f}" y2="{y:.1f}" stroke="{color}" stroke-width="{width}"/>'


#: One bar of a `_bar_row`: `(x, width, colour, tooltip, corner radius)` in user units. The row's
#: y and height follow from the row.
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
    """Return one row of a horizontal bar chart: label, bars, extra marks and value label.

    The label ends at `left - 8` on the row's optical centre `y + row_h / 2 + 4`; bars span `y + inset` to `y + row_h -
    inset`; the value label sits on the same centre line. Sharing this keeps the bar charts aligned. The caller owns
    the row height and advances `y` itself.

    Args:
        label: The row's name, drawn right-aligned before the plot area.
        y: Top of the row in user units.
        row_h: Row height in user units.
        left: Left edge of the plot area.
        bars: `(x, width, colour, tooltip, rx)` per bar, drawn in order.
        marks: Pre-rendered SVG drawn after the bars, such as the whisker and dot of a banded row.
        value: `(x, text, anchor)` of the figure printed next to the bar, or None for no figure.
        inset: Vertical gap between the row edge and its bars.
        emphasis: Draws the label and value bold in the ink colour, for a total row.
        value_size: Font size of the value label.

    Returns:
        The row's SVG parts in drawing order.
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
    """Return a plain HTML result table; every cell must already be a string.

    Cells are inserted verbatim, so a caller can emit `<b>` or `<a>` and must escape external strings with `_esc`
    itself. Rows shorter than the headers render with fewer cells.
    """
    head = "".join(f"<th>{header}</th>" for header in headers)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f"<table><tr>{head}</tr>{body}</table>"


def _details(summary: str, content: str, open_by_default: bool = False) -> str:
    """Wrap content in a native collapsible `<details>` block.

    Keeps the detail tables (cash flows, sources, awards) one click away without script. `summary` is inserted
    verbatim, so callers escape it.
    """
    open_attr = " open" if open_by_default else ""
    return f"<details{open_attr}><summary>{summary}</summary>{content}</details>"


def _category_table(result: LifecycleCostResult) -> str:
    """Return the NPV table by display group and by raw cost category (§3.7).

    A display group is one of the eight coloured groups the charts use; each is printed bold with its total and the raw
    `CostCategory` members under it. Groups and categories that are zero in every band slot (minimum, best estimate,
    maximum) are skipped. The sums come from `views.fold_categories`.
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
    """Return the loan amortization chart: principal and interest per year (§4.4).

    Bars stack principal from the baseline with interest on top, so a bar's height is the year's debt service; an
    annuity loan shows a constant total with interest falling. X-ticks are thinned to every `horizon // 10` years, and
    the interest segment has a 0.5 px floor so a nearly repaid year stays visible.

    Returns:
        The `<svg>` element, or the empty string for an unfinanced perspective.
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
    """Return the colour chips for the display groups a chart actually drew.

    Takes group indices, so swatch and label come from the same `PresentationStyle` entry that coloured the marks.
    """
    chips = "".join(
        f'<span class="chip"><span class="swatch" style="background:var(--g{index})"></span>'
        f"{_esc(group_name(index))}</span>"
        for index in groups_present
    )
    return f'<div class="legend">{chips}</div>'


def _annual_flow_svg(result: LifecycleCostResult) -> str:
    """Return the annual cash-flow chart: stacked nominal bars per year by display group.

    Costs stack above the axis and credits below. Replacements should spike at component lifetimes, the residual value
    appear at the horizon and the investment in year 0. The geometry and stacking are `_StackedYearsFrame` and
    `_stacked_year_bars`, shared with the monthly-burden chart.
    """
    horizon = result.parameters.observation_period_in_years
    per_year: List[Dict[int, float]] = views.fold_category_matrix(
        views.nominal_annual_matrix_by_category(result), PresentationStyle.CATEGORY_TO_GROUP
    )
    max_pos = max((sum(v for v in year.values() if v > 0) for year in per_year), default=1.0)
    max_neg = max((-sum(v for v in year.values() if v < 0) for year in per_year), default=0.0)
    frame = _StackedYearsFrame.fitted(max_pos, max_neg, horizon, height=300, bottom=34)
    parts = _stacked_year_bars(
        per_year, frame, horizon, "EUR", hairline=True, tick_size=10, tick_y=frame.height - 14
    )
    parts.append(_text(frame.left - 6, frame.zero_y + 4, "0", 10, "end", "var(--muted)"))
    parts.append(_text(frame.left - 6, frame.top + 10, _fmt(max_pos), 10, "end", "var(--muted)"))
    if max_neg:
        parts.append(
            _text(frame.left - 6, frame.height - frame.bottom, f"-{_fmt(max_neg)}", 10, "end",
                  "var(--muted)")
        )
    parts.append(_text(frame.width - 10, frame.height - 14, "year", 10, "end", "var(--muted)"))
    parts.append("</svg>")
    return "".join(parts)


def _cumulative_npv_svg(result: LifecycleCostResult) -> str:
    """Return the cumulative discounted cost curve with its min/max band.

    The curve ends at the perspective's NPV, the same figure the perspective table prints, since both come from
    `views.cumulative_discounted_cost_series`. The drawing is delegated to `_xy_lines_svg`, with the NPV band as the
    series label printed at the end point.

    Args:
        result: The perspective whose discounted cumulative cost is drawn.

    Returns:
        The complete `<svg>` element.
    """
    cumulative = views.cumulative_discounted_cost_series(result)
    years = list(range(len(cumulative[Slot.BEST_ESTIMATE])))
    return _xy_lines_svg(
        series=[(f"NPV {_band_str(result.total_npv_in_euro)}",
                 list(zip(years, cumulative[Slot.BEST_ESTIMATE])), "var(--g0)", 2.2, "")],
        bands=[(list(zip(years, cumulative[Slot.LOW])),
                list(zip(years, cumulative[Slot.HIGH])), "var(--g0)")],
        y_label="cumulative discounted cost [EUR] - ends at the NPV",
    )


def _waterfall_svg(steps: List[Tuple[str, float, str]], total_label: str, net: float) -> str:
    """Return a horizontal waterfall of `(label, signed value, colour variable)` steps ending in a net bar.

    Sections 2 (year-0 gross to net outflow) and 8 (each subject's NPV delta to the total delta) use it. Each step
    starts where the previous one ended, in the caller's order; a negative step runs leftward with its label on the
    left. `net` is passed in because it is a result figure and the steps may not add up to it exactly (the comparison
    drops sub-cent subjects). The scale fits the larger of the summed absolute steps and the net.
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
    """Return a dot-and-whisker chart of labelled banded figures on one axis.

    A dot at the best estimate and a bar from minimum to maximum, used for perspectives, payers and per-carrier bills.
    The whiskers are the §3.9 envelope of the low and high worlds, not a statistical interval. The axis always includes
    zero, the band is printed beside each whisker, and a vertical rule marks zero when a row is negative.
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
        # A vertical rule through every row marks which side of zero a row sits on; a horizontal
        # line with identical x coordinates would draw nothing.
        zero_x = to_x(0.0)
        parts.append(
            f'<line x1="{zero_x:.1f}" y1="4" x2="{zero_x:.1f}" y2="{height - 8}" stroke="var(--baseline)"/>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _stacked_subject_svg(result: LifecycleCostResult) -> str:
    """Return the per-subject diverging stacked bars by display group (§7.4).

    A subject is one costed item on the timeline, e.g. a heat pump. Costs stack right of the zero line and credits
    (residual value, subsidies, feed-in, anyway credit) left, never netted; a whisker and dot mark the net NPV band on
    the same axis, so the net markers add up to the perspective's NPV. The widest cost and credit stacks, widened to
    cover the net band, set the scale, so the zero line moves between runs.
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
    """Return the comparison's payback curve per band slot; its zero crossing is the printed payback year.

    The curve is `comparison.cumulative_discounted_savings_in_euro`, the same array `discounted_payback_years` comes
    from, so drawing and number agree.
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
    # By world, not optimistic/pessimistic: which world saves most depends on which uncertainty
    # dominates the savings.
    labels = {"best_estimate": "expected", "min": "LOW world", "max": "HIGH world"}
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


# ------------------------------------------------------- shared chart builders of the visualization set
#
# Reusable builders: a column Sankey, an xy line chart with bands, a Gantt strip, a centred-axis
# tornado and the NPV bridge. Their data comes from `views.py`; the code below is geometry only, in
# y-grows-downward user units, and lays rows out with `_bar_row` so rows line up across charts.


class _ChartGeometry:
    """The pixel frame (canvas width and margins) every chart of the visualization set is drawn in.

    Shared so stacked charts line up; the left margin holds row labels and the right one value labels.
    """

    WIDTH = 860
    LEFT = 150
    RIGHT = 130
    TOP = 14
    BOTTOM = 30
    ROW_HEIGHT = 26
    #: Events closer than this many years share a label stack instead of overprinting.
    CLUSTER_YEARS = 3


@dataclass(frozen=True)
class _StackedYearsFrame:
    """The plot area of a stacked-by-year bar chart, fitted to the extent it must hold.

    Shared by the annual cash-flow and monthly-burden charts. The zero line sits `max_pos * scale` below the top, and
    one `scale` covers `max_pos + max_neg`.
    """

    width: int
    height: int
    left: float
    top: float
    bottom: float
    scale: float
    zero_y: float
    bar_w: float

    @classmethod
    def fitted(
        cls, max_pos: float, max_neg: float, horizon: int, height: int, bottom: float,
        width: int = _ChartGeometry.WIDTH, left: float = 70.0, top: float = 16.0,
    ) -> "_StackedYearsFrame":
        """Return the frame a chart of this extent, horizon and canvas needs.

        Args:
            max_pos: The tallest positive stack, in the chart's own unit.
            max_neg: The deepest negative stack, as a positive number.
            horizon: The last year drawn; the axis carries `horizon + 1` bars.
            height: Canvas height in user units.
            bottom: Bottom margin, which carries the year ticks.
            width: Canvas width in user units.
            left: Left margin, which carries the axis labels.
            top: Top margin.

        Returns:
            The frame, with `scale`, `zero_y` and `bar_w` derived.
        """
        scale = (height - top - bottom) / max(max_pos + max_neg, 1e-9)
        return cls(
            width=width, height=height, left=left, top=top, bottom=bottom, scale=scale,
            zero_y=top + max_pos * scale, bar_w=(width - left - 20) / (horizon + 1),
        )


def _stacked_year_bars(
    per_year: Sequence[Mapping[int, float]],
    frame: _StackedYearsFrame,
    horizon: int,
    unit: str,
    hairline: bool,
    tick_size: int,
    tick_y: float,
    year_marks: Optional[Callable[[int, float], List[str]]] = None,
) -> List[str]:
    """Return the open `<svg>`, the zero line and one stacked bar per year for a stacked-by-year chart.

    Costs grow upward and credits downward from the zero line, never netted, and groups stack in
    `PresentationStyle.DISPLAY_GROUPS` order. X-ticks are drawn every `max(1, horizon // 10)` years.

    Args:
        per_year: Group index -> amount, one mapping per year, index-aligned with the year axis.
        frame: The fitted plot area.
        horizon: The last year drawn, which sets the tick thinning.
        unit: The unit the tooltips name, e.g. `"EUR"` or `"EUR/month"`.
        hairline: Whether to shorten each segment by `min(1.0, bar_h * 0.3)` so stacked groups stay distinguishable.
        tick_size: Font size of the year ticks.
        tick_y: Baseline the year ticks are printed on.
        year_marks: Optional extra marks per year, called with the year and the left edge of its bar and drawn after
            its stack.

    Returns:
        The parts so far; the caller appends its axis labels and `</svg>`.
    """
    parts = _svg_open(frame.width, frame.height)
    parts.append(_hline(frame.left, frame.width - 10, frame.zero_y))
    for year, groups in enumerate(per_year):
        x = frame.left + year * frame.bar_w
        y_pos, y_neg = frame.zero_y, frame.zero_y
        for index in range(len(PresentationStyle.DISPLAY_GROUPS)):
            value = groups.get(index, 0.0)
            if not value:
                continue
            bar_h = abs(value) * frame.scale
            drawn_h = bar_h - min(1.0, bar_h * 0.3) if hairline else bar_h
            tooltip = f"year {year} - {group_name(index)}: {_fmt(value)} {unit}"
            if value > 0:
                y_pos -= bar_h
                parts.append(_rect(x + 1, y_pos, frame.bar_w - 2, drawn_h, f"var(--g{index})", tooltip))
            else:
                parts.append(_rect(x + 1, y_neg, frame.bar_w - 2, drawn_h, f"var(--g{index})", tooltip))
                y_neg += bar_h
        if year_marks is not None:
            parts.extend(year_marks(year, x))
        if year % max(1, horizon // 10) == 0:
            parts.append(
                _text(x + frame.bar_w / 2, tick_y, str(year), tick_size, "middle", "var(--muted)")
            )
    return parts


def _net_stub_svg(
    geometry: SankeyGeometry,
    labels: Dict[str, str],
    node_pixels: Callable[[str], Tuple[float, float, float]],
    plot_w: float,
    plot_h: float,
    pixels_per_unit: float,
    stub_labels: Optional[Dict[str, str]] = None,
) -> List[str]:
    """Return the net-position stubs that close a Sankey node's unfilled face.

    A stub is a short, flat, muted band ending in mid-air, labelled with a signed amount: it is a position, not a
    payment. A node that receives more than it passes on gets a `+` stub on its right face; one that pays out more than
    it takes in gets a `-` stub on its left.

    Args:
        geometry: The layout `presentation_style.sankey_node_boxes` returned; its `net_stubs` are drawn where `boxes`
            has a rectangle for them.
        labels: Node label per node key, for the tooltip.
        node_pixels: The caller's node-to-user-units mapping.
        plot_w: Width of the plot area in user units.
        plot_h: Height of the plot area in user units.
        pixels_per_unit: The diagram's global scale, in user units per euro.
        stub_labels: The caller's own wording per node, overriding the default label.

    Returns:
        The stub rectangles and their labels.
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
    """Return the hover text of one ribbon: the caller's exact string if given, else a rounded default."""
    if ribbon_tooltips is not None and index < len(ribbon_tooltips):
        return ribbon_tooltips[index]
    return f"{labels.get(source, source)} -> {labels.get(target, target)}: {_fmt(amount)}"


class _SankeyLabels:
    """Styling of Sankey node labels and ribbons.

    A node label gets a second line with its amounts only where the rectangle is tall enough for two baselines;
    otherwise it shows the node total and the split stays in the tooltip. A ribbon has no sign, so a credit is drawn
    outlined, translucent and dashed (`CREDIT_STYLE`) where a cost is solid (`COST_STYLE`).
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
    """Return a column Sankey as inline SVG: node rectangles plus one Bezier ribbon per flow.

    Used by the actor-flow and statement income diagrams. Node placement comes from
    `presentation_style.sankey_node_boxes`, shared with the matplotlib companions. Ribbons leave a node's right face
    and arrive at the next node's left face in the given order, stacking so each face is exactly filled; every ribbon
    keeps one width end to end, from one global euro scale. Every flow runs between two different columns.

    Args:
        columns: Node keys per column, left to right.
        ribbons: `(source, target, amount, colour, is_credit)` per flow, in drawing order; a credit is drawn in
            `_SankeyLabels.CREDIT_STYLE`.
        labels: Visible label per node key; a key without one is labelled with itself.
        height: Canvas height in user units.
        tooltips: Hover text per node, e.g. the raw scheme id behind a subsidy node's friendly name.
        sublabels: `(full, compact)` amount line under a node's name; the full form where the node is tall enough, else
            the compact total.
        ribbon_tooltips: Exact hover text replacing the rounded default of the ribbon at the same index.
        stub_labels: The caller's wording for a node's net-position stub, for sections whose sign convention differs
            from the default `+`/`-` (the who-pays-whom chart states costs as positive).

    Returns:
        The complete `<svg>` element.
    """
    geometry = sankey_node_boxes(columns, [(s, t, a) for s, t, a, _c, _credit in ribbons])
    boxes = geometry.boxes
    plot_w = _ChartGeometry.WIDTH - _ChartGeometry.LEFT - _ChartGeometry.RIGHT
    plot_h = height - _ChartGeometry.TOP - _ChartGeometry.BOTTOM
    pixels_per_unit = geometry.unit_scale * plot_h

    def node_pixels(node: str) -> Tuple[float, float, float]:
        """Return (left x, top y, height) of a node in user units."""
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
    """Return one flow's Bezier bands, one leg per column gap it crosses.

    A flow that skips a column is routed through the corridor the layout reserved for it. Every leg has the same
    `ribbon_h`, so the flow keeps one width.

    Args:
        segments: The legs `sankey_node_boxes` routed this flow through, in travel order.
        boxes: The layout's node boxes; a leg whose ends were dropped is skipped.
        node_pixels: The caller's node-to-user-units mapping.
        ribbon_h: The flow's width in user units.
        plot_h: Height of the plot area.
        plot_w: Width of the plot area.
        color: Fill and stroke colour of the band.
        is_credit: Draws the credit style (outlined, translucent, dashed) instead of the cost one.
        title: The already-escaped hover text, repeated on every leg.

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
    """Return one Sankey node's name and, where supplied, its amount line.

    The first and last columns are labelled outside the diagram, middle columns above the rectangle; the amount line
    falls back to its compact form when the node is too short for two lines.

    Args:
        node: The node key being labelled.
        labels: Visible label per node key.
        sublabels: `(full, compact)` amount line per node, or None for no amount line.
        index: Index of the node's column.
        column_count: Number of columns.
        box: `(left x, top y, height)` of the node rectangle in user units.
        plot_w: Width of the plot area.

    Returns:
        The one or two `<text>` elements of the label.
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
    """Return an xy line chart with optional filled bands, e.g. the cash-curve fan or the loan balance.

    The axes span all given points and always include zero, and the zero line is drawn.

    Args:
        series: `(label, points, colour, stroke width, dash pattern)` per line.
        bands: `(lower points, upper points, colour)` polygons filled under the lines.
        x_label: Axis caption printed at the bottom left.
        y_label: Axis caption printed at the top left; omitted when empty.
        annotations: `(x, y, text)` labels in data units, such as the cash curve's "deepest out-of-pocket" marker.
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
        """Map data x to user units."""
        return _ChartGeometry.LEFT + (value - x_min) * x_scale

    def to_y(value: float) -> float:
        """Map data y to user units, which grow downward."""
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
    """Push end-of-line labels apart so none overlaps another.

    Labels are sorted by y and pushed down to a minimum spacing; none is dropped. Only the label moves, not the line it
    names, and its x is unchanged.

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
    """Return a swimlane (Gantt) strip, used for component lifetimes and the lifecycle overview.

    One row per lane with muted span bars, event tick marks and gridlines every five years; rows go through `_bar_row`,
    so lane labels align with the bar charts.

    Args:
        rows: `(lane label, spans, events, colour)` per lane; a span is `(start year, end year or None for "to the
            horizon", label)` and an event is `(year, label, amount in euro or None)`.
        horizon: Last year of the axis.

    Returns:
        The complete `<svg>` element, or the empty string when there is no lane.
    """
    if not rows:
        return ""
    height = len(rows) * _ChartGeometry.ROW_HEIGHT + 40
    plot_w = _ChartGeometry.WIDTH - _ChartGeometry.LEFT - _ChartGeometry.RIGHT
    scale = plot_w / max(horizon, 1)

    def to_x(year: float) -> float:
        """Map a year to user units."""
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
    """Return the event tick marks of one Gantt lane, labelled only where labels do not collide.

    Every event keeps its marker and tooltip; the printed label is dropped for an event within
    `_ChartGeometry.CLUSTER_YEARS` of the last labelled one.

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


def _treemap_svg(
    tiles: List[Tuple[str, float, str]], height: int = 240, width: int = _ChartGeometry.WIDTH
) -> str:
    """Return a squarified treemap, used for the cost-structure panels (one call per basis).

    The layout is `presentation_style.squarified_layout`, shared with the matplotlib companion. A tile is labelled only
    where it fits two baselines; every tile carries its label and amount as a tooltip. The caller sets the box size,
    since how many panels sit side by side is a layout decision.

    Args:
        tiles: `(label, area in euro, colour)` per rectangle, in layout order; non-positive areas are dropped.
        height: Canvas height in user units.
        width: Canvas width in user units.

    Returns:
        The complete `<svg>` element, or the empty string when no tile has a positive area.
    """
    drawable = [tile for tile in tiles if tile[1] > 0]
    if not drawable:
        return ""
    parts = _svg_open(width, height)
    layout = squarified_layout([tile[1] for tile in drawable], 0.0, 0.0, float(width), float(height))
    for (label, area, color), (x, y, tile_w, tile_h) in zip(drawable, layout):
        parts.append(_rect(x + 1, y + 1, max(tile_w - 2, 0.5), max(tile_h - 2, 0.5), color,
                           f"{label}: {_fmt(area)} EUR", rx=2))
        if tile_w > 70 and tile_h > 26:
            parts.append(_text(x + 6, y + 16, label, 9, "start", "var(--surface)"))
            parts.append(_text(x + 6, y + 28, _fmt(area), 9, "start", "var(--surface)"))
    parts.append("</svg>")
    return "".join(parts)


def _bridge_svg(
    anchors: Tuple[Tuple[str, UncertainValue], Tuple[str, UncertainValue]],
    steps: List[Tuple[str, float, str]],
) -> str:
    """Return the NPV bridge: two anchor bars with their bands and the floating deltas between them.

    The anchors are absolute NPVs drawn from zero with a min/max whisker; each delta bar floats at the running total.
    The axis covers the whole excursion of the running total and zero. Delta bars carry no whisker, since the band of a
    difference is not the difference of the bands.

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
        """Map euro to user units."""
        return _ChartGeometry.LEFT + (value - span_min) * scale

    def anchor_row(label: str, band: UncertainValue, position: float) -> List[str]:
        """Return one absolute NPV bar with its band, drawn from the zero line."""
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


#: One row of a centred-axis chart: `(label, bars, value label)` as `_bar_row` takes them. The
#: bars are already positioned against the axis, because only the caller knows what its own
#: quantity means either side of zero.
_CentredRow = Tuple[str, List[_Bar], Tuple[float, str, str]]


@dataclass(frozen=True)
class _CentredAxisFrame:
    """Layout of a chart with a centred zero axis: the axis, the rows and the footnote.

    Shared by the scenario tornado and the uncertainty-attribution tornado.

    Attributes:
        left: Left edge of the plot area; row labels end 8 units before it.
        center: The zero axis in user units, computed by the caller, which scales its bars against it.
        row_h: Row height, added to `first_y` once per row.
        first_y: Top of the first row.
        inset: Vertical gap between a row and its bars.
        text_size: Font size of the per-row value label and the footnote.
    """

    left: float
    center: float
    row_h: float
    first_y: float
    inset: float
    text_size: int


def _centred_axis_svg(rows: List[_CentredRow], frame: _CentredAxisFrame, height: int, footer: str) -> str:
    """Return a diverging bar chart: a zero axis down the middle, one `_bar_row` per row and a footnote.

    Args:
        rows: The rows in drawing order, each with its bars already placed against `frame.center`.
        frame: The chart's layout.
        height: Canvas height; the axis stops 20 units above it and the footnote sits 6 above.
        footer: What the axis means, e.g. "base: ... EUR/a", centred under it.

    Returns:
        The complete `<svg>` element.
    """
    parts = _svg_open(_ChartGeometry.WIDTH, height)
    parts.append(
        f'<line x1="{frame.center:.1f}" y1="4" x2="{frame.center:.1f}" y2="{height - 20}" '
        'stroke="var(--baseline)"/>'
    )
    y = frame.first_y
    for label, bars, value in rows:
        parts.extend(
            _bar_row(
                label, y, frame.row_h, frame.left, bars, value=value,
                inset=frame.inset, value_size=frame.text_size,
            )
        )
        y += frame.row_h
    parts.append(_text(frame.center, height - 6, footer, frame.text_size, "middle", "var(--muted)"))
    parts.append("</svg>")
    return "".join(parts)


def _attribution_tornado_svg(rows: List[views.AttributionRow], total: UncertainValue) -> str:
    """Return the uncertainty tornado: each subject's low and high deltas around the best-estimate NPV.

    The zero axis is the total best-estimate NPV; bars run left for a negative delta and right for a positive one. A
    revenue subject can have a positive low delta, putting its whole bar on one side; that is correct.

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
    drawn: List[_CentredRow] = []
    for row in rows:
        bars: List[_Bar] = [
            (zero_x + min(delta, 0.0) * scale, abs(delta) * scale, color,
             f"{row.subject}: {_fmt(delta)} EUR", 2.0)
            for delta, color in ((row.low_delta_in_euro, "var(--g0)"),
                                 (row.high_delta_in_euro, "var(--g5)"))
            if delta
        ]
        drawn.append((
            row.subject,
            bars,
            (zero_x + max(row.high_delta_in_euro, 0.0) * scale + 6,
             f"{_fmt(row.low_delta_in_euro)} | {_fmt(row.high_delta_in_euro)}", "start"),
        ))
    return _centred_axis_svg(
        drawn,
        _CentredAxisFrame(
            left=_ChartGeometry.LEFT, center=zero_x, row_h=_ChartGeometry.ROW_HEIGHT,
            first_y=6.0, inset=6.0, text_size=9,
        ),
        height,
        f"total band {_band_str(total)}",
    )


def _monthly_burden_svg(result: LifecycleCostResult, burden: views.MonthlyBurden) -> str:
    """Return the monthly-burden chart: stacked monthly bars per year with a whisker on the total.

    Same geometry as the annual cash-flow chart, on the recurring monthly figures of `views.monthly_burden_series` and
    `views.monthly_burden_by_group`. Only the monthly total is banded. Replacements are capital events and stay out of
    the bars; a dashed segment per year shows the total plus the monthly replacement reserve (the sinking fund for
    those replacements).

    Args:
        result: The perspective whose recurring burden is drawn.
        burden: Its monthly burden, as the section derived it.

    Returns:
        The complete `<svg>` element, or the empty string when the perspective books no month (a horizon of zero
            years).
    """
    totals = burden.series
    per_group = views.monthly_burden_by_group(result, PresentationStyle.CATEGORY_TO_GROUP)
    if not totals:
        return ""
    reserve = burden.replacement_reserve_per_month
    horizon = len(totals) - 1
    max_pos = max([sum(v for v in row.values() if v > 0) for row in per_group] +
                  [value.maximum for value in totals] +
                  [value.best_estimate + reserve for value in totals] + [1.0])
    max_neg = max([-sum(v for v in row.values() if v < 0) for row in per_group] +
                  [-min(value.minimum, 0.0) for value in totals] + [0.0])
    frame = _StackedYearsFrame.fitted(max_pos, max_neg, horizon, height=260, bottom=30)

    def whisker(year: int, x: float) -> List[str]:
        """Return the min/max band of that year's monthly total, or nothing when it is degenerate."""
        band = totals[year]
        if band.is_exact():
            return []
        centre = x + frame.bar_w / 2
        return [
            f'<line x1="{centre:.1f}" y1="{frame.zero_y - band.maximum * frame.scale:.1f}" '
            f'x2="{centre:.1f}" y2="{frame.zero_y - band.minimum * frame.scale:.1f}" '
            f'stroke="var(--ink-1)" stroke-width="1"><title>year {year} total: '
            f'{_esc(_band_str(band, "EUR/month"))}</title></line>'
        ]

    parts = _stacked_year_bars(
        per_group, frame, horizon, "EUR/month", hairline=False, tick_size=9,
        tick_y=frame.height - 12, year_marks=whisker,
    )
    parts.extend(_replacement_reserve_marks(totals, per_group, reserve, frame))
    parts.append(_text(frame.left - 6, frame.top + 10, _fmt(max_pos), 9, "end", "var(--muted)"))
    parts.append(_text(frame.left - 6, frame.zero_y + 4, "0", 9, "end", "var(--muted)"))
    parts.append(_text(frame.width - 10, frame.height - 12, "year", 9, "end", "var(--muted)"))
    parts.append("</svg>")
    return "".join(parts)


def _replacement_reserve_marks(
    totals: Sequence[UncertainValue],
    per_year: Sequence[Mapping[int, float]],
    reserve: float,
    frame: _StackedYearsFrame,
) -> List[str]:
    """Return the dashed replacement-reserve overlay of the monthly-burden chart and its legend.

    One segment per year at that year's recurring total plus the reserve, starting at the first year that draws a bar.
    Nothing is drawn when the reserve is zero.

    Args:
        totals: The monthly total per year, index = year.
        per_year: The stacked amounts per year, index-aligned with `totals`; a year that is all zero draws no bar and
            no segment.
        reserve: The constant monthly replacement reserve; zero means no overlay.
        frame: The chart's fitted plot area.

    Returns:
        The segments and the legend, or an empty list when there is no reserve or no bar.
    """
    first_drawn = next(
        (year for year, groups in enumerate(per_year) if any(groups.values())), None
    )
    if not reserve or first_drawn is None:
        return []
    parts: List[str] = []
    for year in range(first_drawn, len(totals)):
        band = totals[year]
        x = frame.left + year * frame.bar_w
        line_y = frame.zero_y - (band.best_estimate + reserve) * frame.scale
        parts.append(
            f'<line x1="{x + 1:.1f}" y1="{line_y:.1f}" x2="{x + frame.bar_w - 1:.1f}" '
            f'y2="{line_y:.1f}" stroke="var(--ink-1)" stroke-width="1.4" '
            f'stroke-dasharray="5 3"><title>year {year} with replacement reserve: '
            f"{_fmt(band.best_estimate + reserve)} EUR/month (of which {_fmt(reserve)} reserve)"
            f"</title></line>"
        )
    parts.append(
        _text(frame.left + 4, frame.top + 10,
              f"— — with replacement reserve (+{_fmt(reserve)} EUR/month)",
              9, "start", "var(--muted)")
    )
    return parts


def _cost_of_credit_svg(credit: views.TotalCostOfCredit) -> str:
    """Return the loan's cost-of-credit panel: one stacked bar of principal, interest and fees, plus the grant.

    Like the disclosure on a loan document ("you borrow 50,000 and pay back 63,400"). The repayment grant sits on its
    own row below, since it is money coming back.

    Args:
        credit: The decomposition `views.total_cost_of_credit` returned.

    Returns:
        The complete `<svg>` element, or the empty string when nothing was repaid.
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
