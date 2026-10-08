"""The economic evaluator: facts -> cash flows -> results (cost_spec.md §3, §4).

`EconomicEvaluator.build_timeline` runs the calculators of `hisim/economics/calculators/` in order to build one
perspective's `CashFlowTimeline`; `evaluate` allocates payers (§6) and discounts and pivots the timeline into a
`LifecycleCostResult` (§3.7). The evaluator is a pure function of `EvaluationInputs` (the plain record stored in
`economic_inputs.json`), the cost database, the subsidy catalog, the economic parameters and the perspective, so an
archived run can be re-priced without HiSim. It also decides the price basis year and whether the extract can be priced
at all (§9.3).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import ClassVar, Dict, FrozenSet, List, Mapping, Optional, Sequence, Set, Tuple

from hisim import log
from hisim.economics.actors import (
    AllocationContext,
    ModernizationLevyOutcome,
    ModernizationLevySubjectBasis,
    get_ruleset,
)
from hisim.economics.calculators.aggregation import (
    aggregate_timeline,
    annual_energy_attribution,
    annual_energy_quantities,
)
from hisim.economics.calculators.annualization import annualize_optional
from hisim.economics.calculators.context_resolution import (
    DeviceCosting,
    installation_verdict,
    resolve_device,
    resolve_replaced_asset,
)
from hisim.economics.calculators.co2 import (
    accumulate_embodied_co2,
    accumulate_operational_emissions,
    build_co2_damage_entries,
    finalize_total_co2,
)
from hisim.economics.calculators.energy import build_energy_flows
from hisim.economics.calculators.financing_application import (
    build_financing_flows,
    compute_year0_net_investment,
    resolve_loan_plan,
)
from hisim.economics.calculators.escalation import (
    carrier_escalation_rate,
    escalation_factor,
    resolve_carrier_escalation_rate,
    resolve_investment_escalation_rate,
)
from hisim.economics.calculators.investment import build_investment_schedule
from hisim.economics.calculators.maintenance import build_maintenance_entries
from hisim.economics.calculators.reserve import build_replacement_reserve_entries
from hisim.economics.calculators.subsidy_application import (
    build_subsidy_flows,
    nominal_support_from_entries,
)
from hisim.economics.carriers import EnergyCarrier, UsefulHeatKind, revenue_subject
from hisim.economics.database import CostDatabase, CostDataError
from hisim.economics.facts import (
    BillingDeterminants,
    ComponentCostFacts,
    ExistingAssetRegister,
    QuotedPurchase,
)
from hisim.economics.parameters import EconomicParameters
from hisim.economics.perspectives import (
    Accounting,
    ActorScope,
    InstallationContext,
    Perspective,
    SubsidyModeKind,
)
from hisim.economics.provenance import ParameterOrigin, ParameterProvenance, ProvenanceLedger, ResolvedSource
from hisim.economics.results import (
    AnywayBasisKinds,
    EconomicAssumptions,
    EmbodiedCo2Basis,
    EvaluationMatrix,
    LifecycleCo2Result,
    LifecycleCostResult,
    ModernizationLevySummary,
    RateOrigin,
    ReferenceAreas,
    ResolvedRate,
    TariffAssumption,
)
from hisim.economics.subsidies import (
    BenefitKind,
    SubsidyCatalog,
    SubsidyContext,
    SubsidyDecision,
    SubsidyPackageContext,
)
from hisim.economics.tariffs import TariffContract
from hisim.economics.timeline import CashFlowEntry, CashFlowTimeline, CostCategory
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import ComponentType
from hisim.postprocessing.kpi_computation.kpi_structure import KpiSource


@dataclass
class SubjectCostFacts:
    """A component's cost facts together with its timeline subject name.

    The subject is the key every cash flow, pivot and export row of this component is filed under: the HiSim component
    name assigned by `bridge.py`, or a free name for subjects that are not simulation components, such as envelope
    measures. Keeping the name here leaves `ComponentCostFacts` without an identity, so the same facts can come from a
    component, a system setup or `economic_inputs.json`.
    """

    subject: str
    facts: ComponentCostFacts


@dataclass
class UnresolvedSubject:
    """A cost subject whose facts the extraction could not establish, carried into the resolution check.

    Examples: a boiler burning a fuel with no asset class, or a meter whose load type maps to no carrier. It is written
    into `economic_inputs.json` and turned into a blocking `ResolutionProblem` by `resolve_check`, so all such subjects
    are reported together with the ones the cost database cannot price.
    """

    subject: str
    #: Why no facts could be extracted, phrased for someone who has to fix the setup.
    reason: str


class _PriceBasisYearWarnings:
    """Warn-once bookkeeping for `effective_price_basis_year`, so a scenario sweep logs each warning only once.

    The seen keys are class-level, so deduplication holds across separately constructed evaluators.
    """

    WARNED: set = set()


def effective_price_basis_year(
    parameters: EconomicParameters,
    database: CostDatabase,
    simulation_year: int,
    plan_start_year: Optional[int] = None,
) -> int:
    """Return the price basis year used for every database lookup (§2.1).

    The price basis year is the year whose prices the cost database is read at. An explicit
    `EconomicParameters.price_basis_year` wins. Otherwise it is `plan_start_year` when given, else the simulation year;
    if the shipped database starts later than that year, its earliest covered year is used and a warning is logged,
    since the database itself refuses uncovered years (§3.5). Only `StagedEvaluator` passes `plan_start_year`. The
    bridge and the re-pricing CLI both call this, so they derive the same year from the same file.

    Args:
        parameters: The assumptions; their `price_basis_year` wins when set.
        database: The cost database, for the earliest year it prices the country's devices at.
        simulation_year: The `simulation_year` of the evaluated inputs.
        plan_start_year: The calendar year a staged plan starts in, or None outside a staged plan or when the plan
            names none.

    Returns:
        The price basis year.
    """
    if parameters.price_basis_year is not None:
        return parameters.price_basis_year
    anchor = plan_start_year if plan_start_year is not None else simulation_year
    earliest = database.earliest_device_year(parameters.country)
    if earliest is None or earliest <= anchor:
        return anchor
    which = "plan start year" if plan_start_year is not None else "simulation year"
    # The anchor's kind is part of the key: a plan start year and a simulation year that happen to
    # be equal are two different statements, and silencing one with the other would hide which.
    key = (parameters.country, which, anchor, earliest)
    if key not in _PriceBasisYearWarnings.WARNED:
        _PriceBasisYearWarnings.WARNED.add(key)
        log.warning(
            f"No device cost data valid at {which} {anchor} for {parameters.country}; "
            f"using price basis year {earliest} (earliest available). Set "
            "EconomicParameters.price_basis_year to override."
        )
    return earliest


@dataclass
class EvaluationInputs:
    """Everything the pure evaluator needs about one simulated variant, serialized to `economic_inputs.json` (§4.6).

    Plain data only, with no component objects or prices, so an archived run can be re-priced under new assumptions
    without re-running the simulation. `bridge.py` fills it from a finished simulation and the setup's
    `EconomicContext`; `serialization.py` round-trips it. It is written before the resolution check, so it stays
    complete even when nothing in it can be priced.

    `cost_facts` lists every priced subject, including non-components such as envelope measures; `billing` has one
    record per meter, i.e. per carrier flow across the system boundary (§3.4); `existing_assets` None means greenfield,
    and its presence enables the brownfield and status-quo perspectives (§4.1); `subsidy_context` holds the applicant
    and building answers the eligibility conditions read, where an unanswered field stays undetermined (§5.7);
    `unresolved_subjects` lists components the extraction could not describe. Energy quantities are as simulated; the
    engine annualizes them with `simulated_period_fraction`.
    """

    simulation_year: int
    simulated_period_fraction: float  # simulated seconds / seconds of a full year
    cost_facts: List[SubjectCostFacts] = field(default_factory=list)
    billing: List[BillingDeterminants] = field(default_factory=list)
    # Subjects the extraction recognized but could not describe; they block the resolution
    # check exactly like a subject the cost database cannot price.
    unresolved_subjects: List[UnresolvedSubject] = field(default_factory=list)
    #: Subject -> energy-balance role (`EnergyFlowRole.value`) -> energy of the simulated period
    #: in kWh, as positive magnitudes (the role says the direction). Filled from the columns the
    #: adapter's `DeviceEnergySpecs` names; never priced, only annualized onto the result for the
    #: household energy balance. Older files load with an empty map.
    energy_attribution_by_subject_in_kwh: Dict[str, Dict[str, float]] = field(default_factory=dict)
    existing_assets: Optional[ExistingAssetRegister] = None
    subsidy_context: SubsidyContext = field(default_factory=SubsidyContext)
    tariff_contracts: Dict[EnergyCarrier, TariffContract] = field(default_factory=dict)
    # Tariff ids whose price signal a controller consumed during the run (§4.6 boundary):
    consumed_tariff_ids: List[str] = field(default_factory=list)
    # For the system cost per unit of heat: a figure the setup declared for a whole year, and the
    # useful heat the simulation measured over its own period (the building's room-heating demand
    # plus the hot water drawn, `adapter.UsefulHeatSources`). `annual_heat_demand()` picks one.
    annual_heat_demand_in_kwh: Optional[float] = None
    # The measured total, None when the run lists no source or its heat sums to zero, and the same
    # heat split by `UsefulHeatKind` value, one entry per kind the run has a source of (a zero
    # included). A file written before the split existed loads with an empty map.
    useful_heat_of_simulated_period_in_kwh: Optional[float] = None
    useful_heat_of_simulated_period_by_kind_in_kwh: Dict[str, float] = field(default_factory=dict)
    # Building context for the actor model (§6.3, §6.4):
    building_specific_emissions_in_kg_per_m2_a: Optional[float] = None
    heated_floor_area_in_m2: Optional[float] = None
    living_area_in_m2: Optional[float] = None
    current_cold_rent_in_euro_per_m2_month: Optional[float] = None
    #: Component name -> KPI source of every component the run simulated; tells a subject that is
    #: a HiSim component from one that is not (envelope measure, carrier, synthetic subject).
    #: ``{}`` means no simulated components (inputs built in code), so every subject is a
    #: non-component. ``None`` means unknown: the inputs came from an older ``economic_inputs.json``
    #: without the field, and a reader that needs the sources (``StagedDocument``) refuses it.
    component_sources: Optional[Dict[str, KpiSource]] = field(default_factory=dict)

    def annual_heat_demand(self) -> Optional[float]:
        """Return the annual heat in kWh the levelized cost of heat divides by, or None when nothing states it.

        A heat demand declared in `EconomicContext` wins. Otherwise the useful heat the simulation measured (rooms plus
        hot water) is annualized with `simulated_period_fraction`, like the energy bills, through `annualize_optional`.
        A non-positive fraction yields None; the energy calculator refuses such a record anyway.
        """
        if self.annual_heat_demand_in_kwh is not None:
            return self.annual_heat_demand_in_kwh
        if self.simulated_period_fraction <= 0:
            return None
        return annualize_optional(self.useful_heat_of_simulated_period_in_kwh, self.simulated_period_fraction)

    def heat_cost_omits_hot_water(self) -> bool:
        """Return True when the heat-cost figure divides by the rooms' measured heat and no hot water.

        That is a run with a building, no hot-water source listed in `adapter.UsefulHeatSources`, and no declared
        demand. The costs still pay for heating the water, so the heat cost reads too high by the hot water's share;
        the bridge logs it and the plausibility panel reports it.
        """
        by_kind = self.useful_heat_of_simulated_period_by_kind_in_kwh
        return (
            self.annual_heat_demand_in_kwh is None
            and self.useful_heat_of_simulated_period_in_kwh is not None
            and UsefulHeatKind.ROOM_HEATING.value in by_kind
            and UsefulHeatKind.HOT_WATER.value not in by_kind
        )


@dataclass(frozen=True)
class ResolutionProblem:
    """One problem found by `EconomicEvaluator.resolve_check` (§9.3).

    `subject` is the cost-facts subject the problem belongs to, or None for problems that are not per subject (e.g. a
    missing carrier price), so consumers can act per subject without matching message text.
    """

    message: str
    subject: Optional[str] = None
    kind: str = ""
    #: Whether the subject cannot be priced at all (as opposed to a documentation defect).
    blocks_evaluation: bool = True

    def __str__(self) -> str:
        """Return the message, so log formatting reads as with plain strings."""
        return self.message


class UnresolvableSubjectsError(CostDataError):
    """Raised when part of the extract cannot be priced; no partial cost results are produced (§8).

    Raised in the postprocessing bridge and in the CLI alike, after `economic_inputs.json` is written, so the extract
    can still be inspected or re-priced against a fixed database. `problems` holds the structured `ResolutionProblem`s.
    """

    def __init__(self, problems: Sequence[ResolutionProblem]) -> None:
        """Render one bullet per blocked subject; blockers without a subject are labelled by kind."""
        self.problems: Tuple[ResolutionProblem, ...] = tuple(problems)
        bullets = "\n".join(
            f"  - {problem.subject if problem.subject is not None else '<' + (problem.kind or 'unscoped') + '>'}"
            f": {problem.message}"
            for problem in self.problems
        )
        super().__init__(
            f"Lifecycle cost engine: {len(self.problems)} unresolvable cost subject(s) — evaluation "
            "aborted, no partial cost results are produced (cost-spec-v2 §8, D7). "
            "economic_inputs.json holds the complete simulation extract and is unaffected.\n"
            + bullets
        )


def require_resolvable_subjects(inputs: EvaluationInputs, evaluator: "EconomicEvaluator") -> None:
    """Raise if the cost database cannot price part of the extract; return silently otherwise.

    Every consumer of `economic_inputs.json` runs this before evaluating. Non-blocking problems (e.g. an override
    without `override_source`) only warn. Blockers without a subject, such as a missing energy price for a billed
    carrier, are included. The inputs are not modified.

    Args:
        inputs: The simulation extract to check.
        evaluator: Its cost database, country and price basis year decide what is resolvable; only `resolve_check` is
            called.

    Raises:
        UnresolvableSubjectsError: If any blocking problem exists; it lists the first reason per blocked subject plus
            every blocker without a subject. The CLI turns it into exit code 2.
    """
    problems = evaluator.resolve_check(inputs, strict=False)
    for problem in problems:
        log.warning(f"Lifecycle cost engine resolution check: {problem}")
    blocking: List[ResolutionProblem] = []
    seen_subjects: Set[str] = set()
    for problem in problems:
        if not problem.blocks_evaluation:
            continue
        if problem.subject is not None:
            if problem.subject in seen_subjects:
                continue  # one bullet per subject, the first reason found
            seen_subjects.add(problem.subject)
        blocking.append(problem)
    if blocking:
        raise UnresolvableSubjectsError(blocking)


@dataclass
class ModernizationLevyBasis:
    """The parts the §6.4 modernization levy is computed from, as nominal, undiscounted euro bands.

    The modernization levy is the share of a landlord's modernization cost that may be passed on to the tenant as a
    rent increase (§559 BGB). `subsidies` is the sum of every SUBSIDY entry on the finished timeline, since §559
    deducts the support actually received. `by_subject` attributes the same money to the measure that produced it,
    because §559e levies heating measures at a different rate and cap than envelope measures; support belonging to no
    single measure (the financing repayment grant) has a record with asset class None, so `by_subject` always adds up
    to the aggregates, which `AllocationContext` checks.
    """

    modernization_cost: UncertainValue
    subsidies: UncertainValue
    avoided_maintenance: UncertainValue
    by_subject: List[ModernizationLevySubjectBasis] = field(default_factory=list)


def _levy_basis_by_subject(
    timeline: CashFlowTimeline,
    asset_classes: Dict[str, str],
    modernization_cost: Dict[str, UncertainValue],
    avoided_maintenance: Dict[str, UncertainValue],
) -> List[ModernizationLevySubjectBasis]:
    """Split the §6.4 levy basis into one record per measure.

    Modernization cost and the anyway credit are already per measure; support received is read from the finished
    timeline's SUBSIDY entries, grouped by subject, so it includes the financing repayment grant. Support whose subject
    is not a costed measure gets its own record with asset class None, so the records add up to the aggregate exactly.

    Args:
        timeline: The finished, pre-allocation timeline; read only.
        asset_classes: `ComponentType` name per costed subject.
        modernization_cost: Allocatable modernization cost per costed subject.
        avoided_maintenance: Anyway credit per costed subject (the avoided cost of a replacement that was due anyway).

    Returns:
        One record per costed subject in costing order, then one per unattributed support subject in timeline order.
    """
    signed_subsidies: Dict[str, UncertainValue] = {}
    for entry in timeline.entries:
        if entry.category != CostCategory.SUBSIDY:
            continue
        running = signed_subsidies.get(entry.subject, UncertainValue.exact(0.0))
        signed_subsidies[entry.subject] = running + entry.amount_in_euro
    zero = UncertainValue.exact(0.0)
    records = [
        ModernizationLevySubjectBasis(
            subject=subject,
            asset_class_name=asset_classes.get(subject),
            modernization_cost_in_euro=cost,
            subsidies_received_in_euro=signed_subsidies.pop(subject, zero).as_revenue(),
            avoided_maintenance_in_euro=avoided_maintenance.get(subject, zero),
        )
        for subject, cost in modernization_cost.items()
    ]
    records.extend(
        ModernizationLevySubjectBasis(subject=subject, subsidies_received_in_euro=signed.as_revenue())
        for subject, signed in signed_subsidies.items()
    )
    return records


@dataclass
class TimelineBuildResult:
    """What one timeline build produces: the timeline plus four outputs that are not cash flows.

    The four are the subsidy `decisions`, the CO2 mass accounting, the written-off `sunk_cost` and the
    modernization-levy `basis`. Internal; `evaluate` is the only consumer.
    """

    timeline: CashFlowTimeline
    decisions: List[SubsidyDecision]
    co2_result: LifecycleCo2Result
    sunk_cost: UncertainValue
    basis: ModernizationLevyBasis
    #: Per-carrier flexibility value before the §8.5 clamp, for the plausibility panel (#25b).
    raw_flexibility_value_by_carrier: Dict[str, float] = field(default_factory=dict)
    #: The Sowieso share every booked anyway credit was computed at (subject -> share), for the
    #: result the report reads it from.
    anyway_share_by_subject: Dict[str, float] = field(default_factory=dict)
    #: The cost each of those credits was computed *on* (subject -> euro).
    anyway_basis_by_subject: Dict[str, float] = field(default_factory=dict)
    #: What that basis is on the branch that produced it (subject -> `AnywayBasisKinds`).
    anyway_basis_kind_by_subject: Dict[str, str] = field(default_factory=dict)
    #: The tariff contracts the energy calculator actually billed under, for the assumptions
    #: record the report's assumptions section publishes.
    tariffs_applied: List[TariffContract] = field(default_factory=list)
    #: Every scheduled replacement as ``(subject, year, nominal escalated amount)``, in the order
    #: the subjects were processed. The same flows the OPERATING_ONLY reserve is levelized from,
    #: carried *per subject* because the staged evaluator re-dates them to the year the stage that
    #: bought the subject starts in and has to know whose flow each one is. Collected under every
    #: perspective, including the ones that carry the REPLACEMENT entries themselves.
    replacement_flows: List[Tuple[str, int, UncertainValue]] = field(default_factory=list)


@dataclass
class YearZeroPriceLevel:
    """Moves one timeline from the price basis year's money into the money of its year 0.

    Prices are read at the price basis year. When a staged plan's year 0 (`plan_start_year`) is another calendar year,
    every amount is multiplied by `(1 + r)**years`, with `years = year 0 - price basis year` and `r` the rate that
    amount escalates with in later years:

    - year-0 purchase, its replacements, its residual value and a coupled-cost anyway credit: the subject's investment
      rate;
    - a like-for-like anyway credit: the replaced asset's investment rate;
    - maintenance, fixed operation and the standing charge: the general rate; the capacity charge: the grid-fee rate;
      the working price: the carrier rate on the volume and the spread rate on the flexibility correction; feed-in
      revenue: the feed-in rate once its fixed-price period is over;
    - the CO2 price: the price path is read `years` later; the CO2 damage cost: unchanged.

    Not shifted: a stated purchase price (a reader's quote) and the residual value and credit computed from it.
    Subsidies are not shifted here; the solver already gets the cost in year-0 money (`purchase`), so a share-of-cost
    grant follows it and a fixed amount is clamped to the year-0 cost. `years` may be negative (de-escalation); `years
    == 0` changes nothing. The loan is computed afterwards on the shifted net investment.

    Attributes:
        years: The shift, `year 0 - price basis year`; 0 leaves every amount as it is.
        parameters: The general, grid-fee, spread and feed-in rates.
        database: For the carrier rates, resolved by the energy calculator's own chain.
        price_basis_year: Where the CO2 price path is read for year 1.
        subject_rates: Subject -> its investment escalation rate.
        credit_factors: Subject -> the factor its anyway credit (and the credit's basis) is shifted by.
        stated: Subjects whose year-0 purchase is a stated price, never shifted.
        stated_residuals: Subjects whose residual value is written down from that stated price.
        carriers: Carrier subject -> (carrier rate, spread rate, flexibility value in euro).
        feed_in: Revenue subject -> (feed-in rate, fixed-price duration in years).
    """

    #: Benefit kinds that pay a fixed nominal amount (in total, per unit of size, or per kWh): a
    #: grant of EUR 6,500 is EUR 6,500 in whichever year it is paid. The other kinds are a share
    #: of a cost and follow it. Nothing branches on the list: the rule follows from valuing every
    #: subsidy on the cost in the money of the year it is booked.
    FIXED_AMOUNT_BENEFITS: ClassVar[FrozenSet[BenefitKind]] = frozenset(
        {BenefitKind.LUMP_SUM, BenefitKind.PER_UNIT, BenefitKind.TIERED_PER_UNIT, BenefitKind.OPERATIONAL}
    )

    years: int
    parameters: EconomicParameters
    database: CostDatabase
    price_basis_year: int
    subject_rates: Dict[str, float] = field(default_factory=dict)
    credit_factors: Dict[str, float] = field(default_factory=dict)
    stated: Set[str] = field(default_factory=set)
    stated_residuals: Set[str] = field(default_factory=set)
    carriers: Dict[str, Tuple[float, float, float]] = field(default_factory=dict)
    feed_in: Dict[str, Tuple[float, int]] = field(default_factory=dict)

    @property
    def shifts(self) -> bool:
        """Whether any amount moves at all; False keeps the timeline bit-identical."""
        return self.years != 0

    def factor(self, rate: float) -> float:
        """``(1 + rate)**years``: one rate's price level of year 0 relative to the price basis year."""
        return escalation_factor(rate, self.years)

    def purchase(self, subject: str) -> float:
        """The factor of one subject's year-0 purchase and of what is a share of it."""
        if subject in self.stated:
            return 1.0
        return self.factor(self.subject_rates[subject])

    def add_subject(self, costing: DeviceCosting, asset_rate: float, replaced: bool) -> None:
        """Record one costed subject: its rate and whether its purchase is a stated price.

        Args:
            costing: The subject's resolved costing.
            asset_rate: Its investment escalation rate.
            replaced: Whether its investment schedule re-buys it inside the horizon.
        """
        self.subject_rates[costing.subject] = asset_rate
        if costing.purchase_override is not None and costing.is_new_investment:
            self.stated.add(costing.subject)
            if not replaced:
                self.stated_residuals.add(costing.subject)

    def add_credit(self, subject: str, like_for_like: bool, replaced_rate: float) -> float:
        """Record the factor of one subject's anyway credit and return it.

        Args:
            subject: The measure's subject.
            like_for_like: Whether the credit is the replaced asset's own escalated price rather
                than the coupled-cost share of the measure.
            replaced_rate: The replaced asset's investment rate, used on the like-for-like branch.

        Returns:
            The factor.
        """
        factor = self.factor(replaced_rate) if like_for_like else self.purchase(subject)
        self.credit_factors[subject] = factor
        return factor

    def add_energy(
        self, contracts: Sequence[TariffContract], flexibility_by_carrier: Mapping[str, float]
    ) -> None:
        """Record every billed carrier's rates the way the energy calculator resolved them.

        Args:
            contracts: The contract each carrier was billed under, in billing order.
            flexibility_by_carrier: The raw flexibility value per carrier (clamped at 0 here, as the
                calculator clamps it).
        """
        params = self.parameters
        for contract in contracts:
            carrier = contract.carrier
            rate = carrier_escalation_rate(carrier, params, self.database)
            spread = params.spread_escalation_rate if params.spread_escalation_rate is not None else rate
            flexibility = max(0.0, flexibility_by_carrier.get(carrier.value, 0.0))
            self.carriers[carrier.value] = (rate, spread, flexibility)
            duration = contract.feed_in.duration_in_years
            self.feed_in[revenue_subject(carrier)] = (params.feed_in_escalation_rate, duration)

    def timeline(self, timeline: CashFlowTimeline) -> CashFlowTimeline:
        """The timeline with every entry in year-0 money; the same object when nothing shifts."""
        if not self.shifts:
            return timeline
        shifted = CashFlowTimeline(validate=timeline.validate)
        shifted.extend(self.entry(entry) for entry in timeline.entries)
        return shifted

    def entry(self, entry: CashFlowEntry) -> CashFlowEntry:
        """Return one entry in year-0 money, scaled by the rate its category escalates with (see the class).

        Raises:
            ValueError: For a category the shift does not handle (loan, levy, replacement reserve); those are built
                after the shift, so meeting one here is a defect.
        """
        category, subject, params = entry.category, entry.subject, self.parameters
        if category in (CostCategory.INVESTMENT, CostCategory.PLANNING, CostCategory.REMOVAL):
            factor = self.purchase(subject)
        elif category is CostCategory.REPLACEMENT:
            factor = self.factor(self.subject_rates[subject])
        elif category is CostCategory.RESIDUAL_VALUE:
            factor = 1.0 if subject in self.stated_residuals else self.factor(self.subject_rates[subject])
        elif category is CostCategory.ANYWAY_COST_CREDIT:
            factor = self.credit_factors[subject]
        elif category is CostCategory.SUBSIDY:
            # Valued on the year-0 cost already (``build_subsidy_flows(cost_factor=...)``).
            factor = 1.0
        elif category in (CostCategory.MAINTENANCE, CostCategory.FIXED_OPERATION, CostCategory.ENERGY_STANDING):
            factor = self.factor(params.general_price_escalation_rate)
        elif category is CostCategory.ENERGY_CAPACITY_CHARGE:
            grid = params.grid_fee_escalation_rate
            factor = self.factor(grid if grid is not None else params.general_price_escalation_rate)
        elif category is CostCategory.ENERGY_WORKING:
            return self._working(entry)
        elif category is CostCategory.FEED_IN_REVENUE:
            rate, duration = self.feed_in[subject]
            factor = 1.0 if entry.year <= duration else self.factor(rate)
        elif category is CostCategory.ENERGY_CO2_PRICE:
            factor = self._co2_price_factor(entry.year)
        elif category is CostCategory.CO2_DAMAGE:
            factor = 1.0
        else:
            raise ValueError(f"{category.value} is not shifted to year-0 money: it is built after the shift")
        return replace(entry, amount_in_euro=entry.amount_in_euro.scale(factor))

    def _working(self, entry: CashFlowEntry) -> CashFlowEntry:
        """Shift the working-price entry: the volume part at the carrier rate, the correction at the spread rate.

        The calculator books `V (1+c)**(t-1) - F (1+s)**(t-1)`; shifted by `d` years, that is `(1+c)**d` times the
        entry plus `F (1+s)**(t-1) ((1+c)**d - (1+s)**d)`.
        """
        rate, spread, flexibility = self.carriers[entry.subject]
        amount = entry.amount_in_euro.scale(self.factor(rate))
        if flexibility:
            correction = flexibility * escalation_factor(spread, entry.year - 1)
            amount = amount + UncertainValue.exact(correction * (self.factor(rate) - self.factor(spread)))
        return replace(entry, amount_in_euro=amount)

    def _co2_price_factor(self, year: int) -> float:
        """The CO2 path read ``years`` later than the calculator read it for plan year ``year``."""
        path = self.database.get_co2_price_path(self.parameters.country, self.parameters.co2_price_scenario)
        if path is None:
            return 1.0
        read = path.price(self.price_basis_year + year - 1)
        return path.price(self.price_basis_year + self.years + year - 1) / read if read else 1.0


def _levy_summary(outcome: Optional[ModernizationLevyOutcome]) -> Optional[ModernizationLevySummary]:
    """Return the result-side summary of a ruleset's levy outcome, or None when there is no levy.

    Keeps what a reader needs beside the levy amount: the two legs, whether a ceiling decided the figure and which
    mechanism decided it in each band slot. A ruleset without a levy and a levy of zero both yield None.

    Args:
        outcome: What `AllocationRuleset.modernization_levy_outcome` returned.

    Returns:
        The record for `LifecycleCostResult.modernization_levy`, or None.
    """
    if outcome is None or outcome.total_in_euro.maximum <= 0:
        return None
    return ModernizationLevySummary(
        annual_amount_in_euro=outcome.total_in_euro,
        general_leg_in_euro=outcome.general_levy_in_euro,
        heating_leg_in_euro=outcome.heating_levy_in_euro,
        cap_in_euro_per_m2_per_month=outcome.cap_in_euro_per_m2_per_month,
        # Which mechanism set the levy in each of the three worlds. Copied through as one shape:
        # both sides key by `Slot`, so this is a copy rather than a translation, and "did a
        # ceiling decide the headline figure" is derived from it on the summary.
        binding_mechanism_by_slot=dict(outcome.binding_mechanism_by_slot),
    )


class EconomicEvaluator:
    """Builds the canonical cash flow timeline and evaluates perspectives against it; the core of the cost engine.

    A pure function: from an `EvaluationInputs` extract, a `CostDatabase`, an optional `SubsidyCatalog`,
    `EconomicParameters` and one `Perspective` (a named combination of installation context, actor scope, subsidy mode,
    financing and accounting, §4) it yields a `LifecycleCostResult`, reading no simulation object and no file. So an
    evaluation can be reproduced from `economic_inputs.json` alone, and scenario sweeps stay cheap.

    `build_timeline` composes the calculators into one timeline; `evaluate` lets the §6 allocation ruleset assign a
    payer to every entry and aggregates the timeline into every published figure, so totals and pivots reconcile by
    construction (§3.1). Every amount is an `UncertainValue` band with LOW, BEST_ESTIMATE and HIGH slots (§3.9),
    computed slot-wise in one pass; choices such as the subsidy combination (§5.4) are made on BEST_ESTIMATE and valued
    in all three slots. Callers: `bridge.py`, `scenarios.py` and the `python -m hisim.economics` CLI.
    """

    def __init__(
        self,
        cost_database: CostDatabase,
        parameters: EconomicParameters,
        subsidy_catalog: Optional[SubsidyCatalog] = None,
        plan_year_zero: Optional[int] = None,
        book_anyway_credit: bool = True,
    ) -> None:
        """Hold the database, parameters and optional subsidy catalog; without a catalog no subsidy is booked.

        The arguments are never modified, so an evaluator is a cheap reusable handle; `scenarios.evaluate_cube` builds
        one per scenario cell.

        Args:
            cost_database: Device, energy price and default-rate data.
            parameters: The economic assumptions.
            subsidy_catalog: The subsidy schemes, or None for no subsidies.
            plan_year_zero: The calendar year of the timeline's year 0, at which register assets are aged and whose
                money the timeline is in (`YearZeroPriceLevel`). Only the staged evaluator sets it; None ages and
                prices at the price basis year.
            book_anyway_credit: Whether the anyway credit of a replaced asset (§4.1; the avoided cost of a replacement
                that was due anyway) is booked as an `ANYWAY_COST_CREDIT` flow. The staged evaluator turns it off
                because its reference already pays every renewal; the credit still enters the modernization-levy basis.
        """
        self.database = cost_database
        self.book_anyway_credit = book_anyway_credit
        self.parameters = parameters
        self.subsidy_catalog = subsidy_catalog
        self._plan_year_zero = plan_year_zero

    # ------------------------------------------------------------------ rate resolution

    def carrier_escalation_rate(self, carrier: EnergyCarrier) -> float:
        """Return a carrier's nominal annual price escalation rate (§3.2).

        Fallback chain: explicit parameter, then the country defaults file, then the general rate; delegated to
        `calculators/escalation.py`. The energy calculator escalates a carrier's year-1 bill with it (§3.6 rule 5).
        """
        return resolve_carrier_escalation_rate(carrier, self.parameters, self.database).rate

    def investment_escalation_rate(self, asset_class: ComponentType) -> float:
        """Return an asset class's annual investment price escalation rate (§3.2).

        Same fallback chain as `carrier_escalation_rate`. May be negative (PV and batteries get cheaper). Used for
        replacements and the residual value (§3.6 rules 2-3).
        """
        return resolve_investment_escalation_rate(asset_class, self.parameters, self.database).rate

    def price_basis_year(self, inputs: EvaluationInputs) -> int:
        """Return the price basis year for database lookups (see `effective_price_basis_year`).

        Every device entry, energy price and asset age in one evaluation is resolved against this year, which need not
        be the simulated weather year.
        """
        return effective_price_basis_year(self.parameters, self.database, inputs.simulation_year)

    def _year_zero(self, price_basis_year: int) -> int:
        """Return the calendar year of the timeline's year 0.

        That is the staged plan's year 0 if the evaluator was given one, else the price basis year. Register assets are
        aged at this year and the timeline is in its money; `year 0 - price basis year` is the shift
        `YearZeroPriceLevel` applies.
        """
        return price_basis_year if self._plan_year_zero is None else self._plan_year_zero

    def effective_parameters(self, inputs: EvaluationInputs) -> EconomicParameters:
        """Return the parameters as used, with the resolved price basis year filled in.

        Reports read the basis year off the result, so the caller's parameters are not modified.
        """
        if self.parameters.price_basis_year is not None:
            return self.parameters
        return replace(self.parameters, price_basis_year=self.price_basis_year(inputs))

    # ------------------------------------------------------------------ pre-run resolution check (§9.3)

    def resolve_check(self, inputs: EvaluationInputs, strict: bool = True) -> List[ResolutionProblem]:
        """Dry-resolve every declared fact against the database and return the problems found (§3.10, §9.3).

        Blocking problems (`blocks_evaluation`): subjects the extraction already gave up on
        (`inputs.unresolved_subjects`, reported first and verbatim), a missing device entry, a `size_unit` that
        disagrees with the entry's `per_unit`, and a missing energy price for a billed carrier. Non-blocking: a cost
        override without `override_source`. Facts that override both investment cost and lifetime skip the device
        checks.

        Args:
            inputs: The extract to check; never modified.
            strict: If True, the override-without-source problem is returned (non-blocking); if False, it is only
                logged.

        Returns:
            All problems in subject order, then carrier order; empty when everything resolves.
        """
        problems: List[ResolutionProblem] = []
        year = self.price_basis_year(inputs)
        for unresolved in inputs.unresolved_subjects:
            problems.append(
                ResolutionProblem(
                    message=f"{unresolved.subject}: {unresolved.reason}",
                    subject=unresolved.subject,
                    kind="extraction_yielded_no_facts",
                )
            )
        for subject_facts in inputs.cost_facts:
            facts = subject_facts.facts
            if facts.has_overrides() and not facts.override_source:
                message = (
                    f"{subject_facts.subject}: cost overrides set without override_source (§3.10)."
                )
                if strict:
                    problems.append(
                        ResolutionProblem(
                            message=message,
                            subject=subject_facts.subject,
                            kind="override_without_source",
                            blocks_evaluation=False,  # documentation defect, the facts still price
                        )
                    )
                else:
                    log.warning(message)
            if facts.investment_cost_override_in_euro is not None and facts.lifetime_override_in_years is not None:
                continue  # fully overridden facts need no database entry
            try:
                entry = self.database.get_device_entry(facts.asset_class, year, self.parameters.country)
            except CostDataError as err:
                problems.append(
                    ResolutionProblem(
                        message=str(err), subject=subject_facts.subject, kind="missing_device_entry"
                    )
                )
                continue
            if entry.size_unit != facts.size_unit:
                problems.append(
                    ResolutionProblem(
                        message=(
                            f"{subject_facts.subject}: declared size_unit {facts.size_unit.value!r} does not "
                            f"match the database entry's per_unit ({entry.per_unit!r})."
                        ),
                        subject=subject_facts.subject,
                        kind="size_unit_mismatch",
                    )
                )
        for determinants in inputs.billing:
            if not self.database.has_energy_price(determinants.carrier, self.parameters.country):
                problems.append(
                    ResolutionProblem(
                        message=(
                            f"No energy price entry for carrier {determinants.carrier.value} in "
                            f"{self.parameters.country}."
                        ),
                        kind="missing_energy_price",
                    )
                )
        return problems

    # ------------------------------------------------------------------ timeline construction (§3.6)

    def build_timeline(
        self,
        inputs: EvaluationInputs,
        perspective: Perspective,
        ledger: ProvenanceLedger,
        quoted_purchases: Sequence[QuotedPurchase] = (),
        booked_price_levels: Mapping[str, float] = MappingProxyType({}),
    ) -> TimelineBuildResult:
        """Build the canonical timeline for one perspective, plus its non-cash outputs.

        The calculators run in this order: per subject, context resolution -> investment schedule -> replaced-asset
        outcome -> maintenance -> subsidies; then energy bills per carrier; the replacement reserve; the macroeconomic
        CO2 damage; financing. Entry order is observable (float sums fold in insertion order), so the accumulators stay
        here. The constraints:

        - Subsidies before financing: the loan principal is the year-0 investment net of upfront grants (§4.4).
        - The anyway credit is emitted between the schedule's year-0 entries and its replacement and residual entries.
        - The replacement reserve needs every subject's replacement flows (§4.2), so it runs after the subject loop.
        - CO2 damage prices the operational emissions of the energy bills (§4.5), so it runs after them.
        - The levy basis is read off the finished timeline, so a loan's repayment grant counts as support (§6.4).

        Args:
            inputs: The variant's extract; never modified.
            perspective: Supplies the installation context, the accounting mode (macroeconomic suppresses subsidies and
                adds CO2 damage, §4.5), the subsidy mode (§5.5) and the optional financing plan.
            ledger: Provenance ledger; mutated: every database lookup records itself here, which
                `LifecycleCostResult.explain` reads (§3.10).
            quoted_purchases: Purchases with no cost facts, priced by a stated amount (`facts.QuotedPurchase`): one
                year-0 INVESTMENT entry each, included in the levy basis and before financing. Empty except for a
                staged plan.
            booked_price_levels: Subject -> the price level, relative to year 0, at which the caller books its year-0
                purchase (a later stage's purchase is booked in the stage's year). Subsidies and the levy basis are
                valued at that level. Missing or 1.0 means year-0 money. Empty except for a staged plan.

        Returns:
            A `TimelineBuildResult`: the timeline in nominal, undiscounted euro bands, plus the subsidy decisions, the
                CO2 mass accounting, the written-off sunk cost and the levy basis.
        """
        params = self.parameters
        price_basis_year = self.price_basis_year(inputs)
        ageing_reference_year = self._year_zero(price_basis_year)
        horizon = params.observation_period_in_years
        timeline = CashFlowTimeline()
        co2_result = LifecycleCo2Result(operational_co2_by_year_in_kg=[0.0] * (horizon + 1))
        decisions: List[SubsidyDecision] = []
        sunk_cost = UncertainValue.exact(0.0)
        modernization_cost = UncertainValue.exact(0.0)
        anyway_credit_total = UncertainValue.exact(0.0)
        anyway_share_by_subject: Dict[str, float] = {}
        anyway_basis_by_subject: Dict[str, float] = {}
        anyway_basis_kind_by_subject: Dict[str, str] = {}
        # Per-measure levy basis for the §559/§559e split (§6.4): asset class, modernization
        # cost and anyway credit per subject; the subsidy leg is read off the finished timeline.
        levy_asset_classes: Dict[str, str] = {}
        levy_cost_by_subject: Dict[str, UncertainValue] = {}
        levy_credit_by_subject: Dict[str, UncertainValue] = {}
        context = perspective.installation_context
        include_investment = context != InstallationContext.OPERATING_ONLY
        macro = perspective.accounting == Accounting.MACROECONOMIC

        replacement_flows_for_reserve: List[Tuple[int, UncertainValue]] = []
        replacement_flows_by_subject: List[Tuple[str, int, UncertainValue]] = []
        subsidy_context = self._with_package(inputs, context)
        # Year 0 in the money of its own calendar year: every amount below is computed at the
        # price basis year, the side figures are shifted as they are summed, and the entries are
        # shifted once, after the energy bills. Zero years shifts nothing.
        level = YearZeroPriceLevel(
            years=ageing_reference_year - price_basis_year,
            parameters=params,
            database=self.database,
            price_basis_year=price_basis_year,
        )

        for subject_facts in inputs.cost_facts:
            costing = resolve_device(
                subject=subject_facts.subject,
                facts=subject_facts.facts,
                context=context,
                existing_assets=inputs.existing_assets,
                ledger=ledger,
                database=self.database,
                parameters=params,
                price_basis_year=price_basis_year,
                ageing_reference_year=ageing_reference_year,
            )
            gross = costing.gross_investment
            subject = costing.subject
            asset_rate = self.investment_escalation_rate(costing.facts.asset_class)
            levy_asset_classes[subject] = costing.facts.asset_class.name
            levy_cost_by_subject.setdefault(subject, UncertainValue.exact(0.0))
            levy_credit_by_subject.setdefault(subject, UncertainValue.exact(0.0))

            # --- year-0 investment, replacements and residual value (§3.6 rules 1-3)
            schedule = build_investment_schedule(costing, gross, asset_rate, horizon, include_investment)
            level.add_subject(costing, asset_rate, replaced=bool(schedule.reserve_flows))
            # The price level the caller books this purchase at (a later stage's year).
            booked = booked_price_levels.get(subject, 1.0)
            timeline.extend(schedule.year_zero_entries)
            for addend in schedule.modernization_cost_addends:
                if level.shifts:
                    addend = addend.scale(level.purchase(subject))
                if booked != 1.0:
                    addend = addend.scale(booked)
                modernization_cost = modernization_cost + addend
                levy_cost_by_subject[subject] = levy_cost_by_subject[subject] + addend
            accumulate_embodied_co2(co2_result, subject, schedule.embodied_co2_addends)
            size = costing.facts.size * costing.facts.count
            if schedule.embodied_co2_addends and size:
                # The factor and size behind the mass, so the CO2 section can state
                # `factor x size = kg`. The factor is the quotient of the installation's mass and
                # its size; for a size of zero there is none, so no basis record is written and the
                # section prints the bare mass.
                co2_result.embodied_basis_by_subject[subject] = EmbodiedCo2Basis(
                    factor_in_kg_per_unit=costing.embodied_co2_kg / size,
                    size=size,
                    size_unit=costing.facts.size_unit.value,
                    per_installation_in_kg=costing.embodied_co2_kg,
                    installations=len(schedule.embodied_co2_addends),
                )
            reserve_flows = schedule.reserve_flows
            if level.shifts:
                reserve_flows = [(year, amount.scale(level.factor(asset_rate))) for year, amount in reserve_flows]
            replacement_flows_for_reserve.extend(reserve_flows)
            replacement_flows_by_subject.extend((subject, repl_year, amount) for repl_year, amount in reserve_flows)

            # --- replaced asset: sunk cost and anyway-cost credit (§4.1)
            if include_investment and costing.is_new_investment and costing.replaced_asset is not None:
                replaced_outcome = resolve_replaced_asset(
                    costing=costing,
                    # The coupled-cost credit is a share of what this measure costs; a stated
                    # purchase price (a reader's quote) is that cost. The like-for-like credit
                    # reads the replaced asset's own price and is not touched by it.
                    gross=costing.purchased_gross,
                    database=self.database,
                    parameters=params,
                    price_basis_year=price_basis_year,
                    ledger=ledger,
                    ageing_reference_year=ageing_reference_year,
                )
                sunk_cost = sunk_cost + replaced_outcome.sunk_cost
                if replaced_outcome.credit_entry is not None:
                    if self.book_anyway_credit:
                        timeline.add(replaced_outcome.credit_entry)
                    credit_factor = level.add_credit(
                        subject,
                        like_for_like=replaced_outcome.credit_basis_kind == AnywayBasisKinds.LIKE_FOR_LIKE,
                        replaced_rate=self.investment_escalation_rate(costing.replaced_asset.asset_class),
                    )
                    if level.shifts:
                        replaced_outcome = replace(
                            replaced_outcome,
                            credit_amount=replaced_outcome.credit_amount.scale(credit_factor),
                            credit_basis_in_euro=replaced_outcome.credit_basis_in_euro * credit_factor,
                        )
                    if self.book_anyway_credit:
                        anyway_share_by_subject[subject] = replaced_outcome.anyway_share
                        # The cost the share was applied to and what that cost is: the
                        # like-for-like and the coupled-cost branch credit different quantities.
                        anyway_basis_by_subject[subject] = replaced_outcome.credit_basis_in_euro
                        anyway_basis_kind_by_subject[subject] = replaced_outcome.credit_basis_kind
                    # The levy basis keeps the credit whether or not the evaluation books it: the
                    # avoided maintenance share is a deduction of the law (§6.4), not a plan flow.
                    levy_credit = replaced_outcome.credit_amount
                    if booked != 1.0:
                        levy_credit = levy_credit.scale(booked)
                    anyway_credit_total = anyway_credit_total + levy_credit
                    levy_credit_by_subject[subject] = levy_credit_by_subject[subject] + levy_credit
            # Replacements and the residual value are appended here, after the §4.1 credit, so it sits
            # between them and the year-0 entries; timeline insertion order is observable.
            schedule.add_to(timeline)

            # --- maintenance & fixed operation (§3.6 rule 4)
            timeline.extend(
                build_maintenance_entries(costing, gross, params.general_price_escalation_rate, horizon)
            )

            # --- subsidies (§5; none without a catalog). Suppressed under MACROECONOMIC accounting
            # because a subsidy is a transfer, not a resource cost (§4.5), and never applied to a
            # kept existing asset. Must precede the financing block below, which nets the year-0
            # SUBSIDY entries out of the loan principal.
            if (
                include_investment
                and costing.is_new_investment
                and not macro
                and perspective.subsidy_mode.kind != SubsidyModeKind.NONE
            ):
                subsidy_result = build_subsidy_flows(
                    costing=costing,
                    subsidy_catalog=self.subsidy_catalog,
                    subsidy_context=subsidy_context,
                    subsidy_mode=perspective.subsidy_mode,
                    billing=inputs.billing,
                    simulated_period_fraction=inputs.simulated_period_fraction,
                    ledger=ledger,
                    parameters=params,
                    price_basis_year=price_basis_year,
                    cost_factor=(level.purchase(subject) if level.shifts else 1.0) * booked,
                )
                if subsidy_result.decision is not None:
                    decisions.append(subsidy_result.decision)
                timeline.extend(subsidy_result.entries)

        # --- purchases priced whole by a stated amount, with no cost facts behind them
        if include_investment:
            for purchase in quoted_purchases:
                level.stated.add(purchase.subject)
                provenance = ledger.record(
                    ParameterProvenance(
                        parameter=f"{purchase.subject}.quoted_purchase_in_euro",
                        value=purchase.amount_in_euro,
                        origin=ParameterOrigin.CONFIG_OVERRIDE,
                        source_ids=(f"inline:{purchase.source}",),
                        detail=purchase.source,
                    )
                )
                timeline.add(
                    CashFlowEntry(
                        year=0,
                        amount_in_euro=purchase.amount_in_euro,
                        category=CostCategory.INVESTMENT,
                        subject=purchase.subject,
                        provenance_ids=(provenance,),
                    )
                )
                modernization_cost = modernization_cost + purchase.amount_in_euro
                levy_cost_by_subject[purchase.subject] = (
                    levy_cost_by_subject.get(purchase.subject, UncertainValue.exact(0.0)) + purchase.amount_in_euro
                )
                levy_credit_by_subject.setdefault(purchase.subject, UncertainValue.exact(0.0))

        # --- energy costs per carrier (§3.6 rule 5, §8)
        energy_result = build_energy_flows(
            billing=inputs.billing,
            tariff_contracts=inputs.tariff_contracts,
            simulated_period_fraction=inputs.simulated_period_fraction,
            ledger=ledger,
            database=self.database,
            parameters=params,
            price_basis_year=price_basis_year,
            horizon=horizon,
            macro=macro,
        )
        timeline.extend(energy_result.entries)
        accumulate_operational_emissions(energy_result, co2_result, horizon)

        # --- year 0 in its own calendar year's money: the one shift of every entry so far;
        # the reserve, the CO2 damage and the loan below are built from shifted figures.
        level.add_energy(energy_result.tariffs_applied, energy_result.raw_flexibility_value_by_carrier)
        timeline = level.timeline(timeline)

        # --- operating view: replacement reserve instead of investment categories (§4.2)
        if context == InstallationContext.OPERATING_ONLY and replacement_flows_for_reserve:
            timeline.extend(
                build_replacement_reserve_entries(replacement_flows_for_reserve, params, horizon)
            )

        # --- macroeconomic CO2 damage (§4.5)
        if macro:
            timeline.extend(build_co2_damage_entries(co2_result, params, horizon))

        # --- financing (§4.4): the year-0 net investment already on the timeline and the
        # LOAN_TERMS award the subsidy phase decided are passed in.
        if perspective.financing is not None and include_investment:
            loan_plan = resolve_loan_plan(perspective.financing, decisions)
            year0_net = compute_year0_net_investment(timeline)
            timeline.extend(build_financing_flows(loan_plan, year0_net, params.observation_period_in_years))

        finalize_total_co2(co2_result)
        # --- modernization-levy basis (§6.4): the support figure is read off the finished
        # timeline after financing, so the repayment grant counts. Nominal euros received.
        return TimelineBuildResult(
            timeline=timeline,
            decisions=decisions,
            co2_result=co2_result,
            sunk_cost=sunk_cost,
            basis=ModernizationLevyBasis(
                modernization_cost=modernization_cost,
                subsidies=nominal_support_from_entries(timeline.entries),
                avoided_maintenance=anyway_credit_total,
                by_subject=_levy_basis_by_subject(
                    timeline, levy_asset_classes, levy_cost_by_subject, levy_credit_by_subject
                ),
            ),
            raw_flexibility_value_by_carrier=energy_result.raw_flexibility_value_by_carrier,
            anyway_share_by_subject=anyway_share_by_subject,
            anyway_basis_by_subject=anyway_basis_by_subject,
            anyway_basis_kind_by_subject=anyway_basis_kind_by_subject,
            tariffs_applied=list(energy_result.tariffs_applied),
            replacement_flows=replacement_flows_by_subject,
        )

    # ------------------------------------------------------------------ evaluation (§3.7)

    @staticmethod
    def _with_package(inputs: EvaluationInputs, context: InstallationContext) -> SubsidyContext:
        """Return the subsidy context with `package` listing what this evaluation installs.

        Uses the same §4.1 rule as the pricing (`context_resolution.installation_verdict`) before anything is priced,
        so a scheme conditioned on a co-installed measure (e.g. SEAI's central-heating grant beside a heat pump) sees
        the whole evaluation. Subjects whose facts say not installed (size 0) are left out.

        Args:
            inputs: The variant's extract; not modified.
            context: The perspective's installation context.

        Returns:
            A copy of `inputs.subsidy_context` whose `package` lists the `ComponentType` values of every new
                investment, sorted.
        """
        installed = sorted(
            {
                subject_facts.facts.asset_class.value
                for subject_facts in inputs.cost_facts
                if not subject_facts.facts.is_not_installed()
                and installation_verdict(
                    subject_facts.facts.asset_class,
                    context,
                    inputs.existing_assets,
                    subject_facts.subject,
                    subject_facts.facts.own_register_entry,
                ).is_new_investment
            }
        )
        return replace(
            inputs.subsidy_context, package=SubsidyPackageContext(installed_asset_classes=tuple(installed))
        )

    def evaluate(
        self,
        inputs: EvaluationInputs,
        perspective: Perspective,
        ledger: Optional[ProvenanceLedger] = None,
        quoted_purchases: Sequence[QuotedPurchase] = (),
        booked_price_levels: Mapping[str, float] = MappingProxyType({}),
    ) -> LifecycleCostResult:
        """Evaluate one perspective: build the timeline, allocate payers, discount and aggregate into a result.

        The engine's main entry point. When the perspective is actor-scoped, the country's allocation ruleset assigns a
        payer to every entry (§6); the §6.4 modernization levy may add a tenant/landlord transfer pair.
        `calculators/aggregation.aggregate_timeline` then derives NPV, equivalent annual cost, pivots, per-subject
        breakdowns, the nominal liquidity series and the levelized cost of heat. The result's `timeline` holds every
        payer, so the §6.5 zero-sum check stays possible; the perspective's own flows are `scoped_timeline()`.

        Args:
            inputs: The variant's extract; never modified.
            perspective: The installation context, actor scope, subsidy mode, financing and accounting to evaluate
                (§4).
            ledger: Provenance ledger to record into; a fresh one when None. Stored on the result for `explain()`
                (§3.10).
            quoted_purchases: Purchases priced by a stated amount; see `build_timeline`.
            booked_price_levels: Subject -> the price level its year-0 purchase is booked at; see `build_timeline`.

        Returns:
            The `LifecycleCostResult`; every money field is a LOW/BEST_ESTIMATE/HIGH band (§3.9) and `parameters`
                carries the resolved price basis year.
        """
        # Same values as self.parameters, but with the resolved price basis year recorded.
        params = self.effective_parameters(inputs)
        # `is None`, not `or`: `ProvenanceLedger` defines `__len__`, so a caller-supplied but
        # still-empty ledger is falsy and must not be replaced by a fresh one.
        ledger = ledger if ledger is not None else ProvenanceLedger()
        build = self.build_timeline(inputs, perspective, ledger, quoted_purchases, booked_price_levels)
        timeline = build.timeline
        co2_result = build.co2_result

        # Actor allocation (§6).
        levy_summary: Optional[ModernizationLevySummary] = None
        if perspective.actor_scope != ActorScope.SYSTEM:
            rented = perspective.actor_scope in (ActorScope.LANDLORD, ActorScope.TENANT)
            ruleset = get_ruleset(rented, params.country)
            allocation_context = AllocationContext(
                horizon_years=params.observation_period_in_years,
                building_specific_emissions_in_kg_per_m2_a=inputs.building_specific_emissions_in_kg_per_m2_a,
                heated_floor_area_in_m2=inputs.heated_floor_area_in_m2,
                living_area_in_m2=inputs.living_area_in_m2,
                current_cold_rent_in_euro_per_m2_month=inputs.current_cold_rent_in_euro_per_m2_month,
                modernization_cost_in_euro=build.basis.modernization_cost,
                subsidies_received_in_euro=build.basis.subsidies,
                avoided_maintenance_in_euro=build.basis.avoided_maintenance,
                levy_subjects=build.basis.by_subject,
            )
            timeline = ruleset.allocate(timeline, allocation_context)
            # The levy's ceiling verdict, asked of the ruleset that just applied it. The amount is
            # on the timeline; whether a cap decided it is not, and the landlord statement has to
            # say so.
            levy_summary = _levy_summary(ruleset.modernization_levy_outcome(allocation_context))

        aggregation = aggregate_timeline(
            timeline=timeline,
            actor_scope=perspective.actor_scope,
            facts_by_subject={
                subject_facts.subject: subject_facts.facts for subject_facts in inputs.cost_facts
            },
            co2_result=co2_result,
            parameters=params,
            annual_heat_demand_in_kwh=inputs.annual_heat_demand(),
        )

        return LifecycleCostResult(
            perspective_id=perspective.id,
            parameters=params,
            total_npv_in_euro=aggregation.total_npv_in_euro,
            equivalent_annual_cost_in_euro=aggregation.equivalent_annual_cost_in_euro,
            monthly_equivalent_cost_in_euro=aggregation.monthly_equivalent_cost_in_euro,
            npv_by_category=aggregation.npv_by_category,
            npv_by_component=aggregation.npv_by_component,
            npv_by_payer=aggregation.npv_by_payer,
            component_breakdowns=aggregation.component_breakdowns,
            annual_cost_series_nominal_in_euro=aggregation.annual_cost_series_nominal_in_euro,
            monthly_cost_year1_in_euro=aggregation.monthly_cost_year1_in_euro,
            levelized_cost_of_heat_in_euro_per_kwh=aggregation.levelized_cost_of_heat_in_euro_per_kwh,
            timeline=timeline,
            lifecycle_co2_result=co2_result,
            subsidy_decisions=build.decisions,
            sunk_cost_written_off_in_euro=build.sunk_cost,
            ledger=ledger,
            source_resolver=self._source_resolver(),
            scope_payer=aggregation.scope_payer,
            # Physical context of the evaluation, so the derived views and the plausibility
            # report never read `EvaluationInputs`:
            annual_energy_quantities_by_carrier=annual_energy_quantities(
                inputs.billing, inputs.simulated_period_fraction
            ),
            # The per-subject half of the same physical context, annualized with the identical
            # divisor so the device column of the household energy balance adds up to the carrier
            # totals above it rather than to a slightly different year.
            annual_energy_attribution_by_subject_in_kwh=annual_energy_attribution(
                inputs.energy_attribution_by_subject_in_kwh, inputs.simulated_period_fraction
            ),
            reference_areas=ReferenceAreas(
                heated_floor_area_in_m2=inputs.heated_floor_area_in_m2,
                living_area_in_m2=inputs.living_area_in_m2,
            ),
            simulated_period_fraction=inputs.simulated_period_fraction,
            simulation_year=inputs.simulation_year,
            # Diagnostics rather than result: the pre-clamp §8.5 flexibility value, which the
            # plausibility panel warns about when it is negative.
            raw_flexibility_value_by_carrier=build.raw_flexibility_value_by_carrier,
            # The Sowieso share behind each anyway credit, so the report can state it beside the
            # credit rather than leaving a reader to guess at the basis.
            anyway_share_by_subject=build.anyway_share_by_subject,
            anyway_basis_by_subject=build.anyway_basis_by_subject,
            anyway_basis_kind_by_subject=build.anyway_basis_kind_by_subject,
            modernization_levy=levy_summary,
            # The assumption set the report's assumptions section publishes — escalation rates as
            # the fallback chains resolved them, the tariff terms actually billed, and the heat
            # demand every per-kWh heat figure divides by.
            assumptions=self._resolve_assumptions(inputs, build),
            # The dated replacement schedule behind the timeline, which the staged evaluator
            # re-dates per stage and the OPERATING_ONLY reserve is levelized from.
            replacement_flows=build.replacement_flows,
        )

    def _resolve_assumptions(
        self, inputs: EvaluationInputs, build: TimelineBuildResult
    ) -> EconomicAssumptions:
        """Return the economic assumptions behind this run, with value and source, for the report.

        Escalation rates come from a fallback chain, tariff terms from the contract the energy calculator billed under,
        and the heat demand from `EvaluationInputs`, which presentation may not read, so they are resolved here. Only
        rates that applied are recorded: one per billed carrier, one per asset class among the subjects, and the three
        general rates.

        Args:
            inputs: The variant's extract: billed carriers, cost subjects, heat demand.
            build: The finished timeline build, read for the contracts the energy calculator used.

        Returns:
            The record stored on `LifecycleCostResult.assumptions`.
        """
        params = self.parameters
        rates: Dict[str, ResolvedRate] = {
            "general": ResolvedRate(
                rate=params.general_price_escalation_rate, origin=RateOrigin.CONFIGURATION
            ),
            "investment": ResolvedRate(
                rate=params.investment_price_escalation_rate, origin=RateOrigin.CONFIGURATION
            ),
            "feed-in": ResolvedRate(rate=params.feed_in_escalation_rate, origin=RateOrigin.CONFIGURATION),
        }
        for determinants in inputs.billing:
            carrier = determinants.carrier
            rates[f"energy:{carrier.value}"] = resolve_carrier_escalation_rate(
                carrier, params, self.database
            )
        for subject_facts in inputs.cost_facts:
            asset_class = subject_facts.facts.asset_class
            rates[f"investment:{asset_class.name}"] = resolve_investment_escalation_rate(
                asset_class, params, self.database
            )
        tariffs: Dict[str, TariffAssumption] = {
            contract.carrier.value: TariffAssumption.from_contract(contract)
            for contract in build.tariffs_applied
        }
        return EconomicAssumptions(
            escalation_rates=rates,
            tariffs=tariffs,
            annual_heat_demand_in_kwh=inputs.annual_heat_demand(),
        )

    def _source_resolver(self) -> Dict[str, ResolvedSource]:
        """Return every source registry a result's provenance can cite: the cost database's and the subsidy catalog's.

        The two have disjoint id spaces; the cost database wins a collision.
        """
        resolver: Dict[str, ResolvedSource] = {}
        if self.subsidy_catalog is not None:
            resolver.update(self.subsidy_catalog.source_resolver())
        resolver.update(
            {source_id: entry.to_resolved() for source_id, entry in self.database.sources.entries.items()}
        )
        return resolver

    def evaluate_matrix(
        self,
        inputs: EvaluationInputs,
        perspectives: List[Perspective],
    ) -> EvaluationMatrix:
        """Evaluate several perspectives against the same simulation extract (§4).

        Usually the default nine-row bundle (`cost_database/perspectives_default.json`, §7.1). Each perspective is
        evaluated independently with its own provenance ledger.

        Args:
            inputs: The variant's extract; never modified.
            perspectives: The bundle, already pruned by `perspectives.select_applicable` (greenfield rows drop out when
                an existing-asset register is present, brownfield and status-quo rows when there is none).

        Returns:
            An `EvaluationMatrix` keyed by `Perspective.id`, in the given order.
        """
        matrix = EvaluationMatrix()
        for perspective in perspectives:
            matrix.results[perspective.id] = self.evaluate(inputs, perspective)
        return matrix
