"""Matplotlib PNG companions of the lifecycle cost report.

`write_report_plots` draws the report set next to `lifecycle_report.html`, one set per perspective
(`lifecycle_<chart>_<perspective_id>.png`) plus `lifecycle_perspective_costs.png` once; comparison charts are drawn
only for the perspective the reference variant names. `write_audit_plots` draws the year × category ledger heatmap next
to `cost_audit.csv` on every cost run, which makes matplotlib a dependency of the plain cost path. The PNGs are for
pasting into documents; the HTML report is the full output.

Nothing is computed here: figures come from `views.py` and `results.py`, colours and display groups from
`presentation_style.py`, and the shared captions from `report_prose.py`. A chart with nothing to draw writes no file
and returns a `SkippedPlot` to its caller; nothing here logs. The PNGs are not golden-tested, since matplotlib output
is not byte-stable.
"""

from __future__ import annotations

import contextlib
import os
import textwrap
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

import matplotlib

matplotlib.use("Agg")  # postprocessing runs headless

# The backend has to be chosen before pyplot is imported, which is exactly what both linters
# read as a misplaced import; every import below this line is in that position on purpose.
# pylint: disable=wrong-import-position
import matplotlib.pyplot as plt  # noqa: E402  — backend must be set before pyplot
from matplotlib.axes import Axes  # noqa: E402
from matplotlib.colors import SymLogNorm  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.patches import PathPatch, Rectangle  # noqa: E402
from matplotlib.path import Path as MplPath  # noqa: E402

from hisim.economics import views  # noqa: E402
from hisim.economics.presentation_style import (  # noqa: E402
    ChromeColors,
    PresentationStyle,
    SankeyLayout,
    SequentialRamp,
    group_name,
    group_of,
    sankey_node_boxes,
    squarified_layout,
)
from hisim.economics.report_prose import (  # noqa: E402
    ReportProse,
    payback_interval_sentence,
    treemap_disclosure,
)
from hisim.economics.results import (  # noqa: E402
    EvaluationMatrix,
    LifecycleCostResult,
    VariantComparison,
    compare,
    cumulative_discounted_savings,
    PaybackEnvelope,
)
from hisim.economics.timeline import CostCategory  # noqa: E402
from hisim.economics.uncertainty import Slot  # noqa: E402

# pylint: enable=wrong-import-position


@dataclass(frozen=True)
class SkippedPlot:
    """One chart that was not drawn, and why.

    Callers decide how to report it: `bridge.py` logs it and writes `lifecycle_plots_not_drawn.txt`, the CLI prints it.

    Attributes:
        chart: The chart, named as a reader of the output directory would name it.
        perspective_id: The perspective it would have been drawn for; empty for a skip that belongs to no single
            perspective (e.g. comparison charts of a run without a reference variant).
        reason: One sentence saying what was missing.
    """

    chart: str
    perspective_id: str
    reason: str

    def as_line(self) -> str:
        """Return `chart (perspective): reason`, the line the log and the sidecar file print."""
        subject = f"{self.chart} ({self.perspective_id})" if self.perspective_id else self.chart
        return f"{subject}: {self.reason}"


@dataclass
class PlotsWritten:
    """What a writer produced: the files on disk and the charts that drew nothing.

    `paths` lists what was written (the bridge removes them again if the run fails later); `skipped` lists what was not
    drawn, so a missing figure always has a stated reason.
    """

    paths: List[str] = field(default_factory=list)
    skipped: List[SkippedPlot] = field(default_factory=list)

    def lines(self) -> List[str]:
        """Return one `chart (perspective): reason` line per skip, in drawing order."""
        return [skip.as_line() for skip in self.skipped]


def _skip(
    collector: Optional[List[SkippedPlot]], chart: str, perspective_id: str, reason: str
) -> None:
    """Record one skip in the caller's collector, if the caller passed one.

    The collector is optional so a plot function can be called on its own, e.g. from a test.
    """
    if collector is not None:
        collector.append(SkippedPlot(chart=chart, perspective_id=perspective_id, reason=reason))


class _Typography:
    """Text metrics for estimating a string's width without a matplotlib draw.

    Shared by the treemap's label-fit check and the swimlane's event-label spacing.
    """

    #: Width of an average character as a fraction of the font size — a standard DejaVu Sans
    #: approximation, deliberately generous so the estimate errs towards suppressing a label
    #: rather than towards printing one across its neighbour.
    CHAR_WIDTH_RATIO = 0.62
    #: Points per inch, so a font size in points and a figure size in inches meet in one unit.
    POINTS_PER_INCH = 72.0


class _Palette:
    """Neutral chrome colours (surface, ink, grid) of the PNGs: the light half of `ChromeColors`.

    Read from `presentation_style.ChromeColors`, which the HTML report uses too, so both outputs share the same greys;
    data colours come from `PresentationStyle.GROUP_COLORS_LIGHT`. A PNG has no theme, so only the light set is used.
    """

    SURFACE = ChromeColors.LIGHT["surface"]
    INK = ChromeColors.LIGHT["ink"]
    MUTED = ChromeColors.LIGHT["muted"]
    GRID = ChromeColors.LIGHT["grid"]


def _style_axis(axis) -> None:
    """Apply the shared chart chrome to one matplotlib axis.

    Drops the top and right spines, mutes the others and puts a horizontal grid behind the marks; charts with a
    horizontal value axis change the grid direction afterwards.
    """
    axis.set_facecolor(_Palette.SURFACE)
    for spine in ("top", "right"):
        axis.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        axis.spines[spine].set_color(_Palette.GRID)
    axis.tick_params(colors=_Palette.MUTED, labelsize=8)
    axis.yaxis.grid(True, color=_Palette.GRID, linewidth=0.6)
    axis.set_axisbelow(True)


@contextlib.contextmanager
def _figure(
    width: float = 9.0,
    height: float = 4.2,
    panels: Tuple[int, int] = (1, 1),
    share_x: bool = False,
) -> Iterator[Tuple[Figure, List[Axes]]]:
    """Yield a styled figure and its axes at the report's fixed resolution, and always close it.

    Every chart is built with this, so all PNGs share a DPI and chrome. Two-panel charts (the liquidity fan, the
    treemap pair, the benchmark pair) pass `panels`. Closing in `finally`, with `plt.subplots` inside the `try`, keeps
    pyplot's global registry from leaking a figure when drawing fails.

    Args:
        width: Figure width in inches.
        height: Figure height in inches; horizontal charts scale it with their row count.
        panels: `(rows, columns)` of the axes grid.
        share_x: Whether the panels share one x axis.

    Yields:
        The figure and its styled axes as a flat list of length `rows * columns` in row-major order, also for a single
            panel.
    """
    rows, columns = panels
    figure: Optional[Figure] = None
    try:
        figure, grid = plt.subplots(
            rows, columns, figsize=(width, height), dpi=130, sharex=share_x, squeeze=False
        )
        figure.patch.set_facecolor(_Palette.SURFACE)
        axes = [axis for row in grid for axis in row]
        for axis in axes:
            _style_axis(axis)
        yield figure, axes
    finally:
        if figure is not None:
            plt.close(figure)


def _stack_positive_negative(
    axis,
    positions: Sequence[float],
    values_by_group: Mapping[int, Sequence[float]],
    horizontal: bool = False,
    bar_size: float = 0.82,
    edge_width: float = 0.6,
) -> Tuple[List[float], List[float]]:
    """Stack one signed bar chart by display group, with costs and credits on separate baselines.

    A display group is one of the colour groups of `presentation_style`. Positive values stack from one baseline and
    negative ones from another, so costs and credits are never netted. Groups are drawn in ascending index order; an
    all-zero group draws nothing and gets no legend entry, and each group is labelled once.

    Args:
        axis: The axes to draw on.
        positions: The bar positions: years for a vertical chart, row indices for a horizontal one.
        values_by_group: Display-group index -> one signed value per position.
        horizontal: Draw with `barh` instead of `bar`.
        bar_size: Bar thickness across the position axis, in position units.
        edge_width: Width of the surface-coloured line between two stacked segments.

    Returns:
        The positive and negative stack ends per position after the last group, where a caller places per-bar
            annotations.
    """
    draw = axis.barh if horizontal else axis.bar
    baseline_keyword = "left" if horizontal else "bottom"
    size_keyword = "height" if horizontal else "width"
    places = list(positions)
    bottom_positive = [0.0] * len(places)
    bottom_negative = [0.0] * len(places)
    for index in sorted(values_by_group):
        values = list(values_by_group[index])
        if not any(values):
            continue
        positives = [max(value, 0.0) for value in values]
        negatives = [min(value, 0.0) for value in values]
        color = PresentationStyle.GROUP_COLORS_LIGHT[index]
        if any(positives):
            draw(places, positives, label=group_name(index), color=color, linewidth=edge_width,
                 edgecolor=_Palette.SURFACE,
                 **{baseline_keyword: bottom_positive, size_keyword: bar_size})
            bottom_positive = [base + value for base, value in zip(bottom_positive, positives)]
        if any(negatives):
            # A group with bars on both sides is one legend entry, not two: the second call is
            # the same colour and the same name, and a repeated legend row reads as two groups.
            draw(places, negatives, label=None if any(positives) else group_name(index),
                 color=color, linewidth=edge_width, edgecolor=_Palette.SURFACE,
                 **{baseline_keyword: bottom_negative, size_keyword: bar_size})
            bottom_negative = [base + value for base, value in zip(bottom_negative, negatives)]
    return bottom_positive, bottom_negative


