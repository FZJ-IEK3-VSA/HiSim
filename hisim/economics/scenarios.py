"""Scenario analysis: economic sweeps over stored evaluation inputs (cost_spec.md §4.6).

A scenario changes economic assumptions only, so each one is another call of the pure evaluator on the same stored
facts, taking milliseconds. Anything that changes the physics is a variant needing a new simulation. A scenario set
sweeps `EconomicParameters` fields (parameter axes) and individual cost-database datapoints (data overlays, e.g.
``devices_DE.HEAT_PUMP.specific_investment``). The result is a `ScenarioCube` of full results per perspective and
scenario, read by the tornado, spread, robustness and break-even analyses and exported as ``scenario_cube.csv`` /
``.json``.
"""

from __future__ import annotations

import copy
import csv
import dataclasses
import itertools
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from hisim import log
from hisim.economics.carriers import EnergyCarrier
from hisim.economics.database import CostDatabase
from hisim.economics.evaluator import EconomicEvaluator, EvaluationInputs
from hisim.economics.numerics import bisect_root
from hisim.economics.parameters import EconomicParameters
from hisim.economics.perspectives import Perspective
from hisim.economics.results import LifecycleCostResult
from hisim.economics.subsidies import SubsidyCatalog
from hisim.loadtypes import ComponentType


class ScenarioLimits:
    """Limits on a scenario set: the size of a factorial expansion, and fields that may not be swept (§4.6).

    Expansion warns at `SCENARIO_WARN_THRESHOLD` (1,000) cells and fails at `SCENARIO_ERROR_THRESHOLD` (100,000), so a
    mistyped axis cannot become an overnight job. `NON_SWEEPABLE` names run-level `EconomicParameters` fields with the
    reason given in the error: a whole dataset is swapped with overlays instead, and changing the country changes the
    simulated context, so it is a variant.
    """

    #: Cube explosion control.
    SCENARIO_WARN_THRESHOLD = 1_000
    SCENARIO_ERROR_THRESHOLD = 100_000

    #: EconomicParameters fields that must not be swept (§4.6), with explanatory errors.
    NON_SWEEPABLE = {
        "cost_database_path":
            "a whole-dataset swap is a run-level choice — sweep individual datapoints via overlays",
        "subsidy_catalog_path":
            "a whole-dataset swap is a run-level choice — sweep individual datapoints via overlays",
        "country":
            "a country change invalidates the simulated physics context and is a variant, not an economic scenario",
        "energy_prices":
            "a stated price is a plan's own year-1 bill, not an assumption to vary — sweep the price "
            "datapoints via energy_prices_<COUNTRY> overlays",
    }


class ScenarioDataError(ValueError):
    """Raised for a malformed scenario set or a refused billing-boundary override.

    Covers unknown or non-sweepable fields, unsupported dotted paths, unknown modes, over-large expansions and the §4.6
    billing-boundary refusal.
    """


@dataclass
class ScenarioAxis:
    """One swept dimension: an `EconomicParameters` field or a cost-database overlay path, with named levels.

    `is_data_overlay` is derived from the path's stem at parse time (`_is_data_overlay_path`), not declared. Level
    names become the scenario ids a reader sees ("cheap", "high"); a level value of None means "as shipped".
    """

    name: str
    fieldname: str  # dotted path: an EconomicParameters field, or a cost-database datapoint
    levels: Dict[str, Any]  # level name -> value; None means "as shipped"
    is_data_overlay: bool = False


