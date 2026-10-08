"""The staged evaluator: one renovation plan spread over several years, priced once.

A staged plan is a sequence of stages, each a simulated state of the house that the plan puts in place in a given year:
the house is in stage 0 until year `t1`, in stage 1 until `t2`, and so on, and each stage's purchases are made in the
year it starts. Year 0 of the plan is the calendar year the plan starts in. The plan's cash flows are a splice of
per-stage evaluations: every stage is evaluated alone over the full horizon by `EconomicEvaluator`, then each year
takes its operating flows from the stage active in it and each stage's investment flows are moved to the year the stage
starts. A one-stage plan therefore equals a plain evaluation entry for entry. Equipment an earlier stage bought (kept
equipment) enters the next stages' registers as existing assets, so replacements, residual values and removal costs
fall in the right years through the engine's brownfield logic (cost_spec.md §4.1); stages are therefore evaluated in
order. Staged plans use the full-cost method: no stage books an anyway credit for what it replaces.

Example::

    stages = [Stage(baseline_inputs, 0, "baseline"),
              Stage(envelope_inputs, 0, "stage 1", ("external_insulation",)),
              Stage(heat_pump_inputs, 3, "stage 2", ("heating_system",))]
    result = StagedEvaluator(database).evaluate(stages, parameters, perspective, catalog)
    result.plan.total_npv_in_euro.best_estimate
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field, replace
from typing import Any, ClassVar, Dict, FrozenSet, List, Mapping, Optional, Sequence, Set, Tuple

from hisim import log
from hisim.economics.calculators.aggregation import aggregate_timeline
from hisim.economics.calculators.annualization import annualize
from hisim.economics.calculators.context_resolution import installation_verdict
from hisim.economics.calculators.energy import (
    StatedPrices,
    priced_contract,
    year_one_co2_price_per_kwh,
)
from hisim.economics.calculators.escalation import resolve_carrier_escalation_rate
from hisim.economics.calculators.investment import InvestmentDating
from hisim.economics.calculators.reserve import replacement_reserve_amount
from hisim.economics.calculators.categories import EngineCategoryRules
from hisim.economics.calculators.financing_application import (
    FinancingConstants,
    Year0NetInvestment,
    build_financing_flows,
    resolve_loan_plan,
)
from hisim.economics.carriers import EnergyCarrier
from hisim.economics.database import CostDatabase
from hisim.economics.evaluator import (
    EconomicEvaluator,
    EvaluationInputs,
    SubjectCostFacts,
    effective_price_basis_year,
)
from hisim.economics.financing import FinancingPlan
from hisim.economics.facts import (
    ComponentCostFacts,
    ExistingAsset,
    ExistingAssetRegister,
    InstallationYearOrigin,
    QuotedPurchase,
)
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
    PlanYearBounds,
)
from hisim.economics.subsidies import PayoutKind, SubsidyCatalog
from hisim.economics.tariffs import FeedInKind
from hisim.economics.timeline import CashFlowEntry, CashFlowTimeline, CostCategory
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import ComponentType


class StagedEvaluationError(Exception):
    """A plan that cannot be priced as stated: refused with a reason rather than defaulted around.

    Raised for statements about the plan, not the engine: a first stage not starting in year 0, decreasing stage years,
    a stage starting past the horizon, stages simulated for different years or periods, an unknown perspective id, a
    country without price data, or neither a price basis year nor a start year. The staged CLI turns it into exit code
    2 with a `problems.json`; engine errors (an unresolvable subject, a missing data file) give exit code 3 instead. A
    refused `--parameters` file reports every offending key at once through `problems`; without them the file has one
    row worded from the message.

    Args:
        message: The refusal, in one sentence.
        problems: The `problems.json` rows, each with `path`, `code`, `message` and optionally `accepted`; empty for a
            refusal about the plan as a whole.
    """

    def __init__(self, message: str, problems: Sequence[Mapping[str, Any]] = ()) -> None:
        """Store the message and, for a parameter refusal, the per-key problem rows."""
        super().__init__(message)
        self.problems: Tuple[Mapping[str, Any], ...] = tuple(problems)


class StagedEngineError(RuntimeError):
    """The engine contradicted itself while pricing a plan; never the caller's fault.

    Raised when the stages of one plan, priced under one parameter set against one database, resolve a carrier's
    escalation rate or tariff differently. The CLI turns it into exit code 3.
    """


class StagedCategories:
    """Which cost categories the splice takes from where; the only place the module names a `CostCategory`.

    The splice decides for every entry of every stage's own evaluation whether it belongs to the plan and in which
    year:

    - `STAGE_START`: flows a stage causes when it starts. Taken from the stage's year-0 entries, only for subjects the
      stage pays for (`StagedEvaluator._charged_subjects`), moved to the stage's start year and escalated to it at the
      investment escalation rate. A reader's quote is taken as stated, and a subsidy as its stage valued it (on the
      cost the plan books in the stage's year), times the share the stage pays.
    - `LOAN_SCHEDULE`: the stage's loan, taken out anew on what the stage books in its year and dated from its start;
      flows past the horizon are dropped (`StagedEvaluator._stage_loan`).
    - `OWN_PURCHASE_AGEING`: REPLACEMENT entries of a subject the stage pays for, shifted by the stage's start year and
      escalated like the purchase they follow. Replacements of an inherited subject are not shifted; the stage's
      register already dates them.
    - SUBSIDY entries after the stage's own year 0 (tax-credit instalments, per-kWh operational payments) are dated
      from the stage's start (own year `y` is plan year `from_year + y`) and dropped past the horizon. A tax credit is
      scaled only by the stage's share and kept whichever stage is active, since it is owed for money spent. An
      operational payment is kept while the earning installation is in the house (`_StageCharges.leaves_house_in`), and
      rebased to a piece's share of the energy where the active stage carries the subject in pieces
      (`StagedEvaluator._operational_rebasing`).
    - Everything else (energy, maintenance, fixed operation, feed-in, CO2 price, levy, replacement reserve) is taken
      from the stage active in the entry's year.

    Every stage's evaluation is already in the money of the plan's year 0 (`YearZeroPriceLevel`), so the splice only
    escalates from year 0 to the stage's year. A later stage's subsidies and modernisation-levy basis are valued at the
    price level the plan books its purchase at (`StagedEvaluator._booked_price_levels`). `RESIDUAL_VALUE` is in no set:
    no stage's residual is taken, and the splice computes every residual itself (`StagedEvaluator._residual_entries`).
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

    #: The stage's own debt service, which the splice replaces by the loan on the booked figures.
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
    """The plan's timeline together with the stage each of its entries came from.

    The stage map can only be built while splicing; the document needs it to attribute a debt-service payment to the
    loan being repaid rather than to the stage active in the payment year.

    Attributes:
        timeline: The spliced, sign-validated timeline.
        stage_by_entry: One stage index per entry of `timeline`, in timeline order.
    """

    timeline: CashFlowTimeline
    stage_by_entry: Tuple[int, ...]


@dataclass(frozen=True)
class _SpliceTerms:
    """What the splice needs per plan beyond the stages' own evaluations.

    Attributes:
        quoted_by_stage: Per stage, the subjects whose year-0 purchase is a reader's quote, booked unescalated; empty
            without quotes.
        financing: The perspective's financing plan, or None for cash; each stage's loan is taken out on what the stage
            books (`StagedEvaluator._stage_loan`).
    """

    quoted_by_stage: Tuple[FrozenSet[str], ...] = ()
    financing: Optional[FinancingPlan] = None


@dataclass(frozen=True)
class _StageCharges:
    """What one stage pays for and at what price level, bundled for the splice's per-entry decisions.

    Attributes:
        charged: Subject -> the share of its year-0 investment-class flows the stage pays.
        carried_over: Subjects the stage inherits unchanged and does not pay for again.
        rates: Subject -> its own investment escalation rate, for subjects with an asset class.
        default_rate: The general investment escalation rate, for synthetic subjects such as the replacement reserve.
        quoted: Subjects whose year-0 purchase in this stage is a reader's quote (the measure's main and further
            subjects); booked as stated, never escalated.
        operational: `(subject, scheme id)` -> the carrier paid on, for every per-kWh (OPERATIONAL) award of the
            stage's evaluation.
        operational_rebased: `(subject, scheme id)` -> plan year -> the factor an operational payment is booked with,
            for years whose active stage carries the subject in pieces; absent otherwise.
        leaves_house_in: Subject -> the plan year a later stage buys it whole again or drops it, ending its operational
            payments; absent for a subject that stays to the horizon.
    """

    charged: Dict[str, float]
    carried_over: Set[str]
    rates: Dict[str, float]
    default_rate: float
    quoted: FrozenSet[str] = frozenset()
    operational: Mapping[Tuple[str, str], EnergyCarrier] = field(default_factory=dict)
    operational_rebased: Mapping[Tuple[str, str], Mapping[int, float]] = field(default_factory=dict)
    leaves_house_in: Mapping[str, int] = field(default_factory=dict)

    def share_of(self, subject: str) -> float:
        """Return how much of one subject's year-0 figure this stage pays: all of it or none."""
        if subject in self.charged:
            return self.charged[subject]
        return 0.0 if subject in self.carried_over else 1.0

    def escalation_factor(self, subject: str, from_year: int) -> float:
        """Return the price level of the stage's year relative to year 0, for one subject."""
        return (1.0 + self.rates.get(subject, self.default_rate)) ** from_year

    def stage_start_factor(self, subject: str, from_year: int) -> float:
        """Return the price level one subject's year-0 flows are booked at in the stage's year.

        This is `escalation_factor`, except that a reader's quote is taken as stated (its author already priced that
        year). The quoted subject's later replacements are database prices and still escalate.
        """
        return 1.0 if subject in self.quoted else self.escalation_factor(subject, from_year)

    def booked_factor(self, entry: CashFlowEntry, from_year: int) -> float:
        """Return the factor one of the stage's year-0 entries is booked with in the stage's year.

        The share the stage pays times `stage_start_factor`, except for a subsidy: the stage's evaluation already
        valued it on the cost at that price level (`StagedEvaluator._booked_price_levels`), so only the share applies.
        """
        if entry.category is CostCategory.SUBSIDY:
            return self.share_of(entry.subject)
        return self.share_of(entry.subject) * self.stage_start_factor(entry.subject, from_year)


