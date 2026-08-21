"""Sizing machinery for component configurations (design B of ``system_docs/config_defaults_spec.md``).

This module holds everything a config class needs to declare *how its values derive from
the surrounding system* instead of shipping factory methods that compute finished numbers:

- :data:`AUTO` — the sentinel a field carries when its value should be computed. A preset,
  a scenario file or a hand-written setup only ever says *concrete value* or *AUTO*.
- :func:`sized_field` — a ``dataclasses.field`` wrapper that attaches the field's sizing
  law and the AUTO wire codec in a single declaration.
- :class:`Size` — expression terms over the :class:`SizingContext` facts, so simple laws
  read like the formula (``1.1 * Size.HEATING_LOAD_IN_WATT``) and can name the facts they
  read in error messages and in the audit record.
- :class:`SizingContext` — the frozen, scope-resolved snapshot of the facts a component
  may size against, built once per building via :meth:`SizingContext.for_building`.
- :func:`resolve_config` — the one shared resolver turning AUTO fields into numbers, used
  through ``ConfigBase.resolve``.

The named-preset half of design B lives next door in :mod:`hisim.config.presets`, and the
cross-component fact resolution in :mod:`hisim.config.engine`.

Per the ``hisim.config`` layering rule the module imports nothing from the rest of HiSim
at module level: ``component.py`` imports it (for the central AUTO check and
``ConfigBase.resolve``), so any hisim import here would close a cycle. The single place
that needs component knowledge — :meth:`SizingContext.for_building` — imports lazily.
"""

# clean

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, ClassVar, Dict, Mapping, Optional, Tuple, TypeVar, Union

from dataclasses_json import config as dataclasses_json_config

from hisim.config.presets import Catalog

if TYPE_CHECKING:
    from hisim.components.building.config import BuildingConfig
    from hisim.components.heat_distribution_system import HeatDistributionSystemType

ConfigT = TypeVar("ConfigT")


class SizingError(Exception):
    """Base class of every error the sizing machinery raises.

    Split from ``ValueError`` so that callers (tests, the future v2 executor) can catch
    sizing problems precisely without accidentally swallowing unrelated failures.
    """


class ConfigSizingError(SizingError):
    """A config still requires sizing, or a law could not be evaluated.

    Raised centrally by ``Component.__init__`` when a config carrying :data:`AUTO`
    reaches a component (the "forgot to size" case), and by :func:`resolve_config` when
    a law needs a context fact that the given :class:`SizingContext` does not carry.
    """


class NothingToSizeError(SizingError):
    """``resolve`` was called on a config class that declares no sizable field at all.

    Passing a sizing context to a component that can never use one is a setup bug — the
    author believed something got sized that never could be — so it fails loudly instead
    of silently doing nothing (spec §4.1). A class that *has* sizable fields which are
    all currently concrete is the legitimate no-op case and does not raise.
    """


