"""Subsidy catalog schema and parsing (cost_spec.md §5.1-§5.2, §5.6).

Turns a `subsidy_catalog/<COUNTRY>.json` file into typed objects: eligibility conditions, the benefit kinds,
eligible-cost specs, `SubsidyScheme`, questionnaire entries and the `SubsidyCatalog` loader. Nothing is evaluated here;
that is `assessment` and `solver`.
"""

from __future__ import annotations

import enum
import json
import math
import os
from dataclasses import dataclass, field
from typing import (
    Any,
    Callable,
    ClassVar,
    Dict,
    Iterable,
    List,
    Optional,
    Set,
    Tuple,
)

from hisim.economics.carriers import EnergyCarrier
from hisim.economics.catalog_entries import CostDataError
from hisim.economics.database import SourceEntry, SourceRegistry
from hisim.economics.facts import ComponentCostFacts
from hisim.economics.provenance import (
    ParameterOrigin,
    ParameterProvenance,
    ProvenanceLedger,
    ResolvedSource,
)
from hisim.economics.timeline import CostCategory
from hisim.loadtypes import ComponentType, Units

from hisim.economics.subsidies.context import SubsidyContextFields, SubsidyDataError


@dataclass(frozen=True)
class Condition:
    """One node of a scheme's eligibility condition tree (§5.3), as plain data.

    A node is either a combinator (``all``, ``any``, ``not``) over children or a leaf comparing one context field with
    a value. :func:`parse_condition` builds it and :func:`evaluate_condition` evaluates it.
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
    """Parse and validate one condition node of the catalog JSON, recursively.

    Turns ``{"all": [...]}``, ``{"any": [...]}``, ``{"not": {...}}`` and ``{"field": ..., "op": ..., "value": ...}``
    into a :class:`Condition` tree. The operator must be one of the nine allowed ones, and the field must be in the
    known context vocabulary or be a ``measure.*`` path (those address arbitrary ``technical_attributes`` keys and are
    checked when a measure is at hand). Checking at load time means a mistyped catalog fails with the scheme id instead
    of quietly never matching.

    Args:
        raw: One condition node from the catalog JSON.
        scheme_id: Owning scheme, used in error messages.

    Returns:
        The complete parsed tree.

    Raises:
        SubsidyDataError: On an unknown operator or field name, a value on an asset-class field that is not a
            ``ComponentType`` value, or a node that is neither a combinator nor a leaf.
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
        if operator != "exists" and _is_asset_class_field(fieldname):
            _check_asset_class_values(raw.get("value"), fieldname, scheme_id)
        return Condition(kind="leaf", fieldname=fieldname, op=operator, value=raw.get("value"))
    raise SubsidyDataError(f"Scheme {scheme_id}: condition node {raw!r} is neither all/any/not nor a leaf.")


def _is_asset_class_field(fieldname: str) -> bool:
    """Whether a condition field holds asset classes, compared as ``ComponentType`` values.

    The rule is the name: every such field ends in ``asset_class`` or ``asset_classes`` (``measure.asset_class``,
    ``package.installed_asset_classes``, ...), so a new field following the convention is checked without being listed.
    """
    last = fieldname.rsplit(".", 1)[-1]
    return last.endswith("asset_class") or last.endswith("asset_classes")


def _check_asset_class_values(value: Any, fieldname: str, scheme_id: str) -> None:
    """Refuse a condition value on an asset-class field that is not a ``ComponentType`` value.

    These fields compare ``ComponentType`` values ("HeatPump", "OilHeater"), so a misspelled class would never match,
    or under ``!=`` or ``not`` would always match. A list value (``in``) is checked item by item.

    Raises:
        SubsidyDataError: Naming the scheme, the field and every value that is not a class.
    """
    known = {member.value for member in ComponentType}
    values = value if isinstance(value, list) else [value]
    unknown = [item for item in values if not isinstance(item, str) or item not in known]
    if unknown:
        raise SubsidyDataError(
            f"Scheme {scheme_id}: condition on {fieldname!r} compares against {unknown!r}, which "
            "is not a ComponentType value; the field holds asset classes such as "
            f"{ComponentType.HEAT_PUMP.value!r}."
        )


def referenced_fields(condition: Condition) -> List[str]:
    """Return all context fields referenced anywhere in the tree, in tree order (§5.7).

    Duplicates are kept, so a field tested twice weighs twice in the question ordering.
    """
    if condition.kind == "leaf":
        return [condition.fieldname] if condition.fieldname else []
    names: List[str] = []
    for child in condition.children:
        names.extend(referenced_fields(child))
    return names


