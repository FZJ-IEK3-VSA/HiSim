"""Per-scheme eligibility assessment and the questionnaire derivation (cost_spec.md §5.3-§5.5, §5.7).

`SubsidyContext` carries the answers, `evaluate_condition`/`describe_condition` judge and
explain each condition (D25: an INELIGIBLE scheme names the conditions that failed),
`assess_schemes` produces one `SchemeAssessment` per scheme, and `required_questions`
derives what a half-filled context still needs to ask. The award/cumulation math lives
in `solver`. Split out of the former single-module `subsidies.py` (PR-3 review); the
package `__init__` re-exports everything.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Tuple,
)

from hisim.economics.carriers import EnergyCarrier
from hisim.economics.facts import ComponentCostFacts
from hisim.economics.timeline import CostCategory
from hisim.economics.uncertainty import UncertainValue

from hisim.economics.subsidies.catalog import (
    Condition,
    PayoutKind,
    Question,
    SubsidyCatalog,
    SubsidyScheme,
    scheme_context_fields,
)
from hisim.economics.subsidies.context import (
    ApplicantProfile,
    SubsidyBuildingContext,
    SubsidyDataError,
    question_targets,
)


@dataclass
class SubsidyContext:
    """Full context conditions resolve against: applicant.*, building.*, measure.*.

    The complete set of answers one case supplies — the applicant profile and the building facts —
    which the caller attaches to a run through ``bridge.EconomicContext`` and which round-trips
    through ``economic_inputs.json`` so a stored result can be re-priced without re-simulating.
    The third root, ``measure.*``, is deliberately not stored here: it is the cost facts of the
    measure currently being assessed and is passed in per call, since one context is evaluated
    against many measures.

    The context is answered *partially* by design: every unanswered field makes the conditions
    that touch it undetermined rather than false (§5.7), which is what turns the questionnaire into
    progressive disclosure instead of a mandatory form.
    """

    applicant: ApplicantProfile = field(default_factory=ApplicantProfile)
    building: SubsidyBuildingContext = field(default_factory=SubsidyBuildingContext)

    def resolve_field(self, dotted: str, measure: Optional[ComponentCostFacts]) -> Tuple[bool, Any]:
        """Resolves a condition field; returns (known, value).

        Unknown fields raise, unanswered (None) values return ``(False, None)`` — the tri-state
        input (§5.7).

        The single place a dotted condition path becomes a value, and therefore the place the
        three-valued logic *originates*: a `False` in the first element means "this case has not
        told us", and it propagates through :func:`evaluate_condition` as UNDETERMINED rather than
        as a failed condition. Two failure modes are kept strictly apart — an unanswered field is
        normal and expected, whereas a path the context does not have at all is a catalog defect
        and raises. Walking stops at the first ``None`` on the path, so a building with no existing
        heating registered makes ``building.existing_heating.energy_carrier`` — the field the BEG
        speed bonus tests — unanswered instead of raising, and dictionary hops (the
        ``measure.technical_attributes.*`` case) treat a missing key the same way. Enum values are
        unwrapped to their ``.value`` so catalog conditions compare against plain JSON strings.

        Args:
            dotted: The condition's field path, rooted at applicant, building or measure.
            measure: Cost facts of the measure under assessment; ``None`` makes every
                ``measure.*`` path unanswered, which is how :func:`required_questions` resolves
                fields without a measure at hand.

        Returns:
            ``(known, value)`` — ``known`` is False exactly when the value is unanswered.

        Raises:
            SubsidyDataError: On an unknown root or an attribute the context object does not have.
        """
        parts = dotted.split(".")
        root, rest = parts[0], parts[1:]
        if root == "applicant":
            value: Any = self.applicant
        elif root == "building":
            value = self.building
        elif root == "measure":
            if measure is None:
                return False, None
            value = measure
        else:
            raise SubsidyDataError(f"Unknown condition root {root!r} in field {dotted!r}.")
        for part in rest:
            if isinstance(value, dict):
                if part not in value:
                    return False, None
                value = value[part]
                continue
            if not hasattr(value, part) and not isinstance(value, dict):
                raise SubsidyDataError(f"Unknown condition field {dotted!r} (no attribute {part!r}).")
            value = getattr(value, part)
            if value is None:
                return False, None
        if isinstance(value, enum.Enum):
            value = value.value
        return (value is not None), value


def evaluate_condition(  # pylint: disable=too-many-return-statements
    condition: Condition, context: SubsidyContext, measure: Optional[ComponentCostFacts]
) -> Tuple[Optional[bool], List[str]]:
    """Tri-state evaluation: (True/False/None, missing_fields). None = undetermined (§5.7).

    W2.3: the evaluator of the inert :class:`Condition` AST, engine side. Semantics unchanged —
    a leaf whose field is unanswered (or whose comparison raises `TypeError` on a mistyped
    answer) is undetermined and reports the field; `all` short-circuits on a definite False,
    `any` on a definite True; `not` propagates undetermined.

    This tri-state is the heart of §5.7 and the reason eligibility is not a plain boolean: treating
    "not asked yet" as "does not qualify" would quietly deny support, and treating it as "qualifies"
    would promise money that may not exist. The `all`/`any` short-circuits mean the missing-field
    list is deliberately *empty whenever the verdict is definite* — a scheme ruled out by the one
    answered condition needs no further questions, even if other branches were undetermined. The
    ``exists`` operator is the one leaf that is never undetermined: it tests answeredness itself
    and returns a definite verdict either way (its ``value`` is ignored).

    Args:
        condition: The parsed eligibility tree, or any subtree of it.
        context: The applicant/building answers available for this case.
        measure: Cost facts backing the ``measure.*`` paths; ``None`` makes them unanswered.

    Returns:
        ``(verdict, missing_fields)`` with verdict True/False/None (None = undetermined), and the
        field names whose answers would settle an undetermined verdict. The list may contain
        duplicates; callers deduplicate for reporting.
    """
    if condition.kind == "leaf":
        assert condition.fieldname is not None and condition.op is not None
        known, value = context.resolve_field(condition.fieldname, measure)
        if condition.op == "exists":
            return value is not None, []
        if not known:
            return None, [condition.fieldname]
        try:
            return bool(Condition.CONDITION_OPS[condition.op](value, condition.value)), []
        except TypeError:
            return None, [condition.fieldname]
    results = [evaluate_condition(child, context, measure) for child in condition.children]
    if condition.kind == "not":
        verdict, missing = results[0]
        return (None if verdict is None else not verdict), missing
    verdicts = [verdict for verdict, _ in results]
    missing_fields = [fieldname for _, missing in results for fieldname in missing]
    if condition.kind == "all":
        if any(verdict is False for verdict in verdicts):
            return False, []
        if any(verdict is None for verdict in verdicts):
            return None, missing_fields
        return True, []
    # any
    if any(verdict is True for verdict in verdicts):
        return True, []
    if any(verdict is None for verdict in verdicts):
        return None, missing_fields
    return False, []


def describe_condition(
    condition: Condition, context: SubsidyContext, measure: Optional[ComponentCostFacts]
) -> str:
    """Renders one condition node as the text an audit trail shows (§5.4, issue #22).

    A leaf becomes ``field op value (actual: answer)`` — the comparison the catalog asked for
    next to the answer this case gave, which is what makes a rejection checkable without opening
    the catalog file. Combinators are rendered structurally (``all of``, ``any of``, ``not``) so a
    nested condition stays readable as one line. Unanswered fields print as ``unanswered`` rather
    than ``None``, keeping the §5.7 distinction visible in prose too.

    Args:
        condition: The node to render; any subtree of a scheme's eligibility predicate.
        context: The case's answers, read only to fill in the ``actual`` part.
        measure: Cost facts backing ``measure.*`` paths, as in :func:`evaluate_condition`.

    Returns:
        A single-line description; never empty, so it can always be embedded in a reason.
    """
    if condition.kind == "leaf":
        known, value = context.resolve_field(condition.fieldname or "", measure)
        actual = repr(value) if known else "unanswered"
        if condition.op == "exists":
            return f"{condition.fieldname} exists (actual: {actual})"
        return f"{condition.fieldname} {condition.op} {condition.value!r} (actual: {actual})"
    rendered = "; ".join(describe_condition(child, context, measure) for child in condition.children)
    if condition.kind == "not":
        return f"not ({rendered})"
    return f"{condition.kind} of ({rendered})"


def failed_condition_descriptions(
    condition: Condition, context: SubsidyContext, measure: Optional[ComponentCostFacts]
) -> List[str]:
    """The leaves responsible for a definite ``False`` verdict, described (§5.4, issue #22).

    Every INELIGIBLE scheme used to carry the same fixed string, so the audit trail could say that
    a scheme was ruled out but never why — the §5.4 weakening this closes. The walk mirrors
    :func:`evaluate_condition` exactly, which is what keeps the explanation and the verdict from
    drifting: it descends only into subtrees that are themselves definitely False.

    The three combinators are described the way they actually fail. An ``all`` fails through
    *every* failing child, so all of them are named — a case may miss two criteria at once and
    fixing one would not help. An ``any`` fails only when nothing in it holds, which is one fact
    about the group rather than several about its members, so it is reported as a single
    ``none of: …`` line. A ``not`` fails because its child *does* hold, and is reported honestly
    as such instead of pretending the child failed.

    Args:
        condition: The scheme's eligibility predicate, or any subtree.
        context: The applicant/building answers the verdict was reached with.
        measure: The measure under assessment, backing ``measure.*`` paths.

    Returns:
        One description per responsible condition, in tree order; empty when this subtree is not
        definitely False (an undetermined or satisfied branch explains nothing).
    """
    verdict, _ = evaluate_condition(condition, context, measure)
    if verdict is not False:
        return []
    if condition.kind == "leaf":
        return [describe_condition(condition, context, measure)]
    if condition.kind == "not":
        return [f"must not hold, but does: {describe_condition(condition.children[0], context, measure)}"]
    if condition.kind == "any":
        alternatives = "; ".join(
            describe_condition(child, context, measure) for child in condition.children
        )
        return [f"none of: {alternatives}"]
    descriptions: List[str] = []
    for child in condition.children:
        descriptions.extend(failed_condition_descriptions(child, context, measure))
    return descriptions


def ineligibility_reason(
    condition: Condition, context: SubsidyContext, measure: Optional[ComponentCostFacts]
) -> str:
    """The `rejected_reason` of an INELIGIBLE scheme: which condition(s) it failed (issue #22).

    Wraps :func:`failed_condition_descriptions` into the one string that lands in the decision
    record, in `cost_summary.md` and in the report's subsidy section. The generic fallback is kept
    for the case that no leaf can be blamed — an empty ``all`` node, or a tree whose verdict and
    explanation disagree — so this function always yields a usable reason rather than an empty
    one.
    """
    descriptions = failed_condition_descriptions(condition, context, measure)
    if not descriptions:
        return "failed eligibility condition"
    return "condition not met: " + "; ".join(descriptions)


class EligibilityStatus(str, enum.Enum):
    """Tri-state eligibility (§5.7).

    The verdict of :func:`evaluate_condition` lifted to the scheme level. UNDETERMINED is the
    member that carries the design: it separates "this case does not qualify" from "we have not
    asked enough to tell", so the solver can award only what is certain while the decision still
    reports what the unanswered questions might unlock. Only ELIGIBLE schemes are ever awarded.
    """

    ELIGIBLE = "ELIGIBLE"
    INELIGIBLE = "INELIGIBLE"
    UNDETERMINED = "UNDETERMINED"


@dataclass
class MeasureForSubsidy:
    """What the evaluator hands the subsidy engine per subsidized measure.

    The request object of the whole engine: one funded thing (a heat pump, a wall insulation), its
    cost split into the categories a scheme may count, and the technical facts its conditions test.
    It is assembled by ``calculators/subsidy_application.py`` from the resolved device costing, and
    it is the boundary that keeps this module free of timelines — the costs arrive as year-0 gross
    bands, and nothing here knows how or when they will be booked.

    Units, since they are not visible in the field names: ``cost_by_category`` is in euro at year 0,
    gross of VAT and before any support, per uncertainty slot; ``vat_rate`` is the fraction used to
    strip VAT when a scheme's basis is NET; the two energy dictionaries are annual kWh per carrier
    (already annualized from a possibly shorter simulated period) and are read only by OPERATIONAL
    benefits.
    """

    subject: str  # the cost subject / component this measure belongs to
    facts: ComponentCostFacts
    measure_kind: str  # INSTALL | REPLACE
    # Year-0 gross cost basis by category (per slot); the eligible-cost basis (§5.2):
    cost_by_category: Dict[CostCategory, UncertainValue]
    vat_rate: float = 0.0
    # Annual bought energy per carrier for OPERATIONAL benefits and sold energy for feed-in style
    # support (filled by the evaluator):
    annual_energy_sold_in_kwh: Dict[EnergyCarrier, float] = field(default_factory=dict)
    annual_energy_bought_in_kwh: Dict[EnergyCarrier, float] = field(default_factory=dict)


@dataclass
class SchemeAssessment:
    """Eligibility verdict for one scheme applied to one measure.

    The intermediate record between :func:`assess_schemes` and the cumulation solver: it keeps the
    scheme together with *why* it got its verdict, so that the eventual audit trail can report a
    rejection or an open question rather than just an absence. Only ELIGIBLE assessments feed the
    optimization; INELIGIBLE and UNDETERMINED ones are carried into the :class:`SubsidyDecision`.
    """

    scheme: SubsidyScheme
    status: EligibilityStatus
    missing_fields: List[str] = field(default_factory=list)  # set only for UNDETERMINED
    rejected_reason: Optional[str] = None  # set only for INELIGIBLE


@dataclass
class SubsidyAward:
    """One awarded benefit, ready to be materialized as timeline entries.

    The solver's output unit: one scheme's support for one measure, already valued in all three
    uncertainty slots but not yet placed in time or signed. Which fields carry meaning depends
    entirely on ``payout_kind`` — the record is a flat union rather than a class hierarchy because
    it has to serialize into the audit trail — and the consumer that reads them is
    ``calculators/subsidy_application.py`` (grants, schedules, operational payments) together with
    ``calculators/financing_application.py`` (loan terms).

    All amounts are in **nominal euro, positive, undiscounted, gross of any mirroring**: the sign
    flip to a revenue-type cash flow happens when the entry is booked (`as_revenue`), not here.
    """

    scheme_id: str
    payout_kind: PayoutKind
    # For UPFRONT_GRANT: amount at year 0. For TAX_CREDIT_SCHEDULE: per-year amounts (years 1..N).
    upfront_amount: UncertainValue = field(default_factory=lambda: UncertainValue.exact(0.0))
    schedule_amounts: List[UncertainValue] = field(default_factory=list)
    # For OPERATIONAL: rate, carrier and duration; amounts are energy-dependent.
    operational_rate_per_kwh: float = 0.0
    operational_carrier: Optional[EnergyCarrier] = None
    operational_duration_years: int = 0
    # For LOAN_TERMS: FinancingPlan overrides. All three are `None` when the award does not state
    # them, which `calculators/financing_application.py` reads as "inherit the plan's value" — a
    # stated 0.0 is an override to zero, not an absent field.
    loan_interest_rate: Optional[float] = None
    loan_term_in_years: Optional[int] = None
    loan_repayment_grant_share: Optional[float] = None
    # For VAT_REDUCTION:
    reduced_vat_rate: Optional[float] = None
    caps_binding_per_slot: Dict[str, bool] = field(default_factory=dict)


@dataclass
class SubsidyDecision:
    """Fully reported outcome of the cumulation solver (§5.4) — the audit trail.

    Everything the solver did for one measure, not only what it awarded: which schemes applied,
    which were rejected and why, which stayed undetermined and on which unanswered fields, how much
    those could still unlock, and whether a *different* combination would have won in the LOW or
    HIGH world. §5.4 calls this audit trail a research deliverable in its own right and
    non-negotiable for trust in the results — a subsidy figure that cannot be traced back to named
    schemes is not reviewable.

    Produced by :func:`solve_cumulation`, carried on the subsidy application result, and surfaced
    to users in three places: the subsidy decision cards of the HTML report, the ``cost_audit.csv``
    row of the measure, and the exported JSON via :meth:`to_json`.
    """

    measure_subject: str
    applied: List[SubsidyAward] = field(default_factory=list)
    rejected: List[Dict[str, Any]] = field(default_factory=list)  # scheme id, reason
    undetermined: List[Dict[str, Any]] = field(default_factory=list)  # scheme id, missing fields
    # Optimistic upper bound over undetermined schemes ("answering these questions could
    # unlock up to X", §5.7):
    undetermined_upper_bound_in_euro: float = 0.0
    # Whether a different combination would have been optimal in LOW or HIGH (§3.9):
    other_slot_optimal_combination: Dict[str, Optional[str]] = field(default_factory=dict)

    def to_json(self) -> dict:
        """Serializes the audit trail.

        The exported form of the decision, embedded under ``subsidy_decisions`` in the result JSON
        (``results.py``) and from there in ``lifecycle_costs.json``. Every award field is written
        out regardless of payout kind — including the ones that are meaningless for that kind — so
        the export schema is stable and a reader can diff two runs field by field; amounts keep
        their min/best_estimate/max band.
        """
        return {
            "measure_subject": self.measure_subject,
            "applied": [
                {
                    "scheme_id": award.scheme_id,
                    "payout_kind": award.payout_kind.value,
                    "upfront_amount": award.upfront_amount.to_json(),
                    "schedule_amounts": [amount.to_json() for amount in award.schedule_amounts],
                    "operational_rate_per_kwh": award.operational_rate_per_kwh,
                    "operational_carrier": award.operational_carrier.value if award.operational_carrier else None,
                    "operational_duration_years": award.operational_duration_years,
                    "loan_interest_rate": award.loan_interest_rate,
                    "loan_term_in_years": award.loan_term_in_years,
                    "loan_repayment_grant_share": award.loan_repayment_grant_share,
                    "reduced_vat_rate": award.reduced_vat_rate,
                    "caps_binding_per_slot": award.caps_binding_per_slot,
                }
                for award in self.applied
            ],
            "rejected": self.rejected,
            "undetermined": self.undetermined,
            "undetermined_upper_bound_in_euro": self.undetermined_upper_bound_in_euro,
            "other_slot_optimal_combination": self.other_slot_optimal_combination,
        }


def assess_schemes(
    catalog: SubsidyCatalog,
    measure: MeasureForSubsidy,
    context: SubsidyContext,
    year: int,
    admits: Optional[Callable[[str], bool]] = None,
) -> List[SchemeAssessment]:
    """Tri-state eligibility for all candidate schemes of one measure.

    `admits` is the perspective's subsidy-mode filter (§5.5, §7 B5): a scheme it rejects is not
    assessed at all, so it can neither enter the cumulation solver nor the undetermined bound.

    The step between the jurisdictional pre-filter and the cumulation solver: every candidate is
    evaluated against the case's answers and classified ELIGIBLE / INELIGIBLE / UNDETERMINED, with
    the missing fields recorded for the last group. It returns *all three* classes rather than only
    the winners, because the rejected and undetermined ones are what the audit trail (§5.4) and the
    questionnaire (§5.7) are built from.

    Args:
        catalog: The country catalog to draw candidates from.
        measure: The measure being assessed; its asset class and kind drive the pre-filter and its
            facts back the ``measure.*`` condition paths.
        context: The applicant/building answers.
        year: The year scheme validity is tested against (the price basis year in production).
        admits: Optional predicate on scheme ids; ``None`` admits everything.

    Returns:
        One assessment per admitted candidate, in catalog order. A rejection names the condition
        leaf (or leaves) responsible for it — see :func:`ineligibility_reason` — so the §5.4 audit
        trail says *why* a scheme was ruled out and not merely that it was.
    """
    assessments = []
    for scheme in catalog.candidate_schemes(
        measure.facts.asset_class, measure.measure_kind, context.applicant.region, year
    ):
        if admits is not None and not admits(scheme.id):
            continue
        verdict, missing = evaluate_condition(scheme.eligibility, context, measure.facts)
        if verdict is True:
            assessments.append(SchemeAssessment(scheme=scheme, status=EligibilityStatus.ELIGIBLE))
        elif verdict is False:
            assessments.append(
                SchemeAssessment(
                    scheme=scheme,
                    status=EligibilityStatus.INELIGIBLE,
                    rejected_reason=ineligibility_reason(scheme.eligibility, context, measure.facts),
                )
            )
        else:
            assessments.append(
                SchemeAssessment(
                    scheme=scheme, status=EligibilityStatus.UNDETERMINED, missing_fields=sorted(set(missing))
                )
            )
    return assessments


def required_questions(
    catalog: SubsidyCatalog,
    planned_measures: List[MeasureForSubsidy],
    context: SubsidyContext,
    year: int,
    admits: Optional[Callable[[str], bool]] = None,
) -> List[Question]:
    """Computes the minimal question set for the candidate schemes (§5.7).

    Collects every context field referenced by the eligibility conditions of candidate
    schemes, drops already-answered/derivable ones, and orders by pruning power. `admits` is
    the same subsidy-mode filter the solver takes (§7 B5): a question is only worth asking for
    a scheme the perspective would actually award.

    This is the "ask exactly the questions that matter for *your* case" half of §5.7, and it is
    computed rather than curated: because conditions are data over a statically enumerable
    vocabulary, the question set is derived from the catalog and can never go stale relative to it.
    Fields are collected via :func:`scheme_context_fields`, so implied dependencies count too — a
    scheme that prorates by residential share or caps per dwelling unit asks for those even though
    no condition names them. ``measure.*`` fields are never asked: they come from the simulation and
    the cost facts. Derived fields are replaced by the user-answerable ones behind them
    (:func:`question_targets`), and a field with no catalog entry is skipped here and reported by
    the question-coverage CI instead (§9.6).

    Ordering is by *pruning power*: each candidate scheme's simplified, uncapped, undiscounted
    support estimate (:meth:`Benefit.value_estimate`) is attributed to every field it depends on, so
    the questions that gate the most money come first and a user who abandons the form early has
    still answered the ones that matter. The estimate is deliberately cruder than the solver's
    valuation — it only has to rank.

    Who renders the result: nothing inside HiSim. The list is designed for a frontend questionnaire,
    which §5.7 plans to reach through an additive RenoVisor endpoint (§10.1 Phase 4) so the UI needs
    no scheme knowledge of its own; today the function's in-tree consumers are the tests and, for
    coverage checking, ``validation.py``.

    Args:
        catalog: The country catalog whose schemes and question entries are used.
        planned_measures: The measures the case intends to carry out — they select the candidate
            schemes and supply the cost/size the pruning estimate is scaled on.
        context: The answers already given; anything resolvable is not asked again.
        year: The year scheme validity is tested against.
        admits: Optional scheme-id predicate implementing the perspective's subsidy mode.

    Returns:
        The questions to ask, highest pruning power first, each carrying the deduplicated, sorted
        ids of the schemes that made it necessary.
    """
    field_to_schemes: Dict[str, List[str]] = {}
    scheme_support: Dict[str, float] = {}
    for measure in planned_measures:
        for scheme in catalog.candidate_schemes(
            measure.facts.asset_class, measure.measure_kind, context.applicant.region, year
        ):
            if admits is not None and not admits(scheme.id):
                continue
            gross = UncertainValue.sum(measure.cost_by_category.values()).best_estimate
            support = scheme.benefit.value_estimate(gross, measure.facts.size)
            scheme_support[scheme.id] = max(scheme_support.get(scheme.id, 0.0), support)
            for fieldname in scheme_context_fields(scheme):
                field_to_schemes.setdefault(fieldname, []).append(scheme.id)
    questions: List[Question] = []
    for fieldname, scheme_ids in field_to_schemes.items():
        if fieldname.startswith("measure."):
            continue  # known from the simulation / cost facts, never asked
        known, _value = context.resolve_field(fieldname, None)
        if known:
            continue
        # Derived fields are asked through the friendly questions behind them (§5.7):
        for target in question_targets(fieldname):
            entry = catalog.questions.get(target)
            if entry is None:
                continue  # question-coverage CI flags this (§9.6)
            existing = next((question for question in questions if question.entry.fieldname == target), None)
            if existing is None:
                existing = Question(entry=entry)
                questions.append(existing)
            existing.asked_because.extend(scheme_ids)
            existing.pruning_power_in_euro += sum(scheme_support.get(scheme_id, 0.0) for scheme_id in scheme_ids)
    for question in questions:
        question.asked_because = sorted(set(question.asked_because))
    questions.sort(key=lambda question: -question.pruning_power_in_euro)
    return questions
