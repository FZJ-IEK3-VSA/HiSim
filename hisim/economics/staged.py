"""The staged evaluator: one renovation plan spread over several years, priced once.

The engine of :mod:`hisim.economics` prices **one** simulated state over one horizon with the
whole investment in year 0. A RenoVisor plan is not that: the house is in state ``S0`` until
year ``t1``, in ``S1`` until ``t2`` and so on, each stage's investment falls in its own year, and
each earlier stage's equipment ages as an existing asset for the stages that follow. This module
adds exactly that, without adding a second implementation of the money.

How it does so is the design decision worth reading first: the plan's cash flows are a **splice**
of per-stage evaluations, never a new calculation. Every stage is evaluated alone over the full
horizon with the ordinary :class:`~hisim.economics.evaluator.EconomicEvaluator`; the plan's
timeline then takes each year's operating flows from whichever stage is active in that year, and
each stage's own investment-class flows moved to the year that stage starts in. A plan of one
stage therefore equals a plain evaluation entry for entry by construction rather than by
agreement between two code paths, which is the first of the five invariants
``tests/economics/test_staged.py`` pins down.

Ageing across stages is not a mechanism of this module either. Before stage ``k`` is evaluated,
its :class:`~hisim.economics.evaluator.EvaluationInputs` gets a register that holds the house
inventory *plus* one :class:`~hisim.economics.facts.ExistingAsset` per subject an earlier stage
already paid for, installed in that earlier stage's year. Replacements, residual values, removal
costs and the anyway-cost credit then fall in the right years through the brownfield machinery
the engine already has (``cost_spec.md`` §4.1). The consequence is that stages are evaluated
**in order** and stage ``k``'s inputs depend on every ``j < k``.

Example::

    stages = [Stage(baseline_inputs, 0, "baseline"),
              Stage(envelope_inputs, 0, "stage 1", ("external_insulation",)),
              Stage(heat_pump_inputs, 3, "stage 2", ("heating_system",))]
    result = StagedEvaluator(database).evaluate(stages, parameters, perspective, catalog)
    result.plan.total_npv_in_euro.best_estimate

Specification: ``/home/contract-proposals/economics-hisim-spec.md`` §1 (the requirements) read
together with ``roadmap/renovisor/implementation/step10_staged_economics.md`` §2 (the engine-side
decisions), which wins where the two disagree.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Dict, FrozenSet, List, Optional, Sequence, Set, Tuple

from hisim.economics.calculators.aggregation import aggregate_timeline
from hisim.economics.carriers import EnergyCarrier
from hisim.economics.database import CostDatabase
from hisim.economics.evaluator import EconomicEvaluator, EvaluationInputs
from hisim.economics.facts import ExistingAsset, ExistingAssetRegister
from hisim.economics.parameters import EconomicParameters
from hisim.economics.perspectives import Perspective
from hisim.economics.results import (
    LifecycleCo2Result,
    LifecycleCostResult,
    ReferenceAreas,
    VariantComparison,
    compare,
)
from hisim.economics.subsidies import SubsidyCatalog
from hisim.economics.timeline import CashFlowEntry, CashFlowTimeline, CostCategory
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import ComponentType


class StagedEvaluationError(Exception):
    """A plan that cannot be priced as stated, named rather than defaulted around.

    Every condition this is raised for is a statement about the *plan*, not about the engine: a
    first stage that does not start in year 0, stage years that do not increase, a stage that
    starts past the horizon, stages priced from simulations that do not describe the same year or
    the same simulated period, an unknown perspective id, or a country the cost database has no
    price data for. Each of them would otherwise be answered with a plausible number for a
    different question, which ``economics-hisim-spec.md`` §0 forbids ("fail loudly").

    The CLI (``python -m hisim.economics staged``) turns it into exit code 2 with a
    ``problems.json`` beside the requested output; an *engine* error — an unresolvable cost
    subject, a missing data file — stays what it is and becomes exit code 3.
    """


class StagedCategories:
    """Which cost categories the splice takes from where (step 10 §2, E-spec §1.2).

    The splice has to decide, for every entry of every stage's own evaluation, whether it belongs
    to the plan and in which year. Three answers exist and this class names the three sets they
    are chosen by; nothing else in the module hard-codes a :class:`CostCategory`.

    * :attr:`STAGE_START` — the flows a stage *causes when it starts*. They are read off the
      stage's own year-0 entries, moved to the year that stage begins in, and escalated to that
      year with the investment escalation rate. Only subjects the stage actually pays for are
      taken (see :meth:`StagedEvaluator._charge_share`), so a device carried over from the
      previous stage is never bought twice.
    * :attr:`LOAN_SCHEDULE` — the debt service of a stage's loan. It is not a year-0 flow but it
      belongs to the stage that borrowed, so the whole schedule is shifted by the stage's start
      year and anything falling past the horizon is dropped.
    * everything else — the operating flows of E-spec §1.2 (energy, maintenance, fixed operation,
      feed-in, CO2 price, levy, replacement reserve) *plus* the replacement and residual-value
      flows that E-spec §1.2 item 4 derives from the ageing register. All of them are taken from
      the stage that is active in the entry's own year, which is what makes "year y of the plan is
      year y of whichever state the house is in" literally true.
    """

    #: Booked in the year the stage starts, escalated to it, for the subjects the stage pays for.
    STAGE_START: FrozenSet[CostCategory] = frozenset(
        {
            CostCategory.INVESTMENT,
            CostCategory.PLANNING,
            CostCategory.REMOVAL,
            CostCategory.SUBSIDY,
            CostCategory.ANYWAY_COST_CREDIT,
            CostCategory.LOAN_DISBURSEMENT,
        }
    )

    #: Shifted as a whole by the stage's start year: the stage's own debt service.
    LOAN_SCHEDULE: FrozenSet[CostCategory] = frozenset(
        {CostCategory.LOAN_INTEREST, CostCategory.LOAN_PRINCIPAL}
    )


@dataclass(frozen=True)
class _StageCharges:
    """What one stage pays for, and at what price level, as the splice needs to ask it.

    Four facts the splice consults per entry, bundled so the decision reads as one question
    instead of four parallel lookups. Internal to this module: the splice is the only caller.

    Attributes:
        charged: Subject -> the share of its year-0 investment-class flows the stage pays.
        carried_over: Subjects the stage inherits unchanged and must not pay for again.
        rates: Subject -> its own investment escalation rate, for the subjects with an asset class.
        default_rate: The general investment escalation rate, used for the synthetic subjects
            (``financing``, the replacement reserve) that no technology's learning curve applies
            to but whose flows still move to the stage's year with everything else.
    """

    charged: Dict[str, float]
    carried_over: Set[str]
    rates: Dict[str, float]
    default_rate: float

    def share_of(self, subject: str) -> float:
        """How much of one subject's year-0 figure this stage pays: 1.0, an increment, or none."""
        if subject in self.charged:
            return self.charged[subject]
        return 0.0 if subject in self.carried_over else 1.0

    def escalation_factor(self, subject: str, from_year: int) -> float:
        """The price level of the stage's year relative to year 0, for one subject."""
        return (1.0 + self.rates.get(subject, self.default_rate)) ** from_year