class BenefitKind(str, enum.Enum):
    """The mechanism a scheme uses to compute its support (§5.2).

    The kinds cover the EU schemes surveyed in §5.1: a share of the eligible cost, a stackable bonus share, a fixed
    lump sum, an amount per unit of size, a tiered amount per unit with a cap, a multi-year tax credit, a reduced VAT
    rate, soft-loan terms, and a per-kWh operational payment. Each kind has a typed payload class
    (:attr:`BenefitTypes.BY_KIND`). :class:`PayoutKind` is separate: it says when and in what form the money arrives.
    ``SHARE_OF_ELIGIBLE_COST`` and ``BONUS_SHARE`` share a payload and differ only in intent; bonuses stack on a base
    rate within a cumulation group.
    """

    SHARE_OF_ELIGIBLE_COST = "SHARE_OF_ELIGIBLE_COST"
    BONUS_SHARE = "BONUS_SHARE"
    LUMP_SUM = "LUMP_SUM"
    PER_UNIT = "PER_UNIT"
    TIERED_PER_UNIT = "TIERED_PER_UNIT"
    TAX_CREDIT = "TAX_CREDIT"
    REDUCED_VAT = "REDUCED_VAT"
    SOFT_LOAN = "SOFT_LOAN"
    OPERATIONAL = "OPERATIONAL"


@dataclass(frozen=True)
class BenefitField:
    """One JSON key of a benefit payload: its name, converter and default.

    Each payload class lists these in its ``SPEC``, so :meth:`Benefit.parse` can check any payload generically
    (required keys present, unknown keys refused, values convertible) and name the scheme and key in errors.
    """

    key: str  # key in the catalog JSON
    name: str  # field name on the benefit dataclass
    convert: Callable[[Any], Any]
    required: bool = True
    default: Any = None


def _shares(raw: Any) -> Tuple[float, ...]:
    """Convert a JSON list of annual shares into a tuple of floats.

    Used for :class:`TaxCreditBenefit`'s optional uneven payout schedule. Strings and bytes are refused explicitly
    because they are iterable; :meth:`Benefit.parse` turns the `TypeError` into a :class:`SubsidyDataError` naming the
    scheme.
    """
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Iterable):
        raise TypeError(f"expected a list of annual shares, got {raw!r}")
    return tuple(float(item) for item in raw)


def _optional_float(raw: Any) -> Optional[float]:
    """Convert a JSON number or ``null`` into an optional float; ``null`` means "not set"."""
    return None if raw is None else float(raw)


def _size_unit(raw: Any) -> Units:
    """Convert a catalog ``size_unit`` string into its :class:`Units` member.

    Used by the per-unit benefit kinds. The spelling is that of ``ComponentCostFacts.size_unit`` (``"kW"``, ``"kWh"``,
    ``"L"``, ``"m2"``, ``"-"``), limited to units the cost database prices against, so a per-unit amount can only name
    a unit a measure's size can be stated in.
    """
    if not isinstance(raw, str):
        raise TypeError(f"expected a unit string, got {raw!r}")
    unit = Units(raw)
    _check_size_unit(unit)
    return unit


def _check_size_unit(unit: Any) -> None:
    """Refuse a per-unit benefit's ``size_unit`` that no measure size can be stated in.

    Shared by the JSON converter and the benefits' ``__post_init__``, so a benefit built in Python meets the same rule
    as one read from a catalog.
    """
    if unit not in ComponentCostFacts.SUPPORTED_SIZE_UNITS:
        raise SubsidyDataError(
            f"size_unit {unit!r} is not a unit a measure is sized in; expected one of "
            f"{[item.value for item in ComponentCostFacts.SUPPORTED_SIZE_UNITS]}."
        )


def _checked_size(size: float, kind: str) -> float:
    """Return the measure size a per-unit benefit prices, refusing one that is not a finite number.

    A NaN size would slip through every comparison and price as zero instead of failing.
    """
    if not math.isfinite(size):
        raise SubsidyDataError(f"{kind} benefit cannot price a measure size of {size!r}; a size is a finite number.")
    return size


@dataclass(frozen=True)
class Tier:
    """One band of a :class:`TieredPerUnitBenefit`: an amount per unit, paid up to a size.

    ``up_to`` is where the band ends, in the benefit's ``size_unit``; the band starts where the previous one ended (0
    for the first). ``None`` is an open band that runs to any size, allowed only last. A band checks its own values;
    the benefit checks the band order.
    """

    up_to: Optional[float]
    amount_per_unit: float

    #: The two keys of a tier object in the catalogue, and nothing else.
    KEYS: ClassVar[Tuple[str, str]] = ("up_to", "amount_per_unit")

    def __post_init__(self) -> None:
        """Refuse a negative or non-finite amount, and a bound that is not a finite positive size."""
        if not math.isfinite(self.amount_per_unit) or self.amount_per_unit < 0:
            raise SubsidyDataError(
                f"Tier pays {self.amount_per_unit} per unit; an amount cannot be negative and must be finite."
            )
        if self.up_to is not None and (not math.isfinite(self.up_to) or self.up_to <= 0):
            raise SubsidyDataError(
                f"Tier ends at {self.up_to}; a bound is a finite size above 0, or null for an open band."
            )


def _tiers(raw: Any) -> Tuple[Tier, ...]:
    """Convert the catalog's list of tier objects into :class:`Tier` values.

    Strings and bytes are refused because they are iterable, and a tier object with a missing or unknown key is
    refused, so a misspelled ``amount_per_unit`` fails the load.
    """
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Iterable):
        raise TypeError(f"expected a list of tier objects, got {raw!r}")
    tiers = []
    for item in raw:
        if not isinstance(item, dict) or set(item) != set(Tier.KEYS):
            raise ValueError(f"a tier is an object with exactly the keys {list(Tier.KEYS)}, got {item!r}")
        tiers.append(Tier(up_to=_optional_float(item["up_to"]), amount_per_unit=float(item["amount_per_unit"])))
    return tuple(tiers)