def plot_annual_cash_flows(
    result: LifecycleCostResult, path: str, skips: Optional[List[SkippedPlot]] = None
) -> Optional[str]:
    """Draw stacked nominal bars per year by display group, the PNG of the cash-flow timeline section.

    Costs stack above the zero line and credits below, never netted. Replacements should spike at component lifetimes,
    the residual value appear at the horizon and the investment in year 0. A perspective whose scoped timeline carries
    no money is skipped.

    Args:
        result: The evaluated perspective; the title carries its id and NPV.
        path: Destination PNG path.
        skips: Collector for the skip record, if the caller is collecting.

    Returns:
        `path` when the chart was written, None when it had nothing to draw.
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
    if not any(any(values) for values in per_group.values()):
        _skip(
            skips, "annual cash flows", result.perspective_id,
            "no year of the scoped timeline carries a non-zero amount in any display group, so "
            "every bar of the chart would have height zero.",
        )
        return None
    with _figure() as (figure, axes):
        axis = axes[0]
        _stack_positive_negative(axis, years, per_group)
        axis.axhline(0, color=_Palette.MUTED, linewidth=0.8)
        axis.set_xlabel("year", color=_Palette.MUTED, fontsize=9)
        axis.set_ylabel("nominal EUR per year", color=_Palette.MUTED, fontsize=9)
        axis.set_title(
            f"Annual cash flows — {result.perspective_id} "
            f"(NPV {result.total_npv_in_euro.best_estimate:,.0f} EUR)",
            fontsize=10, color=_Palette.INK, loc="left",
        )
        axis.legend(fontsize=7.5, frameon=False, ncol=2, labelcolor=_Palette.INK)
        figure.tight_layout()
        figure.savefig(path, facecolor=_Palette.SURFACE)
    return path


def plot_investment_waterfall(
    result: LifecycleCostResult, path: str, skips: Optional[List[SkippedPlot]] = None
) -> Optional[str]:
    """Draw the year-0 investment per subject, each bar split into the net and the subsidy-covered part.

    A subject is one costed item, such as a heat pump. Both figures come from `views.subsidy_share_of_gross` (subsidy
    clamped to the gross), which also feeds the HTML report's subsidy bars. Net is drawn in the group-0 hue and the
    subsidy share in the group-3 hue, with the net/gross figures at each bar's end. Subjects without positive gross
    investment are left out; with none at all the chart is skipped.

    Args:
        result: The evaluated perspective whose year-0 investment is drawn.
        path: Destination PNG path.
        skips: Collector for the skip record, if the caller is collecting.

    Returns:
        `path` when the chart was written, None when it had nothing to draw.
    """
    subjects, gross_values, net_values, subsidy_values = [], [], [], []
    for share in views.subsidy_share_of_gross(result).values():
        subjects.append(share.subject)
        gross_values.append(share.gross_in_euro)
        subsidy_values.append(share.subsidy_in_euro)
        net_values.append(share.net_in_euro)
    if not subjects:
        _skip(
            skips, "investment build-up", result.perspective_id,
            "no subject carries a positive year-0 investment, so there is no gross to split.",
        )
        return None
    with _figure(height=max(2.2, 0.55 * len(subjects) + 1.2)) as (figure, axes):
        axis = axes[0]
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
    return path


def plot_perspective_costs(matrix: EvaluationMatrix, path: str) -> str:
    """Draw the equivalent annual cost per perspective as dots with min/max whiskers.

    The PNG of the perspectives section and the only chart covering the whole matrix. The whiskers are the §3.9
    envelope of the low and high worlds, not a confidence interval.

    Args:
        matrix: All evaluated perspectives, drawn top to bottom in insertion order.
        path: Destination PNG path.

    Returns:
        `path`, unchanged.
    """
    labels, best_estimates, lows, highs = [], [], [], []
    for perspective_id, result in matrix.results.items():
        band = result.equivalent_annual_cost_in_euro
        labels.append(perspective_id)
        best_estimates.append(band.best_estimate)
        lows.append(band.best_estimate - band.minimum)
        highs.append(band.maximum - band.best_estimate)
    with _figure(height=max(2.2, 0.5 * len(labels) + 1.2)) as (figure, axes):
        axis = axes[0]
        positions = range(len(labels))
        axis.errorbar(best_estimates, positions, xerr=[lows, highs], fmt="o",
                      color=PresentationStyle.GROUP_COLORS_LIGHT[0],
                      ecolor=PresentationStyle.GROUP_COLORS_LIGHT[0], elinewidth=2, capsize=3, markersize=7,
                      markeredgecolor=_Palette.SURFACE, markeredgewidth=1.5)
        for position, (_label, best_estimate) in enumerate(zip(labels, best_estimates)):
            axis.text(best_estimates[position] + highs[position] + max(best_estimates) * 0.02, position,
                      f"{best_estimate:,.0f} EUR/a", va="center", fontsize=7.5, color=_Palette.MUTED)
        axis.set_yticks(list(positions), labels, fontsize=8, color=_Palette.INK)
        axis.invert_yaxis()
        axis.xaxis.grid(True, color=_Palette.GRID, linewidth=0.6)
        axis.yaxis.grid(False)
        axis.set_xlabel("equivalent annual cost [EUR/a] with min/max band", color=_Palette.MUTED, fontsize=9)
        axis.set_title("Perspectives at a glance", fontsize=10, color=_Palette.INK, loc="left")
        figure.tight_layout()
        figure.savefig(path, facecolor=_Palette.SURFACE)
    return path


#: Distance from the bottom of the axes to a legend placed below them, in inches. Half an inch
#: clears the x-axis label at every figure height the per-component chart is drawn at, and staying
#: a fixed *distance* is the point: an anchor in axes fractions would drift as the row count grows.
_LEGEND_DROP_IN_INCHES = 0.55

#: How much of the per-component figure's height is margins, title and axis label rather than axes.
_COMPONENT_CHROME_IN_INCHES = 1.2

#: Gap between the end of a row and its label, as a share of the widest row; the constant term
#: keeps the two apart on a chart whose rows are all tiny.
_LABEL_GAP_SHARE = 0.035

#: Extra x range reserved past the longest label so the text is not clipped at the frame.
_LABEL_RESERVE_SHARE = 0.30


def _component_label_geometry(
    cost_ends: Sequence[float], credit_ends: Sequence[float], band_maxima: Sequence[float]
) -> Tuple[List[float], float, float]:
    """Return where each component row's net-NPV label starts, its gap, and the widest row extent.

    The label starts past the further of the cost stack's end and the net band's upper whisker, so it never overprints
    the marker.

    Args:
        cost_ends: End of each row's positive (cost) stack, the first baseline `_stack_positive_negative` returns.
        credit_ends: End of each row's negative (credit) stack; only its width matters, for the left axis reserve.
        band_maxima: Upper bound of each row's net NPV band.

    Returns:
        The x each row's label starts at, the gap to leave before the text, and the widest extent any row reaches.
    """
    label_starts = [
        max(cost_end, maximum, 0.0) for cost_end, maximum in zip(cost_ends, band_maxima)
    ]
    span = max(label_starts + [abs(value) for value in credit_ends] + [1.0])
    return label_starts, span * _LABEL_GAP_SHARE + 1.0, span


def _legend_below_axes_anchor(figure_height_in_inches: float) -> float:
    """Return the `bbox_to_anchor` y that places a legend half an inch below the axes at any figure height.

    A diverging horizontal stack has no reliably empty corner, so the component chart's legend goes outside the axes.

    Args:
        figure_height_in_inches: The figure's height.

    Returns:
        A negative y anchor, to be used with `loc="upper center"`.
    """
    axes_height_in_inches = max(figure_height_in_inches - _COMPONENT_CHROME_IN_INCHES, 1.0)
    return -_LEGEND_DROP_IN_INCHES / axes_height_in_inches


def plot_component_costs(
    result: LifecycleCostResult, path: str, skips: Optional[List[SkippedPlot]] = None
) -> Optional[str]:
    """Draw per-subject NPV as diverging stacks (§7.4): costs right of 0, credits left, a net marker.

    Credits (residual value, subsidies, feed-in, anyway credit) are never netted onto the cost side; the black marker
    with whiskers is the net NPV band. Labels start past the stack and whisker (`_component_label_geometry`) and the
    legend sits below the axes (`_legend_below_axes_anchor`), so the figure is saved with `bbox_inches="tight"`. A
    result without component breakdowns is skipped.

    Args:
        result: The evaluated perspective whose subjects are drawn.
        path: Destination PNG path.
        skips: Collector for the skip record, if the caller is collecting.

    Returns:
        `path` when the chart was written, None when it had nothing to draw.
    """
    breakdowns = list(result.component_breakdowns.items())
    if not breakdowns:
        _skip(
            skips, "per-component costs", result.perspective_id,
            "the result carries no component breakdowns, so the chart would have no rows.",
        )
        return None
    with _figure(height=max(2.4, 0.55 * len(breakdowns) + 1.4)) as (figure, axes):
        axis = axes[0]
        positions = list(range(len(breakdowns)))
        per_subject = [
            views.fold_categories(breakdown.npv_by_category, PresentationStyle.CATEGORY_TO_GROUP)
            for _subject, breakdown in breakdowns
        ]
        lefts_pos, lefts_neg = _stack_positive_negative(
            axis,
            positions,
            {
                index: [grouped[index].best_estimate if index in grouped else 0.0 for grouped in per_subject]
                for index in range(len(PresentationStyle.DISPLAY_GROUPS))
            },
            horizontal=True,
            bar_size=0.8,
        )
        axis.axvline(0, color=_Palette.MUTED, linewidth=0.9)
        # Net NPV band per subject: black dot with min/max whiskers on the same signed axis.
        nets = [breakdown.total_npv_in_euro for _subject, breakdown in breakdowns]
        label_starts, gap, span = _component_label_geometry(
            lefts_pos, lefts_neg, [band.maximum for band in nets]
        )
        axis.errorbar(
            [band.best_estimate for band in nets],
            positions,
            xerr=[
                [band.best_estimate - band.minimum for band in nets],
                [band.maximum - band.best_estimate for band in nets],
            ],
            fmt="o", color=_Palette.INK, ecolor=_Palette.INK, elinewidth=1.4, capsize=3, markersize=5,
            markeredgecolor=_Palette.SURFACE, markeredgewidth=1.2, label="net NPV (band)",
        )
        for position, band in enumerate(nets):
            axis.text(label_starts[position] + gap, position,
                      f"{band.best_estimate:,.0f} [{band.minimum:,.0f} | {band.maximum:,.0f}]",
                      va="center", fontsize=7, color=_Palette.MUTED)
        axis.set_yticks(positions, [subject for subject, _b in breakdowns], fontsize=8, color=_Palette.INK)
        axis.invert_yaxis()
        axis.xaxis.grid(True, color=_Palette.GRID, linewidth=0.6)
        axis.yaxis.grid(False)
        # The labels are drawn in data coordinates, so the axis makes room for them in proportion
        # to the label width. The left limit clears the furthest-left mark in the row, which is
        # the net band's lower cap whenever the band reaches past the credit stack.
        axis.set_xlim(min(list(lefts_neg) + [band.minimum for band in nets] + [0.0]) - gap,
                      max(label_starts) + gap + span * _LABEL_RESERVE_SHARE)
        axis.set_xlabel("NPV [EUR] — credits left of 0, costs right; marker = net NPV band",
                        color=_Palette.MUTED, fontsize=9)
        axis.set_title(f"Per-component costs — {result.perspective_id}", fontsize=10, color=_Palette.INK, loc="left")
        axis.legend(fontsize=7.5, frameon=False, ncol=3, labelcolor=_Palette.INK, loc="upper center",
                    bbox_to_anchor=(0.5, _legend_below_axes_anchor(figure.get_figheight())))
        figure.tight_layout()
        figure.savefig(path, facecolor=_Palette.SURFACE, bbox_inches="tight")
    return path


def _comparison_basis(reference: LifecycleCostResult, variant: LifecycleCostResult) -> str:
    """Return the perspective a comparison chart is drawn on, worded for a title and an axis label.

    Payback differs between perspectives (a net basis pays back earlier), so the payback curve, the NPV bridge and the
    benchmark name their basis; when the two sides are different perspectives, both are named.

    Args:
        reference: The baseline result.
        variant: The variant result.

    Returns:
        The basis, as it should appear in the chart.
    """
    if variant.perspective_id == reference.perspective_id:
        return variant.perspective_id
    return f"{variant.perspective_id} vs {reference.perspective_id}"


def plot_payback_curve(
    reference: LifecycleCostResult, variant: LifecycleCostResult, path: str
) -> str:
    """Draw the cumulative discounted savings (reference minus variant) per band slot; the zero crossing is the payback.

    The curves come from `results.cumulative_discounted_savings`, which also yields the printed payback year. Title and
    y label name the basis via `_comparison_basis`.
    """
    curves = cumulative_discounted_savings(reference, variant)
    years = list(range(len(curves["best_estimate"])))
    with _figure(height=3.6) as (figure, axes):
        axis = axes[0]
        # Named by world, not as optimistic/pessimistic: which one pays back first depends on
        # which uncertainty dominates the savings.
        styles = {"low": (":", 1.2, "LOW world"), "best_estimate": ("-", 2.2, "expected"),
                  "high": ("--", 1.2, "HIGH world")}
        for slot, (linestyle, linewidth, label) in styles.items():
            axis.plot(years, curves[slot], linestyle, linewidth=linewidth,
                      color=PresentationStyle.GROUP_COLORS_LIGHT[0], label=label)
        axis.axhline(0, color=_Palette.MUTED, linewidth=0.8)
        axis.set_xlabel("year", color=_Palette.MUTED, fontsize=9)
        basis = _comparison_basis(reference, variant)
        axis.set_ylabel(f"cumulative discounted savings vs the reference [EUR] — {basis} basis",
                        color=_Palette.MUTED, fontsize=9)
        axis.set_title(f"Discounted payback (zero-crossing) — {basis} basis", fontsize=10,
                       color=_Palette.INK, loc="left")
        axis.legend(fontsize=7.5, frameon=False, labelcolor=_Palette.INK)
        figure.tight_layout()
        figure.savefig(path, facecolor=_Palette.SURFACE)
    return path


# ---------------------------------------------------------------------------- Sankey machinery
#
# The actor-flow and sources-and-uses Sankeys share one layout and one ribbon primitive.
# `matplotlib.sankey` draws radial diagrams with fixed arrow stubs and cannot express columns of
# nodes with proportional ribbons, so the ribbons are hand-drawn cubic Bézier patches.

class _SankeyStyle:
    """Ribbon transparency and font sizes of the matplotlib Sankeys.

    Node and column geometry lives in `presentation_style.SankeyLayout`, shared with the SVG report.
    """

    NODE_WIDTH = SankeyLayout.NODE_WIDTH
    CURVATURE = SankeyLayout.CURVATURE
    RIBBON_ALPHA = 0.55
    LABEL_SIZE = 7.0
    VALUE_SIZE = 6.5
    #: Artist label of a net-position stub plate, so a reader of the axis (the geometry tests)
    #: can tell a stub from a node rectangle without measuring it.
    STUB_LABEL = "net-stub"


def _draw_ribbon(
    axis, left: Tuple[float, float], right: Tuple[float, float], band_height: float, color: str,
) -> None:
    """Draw one Bézier ribbon between two vertical faces as a single closed `PathPatch`.

    `left` and `right` are the `(x, y_bottom)` attachment points and `band_height` the ribbon's width, the same at both
    ends because the diagram has one global euro scale.
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
            facecolor=color,
            edgecolor=color,
            alpha=_SankeyStyle.RIBBON_ALPHA,
            linewidth=0.6,
        )
    )


