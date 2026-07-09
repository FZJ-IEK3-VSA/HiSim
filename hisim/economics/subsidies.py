"""Data-driven subsidy engine: EU scheme modeling (cost_spec.md §5).

Schemes live in ``hisim/subsidy_catalog/<COUNTRY>.json``. Eligibility is a small data-only
predicate language over a typed context; unanswered questions yield tri-state eligibility
(§5.7). The cumulation solver enumerates admissible combinations and picks the
NPV-maximizing one on the AVERAGE slot, then values it in all three slots (§5.4).
"""

from __future__ import annotations

import enum
import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from hisim.economics.carriers import EnergyCarrier
from hisim.economics.database import SourceRegistry
from hisim.economics.facts import ComponentCostFacts, ExistingAsset
from hisim.economics.timeline import Actor, CostCategory
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import ComponentType

#: Default on-disk location of the shipped subsidy catalogs.
DEFAULT_SUBSIDY_CATALOG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "subsidy_catalog"
)


class SubsidyDataError(ValueError):
    """Raised for malformed subsidy catalogs."""


class HeritageStatus(str, enum.Enum):
    """Heritage-protection status (§5.3)."""

    NONE = "NONE"
    LISTED_MONUMENT = "LISTED_MONUMENT"  # Einzeldenkmal
    ENSEMBLE_PROTECTED = "ENSEMBLE_PROTECTED"  # Ensembleschutz
    PRESERVATION_WORTHY = "PRESERVATION_WORTHY"  # besonders erhaltenswerte Bausubstanz


class ApplicantActor(str, enum.Enum):
    """Applicant roles for eligibility conditions."""

    OWNER_OCCUPIER = "OWNER_OCCUPIER"
    LANDLORD = "LANDLORD"
    WEG = "WEG"
    TENANT = "TENANT"

    @classmethod
    def from_actor(cls, actor: Actor) -> "ApplicantActor":
        """Maps a timeline actor to an applicant role."""
        return {
            Actor.OWNER_OCCUPIER: cls.OWNER_OCCUPIER,
            Actor.LANDLORD: cls.LANDLORD,
            Actor.TENANT: cls.TENANT,
            Actor.SYSTEM: cls.OWNER_OCCUPIER,
        }[actor]


@dataclass
class ApplicantProfile:
    """Who applies for the subsidy (§5.3)."""

    actor: ApplicantActor = ApplicantActor.OWNER_OCCUPIER
    taxable_household_income_in_euro: Optional[float] = None
    household_size: Optional[int] = None
    main_residence: Optional[bool] = True
    region: Optional[str] = None  # NUTS-3 or municipality key for regional schemes


@dataclass
class SubsidyBuildingContext:
    """Building facts consumed by eligibility conditions (§5.3)."""

    construction_year: Optional[int] = None
    dwelling_units: int = 1
    heated_floor_area_in_m2: Optional[float] = None
    residential_floor_area_in_m2: Optional[float] = None
    commercial_floor_area_in_m2: float = 0.0
    heritage_status: Optional[HeritageStatus] = HeritageStatus.NONE
    energy_performance_class: Optional[str] = None
    existing_heating: Optional[ExistingAsset] = None
    # Whether an individueller Sanierungsfahrplan exists (iSFP bonus for envelope measures):
    has_isfp: Optional[bool] = None

    @property
    def residential_share(self) -> Optional[float]:
        """Derived, never asked separately (§5.7)."""
        if self.residential_floor_area_in_m2 is None:
            return None
        total = self.residential_floor_area_in_m2 + self.commercial_floor_area_in_m2
        if total <= 0:
            return None
        return self.residential_floor_area_in_m2 / total


@dataclass
class SubsidyContext:
    """Full context conditions resolve against: applicant.*, building.*, measure.*."""

    applicant: ApplicantProfile = field(default_factory=ApplicantProfile)
    building: SubsidyBuildingContext = field(default_factory=SubsidyBuildingContext)

    def resolve_field(self, dotted: str, measure: Optional[ComponentCostFacts]) -> Tuple[bool, Any]:
        """Resolves a condition field; returns (known, value). Unknown fields raise, unanswered
        (None) values return (False, None) — the tri-state input (§5.7)."""
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


