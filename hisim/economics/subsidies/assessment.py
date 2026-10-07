"""Per-scheme eligibility assessment and questionnaire derivation (cost_spec.md §5.3-§5.5, §5.7).

`SubsidyContext` carries the answers; `evaluate_condition` and `describe_condition` judge and explain each condition;
`assess_schemes` gives one `SchemeAssessment` per scheme; `required_questions` derives what a partly filled context
still needs to ask. The award arithmetic lives in `solver`.
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
    SubsidyContextFields,
    SubsidyDataError,
    SubsidyPackageContext,
    question_targets,
)


@dataclass
class SubsidyContext:
    """The answers conditions resolve against: the ``applicant.*``, ``building.*`` and ``package.*`` roots.

    The caller attaches it to a run through ``bridge.EconomicContext``, and its answers round-trip through
    ``economic_inputs.json`` so a stored result can be re-priced without re-simulating. The ``measure.*`` root is not
    stored here: it is the cost facts of the measure being assessed, passed per call. Any field may stay unanswered;
    conditions touching it are then UNDETERMINED rather than false (§5.7).
    """

    applicant: ApplicantProfile = field(default_factory=ApplicantProfile)
    building: SubsidyBuildingContext = field(default_factory=SubsidyBuildingContext)
    #: What the evaluation installs beside the measure under assessment. Filled by the evaluator
    #: per evaluation and never stored: ``economic_inputs.json`` carries the answers, not this.
    package: SubsidyPackageContext = field(default_factory=SubsidyPackageContext)

    def resolve_field(self, dotted: str, measure: Optional[ComponentCostFacts]) -> Tuple[bool, Any]:
        """Resolve a dotted condition field to ``(known, value)``.

        This is where the three-valued logic starts: ``known`` False means the case has not answered, which
        :func:`evaluate_condition` turns into UNDETERMINED. Walking stops at the first ``None`` on the path, so
        ``building.existing_heating.energy_carrier`` with no existing heating is unanswered, not an error; a missing
        key under ``measure.technical_attributes.*`` is treated the same way. Enum values are unwrapped to ``.value``
        so conditions compare against plain JSON strings.

        Args:
            dotted: The field path, rooted at ``applicant``, ``building``, ``package`` or ``measure``.
            measure: Cost facts of the measure under assessment; ``None`` makes every ``measure.*`` path unanswered.

        Returns:
            ``(known, value)``; ``known`` is False exactly when the value is unanswered.

        Raises:
            SubsidyDataError: On an unknown root or an attribute the context object does not have (a catalog defect).
        """
        parts = dotted.split(".")
        root, rest = parts[0], parts[1:]
        if root == "applicant":
            value: Any = self.applicant
        elif root == "building":
            value = self.building
        elif root == "package":
            value = self.package
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
    """Evaluate a condition tree to True, False or None (undetermined) plus the fields that would settle it (§5.7).

    A leaf whose field is unanswered, or whose comparison raises `TypeError` on a mistyped answer, is undetermined and
    reports its field. ``all`` short-circuits on a definite False, ``any`` on a definite True, and ``not`` keeps
    undetermined. The ``exists`` operator tests answeredness itself and is never undetermined. Treating "not asked yet"
    as false would quietly deny support, and treating it as true would promise money that may not exist.

    Args:
        condition: The parsed eligibility tree, or any subtree.
        context: The applicant and building answers.
        measure: Cost facts backing the ``measure.*`` paths; ``None`` makes them unanswered.

    Returns:
        ``(verdict, missing_fields)``. The field list is empty whenever the verdict is definite and may contain
            duplicates otherwise.
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
    """Render one condition node as the single line an audit trail shows (§5.4).

    A leaf becomes ``field op value (actual: answer)``, so a rejection can be checked without opening the catalog;
    unanswered fields print as ``unanswered``. Combinators render as ``all of``, ``any of`` and ``not``.

    Args:
        condition: Any node of a scheme's eligibility tree.
        context: The case's answers, read only to fill in the ``actual`` part.
        measure: Cost facts backing ``measure.*`` paths.

    Returns:
        A non-empty, single-line description.
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
    """Describe the condition leaves responsible for a definite ``False`` verdict (§5.4).

    The walk mirrors :func:`evaluate_condition` and descends only into subtrees that are definitely False, so the
    explanation matches the verdict. A failing ``all`` names every failing child, since fixing one may not be enough. A
    failing ``any`` is reported as one ``none of: ...`` line. A failing ``not`` is reported as its child holding.

    Args:
        condition: The scheme's eligibility tree, or any subtree.
        context: The applicant and building answers.
        measure: The measure under assessment, backing ``measure.*`` paths.

    Returns:
        One description per responsible condition, in tree order; empty when the subtree is not definitely False.
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
    """Return the `rejected_reason` of an INELIGIBLE scheme, naming the condition(s) it failed.

    The string lands in the decision record, in `cost_summary.md` and in the report's subsidy section. When no leaf can
    be blamed (an empty ``all`` node, for example) a generic reason is returned, so the result is never empty.
    """
    descriptions = failed_condition_descriptions(condition, context, measure)
    if not descriptions:
        return "failed eligibility condition"
    return "condition not met: " + "; ".join(descriptions)


