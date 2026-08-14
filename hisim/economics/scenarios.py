"""Scenario analysis: economic sweeps on stored results (cost_spec.md §4.6).

Economic-only dimensions are just another call of the pure evaluator on the same facts —
milliseconds each. Physics-affecting dimensions are *variants*, handled by the existing
simulation infrastructure; the boundary is enforced via `consumed_tariff_ids`.

**What a scenario may vary, and the two mechanisms for it.** A *parameter axis* addresses an
`EconomicParameters` field by dotted path — interest rate, escalation rates, CO2 price scenario,
observation period — and is validated against the dataclass schema at load time, so an unknown
field is a hard error. A *data overlay* addresses an individual cost-database datapoint by a path
rooted at the data file stem (`devices_DE.HEAT_PUMP.specific_investment`,
`energy_prices_DE.NATURAL_GAS.working_price_in_euro_per_kwh`) and is applied on top of the loaded,
validated dataset on a copy — so "what if heat pumps get 30 % cheaper" is a diffable few-line
scenario rather than a forked database file, and every overlaid value enters the provenance ledger
as `SCENARIO_OVERLAY` with its scenario id. The two are distinguished purely by the path's stem
(`_is_data_overlay_path`) and carried separately on every `Scenario`.

**FACTORIAL vs. ONE_AT_A_TIME.** FACTORIAL expands the cartesian product of all axis levels
(scenario ids like ``interest=high|electricity_price=low``) — the exhaustive sweep, which is where
the explosion guards below earn their keep. ONE_AT_A_TIME varies each axis from the base
individually (ids like ``interest=high``), which is far easier to interpret and is the required
input shape for a tornado diagram: each bar is one axis moved on its own, so the swings are
attributable. Both modes always include the base scenario, and both append any hand-written
`named_scenarios` — explicit storylines that may mix parameter overrides and data overlays.

**The cube.** `ScenarioCube.results` is indexed `[perspective_id][scenario_id]` and each cell is a
complete `LifecycleCostResult` — not a KPI, the whole result, because a scenario changes every
figure and not just the headline. Note the third, orthogonal axis that is *inside* every cell: the
min/avg/max slots of §3.9 vary the cost *data* within one evaluation, while scenarios vary the
economic *assumptions* across evaluations. The derived analyses read the cube along different
directions: `tornado_data` takes the base cell as origin and reports each one-at-a-time cell's
swing against it; `equivalent_annual_cost_spreads` / `robustness_summary` take the extremes across
all cells (the latter across a *pair* of cubes, i.e. two variants, adding the dominance flags);
`find_break_even` does not read the cube at all but bisects one axis with fresh evaluations.

Its place in the pipeline: this module never touches a simulation. It runs on stored
`EvaluationInputs` — from a finished run via `bridge.py` when the setup declared a scenario set, or
from an archived `economic_inputs.json` via `python -m hisim.economics evaluate --scenarios` — and
exports `scenario_cube.csv` / `.json`, the latter typed for the webtool, the former shaped so the
existing `scenario_evaluation` aggregation can consume it alongside cross-run results.
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


class ScenarioLimits:
    """What a scenario set may not do: explode, or sweep a run-level choice (§4.6, Q21).

    Two unrelated guards, both about keeping a scenario set an *economic* sweep. The thresholds
    bound a factorial expansion — cheap as one evaluation is, four axes with five levels each is
    already 625 cells per perspective — with a warning at 1,000 and a hard error at 100,000, so a
    mistyped axis cannot silently turn into an overnight job. `NON_SWEEPABLE` names the
    `EconomicParameters` fields that are run-level choices rather than assumptions, each with the
    explanation the error message carries: swapping a whole dataset is what overlays exist for, and
    changing the country invalidates the simulated physics context (building stock, weather, codes),
    which makes it a *variant* requiring a new simulation, not a scenario.
    """

    #: Cube explosion control (spec Q21).
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
    }


class ScenarioDataError(ValueError):
    """Raised for malformed scenario sets.

    Covers the load-time rejections (unknown or non-sweepable parameter field, unsupported dotted
    path, unknown mode, over-large expansion) and the §4.6 billing-boundary refusal. It is a
    `ValueError` because a scenario set is user-authored data, and the failure is always "this
    definition cannot mean anything", never an internal error.
    """


@dataclass
class ScenarioAxis:
    """One swept dimension: an EconomicParameters field or a data-overlay path.

    An axis is a name plus a set of named levels, and `is_data_overlay` decides which of the two
    mechanisms applies it — a parameter override on a copy of `EconomicParameters`, or a datapoint
    overlay on a copy of the cost database. The flag is derived from the path at parse time
    (`_is_data_overlay_path`), never declared by the author, so the two vocabularies cannot be
    mixed up in a data file.

    Levels are named because the names become the scenario ids a reader sees ("cheap", "central",
    "high"); a level value of `None` means "as shipped", which is how an overlay axis expresses its
    baseline as an ordinary level.
    """

    name: str
    fieldname: str  # dotted path: an EconomicParameters field, or a cost-database datapoint
    levels: Dict[str, Any]  # level name -> value; None means "as shipped"
    is_data_overlay: bool = False


@dataclass
class Scenario:
    """One expanded scenario: parameter overrides plus data overlays.

    The flat, fully-resolved unit of evaluation — whatever mode produced it, a scenario is just an
    id and the two override dicts `evaluate_cube` applies before calling the evaluator. Keeping the
    two kinds apart to the very end is what lets parameter overrides be validated against the
    dataclass schema and overlays against the database entry schema, and what lets only the latter
    be recorded as `SCENARIO_OVERLAY` provenance.
    """

    id: str
    parameter_overrides: Dict[str, Any] = field(default_factory=dict)
    data_overlays: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ScenarioSet:
    """A scenario-set definition (data file or RenoVisor request block, §4.6).

    The authored form, as opposed to the expanded `Scenario` list: a base id, an expansion mode, the
    axes, and any hand-written named scenarios. It is what a user writes in a JSON file, what a
    system setup attaches through `bridge.EconomicContext.scenario_set`, and what
    ``--scenarios scenarios.json`` loads on the CLI; `expand()` turns it into the cells of a cube.

    Separating definition from expansion is what makes the explosion guard and the axis validation
    possible at load time, before any evaluation has been paid for.
    """

    base_id: str
    mode: str  # FACTORIAL | ONE_AT_A_TIME
    axes: List[ScenarioAxis] = field(default_factory=list)
    named_scenarios: List[Scenario] = field(default_factory=list)

    @classmethod
    def from_json(cls, raw: dict) -> "ScenarioSet":
        """Parses and validates against the EconomicParameters schema (hard error on unknowns).

        Validation happens here rather than at evaluation time so a typo in an axis name fails
        immediately and by name, in the spirit of §9's fail-fast rule — a silently ignored axis
        would produce a full, plausible, meaningless cube. Each axis and each named-scenario
        override is classified as parameter or data overlay by its path stem; parameter paths are
        checked against the `EconomicParameters` dataclass and against the non-sweepable list.

        Args:
            raw: The parsed scenario-set JSON (`base`, `mode`, `axes`, `named_scenarios`).

        Returns:
            The parsed set, ready to `expand()`.

        Raises:
            ScenarioDataError: On an unknown or non-sweepable parameter field, a dotted path into a
                non-dict field, or an unknown mode.
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
        """Expands axes per mode plus the named scenarios; always includes the base scenario.

        Turns the authored definition into the flat cell list `evaluate_cube` iterates. The base
        scenario — no overrides at all, id `base_id` — is always first, because every derived
        analysis measures against it: `tornado_data` and `equivalent_annual_cost_swings` return
        nothing without it. FACTORIAL produces one cell per combination of levels, with ids joined
        by ``|``, which is also the marker `tornado_data` uses to skip combination cells; and
        ONE_AT_A_TIME produces one cell per (axis, level). Levels are sorted by name so a given
        definition always expands to the same order and the exported cube diffs cleanly.

        Returns:
            All scenarios to evaluate: base first, then the expanded axes, then the named ones.

        Raises:
            ScenarioDataError: When the expansion exceeds `SCENARIO_ERROR_THRESHOLD` cells. Above
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
    """Whether a dotted path addresses a cost-database datapoint rather than a parameter.

    The one place the two scenario vocabularies are told apart, by the path's stem: a data file name
    (`devices_DE`, `energy_prices_DE`) means overlay, anything else means `EconomicParameters`.
    Deciding it by stem rather than by an author-declared flag is what keeps a data file from
    claiming to overlay a parameter or vice versa.
    """
    stem = fieldname.split(".", 1)[0]
    return stem.startswith("devices_") or stem.startswith("energy_prices_")


def _validate_parameter_path(fieldname: str, allow_dict_root: bool = False) -> None:
    """Axes address EconomicParameters fields by dotted path; unknown field = hard error.

    Enforces three things in order: the field is not one of the run-level choices that must not be
    swept (`NON_SWEEPABLE`, reported with its explanation), it exists on the `EconomicParameters`
    dataclass, and a dotted path only reaches into a field that is actually a dict — the two
    escalation-rate maps, which are keyed by carrier and by asset class respectively.

    Args:
        fieldname: The dotted path from the scenario definition.
        allow_dict_root: Relaxes the last check only. Set for named-scenario overrides, which state
            whole storylines and may also assign a complete dict to a dict-typed field
            (`apply_parameter_overrides` merges it key by key); axes stay restricted to the two
            escalation maps.

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
    """Returns a copy of the parameters with dotted-path overrides applied.

    Deep-copies first, so a scenario never mutates the caller's parameters and cells stay
    independent of the order they were evaluated in — a real hazard when one object is reused across
    hundreds of cube cells. Three assignment shapes are supported: a dotted path setting one key of
    a dict-typed field, a whole dict merged key by key into a dict-typed field, and a plain
    attribute assignment.

    Args:
        base: The run's parameters; left untouched.
        overrides: Dotted path or field name -> value, from one `Scenario`.

    Returns:
        A new `EconomicParameters` with the overrides applied.

    Raises:
        ScenarioDataError: If a dotted path targets a field that is not a dict.
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
    return params


def _coerce_dict_key(fieldname: str, key: str) -> Any:
    """Turns a JSON string key into the enum the target dict is actually keyed by.

    The escalation-rate maps are keyed by `EnergyCarrier` and `ComponentType`, but JSON only has
    string keys — without this, an override would insert a string key that no lookup ever finds and
    the scenario would silently do nothing. `ComponentType` is matched against both the member name
    and its value, since data files use either spelling; an unrecognized key is passed through
    unchanged rather than rejected.
    """
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
    """An economic scenario overriding a consumed input is rejected by default (§4.6).

    The machine-enforced half of the economic/physics boundary. If a controller consumed a tariff's
    price signal during the run (recorded as `consumed_tariff_ids`, §8.3), then re-billing that load
    profile under different energy prices is a *counterfactual*: the profile was optimized against
    prices that the scenario now denies. That may be exactly what a study wants, but it is a
    semantic choice, so it must be opted into via `allow_counterfactual_billing` rather than
    happening silently.

    Escalation-rate overrides are deliberately *not* blocked, and the code says so by computing the
    flag and then discarding it: escalation only projects years beyond the simulated one, and only
    the year-1 prices were ever consumed.

    Args:
        inputs: The stored inputs, for `consumed_tariff_ids`.
        scenario: The scenario about to be evaluated.
        params: That scenario's parameters, for the opt-in flag.

    Raises:
        ScenarioDataError: When the scenario overlays energy prices on a run whose controller
            consumed a tariff, without the opt-in.
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
    """Min / max / spread of one KPI across all scenarios of one perspective (§4.6).

    The robustness summary's unit of answer: how much does the headline number move when the
    economic assumptions move. Note what it is *not* — these extremes are taken across scenarios on
    the AVERAGE slot, so they are orthogonal to the min/avg/max band inside each cell, which
    expresses cost-*data* uncertainty (§3.9). A wide spread here means "the conclusion depends on
    the assumptions"; a wide band inside a cell means "the conclusion depends on the price data".
    """

    minimum: float
    maximum: float

    @property
    def spread(self) -> float:
        """How far the KPI travels across the scenario set."""
        return self.maximum - self.minimum