#: Statically enumerable user-answerable context fields (§5.7). Derived fields map to the
#: friendly questions that determine them.
KNOWN_CONTEXT_FIELDS = {
    "applicant.actor",
    "applicant.taxable_household_income_in_euro",
    "applicant.household_size",
    "applicant.main_residence",
    "applicant.region",
    "building.construction_year",
    "building.dwelling_units",
    "building.heated_floor_area_in_m2",
    "building.residential_floor_area_in_m2",
    "building.commercial_floor_area_in_m2",
    "building.residential_share",
    "building.heritage_status",
    "building.energy_performance_class",
    "building.has_isfp",
    "building.existing_heating",
    "building.existing_heating.is_functional",
    "building.existing_heating.energy_carrier",
    "building.existing_heating.installation_year",
}

CONDITION_OPS: Dict[str, Callable[[Any, Any], bool]] = {
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "in": lambda a, b: a in b,
    "contains": lambda a, b: b in a,
    "exists": lambda a, b: a is not None,
}


@dataclass
class Condition:
    """One node of the eligibility predicate tree (§5.3). No Python eval."""

    kind: str  # "all" | "any" | "not" | "leaf"
    children: List["Condition"] = field(default_factory=list)
    fieldname: Optional[str] = None
    op: Optional[str] = None
    value: Any = None

    @classmethod
    def parse(cls, raw: dict, scheme_id: str) -> "Condition":
        """Parses and validates a condition node with clear errors."""
        if "all" in raw:
            return cls(kind="all", children=[cls.parse(child, scheme_id) for child in raw["all"]])
        if "any" in raw:
            return cls(kind="any", children=[cls.parse(child, scheme_id) for child in raw["any"]])
        if "not" in raw:
            return cls(kind="not", children=[cls.parse(raw["not"], scheme_id)])
        if "field" in raw:
            fieldname = raw["field"]
            operator = raw.get("op")
            if operator not in CONDITION_OPS:
                raise SubsidyDataError(f"Scheme {scheme_id}: unknown op {operator!r} in condition on {fieldname!r}.")
            if not (fieldname in KNOWN_CONTEXT_FIELDS or fieldname.startswith("measure.")):
                raise SubsidyDataError(f"Scheme {scheme_id} references unknown field {fieldname!r}.")
            return cls(kind="leaf", fieldname=fieldname, op=operator, value=raw.get("value"))
        raise SubsidyDataError(f"Scheme {scheme_id}: condition node {raw!r} is neither all/any/not nor a leaf.")

    def evaluate(
        self, context: SubsidyContext, measure: Optional[ComponentCostFacts]
    ) -> Tuple[Optional[bool], List[str]]:
        """Tri-state evaluation: (True/False/None, missing_fields). None = undetermined (§5.7)."""
        if self.kind == "leaf":
            assert self.fieldname is not None and self.op is not None
            known, value = context.resolve_field(self.fieldname, measure)
            if self.op == "exists":
                return value is not None, []
            if not known:
                return None, [self.fieldname]
            try:
                return bool(CONDITION_OPS[self.op](value, self.value)), []
            except TypeError:
                return None, [self.fieldname]
        results = [child.evaluate(context, measure) for child in self.children]
        if self.kind == "not":
            verdict, missing = results[0]
            return (None if verdict is None else not verdict), missing
        verdicts = [verdict for verdict, _ in results]
        missing_fields = [fieldname for _, missing in results for fieldname in missing]
        if self.kind == "all":
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

    def referenced_fields(self) -> List[str]:
        """All context fields referenced anywhere in the tree (question derivation, §5.7)."""
        if self.kind == "leaf":
            return [self.fieldname] if self.fieldname else []
        fields: List[str] = []
        for child in self.children:
            fields.extend(child.referenced_fields())
        return fields


class BenefitKind(str, enum.Enum):
    """Tagged union of benefit kinds (§5.2)."""

    SHARE_OF_ELIGIBLE_COST = "SHARE_OF_ELIGIBLE_COST"
    BONUS_SHARE = "BONUS_SHARE"
    LUMP_SUM = "LUMP_SUM"
    PER_UNIT = "PER_UNIT"
    TAX_CREDIT = "TAX_CREDIT"
    REDUCED_VAT = "REDUCED_VAT"
    SOFT_LOAN = "SOFT_LOAN"
    OPERATIONAL = "OPERATIONAL"