@dataclass(frozen=True)
class Stage:
    """One state of the house, the year the plan puts it in, and what got it there.

    A stage is a *completed simulation* seen through its stored ``economic_inputs.json`` plus the
    two facts the simulation cannot know: when the plan intends to build it and what it is called.
    ``stages[0]`` is the reference — the state the house is in today, held over the whole horizon —
    and must therefore start in year 0.

    Args:
        inputs: The stage's simulated state, as :func:`hisim.economics.serialization.read_inputs`
            reads it back from a finished job's directory.
        from_year: The year, relative to the start of the horizon, the stage becomes the state of
            the house. 0 for the first stage and never decreasing afterwards; a stage sharing its
            predecessor's year supersedes it immediately, which is the plain baseline-versus-
            package plan.
        label: What the frontend calls this stage ("baseline", "stage 1"). Opaque here: nothing in
            the engine branches on it, and it is copied into the result document unchanged.
        measures: Catalogue measure ids added *in* this stage. Provenance only — the money follows
            from the simulated state, not from this tuple.
        job_id: The backend's id of the job that produced ``inputs``, or ``None`` when the caller
            did not name one. Carried into the document so a stage can be traced to its run.
    """

    inputs: EvaluationInputs
    from_year: int
    label: str
    measures: Tuple[str, ...] = ()
    job_id: Optional[str] = None


