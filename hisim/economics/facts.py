"""Facts that components and meters declare for the cost engine (cost_spec.md §3.3, §3.4, §9.2).

This module is intentionally a leaf: it imports nothing from ``hisim.component`` so the
component base class can import it without cycles.

That leaf status is a hard architectural constraint, not an accident of the current import graph.
`hisim/component.py` carries the additive hooks `get_cost_facts`, `get_energy_flow_facts` and the
`cost_relevance` class attribute, whose type annotations name the classes defined here; if this
module reached back into `hisim.component` — or into any evaluator, database or timeline module that
transitively does — importing the simulator would drag in the whole cost engine and the cycle would
break both. Hence: only `carriers`, `uncertainty`, `loadtypes` and the KPI tag enum are imported,
and no pricing logic of any kind lives here.

**The design question this module answers**: who knows what. Components know what they *are* — asset
class, size, technical attributes — and meters know what actually *crossed the system boundary*;
neither knows, or should know, what anything costs. So the declaration side is reduced to these
value types (§3.1: "components declare, the engine computes"), and every price, lifetime and
emission factor comes from the versioned data files instead. A typical component's cost declaration
shrinks from the ~40 lines of the legacy `get_cost_capex`/`get_cost_opex` pair to about six.

Its place in the pipeline: `bridge.py` collects these objects from a finished simulation (with
`adapter.py` synthesizing them for components that have not adopted the hooks yet), packs them into
`EvaluationInputs`, and the evaluator turns each one into cash-flow entries. `ExistingAssetRegister`
is the one input the simulation cannot produce at all — it describes the building as it was before
the measure — and is supplied through `bridge.EconomicContext`.
"""

from __future__ import annotations

import enum
import json
import math
from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List, Optional, Tuple, Union

from hisim.economics.carriers import EnergyCarrier
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import ComponentType, Units
from hisim.postprocessing.kpi_computation.kpi_structure import KpiTagEnumClass


class CostRelevance(str, enum.Enum):
    """Mandatory class-level declaration of a component's cost role (§9.2).

    Declared as a `ClassVar` on every `Component` subclass, this closes the one failure mode the
    "return None means no costs" default introduces: a *forgotten* `get_cost_facts` implementation
    would otherwise drop a component from every cost result without any complaint. Because the class
    must state its role, the completeness check can compare intent against behavior at component
    registration — an `UNDECLARED` component, or a `PRICED` one whose facts do not build, aborts the
    run before the timestep loop starts rather than producing a quietly incomplete cost report.

    `UNDECLARED` is the base-class default and exists only so that not-yet-migrated components stay
    loadable during the parallel phase; strictness is configurable so CI can treat it as an error
    while legacy system setups get a warning.
    """

    UNDECLARED = "UNDECLARED"
    PRICED = "PRICED"  # must return ComponentCostFacts
    FREE_OF_COST = "FREE_OF_COST"  # controllers, weather, idealized devices
    METER = "METER"  # must provide EnergyFlowFacts / BillingDeterminants


def _coerce_uncertain(
    value: Optional[Union[float, int, UncertainValue]],
) -> Optional[UncertainValue]:
    """Accepts a plain number as an exact band (§3.9).

    Applied to every monetary override field in `__post_init__`, so a component author can write
    `investment_cost_override_in_euro=4200` and still have the engine see a proper triplet. `None`
    passes through unchanged, because "no override" and "an override of zero" are different things.
    """
    if value is None or isinstance(value, UncertainValue):
        return value
    return UncertainValue.exact(float(value))


