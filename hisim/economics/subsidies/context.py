"""Applicant and building context for subsidy eligibility (cost_spec.md §5.3, §5.7).

Holds the country-neutral facts that the questionnaire fills and eligibility conditions read: who applies
(`ApplicantActor`, `ApplicantProfile`), the building (`SubsidyBuildingContext`), what the evaluation installs
(`SubsidyPackageContext`), and the vocabulary of field names a condition may use (`SubsidyContextFields`).
"""

from __future__ import annotations

import dataclasses
import enum
import typing
from dataclasses import dataclass
from typing import (
    Any,
    Dict,
    FrozenSet,
    Optional,
    Set,
    Tuple,
)

from hisim.economics.facts import ExistingAsset


class SubsidyDataError(ValueError):
    """Raised for malformed subsidy catalogs and for problems the cumulation solver cannot handle.

    Raised at load time (unknown benefit kind, missing ``legal_basis``, a condition on a field no context provides, an
    unsourced scheme) and at solve time (a candidate set too large for the solver). A scheme that parsed wrongly would
    yield a plausible euro amount no legal text backs, so the engine fails instead (§3.10).
    """


class HeritageStatus(str, enum.Enum):
    """Heritage-protection status of the building (§5.3).

    Several programmes relax their technical thresholds for protected buildings (the BEG, for example, accepts a lower
    SCOP for a listed monument). The members are legal categories, not a severity order, so conditions compare them
    with ``==``, ``!=`` or ``in``, never ``<``.
    """

    NONE = "NONE"
    LISTED_MONUMENT = "LISTED_MONUMENT"  # Einzeldenkmal
    ENSEMBLE_PROTECTED = "ENSEMBLE_PROTECTED"  # Ensembleschutz
    PRESERVATION_WORTHY = "PRESERVATION_WORTHY"  # besonders erhaltenswerte Bausubstanz


class DwellingType(str, enum.Enum):
    """How the dwelling is attached to its neighbours, in the bands that fixed-amount grants use (§5.3).

    Some programmes pay a different fixed amount per measure depending on this: Ireland's SEAI pays EUR 2,000 for attic
    insulation in a detached house and EUR 1,400 in a mid-terrace house. Semi-detached and end-of-terrace houses are
    paid the same everywhere and share one member. The German BEG has no such axis, so the field is optional. ``None``
    means unanswered and makes a condition on it UNDETERMINED rather than false.

    Example: the catalog leaf ``{"field": "building.dwelling_type", "op": "==", "value": "DETACHED"}`` selects the
    detached-house variant of a banded grant.
    """

    DETACHED = "DETACHED"
    SEMI_DETACHED_OR_END_TERRACE = "SEMI_DETACHED_OR_END_TERRACE"
    MID_TERRACE = "MID_TERRACE"
    APARTMENT = "APARTMENT"


class ApplicantActor(str, enum.Enum):
    """Who signs the funding application, as eligibility conditions test it.

    Most residential programmes are open to owner-occupiers, landlords and condominium associations but not to tenants.
    The names are country-neutral, so one context serves every country catalog. This is not ``timeline.Actor``, which
    says who pays a cash flow; the two are not converted into each other, and ``CONDOMINIUM_ASSOCIATION`` has no
    ``Actor`` counterpart.
    """

    OWNER_OCCUPIER = "OWNER_OCCUPIER"
    LANDLORD = "LANDLORD"
    CONDOMINIUM_ASSOCIATION = "CONDOMINIUM_ASSOCIATION"  # the owners of a multi-dwelling building applying as one body
    TENANT = "TENANT"


@dataclass
class ApplicantProfile:
    """Facts about the person applying for the subsidy (§5.3).

    The applicant half of the eligibility context; the building half is :class:`SubsidyBuildingContext`. No simulation
    produces these facts, so the questionnaire asks them (§5.7). ``None`` means unanswered: a condition touching it
    makes its scheme UNDETERMINED instead of ineligible.
    """

    actor: ApplicantActor = ApplicantActor.OWNER_OCCUPIER
    taxable_household_income_in_euro: Optional[float] = None  # per year, gross of tax; None = unanswered
    household_size: Optional[int] = None  # persons; some income thresholds scale with it
    main_residence: Optional[bool] = True  # self-occupation, required by several bonuses
    region: Optional[str] = None  # NUTS-3 or municipality key for regional schemes
    # Whether the applicant draws a means-tested social benefit. Country-neutral: Ireland's SEAI
    # calls it a "qualifying welfare payment" and pays a higher fixed grant for attic and cavity
    # insulation, the Warmer Homes Scheme funds the works outright; comparable social-tariff
    # conditions exist in most member states. None = unanswered.
    receives_means_tested_benefit: Optional[bool] = None
    # Whether the applicant bought this home as their first home. SEAI pays a higher fixed attic
    # grant to someone who bought a second-hand home on or after 2025-01-01 and had never owned
    # one before. None = unanswered.
    first_time_buyer: Optional[bool] = None
    # Whether the works are delivered as one managed complete upgrade rather than measure by
    # measure — Ireland's One Stop Shop route, a KfW-style full-refurbishment programme elsewhere.
    # Several grants (floor and rafter insulation, mechanical ventilation, air tightness) exist
    # only on that route. None = unanswered.
    managed_full_retrofit: Optional[bool] = None


