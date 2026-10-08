"""Actor model: who pays which cost among owner-occupier, landlord and tenant (cost_spec.md §6).

After the timeline is built, an allocation ruleset stamps a payer on every entry and splits entries where a law splits
them. The German ruleset covers apportionable operating costs (BetrKV), energy pass-through (Heizkostenverordnung), the
CO2 cost split (CO2KostAufG) and the modernization levy (§559/§559e BGB); its percentages live in
``hisim/cost_database/allocation_DE_2024.json``. Allocation only moves money between payers, which
:func:`assert_zero_sum` checks.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Protocol, Tuple

from hisim.economics.catalog_entries import CostDataError
from hisim.economics.results import LevyBindingMechanism
from hisim.economics.timeline import Actor, CashFlowEntry, CashFlowTimeline, CostCategory
from hisim.economics.uncertainty import Slot, UncertainValue
from hisim.loadtypes import ComponentType

#: One pool's levy basis facts: (modernization cost, subsidies received, avoided maintenance).
LevyBasisParts = Tuple[UncertainValue, UncertainValue, UncertainValue]


@dataclass
class ModernizationLevySubjectBasis:
    """One measure's contribution to the §559/§559e levy basis (§6.4).

    §559e BGB charges a different rate for heating measures than §559 does for everything else, so the levy basis is
    kept per measure. The evaluator fills one record per costed subject (one costed thing on the timeline, such as a
    heat pump); the ruleset decides which paragraph each record falls under.

    `asset_class_name` is the enum name of `loadtypes.ComponentType` (e.g. ``"HEAT_PUMP"``), because that is what the
    allocation data file lists. It is ``None`` for support that belongs to no single measure, such as the financing
    repayment grant; such a record is never a heating measure and stays under §559.
    """

    subject: str
    asset_class_name: Optional[str] = None
    modernization_cost_in_euro: UncertainValue = field(default_factory=lambda: UncertainValue.exact(0.0))
    subsidies_received_in_euro: UncertainValue = field(default_factory=lambda: UncertainValue.exact(0.0))
    avoided_maintenance_in_euro: UncertainValue = field(default_factory=lambda: UncertainValue.exact(0.0))


@dataclass
class AllocationContext:
    """Building and tenancy facts the allocation rules need (§6).

    Holds the tenancy facts that cap the modernization levy (living area, current cold rent per m² and month), the
    building's simulated emission intensity that selects the CO2KostAufG tier, and the three levy-basis figures from
    the evaluator. It is built once per perspective in ``evaluator.py``. A missing optional fact has a stated fallback:
    without an emission intensity the tenant pays the whole carbon price, and without a living area the levy is
    uncapped.

    `levy_subjects` breaks the three aggregate levy-basis figures down by measure (§6.4). When it is empty the whole
    levy is charged under §559; when it is non-empty it decides the §559/§559e split and must add up to the aggregates.
    """

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
    #: Per-measure breakdown of the three figures above, for the §559/§559e split (§6.4).
    levy_subjects: List[ModernizationLevySubjectBasis] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Check that the per-measure levy breakdown adds up to the aggregate levy basis.

        A breakdown that lost a measure would silently move its euros between §559 and §559e, so the mismatch is
        refused when the context is built.

        Raises:
            ValueError: If the breakdown's per-slot sums differ from the aggregates beyond float tolerance. An empty
                breakdown is never checked.
        """
        if not self.levy_subjects:
            return
        for name in ("modernization_cost_in_euro", "subsidies_received_in_euro", "avoided_maintenance_in_euro"):
            aggregate: UncertainValue = getattr(self, name)
            parts = UncertainValue.sum(getattr(part, name) for part in self.levy_subjects)
            for slot in ("minimum", "best_estimate", "maximum"):
                expected = getattr(aggregate, slot)
                found = getattr(parts, slot)
                if abs(expected - found) > 1e-6 * max(1.0, abs(expected)):
                    raise ValueError(
                        f"AllocationContext.levy_subjects do not add up to {name} in slot {slot}: "
                        f"aggregate={expected}, breakdown={found} over "
                        f"{[part.subject for part in self.levy_subjects]}."
                    )


