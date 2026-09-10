"""Spot-series parsing, validation gaps, and result serialization round-trips (cost_spec.md §7.2, §9.6).

Second of the three data/integration test files (split per the PR-3 review's 500-line rule):
strict spot-series CSV parsing (review finding 25), the validation harness's gap findings,
the `lifecycle_costs.json` serialization round-trip, and variant comparison. The data-layer
resolution tests are in `test_economics_data_and_integration.py`; the CLI and the parity
harness are in `test_economics_cli_and_parity.py`.
"""

from typing import Any, Dict
import os
import pytest
from hisim.economics.carriers import EnergyCarrier
from hisim.economics.database import CostDatabase
from hisim.economics.evaluator import EconomicEvaluator, EvaluationInputs, SubjectCostFacts
from hisim.economics.facts import BillingDeterminants, ComponentCostFacts, ExistingAsset
from hisim.economics.parameters import EconomicParameters
from hisim.economics.perspectives import InstallationContext, Perspective, SubsidyMode
from hisim.economics.results import compare
from hisim.economics.uncertainty import Slot, UncertainValue
from hisim.loadtypes import ComponentType, Units

pytestmark = pytest.mark.base

SCHEME_TEMPLATE: Dict[str, Any] = {
    "id": "XX_ONE",
    "jurisdiction": {"country": "XX", "region": None},
    "valid_from": "2024-01-01",
    "valid_to": None,
    "legal_basis": "synthetic validation-test scheme",
    "url": "https://example.invalid/scheme",
    "applies_to": {"asset_classes": ["HEAT_PUMP"], "measure_kinds": ["INSTALL"]},
    "eligibility": {"all": []},
    "benefit": {"kind": "SHARE_OF_ELIGIBLE_COST", "rate": 0.3},
    "eligible_cost": {"categories": ["INVESTMENT"]},
    "cumulation": {"group": None, "combined_rate_cap": None, "excludes": []},
    "payout": {"kind": "UPFRONT_GRANT"},
    # Catalog schemes must cite sources since W2.4 (there is no sources.json in the tmp
    # catalogs these fixtures write, so the id is only checked for presence).
    "source_ids": ["src_synthetic_validation_test"],
}


def write_subsidy_catalog(tmp_path, schemes) -> str:
    """Writes a country catalog `XX.json` from the given scheme dicts; returns the base path.

    Each argument is an *overlay* on `SCHEME_TEMPLATE`, deep-copied so the template cannot be
    mutated between tests: a caller states only the field it wants wrong (a typo'd `excludes`, a
    duplicate id, an inconsistent group cap) and inherits a valid scheme for everything else.
    Returns the directory rather than the file because that is what `SubsidyCatalog.load` and the
    validators take — they resolve `<country>.json` beneath it themselves.
    """
    import copy
    import json

    payload: Dict[str, Any] = {
        "catalog_snapshot_date": "2026-01-01",
        "overall_cap_share": None,
        "schemes": [],
    }
    for overrides in schemes:
        scheme = copy.deepcopy(SCHEME_TEMPLATE)
        scheme.update(copy.deepcopy(overrides))
        payload["schemes"].append(scheme)
    with open(os.path.join(str(tmp_path), "XX.json"), "w", encoding="utf-8") as file:
        json.dump(payload, file)
    return str(tmp_path)


class TestSpotSeriesParsing:
    """Issue #25a: a corrupt spot price line shifts every later hour, so it must not be skipped."""

    @staticmethod
    def _write(tmp_path, lines) -> str:
        """Writes `test_series.csv` with the given lines and returns the directory to load from."""
        with open(os.path.join(tmp_path, "test_series.csv"), "w", encoding="utf-8") as file:
            file.write("\n".join(lines))
        return str(tmp_path)

    def test_a_header_blank_lines_and_two_column_rows_still_load(self, tmp_path):
        """Everything the documented format tolerates keeps working."""
        from hisim.economics.tariffs import load_spot_series

        base = self._write(
            tmp_path, ["timestamp,price_in_euro_per_kwh", "2024-01-01 00:00,0.05", "", "2024-01-01 01:00,0.06", ""]
        )
        assert load_spot_series("test_series", base) == pytest.approx([0.05, 0.06])

    def test_a_corrupt_line_fails_with_its_line_number(self, tmp_path):
        """A value that is not a price is a data error, not one hour quietly missing."""
        from hisim.economics.database import CostDataError
        from hisim.economics.tariffs import load_spot_series

        base = self._write(tmp_path, ["price", "0.05", "n/a", "0.07"])
        with pytest.raises(CostDataError) as raised:
            load_spot_series("test_series", base)
        message = str(raised.value)
        assert "line 3" in message
        assert "n/a" in message
        assert "test_series.csv" in message

    def test_the_shipped_reference_series_still_loads(self):
        """The shipped synthetic series is the regression guard for the header rule."""
        from hisim.economics.tariffs import load_spot_series

        assert len(load_spot_series("synthetic_reference_hourly")) == 8760