@dataclass
class SubsidyBuildingContext:
    """Building facts read by eligibility conditions (§5.3).

    Age, size, usage split, heritage status and the heating system being replaced. The caller fills what the building
    model knows (construction year, floor areas); the rest are questionnaire answers (§5.7). ``None`` means unanswered
    and makes a condition UNDETERMINED; the non-optional fields default to a single dwelling unit and no commercial
    floor area.
    """

    construction_year: Optional[int] = None
    dwelling_units: int = 1
    # How the dwelling is attached to its neighbours, for programmes whose fixed amounts band on
    # it (see :class:`DwellingType`). None = unanswered.
    dwelling_type: Optional[DwellingType] = None
    heated_floor_area_in_m2: Optional[float] = None
    residential_floor_area_in_m2: Optional[float] = None
    commercial_floor_area_in_m2: float = 0.0
    heritage_status: Optional[HeritageStatus] = HeritageStatus.NONE
    energy_performance_class: Optional[str] = None
    existing_heating: Optional[ExistingAsset] = None
    # Whether an individual renovation roadmap exists for the building. Country-neutral concept
    # (Sánchez Ramos et al. 2025, Sustainability 17(5):2289 surveys the EU instruments); in the
    # German catalog it is the "individueller Sanierungsfahrplan (iSFP)" behind the BEG envelope
    # bonus.
    has_renovation_roadmap: Optional[bool] = None

    @property
    def residential_share(self) -> Optional[float]:
        """Residential fraction of the floor area in [0, 1], derived and never asked separately (§5.7).

        Read by conditions of residential-only programmes (``building.residential_share >= 0.5``) and by the
        ``RESIDENTIAL_SHARE`` proration of the eligible cost in mixed-use buildings (§5.2).

        Returns:
            The share, or ``None`` when the residential area is unanswered or both areas are zero, which makes
                conditions on it UNDETERMINED.
        """
        if self.residential_floor_area_in_m2 is None:
            return None
        total = self.residential_floor_area_in_m2 + self.commercial_floor_area_in_m2
        if total <= 0:
            return None
        return self.residential_floor_area_in_m2 / total


@dataclass
class SubsidyPackageContext:
    """What the evaluation being priced newly installs, for conditions on what a measure must come with.

    Some grants are paid only for a measure carried out together with another: SEAI's central-heating grant covers
    radiators installed beside a heat pump, not new radiators on an oil boiler. The evaluator lists the asset classes
    it charges at year 0 as new investments or replacements (``calculators.context_resolution.installation_verdict``).
    In a staged plan one evaluation is one stage, so "in the same package" means "bought in the same stage". The fields
    are computed, never asked; a context nobody filled leaves them ``None``, so conditions on them are UNDETERMINED.

    Example: the leaf ``{"field": "package.installed_asset_classes", "op": "contains", "value": "HeatPump"}`` holds
    when the same evaluation installs a heat pump. The values are ``ComponentType`` values.
    """

    #: The ``ComponentType`` values of every asset class the evaluation newly installs, sorted.
    installed_asset_classes: Optional[Tuple[str, ...]] = None


# --------------------------------------------------------------------------- field vocabulary
# The names conditions and questions may use, derived from the context dataclasses themselves.

def _dataclass_of(annotation: Any) -> Optional[type]:
    """Return the dataclass behind a possibly `Optional[...]` annotation, or ``None`` if there is none.

    Lets :func:`_enumerate_context_fields` descend into nested context objects such as ``building.existing_heating``.
    """
    if dataclasses.is_dataclass(annotation) and isinstance(annotation, type):
        return annotation
    for argument in typing.get_args(annotation):
        if dataclasses.is_dataclass(argument) and isinstance(argument, type):
            return argument
    return None