@dataclass
class ScenarioCube:
    """`results[perspective][scenario]`, each cell a full LifecycleCostResult (§4.6).

    The output of a sweep and the input of every derived analysis. Cells hold complete results
    rather than KPIs because a scenario moves every figure — the category pivot, the subsidy
    decisions, the CO2 result — and pinning the cube to one KPI at build time would force a re-sweep
    for the next question. `scenarios` keeps the expanded definitions (so an analysis can tell a
    one-at-a-time cell from a factorial combination) and `base_id` names the cell everything is
    measured against.

    Consumed by `tornado_data` and `robustness_summary` here, by the report's scenario section
    through `equivalent_annual_cost_swings` / `_spreads`, and by `export_cube_csv` / `_json`.
    """

    results: Dict[str, Dict[str, LifecycleCostResult]] = field(default_factory=dict)
    scenarios: List[Scenario] = field(default_factory=list)
    base_id: str = "central"

    def kpi(self, perspective: str, scenario: str, kpi_getter: Callable[[LifecycleCostResult], float]) -> float:
        """One KPI value from one cell.

        The cube's only accessor, deliberately taking a getter rather than a KPI name: which figure
        an analysis reads is the analysis's business, and this keeps the cube free of a KPI
        vocabulary of its own. Raises `KeyError` for an unknown perspective or scenario, which is
        the right behaviour for a cube that should be complete by construction.
        """
        return kpi_getter(self.results[perspective][scenario])

    def equivalent_annual_cost_swings(self, perspective: str) -> Dict[str, float]:
        """Per-scenario EAC swing vs. the base scenario (AVERAGE slot), base included as 0.

        The tornado data of the report's scenario section, which derived it in HTML
        (`reporting.py:1483-1492`, W4.1). Empty when the perspective has no base cell.
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
        """Min/max/spread of the headline KPI per perspective — the §4.6 robustness summary.

        Replaces the derivation in `reporting.py:1496-1501` (W4.1).

        Unlike `equivalent_annual_cost_swings` this needs no base cell: it reports the extremes over
        whatever cells exist, which is the "how far can this number travel at all" question the
        report's section 9 summarizes. Perspectives with no cells are omitted rather than reported
        with a degenerate spread.
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
    """Evaluates the full scenario cube on stored inputs (§4.6).

    The sweep itself: for every scenario, apply its parameter overrides to a copy of the parameters,
    check the billing boundary, build an overlaid copy of the cost database if it has data overlays,
    and evaluate every applicable perspective with a fresh evaluator. Cells are therefore fully
    independent — no state carries from one scenario to the next, which is what makes the cube
    reproducible regardless of iteration order.

    The physical facts never change: `inputs` is the same stored `EvaluationInputs` for every cell,
    which is the whole reason an economic sweep costs milliseconds per cell and needs no simulation
    (§4.6). Anything that *would* change the facts is a variant, not a scenario.

    Args:
        inputs: The stored evaluator inputs, shared by every cell.
        base_parameters: The parameters scenarios deviate from.
        perspectives: The perspectives to evaluate per scenario, normally the applicable subset of
            the default bundle.
        scenario_set: The authored definition; expanded here.
        database: Pre-loaded cost database, to avoid re-reading the data files per call. Loaded from
            `base_parameters` when omitted.
        subsidy_catalog: Optional catalog; without it the §10.1 flat shim applies, in every cell
            alike.

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
    """The headline KPI: equivalent annual cost, AVERAGE slot.

    The default every derived analysis here is parameterized with, so a tornado, a spread and a
    break-even all speak about the same number unless a caller says otherwise. The AVERAGE slot is
    the right default because scenario analysis varies assumptions, not data: comparing a LOW-slot
    cell against a HIGH-slot one would mix the two uncertainty mechanisms (§4.6). The one place that
    deliberately does mix them is `robustness_summary`'s slot-aware dominance flag.
    """
    return result.equivalent_annual_cost_in_euro.average


def tornado_data(
    cube: ScenarioCube, perspective: str, kpi_getter: Callable[[LifecycleCostResult], float] = default_kpi_getter
) -> List[Dict[str, Any]]:
    """Per axis/level swing vs. the base scenario (ONE_AT_A_TIME input, §4.6).

    The table behind a tornado diagram: one row per single-axis scenario with its KPI value, the
    base value and the difference — which, sorted by absolute swing, ranks the assumptions by how
    much the answer depends on them. That is the question a tornado exists to answer, and it is why
    the analysis is only meaningful on a ONE_AT_A_TIME set: a swing is attributable to an axis only
    if nothing else moved with it. Factorial combination cells (ids containing ``|``) and the base
    cell itself are skipped for exactly that reason, so a factorial cube yields an empty table
    rather than a misleading one.

    Args:
        cube: The evaluated cube.
        perspective: Which perspective's cells to read.
        kpi_getter: The figure to swing; equivalent annual cost (AVERAGE slot) by default.

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
    """Min/max/spread of the differential KPI across all scenarios, plus dominance flags (§4.6).

    Compares two *variants* — two separately simulated buildings, each swept over the same scenario
    set — and answers the question a study actually wants to publish: does the retrofit win, and
    does it keep winning when the assumptions move. The deltas are A minus B on a cost KPI, so a
    negative delta means A is cheaper; `a_dominates_b_in_every_scenario` is therefore True only when
    A is strictly cheaper in *every* cell, the strongest claim a scenario sweep supports.

    `a_dominates_b_slot_aware` is stronger still and crosses into the other uncertainty mechanism:
    it requires A's HIGH (most expensive) equivalent annual cost to stay below B's LOW in every
    scenario, i.e. A wins even when the price data conspires against it (§4.6). Note that this flag
    always reads the equivalent annual cost, whatever `kpi_getter` is — it is defined on the banded
    headline KPI, not on an arbitrary figure.

    Args:
        cube_a: The variant under investigation.
        cube_b: The reference variant; must have been swept over the same scenario set, since the
            cells are matched by scenario id.
        perspective: Which perspective to compare in.
        kpi_getter: The figure to difference; equivalent annual cost (AVERAGE slot) by default.

    Returns:
        `min_delta`, `max_delta`, `spread`, the two dominance flags, and the per-scenario `deltas`.

    Raises:
        ScenarioDataError: If `cube_a` holds no scenarios. Every figure below is a fold over the
            per-scenario deltas, so there is nothing to summarize and no honest value to return —
            an empty sweep is a caller error (a set that expanded to nothing, a cube that was
            never evaluated) and is named as such instead of surfacing as a bare `min()` failure.
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
    """Bisection on one EconomicParameters axis for the value where two variants cross (§4.6).

    Runs on the AVERAGE slot; the LOW/HIGH crossings are reported as a bracket.

    Answers the inverse of a sweep: instead of "what happens at 5 % interest", "up to which interest
    rate is the retrofit still worth it" — the parameter value at which the two variants' KPI
    difference crosses zero. Unlike everything else in this module it does not read a cube; it
    evaluates both variants afresh at each bisection step, which stays cheap because the evaluator
    is pure and neither the facts nor the database change between steps.

    The bracket is the honest part of the answer: the LOW and HIGH slots cross at different values,
    so the reported break-even is a point on the AVERAGE slot inside a range implied by the cost-data
    uncertainty. A sign check on the interval ends detects the "no crossing in range" case, which is
    reported as such rather than as a spurious root.

    Args:
        axis_field: Dotted `EconomicParameters` path to bisect on; validated like an axis, so a
            non-sweepable or unknown field is rejected up front.
        search_range: (low, high) bounds of the search.
        inputs_a: Stored inputs of the variant under investigation.
        inputs_b: Stored inputs of the reference variant.
        base_parameters: Parameters held fixed apart from `axis_field`.
        perspective: The single perspective the comparison is made in.
        database: Pre-loaded cost database; loaded from `base_parameters` when omitted.
        subsidy_catalog: Optional catalog, applied to both variants alike.
        kpi_getter: The figure to difference; equivalent annual cost (AVERAGE slot) by default.
        tolerance: Absolute width of the interval at which the bisection stops.
        max_iterations: Hard cap on bisection steps; the midpoint is returned if it is hit.

    Returns:
        The axis name and range, the AVERAGE-slot `break_even` (None if there is no crossing), the
        LOW/HIGH slot crossings as `bracket_low_slot` / `bracket_high_slot`, and
        `no_crossing_in_range`.

    Raises:
        ScenarioDataError: If `axis_field` is not a sweepable `EconomicParameters` path.
    """
    _validate_parameter_path(axis_field)
    base_database = database or CostDatabase(base_parameters.cost_database_path)

    def delta_at(value: float, slot: str) -> float:
        """A minus B at one axis value, in one uncertainty slot — the function being rooted.

        The AVERAGE slot goes through `kpi_getter` (so a caller can bisect on any figure), while the
        LOW/HIGH slots read the equivalent annual cost band directly, since that is the only KPI
        guaranteed to carry a band.
        """
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
        """Plain bisection of `delta_at` in one slot; None when the interval ends share a sign.

        Bisection rather than a faster root finder because the KPI is only piecewise smooth in most
        axes — subsidy caps, replacement years and tier tables all introduce kinks — and bisection
        is the method that cannot be thrown by them as long as the interval brackets a sign change.
        """
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

class CubeKpis:
    """The KPIs a scenario cube is exported with (§4.6).

    The CSV export is long format, so the set of exported figures is data rather than a column list:
    three headline KPIs per cell — net present cost, equivalent annual cost and the year-1 monthly
    cost — each written with its full min/avg/max band. They are deliberately few: the CSV is the
    interchange format for `scenario_evaluation` and for spreadsheets, while the complete typed
    results live in `scenario_cube.json`. A getter may return None (the monthly cost does, when a
    perspective does not define one), in which case the row is simply omitted.
    """

    BY_NAME: Dict[str, Callable[[LifecycleCostResult], Any]] = {
        "total_npv_in_euro": lambda result: result.total_npv_in_euro,
        "equivalent_annual_cost_in_euro": lambda result: result.equivalent_annual_cost_in_euro,
        "monthly_cost_year1_in_euro": lambda result: result.monthly_cost_year1_in_euro,
    }


def export_cube_csv(cube: ScenarioCube, path: str, variant: str = "default") -> None:
    """`scenario_cube.csv` in long format, consumable by scenario_evaluation (§4.6).

    One row per (variant, perspective, scenario, KPI) with the value's min/avg/max — the shape the
    existing `scenario_evaluation` cross-run aggregation already consumes, so a scenario sweep and a
    set of separately simulated variants can be plotted side by side without a converter. The
    `variant` column is what makes several cubes concatenable into one file.

    Args:
        cube: The evaluated cube.
        path: Full path of the CSV to write.
        variant: Label for the variant this cube belongs to; only meaningful when several cubes are
            combined.
    """
    with open(path, "w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["variant", "perspective", "scenario", "kpi", "value_min", "value_avg", "value_max"])
        for perspective, per_scenario in cube.results.items():
            for scenario_id, result in per_scenario.items():
                for kpi_name, getter in CubeKpis.BY_NAME.items():
                    band = getter(result)
                    if band is None:
                        continue
                    writer.writerow(
                        [variant, perspective, scenario_id, kpi_name, band.minimum, band.average, band.maximum]
                    )


def export_cube_json(cube: ScenarioCube, path: str, variant: str = "default") -> None:
    """`scenario_cube.json` with the typed cube for the webtool.

    The lossless counterpart of the CSV: every cell serialized as a full `LifecycleCostResult`, so a
    consumer can pivot, drill into categories or read the subsidy decisions of any scenario without
    re-running the sweep. `base_scenario` is included because most readings of a cube are relative
    to it (swings, tornados) and the base cell is not otherwise distinguishable from the rest.

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