class PayoutKind(str, enum.Enum):
    """How benefits map to timeline entries (§5.2)."""

    UPFRONT_GRANT = "UPFRONT_GRANT"
    TAX_CREDIT_SCHEDULE = "TAX_CREDIT_SCHEDULE"
    LOAN_TERMS = "LOAN_TERMS"
    OPERATIONAL = "OPERATIONAL"
    VAT_REDUCTION = "VAT_REDUCTION"


@dataclass
class EligibleCostSpec:
    """Which cost categories count, capped and prorated how (§5.2)."""

    categories: List[CostCategory] = field(
        default_factory=lambda: [CostCategory.INVESTMENT, CostCategory.PLANNING, CostCategory.REMOVAL]
    )
    cap_per_dwelling_unit_in_euro: List[float] = field(default_factory=list)
    basis: str = "GROSS"  # GROSS | NET (of VAT)
    proration: str = "NONE"  # NONE | RESIDENTIAL_SHARE

    def cap_for_units(self, dwelling_units: int) -> Optional[float]:
        """Total eligible-cost cap; tier list, last value repeated for further units."""
        if not self.cap_per_dwelling_unit_in_euro:
            return None
        total = 0.0
        for unit_index in range(max(1, dwelling_units)):
            tier = min(unit_index, len(self.cap_per_dwelling_unit_in_euro) - 1)
            total += self.cap_per_dwelling_unit_in_euro[tier]
        return total


@dataclass
class SubsidyScheme:
    """One scheme of the catalog (§5.2)."""

    id: str
    country: str
    region: Optional[str]
    valid_from: str
    valid_to: Optional[str]
    legal_basis: str
    url: str
    asset_classes: List[ComponentType]
    measure_kinds: List[str]  # INSTALL | REPLACE
    eligibility: Condition
    benefit_kind: BenefitKind
    benefit: Dict[str, Any]  # kind-specific parameters (rate, amount, years, ...)
    eligible_cost: EligibleCostSpec
    cumulation_group: Optional[str]
    combined_rate_cap: Optional[float]
    excludes: List[str]
    payout_kind: PayoutKind
    source_ids: Tuple[str, ...] = ()

    def applies_to(self, asset_class: ComponentType, measure_kind: str) -> bool:
        """Whether the scheme covers the measure at all."""
        return asset_class in self.asset_classes and measure_kind in self.measure_kinds


@dataclass
class QuestionEntry:
    """One localized question of the questionnaire catalog (§5.7)."""

    fieldname: str
    answer_kind: str  # BOOLEAN | CHOICE | NUMBER | YEAR | INCOME_BAND
    question: Dict[str, str]
    options: List[str] = field(default_factory=list)
    option_labels: Dict[str, Dict[str, str]] = field(default_factory=dict)
    help_text: Dict[str, str] = field(default_factory=dict)
    unit: Optional[str] = None


@dataclass
class Question:
    """A question to ask, with the schemes that made it necessary ("asked because")."""

    entry: QuestionEntry
    asked_because: List[str] = field(default_factory=list)  # scheme ids
    # Upper bound of support the answer could unlock (ordering heuristic, §5.7):
    pruning_power_in_euro: float = 0.0