@dataclass(frozen=True)
class Benefit:
    """Typed benefit payload of a scheme (§5.2).

    The catalog states ``benefit: {"kind": ..., <parameters>}``; the loader parses it into one of the frozen subclasses
    below, so a missing or misspelled parameter fails at load time naming the scheme and the key. Subclasses declare
    their JSON keys in :attr:`SPEC`; :attr:`BenefitTypes.BY_KIND` maps each :class:`BenefitKind` to its subclass.
    """

    #: The payload's JSON keys, in documentation order.
    SPEC: ClassVar[Tuple[BenefitField, ...]] = ()

    @classmethod
    def parse(cls, raw: Dict[str, Any], scheme_id: str, kind: "BenefitKind") -> "Benefit":
        """Build the payload from the catalog dict, refusing missing, unknown or unconvertible keys.

        Unknown keys are refused as hard as missing ones, because a typo would otherwise be dropped and the scheme
        would pay at an unintended default.

        Args:
            raw: The catalog's ``benefit`` object without its ``kind`` key.
            scheme_id: Owning scheme, for error messages.
            kind: The declared benefit kind, for error messages.

        Returns:
            An instance of the payload class this was called on.

        Raises:
            SubsidyDataError: If a mandatory key is missing, an unknown key is present, or a value does not convert;
                the message names the scheme and the key.
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
        try:
            return cls(**values)
        except SubsidyDataError as err:
            # The payload's own cross-field checks (__post_init__) do not know the scheme.
            raise SubsidyDataError(f"Scheme {scheme_id}: {err}") from err

    def value_estimate(self, gross_cost_in_euro: float, measure_size: float) -> float:
        """Return a rough upper bound of the support this benefit could unlock, for ordering questions (§5.7).

        Undiscounted, on the gross measure cost, not clamped to the eligible cost and not bounded by the solver's caps;
        a cap the benefit itself states (a TIERED_PER_UNIT ``cap_in_euro``) does apply. The exact per-slot valuation is
        the solver's (:func:`_combination_awards`).
        """
        del gross_cost_in_euro, measure_size  # unvalued kinds (loans, VAT, operational support)
        return 0.0


@dataclass(frozen=True)
class ShareBenefit(Benefit):
    """A share of the eligible cost: a base rate (SHARE_OF_ELIGIBLE_COST) or a bonus (BONUS_SHARE).

    The common mechanism of the EU programmes (BEG EM: 30 % base plus speed, income and efficiency bonuses). It is the
    only payload the solver stacks: schemes in one ``cumulation_group`` have their rates added and scaled back to the
    group's ``combined_rate_cap`` (§5.4). The rate applies to its own scheme's eligible cost, so two stacked schemes
    may compute on different bases.
    """

    rate: float  # fraction of the eligible cost basis, e.g. 0.30

    SPEC: ClassVar[Tuple[BenefitField, ...]] = (BenefitField("rate", "rate", float),)

    def value_estimate(self, gross_cost_in_euro: float, measure_size: float) -> float:
        """Return the rate times the gross cost."""
        del measure_size
        return gross_cost_in_euro * self.rate


@dataclass(frozen=True)
class LumpSumBenefit(Benefit):
    """A fixed amount, clamped to the eligible cost when the scheme declares cost categories.

    Models fixed grants such as Austria's "Raus aus Öl und Gas" boiler-replacement grant, where the support does not
    scale with the price. The amount is exact in all three band slots, since a statutory number has no band.
    """

    amount: float  # euro, year-0 nominal, exact in all slots

    SPEC: ClassVar[Tuple[BenefitField, ...]] = (BenefitField("amount", "amount", float),)

    def value_estimate(self, gross_cost_in_euro: float, measure_size: float) -> float:
        """Return the amount itself."""
        del gross_cost_in_euro, measure_size
        return self.amount


@dataclass(frozen=True)
class PerUnitBenefit(Benefit):
    """An amount per unit of measure size (EUR/kW, EUR/m², ...), in a unit the catalog states.

    Covers EUR/m² insulation grants and EUR/kWp PV programmes (§5.1). The amount is multiplied by
    ``ComponentCostFacts.size``, which must be in ``size_unit`` (spelled as ``ComponentCostFacts.size_unit``, e.g.
    ``"kW"``, ``"m2"``); the solver refuses a measure sized in another unit, and ``validate`` checks the unit against
    the cost database before any run. The result is exact in all slots and clamped to the eligible cost.
    """

    amount: float  # euro per unit of `ComponentCostFacts.size`, in `size_unit`
    size_unit: Units  # the unit the measure's size must be stated in (kW, m2, ...)

    SPEC: ClassVar[Tuple[BenefitField, ...]] = (
        BenefitField("amount", "amount", float),
        BenefitField("size_unit", "size_unit", _size_unit),
    )

    def __post_init__(self) -> None:
        """Refuse a unit no measure is sized in."""
        _check_size_unit(self.size_unit)

    def amount_for(self, size: float) -> float:
        """Return the benefit for a measure of this size: the amount times the size.

        Args:
            size: The measure's size, in :attr:`size_unit`.

        Returns:
            The amount in euro, before the solver's clamp to the eligible cost.

        Raises:
            SubsidyDataError: If the size is not a finite number.
        """
        return self.amount * _checked_size(size, "Per-unit")

    def value_estimate(self, gross_cost_in_euro: float, measure_size: float) -> float:
        """Return the amount times the measure size."""
        del gross_cost_in_euro
        return self.amount_for(measure_size)


@dataclass(frozen=True)
class TieredPerUnitBenefit(Benefit):
    """An amount per unit of measure size that changes by band, with an optional overall cap.

    Example: SEAI's solar PV grant pays 700 EUR per kWp up to 2 kWp, 200 EUR per further kWp up to 4 kWp, and at most
    1,800 EUR, so a 2.5 kWp array receives 2 x 700 + 0.5 x 200 = 1,500 EUR. Each band pays its rate on the part of the
    size inside it; the sum is capped at ``cap_in_euro``. The size must be in ``size_unit``, as for
    :class:`PerUnitBenefit`. The result is exact in all slots and clamped to the eligible cost by the solver.
    """

    tiers: Tuple[Tier, ...]  # ascending by up_to; only the last may be open (up_to None)
    size_unit: Units  # the unit the measure's size, and every band's up_to, is stated in
    cap_in_euro: Optional[float] = None  # the most the benefit pays, or None for no cap

    SPEC: ClassVar[Tuple[BenefitField, ...]] = (
        BenefitField("tiers", "tiers", _tiers),
        BenefitField("size_unit", "size_unit", _size_unit),
        BenefitField("cap_in_euro", "cap_in_euro", _optional_float, required=False, default=None),
    )

    def __post_init__(self) -> None:
        """Validate the bands and the cap so that every band can pay something.

        The bands must exist, ascend, and be open only at the end. The cap must be finite and above zero. A closed last
        band needs a cap, so what is paid beyond it is stated. With more than one band, a cap at or below the first
        band's full value is refused, since no later band could pay; a single closed band with a binding cap (a flat
        rate with a ceiling) is fine.
        """
        object.__setattr__(self, "tiers", tuple(self.tiers))
        if not self.tiers:
            raise SubsidyDataError("Tiered per-unit benefit needs at least one tier.")
        _check_size_unit(self.size_unit)
        previous = 0.0
        for index, tier in enumerate(self.tiers):
            if not isinstance(tier, Tier):
                raise SubsidyDataError(f"Tiered per-unit benefit tier {index} is not a Tier: {tier!r}.")
            if tier.up_to is None:
                if index != len(self.tiers) - 1:
                    raise SubsidyDataError(
                        f"Tiered per-unit benefit tier {index} is open (up_to null) but is not the "
                        "last tier; only the last tier may run to any size."
                    )
                continue
            if tier.up_to <= previous:
                raise SubsidyDataError(
                    f"Tiered per-unit benefit tiers must ascend: tier {index} ends at {tier.up_to}, "
                    f"which is not above {previous}."
                )
            previous = tier.up_to
        if self.cap_in_euro is None:
            last = self.tiers[-1]
            if last.up_to is not None:
                raise SubsidyDataError(
                    f"Tiered per-unit benefit tier {len(self.tiers) - 1} is the last tier and ends at "
                    f"{last.up_to}, but no cap_in_euro is set; the last tier must be open (up_to null) "
                    "or the benefit must state its cap."
                )
            return
        if not math.isfinite(self.cap_in_euro) or self.cap_in_euro <= 0:
            raise SubsidyDataError(
                f"Tiered per-unit benefit cap {self.cap_in_euro} is not a finite amount above 0; "
                "omit cap_in_euro for no cap."
            )
        first = self.tiers[0]
        if len(self.tiers) > 1 and first.up_to is not None:
            first_full_value = first.amount_per_unit * first.up_to
            if self.cap_in_euro <= first_full_value:
                raise SubsidyDataError(
                    f"Tiered per-unit benefit cap {self.cap_in_euro} is not above the first tier's full "
                    f"value {first_full_value}, so no later tier could ever pay."
                )

    def amount_for(self, size: float) -> float:
        """Return the benefit for a measure of this size: each band's rate on its part of the size, then the cap.

        Args:
            size: The measure's size, in :attr:`size_unit`; a negative size counts as zero.

        Returns:
            The amount in euro, never above ``cap_in_euro``.

        Raises:
            SubsidyDataError: If the size is not a finite number.
        """
        remaining_from = 0.0
        total = 0.0
        size = max(_checked_size(size, "Tiered per-unit"), 0.0)
        for tier in self.tiers:
            upper = size if tier.up_to is None else min(size, tier.up_to)
            if upper > remaining_from:
                total += tier.amount_per_unit * (upper - remaining_from)
            if tier.up_to is None or size <= tier.up_to:
                break
            remaining_from = tier.up_to
        return total if self.cap_in_euro is None else min(total, self.cap_in_euro)

    def value_estimate(self, gross_cost_in_euro: float, measure_size: float) -> float:
        """Return the tiered amount for the measure's size, capped by the benefit's own cap."""
        del gross_cost_in_euro
        return self.amount_for(measure_size)


@dataclass(frozen=True)
class TaxCreditBenefit(Benefit):
    """A share of the eligible cost paid out over `years` instalments, optionally unevenly.

    Models income-tax deductions such as Italy's Ecobonus over ten instalments or Germany's §35c EStG (shipped as 20 %
    over three unevenly split years). The solver discounts each instalment, since timing changes the value. It does not
    stack in a cumulation group; §35c is made exclusive with the grant programmes through ``excludes``.
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
        """Validate the payout schedule (§5.2): whole years, shares summing to 1."""
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
        """Return the per-year shares of the total credit, evenly split unless the catalog states shares.

        One share per instalment year, in order, summing to 1. The solver multiplies the total credit by them to build
        the award's ``schedule_amounts``, booked in years 1..N.
        """
        if self.annual_shares:
            return self.annual_shares
        return tuple(1.0 / self.years for _ in range(self.years))

    def value_estimate(self, gross_cost_in_euro: float, measure_size: float) -> float:
        """Return the rate times the gross cost, undiscounted like the other kinds."""
        del measure_size
        return gross_cost_in_euro * self.rate


