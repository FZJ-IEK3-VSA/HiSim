"""Row types of the cost database: one dataclass per data-file entry (cost_spec.md §3.5).

The bottom of the data layer's import order. This module holds :class:`CostDataError` — the
base error every other data-layer module raises — the dataclasses that mirror one row of a
``devices_*.json`` / ``energy_prices_*.json`` / ``co2_price_paths.json`` /
``escalation_defaults_*.json`` file, the resolved wrappers that carry provenance alongside a
row (W2.1), and the small parsing helpers those rows are built with. It deliberately imports
nothing from :mod:`hisim.economics.sources` or :mod:`hisim.economics.database`; both import
*from here*.

The design question it answers is where the boundary between "data" and "code" runs. Everything in
this file is *schema*: what a device row or a price row contains, in what units, and the handful of
derivations that are properties of a single row (cost for a given size, embodied CO2 for a given
size, a step-interpolated CO2 price, the dotted provenance key). Everything that needs more than one
row — picking the entry valid for a year, applying scenario overlays, resolving sources, caching —
belongs to `database.py`; everything that turns a row into cash flows belongs to the calculators.
Keeping the row types free of those dependencies is what allows adding a country to be a matter of
two JSON files and no code (§10.1 Phase 4: "if it needed a code change, the schema failed").

`ResolvedDeviceEntry` / `ResolvedPriceEntry` are the one place this module reaches toward provenance
— not by importing the ledger, but by carrying the integer ids the database already recorded, so a
pricing path physically cannot read a value from a lookup that recorded nothing (W2.1).
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

    The single error type of the whole data layer — loaders, the source registry and the resolved
    wrappers all raise it — so a caller can distinguish "the data is wrong" from an ordinary
    programming error with one `except` clause. It subclasses `ValueError` so existing generic
    handling still works. Data errors are deliberately fatal rather than warnings: a run that
    silently continued with a missing price would publish a wrong number.
    """


def _require_sources(entry: dict, context: str) -> Tuple[str, ...]:
    """Every data entry must carry at least one source id (§3.10).

    The data-side half of the "no unsourced numbers" rule: this checks that an entry *names*
    sources, `SourceRegistry.resolve` checks that the names exist. Applied while parsing each row,
    so an unsourced datapoint fails the load — and hence `python -m hisim.economics validate` and
    CI — rather than quietly reaching a published result.

    Raises:
        CostDataError: If the entry declares no `source_ids`.
    """
    source_ids = tuple(entry.get("source_ids", ()))
    if not source_ids:
        raise CostDataError(f"{context} has no source_ids — unsourced datapoints are not admissible (§3.10).")
    return source_ids


def _band(entry: dict, key: str, context: str, default: Optional[float] = None) -> UncertainValue:
    """Reads an uncertainty band field; a bare number means exact (§3.9).

    The accessor every monetary field of every row goes through, which is what makes the universal
    value syntax uniform across all data files. Passing a `default` marks the field optional (a
    missing removal cost is zero, not an error); omitting it marks the field mandatory, so the
    distinction between "may be absent" and "must be present" is expressed at the call site rather
    than in prose.

    Args:
        entry: The raw JSON object of one data row.
        key: Field name to read.
        context: Human-readable location of the row, used in error messages.
        default: Value to use when the field is absent or null; `None` makes the field mandatory.

    Returns:
        The parsed band.

    Raises:
        CostDataError: If a mandatory field is missing.
        ValueError: If the value is present but not a number or a well-formed band.
    """
    if key not in entry or entry[key] is None:
        if default is None:
            raise CostDataError(f"{context} misses mandatory field {key!r}.")
        return UncertainValue.exact(default)
    return UncertainValue.from_json(entry[key], context=f"{context}.{key}")