class SubsidyCatalog:
    """One country catalog plus the question catalog and source registry."""

    def __init__(
        self,
        schemes: List[SubsidyScheme],
        questions: Dict[str, QuestionEntry],
        snapshot_date: Optional[str],
        overall_cap_share: Optional[float],
        base_path: str,
        country: str,
    ) -> None:
        """Built by :meth:`load`."""
        self.schemes = schemes
        self.questions = questions
        self.snapshot_date = snapshot_date
        self.overall_cap_share = overall_cap_share
        self.base_path = base_path
        self.country = country

    @classmethod
    def load(cls, country: str, base_path: Optional[str] = None) -> "SubsidyCatalog":
        """Loads and validates `<base_path>/<COUNTRY>.json` plus `questions_<COUNTRY>.json`."""
        base = base_path or DEFAULT_SUBSIDY_CATALOG_PATH
        catalog_path = os.path.join(base, f"{country}.json")
        if not os.path.isfile(catalog_path):
            raise SubsidyDataError(f"No subsidy catalog for country {country!r} at {catalog_path}.")
        with open(catalog_path, encoding="utf-8") as file:
            raw = json.load(file)
        sources_path = os.path.join(base, "sources.json")
        registry = SourceRegistry.load(sources_path) if os.path.isfile(sources_path) else None
        schemes = []
        for item in raw.get("schemes", []):
            scheme_id = item.get("id", "<missing id>")
            for mandatory in ("legal_basis", "url"):
                if not item.get(mandatory):
                    raise SubsidyDataError(f"Scheme {scheme_id}: mandatory field {mandatory!r} missing (§5.2).")
            benefit_raw = dict(item["benefit"])
            benefit_kind = BenefitKind(benefit_raw.pop("kind"))
            eligible_raw = item.get("eligible_cost", {})
            cumulation = item.get("cumulation", {})
            source_ids = tuple(item.get("source_ids", []))
            if registry is not None and source_ids:
                registry.resolve(source_ids, f"scheme {scheme_id}")
            schemes.append(
                SubsidyScheme(
                    id=scheme_id,
                    country=item["jurisdiction"]["country"],
                    region=item["jurisdiction"].get("region"),
                    valid_from=item.get("valid_from", "1900-01-01"),
                    valid_to=item.get("valid_to"),
                    legal_basis=item["legal_basis"],
                    url=item["url"],
                    asset_classes=[
                        _component_type(name, scheme_id) for name in item["applies_to"]["asset_classes"]
                    ],
                    measure_kinds=list(item["applies_to"].get("measure_kinds", ["INSTALL", "REPLACE"])),
                    eligibility=Condition.parse(item.get("eligibility", {"all": []}), scheme_id),
                    benefit_kind=benefit_kind,
                    benefit=benefit_raw,
                    eligible_cost=EligibleCostSpec(
                        categories=[CostCategory(cat) for cat in eligible_raw.get("categories", ["INVESTMENT", "PLANNING", "REMOVAL"])],
                        cap_per_dwelling_unit_in_euro=list(eligible_raw.get("cap_per_dwelling_unit_in_euro", [])),
                        basis=eligible_raw.get("basis", "GROSS"),
                        proration=eligible_raw.get("proration", "NONE"),
                    ),
                    cumulation_group=cumulation.get("group"),
                    combined_rate_cap=cumulation.get("combined_rate_cap"),
                    excludes=list(cumulation.get("excludes", [])),
                    payout_kind=PayoutKind(item.get("payout", {}).get("kind", "UPFRONT_GRANT")),
                    source_ids=source_ids,
                )
            )
        questions = {}
        questions_path = os.path.join(base, f"questions_{country}.json")
        if os.path.isfile(questions_path):
            with open(questions_path, encoding="utf-8") as file:
                questions_raw = json.load(file)
            for item in questions_raw.get("questions", []):
                questions[item["field"]] = QuestionEntry(
                    fieldname=item["field"],
                    answer_kind=item["answer_kind"],
                    question=item["question"],
                    options=item.get("options", []),
                    option_labels=item.get("option_labels", {}),
                    help_text=item.get("help", {}),
                    unit=item.get("unit"),
                )
        return cls(
            schemes=schemes,
            questions=questions,
            snapshot_date=raw.get("catalog_snapshot_date"),
            overall_cap_share=raw.get("overall_cap_share"),
            base_path=base,
            country=country,
        )

    def candidate_schemes(
        self, asset_class: ComponentType, measure_kind: str, region: Optional[str], year: int
    ) -> List[SubsidyScheme]:
        """Pre-filter by jurisdiction, asset class and validity (§5.7)."""
        result = []
        for scheme in self.schemes:
            if not scheme.applies_to(asset_class, measure_kind):
                continue
            if scheme.region is not None and region is not None and scheme.region != region:
                continue
            valid_from_year = int(scheme.valid_from[:4])
            valid_to_year = int(scheme.valid_to[:4]) if scheme.valid_to else 9999
            if not valid_from_year <= year <= valid_to_year:
                continue
            result.append(scheme)
        return result


def _component_type(name: str, scheme_id: str) -> ComponentType:
    for member in ComponentType:
        if name in (member.name, member.value):
            return member
    raise SubsidyDataError(f"Scheme {scheme_id}: unknown asset class {name!r}.")


