"""Facts that components and meters declare for the cost engine (cost_spec.md §3.3, §3.4, §9.2).

Components declare what they are (asset class, size, technical attributes) and meters declare what crossed the system
boundary; prices, lifetimes and emission factors come from the data files. `bridge.py` collects these facts after a
simulation and the evaluator turns them into cash-flow entries. `ExistingAssetRegister` describes the building before
the measure and is supplied from outside the simulation. The module imports nothing from ``hisim.component`` or the
engine, so the component base class can import it without a cycle.
"""

from __future__ import annotations

import enum
import json
import math
from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List, Optional, Tuple, Union

from hisim.economics.carriers import EnergyCarrier
from hisim.economics.catalog_entries import CostDataError
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import ComponentType, Units
from hisim.postprocessing.kpi_computation.kpi_structure import KpiTagEnumClass


class CostRelevance(str, enum.Enum):
    """A component class's mandatory declaration of its cost role (§9.2).

    Every `Component` subclass declares it as a `ClassVar`, so a forgotten `get_cost_facts` cannot silently drop a
    component from the cost results: an `UNDECLARED` component, or a `PRICED` one whose facts do not build, aborts a
    lifecycle-cost run before the first timestep. `UNDECLARED` is only the base-class default and is never accepted.
    `adapter.effective_cost_relevance` reports the declaration without inferring anything, and
    ``tests/test_economics_adapter_contract.py`` requires every component class to declare it in its own body.
    """

    UNDECLARED = "UNDECLARED"
    PRICED = "PRICED"  # must return ComponentCostFacts
    FREE_OF_COST = "FREE_OF_COST"  # controllers, weather, idealized devices
    METER = "METER"  # must provide EnergyFlowFacts / BillingDeterminants


class UndeclaredCostRelevanceError(ValueError):
    """Raised before the run when a component class in a lifecycle-cost run declares no `cost_relevance`.

    Raised by `simulator.check_cost_declarations` (§9.2), so a year-long simulation refuses in its first second instead
    of failing in postprocessing. It carries the offending classes. It is a `ValueError` because
    `Simulator.run_all_timesteps` documents that as its refusal type. The bridge does not raise it; an undeclared
    component reaching the bridge becomes an `UnresolvedSubject`.
    """

    def __init__(self, component_classes: List[type]) -> None:
        """Build the message with one bullet per offending class, in registration order."""
        self.component_classes: Tuple[type, ...] = tuple(component_classes)
        bullets = "\n".join(f"  - {describe_undeclared_class(cls)}" for cls in self.component_classes)
        super().__init__(
            f"Lifecycle cost computation was requested, but {len(self.component_classes)} "
            "component class(es) in this simulation declare no cost_relevance, so the cost model "
            "cannot describe them (cost_spec.md §9.2). The run is refused before the first "
            "timestep rather than after the last one.\n" + bullets
        )


class UnpriceableComponentError(ValueError):
    """Raised before the run when a component declares `PRICED` but no code can describe it.

    Raised by `simulator.check_cost_declarations` (§9.2) next to `UndeclaredCostRelevanceError`. The fix differs: the
    component needs a `get_cost_facts` hook or an adapter entry, and usually a ``devices_<COUNTRY>.json`` row. It
    carries the offending classes and is a `ValueError` for the same reason as its sibling.
    """

    def __init__(self, component_classes: List[type]) -> None:
        """Build the message with one bullet per offending class, in registration order."""
        self.component_classes: Tuple[type, ...] = tuple(component_classes)
        bullets = "\n".join(f"  - {describe_unpriceable_class(cls)}" for cls in self.component_classes)
        super().__init__(
            f"Lifecycle cost computation was requested, but {len(self.component_classes)} "
            "component class(es) in this simulation declare cost_relevance PRICED while nothing "
            "can produce cost facts for them, so the evaluation would abort after the run "
            "(cost_spec.md §9.1/§9.2, decision D7). The run is refused before the first timestep "
            "rather than after the last one.\n" + bullets
        )


