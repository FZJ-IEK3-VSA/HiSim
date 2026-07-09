"""Actor model: owner-occupier / landlord / tenant allocation (cost_spec.md §6).

After the timeline is built, an allocation ruleset stamps a payer on every entry (splitting
entries where a law splits them). Rulesets are country-specific modules with data-file
parameters (``hisim/cost_database/allocation_DE_2024.json``).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import List, Optional, Protocol

from hisim.economics.timeline import Actor, CashFlowEntry, CashFlowTimeline, CostCategory
from hisim.economics.uncertainty import UncertainValue

#: Default location of allocation ruleset parameter files.
DEFAULT_ALLOCATION_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cost_database"
)


@dataclass
class AllocationContext:
    """Building/tenancy facts the allocation rules need (§6)."""

    horizon_years: int
    # Simulated building emission intensity for the CO2KostAufG split (§6.3):
    building_specific_emissions_in_kg_per_m2_a: Optional[float] = None
    heated_floor_area_in_m2: Optional[float] = None
    living_area_in_m2: Optional[float] = None
    current_cold_rent_in_euro_per_m2_month: Optional[float] = None
    # Basis facts for the modernization levy (§6.4), provided by the evaluator:
    modernization_cost_in_euro: UncertainValue = field(default_factory=lambda: UncertainValue.exact(0.0))
    subsidies_received_in_euro: UncertainValue = field(default_factory=lambda: UncertainValue.exact(0.0))
    avoided_maintenance_in_euro: UncertainValue = field(default_factory=lambda: UncertainValue.exact(0.0))


class AllocationRuleset(Protocol):
    """Country-specific allocation of timeline entries to payers (§6.1)."""

    def allocate(self, timeline: CashFlowTimeline, ctx: AllocationContext) -> CashFlowTimeline:
        """Returns a new timeline with payers stamped (and entries split where a law splits them)."""
        ...  # pylint: disable=unnecessary-ellipsis


class OwnerOccupierRuleset:
    """The trivial allocation: everything is paid by the owner-occupier."""

    def allocate(self, timeline: CashFlowTimeline, ctx: AllocationContext) -> CashFlowTimeline:
        """Everything -> OWNER_OCCUPIER."""
        return CashFlowTimeline(entries=[entry.with_payer(Actor.OWNER_OCCUPIER) for entry in timeline.entries])


@dataclass
class ModernizationLevyParameters:
    """Parameterized model of §559/§559e BGB (defaults as of 2024, to be legally verified)."""

    levy_rate_per_year: float = 0.08  # general §559 (heating variant §559e: 0.10)
    cap_in_euro_per_m2_per_month: float = 3.00
    cap_low_rent_in_euro_per_m2_per_month: float = 2.00
    cap_low_rent_threshold_in_euro_per_m2: float = 7.00
    maintenance_deduction_share: float = 0.30  # avoided-maintenance share deducted from the basis
    duration_in_years: Optional[int] = None  # None = permanent rent increase


@dataclass
class Co2CostSplitTier:
    """One tier of the CO2KostAufG step function (§6.3)."""

    max_emissions_in_kg_per_m2_a: Optional[float]  # None = open-ended top tier
    tenant_share: float


class DE2024Ruleset:
    """German rented-building allocation (BetrKV, HeizKV, CO2KostAufG, §559 BGB) (§6.2)."""

    LANDLORD_CATEGORIES = frozenset(
        {
            CostCategory.INVESTMENT,
            CostCategory.PLANNING,
            CostCategory.REMOVAL,
            CostCategory.REPLACEMENT,
            CostCategory.RESIDUAL_VALUE,
            CostCategory.SUBSIDY,
            CostCategory.LOAN_INTEREST,
            CostCategory.LOAN_PRINCIPAL,
            CostCategory.LOAN_DISBURSEMENT,
            CostCategory.FEED_IN_REVENUE,  # tenant-electricity models out of scope v1
            CostCategory.ANYWAY_COST_CREDIT,
            CostCategory.REPLACEMENT_RESERVE,
        }
    )
    TENANT_CATEGORIES = frozenset(
        {
            CostCategory.ENERGY_WORKING,
            CostCategory.ENERGY_STANDING,
            CostCategory.ENERGY_CAPACITY_CHARGE,  # allocated like heating energy (§8.6)
            CostCategory.FIXED_OPERATION,  # chimney sweep, metering/billing service (BetrKV)
        }
    )

    def __init__(
        self,
        levy: Optional[ModernizationLevyParameters] = None,
        co2_tiers: Optional[List[Co2CostSplitTier]] = None,
        maintenance_apportionable_share: float = 0.5,
        apply_modernization_levy: bool = True,
    ) -> None:
        """Parameters default to the shipped data file values when loaded via :meth:`load`."""
        self.levy = levy or ModernizationLevyParameters()
        self.co2_tiers = co2_tiers or []
        self.maintenance_apportionable_share = maintenance_apportionable_share
        self.apply_modernization_levy = apply_modernization_levy

    @classmethod
    def load(cls, base_path: Optional[str] = None) -> "DE2024Ruleset":
        """Loads parameters from allocation_DE_2024.json (legal percentages are data, §6.1)."""
        path = os.path.join(base_path or DEFAULT_ALLOCATION_PATH, "allocation_DE_2024.json")
        with open(path, encoding="utf-8") as file:
            raw = json.load(file)
        levy_raw = raw["modernization_levy"]
        levy = ModernizationLevyParameters(
            levy_rate_per_year=levy_raw["levy_rate_per_year"],
            cap_in_euro_per_m2_per_month=levy_raw["cap_in_euro_per_m2_per_month"],
            cap_low_rent_in_euro_per_m2_per_month=levy_raw["cap_low_rent_in_euro_per_m2_per_month"],
            cap_low_rent_threshold_in_euro_per_m2=levy_raw["cap_low_rent_threshold_in_euro_per_m2"],
            maintenance_deduction_share=levy_raw["maintenance_deduction_share"],
            duration_in_years=levy_raw.get("duration_in_years"),
        )
        tiers = [
            Co2CostSplitTier(
                max_emissions_in_kg_per_m2_a=tier.get("max_emissions_in_kg_per_m2_a"),
                tenant_share=tier["tenant_share"],
            )
            for tier in raw["co2_cost_split_tiers"]
        ]
        return cls(
            levy=levy,
            co2_tiers=tiers,
            maintenance_apportionable_share=raw.get("maintenance_apportionable_share", 0.5),
        )

    def tenant_co2_share(self, emissions_in_kg_per_m2_a: Optional[float]) -> float:
        """The CO2KostAufG step function of the building's simulated emission intensity (§6.3)."""
        if emissions_in_kg_per_m2_a is None or not self.co2_tiers:
            return 1.0  # without intensity data the tenant pays (conservative pre-2023 default)
        for tier in self.co2_tiers:
            if tier.max_emissions_in_kg_per_m2_a is None or emissions_in_kg_per_m2_a < tier.max_emissions_in_kg_per_m2_a:
                return tier.tenant_share
        return self.co2_tiers[-1].tenant_share

    def allocate(self, timeline: CashFlowTimeline, ctx: AllocationContext) -> CashFlowTimeline:
        """Stamps payers, splits CO2 costs and maintenance, and adds the modernization levy."""
        allocated = CashFlowTimeline()
        tenant_co2 = self.tenant_co2_share(ctx.building_specific_emissions_in_kg_per_m2_a)
        for entry in timeline.entries:
            if entry.category in self.LANDLORD_CATEGORIES:
                allocated.add(entry.with_payer(Actor.LANDLORD))
            elif entry.category in self.TENANT_CATEGORIES:
                allocated.add(entry.with_payer(Actor.TENANT))
            elif entry.category == CostCategory.ENERGY_CO2_PRICE:
                if tenant_co2 > 0:
                    allocated.add(entry.scaled(tenant_co2).with_payer(Actor.TENANT))
                if tenant_co2 < 1:
                    allocated.add(entry.scaled(1.0 - tenant_co2).with_payer(Actor.LANDLORD))
            elif entry.category == CostCategory.MAINTENANCE:
                share = self.maintenance_apportionable_share
                if share > 0:
                    allocated.add(entry.scaled(share).with_payer(Actor.TENANT))
                if share < 1:
                    allocated.add(entry.scaled(1.0 - share).with_payer(Actor.LANDLORD))
            elif entry.category == CostCategory.CO2_DAMAGE:
                allocated.add(entry)  # socio-economic, not a household cash flow — stays SYSTEM
            else:
                allocated.add(entry.with_payer(Actor.LANDLORD))
        if self.apply_modernization_levy:
            allocated.extend(self.modernization_levy_entries(ctx))
        return allocated

    def modernization_levy_entries(self, ctx: AllocationContext) -> List[CashFlowEntry]:
        """The §559 BGB levy: TENANT pays a rent increase, LANDLORD receives it (§6.4).

        Basis = allocatable modernization cost - subsidies - avoided-maintenance share.
        """
        basis = (
            ctx.modernization_cost_in_euro
            + ctx.subsidies_received_in_euro.as_revenue()
            + ctx.avoided_maintenance_in_euro.scale(self.levy.maintenance_deduction_share).as_revenue()
        )
        # Slot floors at zero: a fully subsidized measure yields no levy.
        basis = UncertainValue(
            average=max(0.0, basis.average), minimum=max(0.0, basis.minimum), maximum=max(0.0, basis.maximum)
        )
        if basis.maximum <= 0:
            return []
        annual_levy = basis.scale(self.levy.levy_rate_per_year)
        if ctx.living_area_in_m2 is not None:
            cap_rate = self.levy.cap_in_euro_per_m2_per_month
            rent = ctx.current_cold_rent_in_euro_per_m2_month
            if rent is not None and rent < self.levy.cap_low_rent_threshold_in_euro_per_m2:
                cap_rate = self.levy.cap_low_rent_in_euro_per_m2_per_month
            cap = UncertainValue.exact(cap_rate * 12.0 * ctx.living_area_in_m2)
            annual_levy = annual_levy.clamp_upper(cap)
        duration = self.levy.duration_in_years or ctx.horizon_years
        entries: List[CashFlowEntry] = []
        for year in range(1, min(duration, ctx.horizon_years) + 1):
            entries.append(
                CashFlowEntry(
                    year=year,
                    amount_in_euro=annual_levy,
                    category=CostCategory.MODERNIZATION_LEVY,
                    subject="modernization levy",
                    payer=Actor.TENANT,
                )
            )
            entries.append(
                CashFlowEntry(
                    year=year,
                    amount_in_euro=annual_levy.as_revenue(),
                    category=CostCategory.MODERNIZATION_LEVY,
                    subject="modernization levy",
                    payer=Actor.LANDLORD,
                )
            )
        return entries