class AllocationRuleset(Protocol):
    """Country-specific allocation of timeline entries to payers (§6.1).

    A ruleset turns a timeline without payers into one with payers. The legal percentages are data files; a ruleset
    encodes the structure (which category is whose, whether a levy exists). It is a `Protocol`, so a new country's
    ruleset needs no shared base class.
    """

    def allocate(self, timeline: CashFlowTimeline, ctx: AllocationContext) -> CashFlowTimeline:
        """Return a new timeline with payers stamped and entries split where a law splits them.

        The input must not be mutated, since the same timeline is evaluated under several perspectives. Entries may be
        split and transfer pairs added, but the money must still add up as :func:`assert_zero_sum` states.
        """
        ...  # pylint: disable=unnecessary-ellipsis

    def modernization_levy_outcome(self, ctx: AllocationContext) -> Optional["ModernizationLevyOutcome"]:
        """Return the levy this ruleset would charge, or None where the country's law has no levy.

        The levy amount is already on the timeline as a transfer pair; this adds what a report needs beside it, namely
        whether a statutory ceiling decided it and which one.
        """
        ...  # pylint: disable=unnecessary-ellipsis


class OwnerOccupierRuleset:
    """The trivial allocation: the owner-occupier pays everything.

    Stamping the payer explicitly makes ``npv_by_payer`` well-defined for owner-occupier perspectives and lets the
    zero-sum check run over every perspective.
    """

    def allocate(self, timeline: CashFlowTimeline, ctx: AllocationContext) -> CashFlowTimeline:  # pylint: disable=unused-argument
        """Everything -> OWNER_OCCUPIER."""
        return CashFlowTimeline(
            entries=[entry.with_payer(Actor.OWNER_OCCUPIER) for entry in timeline.entries],
            validate=timeline.validate,
        )

    def modernization_levy_outcome(  # pylint: disable=unused-argument
        self, ctx: AllocationContext
    ) -> Optional["ModernizationLevyOutcome"]:
        """None: an owner-occupier charges no rent increase to themselves."""
        return None


@dataclass
class ModernizationLevyParameters:
    """Parameters of the §559/§559e BGB modernization levy (defaults as of 2024, pending legal verification).

    German law lets a landlord turn part of a modernization investment into a permanent rent increase: a percentage of
    the eligible cost per year, capped in euro per m² and month, with a lower cap for already cheap flats. The values
    come from ``allocation_DE_2024.json``. ``duration_in_years = None`` means the increase never ends, which is the
    statutory default.

    §559e adds a second rate and cap for heating measures: 10 % per year instead of 8 %, with the resulting increase
    limited to 0.50 EUR/m²/month on top of the general cap. `heating_measure_component_types` lists the
    `loadtypes.ComponentType` enum names that count as heating measures; an empty set means none do.
    """

    levy_rate_per_year: float = 0.08  # general §559
    cap_in_euro_per_m2_per_month: float = 3.00  # cap on the rent increase, per m² living area
    cap_low_rent_in_euro_per_m2_per_month: float = 2.00  # cap below the low-rent threshold
    cap_low_rent_threshold_in_euro_per_m2: float = 7.00  # cold rent per m²/month below which the low cap applies
    maintenance_deduction_share: float = 0.30  # avoided-maintenance share deducted from the basis
    duration_in_years: Optional[int] = None  # None = permanent rent increase
    heating_levy_rate_per_year: float = 0.10  # §559e rate for heating modernization
    heating_cap_in_euro_per_m2_per_month: float = 0.50  # §559e cap on the heating-attributable increase
    #: `ComponentType` names the §559e rate applies to; empty = no §559e measures (see class doc).
    heating_measure_component_types: FrozenSet[str] = frozenset()


