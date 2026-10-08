"""Postprocessing bridge: runs the lifecycle cost engine after a simulation (cost_spec.md §10).

The only place where `hisim.economics` meets the rest of HiSim. Opt-in via
`PostProcessingOptions.COMPUTE_LIFECYCLE_COSTS`, it walks a finished simulation's components, outputs and results
frame, builds `EvaluationInputs`, merges the setup's `EconomicContext`, evaluates every applicable perspective and
writes the export set. It decides no economics and never calls the legacy cost methods. `economic_inputs.json` is
written first, so it stays a faithful extract of the simulation.

A run that asked for lifecycle costs fails rather than degrading: an unresolvable subject
(`UnresolvableSubjectsError`), a cost database or subsidy catalog that will not load, and a declared scenario cube that
fails all propagate as `CostDataError`. Export files written before a failure are removed; `economic_inputs.json` is
kept.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Callable, ClassVar, Dict, List, Optional, Tuple

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
    effective_price_basis_year,
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
    ComponentCostFacts,
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
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import ComponentType, LoadTypes, Units
from hisim.postprocessing.kpi_computation.kpi_structure import KpiSource

if TYPE_CHECKING:  # The renderer is imported lazily; only its record type is needed for typing.
    from hisim.economics.report_plots import SkippedPlot


#: The length of a reference year, used to turn a run's start/end dates into
#: `EvaluationInputs.simulated_period_fraction`, the factor that annualizes a partial-year run
#: (§8.5). A flat 365-day year: a leap day is far below the uncertainty of any price.
SECONDS_PER_YEAR = 365 * 24 * 3600


@dataclass(frozen=True)
class CostlessPart:
    """A simulation component that is part of another component's purchase (`EconomicContext.costless_subjects`).

    Example: the battery's energy-management controller. It stays a cost subject of its own, but costs nothing and is
    renewed when the device it belongs to is.

    Attributes:
        reason: Why the subject costs nothing, recorded as its overrides' source (§3.10).
        lifetime_of_asset_class: The class whose service life the subject is renewed on, or None to keep its own.
    """

    reason: str
    lifetime_of_asset_class: Optional[ComponentType] = None

    def applied_to(self, facts: ComponentCostFacts) -> ComponentCostFacts:
        """Return the facts overridden to cost nothing and to live the life of `lifetime_of_asset_class`.

        Investment, installation, maintenance rate, fixed operation cost and embodied CO2 are overridden to zero, so a
        replacement costs nothing either. Planning and removal costs cannot be overridden; they come from the cost
        database (zero for the energy-management controller). Asset class and size stay as extracted, so the subject is
        still registered.

        Args:
            facts: The adapter's facts.

        Returns:
            A copy of the facts with the zero overrides.
        """
        zero = UncertainValue.exact(0.0)
        return replace(
            facts,
            investment_cost_override_in_euro=zero,
            installation_cost_override_in_euro=zero,
            maintenance_rate_override=zero,
            fixed_operation_cost_override_in_euro_per_year=zero,
            embodied_co2_override_in_kg=0.0,
            override_source=f"{facts.override_source}; {self.reason}" if facts.override_source else self.reason,
            lifetime_of_asset_class=self.lifetime_of_asset_class or facts.lifetime_of_asset_class,
        )


@dataclass
class EconomicContext:
    """Everything a system setup can declare about the decision situation beyond what the simulation knows.

    The simulation knows device sizes and metered kWh, but not what stood in the cellar before, who applies for which
    grant, whether the building is rented, or which envelope measures belong to the package. Attach it with
    `simulation_parameters.set_economic_context(...)`; `_merge_context` adds it to the simulation-derived
    `EvaluationInputs` and never overwrites a simulated quantity. Every field is optional. See
    system_setups/economic_example/ for a worked example.

    - `existing_assets`: an `ExistingAssetRegister` of the pre-measure system. Its presence switches the perspective
      bundle from greenfield to brownfield and status quo (`perspectives.select_applicable`) and enables kept assets,
      residual values, removal costs and the anyway credit (§4.1).
    - `subsidy_context`: the applicant and building answers the §5.3 eligibility conditions read; unanswered fields
      stay undetermined (§5.7).
    - `extra_cost_facts`: `SubjectCostFacts` for subjects that are not simulation components, such as envelope measures
      sized in m².
    - `technical_attributes_by_subject`: per-subject values merged into the extracted facts for subsidy conditions
      (SCOP, refrigerant, achieved U-value).
    - `costless_subjects`: components that are part of another's purchase (`CostlessPart`).
    - `living_area_in_m2`, `heated_floor_area_in_m2`, `current_cold_rent_in_euro_per_m2_month`,
      `building_specific_emissions_in_kg_per_m2_a`: what the §6.3 CO2 split and the §6.4 modernization levy need to
      divide costs between landlord and tenant.
    - `annual_heat_demand_in_kwh`: overrides the measured useful heat as the denominator of the cost per unit of heat;
      must be positive.
    - `scenario_set`: triggers the §4.6 scenario cube evaluation and the report's scenario section.

    Without a context, only the greenfield perspectives are evaluated: every device is a new purchase, no subsidy is
    booked without a catalog, and the cost of heat divides by the measured useful heat (or is omitted when none is
    measured).
    """

    # Brownfield: what is already installed, and which measures replace what (§4.1).
    existing_assets: Optional[ExistingAssetRegister] = None
    # Applicant/building facts for the subsidy engine (§5.3).
    subsidy_context: Optional[SubsidyContext] = None
    # Additional cost subjects that are not simulation components, such as envelope measures.
    extra_cost_facts: List[SubjectCostFacts] = field(default_factory=list)
    # Technical attributes merged into component-derived facts by subject name (subsidy
    # conditions like SCOP/refrigerant that the adapter cannot know):
    technical_attributes_by_subject: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # Subjects that are part of another subject's system, by subject name (e.g. the controller
    # that comes with a battery): they stay cost subjects, priced at zero, and live that system's
    # life.
    costless_subjects: Dict[str, CostlessPart] = field(default_factory=dict)
    # Actor-model context (§6.3, §6.4):
    living_area_in_m2: Optional[float] = None
    heated_floor_area_in_m2: Optional[float] = None
    current_cold_rent_in_euro_per_m2_month: Optional[float] = None
    building_specific_emissions_in_kg_per_m2_a: Optional[float] = None
    # For the system-cost-per-unit-of-heat KPI:
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
        """Refuse a negative quantity at declaration time, so the error names the field.

        None of these values is checked downstream, and a negative area or demand would produce plausible-looking
        negative KPIs. The heat demand is a denominator, so zero and non-finite values are refused too; a house with no
        heat demand leaves the field unset.

        Raises:
            ValueError: If any of `NON_NEGATIVE_FIELDS` is negative, or `annual_heat_demand_in_kwh` is zero or not
                finite.
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
        heat = self.annual_heat_demand_in_kwh
        if heat is not None and not (math.isfinite(heat) and heat > 0):
            raise ValueError(
                f"EconomicContext.annual_heat_demand_in_kwh={heat!r}: a declared heat demand is the "
                "denominator of the system cost per unit of heat and must be a positive, finite "
                "number of kWh a year. Leave it unset to divide by the heat the simulation measured."
            )


