"""Matplotlib PNG companions of the lifecycle cost report (LIFECYCLE_COST_REPORT).

Same display groups and colors as the HTML report — both take them from
`presentation_style.py`, so a group keeps its hue across every output and this module no longer
imports `reporting.py` (W4.7). Written into the result directory next to the HTML report.

Like `reporting.py`, this module never computes: the numbers plotted come from `views.py` and
`results.py`; the arithmetic here is bar geometry and axis scaling.

**Why raster images exist at all**, given that `lifecycle_report.html` already draws the same
figures as inline SVG: a PNG can be dropped into the PDF report, a slide deck or an issue
comment, which an HTML page cannot. They are companions, never the primary output — the set is
a deliberate subset (thirteen charts out of the report's two dozen sections), always for the
*first* perspective of the matrix, and it carries no tables, no plausibility panel and no audit
trail. A reviewer checking numbers should read the HTML; the PNGs are for pasting.

The subset is the five original charts plus the pasteable half of the visualization extension —
the actor Sankey (V1), the liquidity fan (V2), the comparison bridge (V4), the cost treemap
(V8), the lifecycle swimlane (V9), the sources-and-uses Sankey (V10), the fixed-interest
benchmark (V13) and the monthly burden (V14), per owner decision Q1. The year × category audit
heatmap (V6) is written by `write_audit_plots` instead, next to `cost_audit.csv`: it has the
audit's audience, and it lives here rather than in `audit.py` because the seam-4 import lint
keeps verification modules free of renderers. A chart whose driving view has nothing to draw
writes no file and **logs the skip** — silent absence of a figure is only acceptable because the
log names it.

What the module does not own: which colour a display group has and which categories fall into
it (`presentation_style.py`), any figure being plotted (`views.py`, `results.py`), and the file
naming/orchestration of the report as a whole (`bridge.py`, `__main__.py`, which call
`write_report_plots`). Unlike the HTML and markdown reports these outputs are deliberately *not*
golden-tested — matplotlib rendering is not byte-stable across versions — so the guarantee that
they agree with the report is structural, not pinned: both read the same view functions.
"""

from __future__ import annotations

import os
import textwrap
from typing import Any, Dict, List, Optional, Sequence, Tuple

import matplotlib

matplotlib.use("Agg")  # postprocessing runs headless
import matplotlib.pyplot as plt  # noqa: E402  — backend must be set before pyplot
from matplotlib.colors import SymLogNorm  # noqa: E402
from matplotlib.patches import PathPatch, Rectangle  # noqa: E402
from matplotlib.path import Path as MplPath  # noqa: E402

from hisim import log  # noqa: E402
from hisim.economics import views  # noqa: E402
from hisim.economics.presentation_style import (  # noqa: E402
    PresentationStyle,
    SankeyLayout,
    group_of,
    sankey_node_boxes,
    squarified_layout,
)
from hisim.economics.report_prose import ReportProse  # noqa: E402
from hisim.economics.results import (  # noqa: E402
    EvaluationMatrix,
    LifecycleCostResult,
    VariantComparison,
    cumulative_discounted_savings,
)
from hisim.economics.timeline import CostCategory  # noqa: E402
from hisim.economics.uncertainty import Slot  # noqa: E402


class _Palette:
    """Neutral surface/ink colours of the matplotlib PNGs.

    The chrome of a chart — background, text, gridlines — as opposed to the data colours, which
    come from `PresentationStyle.GROUP_COLORS_LIGHT` so a display group keeps its hue across
    every output. These four are duplicated here rather than shared because a PNG has no theme:
    it is baked once and may end up in a PDF or a slide, so it always uses the light values,
    while the HTML report resolves the same roles through CSS custom properties that flip in
    dark mode.
    """

    SURFACE = "#fcfcfb"
    INK = "#0b0b0b"
    MUTED = "#898781"
    GRID = "#e1e0d9"


def _style_axis(axis) -> None:
    """Applies the shared chart chrome to one matplotlib axis.

    Drops the top and right spines, mutes the remaining ones, and puts a horizontal grid behind
    the marks. Called from `_new_figure` so every chart in the set is styled identically; charts
    with a horizontal value axis override the grid direction afterwards.
    """
    axis.set_facecolor(_Palette.SURFACE)
    for spine in ("top", "right"):
        axis.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        axis.spines[spine].set_color(_Palette.GRID)
    axis.tick_params(colors=_Palette.MUTED, labelsize=8)
    axis.yaxis.grid(True, color=_Palette.GRID, linewidth=0.6)
    axis.set_axisbelow(True)


def _new_figure(width: float = 9.0, height: float = 4.2):
    """Creates a styled figure/axis pair at the report's fixed width and resolution.

    The single constructor for every chart here, so all PNGs of a run share a width, a DPI and
    the chrome from `_style_axis` and can therefore be stacked in a document without looking
    like they came from different tools. Callers pass a taller `height` for the horizontal
    charts, whose height scales with the number of rows.
    """
    figure, axis = plt.subplots(figsize=(width, height), dpi=130)
    figure.patch.set_facecolor(_Palette.SURFACE)
    _style_axis(axis)
    return figure, axis


def plot_annual_cash_flows(result: LifecycleCostResult, path: str) -> str:
    """Stacked bars per year by display group (nominal), the timeline plausibility view.

    The PNG counterpart of the report's cash-flow timeline. It answers "does the money arrive in the years it
    should": replacement spikes at the component lifetimes, a residual-value credit at the
    horizon, an energy band that grows at a believable escalation rate, and a year-0 bar that
    matches the investment. Costs stack above the zero line and credits below it — the two
    stacks have separate baselines and are never netted against each other, so a year with both
    shows both.

    Args:
        result: The evaluated perspective to draw; the title carries its id and NPV.
        path: Destination PNG path.

    Returns:
        `path`, unchanged, so callers can collect the written files.
    """
    horizon = result.parameters.observation_period_in_years
    years = list(range(horizon + 1))
    per_group: Dict[int, List[float]] = {
        index: [0.0] * (horizon + 1) for index in range(len(PresentationStyle.DISPLAY_GROUPS))
    }
    folded = views.fold_category_matrix(
        views.nominal_annual_matrix_by_category(result), PresentationStyle.CATEGORY_TO_GROUP
    )
    for year, row in enumerate(folded):
        for index, value in row.items():
            per_group[index][year] = value
    figure, axis = _new_figure()
    bottom_pos = [0.0] * (horizon + 1)
    bottom_neg = [0.0] * (horizon + 1)
    for index, (group_name, _categories) in enumerate(PresentationStyle.DISPLAY_GROUPS):
        values = per_group[index]
        if not any(values):
            continue
        positives = [max(v, 0.0) for v in values]
        negatives = [min(v, 0.0) for v in values]
        if any(positives):
            axis.bar(years, positives, bottom=bottom_pos, width=0.82, label=group_name,
                     color=PresentationStyle.GROUP_COLORS_LIGHT[index], linewidth=0.6, edgecolor=_Palette.SURFACE)
            bottom_pos = [b + v for b, v in zip(bottom_pos, positives)]
        if any(negatives):
            label = None if any(positives) else group_name
            axis.bar(years, negatives, bottom=bottom_neg, width=0.82, label=label,
                     color=PresentationStyle.GROUP_COLORS_LIGHT[index], linewidth=0.6, edgecolor=_Palette.SURFACE)
            bottom_neg = [b + v for b, v in zip(bottom_neg, negatives)]
    axis.axhline(0, color=_Palette.MUTED, linewidth=0.8)
    axis.set_xlabel("year", color=_Palette.MUTED, fontsize=9)
    axis.set_ylabel("nominal EUR per year", color=_Palette.MUTED, fontsize=9)
    axis.set_title(
        f"Cash-flow timeline — {result.perspective_id} "
        f"(NPV {result.total_npv_in_euro.average:,.0f} EUR)",
        fontsize=10, color=_Palette.INK, loc="left",
    )
    axis.legend(fontsize=7.5, frameon=False, ncol=2, labelcolor=_Palette.INK)
    figure.tight_layout()
    figure.savefig(path, facecolor=_Palette.SURFACE)
    plt.close(figure)
    return path


def plot_investment_waterfall(result: LifecycleCostResult, path: str) -> str:
    """Year-0 build-up per subject: gross bars split into a net and a subsidy-covered segment.

    One horizontal bar per component, split into what the owner pays and what the support
    covers, so "how much of this measure is funded" is answerable per component rather than only
    in total. Both figures come from `views.subsidy_share_of_gross`, including the
    `min(subsidy, gross)` clamp that keeps the funded share inside [0, 1]; the same view feeds
    the HTML report's subsidy composition bars, which is why the two cannot disagree. Subjects
    without a positive gross investment are absent, and with no subjects at all the function
    returns without writing a file — `write_report_plots` filters the missing path out.

    The split is drawn as two stacked segments distinguished by colour — net investment in the
    group-0 hue, the subsidy-covered part in the group-3 hue — with the net/gross figures printed
    at the end of each bar. There is no hatching anywhere in this figure.

    Args:
        result: The evaluated perspective whose year-0 investment is drawn.
        path: Destination PNG path.

    Returns:
        `path`, whether or not a file was written.
    """
    subjects, gross_values, net_values, subsidy_values = [], [], [], []
    for share in views.subsidy_share_of_gross(result).values():
        subjects.append(share.subject)
        gross_values.append(share.gross_in_euro)
        subsidy_values.append(share.subsidy_in_euro)
        net_values.append(share.net_in_euro)
    if not subjects:
        return path
    figure, axis = _new_figure(height=max(2.2, 0.55 * len(subjects) + 1.2))
    positions = range(len(subjects))
    axis.barh(positions, net_values, color=PresentationStyle.GROUP_COLORS_LIGHT[0], label="net investment",
              edgecolor=_Palette.SURFACE, linewidth=0.6)
    axis.barh(positions, subsidy_values, left=net_values,
              color=PresentationStyle.GROUP_COLORS_LIGHT[3],
              label="covered by subsidies", edgecolor=_Palette.SURFACE, linewidth=0.6)
    for position, (gross, net) in enumerate(zip(gross_values, net_values)):
        axis.text(gross * 1.01, position, f"{net:,.0f} net / {gross:,.0f} gross", va="center",
                  fontsize=7.5, color=_Palette.MUTED)
    axis.set_yticks(list(positions), subjects, fontsize=8, color=_Palette.INK)
    axis.invert_yaxis()
    axis.xaxis.grid(True, color=_Palette.GRID, linewidth=0.6)
    axis.yaxis.grid(False)
    axis.set_xlabel("year-0 investment [EUR]", color=_Palette.MUTED, fontsize=9)
    axis.set_title(f"Investment build-up (year 0) — {result.perspective_id}", fontsize=10,
                   color=_Palette.INK, loc="left")
    axis.legend(fontsize=7.5, frameon=False, labelcolor=_Palette.INK)
    figure.tight_layout()
    figure.savefig(path, facecolor=_Palette.SURFACE)
    plt.close(figure)
    return path


