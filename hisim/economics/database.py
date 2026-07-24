"""Cost database: versioned data files instead of Python literals (cost_spec.md §3.5, §3.10).

Loads and validates::

    hisim/cost_database/
        devices_<COUNTRY>.json
        energy_prices_<COUNTRY>.json
        co2_price_paths.json
        escalation_defaults_<COUNTRY>.json
        sources.json
        tariffs/*.json          (loaded by hisim.economics.tariffs)

Every data entry must reference at least one source registry entry; an entry without a
resolvable source fails validation (§9.6).
"""

from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from hisim import log
from hisim.economics.carriers import EnergyCarrier
from hisim.economics.provenance import ParameterOrigin, ParameterProvenance, ProvenanceLedger, ResolvedSource
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import ComponentType, Units

#: Default on-disk location of the shipped cost database.
DEFAULT_COST_DATABASE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cost_database")

#: Admissible source kinds (§3.5).
SOURCE_KINDS = (
    "MARKET_SURVEY",
    "STANDARD",
    "STATUTE",
    "MANUFACTURER",
    "LITERATURE",
    "PROJECT_DATA",
    "EXPERT_ESTIMATE",
)

#: Mapping of `per_unit` strings in device entries to size units of ComponentCostFacts.
PER_UNIT_TO_SIZE_UNIT = {
    "kW": Units.KILOWATT,
    "kWh": Units.KWH,
    "liter": Units.LITER,
    "m2": Units.SQUARE_METER,
    None: Units.ANY,
}


class CostDataError(ValueError):
    """Raised when cost data files are missing, malformed or unsourced."""


@dataclass
class SourceEntry:
    """One entry of the structured source registry (§3.5)."""

    source_id: str
    citation: str
    url: Optional[str]
    publication_year: int
    retrieved: str
    kind: str
    notes: Optional[str] = None

    def to_resolved(self) -> ResolvedSource:
        """Converts to the provenance-report representation."""
        return ResolvedSource(
            source_id=self.source_id,
            citation=self.citation,
            url=self.url,
            publication_year=self.publication_year,
            retrieved=self.retrieved,
            kind=self.kind,
            notes=self.notes,
        )


class SourceRegistry:
    """The `sources.json` registry with mandatory-field validation."""

    def __init__(self, entries: Dict[str, SourceEntry], file_name: str) -> None:
        """Constructed by :meth:`load`."""
        self.entries = entries
        self.file_name = file_name
        self._referenced: set = set()

    @classmethod
    def load(cls, path: str) -> "SourceRegistry":
        """Loads and validates a sources.json file."""
        with open(path, encoding="utf-8") as file:
            raw = json.load(file)
        entries: Dict[str, SourceEntry] = {}
        for item in raw.get("sources", []):
            for mandatory in ("id", "citation", "publication_year", "retrieved", "kind"):
                if mandatory not in item or item[mandatory] in (None, ""):
                    if mandatory == "id" or item.get("id") is None:
                        raise CostDataError(f"Source entry without id in {path}: {item!r}")
                    raise CostDataError(f"Source {item['id']!r} in {path} misses mandatory field {mandatory!r}.")
            if item["kind"] not in SOURCE_KINDS:
                raise CostDataError(f"Source {item['id']!r} has unknown kind {item['kind']!r}.")
            if item.get("url") in (None, "") and not item.get("notes"):
                raise CostDataError(f"Source {item['id']!r} has neither url nor notes explaining its absence.")
            entries[item["id"]] = SourceEntry(
                source_id=item["id"],
                citation=item["citation"],
                url=item.get("url"),
                publication_year=int(item["publication_year"]),
                retrieved=item["retrieved"],
                kind=item["kind"],
                notes=item.get("notes"),
            )
        return cls(entries, os.path.basename(path))

    def resolve(self, source_ids: Tuple[str, ...], context: str) -> List[SourceEntry]:
        """Resolves ids, failing on unknown ones (§9.6). Tracks referenced ids for orphan checks."""
        resolved = []
        for source_id in source_ids:
            if source_id not in self.entries:
                raise CostDataError(f"{context}: unknown source id {source_id!r} (not in {self.file_name}).")
            self._referenced.add(source_id)
            resolved.append(self.entries[source_id])
        return resolved

    def orphaned_ids(self) -> List[str]:
        """Registry entries never referenced by any data entry (flagged by CI, §9.6)."""
        return sorted(set(self.entries.keys()) - self._referenced)

    def stale_ids(self, reference_date: Optional[date] = None, max_age_days: int = 365) -> List[str]:
        """Sources whose `retrieved` date is older than the staleness threshold (§9.6)."""
        reference = reference_date or date.today()
        stale = []
        for source_id, entry in self.entries.items():
            try:
                retrieved = datetime.strptime(entry.retrieved, "%Y-%m-%d").date()
            except ValueError as err:
                raise CostDataError(f"Source {source_id!r} has invalid retrieved date {entry.retrieved!r}.") from err
            if (reference - retrieved).days > max_age_days:
                stale.append(source_id)
        return sorted(stale)


