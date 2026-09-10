"""End-to-end test: a real simulation with COMPUTE_LIFECYCLE_COSTS on (cost_spec.md §10).

Modeled on tests/test_electricity_meter.py. Verifies the parallel engine runs in shadow mode
next to the legacy COMPUTE_OPEX/COMPUTE_CAPEX path and writes only new files.

**Surface.** `hisim/economics/bridge.py` — the postprocessing entry point that turns a *finished*
simulation into `EvaluationInputs` and writes the export set. It is the upstream half of seam 1
(roadmap/cost-spec-v2.md §2.1), and this is the only module in the economics test suite that runs
the actual simulator: everything else works on hand-built inputs, precisely so that this one file
carries the "does it survive contact with HiSim" risk alone.

**How it covers it.** One day at hourly resolution with weather, PV, a building, an occupancy
profile, an electricity meter and an idealized heater — small enough to run in CI, wired richly
enough that the adapter has to recognize a meter, a priced device and a carrier. The assertions
are deliberately structural rather than numeric: the legacy CSVs still exist (§10 rule 1 — the old
path is untouched), every new export file was written (§10 rule 3), the per-subject NPVs
reconcile to the perspective total (§7.4), the PV system and the ELECTRICITY carrier both show up
as subjects, and the KPI file carries namespaced names with bands (§7.3). Exact euro figures are
the job of `test_economics_engine.py`; pinning them here would only make this test break whenever
a price file changes.

**Error class.** A failure here means the *wiring* broke — postprocessing did not reach the
engine, the adapter stopped recognizing a component class, an export file was renamed, or the
engine's guard swallowed an exception and produced nothing. It does not mean a formula is wrong;
if the engine math were broken, the engine tests would fail first and this one would still pass.

**The second half of this file** does not run a simulation at all. The bridge also owns a handful
of policies that decide what a run *does not* produce — which failures abort instead of degrading,
what is deleted when one does, how a partial year is flagged, and how the setup-declared context is
merged — and each of those is a branch an end-to-end run never steers into. They are exercised
directly on the bridge's own functions with an empty fleet, which is enough: none of them depends
on what was simulated.
"""

# clean

import datetime
import json
import os
from pathlib import Path
from typing import Optional, cast

import pandas as pd
import pytest

import hisim.simulator as sim
from hisim import loadtypes, utils
from hisim.economics import bridge
from hisim.economics.bridge import EconomicContext
from hisim.economics.carriers import EnergyCarrier
from hisim.economics.database import CostDataError, CostDatabase
from hisim.economics.evaluator import EconomicEvaluator, EvaluationInputs, SubjectCostFacts
from hisim.economics.facts import ComponentCostFacts
from hisim.economics.parameters import EconomicParameters
from hisim.economics.perspectives import load_default_bundle, select_applicable
from hisim.economics.plausibility import CheckIds, CheckStatus, run_plausibility_checks
from hisim.economics.scenarios import ScenarioSet
from hisim.components import (
    building,
    electricity_meter,
    generic_pv_system,
    idealized_electric_heater,
    loadprofilegenerator_utsp_connector,
    weather,
)
from hisim.postprocessingoptions import PostProcessingOptions
from hisim.simulator import SimulationParameters

# PATH needed to build simulator, PATH is fake
PATH: str = "../system_setups/household_for_test_economics_bridge.py"