@dataclass
class StagedResult:
    """One priced plan: its reference, its staged self, and the difference between them.

    Everything the result document of E-spec §3 publishes is a filter or a pivot of the two
    :class:`~hisim.economics.results.LifecycleCostResult` objects here, which is why they are
    carried whole rather than reduced to KPIs. ``per_stage`` is kept for the same reason: the
    per-subject figures of the document's investment build-up are read off the stage that paid for
    the subject, and a reviewer checking the splice needs the pieces it was spliced from.

    Attributes:
        reference: ``stages[0]`` evaluated alone over the whole horizon — the "do nothing" case.
        plan: The staged plan: one timeline spliced from ``per_stage``, aggregated exactly as an
            ordinary evaluation is.
        comparison: ``results.compare(reference, plan, "reference", "plan")``; deltas are
            plan minus reference, slot-wise, so a negative NPV delta means the plan is cheaper.
        stages: The stages as they were given, in order.
        per_stage: Each stage evaluated alone over the full horizon, in stage order. Stage ``k``'s
            evaluation already carries the merged ageing register of every stage before it.
        charged_subjects_by_stage: Which cost subjects each stage pays for, and at which share of
            the stage's own year-0 figure — 1.0 for a subject new in the stage, the size increment
            for one that grew, and absent for a subject carried over unchanged.
    """

    reference: LifecycleCostResult
    plan: LifecycleCostResult
    comparison: VariantComparison
    stages: Tuple[Stage, ...]
    per_stage: Tuple[LifecycleCostResult, ...]
    charged_subjects_by_stage: Tuple[Dict[str, float], ...] = field(default_factory=tuple)

    def stage_of_year(self, year: int) -> int:
        """Index of the stage the house is in during one horizon year.

        Args:
            year: A year index relative to the start of the horizon.

        Returns:
            The index of the last stage whose ``from_year`` is at or below ``year``; 0 for a year
            before any stage after the first has started.
        """
        active = 0
        for index, stage in enumerate(self.stages):
            if stage.from_year <= year:
                active = index
        return active

    def stage_of_subject(self, subject: str) -> Optional[int]:
        """Index of the stage that paid for one cost subject, or ``None`` when none did.

        The document stamps this on every ``by_subject`` row so the frontend can draw the
        investment build-up per stage. A subject of the baseline that is never re-bought — a
        boiler kept throughout — belongs to no stage and is reported as ``None``.

        Args:
            subject: The timeline subject name.

        Returns:
            The index of the *last* stage that charged the subject, or ``None``.
        """
        found: Optional[int] = None
        for index, charged in enumerate(self.charged_subjects_by_stage):
            if subject in charged:
                found = index
        return found


