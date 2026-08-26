"""Machine-readable description of a config class: fields, presets, sizing, facts.

Everything a tool needs to know about a configuration class without reading its source:
which fields it has and what they default to, which named presets it ships and what each
of them leaves to be sized, how every sizable field is computed and from which facts,
which named constructors it offers for the identifier spaces presets cannot cover, and
which facts the class contributes to the rest of a scenario. A schema exporter, a
``describe`` command line, an audit-artifact renderer and a parameter sweep all ask the
same handful of questions, so they are answered once here instead of once per tool against
the dataclass internals.

The module is pure: :func:`describe_config` reads class declarations and builds each
preset once with a throwaway instance name, and does nothing else — no I/O, no logging, no
component imports. Like every module of the ``hisim.config`` package it imports nothing
from the rest of HiSim, which is what lets a component package import it freely.
"""

# clean

from __future__ import annotations

import dataclasses
import enum
import inspect
from dataclasses import dataclass
from typing import Any, ClassVar, Optional, Tuple

from hisim.config.contributions import FactContribution
from hisim.config.laws import Cardinality, SizingLaw
from hisim.config.presets import canonical_preset, constructors_of, presets_of
from hisim.config.sizing import auto_fields, field_notes, sizable_fields


class SizableFieldKind(enum.Enum):
    """Whether a sizable field derives its value from the system or from its author.

    ``LAW`` is the normal case: the field's value is computed from facts the surrounding
    scenario provides, or from a sibling field of the same config. ``CONSTANT`` marks a law
    that reads nothing at all — the author's usual choice written as a law so the field can
    still say ``AUTO`` — and exists so that a reader of a description is never told that a
    hard-coded default was "derived from the system".
    """

    LAW = "law"
    CONSTANT = "constant"

    def explain(self) -> str:
        """Renders the kind as the phrase a description surface shows to a human reader."""
        if self is SizableFieldKind.CONSTANT:
            return "author default (constant law)"
        return "derived from the system"


@dataclass(frozen=True)
class FieldInfo:
    """One settable field of a config class: its name, type, default and sizability.

    ``type_name`` is the annotation as written in the class body (a string, since config
    modules are read without evaluating their annotations), which is what a schema exporter
    wants to show. ``default`` is the declared default value, or ``dataclasses.MISSING``
    when the field is mandatory — a distinct marker is needed because ``None`` is itself a
    common and meaningful default in HiSim configs.
    """

    name: str
    type_name: str
    default: Any
    sizable: bool


@dataclass(frozen=True)
class PresetInfo:
    """One named preset of a config class and what it leaves for the sizing to fill in.

    ``name`` is the wire name (the ``preset_`` prefix of the declaring classmethod
    stripped), ``pinned`` names the sizable fields this preset gives a concrete value and
    ``auto`` the ones it leaves for resolution (either as the ``AUTO`` sentinel or as a
    per-preset law overriding the field's declared one). The split is determined by
    building the preset once with :attr:`PROBE_NAME`, because a preset is a builder and
    its content cannot be read any other way; the throwaway instance is discarded
    immediately.
    """

    #: Instance name handed to a preset builder purely to inspect the result. It is never
    #: part of a description and never reaches a simulation; it is spelled unmistakably so
    #: that it is obvious if it ever leaks into an error message.
    PROBE_NAME: ClassVar[str] = "<describe-probe>"

    name: str
    canonical: bool
    pinned: Tuple[str, ...]
    auto: Tuple[str, ...]
    note: Optional[str]


@dataclass(frozen=True)
class ParameterInfo:
    """One parameter of a named constructor: what to pass and what happens if you do not.

    ``type_name`` is the annotation as written in the source, which is what a schema
    exporter or a command line shows to whoever has to supply the value. ``default`` is the
    declared default or ``dataclasses.MISSING`` when the parameter is mandatory — the same
    distinction :class:`FieldInfo` makes, and for the same reason: ``None`` is itself a
    common and meaningful default in HiSim configs.
    """

    name: str
    type_name: str
    default: Any