@dataclass
class ModernizationLevyOutcome:
    """One year's rent increase, split into its §559 and §559e legs after both caps (§6.4).

    Returned by :meth:`DE2024Ruleset.compute_modernization_levy`. The timeline books only the sum as one transfer pair,
    so the legs are visible only here. All amounts are annual nominal euro bands (a band is a minimum, best estimate
    and maximum triple; each of the three is a slot).

    `total_in_euro` is the amount actually charged and the one consumers should read. It is summed per slot before the
    legs are reordered into valid bands, so it can differ from `general + heating` when the general cap binds in only
    some slots. The uncapped legs are the plain `basis x rate` products; the two cap fields are the caps as resolved
    for this context, in euro per year, and are None when no living area was given.
    """

    general_levy_in_euro: UncertainValue
    heating_levy_in_euro: UncertainValue
    total_in_euro: UncertainValue
    uncapped_general_levy_in_euro: UncertainValue
    uncapped_heating_levy_in_euro: UncertainValue
    heating_cap_in_euro_per_year: Optional[float] = None
    total_cap_in_euro_per_year: Optional[float] = None
    #: The general §559 Abs. 3a ceiling that applied, in EUR per m² and month, or None when no
    #: living area was known and therefore no cap was evaluated.
    cap_in_euro_per_m2_per_month: Optional[float] = None
    #: Which mechanism set the levy in each world, keyed by `Slot`. Caps apply per slot, so one run can be
    #: cap-decided in the expensive world and rate-decided in the cheap one. The headline answer is the
    #: BEST_ESTIMATE entry read through `LevyBindingMechanism.names_a_cap`. Empty when no living area was known.
    #: A world cut by both ceilings names both, joined by `LevyBindingMechanism.BOTH_CAPS_JOINER`.
    binding_mechanism_by_slot: Dict[Slot, str] = field(default_factory=dict)


def _ordered_band(slots: dict) -> UncertainValue:
    """Build a band from three separately computed slot values, ordered so `minimum <= best <= maximum`.

    Needed by the §559e leg split: the room the general cap leaves the §559 leg shrinks as the heating leg grows, so
    the outer slots can come out in the wrong order. The best estimate is kept and the two outer values are sorted
    around it (§3.9 defines `minimum` as the best case).

    Args:
        slots: Mapping with the keys ``minimum``, ``best_estimate`` and ``maximum``.

    Returns:
        The band with `best_estimate` unchanged and the outer slots ordered around it.
    """
    best_estimate = slots["best_estimate"]
    outer = (slots["minimum"], slots["maximum"])
    return UncertainValue(
        best_estimate=best_estimate,
        minimum=min(*outer, best_estimate),
        maximum=max(*outer, best_estimate),
    )


def _heating_measure_component_types(raw: List[str], path: str) -> FrozenSet[str]:
    """Validate the §559e heating-measure list against `loadtypes.ComponentType` (§6.4).

    A typo would silently move a heat pump into the 8 % paragraph, so every entry is checked at load time and the error
    names the file, the bad name and the closest valid names.

    Args:
        raw: The ``heating_measure_component_types`` list from the allocation data file.
        path: Path of that file, quoted in the error message.

    Returns:
        The validated `ComponentType` names as a frozen set.

    Raises:
        CostDataError: If the list is not a list of strings, or names something that is not a `ComponentType`.
    """
    if not isinstance(raw, list):
        raise CostDataError(
            f"{path}: modernization_levy.heating_measure_component_types must be a list of "
            f"ComponentType names, got {type(raw).__name__}."
        )
    known = {member.name for member in ComponentType}
    names = set()
    for item in raw:
        if not isinstance(item, str) or item not in known:
            close = sorted(name for name in known if isinstance(item, str) and item.split("_")[0] in name)
            raise CostDataError(
                f"{path}: modernization_levy.heating_measure_component_types names {item!r}, "
                f"which is not a ComponentType"
                + (f"; did you mean one of {close}?" if close else ".")
            )
        names.add(item)
    return frozenset(names)