def plot_perspective_costs(matrix: EvaluationMatrix, path: str) -> str:
    """Equivalent annual cost per perspective as dot-with-whiskers (min/avg/max).

    The PNG counterpart of the report's perspectives section, and the one chart that shows the whole matrix at
    once: every perspective's headline KPI on a common axis, with the §3.9 envelope drawn as
    whiskers around the AVERAGE dot. It is what a reader uses to sanity-check the perspective
    model itself — operating-only must sit below brownfield, a gross view above its net
    counterpart, and the macroeconomic row should differ from the financial one only by
    transfers and CO2 damage. The whiskers are an envelope of coherent worlds, not a confidence
    interval, so overlap between two perspectives says nothing about significance.

    Args:
        matrix: All evaluated perspectives, drawn in insertion order (top to bottom).
        path: Destination PNG path.

    Returns:
        `path`, unchanged.
    """
    labels, averages, lows, highs = [], [], [], []
    for perspective_id, result in matrix.results.items():
        band = result.equivalent_annual_cost_in_euro
        labels.append(perspective_id)
        averages.append(band.average)
        lows.append(band.average - band.minimum)
        highs.append(band.maximum - band.average)
    figure, axis = _new_figure(height=max(2.2, 0.5 * len(labels) + 1.2))
    positions = range(len(labels))
    axis.errorbar(averages, positions, xerr=[lows, highs], fmt="o", color=PresentationStyle.GROUP_COLORS_LIGHT[0],
                  ecolor=PresentationStyle.GROUP_COLORS_LIGHT[0], elinewidth=2, capsize=3, markersize=7,
                  markeredgecolor=_Palette.SURFACE, markeredgewidth=1.5)
    for position, (label, average) in enumerate(zip(labels, averages)):
        axis.text(averages[position] + highs[position] + max(averages) * 0.02, position,
                  f"{average:,.0f} EUR/a", va="center", fontsize=7.5, color=_Palette.MUTED)
    axis.set_yticks(list(positions), labels, fontsize=8, color=_Palette.INK)
    axis.invert_yaxis()
    axis.xaxis.grid(True, color=_Palette.GRID, linewidth=0.6)
    axis.yaxis.grid(False)
    axis.set_xlabel("equivalent annual cost [EUR/a] with min/max band", color=_Palette.MUTED, fontsize=9)
    axis.set_title("Perspectives", fontsize=10, color=_Palette.INK, loc="left")
    figure.tight_layout()
    figure.savefig(path, facecolor=_Palette.SURFACE)
    plt.close(figure)
    return path


def plot_component_costs(result: LifecycleCostResult, path: str) -> str:
    """Per-subject NPV as diverging stacks (§7.4): costs right of 0, credits left, net marker.

    Credits (residual value, subsidies, feed-in, anyway credit) are never added onto the cost
    side — the black marker with whiskers is the net NPV band, `net = costs - credits`.

    Two placement rules exist because the chart draws three things per row and they collided in
    every run of the PR-9 evaluation set. The net-NPV text starts past *everything* in its row —
    the end of the cost stack and the upper whisker cap, whichever reaches further — rather than
    past the stack alone, which overprinted the marker on every row whose band exceeded the bars.
    And the legend sits below the axes instead of inside them: a diverging horizontal stack with
    one row per subject has no reliably empty corner, and in the heat-pump-only run the in-axes
    legend covered the ElectricityMeter row outright.
    """
    breakdowns = list(result.component_breakdowns.items())
    if not breakdowns:
        return path
    figure, axis = _new_figure(height=max(2.4, 0.55 * len(breakdowns) + 1.4))
    positions = range(len(breakdowns))
    lefts_pos = [0.0] * len(breakdowns)
    lefts_neg = [0.0] * len(breakdowns)
    per_subject = [
        views.fold_categories(breakdown.npv_by_category, PresentationStyle.CATEGORY_TO_GROUP)
        for _subject, breakdown in breakdowns
    ]
    for index, (group_name, _categories) in enumerate(PresentationStyle.DISPLAY_GROUPS):
        values = [
            grouped[index].average if index in grouped else 0.0 for grouped in per_subject
        ]
        if not any(values):
            continue
        positives = [max(v, 0.0) for v in values]
        negatives = [min(v, 0.0) for v in values]
        if any(positives):
            axis.barh(positions, positives, left=lefts_pos,
                      color=PresentationStyle.GROUP_COLORS_LIGHT[index], label=group_name,
                      edgecolor=_Palette.SURFACE, linewidth=0.6)
            lefts_pos = [left + value for left, value in zip(lefts_pos, positives)]
        if any(negatives):
            label = None if any(positives) else group_name
            axis.barh(positions, negatives, left=lefts_neg,
                      color=PresentationStyle.GROUP_COLORS_LIGHT[index], label=label,
                      edgecolor=_Palette.SURFACE, linewidth=0.6)
            lefts_neg = [left + value for left, value in zip(lefts_neg, negatives)]
    axis.axvline(0, color=_Palette.MUTED, linewidth=0.9)
    # Net NPV band per subject: black dot with min/max whiskers on the same signed axis.
    nets = [breakdown.total_npv_in_euro for _subject, breakdown in breakdowns]
    # The text label of a row starts past everything drawn in it — the end of the cost stack *and*
    # the upper whisker cap — instead of at the stack end alone, which printed the label on top of
    # the marker whenever the net band reached beyond the bars (every run of the PR-9 set).
    label_starts = [
        max(bar_end, band.maximum, 0.0) for bar_end, band in zip(lefts_pos, nets)
    ]
    axis.errorbar(
        [band.average for band in nets],
        list(positions),
        xerr=[
            [band.average - band.minimum for band in nets],
            [band.maximum - band.average for band in nets],
        ],
        fmt="o", color=_Palette.INK, ecolor=_Palette.INK, elinewidth=1.4, capsize=3, markersize=5,
        markeredgecolor=_Palette.SURFACE, markeredgewidth=1.2, label="net NPV (band)",
    )
    span = max(label_starts + [abs(value) for value in lefts_neg] + [1.0])
    gap = span * 0.035 + 1.0
    for position, band in enumerate(nets):
        axis.text(label_starts[position] + gap, position,
                  f"{band.average:,.0f} [{band.minimum:,.0f} | {band.maximum:,.0f}]",
                  va="center", fontsize=7, color=_Palette.MUTED)
    axis.set_yticks(list(positions), [subject for subject, _b in breakdowns], fontsize=8, color=_Palette.INK)
    axis.invert_yaxis()
    axis.xaxis.grid(True, color=_Palette.GRID, linewidth=0.6)
    axis.yaxis.grid(False)
    # The labels are drawn in data coordinates, so the axis has to make room for them or the
    # longest one is clipped at the frame; the reserve is proportional to the label width.
    axis.set_xlim(min(lefts_neg + [0.0]) - gap, max(label_starts) + gap + span * 0.30)
    axis.set_xlabel("NPV [EUR] — credits left of 0, costs right; marker = net NPV band",
                    color=_Palette.MUTED, fontsize=9)
    axis.set_title(f"Component breakdown — {result.perspective_id}", fontsize=10, color=_Palette.INK, loc="left")
    # Below the axes, never inside them: an in-axes legend has no free corner on a diverging
    # horizontal stack — with one row per subject it covered a data row (run 3's ElectricityMeter)
    # and no amount of `loc` guessing can guarantee otherwise on an arbitrary result. The offset
    # is half an inch expressed in axes fractions, so it stays half an inch whether the chart has
    # three rows or fifteen, and never lands on the x-axis label.
    axes_height_in_inches = max(figure.get_figheight() - 1.2, 1.0)
    axis.legend(fontsize=7.5, frameon=False, ncol=3, labelcolor=_Palette.INK,
                loc="upper center", bbox_to_anchor=(0.5, -0.55 / axes_height_in_inches))
    figure.tight_layout()
    figure.savefig(path, facecolor=_Palette.SURFACE, bbox_inches="tight")
    plt.close(figure)
    return path


def plot_payback_curve(
    reference: LifecycleCostResult, variant: LifecycleCostResult, path: str
) -> str:
    """Cumulative discounted savings (reference - variant) per slot; zero-crossing = payback.

    The curves come from `results.cumulative_discounted_savings` — the same function the
    printed payback year is derived from (W4.4).

    The title names the perspective the savings series is computed on, because the report shows
    payback years on more than one basis: the swimlane's payback milestone (V9) is drawn for the
    matrix's first perspective, typically the *gross* one, while this chart is drawn for the
    perspective the two compared directories share, typically `brownfield_net`. Both are correct
    and they legitimately differ — a net basis pays back earlier — so without the basis on the
    chart the two readings look like a contradiction. When the two sides of the comparison are
    not the same perspective (the reference directory lacks the variant's), both are named.
    """
    curves = cumulative_discounted_savings(reference, variant)
    years = list(range(len(curves["average"])))
    figure, axis = _new_figure(height=3.6)
    styles = {"low": (":", 1.2, "optimistic"), "average": ("-", 2.2, "expected"),
              "high": ("--", 1.2, "pessimistic")}
    for slot, (linestyle, linewidth, label) in styles.items():
        axis.plot(years, curves[slot], linestyle, linewidth=linewidth,
                  color=PresentationStyle.GROUP_COLORS_LIGHT[0], label=label)
    axis.axhline(0, color=_Palette.MUTED, linewidth=0.8)
    axis.set_xlabel("year", color=_Palette.MUTED, fontsize=9)
    basis = (
        variant.perspective_id
        if variant.perspective_id == reference.perspective_id
        else f"{variant.perspective_id} vs {reference.perspective_id}"
    )
    axis.set_ylabel(
        f"cumulative discounted savings vs the reference [EUR] — {basis} basis",
        color=_Palette.MUTED, fontsize=9,
    )
    axis.set_title(
        f"Discounted payback (zero-crossing) — {basis} basis", fontsize=10, color=_Palette.INK, loc="left"
    )
    axis.legend(fontsize=7.5, frameon=False, labelcolor=_Palette.INK)
    figure.tight_layout()
    figure.savefig(path, facecolor=_Palette.SURFACE)
    plt.close(figure)
    return path


