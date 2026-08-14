"""Data-file CI (§9.6), cost-facts contract test (§9.4), serialization, comparison and CLI.

**Surface.** The parts of the module that are neither pure formula nor pure presentation, and
that are therefore easy to leave untested: the shipped JSON data files and their validators, the
declaration contract adopted components must satisfy, the `economic_inputs.json` round trip that
makes re-pricing without re-simulation possible (seam 1's contract, cost-spec-v2 §2.1), the
variant comparison, the `evaluate`/`explain`/`report` CLI, and the legacy parity harness.

**How it covers it.** Four distinct techniques, one per concern:

* *Data-file CI.* `validate_all()` and `validate_cost_database(...)` are run against the shipped
  files and must report zero errors — the same checks `python -m hisim.economics validate`
  performs, so a price PR that forgets a source id or a country fails in CI, not in a report.
  `TestValidationGaps` covers the checks W2.5 added by writing *deliberately broken* catalogs and
  contracts into `tmp_path` and asserting the specific error text.
* *Contract test.* The adopted components (heat pump, PV, battery, electricity meter) are
  instantiated from their own default configs, and their declarations are machine-checked: the
  facts build, the asset class resolves in every shipped country database with a matching size
  unit, and doubling the config field marked `metadata={"capacity": True}` doubles `facts.size`.
  That last one is the "uses the correct configuration" property — the class of bug where a
  component declares a plausible but constant size and nobody notices.
* *Round-trip identity.* Serialization is checked by writing, reading back and asserting the
  *evaluation* is unchanged, not just that fields match — plus targeted tests for the two holes
  W1.3/W1.4 named (`existing_heating` silently dropped, in-memory tariff contracts unrestorable)
  and for backward compatibility with files written before either was fixed.
* *Independent recomputation.* The comparison and export tests recompute the payback curve, the
  annuity multiplication and the per-entry discount factors by hand and compare, which is what
  pins the W4.1 claim that exports stopped minting numbers of their own.

**Error class.** Mostly *data*, deliberately: "a wrong number, or a missing one, in a JSON file"
as opposed to "a wrong formula" (cost-spec-v2 §2.2). The contract tests sit one layer up and fail
on *declaration* drift — a component whose config was renamed or whose asset class lost its
entries. The serialization and CLI tests fail on *contract* breakage: the same inputs no longer
price the same way through a file, which is the property the whole "pure function of a JSON file"
story rests on. None of these failures implicate the financial math itself.
"""

# clean

import dataclasses
import os
import types

import pytest

from hisim.economics.carriers import EnergyCarrier
from hisim.economics.database import CostDatabase
from hisim.economics.evaluator import EconomicEvaluator, EvaluationInputs, SubjectCostFacts
from hisim.economics.facts import BillingDeterminants, ComponentCostFacts, CostRelevance, ExistingAsset
from hisim.economics.parameters import EconomicParameters
from hisim.economics.perspectives import InstallationContext, Perspective, SubsidyMode
from hisim.economics.results import compare
from hisim.economics.uncertainty import UncertainValue
from hisim.economics.validation import validate_all, validate_cost_database
from hisim.loadtypes import ComponentType, Units

pytestmark = pytest.mark.base

#: Components adopted in Phase 6 (additive, next to the untouched legacy methods). Each row is
#: (module, component class, config class, default-config factory, the attribute name the
#: component reads its config under) — the five things needed to build a component's declaration
#: without a simulator. The list grows as components adopt `get_cost_facts()`; every entry it
#: gains is automatically covered by all three contract tests below.
ADOPTED_COMPONENTS = [
    ("hisim.components.advanced_heat_pump_hplib", "HeatPumpHplib", "HeatPumpHplibConfig",
     "get_default_generic_advanced_hp_lib", "config"),
    ("hisim.components.generic_pv_system", "PVSystem", "PVSystemConfig", "get_default_pv_system", "config"),
    ("hisim.components.advanced_battery_bslib", "Battery", "BatteryConfig", "get_default_config", "battery_config"),
    ("hisim.components.electricity_meter", "ElectricityMeter", "ElectricityMeterConfig",
     "get_electricity_meter_default_config", "config"),
]


