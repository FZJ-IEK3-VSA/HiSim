"""Postprocessing bridge: runs the lifecycle cost engine after a simulation (cost_spec.md §10).

Activation is opt-in via ``PostProcessingOptions.COMPUTE_LIFECYCLE_COSTS`` and side-effect
free: it only writes *new* files (lifecycle_costs.json, component_costs.*,
cash_flow_timeline.csv, cost_audit.csv, cost_parity_report.csv, lifecycle_kpis.json,
economic_inputs.json, cost_provenance.json). It never calls the legacy cost methods.

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

**Why it can never break a run.** Two independent layers of guarding. Outside, both entry points
are called from `postprocessing_main.py` inside a bare ``except Exception`` that logs
``"Lifecycle cost engine failed (legacy outputs are unaffected): …"`` (respectively
``"Lifecycle cost parity report failed …"``) and continues with the remaining postprocessing
steps — so *every* exception is swallowed there, including the deliberate fail-fast
`UnresolvableSubjectsError` of decision D7, and no lifecycle-cost problem can ever cost a user
their simulation results. Inside, three narrower guards degrade gracefully instead of aborting:
a cost database that fails to load logs an error and returns before anything is written; a
subsidy catalog that fails to load logs an error and leaves `catalog` at None, so evaluation
continues with the §10.1 flat shim; and a failing scenario cube logs
``"… (base results unaffected)"`` and leaves the already-written base exports in place. Note
what is *not* guarded away: the D7 resolution check and the evaluation itself are allowed to
propagate, because a partial cost result is worse than none (cost-spec-v2 §8).

The bridge is also the only consumer of `adapter.py`, and the reason `economic_inputs.json` is
written before any economics happens: the file must be a faithful extract of the simulation,
independent of cost-database state (cost-spec-v2 W1.1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd

from hisim import log
from hisim.economics import adapter
from hisim.economics.audit import build_input_audit, write_cost_audit, write_parity_report
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
from hisim.economics.facts import BillingDeterminants, CostRelevance
from hisim.economics.parameters import EconomicParameters
from hisim.economics.perspectives import load_default_bundle, select_applicable
from hisim.economics.serialization import write_inputs
from hisim.economics.subsidies import SubsidyCatalog, SubsidyContext


class BridgeConstants:
    """Unit conversions the simulation-to-engine bridge needs.

    Only one constant so far: the length of a reference year, used to turn a simulation's
    start/end dates into `EvaluationInputs.simulated_period_fraction` — the factor the engine
    later uses to annualize a partial-year run (§8.5). A flat 365-day year is deliberate: the
    fraction scales energy quantities, so a leap day's worth of difference is far below the
    uncertainty band of any price it is multiplied with.
    """

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
    existing_assets: Optional[Any] = None  # ExistingAssetRegister
    # Applicant/building facts for the subsidy engine (§5.3).
    subsidy_context: Optional[SubsidyContext] = None
    # Additional cost subjects that are not simulation components — envelope measures (Q7).
    extra_cost_facts: List[Any] = field(default_factory=list)  # List[SubjectCostFacts]
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
    scenario_set: Optional[Any] = None  # ScenarioSet


def _sum_output_column(
    component_name: str, field_name: str, all_outputs: List[Any], results: pd.DataFrame
) -> Optional[float]:
    """Sums one output column (Wh) to kWh; None if the output does not exist.

    The results DataFrame has no named columns the engine could rely on, so an output is located
    positionally: `all_outputs` and the frame's columns are in the same order. Returning None
    rather than 0.0 for a missing output is what lets the caller distinguish "this meter measured
    nothing" from "this meter's declared field does not exist", and warn about the latter.
    """
    for index, output in enumerate(all_outputs):
        if output.component_name == component_name and output.field_name == field_name:
            return float(results.iloc[:, index].sum()) * 1e-3
    return None


class EnergyUnitConversion:
    """Factors that turn a summed output column into kilowatt hours, keyed by its declared unit.

    HiSim components publish power and energy channels side by side — a PV system has both
    `ElectricityOutput` in W and `ElectricityEnergyOutput` in Wh — so the energy-balance collector
    reads the *declared* unit of the column it found instead of assuming one. `WATT` needs the
    timestep length as well, which is why it is a callable rather than a number.

    A unit not in this table is not converted at all: the collector logs the column and skips it,
    which keeps a mis-declared output out of the energy balance instead of putting a number three
    orders of magnitude wrong on a chart.
    """

    WATT_HOURS_PER_KWH = 1000.0
    SECONDS_PER_HOUR = 3600.0
    #: Declared unit string (`loadtypes.Units` value) -> factor from the summed column to kWh,
    #: given the timestep length in seconds.
    BY_UNIT: Dict[str, Any] = {
        "Wh": lambda seconds: 1.0 / EnergyUnitConversion.WATT_HOURS_PER_KWH,
        "kWh": lambda seconds: 1.0,
        "W": lambda seconds: seconds / (
            EnergyUnitConversion.SECONDS_PER_HOUR * EnergyUnitConversion.WATT_HOURS_PER_KWH
        ),
    }


def _device_energy_flows(
    component: Any,
    all_outputs: List[Any],
    results: pd.DataFrame,
    seconds_per_timestep: int,
) -> Dict[str, float]:
    """One component's energy-balance flows over the simulated period, as role -> kWh.

    The physical counterpart of `_billing_determinants`, and the data behind the household energy
    balance (V12): for every `adapter.DeviceEnergySpec` this component's class declares, the named
    output column is located positionally, summed, converted to kWh by its own declared unit and
    filed under the spec's role. A battery's signed AC-power channel is split by sign so charging
    and discharging come out as two roles rather than one net number that would hide the round
    trip.

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
        omitted. Empty for a class with no declared specs and for a declared column this run did
        not produce (warned about once, since a renamed output is a real defect).
    """
    flows: Dict[str, float] = {}
    for spec in adapter.get_device_energy_specs(component):
        column = _output_column(component.component_name, spec.field_name, all_outputs, results)
        if column is None:
            log.warning(
                f"Energy balance: component {component.component_name} declares output "
                f"{spec.field_name!r} for role {spec.role.value}, which this run did not produce; "
                "the flow is left out of the household energy balance."
            )
            continue
        series, unit = column
        factor = EnergyUnitConversion.BY_UNIT.get(unit)
        if factor is None:
            log.warning(
                f"Energy balance: output {component.component_name}.{spec.field_name} is declared "
                f"in {unit!r}, which is not an energy or power unit this collector converts; the "
                "flow is left out of the household energy balance."
            )
            continue
        if spec.positive_part is True:
            total = float(series.clip(lower=0.0).sum())
        elif spec.positive_part is False:
            total = -float(series.clip(upper=0.0).sum())
        else:
            total = float(series.sum())
        value = total * factor(seconds_per_timestep)
        if value:
            flows[spec.role.value] = flows.get(spec.role.value, 0.0) + value
    return flows


def _output_column(
    component_name: str, field_name: str, all_outputs: List[Any], results: pd.DataFrame
) -> Optional[tuple]:
    """One output's raw column and its declared unit, located positionally; None if absent.

    The lookup `_device_energy_flows` needs and `_sum_output_column` does not: the energy balance
    reads channels that are sometimes power and sometimes energy, so it has to see the unit the
    component declared rather than assume the Wh the meter contract fixes.
    """
    for index, output in enumerate(all_outputs):
        if output.component_name == component_name and output.field_name == field_name:
            unit = getattr(output, "unit", None)
            return results.iloc[:, index], str(getattr(unit, "value", unit))
    return None


def _power_series(
    component_name: str, field_name: str, all_outputs: List[Any], results: pd.DataFrame
) -> Optional[pd.Series]:
    """The raw per-timestep column of one output (W), located the same positional way.

    Unlike `_sum_output_column` this does not aggregate or rescale: capacity charges are billed on
    peaks, not on sums, so the whole series is handed to `_peaks_from_power_series`. None means the
    meter declared a `power_field` that the run did not produce, in which case the carrier is billed
    without capacity charges.
    """
    for index, output in enumerate(all_outputs):
        if output.component_name == component_name and output.field_name == field_name:
            return results.iloc[:, index]
    return None


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
    grouping, and "months" are blocks of intervals rather than calendar months, which is accurate
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


def _billing_determinants(
    component: Any,
    all_outputs: List[Any],
    postprocessing_results: pd.DataFrame,
    simulation_parameters: Any,
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

    Returns:
        The billing determinants for this component's carrier, or None when it meters nothing. On
        the table path a meter whose declared output column does not exist in this run also
        yields None, with a warning, and its carrier stays unbilled; a component answering
        through the hook owns that judgement itself and a zero it reports is taken as a measured
        zero.
    """
    meter_spec = adapter.get_meter_spec(component)
    flows = adapter.get_energy_flow_facts(component, all_outputs, postprocessing_results)
    if flows is None and meter_spec is None:
        return None
    if flows is not None:
        determinants = BillingDeterminants.from_energy_flow(flows)
    else:
        assert meter_spec is not None
        bought_kwh: Optional[float] = _sum_output_column(
            component.component_name, meter_spec.bought_field, all_outputs, postprocessing_results
        )
        if bought_kwh is None:
            log.warning(
                f"Meter {component.component_name}: output {meter_spec.bought_field!r} not found; "
                "carrier stays unbilled."
            )
            return None
        sold_kwh = 0.0
        if meter_spec.sold_field:
            sold_kwh = (
                _sum_output_column(
                    component.component_name, meter_spec.sold_field, all_outputs, postprocessing_results
                )
                or 0.0
            )
        determinants = BillingDeterminants(
            carrier=meter_spec.carrier, energy_bought_in_kwh=bought_kwh, energy_sold_in_kwh=sold_kwh
        )
    if meter_spec is not None and meter_spec.power_field:
        series = _power_series(
            component.component_name, meter_spec.power_field, all_outputs, postprocessing_results
        )
        if series is not None:
            (
                determinants.peak_per_billing_period_in_kw,
                determinants.annual_peak_in_kw,
            ) = _peaks_from_power_series(series, simulation_parameters.seconds_per_timestep)
    return determinants


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

    Four things it also decides, all reported rather than silent. `UNDECLARED` components are
    skipped and listed at INFO level: during the parallel phase a component that has not adopted
    §9.2 is simply not in the cost model yet, and that has to be visible without being fatal.
    A component the adapter *does* recognize but cannot describe — a registered extractor that
    yielded nothing, a meter whose configured fuel maps to no carrier — becomes an
    `UnresolvedSubject` instead (issues #2 and #3), which the downstream D7 check turns into a
    hard failure rather than a component quietly missing from the cost report. A meter whose
    declared output does not exist warns and leaves its carrier unbilled, and a run with no meter
    flows at all warns that energy costs are missing from the results (§3.4). And the simulated
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
    undeclared: List[str] = []
    unresolved: List[UnresolvedSubject] = []
    not_installed: List[str] = []
    for wrapper in wrapped_components:
        component = wrapper.my_component
        subject = component.component_name
        # The energy balance is a physical record, not a cost classification: it is collected for
        # every component, before and independently of the cost-relevance branch below, because a
        # PV system that is FREE_OF_COST or a load profile that is UNDECLARED still moves the
        # kilowatt hours the household balance is made of.
        energy_flows = _device_energy_flows(
            component, all_outputs, postprocessing_results,
            simulation_parameters.seconds_per_timestep,
        )
        if energy_flows:
            attribution[subject] = energy_flows
        try:
            relevance = adapter.effective_cost_relevance(component)
            if relevance == CostRelevance.UNDECLARED:
                undeclared.append(f"{subject} ({type(component).__name__})")
                continue
            if relevance == CostRelevance.FREE_OF_COST:
                continue
            determinants = _billing_determinants(
                component, all_outputs, postprocessing_results, simulation_parameters
            )
            extraction = adapter.extract_cost_facts(component)
        except CostDataError as err:
            # A configuration the adapter cannot map to a carrier or an asset class (issue #3):
            # the component becomes an unresolved subject instead of being billed at a guessed
            # price, and the D7 check downstream refuses to produce partial results.
            unresolved.append(UnresolvedSubject(subject=subject, reason=str(err)))
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
            # Configured at zero size: absent from the building, so absent from the cost model —
            # a skip, not a failure, and logged so it is never a silent omission.
            not_installed.append(f"{subject}: {extraction.not_installed_reason}")
    if not_installed:
        log.information(
            "Lifecycle cost engine: components configured at zero size, excluded from the cost "
            f"model as not installed: {'; '.join(sorted(not_installed))}"
        )
    if unresolved:
        log.warning(
            "Lifecycle cost engine: components that could not be described for the cost model "
            f"(evaluation will abort, cost-spec-v2 §8/D7): "
            f"{', '.join(sorted(item.subject for item in unresolved))}"
        )
    if undeclared:
        log.information(
            "Lifecycle cost engine: components without cost declaration (not part of the cost "
            f"model during the parallel phase): {', '.join(sorted(undeclared))}"
        )
    if not billing:
        log.warning(
            "Lifecycle cost engine: no meter flows found — energy costs are missing from the "
            "lifecycle results (§3.4)."
        )
    duration_seconds = (simulation_parameters.end_date - simulation_parameters.start_date).total_seconds()
    fraction = min(1.0, duration_seconds / BridgeConstants.SECONDS_PER_YEAR)
    if duration_seconds > BridgeConstants.SECONDS_PER_YEAR * 1.001:
        log.warning(
            "Simulation spans more than one year; the lifecycle cost engine uses the first "
            "simulated year (cost_module_issues.md #15)."
        )
        fraction = 1.0
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
    and every scalar uses ``context.x or inputs.x`` — the context fills a gap, it does not overrule
    a value the extraction already established. That ordering is what keeps the seam-1 promise that
    `economic_inputs.json` never contradicts the simulation it came from.

    One consequence of the ``or`` idiom worth knowing when reading results: a context value of 0.0
    or None is indistinguishable from "not declared" and leaves the extracted value in place.

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
    for subject_facts in inputs.cost_facts:
        extra_attributes = context.technical_attributes_by_subject.get(subject_facts.subject)
        if extra_attributes:
            subject_facts.facts.technical_attributes.update(extra_attributes)
    inputs.living_area_in_m2 = context.living_area_in_m2 or inputs.living_area_in_m2
    inputs.heated_floor_area_in_m2 = context.heated_floor_area_in_m2 or inputs.heated_floor_area_in_m2
    inputs.current_cold_rent_in_euro_per_m2_month = (
        context.current_cold_rent_in_euro_per_m2_month or inputs.current_cold_rent_in_euro_per_m2_month
    )
    inputs.building_specific_emissions_in_kg_per_m2_a = (
        context.building_specific_emissions_in_kg_per_m2_a or inputs.building_specific_emissions_in_kg_per_m2_a
    )
    inputs.annual_heat_demand_in_kwh = context.annual_heat_demand_in_kwh or inputs.annual_heat_demand_in_kwh


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
    `cash_flow_timeline.csv`, `cost_provenance.json`, `lifecycle_kpis.json`, `cost_audit.csv` and
    `cost_audit.json`. With a declared scenario set additionally `scenario_cube.csv`/`.json`, and
    with ``generate_report`` additionally `cost_summary.md`, `lifecycle_report.html` and the PNG
    charts. No legacy file is read, written or otherwise touched.

    Failure behaviour (see the module docstring): a database that will not load, a catalog that will
    not load and a failing scenario cube are each caught here and logged, leaving the rest intact;
    anything else propagates into postprocessing's own ``except Exception``, which logs it and lets
    the simulation's legacy outputs stand.

    Args:
        wrapped_components: The simulator's wrapped components.
        all_outputs: The run's output declarations (column order of the results frame).
        postprocessing_results: The in-memory per-timestep results.
        simulation_parameters: The run's parameters; optionally carrying `economic_parameters`
            and `economic_context`.
        generate_report: Whether option LIFECYCLE_COST_REPORT was set as well.
    """
    result_directory = simulation_parameters.result_directory
    parameters: Optional[EconomicParameters] = getattr(simulation_parameters, "economic_parameters", None)
    if parameters is None:
        parameters = EconomicParameters(country=getattr(simulation_parameters, "country", "DE"))
    try:
        database = CostDatabase(parameters.cost_database_path)
    except CostDataError as err:
        log.error(f"Lifecycle cost engine: cost database failed to load: {err}")
        return
    inputs = build_evaluation_inputs(wrapped_components, all_outputs, postprocessing_results, simulation_parameters)
    # The faithful extract goes to disk before anything economic touches it (W1.1): what the
    # file contains must depend on the simulation only, never on cost-database state.
    write_inputs(inputs, result_directory)
    # A named catalog that cannot be loaded aborts the cost evaluation (D25) instead of being
    # logged and replaced by `None`, which handed the run to the §10.1 legacy flat shim and
    # published a full, plausible result priced under no catalog at all. `postprocessing_main`
    # catches everything this function raises and logs it, so the run itself still finishes with
    # its legacy outputs intact — what does not happen any more is a shim-priced cost result.
    catalog = SubsidyCatalog.load_configured(parameters.country, parameters.subsidy_catalog_path)
    evaluator = EconomicEvaluator(database, parameters, catalog)
    # D7 (cost-spec-v2 §8): an unresolvable subject aborts the whole cost evaluation — no partial
    # results. postprocessing_main catches it and logs an error; the legacy outputs are unaffected.
    require_resolvable_subjects(inputs, evaluator)
    perspectives = select_applicable(load_default_bundle(), has_register=inputs.existing_assets is not None)
    matrix = evaluator.evaluate_matrix(inputs, perspectives)
    write_lifecycle_costs_json(matrix, result_directory)
    write_component_costs(matrix, result_directory)
    write_cash_flow_timeline(matrix, result_directory)
    write_provenance_ledger(matrix, result_directory)
    write_lifecycle_kpis(matrix, result_directory)
    first_result = next(iter(matrix.results.values()), None)
    input_audit = None
    if first_result is not None:
        # Resolved once (W4.6): cost_audit.csv and the report's section 1 are two renderings.
        input_audit = build_input_audit(inputs, database, parameters, first_result)
        write_cost_audit(input_audit, result_directory)
        write_input_audit(input_audit, result_directory)
        # The year x category heatmap is the visual twin of the audit table and is written from
        # here rather than from `audit.py`, which may not import a renderer (seam-4 lint).
        from hisim.economics.report_plots import write_audit_plots

        write_audit_plots(first_result, result_directory)
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
            export_cube_csv(scenario_cube, os.path.join(result_directory, "scenario_cube.csv"))
            export_cube_json(scenario_cube, os.path.join(result_directory, "scenario_cube.json"))
            log.information(
                f"Lifecycle cost engine: evaluated {sum(len(v) for v in scenario_cube.results.values())} "
                "scenario cells into scenario_cube.csv/json."
            )
        except Exception as err:  # pylint: disable=broad-except
            log.error(f"Lifecycle cost scenario analysis failed (base results unaffected): {err}")
            scenario_cube = None
    if generate_report and matrix.results:
        from hisim.economics.plausibility import run_plausibility_checks
        from hisim.economics.report_plots import write_report_plots
        from hisim.economics.reporting import (
            render_plausibility_findings,
            write_cost_summary,
            write_lifecycle_report,
        )

        plausibility = run_plausibility_checks(matrix)
        write_cost_summary(matrix, plausibility, result_directory)
        write_lifecycle_report(
            matrix, plausibility, result_directory, input_audit, scenario_cube=scenario_cube
        )
        write_report_plots(matrix, result_directory)
        bad = [check for check in render_plausibility_findings(plausibility) if check.status != "PASS"]
        if bad:
            for check in bad:
                log.warning(f"Lifecycle cost plausibility {check.status}: {check.name} = {check.value} "
                            f"(expected {check.expected})")
        log.information(
            "Lifecycle cost report: wrote cost_summary.md, lifecycle_report.html and PNG charts."
        )
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
    parameters: Optional[EconomicParameters] = getattr(simulation_parameters, "economic_parameters", None)
    if parameters is None:
        parameters = EconomicParameters(country=getattr(simulation_parameters, "country", "DE"))
    try:
        inputs = read_inputs(result_directory)
    except (OSError, KeyError, ValueError) as err:
        log.warning(f"Parity report skipped: could not read stored economic inputs: {err}")
        return
    database = CostDatabase(parameters.cost_database_path)
    # The price basis year is resolved inside the engine/audit layer (W1.2); the bridge no
    # longer mutates the caller's parameters.
    write_parity_report(inputs, database, parameters, result_directory)