class EligibilityStatus(str, enum.Enum):
    """Tri-state eligibility (§5.7)."""

    ELIGIBLE = "ELIGIBLE"
    INELIGIBLE = "INELIGIBLE"
    UNDETERMINED = "UNDETERMINED"


@dataclass
class MeasureForSubsidy:
    """What the evaluator hands the subsidy engine per subsidized measure."""

    subject: str
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
    """Eligibility verdict for one scheme applied to one measure."""

    scheme: SubsidyScheme
    status: EligibilityStatus
    missing_fields: List[str] = field(default_factory=list)
    rejected_reason: Optional[str] = None


@dataclass
class SubsidyAward:
    """One awarded benefit, ready to be materialized as timeline entries."""

    scheme_id: str
    payout_kind: PayoutKind
    # For UPFRONT_GRANT: amount at year 0. For TAX_CREDIT_SCHEDULE: per-year amounts (years 1..N).
    upfront_amount: UncertainValue = field(default_factory=lambda: UncertainValue.exact(0.0))
    schedule_amounts: List[UncertainValue] = field(default_factory=list)
    # For OPERATIONAL: rate, carrier and duration; amounts are energy-dependent.
    operational_rate_per_kwh: float = 0.0
    operational_carrier: Optional[EnergyCarrier] = None
    operational_duration_years: int = 0
    # For LOAN_TERMS: FinancingPlan overrides.
    loan_interest_rate: Optional[float] = None
    loan_term_in_years: Optional[int] = None
    loan_repayment_grant_share: float = 0.0
    # For VAT_REDUCTION:
    reduced_vat_rate: Optional[float] = None
    caps_binding_per_slot: Dict[str, bool] = field(default_factory=dict)


@dataclass
class SubsidyDecision:
    """Fully reported outcome of the cumulation solver (§5.4) — the audit trail."""

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
        """Serializes the audit trail."""
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


def _eligible_cost_basis(
    scheme: SubsidyScheme, measure: MeasureForSubsidy, context: SubsidyContext
) -> Tuple[UncertainValue, Dict[str, bool]]:
    """Eligible cost per slot, with per-slot cap-binding flags (§3.9, §5.4)."""
    basis = UncertainValue.sum(
        measure.cost_by_category.get(category, UncertainValue.exact(0.0))
        for category in scheme.eligible_cost.categories
    )
    if scheme.eligible_cost.basis == "NET" and measure.vat_rate > 0:
        basis = basis.scale(1.0 / (1.0 + measure.vat_rate))
    if scheme.eligible_cost.proration == "RESIDENTIAL_SHARE":
        share = context.building.residential_share
        if share is not None:
            basis = basis.scale(share)
    cap = scheme.eligible_cost.cap_for_units(context.building.dwelling_units)
    binding = {"low": False, "average": False, "high": False}
    if cap is not None:
        binding = {
            "low": basis.minimum > cap,
            "average": basis.average > cap,
            "high": basis.maximum > cap,
        }
        basis = basis.clamp_upper(UncertainValue.exact(cap))
    return basis, binding


def assess_schemes(
    catalog: SubsidyCatalog,
    measure: MeasureForSubsidy,
    context: SubsidyContext,
    year: int,
) -> List[SchemeAssessment]:
    """Tri-state eligibility for all candidate schemes of one measure."""
    assessments = []
    for scheme in catalog.candidate_schemes(
        measure.facts.asset_class, measure.measure_kind, context.applicant.region, year
    ):
        verdict, missing = scheme.eligibility.evaluate(context, measure.facts)
        if verdict is True:
            assessments.append(SchemeAssessment(scheme=scheme, status=EligibilityStatus.ELIGIBLE))
        elif verdict is False:
            assessments.append(
                SchemeAssessment(
                    scheme=scheme,
                    status=EligibilityStatus.INELIGIBLE,
                    rejected_reason="failed eligibility condition",
                )
            )
        else:
            assessments.append(
                SchemeAssessment(
                    scheme=scheme, status=EligibilityStatus.UNDETERMINED, missing_fields=sorted(set(missing))
                )
            )
    return assessments