class _AutoSize:
    """Singleton sentinel for "compute this field from the SizingContext".

    The sentinel is a distinct object rather than a string, so that ``Sizable[str]``
    fields (device names picked from real catalogs) can never collide with it in Python;
    detection is always the identity check ``value is AUTO``. It is copy-stable —
    ``deepcopy``/``copy`` return the same instance — because configs holding AUTO travel
    through ``dataclasses.replace`` and ``copy.deepcopy`` and the identity check must
    survive that.
    """

    #: What the sentinel is spelled as in a JSON file. In-band by design: a scenario
    #: file cannot express a *device* literally named "AUTO", which is accepted (§4.1).
    WIRE_SPELLING: ClassVar[str] = "AUTO"

    _instance: ClassVar[Optional["_AutoSize"]] = None

    def __new__(cls) -> "_AutoSize":
        """Returns the one shared instance, creating it on first use."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        """Represents the sentinel by its wire spelling, which reads well in errors."""
        return "AUTO"

    def __deepcopy__(self, memo: Any) -> "_AutoSize":
        """Preserves the singleton through deep copies, keeping 'is AUTO' checks valid."""
        del memo
        return self

    def __copy__(self) -> "_AutoSize":
        """Preserves the singleton through shallow copies, same reason as deepcopy."""
        return self


AUTO = _AutoSize()


def _encode_sizable(value: Any) -> Any:
    """Turns the AUTO sentinel into its wire spelling; concrete values pass through."""
    return _AutoSize.WIRE_SPELLING if value is AUTO else value


@dataclass(frozen=True)
class SizingRecordEntry:
    """The provenance of one resolved field: which law computed which value from what.

    A tuple of these is attached to a resolved config as its ``sizing_record`` (a plain
    attribute, deliberately not a dataclass field, so serialization and equality ignore
    it); the audit-artifact writer reads it to show per field how a value came to be.
    """

    field: str
    law: str
    facts_read: Tuple[str, ...]
    value: Any


class SizingLaw:
    """Base class of every sizing law: something that computes a value from the context.

    Laws are attached to fields by :func:`sized_field` (under :attr:`METADATA_KEY`) and
    evaluated by :func:`resolve_config`. The expression subclasses build small trees via operator
    overloading so that a linear law reads like the formula it is, can be described in
    error messages, and can name the context facts it reads. Genuinely computational laws
    are plain functions wrapped in :class:`_FunctionLaw`.
    """

    #: Field-metadata key under which :func:`sized_field` stores the law.
    METADATA_KEY: ClassVar[str] = "hisim_sizing_law"

    def evaluate(self, ctx: "SizingContext") -> Any:
        """Computes the law's value against the given context."""
        raise NotImplementedError

    def describe(self) -> str:
        """Renders the law as a short human-readable formula for errors and the audit."""
        raise NotImplementedError

    def facts_read(self) -> Tuple[str, ...]:
        """Names the context fields this law reads; empty when not statically knowable."""
        return ()

    def __mul__(self, factor: float) -> "SizingLaw":
        """Scales the law by a constant factor, e.g. ``Size.HEATING_LOAD_IN_WATT * 1.1``."""
        return _ScaledLaw(self, factor)

    def __rmul__(self, factor: float) -> "SizingLaw":
        """Scales the law by a constant factor written on the left, ``1.1 * Size...``."""
        return _ScaledLaw(self, factor)

    def at_least(self, minimum: float) -> "SizingLaw":
        """Clamps the law's value from below."""
        return _ClampedLaw(self, minimum=minimum)

    def at_most(self, maximum: float) -> "SizingLaw":
        """Clamps the law's value from above."""
        return _ClampedLaw(self, maximum=maximum)

    def rounded(self, digits: int) -> "SizingLaw":
        """Rounds the law's value to the given number of digits."""
        return _RoundedLaw(self, digits)


#: Type alias for a sizable field's value: the concrete value, the AUTO sentinel, or —
#: the per-preset escape hatch — a SizingLaw overriding the field's declared law. Defined
#: after the class with a real reference, so that get_type_hints resolves it in any module.
Sizable = Union[ConfigT, _AutoSize, SizingLaw]


class _FactTerm(SizingLaw):
    """A law that passes one context fact through unchanged.

    These are the leaves of every expression law. The :class:`Size` registry holds one
    per :class:`SizingContext` field, so the vocabulary of terms and the vocabulary of
    facts are the same thing by construction and cannot drift apart.
    """

    def __init__(self, field_name: str) -> None:
        """Stores the context field this term reads."""
        self.field_name = field_name

    def evaluate(self, ctx: "SizingContext") -> Any:
        """Reads the fact, raising precisely when the context does not carry it."""
        value = getattr(ctx, self.field_name)
        if value is None:
            raise ConfigSizingError(
                f"the SizingContext carries no '{self.field_name}', which this law reads"
            )
        return value

    def describe(self) -> str:
        """Renders as the Size-registry spelling."""
        return f"Size.{self.field_name.upper()}"

    def facts_read(self) -> Tuple[str, ...]:
        """Names exactly the one fact this term reads."""
        return (self.field_name,)


class _ScaledLaw(SizingLaw):
    """A law multiplied by a constant factor."""

    def __init__(self, inner: SizingLaw, factor: float) -> None:
        """Stores the wrapped law and the factor."""
        self.inner = inner
        self.factor = factor

    def evaluate(self, ctx: "SizingContext") -> Any:
        """Evaluates the inner law and scales the result."""
        return self.inner.evaluate(ctx) * self.factor

    def describe(self) -> str:
        """Renders as ``factor * inner``."""
        return f"{self.factor} * {self.inner.describe()}"

    def facts_read(self) -> Tuple[str, ...]:
        """Delegates to the wrapped law."""
        return self.inner.facts_read()


