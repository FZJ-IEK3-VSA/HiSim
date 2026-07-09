"""Matplotlib PNG companions of the lifecycle cost report (LIFECYCLE_COST_REPORT).

Same display groups and colors as `reporting.py` (colors follow the entity across all
outputs). Written into the result directory next to the HTML report.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

import matplotlib

matplotlib.use("Agg")  # postprocessing runs headless
import matplotlib.pyplot as plt  # noqa: E402  — backend must be set before pyplot

from hisim.economics.reporting import DISPLAY_GROUPS, GROUP_COLORS_LIGHT, group_of  # noqa: E402
from hisim.economics.results import EvaluationMatrix, LifecycleCostResult  # noqa: E402

_SURFACE = "#fcfcfb"
_INK = "#0b0b0b"
_MUTED = "#898781"
_GRID = "#e1e0d9"


def _style_axis(axis) -> None:
    axis.set_facecolor(_SURFACE)
    for spine in ("top", "right"):
        axis.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        axis.spines[spine].set_color(_GRID)
    axis.tick_params(colors=_MUTED, labelsize=8)
    axis.yaxis.grid(True, color=_GRID, linewidth=0.6)
    axis.set_axisbelow(True)


def _new_figure(width: float = 9.0, height: float = 4.2):
    figure, axis = plt.subplots(figsize=(width, height), dpi=130)
    figure.patch.set_facecolor(_SURFACE)
    _style_axis(axis)
    return figure, axis


def plot_annual_cash_flows(result: LifecycleCostResult, path: str) -> str:
    """Stacked bars per year by display group (nominal), the timeline plausibility view."""
    horizon = result.parameters.observation_period_in_years
    years = list(range(horizon + 1))
    per_group: Dict[int, List[float]] = {index: [0.0] * (horizon + 1) for index in range(len(DISPLAY_GROUPS))}
    for entry in result.scoped_timeline().entries:
        if 0 <= entry.year <= horizon:
            per_group[group_of(entry.category)][entry.year] += entry.amount_in_euro.average
    figure, axis = _new_figure()
    bottom_pos = [0.0] * (horizon + 1)
    bottom_neg = [0.0] * (horizon + 1)
    for index, (group_name, _categories) in enumerate(DISPLAY_GROUPS):
        values = per_group[index]
        if not any(values):
            continue
        positives = [max(v, 0.0) for v in values]
        negatives = [min(v, 0.0) for v in values]
        if any(positives):
            axis.bar(years, positives, bottom=bottom_pos, width=0.82, label=group_name,
                     color=GROUP_COLORS_LIGHT[index], linewidth=0.6, edgecolor=_SURFACE)
            bottom_pos = [b + v for b, v in zip(bottom_pos, positives)]
        if any(negatives):
            label = None if any(positives) else group_name
            axis.bar(years, negatives, bottom=bottom_neg, width=0.82, label=label,
                     color=GROUP_COLORS_LIGHT[index], linewidth=0.6, edgecolor=_SURFACE)
            bottom_neg = [b + v for b, v in zip(bottom_neg, negatives)]
    axis.axhline(0, color=_MUTED, linewidth=0.8)
    axis.set_xlabel("year", color=_MUTED, fontsize=9)
    axis.set_ylabel("nominal EUR per year", color=_MUTED, fontsize=9)
    axis.set_title(
        f"Annual cash flows — {result.perspective_id} "
        f"(NPV {result.total_npv_in_euro.average:,.0f} EUR)",
        fontsize=10, color=_INK, loc="left",
    )
    axis.legend(fontsize=7.5, frameon=False, ncol=2, labelcolor=_INK)
    figure.tight_layout()
    figure.savefig(path, facecolor=_SURFACE)
    plt.close(figure)
    return path


def plot_investment_waterfall(result: LifecycleCostResult, path: str) -> str:
    """Year-0 build-up per subject: gross bars with the subsidy share hatched off."""
    subjects, gross_values, net_values, subsidy_values = [], [], [], []
    for subject, breakdown in result.component_breakdowns.items():
        gross = breakdown.investment_gross_in_euro.average
        subsidy = breakdown.subsidies_in_euro.average
        if gross <= 0:
            continue
        subjects.append(subject)
        gross_values.append(gross)
        subsidy_values.append(min(subsidy, gross))
        net_values.append(gross - min(subsidy, gross))
    if not subjects:
        return path
    figure, axis = _new_figure(height=max(2.2, 0.55 * len(subjects) + 1.2))
    positions = range(len(subjects))
    axis.barh(positions, net_values, color=GROUP_COLORS_LIGHT[0], label="net investment",
              edgecolor=_SURFACE, linewidth=0.6)
    axis.barh(positions, subsidy_values, left=net_values, color=GROUP_COLORS_LIGHT[3],
              label="covered by subsidies", edgecolor=_SURFACE, linewidth=0.6)
    for position, (gross, net) in enumerate(zip(gross_values, net_values)):
        axis.text(gross * 1.01, position, f"{net:,.0f} net / {gross:,.0f} gross", va="center",
                  fontsize=7.5, color=_MUTED)
    axis.set_yticks(list(positions), subjects, fontsize=8, color=_INK)
    axis.invert_yaxis()
    axis.xaxis.grid(True, color=_GRID, linewidth=0.6)
    axis.yaxis.grid(False)
    axis.set_xlabel("year-0 investment [EUR]", color=_MUTED, fontsize=9)
    axis.set_title(f"Investment build-up (year 0) — {result.perspective_id}", fontsize=10, color=_INK, loc="left")
    axis.legend(fontsize=7.5, frameon=False, labelcolor=_INK)
    figure.tight_layout()
    figure.savefig(path, facecolor=_SURFACE)
    plt.close(figure)
    return path


def plot_perspective_costs(matrix: EvaluationMatrix, path: str) -> str:
    """Equivalent annual cost per perspective as dot-with-whiskers (min/avg/max)."""
    labels, averages, lows, highs = [], [], [], []
    for perspective_id, result in matrix.results.items():
        band = result.equivalent_annual_cost_in_euro
        labels.append(perspective_id)
        averages.append(band.average)
        lows.append(band.average - band.minimum)
        highs.append(band.maximum - band.average)
    figure, axis = _new_figure(height=max(2.2, 0.5 * len(labels) + 1.2))
    positions = range(len(labels))
    axis.errorbar(averages, positions, xerr=[lows, highs], fmt="o", color=GROUP_COLORS_LIGHT[0],
                  ecolor=GROUP_COLORS_LIGHT[0], elinewidth=2, capsize=3, markersize=7,
                  markeredgecolor=_SURFACE, markeredgewidth=1.5)
    for position, (label, average) in enumerate(zip(labels, averages)):
        axis.text(averages[position] + highs[position] + max(averages) * 0.02, position,
                  f"{average:,.0f} EUR/a", va="center", fontsize=7.5, color=_MUTED)
    axis.set_yticks(list(positions), labels, fontsize=8, color=_INK)
    axis.invert_yaxis()
    axis.xaxis.grid(True, color=_GRID, linewidth=0.6)
    axis.yaxis.grid(False)
    axis.set_xlabel("equivalent annual cost [EUR/a] with min/max band", color=_MUTED, fontsize=9)
    axis.set_title("Perspectives at a glance", fontsize=10, color=_INK, loc="left")
    figure.tight_layout()
    figure.savefig(path, facecolor=_SURFACE)
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
    for index, (group_name, _categories) in enumerate(DISPLAY_GROUPS):
        values = []
        for _subject, breakdown in breakdowns:
            values.append(
                sum(v.average for category, v in breakdown.npv_by_category.items() if group_of(category) == index)
            )
        if not any(values):
            continue
        positives = [max(v, 0.0) for v in values]
        negatives = [min(v, 0.0) for v in values]
        if any(positives):
            axis.barh(positions, positives, left=lefts_pos, color=GROUP_COLORS_LIGHT[index], label=group_name,
                      edgecolor=_SURFACE, linewidth=0.6)
            lefts_pos = [left + value for left, value in zip(lefts_pos, positives)]
        if any(negatives):
            label = None if any(positives) else group_name
            axis.barh(positions, negatives, left=lefts_neg, color=GROUP_COLORS_LIGHT[index], label=label,
                      edgecolor=_SURFACE, linewidth=0.6)
            lefts_neg = [left + value for left, value in zip(lefts_neg, negatives)]
    axis.axvline(0, color=_MUTED, linewidth=0.9)
    # Net NPV band per subject: black dot with min/max whiskers on the same signed axis.
    nets = [breakdown.total_npv_in_euro for _subject, breakdown in breakdowns]
    axis.errorbar(
        [band.average for band in nets],
        list(positions),
        xerr=[
            [band.average - band.minimum for band in nets],
            [band.maximum - band.average for band in nets],
        ],
        fmt="o", color=_INK, ecolor=_INK, elinewidth=1.4, capsize=3, markersize=5,
        markeredgecolor=_SURFACE, markeredgewidth=1.2, label="net NPV (band)",
    )
    for position, band in enumerate(nets):
        axis.text(lefts_pos[position] + max(lefts_pos) * 0.02 + 1, position,
                  f"{band.average:,.0f} [{band.minimum:,.0f} | {band.maximum:,.0f}]",
                  va="center", fontsize=7, color=_MUTED)
    axis.set_yticks(list(positions), [subject for subject, _b in breakdowns], fontsize=8, color=_INK)
    axis.invert_yaxis()
    axis.xaxis.grid(True, color=_GRID, linewidth=0.6)
    axis.yaxis.grid(False)
    axis.set_xlabel("NPV [EUR] — credits left of 0, costs right; marker = net NPV band", color=_MUTED, fontsize=9)
    axis.set_title(f"Per-component costs — {result.perspective_id}", fontsize=10, color=_INK, loc="left")
    axis.legend(fontsize=7.5, frameon=False, ncol=2, labelcolor=_INK)
    figure.tight_layout()
    figure.savefig(path, facecolor=_SURFACE)
    plt.close(figure)
    return path


def plot_payback_curve(
    reference: LifecycleCostResult, variant: LifecycleCostResult, path: str
) -> str:
    """Cumulative discounted savings (reference - variant) per slot; zero-crossing = payback."""
    horizon = variant.parameters.observation_period_in_years
    interest = variant.parameters.interest_rate
    years = list(range(horizon + 1))
    figure, axis = _new_figure(height=3.6)
    styles = {"minimum": (":", 1.2, "optimistic"), "average": ("-", 2.2, "expected"),
              "maximum": ("--", 1.2, "pessimistic")}
    for attribute, (linestyle, linewidth, label) in styles.items():
        running, series = 0.0, []
        for year in years:
            ref_value = getattr(reference.annual_cost_series_nominal_in_euro[year], attribute)
            var_value = getattr(variant.annual_cost_series_nominal_in_euro[year], attribute)
            running += (ref_value - var_value) / ((1 + interest) ** year)
            series.append(running)
        axis.plot(years, series, linestyle, linewidth=linewidth, color=GROUP_COLORS_LIGHT[0], label=label)
    axis.axhline(0, color=_MUTED, linewidth=0.8)
    axis.set_xlabel("year", color=_MUTED, fontsize=9)
    axis.set_ylabel("cumulative discounted savings [EUR]", color=_MUTED, fontsize=9)
    axis.set_title("Discounted payback (zero-crossing)", fontsize=10, color=_INK, loc="left")
    axis.legend(fontsize=7.5, frameon=False, labelcolor=_INK)
    figure.tight_layout()
    figure.savefig(path, facecolor=_SURFACE)
    plt.close(figure)
    return path


def write_report_plots(
    matrix: EvaluationMatrix,
    result_directory: str,
    reference_result: Optional[LifecycleCostResult] = None,
) -> List[str]:
    """Writes the PNG set for the first perspective (+ payback when comparing)."""
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