@dataclass
class Scenario:
    """One expanded scenario: an id plus parameter overrides and data overlays.

    The two kinds stay separate because parameter overrides are validated against `EconomicParameters` while overlays
    are validated against the database and recorded as SCENARIO_OVERLAY provenance.
    """

    id: str
    parameter_overrides: Dict[str, Any] = field(default_factory=dict)
    data_overlays: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ScenarioSet:
    """A scenario-set definition as authored: base id, expansion mode, axes and named scenarios (§4.6).

    Loaded from a JSON file (``--scenarios scenarios.json`` on the CLI), attached by a system setup via
    `bridge.EconomicContext.scenario_set`, or sent in a RenoVisor request. `expand()` turns it into the cells of a
    cube; axes are validated when the set is loaded.

    Modes: FACTORIAL evaluates every combination of levels (ids like ``interest=high|electricity_price=low``).
    ONE_AT_A_TIME moves one axis at a time from the base (ids like ``interest=high``), which a tornado diagram needs.
    """

    base_id: str
    mode: str  # FACTORIAL | ONE_AT_A_TIME
    axes: List[ScenarioAxis] = field(default_factory=list)
    named_scenarios: List[Scenario] = field(default_factory=list)

    @classmethod
    def from_json(cls, raw: dict) -> "ScenarioSet":
        """Parse a scenario set from JSON and validate every path.

        Each axis and named-scenario override is classified as parameter or data overlay by its path stem; parameter
        paths are checked against `EconomicParameters` and the non-sweepable list, so a typo fails here by name.

        Args:
            raw: The parsed scenario-set JSON (`base`, `mode`, `axes`, `named_scenarios`).

        Returns:
            The parsed set, ready to `expand()`.

        Raises:
            ScenarioDataError: On an unknown or non-sweepable parameter field, a dotted path into a non-dict field, or
                an unknown mode.
        """
        axes = []
        for axis_raw in raw.get("axes", []):
            fieldname = axis_raw["field"]
            is_overlay = _is_data_overlay_path(fieldname)
            if not is_overlay:
                _validate_parameter_path(fieldname)
            axes.append(
                ScenarioAxis(
                    name=axis_raw["name"],
                    fieldname=fieldname,
                    levels=dict(axis_raw["levels"]),
                    is_data_overlay=is_overlay,
                )
            )
        named = []
        for scenario_raw in raw.get("named_scenarios", []):
            parameter_overrides: Dict[str, Any] = {}
            data_overlays: Dict[str, Any] = {}
            for fieldname, value in scenario_raw.get("overrides", {}).items():
                if _is_data_overlay_path(fieldname):
                    data_overlays[fieldname] = value
                else:
                    _validate_parameter_path(fieldname, allow_dict_root=True)
                    parameter_overrides[fieldname] = value
            named.append(
                Scenario(id=scenario_raw["id"], parameter_overrides=parameter_overrides, data_overlays=data_overlays)
            )
        mode = raw.get("mode", "ONE_AT_A_TIME")
        if mode not in ("FACTORIAL", "ONE_AT_A_TIME"):
            raise ScenarioDataError(f"Unknown scenario mode {mode!r}.")
        return cls(base_id=raw.get("base", "central"), mode=mode, axes=axes, named_scenarios=named)

    def expand(self) -> List[Scenario]:
        """Expand the set into scenarios: the base first, then the axes per mode, then the named scenarios.

        The base scenario (no overrides, id `base_id`) is always included because every derived analysis measures
        against it. FACTORIAL ids join levels with ``|``, which `tornado_data` uses to skip combination cells. Levels
        are sorted by name, so the order is stable and exports diff cleanly.

        Returns:
            All scenarios to evaluate.

        Raises:
            ScenarioDataError: When the expansion exceeds `SCENARIO_ERROR_THRESHOLD` cells; above
                `SCENARIO_WARN_THRESHOLD` it only warns.
        """
        scenarios: List[Scenario] = [Scenario(id=self.base_id)]
        if self.mode == "FACTORIAL" and self.axes:
            level_lists = [sorted(axis.levels.items()) for axis in self.axes]
            for combination in itertools.product(*level_lists):
                scenario = Scenario(
                    id="|".join(f"{axis.name}={level_name}" for axis, (level_name, _) in zip(self.axes, combination))
                )
                for axis, (_level_name, value) in zip(self.axes, combination):
                    if axis.is_data_overlay:
                        scenario.data_overlays[axis.fieldname] = value
                    else:
                        scenario.parameter_overrides[axis.fieldname] = value
                scenarios.append(scenario)
        elif self.mode == "ONE_AT_A_TIME":
            for axis in self.axes:
                for level_name, value in sorted(axis.levels.items()):
                    scenario = Scenario(id=f"{axis.name}={level_name}")
                    if axis.is_data_overlay:
                        scenario.data_overlays[axis.fieldname] = value
                    else:
                        scenario.parameter_overrides[axis.fieldname] = value
                    scenarios.append(scenario)
        scenarios.extend(self.named_scenarios)
        if len(scenarios) > ScenarioLimits.SCENARIO_ERROR_THRESHOLD:
            raise ScenarioDataError(
                f"Scenario set expands to {len(scenarios)} scenarios "
                f"(> {ScenarioLimits.SCENARIO_ERROR_THRESHOLD})."
            )
        if len(scenarios) > ScenarioLimits.SCENARIO_WARN_THRESHOLD:
            log.warning(
                f"Scenario set expands to {len(scenarios)} scenarios "
                f"(> {ScenarioLimits.SCENARIO_WARN_THRESHOLD})."
            )
        return scenarios