@dataclass
class ComponentCostFacts:
    """Facts a component declares about itself for cost/emission evaluation. No prices.

    The replacement for `get_cost_capex`/`get_cost_opex`: a component says which cost-database row
    describes it (`asset_class`), how big it is (`size` in `size_unit`) and — only where it genuinely
    knows better than the database — supplies per-field overrides. Everything monetary is then looked
    up by the engine, which is what makes prices versioned, sourced, country-specific and
    scenario-overlayable instead of hard-coded in component modules.

    Three properties are worth a reviewer's attention. Overrides are **per field**, so quoting a real
    installer price for the device no longer forces the caller to also invent a lifetime and a
    maintenance rate. `override_source` is mandatory whenever any override is set (strict mode,
    §9.3) and lands in the provenance ledger, so an overridden number stays as traceable as a
    database one. And `technical_attributes` is the free-form channel subsidy eligibility conditions
    read (§5.4) — an SCOP, a refrigerant, an achieved U-value — which is why it must be
    JSON-serializable: it is exported and re-read on `evaluate`/`explain` runs.

    Envelope measures (wall insulation, windows, doors, ventilation — §3.2b) use the same type even
    though they are not simulation components at all: whoever defines the variant wraps their facts
    in an `evaluator.SubjectCostFacts` and injects them into `EvaluationInputs` directly, sized in m²
    of the respective element.
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
    # Per-field overrides (no more all-or-nothing). Monetary overrides are UncertainValue
    # triplets (§3.9); a plain number is accepted and means exact (min = avg = max):
    investment_cost_override_in_euro: Optional[UncertainValue] = None
    installation_cost_override_in_euro: Optional[UncertainValue] = None
    lifetime_override_in_years: Optional[float] = None
    maintenance_rate_override: Optional[UncertainValue] = None
    fixed_operation_cost_override_in_euro_per_year: Optional[UncertainValue] = None
    embodied_co2_override_in_kg: Optional[float] = None
    # Provenance of the overrides (§3.10). Mandatory whenever any override is set
    # (enforced in strict mode, §9.3); recorded in the provenance ledger.
    override_source: Optional[str] = None
    # Technical attributes consumed by subsidy eligibility conditions (§5.4).
    technical_attributes: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Local fail-fast validation (§9.3).

        Runs at declaration time — i.e. while the component is being registered, long before the
        timestep loop — so a mis-declared fact fails in seconds rather than after an hour of
        simulation followed by an unusable cost report. It also normalizes the monetary overrides,
        accepting plain numbers as exact bands. Only *local* consistency is checked here; whether the
        cost database actually has an entry for this asset class and whether its `per_unit` matches
        `size_unit` is the pre-run resolution check's job, since that needs the database.

        Raises:
            ValueError: If the asset class is not a `ComponentType`, the size is not finite and
                positive, the size unit is not priceable, `count` is below 1, the maintenance-rate
                override is negative in any slot, a non-positive lifetime override was given, or the
                technical attributes are not JSON-serializable.
        """
        self.investment_cost_override_in_euro = _coerce_uncertain(self.investment_cost_override_in_euro)
        self.installation_cost_override_in_euro = _coerce_uncertain(self.installation_cost_override_in_euro)
        self.maintenance_rate_override = _coerce_uncertain(self.maintenance_rate_override)
        self.fixed_operation_cost_override_in_euro_per_year = _coerce_uncertain(
            self.fixed_operation_cost_override_in_euro_per_year
        )
        if not isinstance(self.asset_class, ComponentType):
            raise ValueError(f"asset_class must be a ComponentType, got {self.asset_class!r}.")
        if not math.isfinite(self.size) or self.size <= 0:
            raise ValueError(f"ComponentCostFacts.size must be finite and > 0, got {self.size!r}.")
        if self.size_unit not in ComponentCostFacts.SUPPORTED_SIZE_UNITS:
            raise ValueError(
                f"size_unit {self.size_unit!r} is not supported for costing; "
                f"expected one of {[u.value for u in ComponentCostFacts.SUPPORTED_SIZE_UNITS]}."
            )
        if self.count < 1:
            raise ValueError("count must be >= 1.")
        for band_name in ("maintenance_rate_override",):
            band = getattr(self, band_name)
            if band is not None and band.minimum < 0:
                raise ValueError(f"{band_name} must be non-negative in every slot.")
        if self.lifetime_override_in_years is not None and self.lifetime_override_in_years <= 0:
            raise ValueError("lifetime_override_in_years must be > 0.")
        try:
            json.dumps(self.technical_attributes)
        except (TypeError, ValueError) as err:
            raise ValueError("technical_attributes must be JSON-serializable.") from err

    def has_overrides(self) -> bool:
        """True if any per-field override is set (then `override_source` is required in strict mode).

        Used by the completeness/strictness checks and by the input audit, which flags a row whose
        price came from an override that cites nothing. The tuple below is the authoritative list of
        override fields; `override_source` and `technical_attributes` are deliberately not in it,
        because neither replaces a database value.
        """
        return any(
            getattr(self, name) is not None
            for name in (
                "investment_cost_override_in_euro",
                "installation_cost_override_in_euro",
                "lifetime_override_in_years",
                "maintenance_rate_override",
                "fixed_operation_cost_override_in_euro_per_year",
                "embodied_co2_override_in_kg",
            )
        )