@dataclass
class Co2CostSplitTier:
    """One tier of the CO2KostAufG table that splits the carbon-price component of the heating bill (§6.3).

    A tier holds an upper bound on the building's specific emissions and the tenant's share below it. The worse the
    building, the less the tenant pays, because the law puts the carbon cost on whoever can renovate. Tiers are read in
    ascending order; the last tier has ``None`` as its bound, meaning open-ended.
    """

    max_emissions_in_kg_per_m2_a: Optional[float]  # None = open-ended top tier
    tenant_share: float  # fraction of the CO2-price component the tenant pays, in [0, 1]


class DE2024Ruleset:
    """German allocation for rented buildings: BetrKV, HeizKV, CO2KostAufG and §559/§559e BGB (§6.2).

    Capital costs stay with the landlord. Apportionable operating costs (chimney sweep, metering and billing) and the
    energy bill go to the tenant. The carbon-price component is split by the CO2KostAufG tier table, maintenance by a
    configurable apportionable share, and the modernization levy adds a tenant-to-landlord transfer.

    The two class-level frozensets state the category-to-payer mapping. A category in neither set and not handled
    explicitly falls to the landlord, the safer default. ``CO2_DAMAGE`` is a socio-economic shadow cost, not a payment,
    and stays with ``SYSTEM``.
    """

    #: Default location of allocation ruleset parameter files.
    DEFAULT_ALLOCATION_PATH = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cost_database"
    )

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
        """Create a ruleset from explicit parameters; production code uses :meth:`load` instead.

        Direct construction is how the non-German fallback and tests get a ruleset without a levy or tier table. An
        empty ``co2_tiers`` list is allowed: :meth:`tenant_co2_share` then charges the whole carbon price to the
        tenant.
        """
        self.levy = levy or ModernizationLevyParameters()
        self.co2_tiers = co2_tiers or []
        self.maintenance_apportionable_share = maintenance_apportionable_share
        self.apply_modernization_levy = apply_modernization_levy

    @classmethod
    def load(cls, base_path: Optional[str] = None) -> "DE2024Ruleset":
        """Load the ruleset from ``allocation_DE_2024.json`` (§6.1).

        The ``modernization_levy`` block, including the §559e fields (``heating_levy_rate_per_year``,
        ``heating_cap_in_euro_per_m2_per_month``, ``heating_measure_component_types``), and the
        ``co2_cost_split_tiers`` block are mandatory; ``maintenance_apportionable_share`` defaults to 0.5.

        Args:
            base_path: Directory holding ``allocation_DE_2024.json``; defaults to the shipped ``cost_database``.

        Returns:
            The ruleset parameterized from the file.

        Raises:
            OSError: If the file does not exist.
            KeyError: If a mandatory block or field is missing.
            CostDataError: If ``heating_measure_component_types`` names something that is not a
                `loadtypes.ComponentType`.
        """
        path = os.path.join(base_path or DE2024Ruleset.DEFAULT_ALLOCATION_PATH, "allocation_DE_2024.json")
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
            heating_levy_rate_per_year=levy_raw["heating_levy_rate_per_year"],
            heating_cap_in_euro_per_m2_per_month=levy_raw["heating_cap_in_euro_per_m2_per_month"],
            heating_measure_component_types=_heating_measure_component_types(
                levy_raw["heating_measure_component_types"], path
            ),
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
        """Return the tenant's share of the carbon-price component for the building's emission intensity (§6.3).

        Example: in the shipped table a building below 12 kg CO2/m²·a puts 100 % on the tenant, and the share falls in
        steps to 5 % for the worst tier. The intensity is simulated, so a retrofit variant can move the building to
        another tier.

        Args:
            emissions_in_kg_per_m2_a: The building's simulated specific emissions in kg CO2 per m² floor area and year,
                or None if the run did not produce them.

        Returns:
            The tenant's share in [0, 1]; 1.0 when no intensity is known or no tier table is configured.
        """
        if emissions_in_kg_per_m2_a is None or not self.co2_tiers:
            return 1.0  # without intensity data the tenant pays (conservative pre-2023 default)
        for tier in self.co2_tiers:
            if tier.max_emissions_in_kg_per_m2_a is None or emissions_in_kg_per_m2_a < tier.max_emissions_in_kg_per_m2_a:
                return tier.tenant_share
        return self.co2_tiers[-1].tenant_share

    def allocate(self, timeline: CashFlowTimeline, ctx: AllocationContext) -> CashFlowTimeline:
        """Stamp payers, split CO2 costs and maintenance, and add the modernization levy.

        Most entries are retagged via the two category sets. The carbon price (by the CO2KostAufG tier) and maintenance
        (by the apportionable share) are split into two entries of the same year and category with complementary
        shares. The levy is appended last as a tenant-pays / landlord-receives transfer pair per year; it is the only
        part that creates entries.

        Args:
            timeline: The finished timeline, all entries still paid by SYSTEM.
            ctx: Tenancy and building facts, plus the levy basis figures.

        Returns:
            A new timeline in input order, with split entries expanded in place and levy pairs appended. ``CO2_DAMAGE``
                entries keep the SYSTEM payer.
        """
        allocated = CashFlowTimeline(validate=timeline.validate)
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

    def is_heating_measure(self, asset_class_name: Optional[str]) -> bool:
        """Return True if a measure of this `ComponentType` name is levied under §559e (§6.4).

        The lookup is against the data file's ``heating_measure_component_types``. A measure without a known asset
        class (``None``) is never a heating measure, so it stays under §559.
        """
        if asset_class_name is None:
            return False
        return asset_class_name in self.levy.heating_measure_component_types

    def levy_basis(
        self,
        modernization_cost: UncertainValue,
        subsidies: UncertainValue,
        avoided_maintenance: UncertainValue,
    ) -> UncertainValue:
        """Return the levy basis: modernization cost minus subsidies minus avoided maintenance, floored at zero.

        This is the deduction rule of §559 Abs. 2 BGB, which §559e repeats for heating measures, so both paragraphs
        call it. Only the configured share of the avoided maintenance is deducted, and subsidies are deducted as
        received nominal support (§5.6). The floor applies per slot.

        Args:
            modernization_cost: Allocatable modernization cost of the pool, nominal euro.
            subsidies: Support received for that pool, as a positive band.
            avoided_maintenance: Maintenance the measure avoided, as a positive band; the configured share of it is
                deducted.

        Returns:
            The levy basis band, zero in every slot where the deductions exceed the cost.
        """
        basis = (
            modernization_cost
            + subsidies.as_revenue()
            + avoided_maintenance.scale(self.levy.maintenance_deduction_share).as_revenue()
        )
        return UncertainValue(
            best_estimate=max(0.0, basis.best_estimate), minimum=max(0.0, basis.minimum), maximum=max(0.0, basis.maximum)
        )

    def general_cap_rate(self, ctx: AllocationContext) -> float:
        """Return the §559 Abs. 3a cap in EUR/m²/month: 3, or 2 below the low-rent threshold.

        The tier is chosen from the current cold rent, not the rent after the increase. An unknown rent keeps the
        higher cap.
        """
        rent = ctx.current_cold_rent_in_euro_per_m2_month
        if rent is not None and rent < self.levy.cap_low_rent_threshold_in_euro_per_m2:
            return self.levy.cap_low_rent_in_euro_per_m2_per_month
        return self.levy.cap_in_euro_per_m2_per_month

    def levy_pools(self, ctx: AllocationContext) -> Tuple[LevyBasisParts, LevyBasisParts]:
        """Split the levy basis into the §559e (heating) and §559 (general) pools.

        Without a per-measure breakdown the whole basis is general. With one, each measure's three figures are added to
        one pool or the other, in list order.

        Returns:
            ``(heating, general)``, each a ``(modernization_cost, subsidies, avoided_maintenance)`` triple of bands.
        """
        zero = UncertainValue.exact(0.0)
        if not ctx.levy_subjects:
            return (zero, zero, zero), (
                ctx.modernization_cost_in_euro,
                ctx.subsidies_received_in_euro,
                ctx.avoided_maintenance_in_euro,
            )
        pools = {True: [zero, zero, zero], False: [zero, zero, zero]}
        for part in ctx.levy_subjects:
            pool = pools[self.is_heating_measure(part.asset_class_name)]
            pool[0] = pool[0] + part.modernization_cost_in_euro
            pool[1] = pool[1] + part.subsidies_received_in_euro
            pool[2] = pool[2] + part.avoided_maintenance_in_euro
        heating, general = pools[True], pools[False]
        return (heating[0], heating[1], heating[2]), (general[0], general[1], general[2])

    def compute_modernization_levy(self, ctx: AllocationContext) -> ModernizationLevyOutcome:
        """Return the annual rent increase, split into its §559 and §559e legs and capped (§6.4).

        Both paragraphs use :meth:`levy_basis` and differ in rate and cap: §559 charges 8 %/year of the general pool,
        §559e 10 %/year of the heating pool. Two caps then apply in order:

        1. the heating cap: the §559e leg alone may not exceed 0.50 EUR/m²/month;
        2. the general cap: 3 EUR/m²/month (2 below the low-rent threshold) on the whole increase.

        When the general cap binds, the §559 leg is reduced first, so doing a heating measure never lowers the
        landlord's total increase. The heating leg is cut only when it alone exceeds the general cap. Caps apply per
        slot, so a cap can bind in the expensive world and not in the cheap one.

        Args:
            ctx: Levy basis facts, per-measure breakdown, living area and current cold rent.

        Returns:
            The two capped legs and the total as annual nominal euro bands, the two uncapped legs, and the resolved
                caps. Without a living area both legs are uncapped and the cap fields are None.
        """
        heating_parts, general_parts = self.levy_pools(ctx)
        heating = self.levy_basis(*heating_parts).scale(self.levy.heating_levy_rate_per_year)
        general = self.levy_basis(*general_parts).scale(self.levy.levy_rate_per_year)
        if ctx.living_area_in_m2 is None:
            return ModernizationLevyOutcome(
                general_levy_in_euro=general,
                heating_levy_in_euro=heating,
                total_in_euro=general + heating,
                uncapped_general_levy_in_euro=general,
                uncapped_heating_levy_in_euro=heating,
            )
        months_of_area = 12.0 * ctx.living_area_in_m2
        heating_cap = self.levy.heating_cap_in_euro_per_m2_per_month * months_of_area
        cap_rate = self.general_cap_rate(ctx)
        total_cap = cap_rate * months_of_area
        capped = {}
        mechanisms: Dict[Slot, str] = {}
        for slot in ("minimum", "best_estimate", "maximum"):
            heating_slot = min(getattr(heating, slot), heating_cap, total_cap)
            general_slot = min(getattr(general, slot), total_cap - heating_slot)
            capped[slot] = (heating_slot, general_slot)
            mechanisms[LevyBindingMechanism.SLOT_NAMES[slot]] = self._binding_mechanism(
                heating_raw=getattr(heating, slot),
                general_raw=getattr(general, slot),
                heating_capped=heating_slot,
                general_capped=general_slot,
                heating_cap=heating_cap,
                total_cap=total_cap,
                cap_rate=cap_rate,
            )
        return ModernizationLevyOutcome(
            general_levy_in_euro=_ordered_band({slot: values[1] for slot, values in capped.items()}),
            heating_levy_in_euro=_ordered_band({slot: values[0] for slot, values in capped.items()}),
            total_in_euro=UncertainValue(
                minimum=sum(capped["minimum"]), best_estimate=sum(capped["best_estimate"]), maximum=sum(capped["maximum"])
            ),
            uncapped_general_levy_in_euro=general,
            uncapped_heating_levy_in_euro=heating,
            heating_cap_in_euro_per_year=heating_cap,
            total_cap_in_euro_per_year=total_cap,
            cap_in_euro_per_m2_per_month=cap_rate,
            # The per-world verdict, because a cap applied per slot can decide one world and leave
            # the next one to the percentage of the modernization cost.
            binding_mechanism_by_slot=mechanisms,
        )

    def _binding_mechanism(
        self,
        heating_raw: float,
        general_raw: float,
        heating_capped: float,
        general_capped: float,
        heating_cap: float,
        total_cap: float,
        cap_rate: float,
    ) -> str:
        """Return a sentence naming the ceiling, or the rate, that set the levy in one world.

        The §559e ceiling applies to the heating leg alone and the §559 Abs. 3a ceiling to both legs together. The
        heating leg can be cut by either, whichever is lower; the general leg only by the general ceiling. If both
        removed euros, both are named. If neither did, the verdict names the statutory rate.

        Args:
            heating_raw: The §559e leg before any cap, in euro per year.
            general_raw: The §559 leg before any cap, in euro per year.
            heating_capped: The §559e leg after both caps.
            general_capped: The §559 leg after both caps.
            heating_cap: The §559e ceiling for this context, in euro per year.
            total_cap: The general ceiling for this context, in euro per year.
            cap_rate: The general ceiling in EUR per m² and month, used in the wording.

        Returns:
            A short sentence ready to be rendered verbatim.
        """
        tolerance = LevyBindingMechanism.TOLERANCE_IN_EURO
        heating_cut = heating_raw - heating_capped > tolerance
        general_cut = general_raw - general_capped > tolerance
        clauses = []
        # The §559e ceiling can only be what bound the heating leg when it is the lower of the
        # two; above the general cap it is never reached, and the euros were removed by §559.
        if heating_cut and heating_cap <= total_cap:
            clauses.append(
                f"{LevyBindingMechanism.HEATING_CAP} "
                f"{self.levy.heating_cap_in_euro_per_m2_per_month:,.2f} EUR/m2*mo"
            )
        if general_cut or (heating_cut and heating_cap > total_cap):
            clauses.append(f"{LevyBindingMechanism.GENERAL_CAP} {cap_rate:,.2f} EUR/m2*mo")
        if clauses:
            return LevyBindingMechanism.BOTH_CAPS_JOINER.join(clauses)
        rate = (
            self.levy.heating_levy_rate_per_year
            if heating_capped > general_capped
            else self.levy.levy_rate_per_year
        )
        return f"{rate:.0%} {LevyBindingMechanism.RATE_BELOW_CAP}"

    def modernization_levy_outcome(self, ctx: AllocationContext) -> Optional[ModernizationLevyOutcome]:
        """Return the §559/§559e outcome via :meth:`compute_modernization_levy`, under the protocol's name."""
        return self.compute_modernization_levy(ctx)

    def modernization_levy_entries(self, ctx: AllocationContext) -> List[CashFlowEntry]:
        """Return the §559/§559e levy as transfer pairs: the tenant pays a rent increase, the landlord receives it.

        Each levy year gets a positive TENANT entry and a mirrored negative LANDLORD entry, so the pair nets to zero in
        the best-estimate slot (§6.4). The amount is a fixed nominal rent increase, not escalated. The §559 and §559e
        legs are booked as one pair: with a banded basis a single leg can come out with its slots out of order while
        the sum cannot, so per-paragraph figures are read from :meth:`compute_modernization_levy`, not from entries.

        Args:
            ctx: Supplies the levy basis, the living area and current rent for the caps, and the horizon.

        Returns:
            Two entries per levy year in nominal euro, from year 1 for the levy duration or the horizon, whichever is
                shorter (a duration of None runs to the horizon). Empty when the levy is zero in every slot.
        """
        annual_levy = self.compute_modernization_levy(ctx).total_in_euro
        if annual_levy.maximum <= 0:
            return []
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
    """Return the allocation ruleset: DE_2024 for rented German buildings, owner-occupier otherwise (§6.1).

    Rented buildings outside Germany get a generic fallback: the German ruleset with no levy, no CO2 tier table (the
    tenant pays the whole carbon price) and no apportionable maintenance. It is structurally plausible, not legally
    right.

    Args:
        actor_scope_is_rented: True for LANDLORD/TENANT scopes, False for owner-occupier.
        country: ISO country code of the run.
        base_path: Optional override for the allocation parameter directory.

    Returns:
        The ruleset to allocate with.
    """
    if not actor_scope_is_rented:
        return OwnerOccupierRuleset()
    if country == "DE":
        return DE2024Ruleset.load(base_path)
    # Generic fallback for rented buildings outside Germany: landlord pays capex and maintenance, tenant pays
    # energy; no levy, no CO2 split table.
    return DE2024Ruleset(
        levy=ModernizationLevyParameters(levy_rate_per_year=0.0, heating_levy_rate_per_year=0.0),
        co2_tiers=[Co2CostSplitTier(max_emissions_in_kg_per_m2_a=None, tenant_share=1.0)],
        maintenance_apportionable_share=0.0,
        apply_modernization_levy=False,
    )