def describe_undeclared_class(component_class: type) -> str:
    """Return one sentence naming an undeclared component class, its module, and what its author must write.

    Shared by the pre-run check and the bridge so both report the defect the same way.

    Args:
        component_class: The `Component` subclass that carries no own `cost_relevance`.

    Returns:
        The message, without trailing newline or bullet, usable as an `UnresolvedSubject.reason`.
    """
    return (
        f"{component_class.__name__} (module {component_class.__module__}) declares no "
        "cost_relevance, so nothing can say whether it costs money: declare "
        "cost_relevance = CostRelevance.PRICED, CostRelevance.METER or "
        "CostRelevance.FREE_OF_COST in the class body (cost_spec.md §9.2)"
    )


def describe_unpriceable_class(component_class: type) -> str:
    """Return one sentence naming a PRICED class with no facts source, its module, and what its author must write.

    Shared by the pre-run check and the adapter so both report the defect the same way (§9.1).

    Args:
        component_class: The `Component` subclass declaring PRICED with no facts source.

    Returns:
        The message, without trailing newline or bullet.
    """
    return (
        f"{component_class.__name__} (module {component_class.__module__}) declares "
        "cost_relevance PRICED, but it implements no get_cost_facts() of its own and has no entry "
        "in hisim.economics.adapter.FactsExtractors.BY_CLASS_NAME, so nothing can tell the cost "
        "model what it is: implement get_cost_facts() on the class or register an extractor for it "
        "(cost_spec.md §9.1)"
    )


def missing_meter_column_error(component_name: str, field_name: str, role: str) -> CostDataError:
    """Return the error for a meter output that the meter's class declares but the run does not contain.

    A meter is the only place a carrier is billed from (§3.4), so a missing column must not be skipped: the bill would
    silently lack a flow. The error makes the meter an unresolved subject and the evaluation refuses to price the rest.
    It lives here so both the bridge (reading via a `MeterSpec`) and a meter implementing `get_energy_flow_facts` raise
    the same error; a component may not import the bridge.

    Args:
        component_name: The meter instance whose column is missing.
        field_name: The output field name that was looked for.
        role: What the column carries, in plain words ("bought energy", "peak power").

    Returns:
        The `CostDataError`; the caller raises it so the traceback points at the lookup.
    """
    return CostDataError(
        f"Meter {component_name}: the {role} column {field_name!r} declared by its class "
        "is not among this run's outputs, so the flow it measures cannot be read. Billing the "
        "carrier without it would publish a bill that silently omits that flow, so the meter "
        "becomes an unresolved subject and the evaluation aborts instead (D7)."
    )


def _coerce_uncertain(
    value: Optional[Union[float, int, UncertainValue]],
) -> Optional[UncertainValue]:
    """Turn a plain number into an exact band (§3.9); None stays None.

    Lets a component author write ``investment_cost_override_in_euro=4200``. None means "no override", which differs
    from an override of zero.
    """
    if value is None or isinstance(value, UncertainValue):
        return value
    return UncertainValue.exact(float(value))