@dataclass(frozen=True)
class ReducedVatBenefit(Benefit):
    """A reduced VAT rate on the measure.

    The resulting `PayoutKind.VAT_REDUCTION` award has no consumer in the engine: no VAT netting reads
    `reduced_vat_rate`. The kind exists so a catalog entry is well-formed.
    """

    vat_rate: float

    SPEC: ClassVar[Tuple[BenefitField, ...]] = (BenefitField("vat_rate", "vat_rate", float),)


@dataclass(frozen=True)
class LoanTermsBenefit(Benefit):
    """Soft-loan terms: interest rate, term and an optional repayment grant (Tilgungszuschuss).

    A subsidized loan overrides the financing plan (§4.4): ``calculators/financing_application.py`` substitutes these
    terms into the :class:`~hisim.economics.financing.FinancingPlan`, and the benefit shows as interest not paid. The
    repayment grant is the part paid as support. The solver values it on the gross measure cost, while the financing
    applies it to the loan principal; the two differ when less than the full cost is financed (the shipped KfW rate is
    0.0).
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
    """A per-kWh payment on one carrier for a number of years, such as feed-in remuneration.

    Its value depends on the simulation: it is paid per kWh sold or, where no sold energy is recorded, bought (see
    :func:`_support_value`). Payments run for ``duration_years`` from year 1, cut at the observation horizon; the rate
    is nominal and does not escalate, as in fixed-term EEG contracts (§8.5).
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
    """The payload class of each benefit kind, the one dispatch table for loader and engine.

    :func:`parse_benefit` uses it to pick the class for a catalog entry, and :meth:`SubsidyScheme.__post_init__` uses
    it to check a scheme built in Python.
    """

    BY_KIND: Dict[BenefitKind, type] = {
        BenefitKind.SHARE_OF_ELIGIBLE_COST: ShareBenefit,
        BenefitKind.BONUS_SHARE: ShareBenefit,
        BenefitKind.LUMP_SUM: LumpSumBenefit,
        BenefitKind.PER_UNIT: PerUnitBenefit,
        BenefitKind.TIERED_PER_UNIT: TieredPerUnitBenefit,
        BenefitKind.TAX_CREDIT: TaxCreditBenefit,
        BenefitKind.REDUCED_VAT: ReducedVatBenefit,
        BenefitKind.SOFT_LOAN: LoanTermsBenefit,
        BenefitKind.OPERATIONAL: OperationalBenefit,
    }