@dataclass(frozen=True)
class ConstructorInfo:
    """One named constructor of a config class and the parameters it takes.

    A constructor serves the classes whose variation is an open identifier space — a TABULA
    building code, a weather location — where presets would mint hundreds of arbitrary wire
    names. ``name`` is the wire name (the full method name, ``for_``/``from_`` prefix
    included) and ``parameters`` lists everything after the instance name, in declaration
    order, so a caller can build the call without reading the source.
    """

    name: str
    parameters: Tuple[ParameterInfo, ...]
    note: Optional[str]


@dataclass(frozen=True)
class SizableFieldInfo:
    """One sizable field of a config class: how it is computed and from what.

    ``law`` is the law's own rendering of itself (the formula as it appears in errors and
    the audit trail), ``facts_read`` pairs every scenario fact the law reads with the name
    of its cardinality (``"ONE"`` or ``"MANY"``), and ``fields_read`` names the sibling
    fields of the same config it reads. ``kind`` separates a real derivation from an
    author's constant, and ``note`` carries the author-declared source of the value.
    """

    name: str
    law: str
    facts_read: Tuple[Tuple[str, str], ...]
    fields_read: Tuple[str, ...]
    kind: SizableFieldKind
    note: Optional[str]


@dataclass(frozen=True)
class ConfigDescription:
    """The complete machine-readable description of one config class.

    Holds the five answers :func:`describe_config` produces — the settable fields, the
    named presets, the named constructors, the sizable fields with their laws, and the
    facts the class contributes to its siblings — as plain frozen data, so a caller may
    compare two descriptions, dump one to JSON-ish structures or render it without ever
    touching the class again.
    """

    config_class_name: str
    fields: Tuple[FieldInfo, ...]
    presets: Tuple[PresetInfo, ...]
    constructors: Tuple[ConstructorInfo, ...]
    sizable_fields: Tuple[SizableFieldInfo, ...]
    facts_provided: Tuple[str, ...]


def _annotation_name(annotation: Any) -> str:
    """Renders an annotation as the string a description shows, however it was written.

    Annotations arrive either as strings (modules that postpone evaluation) or as typing
    objects; both are reduced to one readable spelling here so the caller never has to care
    which import style the module it describes happened to use. A parameterised annotation
    keeps its arguments (``Optional[float]``, not ``Optional``), because the arguments are
    the half a caller needs.
    """
    if isinstance(annotation, str):
        return annotation
    if getattr(annotation, "__args__", None) is not None:
        return str(annotation).replace("typing.", "")
    return getattr(annotation, "__name__", None) or str(annotation)


def _type_name(field: dataclasses.Field) -> str:
    """Renders a dataclass field's annotation as the string a description shows."""
    return _annotation_name(field.type)


def _describe_fields(config_class: type, sizable: Tuple[str, ...]) -> Tuple[FieldInfo, ...]:
    """Describes every dataclass field of the class, marking the sizable ones.

    A field with a default factory is reported with the value that factory produces, since
    a description is about what a user would get, not about how the default is spelled.
    """
    infos = []
    for field in dataclasses.fields(config_class):
        if field.default is not dataclasses.MISSING:
            default: Any = field.default
        elif field.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
            default = field.default_factory()  # type: ignore[misc]
        else:
            default = dataclasses.MISSING
        infos.append(
            FieldInfo(
                name=field.name,
                type_name=_type_name(field),
                default=default,
                sizable=field.name in sizable,
            )
        )
    return tuple(infos)


def _describe_presets(config_class: type, sizable: Tuple[str, ...]) -> Tuple[PresetInfo, ...]:
    """Describes the named presets of the class, splitting sizable fields into pinned/auto.

    Returns an empty tuple for a class that declares no preset at all, which is legal — not
    every configuration has a defensible default. Each preset is built once with
    :attr:`PresetInfo.PROBE_NAME`; a builder that raises is a bug in the preset, not
    something to be swallowed here, so the exception propagates.
    """
    canonical = canonical_preset(config_class)
    infos = []
    for name, builder in presets_of(config_class).items():
        probe = builder.build(PresetInfo.PROBE_NAME)
        unresolved = set(auto_fields(probe))
        infos.append(
            PresetInfo(
                name=name,
                canonical=canonical is not None and name == canonical.name,
                pinned=tuple(field for field in sizable if field not in unresolved),
                auto=tuple(field for field in sizable if field in unresolved),
                note=builder.note,
            )
        )
    return tuple(infos)


