"""Postprocessing bridge: runs the lifecycle cost engine after a simulation (cost_spec.md §10).

Activation is opt-in via ``PostProcessingOptions.COMPUTE_LIFECYCLE_COSTS`` and side-effect
free: it only writes *new* files (lifecycle_costs.json, component_costs.*,
cash_flow_timeline.csv, cost_audit.csv, cost_parity_report.csv, lifecycle_kpis.json,
economic_inputs.json, cost_provenance.json). It never calls the legacy cost methods.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd

from hisim import log
from hisim.economics import adapter
from hisim.economics.audit import write_cost_audit, write_parity_report
from hisim.economics.database import CostDatabase, CostDataError
from hisim.economics.evaluator import EconomicEvaluator, EvaluationInputs, SubjectCostFacts
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

SECONDS_PER_YEAR = 365 * 24 * 3600


@dataclass
class EconomicContext:
    """Everything a system setup can declare beyond what the simulation knows itself.

    Attach via ``simulation_parameters.set_economic_context(...)``. With an existing-asset
    register present, the default perspective bundle switches from greenfield to the full
    brownfield set (owner/landlord/tenant, macroeconomic, ...); with a subsidy context and
    ``EconomicParameters.subsidy_catalog_path`` set, the real subsidy engine replaces the
    flat shim. See system_setups/economic_example/ for a complete worked example.
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


def _pick_price_basis_year(database: CostDatabase, country: str, simulation_year: int) -> int:
    """Bridge-level default: the simulation year, or the earliest covered year with a warning.

    The engine itself is strict (§3.5: hard error on uncovered years); during the parallel
    phase the shipped database only covers the years migrated from configuration.py, so the
    bridge picks a covered basis year explicitly rather than reintroducing silent fallbacks.
    """
    entries = database.devices.get(country, [])
    if not entries:
        return simulation_year
    years = sorted({entry.valid_from_year for entry in entries})
    if years and years[0] > simulation_year:
        log.warning(
            f"No device cost data valid at simulation year {simulation_year} for {country}; "
            f"using price basis year {years[0]} (earliest available). Set "
            "EconomicParameters.price_basis_year to override."
        )
        return years[0]
    return simulation_year


def _sum_output_column(
    component_name: str, field_name: str, all_outputs: List[Any], results: pd.DataFrame
) -> Optional[float]:
    """Sums one output column (Wh) to kWh; None if the output does not exist."""
    for index, output in enumerate(all_outputs):
        if output.component_name == component_name and output.field_name == field_name:
            return float(results.iloc[:, index].sum()) * 1e-3
    return None


def _power_series(
    component_name: str, field_name: str, all_outputs: List[Any], results: pd.DataFrame
) -> Optional[pd.Series]:
    for index, output in enumerate(all_outputs):
        if output.component_name == component_name and output.field_name == field_name:
            return results.iloc[:, index]
    return None


