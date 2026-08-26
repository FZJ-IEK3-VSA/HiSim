"""The results of class-bound validation: what each entry of a file resolved to.

Checking an energy-system file against the classes it names produces knowledge worth
keeping. Every entry resolves to a component class, to the configuration class that
component takes, to the machine-readable description of that configuration class and to the
preset or named constructor the entry selected; throwing all of that away and re-importing it
one stage later would cost time, and worse, it would let two stages disagree about what a
line of the file means.

This module holds those results and nothing else. It knows how to *hold* a binding, not how
to *make* one — the checks that produce bindings need to import component modules and
therefore live in :mod:`hisim.energy_system.classes`, while these types stay importable by
anything that merely wants to read a result. The one piece of logic here is
:func:`facts_read_by`, which answers what sizing facts a configuration class reads; it is
shared between the validator that rejects a source line for a fact nobody reads and any
surface that lists a class's facts for an author.
"""

# clean

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Mapping, Optional, Tuple

from hisim.config.contributions import FactContribution
from hisim.config.introspection import ConfigDescription, ConstructorInfo
from hisim.config.presets import ConfigBuilder, constructors_of, presets_of
from hisim.config.sizing import _AutoSize, sizable_fields
from hisim.energy_system.errors import EnergySystemBindingError, EnergySystemErrorId
from hisim.energy_system.model import ComponentEntry


@dataclass(frozen=True)
class ClassBinding:
    """One component entry resolved against the classes it names.

    The binding is the bridge between a line of YAML and the Python objects behind it: the
    component class the entry names, the configuration class that component takes, the
    machine-readable description of that configuration class, and the preset or named
    constructor the entry selected. Holding them together means the later stages ask the
    entry for its class rather than importing it again, and it means every message about the
    entry can name the class without a second lookup.

    ``preset`` and ``constructor`` are mutually exclusive and both may be absent, which is the
    case of an entry configured by a complete ``config`` block alone.
    """

    name: str
    entry: ComponentEntry
    component_class: type
    config_class: type
    description: ConfigDescription
    preset: Optional[ConfigBuilder] = None
    constructor: Optional[ConfigBuilder] = None


class ClassBindings:
    """Every entry of one energy-system file, resolved against its classes, in file order.

    The result of :func:`validate_classes`, and the input of every stage that needs a class:
    it behaves like an ordered read-only mapping from component name to :class:`ClassBinding`,
    so a caller can iterate it to build configurations in file order or index it to answer a
    question about one entry.

    It is deliberately not a plain ``dict``: the ordering guarantee is part of the contract
    (configurations are built and reported in the order the file writes them), and a named
    type gives the boundary between the validating stage and the configuring stage something
    to be spelled with.
    """

    def __init__(self, bindings: Mapping[str, ClassBinding]) -> None:
        """Stores the resolved bindings in the order they were produced.

        Args:
            bindings: Component name to binding, already in file order.
        """
        self._bindings: Dict[str, ClassBinding] = dict(bindings)

    def __iter__(self) -> Iterator[ClassBinding]:
        """Iterates the bindings in file order.

        Returns:
            An iterator over the bindings themselves, not over their names, because every
            caller that walks this object wants the binding.
        """
        return iter(self._bindings.values())

    def __len__(self) -> int:
        """The number of resolved entries.

        Returns:
            How many components the file declares in its enabled set.
        """
        return len(self._bindings)

    def __getitem__(self, name: str) -> ClassBinding:
        """Returns the binding of one component.

        Args:
            name: The component's name, which is its key in the file.

        Returns:
            The binding.

        Raises:
            KeyError: If the file declares no such component.
        """
        return self._bindings[name]

    def __contains__(self, name: object) -> bool:
        """Whether a component of that name was bound.

        Args:
            name: The name to look for.

        Returns:
            ``True`` if the file declares it.
        """
        return name in self._bindings

    def names(self) -> Tuple[str, ...]:
        """The component names in file order.

        Returns:
            The names, useful for building the sizing bridge and for messages that list the
            system's components.
        """
        return tuple(self._bindings)