@utils.measure_execution_time
@pytest.mark.extendedbase
def test_lifecycle_cost_engine_runs_in_shadow_mode() -> None:
    """One-day simulation; the lifecycle engine writes its exports next to the legacy CSVs."""
    my_simulation_parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60 * 60)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_OPEX)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_CAPEX)
    # LIFECYCLE_COST_REPORT implies COMPUTE_LIFECYCLE_COSTS and adds the human-readable reports.
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.LIFECYCLE_COST_REPORT)

    path_to_be_added = str(Path(PATH).resolve().parent)
    my_sim: sim.Simulator = sim.Simulator(
        module_directory=path_to_be_added,
        my_simulation_parameters=my_simulation_parameters,
        module_filename="household_for_test_economics_bridge",
    )
    my_sim.set_simulation_parameters(my_simulation_parameters)

    # The weather config is built first because the PV and building configs copy its identity
    # (weather_identity) and the sizing kernel refuses a config that still carries an unresolved
    # field, exactly as the shipped system setups do it.
    my_weather_config = weather.WeatherConfig.get_default(location_entry=weather.LocationEnum.AACHEN)
    my_weather = weather.Weather(
        config=my_weather_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    my_photovoltaic_system_config = generic_pv_system.PVSystemConfig.get_scaled_pv_system(
        share_of_maximum_pv_potential=1, rooftop_area_in_m2=120
    )
    my_photovoltaic_system_config.weather_identity = my_weather_config.identity()
    my_photovoltaic_system = generic_pv_system.PVSystem(
        config=my_photovoltaic_system_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    my_building_config = building.BuildingConfig.preset_standard("Building")
    my_building_config.weather_identity = my_weather_config.identity()
    my_building = building.Building(
        config=my_building_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    my_occupancy = loadprofilegenerator_utsp_connector.UtspLpgConnector(
        config=loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig.get_default_utsp_connector_config(),
        my_simulation_parameters=my_simulation_parameters,
    )
    my_electricity_meter = electricity_meter.ElectricityMeter(
        my_simulation_parameters=my_simulation_parameters,
        config=electricity_meter.ElectricityMeterConfig.get_electricity_meter_default_config(),
    )
    my_idealized_electric_heater = idealized_electric_heater.IdealizedElectricHeater(
        my_simulation_parameters=my_simulation_parameters,
        config=idealized_electric_heater.IdealizedHeaterConfig.get_default_config(),
    )

    my_photovoltaic_system.connect_only_predefined_connections(my_weather)
    my_building.connect_only_predefined_connections(my_weather, my_occupancy)
    my_building.connect_input(
        my_building.ThermalPowerDelivered,
        my_idealized_electric_heater.component_name,
        my_idealized_electric_heater.ThermalPowerDelivered,
    )
    my_idealized_electric_heater.connect_input(
        my_idealized_electric_heater.TheoreticalThermalBuildingDemand,
        my_building.component_name,
        my_building.TheoreticalThermalBuildingDemand,
    )
    my_electricity_meter.add_component_input_and_connect(
        source_object_name=my_photovoltaic_system.component_name,
        source_component_output=my_photovoltaic_system.ElectricityOutput,
        source_load_type=loadtypes.LoadTypes.ELECTRICITY,
        source_unit=loadtypes.Units.WATT,
        source_tags=[loadtypes.ComponentType.PV, loadtypes.InandOutputType.ELECTRICITY_PRODUCTION],
        source_weight=999,
    )
    my_electricity_meter.add_component_input_and_connect(
        source_object_name=my_occupancy.component_name,
        source_component_output=my_occupancy.ElectricalPowerConsumption,
        source_load_type=loadtypes.LoadTypes.ELECTRICITY,
        source_unit=loadtypes.Units.WATT,
        source_tags=[loadtypes.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED],
        source_weight=999,
    )

    my_sim.add_component(my_weather)
    my_sim.add_component(my_photovoltaic_system)
    my_sim.add_component(my_occupancy)
    my_sim.add_component(my_building)
    my_sim.add_component(my_idealized_electric_heater)
    my_sim.add_component(my_electricity_meter)

    my_sim.run_all_timesteps()

    result_directory = Path(my_sim._simulation_parameters.result_directory)  # pylint: disable=W0212

    # Legacy outputs are still written (the parallel engine never touches them).
    assert (result_directory / "investment_cost_co2_footprint.csv").is_file()
    assert (result_directory / "operational_costs_co2_footprint.csv").is_file()

    # The new engine wrote its exports (§10 rule 3).
    for file_name in (
        "lifecycle_costs.json",
        "component_costs.json",
        "component_costs.csv",
        "cash_flow_timeline.csv",
        "lifecycle_kpis.json",
        "economic_inputs.json",
        "cost_provenance.json",
        "cost_audit.csv",
        "cost_parity_report.csv",
        # The audit's own figure, written on every cost run rather than with the report (Q9),
        # which is why matplotlib is a dependency of this path and not only of the report path.
        "cost_audit_timeline_heatmap.png",
        # LIFECYCLE_COST_REPORT outputs. The per-perspective charts carry the perspective they
        # are about; only the comparison of perspectives is matrix-wide and therefore unsuffixed.
        "cost_summary.md",
        "lifecycle_report.html",
        "lifecycle_annual_cash_flows_greenfield_gross.png",
        "lifecycle_perspective_costs.png",
    ):
        assert (result_directory / file_name).is_file(), f"missing {file_name}"

    with open(result_directory / "lifecycle_costs.json", encoding="utf-8") as file:
        lifecycle = json.load(file)
    assert "greenfield_gross" in lifecycle
    result = lifecycle["greenfield_gross"]

    # Reconciliation invariant (§7.4): subject NPVs sum to the perspective total.
    def best_estimate_of(value):
        """Reads the BEST_ESTIMATE slot out of an exported money figure.

        Every monetary field in `lifecycle_costs.json` is written either as a `{min, best_estimate, max}`
        object (§3.9 band) or, for degenerate bands, as a bare number; this picks the middle
        world in both encodings so the reconciliation below does not depend on which form the
        current price data happens to produce.
        """
        return value["best_estimate"] if isinstance(value, dict) else value

    subject_sum = sum(best_estimate_of(value) for value in result["npv_by_component"].values())
    assert subject_sum == pytest.approx(best_estimate_of(result["total_npv_in_euro"]), rel=1e-6)

    # The PV system and the electricity carrier both appear as subjects.
    subjects = set(result["npv_by_component"].keys())
    assert any("PVSystem" in subject for subject in subjects)
    assert "ELECTRICITY" in subjects

    # The KPI file carries namespaced names with uncertainty bands (§7.3).
    with open(result_directory / "lifecycle_kpis.json", encoding="utf-8") as file:
        kpis = json.load(file)["Lifecycle costs"]
    assert any("Equivalent annual cost" in name for name in kpis)


# --------------------------------------------------------------------- bridge policy (no run)


class _Parameters:
    """The attributes the bridge reads off `SimulationParameters`, and nothing else.

    A real `SimulationParameters` would drag in a result-path provider and a logging setup that
    none of the policies below touch. Everything here is what `compute_lifecycle_costs` and
    `build_evaluation_inputs` actually read: the clock, the result directory and the two optional
    economic attachments.
    """

    def __init__(self, result_directory: str, days: float = 365.0) -> None:
        """A run of `days` days at quarter-hour resolution writing into `result_directory`."""
        self.year = 2024
        self.country = "DE"
        self.seconds_per_timestep = 900
        self.start_date = datetime.datetime(2024, 1, 1)
        self.end_date = self.start_date + datetime.timedelta(days=days)
        self.result_directory = result_directory
        # Annotated rather than inferred from the None: the tests below assign real objects, and
        # a variable mypy typed `None` from its initializer cannot hold one.
        self.economic_parameters: Optional[EconomicParameters] = None
        self.economic_context: Optional[EconomicContext] = None


def _unusable_scenario_set() -> ScenarioSet:
    """A value in the `scenario_set` slot that the cube cannot evaluate.

    Every *malformed* real `ScenarioSet` is rejected by `ScenarioSet.from_json` at parse time —
    which is the behaviour §4.6 wants and therefore exactly what cannot be used to reach the
    evaluation-time failure path this test is about. So the slot is filled with a value that is not
    a scenario set at all, and the cast records that deliberate lie rather than hiding it: whatever
    `evaluate_cube` reaches for is missing, so the cube fails without the test depending on its
    internals.
    """
    return cast(ScenarioSet, object())


class _Contract:
    """A stand-in for a `TariffContract` with only the two fields the interval lookup reads."""

    class _CapacityCharge:
        """The capacity-charge block, carrying only its billing interval."""

        def __init__(self, minutes: int) -> None:
            """Holds the interval the peaks are metered over."""
            self.billing_interval_in_minutes = minutes

    def __init__(self, carrier, minutes: int) -> None:
        """A contract for `carrier` whose capacity charge bills on `minutes`-long intervals."""
        self.carrier = carrier
        self.capacity_charge = self._CapacityCharge(minutes)


class _ProviderWrapper:
    """A wrapper carrying a component that holds a tariff contract, as `TariffProvider` does."""

    def __init__(self, contract) -> None:
        """Wraps a bare object whose only attribute is the contract."""
        self.my_component = type("_Provider", (), {"contract": contract})()


def _evaluated_empty_matrix():
    """A real `EvaluationMatrix` from an empty fleet — the cheapest one the engine will produce.

    The panel checks below are about a finding that depends on the *simulated period*, not on what
    was simulated, so the matrix only has to be a genuine one. Evaluating empty inputs takes
    milliseconds and keeps the test off hand-built result objects, which would have to be updated
    whenever `LifecycleCostResult` grows a field.
    """
    parameters = EconomicParameters(country="DE")
    evaluator = EconomicEvaluator(CostDatabase(), parameters, None)
    inputs = EvaluationInputs(simulation_year=2024, simulated_period_fraction=1.0)
    return evaluator.evaluate_matrix(
        inputs, select_applicable(load_default_bundle(), has_register=False)
    )


class TestExtrapolationOfAPartialYear:
    """§8.5: a run shorter than a year is multiplied up, and both outputs have to say so."""

    def test_a_short_run_warns_with_the_span_the_fraction_and_the_factor(self, tmp_path, capsys):
        """Catches a 365x extrapolation being published as if it had been measured.

        A one-day run's energy quantities and bills are divided by 1/365 to reach a year. Nothing
        in the outputs used to mention it: the numbers look exactly like a full-year run's. The
        warning has to carry all three figures a reader needs to judge the result — how long was
        simulated, what share of a year that is, and what everything was multiplied by.
        """
        parameters = _Parameters(str(tmp_path), days=1.0)

        inputs = bridge.build_evaluation_inputs([], [], pd.DataFrame(), parameters)

        assert inputs.simulated_period_fraction == pytest.approx(1.0 / 365.0)
        logged = capsys.readouterr().out
        assert "extrapolat" in logged
        assert "365" in logged  # the factor
        assert "not a measurement" in logged

    def test_a_full_year_run_does_not_warn(self, tmp_path, capsys):
        """The warning must not fire on the ordinary case, or it stops being read."""
        bridge.build_evaluation_inputs([], [], pd.DataFrame(), _Parameters(str(tmp_path)))

        assert "extrapolat" not in capsys.readouterr().out

    def test_the_panel_carries_the_same_statement(self):
        """Catches the extrapolation living only in a log the reader of the report never sees.

        `cost_summary.md` and the HTML report are what a reviewer reads weeks later; a log line is
        gone by then. The plausibility panel is the one channel both of them render, so the
        statement goes in as a WARN finding with the factor in its context.
        """
        report = run_plausibility_checks(_evaluated_empty_matrix(), simulated_period_fraction=1.0 / 365.0)

        findings = [f for f in report.findings if f.check_id == CheckIds.CHECK_SIMULATED_PERIOD]
        assert len(findings) == 1
        assert findings[0].status == CheckStatus.WARN
        assert findings[0].context["extrapolation_factor"] == pytest.approx(365.0)

    def test_a_full_year_adds_no_panel_row(self):
        """A row saying "this run covered a whole year" would be noise in every full-year report."""
        report = run_plausibility_checks(_evaluated_empty_matrix(), simulated_period_fraction=1.0)

        assert not [f for f in report.findings if f.check_id == CheckIds.CHECK_SIMULATED_PERIOD]


class TestFailuresAbortInsteadOfDegrading:
    """Every failure that used to be logged and worked around now fails the run (§10)."""

    def test_an_unloadable_subsidy_catalog_aborts(self, tmp_path):
        """Catches a configured subsidy engine silently becoming the flat legacy shim.

        Setting `subsidy_catalog_path` is what switches the §5.4 solver on. A catalog that will not
        load used to log an error and leave `catalog` at None, which continues under the §10.1 flat
        shim: the run then publishes subsidy figures that have nothing to do with the catalog it
        was configured with, and the only trace is a line in a log. The path and the underlying
        error have to be in the message, because "it did not load" is not actionable on its own.
        """
        parameters = _Parameters(str(tmp_path))
        parameters.economic_parameters = EconomicParameters(
            country="DE", subsidy_catalog_path=str(tmp_path / "there_is_no_catalog_here")
        )

        with pytest.raises(CostDataError) as raised:
            bridge.compute_lifecycle_costs([], [], pd.DataFrame(), parameters)

        assert "there_is_no_catalog_here" in str(raised.value)
        assert "flat-shim" in str(raised.value)

    def test_a_failing_scenario_cube_aborts(self, tmp_path):
        """Catches a report quietly missing the sensitivity section it was asked for.

        A declared scenario set is a requested part of the answer. The cube failing used to log
        "base results unaffected" and continue, which is true and beside the point: section 9 and
        `scenario_cube.csv` are then simply absent from a run that asked for them, and absence is
        not something a reader notices.
        """
        parameters = _Parameters(str(tmp_path))
        parameters.economic_context = EconomicContext(scenario_set=_unusable_scenario_set())

        with pytest.raises(CostDataError) as raised:
            bridge.compute_lifecycle_costs([], [], pd.DataFrame(), parameters)

        assert "scenario" in str(raised.value).lower()

    def test_the_exports_written_before_a_failure_are_removed(self, tmp_path):
        """Catches a half-written export set being readable as a complete one.

        The scenario cube runs *after* the numeric exports, so a failing one is a real failure
        part-way through: `lifecycle_costs.json`, the KPI file and the audit are already on disk.
        `postprocessing_main` deliberately logs and continues for a non-cost error, so without the
        cleanup that run finishes green over a cost report missing half its files.

        `economic_inputs.json` is deliberately kept: it is the extract of the *simulation*, written
        before any economics happened, and the D7 refusal message promises it survives an abort.
        """
        parameters = _Parameters(str(tmp_path))
        parameters.economic_context = EconomicContext(scenario_set=_unusable_scenario_set())

        with pytest.raises(CostDataError):
            bridge.compute_lifecycle_costs([], [], pd.DataFrame(), parameters)

        left_behind = set(os.listdir(tmp_path))
        assert "economic_inputs.json" in left_behind
        for export in ("lifecycle_costs.json", "lifecycle_kpis.json", "cash_flow_timeline.csv",
                       "cost_audit.csv", "component_costs.json"):
            assert export not in left_behind, export

    def test_a_successful_run_keeps_its_exports(self, tmp_path):
        """The cleanup must only ever run on the failure path."""
        bridge.compute_lifecycle_costs([], [], pd.DataFrame(), _Parameters(str(tmp_path)))

        written = set(os.listdir(tmp_path))
        assert {"economic_inputs.json", "lifecycle_costs.json", "lifecycle_kpis.json"} <= written

    def test_a_missing_plot_layer_refuses_before_the_first_export(self, tmp_path, monkeypatch):
        """Catches an environment without matplotlib losing a complete export set to a picture.

        The audit's ledger heatmap is drawn on every cost run, from a lazy import four export
        files in. An installation without the renderer therefore used to fail *after* those files
        existed, and the cleanup then removed a set that was correct — for want of a PNG. The
        probe runs before the first write, so the answer is a refusal naming the module, and the
        directory is untouched.
        """
        import importlib.util

        real_find_spec = importlib.util.find_spec
        monkeypatch.setattr(
            importlib.util, "find_spec",
            lambda name, *rest: None if name == "matplotlib" else real_find_spec(name, *rest),
        )

        with pytest.raises(CostDataError) as raised:
            bridge.compute_lifecycle_costs([], [], pd.DataFrame(), _Parameters(str(tmp_path)))

        assert "matplotlib" in str(raised.value) and "Nothing was written" in str(raised.value)
        assert not os.listdir(tmp_path)


class TestChartsThatWereNotDrawn:
    """A figure the run could not draw is reported, and never takes an export down with it.

    The renderers hand their skips back instead of logging them (the CLI prints them, this path
    logs them), and the bridge leaves the same lines in a file beside the images: a reader who
    finds twelve PNGs where the report names thirteen is rarely the person reading the log.
    """

    def test_a_run_that_skipped_a_chart_writes_the_sidecar(self, tmp_path):
        """The empty-fleet run draws almost nothing, so it has plenty to account for.

        The file exists only when there is something in it — an empty
        `lifecycle_plots_not_drawn.txt` in every result directory would be one more artifact to
        explain — and every line names the chart, its perspective and the reason.
        """
        bridge.compute_lifecycle_costs([], [], pd.DataFrame(), _Parameters(str(tmp_path)))

        note = tmp_path / "lifecycle_plots_not_drawn.txt"
        assert note.is_file()
        lines = [line for line in note.read_text(encoding="utf-8").splitlines() if line]
        assert lines, "the sidecar exists because there was something to say"
        assert all(": " in line for line in lines), lines
        assert any("ledger heatmap" in line for line in lines)

    def test_a_failing_heatmap_renderer_leaves_the_audit_tables_in_place(self, tmp_path, monkeypatch):
        """The rollback may not be triggered by a picture that would not render.

        `cost_audit.csv` and everything before it are written by then, and the engine deletes its
        exports when a run fails afterwards. A matplotlib failure — a font cache, a backend, a
        malformed colour — would therefore have removed a correct audit table. It is recorded as a
        skipped chart instead, and the reason carries the exception.
        """
        from hisim.economics import report_plots

        def explode(*_arguments, **_keywords):
            raise RuntimeError("no fonts in this environment")

        monkeypatch.setattr(report_plots, "plot_timeline_heatmap", explode)

        bridge.compute_lifecycle_costs([], [], pd.DataFrame(), _Parameters(str(tmp_path)))

        written = set(os.listdir(tmp_path))
        assert {"lifecycle_costs.json", "cost_audit.csv", "lifecycle_plots_not_drawn.txt"} <= written
        assert "cost_audit_timeline_heatmap.png" not in written
        note = (tmp_path / "lifecycle_plots_not_drawn.txt").read_text(encoding="utf-8")
        assert "no fonts in this environment" in note


class TestCapacityChargeBillingInterval:
    """§8.4: peaks are metered over the *contract's* interval, not over a hard-coded 15 minutes."""

    def test_the_interval_is_read_from_the_run_s_tariff_contract(self):
        """Catches every contract being metered on 15 minutes whatever it says.

        A capacity charge is billed on the highest mean power over the contract's metering
        interval. Reading that interval off the provider in the run is what keeps the peaks the
        bridge extracts and the peaks the contract bills the same quantity.
        """
        wrappers = [_ProviderWrapper(_Contract(EnergyCarrier.ELECTRICITY, 30))]

        intervals = bridge._capacity_billing_intervals(wrappers)  # pylint: disable=protected-access

        assert intervals == {EnergyCarrier.ELECTRICITY: 30}

    def test_a_run_without_a_provider_has_no_intervals(self):
        """No provider means no contract to read, and the default interval applies."""
        assert not bridge._capacity_billing_intervals([])  # pylint: disable=protected-access

    def test_a_timestep_that_does_not_divide_the_interval_says_so(self, capsys):
        """Catches a carrier silently billed without its capacity charge.

        A timestep that does not divide the billing interval cannot be grouped into interval means,
        so no peaks are computed — which is right, a ragged grouping would be worse. What was wrong
        is that it happened in silence: the bill then misses a component nobody can see is missing.
        """
        series = pd.Series([1000.0] * 20)

        peaks, annual = bridge._peaks_from_power_series(series, 400, 15)  # pylint: disable=protected-access

        assert peaks == [] and annual == 0.0
        logged = capsys.readouterr().out
        assert "400" in logged and "15" in logged


class TestContextMerge:
    """`_merge_context`: what the setup declared fills gaps, and a declared zero is a declaration."""

    def _inputs(self):
        """Simulation-derived inputs with one extracted subject and one extracted scalar."""
        return EvaluationInputs(
            simulation_year=2024,
            simulated_period_fraction=1.0,
            cost_facts=[
                SubjectCostFacts(
                    "PVSystem",
                    ComponentCostFacts(
                        asset_class=loadtypes.ComponentType.PV, size=5.0, size_unit=loadtypes.Units.KILOWATT
                    ),
                )
            ],
            living_area_in_m2=150.0,
        )

    def test_a_declared_zero_overrides_an_extracted_value(self):
        """Catches a declared 0.0 being read as "not declared" and silently dropped.

        The merge used to be `context.x or inputs.x`, which cannot tell a declared zero from an
        absent field. A building with no heat demand, a rent-free unit and an emission intensity of
        zero are all statements a setup author can make, and all three were being ignored.
        """
        inputs = self._inputs()

        bridge._merge_context(inputs, EconomicContext(living_area_in_m2=0.0))  # pylint: disable=protected-access

        assert inputs.living_area_in_m2 == 0.0

    def test_an_undeclared_field_leaves_the_extracted_value_alone(self):
        """The other half of the same rule: the context fills gaps, it does not overrule."""
        inputs = self._inputs()

        bridge._merge_context(inputs, EconomicContext())  # pylint: disable=protected-access

        assert inputs.living_area_in_m2 == 150.0

    def test_a_negative_quantity_is_refused_where_it_is_declared(self):
        """Catches a negative area or demand turning into a negative KPI that looks like a result.

        None of these fields is checked downstream: a negative living area produces a negative
        EUR/m² and a negative heat demand a negative levelized cost of heat, both of which read as
        numbers rather than as the typo they are. The refusal names the field while the setup that
        wrote it is still on screen.
        """
        with pytest.raises(ValueError) as raised:
            EconomicContext(annual_heat_demand_in_kwh=-1.0)

        assert "annual_heat_demand_in_kwh" in str(raised.value)

    def test_a_technical_attribute_key_matching_no_subject_warns(self, capsys):
        """Catches per-subject attributes silently going nowhere because of a typo.

        Those attributes are what §5.3 subsidy conditions resolve against — a SCOP, a refrigerant,
        an achieved U-value — so a key naming a subject that does not exist turns "the grant was
        denied" into a mystery. The warning names the unmatched key and the subjects that do exist.
        """
        inputs = self._inputs()
        context = EconomicContext(technical_attributes_by_subject={"HeatPumpp": {"scop": 4.2}})

        bridge._merge_context(inputs, context)  # pylint: disable=protected-access

        logged = capsys.readouterr().out
        assert "HeatPumpp" in logged and "PVSystem" in logged

    def test_a_matching_key_is_merged_and_does_not_warn(self):
        """The ordinary case: attributes join the extracted facts rather than replacing them."""
        inputs = self._inputs()
        context = EconomicContext(technical_attributes_by_subject={"PVSystem": {"module": "mono"}})

        bridge._merge_context(inputs, context)  # pylint: disable=protected-access

        assert inputs.cost_facts[0].facts.technical_attributes == {"module": "mono"}
        assert inputs.cost_facts[0].facts.size == 5.0


class TestSharedHelpers:
    """The two duplications the review asked to collapse, pinned so they stay collapsed."""

    def test_the_parameters_resolver_prefers_what_the_setup_attached(self, tmp_path):
        """A setup's own `EconomicParameters` win over the country default."""
        parameters = _Parameters(str(tmp_path))
        attached = EconomicParameters(country="AT")
        parameters.economic_parameters = attached

        assert bridge._resolve_economic_parameters(parameters) is attached  # pylint: disable=protected-access

    def test_the_parameters_resolver_falls_back_to_the_simulation_country(self, tmp_path):
        """Without an attachment the run is priced against its own country's defaults."""
        parameters = _Parameters(str(tmp_path))

        resolved = bridge._resolve_economic_parameters(parameters)  # pylint: disable=protected-access

        assert resolved.country == "DE"

    def test_the_output_column_lookup_is_positional_and_absence_is_none(self):
        """One lookup behind both the sum and the raw series, so they cannot locate differently."""

        class _Output:
            """The three attributes the positional lookup and the unit conversion read."""

            def __init__(self, component_name: str, field_name: str, unit) -> None:
                """Names one declared output and the unit it is declared in."""
                self.component_name = component_name
                self.field_name = field_name
                self.unit = unit

        outputs = [
            _Output("Meter", "A", loadtypes.Units.WATT_HOUR),
            _Output("Meter", "B", loadtypes.Units.WATT),
        ]
        frame = pd.DataFrame({0: [1000.0, 2000.0], 1: [7.0, 8.0]})

        # pylint: disable=protected-access
        assert bridge._sum_output_column("Meter", "A", outputs, frame, 900) == pytest.approx(3.0)
        series = bridge._power_series("Meter", "B", outputs, frame)
        assert series is not None
        assert series.tolist() == [7.0, 8.0]
        assert bridge._sum_output_column("Meter", "C", outputs, frame, 900) is None
        assert bridge._power_series("Meter", "C", outputs, frame) is None
