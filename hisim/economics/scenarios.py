"""Scenario analysis: economic sweeps on stored results (cost_spec.md §4.6).

Economic-only dimensions are just another call of the pure evaluator on the same facts —
milliseconds each. Physics-affecting dimensions are *variants*, handled by the existing
simulation infrastructure; the boundary is enforced via `consumed_tariff_ids`.
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
from hisim.economics.database import CostDatabase
from hisim.economics.evaluator import EconomicEvaluator, EvaluationInputs
from hisim.economics.parameters import EconomicParameters
from hisim.economics.perspectives import Perspective
from hisim.economics.results import LifecycleCostResult
from hisim.economics.subsidies import SubsidyCatalog

#: Cube explosion control (spec Q21).
SCENARIO_WARN_THRESHOLD = 1_000
SCENARIO_ERROR_THRESHOLD = 100_000

#: EconomicParameters fields that must not be swept (§4.6), with explanatory errors.
NON_SWEEPABLE = {
    "cost_database_path": "a whole-dataset swap is a run-level choice — sweep individual datapoints via overlays",
    "subsidy_catalog_path": "a whole-dataset swap is a run-level choice — sweep individual datapoints via overlays",
    "country": "a country change invalidates the simulated physics context and is a variant, not an economic scenario",
}


class ScenarioDataError(ValueError):
    """Raised for malformed scenario sets."""


@dataclass
class ScenarioAxis:
    """One swept dimension: an EconomicParameters field or a data-overlay path."""

    name: str
    fieldname: str
    levels: Dict[str, Any]
    is_data_overlay: bool = False


@dataclass
class Scenario:
    """One expanded scenario: parameter overrides plus data overlays."""

    id: str
    parameter_overrides: Dict[str, Any] = field(default_factory=dict)
    data_overlays: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ScenarioSet:
    """A scenario-set definition (data file or RenoVisor request block, §4.6)."""

    base_id: str
    mode: str  # FACTORIAL | ONE_AT_A_TIME
    axes: List[ScenarioAxis] = field(default_factory=list)
    named_scenarios: List[Scenario] = field(default_factory=list)

    @classmethod
    def from_json(cls, raw: dict) -> "ScenarioSet":
        """Parses and validates against the EconomicParameters schema (hard error on unknowns)."""
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
        """Expands axes per mode plus the named scenarios; always includes the base scenario."""
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
        if len(scenarios) > SCENARIO_ERROR_THRESHOLD:
            raise ScenarioDataError(f"Scenario set expands to {len(scenarios)} scenarios (> {SCENARIO_ERROR_THRESHOLD}).")
        if len(scenarios) > SCENARIO_WARN_THRESHOLD:
            log.warning(f"Scenario set expands to {len(scenarios)} scenarios (> {SCENARIO_WARN_THRESHOLD}).")
        return scenarios


def _is_data_overlay_path(fieldname: str) -> bool:
    stem = fieldname.split(".", 1)[0]
    return stem.startswith("devices_") or stem.startswith("energy_prices_")


def _validate_parameter_path(fieldname: str, allow_dict_root: bool = False) -> None:
    """Axes address EconomicParameters fields by dotted path; unknown field = hard error."""
    root = fieldname.split(".", 1)[0]
    if root in NON_SWEEPABLE:
        raise ScenarioDataError(f"Field {fieldname!r} is not sweepable: {NON_SWEEPABLE[root]} (§4.6).")
    known_fields = {dataclass_field.name for dataclass_field in dataclasses.fields(EconomicParameters)}
    if root not in known_fields:
        raise ScenarioDataError(f"Scenario axis targets unknown EconomicParameters field {fieldname!r}.")
    if "." in fieldname and root not in (
        "energy_price_escalation_rates",
        "investment_price_escalation_rates",
    ) and not allow_dict_root:
        raise ScenarioDataError(f"Dotted path {fieldname!r} is only supported into dict-typed fields.")


def apply_parameter_overrides(base: EconomicParameters, overrides: Dict[str, Any]) -> EconomicParameters:
    """Returns a copy of the parameters with dotted-path overrides applied."""
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
    return params


def _coerce_dict_key(fieldname: str, key: str) -> Any:
    from hisim.economics.carriers import EnergyCarrier
    from hisim.loadtypes import ComponentType

    if fieldname == "energy_price_escalation_rates":
        return EnergyCarrier(key)
    if fieldname == "investment_price_escalation_rates":
        for member in ComponentType:
            if key in (member.name, member.value):
                return member
    return key


def _check_billing_boundary(inputs: EvaluationInputs, scenario: Scenario, params: EconomicParameters) -> None:
    """An economic scenario overriding a consumed input is rejected by default (§4.6)."""
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


@dataclass
class ScenarioCube:
    """`results[perspective][scenario]`, each cell a full LifecycleCostResult (§4.6)."""

    results: Dict[str, Dict[str, LifecycleCostResult]] = field(default_factory=dict)
    scenarios: List[Scenario] = field(default_factory=list)
    base_id: str = "central"

    def kpi(self, perspective: str, scenario: str, kpi_getter: Callable[[LifecycleCostResult], float]) -> float:
        """One KPI value from one cell."""
        return kpi_getter(self.results[perspective][scenario])


def evaluate_cube(
    inputs: EvaluationInputs,
    base_parameters: EconomicParameters,
    perspectives: List[Perspective],
    scenario_set: ScenarioSet,
    database: Optional[CostDatabase] = None,
    subsidy_catalog: Optional[SubsidyCatalog] = None,
) -> ScenarioCube:
    """Evaluates the full scenario cube on stored inputs (§4.6)."""
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
    """The headline KPI: equivalent annual cost, AVERAGE slot."""
    return result.equivalent_annual_cost_in_euro.average


def tornado_data(
    cube: ScenarioCube, perspective: str, kpi_getter: Callable[[LifecycleCostResult], float] = default_kpi_getter
) -> List[Dict[str, Any]]:
    """Per axis/level swing vs. the base scenario (ONE_AT_A_TIME input, §4.6)."""
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
    """Min/max/spread of the differential KPI across all scenarios, plus dominance flags (§4.6)."""
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
    """Bisection on one EconomicParameters axis for the value where two variants cross (§4.6).

    Runs on the AVERAGE slot; the LOW/HIGH crossings are reported as a bracket.
    """
    _validate_parameter_path(axis_field)
    base_database = database or CostDatabase(base_parameters.cost_database_path)

    def delta_at(value: float, slot: str) -> float:
        params = apply_parameter_overrides(base_parameters, {axis_field: value})
        evaluator = EconomicEvaluator(base_database, params, subsidy_catalog)
        result_a = evaluator.evaluate(inputs_a, perspective)
        result_b = evaluator.evaluate(inputs_b, perspective)
        if slot == "average":
            return kpi_getter(result_a) - kpi_getter(result_b)
        getter = (lambda band: band.minimum) if slot == "low" else (lambda band: band.maximum)
        return float(
            getter(result_a.equivalent_annual_cost_in_euro) - getter(result_b.equivalent_annual_cost_in_euro)
        )

    def bisect(slot: str) -> Optional[float]:
        low, high = search_range
        delta_low, delta_high = delta_at(low, slot), delta_at(high, slot)
        if delta_low * delta_high > 0:
            return None  # no crossing in range
        for _ in range(max_iterations):
            mid = (low + high) / 2.0
            delta_mid = delta_at(mid, slot)
            if abs(high - low) < tolerance:
                return mid
            if delta_low * delta_mid <= 0:
                high, delta_high = mid, delta_mid
            else:
                low, delta_low = mid, delta_mid
        return (low + high) / 2.0

    crossing = bisect("average")
    return {
        "axis": axis_field,
        "range": list(search_range),
        "break_even": crossing,
        "bracket_low_slot": bisect("low"),
        "bracket_high_slot": bisect("high"),
        "no_crossing_in_range": crossing is None,
    }


# ---------------------------------------------------------------------- exports (§4.6)

CUBE_KPIS: Dict[str, Callable[[LifecycleCostResult], Any]] = {
    "total_npv_in_euro": lambda result: result.total_npv_in_euro,
    "equivalent_annual_cost_in_euro": lambda result: result.equivalent_annual_cost_in_euro,
    "monthly_cost_year1_in_euro": lambda result: result.monthly_cost_year1_in_euro,
}


def export_cube_csv(cube: ScenarioCube, path: str, variant: str = "default") -> None:
    """`scenario_cube.csv` in long format, consumable by scenario_evaluation (§4.6)."""
    with open(path, "w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["variant", "perspective", "scenario", "kpi", "value_min", "value_avg", "value_max"])
        for perspective, per_scenario in cube.results.items():
            for scenario_id, result in per_scenario.items():
                for kpi_name, getter in CUBE_KPIS.items():
                    band = getter(result)
                    if band is None:
                        continue
                    writer.writerow(
                        [variant, perspective, scenario_id, kpi_name, band.minimum, band.average, band.maximum]
                    )


def export_cube_json(cube: ScenarioCube, path: str, variant: str = "default") -> None:
    """`scenario_cube.json` with the typed cube for the webtool."""
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