class _ClampedLaw(SizingLaw):
    """A law clamped from below and/or above."""

    def __init__(self, inner: SizingLaw, minimum: Optional[float] = None, maximum: Optional[float] = None) -> None:
        """Stores the wrapped law and the bounds."""
        self.inner = inner
        self.minimum = minimum
        self.maximum = maximum

    def evaluate(self, ctx: "SizingContext") -> Any:
        """Evaluates the inner law and applies the bounds."""
        value = self.inner.evaluate(ctx)
        if self.minimum is not None:
            value = max(value, self.minimum)
        if self.maximum is not None:
            value = min(value, self.maximum)
        return value

    def describe(self) -> str:
        """Renders the bounds as ``.at_least``/``.at_most`` suffixes."""
        rendered = self.inner.describe()
        if self.minimum is not None:
            rendered += f".at_least({self.minimum})"
        if self.maximum is not None:
            rendered += f".at_most({self.maximum})"
        return rendered

    def facts_read(self) -> Tuple[str, ...]:
        """Delegates to the wrapped law."""
        return self.inner.facts_read()


class _RoundedLaw(SizingLaw):
    """A law whose value is rounded to a fixed number of digits."""

    def __init__(self, inner: SizingLaw, digits: int) -> None:
        """Stores the wrapped law and the digit count."""
        self.inner = inner
        self.digits = digits

    def evaluate(self, ctx: "SizingContext") -> Any:
        """Evaluates the inner law and rounds the result."""
        return round(self.inner.evaluate(ctx), self.digits)

    def describe(self) -> str:
        """Renders as a ``.rounded(n)`` suffix."""
        return f"{self.inner.describe()}.rounded({self.digits})"

    def facts_read(self) -> Tuple[str, ...]:
        """Delegates to the wrapped law."""
        return self.inner.facts_read()


class _ConstantLaw(SizingLaw):
    """A law whose sized value is a plain constant (``rule=0.0``).

    Several real scaled factories set a field to a fixed value in the sized variant (the
    scaled boiler's minimal power is 0.0); a constant law expresses that directly instead
    of contorting it into a zero-factor expression.
    """

    def __init__(self, value: Any) -> None:
        """Stores the constant."""
        self.value = value

    def evaluate(self, ctx: "SizingContext") -> Any:
        """Returns the constant; the context is deliberately ignored."""
        del ctx
        return self.value

    def describe(self) -> str:
        """Renders the constant itself."""
        return repr(self.value)


class _FunctionLaw(SizingLaw):
    """A genuinely computational law: a plain function of the context.

    Functions cannot be introspected, so the facts they read must be **declared**
    (spec §4.1, decided 2026-08-19): without the declaration the sizing dependency
    graph would be a guess, silently degrading the engine's precise up-front errors
    into "no progress" mysteries. Enforcement happens at declaration time in
    :func:`law`, so a forgetful author fails on import, not three resolution waves in.
    """

    def __init__(self, function: Callable[["SizingContext"], Any], reads: Tuple[str, ...]) -> None:
        """Stores the callable and its declared context facts."""
        self.function = function
        self.reads = reads

    def evaluate(self, ctx: "SizingContext") -> Any:
        """Calls the function on the context."""
        return self.function(ctx)

    def describe(self) -> str:
        """Renders the function by its qualified name where available."""
        return getattr(self.function, "__qualname__", repr(self.function))

    def facts_read(self) -> Tuple[str, ...]:
        """Returns the declared facts, which registration uses as graph edges."""
        return self.reads


def _normalize_law(rule: Any, reads: Optional[Tuple[Any, ...]] = None) -> SizingLaw:
    """Turns whatever ``sized_field(rule=...)`` or :func:`law` received into a law object.

    Accepts a ready law, a callable (which **must** declare its reads, spec §4.1), or any
    other value (wrapped as a constant law) — the three spellings the spec allows. The
    ``reads`` entries may be ``Size.*`` terms or plain fact names.
    """
    if isinstance(rule, SizingLaw):
        return rule
    if callable(rule):
        if reads is None:
            raise SizingError(
                "a function law must declare the facts it reads, e.g. "
                "law(fn, reads=(Size.HEATING_LOAD_IN_WATT, ...)) - expression laws "
                "derive theirs automatically; without the declaration the sizing "
                "dependency graph is a guess."
            )
        fact_names = tuple(
            entry.field_name if isinstance(entry, _FactTerm) else str(entry) for entry in reads
        )
        return _FunctionLaw(rule, fact_names)
    return _ConstantLaw(rule)