def _is_data_overlay_path(fieldname: str) -> bool:
    """Return whether a dotted path addresses a cost-database datapoint rather than a parameter.

    A data file stem (``devices_DE``, ``energy_prices_DE``) means overlay; anything else is an `EconomicParameters`
    path.
    """
    stem = fieldname.split(".", 1)[0]
    return stem.startswith("devices_") or stem.startswith("energy_prices_")


def _validate_parameter_path(fieldname: str, allow_dict_root: bool = False) -> None:
    """Check that a dotted path addresses a sweepable `EconomicParameters` field.

    Checks in order: the field is not in `NON_SWEEPABLE` (reported with its reason), it exists on `EconomicParameters`,
    and a dotted path only reaches into a dict field (the escalation-rate maps keyed by carrier and by asset class).

    Args:
        fieldname: The dotted path from the scenario definition.
        allow_dict_root: Allows assigning a whole dict to any dict-typed field; set for named-scenario overrides
            (merged key by key by `apply_parameter_overrides`). Axes stay limited to the two escalation maps.

    Raises:
        ScenarioDataError: If the field is non-sweepable, unknown, or reached into illegally.
    """
    root = fieldname.split(".", 1)[0]
    if root in ScenarioLimits.NON_SWEEPABLE:
        raise ScenarioDataError(
            f"Field {fieldname!r} is not sweepable: {ScenarioLimits.NON_SWEEPABLE[root]} (§4.6)."
        )
    known_fields = {dataclass_field.name for dataclass_field in dataclasses.fields(EconomicParameters)}
    if root not in known_fields:
        raise ScenarioDataError(f"Scenario axis targets unknown EconomicParameters field {fieldname!r}.")
    if "." in fieldname and root not in (
        "energy_price_escalation_rates",
        "investment_price_escalation_rates",
    ) and not allow_dict_root:
        raise ScenarioDataError(f"Dotted path {fieldname!r} is only supported into dict-typed fields.")


def apply_parameter_overrides(base: EconomicParameters, overrides: Dict[str, Any]) -> EconomicParameters:
    """Return a copy of the parameters with dotted-path overrides applied.

    Deep-copies first, so cube cells never share state. Supports a dotted path setting one key of a dict field, a whole
    dict merged key by key into a dict field, and plain attribute assignment. The result is re-validated by re-running
    `EconomicParameters.__post_init__`, so e.g. a negative interest rate is refused.

    Args:
        base: The run's parameters; left untouched.
        overrides: Dotted path or field name to value, from one `Scenario`.

    Returns:
        A new `EconomicParameters` with the overrides applied.

    Raises:
        ScenarioDataError: If a dotted path targets a field that is not a dict.
        ValueError: If the overridden parameters fail their own validation.
    """
    params = copy.deepcopy(base)
    for fieldname, value in overrides.items():
        if "." in fieldname:
            root, key = fieldname.split(".", 1)
            container = getattr(params, root)
            if not isinstance(container, dict):
                raise ScenarioDataError(f"Cannot apply dotted override {fieldname!r}: {root} is not a dict.")
            container[_coerce_dict_key(root, key)] = value
        elif isinstance(value, dict) and isinstance(getattr(params, fieldname), dict):
            container = getattr(params, fieldname)
            for key, sub_value in value.items():
                container[_coerce_dict_key(fieldname, key)] = sub_value
        else:
            setattr(params, fieldname, value)
    params.__post_init__()
    return params


