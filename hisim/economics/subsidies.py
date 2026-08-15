"""Data-driven subsidy engine: EU scheme modeling (cost_spec.md §5).

Schemes live in ``hisim/subsidy_catalog/<COUNTRY>.json``. Eligibility is a small data-only
predicate language over a typed context; unanswered questions yield tri-state eligibility
(§5.7). The cumulation solver enumerates admissible combinations and picks the
NPV-maximizing one on the AVERAGE slot, then values it in all three slots (§5.4).

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

from __future__ import annotations

import dataclasses
import enum
import json
import os
import typing
from dataclasses import dataclass, field
from typing import Any, Callable, ClassVar, Dict, FrozenSet, Iterable, List, Optional, Set, Tuple

from hisim.economics.carriers import EnergyCarrier
from hisim.economics.database import SourceEntry, SourceRegistry
from hisim.economics.facts import ComponentCostFacts, ExistingAsset
from hisim.economics.provenance import (
    ParameterOrigin,
    ParameterProvenance,
    ProvenanceLedger,
    ResolvedSource,
)
from hisim.economics.timeline import CostCategory
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import ComponentType


class CumulationLimits:
    """Bounds the exponential cumulation solver is guarded by (§5.4).

    The solver enumerates *every* subset of the schemes that apply to one measure, so its cost is
    2^n in that number; this class is the one place that bound is stated. It exists as a named
    constant rather than a magic number because the guard's failure mode is a policy decision — a
    catalog that trips it is treated as a data defect (see :func:`_check_cumulation_size`) rather
    than silently truncated, since truncation would understate the support a user is entitled to.
    """

    #: Upper bound on the number of schemes handed to a subset enumeration in the cumulation
    #: solver (§5.4). The solver is exponential in that number, so a catalog that made many
    #: schemes apply to one measure would hang instead of returning a wrong answer. Real
    #: catalogs stay far below the limit (the shipped DE catalog holds nine schemes in total).
    MAX_CUMULATION_SCHEMES = 16


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
    landlords and condominium associations (WEG) but not to tenants, and several bonuses (the BEG
    income bonus, for example) require self-occupation. This is deliberately *not*
    ``timeline.Actor``: that enum is about who pays a cash flow and includes the pre-allocation
    ``SYSTEM`` view, whereas this one is about who signs the funding application. The two are not
    convertible into each other, and nothing tries: the applicant is stated directly on the
    :class:`ApplicantProfile` of the economic context, and ``WEG`` has no ``Actor`` counterpart to
    map back to at all (§8 D23).
    """

    OWNER_OCCUPIER = "OWNER_OCCUPIER"
    LANDLORD = "LANDLORD"
    WEG = "WEG"  # Wohnungseigentümergemeinschaft — condominium association applying as one body
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
    # Whether an individueller Sanierungsfahrplan exists (iSFP bonus for envelope measures):
    has_isfp: Optional[bool] = None

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


@dataclass(frozen=True)
class Condition:
    """One node of the eligibility predicate tree (§5.3) — inert catalog payload.

    W2.3: the node carries data only. Parsing it lives in :func:`parse_condition` (data side,
    called by the catalog loader) and evaluating it in :func:`evaluate_condition` (engine side,
    below), so the catalog half of this module has no behavior an evaluator could diverge from.
    """

    #: The comparison operators a leaf may use, and what each one means.
    CONDITION_OPS: ClassVar[Dict[str, Callable[[Any, Any], bool]]] = {
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

    kind: str  # "all" | "any" | "not" | "leaf"
    children: Tuple["Condition", ...] = ()
    fieldname: Optional[str] = None
    op: Optional[str] = None
    value: Any = None


def parse_condition(raw: dict, scheme_id: str) -> Condition:
    """Parses and validates a condition node with clear errors (data side).

    Recursively turns the catalog's ``{"all": [...]}`` / ``{"any": [...]}`` / ``{"not": {...}}`` /
    ``{"field": ..., "op": ..., "value": ...}`` JSON into the inert :class:`Condition` AST, checking
    as it goes that the operator is one of the nine allowed ones and that the field name is either
    part of the statically known vocabulary or a ``measure.*`` path (those address arbitrary
    ``technical_attributes`` keys and can only be checked when a measure is at hand). Validating at
    load time rather than at solve time is the whole point of the data-only predicate language:
    there is no Python ``eval`` anywhere, and a mistyped catalog fails with the scheme id in the
    message instead of quietly never matching.

    Args:
        raw: One condition node from the catalog JSON.
        scheme_id: Owning scheme, used only to make error messages actionable.

    Returns:
        The parsed node; children are parsed depth-first, so the returned tree is complete.

    Raises:
        SubsidyDataError: On an unknown operator, an unknown field name, or a node that is neither
            an ``all``/``any``/``not`` combinator nor a leaf.
    """
    if "all" in raw:
        return Condition(kind="all", children=tuple(parse_condition(child, scheme_id) for child in raw["all"]))
    if "any" in raw:
        return Condition(kind="any", children=tuple(parse_condition(child, scheme_id) for child in raw["any"]))
    if "not" in raw:
        return Condition(kind="not", children=(parse_condition(raw["not"], scheme_id),))
    if "field" in raw:
        fieldname = raw["field"]
        operator = raw.get("op")
        if operator not in Condition.CONDITION_OPS:
            raise SubsidyDataError(f"Scheme {scheme_id}: unknown op {operator!r} in condition on {fieldname!r}.")
        if not (fieldname in SubsidyContextFields.KNOWN_CONTEXT_FIELDS or fieldname.startswith("measure.")):
            raise SubsidyDataError(f"Scheme {scheme_id} references unknown field {fieldname!r}.")
        return Condition(kind="leaf", fieldname=fieldname, op=operator, value=raw.get("value"))
    raise SubsidyDataError(f"Scheme {scheme_id}: condition node {raw!r} is neither all/any/not nor a leaf.")


def referenced_fields(condition: Condition) -> List[str]:
    """All context fields referenced anywhere in the tree, in tree order (§5.7).

    Duplicates are kept: a field two schemes' conditions test twice weighs twice in the
    question-ordering heuristic, which is the established behavior.
    """
    if condition.kind == "leaf":
        return [condition.fieldname] if condition.fieldname else []
    names: List[str] = []
    for child in condition.children:
        names.extend(referenced_fields(child))
    return names


class BenefitKind(str, enum.Enum):
    """Tagged union of benefit kinds (§5.2).

    The *mechanism* a scheme uses to compute its support, surveyed from the schemes actually
    running in the EU (§5.1): a share of the eligible cost, a stackable bonus share, a fixed lump
    sum, an amount per unit of size, a multi-year tax credit, a reduced VAT rate, soft-loan terms,
    or a per-kWh operational payment. The tag selects the typed payload class
    (:attr:`BenefitTypes.BY_KIND`) that carries the mechanism's parameters, so adding a mechanism
    means adding a payload type here — adding a *programme* means editing a catalog file only.

    Note the deliberate distinction from :class:`PayoutKind`: a kind here says how much support is
    computed, a payout kind says when and in what form the money reaches the applicant.
    ``SHARE_OF_ELIGIBLE_COST`` and ``BONUS_SHARE`` share one payload type and differ only in
    intent — bonuses are the ones meant to stack on a base rate within a cumulation group.
    """

    SHARE_OF_ELIGIBLE_COST = "SHARE_OF_ELIGIBLE_COST"
    BONUS_SHARE = "BONUS_SHARE"
    LUMP_SUM = "LUMP_SUM"
    PER_UNIT = "PER_UNIT"
    TAX_CREDIT = "TAX_CREDIT"
    REDUCED_VAT = "REDUCED_VAT"
    SOFT_LOAN = "SOFT_LOAN"
    OPERATIONAL = "OPERATIONAL"


@dataclass(frozen=True)
class BenefitField:
    """One JSON key of a benefit payload: how it is named, converted and defaulted.

    The declarative description each benefit payload uses to state its JSON surface (its ``SPEC``),
    so that :meth:`Benefit.parse` can validate any payload generically — required keys present,
    unknown keys rejected, values convertible — instead of every kind hand-rolling its own parsing.
    Keeping the mapping declarative is what lets the loader name both the scheme and the offending
    key in an error message (W2.2).
    """

    key: str  # key in the catalog JSON
    name: str  # field name on the benefit dataclass
    convert: Callable[[Any], Any]
    required: bool = True
    default: Any = None


def _shares(raw: Any) -> Tuple[float, ...]:
    """Converts a JSON list of annual shares into a tuple of floats.

    The converter for :class:`TaxCreditBenefit`'s optional uneven payout schedule. It rejects
    strings and bytes explicitly because both are iterable and would otherwise be silently
    accepted character by character; the resulting `TypeError` is turned into a
    :class:`SubsidyDataError` naming the scheme by :meth:`Benefit.parse`.
    """
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Iterable):
        raise TypeError(f"expected a list of annual shares, got {raw!r}")
    return tuple(float(item) for item in raw)