class StagedEvaluator:
    """Prices a staged renovation plan by splicing per-stage evaluations (E-spec §1).

    One instance binds a :class:`~hisim.economics.database.CostDatabase`; the assumptions, the
    perspective and the subsidy catalogue are arguments of :meth:`evaluate`, because the same
    stored stages are routinely re-priced under different assumptions and re-reading the data
    files for each of them would dominate the runtime. Three stages evaluate in well under a
    second: no simulation runs and no file is read inside the calculation.

    Example::

        evaluator = StagedEvaluator(CostDatabase(None))
        result = evaluator.evaluate(stages, EconomicParameters(country="IE"), perspective, None)

    The class holds no state between calls and mutates none of its arguments: a stage's
    ``EvaluationInputs`` is copied before its register is replaced, so the caller's record still
    describes the simulation it came from.
    """

    #: Message of the refusal raised when the first stage does not start the plan.
    FIRST_STAGE_MESSAGE = (
        "the first stage of a plan is its reference and must start in year 0, but "
        "stages[0].from_year is {from_year}."
    )

    #: Message of the refusal raised when the stage years run backwards.
    ORDER_MESSAGE = (
        "stage years must not run backwards: stages[{index}] ({label!r}) starts in year "
        "{from_year}, before stages[{previous_index}] in year {previous_year}. Two stages may "
        "share a year — the later one supersedes the earlier one immediately, which is the "
        "ordinary baseline-versus-package plan — but a plan cannot go back in time."
    )

    #: Message of the refusal raised when a stage starts beyond the horizon.
    HORIZON_MESSAGE = (
        "stages[{index}] ({label!r}) starts in year {from_year}, past the last year that could "
        "follow the {horizon}-year horizon. A stage at year {never} is the degenerate 'never "
        "within the horizon' plan and is accepted; anything later prices nothing at all."
    )

    #: Message of the refusal raised when two stages were simulated under different conditions.
    MISMATCH_MESSAGE = (
        "stages[{index}] ({label!r}) has {name} {value!r} but stages[0] has {reference!r}; the "
        "stages of one plan must describe the same building-year and the same simulated period, "
        "or their flows cannot be put on one timeline."
    )

    #: Message of the refusal raised when the country has no price data.
    COUNTRY_MESSAGE = (
        "no energy price data for country {country!r} in the configured cost database, so the "
        "plan cannot be priced. Add hisim/cost_database/energy_prices_{country}.json or price the "
        "plan for a country that has one."
    )

    def __init__(self, cost_database: CostDatabase) -> None:
        """Bind the evaluator to one cost database.

        Args:
            cost_database: The device, price and escalation data every stage is priced against.
                Held as given and never modified.
        """
        self.database = cost_database

    def evaluate(
        self,
        stages: Sequence[Stage],
        parameters: EconomicParameters,
        perspective: Perspective,
        catalog: Optional[SubsidyCatalog] = None,
    ) -> StagedResult:
        """Price one plan: evaluate every stage, splice the timelines, compare against stage 0.

        The seven steps of step 10 §2, in order: validate the plan, evaluate each stage over the
        full horizon with the ageing register of the stages before it, splice the operating flows
        by active year and the investment flows by stage start year, aggregate the spliced
        timeline exactly as an ordinary evaluation aggregates its own, and compare the result
        against ``stages[0]``.

        Args:
            stages: The plan, in ascending ``from_year`` order; at least one stage.
            parameters: The assumptions every stage is priced under — one set for the whole plan,
                because a plan whose stages disagree about the interest rate is not one plan.
            perspective: The accounting frame (installation context, actor scope, subsidy mode,
                financing, accounting) every stage is evaluated under.
            catalog: The subsidy catalogue in force, or ``None`` for a plan priced with no
                catalogue at all, where every scheme stays undetermined.

        Returns:
            The :class:`StagedResult`.

        Raises:
            StagedEvaluationError: For any condition of the class docstring's list — a plan this
                module refuses to price.
            hisim.economics.evaluator.UnresolvableSubjectsError: When a stage declares a cost
                subject nothing can price (D7). An engine error, deliberately not wrapped.
        """
        ordered = tuple(stages)
        self._validate(ordered, parameters)
        evaluator = EconomicEvaluator(self.database, parameters, catalog)

        per_stage: List[LifecycleCostResult] = []
        charged_by_stage: List[Dict[str, float]] = []
        for index in range(len(ordered)):
            charged = self._charged_subjects(ordered, index)
            inputs = self._staged_inputs(ordered, index, charged_by_stage, parameters)
            per_stage.append(evaluator.evaluate(inputs, perspective))
            charged_by_stage.append(charged)

        timeline = self._splice(ordered, tuple(per_stage), tuple(charged_by_stage), evaluator, parameters)
        plan = self._aggregate(ordered, tuple(per_stage), timeline, perspective, parameters)
        reference = per_stage[0]
        return StagedResult(
            reference=reference,
            plan=plan,
            comparison=compare(reference, plan, "reference", "plan"),
            stages=ordered,
            per_stage=tuple(per_stage),
            charged_subjects_by_stage=tuple(charged_by_stage),
        )

    # ------------------------------------------------------------------ validation

    def _validate(self, stages: Tuple[Stage, ...], parameters: EconomicParameters) -> None:
        """Refuse a plan that cannot be put on one timeline, naming what is wrong with it.

        Checked in the order a reader would check them: is there a plan at all, does it start at
        year 0, do the years run forwards, does every stage start inside the horizon, were the
        stages simulated under comparable conditions, and does the country have price data. The
        last one is asked of the database rather than of a country list, so a country whose files
        are added tomorrow works with no code change.

        Equal years are accepted although E-spec §1.1 says "strictly increasing": the E-spec's own
        §3 example and step 10 §7's end-to-end plan both put the baseline and the first package
        stage in year 0, and that is the ordinary "what does the package cost against doing
        nothing" question. A stage sharing its predecessor's year supersedes it immediately, which
        is what :meth:`_active_by_year` does with no special case.

        Args:
            stages: The plan as given.
            parameters: The assumptions, for the horizon and the country.

        Raises:
            StagedEvaluationError: Naming the first condition that fails.
        """
        if not stages:
            raise StagedEvaluationError("a staged evaluation needs at least one stage; none were given.")
        if stages[0].from_year != 0:
            raise StagedEvaluationError(self.FIRST_STAGE_MESSAGE.format(from_year=stages[0].from_year))
        horizon = parameters.observation_period_in_years
        for index in range(1, len(stages)):
            stage, previous = stages[index], stages[index - 1]
            if stage.from_year < previous.from_year:
                raise StagedEvaluationError(
                    self.ORDER_MESSAGE.format(
                        index=index,
                        label=stage.label,
                        from_year=stage.from_year,
                        previous_index=index - 1,
                        previous_year=previous.from_year,
                    )
                )
        for index, stage in enumerate(stages):
            # `horizon + 1` is the first year outside the horizon and is accepted on purpose: a
            # stage there never becomes active, which is the "equals the baseline alone" case
            # E-spec §1.3 pins down. Anything beyond it prices nothing and says nothing.
            if stage.from_year > horizon + 1:
                raise StagedEvaluationError(
                    self.HORIZON_MESSAGE.format(
                        index=index,
                        label=stage.label,
                        from_year=stage.from_year,
                        horizon=horizon,
                        never=horizon + 1,
                    )
                )
        self._validate_comparable(stages)
        if not any(self.database.has_energy_price(carrier, parameters.country) for carrier in EnergyCarrier):
            raise StagedEvaluationError(self.COUNTRY_MESSAGE.format(country=parameters.country))

    def _validate_comparable(self, stages: Tuple[Stage, ...]) -> None:
        """Refuse stages whose simulations describe different years or different periods.

        Two figures must agree across a plan or the splice puts incomparable numbers next to each
        other: ``simulation_year``, because it anchors every calendar year the document prints and
        every database lookup, and ``simulated_period_fraction``, because it is the divisor that
        turns a simulated period into a year. Everything else a stage carries is allowed to differ
        — that is what makes it a different state of the house.

        Args:
            stages: The plan as given.

        Raises:
            StagedEvaluationError: Naming the stage, the field and the two values.
        """
        first = stages[0].inputs
        for index, stage in enumerate(stages[1:], start=1):
            for name, value, reference in (
                ("simulation_year", stage.inputs.simulation_year, first.simulation_year),
                (
                    "simulated_period_fraction",
                    stage.inputs.simulated_period_fraction,
                    first.simulated_period_fraction,
                ),
            ):
                if value != reference:
                    raise StagedEvaluationError(
                        self.MISMATCH_MESSAGE.format(
                            index=index,
                            label=stage.label,
                            name=name,
                            value=value,
                            reference=reference,
                        )
                    )

    # ------------------------------------------------------------------ per-stage inputs

    @classmethod
    def _charged_subjects(cls, stages: Tuple[Stage, ...], index: int) -> Dict[str, float]:
        """Which subjects stage ``index`` pays for, and at what share of its own year-0 figure.

        E-spec §1.2 item 2: a subject is charged when it is present in ``S_k`` and absent from
        ``S_{k-1}`` under the same asset class, or present in both but larger, in which case only
        the increment is charged. A subject carried over unchanged is not charged again, and is
        absent from the returned mapping rather than present with a zero, so "did this stage buy
        this" is one membership test.

        Args:
            stages: The plan as given.
            index: The stage to answer for.

        Returns:
            Subject name -> the share of that subject's year-0 investment-class flows the stage
            pays, in ``(0, 1]``.
        """
        current = {facts.subject: facts.facts for facts in stages[index].inputs.cost_facts}
        if index == 0:
            return {subject: 1.0 for subject in current}
        previous = {facts.subject: facts.facts for facts in stages[index - 1].inputs.cost_facts}
        charged: Dict[str, float] = {}
        for subject, facts in current.items():
            before = previous.get(subject)
            if before is None or before.asset_class != facts.asset_class:
                charged[subject] = 1.0
                continue
            if facts.size > before.size > 0.0:
                charged[subject] = (facts.size - before.size) / facts.size
        return charged

    @classmethod
    def _carried_over_subjects(cls, stages: Tuple[Stage, ...], index: int) -> Set[str]:
        """Subjects stage ``index`` inherits from its predecessor without paying for them again.

        The complement of :meth:`_charged_subjects` among the stage's own cost subjects, and the
        set the splice uses to decide what *not* to book. Everything else a stage's year-0 entries
        mention — the synthetic ``financing`` subject, an entry filed under an asset the stage
        tears out — is neither charged nor carried over, and is taken as the stage's own.

        Args:
            stages: The plan as given.
            index: The stage to answer for.

        Returns:
            The subject names carried over unchanged; empty for stage 0.
        """
        if index == 0:
            return set()
        charged = cls._charged_subjects(stages, index)
        previous = {facts.subject for facts in stages[index - 1].inputs.cost_facts}
        return {
            facts.subject
            for facts in stages[index].inputs.cost_facts
            if facts.subject in previous and facts.subject not in charged
        }

    def _staged_inputs(
        self,
        stages: Tuple[Stage, ...],
        index: int,
        charged_by_stage: List[Dict[str, float]],
        parameters: EconomicParameters,
    ) -> EvaluationInputs:
        """Stage ``index``'s inputs with the ageing register of every earlier stage merged in.

        This is step 10 §2 item 4, and it is the whole of "ageing across stages": the register the
        stage is evaluated with holds the house inventory *minus* whatever an earlier stage has
        already torn out, *plus* one :class:`~hisim.economics.facts.ExistingAsset` per subject an
        earlier stage paid for, installed in that stage's year. The engine's brownfield machinery
        then schedules the replacements, the residual values and the removal costs itself; this
        module adds no second mechanism.

        Stage 0 is returned unchanged — it is the reference, and its register is the inventory as
        the translator declared it.

        Args:
            stages: The plan as given.
            index: The stage to build inputs for.
            charged_by_stage: What every *earlier* stage charged, in stage order; this method is
                called in order, so the list holds exactly ``index`` entries.
            parameters: The assumptions, for nothing but the documented horizon in error text.

        Returns:
            A copy of the stage's inputs carrying the merged register. The caller's record is not
            modified.
        """
        del parameters  # the merge needs only the stages themselves; kept for call symmetry
        stage = stages[index]
        if index == 0:
            return stage.inputs
        simulation_year = stage.inputs.simulation_year
        facts_by_subject = {facts.subject: facts.facts for facts in stage.inputs.cost_facts}
        newly_charged_classes = {
            facts_by_subject[subject].asset_class
            for subject in self._charged_subjects(stages, index)
            if subject in facts_by_subject
        }
        installed_classes: Set[ComponentType] = set()
        aged: Dict[str, ExistingAsset] = {}
        for earlier in range(index):
            earlier_facts = {facts.subject: facts.facts for facts in stages[earlier].inputs.cost_facts}
            for subject, share in charged_by_stage[earlier].items():
                facts = earlier_facts.get(subject)
                if facts is None:
                    continue
                del share  # the register records the asset, not the share that was paid for it
                installed_classes.add(facts.asset_class)
                aged[subject] = ExistingAsset(
                    asset_class=facts.asset_class,
                    size=facts.size,
                    size_unit=facts.size_unit,
                    installation_year=simulation_year + stages[earlier].from_year,
                    is_functional=True,
                    replaced_by_asset_classes=(
                        [facts.asset_class]
                        if subject not in facts_by_subject and facts.asset_class in newly_charged_classes
                        else []
                    ),
                )
        assets = [
            asset
            for asset in self._inventory(stage.inputs)
            if asset.asset_class not in installed_classes
            and not installed_classes.intersection(asset.replaced_by_asset_classes)
        ]
        assets.extend(aged[subject] for subject in sorted(aged))
        return replace(stage.inputs, existing_assets=ExistingAssetRegister(assets=assets))

    @staticmethod
    def _inventory(inputs: EvaluationInputs) -> List[ExistingAsset]:
        """The house inventory a stage was simulated with, as a fresh list.

        A stage with no register at all — a greenfield job — contributes an empty inventory rather
        than ``None``, so the merge below has one shape to handle.
        """
        register = inputs.existing_assets
        return list(register.assets) if register is not None else []

    # ------------------------------------------------------------------ the splice

    def _splice(
        self,
        stages: Tuple[Stage, ...],
        per_stage: Tuple[LifecycleCostResult, ...],
        charged_by_stage: Tuple[Dict[str, float], ...],
        evaluator: EconomicEvaluator,
        parameters: EconomicParameters,
    ) -> CashFlowTimeline:
        """Build the plan's one timeline out of the per-stage timelines (step 10 §2 items 2-3).

        Entries are appended stage by stage and, within a stage, in the order that stage's own
        evaluation produced them. For a plan of one stage that reproduces the original timeline
        entry for entry, which is invariant 1 of E-spec §1.3 and the reason the order is fixed
        this way rather than by sorting.

        Args:
            stages: The plan as given.
            per_stage: Each stage's own evaluation, in stage order.
            charged_by_stage: What each stage pays for, from :meth:`_charged_subjects`.
            evaluator: The bound engine, for the per-asset-class investment escalation rates.
            parameters: The assumptions, for the horizon and the general escalation rate.

        Returns:
            The spliced timeline, sign-validated like any engine timeline.
        """
        horizon = parameters.observation_period_in_years
        active_by_year = self._active_by_year(stages, horizon)
        timeline = CashFlowTimeline()
        for index, stage in enumerate(stages):
            rates = self._escalation_rates(stage, evaluator)
            charges = _StageCharges(
                charged=charged_by_stage[index],
                carried_over=self._carried_over_subjects(stages, index),
                rates=rates,
                default_rate=parameters.investment_price_escalation_rate,
            )
            for entry in per_stage[index].timeline.entries:
                spliced = self._spliced_entry(
                    entry, index, stage.from_year, charges, active_by_year, horizon
                )
                if spliced is not None:
                    timeline.add(spliced)
        return timeline

    @staticmethod
    def _active_by_year(stages: Tuple[Stage, ...], horizon: int) -> Tuple[int, ...]:
        """For every horizon year 0..T, the index of the stage the house is in.

        A stage is active from its own ``from_year`` until the next stage's, the last one to the
        end of the horizon; a stage starting past the horizon is never active, which is how the
        degenerate plan of E-spec §1.3 invariant 2 comes out equal to its baseline.

        Args:
            stages: The plan as given, in ascending year order.
            horizon: The last year index of the horizon.

        Returns:
            One stage index per year 0..T.
        """
        active: List[int] = []
        for year in range(horizon + 1):
            index = 0
            for candidate, stage in enumerate(stages):
                if stage.from_year <= year:
                    index = candidate
            active.append(index)
        return tuple(active)

    @staticmethod
    def _escalation_rates(stage: Stage, evaluator: EconomicEvaluator) -> Dict[str, float]:
        """The investment escalation rate to apply to each of a stage's subjects.

        A stage's year-0 figures are prices of year 0; booking them in year ``t`` means paying
        year-``t`` prices, so they are escalated by that subject's own investment escalation rate
        (§3.2's fallback chain: explicit parameter, then the country defaults file, then the
        general rate). Subjects with no asset class of their own — the synthetic ``financing``
        subject, the replacement reserve — get the general rate, because there is no technology
        whose learning curve would apply to them.

        Args:
            stage: The stage whose subjects are being rated.
            evaluator: The bound engine, which owns the fallback chain.

        Returns:
            Subject name -> nominal annual rate. Subjects absent from the mapping are escalated at
            zero, because the only entries they carry are the loan flows, whose principal is
            already the escalated year-0 net investment.
        """
        return {
            facts.subject: evaluator.investment_escalation_rate(facts.facts.asset_class)
            for facts in stage.inputs.cost_facts
        }

    def _spliced_entry(
        self,
        entry: CashFlowEntry,
        index: int,
        from_year: int,
        charges: "_StageCharges",
        active_by_year: Tuple[int, ...],
        horizon: int,
    ) -> Optional[CashFlowEntry]:
        """One source entry's place in the plan, or ``None`` when it has none.

        The three-way decision of :class:`StagedCategories`, applied to one entry. A stage-start
        entry is kept only for the subjects the stage pays for, moved to the stage's year and
        escalated to it; a loan entry is shifted by the same offset without a charge test, because
        the loan is the stage's whichever subjects it financed; everything else is kept only when
        the entry's own year belongs to this stage.

        Args:
            entry: One entry of the stage's own evaluation.
            index: The stage's index.
            from_year: The stage's start year.
            charges: What the stage pays for and at which escalation rate.
            active_by_year: Which stage is active in each horizon year.
            horizon: The last year index of the horizon.

        Returns:
            The entry as it appears on the plan's timeline, or ``None``.
        """
        if entry.category in StagedCategories.STAGE_START and entry.year == 0:
            share = charges.share_of(entry.subject)
            if share <= 0.0 or from_year > horizon:
                return None
            factor = share * charges.escalation_factor(entry.subject, from_year)
            return replace(entry, year=from_year, amount_in_euro=entry.amount_in_euro.scale(factor))
        if entry.category in StagedCategories.LOAN_SCHEDULE:
            year = entry.year + from_year
            if year > horizon:
                return None
            factor = charges.escalation_factor(entry.subject, from_year)
            return replace(entry, year=year, amount_in_euro=entry.amount_in_euro.scale(factor))
        if 0 <= entry.year <= horizon and active_by_year[entry.year] == index:
            return entry
        return None

    # ------------------------------------------------------------------ aggregation

    def _aggregate(
        self,
        stages: Tuple[Stage, ...],
        per_stage: Tuple[LifecycleCostResult, ...],
        timeline: CashFlowTimeline,
        perspective: Perspective,
        parameters: EconomicParameters,
    ) -> LifecycleCostResult:
        """Turn the spliced timeline into a result, exactly as an ordinary evaluation does.

        The KPIs come from :func:`~hisim.economics.calculators.aggregation.aggregate_timeline`, the
        same function :meth:`hisim.economics.evaluator.EconomicEvaluator.evaluate` calls, so the
        plan's NPV, annuity, category pivot, per-subject breakdown and liquidity series are filters
        of the spliced timeline and nothing else. What cannot come from a timeline — the CO2 masses
        and the subsidy decisions — is composed in :meth:`_splice_co2` and by concatenating the
        stages' decisions; the physical context (areas, period, energy volumes) is taken from the
        stage that is active where it is read, which is named per field below.

        Args:
            stages: The plan as given.
            per_stage: Each stage's own evaluation.
            timeline: The spliced timeline.
            perspective: The accounting frame, for the id and the actor scope.
            parameters: The assumptions.

        Returns:
            The plan's :class:`~hisim.economics.results.LifecycleCostResult`.
        """
        horizon = parameters.observation_period_in_years
        active = self._active_by_year(stages, horizon)
        # Year 1 is where the document's energy table and the monthly figure are read, so the
        # physical context comes from the state the house is in then.
        year_one = per_stage[active[1]] if horizon >= 1 else per_stage[0]
        last = per_stage[active[horizon]]
        facts_by_subject = {}
        for stage in stages:
            for subject_facts in stage.inputs.cost_facts:
                facts_by_subject[subject_facts.subject] = subject_facts.facts
        co2 = self._splice_co2(per_stage, active, horizon)
        aggregation = aggregate_timeline(
            timeline=timeline,
            actor_scope=perspective.actor_scope,
            facts_by_subject=facts_by_subject,
            co2_result=co2,
            parameters=parameters,
            annual_heat_demand_in_kwh=stages[active[horizon]].inputs.annual_heat_demand_in_kwh,
        )
        shares: Dict[str, float] = {}
        bases: Dict[str, float] = {}
        kinds: Dict[str, str] = {}
        for result in per_stage:
            shares.update(result.anyway_share_by_subject)
            bases.update(result.anyway_basis_by_subject)
            kinds.update(result.anyway_basis_kind_by_subject)
        return LifecycleCostResult(
            perspective_id=perspective.id,
            parameters=per_stage[0].parameters,
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
            lifecycle_co2_result=co2,
            subsidy_decisions=[decision for result in per_stage for decision in result.subsidy_decisions],
            # Each stage writes off what *it* tears out, and an asset an earlier stage removed is
            # no longer in the later stages' registers (see `_staged_inputs`), so summing counts
            # every written-off book value exactly once.
            sunk_cost_written_off_in_euro=self._sum_bands(
                [result.sunk_cost_written_off_in_euro for result in per_stage]
            ),
            ledger=per_stage[0].ledger,
            source_resolver=per_stage[0].source_resolver,
            scope_payer=aggregation.scope_payer,
            annual_energy_quantities_by_carrier=dict(year_one.annual_energy_quantities_by_carrier),
            reference_areas=ReferenceAreas(
                heated_floor_area_in_m2=last.reference_areas.heated_floor_area_in_m2,
                living_area_in_m2=last.reference_areas.living_area_in_m2,
            ),
            simulated_period_fraction=per_stage[0].simulated_period_fraction,
            simulation_year=per_stage[0].simulation_year,
            annual_energy_attribution_by_subject_in_kwh=dict(
                year_one.annual_energy_attribution_by_subject_in_kwh
            ),
            anyway_share_by_subject=shares,
            anyway_basis_by_subject=bases,
            anyway_basis_kind_by_subject=kinds,
            modernization_levy=last.modernization_levy,
            assumptions=last.assumptions,
        )

    @staticmethod
    def _sum_bands(bands: Sequence[UncertainValue]) -> UncertainValue:
        """Slot-wise sum of a list of bands, zero for an empty list."""
        total = UncertainValue.exact(0.0)
        for band in bands:
            total = total + band
        return total

    @staticmethod
    def _splice_co2(
        per_stage: Tuple[LifecycleCostResult, ...], active: Tuple[int, ...], horizon: int
    ) -> LifecycleCo2Result:
        """Compose the plan's CO2 accounting from the stages', by the same active-year rule.

        Masses are not cash flows, so they cannot be read off the spliced timeline and are
        composed here instead. Operational emissions of year ``y`` are the active stage's own
        year-``y`` emissions; embodied emissions are the sum over stages of what each stage's
        evaluation attributes to the subjects that stage installed, where "installed" is read off
        the stage's register rather than off the charge shares, because a stage's embodied mass is
        booked per subject by ``calculators/co2.py``.

        The per-carrier totals are apportioned by active years: the engine holds emission factors
        constant over the horizon (:class:`~hisim.economics.results.LifecycleCo2Result`), so a
        stage's carrier total divided by the horizon and multiplied by the years that stage is
        active is the exact figure, not an estimate.

        Args:
            per_stage: Each stage's own evaluation.
            active: Which stage is active in each horizon year.
            horizon: The last year index of the horizon.

        Returns:
            The plan's CO2 accounting.
        """
        by_year: List[float] = []
        for year in range(horizon + 1):
            series = per_stage[active[year]].lifecycle_co2_result.operational_co2_by_year_in_kg
            by_year.append(series[year] if year < len(series) else 0.0)
        by_carrier: Dict[str, float] = {}
        for index, result in enumerate(per_stage):
            years_active = sum(1 for year in range(1, horizon + 1) if active[year] == index)
            if years_active == 0 or horizon == 0:
                continue
            for carrier, mass in result.lifecycle_co2_result.operational_co2_by_carrier_in_kg.items():
                by_carrier[carrier] = by_carrier.get(carrier, 0.0) + mass * years_active / horizon
        embodied_by_subject: Dict[str, float] = {}
        factors: Dict[str, float] = {}
        for result in per_stage:
            factors.update(result.lifecycle_co2_result.emission_factor_by_carrier_in_kg_per_kwh)
            for subject, mass in result.lifecycle_co2_result.embodied_by_subject_in_kg.items():
                embodied_by_subject[subject] = max(embodied_by_subject.get(subject, 0.0), mass)
        embodied = sum(embodied_by_subject.values())
        return LifecycleCo2Result(
            embodied_co2_in_kg=embodied,
            operational_co2_by_year_in_kg=by_year,
            operational_co2_by_carrier_in_kg=by_carrier,
            total_co2_in_kg=embodied + sum(by_year),
            embodied_by_subject_in_kg=embodied_by_subject,
            emission_factor_by_carrier_in_kg_per_kwh=factors,
        )