def _coerce_dict_key(fieldname: str, key: str) -> Any:
    """Convert a JSON string key into the enum the target dict is keyed by.

    The escalation-rate maps are keyed by `EnergyCarrier` and `ComponentType`; a plain string key would never be found
    by a lookup. `ComponentType` matches member name or value. An unrecognized key is passed through unchanged.
    """
    if fieldname == "energy_price_escalation_rates":
        return EnergyCarrier(key)
    if fieldname == "investment_price_escalation_rates":
        for member in ComponentType:
            if key in (member.name, member.value):
                return member
    return key


def _check_billing_boundary(inputs: EvaluationInputs, scenario: Scenario, params: EconomicParameters) -> None:
    """Refuse a scenario that changes energy prices a controller already consumed, unless opted in (§4.6).

    If a controller reacted to a tariff's price signal during the run (`consumed_tariff_ids`, §8.3), re-billing that
    load profile under other prices is a counterfactual; it requires ``allow_counterfactual_billing``. Escalation-rate
    overrides are allowed, since escalation only affects years after the simulated one.

    Args:
        inputs: The stored inputs, for `consumed_tariff_ids`.
        scenario: The scenario about to be evaluated.
        params: That scenario's parameters, for the opt-in flag.

    Raises:
        ScenarioDataError: When the scenario overlays energy prices on a run whose controller consumed a tariff,
            without the opt-in.
    """
    if params.allow_counterfactual_billing or not inputs.consumed_tariff_ids:
        return
    touches_prices = any(path.split(".", 1)[0].startswith("energy_prices_") for path in scenario.data_overlays)
    touches_escalation = any(
        fieldname.split(".", 1)[0] == "energy_price_escalation_rates" for fieldname in scenario.parameter_overrides
    )
    del touches_escalation  # escalation projects future years; only year-1 prices were consumed
    if touches_prices:
        raise ScenarioDataError(
            f"Scenario {scenario.id!r} overrides energy prices, but the simulation consumed tariff "
            f"{inputs.consumed_tariff_ids} — rebilling has counterfactual semantics; set "
            "allow_counterfactual_billing=true to opt in (§4.6)."
        )


@dataclass(frozen=True)
class KpiSpread:
    """Minimum, maximum and spread of one KPI across all scenarios of one perspective (§4.6).

    Taken on the best-estimate value of each cell, so it measures sensitivity to assumptions; the min/best/max band
    inside a cell measures cost-data uncertainty (§3.9).
    """

    minimum: float
    maximum: float

    @property
    def spread(self) -> float:
        """Return how far the KPI travels across the scenario set (maximum minus minimum)."""
        return self.maximum - self.minimum


@dataclass
class ScenarioCube:
    """The output of a sweep: `results[perspective_id][scenario_id]`, each cell a full `LifecycleCostResult` (§4.6).

    Cells hold complete results because a scenario moves every figure. `scenarios` keeps the expanded definitions so an
    analysis can tell a one-at-a-time cell from a factorial one, and `base_id` names the reference cell.
    """

    results: Dict[str, Dict[str, LifecycleCostResult]] = field(default_factory=dict)
    scenarios: List[Scenario] = field(default_factory=list)
    base_id: str = "central"

    def kpi(self, perspective: str, scenario: str, kpi_getter: Callable[[LifecycleCostResult], float]) -> float:
        """Return one KPI value from one cell, read with the given getter.

        Raises:
            KeyError: For an unknown perspective or scenario.
        """
        return kpi_getter(self.results[perspective][scenario])

    def equivalent_annual_cost_swings(self, perspective: str) -> Dict[str, float]:
        """Return each scenario's equivalent annual cost minus the base scenario's (best estimate), base included as 0.

        Feeds the report's tornado chart. Empty when the perspective has no base cell.
        """
        per_scenario = self.results.get(perspective, {})
        if self.base_id not in per_scenario:
            return {}
        base_value = default_kpi_getter(per_scenario[self.base_id])
        return {
            scenario_id: default_kpi_getter(result) - base_value
            for scenario_id, result in per_scenario.items()
        }

    def equivalent_annual_cost_spreads(self) -> Dict[str, KpiSpread]:
        """Return the minimum, maximum and spread of the equivalent annual cost per perspective (§4.6).

        Needs no base cell; perspectives without cells are omitted.
        """
        spreads: Dict[str, KpiSpread] = {}
        for perspective, per_scenario in self.results.items():
            values = [default_kpi_getter(result) for result in per_scenario.values()]
            if not values:
                continue
            spreads[perspective] = KpiSpread(minimum=min(values), maximum=max(values))
        return spreads


