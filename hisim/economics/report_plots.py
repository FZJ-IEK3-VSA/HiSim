"""Matplotlib PNG companions of the lifecycle cost report (LIFECYCLE_COST_REPORT).

Same display groups and colors as the HTML report — both take them from
`presentation_style.py`, so a group keeps its hue across every output and this module no longer
imports `reporting.py` (W4.7). Written into the result directory next to the HTML report.

Like `reporting.py`, this module never computes: the numbers plotted come from `views.py` and
`results.py`; the arithmetic here is bar geometry and axis scaling.

**Why raster images exist at all**, given that `lifecycle_report.html` already draws the same
figures as inline SVG: a PNG can be dropped into the PDF report, a slide deck or an issue
comment, which an HTML page cannot. They are companions, never the primary output — the set is
a deliberate subset (five charts out of the report's dozen-odd sections), always for the *first*
perspective of the matrix, and it carries no tables, no plausibility panel and no audit trail.
A reviewer checking numbers should read the HTML; the PNGs are for pasting.

What the module does not own: which colour a display group has and which categories fall into
it (`presentation_style.py`), any figure being plotted (`views.py`, `results.py`), and the file
naming/orchestration of the report as a whole (`bridge.py`, `__main__.py`, which call
`write_report_plots`). Unlike the HTML and markdown reports these outputs are deliberately *not*
golden-tested — matplotlib rendering is not byte-stable across versions — so the guarantee that
they agree with the report is structural, not pinned: both read the same view functions.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

import matplotlib

matplotlib.use("Agg")  # postprocessing runs headless
import matplotlib.pyplot as plt  # noqa: E402  — backend must be set before pyplot

from hisim.economics import views  # noqa: E402
from hisim.economics.presentation_style import PresentationStyle  # noqa: E402
from hisim.economics.results import (  # noqa: E402
    EvaluationMatrix,
    LifecycleCostResult,
    cumulative_discounted_savings,
)


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

    The PNG counterpart of report section 3. It answers "does the money arrive in the years it
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
        f"Annual cash flows — {result.perspective_id} "
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

    The PNG counterpart of report section 6, and the one chart that shows the whole matrix at
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
    axis.set_title("Perspectives at a glance", fontsize=10, color=_Palette.INK, loc="left")
    figure.tight_layout()
    figure.savefig(path, facecolor=_Palette.SURFACE)
    plt.close(figure)
    return path


def plot_component_costs(result: LifecycleCostResult, path: str) -> str:
    """Per-subject NPV as diverging stacks (§7.4): costs right of 0, credits left, net marker.

    Credits (residual value, subsidies, feed-in, anyway credit) are never added onto the cost
    side — the black marker with whiskers is the net NPV band, `net = costs - credits`.
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
    for position, band in enumerate(nets):
        axis.text(lefts_pos[position] + max(lefts_pos) * 0.02 + 1, position,
                  f"{band.average:,.0f} [{band.minimum:,.0f} | {band.maximum:,.0f}]",
                  va="center", fontsize=7, color=_Palette.MUTED)
    axis.set_yticks(list(positions), [subject for subject, _b in breakdowns], fontsize=8, color=_Palette.INK)
    axis.invert_yaxis()
    axis.xaxis.grid(True, color=_Palette.GRID, linewidth=0.6)
    axis.yaxis.grid(False)
    axis.set_xlabel("NPV [EUR] — credits left of 0, costs right; marker = net NPV band",
                    color=_Palette.MUTED, fontsize=9)
    axis.set_title(f"Per-component costs — {result.perspective_id}", fontsize=10, color=_Palette.INK, loc="left")
    axis.legend(fontsize=7.5, frameon=False, ncol=2, labelcolor=_Palette.INK)
    figure.tight_layout()
    figure.savefig(path, facecolor=_Palette.SURFACE)
    plt.close(figure)
    return path


def plot_payback_curve(
    reference: LifecycleCostResult, variant: LifecycleCostResult, path: str
) -> str:
    """Cumulative discounted savings (reference - variant) per slot; zero-crossing = payback.

    The curves come from `results.cumulative_discounted_savings` — the same function the
    printed payback year is derived from (W4.4).
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
    axis.set_ylabel("cumulative discounted savings [EUR]", color=_Palette.MUTED, fontsize=9)
    axis.set_title("Discounted payback (zero-crossing)", fontsize=10, color=_Palette.INK, loc="left")
    axis.legend(fontsize=7.5, frameon=False, labelcolor=_Palette.INK)
    figure.tight_layout()
    figure.savefig(path, facecolor=_Palette.SURFACE)
    plt.close(figure)
    return path


def write_report_plots(
    matrix: EvaluationMatrix,
    result_directory: str,
    reference_result: Optional[LifecycleCostResult] = None,
) -> List[str]:
    """Writes the PNG set for the first perspective (+ payback when comparing).

    The module's only public orchestration point: called by `bridge.py` right after the HTML and
    markdown reports are written, and by the `report` CLI, so the PNG set always accompanies a
    report rather than being generated on its own. Only the matrix's first perspective is
    plotted — the PNGs are a hand-out, and the full per-perspective treatment is the HTML
    report's job — and an empty matrix produces no files instead of an error.

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
    if reference_result is not None:
        variant = matrix.results.get(reference_result.perspective_id, first)
        written.append(
            plot_payback_curve(reference_result, variant, os.path.join(result_directory, "lifecycle_payback_curve.png"))
        )
    return [path for path in written if os.path.isfile(path)]
