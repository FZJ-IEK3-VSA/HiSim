"""The parts of the energy-system format that assemblies add: imports, ports, verbs and placeholders.

Schema version 4 of the format (``assemblies_spec.md`` §2, lean v1 of §13.1) lets a file import
**assemblies** — fragments of an energy system in files of their own — under a new top-level key,
``imports``, and gives every top-level component entry a ``ports`` block (what a site component
needs from an import, §3) and the three binding verbs ``bind:``, ``optional-bind:`` and ``none:``
(§3.1). Inside an entry's ``inputs`` a placeholder ``{$port: <port>}`` marks where the items a bound
port lowers to land (dry run gap G3); inside a ``config`` block ``{$param: <name>}`` is the one value
placeholder an assembly member may write.

The models are the faithful in-memory form of those blocks and are shared by the two files that
write them: the energy-system file and the assembly file (:mod:`hisim.energy_system.assemblies.model`).
Like the rest of the format's models they know nothing about component classes, and every model is
frozen, so an expanded file can never be mutated behind the back of the record that describes it.
"""

from __future__ import annotations

import enum
from typing import Any, ClassVar, Dict, Mapping, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ParameterReference:
    """The ``{$param: <name>}`` spelling of a value that an assembly parameter supplies.

    ``$`` is outside the identifier grammar, so no configuration field or parameter can collide
    with the key (``assemblies_spec.md`` §2.6). It is kept as the raw one-key mapping wherever it is
    written and recognised here, in one place.
    """

    #: The one key of a parameter reference.
    KEY: ClassVar[str] = "$param"

    #: The other ``$`` spellings of the design, which v1 does not have (D26): per-variant values live
    #: in the variant options, and a field that follows a fact gets a law in its class.
    CUT_KEYS: ClassVar[Tuple[str, ...]] = ("$switch", "$fact", "$derived")

    @classmethod
    def name_of(cls, value: Any) -> Optional[str]:
        """Returns the parameter a value refers to, or ``None`` for any other value."""
        if isinstance(value, Mapping) and len(value) == 1 and cls.KEY in value:
            name = value[cls.KEY]
            return name if isinstance(name, str) else None
        return None

    @classmethod
    def walk(cls, value: Any, path: Tuple[str, ...] = ()) -> Tuple[Tuple[str, Tuple[str, ...]], ...]:
        """Every ``$`` key of a value tree with its key path: ``$param`` with the name, a cut key as itself.

        Args:
            value: A config block, a constructor's arguments, an import's parameters.
            path: The key path of ``value``.

        Returns:
            ``(parameter name or cut key, path)`` in document order.
        """
        name = cls.name_of(value)
        if name is not None:
            return ((name, path),)
        found: Tuple[Tuple[str, Tuple[str, ...]], ...] = ()
        if isinstance(value, Mapping):
            cut = tuple((key, path) for key in value if key in cls.CUT_KEYS or key == cls.KEY)
            if cut:
                return cut
            for key, item in value.items():
                found += cls.walk(item, path + (str(key),))
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                found += cls.walk(item, path + (str(index),))
        return found

    @classmethod
    def substitute(cls, value: Any, values: Mapping[str, Any]) -> Any:
        """Returns the tree with every ``{$param: …}`` replaced by its resolved value; the input is untouched."""
        name = cls.name_of(value)
        if name is not None:
            return values[name]
        if isinstance(value, Mapping):
            return {key: cls.substitute(item, values) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return type(value)(cls.substitute(item, values) for item in value)
        return value


class PortPlaceholder(BaseModel):
    """``{$port: <port>}`` in an ``inputs`` list: where a bound need's lowered items land."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The key that marks the placeholder.
    KEY: ClassVar[str] = "$port"

    port: str

    def to_document(self) -> Dict[str, Any]:
        """The placeholder as the file writes it."""
        return {self.KEY: self.port}


class PlacedPlaceholder(BaseModel):
    """A placeholder with the index it was written at in its entry's ``inputs`` list.

    The entry keeps its ordinary input items in ``inputs``, where every later stage reads them,
    and its placeholders beside them, so the expansion puts the lowered items exactly where the
    author put the placeholder.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    position: int
    placeholder: PortPlaceholder


class PortKind(enum.Enum):
    """What a port is, decided by the key that marks it (``assemblies_spec.md`` §3.2)."""

    NEED = "need"
    PROVIDED = "provided"
    CIRCUIT = "circuit"
    CARRIER = "carrier"
    FACT = "fact"
    OBSERVER = "observer"


class Selector(BaseModel):
    """One selector of an ``observes:`` list (``assemblies_spec.md`` §4.1).

    It matches an output an observer declares a dynamic default connection from, by the runtime
    tags the meter and the energy manager find their inputs by — ``component_type`` (names of
    ``lt.ComponentType``) and ``flow`` (names of ``lt.InandOutputType``) — or by the output's name.
    The keys it writes hold together; each key's list matches any of its values.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The keys a selector may carry, at least one of them.
    KEYS: ClassVar[Tuple[str, ...]] = ("component_type", "flow", "output")

    component_type: Tuple[str, ...] = ()
    flow: Tuple[str, ...] = ()
    output: Tuple[str, ...] = ()

    def matches(self, component_type: Optional[str], flows: Tuple[str, ...], output: str) -> bool:
        """Whether a declared feed — its component type, flow tags and output — is a match."""
        return (
            (not self.component_type or component_type in self.component_type)
            and (not self.flow or any(flow in flows for flow in self.flow))
            and (not self.output or output in self.output)
        )

    def to_document(self) -> Dict[str, Any]:
        """The selector as the file writes it."""
        return {key: list(values) for key, values in self.model_dump().items() if values}

    def text(self) -> str:
        """The selector in flow style, for messages."""
        return "{" + ", ".join(f"{key}: {', '.join(values)}" for key, values in self.to_document().items()) + "}"


class Selection(BaseModel):
    """What an observer observes: ``declared`` (every output its class declares a feed from) or selectors."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The spelling of the selection of every candidate.
    DECLARED: ClassVar[str] = "declared"

    selectors: Optional[Tuple[Selector, ...]] = None

    def to_document(self) -> Any:
        """The selection as the file writes it."""
        selectors = self.selectors or ()
        return [selector.to_document() for selector in selectors] if self.selectors is not None else self.DECLARED

    def text(self) -> str:
        """The selection for messages."""
        selectors = self.selectors or ()
        return "[" + ", ".join(item.text() for item in selectors) + "]" if self.selectors is not None else self.DECLARED


class PortState(enum.Enum):
    """A port's state in one import instance or site entry, as the expansion decides and records it (§3.1)."""

    REQUIRED = "required"
    OPTIONAL = "optional"
    INACTIVE = "inactive"
    PROVIDED = "provided"


class BindingVerbs(BaseModel):
    """The three binding verbs of one site entry or one import (``assemblies_spec.md`` §3.1, D8).

    ``bind: {port: partner}`` binds a need to a partner that must exist; ``optional-bind: {port:
    partner}`` binds an optional need if the partner exists and leaves it unbound otherwise (on a
    required need it is refused); ``none: [port]`` declines an optional need although a partner
    exists. A partner is written ``<component>`` (a site entry), ``<import>`` or
    ``<import>.<instance>``, optionally followed by ``.<port>`` naming a provided port of that import.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The verbs' spellings, in canonical order.
    KEYS: ClassVar[Tuple[str, ...]] = ("bind", "optional-bind", "none")

    bind: Mapping[str, str] = Field(default_factory=dict)
    optional_bind: Mapping[str, str] = Field(default_factory=dict)
    none: Tuple[str, ...] = ()

    @model_validator(mode="after")
    def _one_verb_per_port(self) -> "BindingVerbs":
        """Refuses a port written under two verbs, as the reader does."""
        written = list(self.bind) + list(self.optional_bind) + list(self.none)
        twice = sorted({port for port in written if written.count(port) > 1})
        if twice:
            raise ValueError(f"the ports {', '.join(twice)} carry two verbs; a port carries one.")
        return self

    @property
    def is_empty(self) -> bool:
        """Whether no verb is written."""
        return not (self.bind or self.optional_bind or self.none)

    def verb_for(self, port: str) -> Optional[Tuple[str, Optional[str]]]:
        """The verb written for a port and its partner (``None`` for ``none``), if any."""
        if port in self.bind:
            return "bind", self.bind[port]
        if port in self.optional_bind:
            return "optional-bind", self.optional_bind[port]
        if port in self.none:
            return "none", None
        return None

    def ports(self) -> Tuple[str, ...]:
        """Every port a verb is written for."""
        return tuple(self.bind) + tuple(self.optional_bind) + tuple(self.none)

    def to_document(self) -> Dict[str, Any]:
        """The verbs as the file writes them, empty ones left out."""
        document: Dict[str, Any] = {}
        if self.bind:
            document["bind"] = dict(self.bind)
        if self.optional_bind:
            document["optional-bind"] = dict(self.optional_bind)
        if self.none:
            document["none"] = list(self.none)
        return document


class Port(BaseModel):
    """One port of an assembly's interface or of a site entry (``assemblies_spec.md`` §3, §13.1).

    Attributes:
        name: The port's key.
        section: ``needs``, ``provides``, ``observes`` (an interface) or ``ports`` (a site entry).
        kind: What it is.
        into: The members a need, a fact need or an observer port lowers into; a site entry's need
            lowers into the entry itself.
        partner: The partner classes of a need, by class name.
        wires: Explicit wires ``{input: output}`` a need lowers to instead of default connections.
        output: ``Member.Output`` of a provided port.
        members: Every member the port names: a circuit end's members, a carrier need's consumers,
            a fuel provision's meter, a provided fact's member, an observer's members.
        circuit: A circuit end's circuit, its medium (``dhw``, ``space_heating``, §11.1).
        carrier: A carrier port's ``lt.EnergyBalanceCarrier`` value.
        outputs: A carrier need's consuming outputs, ``Member.Output`` (a site entry's: ``Output``).
        meter: The member metering a provided fuel, where its consumers' feeds land.
        fact: A fact port's sizing fact.
        many: Whether a fact need reads the fact from every provider in scope (a list, ``Sum``).
        selection: An observer port's ``default:``.
        controllable: A provided output's ``controllable:`` block: ``target_input`` or ``via``, and
            ``optional`` (§4.4).
        optional: Whether the port may stay unbound (with a verb saying so).
        required_when: Parameter to values for which the port is required; conjunctive.
        active_when: Parameter to values outside which the port is inactive; conjunctive.
        raw: The block as written, which the emitter and the import record repeat.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    section: str
    kind: PortKind
    into: Tuple[str, ...] = ()
    partner: Tuple[str, ...] = ()
    wires: Optional[Mapping[str, str]] = None
    output: Optional[str] = None
    members: Tuple[str, ...] = ()
    circuit: Optional[str] = None
    carrier: Optional[str] = None
    outputs: Tuple[str, ...] = ()
    meter: Optional[str] = None
    fact: Optional[str] = None
    many: bool = False
    selection: Optional[Selection] = None
    controllable: Mapping[str, Any] = Field(default_factory=dict)
    optional: bool = False
    required_when: Mapping[str, Tuple[Any, ...]] = Field(default_factory=dict)
    active_when: Mapping[str, Tuple[Any, ...]] = Field(default_factory=dict)
    raw: Mapping[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _shape_of_its_kind(self) -> "Port":
        """Refuses a provided port without ``Member.Output`` and a need without members or partner classes."""
        if self.kind == PortKind.PROVIDED:
            member, _, output = (self.output or "").partition(".")
            if not member or not output or "." in output:
                raise ValueError(f"the provided port '{self.name}' names {self.output!r}, not 'Member.Output'.")
        if self.kind == PortKind.NEED and (not self.into or not self.partner):
            raise ValueError(f"the need '{self.name}' names no member to lower into or no partner class.")
        return self

    @property
    def is_provision(self) -> bool:
        """Whether the port offers something another port binds to: a provided output, fact or carrier."""
        if self.kind == PortKind.CARRIER:
            return not self.outputs
        return self.kind == PortKind.PROVIDED or (self.kind == PortKind.FACT and self.section == "provides")

    @property
    def is_fuel_provision(self) -> bool:
        """Whether the port provides a fuel, whose consumers' feeds land in its meter (§5.1)."""
        return self.kind == PortKind.CARRIER and self.is_provision and self.carrier != "electricity"

    @property
    def output_member(self) -> str:
        """The member half of ``output``."""
        return (self.output or "").split(".", 1)[0]

    @property
    def output_name(self) -> str:
        """The output half of ``output``."""
        return (self.output or ".").split(".", 1)[1]


class InstanceEntry(BaseModel):
    """One named instance of an import: its parameter values and the reserved fields (D18, D22).

    Written as one mapping, ``east: {azimuth_in_degree: 90, installation_year: 2026}``: the reserved
    keys ``installation_year`` and ``quote`` sit beside the parameters, which no parameter may be
    named after (the library check refuses it).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    parameters: Mapping[str, Any] = Field(default_factory=dict)
    installation_year: Optional[int] = None
    quote: Optional[Mapping[str, Any]] = None

    def to_document(self) -> Dict[str, Any]:
        """The instance as the file writes it."""
        document: Dict[str, Any] = dict(self.parameters)
        if self.installation_year is not None:
            document["installation_year"] = self.installation_year
        if self.quote is not None:
            document["quote"] = dict(self.quote)
        return document


class ImportEntry(BaseModel):
    """One top-level import: a slot an assembly fills, once or as several named instances (§2.2).

    Attributes:
        name: The import key.
        assembly: The assembly's library path, ``<family>/<name>``.
        parameters: Parameter values of an import without instances.
        instances: The named instances, or ``None`` for an import of one.
        verbs: The binding verbs written on the import.
        observes: The selection replacing the default of its assembly's observer port (§4.1).
        installation_year: The reserved economics field of D18, recorded, never read here.
        quote: The reserved economics field of D22, recorded, never read here.
        order: The flat evaluation order of the import's members, one block (D26 revised, §2.3).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The keys an import may carry, in canonical order.
    IMPORT_KEYS: ClassVar[Tuple[str, ...]] = (
        "assembly",
        "order",
        "parameters",
        "instances",
        "bind",
        "optional-bind",
        "none",
        "observes",
        "installation_year",
        "quote",
    )

    #: The keys reserved beside the parameters of an instance, which no parameter may be named.
    RESERVED_KEYS: ClassVar[Tuple[str, ...]] = ("installation_year", "quote")

    name: str
    assembly: str
    parameters: Mapping[str, Any] = Field(default_factory=dict)
    instances: Optional[Mapping[str, InstanceEntry]] = None
    verbs: BindingVerbs = Field(default_factory=BindingVerbs)
    observes: Optional[Selection] = None
    installation_year: Optional[int] = None
    quote: Optional[Mapping[str, Any]] = None
    order: Optional[int] = None

    def to_document(self) -> Dict[str, Any]:
        """The import as the file writes it, in canonical key order."""
        document: Dict[str, Any] = {"assembly": self.assembly}
        if self.order is not None:
            document["order"] = self.order
        if self.parameters:
            document["parameters"] = dict(self.parameters)
        if self.instances is not None:
            document["instances"] = {name: instance.to_document() for name, instance in self.instances.items()}
        document.update(self.verbs.to_document())
        if self.observes is not None:
            document["observes"] = self.observes.to_document()
        if self.installation_year is not None:
            document["installation_year"] = self.installation_year
        if self.quote is not None:
            document["quote"] = dict(self.quote)
        return document