class EligibilityStatus(str, enum.Enum):
    """Tri-state eligibility of a scheme (§5.7).

    UNDETERMINED separates "does not qualify" from "not asked enough to tell": the solver awards only ELIGIBLE schemes,
    while the decision still reports what unanswered questions might unlock.
    """

    ELIGIBLE = "ELIGIBLE"
    INELIGIBLE = "INELIGIBLE"
    UNDETERMINED = "UNDETERMINED"


@dataclass
class MeasureForSubsidy:
    """One subsidized measure as the subsidy engine receives it from the evaluator.

    A measure is one funded thing (a heat pump, a wall insulation): its year-0 cost split into categories a scheme may
    count, and the technical facts its conditions test. ``calculators/subsidy_application.py`` builds it from the
    resolved device costing; nothing here knows how or when costs are booked.

    Units: ``cost_by_category`` is euro at year 0, gross of VAT and before support, per band slot (minimum, best
    estimate, maximum); ``vat_rate`` is the fraction that strips VAT when a scheme's basis is NET; the two energy
    dictionaries are annual kWh per carrier and are read only by operational benefits.
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

    Keeps the scheme together with why it got its verdict. Only ELIGIBLE assessments feed the cumulation solver;
    INELIGIBLE and UNDETERMINED ones go into the :class:`SubsidyDecision` audit trail.
    """

    scheme: SubsidyScheme
    status: EligibilityStatus
    missing_fields: List[str] = field(default_factory=list)  # set only for UNDETERMINED
    rejected_reason: Optional[str] = None  # set only for INELIGIBLE


