"""Human-readable lifecycle cost reports (postprocessing option LIFECYCLE_COST_REPORT).

Follows the money along the calculation chain so results can be checked for plausibility:

1. plausibility panel (automated checks, thresholds in cost_database/plausibility_checks.json)
2. input audit (facts x database resolution)
3. investment build-up waterfalls (year 0)
4. annual cash-flow timeline + cumulative discounted cost
5. year-1 energy bill decomposition with implied effective prices
6. subsidy decision cards
7. perspective overview (EAC bands)
8. per-component breakdowns
9. variant comparison (delta waterfall by subject + discounted payback curve)

Outputs: `cost_summary.md` (diffable text), `lifecycle_report.html` (self-contained, inline
SVG, light/dark aware) and matplotlib PNGs (see `report_plots.py`). Everything is computed
from the in-memory results / stored exports only — no engine changes.
"""

from __future__ import annotations

import datetime
import html
import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from hisim.economics.database import DEFAULT_COST_DATABASE_PATH, CostDatabase, CostDataError
from hisim.economics.evaluator import EvaluationInputs
from hisim.economics.parameters import EconomicParameters
from hisim.economics.results import EvaluationMatrix, LifecycleCostResult, VariantComparison
from hisim.economics.timeline import CostCategory
from hisim.economics.uncertainty import UncertainValue

COST_SUMMARY_FILE_NAME = "cost_summary.md"
LIFECYCLE_REPORT_FILE_NAME = "lifecycle_report.html"
COMPARISON_REPORT_FILE_NAME = "variant_comparison_report.html"

# ---------------------------------------------------------------------------- display groups
# 16 cost categories fold into 8 display groups so the fixed categorical palette
# (dataviz reference palette; hues assigned in fixed slot order, never cycled) covers them.
# Colors follow the entity: a group keeps its hue in every chart of every report.

DISPLAY_GROUPS: List[Tuple[str, Tuple[CostCategory, ...]]] = [
    ("Investment & financing", (CostCategory.INVESTMENT, CostCategory.PLANNING, CostCategory.REMOVAL,
                                CostCategory.LOAN_INTEREST, CostCategory.LOAN_PRINCIPAL,
                                CostCategory.LOAN_DISBURSEMENT)),
    ("Feed-in revenue", (CostCategory.FEED_IN_REVENUE,)),
    ("Residual value & anyway credit", (CostCategory.RESIDUAL_VALUE, CostCategory.ANYWAY_COST_CREDIT)),
    ("Subsidies", (CostCategory.SUBSIDY,)),
    ("Replacements", (CostCategory.REPLACEMENT, CostCategory.REPLACEMENT_RESERVE)),
    ("Energy", (CostCategory.ENERGY_WORKING, CostCategory.ENERGY_STANDING,
                CostCategory.ENERGY_CAPACITY_CHARGE)),
    ("CO2", (CostCategory.ENERGY_CO2_PRICE, CostCategory.CO2_DAMAGE)),
    ("Maintenance & operation", (CostCategory.MAINTENANCE, CostCategory.FIXED_OPERATION,
                                 CostCategory.MODERNIZATION_LEVY)),
]

#: Light-mode categorical slots 1..8 of the reference palette, in fixed order.
GROUP_COLORS_LIGHT = ["#2a78d6", "#1baf7a", "#eda100", "#008300", "#4a3aa7", "#e34948", "#e87ba4", "#eb6834"]
GROUP_COLORS_DARK = ["#3987e5", "#199e70", "#c98500", "#008300", "#9085e9", "#e66767", "#d55181", "#d95926"]

_CATEGORY_TO_GROUP: Dict[CostCategory, int] = {
    category: index for index, (_name, categories) in enumerate(DISPLAY_GROUPS) for category in categories
}


def group_of(category: CostCategory) -> int:
    """Display-group index of a cost category."""
    return _CATEGORY_TO_GROUP.get(category, 0)


def _fmt(value: float) -> str:
    """Compact euro formatting."""
    if abs(value) >= 100000:
        return f"{value / 1000:,.0f}k"
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    return f"{value:,.2f}" if abs(value) < 100 else f"{value:,.0f}"


def _band_str(band: Optional[UncertainValue], unit: str = "EUR") -> str:
    """`avg [min | max] unit` rendering of a band."""
    if band is None:
        return "-"
    if band.is_exact():
        return f"{_fmt(band.average)} {unit}"
    return f"{_fmt(band.average)} [{_fmt(band.minimum)} | {_fmt(band.maximum)}] {unit}"


# ---------------------------------------------------------------------------- plausibility (B)

@dataclass
class PlausibilityCheck:
    """One automated check result for the panel."""

    name: str
    status: str  # PASS | WARN | FAIL
    value: str
    expected: str
    detail: str = ""


@dataclass
class PlausibilityConfig:
    """Thresholds loaded from cost_database/plausibility_checks.json (reviewable data)."""

    effective_price_ranges: Dict[str, Tuple[float, float]] = field(default_factory=dict)
    eac_per_m2_range: Tuple[float, float] = (5.0, 80.0)
    lcoh_range: Tuple[float, float] = (0.05, 0.50)
    maintenance_ratio_range: Tuple[float, float] = (0.02, 0.80)
    band_width_warn: float = 3.5
    reconciliation_tolerance: float = 1e-6

    @classmethod
    def load(cls, base_path: Optional[str] = None) -> "PlausibilityConfig":
        """Loads the thresholds file; falls back to the defaults above when missing."""
        path = os.path.join(base_path or DEFAULT_COST_DATABASE_PATH, "plausibility_checks.json")
        if not os.path.isfile(path):
            return cls()
        with open(path, encoding="utf-8") as file:
            raw = json.load(file)
        return cls(
            effective_price_ranges={
                carrier: (bounds[0], bounds[1])
                for carrier, bounds in raw.get("effective_price_ranges", {}).items()
                if isinstance(bounds, list)
            },
            eac_per_m2_range=tuple(raw.get("equivalent_annual_cost_per_m2_range", (5.0, 80.0))),
            lcoh_range=tuple(raw.get("levelized_cost_of_heat_range", (0.05, 0.50))),
            maintenance_ratio_range=tuple(raw.get("maintenance_to_investment_npv_ratio_range", (0.02, 0.80))),
            band_width_warn=raw.get("band_width_max_over_min_warn", 3.5),
            reconciliation_tolerance=raw.get("reconciliation_tolerance", 1e-6),
        )


def _range_check(name: str, value: float, bounds: Tuple[float, float], unit: str, detail: str = "") -> PlausibilityCheck:
    low, high = bounds
    status = "PASS" if low <= value <= high else "WARN"
    return PlausibilityCheck(
        name=name,
        status=status,
        value=f"{value:,.3f} {unit}" if abs(value) < 100 else f"{value:,.0f} {unit}",
        expected=f"{low:g} - {high:g} {unit}",
        detail=detail,
    )


def all_bands_degenerate(matrix: EvaluationMatrix) -> bool:
    """True when every result band is exact (min = avg = max).

    That is the expected state when the price basis year resolves to the 1:1-migrated legacy
    data (deliberately degenerate for parity, §10.1 Phase 1); banded AI-estimate data ships
    for 2026 and 2035. The reports surface this so missing whiskers read as a data property,
    not a bug.
    """
    return all(result.total_npv_in_euro.is_exact() for result in matrix.results.values())


def _degenerate_note(matrix: EvaluationMatrix, inputs: EvaluationInputs) -> str:
    reference = next(iter(matrix.results.values()))
    basis = reference.parameters.price_basis_year or inputs.simulation_year
    return (
        f"All cost inputs resolved to exact values, so every min/avg/max band is degenerate and "
        f"no uncertainty whiskers appear. Price basis year {basis} uses the 1:1-migrated legacy "
        f"data, which deliberately carries no bands (parity phase, cost_spec.md §10.1). Banded "
        f"data ships for 2026 and 2035 — set EconomicParameters.price_basis_year accordingly, "
        f"or add bands to the {basis} entries as a data PR."
    )