class TestValidationGaps:
    """W2.5 / §7 B9: what `validate_all` did not check before."""

    def test_shipped_tariff_contracts_validate(self):
        """The tariff directory is walked now, and the shipped contract passes."""
        from hisim.economics.validation import validate_tariff_contracts

        report = validate_tariff_contracts()
        assert report.errors == []

    def test_malformed_tariff_contract_is_an_error(self, tmp_path):
        """A contract that cannot be parsed no longer ships silently."""
        import json

        from hisim.economics.validation import validate_tariff_contracts

        with open(tmp_path / "BROKEN.json", "w", encoding="utf-8") as file:
            json.dump({"id": "BROKEN", "carrier": "ELECTRICITY"}, file)  # no source_ids, no supply
        report = validate_tariff_contracts(str(tmp_path))
        assert any("BROKEN.json" in error and "failed to parse" in error for error in report.errors)

    def test_tariff_contract_id_must_match_its_file_name(self, tmp_path):
        """Contracts are resolved by file name, so a mismatching id is unreachable."""
        import json

        from hisim.economics.validation import validate_tariff_contracts

        contract = {
            "id": "DIFFERENT_ID",
            "carrier": "ELECTRICITY",
            "jurisdiction": {"country": "DE", "region": None},
            "valid_from_year": 2024,
            "supply": {"kind": "FLAT", "working_price_in_euro_per_kwh": 0.3},
            "standing_charge_in_euro_per_year": 100,
            "source_ids": ["src_expert_migration"],
        }
        with open(tmp_path / "FILE_NAME.json", "w", encoding="utf-8") as file:
            json.dump(contract, file)
        report = validate_tariff_contracts(str(tmp_path))
        assert any("can never be resolved by its own id" in error for error in report.errors)

    def test_tariff_sources_must_resolve_against_the_registry(self, tmp_path):
        """A contract citing an unknown registry id is an error (§3.10)."""
        import json

        from hisim.economics.database import SourceRegistry
        from hisim.economics.validation import validate_tariff_contracts

        contract = {
            "id": "SRC_TEST",
            "carrier": "ELECTRICITY",
            "jurisdiction": {"country": "DE", "region": None},
            "valid_from_year": 2024,
            "supply": {"kind": "FLAT", "working_price_in_euro_per_kwh": 0.3},
            "standing_charge_in_euro_per_year": 100,
            "source_ids": ["src_does_not_exist"],
        }
        with open(tmp_path / "SRC_TEST.json", "w", encoding="utf-8") as file:
            json.dump(contract, file)
        registry = SourceRegistry.load(os.path.join(CostDatabase.DEFAULT_PATH, "sources.json"))
        report = validate_tariff_contracts(str(tmp_path), registry)
        assert any("unknown source id 'src_does_not_exist'" in error for error in report.errors)

    def test_dangling_spot_series_is_an_error(self, tmp_path):
        """A DYNAMIC contract pointing at a missing price series is malformed data."""
        import json

        from hisim.economics.validation import validate_tariff_contracts

        contract = {
            "id": "SPOT_TEST",
            "carrier": "ELECTRICITY",
            "jurisdiction": {"country": "DE", "region": None},
            "valid_from_year": 2024,
            "supply": {"kind": "DYNAMIC", "spot_series": "no_such_series"},
            "standing_charge_in_euro_per_year": 100,
            "source_ids": ["src_expert_migration"],
        }
        with open(tmp_path / "SPOT_TEST.json", "w", encoding="utf-8") as file:
            json.dump(contract, file)
        report = validate_tariff_contracts(str(tmp_path))
        assert any("does not exist" in error for error in report.errors)

    def test_inline_sources_in_a_tariff_file_are_an_error(self, tmp_path):
        """W2.4b: a shipped contract must cite registry entries, not a prose citation."""
        import json

        from hisim.economics.validation import validate_tariff_contracts

        contract = {
            "id": "INLINE_TEST",
            "carrier": "ELECTRICITY",
            "jurisdiction": {"country": "DE", "region": None},
            "valid_from_year": 2024,
            "supply": {"kind": "FLAT", "working_price_in_euro_per_kwh": 0.3},
            "standing_charge_in_euro_per_year": 100,
            "source_ids": ["inline:a citation nobody can review"],
        }
        with open(tmp_path / "INLINE_TEST.json", "w", encoding="utf-8") as file:
            json.dump(contract, file)
        report = validate_tariff_contracts(str(tmp_path))
        assert any("cites inline source(s)" in error for error in report.errors)
        assert not report.warnings

    def test_shipped_tariff_contracts_cite_the_registry(self):
        """The shipped dynamic contract now resolves against the cost database registry."""
        from hisim.economics.database import SourceRegistry
        from hisim.economics.validation import validate_tariff_contracts

        registry = SourceRegistry.load(os.path.join(CostDatabase.DEFAULT_PATH, "sources.json"))
        report = validate_tariff_contracts(registry=registry)
        assert report.errors == [] and report.warnings == []

    def test_excludes_must_name_an_existing_scheme(self, tmp_path):
        """A typo in `excludes` silently disabled the exclusion before."""
        from hisim.economics.validation import validate_subsidy_catalog

        base = write_subsidy_catalog(
            tmp_path, [{"cumulation": {"group": None, "combined_rate_cap": None, "excludes": ["XX_TYPO"]}}]
        )
        report = validate_subsidy_catalog("XX", base)
        assert any("excludes unknown scheme id 'XX_TYPO'" in error for error in report.errors)

    def test_duplicate_scheme_ids_are_an_error(self, tmp_path):
        """Two schemes with the same id make the exclusion and award records ambiguous."""
        from hisim.economics.validation import validate_subsidy_catalog

        base = write_subsidy_catalog(tmp_path, [{}, {}])
        report = validate_subsidy_catalog("XX", base)
        assert any("duplicate scheme id 'XX_ONE'" in error for error in report.errors)

    def test_inconsistent_combined_rate_cap_in_a_group_is_an_error(self, tmp_path):
        """The solver applies the group minimum, so members must declare the same cap."""
        from hisim.economics.validation import validate_subsidy_catalog

        base = write_subsidy_catalog(
            tmp_path,
            [
                {"id": "XX_A", "cumulation": {"group": "XX_RATES", "combined_rate_cap": 0.7, "excludes": []}},
                {"id": "XX_B", "cumulation": {"group": "XX_RATES", "combined_rate_cap": 0.5, "excludes": []}},
            ],
        )
        report = validate_subsidy_catalog("XX", base)
        assert any("inconsistent combined_rate_cap" in error for error in report.errors)

    def test_consistent_group_caps_pass(self, tmp_path):
        """The coherent case stays clean (the shipped DE groups are all coherent)."""
        from hisim.economics.validation import validate_subsidy_catalog

        base = write_subsidy_catalog(
            tmp_path,
            [
                {"id": "XX_A", "cumulation": {"group": "XX_RATES", "combined_rate_cap": 0.7, "excludes": []}},
                {"id": "XX_B", "cumulation": {"group": "XX_RATES", "combined_rate_cap": 0.7, "excludes": ["XX_A"]}},
            ],
        )
        report = validate_subsidy_catalog("XX", base)
        assert not [error for error in report.errors if "combined_rate_cap" in error or "excludes" in error]

    def test_broken_annual_shares_are_reported(self, tmp_path):
        """Load-time typing already rejects them; the report names the catalog and the reason."""
        from hisim.economics.validation import validate_subsidy_catalog

        base = write_subsidy_catalog(
            tmp_path,
            [{"benefit": {"kind": "TAX_CREDIT", "rate": 0.2, "years": 3, "annual_shares": [0.5, 0.4, 0.4]}}],
        )
        report = validate_subsidy_catalog("XX", base)
        assert any("annual_shares must sum to 1" in error for error in report.errors)


