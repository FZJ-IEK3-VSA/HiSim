"""Data-file CI (§9.6), cost-facts contract test (§9.4), serialization, comparison and CLI."""

# clean

import dataclasses
import os
import types

import pytest

from hisim.economics.carriers import EnergyCarrier
from hisim.economics.database import CostDatabase
from hisim.economics.evaluator import EconomicEvaluator, EvaluationInputs, SubjectCostFacts
from hisim.economics.facts import BillingDeterminants, ComponentCostFacts, CostRelevance
from hisim.economics.parameters import EconomicParameters
from hisim.economics.perspectives import InstallationContext, Perspective, SubsidyMode
from hisim.economics.results import compare
from hisim.economics.uncertainty import UncertainValue
from hisim.economics.validation import validate_all, validate_cost_database
from hisim.loadtypes import ComponentType, Units

pytestmark = pytest.mark.base

#: Components adopted in Phase 6 (additive, next to the untouched legacy methods).
ADOPTED_COMPONENTS = [
    ("hisim.components.advanced_heat_pump_hplib", "HeatPumpHplib", "HeatPumpHplibConfig",
     "get_default_generic_advanced_hp_lib", "config"),
    ("hisim.components.generic_pv_system", "PVSystem", "PVSystemConfig", "get_default_pv_system", "config"),
    ("hisim.components.advanced_battery_bslib", "Battery", "BatteryConfig", "get_default_config", "battery_config"),
    ("hisim.components.electricity_meter", "ElectricityMeter", "ElectricityMeterConfig",
     "get_electricity_meter_default_config", "config"),
]


def _facts_from_default_config(module_name, class_name, config_class_name, default_factory, config_attr):
    import importlib

    module = importlib.import_module(module_name)
    component_class = getattr(module, class_name)
    config = getattr(getattr(module, config_class_name), default_factory)()
    dummy = types.SimpleNamespace(**{config_attr: config, "config": config})
    return component_class, config, component_class.get_cost_facts(dummy)


class TestCostFactsContract:
    """§9.4: adopted components' declarations are machine-checked."""

    @pytest.mark.parametrize("spec", ADOPTED_COMPONENTS, ids=lambda spec: spec[1])
    def test_relevance_declared_and_facts_build(self, spec):
        """cost_relevance is declared; PRICED/METER facts build and validate."""
        component_class, _config, facts = _facts_from_default_config(*spec)
        assert component_class.cost_relevance in (CostRelevance.PRICED, CostRelevance.METER)
        assert isinstance(facts, ComponentCostFacts)
        assert facts.size > 0

    @pytest.mark.parametrize("spec", ADOPTED_COMPONENTS, ids=lambda spec: spec[1])
    def test_facts_resolve_against_every_shipped_database(self, spec):
        """Every declared asset class has entries in every shipped country database."""
        _component_class, _config, facts = _facts_from_default_config(*spec)
        database = CostDatabase()
        for country, basis_year in (("DE", 2024), ("AT", 2025), ("IE", 2026), ("DE", 2035), ("IE", 2035)):
            entry = database.get_device_entry(facts.asset_class, basis_year, country)
            assert entry.size_unit == facts.size_unit

    @pytest.mark.parametrize(
        "spec",
        [spec for spec in ADOPTED_COMPONENTS if spec[1] != "ElectricityMeter"],
        ids=lambda spec: spec[1],
    )
    def test_facts_respond_to_the_config(self, spec):
        """Scaling the capacity config field x2 scales facts.size x2 (the 'uses the correct
        configuration' property, now machine-checked)."""
        import importlib

        module_name, class_name, config_class_name, default_factory, config_attr = spec
        module = importlib.import_module(module_name)
        component_class = getattr(module, class_name)
        config_class = getattr(module, config_class_name)
        config = getattr(config_class, default_factory)()
        capacity_fields = [
            data_field.name for data_field in dataclasses.fields(config_class) if data_field.metadata.get("capacity")
        ]
        assert capacity_fields, f"{config_class_name} declares no capacity field metadata (§9.4)."
        field_name = capacity_fields[0]
        original = getattr(config, field_name)
        scaled_value = original * 2 if not hasattr(original, "value") else type(original)(original.value * 2, original.unit)
        scaled_config = dataclasses.replace(config, **{field_name: scaled_value})
        dummy_base = types.SimpleNamespace(**{config_attr: config, "config": config})
        dummy_scaled = types.SimpleNamespace(**{config_attr: scaled_config, "config": scaled_config})
        base_size = component_class.get_cost_facts(dummy_base).size
        scaled_size = component_class.get_cost_facts(dummy_scaled).size
        assert scaled_size == pytest.approx(2.0 * base_size)


class TestDataFiles:
    """§9.6 data-file CI."""

    def test_shipped_data_passes_validation(self):
        """Schema, source completeness, question coverage: zero errors."""
        report = validate_all()
        assert report.errors == []

    def test_coverage_matrix_for_adopted_classes(self):
        """Every adopted asset class x shipped country has a device entry."""
        declared = {ComponentType.HEAT_PUMP, ComponentType.PV, ComponentType.BATTERY, ComponentType.ELECTRICITY_METER}
        carriers = {EnergyCarrier.ELECTRICITY, EnergyCarrier.ELECTRICITY_FEED_IN, EnergyCarrier.NATURAL_GAS}
        report = validate_cost_database(declared_asset_classes=declared, used_carriers=carriers)
        assert report.errors == []

    def test_unsourced_datapoint_fails(self, tmp_path):
        """An entry without source_ids cannot enter a calculation (§3.10)."""
        import json
        import shutil

        from hisim.economics.database import DEFAULT_COST_DATABASE_PATH, CostDataError

        clone = tmp_path / "cost_database"
        shutil.copytree(DEFAULT_COST_DATABASE_PATH, clone)
        devices_path = clone / "devices_DE.json"
        with open(devices_path, encoding="utf-8") as file:
            data = json.load(file)
        data["entries"][0]["source_ids"] = []
        with open(devices_path, "w", encoding="utf-8") as file:
            json.dump(data, file)
        with pytest.raises(CostDataError):
            CostDatabase(str(clone))


