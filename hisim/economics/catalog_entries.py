"""Row types of the cost database: one dataclass per data-file entry (cost_spec.md §3.5).

Holds `CostDataError`, the dataclasses mirroring one row of `devices_*.json`, `energy_prices_*.json`,
`co2_price_paths.json` and `escalation_defaults_*.json`, the resolved wrappers carrying a row's provenance ids, and the
parsing helpers. Only single-row derivations live here; picking the entry for a year, overlays and caching are
`database.py`'s job. It imports nothing from `sources` or `database`; both import from here.
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from typing import ClassVar, Dict, List, Optional, Tuple

from hisim.economics.carriers import EnergyCarrier
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import ComponentType, Units


class CostDataError(ValueError):
    """Raised when cost data files are missing, malformed or unsourced.

    The one error type of the data layer, so callers can tell bad data from programming errors. A `ValueError`
    subclass. Data errors are fatal, since a run continuing with a missing price would publish a wrong number.
    """


def _require_sources(entry: dict, context: str) -> Tuple[str, ...]:
    """Return the entry's source ids, refusing an entry without any (§3.10).

    This checks that an entry names sources; `SourceRegistry.resolve` checks that they exist. Called while parsing each
    row, so an unsourced datapoint fails the load and the `validate` CLI.

    Raises:
        CostDataError: If the entry declares no `source_ids`.
    """
    source_ids = tuple(entry.get("source_ids", ()))
    if not source_ids:
        raise CostDataError(f"{context} has no source_ids — unsourced datapoints are not admissible (§3.10).")
    return source_ids


def _band(entry: dict, key: str, context: str, default: Optional[float] = None) -> UncertainValue:
    """Read an uncertainty band field of a data row; a bare number means an exact value (§3.9).

    Every monetary field of every row is read through this. Passing `default` makes the field optional (a missing
    removal cost is zero); omitting it makes it mandatory.

    Args:
        entry: The raw JSON object of one data row.
        key: Field name to read.
        context: Human-readable location of the row, for error messages.
        default: Value used when the field is absent or null; None makes the field mandatory.

    Returns:
        The parsed band.

    Raises:
        CostDataError: If a mandatory field is missing.
        ValueError: If the value is not a number or a well-formed band.
    """
    if key not in entry or entry[key] is None:
        if default is None:
            raise CostDataError(f"{context} misses mandatory field {key!r}.")
        return UncertainValue.exact(default)
    return UncertainValue.from_json(entry[key], context=f"{context}.{key}")


@dataclass
class DeviceEntry:
    """One device cost entry per (component_type, valid_from_year) (§3.5).

    What one asset class costs in one price vintage: purchase, installation, planning, removal, maintenance and
    operation, service life, embodied CO2 and VAT basis. Components declare only class and size
    (`facts.ComponentCostFacts`); this row supplies the money. A lookup for year Y takes the entry with the greatest
    `valid_from_year <= Y`, so a price change adds a row instead of editing one.

    Units: monetary fields are euro (or euro per `per_unit`); `maintenance_rate_per_year` is a share of the gross
    investment, `fixed_operation_cost_in_euro_per_year` an absolute amount; `service_life_in_years` is exact, not
    banded.
    """

    #: Mapping of `per_unit` strings in device entries to size units of ComponentCostFacts.
    PER_UNIT_TO_SIZE_UNIT: ClassVar[Dict[Optional[str], Units]] = {
        "kW": Units.KILOWATT,
        "kWh": Units.KWH,
        "liter": Units.LITER,
        "m2": Units.SQUARE_METER,
        None: Units.ANY,
    }

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
    # Share of the investment that is energy-related (coupled cost, Ohnehin-Kosten, for envelope
    # measures): when a replaced element was due anyway, the non-energy share (1 - this) is
    # credited as ANYWAY_COST_CREDIT. 1.0 (the default and shipped value) means the classic
    # like-for-like credit applies instead.
    energy_related_cost_share: UncertainValue = field(default_factory=lambda: UncertainValue.exact(1.0))
    # Per-asset-class anyway-cost threshold in remaining-life years; envelope measures ship
    # ~5 a, None falls back to EconomicParameters.anyway_threshold_years (2 a).
    anyway_threshold_years_override: Optional[float] = None
    # `legacy_flat_subsidy_share` is subsidy data in the device catalog (§10.1 migration): the flat
    # percentage the pre-catalog implementation applied. Nothing that prices reads it; it is loaded
    # and validated until it leaves the data files, and is not scenario-overlayable.
    legacy_flat_subsidy_share: float = 0.0
    # -------------------------------------------------------------- end of the migration field
    # "AS_LEGACY" marks entries migrated 1:1 from configuration.py whose VAT status is
    # undocumented; the FINANCIAL gross-up is a no-op for them (see cost_module_issues.md).
    price_basis: str = "NET"
    notes: Optional[str] = None
    data_file: str = ""

    @property
    def size_unit(self) -> Units:
        """The `Units` member this entry prices against, from the data file's `per_unit` string.

        The pre-run check compares it with the component's declared size unit, so a kW price is never applied to an m²
        size. A null `per_unit` maps to `Units.ANY`: the price is per device.

        Raises:
            CostDataError: If `per_unit` is not a supported string.
        """
        if self.per_unit not in DeviceEntry.PER_UNIT_TO_SIZE_UNIT:
            raise CostDataError(f"Device entry {self.component_type} has unknown per_unit {self.per_unit!r}.")
        return DeviceEntry.PER_UNIT_TO_SIZE_UNIT[self.per_unit]

    def investment_for_size(self, size: float) -> UncertainValue:
        """Return the device cost for a given size, with economies of scale (§3.5).

        Without `per_unit` the price is per device and the size is ignored; with a `scaling_exponent` the cost is
        `specific_investment * size ** exponent`; otherwise it is linear in the size. Installation, planning and
        removal are separate fields, and the caller applies the unit count. Scaling is slot-wise.

        Args:
            size: Capacity in the entry's `per_unit`.

        Returns:
            The gross device cost band, before installation, planning and VAT treatment.
        """
        if self.per_unit is None:
            return self.specific_investment
        if self.scaling_exponent is not None:
            return self.specific_investment.scale(size**self.scaling_exponent)
        return self.specific_investment.scale(size)

    def embodied_co2_for_size(self, size: float) -> float:
        """Return the embodied CO2 in kg for one unit of the given size (§3.8).

        Linear in size, without a scaling exponent, and exact (emission factors are not banded). Feeds the lifecycle
        CO2 result, charged at installation and each replacement.

        Args:
            size: Capacity in the entry's `embodied_co2_per_unit`; ignored when that is null (the value is then per
                device).

        Returns:
            Embodied CO2 in kg.
        """
        if self.embodied_co2_per_unit is None:
            return self.embodied_co2_value
        return self.embodied_co2_value * size

    @property
    def entry_key(self) -> str:
        """Dotted provenance key, e.g. 'devices_DE.HeatPump@2024'.

        Names file, asset class and the vintage the lookup selected, so an explained number leads straight to its JSON
        row.
        """
        stem = os.path.splitext(self.data_file)[0]
        return f"{stem}.{self.component_type.name}@{self.valid_from_year}"


@dataclass(frozen=True)
class EnergyContent:
    """How many kWh one native billing unit (a ton, a litre) of a solid or liquid fuel holds.

    Converts per-ton or per-litre prices to the EUR/kWh basis the engine bills on. A record rather than a float because
    the provenance detail names both the unit divided by and where the heating value came from. `unit_symbol` is the
    short form for those detail strings ("t", "l"); `quantity_unit` is the data files' spelling.
    """

    quantity_unit: str
    unit_symbol: str
    kwh_per_quantity_unit: float
    #: Where the heating value came from, e.g. "PhysicsConfig PELLETS"; goes into the detail string.
    source_label: str


@dataclass
class EnergyPriceEntry:
    """One energy price entry per (carrier, year): a two-part tariff with explicit CO2 (§3.5).

    Two-part means a working price per quantity and a yearly standing charge (Grundpreis), kept apart because they
    escalate and are allocated differently. `grid_exit_fee_in_euro` is the one-off cost of disconnecting from a
    carrier.

    `quantity_unit` is the unit the row quotes in. Shipped files quote "kWh"; a user file may quote "liter" or "ton",
    and `CostDatabase.get_energy_price` then hands out a copy converted to kWh with `converted_from` set.

    CO2 double counting: when `co2_price_exposure > 0` the working price must exclude the carbon price, because the
    engine adds `emissions * co2_price(country, year)` on top; when the working price already contains it (as the
    migrated pre-2026 entries do) the exposure must be 0. `tax_and_levy_share` is the fraction stripped in the
    MACROECONOMIC view (§4.5).
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
    #: Set on the EUR/kWh copy handed out by the database; None on an as-shipped or kWh-quoted row.
    converted_from: Optional[EnergyContent] = None

    @property
    def entry_key(self) -> str:
        """Dotted provenance key, e.g. 'energy_prices_DE.NATURAL_GAS@2024'; unaffected by the kWh conversion."""
        stem = os.path.splitext(self.data_file)[0]
        return f"{stem}.{self.carrier.value}@{self.year}"

    def in_euro_per_kwh(self, content: EnergyContent) -> "EnergyPriceEntry":
        """Return a copy with the per-quantity fields divided by the carrier's energy content.

        Only the working price and the emission factor are per quantity and divided; the standing charge, grid exit fee
        and the two shares pass through. Since the engine then multiplies by kWh, the products are unchanged.

        Args:
            content: Energy content of one native billing unit, as resolved by the database.

        Returns:
            A new entry with `quantity_unit` "kWh" and `converted_from` set; the receiver is unchanged.
        """
        converted = copy.copy(self)
        converted.working_price_in_euro_per_kwh = self.working_price_in_euro_per_kwh.scale(
            1.0 / content.kwh_per_quantity_unit
        )
        converted.emission_factor_in_kg_per_kwh = (
            self.emission_factor_in_kg_per_kwh / content.kwh_per_quantity_unit
        )
        converted.quantity_unit = "kWh"
        converted.converted_from = content
        return converted