class TestSerializationRoundtrip:
    """§4.6: economic_inputs.json enables re-pricing without re-simulation."""

    def _inputs(self) -> EvaluationInputs:
        """The base input set every round-trip test starts from.

        Chosen to make the serializer work for its result: a real (non-degenerate) band, an
        override with its source string, free-form `technical_attributes`, a partial-year fraction,
        a carrier with bought *and* sold energy plus twelve monthly peaks, and a physical field
        (heat demand) that is not a cost at all. Anything the writer quietly drops shows up as a
        different evaluation on reload.
        """
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
        assert restored.total_npv_in_euro.best_estimate == pytest.approx(original.total_npv_in_euro.best_estimate)
        assert restored.total_npv_in_euro.minimum == pytest.approx(original.total_npv_in_euro.minimum)

    def _inputs_with_existing_heating(self) -> EvaluationInputs:
        """Inputs whose subsidy context carries a functioning gas boiler (BEG speed bonus).

        `SubsidyBuildingContext.existing_heating` was the §7 B1 serialization hole: the shipped DE
        catalog conditions the speed bonus on it, and it was silently dropped on write, so
        re-pricing an archived run lost a 20 % award. The asset here carries every attribute the
        loader has to restore — including a replacement-cost override band and the `is_functional`
        flag the condition actually reads — so a partial fix cannot pass.
        """
        from hisim.economics.subsidies import (
    ApplicantActor,
    ApplicantProfile,
    SubsidyBuildingContext,
    SubsidyContext,
)

        inputs = self._inputs()
        inputs.subsidy_context = SubsidyContext(
            applicant=ApplicantProfile(
                actor=ApplicantActor.OWNER_OCCUPIER,
                taxable_household_income_in_euro=35000.0,
                main_residence=True,
            ),
            building=SubsidyBuildingContext(
                construction_year=1985,
                dwelling_units=1,
                residential_floor_area_in_m2=150.0,
                commercial_floor_area_in_m2=0.0,
                existing_heating=ExistingAsset(
                    asset_class=ComponentType.GAS_HEATER,
                    size=15.0,
                    size_unit=Units.KILOWATT,
                    installation_year=2005,
                    replacement_cost_override_in_euro=UncertainValue(9000, 7500, 11000),
                    is_functional=True,
                    energy_carrier=EnergyCarrier.NATURAL_GAS,
                ),
            ),
        )
        return inputs

    def test_roundtrip_preserves_existing_heating(self, tmp_path):
        """building.existing_heating survives the file round trip with all attributes."""
        from hisim.economics.serialization import read_inputs, write_inputs

        inputs = self._inputs_with_existing_heating()
        write_inputs(inputs, str(tmp_path))
        reloaded = read_inputs(str(tmp_path))

        original_asset = inputs.subsidy_context.building.existing_heating
        restored_asset = reloaded.subsidy_context.building.existing_heating
        assert original_asset is not None
        assert restored_asset is not None
        assert restored_asset.asset_class is original_asset.asset_class
        assert restored_asset.size == pytest.approx(original_asset.size)
        assert restored_asset.size_unit is original_asset.size_unit
        assert restored_asset.installation_year == original_asset.installation_year
        assert restored_asset.is_functional is original_asset.is_functional
        assert restored_asset.energy_carrier is original_asset.energy_carrier
        assert restored_asset.replaced_by_asset_classes == original_asset.replaced_by_asset_classes
        assert restored_asset.replacement_cost_override_in_euro is not None
        assert original_asset.replacement_cost_override_in_euro is not None
        assert restored_asset.replacement_cost_override_in_euro.best_estimate == pytest.approx(
            original_asset.replacement_cost_override_in_euro.best_estimate
        )

    def test_roundtrip_preserves_speed_bonus_eligibility(self, tmp_path):
        """The BEG speed bonus conditions on existing_heating and must survive re-pricing."""
        from hisim.economics.serialization import read_inputs, write_inputs
        from hisim.economics.subsidies import MeasureForSubsidy, SubsidyCatalog, solve_cumulation
        from hisim.economics.timeline import CostCategory

        inputs = self._inputs_with_existing_heating()
        write_inputs(inputs, str(tmp_path))
        reloaded = read_inputs(str(tmp_path))

        catalog = SubsidyCatalog.load("DE")
        measure = MeasureForSubsidy(
            subject="HeatPump",
            facts=ComponentCostFacts(
                asset_class=ComponentType.HEAT_PUMP,
                size=10.0,
                size_unit=Units.KILOWATT,
                technical_attributes={"scop": 4.0, "refrigerant": "R290"},
            ),
            measure_kind="REPLACE",
            cost_by_category={CostCategory.INVESTMENT: UncertainValue.exact(20000.0)},
        )
        discount = EconomicParameters(price_basis_year=2024).discount_factor
        original_ids = {
            award.scheme_id
            for award in solve_cumulation(catalog, measure, inputs.subsidy_context, 2024, discount).applied
        }
        restored_ids = {
            award.scheme_id
            for award in solve_cumulation(catalog, measure, reloaded.subsidy_context, 2024, discount).applied
        }
        assert "DE_BEG_EM_HP_SPEED_2024" in original_ids
        assert restored_ids == original_ids

    @staticmethod
    def _in_memory_contract():
        """A contract that exists in no catalog file (W1.4: it must survive the round trip).

        Contracts used to be stored by id and re-resolved from the shipped tariffs directory, so a
        contract built in memory — by a test, a worked example or a study script — was
        unrecoverable after a write. This one is deliberately maximal: two time-of-use bands (one
        banded, one exact) with weekday and hour masks, all three additive components, a capacity
        charge with its billing interval, a fixed feed-in tariff and a §14a controllability
        discount, so every branch of the embedding format is exercised by a single fixture.
        """
        from hisim.economics.tariffs import (
    CapacityCharge,
    CapacityChargeKind,
    ControllabilityDiscount,
    ControllabilityKind,
    FeedIn,
    FeedInKind,
    SupplyKind,
    TariffContract,
    TariffSupply,
    TimeOfUseBand,
)

        return TariffContract(
            id="TEST_IN_MEMORY_ELECTRICITY",
            carrier=EnergyCarrier.ELECTRICITY,
            country="DE",
            region="NRW",
            valid_from_year=2024,
            supply=TariffSupply(
                kind=SupplyKind.TIME_OF_USE,
                bands=[
                    TimeOfUseBand("day", UncertainValue(0.30, 0.28, 0.34), weekdays=[0, 1, 2, 3, 4], hours=[8, 9]),
                    TimeOfUseBand("night", UncertainValue.exact(0.20), hours=[0, 1, 2]),
                ],
                markup_in_euro_per_kwh=UncertainValue.exact(0.02),
                grid_fee_in_euro_per_kwh=UncertainValue(0.09, 0.08, 0.11),
                taxes_and_levies_in_euro_per_kwh=UncertainValue.exact(0.05),
                vat_rate=0.19,
            ),
            standing_charge_in_euro_per_year=UncertainValue(120.0, 100.0, 150.0),
            capacity_charge=CapacityCharge(
                kind=CapacityChargeKind.MONTHLY_PEAK,
                price_in_euro_per_kw=UncertainValue.exact(12.0),
                billing_interval_in_minutes=15,
            ),
            feed_in=FeedIn(kind=FeedInKind.FIXED_TARIFF, rate_in_euro_per_kwh=UncertainValue.exact(0.081)),
            controllability_discount=ControllabilityDiscount(kind=ControllabilityKind.GRID_FEE_SHARE, grid_fee_reduction_share=0.4),
            source_ids=("inline:test contract",),
        )

    def test_roundtrip_preserves_in_memory_tariff_contract(self, tmp_path):
        """A contract that exists in no file round-trips and prices identically (W1.4)."""
        from hisim.economics.serialization import read_inputs, write_inputs
        from hisim.economics.tariffs import apply_tariff

        contract = self._in_memory_contract()
        inputs = self._inputs()
        inputs.tariff_contracts = {EnergyCarrier.ELECTRICITY: contract}
        write_inputs(inputs, str(tmp_path))
        reloaded = read_inputs(str(tmp_path))

        restored = reloaded.tariff_contracts[EnergyCarrier.ELECTRICITY]
        assert restored == contract  # includes source_ids, bands, feed-in and the discount
        determinants = inputs.billing[0]
        assert apply_tariff(determinants, restored).total().best_estimate == pytest.approx(
            apply_tariff(determinants, contract).total().best_estimate
        )

    def test_default_contracts_are_recorded_but_regenerated(self, tmp_path):
        """Contracts derived from the price entries are written, and not restored (unchanged)."""
        import json

        from hisim.economics.calculators.energy import default_contract as build_default_contract
        from hisim.economics.serialization import read_inputs, write_inputs

        database = CostDatabase()
        # Moved out of EconomicEvaluator._default_contract by the seam-3 split (cost-spec-v2 §2.3).
        default_contract = build_default_contract(
            EnergyCarrier.ELECTRICITY, 2024, database, EconomicParameters(price_basis_year=2024).country
        )
        inputs = self._inputs()
        inputs.tariff_contracts = {EnergyCarrier.ELECTRICITY: default_contract}
        write_inputs(inputs, str(tmp_path))
        with open(tmp_path / "economic_inputs.json", encoding="utf-8") as file:
            written = json.load(file)
        assert written["tariff_contracts"]["ELECTRICITY"]["id"] == default_contract.id
        assert read_inputs(str(tmp_path)).tariff_contracts == {}

    def test_old_format_with_contract_ids_still_reads(self):
        """Files written before contracts were embedded resolve their ids as before."""
        from hisim.economics.serialization import contracts_from_json

        by_id = contracts_from_json({"tariff_contract_ids": {"ELECTRICITY": "DE_DYNAMIC_SYNTHETIC_2024"}})
        assert by_id[EnergyCarrier.ELECTRICITY].id == "DE_DYNAMIC_SYNTHETIC_2024"
        # DEFAULT ids stay regenerated from the price entries.
        assert not contracts_from_json({"tariff_contract_ids": {"ELECTRICITY": "DE_DEFAULT_ELECTRICITY_2024"}})

    def test_embedded_contract_is_a_valid_catalog_file(self, tmp_path):
        """contract_to_json emits the §8.2 catalog schema, so one parser serves both."""
        import json

        from hisim.economics.tariffs import contract_to_json, load_tariff_contract

        contract = self._in_memory_contract()
        with open(tmp_path / f"{contract.id}.json", "w", encoding="utf-8") as file:
            json.dump(contract_to_json(contract), file)
        assert load_tariff_contract(contract.id, str(tmp_path)) == contract

    def test_context_without_existing_heating_key_still_reads(self):
        """Files written before the field was serialized still load; the field stays None."""
        from hisim.economics.serialization import subsidy_context_from_json

        context = subsidy_context_from_json(
            {"applicant": {"actor": "OWNER_OCCUPIER"}, "building": {"construction_year": 1985}}
        )
        assert context.building.existing_heating is None

    def test_roundtrip_preserves_the_per_subject_energy_attribution(self, tmp_path):
        """The energy-balance flows survive `economic_inputs.json` and reach the result again.

        Nothing prices this map, so a writer that dropped it would leave every published figure
        untouched and only the household energy balance would quietly disappear from a report
        rendered off an archived run — the exact failure mode an additive field invites. The
        fraction is 0.5, so the reloaded values are also visibly annualized rather than copied.
        """
        from hisim.economics.serialization import read_inputs, write_inputs

        inputs = self._inputs()
        inputs.energy_attribution_by_subject_in_kwh = {
            "ElectricityMeter": {"GRID_IMPORT": 2500.0, "GRID_EXPORT": 100.0},
            "Battery": {"BATTERY_CHARGE": 400.0, "BATTERY_DISCHARGE": 340.0},
        }
        write_inputs(inputs, str(tmp_path))
        reloaded = read_inputs(str(tmp_path))
        assert reloaded.energy_attribution_by_subject_in_kwh == inputs.energy_attribution_by_subject_in_kwh
        parameters = EconomicParameters(price_basis_year=2024)
        perspective = Perspective(
            id="gross", installation_context=InstallationContext.GREENFIELD, subsidy_mode=SubsidyMode.none()
        )
        result = EconomicEvaluator(CostDatabase(), parameters).evaluate(reloaded, perspective)
        annual = result.annual_energy_attribution_by_subject_in_kwh
        assert annual["Battery"]["BATTERY_CHARGE"] == pytest.approx(800.0)

    def test_an_extract_without_the_attribution_key_still_reads(self):
        """Every additive field defaults to empty, so an archive written before it still loads."""
        from hisim.economics.serialization import inputs_from_json

        restored = inputs_from_json({"simulation_year": 2024, "simulated_period_fraction": 1.0})
        assert restored.energy_attribution_by_subject_in_kwh == {}

    def test_result_roundtrip_preserves_the_additive_result_fields(self, tmp_path):
        """The result fields the report reads survive `lifecycle_costs.json` unchanged.

        One assertion per group added on the result side — the energy attribution, the anyway
        share with its basis, the levy verdict and the resolved assumptions — because each of them
        is read by exactly one section of the report and a lost one is invisible everywhere else.
        """
        from hisim.economics.exports import write_lifecycle_costs_json
        from hisim.economics.results import (
            AnywayBasisKinds,
            EconomicAssumptions,
            EmbodiedCo2Basis,
            EvaluationMatrix,
            ModernizationLevySummary,
            RateOrigin,
            ResolvedRate,
        )
        from hisim.economics.serialization import read_results

        parameters = EconomicParameters(price_basis_year=2024)
        perspective = Perspective(
            id="gross", installation_context=InstallationContext.GREENFIELD, subsidy_mode=SubsidyMode.none()
        )
        result = EconomicEvaluator(CostDatabase(), parameters).evaluate(self._inputs(), perspective)
        result.annual_energy_attribution_by_subject_in_kwh = {"PVSystem": {"PV_GENERATION": 4200.0}}
        result.anyway_share_by_subject = {"HeatPump": 0.3}
        result.anyway_basis_by_subject = {"HeatPump": 9000.0}
        result.anyway_basis_kind_by_subject = {"HeatPump": AnywayBasisKinds.NON_ENERGY_SHARE}
        result.modernization_levy = ModernizationLevySummary(
            annual_amount_in_euro=UncertainValue.exact(1800.0),
            general_leg_in_euro=UncertainValue.exact(1200.0),
            heating_leg_in_euro=UncertainValue.exact(600.0),
            cap_in_euro_per_m2_per_month=3.0,
            binding_mechanism_by_slot={Slot.BEST_ESTIMATE: "§559 general cap 3.00 EUR/m2*mo"},
        )
        result.assumptions = EconomicAssumptions(
            escalation_rates={"general": ResolvedRate(rate=0.02, origin=RateOrigin.COUNTRY_DEFAULTS)},
            annual_heat_demand_in_kwh=15000.0,
        )
        result.lifecycle_co2_result.emission_factor_by_carrier_in_kg_per_kwh = {"ELECTRICITY": 0.38}
        result.lifecycle_co2_result.embodied_basis_by_subject = {
            "HeatPump": EmbodiedCo2Basis(
                factor_in_kg_per_unit=120.0,
                size=10.0,
                size_unit="kW",
                per_installation_in_kg=1200.0,
                installations=2,
            )
        }

        write_lifecycle_costs_json(EvaluationMatrix(results={"gross": result}), str(tmp_path))
        restored = read_results(str(tmp_path))
        assert restored is not None
        reloaded = restored.results["gross"]
        assert reloaded.annual_energy_attribution_by_subject_in_kwh == {
            "PVSystem": {"PV_GENERATION": 4200.0}
        }
        assert reloaded.anyway_share_by_subject == {"HeatPump": 0.3}
        assert reloaded.anyway_basis_by_subject == {"HeatPump": 9000.0}
        assert reloaded.anyway_basis_kind_by_subject == {
            "HeatPump": AnywayBasisKinds.NON_ENERGY_SHARE
        }
        assert reloaded.modernization_levy is not None
        assert reloaded.modernization_levy.cap_binding_in_best_estimate
        assert reloaded.modernization_levy.binding_mechanism_by_slot[Slot.BEST_ESTIMATE].startswith(
            "§559"
        )
        assert reloaded.assumptions is not None
        assert reloaded.assumptions.escalation_rates["general"].origin == RateOrigin.COUNTRY_DEFAULTS
        assert reloaded.assumptions.annual_heat_demand_in_kwh == pytest.approx(15000.0)
        assert reloaded.lifecycle_co2_result.emission_factor_by_carrier_in_kg_per_kwh == {"ELECTRICITY": 0.38}
        assert reloaded.lifecycle_co2_result.embodied_basis_by_subject["HeatPump"].installations == 2

    def test_a_result_written_before_the_additive_fields_still_reads(self, tmp_path):
        """A stored result missing every new key loads with empty/None, never with an invention."""
        import json

        from hisim.economics.exports import write_lifecycle_costs_json
        from hisim.economics.results import EvaluationMatrix
        from hisim.economics.serialization import read_results

        parameters = EconomicParameters(price_basis_year=2024)
        perspective = Perspective(
            id="gross", installation_context=InstallationContext.GREENFIELD, subsidy_mode=SubsidyMode.none()
        )
        result = EconomicEvaluator(CostDatabase(), parameters).evaluate(self._inputs(), perspective)
        write_lifecycle_costs_json(EvaluationMatrix(results={"gross": result}), str(tmp_path))
        path = os.path.join(str(tmp_path), "lifecycle_costs.json")
        with open(path, encoding="utf-8") as file:
            raw = json.load(file)
        for key in (
            "annual_energy_attribution_by_subject_in_kwh",
            "anyway_share_by_subject",
            "anyway_basis_by_subject",
            "anyway_basis_kind_by_subject",
            "modernization_levy",
            "assumptions",
        ):
            raw["gross"].pop(key, None)
        raw["gross"]["lifecycle_co2"].pop("emission_factor_by_carrier_in_kg_per_kwh", None)
        raw["gross"]["lifecycle_co2"].pop("embodied_basis_by_subject", None)
        with open(path, "w", encoding="utf-8") as file:
            json.dump(raw, file)
        reloaded = read_results(str(tmp_path))
        assert reloaded is not None
        older = reloaded.results["gross"]
        assert older.annual_energy_attribution_by_subject_in_kwh == {}
        assert older.anyway_share_by_subject == {} and older.anyway_basis_by_subject == {}
        assert older.anyway_basis_kind_by_subject == {}
        assert older.modernization_levy is None and older.assumptions is None
        assert older.lifecycle_co2_result.emission_factor_by_carrier_in_kg_per_kwh == {}
        assert older.lifecycle_co2_result.embodied_basis_by_subject == {}


