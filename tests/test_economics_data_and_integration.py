"""Cost-fact contract, shipped data files, and database resolution (cost_spec.md §3.4-§3.5, §9).

First of the three data/integration test files (split per the PR-3 review's 500-line rule):
the cost-fact contract of the adopted components, shipped data-file sanity, resolved device
and price entries with their provenance, the uniform EUR/kWh basis (D26 — shipped values are
the as-published quotes over the PhysicsConfig heating values), and located loader errors.
Spot-series parsing, validation gaps and serialization are in
`test_economics_data_serialization.py`; the CLI and the parity harness are in
`test_economics_cli_and_parity.py`.
"""

import dataclasses
import os
import types
import pytest
from hisim.economics.carriers import EnergyCarrier
from hisim.economics.database import CostDatabase
from hisim.economics.facts import ComponentCostFacts, CostRelevance
from hisim.economics.uncertainty import UncertainValue
from hisim.economics.validation import validate_all, validate_cost_database
from hisim.loadtypes import ComponentType

pytestmark = pytest.mark.base

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


class TestEuroPerKilowattHourBasis:
    """D26: EUR/kWh everywhere, and a bill that does not move.

    Review finding 11 was that `BillingDeterminants.energy_bought_in_kwh` held tons for pellets
    and liters for oil, so every "EUR/kWh" label on those carriers was wrong. The fix moved the
    heating value from the quantity to the price, which is an identity on the *product*. The
    PR-2 review then moved the conversion from resolution time into the data files themselves
    (the `*_per_kwh` key holding a per-liter number was judged too confusing), so the shipped
    rows now carry true EUR/kWh and the as-published quote lives in each row's notes; the
    resolution-time conversion machinery remains for user-supplied native quotes. These tests
    pin both: the shipped values are the documented as-published quotes over the PhysicsConfig
    heating values, and the conversion path still works and still explains itself in the audit
    trail.
    """

    #: The as-published 2026 DE pellet quote (recorded in the shipped row's notes) and the
    #: PhysicsConfig heating value it was divided by.
    PELLET_QUOTE_2026_IN_EUR_PER_TON = {"minimum": 250.0, "best_estimate": 300.0, "maximum": 380.0}
    PELLET_EMISSION_2026_IN_KG_PER_TON = 175.0
    PELLET_KWH_PER_TON = 5000.0

    @staticmethod
    def _shipped_entry(carrier):
        """The as-shipped 2026 DE row for a carrier, straight out of the loaded lists."""
        from hisim.economics.database import CostDatabase

        database = CostDatabase()
        rows = [
            entry
            for entry in database.energy_prices["DE"]
            if entry.carrier == carrier and entry.year == 2026
        ]
        assert len(rows) == 1
        return database, rows[0]

    @classmethod
    def _native_pellet_variant(cls, shipped):
        """The shipped 2026 pellet row re-expressed as its as-published per-ton quote.

        This reconstructs the row the file carried before the PR-2 conversion, so the
        resolution-time conversion path — kept for user-supplied files — can be exercised
        against real shipped numbers.
        """
        import dataclasses as dataclasses_module

        from hisim.economics.uncertainty import UncertainValue

        quote = cls.PELLET_QUOTE_2026_IN_EUR_PER_TON
        return dataclasses_module.replace(
            shipped,
            working_price_in_euro_per_kwh=UncertainValue(
                best_estimate=quote["best_estimate"], minimum=quote["minimum"], maximum=quote["maximum"]
            ),
            emission_factor_in_kg_per_kwh=cls.PELLET_EMISSION_2026_IN_KG_PER_TON,
            quantity_unit="ton",
        )

    def test_shipped_pellet_price_is_the_published_quote_over_the_heating_value(self):
        """The stored EUR/kWh value is exactly the notes' EUR/t quote divided by 5000 kWh/t.

        The numerical claim of the PR-2 conversion in one line: nothing was re-estimated, the
        as-published number was divided by the same PhysicsConfig heating value the engine used
        to divide by at resolution — so no pellet bill moved.
        """
        database, shipped = self._shipped_entry(EnergyCarrier.PELLETS)
        assert shipped.quantity_unit == "kWh"
        for slot, quote in self.PELLET_QUOTE_2026_IN_EUR_PER_TON.items():
            stored = getattr(shipped.working_price_in_euro_per_kwh, slot)
            assert stored == pytest.approx(quote / self.PELLET_KWH_PER_TON, rel=1e-9)
        assert shipped.emission_factor_in_kg_per_kwh == pytest.approx(
            self.PELLET_EMISSION_2026_IN_KG_PER_TON / self.PELLET_KWH_PER_TON, rel=1e-9
        )
        resolved = database.get_energy_price(EnergyCarrier.PELLETS, 2026, "DE")
        assert resolved is shipped  # already kWh: handed out unconverted, the very object

    def test_native_quote_conversion_reproduces_the_shipped_values(self):
        """A user-supplied per-ton row converts to exactly what the shipped file stores.

        Round trip of the D26 identity: kWh x converted EUR/kWh == tons x native EUR/t, checked
        by driving the kept conversion machinery with the reconstructed as-published row.
        """
        database, shipped = self._shipped_entry(EnergyCarrier.PELLETS)
        native = self._native_pellet_variant(shipped)
        content = database.energy_content_of(native)
        assert content is not None
        assert content.kwh_per_quantity_unit == pytest.approx(self.PELLET_KWH_PER_TON)

        converted = native.in_euro_per_kwh(content)
        energy_bought_in_kwh = 18_000.0
        for slot in ("minimum", "best_estimate", "maximum"):
            new_bill = energy_bought_in_kwh * getattr(converted.working_price_in_euro_per_kwh, slot)
            tons = energy_bought_in_kwh / content.kwh_per_quantity_unit
            old_bill = tons * getattr(native.working_price_in_euro_per_kwh, slot)
            assert new_bill == pytest.approx(old_bill, rel=1e-9)
            assert getattr(converted.working_price_in_euro_per_kwh, slot) == pytest.approx(
                getattr(shipped.working_price_in_euro_per_kwh, slot), rel=1e-9
            )
        assert converted.emission_factor_in_kg_per_kwh == pytest.approx(
            shipped.emission_factor_in_kg_per_kwh, rel=1e-9
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
            database, shipped = self._shipped_entry(carrier)
            assert database.energy_content_of(shipped) is None
            resolved = database.get_energy_price(carrier, 2026, "DE")
            assert resolved.converted_from is None
            assert resolved is shipped

    def test_resolved_provenance_detail_shows_the_native_quote_and_the_heating_value(self):
        """The audit trail keeps the number a native-quoting source published, not only the derived one.

        The shipped files quote kWh (their as-published numbers live in the notes), but a
        user-supplied file may still quote per ton, and for such a row the ledger must not record
        a bare 0.06 EUR/kWh — a figure that would appear in no data file and no citation. The
        detail sentence is the bridge back: quote, divisor, result and where the divisor came
        from. Exercised by swapping the as-published per-ton pellet row back into the loaded
        list before resolving.
        """
        from hisim.economics.provenance import ProvenanceLedger

        database, shipped = self._shipped_entry(EnergyCarrier.PELLETS)
        rows = database.energy_prices["DE"]
        rows[rows.index(shipped)] = self._native_pellet_variant(shipped)
        ledger = ProvenanceLedger()
        resolved = database.resolve_energy_price(
            EnergyCarrier.PELLETS, 2026, "DE", ledger, ("working_price_in_euro_per_kwh",)
        )
        record = ledger.get(resolved.provenance_id("working_price_in_euro_per_kwh"))
        assert record.value == resolved.entry.working_price_in_euro_per_kwh
        assert record.detail is not None
        assert "300 EUR/t" in record.detail  # the BEST_ESTIMATE slot of the as-published band
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

        from hisim.economics.database import CostDataError

        database, shipped = self._shipped_entry(EnergyCarrier.PELLETS)
        native = self._native_pellet_variant(shipped)
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
        import dataclasses as dataclasses_module

        from hisim.economics.database import CostDatabase
        from hisim.economics.plausibility import PlausibilityConfig

        database = CostDatabase()
        config = PlausibilityConfig.load()
        native_bands_before_d26 = {
            EnergyCarrier.HEATING_OIL: (0.40, 2.50, "liter"),
            EnergyCarrier.DIESEL: (0.80, 3.00, "liter"),
            EnergyCarrier.PELLETS: (150.0, 700.0, "ton"),
            EnergyCarrier.WOOD_CHIPS: (50.0, 400.0, "ton"),
        }
        for carrier, (native_low, native_high, native_unit) in native_bands_before_d26.items():
            _, shipped = self._shipped_entry(carrier)
            # The shipped row is kWh-quoted now; re-stamp its historical native unit only to
            # look up the same heating value the band conversion divided by.
            content = database.energy_content_of(
                dataclasses_module.replace(shipped, quantity_unit=native_unit)
            )
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
        from hisim.economics.plausibility import (
    # pylint: disable=protected-access
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
        bill = energy_bought_in_kwh * resolved.working_price_in_euro_per_kwh.best_estimate

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
            resolved.working_price_in_euro_per_kwh.best_estimate, rel=1e-12
        )
        warning = _effective_price_findings(_reference(bill * 1000.0), config)
        assert [finding.status for finding in warning] == ["WARN"]


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