@dataclass
class DeviceEntry:
    """One device cost entry per (component_type, valid_from_year) (§3.5).

    Everything the engine needs to know about one asset class in one price vintage: what it costs to
    buy, install, plan for and eventually remove, what it costs to maintain and operate, how long it
    lasts, what it embodies in CO2, and at what VAT basis the price is stated. A component declares
    only its class and size (`facts.ComponentCostFacts`); this row supplies all the money.

    The `valid_from_year` key is what makes price history reproducible: a lookup for year Y takes
    the entry with the greatest `valid_from_year <= Y`, so a price change is expressed by *adding* a
    row rather than editing an old one, and a study published against the 2024 basis keeps producing
    its numbers. Note the units, which a reviewer should check against the data files: monetary
    fields are euro (or euro per `per_unit`), `maintenance_rate_per_year` is a *share of the gross
    investment* while `fixed_operation_cost_in_euro_per_year` is an absolute amount, and
    `service_life_in_years` and the escalation-relevant fields are exact rather than banded (§3.9
    keeps physical and time quantities out of the band model in v1).
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
    # Share of the investment that is genuinely energy-related (coupled-cost / Ohnehin-Kosten
    # logic for envelope measures, cost_spec.md Q7): when a replaced element was due for
    # renovation anyway, the non-energy share (1 - this) is credited as ANYWAY_COST_CREDIT.
    # 1.0 (the default and the shipped value everywhere for now) means the whole investment
    # is energy-related and the classic like-for-like credit applies instead.
    energy_related_cost_share: UncertainValue = field(default_factory=lambda: UncertainValue.exact(1.0))
    # Per-asset-class anyway-cost threshold in remaining-life years; envelope measures ship
    # ~5 a, None falls back to EconomicParameters.anyway_threshold_years (2 a).
    anyway_threshold_years_override: Optional[float] = None
    # ---------------------------------------------------------------- §10.1 migration shim
    # NOT device data. `legacy_flat_subsidy_share` is *subsidy* data living in the device
    # catalog: the flat percentage the pre-catalog implementation applied to a device's
    # investment, kept alive so countries without a subsidy catalog (Ireland today, issue #25)
    # still produce the numbers they produced before. It is read only by the legacy flat shim in
    # `calculators/subsidy_application.py`, is ignored whenever a subsidy catalog is active, and
    # is deliberately *not* scenario-overlayable (see `_overlay_entries`). It disappears when
    # §10.1 Phase 4 completes the catalogs; until then it stays, marked, rather than being
    # deleted or quietly reclassified as device data (W2.6).
    # Provenance: recorded with `ParameterOrigin.LEGACY_MIGRATION_SHIM`, citing a
    # `field_sources["legacy_flat_subsidy_share"]` entry if the data file supplies one — the
    # entry's own `source_ids` document its price, not any subsidy programme.
    legacy_flat_subsidy_share: float = 0.0
    # -------------------------------------------------------------- end §10.1 migration shim
    # "AS_LEGACY" marks entries migrated 1:1 from configuration.py whose VAT status is
    # undocumented; the FINANCIAL gross-up is a no-op for them (see cost_module_issues.md).
    price_basis: str = "NET"
    notes: Optional[str] = None
    data_file: str = ""

    @property
    def size_unit(self) -> Units:
        """The size unit this entry prices against.

        Translates the data file's `per_unit` string into the `Units` member a component declares,
        which is what the pre-run resolution check compares: pricing a kW-based entry against a
        component sized in m² is the classic unit mix-up, and it is rejected before the simulation
        starts rather than showing up as an implausible cost afterwards. A `null` `per_unit` maps to
        `Units.ANY` and means the price is absolute per device.

        Raises:
            CostDataError: If the entry's `per_unit` is not one of the supported strings.
        """
        if self.per_unit not in DeviceEntry.PER_UNIT_TO_SIZE_UNIT:
            raise CostDataError(f"Device entry {self.component_type} has unknown per_unit {self.per_unit!r}.")
        return DeviceEntry.PER_UNIT_TO_SIZE_UNIT[self.per_unit]

    def investment_for_size(self, size: float) -> UncertainValue:
        """Device cost for a given size, honoring economies of scale (§3.5).

        The scaling law of the cost database, in three cases: a `per_unit`-less entry is an absolute
        price per device and ignores the size entirely; with a `scaling_exponent` the cost is
        `specific_investment x size^exponent`, the standard power-law form for economies of scale;
        otherwise it is plainly linear in the size. The result is the device cost alone — fixed
        installation, planning and removal costs are separate fields, and the count of identical
        units is applied by the caller.

        Scaling is slot-wise via `UncertainValue.scale`, so the band widens proportionally and the
        cheap and expensive worlds stay coherent.

        Args:
            size: Capacity in the entry's `per_unit`, as declared by the component.

        Returns:
            The gross device cost band, before installation, planning and VAT treatment.
        """
        if self.per_unit is None:
            return self.specific_investment
        if self.scaling_exponent is not None:
            return self.specific_investment.scale(size**self.scaling_exponent)
        return self.specific_investment.scale(size)

    def embodied_co2_for_size(self, size: float) -> float:
        """Embodied CO2 for a given size in kg.

        The emissions counterpart of `investment_for_size`, feeding the lifecycle CO2 result that
        runs parallel to the money (§3.8): embodied emissions are charged at installation and at
        every replacement, undiscounted. Note the deliberate asymmetry with the cost side — there is
        no scaling exponent here (emissions are taken as linear in size) and the value is an exact
        float, since emission factors stay outside the uncertainty band model in v1 (Q25).

        Args:
            size: Capacity in the entry's `embodied_co2_per_unit`; ignored when that is null, in
                which case the value is per device.

        Returns:
            Embodied CO2 in kg for one unit of this size.
        """
        if self.embodied_co2_per_unit is None:
            return self.embodied_co2_value
        return self.embodied_co2_value * size

    @property
    def entry_key(self) -> str:
        """Dotted provenance key, e.g. 'devices_DE.HeatPump@2024'.

        The address under which this row's fields appear in the provenance ledger and in `explain`
        output. It names file, asset class and price vintage, so a reader can go from an explained
        number straight to the JSON row — including seeing *which* vintage the greatest-year-≤
        lookup actually selected. Scenario overlay paths use the same file/entry naming but pin the
        year optionally rather than always (see `database._overlay_entries`).
        """
        stem = os.path.splitext(self.data_file)[0]
        return f"{stem}.{self.component_type.name}@{self.valid_from_year}"


@dataclass(frozen=True)
class EnergyContent:
    """How many kWh one native billing unit of a solid or liquid fuel holds (D26).

    The bridge between a literature price quoted per ton or per liter and the uniform EUR/kWh
    basis the engine bills on. It is deliberately a small record rather than a bare float: the
    provenance detail of a converted price has to name the unit it divided by *and* where the
    heating value came from, so that a reader of `cost_provenance.json` can redo the division by
    hand instead of taking a rounded EUR/kWh figure on trust.

    `unit_symbol` is only the short form used in those human-readable detail strings ("t", "l");
    `quantity_unit` is the spelling the data files use and the one `EnergyPriceEntry.quantity_unit`
    carried before the conversion.
    """

    quantity_unit: str
    unit_symbol: str
    kwh_per_quantity_unit: float
    #: Where the heating value came from, e.g. "PhysicsConfig PELLETS"; goes into the detail string.
    source_label: str


@dataclass
class EnergyPriceEntry:
    """One energy price entry per (carrier, year): a two-part tariff with explicit CO2 (§3.5).

    ``quantity_unit`` is the unit the *data file* quotes this row in ("kWh" default; "liter" for
    oil and diesel, "ton" for pellets and wood chips, as the literature sources publish them).
    Rows keep their native quotes on disk so that a reviewer can compare them against the cited
    source, but no engine code ever bills in those units: `CostDatabase.get_energy_price` divides
    the working price and the emission factor by the carrier's energy content and hands out a copy
    whose `quantity_unit` is "kWh" and whose `converted_from` records the division (D26). Once
    that has happened the `*_per_kwh` field names are literally true, which is the whole point —
    quantities are kWh everywhere, so prices must be too.

    Two-part means the bill has a consumption-dependent working price and a consumption-independent
    standing charge (Grundpreis), which escalate at different rates and are allocated differently
    between landlord and tenant, so they must stay separate rather than being merged into an average
    €/kWh. `grid_exit_fee_in_euro` is the one-off charge for abandoning a carrier entirely — the
    disconnection cost a fuel switch incurs. Neither of those two is per quantity, so neither is
    touched by the conversion.

    The **CO2 double-counting rule** is the field interaction a reviewer should check first: when
    `co2_price_exposure > 0` the working price must *exclude* the explicit carbon price, because the
    engine adds `emissions x co2_price(country, year)` from the trajectory file on top; where the
    working price already contains carbon costs (as the migrated pre-2026 entries do) the exposure
    must be 0. Never both. `tax_and_levy_share` is the fraction stripped in the MACROECONOMIC view
    (§4.5).
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
        """Dotted provenance key, e.g. 'energy_prices_DE.NATURAL_GAS@2024'.

        The price-side analogue of `DeviceEntry.entry_key`: the address this row's fields are
        recorded under in the provenance ledger, naming file, carrier and price year so an explained
        energy cost identifies exactly which vintage was applied. Unaffected by the EUR/kWh
        conversion — the converted copy is the same row, expressed in the engine's unit.
        """
        stem = os.path.splitext(self.data_file)[0]
        return f"{stem}.{self.carrier.value}@{self.year}"

    def in_euro_per_kwh(self, content: EnergyContent) -> "EnergyPriceEntry":
        """A copy of this row with the per-quantity fields divided by the carrier's energy content.

        The one place the D26 conversion arithmetic lives. Exactly two fields are per native
        quantity and therefore divided — the working price (EUR per ton/liter becomes EUR/kWh) and
        the emission factor (kg per ton/liter becomes kg/kWh); the standing charge is per year, the
        grid exit fee is a one-off, and the two shares are dimensionless, so all four pass through
        untouched. Because the *quantity* the engine multiplies these by is kWh rather than tons,
        both products are mathematically unchanged by this move; only the labels become truthful.

        Args:
            content: Energy content of one native billing unit, as resolved by the database.

        Returns:
            A new entry with `quantity_unit` "kWh" and `converted_from` set to `content`; the
            receiver, which still carries the as-shipped quote, is left unchanged.
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
    """A named CO2-price trajectory for one country (§3.5). Values in EUR per ton CO2.

    Carbon pricing is a policy variable with genuinely divergent futures, so instead of a band it is
    expressed as named `low`/`central`/`high` trajectories that a run selects via
    `EconomicParameters.co2_price_scenario` ("none" disables carbon pricing altogether). That keeps
    the choice explicit and reportable — a result says which path it assumed — rather than hiding a
    policy assumption inside an uncertainty envelope.

    A path multiplies a carrier's emissions to produce the ENERGY_CO2_PRICE cash flows, but only for
    the share the entry declares as `co2_price_exposure`; the EU-wide segments are shared across
    countries via `include_eu_shared` rather than duplicated per country file.
    """

    country: str
    name: str
    points: List[Tuple[int, float]]  # sorted (year, price)
    source_ids: Tuple[str, ...] = ()

    def price(self, year: int) -> float:
        """Price for a calendar year: step-interpolated (last defined point <= year), 0 before.

        Step rather than linear interpolation because these trajectories are statutory corridors and
        auction schedules that change on fixed dates, not smooth trends — the nEHS price steps on
        1 January, it does not drift. Years before the first point yield zero, which correctly models
        "no carbon price in force yet"; after the last point the final value persists indefinitely,
        so a horizon extending past the data does not silently fall back to zero.

        Args:
            year: Calendar year of the emission.

        Returns:
            The carbon price in EUR per ton for that year.
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
    """Country default escalation rates (§3.2 fallback chain).

    The middle link of the three-step fallback for price-change rates: an explicit
    `EconomicParameters` value wins, then this country file, then the corresponding general rate.
    It exists so that country-specific knowledge — Irish carbon-tax-driven fuel escalation, German
    grid-fee trends — lives in versioned data next to the prices it belongs with, instead of being
    baked into engine defaults that apply everywhere.

    `asset_class_rates` expresses technology learning curves (PV and batteries falling, labor-heavy
    trades rising). It ships empty on purpose (spec Q2): the mechanism is implemented and tested,
    but no reviewed source set has been adopted yet, so the general investment escalation rate
    applies to every asset class today.
    """

    country: str
    carrier_rates: Dict[EnergyCarrier, float] = field(default_factory=dict)
    asset_class_rates: Dict[ComponentType, float] = field(default_factory=dict)
    source_ids: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ResolvedDeviceEntry:
    """A device entry *plus* the ledger ids recorded for it at resolution time (W2.1).

    The pragmatic half of the spec's `ResolvedEntry`: value bands still live on `entry`, but the
    provenance is no longer a side channel a calculator has to remember to open. `CostDatabase`
    mints this wrapper and records the provenance of the requested fields itself, so a pricing
    path cannot read `entry.specific_investment` from a lookup that recorded nothing.
    """

    entry: DeviceEntry
    #: field name -> ledger record id, in the order the fields were requested.
    provenance_by_field: Dict[str, int]

    @property
    def provenance_ids(self) -> Tuple[int, ...]:
        """Every recorded ledger id, in recording order.

        What a calculator attaches to `CashFlowEntry.provenance_ids` when an amount draws on this
        entry as a whole. Recording order (a dict preserves insertion order) is the request order,
        which keeps exported provenance id lists stable between runs.
        """
        return tuple(self.provenance_by_field.values())

    def provenance_id(self, parameter_field: str) -> int:
        """The ledger id recorded for one field; asking for an unrequested field is a bug.

        Used where a cash flow draws on one field only — a maintenance entry citing the maintenance
        rate rather than the whole device row. Raising rather than recording lazily is the point of
        W2.1: a field that was not requested at resolution time has no provenance, and silently
        producing an unprovenanced number is exactly what this wrapper exists to prevent.

        Raises:
            CostDataError: If no provenance was recorded for that field.
        """
        if parameter_field not in self.provenance_by_field:
            raise CostDataError(
                f"{self.entry.entry_key}: no provenance was recorded for {parameter_field!r} — "
                "request the field when resolving the entry (W2.1)."
            )
        return self.provenance_by_field[parameter_field]


