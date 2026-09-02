"""Sizable config fields: the AUTO sentinel, ``sized_field`` and the resolver (design B).

This module holds the *field* half of the declarative sizing design: everything a
config class needs so that a preset, a scenario file or a hand-written
setup only ever says *concrete value* or *AUTO* for a field, while the field's sizing
law lives once at its declaration:

- :data:`AUTO` — the sentinel a field carries when its value should be computed.
- :func:`sized_field` — a ``dataclasses.field`` wrapper that attaches the field's sizing
  law and the AUTO wire codec in a single declaration.
- :func:`resolve_config` — the one shared resolver turning AUTO fields into numbers,
  used through ``ConfigBase.resolve``; it orders a config's sizable fields so that a law
  reading a sibling field always sees that sibling's final value.
- :class:`OwnFields` — the read-only view of the config under resolution through which a
  ``Self("field")`` term reads its sibling.
- :func:`auto_fields` / :func:`describe_auto_fields` — the queries behind the central
  ``Component.__init__`` check that no unresolved config ever reaches a component.

The laws themselves (the ``SizingLaw`` algebra and the sizing errors) live in
:mod:`hisim.config.laws`; the facts laws read (``SizingContext`` and the ``Size`` term
vocabulary) in :mod:`hisim.config.context`; the named-preset half of design B in
:mod:`hisim.config.presets`; and the cross-component fact resolution in
:mod:`hisim.config.engine`.

Per the ``hisim.config`` layering rule the module imports nothing from the rest of HiSim
— except ``hisim.log``, the package's sanctioned logging exception (see the layering
rule in ``hisim/config/__init__.py``): ``component.py`` imports this module (for the
central AUTO check and ``ConfigBase.resolve``), so any other hisim import here would
close a cycle.
"""

# clean

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import (
    TYPE_CHECKING, Any, Callable, ClassVar, Dict, Iterable, List, Mapping, Optional, Set, Tuple, TypeVar, Union,
)

from dataclasses_json import config as dataclasses_json_config

from hisim import log
from hisim.config.laws import (
    ConfigSizingError,
    NothingToSizeError,
    OwnFieldsView,
    SizingLaw,
    normalize_law,
)
from hisim.config.presets import ConfigBuilder

if TYPE_CHECKING:
    from hisim.config.context import SizingContext

ConfigT = TypeVar("ConfigT")


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
    #: file cannot express a *device* literally named "AUTO" — a deliberate trade-off.
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


#: Type alias for a sizable field's value: the concrete value, the AUTO sentinel, or —
#: the per-preset escape hatch — a SizingLaw overriding the field's declared law.
Sizable = Union[ConfigT, _AutoSize, SizingLaw]


@dataclass(frozen=True)
class SizingRecordEntry:
    """The provenance of one resolved field: which law computed which value from what.

    A tuple of these is attached to a resolved config as its ``sizing_record`` (a plain
    attribute, deliberately not a dataclass field, so serialization and equality ignore
    it); the audit-artifact writer reads it to show per field how a value came to be.
    ``inputs`` carries the *values* the law actually read, as ``(source, value)`` pairs,
    so a wrong result is diagnosable from the record alone — ``facts_read`` alone would
    name the ingredients but not what they were. A source is the qualified
    ``"<provider>.<fact>"`` when the fact engine bound the fact to a named provider, the
    bare fact name when the config was resolved against a hand-built context, and
    ``"self.<field>"`` for a sibling field of the same config.
    """

    field: str
    law: str
    facts_read: Tuple[str, ...]
    value: Any
    inputs: Tuple[Tuple[str, Any], ...] = ()


def _encode_sizable(value: Any) -> Any:
    """Turns the AUTO sentinel into its wire spelling; concrete values pass through."""
    return _AutoSize.WIRE_SPELLING if value is AUTO else value


def _sizable_decoder(value_type: Optional[type]) -> Callable[[Any], Any]:
    """Builds the wire decoder of one sizable field.

    The field-level decoder *replaces* dataclasses_json's default handling, so for
    enum-typed sizable fields it must also perform the enum coercion the library would
    otherwise do (config enums decode by value, which equals the member name since the
    string-valued-enums cutover). ``value_type`` is only needed for those fields;
    plain-number fields pass values through untouched.
    """

    def decode(raw: Any) -> Any:
        # The sentinel itself can arrive here, not only its wire spelling: dataclasses_json runs the
        # field decoder over a *missing* key's default as well as over a present value. Coercing the
        # sentinel through ``value_type`` is wrong for every type and silent for one -- ``str(AUTO)`` is
        # the wire spelling, so a string-typed sized field left out of a config block quietly turned
        # from "size me" into the literal text "AUTO", and the engine, seeing a concrete value, left
        # it alone. The sentinel passes through untouched, whatever the field's type.
        if raw is AUTO or (isinstance(raw, str) and raw == _AutoSize.WIRE_SPELLING):
            return AUTO
        if value_type is not None and raw is not None and not isinstance(raw, value_type):
            return value_type(raw)
        return raw

    return decode