class TestVariantComparison:
    """§3.7 differential analysis."""

    def _result(self, investment, energy_kwh, band=None):
        """One evaluated variant: a device of the given cost running the given consumption.

        The two arguments are exactly the capex/opex trade-off a comparison is about — a
        capex-heavy variant against an opex-heavy one — so a test states its scenario in one line.
        The service life is set to the horizon and maintenance to zero, which removes replacements
        and recurring costs from the delta and leaves the payback curve a function of the
        investment and the energy bill alone. `band` replaces the exact investment when the test
        is about how a *shared* uncertainty behaves across two variants.
        """
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
        payback = comparison.discounted_payback_years["best_estimate"]
        assert payback is not None and 1 <= payback <= 20
        assert comparison.npv_delta_in_euro.best_estimate < 0  # the variant wins over 20 years

    def test_payback_curve_matches_an_independent_recomputation(self):
        """W4.4: the curve on the comparison equals a hand-rolled cumulative discounting."""
        reference = self._result(0.0, 20000.0)
        variant = self._result(15000.0, 5000.0)
        comparison = compare(reference, variant)
        interest = variant.parameters.interest_rate
        horizon = variant.parameters.observation_period_in_years
        for slot, attribute in (("low", "minimum"), ("best_estimate", "best_estimate"), ("high", "maximum")):
            expected, running = [], 0.0
            for year in range(horizon + 1):
                reference_amount = getattr(reference.annual_cost_series_nominal_in_euro[year], attribute)
                variant_amount = getattr(variant.annual_cost_series_nominal_in_euro[year], attribute)
                running += (reference_amount - variant_amount) / ((1 + interest) ** year)
                expected.append(running)
            assert comparison.cumulative_discounted_savings_in_euro[slot] == pytest.approx(expected)

    def test_payback_year_is_the_curves_zero_crossing(self):
        """The printed payback and the drawn curve cannot disagree (W4.4)."""
        reference = self._result(0.0, 20000.0)
        variant = self._result(15000.0, 5000.0)
        comparison = compare(reference, variant)
        for slot, curve in comparison.cumulative_discounted_savings_in_euro.items():
            year = comparison.discounted_payback_years[slot]
            crossings = [index for index, value in enumerate(curve) if index > 0 and value >= 0]
            assert year == (crossings[0] if crossings else None)

    def test_final_curve_point_is_the_npv_saving(self):
        """The curve ends at the NPV delta of that slot — same flows, same discounting."""
        reference = self._result(0.0, 20000.0)
        variant = self._result(15000.0, 5000.0)
        comparison = compare(reference, variant)
        saving = comparison.cumulative_discounted_savings_in_euro["best_estimate"][-1]
        assert saving == pytest.approx(
            reference.total_npv_in_euro.best_estimate - variant.total_npv_in_euro.best_estimate, rel=1e-9
        )

    def test_subject_alignment_emits_explicit_zeros(self):
        """Subjects present in only one variant appear with explicit deltas (§3.7)."""
        reference = self._result(0.0, 8000.0)
        variant = self._result(15000.0, 3000.0)
        comparison = compare(reference, variant)
        assert "Heater" in comparison.npv_delta_by_subject
        assert EnergyCarrier.ELECTRICITY.value in comparison.npv_delta_by_subject


