"""Automated plausibility checks on an evaluated result, as typed numbers (cost_spec.md §2.4).

A check either FAILs (a structural invariant is broken, e.g. subject NPVs do not sum to the total) or WARNs (a
magnitude left a generous range, which usually means a unit mix-up). Nothing here raises or suppresses output: the
report renders the findings as its first panel and `bridge.py` logs every non-PASS finding. The thresholds are data in
`cost_database/plausibility_checks.json`; formatting belongs to the report writers.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from hisim.economics.database import CostDatabase
from hisim.economics.results import EvaluationMatrix, HeatCostNaming, LifecycleCostResult
from hisim.economics.timeline import CategoryRules, CostCategory
from hisim.economics.uncertainty import UncertainValue


class CheckStatus:
    """Status strings a finding can carry: PASS, WARN or FAIL.

    FAIL marks a broken structural invariant, WARN a magnitude outside a reviewable range. The values are written
    verbatim into JSON, the rendered panel and the CSS class of the HTML status cell, so their spelling is part of the
    output.
    """

    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


class CheckIds:
    """Stable ids, one per check kind, that renderers and machine readers switch on.

    A finding's `name` is prose and may change; the id does not, so renaming an id breaks readers of the findings JSON.
    """

    CHECK_RESULTS_PRESENT = "results_present"
    CHECK_SUBJECTS_SUM_TO_TOTAL = "subjects_sum_to_total"
    CHECK_BAND_ORDERING = "band_ordering"
    CHECK_RESIDUAL_BELOW_PURCHASES = "residual_value_below_purchases"
    CHECK_SUBSIDIES_BELOW_BASIS = "subsidies_below_basis"
    CHECK_EFFECTIVE_PRICE = "effective_price"
    CHECK_EAC_PER_M2 = "equivalent_annual_cost_per_m2"
    # The check reads "system cost per unit of heat" (`results.HeatCostNaming`); the id keeps
    # its spelling because ids are a contract and names are prose.
    CHECK_LEVELIZED_COST_OF_HEAT = "levelized_cost_of_heat"
    CHECK_MAINTENANCE_RATIO = "maintenance_to_investment_ratio"
    CHECK_BAND_WIDTH = "band_width"
    CHECK_FLEXIBILITY_VALUE = "flexibility_value_sign"
    CHECK_SIMULATED_PERIOD = "simulated_period_extrapolated"
    CHECK_USEFUL_HEAT_WITHOUT_HOT_WATER = "useful_heat_without_hot_water"


class PlausibilityCategories:
    """The category groupings the checks compute over.

    `BILL_CATEGORIES` is the kernel's `timeline.CategoryRules.BILL_CATEGORIES`, so the effective-price check and report
    section 4 judge the same bill.
    """

    #: The categories that make up an energy carrier's bill (§8), the numerator of the effective
    #: price check. The kernel's set itself; `views.ViewCategories` binds the same object.
    BILL_CATEGORIES = CategoryRules.BILL_CATEGORIES


@dataclass
class PlausibilityConfig:
    """Thresholds of the checks, loaded from `cost_database/plausibility_checks.json`.

    Ranges are inclusive `(low, high)` pairs in the unit of the figure they bound; `reconciliation_tolerance` is a
    relative tolerance instead. The field defaults apply only when no thresholds file exists; a value in the file
    always wins.
    """

    #: carrier id -> plausible year-1 effective price in EUR/kWh, for every carrier.
    #: Carriers absent from the map are not checked.
    effective_price_ranges: Dict[str, Tuple[float, float]] = field(default_factory=dict)
    eac_per_m2_range: Tuple[float, float] = (5.0, 80.0)  # EUR per m2 of reference area per year
    lcoh_range: Tuple[float, float] = (0.05, 0.50)  # EUR per kWh of delivered heat
    maintenance_ratio_range: Tuple[float, float] = (0.02, 0.80)  # dimensionless NPV ratio
    band_width_warn: float = 3.5  # max/min of the NPV band; a factor, not a percentage
    #: Relative tolerance of the subjects-sum-to-total invariant, scaled by the total NPV.
    reconciliation_tolerance: float = 1e-6

    @classmethod
    def load(cls, base_path: Optional[str] = None) -> "PlausibilityConfig":
        """Load the thresholds file, falling back to the field defaults when it is missing.

        A missing file is not an error. Non-list values in `effective_price_ranges` (such as a `"comment"` string) are
        skipped.

        Args:
            base_path: Directory holding `plausibility_checks.json`; defaults to the shipped `cost_database/` directory
                (`CostDatabase.DEFAULT_PATH`).

        Returns:
            A fully populated config.
        """
        path = os.path.join(base_path or CostDatabase.DEFAULT_PATH, "plausibility_checks.json")
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


@dataclass(frozen=True)
class PlausibilityFinding:
    """One check outcome as unformatted data: a value, its unit and the range it was judged against.

    `context` holds further numbers the check looked at, keyed by role, so a renderer can write "4,800 vs 16,000 EUR"
    without recomputing. Which keys exist depends on `check_id` and is documented where the check is produced.
    """

    check_id: str
    #: Human-readable label including the scope it applies to ("... (greenfield_net)").
    name: str
    status: str
    value: Optional[float] = None
    unit: str = ""
    #: Inclusive bounds of a range check; None for structural checks.
    bounds: Optional[Tuple[float, float]] = None
    #: Further numbers the check compared, keyed by role.
    context: Dict[str, float] = field(default_factory=dict)
    #: The band a band-ordering check rejected (that check has no single value).
    band: Optional[UncertainValue] = None

    def to_json(self) -> dict:
        """Serialize every field, including those that are None, so all findings share one record shape."""
        return {
            "check_id": self.check_id,
            "name": self.name,
            "status": self.status,
            "value": self.value,
            "unit": self.unit,
            "bounds": list(self.bounds) if self.bounds else None,
            "context": dict(self.context),
            "band": self.band.to_json() if self.band else None,
        }


@dataclass
class PlausibilityReport:
    """All findings of one evaluation, in panel order.

    The order is the panel's order and is pinned by the golden reports. Iterating the report iterates its findings.
    """

    findings: List[PlausibilityFinding] = field(default_factory=list)

    def __iter__(self):
        """Iterate the findings."""
        return iter(self.findings)

    def __len__(self) -> int:
        """Return the number of findings."""
        return len(self.findings)

    def flagged(self) -> List[PlausibilityFinding]:
        """Return every finding that is not a PASS."""
        return [finding for finding in self.findings if finding.status != CheckStatus.PASS]

    def ok(self) -> bool:
        """Return True when no finding is flagged."""
        return not self.flagged()

    def to_json(self) -> dict:
        """Serialize as `{"findings": [...]}`; an object, so top-level keys can be added later."""
        return {"findings": [finding.to_json() for finding in self.findings]}


def _range_finding(
    check_id: str,
    name: str,
    value: float,
    bounds: Tuple[float, float],
    unit: str,
    context: Optional[Dict[str, float]] = None,
) -> PlausibilityFinding:
    """Build a magnitude finding: PASS inside the inclusive bounds, WARN outside.

    Every range check uses this, so a magnitude check can never FAIL.
    """
    low, high = bounds
    return PlausibilityFinding(
        check_id=check_id,
        name=name,
        status=CheckStatus.PASS if low <= value <= high else CheckStatus.WARN,
        value=value,
        unit=unit,
        bounds=(low, high),
        context=context or {},
    )


def _npv_of(result: LifecycleCostResult, *categories: CostCategory) -> float:
    """Return the best-estimate NPV of the given categories, or 0.0 when the result has none of them.

    Only the best-estimate slot is used; the minimum and maximum slots are deliberate extremes that would trip generous
    ranges (§3.9).
    """
    return sum(
        result.npv_by_category[category].best_estimate
        for category in categories
        if category in result.npv_by_category
    )


def _structural_findings(
    matrix: EvaluationMatrix, config: PlausibilityConfig
) -> List[PlausibilityFinding]:
    """Check the hard invariants on every perspective; any violation is a FAIL (§7.4, §3.9, §5.4).

    The four checks: subject NPVs sum to the perspective total within a relative tolerance; the total NPV band is
    ordered minimum <= best estimate <= maximum (a finding only when violated); the residual value does not exceed what
    was purchased; support does not exceed its eligible cost basis. The last two compare magnitudes (credits are
    negative), allow `1e-9` relative slack for float error, and are emitted only when both sides are non-zero.

    All reconciliation and band findings come before all residual and subsidy findings, which is why `matrix.results`
    is walked twice.
    """
    findings: List[PlausibilityFinding] = []
    for perspective_id, result in matrix.results.items():
        subject_sum = UncertainValue.sum(result.npv_by_component.values())
        delta = abs(subject_sum.best_estimate - result.total_npv_in_euro.best_estimate)
        tolerance = config.reconciliation_tolerance * max(1.0, abs(result.total_npv_in_euro.best_estimate))
        findings.append(
            PlausibilityFinding(
                check_id=CheckIds.CHECK_SUBJECTS_SUM_TO_TOTAL,
                name=f"subjects sum to total ({perspective_id})",
                status=CheckStatus.PASS if delta <= tolerance else CheckStatus.FAIL,
                value=delta,
                unit="EUR",
                context={"tolerance": tolerance},
            )
        )
        band = result.total_npv_in_euro
        if not band.minimum <= band.best_estimate <= band.maximum:
            findings.append(
                PlausibilityFinding(
                    check_id=CheckIds.CHECK_BAND_ORDERING,
                    name=f"band ordering ({perspective_id})",
                    status=CheckStatus.FAIL,
                    band=band,
                )
            )

    for perspective_id, result in matrix.results.items():
        residual = abs(_npv_of(result, CostCategory.RESIDUAL_VALUE))
        purchases = _npv_of(result, CostCategory.INVESTMENT, CostCategory.REPLACEMENT)
        if residual and purchases:
            findings.append(
                PlausibilityFinding(
                    check_id=CheckIds.CHECK_RESIDUAL_BELOW_PURCHASES,
                    name=f"residual value <= purchases ({perspective_id})",
                    status=CheckStatus.PASS if residual <= purchases * (1 + 1e-9) else CheckStatus.FAIL,
                    value=residual,
                    unit="EUR",
                    context={"purchases": purchases},
                )
            )
        subsidies = abs(_npv_of(result, CostCategory.SUBSIDY))
        basis = _npv_of(result, CostCategory.INVESTMENT, CostCategory.PLANNING, CostCategory.REMOVAL)
        if subsidies and basis:
            findings.append(
                PlausibilityFinding(
                    check_id=CheckIds.CHECK_SUBSIDIES_BELOW_BASIS,
                    name=f"subsidies <= eligible basis ({perspective_id})",
                    status=CheckStatus.PASS if subsidies <= basis * (1 + 1e-9) else CheckStatus.FAIL,
                    value=subsidies,
                    unit="EUR",
                    context={"basis": basis},
                )
            )
    return findings


def _effective_price_findings(
    reference: LifecycleCostResult, config: PlausibilityConfig
) -> List[PlausibilityFinding]:
    """Check each carrier's year-1 effective price, its year-1 bill divided by the kWh bought, against its range.

    A price orders of magnitude off reveals a Wh/kWh or ct/EUR confusion anywhere between meter and tariff. The bill is
    the four `BILL_CATEGORIES` (feed-in revenue excluded). The quotient is EUR/kWh for every carrier, pellets and oil
    included, because per-ton and per-litre quotes are converted when prices are resolved. A carrier is skipped when it
    has no range, nothing was bought or its year-1 bill is zero.

    Context keys: `year1_cost` (the bill) and `quantity` (kWh bought).
    """
    findings: List[PlausibilityFinding] = []
    for carrier, quantities in reference.annual_energy_quantities_by_carrier.items():
        bounds = config.effective_price_ranges.get(carrier)
        quantity = quantities.bought_in_kwh
        if bounds is None or quantity <= 0:
            continue
        year1 = UncertainValue.sum(
            entry.amount_in_euro
            for entry in reference.scoped_timeline().entries
            if entry.year == 1 and entry.subject == carrier and entry.category in PlausibilityCategories.BILL_CATEGORIES
        )
        if year1.best_estimate == 0:
            continue
        findings.append(
            _range_finding(
                CheckIds.CHECK_EFFECTIVE_PRICE,
                f"effective {carrier} price (year 1)",
                year1.best_estimate / quantity,
                bounds,
                "EUR/kWh",
                context={"year1_cost": year1.best_estimate, "quantity": quantity},
            )
        )
    return findings


def _flexibility_value_findings(reference: LifecycleCostResult) -> List[PlausibilityFinding]:
    """Warn for each dynamic-tariff carrier whose consumption timing cost more than a flat profile (§8.5).

    The flexibility value is what consumption timing was worth against a flat profile at the mean spot price. A
    negative value means the load sat on expensive hours, e.g. a controller following the wrong signal. The headline
    figures use the value clamped to zero; this check reports the raw one.

    Args:
        reference: The reference perspective's result, read for its per-carrier raw values.

    Returns:
        One WARN per carrier with a negative flexibility value, in carrier order; usually empty. Context key
            `clamped_to` holds the 0.0 actually used.
    """
    findings: List[PlausibilityFinding] = []
    for carrier, value in reference.raw_flexibility_value_by_carrier.items():
        if value >= 0:
            continue
        findings.append(
            PlausibilityFinding(
                check_id=CheckIds.CHECK_FLEXIBILITY_VALUE,
                name=f"{carrier} flexibility value is negative (load timed worse than flat)",
                status=CheckStatus.WARN,
                value=value,
                unit="EUR",
                context={"clamped_to": 0.0},
            )
        )
    return findings


def _magnitude_findings(
    reference: LifecycleCostResult, config: PlausibilityConfig
) -> List[PlausibilityFinding]:
    """Run the advisory range checks on the reference (first) perspective; each yields at most a WARN.

    In panel order: each carrier's effective price, the equivalent annual cost per m², the system cost per unit of
    heat, the maintenance-to-investment ratio (a huge ratio means an absolute fee stored as a rate), the band width (a
    very wide band usually means a typo in a data file), and the flexibility value. A check is skipped when its inputs
    are missing; the band-width check needs a strictly positive minimum.
    """
    findings = _effective_price_findings(reference, config)
    area = reference.reference_areas.preferred()
    if area:
        findings.append(
            _range_finding(
                CheckIds.CHECK_EAC_PER_M2,
                f"equivalent annual cost per m2 ({reference.perspective_id})",
                reference.equivalent_annual_cost_in_euro.best_estimate / area,
                config.eac_per_m2_range,
                "EUR/m2a",
                context={"area_in_m2": area},
            )
        )
    if reference.levelized_cost_of_heat_in_euro_per_kwh is not None:
        findings.append(
            _range_finding(
                CheckIds.CHECK_LEVELIZED_COST_OF_HEAT,
                HeatCostNaming.CHECK_LABEL,
                reference.levelized_cost_of_heat_in_euro_per_kwh.best_estimate,
                config.lcoh_range,
                "EUR/kWh",
            )
        )
    maintenance = _npv_of(reference, CostCategory.MAINTENANCE, CostCategory.FIXED_OPERATION)
    investment = _npv_of(reference, CostCategory.INVESTMENT, CostCategory.REPLACEMENT)
    if maintenance and investment:
        findings.append(
            _range_finding(
                CheckIds.CHECK_MAINTENANCE_RATIO,
                f"maintenance / investment NPV ratio ({reference.perspective_id})",
                maintenance / investment,
                config.maintenance_ratio_range,
                "",
                context={"maintenance_npv": maintenance, "investment_npv": investment},
            )
        )
    band = reference.total_npv_in_euro
    if band.minimum > 0:
        findings.append(
            _range_finding(
                CheckIds.CHECK_BAND_WIDTH,
                f"uncertainty band width max/min ({reference.perspective_id})",
                band.maximum / band.minimum,
                (1.0, config.band_width_warn),
                "x",
                context={"horizon_in_years": float(reference.parameters.observation_period_in_years)},
            )
        )
    findings.extend(_flexibility_value_findings(reference))
    return findings


def _extrapolation_findings(simulated_period_fraction: Optional[float]) -> List[PlausibilityFinding]:
    """Warn when results were extrapolated from less than a simulated year (§8.5).

    A shorter run is annualized by dividing by the simulated fraction, so a one-day run multiplies everything by 365;
    the figures look like a full year's but are not. The finding's value is the fraction, and `context` carries the
    extrapolation factor.

    Args:
        simulated_period_fraction: Share of a year the simulation covered, or None when unknown (stored results, golden
            fixtures).

    Returns:
        One WARN for a partial year; nothing for a full year or an unknown fraction.
    """
    if simulated_period_fraction is None or simulated_period_fraction >= 1.0:
        return []
    fraction = max(simulated_period_fraction, 1e-12)
    return [
        PlausibilityFinding(
            check_id=CheckIds.CHECK_SIMULATED_PERIOD,
            name="simulated period covers a full year",
            status=CheckStatus.WARN,
            value=simulated_period_fraction,
            unit="of a year",
            bounds=(1.0, 1.0),
            context={"extrapolation_factor": 1.0 / fraction},
        )
    ]


def _heat_without_hot_water_findings(heat_without_hot_water_in_kwh: Optional[float]) -> List[PlausibilityFinding]:
    """Warn when the heat-cost figure divides by room heat only, without hot water.

    The system cost per unit of heat divides by the measured useful heat, rooms plus hot water
    (`adapter.UsefulHeatSources`). If the building's hot-water source is not one the table lists, only room heat is
    measured while the costs still pay for hot water, so the figure reads too high.

    Args:
        heat_without_hot_water_in_kwh: The annual room-only heat the figure divides by, or None when the denominator is
            complete or unknown.

    Returns:
        One WARN carrying the heat as its value, or nothing.
    """
    if heat_without_hot_water_in_kwh is None:
        return []
    return [
        PlausibilityFinding(
            check_id=CheckIds.CHECK_USEFUL_HEAT_WITHOUT_HOT_WATER,
            name="heat-cost denominator includes hot water",
            status=CheckStatus.WARN,
            value=heat_without_hot_water_in_kwh,
            unit="kWh/a",
        )
    ]


def run_plausibility_checks(
    matrix: EvaluationMatrix,
    config: Optional[PlausibilityConfig] = None,
    simulated_period_fraction: Optional[float] = None,
    heat_without_hot_water_in_kwh: Optional[float] = None,
) -> PlausibilityReport:
    """Run the plausibility panel: structural invariants on every perspective, magnitude ranges on the first.

    Called by `bridge.py` after a simulation, by the `report` CLI on stored results and by the golden tests;
    `reporting.summary.render_plausibility_findings` renders the result. It never raises: an empty matrix yields a
    single FAIL finding.

    Args:
        matrix: The evaluated perspectives. The first is the reference for the magnitude checks, and the order is the
            order findings are emitted in.
        config: Thresholds; loaded from `cost_database/plausibility_checks.json` when omitted.
        simulated_period_fraction: Share of a year the run covered, if known; adds the §8.5 extrapolation warning.
        heat_without_hot_water_in_kwh: Annual heat the heat-cost figure divides by when it omits hot water
            (`EvaluationInputs.heat_cost_omits_hot_water`); adds a WARN saying so.

    Returns:
        The findings, structural first, then magnitude. `report.ok()` is True when every check passed.
    """
    config = config or PlausibilityConfig.load()
    if not matrix.results:
        return PlausibilityReport(
            [
                PlausibilityFinding(
                    check_id=CheckIds.CHECK_RESULTS_PRESENT,
                    name="results present",
                    status=CheckStatus.FAIL,
                    value=0.0,
                    unit="perspectives",
                    bounds=(1.0, float("inf")),
                )
            ]
        )
    reference = next(iter(matrix.results.values()))
    findings = _extrapolation_findings(simulated_period_fraction)
    findings.extend(_heat_without_hot_water_findings(heat_without_hot_water_in_kwh))
    findings.extend(_structural_findings(matrix, config))
    findings.extend(_magnitude_findings(reference, config))
    return PlausibilityReport(findings)