class SizedFieldMetadata:
    """The keys :func:`sized_field` writes into a dataclass field's metadata mapping.

    Field metadata is a shared, untyped mapping, so the two entries the sizing machinery
    owns are named here once instead of being spelled as literals at every read site. The
    law key is the one the law algebra itself defines; the note key holds the optional
    author-supplied source of a value ("VDI 4645", a datasheet), which the audit trail
    renders next to the computed number.
    """

    LAW: ClassVar[str] = SizingLaw.METADATA_KEY
    NOTE: ClassVar[str] = "hisim_sizing_note"
    #: The concrete type the field's wire decoder coerces into, recorded so that the
    #: class-creation check can tell a declared ``value_type`` from a forgotten one.
    VALUE_TYPE: ClassVar[str] = "hisim_sizing_value_type"


def sized_field(
    *, rule: Any, default: Any = AUTO, value_type: Optional[type] = None,
    reads: Optional[Tuple[Any, ...]] = None, fields: Optional[Tuple[str, ...]] = None,
    note: Optional[str] = None, **field_kwargs: Any,
) -> Any:
    """Declares a sizable dataclass field: its law and its AUTO wire codec in one place.

    The law lands in the field metadata under :attr:`SizedFieldMetadata.LAW`; the
    dataclasses_json encoder/decoder pair (``AUTO`` ↔ ``"AUTO"``) is merged into the same
    metadata dict, so the config author writes exactly one declaration and never sees
    codec machinery (same pattern as ``KpiEntry.tag``). The default is :data:`AUTO`,
    which shrinks presets: a preset that wants the field sized simply omits it.

    Args:
        rule: The sizing law — an expression over ``Size.*`` and ``Self(...)`` terms, a
            plain function of the context, or a constant.
        default: The field default; :data:`AUTO` unless a concrete nominal is wanted.
        value_type: The concrete type of the field where the wire form needs coercion —
            required for enum-typed sizable fields, since the injected decoder replaces
            the library's own enum handling. Plain numeric fields omit it.
        reads: The context facts a function law reads, mandatory for callables.
        fields: The sibling fields of the same config a function law reads; declaring
            them switches the callable to the two-argument ``fn(ctx, own)`` protocol.
        note: Where the value comes from, when the author knows it (a standard, a
            datasheet). Optional and never a placeholder; it is recorded as-is and
            rendered by the audit trail so a hard-coded constant can cite its source.
        **field_kwargs: Passed through to ``dataclasses.field`` (respecting an existing
            ``metadata`` mapping by merging into it).

    Returns:
        The ``dataclasses.field`` descriptor for the class body.
    """
    metadata: Dict[str, Any] = dict(field_kwargs.pop("metadata", {}) or {})
    metadata[SizedFieldMetadata.LAW] = normalize_law(rule, reads, fields)
    if note is not None:
        metadata[SizedFieldMetadata.NOTE] = note
    if value_type is not None:
        metadata[SizedFieldMetadata.VALUE_TYPE] = value_type
    metadata.update(dataclasses_json_config(encoder=_encode_sizable, decoder=_sizable_decoder(value_type)))
    # invalid-field-call is a false positive here: this helper returns the field()
    # descriptor for use inside a dataclass body, exactly like dataclasses_json.config.
    return dataclasses.field(default=default, metadata=metadata, **field_kwargs)  # pylint: disable=invalid-field-call


def field_notes(config_class: type) -> Mapping[str, str]:
    """Returns the author notes declared on a config class's sizable fields, by field name.

    Only fields whose ``sized_field(note=...)`` was actually given appear, so an empty
    mapping means the class documents none of its sized values. The audit trail and the
    later introspection surface read the notes through this one accessor rather than
    poking at the metadata mapping themselves.
    """
    if not dataclasses.is_dataclass(config_class):
        return {}
    return {
        field.name: field.metadata[SizedFieldMetadata.NOTE]
        for field in dataclasses.fields(config_class)
        if SizedFieldMetadata.NOTE in field.metadata
    }


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