def run_plausibility_checks(
    matrix: EvaluationMatrix,
    inputs: EvaluationInputs,
    config: Optional[PlausibilityConfig] = None,
) -> List[PlausibilityCheck]:
    """The automated panel: structural invariants (FAIL) and magnitude ranges (WARN)."""
    config = config or PlausibilityConfig.load()
    checks: List[PlausibilityCheck] = []
    if not matrix.results:
        return [PlausibilityCheck("results present", "FAIL", "0 perspectives", ">= 1")]
    reference = next(iter(matrix.results.values()))
    horizon = reference.parameters.observation_period_in_years

    # --- structural invariants (hard) --------------------------------------------------
    for perspective_id, result in matrix.results.items():
        subject_sum = UncertainValue.sum(result.npv_by_component.values())
        delta = abs(subject_sum.average - result.total_npv_in_euro.average)
        tolerance = config.reconciliation_tolerance * max(1.0, abs(result.total_npv_in_euro.average))
        checks.append(
            PlausibilityCheck(
                name=f"subjects sum to total ({perspective_id})",
                status="PASS" if delta <= tolerance else "FAIL",
                value=f"delta {_fmt(delta)} EUR",
                expected="0",
            )
        )
        band = result.total_npv_in_euro
        if not band.minimum <= band.average <= band.maximum:
            checks.append(PlausibilityCheck(f"band ordering ({perspective_id})", "FAIL", str(band), "min<=avg<=max"))

    def npv_of(result: LifecycleCostResult, *categories: CostCategory) -> float:
        return sum(result.npv_by_category[cat].average for cat in categories if cat in result.npv_by_category)

    for perspective_id, result in matrix.results.items():
        residual = abs(npv_of(result, CostCategory.RESIDUAL_VALUE))
        purchases = npv_of(result, CostCategory.INVESTMENT, CostCategory.REPLACEMENT)
        if residual and purchases:
            checks.append(
                PlausibilityCheck(
                    name=f"residual value <= purchases ({perspective_id})",
                    status="PASS" if residual <= purchases * (1 + 1e-9) else "FAIL",
                    value=f"{_fmt(residual)} vs {_fmt(purchases)} EUR",
                    expected="residual below discounted purchases",
                )
            )
        subsidies = abs(npv_of(result, CostCategory.SUBSIDY))
        basis = npv_of(result, CostCategory.INVESTMENT, CostCategory.PLANNING, CostCategory.REMOVAL)
        if subsidies and basis:
            checks.append(
                PlausibilityCheck(
                    name=f"subsidies <= eligible basis ({perspective_id})",
                    status="PASS" if subsidies <= basis * (1 + 1e-9) else "FAIL",
                    value=f"{_fmt(subsidies)} vs {_fmt(basis)} EUR",
                    expected="support below its cost basis",
                )
            )

    # --- magnitude ranges (advisory) ----------------------------------------------------
    fraction = max(inputs.simulated_period_fraction, 1e-9)
    for determinants in inputs.billing:
        carrier = determinants.carrier.value
        bounds = config.effective_price_ranges.get(carrier)
        quantity = determinants.energy_bought_in_kwh / fraction
        if bounds is None or quantity <= 0:
            continue
        year1 = UncertainValue.sum(
            entry.amount_in_euro
            for entry in reference.scoped_timeline().entries
            if entry.year == 1
            and entry.subject == carrier
            and entry.category
            in (
                CostCategory.ENERGY_WORKING,
                CostCategory.ENERGY_STANDING,
                CostCategory.ENERGY_CAPACITY_CHARGE,
                CostCategory.ENERGY_CO2_PRICE,
            )
        )
        if year1.average == 0:
            continue
        checks.append(
            _range_check(
                f"effective {carrier} price (year 1)",
                year1.average / quantity,
                bounds,
                "EUR/unit",
                detail=f"{_fmt(year1.average)} EUR for {quantity:,.0f} units — catches unit mix-ups",
            )
        )

    area = inputs.living_area_in_m2 or inputs.heated_floor_area_in_m2
    if area:
        checks.append(
            _range_check(
                f"equivalent annual cost per m2 ({reference.perspective_id})",
                reference.equivalent_annual_cost_in_euro.average / area,
                config.eac_per_m2_range,
                "EUR/m2a",
            )
        )
    if reference.levelized_cost_of_heat_in_euro_per_kwh is not None:
        checks.append(
            _range_check(
                "levelized cost of heat",
                reference.levelized_cost_of_heat_in_euro_per_kwh.average,
                config.lcoh_range,
                "EUR/kWh",
            )
        )
    maintenance = npv_of(reference, CostCategory.MAINTENANCE, CostCategory.FIXED_OPERATION)
    investment = npv_of(reference, CostCategory.INVESTMENT, CostCategory.REPLACEMENT)
    if maintenance and investment:
        checks.append(
            _range_check(
                f"maintenance / investment NPV ratio ({reference.perspective_id})",
                maintenance / investment,
                config.maintenance_ratio_range,
                "",
                detail="a huge ratio usually means an absolute fee stored as a rate (issues #1)",
            )
        )
    band = reference.total_npv_in_euro
    if band.minimum > 0:
        ratio = band.maximum / band.minimum
        checks.append(
            _range_check(
                f"uncertainty band width max/min ({reference.perspective_id})",
                ratio,
                (1.0, config.band_width_warn),
                "x",
                detail=f"over {horizon} years; very wide bands usually mean a band typo in the data",
            )
        )
    return checks


# ---------------------------------------------------------------------------- markdown (C)