def evaluate_cube(
    inputs: EvaluationInputs,
    base_parameters: EconomicParameters,
    perspectives: List[Perspective],
    scenario_set: ScenarioSet,
    database: Optional[CostDatabase] = None,
    subsidy_catalog: Optional[SubsidyCatalog] = None,
) -> ScenarioCube:
    """Evaluate the full scenario cube on stored inputs (§4.6).

    For each scenario: apply its parameter overrides to a copy, check the billing boundary, build an overlaid database
    copy if it has data overlays, and evaluate every perspective with a fresh evaluator. Cells are independent and the
    stored facts are identical for all of them.

    Args:
        inputs: The stored evaluator inputs, shared by every cell.
        base_parameters: The parameters scenarios deviate from.
        perspectives: The perspectives to evaluate per scenario, normally the applicable subset of the default bundle.
        scenario_set: The authored definition; expanded here.
        database: Pre-loaded cost database; loaded from `base_parameters` when omitted.
        subsidy_catalog: Optional catalog; without it no subsidy is booked in any cell.

    Returns:
        The populated cube, indexed `[perspective_id][scenario_id]`.

    Raises:
        ScenarioDataError: From the expansion (too many cells) or the billing-boundary check.
    """
    base_database = database or CostDatabase(base_parameters.cost_database_path)
    scenarios = scenario_set.expand()
    cube = ScenarioCube(scenarios=scenarios, base_id=scenario_set.base_id)
    for scenario in scenarios:
        params = apply_parameter_overrides(base_parameters, scenario.parameter_overrides)
        _check_billing_boundary(inputs, scenario, params)
        scenario_database = (
            base_database.with_overlays(scenario.data_overlays, scenario.id)
            if scenario.data_overlays
            else base_database
        )
        evaluator = EconomicEvaluator(scenario_database, params, subsidy_catalog)
        for perspective in perspectives:
            result = evaluator.evaluate(inputs, perspective)
            cube.results.setdefault(perspective.id, {})[scenario.id] = result
    return cube


# ---------------------------------------------------------------------- derived analyses

def default_kpi_getter(result: LifecycleCostResult) -> float:
    """Return the headline KPI: equivalent annual cost, best-estimate slot.

    The default of every analysis here, so a tornado, a spread and a break-even talk about the same number. Scenarios
    vary assumptions, not data, so the best estimate is used; only `robustness_summary`'s slot-aware flag reads the
    band.
    """
    return result.equivalent_annual_cost_in_euro.best_estimate


def tornado_data(
    cube: ScenarioCube, perspective: str, kpi_getter: Callable[[LifecycleCostResult], float] = default_kpi_getter
) -> List[Dict[str, Any]]:
    """Return each single-axis scenario's KPI swing against the base, for a tornado diagram (§4.6).

    Sorted by absolute swing, the rows rank assumptions by how much the answer depends on them. A swing is attributable
    to one axis only in a ONE_AT_A_TIME set, so factorial cells (ids with ``|``) and the base cell are skipped; a
    factorial cube yields an empty table.

    Args:
        cube: The evaluated cube.
        perspective: Which perspective's cells to read.
        kpi_getter: The figure to swing; equivalent annual cost (best estimate) by default.

    Returns:
        One dict per scenario with `scenario`, `kpi`, `base` and `swing`, in expansion order.
    """
    base_value = cube.kpi(perspective, cube.base_id, kpi_getter)
    rows = []
    for scenario in cube.scenarios:
        if scenario.id == cube.base_id or "|" in scenario.id:
            continue
        value = cube.kpi(perspective, scenario.id, kpi_getter)
        rows.append({"scenario": scenario.id, "kpi": value, "base": base_value, "swing": value - base_value})
    return rows