def _draw_net_stubs(axis, geometry, stub_labels: Optional[Dict[str, str]] = None) -> None:
    """Draw the net-position stubs of a Sankey's internal nodes.

    A stub is a short flat plate off the face the node's ribbons do not fill, labelled with the signed amount and drawn
    in the muted ink, so it reads as a remainder rather than a payment. It makes both faces of every node fully
    covered, as in the SVG version.
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
    ribbons: Sequence[Tuple[str, str, float, str]],
    node_labels: Optional[Dict[str, str]] = None,
    stub_labels: Optional[Dict[str, str]] = None,
) -> None:
    """Draw a column Sankey: node rectangles plus one ribbon per flow.

    Ribbons leave a node's right face and arrive at the next node's left face in the given order, stacking so each face
    is exactly filled. Every ribbon keeps one width, from the global euro scale `sankey_node_boxes` returns. Labels sit
    outside the first and last columns and above the middle ones.
    """
    geometry = sankey_node_boxes(
        [list(column) for column in columns],
        [(source, target, amount) for source, target, amount, _color in ribbons],
    )
    boxes = geometry.boxes
    axis.set_xlim(-0.02, 1.02)
    axis.set_ylim(-0.06, 1.06)
    axis.axis("off")
    for index, (source, target, amount, color) in enumerate(ribbons):
        if source not in boxes or target not in boxes:
            continue
        band_height = amount * geometry.unit_scale
        # A ribbon spanning more than one column gap is drawn as a chain of legs through the
        # corridors the layout reserved, so it cannot cross an intervening node's rectangle.
        for leg in geometry.ribbon_segments[index]:
            if leg.source not in boxes or leg.target not in boxes:
                continue
            source_x, source_y, _source_h = boxes[leg.source]
            target_x, target_y, _target_h = boxes[leg.target]
            left = (source_x + _SankeyStyle.NODE_WIDTH, source_y + leg.out_anchor)
            right = (target_x, target_y + leg.in_anchor)
            _draw_ribbon(axis, left, right, band_height, color)
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
    """Return the display-group colour of a category, or the muted grey for the folded "other" ribbon.

    Looked up through `presentation_style.group_of`, so the same money has the same hue in every output.
    """
    if category is None:
        return _Palette.MUTED
    return PresentationStyle.GROUP_COLORS_LIGHT[group_of(category)]


# ---------------------------------------------------------------------------- actor flows

def plot_actor_flows(
    result: LifecycleCostResult, path: str, skips: Optional[List[SkippedPlot]] = None
) -> Optional[str]:
    """Draw who pays whom over the horizon as a column Sankey (nominal euros, best estimate).

    Sources on the left, one column per actor in the order of `actor_columns`, sinks on the right, so a transfer
    between actors (the §559e levy) is an ordinary left-to-right ribbon. Ribbon widths are lifetime nominal euros of
    the best-estimate slot; the band of the total is in the title. A perspective with fewer than two actors is skipped.

    Args:
        result: The evaluated perspective; its full timeline is read, so every payer appears.
        path: Destination PNG path.
        skips: Collector for the skip record, if the caller is collecting.

    Returns:
        `path` when the chart was written, None when it had nothing to draw.
    """
    matrix = views.actor_flow_matrix(result)
    if len(matrix.actors) < 2:
        _skip(
            skips, "actor-flow Sankey", result.perspective_id,
            f"the perspective has {len(matrix.actors)} actor node(s), so there is no "
            "who-pays-whom story to draw.",
        )
        return None

    def node_key(node: str, is_target: bool) -> str:
        """Return a column-qualified node id, since a counterparty can be both a source and a sink."""
        if node in matrix.actors:
            return f"actor:{node}"
        return f"snk:{node}" if is_target else f"src:{node}"

    ribbons = [
        (
            node_key(flow.source, False),
            node_key(flow.target, True),
            flow.amount_in_euro,
            _category_color(flow.category),
        )
        for flow in sorted(matrix.flows, key=lambda item: -item.amount_in_euro)
    ]
    nets = matrix.net_by_actor()
    labels = {f"actor:{actor}": f"{actor}\nnet {nets[actor]:,.0f} EUR" for actor in matrix.actors}
    labels.update({f"src:{node}": node for node in matrix.sources})
    labels.update({f"snk:{node}": node for node in matrix.sinks})
    with _figure(
        height=max(3.6, 0.5 * (len(matrix.actors) + len(matrix.sinks)) + 2.4)
    ) as (figure, axes):
        axis = axes[0]
        _draw_sankey(
            axis,
            # One column per party, in the order the view's topological sort puts them, so a
            # transfer between two parties is an ordinary left-to-right ribbon.
            [[f"src:{node}" for node in matrix.sources]]
            + [[f"actor:{actor}" for actor in column] for column in matrix.actor_columns()]
            + [[f"snk:{node}" for node in matrix.sinks]],
            ribbons,
            labels,
            # The face-closing stub carries the view's own net, in its sign convention.
            stub_labels={f"actor:{actor}": f"net {net:,.0f} EUR" for actor, net in nets.items()},
        )
        band = matrix.total_band
        axis.set_title(
            f"Who pays whom over {result.parameters.observation_period_in_years} years — "
            f"{result.perspective_id} (best estimate; lifetime total "
            f"{band.best_estimate:,.0f} [{band.minimum:,.0f} | {band.maximum:,.0f}] EUR nominal)",
            fontsize=10, color=_Palette.INK, loc="left",
        )
        figure.text(
            0.01, 0.015,
            f"Ribbons are nominal lifetime euros. {matrix.folded_ribbon_count} small ribbon(s) "
            f"carrying {matrix.folded_amount_in_euro:,.0f} EUR folded into 'other' per node pair.",
            fontsize=7, color=_Palette.MUTED,
        )
        figure.tight_layout(rect=(0, 0.04, 1, 1))
        figure.savefig(path, facecolor=_Palette.SURFACE)
    return path


# ---------------------------------------------------------------------------- liquidity fan

def plot_liquidity_fan(
    result: LifecycleCostResult,
    path: str,
    comparison: Optional[VariantComparison] = None,
    skips: Optional[List[SkippedPlot]] = None,
) -> Optional[str]:
    """Draw the cumulative cash position over time as a fan, nominal above and discounted below.

    The upper panel is the cumulative nominal cost (cost positive, upward), annotated with the deepest out-of-pocket
    position. The lower panel is the cumulative discounted cost ending at the NPV, or with a comparison the cumulative
    discounted savings, whose zero crossings give the payback interval. Each panel draws the best estimate as a line
    and the low/high envelope as a fill, over the nominal series' year range.

    Args:
        result: The perspective whose position is drawn.
        path: Destination PNG path.
        comparison: Optional comparison computed for this perspective; turns the lower panel into the payback fan.
        skips: Collector for the skip record, if the caller is collecting.

    Returns:
        `path` when the chart was written, None when it had nothing to draw.

    Raises:
        views.CostDataError: If the comparison's savings curves do not span the same years as this result's own
            cumulative series.
    """
    nominal = views.cumulative_nominal_cost_series(result)
    years = list(range(len(nominal[Slot.BEST_ESTIMATE])))
    if not any(any(nominal[slot]) for slot in (Slot.LOW, Slot.BEST_ESTIMATE, Slot.HIGH)):
        _skip(
            skips, "liquidity fan", result.perspective_id,
            "the cumulative cash position is zero in every year of every world, so both panels "
            "would be a flat line on the axis.",
        )
        return None
    hue = PresentationStyle.GROUP_COLORS_LIGHT[0]
    with _figure(height=6.0, panels=(2, 1), share_x=True) as (figure, axes):
        axes[0].fill_between(years, nominal[Slot.LOW], nominal[Slot.HIGH], color=hue, alpha=0.18,
                             linewidth=0, label="min/max envelope")
        axes[0].plot(years, nominal[Slot.BEST_ESTIMATE], color=hue, linewidth=2.2, label="best estimate")
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
            low, best_estimate, high = curves["low"], curves["best_estimate"], curves["high"]
            if len(best_estimate) != len(years):
                raise views.CostDataError(
                    f"The cash curve of perspective {result.perspective_id!r} spans "
                    f"{len(years)} years but the comparison it was handed carries "
                    f"{len(best_estimate)}: the two panels share one year axis, so the savings "
                    "would be drawn against years they were not computed for. Compare two "
                    "evaluations of the same observation period."
                )
            lower_label = "cumulative discounted savings [EUR]"
            envelope = PaybackEnvelope.of(views.band_zero_crossings(curves))
            note = payback_interval_sentence(envelope.earliest, envelope.central, envelope.latest)
        else:
            discounted = views.cumulative_discounted_cost_series(result)
            low = discounted[Slot.LOW]
            best_estimate = discounted[Slot.BEST_ESTIMATE]
            high = discounted[Slot.HIGH]
            lower_label = "cumulative discounted cost [EUR]"
            note = f"end point = NPV {result.total_npv_in_euro.best_estimate:,.0f} EUR"
        axes[1].fill_between(years, low, high, color=hue, alpha=0.18, linewidth=0)
        axes[1].plot(years, best_estimate, color=hue, linewidth=2.2)
        axes[1].axhline(0, color=_Palette.MUTED, linewidth=0.8)
        axes[1].set_ylabel(lower_label, color=_Palette.MUTED, fontsize=9)
        axes[1].set_xlabel("year", color=_Palette.MUTED, fontsize=9)
        axes[1].set_xticks(list(range(0, len(years), max(1, len(years) // 10))))
        axes[1].set_title(
            textwrap.fill(note, width=110), fontsize=8, color=_Palette.INK, loc="left"
        )
        figure.tight_layout()
        figure.savefig(path, facecolor=_Palette.SURFACE)
    return path


# ---------------------------------------------------------------------------- comparison bridge

def plot_comparison_bridge(
    reference: LifecycleCostResult, variant: LifecycleCostResult, path: str
) -> str:
    """Draw why the variant's NPV differs from the reference's, as a bridge waterfall.

    An anchor bar for the reference, one floating bar per display group, an anchor bar for the variant. Only the
    anchors carry a min/max whisker, since the band of a difference is not the difference of the bands. Deltas are
    coloured by display group, not red/green, because whether a cost increase is bad depends on the payer. The title
    names the basis via `_comparison_basis`.

    Args:
        reference: The base result.
        variant: The variant result, normally the same perspective.
        path: Destination PNG path.

    Returns:
        `path`, unchanged.
    """
    steps = views.comparison_bridge(reference, variant, PresentationStyle.CATEGORY_TO_GROUP)
    labels = ["reference"] + [group_name(step.group) for step in steps] + ["variant"]
    base = reference.total_npv_in_euro.best_estimate
    with _figure(height=max(3.2, 0.45 * len(labels) + 2.0)) as (figure, axes):
        axis = axes[0]
        positions = list(range(len(labels)))
        axis.bar([0], [base], color=_Palette.INK, width=0.7)
        cursor = base
        for index, step in enumerate(steps, start=1):
            bottom = min(cursor, cursor + step.delta_in_euro)
            axis.bar([index], [abs(step.delta_in_euro)], bottom=bottom, width=0.7,
                     color=PresentationStyle.GROUP_COLORS_LIGHT[step.group])
            # The connector a waterfall is read along: a horizontal step at the running total,
            # from the right edge of the previous bar to the left edge of this one.
            axis.plot([index - 1 + 0.35, index - 0.35], [cursor, cursor],
                      color=_Palette.GRID, linewidth=0.8)
            axis.text(index, bottom + abs(step.delta_in_euro) + abs(base) * 0.01,
                      f"{step.delta_in_euro:+,.0f}", ha="center", fontsize=7, color=_Palette.MUTED)
            cursor += step.delta_in_euro
        axis.plot([len(labels) - 2 + 0.35, len(labels) - 1 - 0.35], [cursor, cursor],
                  color=_Palette.GRID, linewidth=0.8)
        axis.bar([len(labels) - 1], [cursor], color=_Palette.INK, width=0.7)
        for position, band in ((0, reference.total_npv_in_euro), (len(labels) - 1, variant.total_npv_in_euro)):
            axis.errorbar([position], [band.best_estimate],
                          yerr=[[band.best_estimate - band.minimum], [band.maximum - band.best_estimate]],
                          fmt="none", ecolor=_Palette.MUTED, elinewidth=1.2, capsize=3)
        axis.axhline(0, color=_Palette.MUTED, linewidth=0.8)
        axis.set_xticks(positions, labels, fontsize=7.5, rotation=35, ha="right", color=_Palette.INK)
        axis.set_ylabel("NPV [EUR], discounted", color=_Palette.MUTED, fontsize=9)
        axis.set_title(
            f"NPV bridge — {_comparison_basis(reference, variant)} basis (best estimate; delta "
            f"{variant.total_npv_in_euro.best_estimate - base:+,.0f} EUR)",
            fontsize=10, color=_Palette.INK, loc="left",
        )
        figure.tight_layout()
        figure.savefig(path, facecolor=_Palette.SURFACE)
    return path


# ---------------------------------------------------------------------------- audit heatmap

class _HeatmapStyle:
    """Colour scale, annotation limit and caption layout of the year × category ledger heatmap.

    `LINEAR_THRESHOLD` is the euro amount below which the symlog scale is linear, so the year-0 investment does not
    flatten the operating years. `MAX_ANNOTATED_CELLS` is the largest matrix that still gets per-cell numbers.
    `CAPTION_WRAP_WIDTH` fills the 9-inch figure at `CAPTION_FONT_SIZE`, and `CAPTION_LINE_HEIGHT_IN_INCHES` is how
    much the figure grows per caption line.
    """

    LINEAR_THRESHOLD = 100.0
    MAX_ANNOTATED_CELLS = 240
    COLORMAP = "RdBu_r"
    CAPTION_FONT_SIZE = 7
    CAPTION_WRAP_WIDTH = 145
    CAPTION_LINE_HEIGHT_IN_INCHES = 0.135
    CAPTION_PADDING_IN_INCHES = 0.14

    @classmethod
    def annotates(cls, cell_count: int) -> bool:
        """Return whether a matrix of this size gets its per-cell euros printed.

        The drawing and the caption both use this, so they agree.
        """
        return cell_count <= cls.MAX_ANNOTATED_CELLS


def _heatmap_caption_lines(dropped: int, cell_count: int) -> List[str]:
    """Return the heatmap caption wrapped to the figure width: the authored text, then this run's facts.

    The authored "shows" paragraph of the ledger heatmap comes first, so the PNG reads on its own. Then the
    run-specific sentences: which reconciliations hold, how many cost categories carried no flow, and whether per-cell
    euros were printed.

    Args:
        dropped: How many `CostCategory` members are absent from the matrix.
        cell_count: Rows times columns of the drawn matrix.

    Returns:
        One string per rendered line; the caller sizes the figure from their count.
    """
    prose = ReportProse.for_section(ReportProse.LEDGER_HEATMAP_SECTION_NAME)
    paragraphs = [
        ReportProse.to_plain_text(prose.shows),
        "Column sums equal the nominal annual series, row sums the per-category totals of "
        f"cost_audit.csv. {dropped} categor(ies) carried no flows and are not shown.",
    ]
    if not _HeatmapStyle.annotates(cell_count):
        paragraphs.append(
            f"Per-cell euros omitted ({cell_count} cells); read the amounts off the colour bar."
        )
    return [
        line
        for paragraph in paragraphs
        for line in textwrap.wrap(paragraph, width=_HeatmapStyle.CAPTION_WRAP_WIDTH)
    ]


def _symlog_norm(extent: float) -> SymLogNorm:
    """Return the heatmap's colour normalization: diverging around zero, linear near it, logarithmic beyond.

    A separate function because matplotlib generates `SymLogNorm.__init__` at import time and pylint cannot see its
    keywords; this keeps that exemption in one place.

    Args:
        extent: The largest absolute euro amount in the matrix; the scale runs from its negative to it.

    Returns:
        The norm to hand to `imshow`.
    """
    # pylint: disable=unexpected-keyword-arg
    return SymLogNorm(
        linthresh=_HeatmapStyle.LINEAR_THRESHOLD, vmin=-extent, vmax=extent, base=10
    )


def plot_timeline_heatmap(
    result: LifecycleCostResult, path: str, skips: Optional[List[SkippedPlot]] = None
) -> Optional[str]:
    """Draw the whole ledger as a year × category matrix, the visual twin of the audit table.

    Every cost category with a flow, unfolded, against every year, in nominal euros of the best-estimate slot. The
    colour scale is diverging around zero and symlog. Rows are ordered by display group, then category order. Column
    sums equal `annual_cost_series_nominal_in_euro` and row sums the audit table's per-category totals; the caption
    states both under the authored "shows" paragraph, and the figure grows to fit it. Written by `write_audit_plots`,
    because `audit.py` may not import a renderer.

    Args:
        result: The perspective to audit.
        path: Destination PNG path.
        skips: Collector for the skip record, if the caller is collecting.

    Returns:
        `path` when the chart was written, None when it had nothing to draw.
    """
    matrix = views.nominal_annual_matrix_by_category(result)
    ordered = [
        category
        for _name, categories in PresentationStyle.DISPLAY_GROUPS
        for category in categories
    ]
    ordered += [category for category in CostCategory if category not in ordered]
    present = [category for category in ordered if any(row.get(category) for row in matrix)]
    dropped = sum(1 for category in CostCategory if category not in present)
    if not present:
        _skip(
            skips, "ledger heatmap", result.perspective_id,
            "the scoped timeline carries no flows at all, so the matrix has no row to draw.",
        )
        return None
    values = [[row.get(category, 0.0) for row in matrix] for category in present]
    extent = max(abs(value) for row in values for value in row) or 1.0
    cell_count = len(present) * len(matrix)
    caption_lines = _heatmap_caption_lines(dropped, cell_count)
    caption_height = (
        len(caption_lines) * _HeatmapStyle.CAPTION_LINE_HEIGHT_IN_INCHES
        + _HeatmapStyle.CAPTION_PADDING_IN_INCHES
    )
    chart_height = max(2.6, 0.28 * len(present) + 2.0)
    with _figure(height=chart_height + caption_height) as (figure, axes):
        axis = axes[0]
        axis.yaxis.grid(False)
        image = axis.imshow(
            values, aspect="auto", cmap=_HeatmapStyle.COLORMAP, norm=_symlog_norm(extent)
        )
        ticks = list(range(0, len(matrix), max(1, len(matrix) // 12)))
        axis.set_xticks(ticks, [str(year) for year in ticks], fontsize=7, color=_Palette.INK)
        axis.set_yticks(range(len(present)), [category.value for category in present], fontsize=7,
                        color=_Palette.INK)
        if _HeatmapStyle.annotates(cell_count):
            for row_index, row in enumerate(values):
                for column_index, value in enumerate(row):
                    if value:
                        axis.text(column_index, row_index, f"{value:,.0f}", ha="center", va="center",
                                  fontsize=5.5, color=_Palette.INK)
        figure.colorbar(image, ax=axis, shrink=0.85, label="nominal EUR (symlog)")
        axis.set_xlabel("year", color=_Palette.MUTED, fontsize=9)
        axis.set_title(
            f"Ledger heatmap — {result.perspective_id} (nominal, best estimate)",
            fontsize=10, color=_Palette.INK, loc="left",
        )
        figure.text(
            0.01, _HeatmapStyle.CAPTION_PADDING_IN_INCHES / 2 / (chart_height + caption_height),
            "\n".join(caption_lines),
            fontsize=_HeatmapStyle.CAPTION_FONT_SIZE, color=_Palette.MUTED, va="bottom",
        )
        figure.tight_layout(rect=(0, caption_height / (chart_height + caption_height), 1, 1))
        figure.savefig(path, facecolor=_Palette.SURFACE)
    return path


#: The audit heatmap's file name, which is the audit's own output rather than the report's and is
#: therefore not suffixed with a perspective: it is written for the result the audit was built for.
AUDIT_HEATMAP_FILE_NAME = "cost_audit_timeline_heatmap.png"


def write_audit_plots(result: LifecycleCostResult, result_directory: str) -> PlotsWritten:
    """Write the audit-side heatmap next to `cost_audit.csv`.

    Called by `bridge.compute_lifecycle_costs` and the `evaluate` CLI command, the two writers of `cost_audit.csv`. A
    rendering failure becomes a skip (with the exception's type and message as reason) rather than a run failure,
    because a failed run removes its exports and the CSV must not be lost over a picture. A half-written PNG is
    removed.

    Args:
        result: The perspective the audit was built for (the run's first).
        result_directory: Directory holding the audit CSVs; the PNG lands beside them.

    Returns:
        The paths that exist on disk afterwards and the record of anything not drawn.
    """
    written = PlotsWritten()
    path = os.path.join(result_directory, AUDIT_HEATMAP_FILE_NAME)
    try:
        drawn = plot_timeline_heatmap(result, path, written.skipped)
    except Exception as error:  # pylint: disable=broad-except
        _remove_if_present(path)
        written.skipped.append(
            SkippedPlot(
                chart="ledger heatmap",
                perspective_id=result.perspective_id,
                reason=(
                    f"the renderer failed with {type(error).__name__}: {error}. The audit tables "
                    "beside it are unaffected."
                ),
            )
        )
        return written
    if drawn is not None and os.path.isfile(drawn):
        written.paths.append(drawn)
    return written


def _remove_if_present(path: str) -> None:
    """Delete a half-written figure, silently if there is none.

    Removal errors are swallowed, since this runs while another failure is being reported.
    """
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


# ---------------------------------------------------------------------------- cost treemap

class _TreemapLabels:
    """Font limits for deciding whether a treemap tile can carry its label.

    The label is measured against the tile, the font shrinks until it fits, and below `MIN_FONT_SIZE` the tile stays
    unlabelled.
    """

    #: Starting font size, in points; the same size the other charts' in-plot labels use.
    FONT_SIZE = 6.5
    #: Below this the text is unreadable anyway, so the tile is left blank instead.
    MIN_FONT_SIZE = 4.5
    #: Height of one text line as a multiple of the font size, leading included.
    LINE_HEIGHT_RATIO = 1.35
    #: Fraction of the tile the text may occupy before it is considered not to fit.
    FILL_LIMIT = 0.92
    #: Share of its half of the figure a panel's axes occupy once the margins are taken; it
    #: converts the figure size into the axes extent without forcing an early draw.
    PANEL_WIDTH_SHARE = 0.92
    #: Share of the figure height left for the axes after the suptitle and the caption band.
    PANEL_HEIGHT_SHARE = 0.72


def _fitting_font_size(
    lines: List[str], width: float, height: float, axis_width_in_points: float, axis_height_in_points: float
) -> Optional[float]:
    """Return the largest font size at which a label fits inside its tile, or None if none does.

    The tile's size in axes fractions is converted to points with the axes' own extent. None means even
    `_TreemapLabels.MIN_FONT_SIZE` overflows and the tile stays unlabelled.
    """
    available_width = width * axis_width_in_points * _TreemapLabels.FILL_LIMIT
    available_height = height * axis_height_in_points * _TreemapLabels.FILL_LIMIT
    longest = max(len(line) for line in lines) if lines else 0
    if not longest:
        return None
    by_width = available_width / (longest * _Typography.CHAR_WIDTH_RATIO)
    by_height = available_height / (len(lines) * _TreemapLabels.LINE_HEIGHT_RATIO)
    size = min(_TreemapLabels.FONT_SIZE, by_width, by_height)
    return size if size >= _TreemapLabels.MIN_FONT_SIZE else None


def plot_cost_treemap(
    result: LifecycleCostResult, path: str, skips: Optional[List[SkippedPlot]] = None
) -> Optional[str]:
    """Draw the lifetime cost composition as treemaps, gross and net of credits side by side.

    A treemap cannot show credits, so both are drawn. Gross: tile area is the positive NPV per (display group,
    subject); the caption states the excluded credit total. Net: each subject's credits are applied to its own cost
    tiles, and subjects whose credits exceed their costs are clamped to zero and named in the caption. Both captions
    are `report_prose.treemap_disclosure`, as in the HTML report. Labels shrink to fit their tile and vanish below
    `_TreemapLabels.MIN_FONT_SIZE`. A perspective with no positive tile on either basis is skipped.

    Args:
        result: The perspective whose cost structure is drawn.
        path: Destination PNG path.
        skips: Collector for the skip record, if the caller is collecting.

    Returns:
        `path` when the chart was written, None when it had nothing to draw.
    """
    bases = [
        (basis, views.cost_structure_tiles(result, PresentationStyle.CATEGORY_TO_GROUP, basis))
        for basis in (views.TileBasis.GROSS, views.TileBasis.NET_OF_CREDITS)
    ]
    if not any(tile.area_in_euro > 0 for _basis, tiles in bases for tile in tiles.tiles):
        _skip(
            skips, "cost treemap", result.perspective_id,
            "neither the gross nor the net basis has a tile with a positive area, and a treemap "
            "has no negative tile to draw the credits with.",
        )
        return None
    figure_width_in_inches, figure_height_in_inches = 10.0, 4.6
    panel_width_in_points = (
        figure_width_in_inches / 2 * _TreemapLabels.PANEL_WIDTH_SHARE * _Typography.POINTS_PER_INCH
    )
    panel_height_in_points = (
        figure_height_in_inches * _TreemapLabels.PANEL_HEIGHT_SHARE * _Typography.POINTS_PER_INCH
    )
    captions: List[str] = []
    with _figure(
        width=figure_width_in_inches, height=figure_height_in_inches, panels=(1, 2)
    ) as (figure, axes):
        for axis, (basis, tiles), headline in zip(
            axes, bases, ("gross cost", "net of credits")
        ):
            drawable = [tile for tile in tiles.tiles if tile.area_in_euro > 0]
            drawable.sort(key=lambda tile: (tile.group, -tile.area_in_euro))
            axis.set_xlim(0, 1)
            axis.set_ylim(0, 1)
            axis.axis("off")
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
                    lines = lines[1:]
                    font_size = _fitting_font_size(
                        lines, width, height, panel_width_in_points, panel_height_in_points
                    )
                if font_size is not None:
                    axis.text(x + width / 2, y + height / 2, "\n".join(lines), ha="center", va="center",
                              fontsize=font_size, color=_Palette.SURFACE)
            total = sum(tile.area_in_euro for tile in drawable)
            axis.set_title(
                f"{headline}: {total:,.0f} EUR (net NPV {tiles.net_npv_in_euro:,.0f} EUR)",
                fontsize=9, color=_Palette.INK, loc="left",
            )
            captions.append(treemap_disclosure(tiles, basis))
        figure.suptitle(
            f"Cost structure — {result.perspective_id} (NPV, best estimate)",
            fontsize=10, color=_Palette.INK, x=0.01, ha="left",
        )
        figure.text(0.01, 0.015, "  ".join(captions), fontsize=6.5, color=_Palette.MUTED, wrap=True)
        figure.tight_layout(rect=(0, 0.07, 1, 0.95))
        figure.savefig(path, facecolor=_Palette.SURFACE)
    return path


# ---------------------------------------------------------------------------- lifecycle swimlane

class _SwimlaneStyle:
    """Row geometry of the lifecycle swimlane.

    The figure height scales with the lane count so rows stay tall enough for labels; `SPAN_HEIGHT` is the share of a
    row a span bar fills.
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
    #: Distance from a lane's centre line down to its first label row, in data units.
    FIRST_LABEL_OFFSET = 0.28
    #: Vertical distance between two stacked label rows, in data units.
    LABEL_ROW_PITCH = 0.19
    #: Share of the figure width the axes occupy once the y tick labels and margins are taken.
    AXES_WIDTH_SHARE = 0.78


def _draw_lane_events(
    axis,
    position: float,
    events: Sequence[Tuple[int, str, Optional[float]]],
    color: str,
    horizon: int,
    years_per_character: float,
) -> None:
    """Draw one swimlane lane's event markers and stack their labels so none overprints another.

    Each label claims its estimated text width and is pushed down a level until it fits; past
    `_SwimlaneStyle.MAX_LABELS_PER_CLUSTER` levels the rest is summarized as "+N more". Labels that would run off the
    right edge are flipped left of their marker.

    Args:
        axis: The swimlane axes.
        position: The lane's y coordinate.
        events: `(year, label, amount)` triples, in year order.
        color: The lane's marker colour.
        horizon: The observation horizon, where the drawable area ends.
        years_per_character: Width of one label character in year units.
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
            # A wide label on an early event runs off the *left* edge once it is flipped — the
            # year-0 investment is exactly that case — so it is clamped to the frame the way the
            # right edge is, rather than being drawn into the margin and cropped away.
            start = max(start, -_SwimlaneStyle.RIGHT_MARGIN_IN_YEARS)
        level = 0
        while level < _SwimlaneStyle.MAX_LABELS_PER_CLUSTER and occupied.get(level, start) > start:
            level += 1
        if level >= _SwimlaneStyle.MAX_LABELS_PER_CLUSTER:
            hidden += 1
            last_hidden_year = year
            continue
        occupied[level] = start + width
        axis.text(
            start if alignment == "left" else start + width,
            position - _SwimlaneStyle.FIRST_LABEL_OFFSET - _SwimlaneStyle.LABEL_ROW_PITCH * level,
            text, fontsize=_SwimlaneStyle.EVENT_LABEL_SIZE, color=_Palette.INK, ha=alignment,
        )
    if hidden and last_hidden_year is not None:
        axis.text(
            last_hidden_year,
            position
            - _SwimlaneStyle.FIRST_LABEL_OFFSET
            - _SwimlaneStyle.LABEL_ROW_PITCH * _SwimlaneStyle.MAX_LABELS_PER_CLUSTER,
            f"+{hidden} more", fontsize=_SwimlaneStyle.EVENT_LABEL_SIZE, color=_Palette.MUTED,
            ha="right",
        )


def plot_lifecycle_swimlane(
    result: LifecycleCostResult,
    path: str,
    comparison: Optional[VariantComparison] = None,
    skips: Optional[List[SkippedPlot]] = None,
) -> str:
    """Draw the life of the renovation on one page: assets, financing, support and milestones.

    Every lane restates a figure shown in full elsewhere (asset events, amortization, payback crossings). The payback
    milestone is a range bar between the band's zero crossings and appears only with a comparison. An empty lane is
    dropped and recorded as a skip.

    Args:
        result: The perspective to summarize.
        path: Destination PNG path.
        comparison: Optional comparison computed for this perspective; gives the payback range.
        skips: Collector for the dropped lanes, if the caller is collecting.

    Returns:
        `path`, unchanged; the milestone lane always exists, so the chart is never skipped.
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
            _skip(
                skips, f"lifecycle swimlane, {lane.name} lane", result.perspective_id,
                "the lane carries no spans and no events, so it would be drawn as an empty row.",
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
            (event.year, event.kind.value, event.amount_in_euro) for event in asset.events
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
    # One label character, in year units: the drawn year range divided by the axes' width in
    # points. Labels claim that much space so a wide one pushes the next one down a row.
    axes_width_in_points = (
        figure_width_in_inches * _SwimlaneStyle.AXES_WIDTH_SHARE * _Typography.POINTS_PER_INCH
    )
    years_per_character = (
        (lanes.horizon + 2 * _SwimlaneStyle.RIGHT_MARGIN_IN_YEARS)
        * _SwimlaneStyle.EVENT_LABEL_SIZE
        * _Typography.CHAR_WIDTH_RATIO
        / axes_width_in_points
    )
    with _figure(
        width=figure_width_in_inches,
        height=max(3.0, _SwimlaneStyle.ROW_HEIGHT_IN_INCHES * len(rows) + 2.0),
    ) as (figure, axes):
        axis = axes[0]
        axis.yaxis.grid(False)
        axis.xaxis.grid(True, color=_Palette.GRID, linewidth=0.6)
        for index, (_name, spans, events, color) in enumerate(rows):
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
        # The lanes are markers and bars on a small number of y positions, so a one-lane chart —
        # an operating-only perspective has exactly that — leaves matplotlib autoscaling a
        # degenerate y range and giving up on the layout with a warning on every run. The extent
        # is known here: one row pitch of air around the outermost lanes, which is also what the
        # stacked event labels below a lane need.
        axis.set_ylim(
            -_SwimlaneStyle.ROW_PITCH * 0.7,
            (len(rows) - 1) * _SwimlaneStyle.ROW_PITCH + _SwimlaneStyle.ROW_PITCH * 0.7,
        )
        axis.set_xticks(range(0, lanes.horizon + 1, 5))
        axis.set_xlabel("year", color=_Palette.MUTED, fontsize=9)
        axis.set_title(
            f"At a glance — {result.perspective_id} (best estimate)",
            fontsize=10, color=_Palette.INK, loc="left",
        )
        figure.tight_layout()
        figure.savefig(path, facecolor=_Palette.SURFACE)
    return path


# ---------------------------------------------------------------------------- sources & uses

def plot_sources_and_uses(
    result: LifecycleCostResult, path: str, skips: Optional[List[SkippedPlot]] = None
) -> Optional[str]:
    """Draw how year 0 is funded and what it buys, as a two-column Sankey (sources and uses).

    Each subsidy scheme is its own node (e.g. "state -> KfW 261 -> heat pump"), with the loan disbursement and own
    capital as the balancing item, against the gross year-0 uses. Both columns balance to the euro, and the caption
    says so. A pure own-capital purchase is skipped.

    Args:
        result: The perspective whose year 0 is drawn.
        path: Destination PNG path.
        skips: Collector for the skip record, if the caller is collecting.

    Returns:
        `path` when the chart was written, None when it had nothing to draw.
    """
    statement = views.funding_sources_and_uses(result)
    if not statement.has_external_funding():
        _skip(
            skips, "sources-and-uses Sankey", result.perspective_id,
            "year 0 is funded entirely from own capital, which the investment waterfall shows "
            "better.",
        )
        return None
    color_by_source = {node.label: _category_color(node.category) for node in statement.sources}
    ribbons = [
        (f"src:{source}", f"use:{use}", amount, color_by_source[source])
        for source, use, amount in statement.ribbons()
        if amount > 0
    ]
    labels = {f"src:{node.label}": f"{node.label}\n{node.amount_in_euro:,.0f} EUR"
              for node in statement.sources}
    labels.update({f"use:{node.label}": f"{node.label}\n{node.amount_in_euro:,.0f} EUR"
                   for node in statement.uses})
    with _figure(
        height=max(3.2, 0.42 * (len(statement.sources) + len(statement.uses)) + 2.0)
    ) as (figure, axes):
        axis = axes[0]
        _draw_sankey(
            axis,
            [[f"src:{node.label}" for node in statement.sources],
             [f"use:{node.label}" for node in statement.uses]],
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
    return path


# ---------------------------------------------------------------------------- bank benchmark

def plot_wealth_benchmark(
    reference: LifecycleCostResult, variant: LifecycleCostResult, path: str
) -> str:
    """Draw the bank benchmark: renovating against keeping the money in the bank at 1 % to 10 % interest.

    Panel A shows the wealth advantage of renovating over time, one line per rate, with the evaluation's own discount
    rate highlighted and banded. Panel B shows the terminal advantage against the rate; its zero crossing is the
    break-even rate. Interest is nominal and pre-tax, as the caption says, because capital-income taxation is
    country-specific. The title names the basis via `_comparison_basis`.

    Args:
        reference: The do-nothing baseline.
        variant: The renovation variant.
        path: Destination PNG path.

    Returns:
        `path`, unchanged.
    """
    benchmark = views.wealth_benchmark(reference, variant)
    # The same ten ordered steps as the HTML section's fan, from `SequentialRamp.LIGHT` (a baked
    # PNG has no theme), so both renderings colour the same rate the same way.
    if len(benchmark.rates) > len(SequentialRamp.LIGHT):
        raise views.CostDataError(
            f"The benchmark carries {len(benchmark.rates)} rates but the sequential ramp declares "
            f"{len(SequentialRamp.LIGHT)} steps, so the last lines of the fan would have no colour "
            "of their own. Extend `presentation_style.SequentialRamp` alongside "
            "`views.WealthBenchmarkGrid.RATES`."
        )
    with _figure(width=10.0, height=4.2, panels=(1, 2)) as (figure, axes):
        for index, rate in enumerate(benchmark.rates):
            series = benchmark.series_by_rate[rate]
            axes[0].plot(range(len(series)), series, linewidth=1.0,
                         color=SequentialRamp.LIGHT[index], label=f"{rate:.0%}")
        parameter = benchmark.parameter_series_by_slot
        axes[0].fill_between(
            range(len(parameter[Slot.BEST_ESTIMATE])), parameter[Slot.LOW], parameter[Slot.HIGH],
            color=PresentationStyle.GROUP_COLORS_LIGHT[0], alpha=0.18, linewidth=0,
        )
        axes[0].plot(range(len(parameter[Slot.BEST_ESTIMATE])), parameter[Slot.BEST_ESTIMATE], linewidth=2.4,
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
            f"Bank benchmark — {_comparison_basis(reference, variant)} basis "
            "(renovate, or bank the money? best estimate)",
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
    return path


# ---------------------------------------------------------------------------- monthly burden

def plot_monthly_burden(
    result: LifecycleCostResult, path: str, skips: Optional[List[SkippedPlot]] = None
) -> Optional[str]:
    """Draw what this costs per month, year by year, stacked by display group.

    Recurring cost only: debt service, energy, maintenance, taxes and levies, minus recurring credits. All capital
    events are excluded, the year-0 investment and the replacements alike; the replacements return as a dashed reserve
    line (their equivalent annual cost over twelve months). Only the monthly total carries a whisker. A perspective
    with no recurring cost is skipped.

    Args:
        result: The perspective whose burden is drawn.
        path: Destination PNG path.
        skips: Collector for the skip record, if the caller is collecting.

    Returns:
        `path` when the chart was written, None when it had nothing to draw.
    """
    burden = views.monthly_burden_series(result)
    totals = burden.series
    per_group = views.monthly_burden_by_group(result, PresentationStyle.CATEGORY_TO_GROUP)
    years = list(range(len(totals)))
    reserve = burden.replacement_reserve_per_month
    if not any(value.best_estimate for value in totals) and not reserve:
        _skip(
            skips, "monthly burden", result.perspective_id,
            "every month of every year carries a zero recurring burden and there is no "
            "replacement reserve, so the chart would be an empty axis.",
        )
        return None
    with _figure(height=4.0) as (figure, axes):
        axis = axes[0]
        _stack_positive_negative(
            axis,
            years,
            {
                index: [row.get(index, 0.0) for row in per_group]
                for index in range(len(PresentationStyle.DISPLAY_GROUPS))
            },
            edge_width=0.5,
        )
        axis.errorbar(
            years, [value.best_estimate for value in totals],
            yerr=[[value.best_estimate - value.minimum for value in totals],
                  [value.maximum - value.best_estimate for value in totals]],
            fmt="none", ecolor=_Palette.INK, elinewidth=0.9, capsize=2,
        )
        if reserve:
            # One dashed segment per bar rather than one line across the chart: the reserve is a
            # constant, but what the reader budgets is the bar *plus* the reserve, which is not.
            for year, band in zip(years, totals):
                axis.hlines(
                    band.best_estimate + reserve, year - 0.41, year + 0.41, colors=_Palette.INK,
                    linestyles="dashed", linewidth=1.2,
                    # One legend entry for the whole dashed line: matplotlib's own convention for
                    # "drawn but not listed" is a label starting with an underscore.
                    label="with replacement reserve" if year == 0 else "_reserve segment",
                )
        axis.axhline(0, color=_Palette.MUTED, linewidth=0.8)
        axis.set_xlabel("year", color=_Palette.MUTED, fontsize=9)
        axis.set_ylabel("EUR per month (nominal)", color=_Palette.MUTED, fontsize=9)
        axis.set_title(
            f"Monthly burden — {result.perspective_id} (recurring cost, best estimate with "
            "min/max whiskers)",
            fontsize=10, color=_Palette.INK, loc="left",
        )
        axis.legend(fontsize=7, frameon=False, ncol=3, labelcolor=_Palette.INK)
        # The reserve sentence is printed only when the dashed line is drawn, so the caption
        # never promises a mark that is not there.
        notes = [
            "Excludes every capital event — the year-0 investment and its financing, and the "
            "replacement years; the funding statement and the cash-flow timeline show those."
        ]
        if reserve:
            notes.append(
                f"The dashed line adds the replacement reserve of {reserve:,.0f} EUR/month, the "
                "equivalent annual cost of the replacement flows spread over twelve months."
            )
        for index, note in enumerate(reversed(notes)):
            figure.text(0.01, 0.015 + 0.03 * index, note, fontsize=7, color=_Palette.MUTED)
        figure.tight_layout(rect=(0, 0.08, 1, 1))
        figure.savefig(path, facecolor=_Palette.SURFACE)
    return path


#: File-name stem of every chart in the report set, keyed by nothing but read in this order. The
#: names are what a reader finds in the result directory, so they are declared here rather than
#: spelled at each call — a chart renamed in one place and not the other is a file nobody finds.
_REPORT_FILE_PREFIX = "lifecycle_"

#: The one chart of the set that is drawn from the whole matrix and therefore carries no
#: perspective in its name.
PERSPECTIVE_COSTS_FILE_NAME = f"{_REPORT_FILE_PREFIX}perspective_costs.png"

#: The sidecar the engine-side callers write when a run skipped at least one chart. Named here
#: because the file belongs to this set even though this module never writes it: the decision of
#: what a run says out loud is the caller's (see `bridge.compute_lifecycle_costs`).
SKIPPED_PLOTS_FILE_NAME = "lifecycle_plots_not_drawn.txt"


def report_plot_file_name(chart: str, perspective_id: str) -> str:
    """Return `lifecycle_<chart>_<perspective_id>.png`, the file name of a per-perspective chart.

    Args:
        chart: The chart's stem, e.g. `"cash_flow"`.
        perspective_id: The perspective the chart was drawn for.

    Returns:
        The file name, without a directory.
    """
    return f"{_REPORT_FILE_PREFIX}{chart}_{perspective_id}.png"


#: The charts drawn once per perspective, as (file-name stem, renderer). A table rather than a
#: sequence of calls: every one of them takes the same three arguments and differs only in its
#: name, and the two that do not — the swimlane and the fan, which also take the comparison — are
#: called below where that difference is visible.
_PER_PERSPECTIVE_CHARTS: Sequence[Tuple[str, Callable[..., Optional[str]]]] = (
    ("annual_cash_flows", plot_annual_cash_flows),
    ("investment_waterfall", plot_investment_waterfall),
    ("component_costs", plot_component_costs),
    # The pasteable subset of the visualization set per perspective; the bridge and the
    # benchmark need the reference and are in the table below.
    ("actor_flows", plot_actor_flows),
    ("sources_and_uses", plot_sources_and_uses),
    ("cost_treemap", plot_cost_treemap),
    ("monthly_burden", plot_monthly_burden),
)

#: The charts that decompose a difference, drawn for the compared perspective only. They take the
#: reference and the variant rather than one result, which is exactly why they are a second table.
_COMPARISON_RENDERERS: Sequence[Tuple[str, Callable[..., Optional[str]]]] = (
    ("payback_curve", plot_payback_curve),
    ("comparison_bridge", plot_comparison_bridge),
    ("wealth_benchmark", plot_wealth_benchmark),
)


def write_report_plots(
    matrix: EvaluationMatrix,
    result_directory: str,
    reference_result: Optional[LifecycleCostResult] = None,
    comparison: Optional[VariantComparison] = None,
) -> PlotsWritten:
    """Write the report's PNG set: every perspective's charts, plus the comparison's.

    Called by the `report` CLI and by `bridge.py` after the HTML and markdown reports. Every perspective gets its
    charts as `lifecycle_<chart>_<perspective_id>.png`; `lifecycle_perspective_costs.png` is drawn once. With a
    reference, the comparison charts (payback curve, NPV bridge, benchmark, and the comparison forms of the liquidity
    fan and swimlane) are drawn for the reference's own perspective id only; when the matrix lacks it, they are skipped
    with a reason, never drawn for another perspective. An empty matrix writes nothing. The audit heatmap is
    `write_audit_plots`.

    Args:
        matrix: The evaluated perspectives; each one gets the per-perspective charts.
        result_directory: Directory the `lifecycle_*.png` files are written into, next to the HTML report.
        reference_result: The baseline of a variant comparison, or None for no comparison charts.
        comparison: A comparison the caller already computed (the `report` CLI labels it with its directory ids);
            computed from the two results when None and a reference is given.

    Returns:
        The paths that exist on disk and one record per chart that drew nothing.

    Raises:
        views.CostDataError: If a prebuilt `comparison` was computed for a different perspective than the reference
            names.
    """
    written = PlotsWritten()
    if not matrix.results:
        return written
    _draw(written, plot_perspective_costs, matrix,
          os.path.join(result_directory, PERSPECTIVE_COSTS_FILE_NAME))
    comparison_perspective, comparison = _comparison_side(matrix, reference_result, comparison, written)
    for perspective_id, result in matrix.results.items():
        # The comparison forms of the fan and the swimlane belong to the compared perspective
        # alone; every other perspective gets the single-evaluation form of the same chart.
        pair = comparison if perspective_id == comparison_perspective else None
        for chart, renderer in _PER_PERSPECTIVE_CHARTS:
            _draw(written, renderer, result,
                  os.path.join(result_directory, report_plot_file_name(chart, perspective_id)),
                  skips=written.skipped)
        _draw(written, plot_lifecycle_swimlane, result,
              os.path.join(result_directory, report_plot_file_name("swimlane", perspective_id)),
              pair, skips=written.skipped)
        _draw(written, plot_liquidity_fan, result,
              os.path.join(result_directory, report_plot_file_name("liquidity_fan", perspective_id)),
              pair, skips=written.skipped)
    if reference_result is not None and comparison_perspective is not None:
        variant = matrix.results[comparison_perspective]
        for chart, renderer in _COMPARISON_RENDERERS:
            _draw(written, renderer, reference_result, variant,
                  os.path.join(result_directory,
                               report_plot_file_name(chart, comparison_perspective)))
    written.paths = [path for path in written.paths if os.path.isfile(path)]
    return written


def _draw(
    written: PlotsWritten, renderer: Callable[..., Optional[str]], *arguments: Any, **keywords: Any
) -> None:
    """Call one renderer and record its path, if any, under the paths this run wrote."""
    path = renderer(*arguments, **keywords)
    if path is not None:
        written.paths.append(path)


#: The charts that exist only as a difference between two evaluations, named as the skip records
#: name them. Listed once, because a run without a usable reference has to account for all of them.
_COMPARISON_CHART_NAMES = ("payback curve", "NPV bridge", "fixed-interest benchmark")


def _comparison_side(
    matrix: EvaluationMatrix,
    reference_result: Optional[LifecycleCostResult],
    comparison: Optional[VariantComparison],
    written: PlotsWritten,
) -> Tuple[Optional[str], Optional[VariantComparison]]:
    """Return which perspective the comparison charts are drawn for, and the comparison itself.

    The variant side is the matrix's result for the reference's own perspective id; if the matrix lacks it, no
    comparison chart is drawn and a skip is recorded.

    Args:
        matrix: The evaluated perspectives.
        reference_result: The comparison's baseline, or None for a run without one.
        comparison: A comparison the caller already computed, or None to compute it here.
        written: The record the skips are added to.

    Returns:
        `(perspective_id, comparison)`, both None when no comparison chart can be drawn.

    Raises:
        views.CostDataError: If a prebuilt comparison names a different perspective.
    """
    if reference_result is None:
        for chart in _COMPARISON_CHART_NAMES:
            _skip(written.skipped, chart, "",
                  "this run has no reference variant, and these charts are differences between "
                  "two evaluations.")
        return None, None
    perspective_id = reference_result.perspective_id
    variant = matrix.results.get(perspective_id)
    if variant is None:
        for chart in _COMPARISON_CHART_NAMES:
            _skip(written.skipped, chart, perspective_id,
                  f"the reference carries perspective {perspective_id!r}, which this matrix does "
                  f"not evaluate (it has {sorted(matrix.results)}); comparing it against a "
                  "different perspective would compare two different parties.")
        return None, None
    if comparison is not None and comparison.perspective_id != perspective_id:
        raise views.CostDataError(
            f"The PNG set was handed a comparison of perspective "
            f"{comparison.perspective_id!r} for a reference on {perspective_id!r}: the bridge, "
            "the benchmark and the fan's lower panel would then be three statements about "
            "different parties under one title. Pass the comparison computed for the reference's "
            "own perspective."
        )
    return perspective_id, comparison or compare(reference_result, variant)
