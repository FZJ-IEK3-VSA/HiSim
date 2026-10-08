"""Data-driven subsidy engine (cost_spec.md §5).

Schemes live in ``hisim/subsidy_catalog/<COUNTRY>.json``. Eligibility conditions are a small data-only predicate
language over a typed applicant and building context; an unanswered question makes a scheme UNDETERMINED rather than
ineligible (§5.7). The cumulation solver picks the admissible scheme combination with the highest best-estimate value
and values it in all three band slots (§5.4). The package returns abstract awards (`SubsidyDecision`);
`calculators/subsidy_application.py` turns them into timeline entries.

``context`` holds the applicant and building facts, ``catalog`` the scheme data and its loader, ``assessment`` the
condition evaluation and question derivation, ``solver`` the cumulation solver. This module re-exports them all.
"""


from hisim.economics.subsidies.assessment import (
    EligibilityStatus,
    MeasureForSubsidy,
    SchemeAssessment,
    SchemeMaximum,
    SubsidyAward,
    SubsidyContext,
    SubsidyDecision,
    SubsidySchemeLabels,
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
    Tier,
    TieredPerUnitBenefit,
    parse_benefit,
    parse_condition,
    referenced_fields,
    scheme_context_fields,
)
from hisim.economics.subsidies.context import _enumerate_context_fields  # noqa: F401 — unit-tested directly
from hisim.economics.subsidies.context import (
    ApplicantActor,
    ApplicantProfile,
    DwellingType,
    HeritageStatus,
    SubsidyBuildingContext,
    SubsidyContextFields,
    SubsidyDataError,
    SubsidyPackageContext,
    question_targets,
)
from hisim.economics.subsidies.solver import _combination_awards, _eligible_cost_basis  # noqa: F401 — used by tests
from hisim.economics.subsidies.solver import (
    CapRatios,
    CumulationLimits,
    SchemeMaximumNotes,
    UnpricedMeasures,
    scheme_maximum,
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
    "DwellingType",
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
    "SchemeMaximum",
    "SchemeMaximumNotes",
    "ShareBenefit",
    "SubsidyAward",
    "SubsidyBuildingContext",
    "SubsidyCatalog",
    "SubsidyContext",
    "SubsidyContextFields",
    "SubsidyDataError",
    "SubsidyDecision",
    "SubsidyPackageContext",
    "SubsidyScheme",
    "SubsidySchemeLabels",
    "TaxCreditBenefit",
    "Tier",
    "TieredPerUnitBenefit",
    "UnpricedMeasures",
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
    "scheme_maximum",
    "solve_cumulation",
]