def assert_zero_sum(
    system_npv: UncertainValue,
    payer_npvs: List[UncertainValue],
    tolerance: float = 1e-6,
    require_per_slot_equality: bool = False,
) -> None:
    """Assert that allocation moved money between payers without creating any (§6.5).

    Two checks:

    1. best-estimate slot: the payer NPVs sum exactly to the system NPV;
    2. minimum and maximum: the system band lies inside the band of the payer sum::

           sum(payer NPVs).minimum <= system.minimum   and   system.maximum <= sum(...).maximum

    Equality cannot hold in the outer slots once a transfer pair is added: the tenant leg carries the levy band ``L``
    and the landlord leg ``L.as_revenue() = (-L.max, -L.best_estimate, -L.min)``, so the pair sums to ``(L.min - L.max,
    0, L.max - L.min)`` and only widens the band. Rulesets that only retag payers should pass
    ``require_per_slot_equality=True``. This is a test helper, used by ``tests/test_economics_invariants.py``, not a
    runtime guard.

    Args:
        system_npv: The NPV before allocation (all SYSTEM).
        payer_npvs: The NPV of each payer after allocation.
        tolerance: Relative float tolerance, scaled by the magnitude of the compared value.
        require_per_slot_equality: Demand equality in all three slots instead of containment.

    Raises:
        AssertionError: Naming the slot and both values, when the invariant does not hold.
    """
    total = UncertainValue.sum(payer_npvs)

    def _scaled_tolerance(value: float) -> float:
        return tolerance * max(1.0, abs(value))

    if abs(system_npv.best_estimate - total.best_estimate) > _scaled_tolerance(system_npv.best_estimate):
        raise AssertionError(
            f"Zero-sum invariant violated in slot best_estimate: system={system_npv.best_estimate}, "
            f"payers={total.best_estimate}."
        )
    if require_per_slot_equality:
        for attribute in ("minimum", "maximum"):
            system_value = getattr(system_npv, attribute)
            payer_value = getattr(total, attribute)
            if abs(system_value - payer_value) > _scaled_tolerance(system_value):
                raise AssertionError(
                    f"Zero-sum invariant violated in slot {attribute}: system={system_value}, "
                    f"payers={payer_value}."
                )
        return
    if total.minimum > system_npv.minimum + _scaled_tolerance(system_npv.minimum):
        raise AssertionError(
            f"Zero-sum envelope violated: the payer sum's minimum ({total.minimum}) is above the "
            f"system minimum ({system_npv.minimum}); allocation may only widen the band."
        )
    if system_npv.maximum > total.maximum + _scaled_tolerance(system_npv.maximum):
        raise AssertionError(
            f"Zero-sum envelope violated: the system maximum ({system_npv.maximum}) is above the "
            f"payer sum's maximum ({total.maximum}); allocation may only widen the band."
        )
