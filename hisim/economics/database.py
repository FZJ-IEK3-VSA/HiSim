"""Cost database: loads and validates the versioned cost data files (cost_spec.md §3.5, §3.10).

Reads ``devices_<COUNTRY>.json``, ``energy_prices_<COUNTRY>.json``, ``co2_price_paths.json``,
``escalation_defaults_<COUNTRY>.json`` and ``sources.json`` from ``hisim/cost_database/``. Every entry must cite at
least one source in ``sources.json`` (§9.6). A lookup for year Y returns the entry with the greatest `valid_from_year`
(devices) or `year` (prices) not exceeding Y, so repricing means adding a row, never editing one. Bad data raises
`CostDataError`. Tariffs and the subsidy catalog have their own modules.
"""

from __future__ import annotations

import copy
import json
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

from hisim import log
from hisim.economics.carriers import EnergyCarrier
from hisim.economics.catalog_entries import (
    Co2PricePath,
    CostDataError,
    DeviceEntry,
    EnergyContent,
    EnergyPriceEntry,
    EscalationDefaults,
    ResolvedDeviceEntry,
    ResolvedPriceEntry,
    _band,
    _require_sources,
)
from hisim.economics.provenance import ParameterOrigin, ParameterProvenance, ProvenanceLedger
from hisim.economics.sources import SourceEntry, SourceRegistry
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import ComponentType

#: The row types and the source registry live in `catalog_entries` / `sources`; they stay importable from here so
#: the data layer keeps one public import surface.
__all__ = [
    "Co2PricePath",
    "CostDatabase",
    "CostDataError",
    "DeviceEntry",
    "EnergyPriceEntry",
    "EscalationDefaults",
    "ResolvedDeviceEntry",
    "ResolvedPriceEntry",
    "SourceEntry",
    "SourceRegistry",
]


def _component_type_from_name(name: str, context: str) -> ComponentType:
    """Resolve a `ComponentType` from its enum name or value.

    Example: both ``"HEAT_PUMP"`` and the enum's value resolve to `ComponentType.HEAT_PUMP`.

    Raises:
        CostDataError: On an unknown name, naming the offending row (`context`).
    """
    for member in ComponentType:
        if name in (member.name, member.value):
            return member
    raise CostDataError(f"{context}: unknown component_type {name!r}.")


def _energy_carrier_from_name(name: Any, context: str) -> EnergyCarrier:
    """Resolve an `EnergyCarrier` from its enum value or member name.

    Args:
        name: The `carrier` field as it stands in the file; a non-string fails too.
        context: The `file:carrier@year` location string, quoted in the error.

    Returns:
        The matching carrier.

    Raises:
        CostDataError: On an unknown or non-string carrier, naming the location and the value.
    """
    for member in EnergyCarrier:
        if name in (member.name, member.value):
            return member
    known = ", ".join(member.value for member in EnergyCarrier)
    raise CostDataError(f"{context}: unknown carrier {name!r} (known carriers: {known}).")