def _peaks_from_power_series(
    series: pd.Series, seconds_per_timestep: int, billing_interval_minutes: int = 15
) -> tuple:
    """Billing-interval mean peaks (kW) per month plus the annual peak (§8.4)."""
    interval_seconds = billing_interval_minutes * 60
    if interval_seconds % seconds_per_timestep != 0:
        return [], 0.0
    steps = interval_seconds // seconds_per_timestep
    kw_series = series.astype(float) * 1e-3
    interval_means = kw_series.groupby(kw_series.reset_index(drop=True).index // steps).mean()
    if interval_means.empty:
        return [], 0.0
    annual_peak = float(interval_means.max())
    intervals_per_month = max(1, len(interval_means) // 12)
    monthly_peaks = [
        float(interval_means.iloc[start:start + intervals_per_month].max())
        for start in range(0, len(interval_means), intervals_per_month)
    ][:12]
    return monthly_peaks, annual_peak


def build_evaluation_inputs(
    wrapped_components: List[Any],
    all_outputs: List[Any],
    postprocessing_results: pd.DataFrame,
    simulation_parameters: Any,
) -> EvaluationInputs:
    """Collects facts and billing determinants from a finished simulation."""
    cost_facts: List[SubjectCostFacts] = []
    billing: List[BillingDeterminants] = []
    undeclared: List[str] = []
    for wrapper in wrapped_components:
        component = wrapper.my_component
        relevance = adapter.effective_cost_relevance(component)
        if relevance == CostRelevance.UNDECLARED:
            undeclared.append(f"{component.component_name} ({type(component).__name__})")
            continue
        if relevance == CostRelevance.FREE_OF_COST:
            continue
        meter_spec = adapter.get_meter_spec(component)
        if meter_spec is not None:
            bought_kwh = _sum_output_column(
                component.component_name, meter_spec.bought_field, all_outputs, postprocessing_results
            )
            if bought_kwh is None:
                log.warning(
                    f"Meter {component.component_name}: output {meter_spec.bought_field!r} not found; "
                    "carrier stays unbilled."
                )
            else:
                sold_kwh = 0.0
                if meter_spec.sold_field:
                    sold_kwh = (
                        _sum_output_column(
                            component.component_name, meter_spec.sold_field, all_outputs, postprocessing_results
                        )
                        or 0.0
                    )
                monthly_peaks: List[float] = []
                annual_peak = 0.0
                if meter_spec.power_field:
                    series = _power_series(
                        component.component_name, meter_spec.power_field, all_outputs, postprocessing_results
                    )
                    if series is not None:
                        monthly_peaks, annual_peak = _peaks_from_power_series(
                            series, simulation_parameters.seconds_per_timestep
                        )
                quantity = meter_spec.quantity_conversion(bought_kwh, component.config)
                billing.append(
                    BillingDeterminants(
                        carrier=meter_spec.carrier,
                        energy_bought_in_kwh=quantity,
                        energy_sold_in_kwh=sold_kwh,
                        peak_per_billing_period_in_kw=monthly_peaks,
                        annual_peak_in_kw=annual_peak,
                    )
                )
        # Meters can also be PRICED devices (the meter hardware itself has capex).
        facts = adapter.get_cost_facts(component)
        if facts is not None:
            cost_facts.append(SubjectCostFacts(subject=component.component_name, facts=facts))
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
    fraction = min(1.0, duration_seconds / SECONDS_PER_YEAR)
    if duration_seconds > SECONDS_PER_YEAR * 1.001:
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
    )
    context: Optional[EconomicContext] = getattr(simulation_parameters, "economic_context", None)
    if context is not None:
        _merge_context(inputs, context)
    return inputs


def _merge_context(inputs: EvaluationInputs, context: EconomicContext) -> None:
    """Merges the setup-declared EconomicContext into the simulation-derived inputs."""
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
    if parameters.price_basis_year is None:
        parameters.price_basis_year = _pick_price_basis_year(database, parameters.country, inputs.simulation_year)
    catalog = None
    if parameters.subsidy_catalog_path:
        try:
            catalog = SubsidyCatalog.load(parameters.country, parameters.subsidy_catalog_path)
        except Exception as err:  # pylint: disable=broad-except — engine must not break postprocessing
            log.error(f"Lifecycle cost engine: subsidy catalog failed to load: {err}")
    evaluator = EconomicEvaluator(database, parameters, catalog)
    problems = evaluator.resolve_check(inputs, strict=False)
    if problems:
        for problem in problems:
            log.warning(f"Lifecycle cost engine resolution check: {problem}")
        inputs.cost_facts = [
            subject_facts
            for subject_facts in inputs.cost_facts
            if not any(subject_facts.subject in problem for problem in problems)
        ]
    perspectives = select_applicable(load_default_bundle(), has_register=inputs.existing_assets is not None)
    matrix = evaluator.evaluate_matrix(inputs, perspectives)
    write_lifecycle_costs_json(matrix, result_directory)
    write_component_costs(matrix, result_directory)
    write_cash_flow_timeline(matrix, result_directory)
    write_provenance_ledger(matrix, result_directory)
    write_lifecycle_kpis(matrix, result_directory)
    write_inputs(inputs, result_directory)
    first_result = next(iter(matrix.results.values()), None)
    if first_result is not None:
        write_cost_audit(inputs, database, parameters, first_result, result_directory)
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
        from hisim.economics.report_plots import write_report_plots
        from hisim.economics.reporting import run_plausibility_checks, write_cost_summary, write_lifecycle_report

        checks = run_plausibility_checks(matrix, inputs)
        write_cost_summary(matrix, inputs, checks, result_directory)
        write_lifecycle_report(matrix, inputs, database, checks, result_directory, scenario_cube=scenario_cube)
        write_report_plots(matrix, result_directory)
        bad = [check for check in checks if check.status != "PASS"]
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
    if parameters.price_basis_year is None:
        parameters.price_basis_year = _pick_price_basis_year(database, parameters.country, inputs.simulation_year)
    write_parity_report(inputs, database, parameters, result_directory)