def parse_benefit(raw: Dict[str, Any], scheme_id: str) -> Tuple[BenefitKind, Benefit]:
    """Parse a catalog `benefit` object into its kind and typed payload (§5.2).

    Reads the ``kind`` tag, looks up the payload class in :attr:`BenefitTypes.BY_KIND` and hands the remaining keys to
    :meth:`Benefit.parse`.

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
    """When and in what form a scheme's support arrives on the timeline (§5.2).

    :class:`BenefitKind` says how much support is computed; this says how it is paid, which matters because the same 30
    % share may be a year-0 grant in one country and a multi-year tax deduction in another. A scheme declares it in
    ``payout.kind`` (default ``UPFRONT_GRANT``); ``calculators/subsidy_application.py`` interprets it:

    - ``UPFRONT_GRANT``: one negative SUBSIDY entry at year 0.
    - ``TAX_CREDIT_SCHEDULE``: one entry per scheduled year 1..N, cut at the horizon.
    - ``OPERATIONAL``: one entry per year of the award's duration, valued on the annualized energy.
    - ``LOAN_TERMS``: no entry of its own; it overrides the financing plan (§4.4).
    - ``VAT_REDUCTION``: typed but not booked (see :class:`ReducedVatBenefit`).
    """

    UPFRONT_GRANT = "UPFRONT_GRANT"
    TAX_CREDIT_SCHEDULE = "TAX_CREDIT_SCHEDULE"
    LOAN_TERMS = "LOAN_TERMS"
    OPERATIONAL = "OPERATIONAL"
    VAT_REDUCTION = "VAT_REDUCTION"


@dataclass
class EligibleCostSpec:
    """Which cost categories count toward a scheme's eligible cost, and how it is capped and prorated (§5.2).

    The eligible cost is the euro figure a scheme's rate, lump sum or credit is computed on; it is per scheme, so two
    schemes on the same heat pump may count different categories. The settings are: the counted timeline categories
    (investment, planning and removal by default), whether the basis is gross or net of VAT, whether mixed-use
    buildings are prorated to their residential share, and a per-dwelling-unit ceiling. :func:`_eligible_cost_basis`
    applies them in that order.
    """

    categories: List[CostCategory] = field(
        default_factory=lambda: [CostCategory.INVESTMENT, CostCategory.PLANNING, CostCategory.REMOVAL]
    )
    cap_per_dwelling_unit_in_euro: List[float] = field(default_factory=list)  # tiered; empty = uncapped
    basis: str = "GROSS"  # GROSS | NET (of VAT)
    proration: str = "NONE"  # NONE | RESIDENTIAL_SHARE

    def cap_for_units(self, dwelling_units: int) -> Optional[float]:
        """Return the building-wide eligible-cost cap for a number of dwelling units.

        Programmes cap per unit in descending tiers; the shipped BEG entry is 30 kEUR for the first unit, 15 kEUR for
        the next five and 8 kEUR for every further one. The last tier repeats for all remaining units.

        Args:
            dwelling_units: Number of dwelling units in the building; values below 1 count as 1.

        Returns:
            The total cap in euro, or ``None`` when the scheme declares no cap (distinct from a cap of 0).
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
    """One funding programme of the catalog (§5.2).

    States where and when it applies, to which asset classes and measure kinds, who qualifies, what it pays, on which
    cost basis and how it combines with other schemes. ``legal_basis`` and ``url`` are mandatory, so every amount can
    be checked against the directive (§3.10). ``cumulation_group`` is a free-text label; every id in ``excludes`` must
    name a real scheme, which the data-file checks verify.
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
    benefit: Benefit  # typed, kind-specific parameters
    eligible_cost: EligibleCostSpec
    cumulation_group: Optional[str]  # label; schemes sharing it stack additively (share kinds)
    combined_rate_cap: Optional[float]  # cap on the group's summed rate, e.g. 0.70
    excludes: List[str]  # scheme ids this one cannot be combined with (symmetric in effect)
    payout_kind: PayoutKind
    source_ids: Tuple[str, ...] = ()  # registry ids; mandatory for catalog-loaded schemes
    #: Human-readable name for the report ("BEG EM heat pump — base grant (30 %)"). Optional in
    #: the schema; every renderer reads :attr:`label`, which falls back to the id, and `validate`
    #: warns about a scheme that ships without one.
    display_name: Optional[str] = None

    @property
    def label(self) -> str:
        """Return the name a reader sees: the display name, or the scheme id when the catalog declares none.

        Renderers show this and keep :attr:`id` in a tooltip or parenthesis, since the id is what a reviewer greps the
        catalog for.
        """
        return self.display_name or self.id

    def __post_init__(self) -> None:
        """Check that `benefit_kind` matches the typed payload class."""
        expected = BenefitTypes.BY_KIND[self.benefit_kind]
        if not isinstance(self.benefit, expected):
            raise SubsidyDataError(
                f"Scheme {self.id}: benefit kind {self.benefit_kind.value} needs a "
                f"{expected.__name__}, got {type(self.benefit).__name__}."
            )

    def applies_to(self, asset_class: ComponentType, measure_kind: str) -> bool:
        """Whether the scheme covers this asset class and measure kind at all.

        The cheapest pre-filter. The measure kind (INSTALL or REPLACE) matters because some programmes, such as the BEG
        speed bonus, reward replacing a working fossil system and must not pay for a new installation.
        """
        return asset_class in self.asset_classes and measure_kind in self.measure_kinds


def scheme_context_fields(scheme: SubsidyScheme) -> List[str]:
    """Return every context field a scheme depends on (§5.7).

    Two sources: the eligibility conditions, and the eligible-cost spec, which needs the residential share when it
    prorates and the dwelling-unit count when it caps per unit. Question derivation and the coverage check both read
    this.
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

    How to ask a user for one context field, in every supported language, with answer options and help text. Entries
    live in ``subsidy_catalog/questions_<COUNTRY>.json`` keyed by the field they fill; the data-file checks (§9.6) fail
    if a field used by a shipped scheme has no entry.
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
    """A question to ask, with the schemes that made it necessary.

    Returned by :func:`required_questions`. ``asked_because`` lists the scheme ids, so a frontend can say why the
    question appears; ``pruning_power_in_euro`` is the ordering key that puts the question gating the most money first.
    """

    entry: QuestionEntry
    asked_because: List[str] = field(default_factory=list)  # scheme ids
    # Upper bound of support the answer could unlock (ordering heuristic, §5.7):
    pruning_power_in_euro: float = 0.0


