"""The economics CLI, its exports, and the legacy parity harness (cost_spec.md §7.2, §9.7).

Third of the three data/integration test files (split per the PR-3 review's 500-line rule):
`python -m hisim.economics` end to end with its export files, and the parity harness that
compares the engine against the legacy capex/opex path (documented deltas only). Data-layer
resolution is in `test_economics_data_and_integration.py`; serialization round-trips are in
`test_economics_data_serialization.py`.
"""

import os
import pytest
from hisim.economics.carriers import EnergyCarrier
from hisim.economics.database import CostDatabase
from hisim.economics.evaluator import EconomicEvaluator, EvaluationInputs, SubjectCostFacts
from hisim.economics.facts import BillingDeterminants, ComponentCostFacts
from hisim.economics.parameters import EconomicParameters
from hisim.economics.perspectives import InstallationContext, Perspective, SubsidyMode
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import ComponentType, Units

pytestmark = pytest.mark.base


class TestCliAndExports:
    """§3.10 / §4.6 CLI on a stored result directory."""

    def test_evaluate_and_explain_cli(self, tmp_path, capsys):
        """The evaluate and explain subcommands work offline on archived inputs."""
        from hisim.economics.__main__ import main
        from hisim.economics.serialization import write_inputs

        facts = ComponentCostFacts(asset_class=ComponentType.HEAT_PUMP, size=10.0, size_unit=Units.KILOWATT)
        inputs = EvaluationInputs(
            simulation_year=2024,
            simulated_period_fraction=1.0,
            cost_facts=[SubjectCostFacts("HeatPump", facts)],
            billing=[BillingDeterminants(carrier=EnergyCarrier.ELECTRICITY, energy_bought_in_kwh=4000.0)],
        )
        write_inputs(inputs, str(tmp_path))
        assert main(["evaluate", str(tmp_path)]) == 0
        for file_name in ("lifecycle_costs.json", "component_costs.json", "cash_flow_timeline.csv",
                          "cost_provenance.json"):
            assert os.path.isfile(tmp_path / file_name), file_name
        assert main(["explain", str(tmp_path), "--value", "greenfield_gross/total_npv_in_euro"]) == 0
        output = capsys.readouterr().out
        assert "src_capex_" in output  # the report reaches the resolved sources

    def test_unresolvable_subject_makes_the_cli_exit_non_zero(self, tmp_path, capsys):
        """D7 (§8): evaluate/explain/report refuse to price a partially unresolvable extract."""
        from hisim.economics.__main__ import main
        from hisim.economics.serialization import write_inputs

        assert not CostDatabase().has_device_entry(ComponentType.WINDTURBINE, "DE")  # premise
        inputs = EvaluationInputs(
            simulation_year=2024,
            simulated_period_fraction=1.0,
            cost_facts=[
                SubjectCostFacts(
                    "HeatPump",
                    ComponentCostFacts(asset_class=ComponentType.HEAT_PUMP, size=10.0, size_unit=Units.KILOWATT),
                ),
                SubjectCostFacts(
                    "WindTurbine",
                    ComponentCostFacts(asset_class=ComponentType.WINDTURBINE, size=5.0, size_unit=Units.KILOWATT),
                ),
            ],
            billing=[BillingDeterminants(carrier=EnergyCarrier.ELECTRICITY, energy_bought_in_kwh=4000.0)],
        )
        write_inputs(inputs, str(tmp_path))
        for argv in (
            ["evaluate", str(tmp_path)],
            ["explain", str(tmp_path), "--value", "greenfield_gross/total_npv_in_euro"],
            ["report", str(tmp_path)],
        ):
            assert main(argv) == 2, argv
            error_output = capsys.readouterr().err
            assert "WindTurbine" in error_output
            assert "no partial cost results" in error_output
        # Nothing was priced: no result artifact was written for the blocked extract.
        assert not os.path.isfile(tmp_path / "lifecycle_costs.json")

    def test_a_missing_parameters_file_fails_instead_of_pricing_with_defaults(self, tmp_path, capsys):
        """Issue #23: a mistyped `--parameters` path used to produce a full, silently-defaulted run."""
        from hisim.economics.__main__ import main
        from hisim.economics.serialization import write_inputs

        inputs = EvaluationInputs(
            simulation_year=2024,
            simulated_period_fraction=1.0,
            cost_facts=[
                SubjectCostFacts(
                    "HeatPump",
                    ComponentCostFacts(asset_class=ComponentType.HEAT_PUMP, size=10.0, size_unit=Units.KILOWATT),
                )
            ],
        )
        write_inputs(inputs, str(tmp_path))
        bogus = str(tmp_path / "does_not_exist.json")
        assert main(["evaluate", str(tmp_path), "--parameters", bogus]) == 2
        error_output = capsys.readouterr().err
        assert "does_not_exist.json" in error_output
        assert not os.path.isfile(tmp_path / "lifecycle_costs.json")  # nothing was priced
        # Omitting the flag keeps the documented default behaviour.
        assert main(["evaluate", str(tmp_path)]) == 0
        assert os.path.isfile(tmp_path / "lifecycle_costs.json")

    def test_exported_figures_are_not_minted_by_the_writer(self, tmp_path):
        """W4.1: every derived number in an export equals its independent recomputation.

        The exports stopped deriving anything themselves (annuity multiplication, discount
        factors, the support total); this pins the *written files* against hand-computed values
        so the switch to the view-model cannot have moved a number.
        """
        import csv

        from hisim.economics.exports import (
    build_lifecycle_kpi_entries,
    write_cash_flow_timeline,
    write_component_costs,
)
        from hisim.economics.results import EvaluationMatrix
        from hisim.economics.timeline import CostCategory

        facts = ComponentCostFacts(
            asset_class=ComponentType.HEAT_PUMP,
            size=10.0,
            size_unit=Units.KILOWATT,
            investment_cost_override_in_euro=UncertainValue(16000.0, 12800.0, 20800.0),
            lifetime_override_in_years=18.0,
            override_source="test",
        )
        inputs = EvaluationInputs(
            simulation_year=2024,
            simulated_period_fraction=1.0,
            cost_facts=[SubjectCostFacts("HeatPump", facts)],
            billing=[BillingDeterminants(carrier=EnergyCarrier.ELECTRICITY, energy_bought_in_kwh=4000.0)],
        )
        perspective = Perspective(
            id="gross", installation_context=InstallationContext.GREENFIELD, subsidy_mode=SubsidyMode.none()
        )
        result = EconomicEvaluator(CostDatabase(), EconomicParameters(price_basis_year=2024)).evaluate(
            inputs, perspective
        )
        matrix = EvaluationMatrix(results={"gross": result})
        interest = result.parameters.interest_rate
        horizon = result.parameters.observation_period_in_years
        annuity = interest * (1 + interest) ** horizon / ((1 + interest) ** horizon - 1)

        write_component_costs(matrix, str(tmp_path))
        with open(tmp_path / "component_costs.csv", encoding="utf-8") as file:
            rows = list(csv.DictReader(file, delimiter=";"))
        assert rows
        for row in rows:
            npv = result.component_breakdowns[row["subject"]].npv_by_category[CostCategory(row["category"])]
            assert float(row["npv_best_estimate"]) == pytest.approx(npv.best_estimate)
            assert float(row["eac_best_estimate"]) == pytest.approx(npv.best_estimate * annuity)
            assert float(row["eac_min"]) == pytest.approx(npv.minimum * annuity)

        write_cash_flow_timeline(matrix, str(tmp_path))
        with open(tmp_path / "cash_flow_timeline.csv", encoding="utf-8") as file:
            flows = list(csv.DictReader(file, delimiter=";"))
        assert len(flows) == len(result.timeline.entries)
        for row, entry in zip(flows, result.timeline.entries):
            assert float(row["discounted_best_estimate"]) == pytest.approx(
                entry.amount_in_euro.best_estimate / ((1 + interest) ** entry.year)
            )

        # No catalog decisions here, so the support KPI must be absent rather than zero.
        names = {entry.name for entry in build_lifecycle_kpi_entries(matrix)}
        assert not any(name.startswith("Total subsidies received") for name in names)

    def test_actor_kpis_reach_the_kpi_file(self, tmp_path):
        """§6.5 per-actor net present costs are published, and stay zero-sum in the file (19).

        The actor split was computed on every allocated perspective and then reached no KPI file
        at all: `actor_kpi_entries` had no caller. Publishing it is only worth anything if the
        published numbers still add up, so the landlord and tenant KPIs are checked against the
        system total of the same evaluation — the §6 invariant, asserted on the written JSON
        rather than on the in-memory result.
        """
        import json

        from hisim.economics.exports import write_lifecycle_kpis
        from hisim.economics.perspectives import ActorScope
        from hisim.economics.results import EvaluationMatrix

        facts = ComponentCostFacts(
            asset_class=ComponentType.HEAT_PUMP,
            size=10.0,
            size_unit=Units.KILOWATT,
            investment_cost_override_in_euro=UncertainValue(16000.0, 12800.0, 20800.0),
            lifetime_override_in_years=18.0,
            override_source="test",
        )
        inputs = EvaluationInputs(
            simulation_year=2024,
            simulated_period_fraction=1.0,
            cost_facts=[SubjectCostFacts("HeatPump", facts)],
            billing=[
                BillingDeterminants(carrier=EnergyCarrier.ELECTRICITY, energy_bought_in_kwh=4000.0)
            ],
            living_area_in_m2=120.0,
            heated_floor_area_in_m2=120.0,
            current_cold_rent_in_euro_per_m2_month=8.0,
        )
        evaluator = EconomicEvaluator(CostDatabase(), EconomicParameters(price_basis_year=2024))
        matrix = EvaluationMatrix()
        for perspective_id, scope in (("system", ActorScope.SYSTEM), ("landlord", ActorScope.LANDLORD)):
            matrix.results[perspective_id] = evaluator.evaluate(
                inputs,
                Perspective(
                    id=perspective_id,
                    installation_context=InstallationContext.GREENFIELD,
                    actor_scope=scope,
                    subsidy_mode=SubsidyMode.none(),
                ),
            )
        with open(write_lifecycle_kpis(matrix, str(tmp_path)), encoding="utf-8") as file:
            published = json.load(file)["Lifecycle costs"]

        landlord = published["Net present cost of landlord [EUR] (landlord)"]
        tenant = published["Net present cost of tenant [EUR] (landlord)"]
        system = published["Net present cost over 20 years [EUR] (system)"]
        for slot in ("value", "valueMin", "valueMax"):
            assert landlord[slot] + tenant[slot] == pytest.approx(system[slot])
        # A perspective that allocated nothing to a payer contributes no actor KPI at all.
        assert "Net present cost of landlord [EUR] (system)" not in published

    def test_scenario_cube_cli(self, tmp_path):
        """The re-pricing CLI writes scenario_cube.csv/json."""
        import json

        from hisim.economics.__main__ import main
        from hisim.economics.serialization import write_inputs

        facts = ComponentCostFacts(asset_class=ComponentType.HEAT_PUMP, size=10.0, size_unit=Units.KILOWATT)
        inputs = EvaluationInputs(
            simulation_year=2024,
            simulated_period_fraction=1.0,
            cost_facts=[SubjectCostFacts("HeatPump", facts)],
        )
        write_inputs(inputs, str(tmp_path))
        scenarios_path = tmp_path / "scenarios.json"
        with open(scenarios_path, "w", encoding="utf-8") as file:
            json.dump(
                {
                    "base": "central",
                    "mode": "ONE_AT_A_TIME",
                    "axes": [{"name": "interest", "field": "interest_rate", "levels": {"low": 0.01, "high": 0.05}}],
                },
                file,
            )
        assert main(["evaluate", str(tmp_path), "--scenarios", str(scenarios_path)]) == 0
        assert os.path.isfile(tmp_path / "scenario_cube.csv")
        assert os.path.isfile(tmp_path / "scenario_cube.json")