def _output_column_and_unit(
    component_name: str, field_name: str, all_outputs: List[Any], results: pd.DataFrame
) -> Optional[Tuple[pd.Series, str]]:
    """Return one output's per-timestep column with the unit it was declared in, or None if there is no such output.

    The results frame's columns are in the same order as `all_outputs`, so the output is located by position. The unit
    travels with the column because some channels are power and some are energy.
    """
    for index, output in enumerate(all_outputs):
        if output.component_name == component_name and output.field_name == field_name:
            unit = getattr(output, "unit", None)
            return results.iloc[:, index], str(getattr(unit, "value", unit))
    return None


def _output_column(
    component_name: str, field_name: str, all_outputs: List[Any], results: pd.DataFrame
) -> Optional[pd.Series]:
    """Return one output's per-timestep column, or None when the run declares no such output.

    None, rather than an empty series, lets callers tell "measured nothing" from "the declared field does not exist";
    they refuse the run on the second.
    """
    found = _output_column_and_unit(component_name, field_name, all_outputs, results)
    return None if found is None else found[0]


class EnergyUnitConversion:
    """Factors that turn a summed output column into kWh, keyed by its declared unit.

    Components publish power and energy channels side by side (a PV system has `ElectricityOutput` in W and
    `ElectricityEnergyOutput` in Wh), so the declared unit is always read. `WATT` needs the timestep length, so it is a
    callable. Billing (`_sum_output_column`) and the energy balance (`_device_energy_flows`) both use this table, so
    bill and chart agree. A unit not in the table is refused, not guessed.
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
        """Convert one summed column to kWh by the unit it was declared in.

        Args:
            total: The summed column in its declared unit (a power column sums to W-timesteps).
            unit: The declared unit's value, as `_output_column_and_unit` reports it.
            seconds_per_timestep: The simulation's resolution, needed for a power column.
            column: `component.field` of the column, for the error message.

        Returns:
            The same quantity in kWh.

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
    """Return one component's energy-balance flows over the simulated period, as role -> kWh.

    For each `adapter.DeviceEnergySpec` of the component's class, the named column is located, summed, converted to kWh
    by its declared unit and filed under the spec's role. A battery's signed AC power is split by sign into charge and
    discharge. Nothing here is priced; components that are free of cost or undeclared still contribute.

    Args:
        component: The finished simulation's component; its class name and `component_name` are read.
        all_outputs: The run's output declarations, in the frame's column order.
        results: The per-timestep results frame.
        seconds_per_timestep: Needed to integrate columns declared in W.

    Returns:
        Role value -> kWh, positive magnitudes, zero roles omitted. Empty for a class with an explicitly empty row or
            one that moves no electricity.

    Raises:
        CostDataError: For a row naming a constant the class does not declare, a declared column this run did not
            produce, a column in a unit the conversion table does not know, or a class with no row that publishes
            electricity in a convertible unit. `build_evaluation_inputs` turns each into an `UnresolvedSubject`.
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
    """Refuse a component class that publishes electricity but has no row in the energy-balance table.

    `adapter.DeviceEnergySpecs` is complete only if every class outside it was left out on purpose; whether such a
    class's watts are a flow or a controller's instruction needs a maintainer's decision.

    Args:
        component: The wrapped component; its class name and `component_name` are read.
        all_outputs: The run's output declarations, scanned for this component's own.

    Raises:
        CostDataError: If the class publishes at least one `LoadTypes.ELECTRICITY` output in a unit
            `EnergyUnitConversion` converts.
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
    """Sum one output column to kWh by its declared unit; None if the output does not exist.

    Uses `EnergyUnitConversion`, so the kWh a carrier is billed for and the kWh the energy balance shows are the same
    conversion of the same column.

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


class UsefulHeatExtraction:
    """The numerical tolerance for reading useful heat off the columns `adapter.UsefulHeatSources` names.

    A timestep against the source's sign convention is refused, because heat flowing the wrong way is not heat the
    house used; the tolerance keeps rounding noise from triggering that refusal.
    """

    #: Largest wrong-signed energy one timestep may carry and still count as zero, in kWh (1 mWh).
    #: Rounding noise is of the order of 1e-12 of a timestep's Wh; the smallest physical hot-water
    #: draw is some Wh (one litre warmed by 1 K is 1.16 Wh), well above this bound.
    SIGN_TOLERANCE_IN_KWH: ClassVar[float] = 1e-6


def _useful_heat_of_component(
    component: Any,
    all_outputs: List[Any],
    results: pd.DataFrame,
    seconds_per_timestep: int,
) -> Optional[Tuple[str, float]]:
    """Return one component's useful heat over the simulated period, as (`UsefulHeatKind` value, kWh).

    Args:
        component: The finished simulation's component; its class name and `component_name` are read.
        all_outputs: The run's output declarations, in the frame's column order.
        results: The per-timestep results frame.
        seconds_per_timestep: Passed to the unit conversion, needed for a power column.

    Returns:
        The kind and the heat in kWh (positive by the source's sign convention), or None for a class
            `adapter.UsefulHeatSources` does not list.

    Raises:
        CostDataError: For a row naming a constant the class does not declare, a listed column this run did not
            produce, a column in an unknown unit, or a timestep that is not finite or runs against the sign convention
            beyond `UsefulHeatExtraction.SIGN_TOLERANCE_IN_KWH`.
    """
    source = adapter.resolve_useful_heat_source(component)
    if source is None:
        return None
    column = f"{component.component_name}.{source.field_name}"
    found = _output_column_and_unit(component.component_name, source.field_name, all_outputs, results)
    if found is None:
        raise CostDataError(
            f"Useful heat: component {component.component_name} is listed in "
            f"adapter.UsefulHeatSources with output {source.field_name!r}, which this run did not "
            "produce. The levelized cost of heat would divide by a sum missing that source and "
            "publish a cost per kWh that is too high."
        )
    series, unit = found
    kwh_per_unit = EnergyUnitConversion.to_kwh(1.0, unit, seconds_per_timestep, column)
    heat = series.astype(float) * (source.sign * kwh_per_unit)
    # Written as the valid set and negated, so a NaN (which compares false both ways) is refused too.
    wrong = heat[~((heat >= -UsefulHeatExtraction.SIGN_TOLERANCE_IN_KWH) & (heat < float("inf")))]
    if not wrong.empty:
        position = wrong.index[0]
        direction = "into the house" if source.sign > 0 else "leaving the component"
        raise CostDataError(
            f"Useful heat: output {column} states heat {direction}, so its sign is "
            f"{'+' if source.sign > 0 else '-'} or zero, but {len(wrong)} timestep(s) are not: the "
            f"first is {series[position]!r} {unit} at row {position!r}. Heat flowing the wrong way "
            "is not heat the house used, and neither its magnitude nor a netted sum is a "
            "denominator the levelized cost of heat can stand behind."
        )
    return source.kind.value, float(heat.sum())


def _useful_heat_by_kind(
    wrapped_components: List[Any],
    all_outputs: List[Any],
    results: pd.DataFrame,
    seconds_per_timestep: int,
) -> Tuple[Dict[str, float], List[UnresolvedSubject]]:
    """Return the simulated period's useful heat by kind, and the listed sources that failed to state it.

    Every component listed in `adapter.UsefulHeatSources` contributes its kind, a zero included, so the record also
    shows which kinds the run has (a missing hot-water source is what `EvaluationInputs.heat_cost_omits_hot_water`
    reports). A listed source whose column is missing, unconvertible or wrong-signed comes back as an
    `UnresolvedSubject`, since a partial sum would overstate the cost per kWh.

    Args:
        wrapped_components: The simulator's `ComponentWrapper` list.
        all_outputs: The run's output declarations, in the frame's column order.
        results: The per-timestep results frame.
        seconds_per_timestep: Passed to the unit conversion, needed for a power column.

    Returns:
        `UsefulHeatKind` value -> kWh of the simulated period, not annualized, and the failures.
    """
    by_kind: Dict[str, float] = {}
    failures: List[UnresolvedSubject] = []
    for wrapper in wrapped_components:
        component = wrapper.my_component
        try:
            heat = _useful_heat_of_component(component, all_outputs, results, seconds_per_timestep)
        except CostDataError as err:
            failures.append(UnresolvedSubject(subject=component.component_name, reason=str(err)))
            continue
        if heat is not None:
            kind, kwh = heat
            by_kind[kind] = by_kind.get(kind, 0.0) + kwh
    return by_kind, failures


def _power_series(
    component_name: str, field_name: str, all_outputs: List[Any], results: pd.DataFrame
) -> Optional[pd.Series]:
    """Return the raw per-timestep power column of one output in W, or None if the run did not produce it.

    Capacity charges are billed on peaks, so the whole series goes to `_peaks_from_power_series`. The caller refuses
    the run on None rather than billing without the capacity charge.
    """
    return _output_column(component_name, field_name, all_outputs, results)


def _peaks_from_power_series(
    series: pd.Series, seconds_per_timestep: int, billing_interval_minutes: int = 15
) -> tuple:
    """Return the monthly and annual peaks in kW of the billing-interval mean power (§8.4).

    Capacity charges are billed on the highest mean power over a metering interval (15 minutes in Germany), so the
    series is averaged into intervals before taking maxima per month (MONTHLY_PEAK) and over the whole series
    (ANNUAL_PEAK). If the timestep does not divide the interval evenly, no peaks are returned (empty list, 0.0) and a
    warning is logged. "Months" are blocks of `ceil(intervals / 12)` intervals, so every interval is in one block; a
    partial-year run has fewer than twelve blocks.

    Args:
        series: Per-timestep power in W.
        seconds_per_timestep: The simulation's resolution.
        billing_interval_minutes: Metering interval the tariff bills on.

    Returns:
        `(monthly_peaks_in_kw, annual_peak_in_kw)`: at most twelve monthly values, and the maximum interval mean over
            the whole series.
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
    # block absorbs the remainder.
    intervals_per_month = max(1, -(-len(interval_means) // 12))
    monthly_peaks = [
        float(interval_means.iloc[start:start + intervals_per_month].max())
        for start in range(0, len(interval_means), intervals_per_month)
    ][:12]
    return monthly_peaks, annual_peak


def _capacity_billing_intervals(wrapped_components: List[Any]) -> Dict[EnergyCarrier, int]:
    """Return the metering interval of each carrier's capacity charge, read from the run's tariff providers.

    The interval belongs to the contract (§8.4). A `TariffProvider` holds the `TariffContract` the billing engine bills
    with, read duck-typed (`contract.capacity_charge.billing_interval_in_minutes`) so the bridge imports no component
    class.

    Args:
        wrapped_components: The simulator's wrapped components, in registration order.

    Returns:
        Carrier -> billing interval in minutes, for every carrier a provider declares a contract for. A missing carrier
            uses the default interval; if two providers declare the same carrier, the first registered wins.
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
    """Return one component's carrier flows: its own §3.4 hook first, the adapter's `MeterSpec` second.

    A meter that declares its own flows (`get_energy_flow_facts`) is believed; the class-name table is used only for
    meters without the hook. `EnergyFlowFacts` carries no capacity peaks, so peaks come from the `MeterSpec`'s
    `power_field` when the component also has a table entry. The kWh are billed as reported for every carrier; fuels
    quoted per ton or liter are converted on the price side. A hook-only meter must declare `cost_relevance = METER`
    itself to be reached.

    Args:
        component: The component to read; non-meters yield None.
        all_outputs: The run's output declarations (positional column index).
        postprocessing_results: The per-timestep results frame.
        simulation_parameters: For `seconds_per_timestep`, which sizes the peak intervals.
        billing_intervals: Carrier -> capacity-charge interval in minutes (`_capacity_billing_intervals`); a missing
            carrier uses `_peaks_from_power_series`' default.

    Returns:
        The billing determinants for this component's carrier, or None when it meters nothing. A zero reported through
            the hook is taken as a measured zero.

    Raises:
        CostDataError: If a column the meter's class declares (bought energy, sold energy or power) is not among this
            run's outputs; `build_evaluation_inputs` turns it into an unresolved subject.
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
    """Return the names of the energy figures in a set of billing determinants that are non-zero.

    Checks the adapter's assumption that a component excluded as zero-size moves no energy; if its meter still reports
    energy, the bridge refuses the run instead of billing energy for absent hardware. Only energy figures count: energy
    bought and sold, the time-of-use split, and integrated cost and revenue. Peaks and the mean spot price are not
    quantities that crossed the boundary.

    Args:
        determinants: The component's determinants, or None when it meters nothing.

    Returns:
        The names of the non-zero figures in a fixed order; empty when nothing or exactly zero was metered.
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
    """Collect cost facts and billing determinants from a finished simulation into `EvaluationInputs`.

    Each component is asked, in order: its energy-balance flows (`_device_energy_flows`, always, since the balance is
    physics); its cost relevance (`effective_cost_relevance`); its `ComponentCostFacts`; and, for meters, what crossed
    the boundary (`_billing_determinants`). A meter with capex answers several. No prices are decided here.

    These become an `UnresolvedSubject`, which later aborts the evaluation: energy-balance flows that cannot be placed;
    an `UNDECLARED` component (§9.2); a recognized component whose facts cannot be built; a meter whose declared column
    is missing; a not-installed component whose meter reported energy (`_non_zero_energy_flows`); a useful-heat source
    whose column is missing, unconvertible or wrong-signed. A run with no metered flows at all only warns (§3.4). The
    simulated period becomes `simulated_period_fraction`; runs longer than a year are clamped to one year with a
    warning. Finally the setup's `EconomicContext`, if any, is merged in.

    Args:
        wrapped_components: The simulator's `ComponentWrapper` list, in registration order.
        all_outputs: The run's `ComponentOutput` declarations, in the order of the frame's columns.
        postprocessing_results: The in-memory per-timestep results frame.
        simulation_parameters: The run's `SimulationParameters`: year, timestep, simulated period and optional
            `economic_context`.

    Returns:
        The `EvaluationInputs` that gets written to `economic_inputs.json`.
    """
    cost_facts: List[SubjectCostFacts] = []
    billing: List[BillingDeterminants] = []
    attribution: Dict[str, Dict[str, float]] = {}
    unresolved: List[UnresolvedSubject] = []
    not_installed: List[str] = []
    billing_intervals = _capacity_billing_intervals(wrapped_components)
    # Every simulated component's address, whatever the branches below make of it: the staged
    # document's rows say which subject is a HiSim component by it, and only here are the live
    # components (their ComponentID and DisplayConfig) at hand.
    component_sources: Dict[str, KpiSource] = {}
    for wrapper in wrapped_components:
        component = wrapper.my_component
        subject = component.component_name
        component_sources[subject] = component.kpi_source()
        # The energy balance is a physical record: it is collected for every component, before
        # the cost-relevance branch, because a FREE_OF_COST PV system or an UNDECLARED load
        # profile still moves kilowatt hours.
        try:
            energy_flows = _device_energy_flows(
                component,
                all_outputs,
                postprocessing_results,
                simulation_parameters.seconds_per_timestep,
            )
        except CostDataError as err:
            # A flow that cannot be placed is treated like a cost that cannot be resolved: the
            # balance would come out incomplete.
            unresolved.append(UnresolvedSubject(subject=subject, reason=str(err)))
            continue
        if energy_flows:
            attribution[subject] = energy_flows
        try:
            relevance = adapter.effective_cost_relevance(component)
            if relevance == CostRelevance.UNDECLARED:
                # §9.2 makes the declaration mandatory, so this is a defect in the component: it
                # becomes an unresolved subject and the resolution check refuses to price the rest.
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
            # A configuration the adapter cannot map to a carrier or an asset class: the component
            # becomes an unresolved subject instead of being billed at a guessed price.
            unresolved.append(UnresolvedSubject(subject=subject, reason=str(err)))
            continue
        flows = _non_zero_energy_flows(determinants) if extraction.not_installed_reason else ()
        if flows:
            # A component excluded as "not installed" that metered energy contradicts the
            # zero-size rule; billing or dropping the energy would both be wrong, so the run
            # refuses and names the flows.
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
            # Registered class, no facts: neither undeclared nor priced; an unresolved subject.
            unresolved.append(UnresolvedSubject(subject=subject, reason=extraction.unresolved_reason))
        elif extraction.not_installed_reason is not None:
            # Configured at zero size and, per the check above, metering nothing: absent from the
            # cost model. A warning, because an asset leaving the cost model must be noticed.
            not_installed.append(f"{subject}: {extraction.not_installed_reason}")
    # The heat the levelized cost of heat divides by. A listed source that cannot state it is an
    # unresolved subject, so the evaluation aborts rather than dividing by a partial sum.
    useful_heat_by_kind, heat_failures = _useful_heat_by_kind(
        wrapped_components, all_outputs, postprocessing_results, simulation_parameters.seconds_per_timestep
    )
    unresolved.extend(heat_failures)
    measured_heat = sum(useful_heat_by_kind.values())
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
        # A short run: every energy quantity and bill is divided by this fraction to reach a year,
        # so a one-day run is multiplied by 365. Warned here, and the plausibility panel carries
        # the same statement into the outputs.
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
        # A measured total of zero states no heat, not a denominator of zero: the KPI is omitted.
        # (`> 0` rather than `!= 0`: the per-timestep tolerance admits rounding noise below zero.)
        useful_heat_of_simulated_period_in_kwh=measured_heat if measured_heat > 0 else None,
        useful_heat_of_simulated_period_by_kind_in_kwh=useful_heat_by_kind,
        component_sources=component_sources,
    )
    context: Optional[EconomicContext] = getattr(simulation_parameters, "economic_context", None)
    if context is not None:
        _merge_context(inputs, context)
    if inputs.heat_cost_omits_hot_water():
        # `adapter.UsefulHeatSources` lists only SimpleDHWStorage as a hot-water source, so a
        # combi boiler or an electric water heater leaves the denominator at the rooms' heat. The
        # plausibility panel carries the same statement into the report.
        log.warning(
            "Lifecycle cost engine: the run has a building but no hot-water source of "
            "adapter.UsefulHeatSources, so the system cost per unit of heat divides by the rooms' "
            "heat only and reads too high by the hot water's share (hisim-4wlu)."
        )
    return inputs


def _merge_context(inputs: EvaluationInputs, context: EconomicContext) -> None:
    """Merge the setup-declared `EconomicContext` into the simulation-derived inputs, in place.

    The register, the subsidy context and the extra cost subjects are added; technical attributes are updated into the
    extracted facts; each scalar fills a gap and never overrides an extracted value. "Declared" means `is not None`, so
    a declared 0.0 (a rent-free unit) is kept.

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
    zeroed_subjects = set()
    for subject_facts in inputs.cost_facts:
        part = context.costless_subjects.get(subject_facts.subject)
        if part is not None:
            zeroed_subjects.add(subject_facts.subject)
            subject_facts.facts = part.applied_to(subject_facts.facts)
    known_subjects = ", ".join(sorted(item.subject for item in inputs.cost_facts)) or "(none)"
    unmatched = sorted(set(context.technical_attributes_by_subject) - matched_subjects)
    if unmatched:
        # A subject name that matches nothing is a typo or a renamed component, and its attributes
        # (SCOP, refrigerant, U-value) are what subsidy conditions read, so it is refused.
        log.warning(
            "Lifecycle cost engine: EconomicContext.technical_attributes_by_subject names "
            f"subject(s) that no extracted cost subject matches, so their attributes were not "
            f"applied: {', '.join(unmatched)}. Known subjects: {known_subjects}."
        )
    unmatched_costless = sorted(set(context.costless_subjects) - zeroed_subjects)
    if unmatched_costless:
        # The same typo here would leave a part unzeroed and, if it exists under another name,
        # book its price twice.
        log.warning(
            "Lifecycle cost engine: EconomicContext.costless_subjects names subject(s) that no "
            f"extracted cost subject matches, so those parts were not zeroed: "
            f"{', '.join(unmatched_costless)}. Known subjects: {known_subjects}."
        )
    for name in EconomicContext.NON_NEGATIVE_FIELDS:
        # `is not None`, not truthiness: a declared 0.0 is a statement, not an absent value.
        declared = getattr(context, name)
        if declared is not None:
            setattr(inputs, name, declared)


#: Modules the cost path needs before it writes anything, because it draws the audit heatmap on
#: every run: the renderer and, through it, matplotlib. `__main__.AuditLayerProbe` makes the same
#: check for the CLI.
_PLOT_LAYER_MODULES = ("matplotlib", "hisim.economics.report_plots")


def _require_plot_layer() -> None:
    """Raise unless the plotting layer (matplotlib) is importable, before the first export is written.

    The ledger heatmap is drawn beside `cost_audit.csv` on every cost run, after other exports exist; checking first
    means a missing dependency writes nothing instead of half a set. Uses `importlib.util.find_spec`, so nothing is
    imported.

    Raises:
        CostDataError: If either module is absent.
    """
    import importlib.util

    missing = [name for name in _PLOT_LAYER_MODULES if importlib.util.find_spec(name) is None]
    if missing:
        raise CostDataError(
            f"Lifecycle cost engine: {', '.join(missing)} is not importable, so the audit's "
            "ledger heatmap — which every cost run writes beside cost_audit.csv — cannot be "
            "drawn. matplotlib is a dependency of the cost path, not only of the report path. "
            "Nothing was written."
        )


def _remove_partial_exports(written: List[str]) -> None:
    """Delete the export files this run wrote before it failed.

    A half-written set looks like a complete one to later readers, and postprocessing continues after non-cost errors,
    so the absence of the files is the signal. Callers leave `economic_inputs.json` out of the list: it is the
    simulation extract and stays true. Removal errors are swallowed, since this runs while another exception
    propagates.

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
    """Return the run's `EconomicParameters`: what the setup attached, else the country's defaults.

    Shared by both entry points of this module so they read the same cost database and country.

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
    """Run the lifecycle cost engine from postprocessing (option COMPUTE_LIFECYCLE_COSTS).

    Steps: resolve the economic parameters, load the cost database, extract `EvaluationInputs`, write
    `economic_inputs.json`, load the subsidy catalog if configured, run the resolution check, select the applicable
    perspectives, evaluate them into one `EvaluationMatrix` and write the exports. It runs before the legacy
    COMPUTE_CAPEX block, because `get_cost_capex` mutates component configs (§10.0 rule 4); the parity report is a
    separate pass (`write_parity_from_stored_inputs`).

    Files: `economic_inputs.json` (before any pricing), `lifecycle_costs.json`, `component_costs.json`/`.csv`,
    `cash_flow_timeline.csv`, `cost_provenance.json`, `lifecycle_kpis.json`, `cost_audit.csv`, `cost_audit.json` and
    `cost_audit_timeline_heatmap.png`; with a scenario set also `scenario_cube.csv`/`.json`; with `generate_report`
    also `cost_summary.md`, `lifecycle_report.html`, the PNG set (`lifecycle_<chart>_<perspective_id>.png` and
    `lifecycle_perspective_costs.png`) and, if a chart was not drawn, `lifecycle_plots_not_drawn.txt`. No legacy file
    is touched. matplotlib is required even without a report, for the heatmap; a heatmap that fails to render is
    recorded as a skipped chart. On any failure the exports already written are removed, except `economic_inputs.json`.

    Args:
        wrapped_components: The simulator's wrapped components.
        all_outputs: The run's output declarations (column order of the results frame).
        postprocessing_results: The in-memory per-timestep results.
        simulation_parameters: The run's parameters, optionally carrying `economic_parameters` and `economic_context`.
        generate_report: Whether option LIFECYCLE_COST_REPORT is set too.

    Raises:
        CostDataError: If the cost database or a configured subsidy catalog cannot be loaded, a declared scenario cube
            cannot be evaluated, or a cost subject is unresolvable.
    """
    # First, before any file: this run draws the audit's ledger heatmap, so a missing renderer
    # must refuse the run rather than roll back files already written.
    _require_plot_layer()
    result_directory = simulation_parameters.result_directory
    parameters = _resolve_economic_parameters(simulation_parameters)
    # A database that will not load is a CostDataError and propagates: a run that asked for
    # lifecycle costs must fail, not finish without cost files.
    database = CostDatabase(parameters.cost_database_path)
    inputs = build_evaluation_inputs(wrapped_components, all_outputs, postprocessing_results, simulation_parameters)
    # The faithful extract goes to disk before anything economic touches it; its content depends
    # on the simulation only. It also records the country and the price basis year the run priced
    # at, which a consumer of this file alone (the staged evaluator) cannot learn otherwise; the
    # basis year is resolved by the one function that owns the policy.
    write_inputs(
        inputs,
        result_directory,
        country=parameters.country,
        price_basis_year=effective_price_basis_year(parameters, database, inputs.simulation_year),
    )
    # A configured catalog that will not load, or whose path does not resolve, raises
    # `CostDataError` instead of silently booking no subsidy. `load_configured` is shared with the
    # CLI.
    catalog = SubsidyCatalog.load_configured(parameters.country, parameters.subsidy_catalog_path)
    evaluator = EconomicEvaluator(database, parameters, catalog)
    # An unresolvable subject aborts the whole cost evaluation (§8): no partial results.
    require_resolvable_subjects(inputs, evaluator)
    perspectives = select_applicable(load_default_bundle(), has_register=inputs.existing_assets is not None)
    written: List[str] = []
    plot_skips: List["SkippedPlot"] = []
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
            # Resolved once: cost_audit.csv and the report's section 1 are two renderings.
            input_audit = build_input_audit(inputs, database, parameters, first_result)
            written.append(write_cost_audit(input_audit, result_directory))
            written.append(write_input_audit(input_audit, result_directory))
            # The year x category ledger heatmap is the visual twin of the audit table, so it is
            # written here beside `cost_audit.csv` (audit.py imports no renderer). It joins
            # `written`, so a later failure removes it with the CSVs.
            from hisim.economics.report_plots import write_audit_plots

            audit_plots = write_audit_plots(first_result, result_directory)
            written.extend(audit_plots.paths)
            plot_skips.extend(audit_plots.skipped)
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
                # A declared scenario set is a requested section of the answer, so a failure
                # propagates rather than leaving the report silently without it.
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

            # The simulated fraction goes in so the §8.5 extrapolation of a short run, and a
            # heat-cost figure that divides by the rooms' heat alone, appear in the plausibility
            # panel of cost_summary.md and the HTML report.
            plausibility = run_plausibility_checks(
                matrix,
                simulated_period_fraction=inputs.simulated_period_fraction,
                heat_without_hot_water_in_kwh=(
                    inputs.annual_heat_demand() if inputs.heat_cost_omits_hot_water() else None
                ),
            )
            written.append(write_cost_summary(matrix, plausibility, result_directory))
            written.append(
                write_lifecycle_report(
                    matrix, plausibility, result_directory, input_audit, scenario_cube=scenario_cube
                )
            )
            report_plots_written = write_report_plots(matrix, result_directory)
            written.extend(report_plots_written.paths)
            plot_skips.extend(report_plots_written.skipped)
            bad = [check for check in render_plausibility_findings(plausibility) if check.status != "PASS"]
            if bad:
                for check in bad:
                    log.warning(f"Lifecycle cost plausibility {check.status}: {check.name} = {check.value} "
                                f"(expected {check.expected})")
            log.information(
                "Lifecycle cost report: wrote cost_summary.md, lifecycle_report.html and PNG charts."
            )
        # The renderers return what they could not draw; it is logged and written to a file
        # beside the PNGs.
        sidecar = _write_skipped_plots_note(plot_skips, result_directory)
        if sidecar is not None:
            written.append(sidecar)
    except BaseException:
        _remove_partial_exports(written)
        raise
    log.information("Lifecycle cost engine: wrote lifecycle_costs.json and companion exports.")


def _write_skipped_plots_note(skips: List["SkippedPlot"], result_directory: str) -> Optional[str]:
    """Log every chart this run did not draw and write the same lines to `lifecycle_plots_not_drawn.txt`.

    The file is written only when a chart was skipped; it belongs to the export set, so a later failure removes it.

    Args:
        skips: The `report_plots.SkippedPlot` records both writers returned.
        result_directory: Where the PNGs went.

    Returns:
        The path written, or None when nothing was skipped.
    """
    if not skips:
        return None
    import os

    from hisim.economics.report_plots import SKIPPED_PLOTS_FILE_NAME

    for skip in skips:
        log.information(f"Lifecycle cost chart not drawn: {skip.as_line()}")
    path = os.path.join(result_directory, SKIPPED_PLOTS_FILE_NAME)
    with open(path, "w", encoding="utf-8") as file:
        file.write("\n".join(skip.as_line() for skip in skips) + "\n")
    return path


def write_parity_from_stored_inputs(simulation_parameters: Any) -> None:
    """Write the §9.7 parity report (`cost_parity_report.csv`) after the legacy cost path has written its CSVs.

    A separate step because the engine must read its facts before legacy `get_cost_capex` mutates configs, while the
    comparison needs the legacy CSVs. The facts are read back from `economic_inputs.json`, which proves they predate
    the legacy run. Only called when COMPUTE_CAPEX is active. A missing or unreadable `economic_inputs.json` only
    warns, since the report is diagnostic.
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
    # The price basis year is resolved inside the engine and audit layer; the caller's parameters
    # are not modified.
    write_parity_report(inputs, database, parameters, result_directory)