class SubsidyCatalog:
    """One country's subsidy catalog with its questions and cited sources.

    The loaded, validated content of ``subsidy_catalog/<COUNTRY>.json`` and ``questions_<COUNTRY>.json``: the schemes,
    the localized questions, the country-level state-aid ceiling and the ``sources.json`` entries the schemes cite. It
    is the only reader of catalog files. A run uses one when ``EconomicParameters.subsidy_catalog_path`` is set;
    without a path no subsidy is booked. A catalog built in Python (tests, worked examples) carries no registry, and
    its schemes record ``IN_MEMORY_DEFINITION`` provenance.
    """

    #: Default on-disk location of the shipped subsidy catalogs: `hisim/subsidy_catalog`,
    #: three levels up from this file.
    DEFAULT_PATH: ClassVar[str] = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "subsidy_catalog"
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
        """Store the loaded catalog content; use :meth:`load` to read one from disk.

        `sources` are the registry entries the loader resolved the schemes' `source_ids` against; a catalog built in
        Python passes none.
        """
        self.schemes = schemes
        self.questions = questions
        self.snapshot_date = snapshot_date
        self.overall_cap_share = overall_cap_share
        self.base_path = base_path
        self.country = country
        #: Resolved `sources.json` entries by id, kept for the provenance ledger.
        self.sources: Dict[str, SourceEntry] = dict(sources or {})

    @classmethod
    def load(cls, country: str, base_path: Optional[str] = None) -> "SubsidyCatalog":
        """Load and validate `<base_path>/<COUNTRY>.json` plus `questions_<COUNTRY>.json`.

        Everything statically checkable is checked here, so a malformed programme is a load error naming the scheme:
        ``legal_basis``, ``url``, ``benefit`` and ``source_ids`` are mandatory, the benefit payload is parsed (§5.2),
        condition field names are checked against the context vocabulary (§5.3), and cited source ids are resolved
        against ``sources.json``. The question file is optional here; its completeness is a data-file check (§9.6).

        Args:
            country: ISO country code selecting both file names.
            base_path: Directory holding the catalog; defaults to :attr:`DEFAULT_PATH`.

        Returns:
            The loaded catalog with resolved source entries.

        Raises:
            SubsidyDataError: If no catalog file exists for the country, or a scheme violates the schema (missing
                mandatory field, unknown benefit kind or asset class, unparsable condition, no source ids).
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
        """Resolve a configured catalog path to the one existing directory it names, or refuse.

        A relative path is tried against three roots: the current directory, the repository or installation root
        containing the `hisim` package, and the package's data directory, so both `hisim/subsidy_catalog` and
        `subsidy_catalog` find the shipped catalog wherever the command runs. An absolute path is used as given.
        Candidates are deduplicated by `realpath`; if more than one distinct directory exists, the path is ambiguous
        and refused, since a stray `subsidy_catalog/` in the working directory must not silently shadow the shipped
        one. The error message tells the user to make the path absolute.

        Args:
            configured_path: The non-empty path a parameter set names.

        Returns:
            The single existing directory to load the catalog from.

        Raises:
            CostDataError: If no candidate exists or more than one does; pricing with no catalog or the wrong one would
                be worse than failing.
        """
        package_directory = os.path.dirname(cls.DEFAULT_PATH)
        install_root = os.path.dirname(package_directory)
        candidates = [os.path.abspath(configured_path)]
        if not os.path.isabs(configured_path):
            candidates.append(os.path.abspath(os.path.join(install_root, configured_path)))
            candidates.append(os.path.abspath(os.path.join(package_directory, configured_path)))
        unique: List[str] = []
        seen: Set[str] = set()
        for candidate in candidates:
            real = os.path.realpath(candidate)
            if real not in seen:
                seen.add(real)
                unique.append(candidate)
        existing = [candidate for candidate in unique if os.path.isdir(candidate)]
        if len(existing) == 1:
            return existing[0]
        if not existing:
            raise CostDataError(
                f"Configured subsidy catalog path {configured_path!r} does not resolve to a "
                f"directory (tried: {', '.join(unique)}). Fix the path or remove "
                "`subsidy_catalog_path` from the parameters — a catalog that was named but cannot "
                "be read is never replaced by a run priced without subsidies."
            )
        raise CostDataError(
            f"Configured subsidy catalog path {configured_path!r} is ambiguous: it exists at "
            f"{' and at '.join(existing)}. Which one a run would have used depends on the working "
            "directory it was started from, so neither is chosen. Make `subsidy_catalog_path` "
            "absolute to name the catalog you mean."
        )

    @classmethod
    def shipped_catalog_file(cls, country: str, directory: Optional[str] = None) -> Optional[str]:
        """Return the ``<COUNTRY>.json`` a catalog directory holds, or None when it holds none.

        The one place that answers "does this country have a catalog?", shared by the RenoVisor translator and the
        staged CLI. A country without a file gets no catalog, the run prices as subsidy mode NONE, and the result
        document shows undetermined rows rather than zeroes.

        Example::

            SubsidyCatalog.shipped_catalog_file("IE")  # -> ".../hisim/subsidy_catalog/IE.json"

        Args:
            country: The ISO-3166 alpha-2 code, in any case.
            directory: Where to look; :attr:`DEFAULT_PATH`, the shipped directory, when omitted.

        Returns:
            The absolute path of the country's catalog file, or None.
        """
        base = directory if directory is not None else cls.DEFAULT_PATH
        candidate = os.path.join(base, f"{country.upper()}.json")
        return candidate if os.path.isfile(candidate) else None

    @classmethod
    def configured_or_shipped_path(
        cls, country: str, configured_path: Optional[str], override_path: Optional[str] = None
    ) -> Optional[str]:
        """Return the directory a run loads its catalog from, falling back to the shipped one for the country.

        A path the caller or the parameters name wins; when neither names one, the shipped directory is used if it has
        this country's file. Without the fallback, a caller holding only a stage extract (which carries no catalog
        path) would price every subsidy as undetermined. :meth:`load_configured` deliberately has no such fallback:
        there "no path" means "no catalog", and changing that would re-price archived studies.

        Args:
            country: The country the run is priced for.
            configured_path: ``EconomicParameters.subsidy_catalog_path``, possibly None.
            override_path: A ``--subsidy-catalog`` flag, which wins over everything.

        Returns:
            The directory to load from, or None when nobody named one and the country ships none.
        """
        named = override_path or configured_path
        if named:
            return named
        shipped = cls.shipped_catalog_file(country)
        return os.path.dirname(shipped) if shipped is not None else None

    @classmethod
    def load_configured(
        cls, country: str, configured_path: Optional[str], override_path: Optional[str] = None
    ) -> Optional["SubsidyCatalog"]:
        """Return the catalog a parameter set asks for, or None when it asks for none.

        Used by the CLI subcommands and the postprocessing bridge. Naming no catalog is valid (the country may have
        none) and the run books no subsidy. Naming one that cannot be read raises. Every failure is converted to
        `CostDataError`, because the bridge must propagate a typed cost error to `postprocessing_main` and the CLI
        turns that type into exit code 2.

        Args:
            country: ISO country code selecting the catalog file.
            configured_path: `EconomicParameters.subsidy_catalog_path`, possibly None.
            override_path: A `--subsidy-catalog` flag, which wins over the parameters when given.

        Returns:
            The loaded catalog, or None when neither a path nor an override was given.

        Raises:
            CostDataError: If a path was given but does not resolve, or the catalog it names is missing or malformed.
        """
        path = override_path or configured_path
        if not path:
            return None
        base_path = cls.resolve_base_path(path)
        try:
            return cls.load(country, base_path)
        except CostDataError:
            raise
        except Exception as err:  # pylint: disable=broad-except
            raise CostDataError(
                f"The subsidy catalog configured at {path!r} for country {country!r} failed to "
                f"load ({type(err).__name__}: {err}). A run that asked for its subsidies must not "
                "quietly be priced without any instead."
            ) from err

    # ------------------------------------------------------------------ provenance (§3.10)

    def scheme_by_id(self, scheme_id: str) -> Optional[SubsidyScheme]:
        """Return the scheme with that id, or None.

        Awards reference schemes by id so decisions stay serializable; this is how a consumer such as
        ``calculators/subsidy_application.py`` gets back to a scheme's legal basis.
        """
        return next((scheme for scheme in self.schemes if scheme.id == scheme_id), None)

    def resolved_sources(self, scheme: SubsidyScheme) -> List[SourceEntry]:
        """Return the ``sources.json`` entries backing one scheme, skipping ids the registry lacks.

        An empty list identifies a scheme defined in Python, recorded as :attr:`ParameterOrigin.IN_MEMORY_DEFINITION`.
        """
        return [self.sources[source_id] for source_id in scheme.source_ids if source_id in self.sources]

    def source_resolver(self) -> Dict[str, ResolvedSource]:
        """Return the catalog's registry entries in the report representation (§3.10).

        The result object merges them with the cost database's registry (the id spaces are disjoint), so a subsidy flow
        explained through the provenance ledger resolves to a citation. Read by the report's "sources used" table and
        the ``explain`` CLI.
        """
        return {source_id: entry.to_resolved() for source_id, entry in self.sources.items()}

    def provenance_for_scheme(
        self, scheme: SubsidyScheme, ledger: ProvenanceLedger, value: Any
    ) -> int:
        """Record the provenance-ledger entry backing one award of `scheme` and return its id.

        A catalog-loaded scheme cites the registry ids resolved at load time, with its `legal_basis` and `url` in
        `detail`, so `explain` reaches the legal text. A scheme defined in Python is recorded as
        :attr:`ParameterOrigin.IN_MEMORY_DEFINITION`.
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
        """Return the schemes that could apply to one measure, by jurisdiction, asset class and validity (§5.7).

        The first stage of every subsidy computation; it keeps the exponential solver and the question derivation on a
        handful of schemes. A scheme with no region is nationwide; a regional scheme matches only its region, and an
        applicant who stated no region is offered regional schemes too (conditions may still reject them). Validity
        compares the years of the ISO dates with the price basis year, the economic "today", not the weather year of
        the simulation.

        Args:
            asset_class: The measure's asset class.
            measure_kind: "INSTALL" or "REPLACE".
            region: The applicant's region key, or None if unstated.
            year: The year scheme validity is tested against.

        Returns:
            The candidate schemes in catalog file order, which is also the solver's tie-break order.
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
    """Resolve a catalog asset-class string to its `ComponentType`, by enum name or value.

    Anything else raises, because a scheme that quietly applies to nothing would look like one whose conditions failed.
    """
    for member in ComponentType:
        if name in (member.name, member.value):
            return member
    raise SubsidyDataError(f"Scheme {scheme_id}: unknown asset class {name!r}.")