def _require_sources(entry: dict, context: str) -> Tuple[str, ...]:
    """Every data entry must carry at least one source id (§3.10)."""
    source_ids = tuple(entry.get("source_ids", ()))
    if not source_ids:
        raise CostDataError(f"{context} has no source_ids — unsourced datapoints are not admissible (§3.10).")
    return source_ids


def _band(entry: dict, key: str, context: str, default: Optional[float] = None) -> UncertainValue:
    """Reads an uncertainty band field; a bare number means exact (§3.9)."""
    if key not in entry or entry[key] is None:
        if default is None:
            raise CostDataError(f"{context} misses mandatory field {key!r}.")
        return UncertainValue.exact(default)
    return UncertainValue.from_json(entry[key], context=f"{context}.{key}")


@dataclass
class DeviceEntry:
    """One device cost entry per (component_type, valid_from_year) (§3.5)."""

    component_type: ComponentType
    valid_from_year: int
    specific_investment: UncertainValue
    per_unit: Optional[str]  # "kW" | "kWh" | "liter" | "m2" | None (absolute per device)
    scaling_exponent: Optional[float]
    fixed_installation_cost_in_euro: UncertainValue
    planning_cost_in_euro: UncertainValue
    removal_cost_in_euro: UncertainValue
    maintenance_rate_per_year: UncertainValue
    fixed_operation_cost_in_euro_per_year: UncertainValue
    service_life_in_years: float
    embodied_co2_value: float
    embodied_co2_per_unit: Optional[str]
    vat_rate: float
    source_ids: Tuple[str, ...]
    field_sources: Dict[str, Tuple[str, ...]] = field(default_factory=dict)
    # Share of the investment that is genuinely energy-related (coupled-cost / Ohnehin-Kosten
    # logic for envelope measures, cost_spec.md Q7): when a replaced element was due for
    # renovation anyway, the non-energy share (1 - this) is credited as ANYWAY_COST_CREDIT.
    # 1.0 (the default and the shipped value everywhere for now) means the whole investment
    # is energy-related and the classic like-for-like credit applies instead.
    energy_related_cost_share: UncertainValue = field(default_factory=lambda: UncertainValue.exact(1.0))
    # Per-asset-class anyway-cost threshold in remaining-life years; envelope measures ship
    # ~5 a, None falls back to EconomicParameters.anyway_threshold_years (2 a).
    anyway_threshold_years_override: Optional[float] = None
    # Phase-1 shim reproducing the legacy flat percentage subsidy; superseded by the
    # subsidy catalog whenever one is active (§10.1 Phase 1).
    legacy_flat_subsidy_share: float = 0.0
    # "AS_LEGACY" marks entries migrated 1:1 from configuration.py whose VAT status is
    # undocumented; the FINANCIAL gross-up is a no-op for them (see cost_module_issues.md).
    price_basis: str = "NET"
    notes: Optional[str] = None
    data_file: str = ""

    @property
    def size_unit(self) -> Units:
        """The size unit this entry prices against."""
        if self.per_unit not in PER_UNIT_TO_SIZE_UNIT:
            raise CostDataError(f"Device entry {self.component_type} has unknown per_unit {self.per_unit!r}.")
        return PER_UNIT_TO_SIZE_UNIT[self.per_unit]

    def investment_for_size(self, size: float) -> UncertainValue:
        """Device cost for a given size, honoring economies of scale (§3.5)."""
        if self.per_unit is None:
            return self.specific_investment
        if self.scaling_exponent is not None:
            return self.specific_investment.scale(size**self.scaling_exponent)
        return self.specific_investment.scale(size)

    def embodied_co2_for_size(self, size: float) -> float:
        """Embodied CO2 for a given size in kg."""
        if self.embodied_co2_per_unit is None:
            return self.embodied_co2_value
        return self.embodied_co2_value * size

    @property
    def entry_key(self) -> str:
        """Dotted provenance key, e.g. 'devices_DE.HeatPump@2024'."""
        stem = os.path.splitext(self.data_file)[0]
        return f"{stem}.{self.component_type.name}@{self.valid_from_year}"


