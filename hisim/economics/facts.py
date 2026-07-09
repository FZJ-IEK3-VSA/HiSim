"""Facts that components and meters declare for the cost engine (cost_spec.md §3.3, §3.4, §9.2).

This module is intentionally a leaf: it imports nothing from ``hisim.component`` so the
component base class can import it without cycles.
"""

from __future__ import annotations

import enum
import json
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

from hisim.economics.carriers import EnergyCarrier
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import ComponentType, Units
from hisim.postprocessing.kpi_computation.kpi_structure import KpiTagEnumClass

#: Size units the cost database can price against.
SUPPORTED_SIZE_UNITS = (
    Units.KILOWATT,
    Units.KWH,
    Units.LITER,
    Units.SQUARE_METER,
    Units.ANY,
)


class CostRelevance(str, enum.Enum):
    """Mandatory class-level declaration of a component's cost role (§9.2)."""

    UNDECLARED = "UNDECLARED"
    PRICED = "PRICED"  # must return ComponentCostFacts
    FREE_OF_COST = "FREE_OF_COST"  # controllers, weather, idealized devices
    METER = "METER"  # must provide EnergyFlowFacts / BillingDeterminants


def _coerce_uncertain(
    value: Optional[Union[float, int, UncertainValue]],
) -> Optional[UncertainValue]:
    """Accepts a plain number as an exact band (§3.9)."""
    if value is None or isinstance(value, UncertainValue):
        return value
    return UncertainValue.exact(float(value))


@dataclass
class ComponentCostFacts:
    """Facts a component declares about itself for cost/emission evaluation. No prices."""

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
        """Local fail-fast validation (§9.3)."""
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
        if self.size_unit not in SUPPORTED_SIZE_UNITS:
            raise ValueError(
                f"size_unit {self.size_unit!r} is not supported for costing; "
                f"expected one of {[u.value for u in SUPPORTED_SIZE_UNITS]}."
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
        """True if any per-field override is set (then `override_source` is required in strict mode)."""
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
    """What a meter measured at a carrier boundary over the simulated period (§3.4)."""

    carrier: EnergyCarrier
    energy_bought_in_kwh: float  # simulated-period total, integrated by the meter
    energy_sold_in_kwh: float = 0.0
    # Optional: cost already computed against a dynamic tariff during simulation; if set, used
    # as the year-1 cost instead of energy * static price.
    simulated_cost_in_euro: Optional[float] = None
    simulated_revenue_in_euro: Optional[float] = None

    def __post_init__(self) -> None:
        """Validation."""
        if not math.isfinite(self.energy_bought_in_kwh) or not math.isfinite(self.energy_sold_in_kwh):
            raise ValueError("Energy flows must be finite.")


@dataclass
class BillingDeterminants:
    """Richer billing basis for time-of-use, dynamic and capacity tariffs (§8.4).

    Supersedes :class:`EnergyFlowFacts` when a non-flat tariff contract is active.
    """

    carrier: EnergyCarrier
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
        """Wraps plain annual flows for flat contracts."""
        return cls(
            carrier=flow.carrier,
            energy_bought_in_kwh=flow.energy_bought_in_kwh,
            energy_sold_in_kwh=flow.energy_sold_in_kwh,
            cost_integrated_in_euro=flow.simulated_cost_in_euro,
            revenue_integrated_in_euro=flow.simulated_revenue_in_euro,
        )


@dataclass
class ExistingAsset:
    """An asset already installed in the building (brownfield register, §4.1)."""

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
        """Validation."""
        self.replacement_cost_override_in_euro = _coerce_uncertain(self.replacement_cost_override_in_euro)
        if self.size <= 0 or not math.isfinite(self.size):
            raise ValueError("ExistingAsset.size must be finite and > 0.")

    def age_in_years(self, reference_year: int) -> int:
        """Age at the reference (simulation) year, floored at 0."""
        return max(0, reference_year - self.installation_year)


@dataclass
class ExistingAssetRegister:
    """The building's existing system, for BROWNFIELD / STATUS_QUO contexts (§4.1)."""

    assets: List[ExistingAsset] = field(default_factory=list)

    def find(self, asset_class: ComponentType) -> Optional[ExistingAsset]:
        """First registered asset of the given class, if any."""
        for asset in self.assets:
            if asset.asset_class == asset_class:
                return asset
        return None
