"""The economic evaluator: facts -> cash flows -> results (cost_spec.md §3, §4).

A pure function of ``(facts, flows, cost_db, subsidy_catalog, econ_params, perspective)``.
No config mutation, no file I/O inside the calculation.

This module owns the *orchestration* of a lifecycle cost evaluation and almost nothing else.
`EconomicEvaluator.build_timeline` sequences the domain calculators of
`hisim/economics/calculators/` into the one canonical `CashFlowTimeline` of a perspective, and
`EconomicEvaluator.evaluate` turns that timeline into a `LifecycleCostResult` by allocating
payers (§6) and discounting/pivoting it (§3.7). Every euro is produced by a calculator, every
number read comes from `CostDatabase` / `SubsidyCatalog` / `EconomicParameters`, and every
published figure is a filter or a pivot of the timeline (§3.1) — so what this file actually
decides is *order*, not money.

It additionally owns the two policies that must be settled before any lookup happens and that no
single calculator could own: which price basis year the database is read at
(`effective_price_basis_year`, cost-spec-v2 W1.2), and whether the declared facts can be priced
at all (`resolve_check` / `require_resolvable_subjects`, §9.3 and decision D7).

Deliberately *not* here: reading a finished simulation (`bridge.py`), the data files
(`database.py`), presentation (`reporting.py`, `exports.py`) and the per-mechanism arithmetic
(`calculators/`). The evaluator touches no simulation object; its entire input is the plain
`EvaluationInputs` record, which is exactly what `economic_inputs.json` stores. That is the
cost-spec-v2 seam-1 contract, and it is what makes an archived run re-priceable years later
without HiSim.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Sequence, Set, Tuple

from hisim import log
from hisim.economics.actors import AllocationContext, ModernizationLevySubjectBasis, get_ruleset
from hisim.economics.calculators.aggregation import aggregate_timeline, annual_energy_quantities
from hisim.economics.calculators.context_resolution import resolve_device, resolve_replaced_asset
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
    carrier_escalation_rate as resolve_carrier_escalation_rate,
    investment_escalation_rate as resolve_investment_escalation_rate,
)
from hisim.economics.calculators.investment import build_investment_schedule
from hisim.economics.calculators.maintenance import build_maintenance_entries
from hisim.economics.calculators.reserve import build_replacement_reserve_entries
from hisim.economics.calculators.subsidy_application import (
    build_subsidy_flows,
    nominal_support_from_entries,
)
from hisim.economics.carriers import EnergyCarrier
from hisim.economics.database import CostDatabase, CostDataError
from hisim.economics.facts import (
    BillingDeterminants,
    ComponentCostFacts,
    ExistingAssetRegister,
)
from hisim.economics.parameters import EconomicParameters
from hisim.economics.perspectives import (
    Accounting,
    ActorScope,
    InstallationContext,
    Perspective,
    SubsidyModeKind,
)
from hisim.economics.provenance import ProvenanceLedger, ResolvedSource
from hisim.economics.results import (
    EvaluationMatrix,
    LifecycleCo2Result,
    LifecycleCostResult,
    ReferenceAreas,
)
from hisim.economics.subsidies import SubsidyCatalog, SubsidyContext, SubsidyDecision
from hisim.economics.tariffs import TariffContract
from hisim.economics.timeline import CashFlowTimeline, CostCategory
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import ComponentType


@dataclass
class SubjectCostFacts:
    """A component's cost facts together with its timeline subject name.

    The subject is the key every cash flow, pivot, breakdown and export row of this component is
    filed under — in practice the HiSim component name assigned by `bridge.py`, or a free-form
    name for cost subjects that are not simulation components at all (envelope measures are
    injected by whoever defines the variant, README §3.2b). Pairing the name with the facts here
    keeps `ComponentCostFacts` free of any identity of its own, so the same facts can be declared
    by a component, injected by a system setup, or read back from `economic_inputs.json`.
    """

    subject: str
    facts: ComponentCostFacts


@dataclass
class UnresolvedSubject:
    """A cost subject the *extraction* could not describe, carried into the resolution check (D7).

    The counterpart of `SubjectCostFacts` for the failure case, and the record that closes the
    silent-omission hole of issue #2: a component the adapter recognizes but whose facts came out
    empty — a boiler burning a fuel with no asset class, a meter whose configured load type maps
    to no carrier — used to disappear from the cost model while still counting as priced. It is
    now extracted as one of these, written into `economic_inputs.json` like everything else the
    simulation yielded, and turned into a blocking `ResolutionProblem` by `resolve_check`.

    It is deliberately a *finding*, not an exception: the extraction pass records everything it
    found wrong and the D7 check reports all of it at once, in the same bullet list as the
    subjects the cost database cannot price.
    """

    subject: str
    #: Why no facts could be extracted, phrased for someone who has to fix the setup.
    reason: str


class _PriceBasisYearWarnings:
    """Warn-once bookkeeping for `effective_price_basis_year` (log noise only, never
    semantics).

    The basis-year policy is re-resolved on every evaluation — once per perspective, and again
    for every cell of a scenario cube — so a simulation year the shipped data does not cover
    would otherwise log the identical warning thousands of times in a sweep. The seen keys live
    at class level so the deduplication also holds across separately constructed evaluators.
    """

    WARNED: set = set()


def effective_price_basis_year(
    parameters: EconomicParameters, database: CostDatabase, simulation_year: int
) -> int:
    """The price basis year used for every database lookup (cost-spec-v2 §2.1, W1.2).

    An explicit ``EconomicParameters.price_basis_year`` always wins. Otherwise the simulation
    year is used, unless the shipped database starts later than the simulation year: the engine
    itself is strict (§3.5: hard error on uncovered years), so the earliest covered year is
    picked explicitly (with a warning) instead of reintroducing silent fallbacks.

    This policy lives downstream of `economic_inputs.json` so that the postprocessing bridge and
    the re-pricing CLI derive the same year from the same file (they used to differ).
    """
    if parameters.price_basis_year is not None:
        return parameters.price_basis_year
    earliest = database.earliest_device_year(parameters.country)
    if earliest is None or earliest <= simulation_year:
        return simulation_year
    key = (parameters.country, simulation_year, earliest)
    if key not in _PriceBasisYearWarnings.WARNED:
        _PriceBasisYearWarnings.WARNED.add(key)
        log.warning(
            f"No device cost data valid at simulation year {simulation_year} for {parameters.country}; "
            f"using price basis year {earliest} (earliest available). Set "
            "EconomicParameters.price_basis_year to override."
        )
    return earliest


@dataclass
class EvaluationInputs:
    """Everything the pure evaluator needs about one simulated variant.

    Serialized to `economic_inputs.json` for post-hoc re-pricing (§4.6).

    This is the seam-1 contract of cost-spec-v2 §2.1: plain data only — no component objects, no
    callables, nothing that needs a running HiSim to interpret — so the boundary between "wrong
    physical quantities went in" and "they were priced wrong" is a file, not a matter of reading
    code. `bridge.py` fills the record from a finished simulation (merged with the
    setup-declared `EconomicContext`), `serialization.py` round-trips it, and `EconomicEvaluator`
    is a pure function of it plus the cost database, the subsidy catalog, the economic parameters
    and one `Perspective`. Because none of the prices live here, re-pricing an archived run under
    new assumptions never re-runs the building simulation (§4.6).

    Faithfulness is the property the file is written for: it is emitted *before* the resolution
    check runs, so it stays a complete extract even when nothing in it can be priced
    (cost-spec-v2 W1.1, decision D7).

    The fields worth a word: `cost_facts` lists every priced subject, including ones that are not
    simulation components (envelope measures, README §3.2b); `billing` carries one record per
    meter, i.e. per carrier flow that crossed the system boundary (§3.4); `existing_assets` being
    None means greenfield, and its presence is what activates the brownfield/status-quo
    perspectives (§4.1); `subsidy_context` holds the applicant/building answers the §5.3
    eligibility conditions resolve against, where an unanswered field stays *undetermined* rather
    than false (§5.7). All energy quantities are the ones actually simulated — annualization to a
    full year happens in the engine via `simulated_period_fraction`, never here.

    `unresolved_subjects` is the one field that records a *failure* of the extraction rather than
    a fact about the system. Faithfulness cuts both ways: a component the extraction could not
    describe belongs in the file just as much as one it could, so that the reason survives into
    the archived record instead of living only in a log line (issue #2, decision D7).
    """

    simulation_year: int
    simulated_period_fraction: float  # simulated seconds / seconds of a full year
    cost_facts: List[SubjectCostFacts] = field(default_factory=list)
    billing: List[BillingDeterminants] = field(default_factory=list)
    # Subjects the extraction recognized but could not describe (issue #2); they block the D7
    # resolution check exactly like a subject the cost database cannot price.
    unresolved_subjects: List[UnresolvedSubject] = field(default_factory=list)
    existing_assets: Optional[ExistingAssetRegister] = None
    subsidy_context: SubsidyContext = field(default_factory=SubsidyContext)
    tariff_contracts: Dict[EnergyCarrier, TariffContract] = field(default_factory=dict)
    # Tariff ids whose price signal a controller consumed during the run (§4.6 boundary):
    consumed_tariff_ids: List[str] = field(default_factory=list)
    annual_heat_demand_in_kwh: Optional[float] = None  # for the levelized cost of heat
    # Building context for the actor model (§6.3, §6.4):
    building_specific_emissions_in_kg_per_m2_a: Optional[float] = None
    heated_floor_area_in_m2: Optional[float] = None
    living_area_in_m2: Optional[float] = None
    current_cold_rent_in_euro_per_m2_month: Optional[float] = None


@dataclass(frozen=True)
class ResolutionProblem:
    """One problem found by `EconomicEvaluator.resolve_check` (§9.3).

    Structured so consumers can decide per subject instead of matching message substrings
    (cost-spec-v2 §2.1, W1.1). `subject` is the cost-facts subject the problem belongs to and
    None for problems that are not subject-scoped (e.g. a missing carrier price entry).
    """

    message: str
    subject: Optional[str] = None
    kind: str = ""
    #: Whether the subject cannot be priced at all (as opposed to a documentation defect).
    blocks_evaluation: bool = True

    def __str__(self) -> str:
        """The message, so log formatting stays as readable as with plain strings."""
        return self.message


class UnresolvableSubjectsError(CostDataError):
    """Something in the extract cannot be priced, so nothing is priced (cost-spec-v2 §8, D7).

    Owner decision D7 overrides the earlier drop-and-continue: an unresolvable cost subject is a
    hard error in the postprocessing bridge and in the CLI alike — there are no partial cost
    results. The check runs *after* `economic_inputs.json` has been written, so the faithful
    extract (§2.1, W1.1) is unaffected and can be inspected or re-priced against a fixed
    database. `problems` carries the structured `ResolutionProblem`s for programmatic consumers.
    """

    def __init__(self, problems: Sequence[ResolutionProblem]) -> None:
        """Renders one bullet per blocked subject; unscoped blockers are labelled by kind."""
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
    """Fails fast when the cost database cannot price part of the extract (D7).

    The downstream half of the extraction seam (cost-spec-v2 §2.1, W1.1): `economic_inputs.json`
    records the *full* simulation extract and is written first, so the file never depends on
    cost-database state; every consumer of that file then runs this check before evaluating and
    refuses to produce a result at all if any declared fact is unresolvable. Non-blocking
    problems (e.g. an override without `override_source`) keep their warning behavior. Blockers
    without a subject — a missing energy price entry for a billed carrier — are included: they
    already aborted the evaluation deep inside the database lookup, here they abort it early and
    with the same typed error. The inputs are never modified.

    Args:
        inputs: The simulation extract to check.
        evaluator: The evaluator whose cost database, country and price basis year decide what
            "resolvable" means here; only `resolve_check` is called on it.

    Raises:
        UnresolvableSubjectsError: as soon as any blocking problem exists — one bullet per
            blocked subject (the first reason found for it) plus every unscoped blocker. It is a
            `CostDataError`, which the CLI turns into exit code 2 and the postprocessing bridge
            lets propagate into its existing guard, so legacy outputs are unaffected.
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
    """The parts the §6.4 modernization levy is computed from, as the timeline build saw them.

    Units (W3.4, decided 2026-08-12): all three are **nominal, undiscounted euro bands**.
    `subsidies` is the nominal sum of *every* SUBSIDY entry on the finished timeline — §559 BGB
    deducts the support actually received — so it is complete by construction and recoverable
    from the timeline (§5.1).

    `by_subject` is the same money attributed to the measure that produced it, which §559e made
    necessary: the heating measures of a package are levied at a different rate and under a
    different cap than its envelope measures (§6.4, D27). It always covers the aggregates exactly
    — support that belongs to no single measure (the financing repayment grant) is carried by a
    record whose asset class is `None` — and `AllocationContext` re-checks that when it is built.
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
    """Attributes the §6.4 levy basis to the measures that produced it (D27).

    The §559/§559e split needs the basis per measure, and two of its three parts are already
    per-measure when the build loop ends; the third — support received — is read off the finished
    timeline for the same reason the aggregate is (W3.4): only there is it complete, financing
    repayment grant included. SUBSIDY entries are grouped by their `subject`, so an award keeps
    the measure it was granted for.

    Support whose subject is not a costed measure (the financing grant's synthetic subject) is
    kept as its own record with no asset class rather than dropped, which is what makes this
    breakdown add up to the aggregate figure exactly — the property `AllocationContext` checks.

    Args:
        timeline: The finished, pre-allocation timeline; read only.
        asset_classes: `ComponentType` name per costed subject.
        modernization_cost: Allocatable modernization cost per costed subject.
        avoided_maintenance: Anyway-cost credit per costed subject.

    Returns:
        One record per costed subject in costing order, followed by one record per unattributed
        support subject in timeline order.
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
    """What one timeline build produces: the timeline and the four outputs that bypass it.

    The timeline alone is not a sufficient contract between the calculators and the
    orchestrator (cost-spec-v2 §2.3): the subsidy `decisions` record, the parallel CO2 mass
    accounting, the written-off `sunk_cost` and the modernization-levy `basis` legitimately do
    not appear as cash flows. Internal API — `evaluate` is the only consumer.
    """

    timeline: CashFlowTimeline
    decisions: List[SubsidyDecision]
    co2_result: LifecycleCo2Result
    sunk_cost: UncertainValue
    basis: ModernizationLevyBasis
    #: Per-carrier flexibility value before the §8.5 clamp, for the plausibility panel (#25b).
    raw_flexibility_value_by_carrier: Dict[str, float] = field(default_factory=dict)


class EconomicEvaluator:
    """Builds the canonical timeline and evaluates perspectives against it.

    The core of the lifecycle cost engine, and the class to read first. It is a **pure function
    of its inputs**: given an `EvaluationInputs` extract, a `CostDatabase`, an optional
    `SubsidyCatalog`, `EconomicParameters` and one `Perspective` it yields a
    `LifecycleCostResult` — no simulation object is consulted, no file is read or written inside
    the calculation, no configuration is mutated. That purity is not an aesthetic preference: it
    is what lets an evaluation be reproduced from `economic_inputs.json` alone (cost-spec-v2
    seam 1, §2.1), what makes a 10,000-cell scenario sweep affordable (§4.6), and what allows a
    reviewer to check the economics without ever running HiSim.

    One evaluation has two stages. `build_timeline` composes the calculators of
    `hisim/economics/calculators/` into a single canonical `CashFlowTimeline` — investment,
    replacements and residual value, the replaced asset's sunk cost and anyway-cost credit,
    maintenance and fixed operation, subsidies, energy bills, the operating view's replacement
    reserve, macroeconomic CO2 damage, and financing — in an order that is load-bearing and
    documented at that method. `evaluate` then has the §6 allocation ruleset stamp a payer on
    every entry and hands the finished timeline to `calculators/aggregation.py`, which discounts
    and pivots it into every published figure. Nothing downstream of the timeline invents a cash
    flow, so totals, category pivots, per-component breakdowns and the liquidity series reconcile
    by construction rather than by agreement between separate code paths (§3.1 principle 3).

    The three uncertainty slots of §3.9 are not three passes. Every amount on the timeline is an
    `UncertainValue` (LOW / BEST_ESTIMATE / HIGH), all arithmetic is slot-wise, and one build therefore
    yields the optimistic, the expected and the pessimistic world at once, at negligible extra
    cost. What the engine *chooses* rather than computes — the subsidy cumulation combination
    (§5.4), the tariff counterfactual (§8.5) — is decided once on the BEST_ESTIMATE slot and then
    valued in all three, so the published band always describes one consistent physical and
    contractual plan.

    Callers: `bridge.py` (postprocessing), `scenarios.py` (the evaluation cube) and the
    `python -m hisim.economics` CLI; `evaluate_matrix` runs a whole perspective bundle (§4)
    against the same extract.
    """

    def __init__(
        self,
        cost_database: CostDatabase,
        parameters: EconomicParameters,
        subsidy_catalog: Optional[SubsidyCatalog] = None,
    ) -> None:
        """The catalog is optional: without one the legacy flat-percentage shim applies (§10.1).

        All three arguments are held as given and never modified, so an evaluator is a cheap,
        reusable handle over one dataset and one parameter set. `scenarios.evaluate_cube` relies
        on that and constructs a fresh evaluator per scenario cell, over the overlaid copy of the
        database that the scenario asked for (§4.6).
        """
        self.database = cost_database
        self.parameters = parameters
        self.subsidy_catalog = subsidy_catalog

    # ------------------------------------------------------------------ rate resolution

    def carrier_escalation_rate(self, carrier: EnergyCarrier) -> float:
        """Fallback chain: explicit parameter -> country defaults file -> general rate (§3.2).

        Thin delegation to `calculators/escalation.py`, which owns both fallback chains since
        W3.1; it stays a method because the chain needs the evaluator's parameters *and* its
        database. The returned nominal annual rate is what the energy calculator escalates a
        carrier's year-1 bill with over the horizon (§3.6 rule 5).
        """
        return resolve_carrier_escalation_rate(carrier, self.parameters, self.database)

    def investment_escalation_rate(self, asset_class: ComponentType) -> float:
        """Fallback chain for per-asset-class investment escalation (learning curves, §3.2).

        Same delegation as `carrier_escalation_rate`, for the rate at which the *purchase price*
        of an asset class moves; it may legitimately be negative (PV and batteries get cheaper).
        `build_timeline` resolves it once per subject and passes it to the investment calculator,
        which uses it for replacements and for the residual value (§3.6 rules 2-3).
        """
        return resolve_investment_escalation_rate(asset_class, self.parameters, self.database)

    def price_basis_year(self, inputs: EvaluationInputs) -> int:
        """Price basis year for database lookups (see `effective_price_basis_year`).

        The economic "today": every device entry, energy price entry and asset age in this
        evaluation is resolved against this year, which need not be the simulated weather year.
        It is resolved once at the top of `build_timeline` and threaded into every calculator, so
        a single evaluation can never mix price levels.
        """
        return effective_price_basis_year(self.parameters, self.database, inputs.simulation_year)

    def effective_parameters(self, inputs: EvaluationInputs) -> EconomicParameters:
        """The parameters as actually used, with the resolved price basis year filled in.

        Reports and audits read the basis year off the result, so the resolved value is recorded
        there instead of being back-written into the caller's parameters (W1.2).
        """
        if self.parameters.price_basis_year is not None:
            return self.parameters
        return replace(self.parameters, price_basis_year=self.price_basis_year(inputs))

    # ------------------------------------------------------------------ pre-run resolution check (§9.3)

    def resolve_check(self, inputs: EvaluationInputs, strict: bool = True) -> List[ResolutionProblem]:
        """Dry-resolves every declared fact against the database; returns structured problems.

        Runs before the timestep loop so a missing database entry fails in seconds. The same
        pass populates the provenance ledger during evaluation (§3.10, §9.3). Problems carry the
        cost-facts subject they belong to (None for carrier-level problems) so consumers can act
        on them by exact subject instead of matching message substrings (W1.1).

        Four things are checked, and they are of two different severities. A subject the
        *extraction* already gave up on (`inputs.unresolved_subjects`, issue #2) is reported first
        and verbatim, since no database lookup can rescue a component whose facts were never
        established. A missing device entry and a `size_unit` that disagrees with the entry's
        `per_unit` mean the subject cannot be priced at all; a missing energy price entry means a
        billed carrier cannot be priced; all of these set `blocks_evaluation` (decision D7 turns
        them into a hard error via `require_resolvable_subjects`). Cost overrides without an
        `override_source` are a
        documentation defect under §3.10 — the facts still price — and are reported
        non-blocking. Facts that override *both* investment cost and lifetime need no database
        entry at all and skip the two device checks (they are still checked for `override_source`).

        Args:
            inputs: The extract to dry-resolve; never modified.
            strict: In strict mode the §3.10 override-without-source defect is *returned* as a
                non-blocking problem, otherwise it is only logged as a warning. It does not
                affect any of the blocking checks.

        Returns:
            All problems found, in subject order then carrier order; empty when everything
            resolves. Callers decide what to do with them — `require_resolvable_subjects` raises,
            `validation`/`audit` report.
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
    ) -> TimelineBuildResult:
        """Builds the canonical timeline for one perspective, plus its non-cash outputs.

        Pure composition (cost-spec-v2 §2.3): every euro is produced by a calculator in
        `hisim/economics/calculators/`, in the order they must run —

        1. per subject: context resolution -> investment schedule -> replaced-asset outcome
           -> maintenance -> subsidies;
        2. energy bills for every carrier;
        3. the replacement reserve, which needs *all* subjects' replacement flows;
        4. the macroeconomic CO2 damage, which needs the operational emissions of (2);
        5. financing, which needs the year-0 entries of (1) and the awards of (1.5).

        The accumulators below stay in the orchestrator because their fold order across
        subjects is observable (float addition is not associative); calculators return ordered
        addends rather than folding themselves.

        **The ordering constraints, and why they bind** (each is verifiable in the body below):

        * *Subsidies before financing.* `build_subsidy_flows` prices a scheme off the resolved
          costing's gross investment (§5.2), and its year-0 entries must already sit on the
          timeline when financing runs, because `compute_year0_net_investment` sums the year-0
          INVESTMENT/PLANNING/REMOVAL **and SUBSIDY** entries — subsidy entries are negative — to
          obtain the principal *net of upfront grants* (§4.4, `calculators/categories.py`).
          Financing is therefore the last money-producing step of the build.
        * *The anyway-cost credit is interleaved into the investment schedule.* The §4.1 credit is
          emitted between the schedule's year-0 entries and its replacement/residual entries,
          which is why `schedule.add_to(timeline)` is called after the §4.1 block instead of
          right after `build_investment_schedule`. Entry order is observable — the timeline keeps
          insertion order and every NPV, pivot and CSV row folds in it.
        * *The replacement reserve needs every subject.* It levelizes all subjects' replacement
          flows into one sinking fund (§4.2), so it can only run once the per-subject loop is
          finished; the flows themselves are collected even under OPERATING_ONLY, where the
          REPLACEMENT entries are suppressed.
        * *CO2 damage after the energy bills.* `build_co2_damage_entries` prices the operational
          emissions that `accumulate_operational_emissions` folded in from the energy result
          (§4.5); running it earlier would price an empty mass accounting.
        * *The levy basis is read off the finished timeline.* `nominal_support_from_entries` runs
          after financing so a soft loan's repayment grant counts as support received (W3.4,
          §6.4) — the figure is complete by construction instead of accumulated as entries are
          emitted.

        Which calculator produces what, and why each was extracted (cost-spec-v2 §2.3 — the
        1,093-line evaluator was the concentration of review risk):
        `calculators/context_resolution.py` decides *which* numbers a subject is priced from and
        what the installation context does to it; `investment.py` owns the VDI 2067-1
        replacement/residual convention; `maintenance.py` the recurring non-energy operating
        cost; `subsidy_application.py` the award-to-cash-flow step (the eligibility and
        cumulation logic stays in `subsidies.py`); `energy.py` the tariff application and the
        per-component escalation of a year-1 bill; `reserve.py` the operating view's sinking
        fund; `co2.py` the parallel mass accounting and the macroeconomic damage entries; and
        `financing_application.py` the loan layout (the closed-form annuity mathematics stays in
        `financing.py`). Each is separately reviewable against a hand-computed example, which the
        inline version was not.

        Args:
            inputs: The variant's extract; read only, never modified.
            perspective: Supplies the three switches this method acts on — the installation
                context (`include_investment` under everything but OPERATING_ONLY), the
                accounting mode (`macro`, which suppresses subsidies and adds CO2 damage, §4.5)
                and the subsidy mode (§5.5) — plus the optional financing plan.
            ledger: Provenance ledger; **mutated** — every database lookup made along the way
                records itself here, which is what makes `LifecycleCostResult.explain` possible
                (§3.10).

        Returns:
            A `TimelineBuildResult`: the timeline in nominal, undiscounted euro bands (discounting
            happens once, in `calculators/aggregation.py`), plus the four outputs that legitimately
            are not cash flows — the subsidy decisions, the CO2 mass accounting, the written-off
            sunk cost and the modernization-levy basis.
        """
        params = self.parameters
        price_basis_year = self.price_basis_year(inputs)
        horizon = params.observation_period_in_years
        timeline = CashFlowTimeline()
        co2_result = LifecycleCo2Result(operational_co2_by_year_in_kg=[0.0] * (horizon + 1))
        decisions: List[SubsidyDecision] = []
        sunk_cost = UncertainValue.exact(0.0)
        modernization_cost = UncertainValue.exact(0.0)
        anyway_credit_total = UncertainValue.exact(0.0)
        # Per-measure levy basis for the §559/§559e split (§6.4, D27): asset class, modernization
        # cost and anyway credit per subject; the subsidy leg is read off the finished timeline.
        levy_asset_classes: Dict[str, str] = {}
        levy_cost_by_subject: Dict[str, UncertainValue] = {}
        levy_credit_by_subject: Dict[str, UncertainValue] = {}
        context = perspective.installation_context
        include_investment = context != InstallationContext.OPERATING_ONLY
        macro = perspective.accounting == Accounting.MACROECONOMIC

        replacement_flows_for_reserve: List[Tuple[int, UncertainValue]] = []

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
            )
            gross = costing.gross_investment
            subject = costing.subject
            asset_rate = self.investment_escalation_rate(costing.facts.asset_class)
            levy_asset_classes[subject] = costing.facts.asset_class.name
            levy_cost_by_subject.setdefault(subject, UncertainValue.exact(0.0))
            levy_credit_by_subject.setdefault(subject, UncertainValue.exact(0.0))

            # --- year-0 investment, replacements and residual value (§3.6 rules 1-3)
            schedule = build_investment_schedule(costing, gross, asset_rate, horizon, include_investment)
            timeline.extend(schedule.year_zero_entries)
            for addend in schedule.modernization_cost_addends:
                modernization_cost = modernization_cost + addend
                levy_cost_by_subject[subject] = levy_cost_by_subject[subject] + addend
            accumulate_embodied_co2(co2_result, subject, schedule.embodied_co2_addends)
            replacement_flows_for_reserve.extend(schedule.reserve_flows)

            # --- replaced asset: sunk cost and anyway-cost credit (§4.1)
            if include_investment and costing.is_new_investment and costing.replaced_asset is not None:
                replaced_outcome = resolve_replaced_asset(
                    costing=costing,
                    gross=gross,
                    database=self.database,
                    parameters=params,
                    price_basis_year=price_basis_year,
                )
                sunk_cost = sunk_cost + replaced_outcome.sunk_cost
                if replaced_outcome.credit_entry is not None:
                    timeline.add(replaced_outcome.credit_entry)
                    anyway_credit_total = anyway_credit_total + replaced_outcome.credit_amount
                    levy_credit_by_subject[subject] = (
                        levy_credit_by_subject[subject] + replaced_outcome.credit_amount
                    )
            # Replacements and the residual value are appended only now, so the §4.1 credit sits
            # between them and the year-0 entries; timeline insertion order is observable.
            schedule.add_to(timeline)

            # --- maintenance & fixed operation (§3.6 rule 4)
            timeline.extend(
                build_maintenance_entries(costing, gross, params.general_price_escalation_rate, horizon)
            )

            # --- subsidies (§5; flat shim §10.1). Suppressed under MACROECONOMIC accounting
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
                    subsidy_context=inputs.subsidy_context,
                    subsidy_mode=perspective.subsidy_mode,
                    billing=inputs.billing,
                    simulated_period_fraction=inputs.simulated_period_fraction,
                    ledger=ledger,
                    parameters=params,
                    price_basis_year=price_basis_year,
                )
                if subsidy_result.decision is not None:
                    decisions.append(subsidy_result.decision)
                timeline.extend(subsidy_result.entries)

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

        # --- operating view: replacement reserve instead of investment categories (§4.2)
        if context == InstallationContext.OPERATING_ONLY and replacement_flows_for_reserve:
            timeline.extend(
                build_replacement_reserve_entries(replacement_flows_for_reserve, params, horizon)
            )

        # --- macroeconomic CO2 damage (§4.5)
        if macro:
            timeline.extend(build_co2_damage_entries(co2_result, params, horizon))

        # --- financing (§4.4). W3.2: what financing depends on — the year-0 net investment
        # already on the timeline and the LOAN_TERMS award the subsidy phase decided — is
        # resolved here and passed in, instead of being re-derived inside the calculator.
        if perspective.financing is not None and include_investment:
            loan_plan = resolve_loan_plan(perspective.financing, decisions)
            year0_net = compute_year0_net_investment(timeline)
            timeline.extend(build_financing_flows(loan_plan, year0_net, params.observation_period_in_years))

        finalize_total_co2(co2_result)
        # --- modernization-levy basis (§6.4). W3.4: the support figure is read off the finished
        # timeline — after financing, so the repayment grant counts — instead of accumulated
        # while entries are emitted. Nominal euros received, per the §6.4 decision.
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
        )

    # ------------------------------------------------------------------ evaluation (§3.7)

    def evaluate(
        self,
        inputs: EvaluationInputs,
        perspective: Perspective,
        ledger: Optional[ProvenanceLedger] = None,
    ) -> LifecycleCostResult:
        """Evaluates one perspective: timeline -> allocation -> discounting -> result.

        The engine's main entry point and the second half of an evaluation: it builds the timeline
        (`build_timeline`), lets the country's allocation ruleset stamp a payer on every entry
        whenever the perspective is actor-scoped (§6), and hands the finished timeline to
        `calculators/aggregation.aggregate_timeline`, which derives the NPV, the equivalent annual
        cost, the category/component/payer pivots, the per-subject breakdowns, the nominal
        liquidity series and the LCOH from it. Nothing is computed twice: what the result carries
        beyond that aggregation is the build's non-cash output (subsidy decisions, CO2 masses,
        sunk cost) and the physical context presentation needs (W4.2, W4.6).

        Allocation is the one step that can *create* money movements rather than only re-tag them
        — the §6.4 modernization levy mints a tenant/landlord transfer pair from the levy basis
        the build recorded (W3.6). The result's `timeline` is therefore always the *full*
        allocated timeline, every payer included, so the §6.5 zero-sum invariant stays checkable;
        the flows this perspective actually reports on are `scope_payer` plus `scoped_timeline()`.

        Args:
            inputs: The variant's extract (see `EvaluationInputs`); never modified.
            perspective: The five dimensions being evaluated — installation context, actor scope,
                subsidy mode, financing and accounting (§4).
            ledger: Provenance ledger to record into; a fresh one is created when omitted. It is
                stored on the result either way, which is what makes `explain()` work without
                extra plumbing (§3.10).

        Returns:
            The `LifecycleCostResult` for this perspective. Every monetary field is a
            LOW/BEST_ESTIMATE/HIGH band (§3.9), and `parameters` is the *effective* parameter set, with
            the resolved price basis year filled in so reports need not re-derive it (W1.2).
        """
        # Same values as self.parameters, but with the resolved price basis year recorded (W1.2).
        params = self.effective_parameters(inputs)
        ledger = ledger or ProvenanceLedger()
        build = self.build_timeline(inputs, perspective, ledger)
        timeline = build.timeline
        co2_result = build.co2_result

        # Actor allocation (§6).
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

        aggregation = aggregate_timeline(
            timeline=timeline,
            actor_scope=perspective.actor_scope,
            facts_by_subject={
                subject_facts.subject: subject_facts.facts for subject_facts in inputs.cost_facts
            },
            co2_result=co2_result,
            parameters=params,
            annual_heat_demand_in_kwh=inputs.annual_heat_demand_in_kwh,
        )

        return LifecycleCostResult(
            perspective_id=perspective.id,
            parameters=params,
            total_npv_in_euro=aggregation.total_npv_in_euro,
            equivalent_annual_cost_in_euro=aggregation.equivalent_annual_cost_in_euro,
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
            # report never have to reach back into `EvaluationInputs` (W4.2):
            annual_energy_quantities_by_carrier=annual_energy_quantities(
                inputs.billing, inputs.simulated_period_fraction
            ),
            reference_areas=ReferenceAreas(
                heated_floor_area_in_m2=inputs.heated_floor_area_in_m2,
                living_area_in_m2=inputs.living_area_in_m2,
            ),
            simulated_period_fraction=inputs.simulated_period_fraction,
            simulation_year=inputs.simulation_year,
            # Diagnostics rather than result: the pre-clamp §8.5 flexibility value, which the
            # plausibility panel warns about when it is negative (issue #25b).
            raw_flexibility_value_by_carrier=build.raw_flexibility_value_by_carrier,
        )

    def _source_resolver(self) -> Dict[str, ResolvedSource]:
        """Every registry a result's provenance can cite: cost database *and* subsidy catalog.

        The two registries are separate files with disjoint id spaces; before W2.4 only the cost
        database's was handed to the result, so a subsidy-derived record could never resolve its
        sources at a report leaf. Cost-database entries win a (never observed) id collision, since
        that registry backs the bulk of the records.
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
        """Evaluates a set of perspectives against the same simulation results (§4).

        The standard way the engine is driven: one simulation extract, the default nine-row
        perspective bundle (`cost_database/perspectives_default.json`, §7.1), and one independent
        evaluation each — a perspective is a *view* of the same variant, never a different
        variant, so there is nothing to share between them but the inputs. Each row gets its own
        provenance ledger, since each resolves its own parameters.

        Args:
            inputs: The variant's extract; never modified.
            perspectives: The bundle to evaluate, already pruned by the caller via
                `perspectives.select_applicable` — greenfield rows drop out when an existing-asset
                register is present, brownfield/status-quo rows when there is none.

        Returns:
            An `EvaluationMatrix` keyed by `Perspective.id`, in the order given — the object the
            exports, the reports and the KPI layer all read.
        """
        matrix = EvaluationMatrix()
        for perspective in perspectives:
            matrix.results[perspective.id] = self.evaluate(inputs, perspective)
        return matrix
