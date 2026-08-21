"""Automated plausibility checks on an evaluated result (cost-spec-v2 §2.4, W4.2).

Engine side of the results/presentation seam: the checks are arithmetic — reconciliation
deltas, effective prices, EUR/m²a, band-width ratios — and they used to live inside
`reporting.py`, down to pre-formatted value strings in the check records. Here they produce a
typed `PlausibilityReport` of numbers; rendering (rounding, units, table markup) belongs to the
report writers and nothing else.

Two severities, unchanged from the original panel:

* **FAIL** — a structural invariant is broken (subject NPVs must sum to the total, a band must
  be ordered, support cannot exceed its own basis). Something in the engine or the data is
  wrong.
* **WARN** — a magnitude left a deliberately generous range. Catches unit mix-ups and rates
  stored as absolute amounts, not modelling disagreements.

Thresholds are reviewable data (`cost_database/plausibility_checks.json`), not code. That is
deliberate: whether 0.48 EUR/kWh is "still plausible for electricity" is a domain judgement a
reviewer must be able to change and diff without touching Python, and the ranges are set wide
enough that a PASS says "no obvious blunder", never "this model is right".

**What a finding means for the caller.** Nothing here raises, aborts or suppresses output. The
report layer renders the panel as section 0 of `lifecycle_report.html` and as the first table of
`cost_summary.md`, and `bridge.py` additionally logs every non-PASS finding as a warning after
writing the files. So a FAIL is a signal to a human that the *engine or its data* is broken —
a reconciliation that does not close, a band whose best estimate sits outside its own bounds — and
should stop the results being used, but it will not stop them being produced; a WARN says a
figure is outside a generous magnitude range and wants a look, most often at a unit.

Every input comes off `LifecycleCostResult`: the annualized energy quantities and reference
areas the range checks divide by are carried on the result itself, so the checks no longer
need `EvaluationInputs` (W4.2).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from hisim.economics.database import CostDatabase
from hisim.economics.results import EvaluationMatrix, LifecycleCostResult
from hisim.economics.timeline import CategoryRules, CostCategory
from hisim.economics.uncertainty import UncertainValue


class CheckStatus:
    """Statuses a finding can carry. The values land in JSON and reports.

    Three levels only, and the split between the two non-PASS ones is the module's central
    convention: FAIL is reserved for broken structural invariants (an arithmetic identity that
    must hold), WARN for a magnitude outside a reviewable range. Plain strings rather than an
    enum because they are serialized verbatim into `to_json`, into the rendered panel and into
    the CSS class of the HTML status cell, so their spelling is part of the output contract.
    """

    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


class CheckIds:
    """Stable ids, one per check kind.

    Consumers (renderers, machine readers) switch on these instead of parsing the
    human-readable name.

    The `name` of a finding carries its scope ("subjects sum to total (greenfield_net)") and is
    written for a reader, so it changes whenever perspectives or wording change; the id does not.
    `reporting.py` keys both its value formatting and its reader hints off these constants, which
    is what lets a check be re-worded without touching the renderer — and, conversely, means
    renaming one of these ids is a breaking change for anything reading the findings JSON.
    """

    CHECK_RESULTS_PRESENT = "results_present"
    CHECK_SUBJECTS_SUM_TO_TOTAL = "subjects_sum_to_total"
    CHECK_BAND_ORDERING = "band_ordering"
    CHECK_RESIDUAL_BELOW_PURCHASES = "residual_value_below_purchases"
    CHECK_SUBSIDIES_BELOW_BASIS = "subsidies_below_basis"
    CHECK_EFFECTIVE_PRICE = "effective_price"
    CHECK_EAC_PER_M2 = "equivalent_annual_cost_per_m2"
    CHECK_LEVELIZED_COST_OF_HEAT = "levelized_cost_of_heat"
    CHECK_MAINTENANCE_RATIO = "maintenance_to_investment_ratio"
    CHECK_BAND_WIDTH = "band_width"
    CHECK_FLEXIBILITY_VALUE = "flexibility_value_sign"


class PlausibilityCategories:
    """Category groupings the checks are computed over.

    Holds the one selection set the checks need, so that "what counts as an energy bill" is a
    reviewable definition rather than a filter buried in `_effective_price_findings`. The set is
    the kernel's `timeline.CategoryRules.BILL_CATEGORIES`, bound here under the name the checks
    use: the panel's effective-price check and report section 4 are the same statement about the
    same carrier, and while the two held verbatim copies nothing stopped them drifting until the
    panel validated a price the report does not show (review finding 14).
    """

    #: The categories that make up an energy carrier's bill (§8) — the numerator of the effective
    #: price check. The kernel definition, not a copy of it; `views.ViewCategories` binds the same
    #: object.
    BILL_CATEGORIES = CategoryRules.BILL_CATEGORIES


@dataclass
class PlausibilityConfig:
    """Thresholds loaded from cost_database/plausibility_checks.json (reviewable data).

    Every bound the checks below judge against, in one place, so that tuning the panel is a data
    PR rather than a code change. The field defaults are the fallback used when no thresholds
    file ships (and only then) — they are not a second source of truth, and a bound the JSON file
    sets always wins. All ranges are inclusive `(low, high)` pairs in the unit of the figure they
    bound; `reconciliation_tolerance` is different in kind, being a *relative* tolerance for the
    structural sum check rather than a plausibility range.
    """

    #: carrier id -> plausible year-1 effective price in EUR/kWh, uniformly for every carrier
    #: (D26). Carriers absent from the map are not checked at all.
    effective_price_ranges: Dict[str, Tuple[float, float]] = field(default_factory=dict)
    eac_per_m2_range: Tuple[float, float] = (5.0, 80.0)  # EUR per m2 of reference area per year
    lcoh_range: Tuple[float, float] = (0.05, 0.50)  # EUR per kWh of delivered heat
    maintenance_ratio_range: Tuple[float, float] = (0.02, 0.80)  # dimensionless NPV ratio
    band_width_warn: float = 3.5  # max/min of the NPV band; a factor, not a percentage
    #: Relative tolerance of the subjects-sum-to-total invariant, scaled by the total NPV.
    reconciliation_tolerance: float = 1e-6

    @classmethod
    def load(cls, base_path: Optional[str] = None) -> "PlausibilityConfig":
        """Loads the thresholds file; falls back to the defaults above when missing.

        The single entry point for obtaining thresholds — `run_plausibility_checks` calls it when
        no config is passed, and tests pass a hand-built config instead to exercise a failing
        check. A missing file is not an error: the panel is meant to run in any deployment, so an
        absent `plausibility_checks.json` silently yields the dataclass defaults. The
        `isinstance(bounds, list)` guard on `effective_price_ranges` is what lets that block
        carry a `"comment"` string alongside the carrier bounds, as the shipped file does.

        Args:
            base_path: Directory holding `plausibility_checks.json`; defaults to the shipped
                `cost_database/` directory (`CostDatabase.DEFAULT_PATH`).

        Returns:
            A fully populated config; never None.
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
    """One check outcome as **data**: numbers, a unit, and the range that was required.

    Nothing here is formatted. `value` is the figure the check judged, in `unit`; `context`
    carries the further numbers the check looked at, keyed by role, so a renderer can say
    "4,800 vs 16,000 EUR" or "1,760 EUR for 5,000 units" without recomputing anything. Which
    context keys exist is fixed per `check_id` and documented at the producing call site.
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
        """Serialization (machine consumers read findings, never rendered strings).

        Emits every field, including the ones that are None for this check kind, so the record
        shape is the same for all findings and a consumer can read `status` and `check_id`
        without knowing which check produced the row.
        """
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

    The single object the report writers and `bridge.py` receive from
    `run_plausibility_checks`; it is a thin wrapper around the finding list whose value is that
    the *order* of that list is the panel's order and is therefore part of the golden output.
    It exposes `flagged()`/`ok()` so a caller can react to the outcome without knowing the
    status vocabulary, and iterating the report iterates the findings, so it can be passed
    anywhere a sequence of findings is expected.
    """

    findings: List[PlausibilityFinding] = field(default_factory=list)

    def __iter__(self):
        """Iterating the report iterates its findings."""
        return iter(self.findings)

    def __len__(self) -> int:
        """Number of findings."""
        return len(self.findings)

    def flagged(self) -> List[PlausibilityFinding]:
        """Everything that is not a PASS — what a reader has to look at."""
        return [finding for finding in self.findings if finding.status != CheckStatus.PASS]

    def ok(self) -> bool:
        """True when nothing is flagged."""
        return not self.flagged()

    def to_json(self) -> dict:
        """Serialization.

        Wraps the findings in a `{"findings": [...]}` object rather than emitting a bare list, so
        the document can grow further top-level keys (a summary count, a config fingerprint)
        without breaking readers.
        """
        return {"findings": [finding.to_json() for finding in self.findings]}