def sizable_fields(config_class: type) -> Mapping[str, SizingLaw]:
    """Returns the sizable fields of a config class, mapped to their declared laws.

    A field is sizable exactly when it was declared through :func:`sized_field`; the
    mapping is empty for classes that declare none, which is what
    :func:`resolve_config` uses to tell the no-op case from the error case.
    """
    if not dataclasses.is_dataclass(config_class):
        return {}
    return {
        field.name: field.metadata[SizedFieldMetadata.LAW]
        for field in dataclasses.fields(config_class)
        if SizedFieldMetadata.LAW in field.metadata
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


class OwnFields(OwnFieldsView):
    """The read-only view of the config under resolution that ``Self(...)`` terms read.

    It starts out holding every field's current value and remembers which sizable fields
    are still waiting for their law; reading one of those raises instead of handing out a
    sentinel, so a resolver ordering bug can never silently feed ``AUTO`` into a formula.
    As :func:`resolve_config` computes a field, it records the result here, which is what
    makes a sibling read see the field's *final* value — pinned by the author or freshly
    sized, the reader cannot tell the difference and does not need to.
    """

    def __init__(self, config: Any, pending: Iterable[str]) -> None:
        """Snapshots the config's current field values and the fields still to be sized."""
        self._class_name = type(config).__name__
        self._values: Dict[str, Any] = {
            field.name: getattr(config, field.name) for field in dataclasses.fields(config)
        }
        self._pending: Set[str] = set(pending)

    def value_of(self, field_name: str) -> Any:
        """Returns the named field's final value, refusing unknown and unresolved fields."""
        if field_name not in self._values:
            raise ConfigSizingError(
                f"{self._class_name} has no field '{field_name}', which a law reads via "
                f"Self(...); known fields: {sorted(self._values)}"
            )
        if field_name in self._pending:
            raise ConfigSizingError(
                f"{self._class_name}.{field_name} is read via Self(...) before its own law "
                "ran; the resolver orders sibling reads, so this indicates a resolver bug."
            )
        return self._values[field_name]

    def record(self, field_name: str, value: Any) -> None:
        """Publishes a freshly computed field value to later sibling reads."""
        self._values[field_name] = value
        self._pending.discard(field_name)

    def known_fields(self) -> Set[str]:
        """Names every field of the config, for validating ``Self(...)`` references."""
        return set(self._values)


def _resolution_order(
    config_class: type, pending: Mapping[str, SizingLaw], known_fields: Set[str]
) -> List[str]:
    """Orders the fields still to be sized so that every sibling read sees a final value.

    A field that reads a sibling which is itself being sized must be evaluated after it;
    a field reading only pinned siblings or plain context facts depends on nothing. The
    order is a depth-first topological sort over the declaration order, so it is fully
    deterministic and independent of how the author happened to arrange the fields.

    Raises:
        ConfigSizingError: If a law names a field the class does not have, or if the
            sibling reads form a cycle — both name the class and the fields involved.
    """
    for field_name, declared_law in pending.items():
        for read in declared_law.fields_read():
            if read not in known_fields:
                raise ConfigSizingError(
                    f"{config_class.__name__}.{field_name} reads the sibling field "
                    f"'{read}' via Self(...), which the class does not declare; known "
                    f"fields: {sorted(known_fields)}"
                )
    order: List[str] = []
    finished: Set[str] = set()
    visiting: List[str] = []

    def visit(field_name: str) -> None:
        """Emits one field after everything it reads, detecting cycles on the way."""
        if field_name in finished:
            return
        if field_name in visiting:
            cycle = visiting[visiting.index(field_name):]
            if len(cycle) == 1:
                detail = f"field {cycle[0]} reads itself"
            elif len(cycle) == 2:
                detail = f"fields {cycle[0]} and {cycle[1]} read each other"
            else:
                detail = "fields " + ", ".join(cycle) + " form a cycle"
            raise ConfigSizingError(f"{config_class.__name__}: {detail} via Self")
        visiting.append(field_name)
        for dependency in pending[field_name].fields_read():
            if dependency in pending:
                visit(dependency)
        visiting.pop()
        finished.add(field_name)
        order.append(field_name)

    for name in pending:
        visit(name)
    return order


def _qualified_source(fact: str, fact_sources: Optional[Mapping[str, str]]) -> str:
    """Renders one fact as ``"<provider>.<fact>"`` when the provider is known, else bare."""
    if fact_sources is None:
        return fact
    provider = fact_sources.get(fact)
    return f"{provider}.{fact}" if provider else fact


def resolve_config(
    config: ConfigT, ctx: "SizingContext", fact_sources: Optional[Mapping[str, str]] = None
) -> ConfigT:
    """Returns a copy of ``config`` in which every AUTO field is computed by its law.

    Raises :class:`NothingToSizeError` when the class declares
    no sizable field at all (sizing a component that can never use it is a setup bug);
    is an idempotent no-op — still returning a fresh copy — when sizable fields exist
    but none currently says AUTO, so "size everything" loops are safe. Fields are
    evaluated in dependency order rather than declaration order: a law reading a sibling
    through ``Self("other_field")`` always sees that sibling's final value, whether the
    author pinned it or its own law produced it. The returned copy
    carries its provenance as the ``sizing_record`` attribute (a tuple of
    :class:`SizingRecordEntry`), deliberately not a dataclass field so that
    serialization, equality and ``dataclasses.replace`` all ignore it. A preset
    provenance stamp (see the ``preset`` decorator of :mod:`hisim.config.presets`) is
    carried onto the copy the same way.

    Args:
        config: The config to resolve; it is never mutated.
        ctx: The facts to size against.
        fact_sources: Fact name → the name of the config instance that provided it, as
            the fact engine bound them. Given, the record's inputs name their provider
            (``"boiler.maximal_thermal_power_in_watt"``); omitted — a config resolved
            against a hand-built context — the inputs carry the bare fact name.

    Returns:
        A resolved copy of the config.

    Raises:
        NothingToSizeError: If the config class declares no sizable field.
        ConfigSizingError: If a law reads a fact the context does not carry, names a
            sibling field that does not exist, sits in a cycle of sibling reads, or a
            function law raises.
    """
    laws = sizable_fields(type(config))
    if not laws:
        raise NothingToSizeError(
            f"{type(config).__name__} declares no sizable field; passing a SizingContext "
            "to it cannot have any effect. Remove the resolve call."
        )
    # A preset may override the class law for one field by assigning a SizingLaw as the
    # field value (the per-preset escape hatch) — e.g. the pellet boiler's minimal power
    # is a twelfth of its own maximal power, while the gas boiler's is a constant zero.
    pending: Dict[str, SizingLaw] = {}
    for field_name, declared_law in laws.items():
        current = getattr(config, field_name)
        if not _needs_sizing(current):
            continue
        pending[field_name] = current if isinstance(current, SizingLaw) else declared_law
    own = OwnFields(config, pending)
    resolved: Dict[str, Any] = {}
    record = []
    for field_name in _resolution_order(type(config), pending, own.known_fields()):
        effective_law = pending[field_name]
        try:
            value = effective_law.evaluate(ctx, own)
        except NotImplementedError:
            # A declared-but-unimplemented term (Many(...)) must surface as itself rather
            # than as a generic "law raised" wrapper, so the parking-lot message survives.
            raise
        except ConfigSizingError as error:
            raise ConfigSizingError(
                f"{type(config).__name__}.{field_name} <- {effective_law.describe()}: {error}"
            ) from error
        except Exception as error:  # pylint: disable=broad-except
            raise ConfigSizingError(
                f"{type(config).__name__}.{field_name} <- {effective_law.describe()} raised {error!r}"
            ) from error
        resolved[field_name] = value
        own.record(field_name, value)
        facts = tuple(fact for fact, _cardinality in effective_law.facts_read())
        record.append(SizingRecordEntry(
            field=field_name, law=effective_law.describe(), facts_read=facts, value=value,
            inputs=tuple(
                (_qualified_source(fact, fact_sources), getattr(ctx, fact, None)) for fact in facts
            ) + tuple(
                (f"self.{name}", own.value_of(name)) for name in effective_law.fields_read()
            )))
    result = dataclasses.replace(config, **resolved)  # type: ignore[type-var]
    setattr(result, "sizing_record", tuple(record))
    if record:
        key = getattr(getattr(config, "component_id", None), "key", type(config).__name__)
        log.debug(
            f"Sizing: resolved {type(config).__name__} '{key}': "
            + "; ".join(f"{entry.field}={entry.value!r} <- {entry.law}" for entry in record)
        )
    # Preset provenance rides along exactly like the sizing record: dataclasses.replace
    # copies fields only, so the non-field stamp must be carried over explicitly for the
    # template creator to still see which preset the resolved config came from.
    provenance = getattr(config, ConfigBuilder.PROVENANCE_ATTRIBUTE, None)
    if provenance is not None:
        setattr(result, ConfigBuilder.PROVENANCE_ATTRIBUTE, provenance)
    return result