@dataclass(frozen=True)
class BindingFailure:
    """One entry that could not be bound to its classes, kept instead of raised.

    Validation itself stops at the first violation, which is right for a run: nothing good
    comes of building half a system. Two other jobs need the opposite — a report telling an
    author how far a file is from executable, and the test that pins how far the class
    conversion has come — and both want every entry's verdict rather than the first one.
    This record is what they collect: the component's name, the catalogue identifier of what
    went wrong and the full message, so a pinned expectation can be written against the
    identifier while a reader still sees the sentence.
    """

    name: str
    error_id: EnergySystemErrorId
    message: str


def facts_read_by(config_class: type) -> Tuple[str, ...]:
    """Returns every sizing fact one configuration class reads, sorted and deduplicated.

    A class reads facts in two ways that are easy to confuse and equally binding: its sizable
    fields have laws that read facts to compute a value, and its fact contributions may need
    facts of their own before they can compute the facts they publish. Both are legitimate
    targets of a ``sizing_sources`` line, so both are collected here, and collecting them in
    one function keeps the validator and any future description surface from disagreeing.

    Args:
        config_class: A configuration dataclass.

    Returns:
        The fact names, sorted so that a message listing them reads the same on every run.
    """
    facts = set()
    for law in sizable_fields(config_class).values():
        for fact, _cardinality in law.facts_read():
            facts.add(fact)
    for contribution in getattr(config_class, FactContribution.CLASS_ATTRIBUTE, ()):
        facts.update(contribution.reads)
    return tuple(sorted(facts))