def concrete(value: Any) -> Any:
    """Asserts that a sizable field's value is concrete and returns it.

    Component runtime code reads sized fields as ``Sizable[T]`` even though the central
    ``Component.__init__`` check guarantees concreteness by then — this helper is the
    read-side idiom that tells the type checker (and the reader) exactly that, with a
    runtime assertion instead of a bare cast.
    """
    if _needs_sizing(value):
        raise ConfigSizingError(f"expected a concrete value, got the unresolved {value!r}")
    return value


def law(rule: Any, reads: Optional[Tuple[Any, ...]] = None) -> SizingLaw:
    """Public spelling of law normalization, for laws declared outside ``sized_field``.

    Config classes use this to name a law once as a ``ClassVar`` and reuse it — in the
    field declaration and in presets that override the class law for one field (the
    per-preset escape hatch of spec §4): a preset may assign a ``SizingLaw`` as a field
    *value*, which the resolver treats like AUTO but computes with that law instead of
    the field's declared one. For callables, ``reads`` is mandatory (Size terms or
    plain fact names); see :class:`_FunctionLaw`.
    """
    return _normalize_law(rule, reads)


def _sizable_decoder(value_type: Optional[type]) -> Callable[[Any], Any]:
    """Builds the wire decoder of one sizable field.

    The field-level decoder *replaces* dataclasses_json's default handling, so for
    enum-typed sizable fields it must also perform the enum coercion the library would
    otherwise do (config enums decode by value, which equals the member name since the
    string-valued-enums cutover). ``value_type`` is only needed for those fields;
    plain-number fields pass values through untouched.
    """

    def decode(raw: Any) -> Any:
        if isinstance(raw, str) and raw == _AutoSize.WIRE_SPELLING:
            return AUTO
        if value_type is not None and raw is not None and not isinstance(raw, value_type):
            return value_type(raw)
        return raw

    return decode


def sized_field(
    *, rule: Any, default: Any = AUTO, value_type: Optional[type] = None,
    reads: Optional[Tuple[Any, ...]] = None, **field_kwargs: Any,
) -> Any:
    """Declares a sizable dataclass field: its law and its AUTO wire codec in one place.

    The law lands in the field metadata under :attr:`SizingLaw.METADATA_KEY`; the
    dataclasses_json encoder/decoder pair (``AUTO`` ↔ ``"AUTO"``) is merged into the same
    metadata dict, so the config author writes exactly one declaration and never sees
    codec machinery (same pattern as ``KpiEntry.tag``). The default is :data:`AUTO`,
    which shrinks presets: a preset that wants the field sized simply omits it.

    Args:
        rule: The sizing law — an expression over ``Size.*`` terms, a plain function of
            the context, or a constant.
        default: The field default; :data:`AUTO` unless a concrete nominal is wanted.
        value_type: The concrete type of the field where the wire form needs coercion —
            required for enum-typed sizable fields, since the injected decoder replaces
            the library's own enum handling. Plain numeric fields omit it.
        reads: The context facts a function law reads, mandatory for callables.
        **field_kwargs: Passed through to ``dataclasses.field`` (respecting an existing
            ``metadata`` mapping by merging into it).

    Returns:
        The ``dataclasses.field`` descriptor for the class body.
    """
    metadata: Dict[str, Any] = dict(field_kwargs.pop("metadata", {}) or {})
    metadata[SizingLaw.METADATA_KEY] = _normalize_law(rule, reads)
    metadata.update(dataclasses_json_config(encoder=_encode_sizable, decoder=_sizable_decoder(value_type)))
    # invalid-field-call is a false positive here: this helper returns the field()
    # descriptor for use inside a dataclass body, exactly like dataclasses_json.config.
    return dataclasses.field(default=default, metadata=metadata, **field_kwargs)  # pylint: disable=invalid-field-call