def _combination_awards(
    schemes: List[SubsidyScheme],
    measure: MeasureForSubsidy,
    context: SubsidyContext,
    overall_cap_share: Optional[float],
) -> List[SubsidyAward]:
    """Values one admissible combination in all three slots (§5.4)."""
    awards: List[SubsidyAward] = []
    # Share-based schemes stack additively per cumulation group, capped by combined_rate_cap.
    share_groups: Dict[Optional[str], List[SubsidyScheme]] = {}
    for scheme in schemes:
        if scheme.benefit_kind in (BenefitKind.SHARE_OF_ELIGIBLE_COST, BenefitKind.BONUS_SHARE):
            share_groups.setdefault(scheme.cumulation_group, []).append(scheme)
    for _group, group_schemes in share_groups.items():
        total_rate = sum(float(scheme.benefit["rate"]) for scheme in group_schemes)
        rate_caps = [scheme.combined_rate_cap for scheme in group_schemes if scheme.combined_rate_cap is not None]
        capped_rate = min([total_rate] + rate_caps)
        scale_down = capped_rate / total_rate if total_rate > 0 else 0.0
        for scheme in group_schemes:
            basis, binding = _eligible_cost_basis(scheme, measure, context)
            rate = float(scheme.benefit["rate"]) * scale_down
            awards.append(
                SubsidyAward(
                    scheme_id=scheme.id,
                    payout_kind=scheme.payout_kind,
                    upfront_amount=basis.scale(rate),
                    caps_binding_per_slot=binding,
                )
            )
    for scheme in schemes:
        if scheme.benefit_kind in (BenefitKind.SHARE_OF_ELIGIBLE_COST, BenefitKind.BONUS_SHARE):
            continue
        basis, binding = _eligible_cost_basis(scheme, measure, context)
        if scheme.benefit_kind == BenefitKind.LUMP_SUM:
            amount = UncertainValue.exact(float(scheme.benefit["amount"]))
            awards.append(
                SubsidyAward(
                    scheme_id=scheme.id,
                    payout_kind=scheme.payout_kind,
                    upfront_amount=amount.clamp_upper(basis) if scheme.eligible_cost.categories else amount,
                    caps_binding_per_slot=binding,
                )
            )
        elif scheme.benefit_kind == BenefitKind.PER_UNIT:
            amount = UncertainValue.exact(float(scheme.benefit["amount"]) * measure.facts.size)
            awards.append(
                SubsidyAward(
                    scheme_id=scheme.id,
                    payout_kind=scheme.payout_kind,
                    upfront_amount=amount.clamp_upper(basis),
                    caps_binding_per_slot=binding,
                )
            )
        elif scheme.benefit_kind == BenefitKind.TAX_CREDIT:
            rate = float(scheme.benefit["rate"])
            years = int(scheme.benefit["years"])
            total = basis.scale(rate)
            shares = scheme.benefit.get("annual_shares")
            if shares:
                if abs(sum(shares) - 1.0) > 1e-9:
                    raise SubsidyDataError(f"Scheme {scheme.id}: annual_shares must sum to 1.")
                schedule = [total.scale(share) for share in shares]
            else:
                schedule = [total.scale(1.0 / years) for _ in range(years)]
            awards.append(
                SubsidyAward(
                    scheme_id=scheme.id,
                    payout_kind=PayoutKind.TAX_CREDIT_SCHEDULE,
                    schedule_amounts=schedule,
                    caps_binding_per_slot=binding,
                )
            )
        elif scheme.benefit_kind == BenefitKind.REDUCED_VAT:
            awards.append(
                SubsidyAward(
                    scheme_id=scheme.id,
                    payout_kind=PayoutKind.VAT_REDUCTION,
                    reduced_vat_rate=float(scheme.benefit["vat_rate"]),
                )
            )
        elif scheme.benefit_kind == BenefitKind.SOFT_LOAN:
            awards.append(
                SubsidyAward(
                    scheme_id=scheme.id,
                    payout_kind=PayoutKind.LOAN_TERMS,
                    loan_interest_rate=float(scheme.benefit["interest_rate"]),
                    loan_term_in_years=int(scheme.benefit["term"]),
                    loan_repayment_grant_share=float(scheme.benefit.get("repayment_grant_rate", 0.0)),
                )
            )
        elif scheme.benefit_kind == BenefitKind.OPERATIONAL:
            awards.append(
                SubsidyAward(
                    scheme_id=scheme.id,
                    payout_kind=PayoutKind.OPERATIONAL,
                    operational_rate_per_kwh=float(scheme.benefit["rate_per_kwh"]),
                    operational_carrier=EnergyCarrier(scheme.benefit["carrier"]),
                    operational_duration_years=int(scheme.benefit["duration_years"]),
                )
            )
    # EU state-aid overall cap: bounds total *upfront* support per measure (§5.4).
    if overall_cap_share is not None:
        gross = UncertainValue.sum(measure.cost_by_category.values())
        cap = gross.scale(overall_cap_share)
        total_upfront = UncertainValue.sum(award.upfront_amount for award in awards)
        if total_upfront.average > cap.average > 0:
            scale_down = cap.average / total_upfront.average
            for award in awards:
                award.upfront_amount = award.upfront_amount.scale(scale_down)
    return awards


