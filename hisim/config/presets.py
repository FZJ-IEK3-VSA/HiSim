"""Named defaults and named constructors of a config class, as decorated classmethods.

A component configuration usually has several defensible defaults — a condensing gas
boiler, an oil boiler, a pellet boiler, all on one ``GenericBoilerConfig`` — which HiSim
used to express as a hundred-odd ``get_*default*`` factory methods in sixty-odd naming
spellings. This module replaces that zoo with **two decorators on ordinary classmethods
of the config class itself**: :func:`preset` marks a named default, :func:`constructor`
marks a named constructor over an open identifier space (a TABULA building code, a
weather location, a device catalogue key). Both build the config's ``ComponentID`` from
an instance name the caller supplies, both are discoverable by tests, the JSON executor
and a future GUI palette without a regex over the source, and both keep the static
typing a plain method has: a misspelled name is a mypy ``attr-defined`` error and a
wrongly typed argument an ``arg-type`` error, because the decorators are typed as
identities and the class carries the methods themselves.

**Method-name prefixes are mandatory and carry meaning.** A preset is declared as
``preset_<wire_name>`` and its *wire name* — what a scenario file, the provenance stamp
and every enumeration use — is the method name with ``preset_`` stripped. A constructor
is declared as ``for_<...>`` or ``from_<...>`` and its wire name is the full method name.
The prefixes exist so that a call site reads for itself: ``GenericBoilerConfig
.preset_condensing_gas("Boiler")`` says what kind of thing it is fetching, where a bare
``GenericBoilerConfig.condensing_gas("Boiler")`` would read like an attribute access.

Neither kind of builder carries the *instance* name of the component it configures: the
name is the first argument, so the same preset serves a scenario with one boiler and a
scenario with three, and a setup must say what it is naming. A preset additionally stamps
the built instance with its **preset provenance** (the wire name), so later code — a
template writer, the audit record — can tell which preset a config came from. A
constructor stamps nothing: its identity is the arguments it was called with, not a name,
and a file that used one records the call rather than a preset reference.

The module is a leaf of the ``hisim.config`` package: it imports nothing at all from
HiSim, which is why :mod:`hisim.config.sizing` may depend on it (it carries the
provenance stamp onto resolved copies) and why :mod:`hisim.config.base` may call
:func:`check_builder_declarations` from ``ConfigBase.__init_subclass__``. In particular
it cannot import ``ComponentID`` from :mod:`hisim.config.base`, since that module imports
the sizing machinery — which is why every builder spells the ``ComponentID`` out itself.
"""

# clean

from __future__ import annotations

import enum
import functools
import inspect
from dataclasses import dataclass
from typing import Any, Callable, ClassVar, Dict, Iterable, Mapping, Optional, Tuple, TypeVar, overload

#: Type variable of the two decorators. It is deliberately unbounded and used as an
#: identity (``def preset(func: BuilderT) -> BuilderT``): that is what makes a decorated
#: classmethod stay a fully typed classmethod for mypy, so a typo in its name is an
#: ``attr-defined`` error and a wrong argument an ``arg-type`` error at the call site.
BuilderT = TypeVar("BuilderT")


class BuilderKind(enum.Enum):
    """Whether a decorated classmethod is a named default or a named constructor.

    ``PRESET`` is a complete, ready-to-use default of the class taking nothing but the
    instance name; ``CONSTRUCTOR`` is a parameterised builder over an open identifier
    space (locations, building codes, catalogue devices) whose parameters a caller — or a
    scenario file — supplies. The distinction is what keeps a lookup class from minting
    hundreds of meaningless preset names that would then be wire format forever.
    """

    PRESET = "preset"
    CONSTRUCTOR = "constructor"

    def explain(self) -> str:
        """Renders the kind as the phrase an error message or a description shows."""
        if self is BuilderKind.PRESET:
            return "preset"
        return "named constructor"