@dataclass(frozen=True)
class Benefit:
    """Typed benefit payload of a scheme (§5.2, W2.2).

    The catalog JSON format is unchanged — ``benefit: {"kind": ..., <parameters>}`` — but the
    loader parses that dict into one of the frozen subclasses below, so a misspelled or missing
    parameter fails at load time naming the scheme and the key, instead of surfacing as a
    ``KeyError`` deep inside the cumulation solver.

    Subclasses declare their JSON surface in :attr:`SPEC`; :attr:`BenefitTypes.BY_KIND` maps each
    :class:`BenefitKind` to the payload type that implements it.
    """

    #: The payload's JSON keys, in documentation order.
    SPEC: ClassVar[Tuple[BenefitField, ...]] = ()

    @classmethod
    def parse(cls, raw: Dict[str, Any], scheme_id: str, kind: "BenefitKind") -> "Benefit":
        """Builds the payload from the catalog dict, rejecting missing/unknown/unparsable keys.

        The generic loader for every benefit kind, driven by the subclass's :attr:`SPEC`. Rejecting
        *unknown* keys as hard as missing ones is deliberate: a typo in a catalog would otherwise be
        dropped silently and the scheme would pay out at a default rate nobody intended.

        Args:
            raw: The ``benefit`` object from the catalog with its ``kind`` key already removed.
            scheme_id: Owning scheme, for error messages.
            kind: The declared benefit kind, likewise only used in error messages.

        Returns:
            An instance of the concrete payload class this was called on.

        Raises:
            SubsidyDataError: If a mandatory key is missing, an unknown key is present, or a value
                does not convert; the message always names the scheme and the key.
        """
        values: Dict[str, Any] = {}
        for spec in cls.SPEC:
            if spec.key in raw:
                try:
                    values[spec.name] = spec.convert(raw[spec.key])
                except (TypeError, ValueError, KeyError) as err:
                    raise SubsidyDataError(
                        f"Scheme {scheme_id}: benefit key {spec.key!r} of kind {kind.value} is not "
                        f"a valid {spec.convert.__name__ if hasattr(spec.convert, '__name__') else 'value'} "
                        f"({raw[spec.key]!r}): {err}."
                    ) from err
            elif spec.required:
                raise SubsidyDataError(
                    f"Scheme {scheme_id}: benefit of kind {kind.value} misses the mandatory key "
                    f"{spec.key!r} (expected keys: {[item.key for item in cls.SPEC]})."
                )
            else:
                values[spec.name] = spec.default
        unknown = sorted(set(raw) - {spec.key for spec in cls.SPEC})
        if unknown:
            raise SubsidyDataError(
                f"Scheme {scheme_id}: benefit of kind {kind.value} has unknown key(s) {unknown} "
                f"(expected keys: {[item.key for item in cls.SPEC]})."
            )
        return cls(**values)

    def value_estimate(self, gross_cost_in_euro: float, measure_size: float) -> float:
        """Rough upper bound of the support this benefit could unlock, for question ordering.

        The single definition of the *simplified* valuation (§5.7 pruning power): undiscounted,
        uncapped, on the gross measure cost. The exact, per-slot, capped valuation lives in the
        cumulation solver (:func:`_combination_awards`); both now read the same typed fields, so
        the two can no longer drift apart on key names or defaults.
        """
        del gross_cost_in_euro, measure_size  # unvalued kinds (loans, VAT, operational support)
        return 0.0


@dataclass(frozen=True)
class ShareBenefit(Benefit):
    """A share of the eligible cost — base rate (SHARE_OF_ELIGIBLE_COST) or bonus (BONUS_SHARE).

    The workhorse mechanism of the EU programmes surveyed in §5.1 (BEG EM: 30 % base plus speed,
    income and efficiency bonuses). It is the only payload the cumulation solver stacks: schemes
    sharing a ``cumulation_group`` have their rates added and then scaled back to the group's
    ``combined_rate_cap`` (§5.4), which is why base rate and bonus need no structural distinction
    here. The rate applies to the *eligible* cost basis of its own scheme — categories, VAT basis,
    proration and cap are per scheme, so two stacked schemes may compute on different bases.
    """

    rate: float  # fraction of the eligible cost basis, e.g. 0.30

    SPEC: ClassVar[Tuple[BenefitField, ...]] = (BenefitField("rate", "rate", float),)

    def value_estimate(self, gross_cost_in_euro: float, measure_size: float) -> float:
        """Rate on the gross cost."""
        del measure_size
        return gross_cost_in_euro * self.rate


@dataclass(frozen=True)
class LumpSumBenefit(Benefit):
    """A fixed amount, clamped to the eligible cost basis when the scheme declares one.

    Models the fixed-grant programmes of §5.1 — Austria's "Raus aus Öl und Gas" boiler-replacement
    grant, income-banded MaPrimeRénov' amounts — where the support does not scale with what the
    measure cost. The amount is *exact* in all three uncertainty slots (a statutory number has no
    band), which is why a lump sum can be the binding constraint in one slot and not another once
    the overall cap scales it; see :func:`_scaled_to_cap`.
    """

    amount: float  # euro, year-0 nominal, exact in all slots

    SPEC: ClassVar[Tuple[BenefitField, ...]] = (BenefitField("amount", "amount", float),)

    def value_estimate(self, gross_cost_in_euro: float, measure_size: float) -> float:
        """The amount itself."""
        del gross_cost_in_euro, measure_size
        return self.amount


@dataclass(frozen=True)
class PerUnitBenefit(Benefit):
    """An amount per unit of measure size (EUR/kW, EUR/m², ... — the unit of the cost facts).

    Covers €/m² insulation grants and €/kWp PV programmes (§5.1). The unit is *not* stated in the
    catalog: the amount is multiplied by ``ComponentCostFacts.size``, so it silently inherits
    whatever ``size_unit`` the asset class declares — which is the same unit the device price is
    quoted in, and is enforced consistent by the pre-run resolution check. Like a lump sum the
    result is exact in all slots and clamped to the eligible-cost basis.
    """

    amount: float  # euro per unit of `ComponentCostFacts.size` (kW, m², liter, ...)

    SPEC: ClassVar[Tuple[BenefitField, ...]] = (BenefitField("amount", "amount", float),)

    def value_estimate(self, gross_cost_in_euro: float, measure_size: float) -> float:
        """Amount times measure size."""
        del gross_cost_in_euro
        return self.amount * measure_size


@dataclass(frozen=True)
class TaxCreditBenefit(Benefit):
    """A share of the eligible cost, paid out over `years` (optionally unevenly).

    Models income-tax deduction programmes — Italy's Ecobonus over ten installments, Germany's
    §35c EStG (shipped as 20 % over three unevenly split years) — where the *timing* of the payout
    materially changes the NPV and is therefore not a detail: the solver discounts each installment
    before comparing combinations. Unlike the share kinds this one does not stack additively in a
    cumulation group; §35c is expressed as mutually exclusive with the grant programmes via
    ``excludes``.
    """

    rate: float  # fraction of the eligible cost basis, spread over `years`
    years: int  # number of annual installments, starting in year 1
    annual_shares: Tuple[float, ...] = ()  # optional uneven split; must sum to 1 and match `years`

    SPEC: ClassVar[Tuple[BenefitField, ...]] = (
        BenefitField("rate", "rate", float),
        BenefitField("years", "years", int),
        BenefitField("annual_shares", "annual_shares", _shares, required=False, default=()),
    )

    def __post_init__(self) -> None:
        """Validates the payout schedule (§5.2): whole years, shares summing to 1."""
        if self.years < 1:
            raise SubsidyDataError(f"Tax credit benefit needs years >= 1, got {self.years}.")
        if self.annual_shares:
            if len(self.annual_shares) != self.years:
                raise SubsidyDataError(
                    f"Tax credit benefit declares {len(self.annual_shares)} annual_shares for "
                    f"{self.years} years — the two must agree."
                )
            if abs(sum(self.annual_shares) - 1.0) > 1e-9:
                raise SubsidyDataError(
                    f"Tax credit benefit annual_shares must sum to 1, got {sum(self.annual_shares)}."
                )

    def schedule_shares(self) -> Tuple[float, ...]:
        """Per-year shares of the total credit; even split unless the catalog says otherwise.

        Returns one share per installment year, in order, always summing to 1 (enforced in
        :meth:`__post_init__`). The cumulation solver multiplies the total credit by these to build
        the award's ``schedule_amounts``, which land on the timeline in years 1..N — so this is the
        function that decides how much of a tax credit survives discounting.
        """
        if self.annual_shares:
            return self.annual_shares
        return tuple(1.0 / self.years for _ in range(self.years))

    def value_estimate(self, gross_cost_in_euro: float, measure_size: float) -> float:
        """Rate on the gross cost (undiscounted, like the other kinds)."""
        del measure_size
        return gross_cost_in_euro * self.rate