def _support_value(
    awards: List[SubsidyAward],
    measure: MeasureForSubsidy,
    discount: Callable[[int], float],
    slot_getter: Callable[[UncertainValue], float],
) -> float:
    """Discounted value of a combination's support in one slot (solver objective)."""
    value = 0.0
    for award in awards:
        value += slot_getter(award.upfront_amount)
        for offset, amount in enumerate(award.schedule_amounts, start=1):
            value += slot_getter(amount) * discount(offset)
        if award.payout_kind == PayoutKind.OPERATIONAL and award.operational_carrier is not None:
            energy = measure.annual_energy_sold_in_kwh.get(
                award.operational_carrier, 0.0
            ) or measure.annual_energy_bought_in_kwh.get(award.operational_carrier, 0.0)
            for year in range(1, award.operational_duration_years + 1):
                value += award.operational_rate_per_kwh * energy * discount(year)
        if award.loan_repayment_grant_share:
            value += slot_getter(UncertainValue.sum(measure.cost_by_category.values())) * award.loan_repayment_grant_share
    return value


def solve_cumulation(
    catalog: SubsidyCatalog,
    measure: MeasureForSubsidy,
    context: SubsidyContext,
    year: int,
    discount: Callable[[int], float],
) -> SubsidyDecision:
    """Enumerates admissible combinations of ELIGIBLE schemes and picks the best (§5.4).

    The decision is made on the AVERAGE slot; the chosen combination is then valued in all
    three slots. UNDETERMINED schemes are excluded but reported with the optimistic upper
    bound they could unlock (§5.7).
    """
    assessments = assess_schemes(catalog, measure, context, year)
    eligible = [assessment.scheme for assessment in assessments if assessment.status == EligibilityStatus.ELIGIBLE]
    decision = SubsidyDecision(measure_subject=measure.subject)
    for assessment in assessments:
        if assessment.status == EligibilityStatus.INELIGIBLE:
            decision.rejected.append({"scheme_id": assessment.scheme.id, "reason": assessment.rejected_reason})
        elif assessment.status == EligibilityStatus.UNDETERMINED:
            decision.undetermined.append(
                {"scheme_id": assessment.scheme.id, "missing_fields": assessment.missing_fields}
            )

    def admissible(combination: List[SubsidyScheme]) -> bool:
        ids = {scheme.id for scheme in combination}
        for scheme in combination:
            if ids & set(scheme.excludes):
                return False
        return True

    # Enumerate subsets (scheme sets are small, typically < 10 per measure).
    best: Optional[Tuple[float, List[SubsidyScheme], List[SubsidyAward]]] = None
    best_per_slot: Dict[str, Optional[str]] = {}
    slot_getters = {
        "low": lambda value: value.maximum,  # optimistic world: max support
        "average": lambda value: value.average,
        "high": lambda value: value.minimum,
    }
    best_by_slot: Dict[str, Tuple[float, str]] = {}
    count = len(eligible)
    for mask in range(1 << count):
        combination = [eligible[index] for index in range(count) if mask & (1 << index)]
        if not admissible(combination):
            continue
        awards = _combination_awards(combination, measure, context, catalog.overall_cap_share)
        for slot_name, getter in slot_getters.items():
            slot_value = _support_value(awards, measure, discount, getter)
            key = "|".join(sorted(scheme.id for scheme in combination)) or "<none>"
            if slot_name not in best_by_slot or slot_value > best_by_slot[slot_name][0] + 1e-9:
                best_by_slot[slot_name] = (slot_value, key)
        average_value = _support_value(awards, measure, discount, slot_getters["average"])
        if best is None or average_value > best[0] + 1e-9:
            best = (average_value, combination, awards)
    if best is None:
        best = (0.0, [], [])
    chosen_key = "|".join(sorted(scheme.id for scheme in best[1])) or "<none>"
    for slot_name in ("low", "high"):
        slot_best = best_by_slot.get(slot_name)
        best_per_slot[slot_name] = slot_best[1] if slot_best and slot_best[1] != chosen_key else None
    decision.applied = best[2]
    decision.other_slot_optimal_combination = best_per_slot
    # Optimistic upper bound over undetermined schemes: value if they all were eligible too.
    if decision.undetermined:
        undetermined_schemes = [
            assessment.scheme for assessment in assessments if assessment.status == EligibilityStatus.UNDETERMINED
        ]
        optimistic = eligible + undetermined_schemes
        best_optimistic = 0.0
        opt_count = len(optimistic)
        if opt_count <= 16:
            for mask in range(1 << opt_count):
                combination = [optimistic[index] for index in range(opt_count) if mask & (1 << index)]
                if not admissible(combination):
                    continue
                awards = _combination_awards(combination, measure, context, catalog.overall_cap_share)
                best_optimistic = max(
                    best_optimistic, _support_value(awards, measure, discount, slot_getters["average"])
                )
        decision.undetermined_upper_bound_in_euro = max(0.0, best_optimistic - best[0])
    return decision