def build_cost_summary_markdown(
    matrix: EvaluationMatrix,
    inputs: EvaluationInputs,
    checks: List[PlausibilityCheck],
    comparison: Optional[VariantComparison] = None,
) -> str:
    """`cost_summary.md`: compact, greppable, git-diffable (the §9.5 review workflow)."""
    reference = next(iter(matrix.results.values()))
    params = reference.parameters
    lines: List[str] = []
    lines.append("# Lifecycle cost summary")
    lines.append("")
    lines.append(
        f"Simulation year {inputs.simulation_year}, country {params.country}, "
        f"horizon {params.observation_period_in_years} a, interest {params.interest_rate:.1%}, "
        f"price basis {params.price_basis_year}. "
        f"Monetary values as `avg [min | max]` (cost_spec.md §3.9)."
    )
    lines.append("")
    if all_bands_degenerate(matrix):
        lines.append(f"> **Note:** {_degenerate_note(matrix, inputs)}")
        lines.append("")
    lines.append("## Plausibility checks")
    lines.append("")
    lines.append("| Status | Check | Value | Expected |")
    lines.append("|---|---|---|---|")
    icon = {"PASS": "OK", "WARN": "WARN(!)", "FAIL": "FAIL(!!)"}
    for check in checks:
        lines.append(f"| {icon[check.status]} | {check.name} | {check.value} | {check.expected} |")
    failed = [check for check in checks if check.status != "PASS"]
    if failed:
        lines.append("")
        for check in failed:
            if check.detail:
                lines.append(f"- **{check.name}**: {check.detail}")
    lines.append("")
    lines.append("## Perspectives")
    lines.append("")
    lines.append("| Perspective | NPV | Equivalent annual cost | Monthly (year 1) | LCOH |")
    lines.append("|---|---|---|---|---|")
    for perspective_id, result in matrix.results.items():
        lines.append(
            f"| {perspective_id} | {_band_str(result.total_npv_in_euro)} "
            f"| {_band_str(result.equivalent_annual_cost_in_euro, 'EUR/a')} "
            f"| {_band_str(result.monthly_cost_year1_in_euro, 'EUR/mo')} "
            f"| {_band_str(result.levelized_cost_of_heat_in_euro_per_kwh, 'EUR/kWh')} |"
        )
    lines.append("")
    lines.append(f"## Cost structure ({reference.perspective_id})")
    lines.append("")
    lines.append("| Display group | NPV |")
    lines.append("|---|---|")
    for index, (group_name, _categories) in enumerate(DISPLAY_GROUPS):
        total = UncertainValue.sum(
            value for category, value in reference.npv_by_category.items() if group_of(category) == index
        )
        if total.average or total.minimum or total.maximum:
            lines.append(f"| {group_name} | {_band_str(total)} |")
    lines.append("")
    lines.append(f"## Per subject ({reference.perspective_id})")
    lines.append("")
    lines.append("| Subject | NPV | Year-0 investment | Subsidies |")
    lines.append("|---|---|---|---|")
    for subject, breakdown in reference.component_breakdowns.items():
        lines.append(
            f"| {subject} | {_band_str(breakdown.total_npv_in_euro)} "
            f"| {_band_str(breakdown.investment_gross_in_euro)} "
            f"| {_band_str(breakdown.subsidies_in_euro)} |"
        )
    decisions = [decision for result in matrix.results.values() for decision in result.subsidy_decisions]
    if decisions:
        lines.append("")
        lines.append("## Subsidy decisions (first perspective with subsidies)")
        lines.append("")
        seen = set()
        for decision in decisions:
            if decision.measure_subject in seen:
                continue
            seen.add(decision.measure_subject)
            applied = ", ".join(
                f"{award.scheme_id} ({_band_str(award.upfront_amount)})"
                for award in decision.applied
                if award.upfront_amount.maximum
            ) or "none"
            lines.append(f"- **{decision.measure_subject}**: applied {applied}")
            for reject in decision.rejected:
                lines.append(f"  - rejected {reject['scheme_id']}: {reject['reason']}")
            for item in decision.undetermined:
                lines.append(f"  - undetermined {item['scheme_id']} (missing: {', '.join(item['missing_fields'])})")
            if decision.undetermined_upper_bound_in_euro > 0:
                lines.append(
                    f"  - answering the open questions could unlock up to "
                    f"{_fmt(decision.undetermined_upper_bound_in_euro)} EUR"
                )
    if comparison is not None:
        lines.append("")
        lines.append(f"## Variant comparison ({comparison.perspective_id})")
        lines.append("")
        lines.append(f"- NPV delta (variant - reference): {_band_str(comparison.npv_delta_in_euro)}")
        lines.append(
            f"- Equivalent annual cost delta: {_band_str(comparison.equivalent_annual_cost_delta_in_euro, 'EUR/a')}"
        )
        payback = comparison.discounted_payback_years
        lines.append(
            f"- Discounted payback [a]: best {payback.get('low')}, expected {payback.get('average')}, "
            f"worst {payback.get('high')} (None = never within horizon)"
        )
        lines.append("")
        lines.append("| Subject | NPV delta |")
        lines.append("|---|---|")
        for subject, delta in sorted(
            comparison.npv_delta_by_subject.items(), key=lambda item: item[1].average
        ):
            lines.append(f"| {subject} | {_band_str(delta)} |")
    lines.append("")
    lines.append(
        f"_Generated {datetime.date.today().isoformat()} by hisim.economics; "
        "trace any value with `python -m hisim.economics explain`._"
    )
    return "\n".join(lines) + "\n"


def write_cost_summary(
    matrix: EvaluationMatrix,
    inputs: EvaluationInputs,
    checks: List[PlausibilityCheck],
    result_directory: str,
    comparison: Optional[VariantComparison] = None,
) -> str:
    """Writes cost_summary.md."""
    path = os.path.join(result_directory, COST_SUMMARY_FILE_NAME)
    with open(path, "w", encoding="utf-8") as file:
        file.write(build_cost_summary_markdown(matrix, inputs, checks, comparison))
    return path


# ---------------------------------------------------------------------------- SVG helpers (A)

_SVG_FONT = 'font-family="system-ui, -apple-system, Segoe UI, sans-serif"'


def _esc(text: str) -> str:
    return html.escape(str(text), quote=True)


def _svg_open(width: int, height: int) -> List[str]:
    return [
        f'<svg viewBox="0 0 {width} {height}" width="100%" style="max-width:{width}px" '
        f'role="img" xmlns="http://www.w3.org/2000/svg">'
    ]


def _rect(x: float, y: float, w: float, h: float, color: str, tooltip: str, rx: float = 0.0) -> str:
    """A mark with a native tooltip and the 2px surface gap handled by the caller."""
    return (
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(w, 0.1):.1f}" height="{max(h, 0.1):.1f}" '
        f'fill="{color}" rx="{rx}"><title>{_esc(tooltip)}</title></rect>'
    )


def _text(x: float, y: float, content: str, size: int = 11, anchor: str = "start",
          color: str = "var(--ink-2)", bold: bool = False) -> str:
    weight = ' font-weight="600"' if bold else ""
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" text-anchor="{anchor}" '
        f'fill="{color}" {_SVG_FONT}{weight}>{_esc(content)}</text>'
    )


def _hline(x1: float, x2: float, y: float, color: str = "var(--baseline)", width: float = 1.0) -> str:
    return f'<line x1="{x1:.1f}" y1="{y:.1f}" x2="{x2:.1f}" y2="{y:.1f}" stroke="{color}" stroke-width="{width}"/>'


def _table(headers: List[str], rows: List[List[str]]) -> str:
    """A plain result table; all cell values must already be strings (and escaped)."""
    head = "".join(f"<th>{header}</th>" for header in headers)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f"<table><tr>{head}</tr>{body}</table>"


def _details(summary: str, content: str, open_by_default: bool = False) -> str:
    open_attr = " open" if open_by_default else ""
    return f"<details{open_attr}><summary>{summary}</summary>{content}</details>"


def _category_table(result: LifecycleCostResult) -> str:
    """NPV by display group and by raw cost category — the §3.7 result table."""
    rows = []
    for index, (group_name, categories) in enumerate(DISPLAY_GROUPS):
        group_total = UncertainValue.sum(
            value for category, value in result.npv_by_category.items() if group_of(category) == index
        )
        if not (group_total.average or group_total.minimum or group_total.maximum):
            continue
        rows.append([f"<b>{_esc(group_name)}</b>", f"<b>{_esc(_band_str(group_total))}</b>"])
        for category in categories:
            value = result.npv_by_category.get(category)
            if value is not None and (value.average or value.minimum or value.maximum):
                rows.append([f"&nbsp;&nbsp;{_esc(category.value)}", _esc(_band_str(value))])
    return _table(["Category", "NPV"], rows)


def _loan_svg(result: LifecycleCostResult) -> str:
    """Loan amortization: interest vs. principal per year (§4.4)."""
    horizon = result.parameters.observation_period_in_years
    interest_per_year = [0.0] * (horizon + 1)
    principal_per_year = [0.0] * (horizon + 1)
    for entry in result.scoped_timeline().entries:
        if 0 <= entry.year <= horizon:
            if entry.category == CostCategory.LOAN_INTEREST:
                interest_per_year[entry.year] += entry.amount_in_euro.average
            elif entry.category == CostCategory.LOAN_PRINCIPAL:
                principal_per_year[entry.year] += entry.amount_in_euro.average
    if not any(interest_per_year) and not any(principal_per_year):
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
    """Section 4b: lifecycle CO2 (§3.8) — embodied vs. operational, per subject/carrier."""
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
    series = co2.operational_co2_by_year_in_kg
    cumulative, running = [], 0.0
    for value in series:
        running += value
        cumulative.append(running)
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
    table_rows.append(["<b>Total</b>", f"<b>{co2.embodied_co2_in_kg:,.0f}</b>",
                       f"<b>{sum(co2.operational_co2_by_year_in_kg):,.0f}</b>", f"<b>{co2.total_co2_in_kg:,.0f}</b>"])
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
    chips = "".join(
        f'<span class="chip"><span class="swatch" style="background:var(--g{index})"></span>'
        f"{_esc(DISPLAY_GROUPS[index][0])}</span>"
        for index in groups_present
    )
    return f'<div class="legend">{chips}</div>'


