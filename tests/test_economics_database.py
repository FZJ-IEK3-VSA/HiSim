"""Cost database and data-file tests (cost_spec.md §3.5, §3.10, §9.6).

Everything here runs against the shipped ``hisim/cost_database`` directory: it loads, every
datapoint carries a source, the lookup rules behave as specified, and a datapoint without a
source is rejected. The schema-wide validators and the subsidy catalog arrive with later
PRs of this stack.

The data layer is three modules (D21): ``catalog_entries`` holds the row types, ``sources`` the
source registry, ``database`` the loader that ties them together. Row types are imported from
their canonical module here; ``TestReExportSurface`` pins the deliberate ``database`` re-exports.
"""

import dataclasses
import json
import os
import shutil

import pytest

from hisim.economics import catalog_entries
from hisim.economics import database as database_module
from hisim.economics import sources
from hisim.economics.carriers import EnergyCarrier
from hisim.economics.catalog_entries import CostDataError
from hisim.economics.database import CostDatabase
from hisim.economics.provenance import ParameterOrigin, ProvenanceLedger
from hisim.economics.sources import SourceRegistry
from hisim import loadtypes as lt
from hisim.components.configuration import PhysicsConfig
from hisim.loadtypes import ComponentType

pytestmark = pytest.mark.base


@pytest.fixture(name="database", scope="module")
def fixture_database() -> CostDatabase:
    """The shipped cost database."""
    return CostDatabase()


def _clone_database(tmp_path) -> str:
    """A writable copy of the shipped database, for negative tests."""
    clone = tmp_path / "cost_database"
    shutil.copytree(CostDatabase.DEFAULT_PATH, clone)
    return str(clone)


class TestShippedDatabaseLoads:
    """§9.6 data-file CI: the shipped files load and are self-consistent."""

    def test_every_shipped_country_has_devices_and_prices(self, database):
        """A country ships either a full set of files or none."""
        assert database.devices, "no device files loaded"
        for country in database.devices:
            assert database.devices[country], f"{country} has no device entries"
            assert database.energy_prices.get(country), f"{country} has no energy price entries"

    def test_every_device_entry_resolves_its_sources(self, database):
        """§3.10: no datapoint without a resolvable source id."""
        for country, entries in database.devices.items():
            for entry in entries:
                assert entry.source_ids, f"{country}/{entry.component_type} has no source ids"
                assert database.sources.resolve(entry.source_ids, entry.entry_key)

    def test_every_price_entry_resolves_its_sources(self, database):
        """Same rule for the energy price entries."""
        for country, entries in database.energy_prices.items():
            for entry in entries:
                assert entry.source_ids, f"{country}/{entry.carrier} has no source ids"
                assert database.sources.resolve(entry.source_ids, entry.entry_key)

    def test_device_bands_are_ordered_and_non_negative(self, database):
        """Cost bands are min <= best_estimate <= max and never negative."""
        for entries in database.devices.values():
            for entry in entries:
                band = entry.specific_investment
                assert band.minimum <= band.best_estimate <= band.maximum
                assert band.minimum >= 0.0
                assert entry.service_life_in_years > 0.0

    def test_entry_keys_are_unique_per_country(self, database):
        """The provenance key identifies exactly one entry."""
        for entries in database.devices.values():
            keys = [entry.entry_key for entry in entries]
            assert len(keys) == len(set(keys))