@dataclass
class ComponentCostFacts:
    """Facts a component declares about itself for cost and emission evaluation; never prices.

    The component names its cost-database row (`asset_class`), its size (`size` in `size_unit`), and optionally
    per-field overrides where it knows better than the database. Each override replaces one field only.
    `purchase_cost_override_in_euro` prices only the year-0 purchase as a whole (e.g. a reader's quote covering device,
    installation, planning and removal); later purchases are priced from the database. `override_source` is required
    when any override is set (strict mode, §9.3) and is recorded in the provenance ledger. `technical_attributes` holds
    values subsidy conditions read (§5.4), such as an SCOP or a U-value, and must be JSON-serializable.

    Envelope measures (insulation, windows, doors, ventilation, §3.2b) use the same type, sized in m², wrapped in
    `evaluator.SubjectCostFacts` and injected into `EvaluationInputs` directly.
    """

    #: Size units the cost database can price against.
    SUPPORTED_SIZE_UNITS: ClassVar[Tuple[Units, ...]] = (
        Units.KILOWATT,
        Units.KWH,
        Units.LITER,
        Units.SQUARE_METER,
        Units.ANY,
    )

    asset_class: ComponentType  # key into the cost database
    size: float  # capacity in `size_unit`
    size_unit: Units  # KILOWATT / KWH / LITER / SQUARE_METER / ANY
    kpi_tag: Optional[KpiTagEnumClass] = None
    count: int = 1
    # Per-field overrides. Monetary overrides are UncertainValue triplets (§3.9); a plain number is accepted
    # and means exact (min = best_estimate = max):
    investment_cost_override_in_euro: Optional[UncertainValue] = None
    installation_cost_override_in_euro: Optional[UncertainValue] = None
    lifetime_override_in_years: Optional[float] = None
    maintenance_rate_override: Optional[UncertainValue] = None
    fixed_operation_cost_override_in_euro_per_year: Optional[UncertainValue] = None
    embodied_co2_override_in_kg: Optional[float] = None
    # The whole year-0 purchase as one stated amount (device, installation, planning and removal of what it
    # replaces), e.g. a reader's quote for a measure. Unlike `investment_cost_override_in_euro` it prices that
    # one purchase only: replacements, maintenance and the lifetime stay what the database or other overrides say.
    purchase_cost_override_in_euro: Optional[UncertainValue] = None
    # Provenance of the overrides (§3.10). Mandatory whenever any override is set
    # (enforced in strict mode, §9.3); recorded in the provenance ledger.
    override_source: Optional[str] = None
    # True when `lifetime_override_in_years` is the engine's fallback, used because the cost database has no
    # entry for the class, rather than a stated lifetime; the result document then says `engine_fallback`.
    lifetime_is_engine_fallback: bool = False
    # True when the subject's price is unknown rather than zero: the investment override is a placeholder zero
    # because nobody stated a price (e.g. an envelope measure without a cost block). The engine books it at
    # zero, but a fixed-amount grant capped at the eligible cost cannot be valued against it (`is_unpriced`).
    price_is_unknown: bool = False
    # The asset class whose service life this subject is renewed on, when it is part of another subject's
    # system rather than a device with a life of its own: the battery's energy-management controller is renewed
    # with the battery. The engine reads that class's `service_life_in_years`; `lifetime_override_in_years` wins.
    lifetime_of_asset_class: Optional[ComponentType] = None
    # True for a subject matched only against the register entry bound to its own name (`ExistingAsset.subject`)
    # and bought new when no entry is bound to it, whatever else the register holds of its class. Only the
    # staged evaluator sets it, for the increment a later stage adds to a kept subject: a same-class lookup would
    # find the enlarged asset and call the increment kept. Every other subject ignores bound entries.
    own_register_entry: bool = False
    # The share of the house's sold energy that per-kWh (OPERATIONAL) subsidies of this subject are paid on, in
    # (0, 1]. 1.0 for every subject except the pieces of one a staged plan split: the unit a later stage enlarges
    # and each increment are paid on their size share of the energy the enlarged installation sells, so the same
    # kWh is never paid twice.
    share_of_energy_sold: float = 1.0
    # Technical attributes consumed by subsidy eligibility conditions (§5.4).
    technical_attributes: Dict[str, Any] = field(default_factory=dict)

    #: The per-field overrides, in declaration order: the authoritative list `has_overrides` reads.
    OVERRIDE_FIELDS: ClassVar[Tuple[str, ...]] = (
        "investment_cost_override_in_euro",
        "installation_cost_override_in_euro",
        "lifetime_override_in_years",
        "maintenance_rate_override",
        "fixed_operation_cost_override_in_euro_per_year",
        "embodied_co2_override_in_kg",
        "purchase_cost_override_in_euro",
    )

    def __post_init__(self) -> None:
        """Validate the facts locally and turn plain-number overrides into exact bands (§9.3).

        Runs when the component is registered, long before the timestep loop. Whether the database has an entry for the
        asset class is checked later by the pre-run resolution check. A size of exactly zero is allowed and means "not
        installed" (see `is_not_installed`).

        Raises:
            ValueError: If the asset class is not a `ComponentType`, the size is negative or not finite, the size unit
                is not priceable, `count` is below 1, the maintenance-rate override is negative in any slot, a lifetime
                override is not positive, the share of energy sold is outside (0, 1], or the technical attributes are
                not JSON-serializable.
        """
        self.investment_cost_override_in_euro = _coerce_uncertain(self.investment_cost_override_in_euro)
        self.installation_cost_override_in_euro = _coerce_uncertain(self.installation_cost_override_in_euro)
        self.purchase_cost_override_in_euro = _coerce_uncertain(self.purchase_cost_override_in_euro)
        self.maintenance_rate_override = _coerce_uncertain(self.maintenance_rate_override)
        self.fixed_operation_cost_override_in_euro_per_year = _coerce_uncertain(
            self.fixed_operation_cost_override_in_euro_per_year
        )
        if not isinstance(self.asset_class, ComponentType):
            raise ValueError(f"asset_class must be a ComponentType, got {self.asset_class!r}.")
        if not math.isfinite(self.size) or self.size < 0:
            raise ValueError(
                f"ComponentCostFacts.size must be finite and >= 0, got {self.size!r} "
                "(a size of exactly 0 is allowed and means 'not installed')."
            )
        if self.size_unit not in ComponentCostFacts.SUPPORTED_SIZE_UNITS:
            raise ValueError(
                f"size_unit {self.size_unit!r} is not supported for costing; "
                f"expected one of {[u.value for u in ComponentCostFacts.SUPPORTED_SIZE_UNITS]}."
            )
        if self.count < 1:
            raise ValueError("count must be >= 1.")
        for band_name in ("maintenance_rate_override", "purchase_cost_override_in_euro"):
            band = getattr(self, band_name)
            if band is not None and band.minimum < 0:
                raise ValueError(f"{band_name} must be non-negative in every slot.")
        if self.lifetime_override_in_years is not None and self.lifetime_override_in_years <= 0:
            raise ValueError("lifetime_override_in_years must be > 0.")
        if self.lifetime_of_asset_class is not None and not isinstance(self.lifetime_of_asset_class, ComponentType):
            raise ValueError(f"lifetime_of_asset_class must be a ComponentType, got {self.lifetime_of_asset_class!r}.")
        if not 0.0 < self.share_of_energy_sold <= 1.0:
            raise ValueError(f"share_of_energy_sold must lie in (0, 1], got {self.share_of_energy_sold!r}.")
        if self.lifetime_is_engine_fallback and self.lifetime_override_in_years is None:
            raise ValueError("lifetime_is_engine_fallback needs the fallback in lifetime_override_in_years.")
        try:
            json.dumps(self.technical_attributes)
        except (TypeError, ValueError) as err:
            raise ValueError("technical_attributes must be JSON-serializable.") from err

    def is_not_installed(self) -> bool:
        """Return True when the component is configured at zero size, i.e. declared but not built.

        Example: a building sizer with ``share_of_maximum_pv_potential = 0`` still constructs a PV system at 0 kWp. The
        extraction side skips such a component with a reason instead of pricing it.

        Returns:
            True when `size` is exactly zero.
        """
        return self.size == 0.0

    def is_unpriced(self) -> bool:
        """Return True when nobody stated what the subject costs, so its price is unknown rather than zero.

        `price_is_unknown` marks a placeholder zero; a stated `purchase_cost_override_in_euro` prices the subject
        anyway. The subsidy engine leaves a fixed grant capped at the eligible cost undecided for such a subject.

        Returns:
            True for a flagged subject without a stated purchase price.
        """
        return self.price_is_unknown and self.purchase_cost_override_in_euro is None

    def has_overrides(self) -> bool:
        """Return True if any per-field override is set; strict mode then requires `override_source`.

        :attr:`OVERRIDE_FIELDS` lists the override fields; `override_source` and `technical_attributes` are not
        overrides.
        """
        return any(getattr(self, name) is not None for name in self.OVERRIDE_FIELDS)