class TestParityHarness:
    """§9.7 shadow-mode parity against the legacy CSVs (read-only)."""

    def test_parity_report_matches_legacy_formula(self, tmp_path):
        """New facts x database reproduce the legacy investment figures for a clean case."""
        import csv

        from hisim.economics.audit import write_parity_report

        database = CostDatabase()
        entry = database.get_device_entry(ComponentType.HEAT_PUMP, 2024, "DE")
        legacy_investment = entry.specific_investment.best_estimate * 10.0
        legacy_period = legacy_investment / entry.service_life_in_years * 1.0
        legacy_csv = tmp_path / "investment_cost_co2_footprint.csv"
        with open(legacy_csv, "w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file, delimiter=";")
            writer.writerow(
                ["Component", "Investment [EUR]", "Device CO2-footprint [kg]",
                 "Subsidy as percentage of investment [-]", "Rest-Investment [EUR]", "Lifetime [Years]",
                 "Investment for simulated period [EUR]", "Rest-Investment for simulated period [EUR]",
                 "Device CO2-footprint for simulated period [kg]"]
            )
            writer.writerow(["HeatPump", legacy_investment, 0, 0.3, legacy_investment * 0.7,
                             entry.service_life_in_years, legacy_period, legacy_period * 0.7, 0])
        facts = ComponentCostFacts(asset_class=ComponentType.HEAT_PUMP, size=10.0, size_unit=Units.KILOWATT)
        inputs = EvaluationInputs(
            simulation_year=2024,
            simulated_period_fraction=1.0,
            cost_facts=[SubjectCostFacts("HeatPump", facts)],
        )
        report_path = write_parity_report(inputs, database, EconomicParameters(price_basis_year=2024), str(tmp_path))
        assert report_path is not None
        with open(report_path, encoding="utf-8") as file:
            rows = list(csv.DictReader(file, delimiter=";"))
        investment_row = next(row for row in rows if row["Figure"] == "investment")
        assert float(investment_row["Delta"]) == pytest.approx(0.0, abs=0.01)
        assert investment_row["Note"] == ""