def robustness_summary(
    cube_a: ScenarioCube,
    cube_b: ScenarioCube,
    perspective: str,
    kpi_getter: Callable[[LifecycleCostResult], float] = default_kpi_getter,
) -> Dict[str, Any]:
    """Compare two variants over the same scenario set: KPI deltas, their spread and dominance flags (§4.6).

    Deltas are A minus B on a cost KPI, so negative means A is cheaper. `a_dominates_b_in_every_scenario` is True when
    A is strictly cheaper in every scenario. `a_dominates_b_slot_aware` is stricter: A's maximum equivalent annual cost
    stays below B's minimum in every scenario; it always reads the equivalent annual cost, whatever `kpi_getter` is.

    Args:
        cube_a: The variant under investigation.
        cube_b: The reference variant, swept over the same scenario set (cells are matched by scenario id).
        perspective: Which perspective to compare in.
        kpi_getter: The figure to difference; equivalent annual cost (best estimate) by default.

    Returns:
        `min_delta`, `max_delta`, `spread`, the two dominance flags, and the per-scenario `deltas`.

    Raises:
        ScenarioDataError: If `cube_a` holds no scenarios.
    """
    if not cube_a.scenarios:
        raise ScenarioDataError(
            "robustness_summary needs at least one scenario: cube_a.scenarios is empty, so there "
            "are no per-scenario deltas to take a minimum, maximum or spread of (§4.6)."
        )
    deltas = {}
    dominates_all = True
    dominates_slot_aware = True
    for scenario in cube_a.scenarios:
        result_a = cube_a.results[perspective][scenario.id]
        result_b = cube_b.results[perspective][scenario.id]
        delta = kpi_getter(result_a) - kpi_getter(result_b)
        deltas[scenario.id] = delta
        if delta >= 0:
            dominates_all = False
        # Slot-aware dominance: A's HIGH beats B's LOW (§4.6) — the strongest statement.
        if result_a.equivalent_annual_cost_in_euro.maximum >= result_b.equivalent_annual_cost_in_euro.minimum:
            dominates_slot_aware = False
    values = list(deltas.values())
    return {
        "min_delta": min(values),
        "max_delta": max(values),
        "spread": max(values) - min(values),
        "a_dominates_b_in_every_scenario": dominates_all,
        "a_dominates_b_slot_aware": dominates_slot_aware,
        "deltas": deltas,
    }


def find_break_even(
    axis_field: str,
    search_range: Tuple[float, float],
    inputs_a: EvaluationInputs,
    inputs_b: EvaluationInputs,
    base_parameters: EconomicParameters,
    perspective: Perspective,
    database: Optional[CostDatabase] = None,
    subsidy_catalog: Optional[SubsidyCatalog] = None,
    kpi_getter: Callable[[LifecycleCostResult], float] = default_kpi_getter,
    tolerance: float = 1e-4,
    max_iterations: int = 60,
) -> Dict[str, Any]:
    """Find by bisection the value of one `EconomicParameters` field at which two variants cost the same (§4.6).

    Example: up to which interest rate is the retrofit still worth it. Both variants are evaluated afresh at each step.
    The answer is on the best-estimate slot; the minimum and maximum slots cross at other values, reported as a
    bracket. If the KPI difference has the same sign at both ends of the range, no crossing is reported.

    Args:
        axis_field: Dotted `EconomicParameters` path to bisect on; validated like an axis.
        search_range: (low, high) bounds of the search.
        inputs_a: Stored inputs of the variant under investigation.
        inputs_b: Stored inputs of the reference variant.
        base_parameters: Parameters held fixed apart from `axis_field`.
        perspective: The single perspective the comparison is made in.
        database: Pre-loaded cost database; loaded from `base_parameters` when omitted.
        subsidy_catalog: Optional catalog, applied to both variants alike.
        kpi_getter: The figure to difference; equivalent annual cost (best estimate) by default.
        tolerance: Absolute interval width at which the bisection stops.
        max_iterations: Cap on bisection steps; the midpoint is returned if it is hit.

    Returns:
        The axis name and range, the best-estimate `break_even` (None without a crossing), the minimum and maximum slot
            crossings as `bracket_low_slot` / `bracket_high_slot`, and `no_crossing_in_range`.

    Raises:
        ScenarioDataError: If `axis_field` is not a sweepable `EconomicParameters` path.
    """
    _validate_parameter_path(axis_field)
    base_database = database or CostDatabase(base_parameters.cost_database_path)

    def delta_at(value: float, slot: str) -> float:
        """Return A minus B at one axis value in one slot, the function being rooted.

        The best-estimate slot uses `kpi_getter`; the minimum and maximum slots read the equivalent annual cost band,
        the only KPI guaranteed to carry a band.
        """
        params = apply_parameter_overrides(base_parameters, {axis_field: value})
        evaluator = EconomicEvaluator(base_database, params, subsidy_catalog)
        result_a = evaluator.evaluate(inputs_a, perspective)
        result_b = evaluator.evaluate(inputs_b, perspective)
        if slot == "best_estimate":
            return kpi_getter(result_a) - kpi_getter(result_b)
        getter = (lambda band: band.minimum) if slot == "low" else (lambda band: band.maximum)
        return float(
            getter(result_a.equivalent_annual_cost_in_euro) - getter(result_b.equivalent_annual_cost_in_euro)
        )

    def bisect(slot: str) -> Optional[float]:
        """Bisect `delta_at` in one slot; return None when the range ends have the same sign.

        Bisection is used because the KPI has kinks (subsidy caps, replacement years, tier tables). The halving is
        `numerics.bisect_root`.
        """
        return bisect_root(
            lambda value: delta_at(value, slot),
            window=search_range,
            max_iterations=max_iterations,
            tolerance=tolerance,
        )

    crossing = bisect("best_estimate")
    return {
        "axis": axis_field,
        "range": list(search_range),
        "break_even": crossing,
        "bracket_low_slot": bisect("low"),
        "bracket_high_slot": bisect("high"),
        "no_crossing_in_range": crossing is None,
    }