@dataclass
class EnergyPriceEntry:
    """One energy price entry per (carrier, year): a two-part tariff with explicit CO2 (§3.5).

    ``quantity_unit`` documents the native billing quantity ("kWh" default; "liter" for oil and
    diesel, "ton" for pellets and wood chips as migrated from the legacy dicts) — the *_per_kwh
    field names read "per quantity_unit" for those carriers.
    """

    carrier: EnergyCarrier
    year: int
    working_price_in_euro_per_kwh: UncertainValue
    standing_charge_in_euro_per_year: UncertainValue
    grid_exit_fee_in_euro: UncertainValue
    emission_factor_in_kg_per_kwh: float
    co2_price_exposure: float
    tax_and_levy_share: float
    quantity_unit: str
    source_ids: Tuple[str, ...]
    field_sources: Dict[str, Tuple[str, ...]] = field(default_factory=dict)
    notes: Optional[str] = None
    data_file: str = ""

    @property
    def entry_key(self) -> str:
        """Dotted provenance key, e.g. 'energy_prices_DE.NATURAL_GAS@2024'."""
        stem = os.path.splitext(self.data_file)[0]
        return f"{stem}.{self.carrier.value}@{self.year}"


@dataclass
class Co2PricePath:
    """A named CO2-price trajectory for one country (§3.5). Values in EUR per ton CO2."""

    country: str
    name: str
    points: List[Tuple[int, float]]  # sorted (year, price)
    source_ids: Tuple[str, ...] = ()

    def price(self, year: int) -> float:
        """Price for a calendar year: step-interpolated (last defined point <= year), 0 before."""
        price = 0.0
        for point_year, point_price in self.points:
            if point_year <= year:
                price = point_price
            else:
                break
        return price


@dataclass
class EscalationDefaults:
    """Country default escalation rates (§3.2 fallback chain)."""

    country: str
    carrier_rates: Dict[EnergyCarrier, float] = field(default_factory=dict)
    asset_class_rates: Dict[ComponentType, float] = field(default_factory=dict)
    source_ids: Tuple[str, ...] = ()


def _component_type_from_name(name: str, context: str) -> ComponentType:
    """Resolves a ComponentType by enum name or value."""
    for member in ComponentType:
        if name in (member.name, member.value):
            return member
    raise CostDataError(f"{context}: unknown component_type {name!r}.")