class CostDatabase:
    """All cost data files of one directory, loaded and validated.

    One instance is one dataset. Countries are discovered from file names, so adding a country is a data change.
    Everything is parsed and validated in `__init__`, so a malformed or unsourced datapoint fails before any simulation
    starts (§9.6). The engine treats an instance as immutable; `with_overlays` returns a modified copy for scenario
    sweeps (§4.6).
    """

    #: Default on-disk location of the shipped cost database.
    DEFAULT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cost_database")

    def __init__(self, base_path: Optional[str] = None) -> None:
        """Load the database from `base_path` (default: the shipped ``hisim/cost_database``).

        ``sources.json`` is mandatory, since every entry's `source_ids` are validated against it.

        Raises:
            CostDataError: If the directory has no ``sources.json``, or any file in it is malformed, cites an unknown
                or no source, or violates a field constraint (non-positive service life, unknown `per_unit`, a cost
                share outside [0, 1]).
        """
        self.base_path = base_path or CostDatabase.DEFAULT_PATH
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
        """Load every recognized data file of the directory, chosen by file-name prefix.

        The country of a device or price file is its file-name suffix. Unrecognized ``.json`` files are ignored. Files
        are read in sorted order, so the entry order is reproducible.
        """
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
        """Parse one ``devices_<COUNTRY>.json`` into validated `DeviceEntry` rows (§3.5).

        Each row must cite resolvable sources (§3.10). Monetary fields may be a bare number (an exact band) or a band
        (minimum, best estimate, maximum). Three constraints are enforced: service life must be positive, `per_unit`
        must be a known unit, and `energy_related_cost_share` must lie in [0, 1]. Optional fields default to zero cost,
        share 1.0 and no override.

        Args:
            path: Absolute path of the file.
            file_name: Its base name, stored on every entry as `data_file` for provenance.

        Returns:
            The entries in file order; several rows per `component_type` are normal and are selected by
                `valid_from_year`.

        Raises:
            CostDataError: On an unknown component type, a missing or unresolvable source, a malformed band, or a
                violated constraint.
        """
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
            if entry.per_unit not in DeviceEntry.PER_UNIT_TO_SIZE_UNIT:
                raise CostDataError(f"{context}: unknown per_unit {entry.per_unit!r}.")
            share = entry.energy_related_cost_share
            if share.minimum < 0.0 or share.maximum > 1.0:
                raise CostDataError(f"{context}: energy_related_cost_share must lie within [0, 1] in every slot.")
            entries.append(entry)
        return entries

    def _load_energy_prices(self, path: str, file_name: str) -> List[EnergyPriceEntry]:
        """Parse one ``energy_prices_<COUNTRY>.json`` into `EnergyPriceEntry` rows (§3.5).

        Each row holds a working price and a standing charge, the emission factor and `co2_price_exposure` for the CO2
        accounting (§3.8), `tax_and_levy_share` for the macroeconomic view (§4.5), and `quantity_unit`, since a price
        may be quoted per liter or per ton. The rule that a working price excludes the explicit carbon price is not
        checked here.

        Args:
            path: Absolute path of the file.
            file_name: Its base name, stored on every entry for provenance.

        Returns:
            The entries in file order; the `(carrier, year)` lookup selects between them.

        Raises:
            CostDataError: On an unknown carrier, a missing or unresolvable source, or a malformed band.
        """
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
                    carrier=_energy_carrier_from_name(item.get("carrier"), context),
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
        """Load the named carbon-price paths into `self.co2_price_paths`, keyed by `(country, name)` (§3.4).

        A path is a list of (year, EUR/t) points per country and scenario name (`low`, `central`, `high`); downstream,
        the last point at or before a year applies. Segments shared by all EU states live once under `eu_shared` and
        are pulled in by `include_eu_shared`. Points are sorted after merging.

        Raises:
            CostDataError: On a missing or unresolvable source, or an `include_eu_shared` reference to an unknown
                segment.
        """
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
        """Return the device entry with the greatest `valid_from_year` <= year (§3.5).

        This raw lookup records no provenance, so it is for code that inspects data (checks, audit tables); pricing
        code calls :meth:`resolve_device_entry`. A year before the earliest row is rejected, not extrapolated.

        Args:
            component_type: The asset class to price.
            year: The price basis year (§3.5), not necessarily the simulated year.
            country: Country code, the ``devices_<COUNTRY>.json`` suffix.

        Returns:
            The single applicable entry.

        Raises:
            CostDataError: If the country has no device file, or no entry of that type is valid in that year; the
                message lists the available years.
        """
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
        """Return whether any entry exists for the type and country, in any year (coverage check, §9.6)."""
        return any(entry.component_type == component_type for entry in self.devices.get(country, []))

    def earliest_device_year(self, country: str) -> Optional[int]:
        """Return the earliest `valid_from_year` of any device entry of the country, or None without data.

        The price-basis-year policy uses it to find a covered year when the simulation year predates the data.
        """
        years = [entry.valid_from_year for entry in self.devices.get(country, [])]
        return min(years) if years else None

    #: kWh per liter of diesel. Diesel has no `PhysicsConfig` row, so the conversion uses the same 9.8 kWh/l that
    #: `components/generic_car.py` uses to turn simulated liters into kWh; pricing must agree with the model.
    DIESEL_ENERGY_CONTENT_IN_KWH_PER_LITER = 9.8

    #: Entry fields quoted per native billing quantity and therefore divided by the energy content; everything else
    #: on the row is per year, one-off or dimensionless.
    CONVERTED_PRICE_FIELDS = ("working_price_in_euro_per_kwh", "emission_factor_in_kg_per_kwh")

    def energy_content_of(self, entry: EnergyPriceEntry) -> Optional[EnergyContent]:
        """Return the energy content of one native billing unit of a fuel-quoted row, or None if it is already per kWh.

        Heating values come from `components.configuration.PhysicsConfig`, the lower heating values the boilers and
        meters use, so a bill matches the physics that produced its kWh. Diesel per liter uses
        `DIESEL_ENERGY_CONTENT_IN_KWH_PER_LITER`. The import happens at call time so that `hisim.economics` stays
        importable without `hisim.components`.

        Args:
            entry: The price row as shipped; its `quantity_unit` says how it is quoted.

        Returns:
            The energy content of one native unit, or None when `quantity_unit` is "kWh".

        Raises:
            CostDataError: If the unit cannot be converted: an unknown unit, or a carrier with no heating value.
        """
        unit = entry.quantity_unit.strip().lower()
        if unit in ("kwh", ""):
            return None
        if unit not in ("liter", "ton"):
            raise CostDataError(
                f"Energy price entry {entry.entry_key}: quantity_unit {entry.quantity_unit!r} is "
                "neither 'kWh', 'liter' nor 'ton', so the quote cannot be converted to EUR/kWh."
            )
        if entry.carrier == EnergyCarrier.DIESEL and unit == "liter":
            return EnergyContent(
                quantity_unit="liter",
                unit_symbol="l",
                kwh_per_quantity_unit=self.DIESEL_ENERGY_CONTENT_IN_KWH_PER_LITER,
                source_label="HiSim generic_car diesel heating value",
            )
        # Deferred imports: `hisim.economics` must stay importable without `hisim.components`.
        from hisim import loadtypes as lt  # pylint: disable=import-outside-toplevel
        from hisim.components.configuration import PhysicsConfig  # pylint: disable=import-outside-toplevel

        load_types_by_carrier = {
            EnergyCarrier.HEATING_OIL: lt.LoadTypes.OIL,
            EnergyCarrier.PELLETS: lt.LoadTypes.PELLETS,
            EnergyCarrier.WOOD_CHIPS: lt.LoadTypes.WOOD_CHIPS,
        }
        load_type = load_types_by_carrier.get(entry.carrier)
        if load_type is None:
            raise CostDataError(
                f"Energy price entry {entry.entry_key}: no lower heating value is known for "
                f"{entry.carrier.value}, so its {entry.quantity_unit} quote cannot be converted "
                "to EUR/kWh."
            )
        physics = PhysicsConfig.get_properties_for_energy_carrier(load_type)
        if unit == "liter":
            kwh_per_unit = physics.lower_heating_value_in_joule_per_m3 / 3.6e9  # J/m3 -> kWh/l
        else:
            kwh_per_unit = physics.lower_heating_value_in_joule_per_kg * 1000.0 / 3.6e6  # -> kWh/t
        return EnergyContent(
            quantity_unit=unit,
            unit_symbol="l" if unit == "liter" else "t",
            kwh_per_quantity_unit=kwh_per_unit,
            source_label=f"PhysicsConfig {load_type.name}",
        )

    def get_energy_price(self, carrier: EnergyCarrier, year: int, country: str) -> EnergyPriceEntry:
        """Return the price entry with the greatest year <= the requested year, in EUR/kWh.

        Records no provenance; pricing code calls :meth:`resolve_energy_price`. The engine reads this entry once, for
        the price basis year, and then escalates it over the horizon (§3.6 rule 5). Rows quoted per ton or per liter
        are returned as a copy whose working price and emission factor are divided by the energy content
        (:meth:`energy_content_of`), with `converted_from` recording the division; the stored row is unchanged.

        Args:
            carrier: The energy carrier billed at the system boundary (`ELECTRICITY_FEED_IN` holds the feed-in
                remuneration as its working price).
            year: The price basis year.
            country: Country code, the ``energy_prices_<COUNTRY>.json`` suffix.

        Returns:
            The single applicable entry, in EUR/kWh and kg/kWh.

        Raises:
            CostDataError: If the country has no price file, no entry for the carrier is valid in that year, or the
                row's unit has no known energy content.
        """
        if country not in self.energy_prices:
            raise CostDataError(f"No energy price data for country {country!r} in {self.base_path}.")
        candidates = [
            entry for entry in self.energy_prices[country] if entry.carrier == carrier and entry.year <= year
        ]
        if not candidates:
            raise CostDataError(f"No energy price entry for {carrier.value} in {country} valid at {year}.")
        entry = max(candidates, key=lambda entry: entry.year)
        content = self.energy_content_of(entry)
        return entry if content is None else entry.in_euro_per_kwh(content)

    def has_energy_price(self, carrier: EnergyCarrier, country: str) -> bool:
        """Return whether any price entry exists for the carrier and country, in any year.

        `evaluator.resolve_check` uses it to report a carrier the meters billed but the dataset never priced before the
        simulation starts (§9.3).
        """
        return any(entry.carrier == carrier for entry in self.energy_prices.get(country, []))

    def get_co2_price_path(self, country: str, scenario: str) -> Optional[Co2PricePath]:
        """Return the named CO2-price path, or None for scenario ``"none"``.

        ``"none"`` turns carbon pricing off. The path prices the ENERGY_CO2_PRICE part of the energy bill; it is
        unrelated to the macroeconomic CO2 damage cost (§4.5, §3.8).

        Raises:
            CostDataError: If `scenario` is not ``"none"`` and no path of that name exists for the country.
        """
        if scenario == "none":
            return None
        key = (country, scenario)
        if key not in self.co2_price_paths:
            raise CostDataError(f"No CO2 price path {scenario!r} for country {country!r}.")
        return self.co2_price_paths[key]

    def get_escalation_defaults(self, country: str) -> EscalationDefaults:
        """Return the country's escalation defaults, or empty defaults if no file ships for the country.

        This is the middle step of the §3.2 fallback (explicit `EconomicParameters` value, then this file, then the
        general escalation rate), so a missing file is not an error.
        """
        return self.escalation_defaults.get(country, EscalationDefaults(country=country))

    # ------------------------------------------------------------------ resolved entries

    def resolve_device_entry(
        self,
        component_type: ComponentType,
        year: int,
        country: str,
        ledger: ProvenanceLedger,
        fields: Sequence[str],
    ) -> ResolvedDeviceEntry:
        """Return the device entry and record the provenance of the fields the caller will price from (§3.5, §3.10).

        Resolving and recording in one step means an entry cannot reach a calculation without its provenance.

        Args:
            component_type: Asset class to resolve.
            year: Price basis year.
            country: Country code.
            ledger: The evaluation's provenance ledger; one record per named field is added.
            fields: Entry attribute names the caller will price from, in recording order.

        Returns:
            The entry paired with the ledger id of each named field.
        """
        entry = self.get_device_entry(component_type, year, country)
        return ResolvedDeviceEntry(
            entry=entry,
            provenance_by_field={
                parameter_field: self.provenance_for_device(entry, ledger, parameter_field)
                for parameter_field in fields
            },
        )

    def resolve_energy_price(
        self,
        carrier: EnergyCarrier,
        year: int,
        country: str,
        ledger: ProvenanceLedger,
        fields: Sequence[str],
    ) -> ResolvedPriceEntry:
        """Return the energy price entry and record the provenance of the named fields in one step.

        The energy counterpart of :meth:`resolve_device_entry`; `calculators/energy.py` calls it once per carrier. The
        entry is the EUR/kWh one (see :meth:`get_energy_price`); for a converted row the record's value is the
        converted figure and its `detail` names the native quote and the heating value used.
        """
        entry = self.get_energy_price(carrier, year, country)
        return ResolvedPriceEntry(
            entry=entry,
            provenance_by_field={
                parameter_field: self.provenance_for_price(entry, ledger, parameter_field)
                for parameter_field in fields
            },
        )

    # ------------------------------------------------------------------ provenance helpers

    def provenance_for_device(self, entry: DeviceEntry, ledger: ProvenanceLedger, parameter_field: str) -> int:
        """Add a DATABASE_ENTRY provenance record for one field of a device entry and return its id.

        Sourcing is per field: `field_sources` overrides the entry's `source_ids` for individual fields. Values that
        are not a band, float or string are stringified so the ledger can be serialized.

        Returns:
            The interned ledger id, stored on the cash-flow entries the field contributed to (§3.10).
        """
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

    def conversion_detail(self, entry: EnergyPriceEntry, parameter_field: str) -> Optional[str]:
        """Return the audit sentence for a field that was divided by an energy content, else None.

        Example: "300 EUR/t ÷ 5000 kWh/t = 0.06 EUR/kWh (LHV: PhysicsConfig PELLETS)". It keeps the native quote
        visible in ``cost_provenance.json`` although the recorded value is in EUR/kWh.

        Args:
            entry: A price entry; only one with `converted_from` set gets a sentence.
            parameter_field: The field being recorded; only the fields in `CONVERTED_PRICE_FIELDS` get a sentence.

        Returns:
            The detail string, or None. Bands are described by their best estimate.
        """
        content = entry.converted_from
        if content is None or parameter_field not in self.CONVERTED_PRICE_FIELDS:
            return None
        converted: Any = getattr(entry, parameter_field)
        converted_value = converted.best_estimate if isinstance(converted, UncertainValue) else float(converted)
        native_value = converted_value * content.kwh_per_quantity_unit
        numerator_unit = "EUR" if parameter_field.startswith("working_price") else "kg"
        symbol = content.unit_symbol
        return (
            f"{native_value:.6g} {numerator_unit}/{symbol} ÷ "
            f"{content.kwh_per_quantity_unit:.6g} kWh/{symbol} = "
            f"{converted_value:.6g} {numerator_unit}/kWh (LHV: {content.source_label})"
        )

    def provenance_for_price(self, entry: EnergyPriceEntry, ledger: ProvenanceLedger, parameter_field: str) -> int:
        """Add a DATABASE_ENTRY provenance record for one field of an energy price entry and return its id.

        Same `field_sources` rule and return value as :meth:`provenance_for_device`. The record's `detail` describes
        the unit conversion of a row quoted per ton or per liter (:meth:`conversion_detail`).
        """
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
                detail=self.conversion_detail(entry, parameter_field),
            )
        )

    # ------------------------------------------------------------------ scenario overlays (§4.6)

    #: EconomicParameters fields that must not be swept (§4.6).
    NON_SWEEPABLE_FIELDS = ("cost_database_path", "subsidy_catalog_path", "country")

    def with_overlays(self, overlays: Dict[str, Any], scenario_id: str) -> "CostDatabase":
        """Return a copy of the database with individual datapoints overlaid (§4.6).

        Example: ``{"devices_DE.HEAT_PUMP.specific_investment": 900}`` changes the specific investment of every heat
        pump row; ``devices_DE.HEAT_PUMP@2030.specific_investment`` pins one row. Everything not named keeps the
        shipped value. Each applied overlay is recorded in `overlay_records` as a SCENARIO_OVERLAY provenance record.
        Values are in the unit the file uses (e.g. EUR/t for pellets), since the EUR/kWh conversion happens later at
        lookup.

        Args:
            overlays: Dotted path to new value. `None` values are skipped, meaning "as shipped".
            scenario_id: Id of the scenario being built; stored in every provenance record.

        Returns:
            A new database. The source registry, CO2 paths and escalation defaults are shared; device and price entries
                are copied. The receiver is unchanged.

        Raises:
            CostDataError: On an unknown file stem, a path that is not ``<stem>.<entry>.<field>``, a path matching no
                entry, a field the entry lacks, or a field that may not be overlaid.
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
            # `clone` is a copy of `self`, i.e. the very class this method belongs to, so the
            # protected call is internal; pylint only sees an access on a foreign name.
            clone._apply_overlay(path, value, scenario_id)  # pylint: disable=protected-access
        return clone

    def _apply_overlay(self, path: str, value: Any, scenario_id: str) -> None:
        """Apply one dotted overlay path in place; called only on the copy made by :meth:`with_overlays`.

        The path is ``<file_stem>.<entry>[@year].<field>``: the stem carries the country (``devices_DE``), the entry is
        a `ComponentType` name or value for device files and an `EnergyCarrier` value for price files. Without
        ``@year`` every matching row is overlaid.

        Raises:
            CostDataError: On a malformed path, an unknown file stem, or a path matching no entry.
        """
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
        """Write one overlaid value into every matched entry and record its provenance.

        The sets below are the fields a scenario may overlay. Band fields are parsed with `UncertainValue.from_json` (a
        bare number becomes an exact band); scalar fields are coerced to float. Overlaying `service_life_in_years` logs
        a warning, because it changes the replacement years and the timeline must be rebuilt per scenario. One
        SCENARIO_OVERLAY record is added per path, not per entry.

        Raises:
            CostDataError: If an entry lacks the field, or the field may not be overlaid.
        """
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
            "scaling_exponent",
            "anyway_threshold_years_override",
        }
        # Subsidy levels are not device data and cannot be overlaid here: they are swept through the subsidy
        # catalog and the perspective's subsidy mode (§5.5). Scenarios that need "no subsidies" use
        # SubsidyMode.none().
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
