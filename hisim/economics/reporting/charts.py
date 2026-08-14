"""Inline-SVG primitives and the per-result charts of the HTML report (cost_spec.md §7.2).

The drawing layer: escaping, `_svg_open`/`_rect`/`_text` primitives, tables and
`<details>` blocks, and the charts built from them (annual flows, cumulative NPV,
waterfall, whiskers, stacked subjects, loan and payback). Everything here renders from
already-computed results; no section layout. Split out of the former single-module
`reporting.py` (PR-3 review); the package `__init__` re-exports everything.
"""


from __future__ import annotations

import html
from typing import Dict, List, Tuple

from hisim.economics import views
from hisim.economics.presentation_style import PresentationStyle
from hisim.economics.results import EvaluationMatrix, LifecycleCostResult, VariantComparison
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
    banded charts (caller passes a group colour and a heavier stroke). It is also used
    degenerately, with `x1 == x2`, to mark a point.
    """
    return f'<line x1="{x1:.1f}" y1="{y:.1f}" x2="{x2:.1f}" y2="{y:.1f}" stroke="{color}" stroke-width="{width}"/>'


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
    for index, (group_name, categories) in enumerate(PresentationStyle.DISPLAY_GROUPS):
        group_total = group_npv.get(index)
        if group_total is None or not (group_total.best_estimate or group_total.minimum or group_total.maximum):
            continue
        rows.append([f"<b>{_esc(group_name)}</b>", f"<b>{_esc(_band_str(group_total))}</b>"])
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


def _co2_section_html(matrix: EvaluationMatrix) -> str:
    """Section 4b: lifecycle CO2 (§3.8) — embodied vs. operational, per subject/carrier.

    Answers "does the emissions accounting behave like the money does, and are the two kept
    apart?" Embodied emissions (blue, keyed by component subject, booked at installation and at
    every replacement) and operational emissions (orange, keyed by energy carrier) are drawn on
    one axis so their relative size is visible, since for a well-insulated building with a heat
    pump the embodied share stops being negligible. The section's caption states the separation
    the spec insists on: these are *masses*, and neither the CO2 price (a cash flow) nor the CO2
    damage cost (a macroeconomic charge) is ever added to them.

    Three views of the same figures: sorted horizontal bars, a cumulative operational curve over
    the horizon (flat-sloped, because v1 holds emission factors constant — the caption says so),
    and a table whose Total row closes against `total_co2_in_kg`. Emissions are never discounted,
    so unlike the money charts this one has no present-value counterpart. Renders as the empty
    string when the run has no emissions data at all.
    """
    result = next(iter(matrix.results.values()))
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
        parts.append(_text(left - 8, y + row_h / 2 + 4, subject, 11, "end"))
        parts.append(_rect(left, y + 4, value * scale, row_h - 8, color,
                           f"{subject} ({kind}): {value:,.0f} kg CO2 over the horizon", rx=3))
        parts.append(_text(left + value * scale + 6, y + row_h / 2 + 4, f"{value:,.0f} kg", 10,
                           "start", "var(--muted)"))
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
        "<section><h2>4b - Lifecycle CO2</h2>"
        '<p class="sub">Undiscounted, parallel to the money (§3.8): embodied emissions at install '
        "and each replacement (blue) and operational emissions per carrier (orange). The CO2 "
        "<i>price</i> (a cash flow) and the CO2 <i>damage cost</i> (macroeconomic) are separate "
        "and never added to these masses.</p>"
        + bars + line
        + _details("CO2 table [kg]", _table(["Subject / carrier", "Embodied", "Operational", "Total"], table_rows))
        + "</section>"
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
        f"{_esc(PresentationStyle.DISPLAY_GROUPS[index][0])}</span>"
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
    swallowing a thin one, and x-ticks are thinned to about ten labels whatever the horizon.
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
            tooltip = f"year {year} - {PresentationStyle.DISPLAY_GROUPS[index][0]}: {_fmt(value)} EUR"
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
        parts.append(_text(left - 8, y + row_h / 2 + 4, label, 11, "end"))
        parts.append(_rect(x_from, y + 4, bar_w, row_h - 8, color, f"{label}: {_fmt(value)} EUR", rx=3))
        parts.append(
            _text(x_from + bar_w + 6 if value >= 0 else x_from - 6, y + row_h / 2 + 4,
                  f"{'+' if value >= 0 else ''}{_fmt(value)}", 10,
                  "start" if value >= 0 else "end", "var(--muted)")
        )
        cursor += value
        y += row_h
    parts.append(_hline(left, width - 10, y + 2, "var(--baseline)"))
    y += 8
    parts.append(_text(left - 8, y + row_h / 2 + 4, total_label, 11, "end", "var(--ink-1)", bold=True))
    parts.append(
        _rect(left, y + 4, abs(net) * scale, row_h - 8, "var(--ink-1)", f"{total_label}: {_fmt(net)} EUR", rx=3)
    )
    parts.append(_text(left + abs(net) * scale + 6, y + row_h / 2 + 4, f"{_fmt(net)} EUR", 11, "start",
                       "var(--ink-1)", bold=True))
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
    printed next to the whisker so the chart is readable without hovering.
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
        parts.append(_text(left - 8, mid + 4, label, 11, "end"))
        parts.append(_hline(to_x(band.minimum), to_x(band.maximum), mid, "var(--g0)", 2))
        parts.append(
            f'<circle cx="{to_x(band.best_estimate):.1f}" cy="{mid:.1f}" r="5" fill="var(--g0)" '
            f'stroke="var(--surface)" stroke-width="2">'
            f"<title>{_esc(label)}: {_esc(_band_str(band, unit))}</title></circle>"
        )
        parts.append(_text(to_x(band.maximum) + 8, mid + 4, _band_str(band, unit), 10, "start", "var(--muted)"))
        y += row_h
    if min_value < 0:
        parts.append(_hline(to_x(0.0), to_x(0.0), 4, "var(--baseline)"))
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
    between runs. Credit rects are emitted inline rather than through `_rect` so they can carry
    a `class="credit"` hook for styling; the shipped stylesheet does not currently use it.
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
        parts.append(_text(left - 8, mid + 4, breakdown.subject, 11, "end"))
        x_pos = zero_x
        x_neg = zero_x
        for index in range(len(PresentationStyle.DISPLAY_GROUPS)):
            value = values.get(index, 0.0)
            if not value:
                continue
            bar_w = abs(value) * scale
            tooltip = f"{breakdown.subject} - {PresentationStyle.DISPLAY_GROUPS[index][0]}: {_fmt(value)} EUR NPV"
            if value > 0:
                parts.append(
                    _rect(x_pos, y + 5, max(bar_w - 1.5, 0.5), row_h - 10, f"var(--g{index})", tooltip, rx=2)
                )
                x_pos += bar_w
            else:
                x_neg -= bar_w
                parts.append(
                    f'<rect class="credit" x="{x_neg:.1f}" y="{y + 5:.1f}" width="{max(bar_w - 1.5, 0.5):.1f}" '
                    f'height="{row_h - 10:.1f}" fill="var(--g{index})" rx="2">'
                    f"<title>{_esc(tooltip)}</title></rect>"
                )
        total = breakdown.total_npv_in_euro
        parts.append(_hline(to_x(total.minimum), to_x(total.maximum), mid, "var(--ink-1)", 1.5))
        parts.append(
            f'<circle cx="{to_x(total.best_estimate):.1f}" cy="{mid:.1f}" r="4" fill="var(--ink-1)" '
            f'stroke="var(--surface)" stroke-width="1.5">'
            f"<title>{_esc(breakdown.subject)} net NPV: {_esc(_band_str(total))}</title></circle>"
        )
        parts.append(_text(x_pos + 8, mid + 4, _band_str(total), 10, "start", "var(--muted)"))
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