class CostDatabase:
    """All cost data files of one directory, loaded and validated."""

    def __init__(self, base_path: Optional[str] = None) -> None:
        """Loads the database from `base_path` (default: the shipped hisim/cost_database)."""
        self.base_path = base_path or DEFAULT_COST_DATABASE_PATH
        sources_path = os.path.join(self.base_path, "sources.json")
        if not os.path.isfile(sources_path):
            raise CostDataError(f"Cost database at {self.base_path} has no sources.json (§3.10).")
        self.sources = SourceRegistry.load(sources_path)
        self.devices: Dict[str, List[DeviceEntry]] = {}  # country -> entries
        self.energy_prices: Dict[str, List[EnergyPriceEntry]] = {}
        self.co2_price_paths: Dict[Tuple[str, str], Co2PricePath] = {}
        self.escalation_defaults: Dict[str, EscalationDefaults] = {}
        #: Provenance records of applied scenario overlays (§4.6); empty for the shipped data.
        self.overlay_records: List[ParameterProvenance] = []
        self._load_all()

    # ------------------------------------------------------------------ loading

    def _load_all(self) -> None:
        for file_name in sorted(os.listdir(self.base_path)):
            path = os.path.join(self.base_path, file_name)
            if not file_name.endswith(".json") or not os.path.isfile(path):
                continue
            if file_name.startswith("devices_"):
                country = file_name[len("devices_"):-len(".json")]
                self.devices[country] = self._load_devices(path, file_name)
            elif file_name.startswith("energy_prices_"):
                country = file_name[len("energy_prices_"):-len(".json")]
                self.energy_prices[country] = self._load_energy_prices(path, file_name)
            elif file_name == "co2_price_paths.json":
                self._load_co2_price_paths(path)
            elif file_name.startswith("escalation_defaults_"):
                country = file_name[len("escalation_defaults_"):-len(".json")]
                self.escalation_defaults[country] = self._load_escalation_defaults(path, country)

    def _load_devices(self, path: str, file_name: str) -> List[DeviceEntry]:
        with open(path, encoding="utf-8") as file:
            raw = json.load(file)
        entries = []
        for item in raw.get("entries", []):
            context = f"{file_name}:{item.get('component_type')}@{item.get('valid_from_year')}"
            component_type = _component_type_from_name(item["component_type"], context)
            source_ids = _require_sources(item, context)
            self.sources.resolve(source_ids, context)
            field_sources = {
                key: tuple(value) for key, value in (item.get("field_sources") or {}).items()
            }
            for ids in field_sources.values():
                self.sources.resolve(ids, context)
            specific_investment_raw = item["specific_investment"]
            entry = DeviceEntry(
                component_type=component_type,
                valid_from_year=int(item["valid_from_year"]),
                specific_investment=UncertainValue.from_json(
                    specific_investment_raw["value"], context=f"{context}.specific_investment"
                ),
                per_unit=specific_investment_raw.get("per_unit"),
                scaling_exponent=item.get("scaling_exponent"),
                fixed_installation_cost_in_euro=_band(item, "fixed_installation_cost_in_euro", context, default=0.0),
                planning_cost_in_euro=_band(item, "planning_cost_in_euro", context, default=0.0),
                removal_cost_in_euro=_band(item, "removal_cost_in_euro", context, default=0.0),
                maintenance_rate_per_year=_band(item, "maintenance_rate_per_year", context, default=0.0),
                fixed_operation_cost_in_euro_per_year=_band(
                    item, "fixed_operation_cost_in_euro_per_year", context, default=0.0
                ),
                service_life_in_years=float(item["service_life_in_years"]),
                embodied_co2_value=float((item.get("embodied_co2") or {}).get("value", 0.0)),
                embodied_co2_per_unit=(item.get("embodied_co2") or {}).get("per_unit"),
                vat_rate=float(item.get("vat_rate", 0.0)),
                source_ids=source_ids,
                field_sources=field_sources,
                energy_related_cost_share=_band(item, "energy_related_cost_share", context, default=1.0),
                anyway_threshold_years_override=(
                    float(item["anyway_threshold_years_override"])
                    if item.get("anyway_threshold_years_override") is not None
                    else None
                ),
                legacy_flat_subsidy_share=float(item.get("legacy_flat_subsidy_share", 0.0)),
                price_basis=item.get("price_basis", "NET"),
                notes=item.get("notes"),
                data_file=file_name,
            )
            if entry.service_life_in_years <= 0:
                raise CostDataError(f"{context}: service_life_in_years must be > 0.")
            if entry.per_unit not in PER_UNIT_TO_SIZE_UNIT:
                raise CostDataError(f"{context}: unknown per_unit {entry.per_unit!r}.")
            share = entry.energy_related_cost_share
            if share.minimum < 0.0 or share.maximum > 1.0:
                raise CostDataError(f"{context}: energy_related_cost_share must lie within [0, 1] in every slot.")
            entries.append(entry)
        return entries

    def _load_energy_prices(self, path: str, file_name: str) -> List[EnergyPriceEntry]:
        with open(path, encoding="utf-8") as file:
            raw = json.load(file)
        entries = []
        for item in raw.get("entries", []):
            context = f"{file_name}:{item.get('carrier')}@{item.get('year')}"
            source_ids = _require_sources(item, context)
            self.sources.resolve(source_ids, context)
            field_sources = {key: tuple(value) for key, value in (item.get("field_sources") or {}).items()}
            for ids in field_sources.values():
                self.sources.resolve(ids, context)
            entries.append(
                EnergyPriceEntry(
                    carrier=EnergyCarrier(item["carrier"]),
                    year=int(item["year"]),
                    working_price_in_euro_per_kwh=_band(item, "working_price_in_euro_per_kwh", context),
                    standing_charge_in_euro_per_year=_band(item, "standing_charge_in_euro_per_year", context, 0.0),
                    grid_exit_fee_in_euro=_band(item, "grid_exit_fee_in_euro", context, 0.0),
                    emission_factor_in_kg_per_kwh=float(item.get("emission_factor_in_kg_per_kwh", 0.0)),
                    co2_price_exposure=float(item.get("co2_price_exposure", 0.0)),
                    tax_and_levy_share=float(item.get("tax_and_levy_share", 0.0)),
                    quantity_unit=item.get("quantity_unit", "kWh"),
                    source_ids=source_ids,
                    field_sources=field_sources,
                    notes=item.get("notes"),
                    data_file=file_name,
                )
            )
        return entries

    def _load_co2_price_paths(self, path: str) -> None:
        with open(path, encoding="utf-8") as file:
            raw = json.load(file)
        shared: Dict[str, List[Tuple[int, float]]] = {}
        for name, segment_points in (raw.get("eu_shared") or {}).items():
            shared[name] = sorted((int(year), float(price)) for year, price in segment_points.items())
        for country, paths in (raw.get("countries") or {}).items():
            for name, definition in paths.items():
                context = f"co2_price_paths.json:{country}/{name}"
                source_ids = _require_sources(definition, context)
                self.sources.resolve(source_ids, context)
                points: List[Tuple[int, float]] = []
                for year, price in (definition.get("points") or {}).items():
                    points.append((int(year), float(price)))
                for include in definition.get("include_eu_shared", []):
                    if include not in shared:
                        raise CostDataError(f"{context}: unknown eu_shared segment {include!r}.")
                    points.extend(shared[include])
                self.co2_price_paths[(country, name)] = Co2PricePath(
                    country=country, name=name, points=sorted(points), source_ids=source_ids
                )

    def _load_escalation_defaults(self, path: str, country: str) -> EscalationDefaults:
        with open(path, encoding="utf-8") as file:
            raw = json.load(file)
        context = f"escalation_defaults_{country}.json"
        source_ids = _require_sources(raw, context)
        self.sources.resolve(source_ids, context)
        carrier_rates = {
            EnergyCarrier(carrier): float(rate) for carrier, rate in (raw.get("carriers") or {}).items()
        }
        asset_class_rates = {
            _component_type_from_name(name, context): float(rate)
            for name, rate in (raw.get("asset_classes") or {}).items()
        }
        return EscalationDefaults(
            country=country,
            carrier_rates=carrier_rates,
            asset_class_rates=asset_class_rates,
            source_ids=source_ids,
        )

    # ------------------------------------------------------------------ lookups

    def get_device_entry(self, component_type: ComponentType, year: int, country: str) -> DeviceEntry:
        """Entry with the greatest valid_from_year <= year; hard error when none exists (§3.5)."""
        if country not in self.devices:
            raise CostDataError(f"No device cost data for country {country!r} in {self.base_path}.")
        candidates = [
            entry
            for entry in self.devices[country]
            if entry.component_type == component_type and entry.valid_from_year <= year
        ]
        if not candidates:
            available = sorted(
                entry.valid_from_year for entry in self.devices[country] if entry.component_type == component_type
            )
            raise CostDataError(
                f"No device entry for {component_type.value!r} in {country} valid at {year} "
                f"(available valid_from years: {available or 'none'})."
            )
        return max(candidates, key=lambda entry: entry.valid_from_year)

    def has_device_entry(self, component_type: ComponentType, country: str) -> bool:
        """Whether any entry exists for the type/country (coverage matrix check, §9.6)."""
        return any(entry.component_type == component_type for entry in self.devices.get(country, []))

    def get_energy_price(self, carrier: EnergyCarrier, year: int, country: str) -> EnergyPriceEntry:
        """Price entry with the greatest year <= requested year; hard error when none exists."""
        if country not in self.energy_prices:
            raise CostDataError(f"No energy price data for country {country!r} in {self.base_path}.")
        candidates = [
            entry for entry in self.energy_prices[country] if entry.carrier == carrier and entry.year <= year
        ]
        if not candidates:
            raise CostDataError(f"No energy price entry for {carrier.value} in {country} valid at {year}.")
        return max(candidates, key=lambda entry: entry.year)

    def has_energy_price(self, carrier: EnergyCarrier, country: str) -> bool:
        """Whether any price entry exists for the carrier/country."""
        return any(entry.carrier == carrier for entry in self.energy_prices.get(country, []))

    def get_co2_price_path(self, country: str, scenario: str) -> Optional[Co2PricePath]:
        """Named CO2-price trajectory; None for scenario == 'none'."""
        if scenario == "none":
            return None
        key = (country, scenario)
        if key not in self.co2_price_paths:
            raise CostDataError(f"No CO2 price path {scenario!r} for country {country!r}.")
        return self.co2_price_paths[key]

    def get_escalation_defaults(self, country: str) -> EscalationDefaults:
        """Country escalation defaults; empty defaults if no file ships for the country."""
        return self.escalation_defaults.get(country, EscalationDefaults(country=country))

    # ------------------------------------------------------------------ provenance helpers

    def provenance_for_device(self, entry: DeviceEntry, ledger: ProvenanceLedger, parameter_field: str) -> int:
        """Records a DATABASE_ENTRY provenance record for one field of a device entry."""
        source_ids = entry.field_sources.get(parameter_field, entry.source_ids)
        value: Any = getattr(entry, parameter_field, None)
        if not isinstance(value, (UncertainValue, float, str)):
            value = str(value)
        return ledger.record(
            ParameterProvenance(
                parameter=f"{entry.entry_key}.{parameter_field}",
                value=value,
                origin=ParameterOrigin.DATABASE_ENTRY,
                data_file=f"{entry.data_file}#{entry.entry_key}",
                source_ids=source_ids,
            )
        )

    def provenance_for_price(self, entry: EnergyPriceEntry, ledger: ProvenanceLedger, parameter_field: str) -> int:
        """Records a DATABASE_ENTRY provenance record for one field of an energy price entry."""
        source_ids = entry.field_sources.get(parameter_field, entry.source_ids)
        value: Any = getattr(entry, parameter_field, None)
        if not isinstance(value, (UncertainValue, float, str)):
            value = str(value)
        return ledger.record(
            ParameterProvenance(
                parameter=f"{entry.entry_key}.{parameter_field}",
                value=value,
                origin=ParameterOrigin.DATABASE_ENTRY,
                data_file=f"{entry.data_file}#{entry.entry_key}",
                source_ids=source_ids,
            )
        )

    # ------------------------------------------------------------------ scenario overlays (§4.6)

    #: EconomicParameters fields that must not be swept (§4.6).
    NON_SWEEPABLE_FIELDS = ("cost_database_path", "subsidy_catalog_path", "country")

    def with_overlays(self, overlays: Dict[str, Any], scenario_id: str) -> "CostDatabase":
        """Returns a copy with individual datapoints overlaid (§4.6).

        Overlay paths are rooted at the data file stem, e.g.
        ``devices_DE.HEAT_PUMP.specific_investment`` (optionally ``@year``-pinned). Unknown
        entries or fields are hard errors. A ``None`` value means "as shipped".
        """
        clone = copy.copy(self)
        clone.devices = {country: [copy.copy(entry) for entry in entries] for country, entries in self.devices.items()}
        clone.energy_prices = {
            country: [copy.copy(entry) for entry in entries] for country, entries in self.energy_prices.items()
        }
        clone.overlay_records = []
        for path, value in overlays.items():
            if value is None:
                continue
            clone._apply_overlay(path, value, scenario_id)
        return clone

    def _apply_overlay(self, path: str, value: Any, scenario_id: str) -> None:
        parts = path.split(".")
        if len(parts) != 3:
            raise CostDataError(
                f"Overlay path {path!r} must have the form <file_stem>.<entry>.<field> (optionally <entry>@year)."
            )
        stem, entry_name, field_name = parts
        year_pin: Optional[int] = None
        if "@" in entry_name:
            entry_name, year_str = entry_name.split("@", 1)
            year_pin = int(year_str)
        if stem.startswith("devices_"):
            country = stem[len("devices_"):]
            component_type = _component_type_from_name(entry_name, f"overlay {path}")
            entries = [
                entry
                for entry in self.devices.get(country, [])
                if entry.component_type == component_type and (year_pin is None or entry.valid_from_year == year_pin)
            ]
            if not entries:
                raise CostDataError(f"Overlay {path!r}: no matching device entry.")
            self._overlay_entries(entries, field_name, value, path, scenario_id)
        elif stem.startswith("energy_prices_"):
            country = stem[len("energy_prices_"):]
            carrier = EnergyCarrier(entry_name)
            entries = [
                entry
                for entry in self.energy_prices.get(country, [])
                if entry.carrier == carrier and (year_pin is None or entry.year == year_pin)
            ]
            if not entries:
                raise CostDataError(f"Overlay {path!r}: no matching energy price entry.")
            self._overlay_entries(entries, field_name, value, path, scenario_id)
        else:
            raise CostDataError(f"Overlay {path!r}: unknown data file stem {stem!r}.")

    def _overlay_entries(self, entries: list, field_name: str, value: Any, path: str, scenario_id: str) -> None:
        band_fields_device = {
            "specific_investment",
            "fixed_installation_cost_in_euro",
            "planning_cost_in_euro",
            "removal_cost_in_euro",
            "maintenance_rate_per_year",
            "fixed_operation_cost_in_euro_per_year",
            "energy_related_cost_share",
        }
        band_fields_price = {
            "working_price_in_euro_per_kwh",
            "standing_charge_in_euro_per_year",
            "grid_exit_fee_in_euro",
        }
        scalar_fields = {
            "service_life_in_years",
            "emission_factor_in_kg_per_kwh",
            "co2_price_exposure",
            "tax_and_levy_share",
            "vat_rate",
            "legacy_flat_subsidy_share",
            "scaling_exponent",
            "anyway_threshold_years_override",
        }
        for entry in entries:
            if not hasattr(entry, field_name):
                raise CostDataError(f"Overlay {path!r}: entry has no field {field_name!r}.")
            if field_name in band_fields_device or field_name in band_fields_price:
                new_value: Any = UncertainValue.from_json(value, context=path)
            elif field_name in scalar_fields:
                new_value = float(value)
                if field_name == "service_life_in_years":
                    log.warning(
                        f"Scenario overlay {path!r} changes a service life — timeline structure is "
                        "rebuilt per scenario for this axis (slower, §4.6)."
                    )
            else:
                raise CostDataError(f"Overlay {path!r}: field {field_name!r} is not overlayable.")
            setattr(entry, field_name, new_value)
        self.overlay_records.append(
            ParameterProvenance(
                parameter=path,
                value=UncertainValue.from_json(value, context=path)
                if not isinstance(value, str)
                else value,
                origin=ParameterOrigin.SCENARIO_OVERLAY,
                source_ids=(f"inline:scenario overlay {scenario_id}",),
                detail=scenario_id,
            )
        )