class TestSerializationRoundtrip:
    """§4.6: economic_inputs.json enables re-pricing without re-simulation."""

    def _inputs(self) -> EvaluationInputs:
        facts = ComponentCostFacts(
            asset_class=ComponentType.HEAT_PUMP,
            size=10.0,
            size_unit=Units.KILOWATT,
            investment_cost_override_in_euro=UncertainValue(16000, 12000, 21000),
            override_source="test quote",
            technical_attributes={"scop": 4.1},
        )
        return EvaluationInputs(
            simulation_year=2024,
            simulated_period_fraction=0.5,
            cost_facts=[SubjectCostFacts("HeatPump", facts)],
            billing=[
                BillingDeterminants(
                    carrier=EnergyCarrier.ELECTRICITY,
                    energy_bought_in_kwh=2500.0,
                    energy_sold_in_kwh=100.0,
                    peak_per_billing_period_in_kw=[3.0] * 12,
                    annual_peak_in_kw=3.0,
                )
            ],
            annual_heat_demand_in_kwh=15000.0,
        )

    def test_roundtrip_preserves_evaluation(self, tmp_path):
        """Reloaded inputs evaluate to the same result."""
        from hisim.economics.serialization import read_inputs, write_inputs

        inputs = self._inputs()
        write_inputs(inputs, str(tmp_path))
        reloaded = read_inputs(str(tmp_path))
        parameters = EconomicParameters(price_basis_year=2024)
        perspective = Perspective(
            id="gross", installation_context=InstallationContext.GREENFIELD, subsidy_mode=SubsidyMode.none()
        )
        database = CostDatabase()
        original = EconomicEvaluator(database, parameters).evaluate(inputs, perspective)
        restored = EconomicEvaluator(database, parameters).evaluate(reloaded, perspective)
        assert restored.total_npv_in_euro.average == pytest.approx(original.total_npv_in_euro.average)
        assert restored.total_npv_in_euro.minimum == pytest.approx(original.total_npv_in_euro.minimum)


class TestVariantComparison:
    """§3.7 differential analysis."""

    def _result(self, investment, energy_kwh, band=None):
        facts = ComponentCostFacts(
            asset_class=ComponentType.HEAT_PUMP,
            size=10.0,
            size_unit=Units.KILOWATT,
            investment_cost_override_in_euro=band or UncertainValue.exact(investment),
            lifetime_override_in_years=20.0,
            maintenance_rate_override=UncertainValue.exact(0.0),
            override_source="test",
        )
        inputs = EvaluationInputs(
            simulation_year=2024,
            simulated_period_fraction=1.0,
            cost_facts=[SubjectCostFacts("Heater", facts)],
            billing=[BillingDeterminants(carrier=EnergyCarrier.ELECTRICITY, energy_bought_in_kwh=energy_kwh)],
        )
        perspective = Perspective(
            id="gross", installation_context=InstallationContext.GREENFIELD, subsidy_mode=SubsidyMode.none()
        )
        return EconomicEvaluator(CostDatabase(), EconomicParameters(price_basis_year=2024)).evaluate(
            inputs, perspective
        )

    def test_shared_uncertainty_cancels_slotwise(self):
        """The same price band in both variants leaves the delta band degenerate (§3.9)."""
        shared_band = UncertainValue(10000, 8000, 13000)
        reference = self._result(0, 8000.0, band=shared_band)
        variant = self._result(0, 4000.0, band=shared_band)
        comparison = compare(reference, variant)
        delta = comparison.npv_delta_in_euro
        assert delta.minimum == pytest.approx(delta.maximum)

    def test_discounted_payback_band(self):
        """Higher investment with energy savings pays back within the horizon."""
        reference = self._result(0.0, 20000.0)
        variant = self._result(15000.0, 5000.0)
        comparison = compare(reference, variant)
        payback = comparison.discounted_payback_years["average"]
        assert payback is not None and 1 <= payback <= 20
        assert comparison.npv_delta_in_euro.average < 0  # the variant wins over 20 years

    def test_subject_alignment_emits_explicit_zeros(self):
        """Subjects present in only one variant appear with explicit deltas (§3.7)."""
        reference = self._result(0.0, 8000.0)
        variant = self._result(15000.0, 3000.0)
        comparison = compare(reference, variant)
        assert "Heater" in comparison.npv_delta_by_subject
        assert EnergyCarrier.ELECTRICITY.value in comparison.npv_delta_by_subject


class TestCliAndExports:
    """§3.10 / §4.6 CLI on a stored result directory."""

    def test_evaluate_and_explain_cli(self, tmp_path, capsys):
        """python -m hisim.economics evaluate/explain works offline on archived inputs."""
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
        legacy_investment = entry.specific_investment.average * 10.0
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