@dataclass(frozen=True)
class ReducedVatBenefit(Benefit):
    """A reduced VAT rate on the measure.

    **Unwired (§7 B7):** the resulting `PayoutKind.VAT_REDUCTION` award has no consumer anywhere
    in the engine — no VAT netting reads `reduced_vat_rate`. The kind is typed here so a catalog
    entry is at least well-formed; wiring it (or deleting it) is a separate fix-or-freeze call.
    """

    vat_rate: float

    SPEC: ClassVar[Tuple[BenefitField, ...]] = (BenefitField("vat_rate", "vat_rate", float),)


@dataclass(frozen=True)
class LoanTermsBenefit(Benefit):
    """Soft-loan terms: interest rate, term and an optional repayment grant (Tilgungszuschuss).

    A subsidized loan is not a cash grant but an *override of the financing plan* (§4.4): the award
    carries these terms, ``calculators/financing_application.py`` substitutes them into the
    :class:`~hisim.economics.financing.FinancingPlan`, and the benefit shows up as the interest the
    applicant does not pay. The optional repayment grant is the one part that is genuine support in
    year 0 — note §7 B3: the solver values it on the gross measure cost while financing applies it
    to the loan principal, which disagree whenever less than the full cost is financed (masked
    today because the shipped KfW rate is 0.0).
    """

    interest_rate: float  # nominal annual rate of the subsidized loan
    term: int  # years
    repayment_grant_rate: float = 0.0  # share of the principal written off (Tilgungszuschuss)

    SPEC: ClassVar[Tuple[BenefitField, ...]] = (
        BenefitField("interest_rate", "interest_rate", float),
        BenefitField("term", "term", int),
        BenefitField("repayment_grant_rate", "repayment_grant_rate", float, required=False, default=0.0),
    )


@dataclass(frozen=True)
class OperationalBenefit(Benefit):
    """A per-kWh payment on one carrier for a number of years (feed-in style support).

    The one benefit kind whose value depends on the *simulation* rather than on the investment:
    EEG-style feed-in remuneration and heat-generation premiums are paid per kWh actually sold (or,
    where no sold energy is recorded, bought — see :func:`_support_value`), so the engine can only
    value it once the meters have reported. Payments run for ``duration_years`` from year 1 and are
    truncated by the observation horizon when the flows are laid out; the rate is nominal and does
    not escalate, matching the fixed-for-20-years EEG contract convention (§8.5).
    """

    rate_per_kwh: float  # euro per kWh of the named carrier, nominal, fixed for the duration
    carrier: EnergyCarrier  # the carrier whose sold (or bought) energy is paid on
    duration_years: int  # payment years, counted from year 1

    SPEC: ClassVar[Tuple[BenefitField, ...]] = (
        BenefitField("rate_per_kwh", "rate_per_kwh", float),
        BenefitField("carrier", "carrier", EnergyCarrier),
        BenefitField("duration_years", "duration_years", int),
    )


class BenefitTypes:
    """The benefit payload type of each kind — the single dispatch table used by loader and
    engine.

    One table serves both directions: :func:`parse_benefit` uses it to pick the payload class for a
    catalog entry, and :meth:`SubsidyScheme.__post_init__` uses it to verify that a scheme built in
    Python pairs its declared kind with the matching payload. Having exactly one mapping is what
    lets the rest of the module narrow a payload by ``isinstance`` without defensive re-checks.
    """

    BY_KIND: Dict[BenefitKind, type] = {
        BenefitKind.SHARE_OF_ELIGIBLE_COST: ShareBenefit,
        BenefitKind.BONUS_SHARE: ShareBenefit,
        BenefitKind.LUMP_SUM: LumpSumBenefit,
        BenefitKind.PER_UNIT: PerUnitBenefit,
        BenefitKind.TAX_CREDIT: TaxCreditBenefit,
        BenefitKind.REDUCED_VAT: ReducedVatBenefit,
        BenefitKind.SOFT_LOAN: LoanTermsBenefit,
        BenefitKind.OPERATIONAL: OperationalBenefit,
    }


def parse_benefit(raw: Dict[str, Any], scheme_id: str) -> Tuple[BenefitKind, Benefit]:
    """Parses a catalog `benefit` object into its kind and typed payload (§5.2).

    The entry point the catalog loader uses for the ``benefit`` block: it reads the ``kind`` tag,
    looks the payload class up in :attr:`BenefitTypes.BY_KIND` and delegates the remaining keys to
    :meth:`Benefit.parse`. The pair it returns is stored on the scheme as-is, so from here on the
    engine works with typed fields and never with raw dictionary lookups (W2.2).

    Args:
        raw: The catalog's ``benefit`` object, including its ``kind`` key.
        scheme_id: Owning scheme, for error messages.

    Returns:
        The declared kind and its parsed, frozen payload.

    Raises:
        SubsidyDataError: If ``kind`` is missing or unknown, or the payload fails to parse.
    """
    if "kind" not in raw:
        raise SubsidyDataError(f"Scheme {scheme_id}: benefit misses the mandatory key 'kind'.")
    try:
        kind = BenefitKind(raw["kind"])
    except ValueError as err:
        raise SubsidyDataError(
            f"Scheme {scheme_id}: unknown benefit kind {raw['kind']!r} "
            f"(known kinds: {[item.value for item in BenefitKind]})."
        ) from err
    payload = {key: value for key, value in raw.items() if key != "kind"}
    return kind, BenefitTypes.BY_KIND[kind].parse(payload, scheme_id, kind)  # type: ignore[attr-defined]


class PayoutKind(str, enum.Enum):
    """How benefits map to timeline entries (§5.2).

    The second half of the benefit model: :class:`BenefitKind` says how much support is computed,
    this says *when and in what form* it arrives, which is what the timeline needs. The separation
    matters because the two are not in bijection — the same 30 % share can be paid as a year-0
    grant in one country and as a multi-year tax deduction in another, and NPV distinguishes them
    sharply. A scheme declares its payout in the catalog's ``payout.kind`` and defaults to
    ``UPFRONT_GRANT``; ``calculators/subsidy_application.py`` is the sole interpreter.

    The mapping it implements: ``UPFRONT_GRANT`` → one negative SUBSIDY entry at year 0;
    ``TAX_CREDIT_SCHEDULE`` → one entry per scheduled year 1..N, truncated at the horizon;
    ``OPERATIONAL`` → one entry per year of the award's duration, valued on the annualized energy;
    ``LOAN_TERMS`` → no entry of its own, it overrides the financing plan (§4.4);
    ``VAT_REDUCTION`` → typed but deliberately unwired, see :class:`ReducedVatBenefit` (§7 B7).
    """

    UPFRONT_GRANT = "UPFRONT_GRANT"
    TAX_CREDIT_SCHEDULE = "TAX_CREDIT_SCHEDULE"
    LOAN_TERMS = "LOAN_TERMS"
    OPERATIONAL = "OPERATIONAL"
    VAT_REDUCTION = "VAT_REDUCTION"


@dataclass
class EligibleCostSpec:
    """Which cost categories count, capped and prorated how (§5.2).

    Defines a scheme's *cost basis* — the euro figure its rate, lump sum or credit is computed on —
    and it is per scheme, not per measure: two schemes stacking on the same heat pump may count
    different categories or cap at different ceilings. Four dials, all data: which timeline
    categories count (investment, planning, removal by default — the removal of the old boiler is
    fundable, its maintenance is not), whether the basis is gross or net of VAT, whether mixed-use
    buildings are prorated down to their residential share, and a per-dwelling-unit ceiling.
    :func:`_eligible_cost_basis` is the one place that applies all four, in that order.
    """

    categories: List[CostCategory] = field(
        default_factory=lambda: [CostCategory.INVESTMENT, CostCategory.PLANNING, CostCategory.REMOVAL]
    )
    cap_per_dwelling_unit_in_euro: List[float] = field(default_factory=list)  # tiered; empty = uncapped
    basis: str = "GROSS"  # GROSS | NET (of VAT)
    proration: str = "NONE"  # NONE | RESIDENTIAL_SHARE

    def cap_for_units(self, dwelling_units: int) -> Optional[float]:
        """Total eligible-cost cap; tier list, last value repeated for further units.

        Programmes cap the eligible cost per dwelling unit in descending tiers — the shipped BEG
        entry is 30 k€ for the first unit, 15 k€ for the next five and 8 k€ for every further one —
        so the building-wide ceiling is the tiered sum, not a flat multiple. Repeating the last
        tier for all remaining units is the standard "and further units" clause of those directives.

        Args:
            dwelling_units: Number of dwelling units in the building; values below 1 count as 1.

        Returns:
            The total cap in euro, or ``None`` when the scheme declares no cap at all — which the
            caller must distinguish from a cap of 0.
        """
        if not self.cap_per_dwelling_unit_in_euro:
            return None
        total = 0.0
        for unit_index in range(max(1, dwelling_units)):
            tier = min(unit_index, len(self.cap_per_dwelling_unit_in_euro) - 1)
            total += self.cap_per_dwelling_unit_in_euro[tier]
        return total