def _facts_from_default_config(module_name, class_name, config_class_name, default_factory, config_attr):
    """Builds a component's cost facts from its own default config, without constructing it.

    `get_cost_facts` reads only the config, so a `SimpleNamespace` carrying that config under both
    the component's own attribute name and the generic `config` name is enough — instantiating the
    real component would drag in simulation parameters, file loading and connections that have
    nothing to do with the declaration under test. Importing by name keeps this file free of
    top-level component imports.

    Returns:
        The component class, the default config, and the `ComponentCostFacts` it declares.
    """
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
        scaled_value = (
            original * 2
            if not hasattr(original, "value")
            else type(original)(original.value * 2, original.unit)
        )
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

        from hisim.economics.database import CostDatabase, CostDataError

        clone = tmp_path / "cost_database"
        shutil.copytree(CostDatabase.DEFAULT_PATH, clone)
        devices_path = clone / "devices_DE.json"
        with open(devices_path, encoding="utf-8") as file:
            data = json.load(file)
        data["entries"][0]["source_ids"] = []
        with open(devices_path, "w", encoding="utf-8") as file:
            json.dump(data, file)
        with pytest.raises(CostDataError):
            CostDatabase(str(clone))


class TestResolvedEntries:
    """W2.1: resolution and provenance recording are one step."""

    def test_resolving_a_device_entry_records_its_provenance(self):
        """The database layer records; the caller only names the fields it prices from."""
        from hisim.economics.database import CostDatabase
        from hisim.economics.provenance import ParameterOrigin, ProvenanceLedger

        database = CostDatabase()
        ledger = ProvenanceLedger()
        resolved = database.resolve_device_entry(
            ComponentType.HEAT_PUMP, 2024, "DE", ledger, ("specific_investment", "service_life_in_years")
        )
        assert len(ledger) == 2
        assert resolved.provenance_ids == (
            resolved.provenance_id("specific_investment"),
            resolved.provenance_id("service_life_in_years"),
        )
        record = ledger.get(resolved.provenance_id("specific_investment"))
        assert record.origin == ParameterOrigin.DATABASE_ENTRY
        assert record.source_ids and record.value == resolved.entry.specific_investment

    def test_unrequested_field_has_no_provenance(self):
        """Reading a field nobody declared is a coding error, not a silent gap."""
        from hisim.economics.database import CostDatabase, CostDataError
        from hisim.economics.provenance import ProvenanceLedger

        database = CostDatabase()
        resolved = database.resolve_device_entry(
            ComponentType.HEAT_PUMP, 2024, "DE", ProvenanceLedger(), ("specific_investment",)
        )
        with pytest.raises(CostDataError, match="no provenance was recorded"):
            resolved.provenance_id("maintenance_rate_per_year")

    def test_resolving_an_energy_price_records_its_provenance(self):
        """Same contract on the energy side."""
        from hisim.economics.database import CostDatabase
        from hisim.economics.provenance import ProvenanceLedger

        database = CostDatabase()
        ledger = ProvenanceLedger()
        resolved = database.resolve_energy_price(
            EnergyCarrier.ELECTRICITY, 2024, "DE", ledger, ("working_price_in_euro_per_kwh",)
        )
        assert len(ledger) == 1
        assert resolved.entry.carrier == EnergyCarrier.ELECTRICITY
        assert ledger.get(resolved.provenance_id("working_price_in_euro_per_kwh")).source_ids

    def test_missing_entry_still_raises_before_anything_is_recorded(self):
        """A failed resolution leaves no half-written provenance behind."""
        from hisim.economics.database import CostDatabase, CostDataError
        from hisim.economics.provenance import ProvenanceLedger

        database = CostDatabase()
        ledger = ProvenanceLedger()
        with pytest.raises(CostDataError):
            database.resolve_device_entry(
                ComponentType.HEAT_PUMP, 1990, "DE", ledger, ("specific_investment",)
            )
        assert len(ledger) == 0

    def test_rehydrated_ledger_still_interns(self):
        """`from_json` restores the interning index, not only the records (§3.10).

        A ledger read back from `cost_provenance.json` must behave like the ledger it was written
        from: recording a record that is already in it returns the existing id and appends
        nothing. Rehydration used to refill the record list alone, so the first `record()` call on
        a rehydrated ledger duplicated an existing datapoint under a new id — which would silently
        renumber the ids that exported cash-flow rows already point at.
        """
        from hisim.economics.provenance import ParameterOrigin, ParameterProvenance, ProvenanceLedger

        original = ProvenanceLedger()
        first = ParameterProvenance(
            parameter="devices_XX.HEAT_PUMP@2024.specific_investment",
            value=1234.0,
            origin=ParameterOrigin.DATABASE_ENTRY,
            data_file="devices_XX.json",
            source_ids=("src_test",),
        )
        second = ParameterProvenance(
            parameter="parameters.interest_rate",
            value=0.03,
            origin=ParameterOrigin.ENGINE_DEFAULT,
        )
        first_id = original.record(first)
        second_id = original.record(second)

        rehydrated = ProvenanceLedger.from_json(original.to_json())
        assert len(rehydrated) == 2
        assert rehydrated.record(second) == second_id
        assert rehydrated.record(first) == first_id
        assert len(rehydrated) == 2