# ---------------------------------------------------------------------------- Sankey machinery
#
# The four Sankeys of the visualization set (V1, V10, V11, V12) share one layout and one ribbon
# primitive. `matplotlib.sankey` is deliberately not used: it draws a radial diagram with fixed
# arrow stubs, which cannot express "three columns of nodes with proportional ribbons between
# them" at all. Hand-drawn cubic Bézier patches can, in about eighty lines, with no dependency.

class _SankeyStyle:
    """Rendering weights of the matplotlib Sankeys, on top of the shared geometry.

    The node/column geometry itself lives on `presentation_style.SankeyLayout`, because the
    inline-SVG report draws the same diagrams and the two must place a node identically. What is
    left here is what only a raster chart has an opinion about: ribbon transparency and the two
    font sizes.
    """

    NODE_WIDTH = SankeyLayout.NODE_WIDTH
    CURVATURE = SankeyLayout.CURVATURE
    RIBBON_ALPHA = 0.55
    LABEL_SIZE = 7.0
    VALUE_SIZE = 6.5
    #: Artist label of a net-position stub plate (Q29 R7), so a reader of the axis — the geometry
    #: tests above all — can tell a stub from a node rectangle without measuring it.
    STUB_LABEL = "net-stub"


def _draw_ribbon(
    axis, left: Tuple[float, float], right: Tuple[float, float], band_height: float,
    color: str, hatched: bool,
) -> None:
    """One Bézier ribbon between two vertical faces, as a single closed patch.

    `left` and `right` are the (x, y_bottom) attachment points and `band_height` is the ribbon's
    width — **one width for both ends**, because a ribbon is one flow and the diagram has one
    global unit scale (rule 2.7). The patch is a cubic curve along the top, a straight drop down
    the right face, the mirrored curve back along the bottom and a close — eight vertices, which
    is why this is a `PathPatch` rather than a polygon approximation. Credit ribbons are hatched
    instead of solid, so that "money coming back" is distinguishable from "money going out"
    without relying on colour (V11's rule).
    """
    x_left, y_left = left
    x_right, y_right = right
    control = (x_right - x_left) * _SankeyStyle.CURVATURE
    vertices = [
        (x_left, y_left + band_height),
        (x_left + control, y_left + band_height),
        (x_right - control, y_right + band_height),
        (x_right, y_right + band_height),
        (x_right, y_right),
        (x_right - control, y_right),
        (x_left + control, y_left),
        (x_left, y_left),
        (x_left, y_left + band_height),
    ]
    codes = [
        MplPath.MOVETO, MplPath.CURVE4, MplPath.CURVE4, MplPath.CURVE4, MplPath.LINETO,
        MplPath.CURVE4, MplPath.CURVE4, MplPath.CURVE4, MplPath.CLOSEPOLY,
    ]
    axis.add_patch(
        PathPatch(
            MplPath(vertices, codes),
            facecolor="none" if hatched else color,
            edgecolor=color,
            hatch="///" if hatched else None,
            alpha=_SankeyStyle.RIBBON_ALPHA,
            linewidth=0.6,
        )
    )


def _draw_net_stubs(axis, geometry, stub_labels: Optional[Dict[str, str]] = None) -> None:
    """The net-position stubs of a Sankey's internal nodes, PNG side (Q29 R7).

    The raster twin of the report's stubs: a short flat plate off the face the node's ribbons do
    not fill, labelled with the signed amount. It is drawn without a curve and in the muted ink so
    it reads as a closing remainder rather than as a payment to an unnamed counterparty, and it is
    what makes both faces of every node in the PNG tile at 100 % like the SVG's.
    """
    for stub in geometry.net_stubs:
        if stub.node not in geometry.boxes:
            continue
        x, y, _height = geometry.boxes[stub.node]
        band = stub.amount * geometry.unit_scale
        bottom = y + stub.anchor
        length = SankeyLayout.STUB_LENGTH * (SankeyLayout.NODE_WIDTH * 4.0)
        sign = "+" if stub.is_outgoing else "-"
        left = x + _SankeyStyle.NODE_WIDTH if stub.is_outgoing else x - length
        axis.add_patch(
            Rectangle(
                (left, bottom), length, band, facecolor=_Palette.MUTED, edgecolor=_Palette.MUTED,
                alpha=0.35, linewidth=0.6, linestyle=(0, (2, 2)), label=_SankeyStyle.STUB_LABEL,
            )
        )
        axis.text(
            left + length + 0.006 if stub.is_outgoing else left - 0.006,
            bottom + band / 2,
            (stub_labels or {}).get(stub.node, f"net {sign}{stub.amount:,.0f} EUR"),
            ha="left" if stub.is_outgoing else "right",
            va="center",
            fontsize=_SankeyStyle.VALUE_SIZE,
            color=_Palette.MUTED,
        )


def _draw_sankey(
    axis,
    columns: Sequence[Sequence[str]],
    ribbons: Sequence[Tuple[str, str, float, str, bool]],
    node_labels: Optional[Dict[str, str]] = None,
    stub_labels: Optional[Dict[str, str]] = None,
) -> None:
    """Draws a column Sankey: node rectangles plus one ribbon per flow.

    The shared renderer of the whole Sankey family. Ribbons leave a node's right face and arrive
    at the next node's left face in the order they are given, stacking on each face, so a node's
    rectangle is exactly filled by the ribbons it carries and no ribbon can overflow it. Every
    ribbon keeps one width end to end, taken from the single global unit scale
    `sankey_node_boxes` returns (rule 2.7). Labels sit outside the first and last columns and
    above the middle ones, which is what keeps a three-column diagram readable at report width.
    """
    geometry = sankey_node_boxes(
        [list(column) for column in columns],
        [(source, target, amount) for source, target, amount, _color, _credit in ribbons],
    )
    boxes = geometry.boxes
    axis.set_xlim(-0.02, 1.02)
    axis.set_ylim(-0.06, 1.06)
    axis.axis("off")
    for index, (source, target, amount, color, credit) in enumerate(ribbons):
        if source not in boxes or target not in boxes:
            continue
        band_height = amount * geometry.unit_scale
        # Q29 R7: a ribbon spanning more than one column gap is drawn as a chain of legs through
        # the corridors the layout reserved, so it cannot cross an intervening node's rectangle.
        for leg in geometry.ribbon_segments[index]:
            if leg.source not in boxes or leg.target not in boxes:
                continue
            source_x, source_y, _source_h = boxes[leg.source]
            target_x, target_y, _target_h = boxes[leg.target]
            left = (source_x + _SankeyStyle.NODE_WIDTH, source_y + leg.out_anchor)
            right = (target_x, target_y + leg.in_anchor)
            _draw_ribbon(axis, left, right, band_height, color, credit)
    _draw_net_stubs(axis, geometry, stub_labels)
    last_column = len(columns) - 1
    for index, nodes in enumerate(columns):
        for node in nodes:
            if node not in boxes:
                continue
            x, y, height = boxes[node]
            axis.add_patch(
                Rectangle((x, y), _SankeyStyle.NODE_WIDTH, height, facecolor=_Palette.INK, linewidth=0)
            )
            label = (node_labels or {}).get(node, node)
            if index == 0:
                axis.text(x - 0.008, y + height / 2, label, ha="right", va="center",
                          fontsize=_SankeyStyle.LABEL_SIZE, color=_Palette.INK)
            elif index == last_column:
                axis.text(x + _SankeyStyle.NODE_WIDTH + 0.008, y + height / 2, label, ha="left",
                          va="center", fontsize=_SankeyStyle.LABEL_SIZE, color=_Palette.INK)
            else:
                # A middle-column node is too narrow to hold a label, and the space around it is
                # full of ribbons, so the label sits on the node with an opaque plate behind it.
                axis.text(
                    x + _SankeyStyle.NODE_WIDTH / 2, y + height / 2, label, ha="center",
                    va="center", fontsize=_SankeyStyle.LABEL_SIZE, color=_Palette.INK,
                    bbox={"facecolor": _Palette.SURFACE, "edgecolor": "none", "alpha": 0.85, "pad": 1.5},
                )


def _category_color(category: Optional[CostCategory]) -> str:
    """Display-group hue of a category, muted grey for the folded "other" ribbon.

    Colour is never chosen here — it is looked up through `presentation_style.group_of`, so a
    ribbon, a bar and an HTML rect showing the same money carry the same hue. A folded ribbon has
    no single category left and therefore gets the chrome's muted tone rather than an arbitrary
    hue that would suggest it belonged to one group.
    """
    if category is None:
        return _Palette.MUTED
    return PresentationStyle.GROUP_COLORS_LIGHT[group_of(category)]


# ---------------------------------------------------------------------------- V1 actor flows