@dataclass(frozen=True)
class ConfigBuilder:
    """One discovered builder of a config class: its wire name, kind, note and callable.

    An entry of the registries :func:`presets_of` and :func:`constructors_of` return. It
    is what a test, the introspection surface or a scenario loader works with: ``name`` is
    the wire name (a preset's method name without the ``preset_`` prefix, a constructor's
    method name as written), ``method_name`` the attribute to call in Python source,
    ``build`` the classmethod already bound to the config class, and ``function`` the
    undecorated function underneath it, whose signature the introspection reads.

    The class also holds the spelling constants the whole preset mechanism agrees on — the
    provenance attribute, the marker attribute and the two method-name prefixes — so that
    the decorators, the discovery, the resolver and their tests never spell them twice.
    """

    #: Name of the non-field attribute a preset stamps onto every instance it builds,
    #: carrying the preset's wire name (e.g. ``"oil"``). Read it through
    #: :func:`preset_provenance` rather than with a raw ``getattr``.
    PROVENANCE_ATTRIBUTE: ClassVar[str] = "preset_provenance"

    #: Attribute the decorators set on the wrapper function to mark it as a builder and
    #: to carry its declaration data. Private by convention: discovery goes through
    #: :func:`presets_of` and :func:`constructors_of`, never through this name.
    MARKER_ATTRIBUTE: ClassVar[str] = "__hisim_config_builder__"

    #: Mandatory method-name prefix of a preset; the rest of the method name is the wire
    #: name that scenario files, provenance stamps and enumerations use.
    PRESET_PREFIX: ClassVar[str] = "preset_"

    #: Mandatory method-name prefixes of a named constructor. Unlike the preset prefix
    #: these stay part of the wire name, because ``for_tabula_code`` reads as a whole.
    CONSTRUCTOR_PREFIXES: ClassVar[Tuple[str, ...]] = ("for_", "from_")

    name: str
    method_name: str
    kind: BuilderKind
    note: Optional[str]
    build: Callable[..., Any]
    function: Callable[..., Any]


@dataclass(frozen=True)
class _BuilderMarker:
    """The declaration data a decorator attaches to the wrapper function it creates.

    Kept as a small frozen record rather than as loose attributes so that discovery can
    recognise a builder with one ``isinstance`` check and cannot mistake an unrelated
    function attribute for a preset declaration.
    """

    kind: BuilderKind
    name: str
    note: Optional[str]
    function: Callable[..., Any]


def _wire_name(kind: BuilderKind, method_name: str) -> str:
    """Derives the wire name of a builder from its method name, enforcing the prefix.

    A preset's wire name is its method name without ``preset_``; a constructor's wire name
    is its method name in full, which must start with ``for_`` or ``from_``. The prefixes
    are mandatory because they are what makes a call site self-explanatory, and enforcing
    them here means the mistake is reported while the class body is still executing.

    Raises:
        ValueError: If the method name lacks the prefix its kind requires, naming the
            method and the required form.
    """
    if kind is BuilderKind.PRESET:
        prefix = ConfigBuilder.PRESET_PREFIX
        if not method_name.startswith(prefix) or len(method_name) == len(prefix):
            raise ValueError(
                f"@preset method '{method_name}' must be named '{prefix}<wire_name>' "
                f"(for example '{prefix}standard'); the wire name is the method name "
                "without that prefix."
            )
        return method_name[len(prefix):]
    if not any(method_name.startswith(prefix) for prefix in ConfigBuilder.CONSTRUCTOR_PREFIXES):
        raise ValueError(
            f"@constructor method '{method_name}' must be named "
            f"{' or '.join(prefix + '<...>' for prefix in ConfigBuilder.CONSTRUCTOR_PREFIXES)} "
            "(for example 'for_tabula_code'); the wire name is the full method name."
        )
    return method_name