class TestSourceRegistry:
    """§3.5 structured source registry."""

    def test_shipped_registry_loads_with_mandatory_fields(self):
        """Loading is the validation: mandatory fields and known kinds."""
        registry = SourceRegistry.load(os.path.join(CostDatabase.DEFAULT_PATH, "sources.json"))
        assert registry.entries
        for source_id, entry in registry.entries.items():
            assert entry.source_id == source_id
            assert entry.citation and entry.kind and entry.retrieved
            assert entry.url or entry.notes  # a missing url must be explained

    def test_unknown_source_id_is_rejected(self):
        """An entry may not cite a source the registry does not know."""
        registry = SourceRegistry.load(os.path.join(CostDatabase.DEFAULT_PATH, "sources.json"))
        with pytest.raises(CostDataError, match="unknown source id"):
            registry.resolve(("DEFINITELY_NOT_A_SOURCE",), "test")

    def test_resolving_marks_the_source_as_referenced(self):
        """Orphan detection relies on the resolve() bookkeeping."""
        registry = SourceRegistry.load(os.path.join(CostDatabase.DEFAULT_PATH, "sources.json"))
        some_id = sorted(registry.entries)[0]
        assert some_id in registry.orphaned_ids()
        registry.resolve((some_id,), "test")
        assert some_id not in registry.orphaned_ids()

    def test_source_without_mandatory_field_is_rejected(self, tmp_path):
        """A citation without a retrieval date cannot be checked for staleness."""
        path = tmp_path / "sources.json"
        path.write_text(
            json.dumps({"sources": [{"id": "X", "citation": "c", "publication_year": 2024, "kind": "LITERATURE"}]}),
            encoding="utf-8",
        )
        with pytest.raises(CostDataError, match="retrieved"):
            SourceRegistry.load(str(path))

    def test_source_without_url_needs_a_note(self, tmp_path):
        """A source that cannot be linked must say why."""
        path = tmp_path / "sources.json"
        path.write_text(
            json.dumps(
                {
                    "sources": [
                        {
                            "id": "X",
                            "citation": "c",
                            "publication_year": 2024,
                            "retrieved": "2024-01-01",
                            "kind": "LITERATURE",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(CostDataError, match="url"):
            SourceRegistry.load(str(path))


class TestLookupRules:
    """§3.5 lookup semantics: newest entry valid at the requested year, hard error otherwise."""

    def test_device_lookup_takes_the_newest_valid_entry(self, database):
        """A far-future year resolves to the latest shipped entry, not to an error."""
        entry = database.get_device_entry(ComponentType.HEAT_PUMP, 2100, "DE")
        available = sorted(
            candidate.valid_from_year
            for candidate in database.devices["DE"]
            if candidate.component_type == ComponentType.HEAT_PUMP
        )
        assert entry.valid_from_year == available[-1]

    def test_device_lookup_before_the_first_entry_is_an_error(self, database):
        """Silently extrapolating backwards would invent data."""
        with pytest.raises(CostDataError, match="valid at"):
            database.get_device_entry(ComponentType.HEAT_PUMP, 1900, "DE")

    def test_unknown_country_is_an_error(self, database):
        """No country fallback: missing data must be visible."""
        with pytest.raises(CostDataError, match="country"):
            database.get_device_entry(ComponentType.HEAT_PUMP, 2024, "ZZ")

    def test_has_device_entry_matches_the_lookup(self, database):
        """The coverage predicate agrees with the lookup."""
        assert database.has_device_entry(ComponentType.HEAT_PUMP, "DE")
        assert not database.has_device_entry(ComponentType.HEAT_PUMP, "ZZ")

    def test_energy_price_lookup_takes_the_newest_valid_entry(self, database):
        """Same rule as for devices."""
        entry = database.get_energy_price(EnergyCarrier.ELECTRICITY, 2100, "DE")
        available = sorted(
            candidate.year
            for candidate in database.energy_prices["DE"]
            if candidate.carrier == EnergyCarrier.ELECTRICITY
        )
        assert entry.year == available[-1]
        assert database.has_energy_price(EnergyCarrier.ELECTRICITY, "DE")

    def test_investment_scales_with_size(self, database):
        """Per-unit entries scale linearly unless an economies-of-scale exponent is given."""
        entry = database.get_device_entry(ComponentType.HEAT_PUMP, 2024, "DE")
        if entry.per_unit is None:
            pytest.skip("shipped heat pump entry is priced per device, not per unit")
        single = entry.investment_for_size(1.0).best_estimate
        if entry.scaling_exponent is None:
            assert entry.investment_for_size(10.0).best_estimate == pytest.approx(10.0 * single)
        else:
            assert entry.investment_for_size(10.0).best_estimate == pytest.approx(single * 10.0**entry.scaling_exponent)

    def test_co2_price_path_is_step_interpolated(self, database):
        """A year before the first point costs nothing; later years hold the last point."""
        path = next(iter(database.co2_price_paths.values()), None)
        if path is None:
            pytest.skip("no CO2 price paths shipped")
        first_year, first_price = path.points[0]
        assert path.price(first_year - 1) == 0.0
        assert path.price(first_year) == pytest.approx(first_price)
        assert path.price(path.points[-1][0] + 50) == pytest.approx(path.points[-1][1])

    def test_co2_price_path_none_scenario_is_no_path(self, database):
        """'none' means no CO2 price at all, not a missing-data error."""
        assert database.get_co2_price_path("DE", "none") is None

    def test_escalation_defaults_fall_back_to_empty(self, database):
        """An unknown country yields empty defaults, not an error."""
        defaults = database.get_escalation_defaults("ZZ")
        assert defaults.country == "ZZ"
        assert not defaults.carrier_rates and not defaults.asset_class_rates


class TestProvenanceRecording:
    """§3.10: database lookups can be traced back to their sources."""

    def test_device_provenance_carries_file_and_sources(self, database):
        """The recorded record names the data file and the citing source ids."""
        ledger = ProvenanceLedger()
        entry = database.get_device_entry(ComponentType.HEAT_PUMP, 2024, "DE")
        record_id = database.provenance_for_device(entry, ledger, "specific_investment")
        record = ledger.get(record_id)
        assert record.origin is ParameterOrigin.DATABASE_ENTRY
        assert record.parameter.endswith(".specific_investment")
        assert record.source_ids
        assert record.data_file

    def test_repeated_lookups_share_one_record(self, database):
        """Interning keeps cost_provenance.json small."""
        ledger = ProvenanceLedger()
        entry = database.get_device_entry(ComponentType.HEAT_PUMP, 2024, "DE")
        first = database.provenance_for_device(entry, ledger, "specific_investment")
        second = database.provenance_for_device(entry, ledger, "specific_investment")
        assert first == second
        assert len(ledger) == 1

    def test_price_provenance_is_recorded_too(self, database):
        """Energy prices are traceable on the same terms."""
        ledger = ProvenanceLedger()
        entry = database.get_energy_price(EnergyCarrier.ELECTRICITY, 2024, "DE")
        record = ledger.get(database.provenance_for_price(entry, ledger, "working_price_in_euro_per_kwh"))
        assert record.origin is ParameterOrigin.DATABASE_ENTRY
        assert record.source_ids


class TestRejectedData:
    """§3.10 / §9.6: malformed data must not load."""

    def test_unsourced_datapoint_fails(self, tmp_path):
        """An entry without source_ids cannot enter a calculation (§3.10)."""
        clone = _clone_database(tmp_path)
        devices_path = os.path.join(clone, "devices_DE.json")
        with open(devices_path, encoding="utf-8") as file:
            data = json.load(file)
        data["entries"][0]["source_ids"] = []
        with open(devices_path, "w", encoding="utf-8") as file:
            json.dump(data, file)
        with pytest.raises(CostDataError):
            CostDatabase(clone)

    def test_unknown_source_reference_fails(self, tmp_path):
        """A typo in a source id must not pass silently."""
        clone = _clone_database(tmp_path)
        devices_path = os.path.join(clone, "devices_DE.json")
        with open(devices_path, encoding="utf-8") as file:
            data = json.load(file)
        data["entries"][0]["source_ids"] = ["NO_SUCH_SOURCE"]
        with open(devices_path, "w", encoding="utf-8") as file:
            json.dump(data, file)
        with pytest.raises(CostDataError, match="unknown source id"):
            CostDatabase(clone)

    def test_database_without_sources_file_fails(self, tmp_path):
        """No registry means no traceability, so the database refuses to load."""
        clone = _clone_database(tmp_path)
        os.remove(os.path.join(clone, "sources.json"))
        with pytest.raises(CostDataError, match="sources.json"):
            CostDatabase(clone)

    def test_unknown_component_type_fails(self, tmp_path):
        """Asset classes are enum members, never free-form strings."""
        clone = _clone_database(tmp_path)
        devices_path = os.path.join(clone, "devices_DE.json")
        with open(devices_path, encoding="utf-8") as file:
            data = json.load(file)
        data["entries"][0]["component_type"] = "NotAComponentType"
        with open(devices_path, "w", encoding="utf-8") as file:
            json.dump(data, file)
        with pytest.raises(CostDataError, match="component_type"):
            CostDatabase(clone)


class TestScenarioOverlays:
    """§4.6: individual datapoints can be overlaid without touching the shipped files."""

    def test_overlay_replaces_one_datapoint_only(self, database):
        """The clone changes, the shipped database does not."""
        original = database.get_device_entry(ComponentType.HEAT_PUMP, 2024, "DE").specific_investment.best_estimate
        clone = database.with_overlays(
            {"devices_DE.HEAT_PUMP.specific_investment": original * 2.0}, scenario_id="test"
        )
        assert clone.get_device_entry(ComponentType.HEAT_PUMP, 2024, "DE").specific_investment.best_estimate == pytest.approx(
            original * 2.0
        )
        assert database.get_device_entry(
            ComponentType.HEAT_PUMP, 2024, "DE"
        ).specific_investment.best_estimate == pytest.approx(original)

    def test_overlay_is_recorded_in_provenance(self, database):
        """A scenario overlay is a first-class provenance origin."""
        original = database.get_device_entry(ComponentType.HEAT_PUMP, 2024, "DE").specific_investment.best_estimate
        clone = database.with_overlays(
            {"devices_DE.HEAT_PUMP.specific_investment": original * 2.0}, scenario_id="test"
        )
        assert clone.overlay_records
        assert all(record.origin is ParameterOrigin.SCENARIO_OVERLAY for record in clone.overlay_records)

    def test_none_value_means_as_shipped(self, database):
        """A None overlay is a no-op, so sweeps can leave single axes untouched."""
        clone = database.with_overlays({"devices_DE.HEAT_PUMP.specific_investment": None}, scenario_id="test")
        assert not clone.overlay_records

    def test_unknown_overlay_path_is_an_error(self, database):
        """Typos in an overlay path must not silently do nothing."""
        with pytest.raises(CostDataError):
            database.with_overlays({"devices_DE.NOT_AN_ASSET.specific_investment": 1.0}, scenario_id="test")


class TestReExportSurface:
    """D21: the three-module data layer keeps one public import surface.

    ``catalog_entries`` and ``sources`` are the canonical homes of the row types and the source
    registry, but ``database`` re-exports them deliberately so that existing importers, the CLI
    and downstream code can keep saying ``from hisim.economics.database import ...``.
    """

    #: The row types and the registry that `database` re-exports from `catalog_entries` /
    #: `sources`. Held as a literal rather than derived from `__all__`, so that the assertions
    #: below can compare the two and notice drift in *either* direction.
    RE_EXPORTED = (
        "Co2PricePath",
        "CostDataError",
        "DeviceEntry",
        "EnergyPriceEntry",
        "EscalationDefaults",
        "ResolvedDeviceEntry",
        "ResolvedPriceEntry",
        "SourceEntry",
        "SourceRegistry",
    )

    def test_row_types_stay_importable_from_database(self):
        """Every name in the split-out modules is reachable through `database`."""
        for name in self.RE_EXPORTED:
            assert hasattr(database_module, name), f"database.py no longer re-exports {name}"
            assert name in database_module.__all__, f"{name} is re-exported but missing from __all__"

    def test_the_public_surface_gains_nothing_unnoticed(self):
        """`__all__` is exactly the re-exports plus `CostDatabase` — its own class.

        The reverse of the check above. Without it a name *added* to `__all__` would silently join
        the public surface of the data layer, which is the compatibility promise D21 rests on, and
        no test would have an opinion about it.
        """
        assert set(database_module.__all__) == set(self.RE_EXPORTED) | {"CostDatabase"}

    def test_re_exports_are_the_canonical_objects(self):
        """The re-export is an alias, not a second definition that could drift."""
        assert database_module.CostDataError is CostDataError
        assert database_module.SourceRegistry is SourceRegistry
        assert database_module.DeviceEntry is catalog_entries.DeviceEntry
        assert database_module.SourceEntry is sources.SourceEntry

    def test_per_unit_size_units_are_class_scoped_on_the_row_type(self):
        """D22: the per-unit mapping lives on `DeviceEntry`, not as a module constant."""
        assert catalog_entries.DeviceEntry.PER_UNIT_TO_SIZE_UNIT
        assert not hasattr(catalog_entries, "PER_UNIT_TO_SIZE_UNIT")
        assert not hasattr(database_module, "PER_UNIT_TO_SIZE_UNIT")

    def test_source_kinds_are_class_scoped_on_the_registry(self):
        """D22, same rule for the source-kind vocabulary."""
        assert sources.SourceRegistry.SOURCE_KINDS
        assert not hasattr(sources, "SOURCE_KINDS")
        assert not hasattr(database_module, "DEFAULT_COST_DATABASE_PATH")


class TestShippedFilesQuoteKwh:
    """PR-2 review: the shipped price files carry true EUR/kWh values throughout.

    The `*_per_kwh` field names used to hold per-liter and per-ton quotes for solid and liquid
    fuels (`quantity_unit` said so, but the key name did not), which the review flagged as
    confusing. The shipped rows are now converted at authoring time; the conversion machinery in
    `get_energy_price` remains only for user-supplied files. This pin keeps a future data PR from
    quietly reintroducing a native quote under a per-kWh name.
    """

    def test_every_shipped_price_row_is_quoted_per_kwh(self, database):
        """Every entry of every shipped country file has quantity_unit kWh."""
        for country, entries in database.energy_prices.items():
            for entry in entries:
                assert entry.quantity_unit == "kWh", (
                    f"{country} {entry.carrier.value}@{entry.year} is quoted per "
                    f"{entry.quantity_unit}; shipped files must quote per kWh, with the "
                    f"as-published native quote recorded in the row's notes"
                )

    def test_converted_rows_document_their_native_quote(self, database):
        """A row whose source published per liter/ton says so in its notes.

        Checked via the note vocabulary the conversion pass wrote ("Converted to kWh basis"),
        for the carriers that are quoted natively in the literature. This is what preserves
        reviewability against the cited source now that the value itself is per kWh.
        """
        natively_quoted = {EnergyCarrier.HEATING_OIL, EnergyCarrier.DIESEL,
                          EnergyCarrier.PELLETS, EnergyCarrier.WOOD_CHIPS}
        for country, entries in database.energy_prices.items():
            for entry in entries:
                if entry.carrier in natively_quoted:
                    assert entry.notes and "Converted to kWh basis" in entry.notes, (
                        f"{country} {entry.carrier.value}@{entry.year} lacks the conversion "
                        f"note recording its as-published native quote"
                    )


class TestSpotSeriesProvenance:
    """The shipped synthetic spot CSV is the documented formula's output, and nothing else.

    PR-2 review asked where the series comes from: it is the closed-form daily sine of
    `tariffs.synthetic_reference_spot_series` (spec Q16 fallback — real EPEX series cannot be
    redistributed), authored as a fixture and rounded to six decimals. The formula is deliberately
    *re-stated here* instead of imported (the tariff engine ships with a later PR of this stack,
    and an independent restatement is the stronger provenance check anyway): if the CSV drifts
    from the formula, or the formula drifts from this pin, the file's claimed origin is false and
    this fails.
    """

    def test_csv_equals_the_generator_formula(self):
        """Row-by-row equality with the documented sine, to the file's 6-decimal rounding."""
        import csv
        import math

        path = os.path.join(CostDatabase.DEFAULT_PATH, "spot_series", "synthetic_reference_hourly.csv")
        with open(path, encoding="utf-8") as handle:
            reader = csv.reader(handle)
            header = next(reader)
            assert header == ["price_in_euro_per_kwh"]
            rows = [float(row[0]) for row in reader if row]
        assert len(rows) == 8760
        mean_price, amplitude = 0.08, 0.04
        for hour, stored in enumerate(rows):
            daily = math.sin((hour % 24 - 4) / 24.0 * 2.0 * math.pi)
            seasonal = 0.2 * math.cos(hour / 8760.0 * 2.0 * math.pi)
            expected = max(0.0, mean_price + amplitude * (daily + seasonal))
            assert stored == pytest.approx(expected, abs=5e-7), f"hour {hour}"


class TestTheWoodFuelEmissionFactors:
    """hisim-l07.5: the legacy wood-fuel factors are per kWh and stay undivided.

    The legacy dicts priced pellets and wood chips per ton but stated their emission
    factors per kWh; the migration divided both columns by the heating value, so the
    engine multiplied tons of fuel by a per-kWh factor and understated the CO2 of these
    two carriers by a factor of a few thousand. These tests pin the restored figures
    against hand arithmetic, per the issue's done-when.
    """

    #: Lower heating values, from the same PhysicsConfig rows the migration notes name: pellets 11.7 GJ/m3 at
    #: 650 kg/m3, wood chips 15.6 GJ per tonne of fresh mass at 15 % water content (UBA factsheet 2024, table 1).
    KWH_PER_TON = {"PELLETS": 5000.0, "WOOD_CHIPS": 4333.3}

    #: The as-published per-kWh factors, as the legacy dict stated them.
    AS_PUBLISHED = {("DE", "PELLETS"): 0.036, ("DE", "WOOD_CHIPS"): 0.0313,
                    ("AT", "PELLETS"): 0.026, ("AT", "WOOD_CHIPS"): 0.019}

    #: The lookup year each series is valid at: DE 2024, AT 2025, IE has AI estimates only.
    LOOKUP_YEARS = {"DE": 2024, "AT": 2025}

    @pytest.mark.parametrize("country, carrier", sorted(AS_PUBLISHED))
    def test_the_factor_is_the_as_published_per_kwh_figure(self, database, country, carrier):
        """The shipped factor is the legacy dict's own number, in its own unit."""
        price = database.get_energy_price(EnergyCarrier[carrier], self.LOOKUP_YEARS[country], country)

        assert price.emission_factor_in_kg_per_kwh == pytest.approx(self.AS_PUBLISHED[(country, carrier)])

    @pytest.mark.parametrize("country, carrier", sorted(AS_PUBLISHED))
    def test_burning_one_ton_emits_the_hand_figure(self, database, country, carrier):
        """A hand figure in tons: factor × heating value is the CO2 of one burned ton.

        The engine's side takes its kWh per ton from `CostDatabase.energy_content_of`, the PhysicsConfig
        heating value a per-ton quote is divided by, so a regressed heating value fails here against the hand
        kWh per ton (4333.3 is rounded, hence the tolerance).
        """
        price = database.get_energy_price(EnergyCarrier[carrier], self.LOOKUP_YEARS[country], country)
        content = database.energy_content_of(dataclasses.replace(price, quantity_unit="ton"))
        assert content is not None
        hand_figure = self.AS_PUBLISHED[(country, carrier)] * self.KWH_PER_TON[carrier]

        engine_figure = price.emission_factor_in_kg_per_kwh * content.kwh_per_quantity_unit
        assert engine_figure == pytest.approx(hand_figure, rel=1e-4)

    def test_one_ton_of_pellets_is_about_180_kg_of_co2(self, database):
        """The issue's own hand figure, cross-checked against the AI estimate's 175 kg/t."""
        price = database.get_energy_price(EnergyCarrier.PELLETS, 2024, "DE")

        assert price.emission_factor_in_kg_per_kwh * self.KWH_PER_TON["PELLETS"] == pytest.approx(180.0, rel=0.01)

    def test_the_wood_chip_heating_value_is_read_per_tonne(self):
        """The UBA factsheet's 15.6 GJ is per tonne of fresh mass, so a kilogram of chips holds 15.6 MJ (hisim-l07.15).

        Until hisim-l07.15 the figure was stored as GJ per bulk cubic metre, which made a kilogram hold
        62.4 MJ and every wood-chip price per kWh four times too small.
        """
        physics = PhysicsConfig.get_properties_for_energy_carrier(lt.LoadTypes.WOOD_CHIPS)

        assert physics.lower_heating_value_in_joule_per_kg == pytest.approx(15.6e6)
        assert physics.lower_heating_value_in_joule_per_kg * 1000.0 / 3.6e6 == pytest.approx(
            self.KWH_PER_TON["WOOD_CHIPS"], rel=1e-4
        )

    def test_no_shipped_factor_sits_in_the_bug_band(self, database):
        """The bug's signature was a non-zero factor near 1e-6; nothing ships with one.

        Real combustion factors live at or above ~0.005 kg/kWh (the smallest shipped
        figure is wood chips'); the zero rows are deliberate 2050 no-emission assumptions.
        """
        for country, entries in database.energy_prices.items():
            for entry in entries:
                factor = entry.emission_factor_in_kg_per_kwh
                if factor == 0.0:
                    continue
                assert factor >= 0.004, (country, entry.carrier.value if hasattr(entry.carrier, "value") else entry.carrier, entry.year, factor)