def _annual_flow_svg(result: LifecycleCostResult) -> str:
    """Stacked bars per year by display group (nominal, negatives below the axis)."""
    horizon = result.parameters.observation_period_in_years
    per_year: List[Dict[int, float]] = [dict() for _ in range(horizon + 1)]
    for entry in result.scoped_timeline().entries:
        if 0 <= entry.year <= horizon:
            index = group_of(entry.category)
            per_year[entry.year][index] = per_year[entry.year].get(index, 0.0) + entry.amount_in_euro.average
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
        for index in range(len(DISPLAY_GROUPS)):
            value = groups.get(index, 0.0)
            if not value:
                continue
            bar_h = abs(value) * scale
            tooltip = f"year {year} - {DISPLAY_GROUPS[index][0]}: {_fmt(value)} EUR"
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
    """
    horizon = result.parameters.observation_period_in_years
    interest = result.parameters.interest_rate
    per_year = {"minimum": [0.0] * (horizon + 1), "average": [0.0] * (horizon + 1), "maximum": [0.0] * (horizon + 1)}
    for entry in result.scoped_timeline().entries:
        if 0 <= entry.year <= horizon:
            factor = 1.0 / ((1 + interest) ** entry.year)
            for attribute in per_year:
                per_year[attribute][entry.year] += getattr(entry.amount_in_euro, attribute) * factor
    cumulative: Dict[str, List[float]] = {}
    for attribute, values in per_year.items():
        running, series = 0.0, []
        for value in values:
            running += value
            series.append(running)
        cumulative[attribute] = series
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
        forward = points_of(cumulative["minimum"])
        backward = " ".join(
            f"{left + year * step:.1f},{to_y(value):.1f}"
            for year, value in reversed(list(enumerate(cumulative["maximum"])))
        )
        parts.append(
            f'<polygon points="{forward} {backward}" fill="var(--g0)" opacity="0.15">'
            f"<title>cumulative discounted cost, min/max envelope</title></polygon>"
        )
    parts.append(
        f'<polyline points="{points_of(cumulative["average"])}" fill="none" stroke="var(--g0)" stroke-width="2">'
        f"<title>cumulative discounted cost (average slot)</title></polyline>"
    )
    parts.append(_text(left - 6, to_y(top_value) + 8, _fmt(top_value), 10, "end", "var(--muted)"))
    parts.append(_text(left - 6, to_y(0.0) + 4, "0", 10, "end", "var(--muted)"))
    parts.append(
        _text(left + horizon * step, to_y(cumulative["average"][-1]) - 6,
              f"NPV {_band_str(band)}", 11, "end", "var(--ink-1)", bold=True)
    )
    parts.append("</svg>")
    return "".join(parts)


def _waterfall_svg(steps: List[Tuple[str, float, str]], total_label: str) -> str:
    """Horizontal waterfall: (label, signed value, color-var) steps ending in a net bar."""
    width, row_h, left = 860, 26, 220
    height = (len(steps) + 2) * row_h + 10
    net = sum(value for _label, value, _color in steps)
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
    parts.append(_rect(left, y + 4, abs(net) * scale, row_h - 8, "var(--ink-1)", f"{total_label}: {_fmt(net)} EUR", rx=3))
    parts.append(_text(left + abs(net) * scale + 6, y + row_h / 2 + 4, f"{_fmt(net)} EUR", 11, "start",
                       "var(--ink-1)", bold=True))
    parts.append("</svg>")
    return "".join(parts)


def _whisker_svg(rows: List[Tuple[str, UncertainValue]], unit: str) -> str:
    """Dot-with-whiskers per row (perspective overview / any banded metric list)."""
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
            f'<circle cx="{to_x(band.average):.1f}" cy="{mid:.1f}" r="5" fill="var(--g0)" '
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
    """
    breakdowns = list(result.component_breakdowns.values())
    if not breakdowns:
        return ""

    def group_values(breakdown) -> Dict[int, float]:
        values: Dict[int, float] = {}
        for category, band in breakdown.npv_by_category.items():
            index = group_of(category)
            values[index] = values.get(index, 0.0) + band.average
        return values

    per_subject = {breakdown.subject: group_values(breakdown) for breakdown in breakdowns}
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
        for index in range(len(DISPLAY_GROUPS)):
            value = values.get(index, 0.0)
            if not value:
                continue
            bar_w = abs(value) * scale
            tooltip = f"{breakdown.subject} - {DISPLAY_GROUPS[index][0]}: {_fmt(value)} EUR NPV"
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
            f'<circle cx="{to_x(total.average):.1f}" cy="{mid:.1f}" r="4" fill="var(--ink-1)" '
            f'stroke="var(--surface)" stroke-width="1.5">'
            f"<title>{_esc(breakdown.subject)} net NPV: {_esc(_band_str(total))}</title></circle>"
        )
        parts.append(_text(x_pos + 8, mid + 4, _band_str(total), 10, "start", "var(--muted)"))
        y += row_h
    parts.append(_text(zero_x, height - 6, "credits left | costs right of 0; whisker + dot = net NPV band",
                       9, "middle", "var(--muted)"))
    parts.append("</svg>")
    return "".join(parts)


def _payback_svg(reference: LifecycleCostResult, variant: LifecycleCostResult) -> str:
    """Cumulative discounted savings (reference - variant) per slot; zero-crossing = payback."""
    horizon = variant.parameters.observation_period_in_years
    interest = variant.parameters.interest_rate
    series: Dict[str, List[float]] = {}
    for slot_name, getter in (("min", lambda b: b.minimum), ("avg", lambda b: b.average), ("max", lambda b: b.maximum)):
        cumulative, running = [], 0.0
        for year in range(horizon + 1):
            ref_value = getter(reference.annual_cost_series_nominal_in_euro[year])
            var_value = getter(variant.annual_cost_series_nominal_in_euro[year])
            running += (ref_value - var_value) / ((1 + interest) ** year)
            cumulative.append(running)
        series[slot_name] = cumulative
    top_value = max(max(values) for values in series.values())
    low_value = min(min(values) for values in series.values())
    width, height, left, top, bottom = 860, 240, 70, 14, 28
    scale = (height - top - bottom) / max(top_value - low_value, 1e-9)
    step = (width - left - 20) / max(horizon, 1)

    def to_y(value: float) -> float:
        return top + (top_value - value) * scale

    parts = _svg_open(width, height)
    parts.append(_hline(left, width - 10, to_y(0.0), "var(--baseline)"))
    styles = {"avg": ("var(--g0)", 2.5, ""), "min": ("var(--g0)", 1.2, ' stroke-dasharray="5 4"'),
              "max": ("var(--g0)", 1.2, ' stroke-dasharray="2 4"')}
    labels = {"avg": "expected", "min": "optimistic (LOW world)", "max": "pessimistic (HIGH world)"}
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


# ---------------------------------------------------------------------------- HTML report (A)