def _check_signature(kind: BuilderKind, method_name: str, function: Callable[..., Any]) -> None:
    """Checks that a builder takes the instance name first and nothing unusable after it.

    A preset takes exactly ``(cls, name)``: anything a preset would need beyond the name
    makes it a constructor, not a default. A constructor takes ``(cls, name, *params)``
    where every parameter is annotated and usable as a keyword argument, because that is
    what lets a scenario file spell the call as a mapping and the introspection describe
    it without reading the source.

    Raises:
        ValueError: If the first parameter after ``cls`` is not ``name``, if a preset
            declares further parameters, or if a constructor parameter is variadic,
            positional-only or unannotated.
    """
    parameters = list(inspect.signature(function).parameters.values())[1:]
    if not parameters or parameters[0].name != "name":
        raise ValueError(
            f"{kind.explain()} '{method_name}' must take the instance name as its first "
            "argument after cls, spelled 'name': the builder constructs the ComponentID "
            "from it and never carries a component name of its own."
        )
    if kind is BuilderKind.PRESET and len(parameters) > 1:
        extra = [parameter.name for parameter in parameters[1:]]
        raise ValueError(
            f"preset '{method_name}' takes {extra} besides the instance name; a preset is "
            "a complete default taking only 'name' — declare it as a @constructor instead."
        )
    for parameter in parameters:
        if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD, parameter.POSITIONAL_ONLY):
            raise ValueError(
                f"{kind.explain()} '{method_name}': parameter '{parameter.name}' is "
                "variadic or positional-only; every parameter must be usable as a keyword "
                "argument so a file or a tool can name it."
            )
        if parameter.annotation is inspect.Parameter.empty:
            raise ValueError(
                f"{kind.explain()} '{method_name}': parameter '{parameter.name}' has no "
                "type annotation; the description surface reports the type of every "
                "parameter, so an unannotated one cannot be offered to a caller."
            )


def _declare(kind: BuilderKind, decorated: Any, note: Optional[str]) -> Any:
    """Turns a decorated classmethod into a marked, provenance-stamping builder.

    Wraps the underlying function so that a preset stamps its wire name onto every
    instance it builds, attaches the :class:`_BuilderMarker` that makes the method
    discoverable, and hands back a fresh ``classmethod``. Everything that can be checked
    without knowing the owning class — the method-name prefix and the signature — is
    checked here, while the class body is still executing, so the traceback points at the
    offending method rather than at the first use of it.

    Raises:
        TypeError: If the decorator was not applied above ``@classmethod``, which is the
            one mistake that would otherwise produce a silently unbound builder.
        ValueError: For a wrong method-name prefix or an unusable signature, see
            :func:`_wire_name` and :func:`_check_signature`.
    """
    if not isinstance(decorated, classmethod):
        raise TypeError(
            f"@{kind.value} must be applied above @classmethod: write '@{kind.value}' on "
            f"top of '@classmethod', got {decorated!r}. A builder is a classmethod because "
            "it constructs the class it is declared on."
        )
    function = decorated.__func__
    method_name = function.__name__
    name = _wire_name(kind, method_name)
    _check_signature(kind, method_name, function)

    @functools.wraps(function)
    def wrapper(cls: Any, *args: Any, **kwargs: Any) -> Any:
        """Builds the config and, for a preset, stamps the wire name onto the instance."""
        instance = function(cls, *args, **kwargs)
        if kind is BuilderKind.PRESET:
            setattr(instance, ConfigBuilder.PROVENANCE_ATTRIBUTE, name)
        return instance

    setattr(wrapper, ConfigBuilder.MARKER_ATTRIBUTE, _BuilderMarker(kind, name, note, function))
    return classmethod(wrapper)


@overload
def preset(func: BuilderT) -> BuilderT:
    ...


@overload
def preset(*, note: str) -> Callable[[BuilderT], BuilderT]:
    ...