@dataclass(frozen=True)
class SizingContext:
    """The facts about the surrounding system that sizing laws may read (spec §4.1, §1).

    A small frozen snapshot, scope-resolved: today per building via
    :meth:`for_building`, later per unit/apartment via a ``for_unit`` constructor —
    components never slice building structure themselves. All fields are optional; a law
    reading an absent fact fails precisely, naming field and fact. Facts that derive
    from *sibling components* rather than the building (a boiler controller's power
    band) are added by the setup via :meth:`with_facts`.
    """

    heating_load_in_watt: Optional[float] = None
    heating_reference_temperature_in_celsius: Optional[float] = None
    number_of_apartments: Optional[float] = None
    conditioned_floor_area_in_m2: Optional[float] = None
    water_mass_flow_rate_in_kg_per_second: Optional[float] = None
    heat_distribution_system_type: Optional["HeatDistributionSystemType"] = None
    number_of_residents: Optional[float] = None
    maximal_thermal_power_in_watt: Optional[float] = None
    minimal_thermal_power_in_watt: Optional[float] = None

    def with_facts(self, **facts: Any) -> "SizingContext":
        """Returns a copy of this context with the given facts added or replaced.

        This is how a setup enriches the building-derived context with facts only it
        knows — typically values derived from sibling components, like the power band of
        the boiler a controller belongs to.
        """
        return dataclasses.replace(self, **facts)

    @classmethod
    def for_building(cls, building_config: "BuildingConfig") -> "SizingContext":
        """Derives the building-scope facts from a building configuration.

        Runs the TABULA/EPISCOPE lookup (``BuildingInformation``) exactly once and
        snapshots the derived quantities, so no sizing law ever triggers hidden file I/O
        and no setup recomputes the heating load per component. The import is local
        because this module must not import components at module level (see the module
        docstring); it is the one sanctioned exception to the package's layering rule.

        Args:
            building_config: The building whose derived facts the returned context carries.

        Returns:
            A context with the building-derived facts filled in.
        """
        from hisim.components.building.information import (  # pylint: disable=import-outside-toplevel
            BuildingInformation,
        )

        information = BuildingInformation(config=building_config)
        return cls(
            heating_load_in_watt=information.max_thermal_building_demand_in_watt,
            heating_reference_temperature_in_celsius=building_config.heating_reference_temperature_in_celsius,
            number_of_apartments=information.number_of_apartments,
            conditioned_floor_area_in_m2=information.scaled_conditioned_floor_area_in_m2,
        )


class Size:
    """Expression terms over the :class:`SizingContext` facts, exactly one per field.

    Terms keep the full field name including its unit — ``Size.HEATING_LOAD_IN_WATT`` —
    matching the repository's unit-explicit naming convention. The one-term-per-field
    invariant (the single registry of spec §4.1) is enforced by ``tests/test_sizing.py``
    rather than by dynamic class construction, so type checkers see every term.
    """

    HEATING_LOAD_IN_WATT: ClassVar[_FactTerm] = _FactTerm("heating_load_in_watt")
    HEATING_REFERENCE_TEMPERATURE_IN_CELSIUS: ClassVar[_FactTerm] = _FactTerm(
        "heating_reference_temperature_in_celsius")
    NUMBER_OF_APARTMENTS: ClassVar[_FactTerm] = _FactTerm("number_of_apartments")
    CONDITIONED_FLOOR_AREA_IN_M2: ClassVar[_FactTerm] = _FactTerm("conditioned_floor_area_in_m2")
    WATER_MASS_FLOW_RATE_IN_KG_PER_SECOND: ClassVar[_FactTerm] = _FactTerm(
        "water_mass_flow_rate_in_kg_per_second")
    HEAT_DISTRIBUTION_SYSTEM_TYPE: ClassVar[_FactTerm] = _FactTerm("heat_distribution_system_type")
    NUMBER_OF_RESIDENTS: ClassVar[_FactTerm] = _FactTerm("number_of_residents")
    MAXIMAL_THERMAL_POWER_IN_WATT: ClassVar[_FactTerm] = _FactTerm("maximal_thermal_power_in_watt")
    MINIMAL_THERMAL_POWER_IN_WATT: ClassVar[_FactTerm] = _FactTerm("minimal_thermal_power_in_watt")


def sizable_fields(config_class: type) -> Mapping[str, SizingLaw]:
    """Returns the sizable fields of a config class, mapped to their declared laws.

    A field is sizable exactly when it was declared through :func:`sized_field`; the
    mapping is empty for classes that declare none, which is what
    :func:`resolve_config` uses to tell the no-op case from the error case.
    """
    if not dataclasses.is_dataclass(config_class):
        return {}
    return {
        field.name: field.metadata[SizingLaw.METADATA_KEY]
        for field in dataclasses.fields(config_class)
        if SizingLaw.METADATA_KEY in field.metadata
    }