@dataclass
class SubsidyScheme:
    """One scheme of the catalog (§5.2).

    The in-memory image of one funding programme: where and when it applies, what it applies to,
    who qualifies, what it pays, on which cost basis, and how it interacts with other schemes.
    Everything a reviewer needs to check a euro amount against the directive is on this object,
    including the mandatory ``legal_basis`` and ``url`` — a scheme is a STATUTE-kind source in the
    sense of §3.10, which is why an unsourced one is refused at load time.

    Schemes are inert: they are read by :meth:`SubsidyCatalog.candidate_schemes` (jurisdiction and
    validity pre-filter), by :func:`assess_schemes` (eligibility) and by the cumulation solver
    (stacking, exclusions, valuation). Note that ``cumulation_group`` is a free-text *label*, not a
    reference to anything, whereas every id in ``excludes`` must name a real scheme — the data-file
    CI checks the latter.
    """

    id: str
    country: str
    region: Optional[str]  # NUTS code; None = nationwide, matches any applicant region
    valid_from: str  # ISO date; only the year is used for the validity test
    valid_to: Optional[str]  # ISO date, None = open-ended
    legal_basis: str  # mandatory: the directive/statute this encodes
    url: str  # mandatory: where that text can be read
    asset_classes: List[ComponentType]
    measure_kinds: List[str]  # INSTALL | REPLACE
    eligibility: Condition
    benefit_kind: BenefitKind
    benefit: Benefit  # typed, kind-specific parameters (W2.2)
    eligible_cost: EligibleCostSpec
    cumulation_group: Optional[str]  # label; schemes sharing it stack additively (share kinds)
    combined_rate_cap: Optional[float]  # cap on the group's summed rate, e.g. 0.70
    excludes: List[str]  # scheme ids this one cannot be combined with (symmetric in effect)
    payout_kind: PayoutKind
    source_ids: Tuple[str, ...] = ()  # registry ids; mandatory for catalog-loaded schemes (W2.4)
    #: Human-readable name for the report ("BEG EM heat pump — base grant (30 %)", owner decision
    #: Q20). Optional in the schema so a catalog written before Q20 still loads; every renderer
    #: reads :attr:`label`, which falls back to the id, and `validate` warns about a scheme that
    #: ships without one.
    display_name: Optional[str] = None

    @property
    def label(self) -> str:
        """The name a reader sees, with the scheme id as the fallback (Q20).

        The one place the fallback lives, so a catalog that predates ``display_name`` degrades to
        exactly the old behaviour — an id on screen — instead of an empty cell. Renderers put this
        in the visible text and keep :attr:`id` in a tooltip or a trailing parenthesis, because
        the id is what a reviewer needs to grep the catalog and the audit trail with.
        """
        return self.display_name or self.id

    def __post_init__(self) -> None:
        """Keeps `benefit_kind` and the typed payload in sync (W2.2)."""
        expected = BenefitTypes.BY_KIND[self.benefit_kind]
        if not isinstance(self.benefit, expected):
            raise SubsidyDataError(
                f"Scheme {self.id}: benefit kind {self.benefit_kind.value} needs a "
                f"{expected.__name__}, got {type(self.benefit).__name__}."
            )

    def applies_to(self, asset_class: ComponentType, measure_kind: str) -> bool:
        """Whether the scheme covers the measure at all.

        The cheapest of the pre-filters: a scheme only enters the candidate set for an asset class
        it names and a measure kind (INSTALL / REPLACE) it funds. The distinction matters because
        several programmes — the BEG speed bonus is the obvious one — exist precisely to reward
        *replacing* a working fossil system and must not pay for a new-build installation.
        """
        return asset_class in self.asset_classes and measure_kind in self.measure_kinds


def scheme_context_fields(scheme: SubsidyScheme) -> List[str]:
    """Every context field a scheme depends on (§5.7) — the one definition of that set.

    Two sources: the eligibility conditions, and the eligible-cost spec, which needs the
    residential share when it prorates and the dwelling-unit count when it caps per unit.
    Question derivation and question-coverage validation both read this, so a new implied
    dependency is added once.
    """
    names = list(referenced_fields(scheme.eligibility))
    if scheme.eligible_cost.proration == "RESIDENTIAL_SHARE":
        names.append("building.residential_share")
    if scheme.eligible_cost.cap_per_dwelling_unit_in_euro:
        names.append("building.dwelling_units")
    return names


@dataclass
class QuestionEntry:
    """One localized question of the questionnaire catalog (§5.7).

    The presentation half of a context field: how to ask a user for it, in every supported
    language, with the answer options and the help text that explains why it matters. Entries live
    in ``subsidy_catalog/questions_<COUNTRY>.json`` keyed by the context field they fill, and the
    data-file CI (§9.6) fails if any field referenced by a shipped scheme has no entry — that check
    is what keeps the questionnaire complete by construction rather than by review.
    """

    fieldname: str  # the context field this answer fills, e.g. "building.heritage_status"
    answer_kind: str  # BOOLEAN | CHOICE | NUMBER | YEAR | INCOME_BAND
    question: Dict[str, str]  # language code -> question text (de, en)
    options: List[str] = field(default_factory=list)  # CHOICE: the admissible raw values
    option_labels: Dict[str, Dict[str, str]] = field(default_factory=dict)  # language -> value -> label
    help_text: Dict[str, str] = field(default_factory=dict)  # language -> explanatory text
    unit: Optional[str] = None  # NUMBER: the unit to display, e.g. "m2"


@dataclass
class Question:
    """A question to ask, with the schemes that made it necessary ("asked because").

    What :func:`required_questions` returns: a catalog entry plus the derived justification for
    asking it. ``asked_because`` lets a frontend show *why* a question appears and can never go
    stale relative to the catalog, since it is computed from the conditions rather than curated;
    ``pruning_power_in_euro`` is the ordering key that puts the most consequential question first,
    so a user who stops answering early has still answered the questions that mattered most.
    """

    entry: QuestionEntry
    asked_because: List[str] = field(default_factory=list)  # scheme ids
    # Upper bound of support the answer could unlock (ordering heuristic, §5.7):
    pruning_power_in_euro: float = 0.0