def preset(func: Any = None, *, note: Optional[str] = None) -> Any:
    """Marks a classmethod as one named default of its config class.

    Declared above ``@classmethod`` on a method named ``preset_<wire_name>`` taking
    exactly ``(cls, name: str)`` and returning a fresh instance whose ``ComponentID`` is
    built from ``name``. The wire name — the method name without the ``preset_`` prefix —
    is what a scenario file references, what :func:`preset_provenance` reports and what
    the registries key on; the prefix itself exists so that a call site
    (``GenericBoilerConfig.preset_oil("BackupBoiler")``) says what it is doing. Usable
    bare (``@preset``) or with an author note (``@preset(note="VDI 4645")``) recording
    where the default comes from; the note is optional and never a placeholder.

    Every call builds a **fresh instance** (setups mutate configs freely, so sharing one
    would be a footgun) and nothing but the name may vary between two builds: the method
    is otherwise pure, which is what lets a scenario file record ``"preset": "<name>"``
    plus a sparse override instead of a full config dump.

    Args:
        func: The ``classmethod`` object being decorated, when used bare.
        note: Where this default comes from, when the author knows it (a catalogue device,
            a standard, a datasheet).

    Returns:
        The decorated classmethod, typed as itself so that the call site keeps full static
        checking, or the decorator to apply when called with ``note=``.
    """
    if func is None:
        return lambda decorated: _declare(BuilderKind.PRESET, decorated, note)
    return _declare(BuilderKind.PRESET, func, note)


@overload
def constructor(func: BuilderT) -> BuilderT:
    ...


@overload
def constructor(*, note: str) -> Callable[[BuilderT], BuilderT]:
    ...


def constructor(func: Any = None, *, note: Optional[str] = None) -> Any:
    """Marks a classmethod as a named constructor over an open identifier space.

    Declared above ``@classmethod`` on a method named ``for_<...>`` or ``from_<...>``
    taking ``(cls, name: str, *params)`` where every parameter is annotated and usable as
    a keyword argument. It exists for the classes whose variation is a *lookup* rather
    than a handful of variants — a TABULA building code, a weather location, a catalogue
    device — where minting one preset per identifier would turn hundreds of arbitrary
    strings into permanent wire format. Such a class ships one preset (the repo's usual
    choice, typically delegating to the constructor) plus this constructor for everything
    else. The wire name is the full method name, prefix included, because
    ``for_tabula_code`` reads as a whole.

    A constructor stamps no provenance: what identifies its result is the arguments it was
    called with, not a name, so a file that used one records the call itself. A preset
    delegating to a constructor still stamps its own wire name, since the stamp happens
    after the delegate returns.

    Args:
        func: The ``classmethod`` object being decorated, when used bare.
        note: Where the constructed values come from, when the author knows it.

    Returns:
        The decorated classmethod, typed as itself so that the call site keeps full static
        checking, or the decorator to apply when called with ``note=``.
    """
    if func is None:
        return lambda decorated: _declare(BuilderKind.CONSTRUCTOR, decorated, note)
    return _declare(BuilderKind.CONSTRUCTOR, func, note)


def _markers_of(config_class: type) -> Tuple[Tuple[str, _BuilderMarker], ...]:
    """Collects the ``(method_name, marker)`` pairs a class declares, in declaration order.

    The class's own methods come last: the method resolution order is walked in reverse so
    that an inherited builder keeps the position it had on the base class while a subclass
    redeclaring it replaces it in place. Declaration order is the registry order, which is
    what makes the first-declared preset the canonical one.
    """
    found: Dict[str, _BuilderMarker] = {}
    for klass in reversed(getattr(config_class, "__mro__", (config_class,))):
        for method_name, value in vars(klass).items():
            if not isinstance(value, classmethod):
                continue
            marker = getattr(value.__func__, ConfigBuilder.MARKER_ATTRIBUTE, None)
            if isinstance(marker, _BuilderMarker):
                found[method_name] = marker
    return tuple(found.items())


def _builders_of(config_class: type, kind: BuilderKind) -> Mapping[str, ConfigBuilder]:
    """Builds the registry of one kind of builder, keyed by wire name in declaration order."""
    registry: Dict[str, ConfigBuilder] = {}
    for method_name, marker in _markers_of(config_class):
        if marker.kind is not kind:
            continue
        registry[marker.name] = ConfigBuilder(
            name=marker.name,
            method_name=method_name,
            kind=marker.kind,
            note=marker.note,
            build=getattr(config_class, method_name),
            function=marker.function,
        )
    return registry