def _range_finding(
    check_id: str,
    name: str,
    value: float,
    bounds: Tuple[float, float],
    unit: str,
    context: Optional[Dict[str, float]] = None,
) -> PlausibilityFinding:
    """A magnitude check: inside the bounds is a PASS, outside is advisory (WARN).

    The constructor for every range check in the module, which is what guarantees that no
    magnitude check can ever produce a FAIL — being outside a generous range is evidence of a
    likely mistake, never proof of one. Bounds are inclusive on both ends, and the finding
    carries them so the panel can print the range it judged against instead of a bare verdict.
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
    """BEST_ESTIMATE-slot NPV of the given categories (the checks judge the expected world).

    Sums `result.npv_by_category` over the categories passed, skipping any the perspective has
    no entry for, and returns 0.0 when none are present — which is why the callers below guard
    on truthiness before emitting a finding rather than dividing by a possible zero. Only the
    BEST_ESTIMATE slot is used: LOW and HIGH are the deliberately extreme corners of the envelope and
    would trip generous ranges for reasons that are not defects (§3.9).
    """
    return sum(
        result.npv_by_category[category].best_estimate
        for category in categories
        if category in result.npv_by_category
    )


def _structural_findings(
    matrix: EvaluationMatrix, config: PlausibilityConfig
) -> List[PlausibilityFinding]:
    """The hard invariants (§7.4 reconciliation, §3.9 band ordering, §5.4 support bounds).

    Four identities that must hold for *every* perspective in the matrix, not just the reference
    one, because each of them can break in one perspective while holding in the others (an actor
    scope reconciles differently from a system scope, a subsidy mode changes what support exists
    at all). Any violation is a FAIL: the engine or the data produced a result that contradicts
    itself, so nothing downstream of it can be trusted.

    The four are: subject NPVs sum to the perspective total (within a relative tolerance, since
    the two sides are different fold orders of the same floats); the total NPV band is ordered
    min <= best_estimate <= max; the residual value never exceeds what was ever purchased; and support
    never exceeds its own eligible cost basis. The last two are only emitted when both sides are
    non-zero — a perspective without subsidies has nothing to check — and both compare
    magnitudes via `abs()`, because credits are booked negative under the package sign
    convention. The `1e-9` relative slack on those two absorbs float error at the equality case,
    where a cap makes support exactly equal its basis. The band-ordering check differs from the
    other three in that it appends a finding only when it is violated, so it is invisible in a
    healthy panel.

    The two passes over `matrix.results` are not redundant: they put all reconciliation and band
    findings above all residual/subsidy findings in the panel, and panel order is pinned by the
    golden reports.
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
    """Year-1 bill / annualized quantity per carrier — the fastest unit-mix-up detector.

    Divides what a carrier cost in its first full operating year by how much of it was bought,
    and compares the result against the per-carrier range in the thresholds file. A price that
    lands three orders of magnitude off is the signature of a Wh/kWh or ct/EUR confusion
    anywhere between the meter, the annualization and the tariff, and this check finds it
    without anyone having to know where the mistake was made. The numerator is the same four
    bill categories the report's section-4 decomposition adds up (feed-in revenue excluded — a
    credit is not part of what a kWh costs).

    The quotient is EUR/kWh for every carrier, pellets and heating oil included: the denominator
    is kWh throughout and the per-ton and per-liter quotes of the data files are divided out when
    the price entry is resolved (D26), so the shipped bands are EUR/kWh bands too. That uniformity
    is what makes the check readable — before it, two of the eight bands were per ton and could
    only ever have judged a number of a different kind (review finding 11).

    A carrier is skipped silently when it has no range configured, when nothing was bought, or
    when its year-1 bill is zero, so free or unpriced carriers do not produce noise.

    Context keys: ``year1_cost`` (the bill the price was derived from) and ``quantity``.
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
    """Warns when a dynamic-tariff carrier was timed worse than the flat mean price (issue #25b).

    The §8.5 decomposition splits an energy bill into a volume effect and a *flexibility value* —
    what the timing of consumption was worth against a flat profile at the unweighted mean spot
    price. A negative value means the simulated load was systematically on the expensive hours: a
    controller optimizing the wrong signal, an inverted price series, a tariff assigned to the
    wrong carrier. The projection clamps it to zero so the two components do not escalate apart,
    and that clamp used to be the end of the story; the raw figure now travels on the result and
    is reported here.

    Like every magnitude check this is advisory: the headline numbers keep the clamped value, and
    a healthy run emits nothing at all — the finding is appended only when the value is actually
    negative, so a panel with no dynamic tariff looks exactly as it did before.

    Args:
        reference: The reference perspective's result, read for its per-carrier raw values.

    Returns:
        One WARN per carrier with a negative flexibility value, in carrier order; usually empty.
        Context key: ``clamped_to`` — the 0.0 the projection actually used.
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
    """The advisory range checks on the first (reference) perspective.

    Six magnitude questions a domain expert would ask first, in the order the panel prints
    them: is each carrier's effective price recognizable, is the equivalent annual cost per
    square metre in the right order of magnitude for a dwelling, is the levelized cost of heat
    plausible, is maintenance a sane fraction of investment (a huge ratio is the signature of an
    absolute fee stored as a rate), is the uncertainty band suspiciously wide (which usually
    means a band typo in a data file), and did any carrier's load timing actually *cost* money
    against a flat profile (`_flexibility_value_findings`). Every one of them yields at most a
    WARN.

    Each check is emitted only when its inputs exist — no reference area, no LCOH, no
    maintenance or no investment simply drops that row — the band-width check additionally
    requires a strictly positive minimum, since `max/min` is meaningless for a band that spans
    zero or is negative throughout, and the flexibility check appears only when something is
    wrong, so an unchanged run yields an unchanged panel.
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
                "levelized cost of heat",
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


def run_plausibility_checks(
    matrix: EvaluationMatrix, config: Optional[PlausibilityConfig] = None
) -> PlausibilityReport:
    """The automated panel: structural invariants (FAIL) and magnitude ranges (WARN).

    Magnitude checks are evaluated on the *first* perspective of the matrix — the reference
    view a reader reads first; the structural ones run on every perspective. Order is part of
    the contract: the report panel prints findings in the order they are produced here.

    This is the module's only public entry point and the engine-side half of report section 0.
    It is called by `bridge.py` after a simulation, by the `report` CLI on stored results, and by
    the golden tests; each of those then hands the report to `reporting.render_plausibility_findings`
    for display. It never raises on a bad result — an empty matrix is itself reported, as a single
    FAIL finding, so that "the engine produced nothing" arrives through the same channel as every
    other defect instead of as an exception in a postprocessing step.

    Args:
        matrix: The evaluated perspectives. Insertion order matters twice — the first entry is
            the reference perspective for the magnitude checks, and it is the order findings are
            emitted in.
        config: Thresholds to judge against; loaded from `cost_database/plausibility_checks.json`
            when omitted.

    Returns:
        A `PlausibilityReport` whose findings are ordered structural-first, then magnitude.
        `report.ok()` is True when every check passed; a FAIL means an engine/data invariant is
        broken, a WARN means a magnitude wants a look (see the module docstring).
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
    findings = _structural_findings(matrix, config)
    findings.extend(_magnitude_findings(reference, config))
    return PlausibilityReport(findings)