class LifeOrigin(str, enum.Enum):
    """Where a subject's service life came from, as `economics_result.json` states it.

    `REQUEST` is a `lifetime_override_in_years` on the subject's cost facts; `COST_DATABASE` is the database entry's
    `service_life_in_years` for the asset class at the price basis year; an override wins, as in
    `context_resolution.py`. `ENGINE_FALLBACK` is an override that is the engine's fallback life, used where the
    database has no entry for the class (`ComponentCostFacts.lifetime_is_engine_fallback`).
    """

    REQUEST = "request"
    COST_DATABASE = "cost_database"
    ENGINE_FALLBACK = "engine_fallback"


@dataclass(frozen=True)
class SubjectLife:
    """The lifetime and age one evaluation priced a cost subject with.

    It lets a reader follow a replacement year in the document. The life is read by the engine's chain
    (`StagedEvaluator._service_life`); the year is the register entry a kept subject is aged from, or the start of the
    stage that bought it.

    Attributes:
        service_life_years: The service life in years.
        service_life_origin: Where it came from.
        installation_year: The calendar year the subject counts as installed: a kept asset's register year, or plan
            year 0 (`StagedEvaluator.plan_year_zero`) plus `from_year` of the buying stage.
        installation_year_origin: Where that year came from; None for a register entry that does not say.
    """

    service_life_years: float
    service_life_origin: LifeOrigin
    installation_year: int
    installation_year_origin: Optional[InstallationYearOrigin]


class InvestmentOrigin(str, enum.Enum):
    """Where a subject's year-0 investment came from, as `economics_result.json` states it.

    `READER_QUOTE`: the reader's quote, for the measure's main subject. `INCLUDED_IN_READER_QUOTE`: a further subject
    of that measure, bought at zero since the quote covers the whole job. `REQUEST`: an
    `investment_cost_override_in_euro` from the calculation's inputs (e.g. an envelope measure's cost block).
    `COST_DATABASE`: the database entry for the asset class.
    """

    READER_QUOTE = "reader_quote"
    INCLUDED_IN_READER_QUOTE = "included_in_reader_quote"
    REQUEST = "request"
    COST_DATABASE = "cost_database"


@dataclass(frozen=True)
class InvestmentOverride:
    """The reader's quote for one measure of one stage, resolved to the subjects it prices.

    A quote is an installed total in euro for one measure. It replaces the year-0 investment, planning and removal of
    the measure's main subject in that stage; the measure's other subjects there are bought at zero and keep their
    lifetimes and later database-priced replacements. The caller decides which subject is the main one (for RenoVisor,
    `hisim.renovisor.economics.MainSubjects`).

    Args:
        stage: The index of the stage the quote is for.
        measure_id: The catalogue measure it quotes.
        amount_in_euro: The quote, positive and exact, booked nominal in the stage's year as stated (never escalated).
        source: Where the quote comes from, as the reader stated it.
        main_subject: The cost subject the quote prices; one without cost facts in the stage (a measure HiSim holds no
            price for) is bought as a `QuotedPurchase`.
        other_subjects: The measure's further subjects in that stage, bought at zero.
    """

    stage: int
    measure_id: str
    amount_in_euro: float
    source: str
    main_subject: str
    other_subjects: Tuple[str, ...] = ()

    #: How the provenance ledger and a quoted subject's facts cite the main subject's price.
    MAIN_SOURCE: ClassVar[str] = "reader's quote for {measure_id} (stage {stage}): {source}"

    #: How they cite a further subject's zero.
    INCLUDED_SOURCE: ClassVar[str] = "included in the reader's quote for {measure_id} (stage {stage}): {source}"

    def cited(self, main: bool) -> str:
        """Return the source sentence for the main subject's price or for a further subject's zero."""
        template = self.MAIN_SOURCE if main else self.INCLUDED_SOURCE
        return template.format(measure_id=self.measure_id, stage=self.stage, source=self.source)

    def covers(self, subject: str) -> Optional[bool]:
        """Return True if the quote prices `subject` as main subject, False as a further one, else None."""
        if subject == self.main_subject:
            return True
        if subject in self.other_subjects:
            return False
        return None


class IncrementSubjects:
    """Names the subject a later stage's enlargement of a kept subject is bought as (an increment).

    A stage that enlarges something the house keeps (a 5 kWp array grown to 8 kWp) buys the 3 kWp increment as a
    purchase of its own, with its own life and replacements, while the original unit keeps ageing. The engine prices
    one subject per name, so the increment gets its own name, formed only here.
    """

    #: What separates the enlarged subject from the stage in :attr:`NAME`.
    SEPARATOR: ClassVar[str] = "#increment_stage"

    #: The increment's subject: the enlarged subject and the stage that bought the increment.
    NAME: ClassVar[str] = "{subject}" + SEPARATOR + "{stage}"

    @classmethod
    def name(cls, subject: str, stage: int) -> str:
        """Return the subject name of the increment stage `stage` adds to `subject`."""
        return cls.NAME.format(subject=subject, stage=stage)

    @classmethod
    def base_of(cls, subject: str) -> Optional[str]:
        """Return the subject an increment enlarges, or None for a subject that is not an increment."""
        base, separator, stage = subject.rpartition(cls.SEPARATOR)
        return base if separator and base and stage.isdigit() else None


@dataclass(frozen=True)
class Stage:
    """One stage of a plan: a simulated state of the house, the year the plan puts it in place, and its name.

    `stages[0]` is the reference, the house as it is today held over the whole horizon, and starts in year 0.

    Args:
        inputs: The stage's simulated state, as `serialization.read_inputs` reads it from a finished job's directory.
        from_year: The year, relative to the start of the horizon, the stage becomes the state of the house; 0 for the
            first stage and never decreasing. A stage sharing its predecessor's year supersedes it immediately.
        label: What the frontend calls this stage ("baseline", "stage 1"); copied to the document unchanged.
        measures: Catalogue measure ids added in this stage; provenance only, the money follows the simulated state.
        job_id: The backend's id of the job that produced `inputs`, or None.
    """

    inputs: EvaluationInputs
    from_year: int
    label: str
    measures: Tuple[str, ...] = ()
    job_id: Optional[str] = None