def presets_of(config_class: type) -> Mapping[str, ConfigBuilder]:
    """Returns the named defaults of a config class by wire name, in declaration order.

    The enumeration a contract test, the JSON executor, a schema exporter and a future GUI
    palette all read instead of grepping the source. Declaration order is the registry
    order and the first entry is the canonical default (see :func:`canonical_preset`).
    Classes with no preset at all return an empty mapping, which is legal: not every
    configuration has a defensible default.

    Args:
        config_class: Any class; one that declares no ``@preset`` simply yields nothing.

    Returns:
        A mapping from wire name to :class:`ConfigBuilder`, freshly built on every call.
    """
    return _builders_of(config_class, BuilderKind.PRESET)


def constructors_of(config_class: type) -> Mapping[str, ConfigBuilder]:
    """Returns the named constructors of a config class by wire name, in declaration order.

    The counterpart of :func:`presets_of` for the lookup-parameterised classes: it is how a
    tool discovers that a config can be built for an arbitrary building code or location,
    and — through :attr:`ConfigBuilder.function` — which parameters that takes.

    Args:
        config_class: Any class; one that declares no ``@constructor`` yields nothing.

    Returns:
        A mapping from wire name (the full method name) to :class:`ConfigBuilder`.
    """
    return _builders_of(config_class, BuilderKind.CONSTRUCTOR)


def canonical_preset(config_class: type) -> Optional[ConfigBuilder]:
    """Returns the canonical default of a config class, which is its first-declared preset.

    Returned as the :class:`ConfigBuilder` rather than as a built config, because building
    one still requires the caller to say what the instance is named:
    ``canonical_preset(SomeConfig).build("Boiler")``. ``None`` for a class that declares no
    preset, so a caller that needs one must say what it does in that case.
    """
    presets = presets_of(config_class)
    return next(iter(presets.values()), None)


def preset_provenance(config: Any) -> Optional[str]:
    """Returns the wire name of the preset a config instance was built from, or ``None``.

    The name is stamped onto the instance at build time and carried through
    ``resolve_config`` — the exact string a v2 scenario entry spells as its ``"preset"``
    value. Manually constructed configs, configs built by a named constructor and configs
    deserialized from files carry no provenance and return ``None``, which is what tells
    the template creator to fall back to a full config dump.
    """
    provenance = getattr(config, ConfigBuilder.PROVENANCE_ATTRIBUTE, None)
    return provenance if isinstance(provenance, str) else None


def check_builder_declarations(config_class: type, field_names: Iterable[str]) -> None:
    """Rejects a class whose builder names collide with its own fields, at class creation.

    Called from ``ConfigBase.__init_subclass__``, i.e. while the class object is being
    created and *before* ``@dataclass`` processes it. That is the earliest point at which
    both the decorated methods and the field annotations are visible, and it is early on
    purpose: a method sharing a name with an annotated field would otherwise be silently
    taken by ``@dataclass`` as that field's default value, producing a config whose field
    holds a classmethod. Both the wire name and the method name are checked — the wire
    name because it is what a file references, the method name because it is what Python
    resolves.

    Args:
        config_class: The class under construction.
        field_names: The names that will become dataclass fields of the class, own
            annotations and inherited fields alike.

    Raises:
        ValueError: If a preset's wire name, or any builder's method name, is also a field
            name of the class.
    """
    known = set(field_names)
    clashes = []
    for method_name, marker in _markers_of(config_class):
        for spelling in sorted({method_name, marker.name}):
            if spelling in known:
                clashes.append(f"{marker.kind.explain()} '{method_name}' collides with field '{spelling}'")
    if clashes:
        raise ValueError(
            f"{config_class.__name__}: " + "; ".join(sorted(clashes)) + ". Rename the builder or the field: "
            "a name serving as both would be swallowed by the dataclass machinery."
        )
