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

Specification: ``specs/economics-hisim-spec.md`` of the renovisorissues project §1 (the requirements) read
together with ``roadmap/renovisor/implementation/step10_staged_economics.md`` §2 (the engine-side
decisions), which wins where the two disagree.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Sequence, Set, Tuple

from hisim.economics.calculators.aggregation import aggregate_timeline
from hisim.economics.calculators.energy import (
    StatedPrices,
    priced_contract,
    year_one_co2_price_per_kwh,
)
from hisim.economics.calculators.escalation import resolve_carrier_escalation_rate
from hisim.economics.calculators.investment import InvestmentDating
from hisim.economics.calculators.reserve import replacement_reserve_amount
from hisim.economics.carriers import EnergyCarrier
from hisim.economics.database import CostDatabase
from hisim.economics.evaluator import EconomicEvaluator, EvaluationInputs, effective_price_basis_year
from hisim.economics.facts import ComponentCostFacts, ExistingAsset, ExistingAssetRegister
from hisim.economics.parameters import EconomicParameters
from hisim.economics.perspectives import Perspective, SubsidyMode
from hisim.economics.provenance import ProvenanceLedger
from hisim.economics.results import (
    LifecycleCo2Result,
    LifecycleCostResult,
    ReferenceAreas,
    ResolvedRate,
    TariffAssumption,
    VariantComparison,
    compare,
)
from hisim.economics.staged_parameters import (
    EchoedPrice,
    EchoOrigin,
    EnergyEcho,
    ParameterKeys,
    ParameterProblem,
    ParameterProblemCodes,
)
from hisim.economics.subsidies import SubsidyCatalog
from hisim.economics.tariffs import FeedInKind
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

    Most refusals are one sentence about one plan, which is what the message carries. A refused
    ``--parameters`` file is the exception: it reports every offending key at once, so the rows
    are carried alongside the message and become the ``problems.json`` rows verbatim. A raise
    without them produces a one-row file worded from the message, which is what every existing
    call site relies on.

    Args:
        message: The refusal, in one sentence.
        problems: The rows ``problems.json`` should carry, each already in its published shape
            (``path``, ``code``, ``message`` and optionally ``accepted``); empty for a refusal
            that is one sentence about the plan as a whole.
    """

    def __init__(self, message: str, problems: Sequence[Mapping[str, Any]] = ()) -> None:
        """Store the message and, for a parameter refusal, the per-key problem rows."""
        super().__init__(message)
        self.problems: Tuple[Mapping[str, Any], ...] = tuple(problems)


class StagedEngineError(RuntimeError):
    """The engine contradicted itself while pricing a plan: never the caller's fault.

    Raised when the stages of one plan, priced under one parameter set against one database,
    resolve a carrier's escalation rate or tariff differently — which the engine's own construction
    rules out, so a disagreement is a defect to report rather than a plan to refuse. The CLI turns
    it into exit code 3.
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
    * :attr:`OWN_PURCHASE_AGEING` — the REPLACEMENT entries of a subject the stage *pays for*.
      A unit bought in the stage's own year 0 is bought in plan year ``from_year``, so it wears
      out ``from_year`` years later than the stage's own evaluation says; the entries are shifted
      by the stage's start year and escalated to it exactly like the purchase they follow (step 12
      §2.1). The REPLACEMENT entries of a subject the stage merely *inherits* are not re-dated:
      the stage's own register already installed them in the right year, and shifting them would
      book them twice.
    * everything else — the operating flows of E-spec §1.2 (energy, maintenance, fixed operation,
      feed-in, CO2 price, levy, replacement reserve). All of them are taken from the stage that is
      active in the entry's own year, which is what makes "year y of the plan is year y of
      whichever state the house is in" literally true.

    ``RESIDUAL_VALUE`` is in none of the three sets, because no stage's residual entry is ever
    taken: a stage writes its own purchase down from *its* year 0, which is the wrong year in a
    plan, and a stage that ends the horizon holding an earlier stage's asset writes it down not at
    all. The splice therefore computes every residual value itself from the spliced timeline
    (:meth:`StagedEvaluator._residual_entries`).
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

    #: Shifted by the stage's start year and escalated to it, but only for the subjects the stage
    #: pays for: the wear-out of its own purchases.
    OWN_PURCHASE_AGEING: FrozenSet[CostCategory] = frozenset({CostCategory.REPLACEMENT})

    #: The entries a residual value is written down from: the installations the plan charged.
    #: A purchase is split over an INVESTMENT and a PLANNING entry, and their sum in the purchase
    #: year is the gross investment the engine's own write-down uses (``cost_spec.md`` §3.6).
    RESIDUAL_BASIS: FrozenSet[CostCategory] = frozenset(
        {CostCategory.INVESTMENT, CostCategory.PLANNING, CostCategory.REPLACEMENT}
    )