def auto_fields(config: Any) -> Tuple[str, ...]:
    """Names the fields of a config instance that currently carry the AUTO sentinel.

    Used by the central ``Component.__init__`` check: a component must never be
    constructed from a config that still requires sizing, no matter whether the config
    came from a preset, a scenario file or manual construction.
    """
    if not dataclasses.is_dataclass(config):
        return ()
    return tuple(
        field.name
        for field in dataclasses.fields(config)
        if _needs_sizing(getattr(config, field.name, None))
    )


def _needs_sizing(value: Any) -> bool:
    """True when a field value still requires resolution: AUTO, or a per-preset law."""
    return value is AUTO or isinstance(value, SizingLaw)


def describe_auto_fields(config: Any) -> str:
    """Renders the unresolved fields of a config with their laws, for error messages.

    The output is what makes the central check self-describing: each line names the
    field and the law that would compute it, so the fix (call ``resolve`` or assign a
    value) is obvious from the error alone.
    """
    laws = sizable_fields(type(config))
    lines = []
    for name in auto_fields(config):
        value = getattr(config, name)
        effective = value if isinstance(value, SizingLaw) else laws.get(name)
        lines.append(f"  {name}  <-  {effective.describe() if effective is not None else '<no law declared>'}")
    return "\n".join(lines)


def resolve_config(config: ConfigT, ctx: SizingContext) -> ConfigT:
    """Returns a copy of ``config`` in which every AUTO field is computed by its law.

    Semantics per spec §4.1: raises :class:`NothingToSizeError` when the class declares
    no sizable field at all (sizing a component that can never use it is a setup bug);
    is an idempotent no-op — still returning a fresh copy — when sizable fields exist
    but none currently says AUTO, so "size everything" loops are safe. The returned copy
    carries its provenance as the ``sizing_record`` attribute (a tuple of
    :class:`SizingRecordEntry`), deliberately not a dataclass field so that
    serialization, equality and ``dataclasses.replace`` all ignore it. A preset
    provenance stamp (see :class:`hisim.config.presets.Catalog`) is carried onto the copy
    the same way.

    Args:
        config: The config to resolve; it is never mutated.
        ctx: The facts to size against.

    Returns:
        A resolved copy of the config.

    Raises:
        NothingToSizeError: If the config class declares no sizable field.
        ConfigSizingError: If a law reads a fact the context does not carry, or a
            function law raises.
    """
    laws = sizable_fields(type(config))
    if not laws:
        raise NothingToSizeError(
            f"{type(config).__name__} declares no sizable field; passing a SizingContext "
            "to it cannot have any effect. Remove the resolve call."
        )
    resolved: Dict[str, Any] = {}
    record = []
    for field_name, declared_law in laws.items():
        current = getattr(config, field_name)
        if not _needs_sizing(current):
            continue
        # A preset may override the class law for one field by assigning a SizingLaw as
        # the field value (the per-preset escape hatch of spec §4) — e.g. the pellet
        # boiler's minimal power is a twelfth of its *sized* maximal power, while the gas
        # boiler's is a constant zero.
        effective_law = current if isinstance(current, SizingLaw) else declared_law
        try:
            value = effective_law.evaluate(ctx)
        except ConfigSizingError as error:
            raise ConfigSizingError(
                f"{type(config).__name__}.{field_name} <- {effective_law.describe()}: {error}"
            ) from error
        except Exception as error:  # pylint: disable=broad-except
            raise ConfigSizingError(
                f"{type(config).__name__}.{field_name} <- {effective_law.describe()} raised {error!r}"
            ) from error
        resolved[field_name] = value
        record.append(SizingRecordEntry(
            field=field_name, law=effective_law.describe(), facts_read=effective_law.facts_read(), value=value))
    result = dataclasses.replace(config, **resolved)  # type: ignore[type-var]
    setattr(result, "sizing_record", tuple(record))
    # Preset provenance rides along exactly like the sizing record: dataclasses.replace
    # copies fields only, so the non-field stamp must be carried over explicitly for the
    # template creator to still see which preset the resolved config came from.
    provenance = getattr(config, Catalog.PROVENANCE_ATTRIBUTE, None)
    if provenance is not None:
        setattr(result, Catalog.PROVENANCE_ATTRIBUTE, provenance)
    return result
