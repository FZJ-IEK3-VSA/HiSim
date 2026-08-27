"""Actor model: owner-occupier / landlord / tenant allocation (cost_spec.md §6).

After the timeline is built, an allocation ruleset stamps a payer on every entry (splitting
entries where a law splits them). Rulesets are country-specific modules with data-file
parameters (``hisim/cost_database/allocation_DE_2024.json``).

The question this module answers is the one that decides whether a retrofit actually happens in a
rented building: *who bears which cost*. The owner-occupied case is trivial (one payer), but the
landlord/tenant split is a genuine legal structure — operating costs are apportionable under the
BetrKV, energy is passed through under the Heizkostenverordnung, the carbon-price component is
split by a statutory tier table (CO2KostAufG), and part of the investment can be converted into a
permanent rent increase (§559 BGB). Those rules produce the *landlord-tenant dilemma* the policy
literature is about, and modeling them is what lets the engine report a tenant's warm-rent change
and a landlord's net position instead of one undifferentiated total.

Allocation is a strictly derived view: it is applied by ``evaluator.py`` after the timeline is
complete, only for perspectives whose actor scope is not SYSTEM, and it never changes what the
system spends. That invariant is stated and checked by :func:`assert_zero_sum`. What this module
deliberately does NOT own: cost *amounts* (it only retags and splits existing entries, with the
single exception of the levy transfer pair it mints), discounting, and the legal percentages
themselves — those are data, pending legal review (issues #10).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import FrozenSet, List, Optional, Protocol, Tuple

from hisim.economics.catalog_entries import CostDataError
from hisim.economics.timeline import Actor, CashFlowEntry, CashFlowTimeline, CostCategory
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import ComponentType

#: One pool's levy basis facts: (modernization cost, subsidies received, avoided maintenance).
LevyBasisParts = Tuple[UncertainValue, UncertainValue, UncertainValue]


@dataclass
class ModernizationLevySubjectBasis:
    """One measure's contribution to the §559/§559e levy basis (§6.4).

    §559e BGB charges a *different* percentage for heating measures than §559 does for everything
    else, so the levy basis can no longer be one number: it has to be attributable to the measure
    that produced it. This is that attribution — one record per costed subject, carrying the same
    three figures the aggregate basis carries, plus the `ComponentType` name the classification
    reads. The evaluator fills it while building the timeline; the ruleset decides which paragraph
    each record falls under, so the law stays in ``actors.py`` and the bookkeeping in
    ``evaluator.py``.

    `asset_class_name` is the **enum name** of `loadtypes.ComponentType` (e.g. ``"HEAT_PUMP"``),
    not its value, because that is what the allocation data file lists. It is ``None`` for support
    that belongs to no single measure — the financing repayment grant is the shipped case — and
    such a record can therefore never be classified as a heating measure; its subsidy reduces the
    §559 basis, the conservative direction for the tenant.
    """

    subject: str
    asset_class_name: Optional[str] = None
    modernization_cost_in_euro: UncertainValue = field(default_factory=lambda: UncertainValue.exact(0.0))
    subsidies_received_in_euro: UncertainValue = field(default_factory=lambda: UncertainValue.exact(0.0))
    avoided_maintenance_in_euro: UncertainValue = field(default_factory=lambda: UncertainValue.exact(0.0))


@dataclass
class AllocationContext:
    """Building/tenancy facts the allocation rules need (§6).

    Everything the rules need that is not already on the timeline: the tenancy facts (living area,
    current cold rent) that cap the modernization levy, the building's simulated emission intensity
    that selects the CO2KostAufG tier, and the three levy-basis figures the evaluator computed
    while building the timeline. It is assembled once per perspective in ``evaluator.py`` and
    handed to :meth:`AllocationRuleset.allocate`.

    The optional fields are optional because a run may not know them, and each rule degrades
    explicitly rather than guessing: without an emission intensity the tenant pays the whole carbon
    price, without a living area the levy is uncapped. Units are in the field names; the rent is
    the *cold* rent (Kaltmiete) per m² and month, and it is only ever compared against the levy's
    low-rent threshold, never billed.

    The three levy-basis fields stay the aggregate figures they always were, and `levy_subjects`
    is the same money broken down by measure (§6.4, D27). When the list is empty the aggregates
    *are* the basis and the whole levy is charged under §559 — which is what every caller that
    does not know its measures gets, and what keeps a ruleset without heating data on the
    pre-§559e behavior. When it is non-empty the list is authoritative for the §559/§559e split,
    and :meth:`__post_init__` refuses a context whose breakdown does not add up to its aggregates
    rather than letting a silently truncated list shift money between paragraphs.
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
    #: Per-measure breakdown of the three figures above, for the §559/§559e split (§6.4, D27).
    levy_subjects: List[ModernizationLevySubjectBasis] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Fail fast when the per-measure breakdown contradicts the aggregate levy basis (D7).

        The §559e split reads `levy_subjects` while every other consumer of the levy basis reads
        the aggregates, so a breakdown that lost a measure would quietly move that measure's euros
        from one paragraph to the other — a wrong rent increase reported with no visible symptom.
        Comparing the two representations here is cheap (three slot-wise sums, once per
        perspective) and turns that class of bug into an exception at assembly time.

        Raises:
            ValueError: If the breakdown's slot-wise sums differ from the aggregates by more than
                float-noise tolerance. Empty breakdowns are exempt: they mean "not broken down".
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

    The one seam through which national tenancy law enters the engine. It is a `Protocol` rather
    than a base class because a ruleset needs no shared implementation — only the guarantee that it
    turns a payer-less timeline into a payer-stamped one — and because that keeps rulesets addable
    without touching the evaluator. The legal *percentages* are data files; the *structure* (which
    category is whose, whether a levy exists) is what a ruleset encodes, on the observation that
    the numbers change often and the structure rarely.
    """

    def allocate(self, timeline: CashFlowTimeline, ctx: AllocationContext) -> CashFlowTimeline:
        """Returns a new timeline with payers stamped (and entries split where a law splits them).

        Implementations must return a *new* timeline rather than mutating the input, since the same
        pre-allocation timeline is evaluated under several perspectives. Amounts may be split
        across payers and transfer pairs may be minted, but the money must still add up: see
        :func:`assert_zero_sum` for the exact invariant.
        """
        ...  # pylint: disable=unnecessary-ellipsis


class OwnerOccupierRuleset:
    """The trivial allocation: everything is paid by the owner-occupier.

    The degenerate ruleset for the case where the person paying the investment is the person paying
    the bills, so no split exists at all. It is not a no-op though: stamping the payer explicitly is
    what makes ``npv_by_payer`` well-defined for owner-occupier perspectives and lets the same
    zero-sum check run over every perspective. Because it only retags entries, it satisfies the
    strict per-slot form of the invariant.
    """

    def allocate(self, timeline: CashFlowTimeline, ctx: AllocationContext) -> CashFlowTimeline:  # pylint: disable=unused-argument
        """Everything -> OWNER_OCCUPIER."""
        return CashFlowTimeline(
            entries=[entry.with_payer(Actor.OWNER_OCCUPIER) for entry in timeline.entries],
            validate=timeline.validate,
        )


@dataclass
class ModernizationLevyParameters:
    """Parameterized model of §559/§559e BGB (defaults as of 2024, to be legally verified).

    German law lets a landlord convert part of a modernization investment into a *permanent* rent
    increase — a fixed percentage of the eligible cost per year, capped in euro per m² and month,
    with a lower cap for already-cheap flats. These five numbers are what turns a one-off capital
    expenditure into an annual transfer from tenant to landlord, and they are therefore the single
    most decision-relevant parameter block in the rented case.

    All values are data (``allocation_DE_2024.json``) precisely because they are politically
    volatile; the defaults here are the §559 state as of 2024 and are explicitly pending legal
    review before release. Note that ``duration_in_years = None`` means the increase never ends,
    which is the statutory default and not a missing value — over a 20-year horizon that
    distinction is worth several thousand euro.

    §559e adds a second rate/cap pair for heating modernization (D27): 10 % per year instead of
    8 %, but the resulting rent increase is limited to 0.50 EUR/m²/month on top of — not instead
    of — the general cap. Which measures are "heating" is data too:
    `heating_measure_component_types` holds `loadtypes.ComponentType` **enum names**, so the
    classification can be reviewed against the law in the same file as the percentages. An empty
    set means no measure is a §559e measure, which is the pre-§559e behavior and the default for
    a ruleset constructed without a data file.
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

    What :meth:`DE2024Ruleset.compute_modernization_levy` returns, and the only place the two
    paragraphs are visible separately: the timeline carries their **sum** as one transfer pair
    (see :meth:`DE2024Ruleset.modernization_levy_entries` for why the split is not bookable
    per slot). Tests, worked examples and any future per-paragraph report read the legs here.

    All three fields are annual nominal amounts in euro, already capped. `total_in_euro` is the
    figure that is actually charged and the one every other consumer should read: it is summed
    slot-wise *before* the legs are repaired into valid bands, so with a banded basis whose
    general cap binds in one slot and not another it can differ from `general + heating` by
    exactly that repair. The legs are diagnostics; the total is the money.
    """

    general_levy_in_euro: UncertainValue
    heating_levy_in_euro: UncertainValue
    total_in_euro: UncertainValue


def _ordered_band(slots: dict) -> UncertainValue:
    """Builds a band from three independently computed slot values, restoring `min <= best_estimate <= max`.

    Needed by exactly one caller — the §559e leg split, where the general cap leaves the §559 leg
    a residual room that *shrinks* as the heating leg grows, so the two slot values can come out
    the wrong way round even though every one of them is individually right (§6.4). §3.9 defines
    `minimum` as "best case", not "the LOW world's value", so re-sorting the two outer slots around
    the best estimate is the meaning-preserving repair rather than a fudge.

    Args:
        slots: Mapping with the keys ``minimum``, ``best_estimate`` and ``maximum``.

    Returns:
        The band with `best_estimate` untouched and the outer slots ordered around it.
    """
    best_estimate = slots["best_estimate"]
    outer = (slots["minimum"], slots["maximum"])
    return UncertainValue(
        best_estimate=best_estimate,
        minimum=min(*outer, best_estimate),
        maximum=max(*outer, best_estimate),
    )


def _heating_measure_component_types(raw: List[str], path: str) -> FrozenSet[str]:
    """Validates the §559e classification list against `loadtypes.ComponentType` (§6.4, D7).

    The list decides which measures earn the 10 % rate, so a typo in it would silently move a heat
    pump into the 8 % paragraph and publish a wrong rent increase — the archetype of the data
    error §9.2 refuses to let pass. Every entry is therefore checked against the enum at load time
    and the error names the file, the offending name and the closest legal alternatives, because
    the reader is whoever edited the JSON.

    Args:
        raw: The ``heating_measure_component_types`` list as read from the allocation data file.
        path: Absolute path of that file, quoted in the error message.

    Returns:
        The validated `ComponentType` **names**, as a frozen set (the evaluator reports a
        subject's asset class by name, so names are what the classification compares).

    Raises:
        CostDataError: If the list is not a list of strings, or names a non-`ComponentType`.
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
    """One tier of the CO2KostAufG step function (§6.3).

    One row of the statutory table that decides how the carbon-price component of the heating bill
    is shared: an upper bound on the building's specific emissions and the share the tenant pays
    below it. The logic is deliberately inverted against intuition — the *worse* the building, the
    *less* the tenant pays — because the law puts the carbon cost on whoever can fix the building.

    Tiers are ordered ascending in the data file and read in order; the final tier states
    ``None`` as its bound, meaning open-ended.
    """

    max_emissions_in_kg_per_m2_a: Optional[float]  # None = open-ended top tier
    tenant_share: float  # fraction of the CO2-price component the tenant pays, in [0, 1]


class DE2024Ruleset:
    """German rented-building allocation (BetrKV, HeizKV, CO2KostAufG, §559 BGB) (§6.2).

    The first shipped rented-case ruleset, chosen because Germany is the structurally most complex
    EU regime and therefore the hardest test of the design. It implements four distinct legal
    mechanisms in one pass over the timeline: capital costs stay with the landlord; operating costs
    the BetrKV lists as apportionable (chimney sweep, metering and billing service) and the energy
    bill under the Heizkostenverordnung go to the tenant; the carbon-price component is split by the
    CO2KostAufG tier table; and part of the maintenance and of the investment crosses between the
    two — maintenance by a configurable apportionable share, investment via the §559 modernization
    levy.

    Two class-level frozensets state the category-to-payer mapping so a reviewer can check it
    against the §6.2 table without reading the loop. Anything in neither set and not handled
    explicitly falls to the landlord — the deliberate default, since a new *cost* category is far
    more likely to be a capital cost than an apportionable one, and the alternative (silently
    charging a tenant) is the harmful direction of a mistake. The one category exempted from
    allocation entirely is ``CO2_DAMAGE``: it is a socio-economic shadow cost of the macroeconomic
    view, not a payment anyone makes, so it stays with ``SYSTEM``.
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
        """Parameters default to the shipped data file values when loaded via :meth:`load`.

        Constructing directly is how the non-German fallback and the tests get a ruleset with
        deliberately neutered rules (no levy, no tier table); production code goes through
        :meth:`load` so the legal percentages come from the reviewable data file. Note that an
        empty ``co2_tiers`` list is not an error — :meth:`tenant_co2_share` then charges the whole
        carbon price to the tenant, the pre-2023 situation.
        """
        self.levy = levy or ModernizationLevyParameters()
        self.co2_tiers = co2_tiers or []
        self.maintenance_apportionable_share = maintenance_apportionable_share
        self.apply_modernization_levy = apply_modernization_levy

    @classmethod
    def load(cls, base_path: Optional[str] = None) -> "DE2024Ruleset":
        """Loads parameters from allocation_DE_2024.json (legal percentages are data, §6.1).

        Keeping the rates, caps and the whole CO2 tier table in a sourced data file is what makes
        this ruleset reviewable by a lawyer rather than by a programmer, and what lets a statutory
        change ship as a data diff. The file's ``modernization_levy`` and ``co2_cost_split_tiers``
        blocks are mandatory — a partially specified legal ruleset is refused rather than silently
        completed from the defaults — while ``maintenance_apportionable_share`` falls back to 0.5.
        The §559e fields (``heating_levy_rate_per_year``, ``heating_cap_in_euro_per_m2_per_month``,
        ``heating_measure_component_types``) are mandatory too, since a file that named a heating
        rate without saying which measures earn it is exactly the dangling-parameter state this
        implementation closed (review finding 15, D27).

        Args:
            base_path: Directory holding ``allocation_DE_2024.json``; defaults to the shipped
                ``cost_database``.

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
        """The CO2KostAufG step function of the building's simulated emission intensity (§6.3).

        Looks the building up in the ten-tier statutory table and returns the fraction of the
        carbon-price component the tenant pays: 100 % for an efficient building (below 12 kg
        CO2/m²·a in the shipped table) falling in steps to 5 % for the worst tier. The tiers are
        keyed on the building's **specific emissions in kg CO2 per m² of floor area and year** —
        a property of the building, not of the tenant's behavior — which is the mechanism's whole
        point: the carbon cost lands on whoever can renovate.

        This is where the engine has a real advantage over spreadsheet studies (§6.3): HiSim
        *simulates* the emission intensity, so a retrofit variant can move the building several
        tiers and visibly shift who pays, rather than the split being assumed constant.

        Args:
            emissions_in_kg_per_m2_a: The building's simulated specific emissions, or None if the
                run did not produce them.

        Returns:
            The tenant's share in [0, 1]. Falls back to 1.0 — tenant pays everything, the pre-2023
            situation — when no intensity is known or no tier table is configured; that is the
            conservative direction for a *landlord* decision and is stated rather than silent.
        """
        if emissions_in_kg_per_m2_a is None or not self.co2_tiers:
            return 1.0  # without intensity data the tenant pays (conservative pre-2023 default)
        for tier in self.co2_tiers:
            if tier.max_emissions_in_kg_per_m2_a is None or emissions_in_kg_per_m2_a < tier.max_emissions_in_kg_per_m2_a:
                return tier.tenant_share
        return self.co2_tiers[-1].tenant_share

    def allocate(self, timeline: CashFlowTimeline, ctx: AllocationContext) -> CashFlowTimeline:
        """Stamps payers, splits CO2 costs and maintenance, and adds the modernization levy.

        One pass over the finished timeline, in entry order, producing a new timeline. Most entries
        are simply retagged via the two category sets; two categories are *split* into two entries
        of the same year and category with complementary shares (the carbon price by the
        CO2KostAufG tier, maintenance by the apportionable share), which is why the result can hold
        more entries than the input. Splitting rather than assigning a majority payer is what keeps
        both the tenant's warm-rent view and the landlord's net position exact.

        The levy is appended last and is the only part that *creates* entries rather than
        redistributing existing ones — a tenant-pays / landlord-receives transfer pair per year.
        That is also the only reason the zero-sum invariant needs its envelope form: see
        :func:`assert_zero_sum`.

        Args:
            timeline: The finished, payer-less (SYSTEM) timeline.
            ctx: Tenancy and building facts, plus the levy basis figures.

        Returns:
            A new timeline whose entries carry a payer, in the input's order with split entries
            expanded in place and the levy pairs appended. ``CO2_DAMAGE`` entries are passed
            through untouched and keep the SYSTEM payer.
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
        """True if a measure of this `ComponentType` name is levied under §559e (§6.4, D27).

        The whole classification, in one lookup against the data file's
        ``heating_measure_component_types``. Keeping it a method rather than an inline set test is
        what lets a test assert the shipped list and a future ruleset override it without touching
        the levy arithmetic. A measure with no known asset class (``None``) is never a heating
        measure: unclassifiable money stays under the general paragraph.
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
        """Modernization cost - subsidies - avoided-maintenance share, floored at zero per slot.

        The deduction rule of §559 Abs. 2 BGB, which §559e Abs. 2 repeats verbatim for heating
        measures — which is why it lives here once and is called twice rather than being forked per
        paragraph (D27). Subsidies are deducted as *received nominal* support (§5.6, W3.4), which
        is exactly why they may never be netted against the gross investment upstream: the levy
        needs both figures separately. The avoided-maintenance share is the link back to the
        anyway-cost logic of §4.1.

        Args:
            modernization_cost: Allocatable modernization cost of the pool, nominal euro.
            subsidies: Support received for that pool, as a positive band.
            avoided_maintenance: Maintenance the measure avoided, as a positive band; the
                configured share of it is deducted.

        Returns:
            The levy basis band, floored at zero in every slot so a fully subsidized measure
            yields no levy at all rather than a negative rent.
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
        """The §559 Abs. 3a cap rate in EUR/m²/month, lowered below the low-rent threshold.

        The tiered cap of the general paragraph: 3 EUR/m²/month, or 2 EUR/m²/month where the
        current cold rent is below the statutory threshold. The tier is chosen from the *current*
        rent, not the rent after the increase, and an unknown rent keeps the higher cap — the
        engine does not invent a tenancy fact in the tenant's favor without data.
        """
        rent = ctx.current_cold_rent_in_euro_per_m2_month
        if rent is not None and rent < self.levy.cap_low_rent_threshold_in_euro_per_m2:
            return self.levy.cap_low_rent_in_euro_per_m2_per_month
        return self.levy.cap_in_euro_per_m2_per_month

    def levy_pools(self, ctx: AllocationContext) -> Tuple[LevyBasisParts, LevyBasisParts]:
        """Splits the levy basis facts into the §559e (heating) and §559 (general) pools.

        The one place the classification is applied. With no per-measure breakdown the whole basis
        is general, which is both the pre-§559e behavior and the honest answer when the caller
        cannot say what it built. With a breakdown, each measure's three figures are added to one
        pool or the other, in list order, so the fold order stays observable and reproducible.

        Returns:
            ``(heating, general)``, each a ``(modernization_cost, subsidies, avoided_maintenance)``
            triple of bands.
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
        """The annual rent increase, split into its §559 and §559e legs and nested-capped (§6.4).

        Both paragraphs run the *same* basis rule (:meth:`levy_basis`) and differ only in rate and
        cap: §559 charges 8 %/year of the general pool, §559e 10 %/year of the heating pool. Then
        two caps apply, in this order (D27):

        1. the **heating cap** — the §559e leg alone may not exceed 0.50 EUR/m²/month;
        2. the **general cap** — §559 Abs. 3a's 3 (or 2, below the low-rent threshold)
           EUR/m²/month applies to the *whole* increase, both paragraphs together.

        When the general cap binds, the **non-heating leg is reduced first**. Two reasons, and the
        choice is deliberate: the §559e leg is already individually capped at a small figure, so
        the euros above the general cap are almost always the general leg's — it is the larger
        rate base's natural residual claim — and reducing the heating leg first would hand a
        landlord a *lower* total rent increase for doing the heating measure than for skipping it,
        which inverts the incentive §559e exists to create. The heating leg is only touched when
        it exceeds the general cap on its own, which the shipped parameters never produce
        (0.50 < 2.00 < 3.00) but a future data file could.

        Both caps are applied **per slot**, as §5.4 caps are everywhere in this engine, so a cap
        can bind in the expensive world and not in the cheap one. That has one consequence worth
        stating: the residual room the general cap leaves the §559 leg shrinks as the §559e leg
        grows, so with a banded basis the *legs* can come out slot-reordered even though the total
        cannot. The total is therefore summed slot-wise before the legs are repaired into valid
        bands, and it is the total that is booked (see :meth:`modernization_levy_entries`).

        Args:
            ctx: Levy basis facts, per-measure breakdown, living area and current cold rent.

        Returns:
            The two legs and the total, as annual nominal euro bands. Without a living area no cap
            can be evaluated at all and both legs are returned uncapped, as §6.4 has always done.
        """
        heating_parts, general_parts = self.levy_pools(ctx)
        heating = self.levy_basis(*heating_parts).scale(self.levy.heating_levy_rate_per_year)
        general = self.levy_basis(*general_parts).scale(self.levy.levy_rate_per_year)
        if ctx.living_area_in_m2 is None:
            return ModernizationLevyOutcome(
                general_levy_in_euro=general, heating_levy_in_euro=heating, total_in_euro=general + heating
            )
        months_of_area = 12.0 * ctx.living_area_in_m2
        heating_cap = self.levy.heating_cap_in_euro_per_m2_per_month * months_of_area
        total_cap = self.general_cap_rate(ctx) * months_of_area
        capped = {}
        for slot in ("minimum", "best_estimate", "maximum"):
            heating_slot = min(getattr(heating, slot), heating_cap, total_cap)
            general_slot = min(getattr(general, slot), total_cap - heating_slot)
            capped[slot] = (heating_slot, general_slot)
        return ModernizationLevyOutcome(
            general_levy_in_euro=_ordered_band({slot: values[1] for slot, values in capped.items()}),
            heating_levy_in_euro=_ordered_band({slot: values[0] for slot, values in capped.items()}),
            total_in_euro=UncertainValue(
                minimum=sum(capped["minimum"]), best_estimate=sum(capped["best_estimate"]), maximum=sum(capped["maximum"])
            ),
        )

    def modernization_levy_entries(self, ctx: AllocationContext) -> List[CashFlowEntry]:
        """The §559/§559e BGB levy: TENANT pays a rent increase, LANDLORD receives it (§6.4).

        The mechanism that makes a retrofit financeable in a rented building, and the one place
        this module mints cash flows: :meth:`compute_modernization_levy` turns the levy basis into
        an annual amount, which is booked as a positive TENANT entry and a mirrored negative
        LANDLORD entry for every year of its duration, so the pair nets to zero in the BEST_ESTIMATE slot
        by construction. The amount is booked unchanged in every year: the levy is a fixed nominal
        rent increase and is deliberately not escalated.

        **One pair, not one per paragraph** (D27). The §559 and §559e legs are summed before they
        are booked, even though the split is computed and available on the outcome object. The
        reason is a band property, not laziness: the general cap couples the two legs, so with a
        banded basis a leg can carry a slot-reordered band while their sum cannot — booking the
        legs separately would publish two entries whose slot-wise sum is not the rent increase the
        tenant pays. This is the same representability limit as the transfer-pair mirroring of
        §6.5/B11. Per-paragraph attribution is therefore a *derived* figure, read from
        :meth:`compute_modernization_levy`, and no report may reconstruct it by filtering entries.

        Args:
            ctx: Supplies the levy basis (aggregate and per measure), the living area and current
                rent for the caps, and the horizon.

        Returns:
            Two entries per levy year (tenant leg and mirrored landlord leg) in nominal euro,
            starting in year 1 and running for the levy duration or the horizon, whichever is
            shorter; empty when the levy is zero in every slot. A ``duration_in_years`` of None
            means a permanent increase and is therefore truncated by the horizon.
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
    """Ruleset factory: DE_2024 for rented German buildings, owner-occupier otherwise (§6.1).

    The single place a perspective's actor scope and the run's country turn into an allocation
    ruleset; called by ``evaluator.py`` for every perspective whose scope is not SYSTEM. Keeping
    the choice here means the evaluator never mentions a country-specific rule by name.

    For rented buildings outside Germany there is no ruleset yet, so a generic fallback applies
    (spec Q11): the DE ruleset stripped of everything German — no modernization levy, no CO2 tier
    table (the tenant pays the whole carbon price), and no apportionable maintenance. It is
    structurally honest rather than legally right, and any country with real tenancy rules should
    get its own ruleset rather than lean on it.

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
    # No modernization-levy analogue elsewhere yet: generic EU_SIMPLE fallback (spec Q11) —
    # landlord pays capex/maintenance, tenant pays energy; no levy, no CO2 split table.
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
    """Landlord-tenant allocation moves money between payers; it never creates any (§6.5).

    **Envelope semantics** (decided 2026-08-12, cost-spec-v2 §7 B11; cost_spec.md §6.5 restates
    the invariant). Asserted here:

    1. **BEST_ESTIMATE slot: exact equality**, always — `sum(payer NPVs).best_estimate == system.best_estimate`;
    2. **minimum/maximum: containment**, not equality — the system band must lie *inside* the
       band of the payer sum::

           sum(payer NPVs).minimum <= system.minimum   and   system.maximum <= sum(...).maximum

    Why the min/max slots cannot be asserted equal. A ruleset that *mints* a transfer pair
    (the §6.4 modernization levy) writes the tenant's leg with the levy band ``L`` and the
    landlord's leg with ``L.as_revenue() = (-L.max, -L.best_estimate, -L.min)`` — the mirror is forced by
    the band invariant ``minimum <= best_estimate <= maximum`` on signed amounts (§3.9: "minimum"
    reads *best case*, not *LOW world*), so a slot-coherent transfer entry, which would need
    ``minimum > maximum``, is unrepresentable. The pair therefore sums to
    ``(L.min - L.max, 0, L.max - L.min)`` — zero in BEST_ESTIMATE, non-positive in `minimum`,
    non-negative in `maximum`. Every minted pair widens the payer sum's band symmetrically and
    can only widen it, which is exactly the containment asserted above.

    Pass ``require_per_slot_equality=True`` for rulesets that only *retag* payers (and for
    minting rulesets whose levy basis is a degenerate band): there the widening is zero and the
    stronger per-slot equality must hold.

    This is the correctness property of the whole actor model, and it is a *test* helper rather
    than a runtime guard: the landlord/tenant split is a reallocation of the SYSTEM view, so if the
    payer NPVs did not sum back to the system NPV, some money would have been created or destroyed
    by a bookkeeping rule. The property tests in ``tests/test_economics_invariants.py`` run it over
    randomized timelines and allocation contexts — per-slot equality for the retagging case, the
    envelope form once a levy is minted — which is what keeps a future ruleset from quietly
    breaking it.

    Args:
        system_npv: The pre-allocation SYSTEM NPV.
        payer_npvs: The NPV of each payer after allocation.
        tolerance: Relative float tolerance, scaled by the magnitude of the compared value.
        require_per_slot_equality: Demand exact equality in all three slots instead of containment.

    Raises:
        AssertionError: With the offending slot and both values, when the invariant does not hold.
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