@dataclass
class SubsidyAward:
    """One scheme's support for one measure, valued in all three band slots but not yet placed on the timeline.

    Which fields matter depends on ``payout_kind``; the record is a flat union so it serializes into the audit trail.
    ``calculators/subsidy_application.py`` and ``calculators/financing_application.py`` (loan terms) turn it into
    timeline entries. All amounts are nominal euro, positive and undiscounted; the sign flip to revenue happens when
    the entry is booked. ``display_name`` travels with the award because reports are often built from a serialized
    result in a process that never loaded a catalog; it is empty when the scheme had none.
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
    #: The scheme's friendly name at the time of the award; empty when it had none.
    display_name: str = ""
    #: The rate this award was computed at, as a fraction, for the two percentage forms (a share
    #: of eligible cost and a tax credit); None for lump sums, per-unit amounts, loan terms and
    #: VAT reductions, which state their own terms instead. It is the rate **after** a cumulation
    #: group's combined-rate cap scaled it down, i.e. the rate that actually produced the euros.
    benefit_rate: Optional[float] = None
    #: The same rate before that scaling, when the group's combined-rate cap bit; None otherwise.
    #: The pair is what lets the report say "20 % capped to 17.5 %" instead of showing a rate the
    #: catalog does not contain.
    benefit_rate_before_group_cap: Optional[float] = None
    #: The same rate before the EU state-aid overall cap rescaled the award, when that cap bit;
    #: None otherwise. That cap scales the *amounts* (`solver._scaled_to_cap`), not the rates, so
    #: without this the caption would print a rate times a basis whose product is not the amount
    #: beside it. What is recorded is the **best-estimate slot's** arithmetic: `benefit_rate` is
    #: set to whatever makes ``eligible_basis_in_euro.scale(benefit_rate)`` equal the capped
    #: ``upfront_amount`` in that slot, and this field keeps the rate it had before. The cap ratio
    #: is a per-slot figure (`solver._overall_cap_ratios`), so where the three ratios differ the
    #: LOW and HIGH slots of the product do not reproduce their own amounts — the best-estimate
    #: slot is the one the caption prints, and the one this pair describes.
    benefit_rate_before_overall_cap: Optional[float] = None
    #: The eligible-cost basis the rate was applied to, after proration and after the
    #: per-dwelling-unit cap — the second factor of `rate x basis = amount`.
    eligible_basis_in_euro: Optional[UncertainValue] = None
    #: The per-dwelling-unit eligible-cost ceiling that applied, in euro, or None where the scheme
    #: declares none. With `caps_binding_per_slot` this is what turns "capped" into "capped at X".
    eligible_basis_cap_in_euro: Optional[float] = None

    @property
    def label(self) -> str:
        """Return the award's name for a reader: the scheme's display name, or its id when it had none."""
        return self.display_name or self.scheme_id


class SubsidySchemeLabels:
    """Labels for support sources on a timeline that no catalog scheme covers.

    ``LEGACY_FLAT_ID`` marks the flat support share once carried in the device catalog for countries without a subsidy
    catalog. New evaluations do not write it, but archived results may carry it, and the views name its node when such
    a result is re-read. ``UNATTRIBUTED`` labels a support entry that names no scheme.
    """

    LEGACY_FLAT_ID = "LEGACY_FLAT"
    LEGACY_FLAT = "flat legacy support share (no catalog)"
    UNATTRIBUTED = "subsidy (unattributed)"


@dataclass(frozen=True)
class SchemeMaximum:
    """The most one scheme can pay for one measure, whatever its eligibility verdict.

    Gives the reader the "up to EUR X" to weigh an open question against: the scheme valued alone for the measure, by
    the same arithmetic as an award (:func:`~hisim.economics.subsidies.solver.scheme_maximum`).

    Attributes:
        amount_in_euro: Positive nominal euro in the measure's year 0, per slot; None where the catalog states no
            limiting amount.
        note: Why there is no amount; None when there is one.
    """

    amount_in_euro: Optional[UncertainValue]
    note: Optional[str] = None


@dataclass
class SubsidyDecision:
    """The full outcome of the cumulation solver for one measure, as an audit trail (§5.4).

    Cumulation is combining several schemes for one measure under their stacking rules. The decision records which
    schemes applied, which were rejected and why, which stayed undetermined on which fields, how much those could still
    unlock, and whether a different combination would have won in the LOW or HIGH band slot. It is shown in the
    report's subsidy cards, in ``cost_audit.csv`` and in the exported JSON. ``discounted_support_in_euro`` is the
    solver's objective value for the chosen combination: the present value of the applied awards on the best-estimate
    slot, as ``solver._support_value`` computes it.
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
    # The solver's objective value for `applied`, on the BEST_ESTIMATE slot (see class doc):
    discounted_support_in_euro: float = 0.0
    #: Scheme id -> the most that scheme can pay for this measure on its own, for every scheme
    #: assessed (applied, rejected and undetermined). Read by the staged economics document;
    #: not written by `to_json`.
    maximum_by_scheme: Dict[str, SchemeMaximum] = field(default_factory=dict)

    def to_json(self) -> dict:
        """Serialize the audit trail for the result JSON.

        Written under ``subsidy_decisions`` in the result JSON and ``lifecycle_costs.json``. Every award field is
        written regardless of payout kind, so the schema is stable and two runs diff field by field.
        ``maximum_by_scheme`` is not written.
        """
        return {
            "measure_subject": self.measure_subject,
            "applied": [
                {
                    "scheme_id": award.scheme_id,
                    "display_name": award.display_name,
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
                    # The arithmetic behind the amount, so a report rendered from a
                    # stored result can show `rate x basis = amount` and the cap verdict.
                    "benefit_rate": award.benefit_rate,
                    "benefit_rate_before_group_cap": award.benefit_rate_before_group_cap,
                    "benefit_rate_before_overall_cap": award.benefit_rate_before_overall_cap,
                    "eligible_basis_in_euro": (
                        award.eligible_basis_in_euro.to_json()
                        if award.eligible_basis_in_euro is not None
                        else None
                    ),
                    "eligible_basis_cap_in_euro": award.eligible_basis_cap_in_euro,
                }
                for award in self.applied
            ],
            "rejected": self.rejected,
            "undetermined": self.undetermined,
            "undetermined_upper_bound_in_euro": self.undetermined_upper_bound_in_euro,
            "other_slot_optimal_combination": self.other_slot_optimal_combination,
            "discounted_support_in_euro": self.discounted_support_in_euro,
        }