def plot_actor_flows(result: LifecycleCostResult, path: str) -> str:
    """V1: who pays whom over the horizon, as a three-column Sankey (nominal, average slot).

    Sources on the left, actors in the middle, sinks on the right, with the inter-actor transfer
    (the §559e levy today) drawn as a payer-to-payer ribbon rather than as two external stubs —
    which is the whole reason the chart exists for a landlord/tenant case. Ribbon widths are
    lifetime nominal euros of the AVERAGE slot; the band of the grand total is stated in the
    title, because a banded Sankey is unreadable (Q2).

    Skipped, with a log line, for a perspective with fewer than two actors: a single-payer
    Sankey adds nothing the waterfall does not already show.

    Args:
        result: The evaluated perspective; its FULL timeline is read, so every payer appears.
        path: Destination PNG path.

    Returns:
        `path`, whether or not a file was written (the caller filters missing files out).
    """
    matrix = views.actor_flow_matrix(result)
    if len(matrix.actors) < 2:
        log.information(
            f"Actor-flow Sankey skipped for perspective {result.perspective_id!r}: it has "
            f"{len(matrix.actors)} actor node(s), so there is no who-pays-whom story to draw."
        )
        return path
    figure, axis = _new_figure(height=max(3.6, 0.5 * (len(matrix.actors) + len(matrix.sinks)) + 2.4))

    def node_key(node: str, is_target: bool) -> str:
        """Column-qualified node id: a counterparty can be both a source and a sink."""
        if node in matrix.actors:
            return f"actor:{node}"
        return f"snk:{node}" if is_target else f"src:{node}"

    ribbons = [
        (
            node_key(flow.source, False),
            node_key(flow.target, True),
            flow.amount_in_euro,
            _category_color(flow.category),
            False,
        )
        for flow in sorted(matrix.flows, key=lambda item: -item.amount_in_euro)
    ]
    nets = matrix.net_by_actor()
    labels = {f"actor:{actor}": f"{actor}\nnet {nets[actor]:,.0f} EUR" for actor in matrix.actors}
    labels.update({f"src:{node}": node for node in matrix.sources})
    labels.update({f"snk:{node}": node for node in matrix.sinks})
    _draw_sankey(
        axis,
        # Q23: one column per party, in the order the view's topological sort puts them, so a
        # transfer between two parties is an ordinary left-to-right ribbon here too.
        [[f"src:{node}" for node in matrix.sources]]
        + [[f"actor:{actor}" for actor in column] for column in matrix.actor_columns()]
        + [[f"snk:{node}" for node in matrix.sinks]],
        ribbons,
        labels,
        # Q29 R7: the face-closing stub carries the view's own net, in the view's sign convention.
        stub_labels={f"actor:{actor}": f"net {net:,.0f} EUR" for actor, net in nets.items()},
    )
    band = matrix.total_band
    axis.set_title(
        f"Who pays whom over {result.parameters.observation_period_in_years} years — "
        f"{result.perspective_id} (average scenario; lifetime total "
        f"{band.average:,.0f} [{band.minimum:,.0f} | {band.maximum:,.0f}] EUR nominal)",
        fontsize=10, color=_Palette.INK, loc="left",
    )
    caption = (
        f"Ribbons are nominal lifetime euros. {matrix.folded_ribbon_count} small ribbon(s) "
        f"carrying {matrix.folded_amount_in_euro:,.0f} EUR folded into 'other' per node pair."
    )
    figure.text(0.01, 0.015, caption, fontsize=7, color=_Palette.MUTED)
    figure.tight_layout(rect=(0, 0.04, 1, 1))
    figure.savefig(path, facecolor=_Palette.SURFACE)
    plt.close(figure)
    return path


# ---------------------------------------------------------------------------- V2 liquidity fan

