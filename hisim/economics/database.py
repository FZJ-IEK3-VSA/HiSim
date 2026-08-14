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

**What this module owns.** It is the whole data layer between the JSON files and the engine: it
parses them, validates them at load time, answers the two versioned lookups the engine needs
("which device entry applies in year Y", "which price entry applies in year Y"), records the
provenance of every value it hands out (§3.10), and produces scenario-overlaid copies of itself
for "what if" sweeps (§4.6). It owns no arithmetic — no escalation, no discounting, no pricing
of a device from its size; that is `hisim/economics/calculators/`. It also owns no cost *policy*:
which year to look up is decided by `evaluator.effective_price_basis_year`, not here.

**The two rules a reviewer should check first.**

* *Versioning is "greatest entry ≤ Y".* A lookup for year Y returns the entry with the greatest
  `valid_from_year` (devices) resp. `year` (energy prices) not exceeding Y, and raises when there
  is none — there is no interpolation and no silent fallback to a later entry. Repricing "from
  2030 on" therefore means *adding* an entry with `valid_from_year: 2030`, never editing an old
  one, so results published against an earlier basis year keep reproducing.
* *No unsourced numbers.* Loading rejects any entry without `source_ids`, and every id must
  resolve against `sources.json`; per-field `field_sources` refine that for individual fields
  (§3.10). An honestly labelled expert estimate is acceptable data, an unattributed number is not.

Failures on both paths are raised as `CostDataError` (defined in `catalog_entries.py`, re-exported
here) — the same type the pre-run resolution check and the D7 fail-fast policy build on, so bad
data surfaces as one recognizable error class rather than as KeyErrors deep inside a calculation.

Tariff contracts and the subsidy catalog live in their own modules (`tariffs.py`, `subsidies.py`)
with their own source registries; this module is only the cost-database half.
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

#: The row types and the source registry moved to `catalog_entries` / `sources`; they stay
#: importable from here so the data layer keeps one public import surface.
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
    """Resolves a ComponentType by enum name or value.

    Data files may spell an asset class either way (`"HEAT_PUMP"` or its enum value), which keeps
    hand-edited JSON forgiving without admitting free-form strings: an unknown name is a hard
    `CostDataError` naming the offending row, so a typo fails the load instead of silently
    producing a class nothing is ever priced against.
    """
    for member in ComponentType:
        if name in (member.name, member.value):
            return member
    raise CostDataError(f"{context}: unknown component_type {name!r}.")