def _enumerate_context_fields(context_roots: Optional[Dict[str, type]] = None) -> FrozenSet[str]:
    """Return every field name a condition may address: dataclass fields, one nested level, and properties.

    The vocabulary is derived from the context dataclasses by reflection, so a new field on :class:`ApplicantProfile`
    or :class:`SubsidyBuildingContext` is addressable at once. Properties are included because derived fields such as
    ``building.residential_share`` are valid targets. Only one nesting level is walked, since the catalog cannot
    express deeper paths.

    Args:
        context_roots: Root name to dataclass, e.g. ``{"applicant": ApplicantProfile}``; ``None`` uses
            `SubsidyContextFields.CONTEXT_ROOTS`.

    Returns:
        Dotted names such as ``building.existing_heating.energy_carrier``.
    """
    names: Set[str] = set()
    for root, context_class in (context_roots or SubsidyContextFields.CONTEXT_ROOTS).items():
        hints = typing.get_type_hints(context_class)
        for context_field in dataclasses.fields(context_class):
            names.add(f"{root}.{context_field.name}")
            nested = _dataclass_of(hints.get(context_field.name))
            if nested is not None:
                for sub_field in dataclasses.fields(nested):
                    names.add(f"{root}.{context_field.name}.{sub_field.name}")
        for attribute, member in vars(context_class).items():
            if isinstance(member, property):  # derived fields such as `building.residential_share`
                names.add(f"{root}.{attribute}")
    return frozenset(names)


def _known_context_fields(context_roots: Dict[str, type], derived: Dict[str, Tuple[str, ...]]) -> FrozenSet[str]:
    """Return the condition vocabulary after checking the derived-field registry against it.

    Runs at import time to build :attr:`SubsidyContextFields.KNOWN_CONTEXT_FIELDS`. A stale registry entry would
    otherwise show up much later as a question nobody can answer.

    Raises:
        SubsidyDataError: If the derived-field registry names a field the context does not have.
    """
    names = _enumerate_context_fields(context_roots)
    unknown_derived = sorted(
        {name for name in derived if name not in names}
        | {target for targets in derived.values() for target in targets}
        - names
    )
    if unknown_derived:  # pragma: no cover — a coding error in the registry, not a data error
        raise SubsidyDataError(f"DERIVED_CONTEXT_FIELDS references unknown context fields: {unknown_derived}.")
    return names


class SubsidyContextFields:
    """Namespace for the field names an eligibility condition may use (§5.7).

    Holds which root (``applicant``, ``building``, ``package``) resolves against which dataclass, which fields are
    computed rather than asked, and the resulting set of legal names. The catalog loader uses it to reject a typo like
    ``applicant.incom`` (§5.3), and the data-file checks use it to prove every referenced field has a localized
    question (§9.6).
    """

    #: Condition roots and the dataclass each resolves against. `measure.*` is deliberately
    #: absent: it addresses arbitrary `ComponentCostFacts.technical_attributes` keys and is
    #: checked at resolve time, not against a vocabulary.
    CONTEXT_ROOTS: Dict[str, type] = {
        "applicant": ApplicantProfile,
        "building": SubsidyBuildingContext,
        "package": SubsidyPackageContext,
    }

    #: Roots whose fields the engine computes and no user is ever asked: the measure's own cost
    #: facts and what the evaluation installs beside it. The questionnaire derivation and the
    #: question-coverage check both skip them.
    COMPUTED_ROOTS: Tuple[str, ...] = ("measure", "package")

    @classmethod
    def is_computed(cls, fieldname: str) -> bool:
        """Whether a condition field is computed by the engine rather than asked (§5.7)."""
        return fieldname.split(".", 1)[0] in cls.COMPUTED_ROOTS

    #: Fields that are computed from other fields and therefore never asked directly: the
    #: derived field maps to the user-answerable fields whose answers determine it (§5.7). This
    #: registry is the *only* place that knowledge lives — the question derivation
    #: (`required_questions`) and the question-coverage validation
    #: (`validation.validate_subsidy_catalog`) both read it.
    DERIVED_CONTEXT_FIELDS: Dict[str, Tuple[str, ...]] = {
        "building.residential_share": (
            "building.residential_floor_area_in_m2",
            "building.commercial_floor_area_in_m2",
        ),
    }

    #: Statically enumerable context fields conditions may reference (§5.7) — derived, not
    #: typed out.
    KNOWN_CONTEXT_FIELDS: FrozenSet[str] = _known_context_fields(CONTEXT_ROOTS, DERIVED_CONTEXT_FIELDS)


def question_targets(fieldname: str) -> Tuple[str, ...]:
    """Return the user-answerable field(s) whose answers determine `fieldname` (§5.7).

    Plain fields map to themselves; derived fields map to the questions behind them, so ``building.residential_share``
    becomes the two floor-area questions. Both :func:`required_questions` and the coverage check in ``validation.py``
    use this function, so they agree on which questions a catalog must ship.
    """
    return SubsidyContextFields.DERIVED_CONTEXT_FIELDS.get(fieldname, (fieldname,))