_REPORT_CSS = """
:root { color-scheme: light dark;
  --surface:#fcfcfb; --page:#f9f9f7; --ink-1:#0b0b0b; --ink-2:#52514e; --muted:#898781;
  --grid:#e1e0d9; --baseline:#c3c2b7; --border:rgba(11,11,11,0.10);
  --good:#0ca30c; --warning:#fab219; --critical:#d03b3b;
  --g0:#2a78d6; --g1:#1baf7a; --g2:#eda100; --g3:#008300; --g4:#4a3aa7; --g5:#e34948; --g6:#e87ba4; --g7:#eb6834; }
@media (prefers-color-scheme: dark) { :root {
  --surface:#1a1a19; --page:#0d0d0d; --ink-1:#ffffff; --ink-2:#c3c2b7; --muted:#898781;
  --grid:#2c2c2a; --baseline:#383835; --border:rgba(255,255,255,0.10);
  --g0:#3987e5; --g1:#199e70; --g2:#c98500; --g3:#008300; --g4:#9085e9; --g5:#e66767; --g6:#d55181; --g7:#d95926; } }
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


def _audit_section_html(inputs: EvaluationInputs, database: CostDatabase, params: EconomicParameters) -> str:
    """Section 1: input audit — are the declared facts and resolved prices right?"""
    year = params.price_basis_year or inputs.simulation_year
    rows = []
    for subject_facts in inputs.cost_facts:
        facts = subject_facts.facts
        flags = []
        unit_price, lifetime, origin = None, facts.lifetime_override_in_years, "override"
        try:
            entry = database.get_device_entry(facts.asset_class, year, params.country)
            if facts.investment_cost_override_in_euro is not None:
                unit_price = facts.investment_cost_override_in_euro
                origin = f"override ({facts.override_source or 'NO SOURCE'})"
                if not facts.override_source:
                    flags.append("override without source")
            else:
                unit_price = entry.specific_investment
                origin = entry.entry_key
            lifetime = lifetime or entry.service_life_in_years
        except CostDataError:
            flags.append("no database entry")
        if facts.size > 1e4:
            flags.append(f"size {facts.size:,.0f} {facts.size_unit.value} looks implausible")
        rows.append(
            "<tr><td>{subject}</td><td>{cls}</td><td>{size:,.1f} {unit}</td><td>{price}</td>"
            "<td>{life}</td><td>{origin}</td><td class=\"flag\">{flags}</td></tr>".format(
                subject=_esc(subject_facts.subject),
                cls=_esc(facts.asset_class.value),
                size=facts.size,
                unit=_esc(facts.size_unit.value),
                price=_esc(_band_str(unit_price, "EUR/unit")),
                life=f"{lifetime:g} a" if lifetime else "-",
                origin=_esc(origin),
                flags=_esc("; ".join(flags)),
            )
        )
    return (
        "<section><h2>1 - Input audit</h2>"
        '<p class="sub">Every priced fact with its resolved unit price and origin. '
        "Wiring mistakes (wrong config field, missing source) surface here first (§9.5).</p>"
        "<table><tr><th>Subject</th><th>Asset class</th><th>Size</th><th>Unit price</th>"
        "<th>Lifetime</th><th>Origin</th><th>Flags</th></tr>" + "".join(rows) + "</table>"
        + _sources_table_html(database, params)
        + "</section>"
    )


def _sources_table_html(database: CostDatabase, params: EconomicParameters) -> str:
    """The §3.10 source registry entries referenced by this evaluation's data files."""
    referenced = sorted(getattr(database.sources, "_referenced", set()))
    if not referenced:
        return ""
    rows = []
    for source_id in referenced:
        entry = database.sources.entries.get(source_id)
        if entry is None:
            continue
        url = f'<a href="{_esc(entry.url)}">link</a>' if entry.url else "-"
        rows.append([_esc(source_id), _esc(entry.citation), _esc(entry.kind), _esc(entry.retrieved), url])
    return _details(
        f"sources used ({len(rows)} registry entries, §3.10)",
        _table(["Id", "Citation", "Kind", "Retrieved", "Url"], rows),
    )


