"""The results of class-bound validation: what each entry of a file resolved to.

Checking an energy-system file against the classes it names produces knowledge worth
keeping. Every entry resolves to a component class, to the configuration class that
component takes, to the machine-readable description of that configuration class and to the
preset or named constructor the entry selected; throwing all of that away and re-importing it
one stage later would cost time, and worse, it would let two stages disagree about what a
line of the file means.

This module holds those results and nothing else. It knows how to *hold* a binding, not how
to *make* one — the binder that produces bindings needs to import component modules and
therefore lives in :mod:`hisim.energy_system.classes`, and the checks of a binding are the
binding rules of the setup rules (:mod:`hisim.setup_rules.bindings`), while these types stay
importable by anything that merely wants to read a result. The one piece of logic here is
:func:`facts_read_by`, which answers what sizing facts a configuration class reads; it is
shared between the rule that rejects a source line for a fact nobody reads and any
surface that lists a class's facts for an author.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterator, Mapping, Optional, Sequence, Tuple

from hisim.config.contributions import FactContribution
from hisim.config.introspection import ConfigDescription
from hisim.config.presets import ConfigBuilder
from hisim.config.sizing import sizable_fields
from hisim.energy_system.errors import CatalogueRefusal, EnergySystemErrorId
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
    case of an entry configured by a complete ``config`` block alone. Each is also absent when the
    entry names one that the configuration class does not declare, which the binding rules
    ``EF-13`` and ``EF-15`` refuse before anything is configured.
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

    An entry whose class path does not resolve to a configurable component has no binding; its
    refusal is kept in :attr:`refusals` instead, for the binding rule ``EF-10`` to refuse together
    with every other fault of the file.
    """

    def __init__(self, bindings: Mapping[str, ClassBinding], refusals: Sequence[CatalogueRefusal] = ()) -> None:
        """Stores the resolved bindings in the order they were produced, and the refusals of the entries that did not resolve.

        Args:
            bindings: Component name to binding, already in file order.
            refusals: The refusals of the entries whose class did not resolve, in file order.
        """
        self._bindings: Dict[str, ClassBinding] = dict(bindings)
        self.refusals: Tuple[CatalogueRefusal, ...] = tuple(refusals)

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
    """One entry that could not be bound to its classes, by the first fault the binding rules found in it.

    A run refuses a file with every fault of every entry at once. Two other jobs want one
    verdict per entry instead — a report telling an author how far a file is from executable,
    and the test that pins how far the class conversion has come. This record is what they
    collect: the component's name, the catalogue identifier of its first fault in code order
    and the full message, so a pinned expectation can be written against the identifier while
    a reader still sees the sentence.
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