class ConfigOriginChecker:
    """Every check of one entry that the configuration class alone can decide.

    An entry states where its configuration comes from — a named preset, a named constructor
    with arguments, or a complete block — and then overrides individual fields and names the
    providers of individual sizing facts. Whether any of that is possible is a question about
    the configuration class and nothing else: no component has to be imported, constructed or
    wired to answer it.

    The checks are gathered on one class so that the rule "every rejection names the entry and
    lists the valid alternatives" has a single home, and so that a surface which already holds
    a configuration class — a schema exporter, a describe command — can run them without going
    through the component-importing stage. Every method raises rather than returning a verdict,
    because the format repairs nothing.
    """

    @classmethod
    def resolve_preset(
        cls,
        entry: ComponentEntry,
        config_class: type,
        description: ConfigDescription,
        location: str,
        name: str,
    ) -> Optional[ConfigBuilder]:
        """Looks up the preset an entry names, listing the class's presets when it is unknown.

        Args:
            entry: The entry being checked.
            config_class: Its configuration class.
            description: The description of that class, used for the alternatives.
            location: The entry's key path, for the message.
            name: The component's name, for the message.

        Returns:
            The named preset, or ``None`` when the entry names none.

        Raises:
            EnergySystemBindingError: ``EF-13`` when the class declares no such preset. The
                message lists every preset the class does declare, which is also the list
                that shrinks to nothing while a class is still unconverted.
        """
        if entry.preset is None:
            return None
        presets = presets_of(config_class)
        if entry.preset not in presets:
            known = tuple(info.name for info in description.presets)
            raise EnergySystemBindingError(
                EnergySystemErrorId.UNKNOWN_PRESET,
                f"{location}.preset",
                f"'{name}' names the preset '{entry.preset}', which "
                f"{config_class.__name__} does not declare"
                + ("." if known else "; the class declares no preset at all."),
                alternatives=known,
                alternatives_label=f"presets of {config_class.__name__}",
                offending_value=entry.preset,
                remedy=None if known else "Write a complete 'config' block instead.",
            )
        return presets[entry.preset]

    @classmethod
    def resolve_constructor(
        cls,
        entry: ComponentEntry,
        config_class: type,
        description: ConfigDescription,
        location: str,
        name: str,
    ) -> Optional[ConfigBuilder]:
        """Looks up the named constructor an entry calls and checks the arguments it passes.

        Args:
            entry: The entry being checked.
            config_class: Its configuration class.
            description: The description of that class, which carries the parameters.
            location: The entry's key path, for the message.
            name: The component's name, for the message.

        Returns:
            The named constructor, or ``None`` when the entry names none.

        Raises:
            EnergySystemBindingError: ``EF-15`` when the class declares no such constructor,
                ``EF-16`` for an argument that is not one of its parameters or for a
                mandatory parameter the entry leaves out. Both list the constructor's
                parameters with their types and defaults.
        """
        if entry.constructor is None:
            return None
        constructors = constructors_of(config_class)
        call = entry.constructor
        if call.name not in constructors:
            raise EnergySystemBindingError(
                EnergySystemErrorId.UNKNOWN_CONSTRUCTOR,
                f"{location}.constructor",
                f"'{name}' calls the constructor '{call.name}', which "
                f"{config_class.__name__} does not declare"
                + ("." if description.constructors else "; the class declares none at all."),
                alternatives=tuple(info.name for info in description.constructors),
                alternatives_label=f"constructors of {config_class.__name__}",
                offending_value=call.name,
                remedy=None if description.constructors else "Use a preset or a complete 'config' block.",
            )
        info = next(item for item in description.constructors if item.name == call.name)
        cls.check_constructor_arguments(call.name, dict(call.arguments), info, location, name)
        return constructors[call.name]

    @classmethod
    def check_constructor_arguments(
        cls,
        constructor_name: str,
        arguments: Mapping[str, Any],
        info: ConstructorInfo,
        location: str,
        name: str,
    ) -> None:
        """Checks one constructor call's arguments against the constructor's parameters.

        Args:
            constructor_name: The wire name of the constructor being called.
            arguments: The arguments the file passes.
            info: The described parameters of that constructor.
            location: The entry's key path, for the message.
            name: The component's name, for the message.

        Raises:
            EnergySystemBindingError: ``EF-16`` for an unknown argument or a missing
                mandatory one, listing every parameter with its type and default.
        """
        parameters = {parameter.name: parameter for parameter in info.parameters}
        rendered = cls.render_parameters(info)
        unknown = [argument for argument in arguments if argument not in parameters]
        if unknown:
            raise EnergySystemBindingError(
                EnergySystemErrorId.CONSTRUCTOR_ARGUMENT,
                f"{location}.constructor.{constructor_name}",
                f"the constructor '{constructor_name}' of '{name}' got the unknown "
                f"argument{'s' if len(unknown) > 1 else ''} {', '.join(sorted(unknown))}.",
                alternatives=rendered,
                alternatives_label="parameters",
                offending_value=unknown[0],
            )
        missing = [
            parameter.name
            for parameter in info.parameters
            if parameter.default is dataclasses.MISSING and parameter.name not in arguments
        ]
        if missing:
            raise EnergySystemBindingError(
                EnergySystemErrorId.CONSTRUCTOR_ARGUMENT,
                f"{location}.constructor.{constructor_name}",
                f"the constructor '{constructor_name}' of '{name}' needs the "
                f"argument{'s' if len(missing) > 1 else ''} {', '.join(missing)}, which the "
                "entry does not pass.",
                alternatives=rendered,
                alternatives_label="parameters",
            )

    @classmethod
    def render_parameters(cls, info: ConstructorInfo) -> Tuple[str, ...]:
        """Renders a constructor's parameters as ``name: type`` or ``name: type = default``.

        Args:
            info: The described constructor.

        Returns:
            One rendered parameter per entry, in declaration order.
        """
        rendered: List[str] = []
        for parameter in info.parameters:
            text = f"{parameter.name}: {parameter.type_name}"
            if parameter.default is not dataclasses.MISSING:
                text += f" = {parameter.default!r}"
            rendered.append(text)
        return tuple(rendered)

    @classmethod
    def check_config_keys(
        cls,
        entry: ComponentEntry,
        config_class: type,
        description: ConfigDescription,
        location: str,
        name: str,
    ) -> None:
        """Checks that every key of a ``config`` block is a settable field of the class.

        Two keys are refused rather than accepted: one that names no field at all, and
        ``component_id``, which is a field but is not the file's to write — the entry's key
        is the component's whole identity, and a second spelling of it in the block would
        make the file self-contradictory. An ``AUTO`` value is refused on a field no law
        sizes, because re-opening a field for resolution only means something where a law
        can close it again.

        Args:
            entry: The entry being checked.
            config_class: Its configuration class.
            description: The description of that class, used for the alternatives.
            location: The entry's key path, for the message.
            name: The component's name, for the message.

        Raises:
            EnergySystemBindingError: ``EF-17`` for an unknown or forbidden key, ``EF-1B``
                for ``AUTO`` on a field that carries no sizing law.
        """
        settable = tuple(info.name for info in description.fields if info.name != "component_id")
        laws = sizable_fields(config_class)
        for key, value in entry.config.items():
            if key == "component_id":
                raise EnergySystemBindingError(
                    EnergySystemErrorId.UNKNOWN_CONFIG_FIELD,
                    f"{location}.config.component_id",
                    f"'{name}' sets 'component_id' in its config block; the entry's key is "
                    "the component's identity and the only place it is written.",
                    remedy="Delete the line; the identity comes from the entry key.",
                )
            if key not in settable:
                raise EnergySystemBindingError(
                    EnergySystemErrorId.UNKNOWN_CONFIG_FIELD,
                    f"{location}.config.{key}",
                    f"'{name}' sets '{key}', which is no field of {config_class.__name__}.",
                    alternatives=settable,
                    alternatives_label=f"fields of {config_class.__name__}",
                    offending_value=key,
                )
            if value == _AutoSize.WIRE_SPELLING and key not in laws:
                raise EnergySystemBindingError(
                    EnergySystemErrorId.AUTO_ON_CONCRETE_FIELD,
                    f"{location}.config.{key}",
                    f"'{name}' sets '{key}' to AUTO, but {config_class.__name__} declares no "
                    f"sizing law for that field, so nothing would ever fill it in.",
                    alternatives=tuple(laws),
                    alternatives_label=f"sizable fields of {config_class.__name__}",
                    offending_value=key,
                )

    @classmethod
    def check_sizing_facts(cls, entry: ComponentEntry, config_class: type, location: str, name: str) -> None:
        """Checks that every ``sizing_sources`` key is a fact the class actually reads.

        A source line answers the question "where does this component take that fact from",
        so a fact the class never reads makes the line unanswerable: it would be silently
        ignored, and the author would keep believing the number came from where the line
        says. The facts a class reads are the ones its sizing laws read plus the ones its
        fact contributions need in order to compute their own outputs.

        Args:
            entry: The entry being checked.
            config_class: Its configuration class.
            location: The entry's key path, for the message.
            name: The component's name, for the message.

        Raises:
            EnergySystemBindingError: ``EF-43`` naming the fact and listing the facts the
                class does read.
        """
        readable = facts_read_by(config_class)
        for fact in entry.sizing_sources:
            if fact not in readable:
                raise EnergySystemBindingError(
                    EnergySystemErrorId.FACT_NOT_READ,
                    f"{location}.sizing_sources.{fact}",
                    f"'{name}' names a source for '{fact}', which {config_class.__name__} "
                    "never reads"
                    + ("." if readable else "; the class reads no sizing fact at all."),
                    alternatives=readable,
                    alternatives_label=f"facts read by {config_class.__name__}",
                    offending_value=fact,
                    remedy=None if readable else "Delete the block.",
                )