def _investment_section_html(result: LifecycleCostResult) -> str:
    """Section 2: year-0 investment build-up waterfall per subject."""
    blocks = []
    for subject, breakdown in result.component_breakdowns.items():
        year0 = [entry for entry in result.timeline.entries if entry.year == 0 and entry.subject == subject]
        if not year0:
            continue
        steps: List[Tuple[str, float, str]] = []
        for category_names, label in (
            ((CostCategory.INVESTMENT,), "Device + installation"),
            ((CostCategory.PLANNING,), "Planning"),
            ((CostCategory.REMOVAL,), "Removal of old device"),
            ((CostCategory.SUBSIDY,), "Subsidies"),
            ((CostCategory.LOAN_DISBURSEMENT,), "Loan disbursement"),
        ):
            value = sum(e.amount_in_euro.average for e in year0 if e.category in category_names)
            if value:
                index = group_of(category_names[0])
                steps.append((label, value, f"var(--g{index})"))
        if steps:
            blocks.append(f"<h3 style='font-size:13px;margin:14px 0 2px'>{_esc(subject)}</h3>")
            blocks.append(_waterfall_svg(steps, "Net year-0 outflow"))
    if not blocks:
        return ""
    table_rows = []
    for subject, breakdown in result.component_breakdowns.items():
        if breakdown.investment_gross_in_euro.maximum <= 0:
            continue
        net = breakdown.investment_gross_in_euro - breakdown.subsidies_in_euro
        table_rows.append(
            [
                _esc(subject),
                _esc(_band_str(breakdown.investment_gross_in_euro)),
                _esc(_band_str(breakdown.subsidies_in_euro)),
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
    """Every flow behind the timeline chart: (year, subject, category) with nominal band and
    discounted value — the §3.6 canonical timeline as a verification table.

    Same scoping as the chart; duplicate (year, subject, category) entries are aggregated.
    Year subtotal rows make spikes (replacements, anyway credits) attributable at a glance.
    """
    interest = result.parameters.interest_rate
    aggregated: Dict[Tuple[int, str, str], UncertainValue] = {}
    for entry in result.scoped_timeline().entries:
        key = (entry.year, entry.subject, entry.category.value)
        aggregated[key] = aggregated.get(key, UncertainValue.exact(0.0)) + entry.amount_in_euro
    rows: List[List[str]] = []
    current_year: Optional[int] = None
    year_total = UncertainValue.exact(0.0)

    def flush_year(year: Optional[int], total: UncertainValue) -> None:
        if year is not None:
            rows.append(
                [f"<b>{year}</b>", "<b>year total</b>", "", f"<b>{_esc(_band_str(total))}</b>",
                 f"<b>{_fmt(total.average / ((1 + interest) ** year))}</b>"]
            )

    for (year, subject, category), amount in sorted(
        aggregated.items(), key=lambda item: (item[0][0], item[1].average)
    ):
        if abs(amount.average) < 0.005 and abs(amount.minimum) < 0.005 and abs(amount.maximum) < 0.005:
            continue
        if year != current_year:
            flush_year(current_year, year_total)
            current_year, year_total = year, UncertainValue.exact(0.0)
        year_total = year_total + amount
        rows.append(
            [
                str(year),
                _esc(subject),
                _esc(category),
                _esc(_band_str(amount)),
                _fmt(amount.average / ((1 + interest) ** year)),
            ]
        )
    flush_year(current_year, year_total)
    return _table(["Year", "Subject", "Category", "Nominal", "Discounted (avg)"], rows)


def _timeline_section_html(matrix: EvaluationMatrix) -> str:
    """Section 3: annual cash flows + cumulative discounted cost, per perspective."""
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


def _energy_section_html(result: LifecycleCostResult, inputs: EvaluationInputs) -> str:
    """Section 4: year-1 bill decomposition per carrier with implied effective prices."""
    fraction = max(inputs.simulated_period_fraction, 1e-9)
    carriers = {determinants.carrier.value: determinants for determinants in inputs.billing}
    if not carriers:
        return ""
    rows: List[Tuple[str, UncertainValue]] = []
    detail_rows = []
    for carrier, determinants in carriers.items():
        parts_by_category: Dict[CostCategory, float] = {}
        for entry in result.scoped_timeline().entries:
            if entry.year == 1 and entry.subject in (carrier, "ELECTRICITY_FEED_IN" if carrier == "ELECTRICITY" else carrier):
                parts_by_category[entry.category] = (
                    parts_by_category.get(entry.category, 0.0) + entry.amount_in_euro.average
                )
        total_cost = sum(v for c, v in parts_by_category.items() if c != CostCategory.FEED_IN_REVENUE)
        quantity = determinants.energy_bought_in_kwh / fraction
        effective = total_cost / quantity if quantity else 0.0
        year1_band = UncertainValue.sum(
            entry.amount_in_euro
            for entry in result.scoped_timeline().entries
            if entry.year == 1 and entry.subject == carrier
        )
        rows.append((f"{carrier} ({quantity:,.0f} units/a)", year1_band))
        breakdown = ", ".join(f"{category.value}: {_fmt(value)}" for category, value in parts_by_category.items())
        detail_rows.append(
            f"<tr><td>{_esc(carrier)}</td><td>{quantity:,.0f}</td><td>{_fmt(total_cost)}</td>"
            f"<td><b>{effective:,.3f}</b></td><td>{_esc(breakdown)}</td></tr>"
        )
    return (
        "<section><h2>4 - Year-1 energy bill</h2>"
        '<p class="sub">Per carrier with the implied effective price (total / bought quantity) — '
        "the fastest unit-mix-up detector. Feed-in shows as negative.</p>"
        + _whisker_svg(rows, "EUR/a")
        + "<details><summary>decomposition table</summary><table>"
        "<tr><th>Carrier</th><th>Quantity/a</th><th>Cost year 1 [EUR]</th><th>Effective [EUR/unit]</th>"
        "<th>Components</th></tr>" + "".join(detail_rows) + "</table></details></section>"
    )


def _subsidy_composition_svg(matrix: EvaluationMatrix) -> str:
    """Per measure: net cost (blue) + subsidy amount (green) — how far the support carries."""
    result = next(
        (res for res in matrix.results.values() if any(res.subsidy_decisions)), None
    )
    if result is None:
        # No catalog decisions (e.g. the flat shim): use any perspective with subsidy flows.
        result = next(
            (
                res
                for res in matrix.results.values()
                if any(b.subsidies_in_euro.maximum > 0 for b in res.component_breakdowns.values())
            ),
            None,
        )
    if result is None:
        return ""
    rows = []
    for subject, breakdown in result.component_breakdowns.items():
        gross = breakdown.investment_gross_in_euro.average
        subsidy = breakdown.subsidies_in_euro.average
        if gross <= 0:
            continue
        rows.append((subject, gross, min(subsidy, gross)))
    if not rows or not any(subsidy for _s, _g, subsidy in rows):
        return ""
    width, row_h, left = 860, 26, 220
    height = len(rows) * row_h + 10
    peak = max(gross for _s, gross, _sub in rows)
    scale = (width - left - 150) / max(peak, 1e-9)
    parts = _svg_open(width, height)
    y = 4.0
    for subject, gross, subsidy in rows:
        net = gross - subsidy
        parts.append(_text(left - 8, y + row_h / 2 + 4, subject, 11, "end"))
        parts.append(_rect(left, y + 4, net * scale, row_h - 8, "var(--g0)",
                           f"{subject} - net cost after subsidies: {_fmt(net)} EUR", rx=2))
        if subsidy > 0:
            parts.append(_rect(left + net * scale + 1.5, y + 4, max(subsidy * scale - 1.5, 0.5), row_h - 8,
                               "var(--g3)", f"{subject} - subsidies: {_fmt(subsidy)} EUR", rx=2))
        share = subsidy / gross if gross else 0.0
        parts.append(_text(left + gross * scale + 6, y + row_h / 2 + 4,
                           f"{share:.0%} funded", 10, "start", "var(--muted)"))
        y += row_h
    parts.append("</svg>")
    return (
        '<div class="legend"><span class="chip"><span class="swatch" style="background:var(--g0)"></span>'
        'net cost</span><span class="chip"><span class="swatch" style="background:var(--g3)"></span>'
        "subsidies</span></div>" + "".join(parts)
    )


def _subsidy_awards_table(matrix: EvaluationMatrix) -> str:
    """All awards across measures: scheme, amount band, payout kind, binding caps."""
    rows = []
    seen = set()
    for result in matrix.results.values():
        for decision in result.subsidy_decisions:
            if decision.measure_subject in seen:
                continue
            seen.add(decision.measure_subject)
            for award in decision.applied:
                caps = ", ".join(slot for slot, bound in award.caps_binding_per_slot.items() if bound) or "-"
                amount = award.upfront_amount
                if award.schedule_amounts:
                    amount = UncertainValue.sum(award.schedule_amounts)
                rows.append([
                    _esc(decision.measure_subject),
                    _esc(award.scheme_id),
                    _esc(_band_str(amount)),
                    _esc(award.payout_kind.value),
                    _esc(caps),
                ])
    if not rows:
        return ""
    return _details(
        "awards table (§5.4 audit trail)",
        _table(["Measure", "Scheme", "Amount", "Payout", "Caps binding (slots)"], rows),
    )


def _subsidy_section_html(matrix: EvaluationMatrix) -> str:
    """Section 5: the cumulation solver's audit trail, rendered."""
    cards = []
    seen = set()
    for result in matrix.results.values():
        for decision in result.subsidy_decisions:
            if decision.measure_subject in seen:
                continue
            seen.add(decision.measure_subject)
            lines = [f"<h3 style='font-size:13px;margin:12px 0 4px'>{_esc(decision.measure_subject)}</h3><ul>"]
            for award in decision.applied:
                caps = [slot for slot, bound in award.caps_binding_per_slot.items() if bound]
                cap_note = f" — cap binding in {', '.join(caps)}" if caps else ""
                lines.append(
                    f"<li><span class='status PASS'>APPLIED</span> {_esc(award.scheme_id)}: "
                    f"{_esc(_band_str(award.upfront_amount))}{_esc(cap_note)}</li>"
                )
            for reject in decision.rejected:
                lines.append(
                    f"<li><span class='status FAIL'>REJECTED</span> {_esc(reject['scheme_id'])}: "
                    f"{_esc(reject['reason'])}</li>"
                )
            for item in decision.undetermined:
                lines.append(
                    f"<li><span class='status WARN'>OPEN</span> {_esc(item['scheme_id'])}: "
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


def _perspective_section_html(matrix: EvaluationMatrix) -> str:
    """Section 6: equivalent annual cost across perspectives, with bands and the result table."""
    rows = [(perspective_id, result.equivalent_annual_cost_in_euro) for perspective_id, result in matrix.results.items()]
    any_sunk = any(result.sunk_cost_written_off_in_euro.maximum > 0 for result in matrix.results.values())
    headers = ["Perspective", "NPV", "Equivalent annual cost", "Monthly (year 1)", "LCOH"]
    if any_sunk:
        headers.append("Sunk cost (info)")
    table_rows = []
    for perspective_id, result in matrix.results.items():
        row = [
            _esc(perspective_id),
            _esc(_band_str(result.total_npv_in_euro)),
            _esc(_band_str(result.equivalent_annual_cost_in_euro, "EUR/a")),
            _esc(_band_str(result.monthly_cost_year1_in_euro, "EUR/mo")),
            _esc(_band_str(result.levelized_cost_of_heat_in_euro_per_kwh, "EUR/kWh")),
        ]
        if any_sunk:
            row.append(_esc(_band_str(result.sunk_cost_written_off_in_euro)))
        table_rows.append(row)
    return (
        "<section><h2>6 - Perspectives at a glance</h2>"
        '<p class="sub">Equivalent annual cost with min/avg/max whiskers. Sanity: gross &#8805; net; '
        "operating &#8804; brownfield; macroeconomic differs only by transfers + CO2 damage.</p>"
        + _whisker_svg(rows, "EUR/a")
        + _table(headers, table_rows)
        + "</section>"
    )


def _actor_section_html(matrix: EvaluationMatrix) -> str:
    """Section 6b: who pays what — payer NPVs per allocated perspective (§6.5)."""
    from hisim.economics.timeline import Actor

    blocks = []
    for perspective_id, result in matrix.results.items():
        payers = {payer: band for payer, band in result.npv_by_payer.items() if payer != Actor.SYSTEM}
        if len(payers) < 2 and Actor.SYSTEM in result.npv_by_payer:
            continue  # unallocated (system-scope) perspective
        rows = [(payer.value, band) for payer, band in payers.items()]
        if not rows:
            continue
        system_total = UncertainValue.sum(result.npv_by_payer.values())
        # Payer x display-group table: which cost blocks land with whom.
        payer_categories: Dict[str, Dict[int, UncertainValue]] = {}
        for entry in result.timeline.entries:
            if entry.payer == Actor.SYSTEM:
                continue
            bucket = payer_categories.setdefault(entry.payer.value, {})
            index = group_of(entry.category)
            discounted = entry.amount_in_euro.scale(
                1.0 / ((1.0 + result.parameters.interest_rate) ** entry.year)
            )
            bucket[index] = bucket.get(index, UncertainValue.exact(0.0)) + discounted
        group_indices = sorted({index for bucket in payer_categories.values() for index in bucket})
        table_rows = []
        for payer_name, bucket in payer_categories.items():
            table_rows.append(
                [f"<b>{_esc(payer_name)}</b>"]
                + [_esc(_band_str(bucket[index])) if index in bucket else "-" for index in group_indices]
            )
        payer_table = _details(
            "payer x cost-group table (NPV)",
            _table(["Payer"] + [DISPLAY_GROUPS[index][0] for index in group_indices], table_rows),
        )
        blocks.append(
            f"<details open><summary><b>{_esc(perspective_id)}</b> — payer NPVs sum to the system NPV "
            f"({_esc(_band_str(system_total))}, zero-sum invariant §6.5)</summary>"
            + _whisker_svg(rows, "EUR") + payer_table + "</details>"
        )
    if not blocks:
        return ""
    return (
        "<section><h2>6b - Who pays what (actor split)</h2>"
        '<p class="sub">Landlord/tenant allocation per the DE_2024 ruleset: tenant pays energy and '
        "apportionable operation plus the modernization levy; landlord pays investment minus "
        "subsidies and receives the levy. Negative = net gain.</p>" + "".join(blocks) + "</section>"
    )


def _tornado_svg(rows: List[Tuple[str, float]], base_value: float) -> str:
    """Diverging bars: per-scenario swing of the headline KPI vs. the base scenario."""
    if not rows:
        return ""
    width, row_h, left = 860, 28, 250
    height = len(rows) * row_h + 26
    span = max(max(abs(swing) for _label, swing in rows), 1e-9)
    center = left + (width - left - 120) / 2.0
    scale = (width - left - 120) / 2.0 / span
    parts = _svg_open(width, height)
    parts.append(f'<line x1="{center}" y1="4" x2="{center}" y2="{height - 20}" stroke="var(--baseline)"/>')
    y = 4.0
    for label, swing in sorted(rows, key=lambda item: -abs(item[1])):
        mid = y + row_h / 2
        color = "var(--g5)" if swing > 0 else "var(--g1)"
        x_from = center if swing >= 0 else center + swing * scale
        parts.append(_text(left - 8, mid + 4, label, 11, "end"))
        parts.append(_rect(x_from, y + 5, abs(swing) * scale, row_h - 10, color,
                           f"{label}: {'+' if swing >= 0 else ''}{_fmt(swing)} EUR/a vs base", rx=3))
        anchor_x = center + swing * scale + (6 if swing >= 0 else -6)
        parts.append(_text(anchor_x, mid + 4, f"{'+' if swing >= 0 else ''}{_fmt(swing)}", 10,
                           "start" if swing >= 0 else "end", "var(--muted)"))
        y += row_h
    parts.append(_text(center, height - 6, f"base: {_fmt(base_value)} EUR/a", 10, "middle", "var(--muted)"))
    parts.append("</svg>")
    return "".join(parts)


def _scenario_section_html(scenario_cube, matrix: EvaluationMatrix) -> str:
    """Section 9: scenario analysis — tornado of the headline KPI plus the full table (§4.6)."""
    if scenario_cube is None or not scenario_cube.results:
        return ""
    perspective_id = next(iter(matrix.results.keys()))
    per_scenario = scenario_cube.results.get(perspective_id)
    if not per_scenario or scenario_cube.base_id not in per_scenario:
        return ""
    base = per_scenario[scenario_cube.base_id]
    base_value = base.equivalent_annual_cost_in_euro.average
    rows = [
        (scenario_id, result.equivalent_annual_cost_in_euro.average - base_value)
        for scenario_id, result in per_scenario.items()
        if scenario_id != scenario_cube.base_id
    ]
    table_rows = "".join(
        f"<tr><td>{_esc(scenario_id)}</td>"
        f"<td>{_esc(_band_str(result.total_npv_in_euro))}</td>"
        f"<td>{_esc(_band_str(result.equivalent_annual_cost_in_euro, 'EUR/a'))}</td>"
        f"<td>{result.equivalent_annual_cost_in_euro.average - base_value:+,.0f}</td></tr>"
        for scenario_id, result in per_scenario.items()
    )
    # Robustness summary (§4.6): min/max/spread of the headline KPI per perspective.
    robustness_rows = []
    for pid, results_by_scenario in scenario_cube.results.items():
        values = [res.equivalent_annual_cost_in_euro.average for res in results_by_scenario.values()]
        robustness_rows.append(
            [_esc(pid), f"{min(values):,.0f}", f"{max(values):,.0f}", f"{max(values) - min(values):,.0f}"]
        )
    robustness = _details(
        "robustness summary across scenarios (EAC [EUR/a], AVERAGE slot)",
        _table(["Perspective", "Min", "Max", "Spread"], robustness_rows),
    )
    return (
        f"<section><h2>9 - Scenario analysis ({_esc(perspective_id)})</h2>"
        '<p class="sub">Equivalent annual cost swing per economic scenario vs. the base assumptions '
        "(red = more expensive, aqua = cheaper). Scenario axes vary rates and datapoints; the "
        "min/avg/max bands vary the cost data within each scenario — two orthogonal uncertainty "
        "mechanisms (§4.6). Full cube: scenario_cube.csv / scenario_cube.json.</p>"
        + _tornado_svg(rows, base_value)
        + "<details open><summary>all scenarios</summary><table>"
        "<tr><th>Scenario</th><th>NPV</th><th>Equivalent annual cost</th><th>Swing [EUR/a]</th></tr>"
        + table_rows + "</table></details>" + robustness + "</section>"
    )


def _kpi_section_html(matrix: EvaluationMatrix) -> str:
    """Section 10: the namespaced lifecycle KPI set (§7.3) as a table with bands."""
    from hisim.economics.exports import build_lifecycle_kpi_entries

    entries = build_lifecycle_kpi_entries(matrix)
    if not entries:
        return ""
    rows = []
    for entry in entries:
        value = f"{entry.value:,.2f}" if isinstance(entry.value, (int, float)) else _esc(str(entry.value))
        band = (
            f"{entry.value_min:,.2f} | {entry.value_max:,.2f}"
            if entry.value_min is not None and entry.value_max is not None
            else "-"
        )
        rows.append([_esc(entry.name), value, band, _esc(entry.unit)])
    return (
        "<section><h2>10 - Lifecycle KPIs</h2>"
        '<p class="sub">The namespaced KPI set (§7.3) as published to lifecycle_kpis.json; '
        "`value` is the AVERAGE slot, the band column is min | max.</p>"
        + _table(["KPI", "Value", "Band (min | max)", "Unit"], rows)
        + "</section>"
    )


def _components_section_html(matrix: EvaluationMatrix) -> str:
    """Section 7: per-subject stacked NPV bars per perspective (§7.4)."""
    blocks = []
    for index, (perspective_id, result) in enumerate(matrix.results.items()):
        groups_present = sorted(
            {group_of(category) for b in result.component_breakdowns.values() for category in b.npv_by_category}
        )
        subject_rows = [
            [
                _esc(subject),
                _esc(_band_str(breakdown.total_npv_in_euro)),
                _esc(_band_str(breakdown.equivalent_annual_cost_in_euro, "EUR/a")),
                _esc(_band_str(breakdown.investment_gross_in_euro)),
                _esc(_band_str(breakdown.subsidies_in_euro)),
                f"{breakdown.lifecycle_co2_in_kg:,.0f}",
            ]
            for subject, breakdown in result.component_breakdowns.items()
        ]
        subject_table = _details(
            "subject table",
            _table(
                ["Subject", "NPV", "Equivalent annual cost", "Year-0 investment", "Subsidies", "Lifecycle CO2 [kg]"],
                subject_rows,
            ),
        )
        open_attr = " open" if index == 0 else ""
        blocks.append(
            f"<details{open_attr}><summary><b>{_esc(perspective_id)}</b></summary>"
            + _legend_html(groups_present) + _stacked_subject_svg(result) + subject_table + "</details>"
        )
    return (
        "<section><h2>7 - Per-component breakdown</h2>"
        '<p class="sub">Diverging stacks per subject: costs right of the zero line, credits '
        "(residual value, subsidies, feed-in, anyway credit) left — never added onto the cost "
        "side. The whisker + dot mark the net NPV band; net = costs - credits reconciles with "
        "the headline by construction (§7.4).</p>"
        + "".join(blocks) + "</section>"
    )


def _checks_section_html(checks: List[PlausibilityCheck]) -> str:
    """Section 0: the plausibility panel."""
    rows = "".join(
        f"<tr><td><span class='status {check.status}'>{check.status}</span></td>"
        f"<td>{_esc(check.name)}</td><td>{_esc(check.value)}</td><td>{_esc(check.expected)}</td>"
        f"<td>{_esc(check.detail)}</td></tr>"
        for check in checks
    )
    n_bad = sum(1 for check in checks if check.status != "PASS")
    headline = "all checks passed" if n_bad == 0 else f"{n_bad} check(s) need a look"
    return (
        f"<section><h2>0 - Plausibility panel — {headline}</h2>"
        '<p class="sub">Automated ratio and invariant checks (thresholds: '
        "cost_database/plausibility_checks.json). WARN = outside the generous range, FAIL = structural.</p>"
        f"<table><tr><th></th><th>Check</th><th>Value</th><th>Expected</th><th>Note</th></tr>{rows}</table></section>"
    )


def _comparison_section_html(
    reference: LifecycleCostResult, variant: LifecycleCostResult, comparison: VariantComparison
) -> str:
    """Section 8/D: delta waterfall by subject + discounted payback curve."""
    steps: List[Tuple[str, float, str]] = []
    for subject, delta in sorted(comparison.npv_delta_by_subject.items(), key=lambda item: item[1].average):
        if abs(delta.average) < 0.005:
            continue
        color = "var(--g5)" if delta.average > 0 else "var(--g1)"
        steps.append((subject, delta.average, color))
    payback = comparison.discounted_payback_years
    payback_text = (
        f"best case {payback.get('low')} a, expected {payback.get('average')} a, "
        f"worst case {payback.get('high')} a (None = never within the horizon)"
    )
    warm_rent = ""
    if comparison.warm_rent_change_per_month_in_euro is not None:
        warm_rent = (
            f"<p>Warm rent change: <b>{_esc(_band_str(comparison.warm_rent_change_per_month_in_euro, 'EUR/month'))}</b>"
            f" — neutral per slot: {_esc(str(comparison.warm_rent_neutral_per_slot))}</p>"
        )
    delta_rows = [
        [_esc(subject), _esc(_band_str(delta))]
        for subject, delta in sorted(comparison.npv_delta_by_subject.items(), key=lambda item: item[1].average)
    ]
    return (
        f"<section><h2>8 - Variant comparison ({_esc(comparison.perspective_id)})</h2>"
        '<p class="sub">NPV delta by subject (variant - reference; red adds cost, aqua saves) and the '
        "cumulative discounted savings whose zero-crossing is the payback.</p>"
        + _waterfall_svg(steps, "Net NPV delta")
        + _details("delta table (best-case | expected | worst-case, §3.9 envelope)",
                   _table(["Subject", "NPV delta"], delta_rows))
        + f"<p class='sub'>Discounted payback: {_esc(payback_text)}</p>"
        + _payback_svg(reference, variant)
        + warm_rent
        + "</section>"
    )


def build_lifecycle_report_html(
    matrix: EvaluationMatrix,
    inputs: EvaluationInputs,
    database: CostDatabase,
    checks: List[PlausibilityCheck],
    comparison: Optional[VariantComparison] = None,
    reference_result: Optional[LifecycleCostResult] = None,
    scenario_cube=None,
) -> str:
    """The self-contained HTML report, sections along the calculation chain."""
    reference = next(iter(matrix.results.values()))
    params = reference.parameters
    header = (
        f"<h1>Lifecycle cost report</h1><p class='sub'>Simulation year {inputs.simulation_year}, "
        f"country {_esc(params.country)}, horizon {params.observation_period_in_years} a, "
        f"interest {params.interest_rate:.1%}, price basis {params.price_basis_year}, "
        f"CO2 scenario &#39;{_esc(params.co2_price_scenario)}&#39;. All money as avg [min | max] "
        f"envelope bands (§3.9). Generated {datetime.date.today().isoformat()}.</p>"
    )
    if all_bands_degenerate(matrix):
        header += (
            "<section style='border-left:4px solid var(--warning)'><b>No uncertainty bands in "
            f"this run.</b> <span class='sub'>{_esc(_degenerate_note(matrix, inputs))}</span></section>"
        )
    sections = [
        _checks_section_html(checks),
        _audit_section_html(inputs, database, params),
        _investment_section_html(reference),
        _timeline_section_html(matrix),
        _energy_section_html(reference, inputs),
        _co2_section_html(matrix),
        _subsidy_section_html(matrix),
        _perspective_section_html(matrix),
        _actor_section_html(matrix),
        _components_section_html(matrix),
        _scenario_section_html(scenario_cube, matrix),
        _kpi_section_html(matrix),
    ]
    if comparison is not None and reference_result is not None:
        variant_result = matrix.results.get(comparison.perspective_id, reference)
        sections.append(_comparison_section_html(reference_result, variant_result, comparison))
    footer = (
        "<footer>Every number is traceable: "
        "<code>python -m hisim.economics explain &lt;results_dir&gt; --value "
        f"\"{_esc(reference.perspective_id)}/total_npv_in_euro\"</code> — hisim.economics</footer>"
    )
    return (
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>Lifecycle cost report</title><style>{_REPORT_CSS}</style></head>"
        f"<body><main>{header}{''.join(sections)}{footer}</main></body></html>"
    )


def write_lifecycle_report(
    matrix: EvaluationMatrix,
    inputs: EvaluationInputs,
    database: CostDatabase,
    checks: List[PlausibilityCheck],
    result_directory: str,
    comparison: Optional[VariantComparison] = None,
    reference_result: Optional[LifecycleCostResult] = None,
    file_name: str = LIFECYCLE_REPORT_FILE_NAME,
    scenario_cube=None,
) -> str:
    """Writes the HTML report."""
    path = os.path.join(result_directory, file_name)
    with open(path, "w", encoding="utf-8") as file:
        file.write(
            build_lifecycle_report_html(
                matrix, inputs, database, checks, comparison, reference_result, scenario_cube
            )
        )
    return path