class TestTheResultRecordsRefuseWhatTheyCannotVouchFor:
    """Construction-time and read-time invariants of the disclosure records (review, decision 1).

    Every record here is published as a *statement*: "this rate came from the country defaults",
    "this carrier was paid 8.2 ct per exported kWh under a fixed tariff", "this mass is 120 kg per
    kW times 10 kW". A record whose fields do not add up to its statement is worse than a missing
    one, because the report prints the statement either way and the reader checks the arithmetic.
    These tests pin the refusals — at construction, so a defect fails where it is made, and in
    `from_json`, so a stored file cannot smuggle one in past the constructor.
    """

    def test_a_rate_origin_outside_the_chain_is_refused(self):
        """An unknown origin used to read as "configuration", the best-sourced of the three."""
        from hisim.economics.results import RateOrigin, ResolvedRate

        assert ResolvedRate.from_json({"rate": 0.02, "origin": "country defaults"}).origin is (
            RateOrigin.COUNTRY_DEFAULTS
        )
        with pytest.raises(ValueError, match="not a step of the escalation fallback chain"):
            ResolvedRate.from_json({"rate": 0.02, "origin": "vibes"})

    def test_a_resolved_rate_round_trips_through_its_enum(self):
        """The JSON stays the same word it always was, so stored results keep loading."""
        from hisim.economics.results import RateOrigin, ResolvedRate

        rate = ResolvedRate(rate=0.03, origin=RateOrigin.GENERAL_FALLBACK, source_ids=["x"])
        assert rate.to_json()["origin"] == "general fallback"
        assert ResolvedRate.from_json(rate.to_json()) == rate

    @staticmethod
    def _contract(kind, rate=0.082):
        """A flat electricity contract with the given feed-in terms."""
        from hisim.economics.tariffs import FeedIn, SupplyKind, TariffContract, TariffSupply

        return TariffContract(
            id="TEST_FLAT",
            carrier=EnergyCarrier.ELECTRICITY,
            country="DE",
            region=None,
            valid_from_year=2024,
            supply=TariffSupply(
                kind=SupplyKind.FLAT, working_price_in_euro_per_kwh=UncertainValue.exact(0.32)
            ),
            standing_charge_in_euro_per_year=UncertainValue.exact(120.0),
            feed_in=FeedIn(kind=kind, rate_in_euro_per_kwh=UncertainValue.exact(rate)),
            source_ids=("src:1",),
        )

    def test_the_tariff_record_is_built_from_the_contract_and_round_trips(self):
        """One builder, so the kind and the rate cannot be filled inconsistently by hand."""
        from hisim.economics.results import TariffAssumption
        from hisim.economics.tariffs import FeedInKind

        assumption = TariffAssumption.from_contract(self._contract(FeedInKind.FIXED_TARIFF))
        assert assumption.feed_in_kind is FeedInKind.FIXED_TARIFF
        assert assumption.feed_in_rate_in_euro_per_kwh is not None
        assert assumption.feed_in_rate_in_euro_per_kwh.best_estimate == pytest.approx(0.082)
        assert assumption.to_json()["feed_in_kind"] == "FIXED_TARIFF"
        assert TariffAssumption.from_json(assumption.to_json()) == assumption

    def test_a_contract_without_feed_in_carries_no_rate(self):
        """`NONE` with a rate would publish a price nothing was ever paid at."""
        from hisim.economics.results import TariffAssumption
        from hisim.economics.tariffs import FeedInKind

        assumption = TariffAssumption.from_contract(self._contract(FeedInKind.NONE))
        assert assumption.feed_in_rate_in_euro_per_kwh is None
        assert TariffAssumption.from_json(assumption.to_json()) == assumption

    @pytest.mark.parametrize(
        "kind_value,rate",
        [("FIXED_TARIFF", None), ("NONE", {"min": 0.08, "best_estimate": 0.08, "max": 0.08})],
    )
    def test_a_feed_in_kind_and_rate_that_disagree_are_refused(self, kind_value, rate):
        """The assumptions table prints the two as one row; either half alone cannot be read."""
        from hisim.economics.results import TariffAssumption

        raw = {
            "carrier": "ELECTRICITY",
            "contract_id": "TEST_FLAT",
            "working_price_in_euro_per_kwh": 0.32,
            "standing_charge_in_euro_per_year": 120.0,
            "feed_in_kind": kind_value,
            "feed_in_rate_in_euro_per_kwh": rate,
        }
        with pytest.raises(ValueError, match="feed_in_kind"):
            TariffAssumption.from_json(raw)

    def test_an_unknown_feed_in_kind_is_refused(self):
        """The kind selects how the export revenue was computed; an unknown one is not a fact."""
        from hisim.economics.results import TariffAssumption

        with pytest.raises(ValueError, match="not a feed-in remuneration structure"):
            TariffAssumption.from_json(
                {
                    "carrier": "ELECTRICITY",
                    "contract_id": "TEST_FLAT",
                    "working_price_in_euro_per_kwh": 0.32,
                    "standing_charge_in_euro_per_year": 120.0,
                    "feed_in_kind": "BARTER",
                }
            )

    def test_the_levy_verdicts_are_keyed_by_slot_on_both_sides(self):
        """Slot keys travel as their values, and an unknown key is a verdict nobody would read."""
        from hisim.economics.results import LevyBindingMechanism, ModernizationLevySummary

        summary = ModernizationLevySummary(
            annual_amount_in_euro=UncertainValue.exact(1800.0),
            general_leg_in_euro=UncertainValue.exact(1200.0),
            heating_leg_in_euro=UncertainValue.exact(600.0),
            cap_in_euro_per_m2_per_month=3.0,
            binding_mechanism_by_slot={
                Slot.BEST_ESTIMATE: f"{LevyBindingMechanism.GENERAL_CAP} 3.00 EUR/m2*mo"
            },
        )
        assert summary.cap_binding_in_best_estimate
        assert summary.to_json()["binding_mechanism_by_slot"] == {
            "best_estimate": "§559 general cap 3.00 EUR/m2*mo"
        }
        assert ModernizationLevySummary.from_json(summary.to_json()) == summary
        with pytest.raises(ValueError, match="keyed by evaluation slot"):
            ModernizationLevySummary.from_json(
                {**summary.to_json(), "binding_mechanism_by_slot": {"average": "whatever"}}
            )

    def test_the_headline_cap_answer_is_derived_from_the_verdicts(self):
        """No stored flag to disagree with the verdict it summarizes."""
        from hisim.economics.results import LevyBindingMechanism, ModernizationLevySummary

        def summary(verdict):
            return ModernizationLevySummary(
                annual_amount_in_euro=UncertainValue.exact(100.0),
                general_leg_in_euro=UncertainValue.exact(100.0),
                heating_leg_in_euro=UncertainValue.exact(0.0),
                binding_mechanism_by_slot={Slot.BEST_ESTIMATE: verdict},
            )

        assert summary(f"{LevyBindingMechanism.HEATING_CAP} 0.50 EUR/m2*mo").cap_binding_in_best_estimate
        assert not summary(f"8% {LevyBindingMechanism.RATE_BELOW_CAP}").cap_binding_in_best_estimate
        # No verdict at all (no living area, no cap evaluated) is not "a cap decided it".
        assert not ModernizationLevySummary(
            annual_amount_in_euro=UncertainValue.exact(100.0),
            general_leg_in_euro=UncertainValue.exact(100.0),
            heating_leg_in_euro=UncertainValue.exact(0.0),
        ).cap_binding_in_best_estimate

    def test_an_embodied_co2_basis_that_does_not_multiply_out_is_refused(self):
        """The record is printed *as* the multiplication, so the three numbers are one statement."""
        from hisim.economics.results import EmbodiedCo2Basis

        basis = EmbodiedCo2Basis(
            factor_in_kg_per_unit=120.0, size=10.0, size_unit="kW", per_installation_in_kg=1200.0
        )
        assert EmbodiedCo2Basis.from_json(basis.to_json()) == basis
        with pytest.raises(ValueError, match="per installation"):
            EmbodiedCo2Basis(
                factor_in_kg_per_unit=120.0, size=10.0, size_unit="kW", per_installation_in_kg=999.0
            )
        with pytest.raises(ValueError, match="per installation"):
            EmbodiedCo2Basis.from_json({**basis.to_json(), "per_installation_in_kg": 999.0})

    def test_a_negative_energy_attribution_is_refused_on_both_sides_of_the_round_trip(self):
        """Direction is the role, so a negative kWh would be drawn on the wrong side of the bus."""
        from hisim.economics.calculators.aggregation import annual_energy_attribution
        from hisim.economics.serialization import _attribution_from_json, inputs_from_json

        with pytest.raises(ValueError, match="carries negative energy"):
            inputs_from_json(
                {
                    "simulation_year": 2024,
                    "simulated_period_fraction": 1.0,
                    "energy_attribution_by_subject_in_kwh": {"PVSystem": {"PV_GENERATION": -5.0}},
                }
            )
        with pytest.raises(ValueError, match="carries negative energy"):
            annual_energy_attribution({"PVSystem": {"PV_GENERATION": -5.0}}, 1.0)
        # The result side reads its (annualized) map through the same guarded helper.
        with pytest.raises(ValueError, match="carries negative energy"):
            _attribution_from_json(
                {"annual_energy_attribution_by_subject_in_kwh": {"PVSystem": {"PV_GENERATION": -5.0}}},
                "annual_energy_attribution_by_subject_in_kwh",
                "LifecycleCostResult.annual_energy_attribution_by_subject_in_kwh",
            )