@dataclass
class StagedResult:
    """One priced plan: its reference, its staged self, and the difference between them.

    Every figure of the result document is a filter or pivot of the two `LifecycleCostResult` objects here. `per_stage`
    is kept so per-subject figures can be read off the stage that paid for the subject.

    Attributes:
        reference: `stages[0]` evaluated alone over the whole horizon, the "do nothing" case.
        plan: The staged plan: one timeline spliced from `per_stage`, aggregated as an ordinary evaluation.
        comparison: `results.compare(reference, plan, "reference", "plan")`; deltas are plan minus reference per slot,
            so a negative NPV delta means the plan is cheaper.
        stages: The stages as priced: as given, except an enlarged subject is carried as its original unit plus one
            `IncrementSubjects` subject per enlargement.
        per_stage: Each stage evaluated alone over the full horizon, in stage order, with the merged register of the
            stages before it; all share one provenance ledger (`ledger`).
        charged_subjects_by_stage: Per stage, subject -> the share of its year-0 figure the stage pays (1.0 for a new
            subject or an increment); carried-over subjects are absent.
        active_stage_by_year: The stage the house is in for each horizon year 0..T (the last stage whose `from_year` is
            at or below the year).
        stage_by_entry: The stage each entry of `plan.timeline` came from, in timeline order, so a debt-service payment
            can be attributed to the stage that borrowed.
        subsidy_catalog_id: The catalogue the plan was priced under (`StagedEvaluator.catalog_id`), or None for a plan
            priced without one (subsidy mode NONE).
        energy_echo: The per-carrier escalation rates and year-1 prices used, with origins; None for a result assembled
            by hand.
        plan_start_year: The calendar year of plan year 0 as the caller stated it, or None; the document dates its
            years from this alone.
        price_basis_year: The price basis year actually used, as `StagedEvaluator.evaluate` resolved it; None only for
            a result assembled by hand.
        price_basis_year_origin: `EchoOrigin.PLAN_START_YEAR` when the start year supplied the price basis year; None
            otherwise.
        lives_by_stage: Per stage, the `SubjectLife` of every cost subject; empty for a result assembled by hand.
        investment_overrides: The resolved reader's quotes, in the order given; empty without any.
        stage_start_share_by_stage: Per stage, the share of each subject's year-0 figures the stage pays, used for its
            awards and subsidy maxima (`subsidy_scale`); empty for a result assembled by hand.
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
    price_basis_year: Optional[int] = None
    price_basis_year_origin: Optional[EchoOrigin] = None
    lives_by_stage: Tuple[Dict[str, SubjectLife], ...] = field(default_factory=tuple)
    investment_overrides: Tuple[InvestmentOverride, ...] = field(default_factory=tuple)
    stage_start_share_by_stage: Tuple[Dict[str, float], ...] = field(default_factory=tuple)

    @property
    def ledger(self) -> Optional[ProvenanceLedger]:
        """Return the plan's one provenance ledger, which `cost_provenance.json` publishes.

        Every stage was evaluated into it, so it resolves ids on the reference, each stage and the plan alike. None
        only for a result assembled by hand without one.
        """
        return self.plan.ledger

    def stage_of_year(self, year: int) -> int:
        """Return the index of the stage the house is in during one horizon year.

        Args:
            year: A year index relative to the start of the horizon.

        Returns:
            The last stage whose `from_year` is at or below `year`; 0 for a year outside the horizon.
        """
        if 0 <= year < len(self.active_stage_by_year):
            return self.active_stage_by_year[year]
        return 0

    def stage_of_timeline_entry(self, position: int) -> Optional[int]:
        """Return the index of the stage the plan's `position`-th timeline entry came from.

        Args:
            position: The entry's index in `plan.timeline.entries`.

        Returns:
            The stage index, or None for a result built without the stage map (e.g. assembled by hand in a test).
        """
        if 0 <= position < len(self.stage_by_entry):
            return self.stage_by_entry[position]
        return None

    def stage_of_subject(self, subject: str) -> Optional[int]:
        """Return the index of the last stage that paid for one cost subject, or None when none did.

        The document stamps this on every `by_subject` row; a baseline subject never re-bought (a boiler kept
        throughout) belongs to no stage.

        Args:
            subject: The timeline subject name.

        Returns:
            The index of the last stage that charged the subject, or None.
        """
        found: Optional[int] = None
        for index, charged in enumerate(self.charged_subjects_by_stage):
            if subject in charged:
                found = index
        return found

    def subsidy_scale(self, stage: Optional[int], subject: str) -> float:
        """Return the factor one stage's year-0 awards for one subject, and their maxima, are booked with.

        It is only the share the stage pays: the stage's evaluation already valued its subsidies on the cost as booked
        in the stage's year, so no price level is applied on top.

        Args:
            stage: The stage, or None on the reference.
            subject: The measure's cost subject.

        Returns:
            The factor; 1.0 on the reference and for a result without the factors.
        """
        if stage is None or stage >= len(self.stage_start_share_by_stage):
            return 1.0
        return self.stage_start_share_by_stage[stage].get(subject, 1.0)

    def purchase_stage(self, subject: str, staged: bool) -> Optional[int]:
        """Return the stage whose purchase of a subject a document row describes.

        On the plan, the last stage that charged the subject (`stage_of_subject`), else the last stage that has it; on
        the reference, stage 0.

        Args:
            subject: The cost subject.
            staged: Whether the plan rather than the reference is asked about.

        Returns:
            The stage index, or None when no stage has cost facts for the subject and none charged it.
        """
        if not staged:
            return 0 if self.stages else None
        charged = self.stage_of_subject(subject)
        if charged is not None:
            return charged
        for index in range(len(self.stages) - 1, -1, -1):
            if any(facts.subject == subject for facts in self.stages[index].inputs.cost_facts):
                return index
        return None

    def quote_of(self, subject: str, staged: bool) -> Optional[Tuple[InvestmentOverride, bool]]:
        """Return the reader's quote that priced a subject's purchase, and whether it is the main subject.

        Args:
            subject: The cost subject.
            staged: Whether the plan rather than the reference is asked about.

        Returns:
            `(quote, True for the main subject / False for a further one)`, or None when the described purchase
                (`purchase_stage`) was not quoted.
        """
        stage = self.purchase_stage(subject, staged)
        for override in self.investment_overrides:
            covered = override.covers(subject)
            if override.stage == stage and covered is not None:
                return override, covered
        return None

    def investment_origin(self, subject: str, staged: bool) -> Tuple[Optional[InvestmentOrigin], Optional[str]]:
        """Return where a subject's year-0 investment came from, and the source sentence that states it.

        Args:
            subject: The cost subject.
            staged: Whether the plan rather than the reference is asked about.

        Returns:
            `(origin, source)`: the quote and its source, a request override and its `override_source`, or the cost
                database with no source; `(None, None)` for a subject without cost facts and without a quote (a
                carrier, a measure-only row).
        """
        quote = self.quote_of(subject, staged)
        if quote is not None:
            override, main = quote
            return (
                InvestmentOrigin.READER_QUOTE if main else InvestmentOrigin.INCLUDED_IN_READER_QUOTE,
                override.source,
            )
        stage = self.purchase_stage(subject, staged)
        if stage is None:
            return None, None
        facts = next(
            (entry.facts for entry in self.stages[stage].inputs.cost_facts if entry.subject == subject), None
        )
        if facts is None:
            return None, None
        if facts.investment_cost_override_in_euro is not None:
            return InvestmentOrigin.REQUEST, facts.override_source
        return InvestmentOrigin.COST_DATABASE, None

    def life_of(self, subject: str, staged: bool) -> Optional[SubjectLife]:
        """Return the lifetime and age one subject was priced with, on the reference or on the plan.

        The reference is stage 0's evaluation; on the plan the subject is read off the stage that last charged it, else
        the last stage that has it.

        Args:
            subject: The cost subject.
            staged: Whether the plan rather than the reference is asked about.

        Returns:
            The `SubjectLife`, or None for a subject no stage prices as a device (a carrier, a synthetic subject) or a
                result without lives.
        """
        if not self.lives_by_stage:
            return None
        if not staged:
            return self.lives_by_stage[0].get(subject)
        stage = self.stage_of_subject(subject)
        if stage is not None and subject in self.lives_by_stage[stage]:
            return self.lives_by_stage[stage][subject]
        for lives in reversed(self.lives_by_stage):
            if subject in lives:
                return lives[subject]
        return None


class StagedEvaluator:
    """Prices a staged renovation plan by splicing per-stage evaluations.

    One instance binds a `CostDatabase`; assumptions, perspective and subsidy catalogue are arguments of `evaluate`, so
    stored stages can be re-priced under other assumptions without re-reading data files. No simulation runs inside the
    calculation. The class holds no state between calls and mutates none of its arguments: a stage's `EvaluationInputs`
    is copied before its register is replaced.

    Example::

        evaluator = StagedEvaluator(CostDatabase(None))
        result = evaluator.evaluate(stages, EconomicParameters(country="IE"), perspective, None)
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

    #: Message of the refusal raised when nothing states the calendar year of plan year 0.
    YEAR_ZERO_MESSAGE = (
        "neither the stages nor the parameters state a price_basis_year and the plan states no "
        "plan_start_year, so nothing says which calendar year the plan's year 0 is; the stages' "
        "simulation year is the year of their weather and dates nothing. Name price_basis_year "
        "or plan_start_year."
    )

    def __init__(self, cost_database: CostDatabase) -> None:
        """Bind the evaluator to one cost database.

        Args:
            cost_database: The device, price and escalation data every stage is priced against; never modified.
        """
        self.database = cost_database

    def evaluate(
        self,
        stages: Sequence[Stage],
        parameters: EconomicParameters,
        perspective: Perspective,
        catalog: Optional[SubsidyCatalog] = None,
        plan_start_year: Optional[int] = None,
        investment_overrides: Sequence[InvestmentOverride] = (),
    ) -> StagedResult:
        """Price one plan: evaluate every stage, splice the timelines, and compare against stage 0.

        The steps are: validate the plan; evaluate each stage over the full horizon with the register of the stages
        before it, all recording into one shared provenance ledger; splice operating flows by active year and
        investment flows by stage start year; aggregate the spliced timeline as an ordinary evaluation does; and
        compare the result with `stages[0]`.

        Args:
            stages: The plan in ascending `from_year` order; at least one stage.
            parameters: The assumptions every stage is priced under (one set for the whole plan).
            perspective: The accounting frame every stage is evaluated under.
            catalog: The subsidy catalogue in force, or None, in which case every scheme stays undetermined and the
                plan is priced with subsidy mode NONE (`priced_under`).
            plan_start_year: The calendar year the plan starts in, or None. It dates the document's calendar years,
                sets plan year 0 (`plan_year_zero`), and supplies the price basis year when `parameters` states none
                (`effective_price_basis_year`).
            investment_overrides: The reader's quotes, each resolved to the subjects it prices. A quoted stage is
                evaluated with its main subject's year-0 purchase at the quote and the measure's other subjects at zero
                (`purchase_cost_override_in_euro`); the stage pays the whole quote, unescalated, in its year.
                Everything after that purchase is priced as without it.

        Returns:
            The `StagedResult`, carrying the catalogue id (`catalog_id`).

        Raises:
            ValueError: If `plan_start_year` lies outside `PlanYearBounds`.
            StagedEvaluationError: `parameters.price_basis_year.missing` when neither a `price_basis_year` nor a
                `plan_start_year` is stated (`YEAR_ZERO_MESSAGE`), or any other plan this module refuses (see the
                class).
            hisim.economics.evaluator.UnresolvableSubjectsError: When a stage declares a cost subject nothing can
                price; an engine error, not wrapped.
        """
        if plan_start_year is not None and not (
            PlanYearBounds.MINIMUM <= plan_start_year <= PlanYearBounds.MAXIMUM
        ):
            raise ValueError(
                f"plan_start_year {plan_start_year!r} lies outside {PlanYearBounds.MINIMUM}.."
                f"{PlanYearBounds.MAXIMUM}: a plan's start year is a calendar year."
            )
        ordered = tuple(stages)
        overrides = tuple(investment_overrides)
        parameters, perspective = self.priced_under(parameters, perspective, catalog)
        self._validate(ordered, parameters)
        basis_year_origin: Optional[EchoOrigin] = None
        self._validate_overrides(ordered, overrides)
        # An enlargement of a kept subject is bought as its own subject; from here on
        # every stage carries it so, and a quote for the enlarged subject prices the increment.
        ordered = self.split_increments(ordered)
        overrides = self._quotes_on_increments(ordered, overrides)
        if parameters.price_basis_year is None and plan_start_year is not None:
            # Resolved once, here, so every stage's evaluation reads the same basis year and the
            # engine's own fallback to the simulation year never runs inside this plan. The one
            # place both entry points resolve it: `staged --parameters` leaves it unset for this.
            parameters = replace(
                parameters,
                price_basis_year=effective_price_basis_year(
                    parameters, self.database, ordered[0].inputs.simulation_year, plan_start_year
                ),
            )
            basis_year_origin = EchoOrigin.PLAN_START_YEAR
            log.warning(
                f"No price basis year stated by the stages or the parameters; the plan is priced at "
                f"{parameters.price_basis_year}, taken from plan_start_year {plan_start_year}."
            )
        if parameters.price_basis_year is None:
            # Nothing states year 0, and the engine's fallback would take the weather year.
            path = f"{ParameterKeys.ROOT_PATH}.{ParameterKeys.PRICE_BASIS_YEAR}"
            problem = ParameterProblem(
                path=path, code=ParameterProblemCodes.MISSING.format(path=path), message=self.YEAR_ZERO_MESSAGE
            )
            raise StagedEvaluationError(self.YEAR_ZERO_MESSAGE, [problem.to_json()])
        price_basis_year = parameters.price_basis_year
        year_zero = self.plan_year_zero(plan_start_year, price_basis_year)
        # Full-cost method: the reference pays every
        # end-of-life renewal, so no stage books an anyway credit for what it replaces.
        evaluator = EconomicEvaluator(
            self.database, parameters, catalog, plan_year_zero=year_zero, book_anyway_credit=False
        )
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
        lives_by_stage: List[Dict[str, SubjectLife]] = []
        quoted_by_stage = tuple(
            frozenset(
                subject
                for override in overrides
                if override.stage == index
                for subject in (override.main_subject, *override.other_subjects)
            )
            for index in range(len(ordered))
        )
        for index, stage in enumerate(ordered):
            quotes = [override for override in overrides if override.stage == index]
            charged = self._charged_subjects(ordered, index)
            for override in quotes:
                # The quote is the stage's whole job, so the stage pays all of it; where the stage
                # enlarges a subject, the quote already names the increment (`_quotes_on_increments`).
                charged[override.main_subject] = 1.0
            inputs = self._staged_inputs(ordered, index, charged_by_stage, year_zero)
            inputs, purchases = self._quoted_inputs(inputs, quotes)
            charges = self._stage_charges(ordered, index, charged, evaluator, parameters, quoted_by_stage[index])
            booked = self._booked_price_levels(inputs, stage.from_year, charges)
            per_stage.append(evaluator.evaluate(inputs, perspective, ledger, purchases, booked))
            charged_by_stage.append(charged)
            lives_by_stage.append(
                self._subject_lives(
                    inputs, stage.from_year, index, charged, perspective, parameters, price_basis_year, year_zero
                )
            )

        spliced = self._splice(
            ordered,
            tuple(per_stage),
            tuple(charged_by_stage),
            evaluator,
            parameters,
            active_by_year,
            _SpliceTerms(quoted_by_stage=quoted_by_stage, financing=perspective.financing),
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
            price_basis_year=price_basis_year,
            price_basis_year_origin=basis_year_origin,
            lives_by_stage=tuple(lives_by_stage),
            investment_overrides=overrides,
            stage_start_share_by_stage=tuple(
                self._stage_start_shares(ordered, index, charged_by_stage[index], evaluator, parameters)
                for index in range(len(ordered))
            ),
        )

    @staticmethod
    def plan_year_zero(plan_start_year: Optional[int], price_basis_year: int) -> int:
        """Return the calendar year of the plan's year 0.

        It is `plan_start_year` when the plan states one, else the price basis year. Every register asset is aged at
        it, and a subject a stage buys counts as installed in `plan_year_zero + from_year` (also the published
        `installation_year`). Prices are read at the price basis year and escalated from it to this year
        (`YearZeroPriceLevel`), so year 0 is in its own calendar year's money. The stages' simulation (weather) year
        dates nothing, which is why `evaluate` refuses a plan that states neither year.

        Args:
            plan_start_year: The calendar year the plan starts in, or None.
            price_basis_year: The price basis year the plan is priced at, as resolved.

        Returns:
            The calendar year of plan year 0.
        """
        return plan_start_year if plan_start_year is not None else price_basis_year

    #: How a catalogue is named in the document: the country it applies to and the date the
    #: catalogue was taken from the programmes' own pages, which is the pair that identifies one
    #: version of one country's support landscape. The bare country would not: Ireland's schemes
    #: change every few months and a stored document has to say which of them it priced.
    CATALOG_ID_FORMAT = "{country}@{snapshot}"

    #: What stands in the date's place when the catalogue states no snapshot date.
    UNDATED_CATALOG = "undated"

    @classmethod
    def catalog_id(cls, catalog: Optional[SubsidyCatalog], country: str) -> Optional[str]:
        """Return how a priced plan names the subsidy catalogue it was priced under.

        Args:
            catalog: The catalogue in force, or None when the plan ran with none (every subsidy row is then
                undetermined).
            country: The country the plan was priced for.

        Returns:
            An id like `"IE@2026-09-19"`, or None without a catalogue.
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
        """Return the assumptions and perspective a plan is actually priced under, given its catalogue.

        Without a catalogue the plan is priced with subsidy mode NONE whatever the perspective asked, and
        `apply_subsidies` (only the document's echo of the mode) is turned off so the `parameters` block says what ran.
        No figure changes, since the engine books nothing without a catalogue. With a catalogue the arguments are
        returned unchanged. `evaluate` calls this, and so does `StagedDocument`; a second call changes nothing.

        Args:
            parameters: The assumptions the caller resolved.
            perspective: The perspective the caller resolved.
            catalog: The subsidy catalogue in force, or None.

        Returns:
            `(parameters, perspective)`: unchanged with a catalogue; otherwise with `SubsidyMode.none()` and
                `apply_subsidies` off.
        """
        if catalog is not None:
            return parameters, perspective
        return (
            replace(parameters, apply_subsidies=False),
            replace(perspective, subsidy_mode=SubsidyMode.none()),
        )

    # ------------------------------------------------------------------ validation

    def _validate(self, stages: Tuple[Stage, ...], parameters: EconomicParameters) -> None:
        """Refuse a plan that cannot be put on one timeline, naming what is wrong.

        Checked in order: a plan exists, it starts at year 0, years do not decrease, every stage starts inside the
        horizon, the stages were simulated under comparable conditions, and the database has price data for the
        country. Equal years are accepted: a stage sharing its predecessor's year supersedes it at once, which is the
        ordinary "package versus doing nothing" plan.

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
            # stage there never becomes active, so the plan equals the baseline alone. Anything
            # beyond it prices nothing and says nothing.
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

        `simulation_year` (the weather year, and the price basis fallback) and `simulated_period_fraction` (the divisor
        that turns a simulated period into a year) must agree across a plan; everything else may differ between stages.

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

    # ------------------------------------------------------------------ stated energy prices

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
        """Refuse stated energy prices that need the stages and the database to check.

        Refused are: a price for a carrier some stage bills under an explicit contract (its price signal may have
        shaped that stage's simulation); a carrier the country has no price entry for (its emission factor, carbon
        exposure and tax share come from one); and an all-in working price below the year-1 carbon price booked on top
        of it, which would leave a negative working price. All faults are reported at once, in the parameter block's
        codes.

        Args:
            stages: The plan as given.
            parameters: The assumptions, holding the stated prices.
            price_basis_year: The year the year-1 carbon price is read at.

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
        """Return the per-carrier rates and year-1 prices the plan was priced with, and their origins.

        Rates of billed carriers are the union of the stages' resolved assumptions, which must agree. A carrier the
        plan named but no stage bills is resolved through the same fallback chain. Prices are each carrier's contract
        (`priced_contract`), checked against the stages' billed tariffs, with the working price all-in (the database
        price plus the year-1 carbon price, or the stated price). The feed-in rate is echoed as `ELECTRICITY_FEED_IN`
        when the electricity contract pays one. A carrier billed under an explicit contract has no flat price and is
        left out of the prices.

        Args:
            stages: The plan as given.
            per_stage: Each stage's own evaluation.
            parameters: The assumptions the plan was priced under.
            price_basis_year: The price basis year.

        Returns:
            The echo, keyed by carrier in carrier order.

        Raises:
            StagedEngineError: If two stages resolved one carrier's rate or tariff differently, or the rebuilt contract
                differs from the billed one.
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
        """Return one carrier's year-1 price terms as the plan was priced with them, the working price all-in.

        Args:
            carrier: A carrier other than the feed-in one.
            billed: The tariff the stages billed it under, or None when no stage bills it.
            parameters: The assumptions.
            price_basis_year: The price basis year.

        Returns:
            The echoed terms.

        Raises:
            StagedEngineError: If the rebuilt contract differs from the billed one.
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

    #: Code of the refusal of a quote the evaluator cannot place: a stage the plan does not have,
    #: or two quotes for one measure of one stage. The staged command refuses both earlier, naming
    #: the key (``hisim.economics.staged_parameters``); this is the engine's own guard.
    OVERRIDE_PROBLEM_CODE = "parameters.investment_overrides.invalid"

    @classmethod
    def _validate_overrides(cls, stages: Tuple[Stage, ...], overrides: Tuple[InvestmentOverride, ...]) -> None:
        """Refuse a quote for a stage the plan does not have, or a second quote for one measure.

        Args:
            stages: The plan.
            overrides: The resolved quotes.

        Raises:
            StagedEvaluationError: Naming the first such quote.
        """
        seen: Set[Tuple[int, str]] = set()
        for position, override in enumerate(overrides):
            problem = None
            if not 0 <= override.stage < len(stages):
                problem = f"stage {override.stage} is not a stage of this plan of {len(stages)}."
            elif (override.stage, override.measure_id) in seen:
                problem = f"a second quote for {override.measure_id!r} in stage {override.stage}."
            elif override.amount_in_euro <= 0:
                problem = f"the quote {override.amount_in_euro!r} is not a positive amount."
            if problem is not None:
                path = f"{ParameterKeys.ROOT_PATH}.{ParameterKeys.INVESTMENT_OVERRIDES}[{position}]"
                raise StagedEvaluationError(
                    problem, [{"path": path, "code": cls.OVERRIDE_PROBLEM_CODE, "message": problem}]
                )
            seen.add((override.stage, override.measure_id))

    @staticmethod
    def _quoted_inputs(
        inputs: EvaluationInputs, quotes: Sequence[InvestmentOverride]
    ) -> Tuple[EvaluationInputs, List[QuotedPurchase]]:
        """Return one stage's inputs with the reader's quotes on its subjects' facts.

        The main subject's facts get the quote as `purchase_cost_override_in_euro`, each further subject's a zero, and
        `override_source` names the quote (after any existing source). A main subject without cost facts (a measure
        HiSim holds no price for) is bought as a `QuotedPurchase` instead.

        Args:
            inputs: The stage's inputs, with its register already merged in.
            quotes: The quotes for this stage.

        Returns:
            A copy of the inputs carrying the quotes, and the stage's quoted purchases; the inputs unchanged when the
                stage has no quote.
        """
        if not quotes:
            return inputs, []
        stated: Dict[str, Tuple[UncertainValue, str]] = {}
        purchases: List[QuotedPurchase] = []
        priced = {subject_facts.subject for subject_facts in inputs.cost_facts}
        for quote in quotes:
            amount = UncertainValue.exact(quote.amount_in_euro)
            if quote.main_subject in priced:
                stated[quote.main_subject] = (amount, quote.cited(main=True))
            else:
                purchases.append(QuotedPurchase(quote.main_subject, amount, quote.cited(main=True)))
            for other in quote.other_subjects:
                if other in priced:
                    stated[other] = (UncertainValue.exact(0.0), quote.cited(main=False))
        cost_facts = []
        for subject_facts in inputs.cost_facts:
            if subject_facts.subject not in stated:
                cost_facts.append(subject_facts)
                continue
            amount, source = stated[subject_facts.subject]
            facts = subject_facts.facts
            cited = f"{facts.override_source}; year-0 purchase: {source}" if facts.override_source else source
            cost_facts.append(
                replace(
                    subject_facts,
                    facts=replace(facts, purchase_cost_override_in_euro=amount, override_source=cited),
                )
            )
        return replace(inputs, cost_facts=cost_facts), purchases

    def _stage_start_shares(
        self,
        stages: Tuple[Stage, ...],
        index: int,
        charged: Dict[str, float],
        evaluator: EconomicEvaluator,
        parameters: EconomicParameters,
    ) -> Dict[str, float]:
        """Return the share of each of one stage's year-0 figures the stage pays, per subject.

        This is the factor the splice books the stage's subsidies with (`_StageCharges.booked_factor`), and so the
        factor a subsidy row's maximum is moved into the plan by.

        Args:
            stages: The plan.
            index: The stage.
            charged: What the stage pays for.
            evaluator: For the escalation rates in the charges bundle.
            parameters: For the general investment escalation rate.

        Returns:
            Subject -> the share paid, for every subject the stage has cost facts for or charges.
        """
        charges = self._stage_charges(stages, index, charged, evaluator, parameters)
        subjects = {facts.subject for facts in stages[index].inputs.cost_facts} | set(charged)
        return {subject: charges.share_of(subject) for subject in sorted(subjects)}

    @staticmethod
    def _booked_price_levels(
        inputs: EvaluationInputs, from_year: int, charges: "_StageCharges"
    ) -> Dict[str, float]:
        """Return the price level the plan books each of a stage's year-0 purchases at, per subject.

        That is the subject's investment escalation from year 0 to `from_year` (`_StageCharges.stage_start_factor`),
        1.0 for a reader's quote. The stage's evaluation values its subsidies on the cost at this level
        (`booked_price_levels`), so fixed amounts and euro caps apply to the cost the splice books. Only levels other
        than 1.0 are listed.

        Args:
            inputs: The stage's evaluation inputs, for its cost subjects.
            from_year: The stage's start year.
            charges: The stage's charges bundle, for the rates and quoted subjects.

        Returns:
            Subject -> price level, for subjects whose level is not 1.0.
        """
        levels = {facts.subject: charges.stage_start_factor(facts.subject, from_year) for facts in inputs.cost_facts}
        return {subject: level for subject, level in levels.items() if level != 1.0}

    @classmethod
    def charged_subjects(cls, stages: Sequence[Stage], index: int) -> Dict[str, float]:
        """Return which subjects stage `index` pays for, and at what share (public form of `_charged_subjects`).

        For a caller that must know before pricing whether a stage buys anything for a measure, e.g. the staged command
        refusing a quote for a measure a stage only carries over. Asked of the unsplit stages: a subject the stage
        enlarges is listed under its own name, since the stage buys its increment.

        Args:
            stages: The plan, as given.
            index: The stage.

        Returns:
            Subject -> the share of its year-0 flows the stage pays.
        """
        charged = cls._charged_subjects(cls.split_increments(tuple(stages)), index)
        return {IncrementSubjects.base_of(subject) or subject: share for subject, share in charged.items()}

    @classmethod
    def split_increments(cls, stages: Sequence[Stage]) -> Tuple[Stage, ...]:
        """Return the plan with every enlargement of a kept subject carried as a subject of its own.

        An increment is the extra size a later stage adds to a subject it keeps (e.g. a larger PV array under the same
        asset class, not newly replaced and not grown from zero). That stage keeps the unit it had at its old size and
        buys one `IncrementSubjects` subject per enlargement at the increment's size. Later stages holding the subject
        at that size carry the same pieces, so each ages on its own schedule; a stage that drops, replaces or shrinks
        the subject carries it whole again. A subject shrunk and grown again is split again.

        Pricing of the pieces:

        - The unit being enlarged, and every earlier increment, keeps the facts it was bought with (so its
          replacements, residual value and embodied CO2 stay those of its own purchase); a
          `purchase_cost_override_in_euro` it carried is dropped.
        - The new increment takes the enlarging stage's facts at its own size: a database-priced subject by the
          database law for that size (device cost `specific x size` or `specific x size^exponent`, plus fixed
          installation and planning cost). An `investment_cost_override_in_euro` or embodied-CO2 override is taken per
          unit of the stage's whole size times the increment's size. `installation_cost_override_in_euro` and
          `purchase_cost_override_in_euro` go to the increment whole.

        Every piece states its share of the energy sold (`share_of_energy_sold`, its size over the whole), on which
        per-kWh subsidies are paid, and every increment sets `own_register_entry`.

        Args:
            stages: The plan, as given.

        Returns:
            The plan with split cost facts; a stage with nothing split is returned as it was.
        """
        split: List[Stage] = []
        held: Dict[str, List[SubjectCostFacts]] = {}
        for index, stage in enumerate(stages):
            previous = {facts.subject: facts.facts for facts in stages[index - 1].inputs.cost_facts} if index else {}
            replaced = cls._newly_replaced_classes(tuple(stages), index) if index else frozenset()
            pieces: Dict[str, List[SubjectCostFacts]] = {}
            cost_facts: List[SubjectCostFacts] = []
            for subject_facts in stage.inputs.cost_facts:
                subject, facts = subject_facts.subject, subject_facts.facts
                before = previous.get(subject)
                if before is None or subject not in held or cls._held_whole(before, facts, replaced):
                    pieces[subject] = [subject_facts]
                elif facts.size > before.size:
                    increment = IncrementSubjects.name(subject, index)
                    pieces[subject] = held[subject] + [cls._increment_facts(subject_facts, increment, before.size)]
                elif len(held[subject]) > 1:
                    pieces[subject] = held[subject]
                else:
                    pieces[subject] = [subject_facts]
                cost_facts.extend(cls._piece_facts(subject_facts, pieces[subject]))
            # What the next stage carries is not bought there: it keeps no stated year-0 purchase.
            held = {
                subject: [
                    SubjectCostFacts(piece.subject, replace(piece.facts, purchase_cost_override_in_euro=None))
                    for piece in carried
                ]
                for subject, carried in pieces.items()
            }
            if len(cost_facts) == len(stage.inputs.cost_facts):
                split.append(stage)
            else:
                split.append(replace(stage, inputs=replace(stage.inputs, cost_facts=cost_facts)))
        return tuple(split)

    @staticmethod
    def _held_whole(before: ComponentCostFacts, facts: ComponentCostFacts, replaced: FrozenSet[ComponentType]) -> bool:
        """Return whether a subject the stage before also had is one unit again rather than grown pieces.

        True when it is bought whole (another class, a newly replaced class, grown from size zero) or shrunk.
        """
        return (
            before.asset_class != facts.asset_class
            or facts.asset_class in replaced
            or before.size <= 0.0
            or facts.size < before.size
        )

    @staticmethod
    def _increment_facts(subject_facts: SubjectCostFacts, name: str, size_before: float) -> SubjectCostFacts:
        """Return the increment one stage buys of a subject it enlarges, priced as `split_increments` describes.

        Args:
            subject_facts: The enlarging stage's facts for the whole subject.
            name: The increment's subject name (`IncrementSubjects`).
            size_before: The subject's size in the stage before.

        Returns:
            The stage's facts at the increment's size, with whole-subject investment and embodied-CO2 overrides taken
                per unit of size, and `own_register_entry` set.
        """
        facts = subject_facts.facts
        size = facts.size - size_before
        per_unit = size / facts.size
        investment = facts.investment_cost_override_in_euro
        embodied = facts.embodied_co2_override_in_kg
        return SubjectCostFacts(
            name,
            replace(
                facts,
                size=size,
                investment_cost_override_in_euro=investment.scale(per_unit) if investment is not None else None,
                embodied_co2_override_in_kg=embodied * per_unit if embodied is not None else None,
                own_register_entry=True,
            ),
        )

    @staticmethod
    def _piece_facts(subject_facts: SubjectCostFacts, pieces: List[SubjectCostFacts]) -> List[SubjectCostFacts]:
        """Return one stage's facts for a subject as the pieces it is carried in (`split_increments`).

        Args:
            subject_facts: The stage's facts for the whole subject.
            pieces: The pieces' facts in order (the original unit first), each at its own size; the sizes sum to the
                subject's. The last is the stage's own increment when it enlarges the subject.

        Returns:
            The facts unchanged for a one-piece subject, else one record per piece stating its share of the energy
                sold.
        """
        if len(pieces) == 1:
            return [subject_facts]
        whole = subject_facts.facts.size
        return [
            SubjectCostFacts(piece.subject, replace(piece.facts, share_of_energy_sold=piece.facts.size / whole))
            for piece in pieces
        ]

    @staticmethod
    def _quotes_on_increments(
        stages: Tuple[Stage, ...], overrides: Tuple[InvestmentOverride, ...]
    ) -> Tuple[InvestmentOverride, ...]:
        """Return the quotes with every subject a stage enlarges re-pointed to that stage's increment.

        A quote is the stage's whole job for the measure, and what the stage buys of an enlarged subject is the
        increment; the earlier unit is not re-priced.

        Args:
            stages: The plan, split (`split_increments`).
            overrides: The validated quotes.

        Returns:
            The quotes, re-pointed where a subject is enlarged in the quote's stage.
        """
        resolved: List[InvestmentOverride] = []
        for override in overrides:
            subjects = {facts.subject for facts in stages[override.stage].inputs.cost_facts}

            def bought(subject: str, stage: int = override.stage, held: Set[str] = subjects) -> str:
                increment = IncrementSubjects.name(subject, stage)
                return increment if increment in held else subject

            resolved.append(
                replace(
                    override,
                    main_subject=bought(override.main_subject),
                    other_subjects=tuple(bought(subject) for subject in override.other_subjects),
                )
            )
        return tuple(resolved)

    # ------------------------------------------------------------------ per-stage inputs

    @classmethod
    def _charged_subjects(cls, stages: Tuple[Stage, ...], index: int) -> Dict[str, float]:
        """Return which subjects stage `index` pays for, and at what share of its own year-0 figure.

        A subject is charged when it is present in this stage and absent from the stage before under the same asset
        class. A subject carried over unchanged is absent from the mapping (not present with zero). This is asked of
        the split plan (`split_increments`), where an enlarged subject is carried over at its old size and its
        increment is new and charged whole.

        Anything the stage's inventory newly declares replaced (`_newly_replaced_classes`) is bought whole, however
        large the old one was: a heating measure replacing a 430-litre buffer with a 970-litre one buys a 970-litre
        vessel. The stage's own evaluation already prices it so, with the old unit's removal and written-off book value
        (cost_spec.md §4.1). Without this rule a same-class replacement of equal or smaller size would be free.

        Args:
            stages: The plan as given.
            index: The stage to answer for.

        Returns:
            Subject -> the share of its year-0 investment-class flows the stage pays (1.0 for every subject it buys).
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
        return charged

    @classmethod
    def _newly_replaced_classes(cls, stages: Tuple[Stage, ...], index: int) -> FrozenSet[ComponentType]:
        """Return the asset classes stage `index`'s inventory replaces that the stage before did not.

        Each inventory entry names the classes that replace it (`ExistingAsset.replaced_by_asset_classes`). A plan's
        stages each carry every measure before them, so only the difference to the previous stage is this stage's own.

        Args:
            stages: The plan as given.
            index: The stage, at least 1.

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
        """Return the subjects stage `index` inherits from its predecessor without paying for them again.

        The complement of `_charged_subjects` among the stage's cost subjects; the splice uses it to decide what not to
        book. Other subjects in a stage's year-0 entries (the synthetic `financing` subject, an entry filed under an
        asset the stage tears out) are taken as the stage's own.

        Args:
            stages: The plan as given.
            index: The stage to answer for.
            charged: What `_charged_subjects` said this stage pays for, passed in so the two cannot disagree.

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
        plan_year_zero: int,
    ) -> EvaluationInputs:
        """Return stage `index`'s inputs with the register of every earlier stage's purchases merged in.

        This is all of the ageing across stages. The register (the list of existing assets the engine treats as already
        installed) holds the house inventory minus what an earlier stage tore out, plus one `ExistingAsset` with origin
        `STAGE` per subject an earlier stage paid for, installed in that stage's calendar year `plan_year_zero +
        from_year`. The engine's brownfield logic then schedules replacements, residual values and removal costs. The
        one fact the engine cannot derive is such a purchase's age (`stated_age_in_years`), since it would otherwise
        floor it to new: it is `-from_year_j` when this stage keeps the purchase of stage `j` (its first replacement
        falls in `from_year_j + L`), and `from_year - from_year_j` when this stage replaces it. Stage 0 is returned
        unchanged and adds nothing to later registers.

        Args:
            stages: The plan as given.
            index: The stage to build inputs for.
            charged_by_stage: What every earlier stage charged, in stage order (exactly `index` entries).
            plan_year_zero: The calendar year of plan year 0 (`plan_year_zero`), at which the engine ages the register;
                never the stages' simulation (weather) year.

        Returns:
            A copy of the stage's inputs with the merged register; the caller's record is not modified.
        """
        stage = stages[index]
        if index == 0:
            return stage.inputs
        facts_by_subject = {facts.subject: facts.facts for facts in stage.inputs.cost_facts}
        newly_charged_classes = {
            facts_by_subject[subject].asset_class
            for subject in self._charged_subjects(stages, index)
            if subject in facts_by_subject and not facts_by_subject[subject].own_register_entry
        }
        installed_classes: Set[ComponentType] = set()
        aged: Dict[str, ExistingAsset] = {}
        # Stage 0 is the house as it is: what it "charges" is the reference's own year-0 booking,
        # not a purchase the plan makes, and its equipment is already in the inventory register
        # with its real installation year. Letting it age in here would re-date a 2010 boiler to
        # the plan's year 0, so the heat pump that replaces it would write off a nearly new
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
                if facts.own_register_entry:
                    # An increment ages on an entry bound to it, beside the unit it
                    # enlarged, which it neither hides nor replaces. It leaves with that unit: a
                    # stage that no longer carries it has replaced, shrunk or removed the subject.
                    if subject in facts_by_subject:
                        aged[subject] = ExistingAsset(
                            asset_class=facts.asset_class,
                            size=facts.size,
                            size_unit=facts.size_unit,
                            installation_year=plan_year_zero + stages[earlier].from_year,
                            is_functional=True,
                            installation_year_origin=InstallationYearOrigin.STAGE,
                            stated_age_in_years=-stages[earlier].from_year,
                            subject=subject,
                        )
                    continue
                installed_classes.add(facts.asset_class)
                replaced = subject not in facts_by_subject and facts.asset_class in newly_charged_classes
                aged[subject] = ExistingAsset(
                    asset_class=facts.asset_class,
                    size=facts.size,
                    size_unit=facts.size_unit,
                    installation_year=plan_year_zero + stages[earlier].from_year,
                    is_functional=True,
                    replaced_by_asset_classes=[facts.asset_class] if replaced else [],
                    installation_year_origin=InstallationYearOrigin.STAGE,
                    # Replaced: written off in the year this stage starts. Kept: its replacements
                    # stay on the plan's own years, so it is aged at plan year 0 -- negative, and
                    # replaced one service life after it was bought.
                    stated_age_in_years=(
                        stage.from_year - stages[earlier].from_year if replaced else -stages[earlier].from_year
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
        """Return the house inventory a stage was simulated with, as a fresh list (empty for a greenfield stage)."""
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
        terms: _SpliceTerms = _SpliceTerms(),
    ) -> "_SplicedTimeline":
        """Build the plan's one timeline from the per-stage timelines (the splice).

        Entries are appended stage by stage, each stage in its own evaluation's order, so a one-stage plan reproduces
        its original timeline entry for entry. A second pass handles what cannot be decided entry by entry: residual
        values are written down from the plan's own last installation of each subject and put back where the stage's
        own residual stood, and the operating view's replacement reserve is re-levelized from the re-dated replacement
        schedule.

        Args:
            stages: The plan as given.
            per_stage: Each stage's own evaluation, in stage order.
            charged_by_stage: What each stage pays for, from `_charged_subjects`.
            evaluator: The bound engine, for per-asset-class investment escalation rates and the price basis year of
                the service lives.
            parameters: The assumptions, for the horizon and the general escalation rate.
            active_by_year: Which stage is active in each horizon year (`_active_by_year`).
            terms: The quotes and the financing plan; the defaults are a cash plan without quotes.

        Returns:
            The spliced timeline, sign-validated like any engine timeline, with the stage each entry came from.
        """
        horizon = parameters.observation_period_in_years
        active_indices = set(active_by_year)
        spliced: List[Optional[CashFlowEntry]] = []
        owners: List[int] = []
        residual_slots: Dict[str, int] = {}
        reserve_flows: List[Tuple[int, UncertainValue]] = []
        for index, stage in enumerate(stages):
            quoted = terms.quoted_by_stage[index] if index < len(terms.quoted_by_stage) else frozenset()
            operational = self._operational_awards(per_stage[index])
            charges = replace(
                self._stage_charges(stages, index, charged_by_stage[index], evaluator, parameters, quoted),
                operational=operational,
                operational_rebased=self._operational_rebasing(stages, index, operational, active_by_year),
                leaves_house_in=self._leaving_years(stages, index, charged_by_stage),
            )
            reserve_flows.extend(
                self._staged_reserve_flows(per_stage[index], index, stage.from_year, charges, active_by_year)
            )
            loan = self._stage_loan(per_stage[index], stage.from_year, charges, terms.financing, horizon)
            for entry in per_stage[index].timeline.entries:
                if entry.category is CostCategory.RESIDUAL_VALUE:
                    if index in active_indices:
                        residual_slots[entry.subject] = len(spliced)
                        spliced.append(None)
                        owners.append(index)
                    continue
                if self._is_loan_flow(entry):
                    # The stage's own loan is replaced by the loan on what the stage books, placed
                    # where the stage's own loan stood (so a plan of one stage keeps its order).
                    spliced.extend(loan)
                    owners.extend([index] * len(loan))
                    loan = []
                    continue
                moved = self._spliced_entry(
                    entry, index, stage.from_year, charges, active_by_year, horizon
                )
                if moved is not None:
                    spliced.append(moved)
                    owners.append(index)
            spliced.extend(loan)
            owners.extend([index] * len(loan))
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
        """Put the computed residuals into their slots and return one validated timeline.

        A slot is the position a stage's own `RESIDUAL_VALUE` entry stood at; the plan's residual for that subject
        takes it, keeping a one-stage plan in its original order. A residual no stage reserved a slot for is appended,
        in subject order for reproducible output; a slot whose subject earns nothing is dropped.

        Args:
            spliced: The first pass's entries, with None marking a residual slot.
            owners: The stage each position came from, parallel to `spliced`.
            residual_slots: Subject -> the position of its slot; only the last stage that reserved one counts.
            residuals: Subject -> `(stage, entry)` for every residual the plan earns.
            reserve: The plan's re-levelized annual replacement-reserve payment, written into the stages'
                `REPLACEMENT_RESERVE` entries.

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
        quoted: FrozenSet[str] = frozenset(),
    ) -> "_StageCharges":
        """Return what one stage pays for and at which price level, as the splice needs it.

        Args:
            stages: The plan as given.
            index: The stage.
            charged: What `_charged_subjects` said about this stage.
            evaluator: The bound engine, for per-asset-class escalation rates.
            parameters: The assumptions, for the general investment escalation rate.
            quoted: The stage's subjects whose year-0 purchase is a reader's quote.

        Returns:
            The bundle `_spliced_entry` consults.
        """
        return _StageCharges(
            charged=charged,
            carried_over=self._carried_over_subjects(stages, index, charged),
            rates=self._escalation_rates(stages[index], evaluator),
            default_rate=parameters.investment_price_escalation_rate,
            quoted=quoted,
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
        """Return one stage's replacement schedule, re-dated exactly as its REPLACEMENT entries are.

        The operating view charges no capital and levelizes replacements into a reserve (a sinking fund, cost_spec.md
        §4.2), so the reserve must follow the plan's replacement years. The flows come from
        `LifecycleCostResult.replacement_flows`, which exist under every perspective.

        Args:
            result: The stage's own evaluation.
            index: The stage's index.
            from_year: The stage's start year.
            charges: What the stage pays for and at which escalation rate.
            active_by_year: Which stage is active in each horizon year.

        Returns:
            `(plan year, nominal amount)` per replacement this stage contributes.
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
        """Return the residual value of every subject, written down from the plan's own last purchase.

        A single stage cannot compute this: it prices its purchase as if made in year 0, and a stage that inherits
        equipment writes it down not at all. On the spliced timeline each subject's last
        `INVESTMENT`/`PLANNING`/`REPLACEMENT` entry is the installation the plan charged, already escalated to its
        year. The residual entry copies it (subject, payer, provenance), moved to the horizon, recategorized and
        mirrored into a revenue. An increment that the stage active at the horizon does not carry left the house with
        its base subject and earns nothing.

        Args:
            placed: The first pass's entries with each one's stage, in timeline order.
            stages: The plan as given, for the cost facts the service lives come from.
            evaluator: The bound engine, for the price basis year of the database lookup.
            parameters: The assumptions, for the horizon and the country.

        Returns:
            Subject -> `(stage, entry)`; a subject that earns nothing at the horizon is absent.

        Raises:
            hisim.economics.database.CostDataError: If a subject states no service life and the cost database has none
                either (an engine failure, not a bad plan).
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
        final = stages[self._active_by_year(stages, horizon)[horizon]]
        in_house = {subject_facts.subject for subject_facts in final.inputs.cost_facts}
        residuals: Dict[str, Tuple[int, CashFlowEntry]] = {}
        for subject in sorted(last_install):
            facts = facts_by_subject.get(subject)
            if facts is None:
                continue
            if facts.own_register_entry and subject not in in_house:
                # An increment a later stage removed again -- by shrinking, replacing or dropping
                # the subject -- left the house with it; its book value is not written off
                # separately, so it earns no residual value at the horizon.
                continue
            year = last_install[subject]
            life, _origin = self._service_life(facts, price_basis_year, parameters)
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
    ) -> Tuple[float, LifeOrigin]:
        """Return one subject's service life in years and where it came from, by the engine's chain.

        An explicit `lifetime_override_in_years` wins, as in `context_resolution.py`; otherwise the cost database entry
        for the subject's asset class (or for `lifetime_of_asset_class`, the class of the system it is part of) at the
        price basis year states it. The splice must use the same number the stage's own schedule was built from.

        Args:
            facts: The subject's cost facts.
            price_basis_year: The plan's price basis year, as the engine resolved it.
            parameters: The assumptions, for the country whose device file is read.

        Returns:
            `(years, origin)`, with `LifeOrigin.REQUEST` for the override or `LifeOrigin.COST_DATABASE` for the
                database.

        Raises:
            hisim.economics.database.CostDataError: If neither source states one.
        """
        if facts.lifetime_override_in_years is not None:
            origin = LifeOrigin.ENGINE_FALLBACK if facts.lifetime_is_engine_fallback else LifeOrigin.REQUEST
            return float(facts.lifetime_override_in_years), origin
        # A subject renewed with another's system lives that class's life.
        entry = self.database.get_device_entry(
            facts.lifetime_of_asset_class or facts.asset_class, price_basis_year, parameters.country
        )
        return float(entry.service_life_in_years), LifeOrigin.COST_DATABASE

    def _subject_lives(
        self,
        inputs: EvaluationInputs,
        from_year: int,
        index: int,
        charged: Mapping[str, float],
        perspective: Perspective,
        parameters: EconomicParameters,
        price_basis_year: int,
        plan_year_zero: int,
    ) -> Dict[str, SubjectLife]:
        """Return the lifetime and installation year of every cost subject one stage is evaluated with.

        The life is `_service_life`'s. The installation year is the stage's start for a subject the stage buys
        (charges, outside the reference); otherwise it is what `installation_verdict` says: a kept asset's register
        year, or the stage's start for a subject the register does not hold.

        Args:
            inputs: The stage's inputs, with the register already merged in.
            from_year: The stage's start year, relative to the horizon.
            index: The stage's index.
            charged: What the stage pays for (`_charged_subjects`).
            perspective: For the installation context of the verdict.
            parameters: For the country the database is read for.
            price_basis_year: The plan's price basis year, for the service life.
            plan_year_zero: The calendar year of plan year 0; a subject this stage buys is installed in `plan_year_zero
                + from_year`.

        Returns:
            Subject -> its `SubjectLife`.
        """
        start = plan_year_zero + from_year
        lives: Dict[str, SubjectLife] = {}
        for subject_facts in inputs.cost_facts:
            facts = subject_facts.facts
            year: int = start
            year_origin: Optional[InstallationYearOrigin] = InstallationYearOrigin.STAGE
            if index == 0 or subject_facts.subject not in charged:
                kept = installation_verdict(
                    facts.asset_class,
                    perspective.installation_context,
                    inputs.existing_assets,
                    subject_facts.subject,
                    facts.own_register_entry,
                ).kept_asset
                if kept is not None:
                    year, year_origin = kept.installation_year, kept.installation_year_origin
            service_life_years, service_life_origin = self._service_life(facts, price_basis_year, parameters)
            lives[subject_facts.subject] = SubjectLife(
                service_life_years=service_life_years,
                service_life_origin=service_life_origin,
                installation_year=year,
                installation_year_origin=year_origin,
            )
        return lives

    @staticmethod
    def _active_by_year(stages: Tuple[Stage, ...], horizon: int) -> Tuple[int, ...]:
        """Return, for every horizon year 0..T, the index of the stage the house is in.

        A stage is active from its `from_year` until the next stage's; the last one to the end of the horizon. A stage
        starting past the horizon is never active, so a plan whose later stages all start past the horizon equals its
        baseline.

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
        """Return the investment escalation rate for each of a stage's subjects.

        A stage's year-0 figures are year-0 prices; booking them in year `t` means paying year-`t` prices, so each is
        escalated at its subject's own investment escalation rate (§3.2: explicit parameter, then the country defaults
        file, then the general rate).

        Args:
            stage: The stage whose subjects are rated.
            evaluator: The bound engine, which owns the fallback chain.

        Returns:
            Subject -> nominal annual rate, for every subject with cost facts. A subject absent from the mapping (the
                replacement reserve) is escalated at the general investment escalation rate by
                `_StageCharges.escalation_factor`.
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
        """Return one source entry's place in the plan, or None when it has none.

        This applies `StagedCategories` to one entry:

        - a stage-start entry is kept only for subjects the stage pays for, moved to and escalated to the stage's year
          (`_StageCharges.booked_factor`);
        - the stage's own loan flows never reach here; the splice takes the loan out anew (`_stage_loan`);
        - a replacement of a subject the stage buys is shifted and escalated with its purchase and kept only while the
          stage is active (`_replacement_entry`);
        - a later subsidy payment (a tax-credit instalment, an operational payment) is dated from the stage's start
          (`_later_subsidy_entry`);
        - everything else is kept only when its year belongs to this stage.

        Args:
            entry: One entry of the stage's own evaluation.
            index: The stage's index.
            from_year: The stage's start year.
            charges: What the stage pays for and at which escalation rate.
            active_by_year: Which stage is active in each horizon year.
            horizon: The last year index of the horizon.

        Returns:
            The entry as it appears on the plan's timeline, or None.
        """
        if entry.category in StagedCategories.STAGE_START and entry.year == 0:
            return self._stage_start_entry(entry, from_year, charges, horizon)
        if entry.category is CostCategory.SUBSIDY:
            return self._later_subsidy_entry(entry, from_year, charges, horizon)
        if entry.category in StagedCategories.OWN_PURCHASE_AGEING and charges.share_of(entry.subject) > 0.0:
            return self._replacement_entry(entry, index, from_year, charges, active_by_year, horizon)
        if 0 <= entry.year <= horizon and active_by_year[entry.year] == index:
            return entry
        return None

    @staticmethod
    def _stage_start_entry(
        entry: CashFlowEntry, from_year: int, charges: "_StageCharges", horizon: int
    ) -> Optional[CashFlowEntry]:
        """Return one of a stage's year-0 flows, moved to the stage's year and escalated to it.

        Args:
            entry: The stage's own year-0 entry.
            from_year: The stage's start year.
            charges: What the stage pays for and at which escalation rate.
            horizon: The last year index of the horizon.

        Returns:
            The entry in the stage's year, scaled by the stage's share and by that year's price level (not for a quoted
                purchase or a subsidy, which are valued in the stage's year already); None for a subject the stage
                inherits or a stage that never starts inside the horizon.
        """
        if charges.share_of(entry.subject) <= 0.0 or from_year > horizon:
            return None
        factor = charges.booked_factor(entry, from_year)
        return replace(entry, year=from_year, amount_in_euro=entry.amount_in_euro.scale(factor))

    @staticmethod
    def _later_subsidy_entry(
        entry: CashFlowEntry, from_year: int, charges: "_StageCharges", horizon: int
    ) -> Optional[CashFlowEntry]:
        """Return a subsidy payment the stage's award makes after its own year 0, in its plan year.

        Own year `y` is plan year `from_year + y`; a payment past the horizon is dropped. A tax-credit instalment was
        valued at the stage's price level already, so only the stage's share applies, and it is kept whichever stage is
        active. An operational payment (a nominal rate per kWh, never escalated) is kept while the earning installation
        is in the house (`_StageCharges.leaves_house_in`), and paid on the piece's share of the energy when the active
        stage splits its subject (`_StageCharges.operational_rebased`).

        Args:
            entry: The stage's own SUBSIDY entry of a year after 0.
            from_year: The stage's start year.
            charges: What the stage pays for and which of its awards are operational.
            horizon: The last year index of the horizon.

        Returns:
            The entry in its plan year, or None.
        """
        year = entry.year + from_year
        if year > horizon:
            return None
        key = (entry.subject, entry.subsidy_scheme_id or "")
        if key in charges.operational:
            if year >= charges.leaves_house_in.get(entry.subject, horizon + 1):
                return None
            rebased = charges.operational_rebased.get(key, {})
            if year in rebased:
                return replace(entry, year=year, amount_in_euro=entry.amount_in_euro.scale(rebased[year]))
            return replace(entry, year=year)
        share = charges.share_of(entry.subject)
        if share <= 0.0:
            return None
        return replace(entry, year=year, amount_in_euro=entry.amount_in_euro.scale(share))

    @staticmethod
    def _operational_awards(result: LifecycleCostResult) -> Dict[Tuple[str, str], EnergyCarrier]:
        """Return `(subject, scheme id)` -> carrier of every per-kWh (OPERATIONAL) award of one stage."""
        return {
            (decision.measure_subject, award.scheme_id): award.operational_carrier
            for decision in result.subsidy_decisions
            for award in decision.applied
            if award.payout_kind is PayoutKind.OPERATIONAL and award.operational_carrier is not None
        }

    @staticmethod
    def _annual_energy_sold(inputs: EvaluationInputs) -> Dict[EnergyCarrier, float]:
        """Return carrier -> the energy one stage sells a year, as the subsidy application annualizes it."""
        sold: Dict[EnergyCarrier, float] = {}
        for determinants in inputs.billing:
            if determinants.energy_sold_in_kwh:
                sold[determinants.carrier] = annualize(
                    determinants.energy_sold_in_kwh, inputs.simulated_period_fraction, guard_zero=True
                )
        return sold

    @classmethod
    def _operational_rebasing(
        cls,
        stages: Tuple[Stage, ...],
        index: int,
        operational: Mapping[Tuple[str, str], EnergyCarrier],
        active_by_year: Tuple[int, ...],
    ) -> Dict[Tuple[str, str], Dict[int, float]]:
        """Return how one stage's per-kWh payments are booked in years where a later stage splits their subject.

        When a later stage enlarges a subject, each piece (the original unit and each increment) is paid on its size
        share of the energy the enlarged installation sells (`ComponentCostFacts.share_of_energy_sold`), at its own
        scheme's rate, so no kWh is paid twice. In a year whose active stage carries the subject in pieces, a payment
        valued on a whole stage's energy is rebased to the piece's share of the active stage's energy; in other years
        it stays as the stage valued it.

        Args:
            stages: The plan, split (`split_increments`).
            index: The stage whose payments are asked about.
            operational: Its operational awards (`_operational_awards`).
            active_by_year: Which stage is active in each horizon year.

        Returns:
            `(subject, scheme id)` -> plan year -> factor on the stage's own amount, for the rebased awards and years
                only.
        """

        def share(stage: int, subject: str) -> Optional[float]:
            return next(
                (
                    subject_facts.facts.share_of_energy_sold
                    for subject_facts in stages[stage].inputs.cost_facts
                    if subject_facts.subject == subject
                ),
                None,
            )

        sold = {stage: cls._annual_energy_sold(stages[stage].inputs) for stage in {index, *active_by_year}}
        rebased: Dict[Tuple[str, str], Dict[int, float]] = {}
        for (subject, scheme), carrier in operational.items():
            valued = (share(index, subject) or 1.0) * sold[index].get(carrier, 0.0)
            if valued <= 0.0:
                continue
            factors: Dict[int, float] = {}
            for year, active in enumerate(active_by_year):
                piece = share(active, subject) if active > index else None
                if piece is not None and piece < 1.0:
                    factors[year] = piece * sold[active].get(carrier, 0.0) / valued
            if factors:
                rebased[(subject, scheme)] = factors
        return rebased

    @staticmethod
    def _leaving_years(
        stages: Tuple[Stage, ...], index: int, charged_by_stage: Tuple[Dict[str, float], ...]
    ) -> Dict[str, int]:
        """Return, for each of a stage's subjects, the plan year its installation leaves the house, if it does.

        That is the start year of the first later stage that drops the subject or buys it whole again (charges it at
        1.0). A later stage that keeps or only enlarges it leaves the installation in place.

        Args:
            stages: The plan.
            index: The stage whose purchases are asked about.
            charged_by_stage: What every stage charges.

        Returns:
            Subject -> plan year, for subjects that leave inside the plan.
        """
        leaving: Dict[str, int] = {}
        for facts in stages[index].inputs.cost_facts:
            for later in range(index + 1, len(stages)):
                present = any(other.subject == facts.subject for other in stages[later].inputs.cost_facts)
                if not present or charged_by_stage[later].get(facts.subject, 0.0) >= 1.0:
                    leaving[facts.subject] = stages[later].from_year
                    break
        return leaving

    @staticmethod
    def _is_loan_flow(entry: CashFlowEntry) -> bool:
        """Return whether an entry is a flow of the stage's own loan, which the splice takes out anew.

        These are the disbursement, the debt service and a soft loan's repayment grant (the SUBSIDY entry under the
        synthetic financing subject).
        """
        return (
            entry.category is CostCategory.LOAN_DISBURSEMENT
            or entry.category in StagedCategories.LOAN_SCHEDULE
            or entry.subject == FinancingConstants.FINANCING_SUBJECT
        )

    @classmethod
    def _stage_loan(
        cls,
        result: LifecycleCostResult,
        from_year: int,
        charges: "_StageCharges",
        financing: Optional[FinancingPlan],
        horizon: int,
    ) -> List[CashFlowEntry]:
        """Return the stage's loan, taken out on what the stage books in its year and dated from that year.

        The principal is the financed share of the stage's year-0 net investment as the plan books it
        (`_stage_start_entry`): quotes as stated, database prices escalated to the stage's year, grants as valued
        there, and only the stage's share of each subject. The terms are the stage's own (`resolve_loan_plan`) and the
        schedule comes from `build_financing_flows`, so a soft loan's repayment grant is a share of this principal; a
        slot where grants exceed the booked cost finances nothing. A payment in year T is kept.

        Args:
            result: The stage's own evaluation.
            from_year: The stage's start year.
            charges: What the stage pays for and at which price level.
            financing: The perspective's financing plan, or None for a cash purchase.
            horizon: The last year index of the horizon.

        Returns:
            The loan's entries in their plan years, in the engine's order; empty for cash, a stage that never starts
                inside the horizon, or nothing to finance.
        """
        if financing is None or from_year > horizon:
            return []
        net = UncertainValue.exact(0.0)
        for entry in result.timeline.entries:
            if (
                entry.year != 0
                or entry.category not in EngineCategoryRules.FINANCING_YEAR0_PRINCIPAL_CATEGORIES
                or cls._is_loan_flow(entry)
            ):
                continue
            booked = cls._stage_start_entry(entry, from_year, charges, horizon)
            if booked is not None:
                net = net + booked.amount_in_euro
        flows = build_financing_flows(
            resolve_loan_plan(financing, result.subsidy_decisions), Year0NetInvestment(amount=net), horizon - from_year
        )
        return [replace(flow, year=flow.year + from_year) for flow in flows]

    @staticmethod
    def _replacement_entry(
        entry: CashFlowEntry,
        index: int,
        from_year: int,
        charges: "_StageCharges",
        active_by_year: Tuple[int, ...],
        horizon: int,
    ) -> Optional[CashFlowEntry]:
        """Return the re-purchase of something the stage bought, moved by the stage's start year.

        It is dropped once another stage is active, since that stage schedules the remaining replacements from its own
        register and would otherwise book the same re-purchase twice. A replacement landing exactly on the horizon is
        dropped, as the engine does: a unit due at T is not bought.

        Args:
            entry: The stage's own REPLACEMENT entry.
            index: The stage's index.
            from_year: The stage's start year.
            charges: For the subject's escalation rate.
            active_by_year: Which stage is active in each horizon year.
            horizon: The last year index of the horizon.

        Returns:
            The re-purchase in its plan year, or None.
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
        """Turn the spliced timeline into a result, as an ordinary evaluation does.

        The KPIs come from `aggregate_timeline`, the same function `EconomicEvaluator.evaluate` calls, so every figure
        is a filter of the spliced timeline. CO2 masses (`_splice_co2`) and subsidy decisions (concatenated) cannot
        come from a timeline; physical context (areas, period, energy volumes) is taken from the stage active where it
        is read.

        Args:
            stages: The plan as given.
            per_stage: Each stage's own evaluation.
            timeline: The spliced timeline.
            perspective: The accounting frame, for the id and actor scope.
            parameters: The assumptions.
            active: Which stage is active in each horizon year (`_active_by_year`).
            ledger: The provenance ledger every stage recorded into, which every timeline id points into.

        Returns:
            The plan's `LifecycleCostResult`.
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
        """Return the plan's heat as one annual figure: the annuity of its discounted per-year heat.

        The plan's levelized cost of heat is NPV(costs) / NPV(heat), with each year's heat that of the stage active in
        it. The aggregation divides `total_npv x annuity` by this figure, `annuity x sum_y heat_active[y] x df_y` over
        years 1..T with the discount factors `CashFlowTimeline.npv` uses, so the annuity cancels. When every year's
        heat is the same, that figure is returned unchanged, to avoid rounding and match the unstaged evaluation.

        Args:
            stages: The plan as given.
            active: Which stage is active in each horizon year (`_active_by_year`).
            parameters: The assumptions: interest rate, horizon and annuity factor.

        Returns:
            The equivalent annual heat in kWh/a, or None when any active stage states no heat (the heat-cost figure is
                then omitted).
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

        Masses are not cash flows, so they are composed here. Operational emissions of year `y` are the active stage's;
        embodied emissions sum, over stages, what each stage's evaluation attributes to the subjects that stage
        installed (read off its register). Per-carrier totals are apportioned by active years, which is exact because
        emission factors are constant over the horizon.

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