def required_questions(
    catalog: SubsidyCatalog,
    planned_measures: List[MeasureForSubsidy],
    context: SubsidyContext,
    year: int,
) -> List[Question]:
    """Computes the minimal question set for the candidate schemes (§5.7).

    Collects every context field referenced by the eligibility conditions of candidate
    schemes, drops already-answered/derivable ones, and orders by pruning power.
    """
    field_to_schemes: Dict[str, List[str]] = {}
    scheme_support: Dict[str, float] = {}
    for measure in planned_measures:
        for scheme in catalog.candidate_schemes(
            measure.facts.asset_class, measure.measure_kind, context.applicant.region, year
        ):
            gross = UncertainValue.sum(measure.cost_by_category.values()).average
            if scheme.benefit_kind in (BenefitKind.SHARE_OF_ELIGIBLE_COST, BenefitKind.BONUS_SHARE):
                support = gross * float(scheme.benefit.get("rate", 0.0))
            elif scheme.benefit_kind in (BenefitKind.LUMP_SUM,):
                support = float(scheme.benefit.get("amount", 0.0))
            elif scheme.benefit_kind == BenefitKind.PER_UNIT:
                support = float(scheme.benefit.get("amount", 0.0)) * measure.facts.size
            elif scheme.benefit_kind == BenefitKind.TAX_CREDIT:
                support = gross * float(scheme.benefit.get("rate", 0.0))
            else:
                support = 0.0
            scheme_support[scheme.id] = max(scheme_support.get(scheme.id, 0.0), support)
            for fieldname in scheme.eligibility.referenced_fields():
                field_to_schemes.setdefault(fieldname, []).append(scheme.id)
            if scheme.eligible_cost.proration == "RESIDENTIAL_SHARE":
                field_to_schemes.setdefault("building.residential_share", []).append(scheme.id)
            if scheme.eligible_cost.cap_per_dwelling_unit_in_euro:
                field_to_schemes.setdefault("building.dwelling_units", []).append(scheme.id)
    questions: List[Question] = []
    for fieldname, scheme_ids in field_to_schemes.items():
        if fieldname.startswith("measure."):
            continue  # known from the simulation / cost facts, never asked
        known, _value = context.resolve_field(fieldname, None)
        if known:
            continue
        # `residential_share` is derived — ask the two friendly area questions instead (§5.7):
        target_fields = (
            ["building.residential_floor_area_in_m2", "building.commercial_floor_area_in_m2"]
            if fieldname == "building.residential_share"
            else [fieldname]
        )
        for target in target_fields:
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