def _describe_constructors(config_class: type) -> Tuple[ConstructorInfo, ...]:
    """Describes the named constructors of the class and the parameters each one takes.

    The parameters are read from the undecorated function's signature, skipping ``cls`` and
    the instance name, which every builder takes and which is therefore never news to a
    caller. Nothing is called here: a constructor needs arguments the description cannot
    invent, so unlike a preset it is inspected rather than built.
    """
    infos = []
    for name, builder in constructors_of(config_class).items():
        parameters = list(inspect.signature(builder.function).parameters.values())[2:]
        infos.append(
            ConstructorInfo(
                name=name,
                parameters=tuple(
                    ParameterInfo(
                        name=parameter.name,
                        type_name=_annotation_name(parameter.annotation),
                        default=(
                            dataclasses.MISSING if parameter.default is inspect.Parameter.empty else parameter.default
                        ),
                    )
                    for parameter in parameters
                ),
                note=builder.note,
            )
        )
    return tuple(infos)


def _cardinality_name(cardinality: Cardinality) -> str:
    """Renders a fact's cardinality as the plain string a description carries.

    Descriptions are consumed by exporters and comparisons that should not have to import
    the kernel's enum, so the member name (``"ONE"``, ``"MANY"``) travels instead of the
    member itself; the enum stays importable from this module for code that wants it.
    """
    return cardinality.name


def _describe_law(name: str, law: SizingLaw, note: Optional[str]) -> SizableFieldInfo:
    """Describes one sizable field's law, classifying a fact-free law as a constant."""
    facts = tuple((fact, _cardinality_name(cardinality)) for fact, cardinality in law.facts_read())
    fields = law.fields_read()
    kind = SizableFieldKind.LAW if (facts or fields) else SizableFieldKind.CONSTANT
    return SizableFieldInfo(
        name=name, law=law.describe(), facts_read=facts, fields_read=fields, kind=kind, note=note
    )


def _describe_facts_provided(config_class: type) -> Tuple[str, ...]:
    """Lists the sizing facts the class declares it computes, in declaration order.

    Duplicates are collapsed (two contributions may legitimately name the same fact) while
    the declaration order is kept, because that order is what an author reads in the source.
    """
    contributions = getattr(config_class, FactContribution.CLASS_ATTRIBUTE, ())
    ordered: list = []
    for contribution in contributions:
        for fact in contribution.facts:
            if fact not in ordered:
                ordered.append(fact)
    return tuple(ordered)


def describe_config(config_class: type) -> ConfigDescription:
    """Returns the complete machine-readable description of a config class.

    The single entry point of this module: it answers what fields the class has, which
    presets and named constructors it ships and what each preset leaves to be sized, how
    each sizable field is computed and from which facts, and which facts the class provides
    to the rest of a scenario.
    Nothing is cached — a description is cheap and a caller that wants one repeatedly can
    hold on to the returned immutable object.

    Args:
        config_class: A configuration dataclass; it is only read, never instantiated except
            through its own preset builders.

    Returns:
        The immutable description of the class.

    Raises:
        TypeError: If the argument is not a dataclass, which is the one mistake that would
            otherwise produce a silently empty description.
    """
    if not dataclasses.is_dataclass(config_class):
        raise TypeError(
            f"describe_config expects a config dataclass, got {config_class!r}; only "
            "dataclass config classes carry fields, presets and sizing declarations."
        )
    laws = sizable_fields(config_class)
    sizable = tuple(laws)
    notes = field_notes(config_class)
    return ConfigDescription(
        config_class_name=config_class.__name__,
        fields=_describe_fields(config_class, sizable),
        presets=_describe_presets(config_class, sizable),
        constructors=_describe_constructors(config_class),
        sizable_fields=tuple(_describe_law(name, law, notes.get(name)) for name, law in laws.items()),
        facts_provided=_describe_facts_provided(config_class),
    )