@dataclass(frozen=True)
class ResolvedPriceEntry:
    """An energy price entry plus the ledger ids recorded for it at resolution time (W2.1).

    The price-side counterpart of :class:`ResolvedDeviceEntry`, with the same contract: `CostDatabase`
    mints it and records the provenance of the requested fields itself, so an energy cost cannot be
    computed from a lookup that recorded nothing. Kept as a separate type rather than a generic one
    because the two wrap different row types and are requested with different field names.
    """

    entry: EnergyPriceEntry
    provenance_by_field: Dict[str, int]

    @property
    def provenance_ids(self) -> Tuple[int, ...]:
        """Every recorded ledger id, in recording order.

        Attached to the energy cash-flow entries that draw on this price row as a whole; see
        :meth:`ResolvedDeviceEntry.provenance_ids` for the ordering guarantee.
        """
        return tuple(self.provenance_by_field.values())

    def provenance_id(self, parameter_field: str) -> int:
        """The ledger id recorded for one field; asking for an unrequested field is a bug.

        Used for per-field citation — the working price behind an ENERGY_WORKING entry, the standing
        charge behind an ENERGY_STANDING one. Same contract as
        :meth:`ResolvedDeviceEntry.provenance_id`.

        Raises:
            CostDataError: If no provenance was recorded for that field.
        """
        if parameter_field not in self.provenance_by_field:
            raise CostDataError(
                f"{self.entry.entry_key}: no provenance was recorded for {parameter_field!r} — "
                "request the field when resolving the entry (W2.1)."
            )
        return self.provenance_by_field[parameter_field]