@dataclass(frozen=True)
class QuotedPurchase:
    """A purchase the engine has no cost facts for, priced as a whole by a stated amount.

    Example: lagging a hot-water cylinder has no asset class or price in HiSim, so it is a cost subject only when
    someone states its cost, e.g. a reader's quote. It is one INVESTMENT entry in year 0, never replaced, maintained or
    written down, and no subsidy is assessed for it. It is booked before financing, so a loan covers it.

    Args:
        subject: The cost subject it is booked under (the measure id).
        amount_in_euro: The stated amount as a band; exact for a quote.
        source: Where the amount comes from, recorded in the provenance ledger (§3.10).
    """

    subject: str
    amount_in_euro: UncertainValue
    source: str

    def __post_init__(self) -> None:
        """Refuse a negative amount and a purchase that cites nothing."""
        if self.amount_in_euro.minimum < 0:
            raise ValueError(f"QuotedPurchase {self.subject!r}: the amount must be non-negative in every slot.")
        if not self.source:
            raise ValueError(f"QuotedPurchase {self.subject!r}: a stated amount cites its source (§3.10).")


@dataclass
class EnergyFlowFacts:
    """Energy a meter measured at a carrier boundary over the simulated period (§3.4).

    Meter components (`ElectricityMeter`, `GasMeter`, `FuelMeter`, `HeatingMeter`, the EMS as district meter) report
    it. Only metered energy is billed, so an internal flow between components can never be counted twice. Quantities
    are exact floats, since the simulation measures them (§3.9). `simulated_cost_in_euro` and
    `simulated_revenue_in_euro` hold the integral of load × price for dynamic tariffs and are used instead of energy ×
    average price when set. Totals cover the simulated period; the evaluator annualizes a shorter period with a
    warning.
    """

    carrier: EnergyCarrier
    energy_bought_in_kwh: float  # simulated-period total, integrated by the meter
    energy_sold_in_kwh: float = 0.0
    # Optional: cost already computed against a dynamic tariff during simulation; if set, used
    # as the year-1 cost instead of energy * static price.
    simulated_cost_in_euro: Optional[float] = None
    simulated_revenue_in_euro: Optional[float] = None

    def __post_init__(self) -> None:
        """Reject a NaN or infinite energy total so it cannot spread into every KPI.

        Raises:
            ValueError: If either energy total is NaN or infinite.
        """
        if not math.isfinite(self.energy_bought_in_kwh) or not math.isfinite(self.energy_sold_in_kwh):
            raise ValueError("Energy flows must be finite.")