def plot_liquidity_fan(
    result: LifecycleCostResult, path: str, comparison: Optional[VariantComparison] = None
) -> str:
    """V2: cumulative cash position over time, nominal and discounted, with the band as a fan.

    Two panels on one year axis. The upper one is the cumulative *nominal* cost, cost-positive-up
    (owner decision Q4), annotated with the deepest out-of-pocket position — the liquidity
    reading is carried by that annotation rather than by flipping the axis. The lower one is the
    discounted picture: for a single evaluation the cumulative discounted cost ending at the NPV,
    and for a comparison the cumulative discounted savings whose zero crossings give the payback
    *interval* rather than a single false-precision year.

    Both panels draw the AVERAGE slot as a line and the LOW/HIGH envelope as a fill; the fill is
    an envelope of two coherent worlds, not an error bar.

    Args:
        result: The perspective whose position is drawn.
        path: Destination PNG path.
        comparison: Optional comparison; turns the lower panel into the payback fan.

    Returns:
        `path`, unchanged.
    """
    nominal = views.cumulative_nominal_cost_series(result)
    years = list(range(len(nominal[Slot.AVERAGE])))
    figure, axes = plt.subplots(2, 1, figsize=(9.0, 6.0), dpi=130, sharex=True)
    figure.patch.set_facecolor(_Palette.SURFACE)
    for axis in axes:
        _style_axis(axis)
    hue = PresentationStyle.GROUP_COLORS_LIGHT[0]
    axes[0].fill_between(years, nominal[Slot.LOW], nominal[Slot.HIGH], color=hue, alpha=0.18,
                         linewidth=0, label="min/max envelope")
    axes[0].plot(years, nominal[Slot.AVERAGE], color=hue, linewidth=2.2, label="average scenario")
    axes[0].axhline(0, color=_Palette.MUTED, linewidth=0.8)
    worst_year, worst_amount = views.worst_liquidity_position(result)
    late = worst_year > len(years) / 2
    axes[0].annotate(
        f"deepest out-of-pocket: {worst_amount:,.0f} EUR in year {worst_year}",
        xy=(worst_year, worst_amount), xytext=(-6 if late else 6, -14), textcoords="offset points",
        fontsize=7.5, color=_Palette.INK, ha="right" if late else "left",
    )
    axes[0].set_ylabel("cumulative nominal cost [EUR]", color=_Palette.MUTED, fontsize=9)
    axes[0].legend(fontsize=7.5, frameon=False, labelcolor=_Palette.INK)
    axes[0].set_title(
        f"Cash curve — {result.perspective_id} (cumulative position, costs plotted upward)",
        fontsize=10, color=_Palette.INK, loc="left",
    )
    if comparison is not None:
        curves = comparison.cumulative_discounted_savings_in_euro
        low, average, high = curves["low"], curves["average"], curves["high"]
        crossings = views.band_zero_crossings(curves)
        lower_label = "cumulative discounted savings [EUR]"
        note = _payback_interval_note(crossings)
    else:
        discounted = views.cumulative_discounted_cost_series(result)
        low, average, high = discounted[Slot.LOW], discounted[Slot.AVERAGE], discounted[Slot.HIGH]
        lower_label = "cumulative discounted cost [EUR]"
        note = f"end point = NPV {result.total_npv_in_euro.average:,.0f} EUR"
    axes[1].fill_between(range(len(average)), low, high, color=hue, alpha=0.18, linewidth=0)
    axes[1].plot(range(len(average)), average, color=hue, linewidth=2.2)
    axes[1].axhline(0, color=_Palette.MUTED, linewidth=0.8)
    axes[1].set_ylabel(lower_label, color=_Palette.MUTED, fontsize=9)
    axes[1].set_xlabel("year", color=_Palette.MUTED, fontsize=9)
    axes[1].set_xticks(list(range(0, len(years), max(1, len(years) // 10))))
    axes[1].set_title(note, fontsize=9, color=_Palette.INK, loc="left")
    figure.tight_layout()
    figure.savefig(path, facecolor=_Palette.SURFACE)
    plt.close(figure)
    return path


def _payback_interval_note(crossings: Dict[Any, Optional[int]]) -> str:
    """The payback annotation of V2's discounted panel, open end included.

    Formats the band's zero crossings as an interval, and — this is the point — says so in words
    when the HIGH world never crosses inside the horizon instead of dropping the statement. An
    omitted "no payback" is the failure mode this wording exists to prevent.
    """
    low, high = crossings.get("low"), crossings.get("high")
    if low is None:
        return "no payback within the horizon in any scenario"
    if high is None:
        return f"payback from year {low} (no payback in the HIGH world within the horizon)"
    return f"payback between year {low} and year {high}"


# ---------------------------------------------------------------------------- V4 bridge

def plot_comparison_bridge(
    reference: LifecycleCostResult, variant: LifecycleCostResult, path: str
) -> str:
    """V4: why the variant's NPV differs from the reference's, as a bridge waterfall.

    Anchor bar for the reference, one floating bar per display group, anchor bar for the variant.
    The anchors carry a thin min/max whisker; the deltas deliberately do not, because
    `high(variant − reference)` is not `high(variant) − high(reference)` and a whisker there
    would be arithmetic that means nothing. Delta bars are coloured by display group rather than
    red/green (owner decision Q6): whether a cost increase is bad depends on the payer.

    Args:
        reference: The base result.
        variant: The variant result, same perspective.
        path: Destination PNG path.

    Returns:
        `path`, unchanged.
    """
    steps = views.comparison_bridge(reference, variant, PresentationStyle.CATEGORY_TO_GROUP)
    labels = ["reference"] + [PresentationStyle.DISPLAY_GROUPS[step.group][0] for step in steps] + ["variant"]
    figure, axis = _new_figure(height=max(3.2, 0.45 * len(labels) + 2.0))
    positions = list(range(len(labels)))
    base = reference.total_npv_in_euro.average
    axis.bar([0], [base], color=_Palette.INK, width=0.7)
    cursor = base
    for index, step in enumerate(steps, start=1):
        bottom = min(cursor, cursor + step.delta_in_euro)
        axis.bar([index], [abs(step.delta_in_euro)], bottom=bottom, width=0.7,
                 color=PresentationStyle.GROUP_COLORS_LIGHT[step.group])
        axis.plot([index - 0.35, index - 0.35], [cursor, cursor], color=_Palette.GRID, linewidth=0.8)
        axis.text(index, bottom + abs(step.delta_in_euro) + abs(base) * 0.01,
                  f"{step.delta_in_euro:+,.0f}", ha="center", fontsize=7, color=_Palette.MUTED)
        cursor += step.delta_in_euro
    axis.bar([len(labels) - 1], [cursor], color=_Palette.INK, width=0.7)
    for position, band in ((0, reference.total_npv_in_euro), (len(labels) - 1, variant.total_npv_in_euro)):
        axis.errorbar([position], [band.average], yerr=[[band.average - band.minimum],
                      [band.maximum - band.average]], fmt="none", ecolor=_Palette.MUTED,
                      elinewidth=1.2, capsize=3)
    axis.axhline(0, color=_Palette.MUTED, linewidth=0.8)
    axis.set_xticks(positions, labels, fontsize=7.5, rotation=35, ha="right", color=_Palette.INK)
    axis.set_ylabel("NPV [EUR], discounted", color=_Palette.MUTED, fontsize=9)
    axis.set_title(
        f"NPV bridge — {variant.perspective_id} (average scenario; delta "
        f"{variant.total_npv_in_euro.average - base:+,.0f} EUR)",
        fontsize=10, color=_Palette.INK, loc="left",
    )
    figure.tight_layout()
    figure.savefig(path, facecolor=_Palette.SURFACE)
    plt.close(figure)
    return path


# ---------------------------------------------------------------------------- V6 audit heatmap

class _HeatmapStyle:
    """Colour scale, annotation limits and caption layout of the year × category heatmap (V6).

    `LINEAR_THRESHOLD` is the euro amount below which the symlog colour scale behaves linearly
    (owner decision Q10): a linear scale would let the single year-0 investment cell flatten
    every operational-year cell into the same tone, and seeing that texture *is* the audit
    purpose. `MAX_ANNOTATED_CELLS` decides when the rounded per-cell numbers still fit; above it
    the colorbar has to carry the reading alone.

    The caption constants exist because this chart carries the authored explanation the HTML
    sections carry, and a PNG has no collapsible disclosure to put it in: `CAPTION_WRAP_WIDTH` is
    the character count that fills the 9-inch figure at `CAPTION_FONT_SIZE` without reaching the
    right margin, and `CAPTION_LINE_HEIGHT_IN_INCHES` is what the figure grows by per wrapped
    line so the text never lands on top of the matrix.
    """

    LINEAR_THRESHOLD = 100.0
    MAX_ANNOTATED_CELLS = 240
    COLORMAP = "RdBu_r"
    CAPTION_FONT_SIZE = 7
    CAPTION_WRAP_WIDTH = 145
    CAPTION_LINE_HEIGHT_IN_INCHES = 0.135
    CAPTION_PADDING_IN_INCHES = 0.14


def _heatmap_caption_lines(dropped: int) -> List[str]:
    """The heatmap's caption, wrapped to the figure width: authored prose, then this run's facts.

    The authored *shows* paragraph of the ledger heatmap comes first and unchanged — it is the
    same text a reader of the HTML report meets at the top of every section, and it is what makes
    this PNG readable on its own when it is mailed around without the report. The two sentences
    after it are run-specific and stay run-specific: which reconciliations hold and how many cost
    categories carried no flow at all in this evaluation.

    Args:
        dropped: How many `CostCategory` members are absent from the matrix.

    Returns:
        One string per rendered line, in order; the caller sizes the figure from their count.
    """
    prose = ReportProse.for_section(ReportProse.LEDGER_HEATMAP_SECTION_NAME)
    paragraphs = [
        ReportProse.to_plain_text(prose.shows),
        "Column sums equal the nominal annual series, row sums the per-category totals of "
        f"cost_audit.csv. {dropped} categor(ies) carried no flows and are not shown.",
    ]
    return [
        line
        for paragraph in paragraphs
        for line in textwrap.wrap(paragraph, width=_HeatmapStyle.CAPTION_WRAP_WIDTH)
    ]


def plot_timeline_heatmap(result: LifecycleCostResult, path: str) -> str:
    """V6: the whole ledger as a year × category matrix — the audit trail's visual twin.

    Every cost category that carries a flow, unfolded (not grouped), against every year, in
    nominal euros of the AVERAGE slot. The colour scale is **diverging** and centred on zero, so
    a credit and a cost can never look alike, and **symlog**, so the year-0 investment does not
    flatten the operating years. Rows are ordered by display group, then by category order within
    the group, which makes the row blocks match the stacked-bar legend.

    This is the one chart of the set that lives with the audit outputs rather than in the report
    (owner decision Q9): same audience, same question — "does every category put money in the
    years it should". It is written by `write_audit_plots` from the audit's own call sites,
    because the seam-4 import lint forbids `audit.py` from importing a renderer.

    Reconciliation: column sums equal `annual_cost_series_nominal_in_euro` (average slot); row
    sums equal the per-category nominal totals of the audit table. Both are stated in the caption.

    **The caption.** Living outside the report costs this chart the four-part explanation every
    HTML section opens with: a PNG has no `<details>` to hold a definition list. It therefore
    carries the authored *shows* paragraph verbatim (with the emphasis markers stripped, the only
    change a matplotlib caption allows) above the two run-specific reconciliation sentences, and
    the figure grows by exactly the height the wrapped text needs so nothing is clipped. The
    terms and the calculation of the ledger heatmap are not renderable here; they stay in
    `ReportProse` and are noted as unreachable in the PR description rather than dropped.

    Args:
        result: The perspective to audit.
        path: Destination PNG path.

    Returns:
        `path`, unchanged.
    """
    matrix = views.nominal_annual_matrix_by_category(result)
    ordered = [
        category
        for _index, (_name, categories) in enumerate(PresentationStyle.DISPLAY_GROUPS)
        for category in categories
    ]
    ordered += [category for category in CostCategory if category not in ordered]
    present = [
        category for category in ordered if any(row.get(category) for row in matrix)
    ]
    dropped = sum(1 for category in CostCategory if category not in present)
    if not present:
        log.information(
            f"Timeline heatmap skipped for perspective {result.perspective_id!r}: the scoped "
            "timeline carries no flows at all."
        )
        return path
    values = [[row.get(category, 0.0) for row in matrix] for category in present]
    extent = max(abs(value) for row in values for value in row) or 1.0
    caption_lines = _heatmap_caption_lines(dropped)
    caption_height = (
        len(caption_lines) * _HeatmapStyle.CAPTION_LINE_HEIGHT_IN_INCHES
        + _HeatmapStyle.CAPTION_PADDING_IN_INCHES
    )
    chart_height = max(2.6, 0.28 * len(present) + 2.0)
    figure, axis = _new_figure(height=chart_height + caption_height)
    axis.yaxis.grid(False)
    image = axis.imshow(
        values,
        aspect="auto",
        cmap=_HeatmapStyle.COLORMAP,
        norm=SymLogNorm(linthresh=_HeatmapStyle.LINEAR_THRESHOLD, vmin=-extent, vmax=extent, base=10),
    )
    axis.set_xticks(range(0, len(matrix), max(1, len(matrix) // 12)),
                    [str(year) for year in range(0, len(matrix), max(1, len(matrix) // 12))],
                    fontsize=7, color=_Palette.INK)
    axis.set_yticks(range(len(present)), [category.value for category in present], fontsize=7,
                    color=_Palette.INK)
    if len(present) * len(matrix) <= _HeatmapStyle.MAX_ANNOTATED_CELLS:
        for row_index, row in enumerate(values):
            for column_index, value in enumerate(row):
                if value:
                    axis.text(column_index, row_index, f"{value:,.0f}", ha="center", va="center",
                              fontsize=5.5, color=_Palette.INK)
    figure.colorbar(image, ax=axis, shrink=0.85, label="nominal EUR (symlog)")
    axis.set_xlabel("year", color=_Palette.MUTED, fontsize=9)
    axis.set_title(
        f"Ledger heatmap — {result.perspective_id} (nominal, average scenario)",
        fontsize=10, color=_Palette.INK, loc="left",
    )
    figure.text(
        0.01, _HeatmapStyle.CAPTION_PADDING_IN_INCHES / 2 / (chart_height + caption_height),
        "\n".join(caption_lines),
        fontsize=_HeatmapStyle.CAPTION_FONT_SIZE, color=_Palette.MUTED, va="bottom",
    )
    figure.tight_layout(rect=(0, caption_height / (chart_height + caption_height), 1, 1))
    figure.savefig(path, facecolor=_Palette.SURFACE)
    plt.close(figure)
    return path


def write_audit_plots(result: LifecycleCostResult, result_directory: str) -> List[str]:
    """Writes the audit-side figures next to `cost_audit.csv` (V6, owner decision Q9).

    The audit's charts have the audit's audience, so they are written from the audit's own call
    sites (`bridge.py` and the `audit` CLI command) rather than from `write_report_plots`. It
    lives here and not in `audit.py` because the seam-4 import lint keeps the verification module
    free of renderers — the dependency runs from presentation to the engine's outputs, never back.

    Args:
        result: The perspective the audit was built for (the first one of the run).
        result_directory: Directory holding the audit CSVs; the PNG lands beside them.

    Returns:
        The paths that exist on disk afterwards, so the caller can log exactly what was written.
    """
    written = [
        plot_timeline_heatmap(result, os.path.join(result_directory, "cost_audit_timeline_heatmap.png"))
    ]
    return [path for path in written if os.path.isfile(path)]


# ---------------------------------------------------------------------------- V8 treemap

class _TreemapLabels:
    """How a treemap decides whether a tile can carry its label (V8).

    Matplotlib will happily draw a two-line label centred on a rectangle three pixels wide, and
    the result is two subjects writing across each other — which is what these numbers exist to
    prevent. The label is measured against the tile in axes fractions, the font shrinks until it
    fits, and below `MIN_FONT_SIZE` the tile simply goes unlabelled (the HTML variant keeps it
    readable via hover, as the spec allows).
    """

    #: Starting font size, in points; the same size the other charts' in-plot labels use.
    FONT_SIZE = 6.5
    #: Below this the text is unreadable anyway, so the tile is left blank instead.
    MIN_FONT_SIZE = 4.5
    #: Width of an average character as a fraction of the font size — a standard DejaVu Sans
    #: approximation, deliberately generous so the estimate errs towards suppressing a label.
    CHAR_WIDTH_RATIO = 0.62
    #: Height of one text line as a multiple of the font size, leading included.
    LINE_HEIGHT_RATIO = 1.35
    #: Fraction of the tile the text may occupy before it is considered not to fit.
    FILL_LIMIT = 0.92
    #: Share of its half of the figure a panel's axes occupy once the margins are taken; used to
    #: turn the figure size into the axes extent without forcing an early draw.
    PANEL_WIDTH_SHARE = 0.92
    #: Share of the figure height left for the axes after the suptitle and the caption band.
    PANEL_HEIGHT_SHARE = 0.72
    #: Points per inch, so the tile geometry and the font size are compared in the same unit.
    POINTS_PER_INCH = 72.0


def _fitting_font_size(
    lines: List[str], width: float, height: float, axis_width_in_points: float, axis_height_in_points: float
) -> Optional[float]:
    """The largest font size at which a label fits inside its tile, or None if none does.

    Works in points on both axes — the tile's size in axes fractions is converted with the axes'
    own extent — because a font size is a point measurement and comparing it against a fraction
    is how labels end up overflowing. Returns None when even `_TreemapLabels.MIN_FONT_SIZE` would
    overflow, which is the caller's signal to leave the tile unlabelled.
    """
    available_width = width * axis_width_in_points * _TreemapLabels.FILL_LIMIT
    available_height = height * axis_height_in_points * _TreemapLabels.FILL_LIMIT
    longest = max(len(line) for line in lines) if lines else 0
    if not longest:
        return None
    by_width = available_width / (longest * _TreemapLabels.CHAR_WIDTH_RATIO)
    by_height = available_height / (len(lines) * _TreemapLabels.LINE_HEIGHT_RATIO)
    size = min(_TreemapLabels.FONT_SIZE, by_width, by_height)
    return size if size >= _TreemapLabels.MIN_FONT_SIZE else None


def plot_cost_treemap(result: LifecycleCostResult, path: str) -> str:
    """V8: lifetime cost composition as a treemap, gross and net-of-credits side by side.

    Both variants are rendered because a treemap cannot show credits and neither answer is the
    whole truth (owner decision Q11). The left panel is **gross**: tile area is the positive NPV
    per (display group, subject) cell, and the caption restates the excluded credit total, so the
    picture can never be mistaken for the net answer. The right panel is **net of credits**: each
    subject's credits are applied to that subject's own cost tiles across all groups, and the
    subjects whose credits exceed their costs are clamped at zero and *named* in the caption
    together with the euros the clamping erased — those are exactly the entries a reviewer should
    ask about.

    Labels are drawn only where they fit their own rectangle, shrinking down to
    `_TreemapLabels.MIN_FONT_SIZE` and disappearing below it, so a thin tile never writes across
    its neighbour.

    Args:
        result: The perspective whose cost structure is drawn.
        path: Destination PNG path.

    Returns:
        `path`, unchanged.
    """
    figure_width_in_inches, figure_height_in_inches = 10.0, 4.6
    figure, axes = plt.subplots(1, 2, figsize=(figure_width_in_inches, figure_height_in_inches), dpi=130)
    figure.patch.set_facecolor(_Palette.SURFACE)
    panel_width_in_points = (
        figure_width_in_inches / 2 * _TreemapLabels.PANEL_WIDTH_SHARE * _TreemapLabels.POINTS_PER_INCH
    )
    panel_height_in_points = (
        figure_height_in_inches * _TreemapLabels.PANEL_HEIGHT_SHARE * _TreemapLabels.POINTS_PER_INCH
    )
    captions: List[str] = []
    for axis, basis, headline in (
        (axes[0], views.TileBasis.GROSS, "gross cost"),
        (axes[1], views.TileBasis.NET_OF_CREDITS, "net of credits"),
    ):
        tiles = views.cost_structure_tiles(result, PresentationStyle.CATEGORY_TO_GROUP, basis)
        drawable = [tile for tile in tiles.tiles if tile.area_in_euro > 0]
        drawable.sort(key=lambda tile: (tile.group, -tile.area_in_euro))
        axis.set_xlim(0, 1)
        axis.set_ylim(0, 1)
        axis.axis("off")
        axis.set_facecolor(_Palette.SURFACE)
        for tile, (x, y, width, height) in zip(
            drawable, squarified_layout([tile.area_in_euro for tile in drawable], 0.0, 0.0, 1.0, 1.0)
        ):
            axis.add_patch(
                Rectangle((x, y), width, height,
                          facecolor=PresentationStyle.GROUP_COLORS_LIGHT[tile.group],
                          edgecolor=_Palette.SURFACE, linewidth=1.2)
            )
            lines = [str(tile.subject), f"{tile.area_in_euro:,.0f}"]
            font_size = _fitting_font_size(lines, width, height, panel_width_in_points, panel_height_in_points)
            if font_size is None:
                font_size = _fitting_font_size(
                    lines[1:], width, height, panel_width_in_points, panel_height_in_points
                )
                lines = lines[1:]
            if font_size is not None:
                axis.text(x + width / 2, y + height / 2, "\n".join(lines), ha="center", va="center",
                          fontsize=font_size, color=_Palette.SURFACE)
        total = sum(tile.area_in_euro for tile in drawable)
        axis.set_title(
            f"{headline}: {total:,.0f} EUR (net NPV {tiles.net_npv_in_euro:,.0f} EUR)",
            fontsize=9, color=_Palette.INK, loc="left",
        )
        if basis == views.TileBasis.GROSS:
            captions.append(
                f"Gross panel excludes {tiles.credit_total_in_euro:,.0f} EUR of credits "
                f"(subsidies, feed-in, residual value); {tiles.folded_tile_count} small tile(s) "
                f"folded into 'other'."
            )
        else:
            clamped = tiles.clamped_tiles()
            names = ", ".join(f"{tile.subject} ({tile.clamped_from_in_euro:,.0f} EUR)" for tile in clamped)
            captions.append(
                f"Net panel applies each subject's credits to that subject's cost tiles and clamped "
                f"{len(clamped)} subject(s) at zero, erasing {tiles.clamped_total_in_euro:,.0f} EUR: "
                f"{names or 'none'}."
            )
    figure.suptitle(
        f"Cost structure — {result.perspective_id} (NPV, average scenario)",
        fontsize=10, color=_Palette.INK, x=0.01, ha="left",
    )
    figure.text(0.01, 0.015, "  ".join(captions), fontsize=6.5, color=_Palette.MUTED, wrap=True)
    figure.tight_layout(rect=(0, 0.07, 1, 0.95))
    figure.savefig(path, facecolor=_Palette.SURFACE)
    plt.close(figure)
    return path


# ---------------------------------------------------------------------------- V9 swimlane

class _SwimlaneStyle:
    """Row geometry of the lifecycle swimlane (V9).

    A swimlane is readable exactly as long as its rows are tall enough to carry a label, so the
    figure height scales with the lane count instead of the rows shrinking. `SPAN_HEIGHT` is the
    fraction of a row a span bar fills; the rest is the gap that keeps two neighbouring lanes
    apart.
    """

    ROW_HEIGHT_IN_INCHES = 0.62
    #: Vertical distance between two lanes, in data units; a lane is one unit tall, and the rest
    #: is the room the stacked event labels below a lane need.
    ROW_PITCH = 1.6
    SPAN_HEIGHT = 0.5
    LABEL_SIZE = 7.0
    #: How many label rows a lane prints before the rest are summarized as "+N more".
    MAX_LABELS_PER_CLUSTER = 3
    #: Gap between a marker and its label, in year units.
    LABEL_GAP_IN_YEARS = 0.15
    #: How far past the horizon the axes extend; a label may not cross this.
    RIGHT_MARGIN_IN_YEARS = 0.6
    #: Font size of an event label, in points.
    EVENT_LABEL_SIZE = 6.0
    #: Width of an average character as a fraction of the font size (DejaVu Sans, generous), used
    #: to claim horizontal space for a label so a wide one pushes the next label down a row.
    CHAR_WIDTH_RATIO = 0.62
    #: Share of the figure width the axes occupy once the y tick labels and margins are taken.
    AXES_WIDTH_SHARE = 0.78
    #: Points per inch, so a label width in points converts into year units.
    POINTS_PER_INCH = 72.0


def _draw_lane_events(
    axis,
    position: float,
    events: Sequence[Tuple[int, str, Optional[float]]],
    color: str,
    horizon: int,
    years_per_character: float,
) -> None:
    """Draws one lane's markers and stacks their labels so none is overprinted.

    Markers are cheap; labels are not. Each label claims a horizontal extent — its estimated text
    width, not just its year — and is pushed down one row at a time until it finds a level whose
    claimed extent it does not run into. That is what separates a replacement label from the
    residual label at the horizon, which sit years apart and still collide because the first one
    is wide. A lane prints at most `_SwimlaneStyle.MAX_LABELS_PER_CLUSTER` levels and summarizes
    the rest as "+N more", so the chart degrades by naming what it dropped rather than by becoming
    unreadable.

    Labels that would run off the right edge are flipped to the left of their marker, since the
    horizon is where the residual credit lives and cutting that label off is not an option.

    Args:
        axis: The swimlane axes.
        position: The lane's y coordinate.
        events: (year, label, amount) triples, in year order.
        color: The lane's marker colour.
        horizon: The observation horizon, i.e. where the drawable area ends.
        years_per_character: Width of one label character in year units, for the extent estimate.
    """
    occupied: Dict[int, float] = {}
    hidden = 0
    last_hidden_year: Optional[int] = None
    for year, label, amount in events:
        axis.plot([year], [position], marker="|", markersize=11, color=color, markeredgewidth=2)
        text = label if amount is None else f"{label} {amount:,.0f}"
        width = len(text) * years_per_character
        start = year + _SwimlaneStyle.LABEL_GAP_IN_YEARS
        alignment = "left"
        if start + width > horizon + _SwimlaneStyle.RIGHT_MARGIN_IN_YEARS:
            alignment = "right"
            start = year - _SwimlaneStyle.LABEL_GAP_IN_YEARS - width
        level = 0
        while level < _SwimlaneStyle.MAX_LABELS_PER_CLUSTER and occupied.get(level, start) > start:
            level += 1
        if level >= _SwimlaneStyle.MAX_LABELS_PER_CLUSTER:
            hidden += 1
            last_hidden_year = year
            continue
        occupied[level] = start + width
        axis.text(year + (1 if alignment == "left" else -1) * _SwimlaneStyle.LABEL_GAP_IN_YEARS,
                  position - 0.28 - 0.19 * level, text, fontsize=6, color=_Palette.INK,
                  ha=alignment)
    if hidden and last_hidden_year is not None:
        axis.text(last_hidden_year, position - 0.28 - 0.19 * _SwimlaneStyle.MAX_LABELS_PER_CLUSTER,
                  f"+{hidden} more", fontsize=6, color=_Palette.MUTED, ha="right")


def plot_lifecycle_swimlane(
    result: LifecycleCostResult, path: str, comparison: Optional[VariantComparison] = None
) -> str:
    """V9: the life of the renovation on one page — assets, financing, support, milestones.

    The report's opening figure, and deliberately a *composition*: every lane restates a figure
    that exists in full elsewhere (V7's asset events, V5's amortization, V2's crossings), so the
    overview cannot disagree with the detail charts. The payback milestone is drawn as a **range
    bar** spanning the band's zero crossings rather than as a single year — a one-year payback
    label would be exactly the false precision the whole set avoids — and appears only when a
    comparison exists to define it.

    Args:
        result: The perspective to summarize.
        path: Destination PNG path.
        comparison: Optional comparison, which is what gives the payback range a meaning.

    Returns:
        `path`, unchanged.
    """
    lanes = views.lifecycle_lanes(result, comparison)
    rows: List[Tuple[str, List[Tuple[int, Optional[int], str]], List[Tuple[int, str, Optional[float]]], str]] = []
    rows.append((
        lanes.milestones.name,
        [(span.start_year, span.end_year, span.label) for span in lanes.milestones.spans],
        [(event.year, event.label, event.amount_in_euro) for event in lanes.milestones.events],
        _Palette.INK,
    ))
    for lane, color in (
        (lanes.financing, PresentationStyle.GROUP_COLORS_LIGHT[0]),
        (lanes.support, PresentationStyle.GROUP_COLORS_LIGHT[3]),
    ):
        if lane.is_empty():
            log.information(
                f"Lifecycle swimlane: lane {lane.name!r} is empty for perspective "
                f"{result.perspective_id!r} and is not drawn."
            )
            continue
        rows.append((
            lane.name,
            [(span.start_year, span.end_year, span.label) for span in lane.spans],
            [(event.year, event.label, event.amount_in_euro) for event in lane.events],
            color,
        ))
    for asset in lanes.assets:
        asset_events: List[Tuple[int, str, Optional[float]]] = [
            (event.year, event.kind, event.amount_in_euro) for event in asset.events
        ]
        if asset.residual is not None:
            asset_events.append((asset.residual.year, "residual", asset.residual.amount_in_euro))
        rows.append((
            asset.subject,
            [(span.start_year, span.end_year, "in service") for span in asset.spans],
            asset_events,
            PresentationStyle.GROUP_COLORS_LIGHT[4],
        ))
    figure_width_in_inches = 9.0
    figure, axis = _new_figure(
        width=figure_width_in_inches,
        height=max(3.0, _SwimlaneStyle.ROW_HEIGHT_IN_INCHES * len(rows) + 2.0),
    )
    # One label character, in year units: the drawn year range divided by the axes' width in
    # points. Labels claim that much space so a wide one pushes the next one down a row.
    axes_width_in_points = (
        figure_width_in_inches * _SwimlaneStyle.AXES_WIDTH_SHARE * _SwimlaneStyle.POINTS_PER_INCH
    )
    years_per_character = (
        (lanes.horizon + 2 * _SwimlaneStyle.RIGHT_MARGIN_IN_YEARS)
        * _SwimlaneStyle.EVENT_LABEL_SIZE
        * _SwimlaneStyle.CHAR_WIDTH_RATIO
        / axes_width_in_points
    )
    axis.yaxis.grid(False)
    axis.xaxis.grid(True, color=_Palette.GRID, linewidth=0.6)
    for index, (label, spans, events, color) in enumerate(rows):
        position = (len(rows) - index - 1) * _SwimlaneStyle.ROW_PITCH
        for start, end, span_label in spans:
            width = (end if end is not None else lanes.horizon) - start
            axis.barh([position], [max(width, 0.25)], left=start, height=_SwimlaneStyle.SPAN_HEIGHT,
                      color=color, alpha=0.35, linewidth=0)
            axis.text(start + 0.2, position + 0.32, span_label, fontsize=6, color=_Palette.MUTED)
        _draw_lane_events(
            axis, position, sorted(events, key=lambda item: item[0]), color,
            lanes.horizon, years_per_character,
        )
    axis.set_yticks([index * _SwimlaneStyle.ROW_PITCH for index in range(len(rows))],
                    [row[0] for row in reversed(rows)],
                    fontsize=_SwimlaneStyle.LABEL_SIZE, color=_Palette.INK)
    axis.set_xlim(
        -_SwimlaneStyle.RIGHT_MARGIN_IN_YEARS, lanes.horizon + _SwimlaneStyle.RIGHT_MARGIN_IN_YEARS
    )
    axis.set_xticks(range(0, lanes.horizon + 1, 5))
    axis.set_xlabel("year", color=_Palette.MUTED, fontsize=9)
    axis.set_title(
        f"At a glance — {result.perspective_id} (average scenario)",
        fontsize=10, color=_Palette.INK, loc="left",
    )
    figure.tight_layout()
    figure.savefig(path, facecolor=_Palette.SURFACE)
    plt.close(figure)
    return path


# ---------------------------------------------------------------------------- V10 sources & uses

def plot_sources_and_uses(result: LifecycleCostResult, path: str) -> str:
    """V10: how year 0 is funded and what it buys, as a two-column Sankey.

    The project-finance statement ("Mittelherkunft und Mittelverwendung"): every subsidy scheme
    as its own node — *state -> KfW 261 -> heat pump* reads very differently from one grey
    "subsidies" node — the loan disbursement, and own capital as the balancing item, against the
    gross year-0 uses. The two columns balance to the euro by construction, and the caption says
    so, which is what makes this a statement rather than a picture.

    Skipped, with a log line, for a pure own-capital purchase: a single ribbon says less than the
    investment waterfall already does.

    Args:
        result: The perspective whose year 0 is drawn.
        path: Destination PNG path.

    Returns:
        `path`, whether or not a file was written.
    """
    statement = views.funding_sources_and_uses(result)
    if not statement.has_external_funding():
        log.information(
            f"Sources-and-uses Sankey skipped for perspective {result.perspective_id!r}: year 0 "
            "is funded entirely from own capital, which the investment waterfall shows better."
        )
        return path
    figure, axis = _new_figure(
        height=max(3.2, 0.42 * (len(statement.sources) + len(statement.uses)) + 2.0)
    )
    color_by_source = {node.label: _category_color(node.category) for node in statement.sources}
    ribbons = [
        (f"src:{source}", f"use:{use}", amount, color_by_source[source], False)
        for source, use, amount in statement.ribbons()
        if amount > 0
    ]
    labels = {f"src:{node.label}": f"{node.label}\n{node.amount_in_euro:,.0f} EUR"
              for node in statement.sources}
    labels.update({f"use:{node.label}": f"{node.label}\n{node.amount_in_euro:,.0f} EUR"
                   for node in statement.uses})
    _draw_sankey(
        axis,
        [[f"src:{node.label}" for node in statement.sources], [f"use:{node.label}" for node in statement.uses]],
        ribbons,
        labels,
    )
    axis.set_title(
        f"Funding — {result.perspective_id} (sources and uses of funds, year 0)",
        fontsize=10, color=_Palette.INK, loc="left",
    )
    figure.text(
        0.01, 0.015,
        f"Sources {statement.total_sources_in_euro():,.0f} EUR = uses "
        f"{statement.total_uses_in_euro():,.0f} EUR = gross year-0 investment "
        f"{statement.gross_year_zero_investment_in_euro:,.0f} EUR (double entry, validated).",
        fontsize=7, color=_Palette.MUTED,
    )
    figure.tight_layout(rect=(0, 0.05, 1, 1))
    figure.savefig(path, facecolor=_Palette.SURFACE)
    plt.close(figure)
    return path


# ---------------------------------------------------------------------------- V13 benchmark

def plot_wealth_benchmark(
    reference: LifecycleCostResult, variant: LifecycleCostResult, path: str
) -> str:
    """V13: "or should I just leave the money in the bank?", for interest rates from 1 % to 10 %.

    Panel A draws the wealth advantage of renovating over time, one thin line per grid rate in a
    light-to-dark ramp, with the evaluation's own discount rate highlighted and banded. Panel B
    compresses those lines into the number people quote: the terminal advantage against the
    interest rate, whose zero crossing is the break-even rate — "if your bank pays more than X %,
    renovating loses".

    Interest is nominal and pre-tax and the caption says so (owner decision Q14): capital-income
    taxation is country-specific and this module is applied beyond Germany.

    Args:
        reference: The do-nothing baseline.
        variant: The renovation variant.
        path: Destination PNG path.

    Returns:
        `path`, unchanged.
    """
    benchmark = views.wealth_benchmark(reference, variant)
    figure, axes = plt.subplots(1, 2, figsize=(10.0, 4.2), dpi=130)
    figure.patch.set_facecolor(_Palette.SURFACE)
    for axis in axes:
        _style_axis(axis)
    colormap = plt.get_cmap("viridis")
    for index, rate in enumerate(benchmark.rates):
        series = benchmark.series_by_rate[rate]
        axes[0].plot(range(len(series)), series, linewidth=1.0,
                     color=colormap(index / max(len(benchmark.rates) - 1, 1)), label=f"{rate:.0%}")
    parameter = benchmark.parameter_series_by_slot
    axes[0].fill_between(range(len(parameter[Slot.AVERAGE])), parameter[Slot.LOW], parameter[Slot.HIGH],
                         color=PresentationStyle.GROUP_COLORS_LIGHT[0], alpha=0.18, linewidth=0)
    axes[0].plot(range(len(parameter[Slot.AVERAGE])), parameter[Slot.AVERAGE], linewidth=2.4,
                 color=PresentationStyle.GROUP_COLORS_LIGHT[0],
                 label=f"parameter rate {benchmark.parameter_rate:.1%}")
    axes[0].axhline(0, color=_Palette.MUTED, linewidth=0.8)
    axes[0].set_xlabel("year", color=_Palette.MUTED, fontsize=9)
    axes[0].set_ylabel("advantage of renovating [EUR]", color=_Palette.MUTED, fontsize=9)
    axes[0].set_title("A — wealth advantage over time", fontsize=9, color=_Palette.INK, loc="left")
    axes[0].legend(fontsize=6, frameon=False, ncol=2, labelcolor=_Palette.INK)
    axes[1].plot([rate * 100 for rate in benchmark.rates],
                 [benchmark.terminal_by_rate[rate] for rate in benchmark.rates],
                 marker="o", markersize=4, linewidth=1.8, color=PresentationStyle.GROUP_COLORS_LIGHT[0])
    axes[1].axhline(0, color=_Palette.MUTED, linewidth=0.8)
    for crossing in benchmark.break_even_rates:
        axes[1].axvline(crossing * 100, color=_Palette.INK, linewidth=0.9, linestyle=":")
        axes[1].annotate(f"break-even {crossing:.1%}", xy=(crossing * 100, 0), xytext=(4, 8),
                         textcoords="offset points", fontsize=7, color=_Palette.INK)
    axes[1].set_xlabel("interest rate [%] (nominal, pre-tax)", color=_Palette.MUTED, fontsize=9)
    axes[1].set_ylabel("terminal advantage [EUR]", color=_Palette.MUTED, fontsize=9)
    axes[1].set_title("B — terminal advantage vs rate", fontsize=9, color=_Palette.INK, loc="left")
    figure.suptitle(
        f"Bank benchmark — {variant.perspective_id} (renovate, or bank the money? average scenario)",
        fontsize=10, color=_Palette.INK, x=0.01, ha="left",
    )
    figure.text(
        0.01, 0.015,
        "Interest is nominal and pre-tax: capital-income taxation is country-specific\n"
        "(Abgeltungsteuer is only the German case) and is deliberately not modelled.\n"
        "Break-even rate(s) are reported only inside the 1-10 % window shown.",
        fontsize=6.5, color=_Palette.MUTED,
    )
    figure.tight_layout(rect=(0, 0.14, 1, 0.95))
    figure.savefig(path, facecolor=_Palette.SURFACE)
    plt.close(figure)
    return path


# ---------------------------------------------------------------------------- V14 monthly burden

def plot_monthly_burden(result: LifecycleCostResult, path: str) -> str:
    """V14: what this costs per month, year by year, stacked by display group.

    The lay-reader counterpart of V2: the same flows, translated into the unit households budget
    in. Recurring cost only — debt service, energy, maintenance, taxes and levies, minus
    recurring credits. **All capital events are excluded**, the year-0 investment and the
    replacement years alike (owner decision Q15 as revised): a replacement is the same economic
    object as the initial investment, so showing one and hiding the other was inconsistent. They
    return as the dashed "with replacement reserve" line — the equivalent annual cost of the
    replacement flows over twelve months, the sinking fund a prudent owner pays into.

    The whiskers on the monthly *total* are this chart's one banded mark.

    Args:
        result: The perspective whose burden is drawn.
        path: Destination PNG path.

    Returns:
        `path`, unchanged.
    """
    burden = views.monthly_burden_series(result)
    totals = burden.series
    per_group = views.monthly_burden_by_group(result, PresentationStyle.CATEGORY_TO_GROUP)
    years = list(range(len(totals)))
    figure, axis = _new_figure(height=4.0)
    bottom_pos = [0.0] * len(years)
    bottom_neg = [0.0] * len(years)
    for index, (group_name, _categories) in enumerate(PresentationStyle.DISPLAY_GROUPS):
        values = [row.get(index, 0.0) for row in per_group]
        if not any(values):
            continue
        positives = [max(value, 0.0) for value in values]
        negatives = [min(value, 0.0) for value in values]
        if any(positives):
            axis.bar(years, positives, bottom=bottom_pos, width=0.82, label=group_name,
                     color=PresentationStyle.GROUP_COLORS_LIGHT[index], linewidth=0.5,
                     edgecolor=_Palette.SURFACE)
            bottom_pos = [base + value for base, value in zip(bottom_pos, positives)]
        if any(negatives):
            axis.bar(years, negatives, bottom=bottom_neg, width=0.82,
                     label=None if any(positives) else group_name,
                     color=PresentationStyle.GROUP_COLORS_LIGHT[index], linewidth=0.5,
                     edgecolor=_Palette.SURFACE)
            bottom_neg = [base + value for base, value in zip(bottom_neg, negatives)]
    axis.errorbar(
        years, [value.average for value in totals],
        yerr=[[value.average - value.minimum for value in totals],
              [value.maximum - value.average for value in totals]],
        fmt="none", ecolor=_Palette.INK, elinewidth=0.9, capsize=2,
    )
    reserve = burden.replacement_reserve_per_month
    if reserve:
        # One dashed segment per bar rather than one line across the chart: the reserve is a
        # constant, but what the reader budgets is the bar *plus* the reserve, which is not.
        for year, band in zip(years, totals):
            axis.hlines(
                band.average + reserve, year - 0.41, year + 0.41, colors=_Palette.INK,
                linestyles="dashed", linewidth=1.2,
                label="with replacement reserve" if year == 0 else None,
            )
    axis.axhline(0, color=_Palette.MUTED, linewidth=0.8)
    axis.set_xlabel("year", color=_Palette.MUTED, fontsize=9)
    axis.set_ylabel("EUR per month (nominal)", color=_Palette.MUTED, fontsize=9)
    axis.set_title(
        f"Monthly burden — {result.perspective_id} (recurring cost, average scenario with "
        "min/max whiskers)",
        fontsize=10, color=_Palette.INK, loc="left",
    )
    axis.legend(fontsize=7, frameon=False, ncol=3, labelcolor=_Palette.INK)
    figure.text(
        0.01, 0.045,
        "Excludes every capital event — the year-0 investment and its financing, and the "
        "replacement years; the funding statement and the cash-flow timeline show those.",
        fontsize=7, color=_Palette.MUTED,
    )
    figure.text(
        0.01, 0.015,
        f"The dashed line adds the replacement reserve of {reserve:,.0f} EUR/month, the "
        "equivalent annual cost of the replacement flows spread over twelve months.",
        fontsize=7, color=_Palette.MUTED,
    )
    figure.tight_layout(rect=(0, 0.08, 1, 1))
    figure.savefig(path, facecolor=_Palette.SURFACE)
    plt.close(figure)
    return path


def write_report_plots(
    matrix: EvaluationMatrix,
    result_directory: str,
    reference_result: Optional[LifecycleCostResult] = None,
) -> List[str]:
    """Writes the PNG set for the first perspective (+ the comparison charts when comparing).

    The module's main public orchestration point: called by `bridge.py` right after the HTML and
    markdown reports are written, and by the `report` CLI, so the PNG set always accompanies a
    report rather than being generated on its own. Only the matrix's first perspective is
    plotted — the PNGs are a hand-out, and the full per-perspective treatment is the HTML
    report's job — and an empty matrix produces no files instead of an error. The audit-side
    heatmap has its own entry point, `write_audit_plots`.

    Charts that need a reference (the comparison bridge, the fixed-interest benchmark) are
    written only when one is given, and their absence is logged rather than silent; the same
    holds for the charts that skip themselves on a degenerate input (a single-actor Sankey, an
    all-own-capital funding statement).

    Args:
        matrix: Evaluated perspectives; the first one is the subject of the per-result charts.
        result_directory: Directory the `lifecycle_*.png` files are written into (next to the
            HTML report).
        reference_result: When given, the baseline of a variant comparison; adds the payback
            curve. The variant side is this matrix's result for the *same* perspective id, so
            the comparison is like-for-like, falling back to the first perspective if the matrix
            does not carry that id.

    Returns:
        The paths that actually exist on disk, so a caller can log or attach exactly what was
        written (charts that had nothing to draw are filtered out here).
    """
    written: List[str] = []
    first = next(iter(matrix.results.values()), None)
    if first is None:
        return written
    written.append(
        plot_annual_cash_flows(first, os.path.join(result_directory, "lifecycle_annual_cash_flows.png"))
    )
    written.append(
        plot_investment_waterfall(first, os.path.join(result_directory, "lifecycle_investment_waterfall.png"))
    )
    written.append(plot_perspective_costs(matrix, os.path.join(result_directory, "lifecycle_perspective_costs.png")))
    written.append(plot_component_costs(first, os.path.join(result_directory, "lifecycle_component_costs.png")))
    comparison: Optional[VariantComparison] = None
    variant = first
    if reference_result is not None:
        from hisim.economics.results import compare

        variant = matrix.results.get(reference_result.perspective_id, first)
        comparison = compare(reference_result, variant)
        written.append(
            plot_payback_curve(reference_result, variant, os.path.join(result_directory, "lifecycle_payback_curve.png"))
        )
    # The visualization set's pasteable subset (owner decision Q1): V1, V2, V4, V8, V9, V10, V13
    # and V14. Everything else in that set is HTML-only, and V6 goes with the audit outputs.
    written.append(plot_lifecycle_swimlane(first, os.path.join(
        result_directory, "lifecycle_swimlane.png"), comparison))
    written.append(plot_actor_flows(first, os.path.join(result_directory, "lifecycle_actor_flows.png")))
    written.append(plot_liquidity_fan(first, os.path.join(
        result_directory, "lifecycle_liquidity_fan.png"), comparison))
    written.append(plot_sources_and_uses(first, os.path.join(result_directory, "lifecycle_sources_and_uses.png")))
    written.append(plot_cost_treemap(first, os.path.join(result_directory, "lifecycle_cost_treemap.png")))
    written.append(plot_monthly_burden(first, os.path.join(result_directory, "lifecycle_monthly_burden.png")))
    if reference_result is not None:
        written.append(plot_comparison_bridge(reference_result, variant, os.path.join(
            result_directory, "lifecycle_comparison_bridge.png")))
        written.append(plot_wealth_benchmark(reference_result, variant, os.path.join(
            result_directory, "lifecycle_wealth_benchmark.png")))
    else:
        log.information(
            "Comparison charts (bridge, fixed-interest benchmark) skipped: this run has no "
            "reference variant to compare against."
        )
    return [path for path in written if os.path.isfile(path)]
