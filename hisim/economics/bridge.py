"""Postprocessing bridge: runs the lifecycle cost engine after a simulation (cost_spec.md §10).

Activation is opt-in via ``PostProcessingOptions.COMPUTE_LIFECYCLE_COSTS`` and side-effect
free: it only writes *new* files (lifecycle_costs.json, component_costs.*,
cash_flow_timeline.csv, cost_audit.csv, cost_audit_timeline_heatmap.png,
cost_parity_report.csv, lifecycle_kpis.json, economic_inputs.json,
cost_provenance.json). It never calls the legacy cost methods.

**This is the only place where `hisim.economics` meets the rest of HiSim.** Everything else in
the package is a pure function of `EvaluationInputs` plus data files; this module is what walks a
*finished* simulation — its wrapped components, its output declarations and its results DataFrame
— and turns them into that input record. It owns extraction and orchestration: collecting facts
and billing determinants, merging in what only the system setup can know (`EconomicContext`),
calling the evaluator once per perspective, and writing the export set. It deliberately owns no
economics at all: no price lookup, no discounting, no subsidy decision, and — since cost-spec-v2
W1.2 — not even the choice of price basis year, which is resolved downstream in
`evaluator.effective_price_basis_year` so the postprocessing path and the `evaluate` CLI derive
the same year from the same file.

**What breaks a run and what does not.** Computing lifecycle costs is opt-in, and asking for it
means asking for an answer: a fleet the cost model cannot describe therefore *fails the run*. The
`UnresolvableSubjectsError` of decision D7 — raised for an undeclared component (§9.2), for a
recognized component whose facts do not build, for a meter whose declared output column this run
does not contain, and for a declared fact the database cannot price — propagates out of
`postprocessing_main.py` and out of `hisim_main`. That guard used to be a
bare ``except Exception`` that logged ``"Lifecycle cost engine failed (legacy outputs are
unaffected): …"`` and continued, which meant the deliberate fail-fast of D7 arrived as a log line
in the middle of a successful run; it now re-raises `CostDataError` (of which
`UnresolvableSubjectsError` is one) and swallows only the accidents, so an incomplete cost model
cannot be mistaken for a complete one.

**Nothing the run asked for degrades into something else.** Four failures now abort rather than
continue, and they are all the same failure: the run asked a question and would otherwise have got
a *different* question's answer with a log line to explain it.

- A **cost database** that will not load — a wrong path must not turn "compute my lifecycle costs"
  into a run with no cost files.
- A **subsidy catalog** that will not load. It used to log an error and leave `catalog` at None,
  which silently continues under the §10.1 flat shim: a run configured with a catalog would then
  publish subsidy figures that have nothing to do with it. The failure is wrapped into a
  `CostDataError` carrying the path and the original exception.
- A **scenario cube** the setup declared and that fails to evaluate. It used to log ``"… (base
  results unaffected)"`` and leave the base exports in place, which is true and beside the point:
  the report then silently lacks the sensitivity section it was asked for.
- Anything failing **part-way through writing the exports**. The files already written are removed
  (`_remove_partial_exports`) before the exception propagates, so a half-written set cannot be read
  as a complete one — `postprocessing_main` deliberately logs and continues for a non-cost error,
  and without the cleanup that run finishes green over a partial cost report. `economic_inputs.json`
  is kept: it is the extract of the *simulation* and stays true whatever the engine did next.

The bridge is also the only consumer of `adapter.py`, and the reason `economic_inputs.json` is
written before any economics happens: the file must be a faithful extract of the simulation,
independent of cost-database state (cost-spec-v2 W1.1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, ClassVar, Dict, List, Optional, Tuple

import pandas as pd

from hisim import log
from hisim.economics import adapter
from hisim.economics.audit import build_input_audit, write_cost_audit, write_parity_report
from hisim.economics.carriers import EnergyCarrier, validate_energy_attribution
from hisim.economics.input_audit import write_input_audit
from hisim.economics.database import CostDatabase, CostDataError
from hisim.economics.evaluator import (
    EconomicEvaluator,
    EvaluationInputs,
    SubjectCostFacts,
    UnresolvedSubject,
    require_resolvable_subjects,
)
from hisim.economics.exports import (
    write_cash_flow_timeline,
    write_component_costs,
    write_lifecycle_costs_json,
    write_lifecycle_kpis,
    write_provenance_ledger,
)
from hisim.economics.facts import (
    BillingDeterminants,
    CostRelevance,
    ExistingAssetRegister,
    describe_undeclared_class,
    missing_meter_column_error,
)
from hisim.economics.parameters import EconomicParameters
from hisim.economics.perspectives import load_default_bundle, select_applicable
from hisim.economics.scenarios import ScenarioSet
from hisim.economics.serialization import write_inputs
from hisim.economics.subsidies import SubsidyCatalog, SubsidyContext
from hisim.loadtypes import LoadTypes, Units


#: The length of a reference year, used to turn a simulation's start/end dates into
#: `EvaluationInputs.simulated_period_fraction` — the factor the engine later uses to annualize a
#: partial-year run (§8.5). A flat 365-day year is deliberate: the fraction scales energy
#: quantities, so a leap day's worth of difference is far below the uncertainty band of any price
#: it is multiplied with.
SECONDS_PER_YEAR = 365 * 24 * 3600


@dataclass
class EconomicContext:
    """Everything a system setup can declare beyond what the simulation knows itself.

    Attach via ``simulation_parameters.set_economic_context(...)``. With an existing-asset
    register present, the default perspective bundle switches from greenfield to the full
    brownfield set (owner/landlord/tenant, macroeconomic, ...); with a subsidy context and
    ``EconomicParameters.subsidy_catalog_path`` set, the real subsidy engine replaces the
    flat shim. See system_setups/economic_example/ for a complete worked example.

    **The design question it answers**: a HiSim simulation models physics, so it can say how big
    the heat pump is and how many kWh crossed the meter, but it can say nothing about the *decision
    situation* — what stood in the cellar before, who is applying for which grant, whether the
    building is rented, which non-simulated envelope measures are part of the same package. Those
    facts are neither derivable from the results frame nor sensible to hard-code in the engine, so
    they are declared once by whoever defines the variant and merged into the simulation-derived
    `EvaluationInputs` by `_merge_context`. Everything here is therefore optional and additive:
    the context never overwrites a simulated quantity, it only supplies what the simulation left
    unset.

    **What a system-setup author can put in it**, roughly in order of impact:

    - `existing_assets` — an `ExistingAssetRegister` describing the pre-measure system. This is the
      single most consequential field: its mere presence switches the evaluated perspective bundle
      from greenfield to brownfield/status-quo (see `perspectives.select_applicable`) and turns on
      kept-asset accounting, residual values, removal costs and the anyway-cost credit (§4.1).
    - `subsidy_context` — the applicant/building answers the §5.3 eligibility conditions resolve
      against (income, dwelling units, heritage status, existing heating, iSFP). Unanswered fields
      stay *undetermined* rather than false (§5.7), which is reported rather than silently denied.
    - `extra_cost_facts` — `SubjectCostFacts` for cost subjects that are not simulation components
      at all, above all the building-envelope measures of README §3.2b (insulation, windows, doors),
      sized in m² of the respective element.
    - `technical_attributes_by_subject` — per-subject key/value pairs merged into the facts the
      adapter derived, for subsidy conditions the adapter cannot know (SCOP, refrigerant, achieved
      U-value).
    - the actor-model context (`living_area_in_m2`, `heated_floor_area_in_m2`,
      `current_cold_rent_in_euro_per_m2_month`, `building_specific_emissions_in_kg_per_m2_a`), which
      the §6.3/§6.4 CO2 split and modernization levy need to allocate costs between landlord and
      tenant;
    - `annual_heat_demand_in_kwh`, the denominator of the levelized-cost-of-heat KPI;
    - `scenario_set`, which additionally triggers the §4.6 cube evaluation into
      scenario_cube.csv/json and the report's scenario section.

    **What happens if you attach nothing at all** (the default): the run stays valid and produces
    lifecycle costs, but only the greenfield perspectives are evaluated — every device is charged as
    a new purchase into an empty building, nothing is credited as already existing, no sunk cost or
    anyway-cost applies. Subsidies fall back to the flat legacy shim, actor splits have no
    allocation basis, and the LCOH KPI is omitted for want of a heat demand. That is the correct
    answer to a question nobody asked in more detail — not a degraded one — but it is a *greenfield*
    answer, which is the thing to check first when brownfield figures are missing from a result set.
    """

    # Brownfield: what is already installed, and which measures replace what (§4.1).
    existing_assets: Optional[ExistingAssetRegister] = None
    # Applicant/building facts for the subsidy engine (§5.3).
    subsidy_context: Optional[SubsidyContext] = None
    # Additional cost subjects that are not simulation components — envelope measures (Q7).
    extra_cost_facts: List[SubjectCostFacts] = field(default_factory=list)
    # Technical attributes merged into component-derived facts by subject name (subsidy
    # conditions like SCOP/refrigerant that the adapter cannot know):
    technical_attributes_by_subject: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # Actor-model context (§6.3, §6.4):
    living_area_in_m2: Optional[float] = None
    heated_floor_area_in_m2: Optional[float] = None
    current_cold_rent_in_euro_per_m2_month: Optional[float] = None
    building_specific_emissions_in_kg_per_m2_a: Optional[float] = None
    # For the levelized cost of heat KPI:
    annual_heat_demand_in_kwh: Optional[float] = None
    # Scenario analysis (§4.6): evaluated into scenario_cube.csv/json and the report's
    # scenario section when set.
    scenario_set: Optional[ScenarioSet] = None

    #: The context fields that describe a quantity and can therefore only be non-negative.
    NON_NEGATIVE_FIELDS: ClassVar[Tuple[str, ...]] = (
        "living_area_in_m2",
        "heated_floor_area_in_m2",
        "current_cold_rent_in_euro_per_m2_month",
        "building_specific_emissions_in_kg_per_m2_a",
        "annual_heat_demand_in_kwh",
    )

    def __post_init__(self) -> None:
        """Refuses a negative quantity at declaration time, where the author can see it.

        Every scalar here is an area, a rent, an emission intensity or a demand — none of them can
        be negative, and none of them is checked anywhere downstream: a negative living area
        silently produces a negative EUR/m² KPI, a negative heat demand a negative levelized cost
        of heat, and both look like results rather than like the typo they are. Checking here means
        the refusal names the field while the system setup that declared it is on screen, rather
        than in a KPI table hours later.

        Raises:
            ValueError: If any of `NON_NEGATIVE_FIELDS` is set to a negative number.
        """
        negative = [
            name
            for name in self.NON_NEGATIVE_FIELDS
            if getattr(self, name) is not None and getattr(self, name) < 0
        ]
        if negative:
            raise ValueError(
                "EconomicContext fields describe quantities and cannot be negative: "
                + ", ".join(f"{name}={getattr(self, name)!r}" for name in negative)
            )


def _output_column_and_unit(
    component_name: str, field_name: str, all_outputs: List[Any], results: pd.DataFrame
) -> Optional[Tuple[pd.Series, str]]:
    """One output's per-timestep column together with the unit it was declared in.

    The results DataFrame has no named columns the engine could rely on, so an output is located
    positionally: `all_outputs` and the frame's columns are in the same order, and this is the one
    place in the bridge that knows it. The declared unit travels with the column because the energy
    balance reads channels that are sometimes power and sometimes energy, so it has to see what the
    component declared rather than assume the Wh the meter contract fixes.
    """
    for index, output in enumerate(all_outputs):
        if output.component_name == component_name and output.field_name == field_name:
            unit = getattr(output, "unit", None)
            return results.iloc[:, index], str(getattr(unit, "value", unit))
    return None


def _output_column(
    component_name: str, field_name: str, all_outputs: List[Any], results: pd.DataFrame
) -> Optional[pd.Series]:
    """One output's per-timestep column, or None when the run declares no such output.

    Returning None rather than an empty series for a missing output is what lets the callers
    distinguish "this meter measured nothing" from "this meter's declared field does not exist" —
    the second is a broken extraction and they refuse the run over it.
    """
    found = _output_column_and_unit(component_name, field_name, all_outputs, results)
    return None if found is None else found[0]


class EnergyUnitConversion:
    """Factors that turn a summed output column into kilowatt hours, keyed by its declared unit.

    HiSim components publish power and energy channels side by side — a PV system has both
    `ElectricityOutput` in W and `ElectricityEnergyOutput` in Wh — so every summing path reads the
    *declared* unit of the column it found instead of assuming one. `WATT` needs the timestep
    length as well, which is why it is a callable rather than a number.

    **One table, both paths.** The billing side (`_sum_output_column`, which turns a meter column
    into the kilowatt hours a carrier is charged for) and the energy-balance side
    (`_device_energy_flows`) convert through this same table, so a column can never be worth one
    number on the bill and another on the chart. The billing side used to carry its own hardcoded
    `* 1e-3`, which was right only because every meter column happens to be declared in Wh.

    A unit not in this table is not converted at all, and that is a refusal rather than a guess:
    the caller reports the column as an unresolved subject and the run stops, because a
    mis-declared output silently converted is a number three orders of magnitude wrong on a chart
    or on a bill.
    """

    WATT_HOURS_PER_KWH = 1000.0
    SECONDS_PER_HOUR = 3600.0
    #: Declared unit string (`loadtypes.Units` value) -> factor from the summed column to kWh,
    #: given the timestep length in seconds.
    BY_UNIT: Dict[str, Callable[[int], float]] = {
        Units.WATT_HOUR.value: lambda seconds: 1.0 / EnergyUnitConversion.WATT_HOURS_PER_KWH,
        Units.KWH.value: lambda seconds: 1.0,
        Units.WATT.value: lambda seconds: seconds / (
            EnergyUnitConversion.SECONDS_PER_HOUR * EnergyUnitConversion.WATT_HOURS_PER_KWH
        ),
    }

    @staticmethod
    def to_kwh(total: float, unit: str, seconds_per_timestep: int, column: str) -> float:
        """Converts one summed column to kWh by the unit it was declared in.

        Args:
            total: The summed column, in its declared unit (a power column integrates by the
                timestep, so its sum is W-timesteps).
            unit: The declared unit's value, as `_output_column_and_unit` reports it.
            seconds_per_timestep: The simulation's resolution, needed for a power column.
            column: `component.field` of the column, for the error message.

        Returns:
            The same quantity in kilowatt hours.

        Raises:
            CostDataError: If the unit is neither an energy nor a power unit this table converts.
        """
        factor = EnergyUnitConversion.BY_UNIT.get(unit)
        if factor is None:
            raise CostDataError(
                f"Output {column} is declared in {unit!r}, which is neither an energy nor a power "
                f"unit the cost engine converts ({', '.join(sorted(EnergyUnitConversion.BY_UNIT))}"
                "). Converting it anyway would put a number orders of magnitude wrong into the "
                "energy balance or onto a bill."
            )
        return total * factor(seconds_per_timestep)


def _device_energy_flows(
    component: Any,
    all_outputs: List[Any],
    results: pd.DataFrame,
    seconds_per_timestep: int,
) -> Dict[str, float]:
    """One component's energy-balance flows over the simulated period, as role -> kWh.

    The physical counterpart of `_billing_determinants`, and the data behind the household energy
    balance: for every `adapter.DeviceEnergySpec` this component's class declares, the named output
    column is located positionally, summed, converted to kWh by its own declared unit and filed
    under the spec's role. A battery's signed AC-power channel is split by sign so charging and
    discharging come out as two roles rather than one net number that would hide the round trip.

    Nothing here is priced and nothing here crosses the system boundary, which is why it is
    separate from the billing path: the flows of a component that is free of cost or not declared
    at all are just as real, and dropping them would leave the balance unattributed.

    Args:
        component: The finished simulation's component; its class name and `component_name` are
            read.
        all_outputs: The run's output declarations, in the frame's column order.
        results: The per-timestep results frame.
        seconds_per_timestep: Needed to integrate the columns declared in W.

    Returns:
        Role value -> kWh over the simulated period, positive magnitudes, zero-valued roles
        omitted. Empty for a class whose table row is explicitly empty, and for a class that moves
        no electricity at all.

    Raises:
        CostDataError: For a table row naming a constant the class does not declare, for a
            declared column this run did not produce, for a column in a unit the conversion table
            does not know, and for a class the table does not mention at all that nevertheless
            publishes electricity in a convertible unit. All four used to be a warning and a
            dropped flow, which is a balance that looks complete and is not; `build_evaluation_inputs`
            turns each of them into an `UnresolvedSubject` and the D7 check stops the run.
    """
    specs = adapter.resolve_device_energy_flows(component)
    if specs is None:
        _require_no_unplaced_electricity(component, all_outputs)
        return {}
    flows: Dict[str, float] = {}
    for spec in specs:
        column = _output_column_and_unit(component.component_name, spec.field_name, all_outputs, results)
        if column is None:
            raise CostDataError(
                f"Energy balance: component {component.component_name} is listed in "
                f"adapter.DeviceEnergySpecs with output {spec.field_name!r} for role "
                f"{spec.role.value}, which this run did not produce. Leaving the flow out would "
                "publish a household balance whose residual node silently absorbs it."
            )
        series, unit = column
        if spec.positive_part is True:
            total = float(series.clip(lower=0.0).sum())
        elif spec.positive_part is False:
            total = -float(series.clip(upper=0.0).sum())
        else:
            total = float(series.sum())
        value = EnergyUnitConversion.to_kwh(
            total, unit, seconds_per_timestep, f"{component.component_name}.{spec.field_name}"
        )
        if value:
            flows[spec.role.value] = flows.get(spec.role.value, 0.0) + value
    try:
        # The same rule the annualization and the deserializer apply, stated once and checked here
        # first: a role is a direction, so a magnitude that comes out negative means the column
        # this row named runs the other way and the row is wrong about it.
        validate_energy_attribution(
            {component.component_name: flows}, f"Energy balance: {type(component).__name__}"
        )
    except ValueError as err:
        raise CostDataError(str(err)) from err
    return flows


def _require_no_unplaced_electricity(component: Any, all_outputs: List[Any]) -> None:
    """Refuses a component class the energy-balance table has never been asked about.

    `adapter.DeviceEnergySpecs` is meant to be the one statement of who is in the household
    balance, which only holds if a class outside it is outside it *on purpose*. A class that
    publishes electricity in a unit the collector could convert and has no row at all is the case
    nobody decided: it might be a device whose flow belongs on the chart, or a controller whose
    watts are an instruction rather than a flow, and only a maintainer can say which. Before this
    check it was silently the second.

    Args:
        component: The wrapped component; its class name and `component_name` are read.
        all_outputs: The run's output declarations, scanned for this component's own.

    Raises:
        CostDataError: If the class publishes at least one `LoadTypes.ELECTRICITY` output in a
            unit `EnergyUnitConversion` converts.
    """
    columns = [
        output.field_name
        for output in all_outputs
        if output.component_name == component.component_name
        and getattr(output, "load_type", None) == LoadTypes.ELECTRICITY
        and str(getattr(getattr(output, "unit", None), "value", "")) in EnergyUnitConversion.BY_UNIT
    ]
    if not columns:
        return
    raise CostDataError(
        f"Component class {type(component).__name__} publishes electricity "
        f"({', '.join(sorted(columns))}) but has no row in adapter.DeviceEnergySpecs, so the "
        "household energy balance cannot say whether those kilowatt hours are a flow across a "
        "balance node or a control signal. Add a row: the roles it contributes, or an explicit "
        "empty tuple with a comment saying why it contributes none."
    )


def _sum_output_column(
    component_name: str,
    field_name: str,
    all_outputs: List[Any],
    results: pd.DataFrame,
    seconds_per_timestep: int,
) -> Optional[float]:
    """Sums one output column to kWh by its declared unit; None if the output does not exist.

    Routed through `EnergyUnitConversion` rather than the `* 1e-3` it used to hardcode, so the
    kilowatt hours a carrier is billed for and the kilowatt hours the energy balance draws are the
    same conversion of the same column. Every meter column is declared in Wh, so no bill moves;
    what changes is that a meter column redeclared in kW or kWh would now be converted correctly
    instead of by a factor of a thousand.

    Args:
        component_name: The meter's runtime name.
        field_name: The output column's name.
        all_outputs: The run's output declarations, in the frame's column order.
        results: The per-timestep results frame.
        seconds_per_timestep: The simulation's resolution, needed for a power column.

    Returns:
        The column's total in kWh, or None when the run declares no such output.

    Raises:
        CostDataError: If the column is declared in a unit the conversion table does not know.
    """
    found = _output_column_and_unit(component_name, field_name, all_outputs, results)
    if found is None:
        return None
    series, unit = found
    return EnergyUnitConversion.to_kwh(
        float(series.sum()), unit, seconds_per_timestep, f"{component_name}.{field_name}"
    )


def _power_series(
    component_name: str, field_name: str, all_outputs: List[Any], results: pd.DataFrame
) -> Optional[pd.Series]:
    """The raw per-timestep column of one output (W), unaggregated and unscaled.

    Unlike `_sum_output_column` this does not aggregate or rescale: capacity charges are billed on
    peaks, not on sums, so the whole series is handed to `_peaks_from_power_series`. None means the
    meter declared a `power_field` that the run did not produce, which the caller refuses the run
    over rather than billing the carrier without its capacity charge.
    """
    return _output_column(component_name, field_name, all_outputs, results)


def _peaks_from_power_series(
    series: pd.Series, seconds_per_timestep: int, billing_interval_minutes: int = 15
) -> tuple:
    """Billing-interval mean peaks (kW) per month plus the annual peak (§8.4).

    Capacity charges are not billed on instantaneous power but on the highest *mean* power over a
    metering interval (15 minutes in the German grid-fee regime), so the series is first averaged
    into intervals and only then maximized — over each month for MONTHLY_PEAK tariffs and over the
    whole series for ANNUAL_PEAK ones. This is the one billing determinant that cannot be recovered
    from an energy total, which is why the extraction side has to compute it while the full
    time series is still in memory.

    Two deliberate simplifications: a timestep that does not divide the billing interval evenly
    yields no peaks at all (empty list, 0.0) rather than a subtly wrong number from a ragged
    grouping — and says so in the log, because a carrier silently billed without its capacity
    charge is a bill that is wrong by a component nobody can see is missing — and "months" are
    blocks of intervals rather than calendar months, which is accurate
    enough for a charge that only ever reads the maxima. The block width is the interval count
    divided by twelve, rounded *up*, so every interval is billed by exactly one block and the last
    block is the short one when twelve does not divide the count — a partial-year run keeps the
    peak of its final days instead of losing it to a truncated tail. That also means fewer than
    twelve blocks for such a run; there are never more.

    Args:
        series: Per-timestep power in W (not energy).
        seconds_per_timestep: The simulation's resolution, used to size the interval grouping.
        billing_interval_minutes: Metering interval the tariff bills on; 15 by default.

    Returns:
        ``(monthly_peaks_in_kw, annual_peak_in_kw)`` — at most twelve monthly values, and the
        maximum interval mean over the whole series.
    """
    interval_seconds = billing_interval_minutes * 60
    if interval_seconds % seconds_per_timestep != 0:
        log.warning(
            f"Capacity charge: the simulation's {seconds_per_timestep} s timestep does not divide "
            f"the tariff's {billing_interval_minutes} min billing interval, so no interval means "
            "can be formed and this carrier is billed without its capacity charge. "
            "(tariffs.validate_billing_interval refuses this combination before a run when the "
            "contract is known in advance; this is the postprocessing-side report of the same "
            "mismatch.)"
        )
        return [], 0.0
    steps = interval_seconds // seconds_per_timestep
    kw_series = series.astype(float) * 1e-3
    interval_means = kw_series.groupby(kw_series.reset_index(drop=True).index // steps).mean()
    if interval_means.empty:
        return [], 0.0
    annual_peak = float(interval_means.max())
    # Ceil division, so twelve blocks of this width always cover the whole series and the last
    # block absorbs the remainder; floor division plus a [:12] truncation used to drop the tail.
    intervals_per_month = max(1, -(-len(interval_means) // 12))
    monthly_peaks = [
        float(interval_means.iloc[start:start + intervals_per_month].max())
        for start in range(0, len(interval_means), intervals_per_month)
    ][:12]
    return monthly_peaks, annual_peak


def _capacity_billing_intervals(wrapped_components: List[Any]) -> Dict[EnergyCarrier, int]:
    """The billing interval each carrier's capacity charge is metered on, from the run's providers.

    A capacity charge is billed on the highest *mean* power over the contract's metering interval
    (§8.4), and that interval is a property of the contract — 15 minutes in the German grid-fee
    regime, but not everywhere and not for every carrier. `_peaks_from_power_series` used to be
    called with its 15-minute default whatever the run's tariff said, so a contract billing on
    another interval was silently metered on the wrong one and its peaks, and therefore its
    capacity bill, were wrong by a factor nobody could see.

    The contract is read off the components themselves: a `TariffProvider` in the run holds the
    very `TariffContract` the postprocessing billing engine bills with, which is the whole point of
    §8.1. It is duck-typed (`contract.capacity_charge.billing_interval_in_minutes`) rather than
    imported, so the bridge keeps knowing nothing about individual component classes.

    Args:
        wrapped_components: The simulator's wrapped components, in registration order.

    Returns:
        Carrier -> billing interval in minutes, for every carrier a provider in this run declares a
        contract for. A carrier missing from the map is metered on the default interval; when two
        providers declare the same carrier the first one registered wins, and the second is a
        configuration a run should not have in the first place.
    """
    intervals: Dict[EnergyCarrier, int] = {}
    for wrapper in wrapped_components:
        contract = getattr(wrapper.my_component, "contract", None)
        capacity_charge = getattr(contract, "capacity_charge", None)
        carrier = getattr(contract, "carrier", None)
        minutes = getattr(capacity_charge, "billing_interval_in_minutes", None)
        if carrier is not None and minutes is not None and carrier not in intervals:
            intervals[carrier] = int(minutes)
    return intervals


def _billing_determinants(
    component: Any,
    all_outputs: List[Any],
    postprocessing_results: pd.DataFrame,
    simulation_parameters: Any,
    billing_intervals: Optional[Dict[EnergyCarrier, int]] = None,
) -> Optional[BillingDeterminants]:
    """One component's carrier flows: the adopted §3.4 hook first, the adapter's MeterSpec second.

    The energy-side mirror of the precedence `adapter.extract_cost_facts` applies to cost facts
    (§9.1), and what wired up `Component.get_energy_flow_facts` (issue #18): a meter that declares
    its own boundary is believed, and the class-name table is consulted only for the meters that
    have not adopted the hook. Before this, the hook was declared and implemented but never
    called, so the meter boundary existed twice with nothing keeping the two in agreement.

    **The one thing the hook cannot say, and what happens to it.** `EnergyFlowFacts` carries a
    carrier, integrated kWh and optionally a simulated cost/revenue, but no capacity peaks. That
    is not expressible in the record, so peaks keep coming from the `MeterSpec` when the component
    also has a table entry, read from its `power_field`. Nothing else the hook declares is touched:
    the kWh it reports are the kWh that get billed, for every carrier, because a fuel quoted per
    ton or per liter is converted on the price side instead (D26). A hook-only meter with no table
    entry therefore gets no peaks and must declare `cost_relevance = METER` itself for the bridge
    to reach it at all.

    Args:
        component: The component to read; non-meters yield None from both paths.
        all_outputs: The run's output declarations (positional column index).
        postprocessing_results: The per-timestep results frame.
        simulation_parameters: For `seconds_per_timestep`, which sizes the peak intervals.
        billing_intervals: Carrier -> capacity-charge billing interval in minutes, from the run's
            tariff providers (`_capacity_billing_intervals`). A carrier that is not in the map is
            metered on `_peaks_from_power_series`' default.

    Returns:
        The billing determinants for this component's carrier, or None when it meters nothing. A
        component answering through the hook owns that judgement itself, and a zero it reports is
        taken as a measured zero.

    Raises:
        CostDataError: If a column the meter's class declares — bought energy, sold energy or the
            power series the peaks come from — is not among this run's outputs. It used to warn
            and leave the carrier unbilled (or the peaks at zero), which published a bill missing
            a flow; `build_evaluation_inputs` now turns it into an unresolved subject and the
            evaluation aborts (D7).
    """
    meter_spec = adapter.get_meter_spec(component)
    flows = adapter.get_energy_flow_facts(component, all_outputs, postprocessing_results)
    if flows is None and meter_spec is None:
        return None
    if flows is not None:
        determinants = BillingDeterminants.from_energy_flow(flows)
    else:
        assert meter_spec is not None
        seconds = simulation_parameters.seconds_per_timestep
        bought_kwh: Optional[float] = _sum_output_column(
            component.component_name,
            meter_spec.bought_field,
            all_outputs,
            postprocessing_results,
            seconds,
        )
        if bought_kwh is None:
            raise missing_meter_column_error(component.component_name, meter_spec.bought_field, "bought energy")
        sold_kwh = 0.0
        if meter_spec.sold_field:
            sold_column = _sum_output_column(
                component.component_name,
                meter_spec.sold_field,
                all_outputs,
                postprocessing_results,
                seconds,
            )
            if sold_column is None:
                raise missing_meter_column_error(component.component_name, meter_spec.sold_field, "sold energy")
            sold_kwh = sold_column
        determinants = BillingDeterminants(
            carrier=meter_spec.carrier, energy_bought_in_kwh=bought_kwh, energy_sold_in_kwh=sold_kwh
        )
    if meter_spec is not None and meter_spec.power_field:
        series = _power_series(
            component.component_name, meter_spec.power_field, all_outputs, postprocessing_results
        )
        if series is None:
            raise missing_meter_column_error(component.component_name, meter_spec.power_field, "peak power")
        interval_minutes = (billing_intervals or {}).get(determinants.carrier)
        peaks = (
            _peaks_from_power_series(series, simulation_parameters.seconds_per_timestep)
            if interval_minutes is None
            else _peaks_from_power_series(
                series, simulation_parameters.seconds_per_timestep, interval_minutes
            )
        )
        determinants.peak_per_billing_period_in_kw, determinants.annual_peak_in_kw = peaks
    return determinants


def _non_zero_energy_flows(determinants: Optional[BillingDeterminants]) -> Tuple[str, ...]:
    """The energy figures a set of billing determinants reports as non-zero, named (D7).

    The check behind the adapter's zero-size rule. `adapter._resolved_or_not_installed` excludes a
    component configured at zero size from pricing on the grounds that such a device moves no
    energy and its output columns sum to zero of their own accord — a plausible claim about every
    component that exists today, but a claim, and one whose failure mode is silent: a device
    excluded from capex whose meter still reports kilowatt-hours would have its energy billed
    while its hardware is free. This turns the claim into evidence the bridge can act on.

    Only the *energy* determinants are inspected, since they are what a bill is raised on: energy
    bought and sold, the per-band split a time-of-use contract is billed by, and the integrated
    cost and revenue a dynamic contract carries instead of a price lookup. Peaks are deliberately
    not included — a capacity peak is derived from a power series and is a shape, not a quantity
    that crossed the boundary — and neither is the mean spot price, which is a price.

    Args:
        determinants: The component's determinants, or None when it meters nothing.

    Returns:
        The names of the non-zero figures, in a fixed order, for the refusal message; empty when
        the component metered nothing or metered exactly zero.
    """
    if determinants is None:
        return ()
    figures: List[Tuple[str, Optional[float]]] = [
        ("energy_bought_in_kwh", determinants.energy_bought_in_kwh),
        ("energy_sold_in_kwh", determinants.energy_sold_in_kwh),
        ("cost_integrated_in_euro", determinants.cost_integrated_in_euro),
        ("revenue_integrated_in_euro", determinants.revenue_integrated_in_euro),
    ]
    figures.extend(
        (f"energy_bought_per_band_in_kwh[{band}]", value)
        for band, value in sorted(determinants.energy_bought_per_band_in_kwh.items())
    )
    return tuple(name for name, value in figures if value)


def build_evaluation_inputs(
    wrapped_components: List[Any],
    all_outputs: List[Any],
    postprocessing_results: pd.DataFrame,
    simulation_parameters: Any,
) -> EvaluationInputs:
    """Collects facts and billing determinants from a finished simulation.

    The extraction half of the cost-spec-v2 seam-1 cut: it walks every wrapped component once and
    asks four questions — which energy-balance flows does it publish (`_device_energy_flows`); is
    this component priced, free of cost or a meter (`effective_cost_relevance`); what are its
    `ComponentCostFacts`; and, for meters, what crossed the boundary (`_billing_determinants`,
    hook first and `MeterSpec` second). A component can answer more than one of them: a meter with
    capex contributes billing determinants, cost facts *and* the grid import/export flows, which
    is why the branches below are sequential rather than exclusive. The energy question is asked
    first and unconditionally, because the household energy balance is physics rather than
    accounting — a free-of-cost PV system and an undeclared load profile move real kilowatt hours.
    The result is the plain-data record that `write_inputs` persists and the evaluator prices — no
    prices, no perspective, no economics of any kind are decided here.

    Six things it also decides, and none of them is silent. A component whose **energy-balance
    flows cannot be placed** becomes an `UnresolvedSubject`: a class the `DeviceEnergySpecs` table
    has never been asked about that nevertheless publishes electricity, a listed class whose
    declared column this run did not produce, and a column in a unit the conversion table does not
    know. All three used to warn and drop the flow, which publishes a household balance whose
    residual node has silently swallowed a terminal. An `UNDECLARED` component becomes an
    `UnresolvedSubject` naming its class: §9.2 makes the declaration mandatory, so a component
    that reaches the cost engine without one is a defect in that component, not a component
    outside the cost model, and the downstream D7 check aborts the evaluation on it. A component
    the adapter *does* recognize but cannot describe — a registered extractor that yielded
    nothing, a meter whose configured fuel maps to no carrier — becomes an `UnresolvedSubject` the
    same way (issues #2 and #3), rather than quietly missing from the cost report. So does a meter
    whose class declares an output column this run does not contain: it used to warn and leave the
    carrier unbilled, which published a bill missing a flow. So does a component excluded from
    pricing as *not installed* whose meter nevertheless reported energy (`_non_zero_energy_flows`):
    the zero-size exclusion rests on the claim that such a device moves no energy, and where the
    determinants contradict it, billing the flows of a device the result says is absent is not an
    answer this layer may pick. A run with no meter flows *at all* stays a warning, because a
    system with nothing metered is a legitimate — if unpriced — configuration rather than a broken
    meter (§3.4). And the simulated
    period is converted into `simulated_period_fraction`, which the engine uses to annualize; runs
    longer than a year are clamped to one full year with a warning (cost_module_issues.md #15).

    Finally, the setup-declared `EconomicContext` — if `simulation_parameters` carries one — is
    merged in, so what the simulation knows and what only the author knows arrive as one record.

    Args:
        wrapped_components: The simulator's `ComponentWrapper` list, in registration order.
        all_outputs: The run's `ComponentOutput` declarations; their order matches the frame's
            columns and is what makes the positional column lookup work.
        postprocessing_results: The in-memory results DataFrame (per-timestep values), read
            directly rather than via the exported CSVs.
        simulation_parameters: The run's `SimulationParameters`, for the year, the timestep, the
            simulated period and the optional `economic_context` attachment.

    Returns:
        A fully populated `EvaluationInputs` — the exact record that gets written to
        `economic_inputs.json`.
    """
    cost_facts: List[SubjectCostFacts] = []
    billing: List[BillingDeterminants] = []
    attribution: Dict[str, Dict[str, float]] = {}
    unresolved: List[UnresolvedSubject] = []
    not_installed: List[str] = []
    billing_intervals = _capacity_billing_intervals(wrapped_components)
    for wrapper in wrapped_components:
        component = wrapper.my_component
        subject = component.component_name
        # The energy balance is a physical record, not a cost classification: it is collected for
        # every component, before and independently of the cost-relevance branch below, because a
        # PV system that is FREE_OF_COST or a load profile that is UNDECLARED still moves the
        # kilowatt hours the household balance is made of.
        try:
            energy_flows = _device_energy_flows(
                component,
                all_outputs,
                postprocessing_results,
                simulation_parameters.seconds_per_timestep,
            )
        except CostDataError as err:
            # A device whose flow cannot be placed is the same kind of failure as a subject whose
            # cost cannot be resolved: the answer the run asked for would come out incomplete, and
            # a chart missing a terminal reads as a house that does not use that energy.
            unresolved.append(UnresolvedSubject(subject=subject, reason=str(err)))
            continue
        if energy_flows:
            attribution[subject] = energy_flows
        try:
            relevance = adapter.effective_cost_relevance(component)
            if relevance == CostRelevance.UNDECLARED:
                # §9.2 makes the declaration mandatory, so this is a defect in the component, not
                # a component outside the cost model: it becomes an unresolved subject and the D7
                # check below refuses to price the rest of the fleet around the hole.
                unresolved.append(
                    UnresolvedSubject(subject=subject, reason=describe_undeclared_class(type(component)))
                )
                continue
            if relevance == CostRelevance.FREE_OF_COST:
                continue
            determinants = _billing_determinants(
                component, all_outputs, postprocessing_results, simulation_parameters, billing_intervals
            )
            # Meters can also be PRICED devices (the meter hardware itself has capex).
            extraction = adapter.extract_cost_facts(component)
        except CostDataError as err:
            # A configuration the adapter cannot map to a carrier or an asset class (issue #3):
            # the component becomes an unresolved subject instead of being billed at a guessed
            # price, and the D7 check downstream refuses to produce partial results.
            unresolved.append(UnresolvedSubject(subject=subject, reason=str(err)))
            continue
        flows = _non_zero_energy_flows(determinants) if extraction.not_installed_reason else ()
        if flows:
            # A component excluded from capex as "not installed" that nevertheless metered energy
            # is a contradiction, not a skip: the adapter's zero-size rule rests on a zero-size
            # device moving no energy, and here it moved some. Billing it would charge a device
            # the result says does not exist; not billing it would drop measured energy off the
            # boundary. Both are wrong answers, so the run refuses (D7) and names the flows.
            unresolved.append(
                UnresolvedSubject(
                    subject=subject,
                    reason=(
                        f"{extraction.not_installed_reason}, yet its meter reported energy flows "
                        f"({', '.join(flows)}). A device configured at zero size cannot move "
                        "energy: either the size or the metering is wrong, and pricing the run "
                        "either way would publish a bill the cost model does not stand behind."
                    ),
                )
            )
            continue
        if determinants is not None:
            billing.append(determinants)
        if extraction.facts is not None:
            cost_facts.append(SubjectCostFacts(subject=subject, facts=extraction.facts))
        elif extraction.unresolved_reason is not None:
            # Registered class, no facts: not an undeclared component and not a priced one either
            # — the §9.2 hole issue #2 closed.
            unresolved.append(UnresolvedSubject(subject=subject, reason=extraction.unresolved_reason))
        elif extraction.not_installed_reason is not None:
            # Configured at zero size and, per the check above, metering nothing: absent from the
            # building, so absent from the cost model - a skip, not a failure. It is a warning
            # rather than a note because an asset that silently leaves the cost model is exactly
            # the omission a reader has to notice.
            not_installed.append(f"{subject}: {extraction.not_installed_reason}")
    if not_installed:
        log.warning(
            "Lifecycle cost engine: components configured at zero size, excluded from the cost "
            f"model as not installed: {'; '.join(sorted(not_installed))}"
        )
    if unresolved:
        log.warning(
            "Lifecycle cost engine: components that could not be described for the cost model "
            f"(evaluation will abort, cost-spec-v2 §8/D7): "
            f"{', '.join(sorted(item.subject for item in unresolved))}"
        )
    if not billing:
        log.warning(
            "Lifecycle cost engine: no meter flows found — energy costs are missing from the "
            "lifecycle results (§3.4)."
        )
    duration_seconds = (simulation_parameters.end_date - simulation_parameters.start_date).total_seconds()
    fraction = min(1.0, duration_seconds / SECONDS_PER_YEAR)
    if duration_seconds > SECONDS_PER_YEAR * 1.001:
        log.warning(
            "Simulation spans more than one year; the lifecycle cost engine uses the first "
            "simulated year (cost_module_issues.md #15)."
        )
        fraction = 1.0
    elif fraction < 1.0:
        # The short-run case, which is the common one and used to pass without a word: every
        # energy quantity and every bill is divided by this fraction to reach a year, so a one-day
        # run multiplies its measurements by 365 and publishes the product as a lifecycle figure.
        # Saying so at warning level is the least that owes the reader; the plausibility panel
        # carries the same statement into the outputs themselves (see compute_lifecycle_costs).
        log.warning(
            f"Lifecycle cost engine: this run simulates {duration_seconds / 86400.0:.3g} day(s), "
            f"which is {fraction:.4g} of a year, so year-1 energy quantities and bills are "
            f"extrapolated by a factor of {1.0 / max(fraction, 1e-12):.4g} (§8.5). Every lifecycle "
            "figure from this run is an extrapolation of the simulated period, not a measurement "
            "of a year: it inherits whatever weather, occupancy and control behaviour those "
            f"{duration_seconds / 86400.0:.3g} day(s) happened to contain."
        )
    inputs = EvaluationInputs(
        simulation_year=simulation_parameters.year,
        simulated_period_fraction=fraction,
        cost_facts=cost_facts,
        billing=billing,
        energy_attribution_by_subject_in_kwh=attribution,
        unresolved_subjects=unresolved,
    )
    context: Optional[EconomicContext] = getattr(simulation_parameters, "economic_context", None)
    if context is not None:
        _merge_context(inputs, context)
    return inputs


def _merge_context(inputs: EvaluationInputs, context: EconomicContext) -> None:
    """Merges the setup-declared EconomicContext into the simulation-derived inputs.

    The merge is deliberately one-directional and non-destructive: the register, the subsidy context
    and the extra cost subjects are *added*, technical attributes are *updated into* the facts the
    adapter derived (so a declared SCOP joins the extracted size instead of replacing the facts),
    and every scalar fills a gap rather than overruling a value the extraction already established.
    That ordering is what keeps the seam-1 promise that `economic_inputs.json` never contradicts
    the simulation it came from.

    "Declared" is `is not None`, not truthiness. The scalars used to be merged with
    ``context.x or inputs.x``, which silently dropped a declared **0.0** — a building with no
    heat demand, a rent-free unit, an emission intensity of zero — and left whatever the extraction
    had instead. A zero is a statement, and the difference between "nobody said" and "somebody said
    none" is exactly what this merge exists to preserve.

    Args:
        inputs: The simulation-derived record, mutated in place.
        context: What the system setup declared; every field optional.
    """
    if context.existing_assets is not None:
        inputs.existing_assets = context.existing_assets
    if context.subsidy_context is not None:
        inputs.subsidy_context = context.subsidy_context
    for subject_facts in context.extra_cost_facts:
        inputs.cost_facts.append(subject_facts)
    matched_subjects = set()
    for subject_facts in inputs.cost_facts:
        extra_attributes = context.technical_attributes_by_subject.get(subject_facts.subject)
        if extra_attributes is not None:
            matched_subjects.add(subject_facts.subject)
            subject_facts.facts.technical_attributes.update(extra_attributes)
    unmatched = sorted(set(context.technical_attributes_by_subject) - matched_subjects)
    if unmatched:
        # A subject name that matches nothing is a typo or a renamed component, and its attributes
        # — a SCOP, a refrigerant, an achieved U-value — are exactly what a subsidy condition
        # resolves against. Dropping them silently turns "the grant was denied" into a mystery.
        log.warning(
            "Lifecycle cost engine: EconomicContext.technical_attributes_by_subject names "
            f"subject(s) that no extracted cost subject matches, so their attributes were not "
            f"applied: {', '.join(unmatched)}. Known subjects: "
            f"{', '.join(sorted(item.subject for item in inputs.cost_facts)) or '(none)'}."
        )
    for name in EconomicContext.NON_NEGATIVE_FIELDS:
        # `is not None`, not truthiness: a declared 0.0 is a statement, not an absent value.
        declared = getattr(context, name)
        if declared is not None:
            setattr(inputs, name, declared)


def _remove_partial_exports(written: List[str]) -> None:
    """Deletes the export files this run wrote before it failed.

    The engine writes its export set file by file, so a failure part-way through leaves a directory
    holding *some* of it — a `lifecycle_costs.json` with no `cash_flow_timeline.csv` beside it, or a
    KPI file whose matrix never finished. Nothing downstream distinguishes that from a complete set:
    `postprocessing_main` deliberately logs and continues for a non-cost error, the run finishes
    green, and the half-written files are read later as the answer. Removing them makes the absence
    of the cost set the signal that the cost set is absent.

    `economic_inputs.json` is deliberately **not** in the list a caller builds: it is the faithful
    extract of the simulation (W1.1), it is written before any economics happens, and the D7
    refusal message promises it survives an abort. It says what the run contained, not what the
    engine concluded, so it is still true after the engine failed.

    Removal failures are swallowed on purpose: this runs while another exception is propagating,
    and turning a cleanup problem into the reported error would hide the failure that mattered.

    Args:
        written: Paths the engine wrote in this run, in the order it wrote them.
    """
    import os

    removed = []
    for path in written:
        try:
            if os.path.isfile(path):
                os.remove(path)
                removed.append(os.path.basename(path))
        except OSError as err:  # pragma: no cover - a cleanup problem must not mask the failure
            log.warning(f"Lifecycle cost engine: could not remove the partial export {path}: {err}")
    if removed:
        log.error(
            "Lifecycle cost engine failed part-way through writing its exports; removed the "
            f"{len(removed)} file(s) it had already written so an incomplete set cannot be read "
            f"as a complete one: {', '.join(removed)}. economic_inputs.json is kept: it is the "
            "extract of the simulation and is unaffected by the failure."
        )


def _resolve_economic_parameters(simulation_parameters: Any) -> EconomicParameters:
    """The run's `EconomicParameters`: what the setup attached, else the country's defaults.

    Both entry points of this module need them and both used to derive them with the same four
    lines, which is three lines too many for a fallback that decides which cost database is read
    and which country's data is priced against: the two copies drifting would mean the parity
    report compares a run against a differently-parameterized version of itself.

    Args:
        simulation_parameters: The run's parameters, optionally carrying `economic_parameters`.

    Returns:
        The attached parameters, or defaults for the simulation's country (`DE` when it has none).
    """
    parameters: Optional[EconomicParameters] = getattr(simulation_parameters, "economic_parameters", None)
    if parameters is not None:
        return parameters
    return EconomicParameters(country=getattr(simulation_parameters, "country", "DE"))


def compute_lifecycle_costs(
    wrapped_components: List[Any],
    all_outputs: List[Any],
    postprocessing_results: pd.DataFrame,
    simulation_parameters: Any,
    generate_report: bool = False,
) -> None:
    """The COMPUTE_LIFECYCLE_COSTS entry point, called from postprocessing (additive).

    ``generate_report`` (option LIFECYCLE_COST_REPORT) additionally writes the
    human-readable outputs: cost_summary.md, lifecycle_report.html and the PNG set.

    This is the whole run, in order: resolve the economic parameters (falling back to defaults for
    the simulation's country when the setup attached none), load the cost database, extract
    `EvaluationInputs` from the finished simulation, persist that faithful extract *before* anything
    economic touches it (W1.1), load the subsidy catalog if one is configured, run the D7 resolution
    check, select the applicable perspectives from the default bundle, evaluate them all into one
    `EvaluationMatrix`, and write the export set. Ordering is not cosmetic here: it runs before the
    legacy COMPUTE_OPEX/COMPUTE_CAPEX blocks because `get_cost_capex` mutates component configs as a
    side effect and would contaminate the facts this function reads (§10.0 rule 4), which is also
    why the parity report is a separate second pass (`write_parity_from_stored_inputs`).

    Files written on a plain COMPUTE_LIFECYCLE_COSTS run: `economic_inputs.json` first — before any
    pricing, so it exists even when the resolution check then aborts — then `lifecycle_costs.json`,
    `component_costs.json`/`.csv`,
    `cash_flow_timeline.csv`, `cost_provenance.json`, `lifecycle_kpis.json`, `cost_audit.csv`,
    `cost_audit.json` and the audit's own `cost_audit_timeline_heatmap.png`. With a declared
    scenario set additionally `scenario_cube.csv`/`.json`, and with ``generate_report``
    additionally `cost_summary.md`, `lifecycle_report.html` and the report's PNG set. No
    legacy file is read, written or otherwise touched.

    Failure behaviour (see the module docstring): everything propagates. A cost database or a
    subsidy catalog that will not load, a declared scenario cube that will not evaluate and the D7
    `UnresolvableSubjectsError` all abort the evaluation as a `CostDataError`, which postprocessing
    re-raises rather than logging, so a run that asked for lifecycle costs and cannot have them
    fails instead of finishing with a cost report missing a component, a subsidy engine or a
    section. Whatever the failure, the export files this call had already written are removed
    first, so no reader can mistake a partial set for a whole one; `economic_inputs.json` is
    written before any of it and deliberately survives.

    Raises:
        CostDataError: If the cost database or a configured subsidy catalog cannot be loaded, if a
            declared scenario cube cannot be evaluated, or if any cost subject is unresolvable
            (D7).

    Args:
        wrapped_components: The simulator's wrapped components.
        all_outputs: The run's output declarations (column order of the results frame).
        postprocessing_results: The in-memory per-timestep results.
        simulation_parameters: The run's parameters; optionally carrying `economic_parameters`
            and `economic_context`.
        generate_report: Whether option LIFECYCLE_COST_REPORT was set as well.
    """
    result_directory = simulation_parameters.result_directory
    parameters = _resolve_economic_parameters(simulation_parameters)
    # A database that will not load is a CostDataError and propagates: a run that asked for
    # lifecycle costs with an unreadable cost database must fail, not finish without cost files
    # and a line in the log (same principle as the D7 abort below).
    database = CostDatabase(parameters.cost_database_path)
    inputs = build_evaluation_inputs(wrapped_components, all_outputs, postprocessing_results, simulation_parameters)
    # The faithful extract goes to disk before anything economic touches it (W1.1): what the
    # file contains must depend on the simulation only, never on cost-database state.
    write_inputs(inputs, result_directory)
    # A configured catalog that will not load — or whose path does not resolve — is not a
    # degradation, it is a different calculation: evaluation would silently fall back to the §10.1
    # flat shim and publish subsidy figures that have nothing to do with the catalog the run asked
    # for. `load_configured` is the one place that decision lives, shared with the CLI so both
    # paths refuse identically, and it raises the `CostDataError` postprocessing re-raises.
    catalog = SubsidyCatalog.load_configured(parameters.country, parameters.subsidy_catalog_path)
    evaluator = EconomicEvaluator(database, parameters, catalog)
    # D7 (cost-spec-v2 §8): an unresolvable subject aborts the whole cost evaluation — no partial
    # results. postprocessing_main catches it and logs an error; the legacy outputs are unaffected.
    require_resolvable_subjects(inputs, evaluator)
    perspectives = select_applicable(load_default_bundle(), has_register=inputs.existing_assets is not None)
    written: List[str] = []
    try:
        matrix = evaluator.evaluate_matrix(inputs, perspectives)
        written.append(write_lifecycle_costs_json(matrix, result_directory))
        written.extend(write_component_costs(matrix, result_directory))
        written.append(write_cash_flow_timeline(matrix, result_directory))
        provenance_path = write_provenance_ledger(matrix, result_directory)
        if provenance_path is not None:
            written.append(provenance_path)
        written.append(write_lifecycle_kpis(matrix, result_directory))
        first_result = next(iter(matrix.results.values()), None)
        input_audit = None
        if first_result is not None:
            # Resolved once (W4.6): cost_audit.csv and the report's section 1 are two renderings.
            input_audit = build_input_audit(inputs, database, parameters, first_result)
            written.append(write_cost_audit(input_audit, result_directory))
            written.append(write_input_audit(input_audit, result_directory))
            # The year x category ledger heatmap is the visual twin of the audit table, so it is
            # written here, beside `cost_audit.csv`, rather than with the report's PNG set — and
            # from here rather than from `audit.py`, which the seam-4 import lint keeps free of
            # renderers. It joins `written` like every other export, so a failure further down
            # removes the PNG together with the CSVs it belongs to.
            from hisim.economics.report_plots import write_audit_plots

            written.extend(write_audit_plots(first_result, result_directory))
        # The parity report is written later, after the legacy COMPUTE_OPEX/COMPUTE_CAPEX blocks
        # produced their CSVs (see write_parity_from_stored_inputs and postprocessing_main).
        # Scenario analysis (§4.6) when the setup declared a scenario set.
        context: Optional[EconomicContext] = getattr(simulation_parameters, "economic_context", None)
        scenario_cube = None
        if context is not None and context.scenario_set is not None:
            from hisim.economics.scenarios import evaluate_cube, export_cube_csv, export_cube_json
            import os

            try:
                scenario_cube = evaluate_cube(
                    inputs, parameters, perspectives, context.scenario_set, database, catalog
                )
            except CostDataError:
                raise
            except Exception as err:
                # A declared scenario set is a requested section of the answer, not a bonus. A
                # report missing the very sensitivity analysis it was asked for, with nothing but a
                # log line to say so, is a report a reader takes for complete.
                raise CostDataError(
                    "Lifecycle cost engine: the scenario cube the setup declared could not be "
                    f"evaluated ({type(err).__name__}: {err}), so scenario_cube.csv/json and the "
                    "report's scenario section would be missing from a run that asked for them."
                ) from err
            cube_csv = os.path.join(result_directory, "scenario_cube.csv")
            cube_json = os.path.join(result_directory, "scenario_cube.json")
            export_cube_csv(scenario_cube, cube_csv)
            export_cube_json(scenario_cube, cube_json)
            written.extend([cube_csv, cube_json])
            log.information(
                f"Lifecycle cost engine: evaluated {sum(len(v) for v in scenario_cube.results.values())} "
                "scenario cells into scenario_cube.csv/json."
            )
        if generate_report and matrix.results:
            from hisim.economics.plausibility import run_plausibility_checks
            from hisim.economics.report_plots import write_report_plots
            from hisim.economics.reporting import (
                render_plausibility_findings,
                write_cost_summary,
                write_lifecycle_report,
            )

            # The simulated fraction goes in so the §8.5 extrapolation of a short run is a row of
            # the plausibility panel, i.e. visible in cost_summary.md and in the HTML report,
            # rather than only in a log the reader of those files never sees.
            plausibility = run_plausibility_checks(
                matrix, simulated_period_fraction=inputs.simulated_period_fraction
            )
            written.append(write_cost_summary(matrix, plausibility, result_directory))
            written.append(
                write_lifecycle_report(
                    matrix, plausibility, result_directory, input_audit, scenario_cube=scenario_cube
                )
            )
            written.extend(write_report_plots(matrix, result_directory))
            bad = [check for check in render_plausibility_findings(plausibility) if check.status != "PASS"]
            if bad:
                for check in bad:
                    log.warning(f"Lifecycle cost plausibility {check.status}: {check.name} = {check.value} "
                                f"(expected {check.expected})")
            log.information(
                "Lifecycle cost report: wrote cost_summary.md, lifecycle_report.html and PNG charts."
            )
    except BaseException:
        _remove_partial_exports(written)
        raise
    log.information("Lifecycle cost engine: wrote lifecycle_costs.json and companion exports.")


def write_parity_from_stored_inputs(simulation_parameters: Any) -> None:
    """Writes the §9.7 parity report after the legacy cost path has produced its CSVs.

    Runs as a separate postprocessing step because the engine must capture its facts *before*
    the legacy `get_cost_capex` mutates component configs, while the parity comparison needs
    the CSVs that same legacy path writes. Reads the facts back from `economic_inputs.json`.

    Reading the extract back from disk rather than keeping it in memory between the two blocks is
    the point, not an inconvenience: it proves the comparison used facts that predate the legacy
    run, so legacy config mutation cannot fake agreement. The resulting `cost_parity_report.csv` is
    the primary evidence for the Phase-7 cutover decision (§9.7, §10).

    Only invoked when COMPUTE_CAPEX is active as well — without the legacy CSVs there is nothing to
    compare against. A missing or unreadable `economic_inputs.json` is not an error: it warns and
    returns, since the parity report is diagnostic output and its absence must not affect a run.
    """
    from hisim.economics.serialization import read_inputs

    result_directory = simulation_parameters.result_directory
    parameters = _resolve_economic_parameters(simulation_parameters)
    try:
        inputs = read_inputs(result_directory)
    except (OSError, KeyError, ValueError) as err:
        log.warning(f"Parity report skipped: could not read stored economic inputs: {err}")
        return
    database = CostDatabase(parameters.cost_database_path)
    # The price basis year is resolved inside the engine/audit layer (W1.2); the bridge no
    # longer mutates the caller's parameters.
    write_parity_report(inputs, database, parameters, result_directory)