class SubsidyCatalog:
    """One country catalog plus the question catalog and source registry.

    The loaded, validated content of ``subsidy_catalog/<COUNTRY>.json`` and its companion
    ``questions_<COUNTRY>.json``: every scheme of that jurisdiction, the localized questionnaire
    entries, the country-level state-aid ceiling and the ``sources.json`` entries the schemes cite.
    It is the sole entry point to catalog data — the engine below never opens a file — and is
    attached to an evaluation through ``EconomicParameters.subsidy_catalog_path``; where no path is
    set, no catalog is loaded at all and the §10.1 legacy flat shim applies instead.

    A catalog can also be built in Python (tests, worked examples), in which case it carries no
    registry and its schemes record ``IN_MEMORY_DEFINITION`` provenance rather than citing sources.
    """

    #: Default on-disk location of the shipped subsidy catalogs.
    DEFAULT_PATH: ClassVar[str] = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "subsidy_catalog"
    )

    def __init__(
        self,
        schemes: List[SubsidyScheme],
        questions: Dict[str, QuestionEntry],
        snapshot_date: Optional[str],
        overall_cap_share: Optional[float],
        base_path: str,
        country: str,
        sources: Optional[Dict[str, SourceEntry]] = None,
    ) -> None:
        """Built by :meth:`load`.

        `sources` are the registry entries the loader resolved this catalog's `source_ids`
        against (W2.4). A catalog built in Python (tests, worked examples) passes none: its
        schemes then mint `IN_MEMORY_DEFINITION` provenance instead of citing registry ids.
        """
        self.schemes = schemes
        self.questions = questions
        self.snapshot_date = snapshot_date
        self.overall_cap_share = overall_cap_share
        self.base_path = base_path
        self.country = country
        #: Resolved `sources.json` entries by id — the resolution `load` used to be thrown away
        #: (W2.4), which is why the evaluator had to fabricate `inline:` ids.
        self.sources: Dict[str, SourceEntry] = dict(sources or {})

    @classmethod
    def load(cls, country: str, base_path: Optional[str] = None) -> "SubsidyCatalog":
        """Loads and validates `<base_path>/<COUNTRY>.json` plus `questions_<COUNTRY>.json`.

        Everything the catalog schema can be checked for statically is checked here, so that a
        malformed programme is a load error naming the scheme rather than a wrong euro amount much
        later: ``legal_basis``, ``url``, ``benefit`` and ``source_ids`` are mandatory, the benefit
        payload is parsed into its typed form (§5.2, W2.2), the eligibility tree is parsed and its
        field names checked against the context vocabulary (§5.3), and every cited source id is
        resolved against ``sources.json`` and kept for the provenance ledger (W2.4). The question
        file is optional at this level — its *completeness* is a CI check (§9.6), not a load error,
        so a catalog under development still loads.

        Args:
            country: ISO country code selecting both file names.
            base_path: Directory holding the catalog; defaults to the shipped
                :attr:`DEFAULT_PATH`.

        Returns:
            The loaded catalog, with resolved source entries attached.

        Raises:
            SubsidyDataError: If no catalog file exists for the country, or any scheme violates the
                schema (missing mandatory field, unknown benefit kind or asset class, unparsable
                condition, no source ids).
        """
        base = base_path or SubsidyCatalog.DEFAULT_PATH
        catalog_path = os.path.join(base, f"{country}.json")
        if not os.path.isfile(catalog_path):
            raise SubsidyDataError(f"No subsidy catalog for country {country!r} at {catalog_path}.")
        with open(catalog_path, encoding="utf-8") as file:
            raw = json.load(file)
        sources_path = os.path.join(base, "sources.json")
        registry = SourceRegistry.load(sources_path) if os.path.isfile(sources_path) else None
        resolved_sources: Dict[str, SourceEntry] = {}
        schemes = []
        for item in raw.get("schemes", []):
            scheme_id = item.get("id", "<missing id>")
            for mandatory in ("legal_basis", "url"):
                if not item.get(mandatory):
                    raise SubsidyDataError(f"Scheme {scheme_id}: mandatory field {mandatory!r} missing (§5.2).")
            if "benefit" not in item:
                raise SubsidyDataError(f"Scheme {scheme_id}: mandatory field 'benefit' missing (§5.2).")
            benefit_kind, benefit = parse_benefit(dict(item["benefit"]), scheme_id)
            eligible_raw = item.get("eligible_cost", {})
            cumulation = item.get("cumulation", {})
            source_ids = tuple(item.get("source_ids", []))
            if not source_ids:
                raise SubsidyDataError(
                    f"Scheme {scheme_id}: source_ids are mandatory for catalog schemes — an "
                    "unsourced scheme is not admissible (§3.10, W2.4)."
                )
            if registry is not None:
                for entry in registry.resolve(source_ids, f"scheme {scheme_id}"):
                    resolved_sources[entry.source_id] = entry
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
                    eligibility=parse_condition(item.get("eligibility", {"all": []}), scheme_id),
                    benefit_kind=benefit_kind,
                    benefit=benefit,
                    eligible_cost=EligibleCostSpec(
                        categories=[
                            CostCategory(cat)
                            for cat in eligible_raw.get("categories", ["INVESTMENT", "PLANNING", "REMOVAL"])
                        ],
                        cap_per_dwelling_unit_in_euro=list(eligible_raw.get("cap_per_dwelling_unit_in_euro", [])),
                        basis=eligible_raw.get("basis", "GROSS"),
                        proration=eligible_raw.get("proration", "NONE"),
                    ),
                    cumulation_group=cumulation.get("group"),
                    combined_rate_cap=cumulation.get("combined_rate_cap"),
                    excludes=list(cumulation.get("excludes", [])),
                    payout_kind=PayoutKind(item.get("payout", {}).get("kind", "UPFRONT_GRANT")),
                    source_ids=source_ids,
                    display_name=item.get("display_name") or None,
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
            sources=resolved_sources,
        )

    @classmethod
    def resolve_base_path(cls, configured_path: str) -> str:
        """Turns a configured catalog path into a directory that exists, or says where it looked.

        A `subsidy_catalog_path` is written into `EconomicParameters` by a system setup, a scenario
        file or a RenoVisor request, and is then read back by a CLI invocation whose working
        directory is nobody's business — so resolving a relative path against the current directory
        alone makes the same parameter file work from the repository root and fail from anywhere
        else. Three roots are tried, in order: the current working directory (an absolute path is
        used as given), the repository/installation root that contains the `hisim` package, and the
        package's own data directory, so that both `hisim/subsidy_catalog` and `subsidy_catalog`
        resolve to the shipped catalog wherever the command runs.

        Args:
            configured_path: The non-empty path a parameter set names.

        Returns:
            An existing directory to load the catalog from.

        Raises:
            SubsidyDataError: If no candidate exists. Named catalog data that cannot be found is a
                fail-fast condition (D25): the alternative is a full result priced by the §10.1
                flat shim under a catalog the caller believed was active.
        """
        package_directory = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        install_root = os.path.dirname(package_directory)
        candidates = [os.path.abspath(configured_path)]
        if not os.path.isabs(configured_path):
            candidates.append(os.path.abspath(os.path.join(install_root, configured_path)))
            candidates.append(os.path.abspath(os.path.join(package_directory, configured_path)))
        for candidate in candidates:
            if os.path.isdir(candidate):
                return candidate
        raise SubsidyDataError(
            f"Configured subsidy catalog path {configured_path!r} does not resolve to a directory "
            f"(tried: {', '.join(candidates)}). Fix the path or remove `subsidy_catalog_path` from "
            "the parameters — a catalog that was named but cannot be read is never replaced by the "
            "§10.1 legacy flat shim."
        )

    @classmethod
    def load_configured(
        cls, country: str, configured_path: Optional[str], override_path: Optional[str] = None
    ) -> Optional["SubsidyCatalog"]:
        """The catalog a parameter set asks for: loaded, or None when it asks for none.

        The single entry point every caller that holds `EconomicParameters` uses — the CLI's four
        subcommands and the postprocessing bridge — so the two cases stay apart everywhere. Naming
        no catalog is a legitimate parameter set: the country may have none yet (Ireland), and the
        §10.1 legacy flat shim then prices the support from the device entries. Naming one that
        cannot be read is not, and raises rather than falling through to that shim.

        Args:
            country: ISO country code selecting the catalog file.
            configured_path: `EconomicParameters.subsidy_catalog_path`, possibly None.
            override_path: A `--subsidy-catalog` flag, which wins over the parameters when given.

        Returns:
            The loaded catalog, or None when neither a path nor an override was given.

        Raises:
            SubsidyDataError: If a path was given but does not resolve, or the catalog it names is
                missing or malformed.
        """
        path = override_path or configured_path
        if not path:
            return None
        return cls.load(country, cls.resolve_base_path(path))

    # ------------------------------------------------------------------ provenance (§3.10, W2.4)

    def scheme_by_id(self, scheme_id: str) -> Optional[SubsidyScheme]:
        """The scheme with that id, or None (awards carry ids, not scheme objects).

        A :class:`SubsidyAward` deliberately references its scheme by id so that decisions stay
        serializable into the audit trail; this is how a consumer gets back to the scheme's legal
        basis, most importantly ``calculators/subsidy_application.py`` when it mints an award's
        provenance record.
        """
        return next((scheme for scheme in self.schemes if scheme.id == scheme_id), None)

    def resolved_sources(self, scheme: SubsidyScheme) -> List[SourceEntry]:
        """Registry entries backing one scheme — empty for in-memory catalogs.

        Returns the ``sources.json`` entries this catalog resolved for the scheme's ``source_ids``
        at load time, skipping any id the registry did not contain. An empty result is meaningful
        rather than an error: it identifies a scheme defined in Python, which has no registry
        behind it and is recorded as :attr:`ParameterOrigin.IN_MEMORY_DEFINITION`.
        """
        return [self.sources[source_id] for source_id in scheme.source_ids if source_id in self.sources]

    def source_resolver(self) -> Dict[str, ResolvedSource]:
        """This catalog's registry entries in the report representation (§3.10).

        Hands the subsidy registry to the result object, which merges it with the cost database's
        registry — the two id spaces are disjoint — so that a subsidy flow explained through the
        provenance ledger resolves to a real citation instead of a bare id. Consumed by the
        "sources used" table of the report and by the ``explain`` CLI.
        """
        return {source_id: entry.to_resolved() for source_id, entry in self.sources.items()}

    def provenance_for_scheme(
        self, scheme: SubsidyScheme, ledger: ProvenanceLedger, value: Any
    ) -> int:
        """Records the ledger entry backing one award of `scheme` (W2.4).

        A catalog-loaded scheme cites the registry ids its `sources.json` resolution produced at
        load time, so `explain` reaches the legal text a subsidy came from; the scheme's own
        `legal_basis` and `url` — which are scheme *fields*, not registry entries — ride along in
        `detail`. A scheme defined in Python (tests, worked examples) has no registry behind it
        and is recorded as :attr:`ParameterOrigin.IN_MEMORY_DEFINITION`, which is honest about
        having no source, instead of the `inline:` pseudo-ids this replaced.
        """
        detail = f"{scheme.legal_basis} <{scheme.url}>"
        if scheme.source_ids:
            return ledger.record(
                ParameterProvenance(
                    parameter=f"subsidy.{scheme.id}",
                    value=value,
                    origin=ParameterOrigin.DATABASE_ENTRY,
                    data_file=f"subsidy_catalog/{self.country}.json#{scheme.id}",
                    source_ids=scheme.source_ids,
                    detail=detail,
                )
            )
        return ledger.record(
            ParameterProvenance(
                parameter=f"subsidy.{scheme.id}",
                value=value,
                origin=ParameterOrigin.IN_MEMORY_DEFINITION,
                data_file=None,
                source_ids=(),
                detail=detail,
            )
        )

    def candidate_schemes(
        self, asset_class: ComponentType, measure_kind: str, region: Optional[str], year: int
    ) -> List[SubsidyScheme]:
        """Pre-filter by jurisdiction, asset class and validity (§5.7).

        The first stage of every subsidy computation: it narrows the whole catalog to the schemes
        that *could* apply to one measure before any context is looked at, which keeps the
        exponential cumulation solver working on a handful of schemes and keeps the question
        derivation from asking about programmes the case can never reach. A scheme with no region
        is nationwide and matches any applicant; a regional scheme matches only its own region, and
        an applicant who did not state a region is offered regional schemes too (the eligibility
        conditions can still reject them). Validity is tested on the *year* parsed from the ISO
        dates against the price basis year, i.e. against the economic "today" rather than the
        possibly historical weather year of the simulation.

        Args:
            asset_class: The measure's asset class.
            measure_kind: "INSTALL" or "REPLACE".
            region: The applicant's region key, or None if unstated.
            year: The year scheme validity is tested against.

        Returns:
            The candidate schemes, in catalog file order — which is also the order the cumulation
            solver enumerates them in and therefore its tie-break order.
        """
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
    """Resolves a catalog asset-class string to its `ComponentType`, by enum name or value.

    Accepting both spellings keeps catalog files readable while tolerating the enum's value
    strings; anything else is a data error rather than a silently unfunded asset class, which is
    why it raises instead of returning None (a scheme that quietly applies to nothing would look
    exactly like a scheme whose conditions failed).
    """
    for member in ComponentType:
        if name in (member.name, member.value):
            return member
    raise SubsidyDataError(f"Scheme {scheme_id}: unknown asset class {name!r}.")