#: A minimal but *valid* subsidy scheme in catalog-file form. The validation tests below break
#: exactly one thing in a copy of it, so a reported error can only come from that one change —
#: which is what turns "an error was reported" into "this specific check fired". Country `XX` is
#: synthetic, so none of these fixtures can collide with a shipped catalog.
SCHEME_TEMPLATE = {
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


class TestEuroPerKilowattHourBasis:
    """D26: native quotes on disk, EUR/kWh at resolution, and a bill that does not move.

    Review finding 11 was that `BillingDeterminants.energy_bought_in_kwh` held tons for pellets
    and liters for oil, so every "EUR/kWh" label on those carriers was wrong. The fix moved the
    heating value from the quantity to the price, which is an identity on the *product* — these
    tests are what pins that it really is one, and that the arithmetic it replaced is still
    legible in the audit trail afterwards.
    """

    @staticmethod
    def _native_entry(carrier):
        """The as-shipped 2026 DE row for a carrier, straight out of the loaded lists.

        Reaches past `get_energy_price` on purpose: that method is the thing under test, so the
        expected value has to come from the unconverted row the JSON file actually holds.
        """
        from hisim.economics.database import CostDatabase

        database = CostDatabase()
        rows = [
            entry
            for entry in database.energy_prices["DE"]
            if entry.carrier == carrier and entry.year == 2026
        ]
        assert len(rows) == 1
        return database, rows[0]

    def test_pellet_bill_is_identical_to_the_old_per_ton_arithmetic(self):
        """kWh x resolved EUR/kWh == tons x native EUR/t, to within 1e-9 relative.

        The numerical claim of D26 in one line. The old path divided a kWh figure by 5000 kWh/t
        and multiplied the result by the EUR/t quote; the new one multiplies the kWh figure by the
        quote divided by 5000 kWh/t. Same two factors, same product — so no published pellet or
        wood-chip bill moves, and a reviewer can check that here rather than by diffing reports.
        """
        database, native = self._native_entry(EnergyCarrier.PELLETS)
        content = database.energy_content_of(native)
        assert content is not None
        assert content.kwh_per_quantity_unit == pytest.approx(5000.0)  # PhysicsConfig PELLETS

        energy_bought_in_kwh = 18_000.0
        resolved = database.get_energy_price(EnergyCarrier.PELLETS, 2026, "DE")
        assert resolved.quantity_unit == "kWh"
        for slot in ("minimum", "average", "maximum"):
            new_bill = energy_bought_in_kwh * getattr(resolved.working_price_in_euro_per_kwh, slot)
            tons = energy_bought_in_kwh / content.kwh_per_quantity_unit
            old_bill = tons * getattr(native.working_price_in_euro_per_kwh, slot)
            assert new_bill == pytest.approx(old_bill, rel=1e-9)

    def test_emission_factor_is_converted_with_the_same_energy_content(self):
        """Emissions are `quantity x factor` too, so the factor moves with the quantity's unit.

        The shipped 2026 pellet row states 175 kg per ton. Left alone while the quantity became
        kWh it would have overstated pellet emissions by a factor of 5000, so the conversion has
        to cover it — and covering it makes the emission product invariant in exactly the same
        way as the money product.
        """
        database, native = self._native_entry(EnergyCarrier.PELLETS)
        resolved = database.get_energy_price(EnergyCarrier.PELLETS, 2026, "DE")
        assert native.emission_factor_in_kg_per_kwh == pytest.approx(175.0)  # per ton, as shipped
        assert resolved.emission_factor_in_kg_per_kwh == pytest.approx(175.0 / 5000.0)
        energy_bought_in_kwh = 18_000.0
        assert energy_bought_in_kwh * resolved.emission_factor_in_kg_per_kwh == pytest.approx(
            energy_bought_in_kwh / 5000.0 * native.emission_factor_in_kg_per_kwh, rel=1e-9
        )

    def test_kwh_quoted_carriers_are_handed_out_unchanged(self):
        """Electricity, gas, district heating and hydrogen have nothing to convert.

        Identity rather than equality: a kWh-quoted row must come back as the very object the
        loader built, so the conversion cannot quietly copy (and one day round) rows it has no
        business touching.
        """
        for carrier in (
            EnergyCarrier.ELECTRICITY,
            EnergyCarrier.NATURAL_GAS,
            EnergyCarrier.DISTRICT_HEATING,
            EnergyCarrier.HYDROGEN,
        ):
            database, native = self._native_entry(carrier)
            assert database.energy_content_of(native) is None
            resolved = database.get_energy_price(carrier, 2026, "DE")
            assert resolved.converted_from is None
            assert resolved is native

    def test_resolved_provenance_detail_shows_the_native_quote_and_the_heating_value(self):
        """The audit trail keeps the number the source published, not only the derived one.

        Without this the ledger would record 0.06 EUR/kWh for pellets, a figure that appears in
        no data file and in no citation. The detail sentence is the bridge back: quote, divisor,
        result and where the divisor came from.
        """
        from hisim.economics.database import CostDatabase
        from hisim.economics.provenance import ProvenanceLedger

        database = CostDatabase()
        ledger = ProvenanceLedger()
        resolved = database.resolve_energy_price(
            EnergyCarrier.PELLETS, 2026, "DE", ledger, ("working_price_in_euro_per_kwh",)
        )
        record = ledger.get(resolved.provenance_id("working_price_in_euro_per_kwh"))
        assert record.value == resolved.entry.working_price_in_euro_per_kwh
        assert record.detail is not None
        assert "300 EUR/t" in record.detail  # the AVERAGE slot of the shipped 2026 band
        assert "5000 kWh/t" in record.detail
        assert "PhysicsConfig PELLETS" in record.detail
        assert "0.06 EUR/kWh" in record.detail

    def test_a_kwh_quoted_carrier_records_no_conversion_detail(self):
        """Nothing was divided, so there is nothing to explain (and no misleading sentence)."""
        from hisim.economics.database import CostDatabase
        from hisim.economics.provenance import ProvenanceLedger

        database = CostDatabase()
        ledger = ProvenanceLedger()
        resolved = database.resolve_energy_price(
            EnergyCarrier.ELECTRICITY, 2026, "DE", ledger, ("working_price_in_euro_per_kwh",)
        )
        assert ledger.get(resolved.provenance_id("working_price_in_euro_per_kwh")).detail is None

    def test_an_unconvertible_quote_fails_instead_of_being_billed_against_kwh(self):
        """A unit with no known heating value is a data error, not a silent pass-through (D7)."""
        import dataclasses as dataclasses_module

        from hisim.economics.database import CostDatabase, CostDataError

        database = CostDatabase()
        _, native = self._native_entry(EnergyCarrier.PELLETS)
        with pytest.raises(CostDataError, match="cannot be converted to EUR/kWh"):
            database.energy_content_of(dataclasses_module.replace(native, quantity_unit="cord"))
        with pytest.raises(CostDataError, match="no lower heating value is known"):
            database.energy_content_of(
                dataclasses_module.replace(native, carrier=EnergyCarrier.HYDROGEN)
            )

    def test_converted_plausibility_ranges_are_the_native_bands_divided_by_the_energy_content(self):
        """Every fuel band in `plausibility_checks.json` is its old EUR/t or EUR/l band, converted.

        The bands moved units in the same commit as the prices did, and a band that did not move
        with them would flag every run of that carrier. This re-derives each shipped bound from
        the band the file used to carry (quoted in the `comment_<CARRIER>` key next to it) and the
        very energy content the pricing path divides by, allowing only the three-to-four
        significant figures the file deliberately rounds each bound to.
        """
        from hisim.economics.database import CostDatabase
        from hisim.economics.plausibility import PlausibilityConfig

        database = CostDatabase()
        config = PlausibilityConfig.load()
        native_bands_before_d26 = {
            EnergyCarrier.HEATING_OIL: (0.40, 2.50),
            EnergyCarrier.DIESEL: (0.80, 3.00),
            EnergyCarrier.PELLETS: (150.0, 700.0),
            EnergyCarrier.WOOD_CHIPS: (50.0, 400.0),
        }
        for carrier, (native_low, native_high) in native_bands_before_d26.items():
            _, native = self._native_entry(carrier)
            content = database.energy_content_of(native)
            assert content is not None
            low, high = config.effective_price_ranges[carrier.value]
            assert low == pytest.approx(native_low / content.kwh_per_quantity_unit, rel=5e-3)
            assert high == pytest.approx(native_high / content.kwh_per_quantity_unit, rel=5e-3)

    def test_the_effective_price_check_judges_a_pellet_bill_in_euro_per_kwh(self):
        """The panel passes a realistic pellet bill and warns on one a thousand times too big.

        The half of finding 11 a reader sees: with the band still in EUR/t, an honest 0.06 EUR/kWh
        pellet price sat three orders of magnitude below `150 - 700` and the check screamed on
        every correct run. Both directions are asserted, because a band widened until nothing
        fails is not a check.
        """
        import types as types_module

        from hisim.economics.database import CostDatabase
        from hisim.economics.plausibility import (  # pylint: disable=protected-access
            PlausibilityConfig,
            _effective_price_findings,
        )
        from hisim.economics.results import AnnualEnergyQuantities
        from hisim.economics.timeline import (
            CashFlowEntry,
            CashFlowTimeline,
            CostCategory,
            SubjectKind,
        )

        database = CostDatabase()
        resolved = database.get_energy_price(EnergyCarrier.PELLETS, 2026, "DE")
        energy_bought_in_kwh = 18_000.0
        bill = energy_bought_in_kwh * resolved.working_price_in_euro_per_kwh.average

        def _reference(year_one_cost: float):
            """A stand-in result carrying only what the effective-price check reads."""
            timeline = CashFlowTimeline(
                entries=[
                    CashFlowEntry(
                        year=1,
                        amount_in_euro=UncertainValue.exact(year_one_cost),
                        category=CostCategory.ENERGY_WORKING,
                        subject=EnergyCarrier.PELLETS.value,
                        subject_kind=SubjectKind.CARRIER,
                    )
                ]
            )
            return types_module.SimpleNamespace(
                annual_energy_quantities_by_carrier={
                    EnergyCarrier.PELLETS.value: AnnualEnergyQuantities(
                        bought_in_kwh=energy_bought_in_kwh
                    )
                },
                scoped_timeline=lambda: timeline,
            )

        config = PlausibilityConfig.load()
        passing = _effective_price_findings(_reference(bill), config)
        assert [finding.status for finding in passing] == ["PASS"]
        assert passing[0].unit == "EUR/kWh"
        assert passing[0].value == pytest.approx(
            resolved.working_price_in_euro_per_kwh.average, rel=1e-12
        )
        warning = _effective_price_findings(_reference(bill * 1000.0), config)
        assert [finding.status for finding in warning] == ["WARN"]


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

    payload = {"catalog_snapshot_date": "2026-01-01", "overall_cap_share": None, "schemes": []}
    for overrides in schemes:
        scheme = copy.deepcopy(SCHEME_TEMPLATE)
        scheme.update(copy.deepcopy(overrides))
        payload["schemes"].append(scheme)
    with open(os.path.join(str(tmp_path), "XX.json"), "w", encoding="utf-8") as file:
        json.dump(payload, file)
    return str(tmp_path)


class TestLoaderErrorsAreLocated:
    """Issue #24: a malformed data row names its file and its entry, whatever the field."""

    @staticmethod
    def _price_file(tmp_path, carrier: str) -> str:
        """Writes a minimal `energy_prices_XX.json` (plus the mandatory registry) in `tmp_path`.

        A whole database directory is the smallest thing `CostDatabase` will load, so the fixture
        ships a one-entry source registry next to a one-entry price file. Everything except the
        carrier name is valid, which is what isolates the failure under test.
        """
        import json
        import shutil

        shutil.copy(
            os.path.join(CostDatabase.DEFAULT_PATH, "sources.json"), os.path.join(tmp_path, "sources.json")
        )
        path = os.path.join(tmp_path, "energy_prices_XX.json")
        with open(path, "w", encoding="utf-8") as file:
            json.dump(
                {
                    "entries": [
                        {
                            "carrier": carrier,
                            "year": 2024,
                            "working_price_in_euro_per_kwh": 0.3,
                            "source_ids": ["src_expert_migration"],
                        }
                    ]
                },
                file,
            )
        return path

    def test_an_unknown_carrier_is_a_located_cost_data_error(self, tmp_path):
        """It used to be a bare ValueError with neither the file nor the row in it."""
        from hisim.economics.database import CostDataError

        self._price_file(tmp_path, "elektrizitaet")
        with pytest.raises(CostDataError) as raised:
            CostDatabase(str(tmp_path))
        message = str(raised.value)
        assert "energy_prices_XX.json" in message  # the file ...
        assert "elektrizitaet" in message  # ... and the offending value
        assert "2024" in message  # ... and the entry it belongs to

    def test_a_valid_carrier_still_loads(self, tmp_path):
        """The counterpart: the located parse accepts exactly what the enum accepts."""
        self._price_file(tmp_path, EnergyCarrier.ELECTRICITY.value)
        database = CostDatabase(str(tmp_path))
        assert database.energy_prices["XX"][0].carrier is EnergyCarrier.ELECTRICITY


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

        from hisim.economics.database import CostDatabase, SourceRegistry
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
        from hisim.economics.database import CostDatabase, SourceRegistry
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
        assert restored.total_npv_in_euro.average == pytest.approx(original.total_npv_in_euro.average)
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
        assert restored_asset is not None
        assert restored_asset.asset_class is original_asset.asset_class
        assert restored_asset.size == pytest.approx(original_asset.size)
        assert restored_asset.size_unit is original_asset.size_unit
        assert restored_asset.installation_year == original_asset.installation_year
        assert restored_asset.is_functional is original_asset.is_functional
        assert restored_asset.energy_carrier is original_asset.energy_carrier
        assert restored_asset.replaced_by_asset_classes == original_asset.replaced_by_asset_classes
        assert restored_asset.replacement_cost_override_in_euro is not None
        assert restored_asset.replacement_cost_override_in_euro.average == pytest.approx(
            original_asset.replacement_cost_override_in_euro.average
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
            controllability_discount=ControllabilityDiscount(kind="GRID_FEE_SHARE", grid_fee_reduction_share=0.4),
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
        assert apply_tariff(determinants, restored).total().average == pytest.approx(
            apply_tariff(determinants, contract).total().average
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
        assert contracts_from_json({"tariff_contract_ids": {"ELECTRICITY": "DE_DEFAULT_ELECTRICITY_2024"}}) == {}

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
        payback = comparison.discounted_payback_years["average"]
        assert payback is not None and 1 <= payback <= 20
        assert comparison.npv_delta_in_euro.average < 0  # the variant wins over 20 years

    def test_payback_curve_matches_an_independent_recomputation(self):
        """W4.4: the curve on the comparison equals a hand-rolled cumulative discounting."""
        reference = self._result(0.0, 20000.0)
        variant = self._result(15000.0, 5000.0)
        comparison = compare(reference, variant)
        interest = variant.parameters.interest_rate
        horizon = variant.parameters.observation_period_in_years
        for slot, attribute in (("low", "minimum"), ("average", "average"), ("high", "maximum")):
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
        saving = comparison.cumulative_discounted_savings_in_euro["average"][-1]
        assert saving == pytest.approx(
            reference.total_npv_in_euro.average - variant.total_npv_in_euro.average, rel=1e-9
        )

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
            assert float(row["npv_avg"]) == pytest.approx(npv.average)
            assert float(row["eac_avg"]) == pytest.approx(npv.average * annuity)
            assert float(row["eac_min"]) == pytest.approx(npv.minimum * annuity)

        write_cash_flow_timeline(matrix, str(tmp_path))
        with open(tmp_path / "cash_flow_timeline.csv", encoding="utf-8") as file:
            flows = list(csv.DictReader(file, delimiter=";"))
        assert len(flows) == len(result.timeline.entries)
        for row, entry in zip(flows, result.timeline.entries):
            assert float(row["discounted_avg"]) == pytest.approx(
                entry.amount_in_euro.average / ((1 + interest) ** entry.year)
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