@dataclass
class BillingDeterminants:
    """Billing basis for flat, time-of-use, dynamic and capacity tariffs (§8.4).

    Besides annual kWh it holds what only the simulation can supply: energy per time-of-use band, the integral of load
    × spot price, peaks per billing period and per year, and the unweighted mean spot price. The mean spot price lets
    §8.5 separate the volume effect from the flexibility value, which escalate differently. `EvaluationInputs` carries
    only determinants, so all billing goes through `tariffs.apply_tariff`; `from_energy_flow` converts a plain
    `EnergyFlowFacts`.
    """

    carrier: EnergyCarrier
    #: Always kilowatt-hours, for every carrier, including pellets, wood chips and oil; their prices are
    #: converted from EUR/t or EUR/l to EUR/kWh when the price is resolved.
    energy_bought_in_kwh: float
    energy_sold_in_kwh: float = 0.0
    energy_bought_per_band_in_kwh: Dict[str, float] = field(default_factory=dict)  # ToU tariffs
    cost_integrated_in_euro: Optional[float] = None  # integral of load*price for DYNAMIC supply
    revenue_integrated_in_euro: Optional[float] = None
    peak_per_billing_period_in_kw: List[float] = field(default_factory=list)  # billing-interval means
    annual_peak_in_kw: float = 0.0
    # Unweighted mean spot price of the simulated year (energy-only), so the billing engine can
    # separate the volume effect from the flexibility value (§8.5):
    mean_spot_price_in_euro_per_kwh: Optional[float] = None

    @classmethod
    def from_energy_flow(cls, flow: EnergyFlowFacts) -> "BillingDeterminants":
        """Wrap plain annual flows for a flat contract, leaving the shape-dependent fields empty.

        The resulting bill equals a plain price lookup.
        """
        return cls(
            carrier=flow.carrier,
            energy_bought_in_kwh=flow.energy_bought_in_kwh,
            energy_sold_in_kwh=flow.energy_sold_in_kwh,
            cost_integrated_in_euro=flow.simulated_cost_in_euro,
            revenue_integrated_in_euro=flow.simulated_revenue_in_euro,
        )