@dataclass
class Co2PricePath:
    """A named CO2-price trajectory for one country, in EUR per ton CO2 (§3.5).

    Carbon pricing is expressed as named `low`/`central`/`high` paths selected by
    `EconomicParameters.co2_price_scenario` ("none" disables it), not as a band, so a result says which path it
    assumed. A path prices a carrier's emissions only for the share in its `co2_price_exposure`. EU-wide segments are
    shared through `include_eu_shared`.
    """

    country: str
    name: str
    points: List[Tuple[int, float]]  # sorted (year, price)
    source_ids: Tuple[str, ...] = ()

    def price(self, year: int) -> float:
        """Return the price for a calendar year, step-interpolated: the last defined point at or before the year.

        Step interpolation because these are statutory corridors that change on fixed dates. Years before the first
        point yield 0; after the last point its value persists.

        Args:
            year: Calendar year of the emission.

        Returns:
            The carbon price in EUR per ton.
        """
        price = 0.0
        for point_year, point_price in self.points:
            if point_year <= year:
                price = point_price
            else:
                break
        return price


@dataclass
class EscalationDefaults:
    """Country default escalation rates, the middle step of the §3.2 fallback chain.

    An explicit `EconomicParameters` value wins, then this country file, then the general rate. `asset_class_rates`
    holds per-class learning curves; it ships empty, so the general investment escalation rate applies to every class.
    """

    country: str
    carrier_rates: Dict[EnergyCarrier, float] = field(default_factory=dict)
    asset_class_rates: Dict[ComponentType, float] = field(default_factory=dict)
    source_ids: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ResolvedDeviceEntry:
    """A device entry plus the ledger ids recorded for its requested fields at resolution time.

    `CostDatabase` creates it and records the provenance itself, so a pricing path cannot read a value from a lookup
    that recorded nothing.
    """

    entry: DeviceEntry
    #: field name -> ledger record id, in the order the fields were requested.
    provenance_by_field: Dict[str, int]

    @property
    def provenance_ids(self) -> Tuple[int, ...]:
        """Every recorded ledger id, in request order, which keeps exported id lists stable between runs."""
        return tuple(self.provenance_by_field.values())

    def provenance_id(self, parameter_field: str) -> int:
        """Return the ledger id recorded for one field.

        Used where a cash flow draws on one field only, e.g. a maintenance entry citing the maintenance rate.

        Raises:
            CostDataError: If the field was not requested at resolution time and so has no provenance.
        """
        if parameter_field not in self.provenance_by_field:
            raise CostDataError(
                f"{self.entry.entry_key}: no provenance was recorded for {parameter_field!r} — "
                "request the field when resolving the entry (W2.1)."
            )
        return self.provenance_by_field[parameter_field]


@dataclass(frozen=True)
class ResolvedPriceEntry:
    """An energy price entry plus the ledger ids recorded for its requested fields at resolution time.

    The price-side counterpart of `ResolvedDeviceEntry`, with the same contract.
    """

    entry: EnergyPriceEntry
    provenance_by_field: Dict[str, int]

    @property
    def provenance_ids(self) -> Tuple[int, ...]:
        """Every recorded ledger id, in request order."""
        return tuple(self.provenance_by_field.values())

    def provenance_id(self, parameter_field: str) -> int:
        """Return the ledger id recorded for one field, e.g. the working price behind an ENERGY_WORKING entry.

        Raises:
            CostDataError: If the field was not requested at resolution time and so has no provenance.
        """
        if parameter_field not in self.provenance_by_field:
            raise CostDataError(
                f"{self.entry.entry_key}: no provenance was recorded for {parameter_field!r} — "
                "request the field when resolving the entry (W2.1)."
            )
        return self.provenance_by_field[parameter_field]
