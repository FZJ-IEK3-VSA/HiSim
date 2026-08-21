"""Data-driven subsidy engine: EU scheme modeling (cost_spec.md §5).

Schemes live in ``hisim/subsidy_catalog/<COUNTRY>.json``. Eligibility is a small data-only
predicate language over a typed context; unanswered questions yield tri-state eligibility
(§5.7). The cumulation solver enumerates admissible combinations and picks the
NPV-maximizing one on the BEST_ESTIMATE slot, then values it in all three slots (§5.4).

This module answers one question — *how much support does this measure get, from which schemes,
and on what evidence* — and answers it entirely from data, so that adding a country or a funding
programme is a catalog edit rather than a code change (§10.1: if the engine needs changing for a
new country, the schema failed). It is split into two halves by the ``=== engine`` banner in the
middle of the file: above it the inert catalog payload (schemes, benefits, conditions as data,
the loader), below it everything that reads a *context* and decides anything (condition
evaluation, eligibility assessment, cumulation solving, question derivation). The cut is
deliberate and prepares the §2.5 package split into ``economics/data/`` and ``economics/engine/``.

What the module deliberately does NOT own: cash flows, timelines, discounting policy, perspective
types and VAT netting. It returns a :class:`SubsidyDecision` of abstract awards; turning those
into signed timeline entries — and deciding *when* each payout kind lands — is
``calculators/subsidy_application.py``, the only production caller of :func:`solve_cumulation`.
The perspective's subsidy mode (§5.5) reaches the solver as a plain ``admits(scheme_id)``
predicate for the same reason: this module must not depend on ``perspectives.py``. Its other
consumers are ``validation.py`` (question-coverage and catalog CI, §9.6) and
``serialization.py`` (round-tripping the context into ``economic_inputs.json``).
"""


from hisim.economics.subsidies.assessment import (
    EligibilityStatus,
    MeasureForSubsidy,
    SchemeAssessment,
    SubsidyAward,
    SubsidyContext,
    SubsidyDecision,
    describe_condition,
    evaluate_condition,
    failed_condition_descriptions,
    ineligibility_reason,
    required_questions,
    assess_schemes,
)
from hisim.economics.subsidies.catalog import (
    Benefit,
    BenefitField,
    BenefitKind,
    BenefitTypes,
    Condition,
    EligibleCostSpec,
    LoanTermsBenefit,
    LumpSumBenefit,
    OperationalBenefit,
    PayoutKind,
    PerUnitBenefit,
    Question,
    QuestionEntry,
    ReducedVatBenefit,
    ShareBenefit,
    SubsidyCatalog,
    SubsidyScheme,
    TaxCreditBenefit,
    parse_benefit,
    parse_condition,
    referenced_fields,
    scheme_context_fields,
)
from hisim.economics.subsidies.context import _enumerate_context_fields  # noqa: F401 — unit-tested directly
from hisim.economics.subsidies.context import (
    ApplicantActor,
    ApplicantProfile,
    HeritageStatus,
    SubsidyBuildingContext,
    SubsidyContextFields,
    SubsidyDataError,
    question_targets,
)
from hisim.economics.subsidies.solver import _combination_awards, _eligible_cost_basis  # noqa: F401 — unit-tested directly
from hisim.economics.subsidies.solver import (
    CapRatios,
    CumulationLimits,
    solve_cumulation,
)

__all__ = [
    "ApplicantActor",
    "ApplicantProfile",
    "Benefit",
    "BenefitField",
    "BenefitKind",
    "BenefitTypes",
    "CapRatios",
    "Condition",
    "CumulationLimits",
    "EligibilityStatus",
    "EligibleCostSpec",
    "HeritageStatus",
    "LoanTermsBenefit",
    "LumpSumBenefit",
    "MeasureForSubsidy",
    "OperationalBenefit",
    "PayoutKind",
    "PerUnitBenefit",
    "Question",
    "QuestionEntry",
    "ReducedVatBenefit",
    "SchemeAssessment",
    "ShareBenefit",
    "SubsidyAward",
    "SubsidyBuildingContext",
    "SubsidyCatalog",
    "SubsidyContext",
    "SubsidyContextFields",
    "SubsidyDataError",
    "SubsidyDecision",
    "SubsidyScheme",
    "TaxCreditBenefit",
    "assess_schemes",
    "describe_condition",
    "evaluate_condition",
    "failed_condition_descriptions",
    "ineligibility_reason",
    "parse_benefit",
    "parse_condition",
    "question_targets",
    "referenced_fields",
    "required_questions",
    "scheme_context_fields",
    "solve_cumulation",
]