# =========================================================================== engine
# Everything below consumes the catalog data above: the applicant/building context conditions
# resolve against, the condition evaluator, the eligibility assessment and the cumulation
# solver. Nothing above this line reads a context or evaluates anything (§2.5: the data half
# becomes `economics/data/`, this half `economics/engine/`; the cut is prepared, the physical
# move is deliberately left to the package split so imports churn once, not twice).


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
        """Resolves a condition field; returns (known, value). Unknown fields raise, unanswered
        (None) values return (False, None) — the tri-state input (§5.7).

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


def evaluate_condition(
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

    ``display_name`` is carried on the award rather than looked up when a report is rendered
    (Q20): a report is regularly built from a serialized result, in a process that never loaded a
    catalog, so the friendly name has to travel with the award or it is not available where it is
    read. It is empty exactly when the scheme had none, and :attr:`label` then falls back to the
    id.
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
    #: The scheme's friendly name at the time of the award (Q20); empty when it had none.
    display_name: str = ""
    # --- the arithmetic behind the amount (owner decision Q26 F8, rule 2.9) ---------------
    #: The rate this award was computed at, as a fraction, for the two percentage forms (a share
    #: of eligible cost and a tax credit); None for lump sums, per-unit amounts, loan terms and
    #: VAT reductions, which state their own terms instead. It is the rate **after** a cumulation
    #: group's combined-rate cap scaled it down, i.e. the rate that actually produced the euros.
    benefit_rate: Optional[float] = None
    #: The same rate before that scaling, when the group's combined-rate cap bit; None otherwise.
    #: The pair is what lets the report say "20 % capped to 17.5 %" instead of showing a rate the
    #: catalog does not contain.
    benefit_rate_before_group_cap: Optional[float] = None
    #: The eligible-cost basis the rate was applied to, after proration and after the
    #: per-dwelling-unit cap — the second factor of `rate x basis = amount`.
    eligible_basis_in_euro: Optional[UncertainValue] = None
    #: The per-dwelling-unit eligible-cost ceiling that applied, in euro, or None where the scheme
    #: declares none. With `caps_binding_per_slot` this is what turns "capped" into "capped at X".
    eligible_basis_cap_in_euro: Optional[float] = None

    @property
    def label(self) -> str:
        """The award's name for a reader — the scheme's display name, or its id (Q20)."""
        return self.display_name or self.scheme_id


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
        their min/avg/max band.
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
                    # Q26 F8: the arithmetic behind the amount, so a report rendered from a
                    # stored result can show `rate x basis = amount` and the cap verdict.
                    "benefit_rate": award.benefit_rate,
                    "benefit_rate_before_group_cap": award.benefit_rate_before_group_cap,
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
        }


def _eligible_cost_basis(
    scheme: SubsidyScheme, measure: MeasureForSubsidy, context: SubsidyContext
) -> Tuple[UncertainValue, Dict[str, bool]]:
    """Eligible cost per slot, with per-slot cap-binding flags (§3.9, §5.4).

    Turns the measure's gross year-0 cost into the basis *this* scheme computes on, applying the
    four dials of :class:`EligibleCostSpec` in a fixed order: sum the counted categories, strip VAT
    if the scheme's basis is NET, prorate to the residential share for a residential-only programme
    in a mixed-use building, then clamp to the per-dwelling-unit cap. The order matters — capping
    last means the ceiling is compared against the already prorated, already net figure, which is
    how the directives read.

    Both proration and capping degrade gracefully in the direction of *not* inventing support: an
    unanswered residential share leaves the basis unprorated, and a scheme with no cap list is
    uncapped. The cap is applied per slot (`clamp_upper`), so it can bind in the HIGH-cost world
    and not in LOW; the returned flags record exactly that and end up in the audit trail and in the
    input-audit table, because "the cap bound" is usually the single most important thing to know
    about a subsidy result.

    Args:
        scheme: The scheme whose eligible-cost rules apply.
        measure: The measure, supplying the year-0 gross cost per category and the VAT rate.
        context: The building context, read for the residential share and the dwelling-unit count.

    Returns:
        The eligible-cost band in euro, and a ``{"low"/"average"/"high": bool}`` map saying in which
        slots the cap was binding (computed *before* clamping).
    """
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


def _share_benefit(scheme: SubsidyScheme) -> ShareBenefit:
    """The share payload of a SHARE_OF_ELIGIBLE_COST / BONUS_SHARE scheme.

    The pairing of kind and payload type is enforced by `SubsidyScheme.__post_init__`, so this
    is a narrowing helper for the type checker, not a runtime check.
    """
    assert isinstance(scheme.benefit, ShareBenefit)
    return scheme.benefit


#: The three cap ratios of :func:`_overall_cap_ratios`, in slot order LOW, AVERAGE, HIGH.
CapRatios = Tuple[float, float, float]


def _overall_cap_ratios(total_upfront: UncertainValue, cap: UncertainValue) -> CapRatios:
    """How much of the upfront support survives the overall cap, **per slot** (§5.4, §7 B12).

    Each slot is a coherent world: in the LOW-cost world both the support and the state-aid cap
    (a share of that world's gross cost) are smaller, so the binding ratio is a per-slot figure.
    Scaling every slot by the AVERAGE-slot ratio — what this did before — let the LOW/HIGH-world
    support exceed the same world's cap, and with wide cost bands even its gross cost.
    """

    def ratio(total_slot: float, cap_slot: float) -> float:
        if total_slot <= 0.0:
            return 1.0
        return min(1.0, max(0.0, cap_slot) / total_slot)

    return (
        ratio(total_upfront.minimum, cap.minimum),
        ratio(total_upfront.average, cap.average),
        ratio(total_upfront.maximum, cap.maximum),
    )


def _scaled_to_cap(amount: UncertainValue, ratios: CapRatios) -> UncertainValue:
    """Applies the per-slot cap ratios to one award, keeping the band ordered (§3.9, §7 B12).

    Per-slot ratios are *not* monotone across the slots: the cap grows with the gross cost while
    the support grows with the eligible-cost bases, and a scheme paying a statutory lump sum does
    not grow at all. Where the ratio falls from one slot to the next, a plain slot-wise product
    would order an award's band the wrong way round (an exact lump sum keeping its full amount in
    LOW but being cut in AVERAGE), which is not representable as an `UncertainValue`.

    The award is therefore capped from the HIGH slot downwards: each slot takes the smaller of
    its own capped value and the slot above it. That keeps three properties at once —

    * ``minimum <= average <= maximum`` by construction (no reliance on snapping tolerance);
    * per-slot support never exceeds the per-slot cap, because every slot is at most its own
      ``amount_slot * ratio_slot`` and those sum to at most the slot's cap;
    * no award exceeds its own eligible basis, because the ratios are at most 1 and the
      downward clamp only ever lowers a slot.

    The price is a slot that gives up support it could have received in isolation (the LOW world
    in the example above), which is the conservative direction: the cap is never overrun. Where
    the ratios *are* monotone — always, when the cost bands are degenerate, as in every shipped
    catalog and worked example — the result is exactly the plain per-slot product.
    """
    low_ratio, average_ratio, high_ratio = ratios
    maximum = amount.maximum * high_ratio
    average = min(amount.average * average_ratio, maximum)
    minimum = min(amount.minimum * low_ratio, average)
    return UncertainValue(average=average, minimum=minimum, maximum=maximum)


def _combination_awards(
    schemes: List[SubsidyScheme],
    measure: MeasureForSubsidy,
    context: SubsidyContext,
    overall_cap_share: Optional[float],
) -> List[SubsidyAward]:
    """Values one admissible combination in all three slots (§5.4).

    The valuation kernel: given a set of schemes that may legally be combined, it produces one
    :class:`SubsidyAward` per scheme with the amounts each would actually pay. It is called once
    per enumerated subset by the solver, so it must be free of side effects on its inputs and is
    the only place the cumulation *rules* — group stacking, the combined rate cap, the state-aid
    ceiling — turn into euros.

    Three stages, in this order. **Share kinds** (base rates and bonuses) are grouped by
    ``cumulation_group``: their rates are summed, the sum is limited by the smallest
    ``combined_rate_cap`` declared in the group (the BEG's 70 %), and the limit is distributed back
    over the members *proportionally* — so a capped stack reports each scheme's pro-rata share
    rather than dropping the last bonus, which is what makes the composition chart add up. Note
    that grouping is by group label only: schemes with no group share the ``None`` group and stack
    together. **Non-share kinds** are valued individually against their own eligible-cost basis —
    lump sums and per-unit amounts are clamped to it, tax credits are spread over their schedule,
    loan terms and reduced VAT carry parameters rather than amounts. **The overall cap** finally
    bounds the total *upfront* support (grants and clamped lump sums; not tax-credit schedules, not
    loans) by the country-level state-aid share of the gross cost, per slot.

    Args:
        schemes: One admissible combination — assumed already checked against ``excludes``.
        measure: The measure being funded, supplying the cost basis and its size.
        context: The building context the eligible-cost rules read.
        overall_cap_share: The catalog's state-aid ceiling as a share of gross cost, or ``None``
            for no ceiling (the shipped DE catalog declares none).

    Returns:
        One award per scheme, amounts in nominal year-0 euro per slot. Awards may legitimately be
        zero-valued (a loan or VAT award carries terms only).
    """
    awards: List[SubsidyAward] = []
    # Share-based schemes stack additively per cumulation group, capped by combined_rate_cap.
    share_groups: Dict[Optional[str], List[SubsidyScheme]] = {}
    for scheme in schemes:
        if scheme.benefit_kind in (BenefitKind.SHARE_OF_ELIGIBLE_COST, BenefitKind.BONUS_SHARE):
            share_groups.setdefault(scheme.cumulation_group, []).append(scheme)
    for _group, group_schemes in share_groups.items():
        rates = [_share_benefit(scheme).rate for scheme in group_schemes]
        total_rate = sum(rates)
        rate_caps = [scheme.combined_rate_cap for scheme in group_schemes if scheme.combined_rate_cap is not None]
        capped_rate = min([total_rate] + rate_caps)
        scale_down = capped_rate / total_rate if total_rate > 0 else 0.0
        for scheme, scheme_rate in zip(group_schemes, rates):
            basis, binding = _eligible_cost_basis(scheme, measure, context)
            rate = scheme_rate * scale_down
            awards.append(
                SubsidyAward(
                    scheme_id=scheme.id,
                    payout_kind=scheme.payout_kind,
                    upfront_amount=basis.scale(rate),
                    caps_binding_per_slot=binding,
                    # Q26 F8: both factors of the multiplication, and the pre-cap rate when the
                    # group's combined-rate cap scaled this scheme down.
                    benefit_rate=rate,
                    benefit_rate_before_group_cap=scheme_rate if scale_down < 1.0 else None,
                    eligible_basis_in_euro=basis,
                    eligible_basis_cap_in_euro=scheme.eligible_cost.cap_for_units(
                        context.building.dwelling_units
                    ),
                )
            )
    for scheme in schemes:
        if scheme.benefit_kind in (BenefitKind.SHARE_OF_ELIGIBLE_COST, BenefitKind.BONUS_SHARE):
            continue
        basis, binding = _eligible_cost_basis(scheme, measure, context)
        basis_cap = scheme.eligible_cost.cap_for_units(context.building.dwelling_units)
        benefit = scheme.benefit
        if isinstance(benefit, LumpSumBenefit):
            # A grant never exceeds the cost it funds, so the lump sum is clamped to the eligible
            # basis — unless the scheme declares no eligible-cost categories at all, which is the
            # explicit way of saying "this amount is unconditional" (an empty basis would otherwise
            # clamp every lump sum to zero).
            amount = UncertainValue.exact(benefit.amount)
            awards.append(
                SubsidyAward(
                    scheme_id=scheme.id,
                    payout_kind=scheme.payout_kind,
                    upfront_amount=amount.clamp_upper(basis) if scheme.eligible_cost.categories else amount,
                    caps_binding_per_slot=binding,
                    eligible_basis_in_euro=basis if scheme.eligible_cost.categories else None,
                    eligible_basis_cap_in_euro=basis_cap,
                )
            )
        elif isinstance(benefit, PerUnitBenefit):
            amount = UncertainValue.exact(benefit.amount * measure.facts.size)
            awards.append(
                SubsidyAward(
                    scheme_id=scheme.id,
                    payout_kind=scheme.payout_kind,
                    upfront_amount=amount.clamp_upper(basis),
                    caps_binding_per_slot=binding,
                    eligible_basis_in_euro=basis,
                    eligible_basis_cap_in_euro=basis_cap,
                )
            )
        elif isinstance(benefit, TaxCreditBenefit):
            total = basis.scale(benefit.rate)
            schedule = [total.scale(share) for share in benefit.schedule_shares()]
            awards.append(
                SubsidyAward(
                    scheme_id=scheme.id,
                    payout_kind=PayoutKind.TAX_CREDIT_SCHEDULE,
                    schedule_amounts=schedule,
                    caps_binding_per_slot=binding,
                    # Q26 F8: a tax credit is a percentage form like a share award, so it states
                    # the same multiplication; the instalment split is the payout note's job.
                    benefit_rate=benefit.rate,
                    eligible_basis_in_euro=basis,
                    eligible_basis_cap_in_euro=basis_cap,
                )
            )
        elif isinstance(benefit, ReducedVatBenefit):
            # §7 B7: no consumer reads this award — typed, but deliberately left unwired.
            awards.append(
                SubsidyAward(
                    scheme_id=scheme.id,
                    payout_kind=PayoutKind.VAT_REDUCTION,
                    reduced_vat_rate=benefit.vat_rate,
                )
            )
        elif isinstance(benefit, LoanTermsBenefit):
            awards.append(
                SubsidyAward(
                    scheme_id=scheme.id,
                    payout_kind=PayoutKind.LOAN_TERMS,
                    loan_interest_rate=benefit.interest_rate,
                    loan_term_in_years=benefit.term,
                    loan_repayment_grant_share=benefit.repayment_grant_rate,
                )
            )
        elif isinstance(benefit, OperationalBenefit):
            awards.append(
                SubsidyAward(
                    scheme_id=scheme.id,
                    payout_kind=PayoutKind.OPERATIONAL,
                    operational_rate_per_kwh=benefit.rate_per_kwh,
                    operational_carrier=benefit.carrier,
                    operational_duration_years=benefit.duration_years,
                )
            )
    # EU state-aid overall cap: bounds total *upfront* support per measure (§5.4), per slot.
    if overall_cap_share is not None:
        gross = UncertainValue.sum(measure.cost_by_category.values())
        cap = gross.scale(overall_cap_share)
        total_upfront = UncertainValue.sum(award.upfront_amount for award in awards)
        ratios = _overall_cap_ratios(total_upfront, cap)
        if any(ratio < 1.0 for ratio in ratios):
            for award in awards:
                award.upfront_amount = _scaled_to_cap(award.upfront_amount, ratios)
    # Q20: the friendly name travels with the award, because the report that shows it is often
    # built from a serialized result in a process that never loaded a catalog. Attached in one
    # pass rather than at the seven construction sites above, so a new benefit kind cannot forget
    # it.
    names = {scheme.id: scheme.display_name for scheme in schemes}
    for award in awards:
        award.display_name = names.get(award.scheme_id) or ""
    return awards


def _support_value(
    awards: List[SubsidyAward],
    measure: MeasureForSubsidy,
    discount: Callable[[int], float],
    slot_getter: Callable[[UncertainValue], float],
) -> float:
    """Discounted value of a combination's support in one slot (solver objective).

    The scalar the cumulation solver maximizes: the present value, to the applicant, of everything a
    combination pays — grants at year 0 undiscounted, tax-credit installments discounted by their
    year, operational per-kWh payments discounted over their duration, plus a soft loan's repayment
    grant. Discounting is what makes the comparison meaningful at all, since §5.1's mechanisms pay
    at very different times: a 20 % credit over ten years is not worth a 20 % grant today.

    Two details a reviewer should note. Operational support falls back from *sold* to *bought*
    energy for the carrier, so a heat-generation premium on consumed energy is valued even though
    the field is named after feed-in. And the repayment grant is valued on the measure's **gross**
    cost here while ``calculators/financing_application.py`` applies the same share to the loan
    principal — §7 B3, preserved unfixed and currently masked by the shipped KfW rate of 0.0.

    Args:
        awards: The valued awards of one combination.
        measure: The measure, read for the annual energy an OPERATIONAL award is paid on and for
            the gross cost a repayment grant is valued on.
        discount: Year → discount factor, supplied by the caller (``EconomicParameters``).
        slot_getter: Picks the slot to value in; see :func:`solve_cumulation` for why the LOW slot
            reads the band's *maximum*.

    Returns:
        The discounted support in euro — a positive number, larger is better for the applicant.
    """
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
            gross = slot_getter(UncertainValue.sum(measure.cost_by_category.values()))
            value += gross * award.loan_repayment_grant_share
    return value


def _check_cumulation_size(count: int, what: str) -> None:
    """Guards the 2^n subset enumerations of the cumulation solver.

    Args:
        count: Number of schemes about to be enumerated.
        what: Human-readable name of that set, used in the error message.

    Raises:
        SubsidyDataError: If count exceeds MAX_CUMULATION_SCHEMES.
    """
    if count > CumulationLimits.MAX_CUMULATION_SCHEMES:
        raise SubsidyDataError(
            f"{count} {what} exceeds the cumulation solver limit of "
            f"{CumulationLimits.MAX_CUMULATION_SCHEMES}. "
            "The solver enumerates every subset of them, so a set this large would not finish "
            "in reasonable time. Narrow the catalog's candidate schemes for this measure "
            "(applies_to_asset_classes, measure kinds, region, validity years) or split it."
        )


def solve_cumulation(
    catalog: SubsidyCatalog,
    measure: MeasureForSubsidy,
    context: SubsidyContext,
    year: int,
    discount: Callable[[int], float],
    admits: Optional[Callable[[str], bool]] = None,
) -> SubsidyDecision:
    """Enumerates admissible combinations of ELIGIBLE schemes and picks the best (§5.4).

    The decision is made on the AVERAGE slot; the chosen combination is then valued in all
    three slots. UNDETERMINED schemes are excluded but reported with the optimistic upper
    bound they could unlock (§5.7).

    `admits` restricts the candidate set *before* the optimization (§5.5 subsidy modes; §7 B5).
    It is a plain predicate on scheme ids rather than a `SubsidyMode` so this module keeps no
    dependency on the perspective types. Filtering afterwards — what the evaluator used to do —
    left ONLY/EXCLUDE perspectives with the remainder of a combination that was optimal for a
    different (unrestricted) candidate set, which is not the best admissible combination.

    Both enumerations are exponential in the number of schemes and are therefore capped at
    MAX_CUMULATION_SCHEMES: more than that many eligible schemes (or, for the optimistic
    bound, eligible plus undetermined ones) is treated as a catalog defect rather than
    silently truncated, because truncating would understate the support.

    **What is optimized, over what, and how ties break.** The objective is
    :func:`_support_value` — the discounted euro value of a combination to the applicant, maximized
    (the applicant is assumed to claim what is available to them, §5.4). The candidate set is the
    power set of the ELIGIBLE schemes for this one measure: all 2^n subsets are enumerated in
    increasing bitmask order, the ones violating an ``excludes`` relation are skipped, and each
    survivor is fully valued through :func:`_combination_awards` — no greedy or incremental
    shortcut, because caps and exclusions make the objective non-additive. Note the empty subset is
    included, so "no subsidy at all" is always an admissible answer. A candidate replaces the
    incumbent only if it beats it by more than 1e-9 euro, which makes the tie-break *first one
    wins* in enumeration order: since the bits index the eligible list, which is in catalog file
    order, an exact tie is resolved toward the combination using the fewer / earlier-listed
    schemes. That is what makes the result reproducible across runs rather than dependent on set
    iteration order.

    **The per-slot report.** The same enumeration also tracks the best combination in the LOW and
    HIGH slots, and ``other_slot_optimal_combination`` names it where it differs from the chosen
    one — the honest disclosure that the plan is optimal for the average world only. The slot
    getters map LOW to the band's *maximum* and HIGH to its *minimum*, matching the mirroring that
    turns support into a revenue-type cash flow (`as_revenue`): in the optimistic slot the most
    support arrives.

    Args:
        catalog: The country catalog.
        measure: The single measure being funded; the solver has no cross-measure view.
        context: The applicant/building answers.
        year: The year scheme validity is tested against (the price basis year in production).
        discount: Year → discount factor used to compare payout timings.
        admits: Optional scheme-id predicate implementing the perspective's subsidy mode.

    Returns:
        The full :class:`SubsidyDecision`: the chosen combination's awards, plus rejected,
        undetermined, the optimistic bound and the per-slot alternatives.

    Raises:
        SubsidyDataError: If either scheme set exceeds MAX_CUMULATION_SCHEMES.
    """
    assessments = assess_schemes(catalog, measure, context, year, admits)
    eligible = [assessment.scheme for assessment in assessments if assessment.status == EligibilityStatus.ELIGIBLE]
    decision = SubsidyDecision(measure_subject=measure.subject)
    for assessment in assessments:
        if assessment.status == EligibilityStatus.INELIGIBLE:
            decision.rejected.append({
                "scheme_id": assessment.scheme.id,
                "display_name": assessment.scheme.label,
                "reason": assessment.rejected_reason,
            })
        elif assessment.status == EligibilityStatus.UNDETERMINED:
            decision.undetermined.append({
                "scheme_id": assessment.scheme.id,
                "display_name": assessment.scheme.label,
                "missing_fields": assessment.missing_fields,
            })

    def admissible(combination: List[SubsidyScheme]) -> bool:
        """Whether the combination violates no `excludes` relation.

        Declaring the exclusion on one side is enough: the check asks every member whether any
        other member is on its exclusion list, so `excludes` acts symmetrically even though the
        catalog states it once (the DE catalog states it on both sides anyway).
        """
        ids = {scheme.id for scheme in combination}
        for scheme in combination:
            if ids & set(scheme.excludes):
                return False
        return True

    # Enumerate subsets (scheme sets are small, typically < 10 per measure).
    _check_cumulation_size(len(eligible), "eligible schemes")
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
    # Re-solving over eligible + undetermined (rather than adding the undetermined schemes' values)
    # is necessary because exclusions and caps make the best combination change, not just grow. The
    # reported figure is the *increment* over the chosen combination on the AVERAGE slot — "answering
    # these questions could unlock up to X on top of what you already get" (§5.7) — floored at 0,
    # since an undetermined scheme can never make the applicant worse off.
    if decision.undetermined:
        undetermined_schemes = [
            assessment.scheme for assessment in assessments if assessment.status == EligibilityStatus.UNDETERMINED
        ]
        optimistic = eligible + undetermined_schemes
        best_optimistic = 0.0
        opt_count = len(optimistic)
        _check_cumulation_size(opt_count, "eligible and undetermined schemes")
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
            gross = UncertainValue.sum(measure.cost_by_category.values()).average
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