@dataclass
class EnergyFlowFacts:
    """What a meter measured at a carrier boundary over the simulated period (§3.4).

    The billing counterpart of `ComponentCostFacts`: implemented by the meter components
    (`ElectricityMeter`, `GasMeter`, `FuelMeter`, `HeatingMeter`, the EMS acting as district meter),
    it reports the integrated energy that actually crossed the system boundary for one carrier. That
    the *only* quantities that get billed are the ones a meter declares is what makes double counting
    structurally impossible — an internal flow between two components has no meter and therefore no
    price — and lets postprocessing warn about a carrier some component consumes but nobody meters.

    Quantities here are exact (`float`, not banded): they are measured by the simulation, not
    estimated (§3.9). `simulated_cost_in_euro`/`simulated_revenue_in_euro` are the escape hatch for
    dynamic tariffs, where the meter integrated load × price over the run and that integral is a
    better year-1 bill than energy × a static average price; when set, the engine uses them instead.
    Totals cover the *simulated* period, which the evaluator annualizes (with a warning) if it was
    shorter than a year.
    """

    carrier: EnergyCarrier
    energy_bought_in_kwh: float  # simulated-period total, integrated by the meter
    energy_sold_in_kwh: float = 0.0
    # Optional: cost already computed against a dynamic tariff during simulation; if set, used
    # as the year-1 cost instead of energy * static price.
    simulated_cost_in_euro: Optional[float] = None
    simulated_revenue_in_euro: Optional[float] = None

    def __post_init__(self) -> None:
        """Validation: rejects non-finite flows so a NaN cannot propagate into every KPI.

        Raises:
            ValueError: If either energy total is NaN or infinite.
        """
        if not math.isfinite(self.energy_bought_in_kwh) or not math.isfinite(self.energy_sold_in_kwh):
            raise ValueError("Energy flows must be finite.")


@dataclass
class BillingDeterminants:
    """Richer billing basis for time-of-use, dynamic and capacity tariffs (§8.4).

    Supersedes :class:`EnergyFlowFacts` when a non-flat tariff contract is active.

    A flat tariff needs only annual kWh; anything else needs the *shape* of consumption, which only
    the simulation can supply and which cannot be reconstructed from an annual total afterwards.
    Hence the extra determinants: per-band energy for time-of-use contracts, the integral of load ×
    spot price for dynamic supply, per-billing-period and annual peaks for capacity charges, and the
    unweighted mean spot price. That last one is what lets the §8.5 decomposition separate the pure
    *volume* effect (how much was consumed) from the *flexibility value* (how well it was timed) —
    the two escalate at different rates, so they must not be projected as one number.

    `EvaluationInputs` carries only determinants, never raw `EnergyFlowFacts`, so there is exactly
    one billing path (`tariffs.apply_tariff`); `from_energy_flow` is the lift for callers that hold
    the simpler record.
    """

    carrier: EnergyCarrier
    #: Always kilowatt-hours, for every carrier — including pellets, wood chips and oil, whose
    #: prices are converted from EUR/t resp. EUR/l to EUR/kWh at resolution instead (D26).
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
        """Wraps plain annual flows for flat contracts.

        The determinants a flat two-part tariff needs are exactly the fields `EnergyFlowFacts`
        already has, so the conversion is a widening with the shape-dependent fields left empty; the
        resulting bill is identical to a plain price lookup. Any meter that only knows annual totals
        goes through here so the rest of the engine never has to branch on which record it holds.
        """
        return cls(
            carrier=flow.carrier,
            energy_bought_in_kwh=flow.energy_bought_in_kwh,
            energy_sold_in_kwh=flow.energy_sold_in_kwh,
            cost_integrated_in_euro=flow.simulated_cost_in_euro,
            revenue_integrated_in_euro=flow.simulated_revenue_in_euro,
        )