# ---------------------------------------------------------------------- exports (§4.6)

class CubeKpis:
    """The KPIs a scenario cube is exported with: net present cost, equivalent annual cost and year-1 monthly cost.

    Each is written with its full min/best/max band. The CSV is for `scenario_evaluation` and spreadsheets; the full
    results are in ``scenario_cube.json``. A getter may return None (a perspective without a monthly cost), and that
    row is omitted.
    """

    BY_NAME: Dict[str, Callable[[LifecycleCostResult], Any]] = {
        "total_npv_in_euro": lambda result: result.total_npv_in_euro,
        "equivalent_annual_cost_in_euro": lambda result: result.equivalent_annual_cost_in_euro,
        "monthly_cost_year1_in_euro": lambda result: result.monthly_cost_year1_in_euro,
    }


def export_cube_csv(cube: ScenarioCube, path: str, variant: str = "default") -> None:
    """Write ``scenario_cube.csv`` in long format, one row per variant, perspective, scenario and KPI (§4.6).

    Each row carries the KPI's min/best/max. This is the shape the `scenario_evaluation` cross-run aggregation
    consumes, so a scenario sweep and separately simulated variants can be plotted together. The `variant` column lets
    several cubes be concatenated.

    Args:
        cube: The evaluated cube.
        path: Full path of the CSV to write.
        variant: Label for the variant this cube belongs to.
    """
    with open(path, "w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["variant", "perspective", "scenario", "kpi", "value_min", "value_best_estimate", "value_max"])
        for perspective, per_scenario in cube.results.items():
            for scenario_id, result in per_scenario.items():
                for kpi_name, getter in CubeKpis.BY_NAME.items():
                    band = getter(result)
                    if band is None:
                        continue
                    writer.writerow(
                        [variant, perspective, scenario_id, kpi_name, band.minimum, band.best_estimate, band.maximum]
                    )


def export_cube_json(cube: ScenarioCube, path: str, variant: str = "default") -> None:
    """Write ``scenario_cube.json``: every cell as a full serialized `LifecycleCostResult`, for the webtool.

    Includes `base_scenario`, since most readings of a cube are relative to the base cell.

    Args:
        cube: The evaluated cube.
        path: Full path of the JSON to write.
        variant: Label for the variant this cube belongs to.
    """
    payload = {
        "variant": variant,
        "base_scenario": cube.base_id,
        "results": {
            perspective: {scenario_id: result.to_json() for scenario_id, result in per_scenario.items()}
            for perspective, per_scenario in cube.results.items()
        },
    }
    with open(path, "w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2)