@dataclass(frozen=True)
class _SplicedTimeline:
    """The plan's timeline and the stage every one of its entries came from.

    Two results of one pass, returned together because the second is only derivable while the
    first is being built: once the entries are on one timeline nothing on them says which stage
    contributed them, and the document needs exactly that to attribute a debt-service payment to
    the loan that is being repaid rather than to the loan of whichever stage happens to be active
    in the payment year (step 12 §2.2). Internal to this module.

    Attributes:
        timeline: The spliced, sign-validated timeline.
        stage_by_entry: One stage index per entry of ``timeline``, in timeline order.
    """

    timeline: CashFlowTimeline
    stage_by_entry: Tuple[int, ...]


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
            evaluation already carries the merged ageing register of every stage before it. All of
            them, and therefore ``reference`` and ``plan`` too, carry the *same* provenance ledger
            (:attr:`ledger`), so an id means one record wherever in the result it appears.
        charged_subjects_by_stage: Which cost subjects each stage pays for, and at which share of
            the stage's own year-0 figure — 1.0 for a subject new in the stage, the size increment
            for one that grew, and absent for a subject carried over unchanged.
        active_stage_by_year: The index of the stage the house is in, one entry per horizon year
            0..T. Computed once while the plan is priced and read back by
            :meth:`stage_of_year`, so the supersession rule (the last stage whose ``from_year``
            is at or below the year) exists in one place rather than two that can drift.
        stage_by_entry: The index of the stage each entry of ``plan.timeline`` came from, in
            timeline order. The document needs it for one thing the entry itself cannot say: a
            debt-service payment belongs to the loan of the stage that *borrowed*, which is not
            in general the stage active in the payment year (step 12 §2.2).
        subsidy_catalog_id: The catalogue the plan was priced under, as
            :meth:`StagedEvaluator.catalog_id` names it, or ``None`` for a plan priced with none —
            and therefore with ``subsidy_mode: NONE``. Recorded by :meth:`StagedEvaluator.evaluate`
            rather than supplied beside the result, so a document cannot name a catalogue the
            figures were not priced with.
        energy_echo: The per-carrier escalation rates and year-1 prices the plan was priced with,
            for every carrier a stage bills and every carrier the plan named, with their origins
            (renovisorissues #52). Resolved by :meth:`StagedEvaluator.evaluate` for the same reason
            as the catalogue id; ``None`` for a result assembled by hand, whose document then
            echoes only what its parameters state.
        plan_start_year: The calendar year of the plan's year 0, as the caller stated it, or
            ``None`` when it stated none (renovisorissues #57). The document dates its years from
            this alone — never from the stages' ``simulation_year``, which is the year of their
            weather — and dates none of them without it.
    """

    reference: LifecycleCostResult
    plan: LifecycleCostResult
    comparison: VariantComparison
    stages: Tuple[Stage, ...]
    per_stage: Tuple[LifecycleCostResult, ...]
    charged_subjects_by_stage: Tuple[Dict[str, float], ...] = field(default_factory=tuple)
    active_stage_by_year: Tuple[int, ...] = field(default_factory=tuple)
    stage_by_entry: Tuple[int, ...] = field(default_factory=tuple)
    subsidy_catalog_id: Optional[str] = None
    energy_echo: Optional[EnergyEcho] = None
    plan_start_year: Optional[int] = None

    @property
    def ledger(self) -> Optional[ProvenanceLedger]:
        """The one provenance ledger of the plan, which ``cost_provenance.json`` publishes.

        Every stage was evaluated into it, so it resolves the ids on the reference's timeline, on
        each stage's and on the spliced plan's alike. ``None`` only for a result assembled by hand
        without one.
        """
        return self.plan.ledger

    def stage_of_year(self, year: int) -> int:
        """Index of the stage the house is in during one horizon year.

        Args:
            year: A year index relative to the start of the horizon.

        Returns:
            The index of the last stage whose ``from_year`` is at or below ``year``; 0 for a year
            before any stage after the first has started, and for a year outside the horizon,
            which no chart of the document asks about.
        """
        if 0 <= year < len(self.active_stage_by_year):
            return self.active_stage_by_year[year]
        return 0

    def stage_of_timeline_entry(self, position: int) -> Optional[int]:
        """Index of the stage the plan's ``position``-th timeline entry came from.

        Args:
            position: The entry's index in ``plan.timeline.entries``.

        Returns:
            The stage index, or ``None`` for a result built without the map — a plan priced by an
            older evaluator, or one assembled by hand in a test.
        """
        if 0 <= position < len(self.stage_by_entry):
            return self.stage_by_entry[position]
        return None

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
        plan_start_year: Optional[int] = None,
    ) -> StagedResult:
        """Price one plan: evaluate every stage, splice the timelines, compare against stage 0.

        The seven steps of step 10 §2, in order: validate the plan, evaluate each stage over the
        full horizon with the ageing register of the stages before it — every stage recording into
        one shared provenance ledger, so the ids the splice carries over stay resolvable — splice
        the operating flows by active year and the investment flows by stage start year, aggregate
        the spliced timeline exactly as an ordinary evaluation aggregates its own, and compare the
        result against ``stages[0]``.

        Args:
            stages: The plan, in ascending ``from_year`` order; at least one stage.
            parameters: The assumptions every stage is priced under — one set for the whole plan,
                because a plan whose stages disagree about the interest rate is not one plan.
            perspective: The accounting frame (installation context, actor scope, subsidy mode,
                financing, accounting) every stage is evaluated under.
            catalog: The subsidy catalogue in force, or ``None`` for a plan priced with no
                catalogue at all, where every scheme stays undetermined and the plan is priced
                under :meth:`priced_under`'s ``subsidy_mode: NONE``.
            plan_start_year: The calendar year the plan starts in, or ``None``. Recorded on the
                result for the document's calendar years, and — when ``parameters`` states no
                ``price_basis_year`` — the year the price basis falls back to instead of the
                stages' simulation year
                (:func:`~hisim.economics.evaluator.effective_price_basis_year`).

        Returns:
            The :class:`StagedResult`, carrying the id of ``catalog`` (:meth:`catalog_id`).

        Raises:
            StagedEvaluationError: For any condition of the class docstring's list — a plan this
                module refuses to price.
            hisim.economics.evaluator.UnresolvableSubjectsError: When a stage declares a cost
                subject nothing can price (D7). An engine error, deliberately not wrapped.
        """
        ordered = tuple(stages)
        parameters, perspective = self.priced_under(parameters, perspective, catalog)
        self._validate(ordered, parameters)
        if parameters.price_basis_year is None and plan_start_year is not None:
            # Resolved once, here, so every stage's evaluation reads the same basis year and the
            # engine's own fallback to the simulation year never runs inside this plan.
            parameters = replace(
                parameters,
                price_basis_year=effective_price_basis_year(
                    parameters, self.database, ordered[0].inputs.simulation_year, plan_start_year
                ),
            )
        evaluator = EconomicEvaluator(self.database, parameters, catalog)
        price_basis_year = evaluator.price_basis_year(ordered[0].inputs)
        self._validate_stated_prices(ordered, parameters, price_basis_year)
        active_by_year = self._active_by_year(ordered, parameters.observation_period_in_years)

        # One ledger for the whole plan: the spliced timeline carries entries of every stage, and
        # an id is only an index into the ledger it was recorded in. With one ledger per stage,
        # stage 1's ids pointed at stage 0's records once spliced. Interning makes the sharing
        # free — a price every stage reads is one record — and `cost_provenance.json` then
        # resolves every id of the reference, of every stage and of the plan.
        ledger = ProvenanceLedger()
        per_stage: List[LifecycleCostResult] = []
        charged_by_stage: List[Dict[str, float]] = []
        for index in range(len(ordered)):
            charged = self._charged_subjects(ordered, index)
            inputs = self._staged_inputs(ordered, index, charged_by_stage)
            per_stage.append(evaluator.evaluate(inputs, perspective, ledger))
            charged_by_stage.append(charged)

        spliced = self._splice(
            ordered, tuple(per_stage), tuple(charged_by_stage), evaluator, parameters, active_by_year
        )
        plan = self._aggregate(
            ordered, tuple(per_stage), spliced.timeline, perspective, parameters, active_by_year, ledger
        )
        reference = per_stage[0]
        return StagedResult(
            reference=reference,
            plan=plan,
            comparison=compare(reference, plan, "reference", "plan"),
            stages=ordered,
            per_stage=tuple(per_stage),
            charged_subjects_by_stage=tuple(charged_by_stage),
            active_stage_by_year=active_by_year,
            stage_by_entry=spliced.stage_by_entry,
            subsidy_catalog_id=self.catalog_id(catalog, parameters.country),
            energy_echo=self._energy_echo(ordered, tuple(per_stage), parameters, price_basis_year),
            plan_start_year=plan_start_year,
        )

    #: How a catalogue is named in the document: the country it applies to and the date the
    #: catalogue was taken from the programmes' own pages, which is the pair that identifies one
    #: version of one country's support landscape. The bare country would not: Ireland's schemes
    #: change every few months and a stored document has to say which of them it priced.
    CATALOG_ID_FORMAT = "{country}@{snapshot}"

    #: What stands in the date's place when the catalogue states no snapshot date.
    UNDATED_CATALOG = "undated"

    @classmethod
    def catalog_id(cls, catalog: Optional[SubsidyCatalog], country: str) -> Optional[str]:
        """How a priced plan names the subsidy catalogue it was priced under.

        Args:
            catalog: The catalogue in force, or ``None`` when the plan ran with none, in which
                case every subsidy row of the document is undetermined.
            country: The country the plan was priced for.

        Returns:
            ``"IE@2026-09-19"``-style id, or ``None`` for a plan priced with no catalogue.
        """
        if catalog is None:
            return None
        return cls.CATALOG_ID_FORMAT.format(
            country=country, snapshot=catalog.snapshot_date or cls.UNDATED_CATALOG
        )

    @classmethod
    def priced_under(
        cls,
        parameters: EconomicParameters,
        perspective: Perspective,
        catalog: Optional[SubsidyCatalog],
    ) -> Tuple[EconomicParameters, Perspective]:
        """The assumptions and the perspective a plan is actually priced under, given its catalogue.

        A plan with no catalogue is priced with ``subsidy_mode: NONE``, whatever the perspective
        asked for, and the document states every scheme of such a plan as undetermined (step 10
        §1). The lever is ``perspective.subsidy_mode``, which is what the engine reads;
        ``apply_subsidies`` is only the document's echo of that mode
        (:meth:`~hisim.economics.staged_parameters.StagedParameters.applied_to`) and is turned off
        so the echo says what ran. The engine itself books nothing without a catalogue since the
        §10.1 flat shim was retired, so the rewrite changes no figure; it keeps the ``parameters``
        block from echoing a mode the plan did not run under (hisim-cyc.5). With a catalogue the
        arguments are returned unchanged.

        :meth:`evaluate` calls this, and so does
        :class:`~hisim.economics.staged_document.StagedDocument` when the result it is given was
        priced with no catalogue; the second call changes nothing.

        Args:
            parameters: The assumptions the caller resolved.
            perspective: The perspective the caller resolved.
            catalog: The subsidy catalogue in force, or ``None``.

        Returns:
            ``(parameters, perspective)``: unchanged with a catalogue; without one, the perspective
            with ``SubsidyMode.none()`` and the record with ``apply_subsidies`` off.
        """
        if catalog is not None:
            return parameters, perspective
        return (
            replace(parameters, apply_subsidies=False),
            replace(perspective, subsidy_mode=SubsidyMode.none()),
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
        other: ``simulation_year``, because it is the year of the weather every stage's flows were
        simulated with and the year the price basis falls back to when neither the parameters nor
        a plan start year state one, and ``simulated_period_fraction``, because it is the divisor that
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

    # ------------------------------------------------------------------ stated energy prices (#52)

    #: Message of the refusal raised when a stated energy price cannot be priced as stated.
    STATED_PRICES_MESSAGE = (
        "the energy prices this plan states cannot be priced as stated: {count} problem(s), each "
        "named in the problems document."
    )

    #: The label prefix of a per-carrier rate in `EconomicAssumptions.escalation_rates`.
    ENERGY_RATE_PREFIX = "energy:"

    def _validate_stated_prices(
        self, stages: Tuple[Stage, ...], parameters: EconomicParameters, price_basis_year: int
    ) -> None:
        """Refuse stated energy prices the engine could only bill by reading them differently (#52).

        The checks the parameter parser cannot make, because they need the stages and the
        database: a price stated for a carrier some stage bills under an explicit contract (the
        contract's price signal may have driven that stage's simulation, so replacing its terms
        afterwards would price a load the tariff did not produce), a carrier the country has no
        price entry for (its emission factor, carbon exposure and tax share still come from one),
        and an all-in working price below the year-1 carbon price booked on top of the working
        price, which would leave a negative one. Every fault is reported at once, as
        ``problems.json`` rows in the parameter block's own codes.

        Args:
            stages: The plan as given.
            parameters: The assumptions, holding the stated prices.
            price_basis_year: The economic "today" the year-1 carbon price is read at.

        Raises:
            StagedEvaluationError: Carrying one row per offending carrier or field.
        """
        if not parameters.energy_prices:
            return
        root = f"{ParameterKeys.ROOT_PATH}.{ParameterKeys.ENERGY_PRICES}"
        problems: Dict[str, ParameterProblem] = {}

        def refuse(path: str, code: str, message: str) -> None:
            problems.setdefault(path, ParameterProblem(path=path, code=code.format(path=path), message=message))

        for index, stage in enumerate(stages):
            for carrier, contract in stage.inputs.tariff_contracts.items():
                if contract.is_default_contract:
                    continue
                own, feed_in = StatedPrices.stated_for(carrier, parameters)
                for stated, named in ((own, carrier), (feed_in, EnergyCarrier.ELECTRICITY_FEED_IN)):
                    if stated is not None:
                        refuse(
                            f"{root}.{named.value}",
                            ParameterProblemCodes.MISMATCH,
                            f"stages[{index}] ({stage.label!r}) bills {carrier.value} under the explicit "
                            f"contract {contract.id!r}, whose price signal may have driven its "
                            "simulation; its terms cannot be replaced by a stated price.",
                        )
        for carrier, stated in parameters.energy_prices.items():
            if carrier == EnergyCarrier.ELECTRICITY_FEED_IN:
                continue  # a rate on the electricity contract, needing no price entry of its own
            if not self.database.has_energy_price(carrier, parameters.country):
                refuse(
                    f"{root}.{carrier.value}",
                    ParameterProblemCodes.INVALID,
                    f"the cost database has no {carrier.value} price entry for {parameters.country}; a "
                    "stated price replaces the entry's prices, but its emission factor, carbon "
                    "exposure and tax share still come from it.",
                )
                continue
            working = stated.working_price_in_euro_per_kwh
            if working is None:
                continue
            entry = self.database.get_energy_price(carrier, price_basis_year, parameters.country)
            co2_per_kwh = year_one_co2_price_per_kwh(entry, parameters, self.database, price_basis_year)
            if working.minimum < co2_per_kwh - StatedPrices.TOLERANCE_IN_EURO_PER_KWH:
                refuse(
                    f"{root}.{carrier.value}.{ParameterKeys.PRICE_WORKING}",
                    ParameterProblemCodes.INVALID,
                    f"the stated all-in working price ({working.minimum:.6g} EUR/kWh at its lowest) is "
                    f"below the year-1 carbon price of {co2_per_kwh:.6g} EUR/kWh the engine books on "
                    f"top of the {carrier.value} working price ({entry.co2_price_exposure:g} exposure x "
                    f"{entry.emission_factor_in_kg_per_kwh:.6g} kg/kWh x the "
                    f"{parameters.co2_price_scenario!r} CO2 price of {price_basis_year}).",
                )
        if problems:
            rows = [problem.to_json() for problem in problems.values()]
            raise StagedEvaluationError(self.STATED_PRICES_MESSAGE.format(count=len(rows)), rows)

    def _energy_echo(
        self,
        stages: Tuple[Stage, ...],
        per_stage: Tuple[LifecycleCostResult, ...],
        parameters: EconomicParameters,
        price_basis_year: int,
    ) -> EnergyEcho:
        """The per-carrier rates and year-1 prices the plan was priced with, and their origins (#52, E1).

        The rates of the carriers the stages bill are the union of the stages' own resolved
        assumptions, which must agree — every stage is priced under one parameter set against one
        database, so a disagreement is an engine defect (:class:`StagedEngineError`). A carrier the
        plan named but no stage bills is resolved through the same fallback chain. Prices are the
        contract each carrier is billed under
        (:func:`~hisim.economics.calculators.energy.priced_contract`), checked against the stages'
        billed tariffs, with the working price stated all-in: the database's working price plus the
        year-1 carbon price booked beside it, or the stated price as it was stated. The feed-in rate
        is echoed as ``ELECTRICITY_FEED_IN`` whenever the electricity contract pays one. A carrier
        billed under an explicit contract has no flat price a plan could state, and is left out of
        the prices.

        Args:
            stages: The plan as given.
            per_stage: Each stage's own evaluation.
            parameters: The assumptions the plan was priced under.
            price_basis_year: The economic "today".

        Returns:
            The echo, keyed by carrier in carrier order.

        Raises:
            StagedEngineError: If two stages resolved one carrier's rate or tariff differently, or
                the contract rebuilt here is not the one the stages billed.
        """
        billed_rates: Dict[EnergyCarrier, ResolvedRate] = {}
        billed_tariffs: Dict[EnergyCarrier, TariffAssumption] = {}
        for index, result in enumerate(per_stage):
            if result.assumptions is None:
                continue
            for label, rate in result.assumptions.escalation_rates.items():
                if label.startswith(self.ENERGY_RATE_PREFIX):
                    carrier = EnergyCarrier(label[len(self.ENERGY_RATE_PREFIX):])
                    self._agreed(billed_rates, carrier, rate, index, "escalation rate")
            for value, tariff in result.assumptions.tariffs.items():
                self._agreed(billed_tariffs, EnergyCarrier(value), tariff, index, "tariff")
        feed_in = EnergyCarrier.ELECTRICITY_FEED_IN
        named = set(parameters.energy_price_escalation_rates) | set(parameters.energy_prices)
        rates: Dict[EnergyCarrier, Tuple[float, EchoOrigin]] = {}
        for carrier in sorted((set(billed_rates) | named) - {feed_in}, key=lambda member: member.value):
            resolved = billed_rates.get(carrier) or resolve_carrier_escalation_rate(
                carrier, parameters, self.database
            )
            rates[carrier] = (resolved.rate, EchoOrigin.of_rate(resolved.origin))
        explicit = {
            carrier
            for stage in stages
            for carrier, contract in stage.inputs.tariff_contracts.items()
            if not contract.is_default_contract
        }
        prices: Dict[EnergyCarrier, EchoedPrice] = {}
        for carrier in ((set(billed_tariffs) - explicit) | set(parameters.energy_prices)) - {feed_in}:
            prices[carrier] = self._echoed_price(carrier, billed_tariffs.get(carrier), parameters, price_basis_year)
        stated_feed_in = parameters.energy_prices.get(feed_in)
        if stated_feed_in is not None:
            prices[feed_in] = EchoedPrice(
                working_price_in_euro_per_kwh=stated_feed_in.working_price_in_euro_per_kwh,
                working_price_origin=EchoOrigin.STATED,
            )
        elif EnergyCarrier.ELECTRICITY in prices:
            contract = priced_contract(EnergyCarrier.ELECTRICITY, price_basis_year, self.database, parameters)
            if contract.feed_in.kind == FeedInKind.FIXED_TARIFF:
                prices[feed_in] = EchoedPrice(
                    working_price_in_euro_per_kwh=contract.feed_in.rate_in_euro_per_kwh,
                    working_price_origin=EchoOrigin.DATABASE,
                )
        return EnergyEcho(rates=rates, prices=dict(sorted(prices.items(), key=lambda item: item[0].value)))

    def _echoed_price(
        self,
        carrier: EnergyCarrier,
        billed: Optional[TariffAssumption],
        parameters: EconomicParameters,
        price_basis_year: int,
    ) -> EchoedPrice:
        """One carrier's year-1 price terms as the plan was priced with them, the working price all-in.

        Args:
            carrier: A carrier other than the feed-in one.
            billed: The tariff the stages billed it under, or None when no stage bills it.
            parameters: The assumptions.
            price_basis_year: The economic "today".

        Returns:
            The echoed terms.

        Raises:
            StagedEngineError: If the contract rebuilt here is not the one the stages billed.
        """
        contract = priced_contract(carrier, price_basis_year, self.database, parameters)
        if billed is not None and TariffAssumption.from_contract(contract) != billed:
            raise StagedEngineError(
                f"the stages billed {carrier.value} under {billed.contract_id!r}, but the plan's "
                f"parameters price it under {contract.id!r} with different terms."
            )
        stated = parameters.energy_prices.get(carrier)
        stated_working = stated.working_price_in_euro_per_kwh if stated is not None else None
        stated_standing = stated.standing_charge_in_euro_per_year if stated is not None else None
        if stated_working is not None:
            working = stated_working
        else:
            entry = self.database.get_energy_price(carrier, price_basis_year, parameters.country)
            co2_per_kwh = year_one_co2_price_per_kwh(entry, parameters, self.database, price_basis_year)
            working = contract.supply.working_price_in_euro_per_kwh
            if co2_per_kwh:
                working = working + UncertainValue.exact(co2_per_kwh)
        return EchoedPrice(
            working_price_in_euro_per_kwh=working,
            working_price_origin=EchoOrigin.STATED if stated_working is not None else EchoOrigin.DATABASE,
            standing_charge_in_euro_per_year=contract.standing_charge_in_euro_per_year,
            standing_charge_origin=EchoOrigin.STATED if stated_standing is not None else EchoOrigin.DATABASE,
        )

    @staticmethod
    def _agreed(found: Dict[EnergyCarrier, Any], carrier: EnergyCarrier, value: Any, index: int, what: str) -> None:
        """Record one stage's resolution of one carrier, refusing a second stage that disagrees.

        Args:
            found: Carrier -> the resolution recorded so far; written in place.
            carrier: The carrier.
            value: This stage's resolution.
            index: This stage's index, for the message.
            what: What was resolved, for the message.

        Raises:
            StagedEngineError: If an earlier stage resolved the carrier differently.
        """
        earlier = found.setdefault(carrier, value)
        if earlier != value:
            raise StagedEngineError(
                f"stages[{index}] resolved the {what} of {carrier.value} as {value!r}, an earlier stage "
                f"as {earlier!r}; one plan is priced under one set of assumptions."
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

        The increment is only for something the house *keeps* and enlarges. Anything the stage's
        inventory declares *replaced*, where the stage before declared no such replacement
        (:meth:`_newly_replaced_classes`), is a new purchase bought whole in this stage, however
        large the old one was: a generator, a buffer, a cylinder, the emitters, a PV array, a
        battery, a collector. The stage's own evaluation already prices it so -- the full new
        price, the old one's removal, its written-off book value and the anyway credit
        (``cost_spec.md`` §4.1) -- and charging only the size increment of that would book a
        fraction of a replacement. A heating_system measure that replaces a 430-litre buffer with a
        970-litre one buys a 970-litre vessel, not 540 litres of one (renovisorissues #48).

        An owner decision of 2026-09-26 first limited this to the space-heating buffer; it was
        reversed later that day, because under the increment rule every same-class replacement
        of the same size or smaller was free -- a hot_water_system measure replacing a cylinder
        with one of the same size, a heat pump replacing a heat pump.

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
        replaced = cls._newly_replaced_classes(stages, index)
        charged: Dict[str, float] = {}
        for subject, facts in current.items():
            before = previous.get(subject)
            if before is None or before.asset_class != facts.asset_class or facts.asset_class in replaced:
                charged[subject] = 1.0
                continue
            if before.size <= 0.0:
                # A size of exactly 0 means "declared but not installed"
                # (:class:`~hisim.economics.facts.ComponentCostFacts`), so a subject that grows
                # out of it is not an enlargement of something that was there — it is the whole
                # device, bought now, and the stage pays for all of it.
                charged[subject] = 1.0
            elif facts.size > before.size:
                charged[subject] = (facts.size - before.size) / facts.size
        return charged

    @classmethod
    def _newly_replaced_classes(cls, stages: Tuple[Stage, ...], index: int) -> FrozenSet[ComponentType]:
        """The asset classes stage ``index``'s inventory replaces and the stage before did not.

        A stage's inventory is the house as its translator declared it, and each entry names the
        classes that replace it (``ExistingAsset.replaced_by_asset_classes``). A replacement that
        the previous stage already declared was carried out there -- a plan's stages each carry
        every measure before them -- so only the difference is this stage's own.

        Args:
            stages: The plan as given.
            index: The stage to answer for, at least 1.

        Returns:
            The asset classes this stage replaces for the first time.
        """

        def declared(stage: Stage) -> Set[ComponentType]:
            return {
                asset_class
                for asset in cls._inventory(stage.inputs)
                for asset_class in asset.replaced_by_asset_classes
            }

        return frozenset(declared(stages[index]) - declared(stages[index - 1]))

    @classmethod
    def _carried_over_subjects(
        cls, stages: Tuple[Stage, ...], index: int, charged: Dict[str, float]
    ) -> Set[str]:
        """Subjects stage ``index`` inherits from its predecessor without paying for them again.

        The complement of :meth:`_charged_subjects` among the stage's own cost subjects, and the
        set the splice uses to decide what *not* to book. Everything else a stage's year-0 entries
        mention — the synthetic ``financing`` subject, an entry filed under an asset the stage
        tears out — is neither charged nor carried over, and is taken as the stage's own.

        Args:
            stages: The plan as given.
            index: The stage to answer for.
            charged: What :meth:`_charged_subjects` said this stage pays for, passed in rather
                than recomputed so the two answers cannot disagree.

        Returns:
            The subject names carried over unchanged; empty for stage 0.
        """
        if index == 0:
            return set()
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
    ) -> EvaluationInputs:
        """Stage ``index``'s inputs with the ageing register of every earlier stage merged in.

        This is step 10 §2 item 4, and it is the whole of "ageing across stages": the register the
        stage is evaluated with holds the house inventory *minus* whatever an earlier stage has
        already torn out, *plus* one :class:`~hisim.economics.facts.ExistingAsset` per subject an
        earlier stage paid for, installed in that stage's year. The engine's brownfield machinery
        then schedules the replacements, the residual values and the removal costs itself; this
        module adds no second mechanism.

        Stage 0 is returned unchanged, and it contributes nothing to later registers either — it is
        the reference, and its register is the inventory as
        the translator declared it.

        Args:
            stages: The plan as given.
            index: The stage to build inputs for.
            charged_by_stage: What every *earlier* stage charged, in stage order; this method is
                called in order, so the list holds exactly ``index`` entries.

        Returns:
            A copy of the stage's inputs carrying the merged register. The caller's record is not
            modified.
        """
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
        # Stage 0 is the house as it is: what it "charges" is the reference's own year-0 booking,
        # not a purchase the plan makes, and its equipment is already in the inventory register
        # with its real installation year. Letting it age in here would re-date a 2010 boiler to
        # the simulation year, so the heat pump that replaces it would write off a nearly new
        # asset as sunk cost. Only stages 1.. put anything into the building.
        for earlier in range(1, index):
            earlier_facts = {facts.subject: facts.facts for facts in stages[earlier].inputs.cost_facts}
            for subject, share in charged_by_stage[earlier].items():
                facts = earlier_facts.get(subject)
                if facts is None:
                    continue
                del share  # the register records the asset, not the share that was paid for it
                if facts.size <= 0.0:
                    # A charged subject of size 0 is a device that was declared and not installed
                    # (`ComponentCostFacts` allows the zero and means exactly that), and
                    # `ExistingAsset` refuses a size of zero. Nothing was put in the building, so
                    # nothing ages into the next stage's register.
                    continue
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
        active_by_year: Tuple[int, ...],
    ) -> "_SplicedTimeline":
        """Build the plan's one timeline out of the per-stage timelines (step 10 §2 items 2-3).

        Entries are appended stage by stage and, within a stage, in the order that stage's own
        evaluation produced them. For a plan of one stage that reproduces the original timeline
        entry for entry, which is invariant 1 of E-spec §1.3 and the reason the order is fixed
        this way rather than by sorting.

        Two things cannot be decided entry by entry and are therefore done in a second pass over
        the result of the first (step 12 §2.1). The **residual values** are written down from the
        plan's own last installation of each subject, which is only known once every stage's
        purchases and re-dated replacements are on one timeline; each one is put back where the
        stage that would have written it stood, so a plan of one stage still reproduces the
        original order. The **replacement reserve** of the operating view is re-levelized from the
        re-dated replacement schedule, because a stage's own reserve pays for replacements in the
        years that stage would have had them rather than in the years the plan does.

        Args:
            stages: The plan as given.
            per_stage: Each stage's own evaluation, in stage order.
            charged_by_stage: What each stage pays for, from :meth:`_charged_subjects`.
            evaluator: The bound engine, for the per-asset-class investment escalation rates and
                the price basis year the service lives are resolved at.
            parameters: The assumptions, for the horizon and the general escalation rate.
            active_by_year: Which stage is active in each horizon year, from
                :meth:`_active_by_year`.

        Returns:
            The spliced timeline, sign-validated like any engine timeline, together with the stage
            each of its entries came from.
        """
        horizon = parameters.observation_period_in_years
        active_indices = set(active_by_year)
        spliced: List[Optional[CashFlowEntry]] = []
        owners: List[int] = []
        residual_slots: Dict[str, int] = {}
        reserve_flows: List[Tuple[int, UncertainValue]] = []
        for index, stage in enumerate(stages):
            charges = self._stage_charges(stages, index, charged_by_stage[index], evaluator, parameters)
            reserve_flows.extend(
                self._staged_reserve_flows(per_stage[index], index, stage.from_year, charges, active_by_year)
            )
            for entry in per_stage[index].timeline.entries:
                if entry.category is CostCategory.RESIDUAL_VALUE:
                    if index in active_indices:
                        residual_slots[entry.subject] = len(spliced)
                        spliced.append(None)
                        owners.append(index)
                    continue
                moved = self._spliced_entry(
                    entry, index, stage.from_year, charges, active_by_year, horizon
                )
                if moved is not None:
                    spliced.append(moved)
                    owners.append(index)
        placed = [(owner, entry) for owner, entry in zip(owners, spliced) if entry is not None]
        residuals = self._residual_entries(placed, stages, evaluator, parameters)
        reserve = replacement_reserve_amount(reserve_flows, parameters)
        return self._assemble(spliced, owners, residual_slots, residuals, reserve)

    @staticmethod
    def _assemble(
        spliced: List[Optional[CashFlowEntry]],
        owners: List[int],
        residual_slots: Dict[str, int],
        residuals: Dict[str, Tuple[int, CashFlowEntry]],
        reserve: UncertainValue,
    ) -> "_SplicedTimeline":
        """Put the computed residuals into their slots and hand back one validated timeline.

        A slot is the position a stage's own ``RESIDUAL_VALUE`` entry stood at; the plan's residual
        for that subject takes it, which is what keeps a one-stage plan in the original entry
        order. A subject whose residual the splice computed but no stage wrote gets its entry
        appended, in subject order so two runs of one plan produce the same bytes; a slot whose
        subject earns nothing at the horizon is simply dropped.

        Args:
            spliced: The first pass's entries, with ``None`` marking a reserved residual slot.
            owners: The stage each position came from, parallel to ``spliced``.
            residual_slots: Subject -> the position of the slot its residual goes into. Only the
                last stage that reserved one for a subject is in the map.
            residuals: Subject -> ``(stage, entry)`` for every residual the plan actually earns.
            reserve: The plan's re-levelized annual replacement-reserve payment, written into the
                ``REPLACEMENT_RESERVE`` entries the stages contributed.

        Returns:
            The assembled timeline and its stage map.
        """
        timeline = CashFlowTimeline()
        stage_by_entry: List[int] = []
        used: Set[str] = set()
        for position, entry in enumerate(spliced):
            if entry is None:
                subject = next(
                    (name for name, slot in residual_slots.items() if slot == position), None
                )
                if subject is None or subject in used or subject not in residuals:
                    continue
                timeline.add(residuals[subject][1])
                stage_by_entry.append(owners[position])
                used.add(subject)
                continue
            if entry.category is CostCategory.REPLACEMENT_RESERVE:
                entry = replace(entry, amount_in_euro=reserve)
            timeline.add(entry)
            stage_by_entry.append(owners[position])
        for subject in sorted(residuals):
            if subject in used:
                continue
            stage, entry = residuals[subject]
            timeline.add(entry)
            stage_by_entry.append(stage)
        return _SplicedTimeline(timeline=timeline, stage_by_entry=tuple(stage_by_entry))

    def _stage_charges(
        self,
        stages: Tuple[Stage, ...],
        index: int,
        charged: Dict[str, float],
        evaluator: EconomicEvaluator,
        parameters: EconomicParameters,
    ) -> "_StageCharges":
        """What one stage pays for and at which price level, as the splice has to ask it.

        Args:
            stages: The plan as given.
            index: The stage.
            charged: What :meth:`_charged_subjects` already said about this stage.
            evaluator: The bound engine, for the per-asset-class escalation rates.
            parameters: The assumptions, for the general investment escalation rate.

        Returns:
            The bundle :meth:`_spliced_entry` consults.
        """
        return _StageCharges(
            charged=charged,
            carried_over=self._carried_over_subjects(stages, index, charged),
            rates=self._escalation_rates(stages[index], evaluator),
            default_rate=parameters.investment_price_escalation_rate,
        )

    @classmethod
    def _staged_reserve_flows(
        cls,
        result: LifecycleCostResult,
        index: int,
        from_year: int,
        charges: "_StageCharges",
        active_by_year: Tuple[int, ...],
    ) -> List[Tuple[int, UncertainValue]]:
        """One stage's replacement schedule, re-dated exactly as its REPLACEMENT entries are.

        The operating view charges no capital at all and instead levelizes the replacements into a
        sinking fund (``cost_spec.md`` §4.2), which means the reserve has to follow the years the
        *plan* replaces things in and not the years a stage on its own would have. The flows are
        the ones the investment calculator collected
        (:attr:`~hisim.economics.results.LifecycleCostResult.replacement_flows`), which exist under
        every perspective, including the operating one where the entries themselves are suppressed.

        Args:
            result: The stage's own evaluation.
            index: The stage's index.
            from_year: The stage's start year.
            charges: What the stage pays for and at which escalation rate.
            active_by_year: Which stage is active in each horizon year.

        Returns:
            ``(plan year, nominal amount)`` per replacement this stage contributes to the plan.
        """
        horizon = len(active_by_year) - 1
        flows: List[Tuple[int, UncertainValue]] = []
        for subject, year, amount in result.replacement_flows:
            share = charges.share_of(subject)
            if share > 0.0:
                moved, factor = year + from_year, charges.escalation_factor(subject, from_year)
            else:
                moved, factor = year, 1.0
            if moved >= horizon or moved < 0 or active_by_year[moved] != index:
                continue
            flows.append((moved, amount.scale(factor)))
        return flows

    def _residual_entries(
        self,
        placed: List[Tuple[int, CashFlowEntry]],
        stages: Tuple[Stage, ...],
        evaluator: EconomicEvaluator,
        parameters: EconomicParameters,
    ) -> Dict[str, Tuple[int, CashFlowEntry]]:
        """The residual value of every subject, written down from the plan's own last purchase.

        Step 12 §2.1 rule 2. No stage can compute this: a stage prices its purchase as if it
        happened in year 0, so it writes down an asset that is older than the plan's ever gets,
        and a stage that merely *inherits* an earlier stage's equipment writes it down not at all
        (``calculators/investment.py`` §3.6 rule 3: an installation that predates year 0 earns
        nothing). Both mistakes disappear when the write-down is taken from the spliced timeline,
        where each subject's last ``INVESTMENT``/``PLANNING``/``REPLACEMENT`` entry is the
        installation the plan actually charged and its amount is already escalated to that year.

        The entry is a copy of that installation — same subject, same payer, same provenance —
        with the year moved to the horizon, the category changed and the amount mirrored into a
        revenue, so a reader tracing the credit lands on the purchase it belongs to. Payer is
        carried rather than re-derived because ``INVESTMENT``, ``REPLACEMENT`` and
        ``RESIDUAL_VALUE`` are one allocation class in every shipped ruleset
        (:class:`hisim.economics.actors` ``LANDLORD_CATEGORIES``).

        Args:
            placed: The first pass's entries with the stage each came from, in timeline order.
            stages: The plan as given, for the cost facts the service lives come from.
            evaluator: The bound engine, for the price basis year of the database lookup.
            parameters: The assumptions, for the horizon and the country.

        Returns:
            Subject -> ``(stage, entry)``; a subject that earns nothing at the horizon is absent.

        Raises:
            hisim.economics.database.CostDataError: If a subject states no service life of its own
                and the cost database has no entry to take one from. An engine failure, not a bad
                plan: the stage's own evaluation could not have priced the subject either.
        """
        horizon = parameters.observation_period_in_years
        facts_by_subject = {}
        for stage in stages:
            for subject_facts in stage.inputs.cost_facts:
                facts_by_subject[subject_facts.subject] = subject_facts.facts
        last_install: Dict[str, int] = {}
        for _stage, entry in placed:
            if entry.category not in StagedCategories.RESIDUAL_BASIS:
                continue
            if entry.year > last_install.get(entry.subject, -1):
                last_install[entry.subject] = entry.year
        price_basis_year = evaluator.price_basis_year(stages[0].inputs)
        residuals: Dict[str, Tuple[int, CashFlowEntry]] = {}
        for subject in sorted(last_install):
            facts = facts_by_subject.get(subject)
            if facts is None:
                continue
            year = last_install[subject]
            life = self._service_life(facts, price_basis_year, parameters)
            fraction = InvestmentDating.residual_fraction(year, life, horizon)
            if fraction <= 0.0:
                continue
            basis = [
                (stage, entry)
                for stage, entry in placed
                if entry.subject == subject
                and entry.year == year
                and entry.category in StagedCategories.RESIDUAL_BASIS
            ]
            amount = UncertainValue.sum(entry.amount_in_euro for _stage, entry in basis)
            owner, template = basis[0]
            residuals[subject] = (
                owner,
                replace(
                    template,
                    year=horizon,
                    amount_in_euro=amount.scale(fraction).as_revenue(),
                    category=CostCategory.RESIDUAL_VALUE,
                    subsidy_scheme_id=None,
                ),
            )
        return residuals

    def _service_life(
        self, facts: ComponentCostFacts, price_basis_year: int, parameters: EconomicParameters
    ) -> float:
        """One subject's service life in years, by the same chain the engine's pricing uses.

        An explicit ``lifetime_override_in_years`` wins, exactly as it does in
        ``calculators/context_resolution.py``; otherwise the cost database's entry for the
        subject's asset class at the plan's price basis year states it. The splice needs the same
        number the stage's own schedule was built from, so it must not read the database when an
        override exists.

        Args:
            facts: The subject's cost facts.
            price_basis_year: The economic "today" of this plan, as the engine resolved it.
            parameters: The assumptions, for the country whose device file is read.

        Returns:
            The service life in years.

        Raises:
            hisim.economics.database.CostDataError: If neither source states one.
        """
        if facts.lifetime_override_in_years is not None:
            return float(facts.lifetime_override_in_years)
        entry = self.database.get_device_entry(facts.asset_class, price_basis_year, parameters.country)
        return float(entry.service_life_in_years)

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
            Subject name -> nominal annual rate, for every subject with cost facts. A subject
            absent from the mapping — the synthetic ``financing`` subject the loan flows are
            booked under, the replacement reserve — is escalated at the plan's *general investment
            escalation rate* rather than at zero, because
            :meth:`_StageCharges.escalation_factor` falls back to it: the whole loan moves to the
            stage's year, so its principal has to be the year's price level like everything else
            the stage buys.
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

        The decision of :class:`StagedCategories`, applied to one entry. A stage-start entry is
        kept only for the subjects the stage pays for, moved to the stage's year and escalated to
        it; a loan entry is shifted by the same offset without a charge test, because the loan is
        the stage's whichever subjects it financed; a replacement of a subject the stage *buys* is
        shifted and escalated with the purchase it follows and then kept only while the stage is
        still the state of the house, so the stage that supersedes it schedules the rest from its
        own register instead of the two booking the same re-purchase twice; everything else is
        kept only when the entry's own year belongs to this stage.

        A shifted replacement that lands exactly on the horizon is dropped, which is the engine's
        own rule for the unshifted ones (``calculators/investment.py``: the observation period ends
        at T, so a unit due then is not bought). A loan payment in year T is kept, because it is
        paid inside the period rather than at its edge.

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
            return self._stage_start_entry(entry, from_year, charges, horizon)
        if entry.category in StagedCategories.LOAN_SCHEDULE:
            return self._loan_entry(entry, from_year, charges, horizon)
        if entry.category in StagedCategories.OWN_PURCHASE_AGEING and charges.share_of(entry.subject) > 0.0:
            return self._replacement_entry(entry, index, from_year, charges, active_by_year, horizon)
        if 0 <= entry.year <= horizon and active_by_year[entry.year] == index:
            return entry
        return None

    @staticmethod
    def _stage_start_entry(
        entry: CashFlowEntry, from_year: int, charges: "_StageCharges", horizon: int
    ) -> Optional[CashFlowEntry]:
        """One of a stage's year-0 flows, moved to the stage's year and escalated to it.

        Args:
            entry: The stage's own year-0 entry.
            from_year: The stage's start year.
            charges: What the stage pays for and at which escalation rate.
            horizon: The last year index of the horizon.

        Returns:
            The entry in the stage's year, scaled by the share the stage pays and by the price
            level of that year; ``None`` for a subject the stage inherits or a stage that never
            starts inside the horizon.
        """
        share = charges.share_of(entry.subject)
        if share <= 0.0 or from_year > horizon:
            return None
        factor = share * charges.escalation_factor(entry.subject, from_year)
        return replace(entry, year=from_year, amount_in_euro=entry.amount_in_euro.scale(factor))

    @staticmethod
    def _loan_entry(
        entry: CashFlowEntry, from_year: int, charges: "_StageCharges", horizon: int
    ) -> Optional[CashFlowEntry]:
        """One debt-service payment, shifted by the stage's start year.

        No charge test: the loan belongs to the stage that took it out, whichever of its subjects
        it financed, and its principal is the stage's own year-0 net investment escalated to that
        year like everything else the stage buys.

        Args:
            entry: The stage's own debt-service entry.
            from_year: The stage's start year.
            charges: For the escalation rate the whole loan moves at.
            horizon: The last year index of the horizon.

        Returns:
            The payment in its plan year, or ``None`` when it falls past the horizon.
        """
        year = entry.year + from_year
        if year > horizon:
            return None
        factor = charges.escalation_factor(entry.subject, from_year)
        return replace(entry, year=year, amount_in_euro=entry.amount_in_euro.scale(factor))

    @staticmethod
    def _replacement_entry(
        entry: CashFlowEntry,
        index: int,
        from_year: int,
        charges: "_StageCharges",
        active_by_year: Tuple[int, ...],
        horizon: int,
    ) -> Optional[CashFlowEntry]:
        """The re-purchase of something the stage bought, moved by the stage's start year.

        Step 12 §2.1 rule 1. It is dropped once another stage is the state of the house, because
        that stage schedules the subject's remaining replacements from its own register entry and
        the two would otherwise book the same re-purchase twice. A replacement landing exactly on
        the horizon is dropped, which is the engine's rule for the unshifted ones: the observation
        period ends at T, so a unit due then is not bought.

        Args:
            entry: The stage's own REPLACEMENT entry.
            index: The stage's index.
            from_year: The stage's start year.
            charges: For the escalation rate of the subject.
            active_by_year: Which stage is active in each horizon year.
            horizon: The last year index of the horizon.

        Returns:
            The re-purchase in its plan year, or ``None``.
        """
        year = entry.year + from_year
        if year >= horizon or active_by_year[year] != index:
            return None
        factor = charges.escalation_factor(entry.subject, from_year)
        return replace(entry, year=year, amount_in_euro=entry.amount_in_euro.scale(factor))

    # ------------------------------------------------------------------ aggregation

    def _aggregate(
        self,
        stages: Tuple[Stage, ...],
        per_stage: Tuple[LifecycleCostResult, ...],
        timeline: CashFlowTimeline,
        perspective: Perspective,
        parameters: EconomicParameters,
        active: Tuple[int, ...],
        ledger: ProvenanceLedger,
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
            active: Which stage is active in each horizon year, from :meth:`_active_by_year`.
            ledger: The one provenance ledger every stage recorded into, which every id on the
                spliced timeline points into.

        Returns:
            The plan's :class:`~hisim.economics.results.LifecycleCostResult`.
        """
        horizon = parameters.observation_period_in_years
        # Year 1 is where the document's energy table and the monthly figure are read, so the
        # physical context comes from the state the house is in then.
        year_one = per_stage[active[1]] if horizon >= 1 else per_stage[0]
        last = per_stage[active[horizon]]
        facts_by_subject = {}
        for stage in stages:
            for subject_facts in stage.inputs.cost_facts:
                facts_by_subject[subject_facts.subject] = subject_facts.facts
        co2 = self._splice_co2(per_stage, active, horizon)
        equivalent_heat = self._equivalent_annual_heat(stages, active, parameters)
        aggregation = aggregate_timeline(
            timeline=timeline,
            actor_scope=perspective.actor_scope,
            facts_by_subject=facts_by_subject,
            co2_result=co2,
            parameters=parameters,
            annual_heat_demand_in_kwh=equivalent_heat,
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
            monthly_equivalent_cost_in_euro=aggregation.monthly_equivalent_cost_in_euro,
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
            sunk_cost_written_off_in_euro=UncertainValue.sum(
                result.sunk_cost_written_off_in_euro for result in per_stage
            ),
            ledger=ledger,
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
            # The plan's heat demand is the one its heat-cost figure divided by — the equivalent
            # annual heat of the whole horizon, not the last stage's — so the published assumption
            # and the heat-cost derivation that reads it state the division the KPI made.
            assumptions=(
                replace(last.assumptions, annual_heat_demand_in_kwh=equivalent_heat)
                if last.assumptions is not None
                else None
            ),
        )

    @staticmethod
    def _equivalent_annual_heat(
        stages: Tuple[Stage, ...], active: Tuple[int, ...], parameters: EconomicParameters
    ) -> Optional[float]:
        """The plan's heat as one annual figure: the annuity of its discounted per-year heat.

        Decision A of the PR #812 review: the plan's levelized cost of heat is the textbook
        NPV(costs) / NPV(heat), and each horizon year's heat is the heat of the stage active in that
        year. The aggregation divides `total_npv x annuity` by the figure returned here, so it is
        `annuity x sum_y heat_active[y] x df_y` over the years 1..T the energy flows are booked in,
        with the discount factors `CashFlowTimeline.npv` applies to those flows; the annuity cancels
        and the quotient is NPV(costs) / NPV(heat). Dividing by the last stage's heat, as before,
        charged the whole horizon's costs to the insulated house's demand alone.

        When every year's heat is the same figure — a plan whose stages all start in year 0, or
        whose stages all state the same heat — it is returned as it is: the annuity factor is the
        reciprocal of the discount sum, so the arithmetic would reproduce it only up to rounding,
        and the unstaged evaluation divides by exactly this figure.

        Args:
            stages: The plan as given.
            active: Which stage is active in each horizon year, from :meth:`_active_by_year`.
            parameters: The assumptions: the interest rate, the horizon and the annuity factor.

        Returns:
            The equivalent annual heat in kWh/a, or None when any stage active in the horizon
            states no heat — the plan's heat-cost figure is then omitted, as a stage's own is.
        """
        years = range(1, parameters.observation_period_in_years + 1)
        stated = [stages[active[year]].inputs.annual_heat_demand() for year in years]
        heat_by_year = [heat for heat in stated if heat is not None]
        if len(heat_by_year) < len(stated):
            return None
        if len(set(heat_by_year)) == 1:
            return heat_by_year[0]
        discounted = sum(heat * parameters.discount_factor(year) for year, heat in zip(years, heat_by_year))
        return parameters.annuity_factor() * discounted

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