@dataclass
class ExistingAsset:
    """An asset already installed in the building (brownfield register, §4.1).

    Describes one device that is already there before any measure — the thing the simulation cannot
    know, because it models the *result* of the retrofit, not its starting point. From
    `installation_year` the engine derives age, remaining life and hence the replacement schedule; a
    kept asset costs no investment but is replaced at `service_life − age`, while a replaced one adds
    its removal cost, contributes its written-off book value to the reported (but decision-neutral)
    sunk cost, and may trigger the anyway-cost credit.

    Two fields exist purely to feed rules outside the pure cost arithmetic. `is_functional` and
    `energy_carrier` are read by subsidy eligibility conditions such as BEG's speed bonus for
    replacing a *functioning fossil* heating system. `replaced_by_asset_classes` is the explicit
    declaration of which measure supersedes this asset: without it a same-class register entry means
    "kept", and only with it does a like-for-like replacement (old windows → new windows) get
    recognized as a replacement with its avoided future cost credited (§3.2b).
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

    def __post_init__(self) -> None:
        """Validation: normalizes the replacement-cost override and rejects a non-positive size.

        Raises:
            ValueError: If the size is not finite and greater than zero.
        """
        self.replacement_cost_override_in_euro = _coerce_uncertain(self.replacement_cost_override_in_euro)
        if self.size <= 0 or not math.isfinite(self.size):
            raise ValueError("ExistingAsset.size must be finite and > 0.")

    def age_in_years(self, reference_year: int) -> int:
        """Age at the reference (simulation) year, floored at 0.

        The input to both the remaining-life calculation that schedules the first replacement and the
        anyway-cost test of §4.1. Flooring at 0 means an asset registered as installed *after* the
        reference year is treated as brand new rather than producing a negative age that would push
        its replacement beyond the horizon.
        """
        return max(0, reference_year - self.installation_year)


@dataclass
class ExistingAssetRegister:
    """The building's existing system, for BROWNFIELD / STATUS_QUO contexts (§4.1).

    A plain list of `ExistingAsset` entries that answers "what was already here". Its presence is
    also a *switch*: `perspectives.select_applicable` evaluates the brownfield/status-quo/owner/
    landlord/tenant perspectives when a register is attached and the greenfield ones when it is not,
    so a caller declares the situation rather than picking perspectives by hand.

    It is supplied from outside the simulation — a `bridge.EconomicContext`, a RenoVisor request, or
    a system setup — because nothing in a HiSim run knows what the building looked like beforehand.
    """

    assets: List[ExistingAsset] = field(default_factory=list)

    def find(self, asset_class: ComponentType) -> Optional[ExistingAsset]:
        """First registered asset of the given class, if any.

        The lookup the brownfield logic uses to decide whether a declared component is new, kept or
        replacing something. First-match semantics means a register listing two assets of one class
        (two boilers, several window batches) matches only the first — acceptable for the one-of-each
        systems the register is designed for, but worth knowing before registering duplicates.
        """
        for asset in self.assets:
            if asset.asset_class == asset_class:
                return asset
        return None