def _energy_carrier_from_name(name: Any, context: str) -> EnergyCarrier:
    """Resolves an `EnergyCarrier` by enum value, with the row's location in the message.

    The price-file counterpart of `_component_type_from_name`, and the reason a mistyped carrier
    now fails like every other malformed row: `EnergyCarrier(item["carrier"])` raised a bare
    `ValueError` naming only the offending string, so a loader error pointed at neither the file
    nor the entry it came from (issue #24). A carrier is matched by its enum value, and by its
    member name as well, mirroring what the device loader accepts for a component type.

    Args:
        name: The `carrier` field as it stands in the file; anything not a string fails here too.
        context: The `file:carrier@year` location string the caller already assembled.

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

    One instance is one *dataset*: whatever `devices_<COUNTRY>.json`,
    `energy_prices_<COUNTRY>.json`, `co2_price_paths.json` and
    `escalation_defaults_<COUNTRY>.json` files a directory happens to contain, plus the
    `sources.json` registry they are validated against. Countries are discovered from file names,
    which is why adding a country is a data change and not a code change (README §3.2). Loading is
    eager and strict — everything is parsed and validated in `__init__`, so a malformed or
    unsourced datapoint fails before any simulation or evaluation starts (§9.6).

    The engine treats an instance as immutable. Scenario overlays never mutate one; `with_overlays`
    returns a shallow copy with the touched entry lists deep-enough-copied, so the shipped dataset
    stays the single maintained source and a sweep can hold hundreds of variants of it (§4.6).

    Consumers: `evaluator.EconomicEvaluator` and, through it, every calculator that prices
    something; `validation.py` for the data-file CI; `audit.py` and `reporting.py` for the
    input-audit tables. Which entry each of them gets is decided purely by the price basis year
    they pass in.
    """

    #: Default on-disk location of the shipped cost database.
    DEFAULT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cost_database")

    def __init__(self, base_path: Optional[str] = None) -> None:
        """Loads the database from `base_path` (default: the shipped hisim/cost_database).

        The source registry is read first and deliberately made mandatory: without `sources.json`
        there is nothing to validate `source_ids` against, so the whole "no unsourced numbers"
        rule of §3.10 would silently degrade to no rule at all.

        Raises:
            CostDataError: if the directory has no `sources.json`, or if any file in it is
                malformed, references an unknown source id, declares no sources, or violates a
                field constraint (non-positive service life, unknown `per_unit`, a cost share
                outside [0, 1]).
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
        """Discovers and loads every recognized data file of the directory, by file-name prefix.

        The country of a device or price file *is* its file-name suffix, and unrecognized `.json`
        files are ignored rather than rejected — that is what lets a deployment drop
        `devices_XX.json` next to the shipped ones and lets the directory also hold the
        `tariffs/` subtree, which `tariffs.py` loads separately. Iteration is over the sorted file
        list so load order, and hence the order of entries within a country, is reproducible.
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
        """Parses one `devices_<COUNTRY>.json` into validated `DeviceEntry` rows (§3.5).

        Every row is checked while it is read, not afterwards: sources must be declared and must
        resolve (§3.10), monetary fields go through `_band` so a bare number means an exact band
        and a real band is order-checked (§3.9), and three field constraints are enforced here
        because a violation makes the row unusable rather than merely odd — a non-positive service
        life would divide by zero in the residual-value formula, an unknown `per_unit` could never
        be matched against a component's declared `size_unit`, and an `energy_related_cost_share`
        outside [0, 1] is not a share. Optional fields fall back to the neutral value (zero cost,
        share 1.0, no override), so a minimal entry is a valid entry.

        Args:
            path: Absolute path of the file.
            file_name: Its base name — kept on every entry as `data_file`, so a provenance record
                can name the file a value came from.

        Returns:
            The entries in file order; several rows per `component_type` are normal and are what
            the `valid_from_year` lookup selects between.

        Raises:
            CostDataError: on an unknown component type, a missing or unresolvable source, a
                malformed band, or any of the three field constraints above.
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
        """Parses one `energy_prices_<COUNTRY>.json` into `EnergyPriceEntry` rows (§3.5).

        Same source and band discipline as `_load_devices`, over the two-part tariff (working
        price + standing charge) plus the fields the engine needs to keep carbon and taxes
        straight: `emission_factor_in_kg_per_kwh` and `co2_price_exposure` feed the §3.8 CO2
        accounting and the explicit CO2 price component, `tax_and_levy_share` is what the
        MACROECONOMIC view strips (§4.5), and `quantity_unit` documents that a "per kWh" field may
        in fact be per liter or per ton (README §3.3, issue #21).

        Note what is *not* enforced here: the §3.3 rule that a working price must exclude the
        explicit carbon price whenever `co2_price_exposure > 0` is a modelling convention across
        two fields, checked by review and by the plausibility panel, not by this loader.

        Args:
            path: Absolute path of the file.
            file_name: Its base name, carried on every entry for provenance.

        Returns:
            The entries in file order; the `(carrier, year)` lookup selects between them.

        Raises:
            CostDataError: on a missing or unresolvable source, or a malformed band.
            ValueError: from `EnergyCarrier(...)` on an unknown carrier name — unlike the device
                loader, this one does not wrap the enum lookup in a located message.
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
        """Loads the named carbon-price trajectories, expanding shared EU segments (§3.4).

        A path is a set of (year, EUR/t) points per country and scenario name (`low`/`central`/
        `high`), read with **step interpolation** downstream: the last point at or before a year
        applies. Segments that are identical across member states — the EU ETS2 corridor — live
        once under `eu_shared` and are pulled in by name via `include_eu_shared` instead of being
        copied into every country, so a corrected EU figure is a one-line change. Points are
        sorted after merging, which is what the step lookup relies on.

        Populates `self.co2_price_paths`, keyed by `(country, name)`; the scenario a run uses is
        chosen by `EconomicParameters.co2_price_scenario`.

        Raises:
            CostDataError: on a missing or unresolvable source, or an `include_eu_shared`
                reference to a segment that does not exist.
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
        """Entry with the greatest valid_from_year <= year; hard error when none exists (§3.5).

        The raw lookup: it records no provenance, so it is for verification and presentation
        (`resolve_check`, `validation`, `audit`, the report's input-audit table) — code that
        inspects the data rather than pricing with it. Pricing paths call
        :meth:`resolve_device_entry` instead (W2.1).

        The "greatest `valid_from_year` ≤ year" rule is what makes published results stable: a
        price change is expressed by *adding* a row with a later `valid_from_year`, so an
        evaluation pinned to an earlier basis year keeps selecting the row it always selected. A
        year *before* the earliest row is not extrapolated backwards but rejected — the basis-year
        policy in `evaluator.effective_price_basis_year` is the one place allowed to react to
        that, and it does so explicitly and with a warning.

        Args:
            component_type: The asset class to price.
            year: The price basis year (§3.5), not necessarily the simulated year.
            country: Country code, i.e. the `devices_<COUNTRY>.json` suffix.

        Returns:
            The single applicable entry.

        Raises:
            CostDataError: when the country has no device file at all, or when no entry of that
                type is valid at that year; the message lists the years that *are* available.
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
        """Whether any entry exists for the type/country (coverage matrix check, §9.6).

        Deliberately year-agnostic: the §9.6 coverage matrix asks whether a country's dataset
        *covers* an asset class at all, which is a data-completeness question, not a pricing one.
        Use `get_device_entry` when the answer has to be an actual entry.
        """
        return any(entry.component_type == component_type for entry in self.devices.get(country, []))

    def earliest_device_year(self, country: str) -> Optional[int]:
        """Earliest `valid_from_year` of any device entry of the country; None without data.

        Used by the price-basis-year policy (`evaluator.effective_price_basis_year`) to find a
        covered year when the simulation year predates the shipped data.
        """
        years = [entry.valid_from_year for entry in self.devices.get(country, [])]
        return min(years) if years else None

    #: kWh per liter of diesel. Unlike oil, pellets and wood chips, diesel has no `PhysicsConfig`
    #: row to derive a lower heating value from, so the D26 conversion falls back to HiSim's own
    #: figure: `components/generic_car.py` uses `heating_value_of_diesel_in_kwh_per_liter = 9.8`
    #: to turn simulated liters into kWh, and the pricing path must agree with the model that
    #: produced the quantity.
    DIESEL_ENERGY_CONTENT_IN_KWH_PER_LITER = 9.8

    #: Entry fields quoted per native billing quantity and therefore divided by the energy content
    #: (D26); everything else on the row is per year, one-off or dimensionless.
    CONVERTED_PRICE_FIELDS = ("working_price_in_euro_per_kwh", "emission_factor_in_kg_per_kwh")

    def energy_content_of(self, entry: EnergyPriceEntry) -> Optional[EnergyContent]:
        """kWh per native billing unit of a fuel-quoted row; None for a row already quoted per kWh.

        The lookup behind decision D26. Heating values come from exactly one place —
        `components.configuration.PhysicsConfig`, the same lower heating values the boilers and
        meters run on — so a bill and the physics that produced its kWh can never drift apart. The
        import is deferred to call time rather than done at module scope, because `hisim.economics`
        must stay importable without `hisim.components` (§10.0 rule 1) and `database.py` is loaded
        by every economics entry point.

        Args:
            entry: The as-shipped price row, whose `quantity_unit` says how it is quoted.

        Returns:
            The energy content of one native unit, or None when `quantity_unit` is already "kWh"
            and nothing has to be converted.

        Raises:
            CostDataError: when the row is quoted in a unit this method cannot convert — an
                unknown unit spelling, or a carrier with no heating value on either side. A price
                that cannot be expressed in EUR/kWh must fail here rather than be billed against
                kWh quantities as if it were one (D7).
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
        # Deferred, both of them: see the docstring — §10.0 rule 1 keeps `hisim.economics`
        # importable without `hisim.components`.
        from hisim import loadtypes as lt
        from hisim.components.configuration import PhysicsConfig

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
        """Price entry with the greatest year <= requested year, expressed in EUR/kWh (D26).

        Records no provenance — see :meth:`get_device_entry`; pricing paths call
        :meth:`resolve_energy_price` (W2.1).

        Same versioning rule and the same reason for it as the device lookup: a price series is
        extended by adding years, never by editing them. Note that the engine reads this entry
        *once*, for the price basis year, and then projects it over the horizon with the carrier's
        escalation rate — later rows are not consulted year by year (§3.6 rule 5).

        **The unit conversion happens here**, at resolution, and only here. The shipped files
        quote everything per kWh (the as-published liter/ton quotes live in each row's `notes`),
        so for them this is a pass-through; a user-supplied file may still quote solid and liquid
        fuels per ton or per liter, in which case the returned entry is a *copy* whose working
        price and emission factor have been divided by the carrier's energy content
        (:meth:`energy_content_of`) and whose `converted_from` records that division. The stored
        row keeps whatever unit the file quoted.

        Args:
            carrier: The energy carrier billed at the system boundary (`ELECTRICITY_FEED_IN` is
                its own carrier, holding the feed-in remuneration as its working price).
            year: The price basis year.
            country: Country code, i.e. the `energy_prices_<COUNTRY>.json` suffix.

        Returns:
            The single applicable entry, in EUR/kWh and kg/kWh.

        Raises:
            CostDataError: when the country has no price file, no entry for that carrier is valid
                at that year, or the applicable row is quoted in a unit with no known energy
                content.
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
        """Whether any price entry exists for the carrier/country.

        The year-agnostic counterpart of `has_device_entry`, and the check behind the
        "missing_energy_price" problem of `evaluator.resolve_check`: a carrier the meters actually
        billed but the dataset never priced must fail before the timestep loop, not inside the
        energy calculator (§9.3).
        """
        return any(entry.carrier == carrier for entry in self.energy_prices.get(country, []))

    def get_co2_price_path(self, country: str, scenario: str) -> Optional[Co2PricePath]:
        """Named CO2-price trajectory; None for scenario == 'none'.

        `"none"` is a supported modelling choice — evaluate the system with carbon pricing turned
        off — and is therefore *not* an error, whereas asking for a scenario name that the data
        file does not define is. The returned path prices the ENERGY_CO2_PRICE component of the
        energy bill; it has nothing to do with the macroeconomic CO2 *damage* cost, which is a
        flat `EconomicParameters` rate (§4.5) and must never be added to it (§3.8).

        Raises:
            CostDataError: when `scenario` is not `"none"` and no path of that name exists for the
                country.
        """
        if scenario == "none":
            return None
        key = (country, scenario)
        if key not in self.co2_price_paths:
            raise CostDataError(f"No CO2 price path {scenario!r} for country {country!r}.")
        return self.co2_price_paths[key]

    def get_escalation_defaults(self, country: str) -> EscalationDefaults:
        """Country escalation defaults; empty defaults if no file ships for the country.

        The middle link of the §3.2 fallback chain (explicit `EconomicParameters` value → this
        file → the general escalation rate), which is why a missing file is not an error: an empty
        `EscalationDefaults` simply lets every lookup fall through to the general rate. The shipped
        per-asset-class table is intentionally empty as well (spec Q2) — learning curves await
        reviewed sources.
        """
        return self.escalation_defaults.get(country, EscalationDefaults(country=country))

    # ------------------------------------------------------------------ resolved entries (W2.1)

    def resolve_device_entry(
        self,
        component_type: ComponentType,
        year: int,
        country: str,
        ledger: ProvenanceLedger,
        fields: Sequence[str],
    ) -> ResolvedDeviceEntry:
        """The §3.5 device entry *and* its provenance, recorded here rather than by the caller.

        `fields` names the entry fields the caller will price from, in the order they should be
        recorded; each becomes one `DATABASE_ENTRY` ledger record. Resolution and recording are
        one step so an entry can no longer reach a calculation with its provenance forgotten
        (W2.1) — the failure mode this replaced was a silent one: the number was right, the
        ledger simply had nothing to say about it.

        Args:
            component_type: Asset class to resolve.
            year: Price basis year.
            country: Country code.
            ledger: The evaluation's provenance ledger; **mutated** — one interned record per
                named field.
            fields: Entry attribute names the caller will price from, in recording order.

        Returns:
            A `ResolvedDeviceEntry` pairing the §3.5 entry with the ledger id of each named field,
            which the calculators then attach to the cash-flow entries they emit (§3.6).
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
        """The §3.5 energy price entry and its provenance in one step — see
        :meth:`resolve_device_entry`.

        The energy half of the W2.1 resolved-entry contract. `calculators/energy.py` calls it once
        per carrier, naming the fields it is about to bill from (working price, standing charge,
        emission factor, …), so every component of an energy bill can later be traced back to the
        row and the citation it came from (§3.10).

        The entry it wraps is the EUR/kWh one (D26, see :meth:`get_energy_price`), and the ledger
        records both halves of that: the recorded *value* is the converted figure the bill was
        actually built from, while the record's `detail` names the native quote and the heating
        value it was divided by, so nothing about the conversion is left implicit.
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
        """Records a DATABASE_ENTRY provenance record for one field of a device entry.

        Provenance is recorded per *field*, not per entry, because sourcing is per field: an entry
        cites its `source_ids` for everything it declares, and `field_sources` overrides that for
        individual fields (a market survey for the price, VDI 2067 for the service life). This
        method applies exactly that precedence. Values that are neither a band, a float nor a
        string are stringified, so the ledger can always be serialized to `cost_provenance.json`.

        Returns:
            The interned ledger id, which the caller stores on the cash-flow entries the field
            contributed to — that chain is what `LifecycleCostResult.explain` walks (§3.10).
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
        """The audit sentence for a field that was divided by an energy content, else None.

        Spells the D26 conversion out as arithmetic a reader can redo — "300 EUR/t ÷ 5000 kWh/t =
        0.06 EUR/kWh (LHV: PhysicsConfig PELLETS)" — so the native quote the data file actually
        carries survives into `cost_provenance.json` even though the recorded *value* is the
        EUR/kWh figure the bill was built from. Without it the audit trail would show a number
        that appears in no source and in no data file.

        Args:
            entry: A converted entry, i.e. one whose `converted_from` is set; anything else yields
                None because there is nothing to explain.
            parameter_field: The field being recorded. Only the two per-quantity fields of
                `CONVERTED_PRICE_FIELDS` were divided, so only those get a sentence.

        Returns:
            The detail string, or None when the field or the entry was not converted. Bands are
            described through their BEST_ESTIMATE slot; the recorded value keeps the full band.
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
        """Records a DATABASE_ENTRY provenance record for one field of an energy price entry.

        The price-entry twin of :meth:`provenance_for_device`, with the same `field_sources`
        precedence and the same return contract. The two are duplicated rather than generalized
        because they take distinct entry dataclasses with different `entry_key` formats and
        different field vocabularies; the bodies are otherwise near-identical, so a change to one
        usually belongs in both. The one price-side addition is `detail`, which carries the D26
        unit conversion for a row that was quoted per ton or per liter (:meth:`conversion_detail`).
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
        """Returns a copy with individual datapoints overlaid (§4.6).

        Overlay paths are rooted at the data file stem, e.g.
        ``devices_DE.HEAT_PUMP.specific_investment`` (optionally ``@year``-pinned). Unknown
        entries or fields are hard errors. A ``None`` value means "as shipped".

        **How overlays compose with the base files.** They do not replace or shadow a file; they
        are applied *on top of* the already-loaded, already-validated dataset, field by field, on a
        copy. Everything the scenario does not mention keeps the shipped value, so the shipped
        database remains the single maintained source and a scenario stays a diffable few-line
        change instead of a forked data file (§4.6). An unpinned path hits *every* matching entry —
        all `valid_from_year` rows of that component type — which is what makes
        "heat pumps are 30 % cheaper" a statement about the whole price series rather than about
        one year; `@year` pins a single row when that is what is meant.

        Every applied overlay is recorded in `overlay_records` as a `SCENARIO_OVERLAY` provenance
        record naming the scenario, so an explained result says exactly which numbers were
        counterfactual (§3.10). Two things are deliberately *not* overlayable: whole-dataset paths
        and `country` (`NON_SWEEPABLE_FIELDS`), and the `legacy_flat_subsidy_share` shim (W2.6,
        see `_overlay_entries`).

        An overlay value is stated in the unit the *file* uses, because overlays are applied to
        the stored rows and the D26 EUR/kWh conversion happens afterwards, at lookup: sweeping
        `energy_prices_DE.PELLETS.working_price_in_euro_per_kwh` means naming a EUR/t figure, the
        same one a reviewer would read out of the JSON.

        Args:
            overlays: Dotted path -> new value. `None` values are skipped, which is what lets a
                scenario axis express its "as shipped" level as an ordinary level.
            scenario_id: Id of the scenario being built; goes into every provenance record.

        Returns:
            A new `CostDatabase` sharing everything untouched with this one (the source registry,
            the CO2 paths and the escalation defaults are shared, the device and price entry lists
            are copied per entry). The receiver is left unchanged.

        Raises:
            CostDataError: on an unknown file stem, a path that is not
                ``<stem>.<entry>.<field>``, an entry the path matches nothing of, a field the
                entry does not have, or a field that is not overlayable.
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
        """Resolves one dotted overlay path to the entries it addresses and applies it in place.

        Called only on the *clone* produced by :meth:`with_overlays`, which is why mutating in
        place is safe here. The path grammar is `<file_stem>.<entry>[@year].<field>`, where the
        stem carries the country (`devices_DE`) and the entry is a `ComponentType` name/value for
        device files and an `EnergyCarrier` value for price files. Without an `@year` pin every
        matching row of that entry is overlaid; a path that matches no row is an error rather than
        a no-op, so a typo in a scenario file cannot silently produce base-case results.

        Raises:
            CostDataError: on a malformed path, an unknown file stem, or a path matching no entry.
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
        """Writes one overlaid value into every matched entry, and records its provenance.

        The three sets below are the **overlay surface** — the explicit allow-list of what a
        scenario may sweep. A field must be named here to be overlayable at all, so the surface is
        a reviewed decision rather than "whatever attribute happens to exist": band fields are
        parsed through `UncertainValue.from_json` (a bare number stays exact, §3.9), scalar fields
        are coerced to float, and anything else is rejected even when the entry has such an
        attribute. `service_life_in_years` is allowed but warns, because moving a service life
        moves the replacement years and therefore forces the timeline *structure* to be rebuilt
        per scenario instead of only re-priced (§4.6 — correct, merely slower).

        One `SCENARIO_OVERLAY` provenance record is appended per path (not per matched entry),
        carrying the scenario id, so `explain` can name the counterfactual value (§3.10).

        Raises:
            CostDataError: when an entry has no such field, or the field is not on the overlay
                surface.
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
        # W2.6: `legacy_flat_subsidy_share` was overlayable and is not any more. Sweeping a
        # subsidy level is a *subsidy* axis — it belongs to the catalog and the perspective's
        # subsidy mode (§5.5), where the sweep is reported as a scheme decision rather than as a
        # device price change. Keeping it here would also have made the §10.1 shim look like a
        # supported modeling knob at exactly the moment it is being retired. The overlay surface
        # is the outward-facing half of the device catalog, so what is not device data stays out
        # of it; scenarios that need "no subsidies" use SubsidyMode.none().
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