class InstallationYearOrigin(str, enum.Enum):
    """Where an existing asset's installation year came from, as ``economics_result.json`` states it.

    The engine derives the asset's age from the year (its due replacement, the book value a measure writes off), so a
    reader needs to know whether the year was stated or assumed. Values are the document's lower-case spelling. The
    arithmetic never reads the origin.

    - ``REQUEST``: the request states the year.
    - ``MID_LIFE_DEFAULT``: the RenoVisor translator assumed mid-life for an undated device or envelope element (price
      basis year minus half the service life, never before the construction year;
      ``hisim.renovisor.economics.UnknownAge``).
    - ``CONSTRUCTION_YEAR_DEFAULT``: the construction year, used for undated envelope elements in older stored
      registers; the translator does not write it.
    - ``STAGE``: bought by an earlier stage of a staged plan, installed in the calendar year that stage starts (plan
      year 0 plus its ``from_year``). Written by ``hisim.economics.staged``; the age itself is in
      :attr:`ExistingAsset.stated_age_in_years`.
    """

    REQUEST = "request"
    MID_LIFE_DEFAULT = "mid_life_default"
    CONSTRUCTION_YEAR_DEFAULT = "construction_year_default"
    STAGE = "stage"


@dataclass
class ExistingAsset:
    """An asset already installed in the building before any measure (brownfield register, §4.1).

    The simulation models the result of the retrofit, not its starting point, so this is supplied from outside. From
    `installation_year` the engine derives age and remaining life: a kept asset costs no investment but is replaced at
    `service_life - age`; a replaced one adds its removal cost, reports its written-off book value as sunk cost
    (decision-neutral), and may earn the anyway credit (the avoided cost of a replacement that was due anyway).

    `is_functional` and `energy_carrier` feed subsidy conditions such as BEG's bonus for replacing a working fossil
    heating system. `replaced_by_asset_classes` declares which measure replaces the asset; without it a same-class
    entry means "kept". `anyway_share` says how much of the new measure the counterfactual would really have bought.
    """

    asset_class: ComponentType
    size: float
    size_unit: Units
    installation_year: int  # -> age, remaining life, replacement schedule
    replacement_cost_override_in_euro: Optional[UncertainValue] = None  # scalar accepted = exact
    is_functional: bool = True  # feeds subsidy conditions (e.g. "functioning oil boiler")
    # Carrier the asset burns, for subsidy speed-bonus conditions ("existing fossil heating"):
    energy_carrier: Optional[EnergyCarrier] = None
    # Which measure asset classes replace this asset (filled by the scenario/RenoVisor mapping;
    # a component with one of these classes is charged full investment + this asset's removal
    # cost, and triggers the sunk-cost / anyway-cost logic of §4.1):
    replaced_by_asset_classes: List[ComponentType] = field(default_factory=list)
    #: Anyway-cost ("Sowieso-Kosten") share: the fraction of the new measure's cost that the world without the
    #: renovation would really have spent on this asset. 1.0 is a genuine like-for-like replacement (dead
    #: windows replaced by windows). A first-time improvement must be well below 1: a never-insulated facade
    #: would have been repaired, not insulated, so only the repair share (scaffolding, render, paint) counts.
    #: The default 1.0 is the full like-for-like credit.
    anyway_share: float = 1.0
    #: Where `installation_year` came from; published beside the year in the result document, never read by
    #: the arithmetic. None when the register's author did not say.
    installation_year_origin: Optional[InstallationYearOrigin] = None
    #: The age the engine uses for this asset instead of measuring `installation_year` against year 0 of the
    #: timeline (`age_in_years`); not floored. None for every register of a house. Only the staged evaluator
    #: sets it, for a subject an earlier stage bought: if kept, its age at plan year 0 (negative for a stage
    #: starting later, so its first replacement falls one service life after purchase); if replaced, its age in
    #: the year the replacing stage starts, when it is written off and the anyway-cost test is taken.
    stated_age_in_years: Optional[int] = None
    #: The cost subject this entry is bound to, or None for an entry any subject of its class matches (every
    #: register of a house). Only the staged evaluator binds one: the increment a later stage bought for a kept
    #: subject, matched by that increment's subject alone (`ComponentCostFacts.own_register_entry`), so it ages
    #: beside the unit it enlarges.
    subject: Optional[str] = None

    def __post_init__(self) -> None:
        """Normalize the replacement-cost override and reject impossible inputs.

        Raises:
            ValueError: If the size is not finite and positive, `anyway_share` is outside (0, 1] (a share of zero means
                not declaring the asset as replaced), or `installation_year_origin` is neither None nor an
                `InstallationYearOrigin`.
        """
        self.replacement_cost_override_in_euro = _coerce_uncertain(self.replacement_cost_override_in_euro)
        if self.size <= 0 or not math.isfinite(self.size):
            raise ValueError("ExistingAsset.size must be finite and > 0.")
        if not math.isfinite(self.anyway_share) or not 0.0 < self.anyway_share <= 1.0:
            raise ValueError(
                f"ExistingAsset.anyway_share must be in (0, 1], got {self.anyway_share!r} for "
                f"{self.asset_class.value}: it is the share of the new measure's cost the "
                "counterfactual would truly have spent (§4.1)."
            )
        if self.installation_year_origin is not None and not isinstance(
            self.installation_year_origin, InstallationYearOrigin
        ):
            raise ValueError(
                f"ExistingAsset.installation_year_origin must be None or an InstallationYearOrigin, got "
                f"{self.installation_year_origin!r} for {self.asset_class.value}."
            )

    def age_in_years(self, reference_year: int) -> int:
        """Return the age at the reference (simulation) year, floored at 0.

        Feeds the remaining-life calculation and the anyway-cost test of §4.1. An asset installed after the reference
        year counts as new rather than getting a negative age.
        """
        return max(0, reference_year - self.installation_year)


@dataclass
class ExistingAssetRegister:
    """The building's existing system, for BROWNFIELD and STATUS_QUO contexts (§4.1).

    A list of `ExistingAsset` entries. Its presence is a switch: `perspectives.select_applicable` picks the brownfield
    perspectives when a register is attached and the greenfield ones when not. It comes from a
    `bridge.EconomicContext`, a RenoVisor request or a system setup.
    """

    assets: List[ExistingAsset] = field(default_factory=list)

    def find(self, asset_class: ComponentType) -> Optional[ExistingAsset]:
        """Return the first registered asset of the given class, or None.

        With two assets of one class (two boilers, several window batches) only the first matches.
        """
        for asset in self.assets:
            if asset.asset_class == asset_class:
                return asset
        return None