def get_ruleset(actor_scope_is_rented: bool, country: str, base_path: Optional[str] = None) -> AllocationRuleset:
    """Ruleset factory: DE_2024 for rented German buildings, owner-occupier otherwise (§6.1)."""
    if not actor_scope_is_rented:
        return OwnerOccupierRuleset()
    if country == "DE":
        return DE2024Ruleset.load(base_path)
    # No modernization-levy analogue elsewhere yet: generic EU_SIMPLE fallback (spec Q11) —
    # landlord pays capex/maintenance, tenant pays energy; no levy, no CO2 split table.
    return DE2024Ruleset(
        levy=ModernizationLevyParameters(levy_rate_per_year=0.0),
        co2_tiers=[Co2CostSplitTier(max_emissions_in_kg_per_m2_a=None, tenant_share=1.0)],
        maintenance_apportionable_share=0.0,
        apply_modernization_levy=False,
    )


def assert_zero_sum(system_npv: UncertainValue, payer_npvs: List[UncertainValue], tolerance: float = 1e-6) -> None:
    """Landlord-tenant is a zero-sum reallocation: sum(payer NPVs) == system NPV, per slot (§6.5)."""
    total = UncertainValue.sum(payer_npvs)
    for attribute in ("average", "minimum", "maximum"):
        system_value = getattr(system_npv, attribute)
        payer_value = getattr(total, attribute)
        if abs(system_value - payer_value) > tolerance * max(1.0, abs(system_value)):
            raise AssertionError(
                f"Zero-sum invariant violated in slot {attribute}: system={system_value}, payers={payer_value}."
            )