def assess_schemes(
    catalog: SubsidyCatalog,
    measure: MeasureForSubsidy,
    context: SubsidyContext,
    year: int,
    admits: Optional[Callable[[str], bool]] = None,
) -> List[SchemeAssessment]:
    """Assess every candidate scheme of one measure as ELIGIBLE, INELIGIBLE or UNDETERMINED.

    All three classes are returned because the rejected and undetermined ones feed the audit trail (§5.4) and the
    questionnaire (§5.7). A scheme that `admits` rejects is not assessed at all, so it enters neither the solver nor
    the undetermined bound.

    Args:
        catalog: The country catalog to draw candidates from.
        measure: The measure; its asset class and kind select candidates, and its facts back the ``measure.*`` paths.
        context: The applicant and building answers.
        year: The year scheme validity is tested against (the price basis year in production).
        admits: The perspective's subsidy-mode filter on scheme ids (§5.5); ``None`` admits everything.

    Returns:
        One assessment per admitted candidate, in catalog order; a rejection names the responsible condition(s)
            (:func:`ineligibility_reason`).
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
    """Compute the minimal question set for the candidate schemes, most valuable first (§5.7).

    Collects every context field the candidate schemes depend on (:func:`scheme_context_fields`, so implied fields such
    as residential share or dwelling units count), drops answered ones and ``measure.*`` fields, and replaces derived
    fields by the questions behind them (:func:`question_targets`). A field with no catalog question is skipped here;
    the coverage check in ``validation.py`` reports it (§9.6). Ordering is by pruning power: each scheme's rough
    uncapped support estimate (:meth:`Benefit.value_estimate`) is credited to every field it depends on, so the
    questions that gate the most money come first. The list is meant for a frontend questionnaire.

    Args:
        catalog: The country catalog whose schemes and question entries are used.
        planned_measures: The measures the case intends to carry out; they select candidates and scale the estimate.
        context: The answers already given; anything resolvable is not asked again.
        year: The year scheme validity is tested against.
        admits: The perspective's subsidy-mode filter on scheme ids.

    Returns:
        The questions, highest pruning power first, each with the sorted ids of the schemes that need it.
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
        if SubsidyContextFields.is_computed(fieldname):
            continue  # known from the simulation, the cost facts or the package, never asked
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
