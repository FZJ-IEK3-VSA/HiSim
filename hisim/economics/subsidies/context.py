"""Applicant and building context for subsidy eligibility (cost_spec.md §5.3, §5.7).

The country-neutral vocabulary the questionnaire fills and eligibility conditions read:
who applies (`ApplicantActor`, `ApplicantProfile`), the building facts
(`SubsidyBuildingContext`, `HeritageStatus`), and the derived field vocabulary
(`SubsidyContextFields`) that keeps condition/question field names honest (W2.3).
Split out of the former single-module `subsidies.py` (PR-3 review); the package
`__init__` re-exports everything, so `from hisim.economics.subsidies import ...` is
unchanged.
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
    """Raised for malformed subsidy catalogs.

    The single error type of this module, used both at load time (unknown benefit kind, missing
    ``legal_basis``, a condition on a field no context provides, unsourced scheme) and at solve
    time (a candidate set too large for the cumulation solver). Failing loudly on catalog data is
    the point: a scheme that silently parsed wrong would produce a plausible-looking euro amount
    that no legal text backs, which is exactly what the provenance requirements of §3.10 exist to
    prevent.
    """


class HeritageStatus(str, enum.Enum):
    """Heritage-protection status (§5.3).

    Heritage protection is a first-class eligibility input because German (and several other EU)
    programmes *relax* their technical thresholds for protected buildings — the BEG for instance
    accepts a lower SCOP for a listed monument — and because it caps what an envelope retrofit may
    physically do. The member names follow the legal categories rather than a severity ordering;
    conditions test them with ``==`` / ``!=`` / ``in``, never with ``<``.
    """

    NONE = "NONE"
    LISTED_MONUMENT = "LISTED_MONUMENT"  # Einzeldenkmal
    ENSEMBLE_PROTECTED = "ENSEMBLE_PROTECTED"  # Ensembleschutz
    PRESERVATION_WORTHY = "PRESERVATION_WORTHY"  # besonders erhaltenswerte Bausubstanz


class ApplicantActor(str, enum.Enum):
    """Applicant roles for eligibility conditions.

    Who applies decides what is on offer: most residential programmes are open to owner-occupiers,
    landlords and condominium associations but not to tenants, and several bonuses (the BEG
    income bonus, for example) require self-occupation. The vocabulary is deliberately
    country-neutral — a condominium association is a "Wohnungseigentümergemeinschaft (WEG)" in
    Germany, an "owners' management company" in Ireland — so the same context serves every
    country catalog. This is also deliberately *not* ``timeline.Actor``: that enum is about who
    pays a cash flow and includes the pre-allocation ``SYSTEM`` view, whereas this one is about
    who signs the funding application. The two are not convertible into each other, and nothing
    tries: the applicant is stated directly on the :class:`ApplicantProfile` of the economic
    context, and ``CONDOMINIUM_ASSOCIATION`` has no ``Actor`` counterpart to map back to at all
    (§8 D23).
    """

    OWNER_OCCUPIER = "OWNER_OCCUPIER"
    LANDLORD = "LANDLORD"
    CONDOMINIUM_ASSOCIATION = "CONDOMINIUM_ASSOCIATION"  # the owners of a multi-dwelling building applying as one body
    TENANT = "TENANT"


@dataclass
class ApplicantProfile:
    """Who applies for the subsidy (§5.3).

    One half of the eligibility context (the other is :class:`SubsidyBuildingContext`): the facts
    about the *person* that no simulation can produce and that the §5.7 questionnaire therefore
    asks. Every optional field defaults to ``None``, and ``None`` means *unanswered* rather than
    "not applicable" — a condition touching it makes its scheme UNDETERMINED instead of ineligible,
    which is what keeps a half-filled questionnaire from silently costing the user money.
    """

    actor: ApplicantActor = ApplicantActor.OWNER_OCCUPIER
    taxable_household_income_in_euro: Optional[float] = None  # per year, gross of tax; None = unanswered
    household_size: Optional[int] = None  # persons; some income thresholds scale with it
    main_residence: Optional[bool] = True  # self-occupation, required by several bonuses
    region: Optional[str] = None  # NUTS-3 or municipality key for regional schemes


@dataclass
class SubsidyBuildingContext:
    """Building facts consumed by eligibility conditions (§5.3).

    The building half of the eligibility context: age, size, usage split, heritage status and the
    heating system being replaced — the facts programmes key their thresholds on. Some of these the
    simulation or the building model already knows (construction year, floor areas) and the caller
    fills them in; the rest are questionnaire answers (§5.7). As on :class:`ApplicantProfile`,
    ``None`` means unanswered and propagates as UNDETERMINED, whereas the non-optional fields carry
    defaults that are assertions: a single dwelling unit and no commercial floor area.
    """

    construction_year: Optional[int] = None
    dwelling_units: int = 1
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
        """Derived, never asked separately (§5.7).

        The residential fraction of the floor area, in [0, 1]. Two things read it: eligibility
        conditions of residential-only programmes (``building.residential_share >= 0.5``) and the
        ``RESIDENTIAL_SHARE`` proration of the eligible-cost basis in mixed-use buildings (§5.2).
        Returning ``None`` when the residential area is unanswered — or when both areas are zero —
        is what makes those conditions UNDETERMINED rather than false; see
        :attr:`SubsidyContextFields.DERIVED_CONTEXT_FIELDS` for how the questionnaire asks the two
        areas behind it instead.
        """
        if self.residential_floor_area_in_m2 is None:
            return None
        total = self.residential_floor_area_in_m2 + self.commercial_floor_area_in_m2
        if total <= 0:
            return None
        return self.residential_floor_area_in_m2 / total


# --------------------------------------------------------------------------- field vocabulary
# W2.3: ONE source of truth for the names conditions and questions may use. The vocabulary is
# derived from the context dataclasses themselves, so a field added to `ApplicantProfile` or
# `SubsidyBuildingContext` cannot be forgotten in a hand-maintained whitelist.

def _dataclass_of(annotation: Any) -> Optional[type]:
    """The dataclass behind a (possibly `Optional[...]`) annotation, if there is one.

    Lets :func:`_enumerate_context_fields` descend one level into nested context objects, which is
    what makes ``building.existing_heating.energy_carrier`` an addressable condition field. Returns
    ``None`` for plain scalars, so a non-dataclass annotation simply contributes no sub-fields.
    """
    if dataclasses.is_dataclass(annotation) and isinstance(annotation, type):
        return annotation
    for argument in typing.get_args(annotation):
        if dataclasses.is_dataclass(argument) and isinstance(argument, type):
            return argument
    return None


def _enumerate_context_fields(context_roots: Optional[Dict[str, type]] = None) -> FrozenSet[str]:
    """Every addressable context field: dataclass fields, one nested level, and properties.

    Derives the condition vocabulary from the context dataclasses by reflection instead of a
    hand-maintained whitelist (W2.3), so a field added to :class:`ApplicantProfile` or
    :class:`SubsidyBuildingContext` is immediately addressable and cannot be forgotten. Properties
    are included because derived fields such as ``building.residential_share`` are legitimate
    condition targets. Exactly one nesting level is walked — deeper paths are not expressible in
    the catalog today, and unbounded recursion would admit names no question could ever cover.
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
    """The vocabulary, with the derived-field registry checked against it.

    Runs at import time to build :attr:`SubsidyContextFields.KNOWN_CONTEXT_FIELDS`, and takes the
    opportunity to verify that every name in ``DERIVED_CONTEXT_FIELDS`` — both the derived fields
    and the user-answerable targets they point at — actually exists in the enumerated vocabulary.
    A stale registry entry would otherwise surface much later as a question that can never be
    answered, so it is caught as an import-time coding error instead.

    Raises:
        SubsidyDataError: If the derived-field registry references a name the context does not have.
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
    """The addressable eligibility-context vocabulary (§5.7).

    A namespace, not a value type: it holds the three pieces of knowledge about *what a condition
    may talk about* — which roots resolve against which dataclass, which fields are computed rather
    than asked, and the resulting set of legal field names. Keeping them together is what lets the
    catalog loader reject a typo like ``applicant.incom`` at load time (§5.3) and lets the data-file
    CI prove that every field any shipped scheme references has a localized question (§9.6).
    """

    #: Condition roots and the dataclass each resolves against. `measure.*` is deliberately
    #: absent: it addresses arbitrary `ComponentCostFacts.technical_attributes` keys and is
    #: checked at resolve time, not against a vocabulary.
    CONTEXT_ROOTS: Dict[str, type] = {
        "applicant": ApplicantProfile,
        "building": SubsidyBuildingContext,
    }

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
    """The user-answerable field(s) whose answers determine `fieldname` (§5.7).

    Plain fields map to themselves; derived fields map to the friendly questions behind them.
    A condition on ``building.residential_share`` thus turns into two area questions rather than a
    percentage nobody can state off-hand. Both the question derivation (:func:`required_questions`)
    and the question-coverage check in ``validation.py`` route through this function, so the two
    can never disagree about which entry a catalog must ship.
    """
    return SubsidyContextFields.DERIVED_CONTEXT_FIELDS.get(fieldname, (fieldname,))
