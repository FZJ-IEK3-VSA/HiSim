"""The parts of the energy-system format that assemblies add: imports, ports, verbs and placeholders.

Schema version 4 of the format (``assemblies_spec.md`` §2) lets a file import **assemblies** —
fragments of an energy system in files of their own — under a new top-level key, ``imports``, and
gives every component entry four more keys: ``order`` (its place in the evaluation sequence,
§2.3), ``ports`` (what a site component needs from an import, §3), and the three binding verbs
``bind:``, ``optional-bind:`` and ``none:`` (§3.1). Inside an entry's ``inputs`` a placeholder
``{$port: <port>}`` marks where the items a bound port lowers to land (dry run gap G3).

The models here are the faithful in-memory form of those blocks and are shared by the two files
that write them: the energy-system file (site entries and top-level imports) and the assembly file
(its interface and its inner imports), whose models live in
:mod:`hisim.energy_system.assemblies.model`. Like the rest of the format's models they know nothing
about component classes, and a value that may be a parameter reference, ``{$param: <name>}``, is
kept as the raw mapping the file wrote: the expansion substitutes it.

Every model is frozen, so an expanded file can never be mutated behind the back of the record
that describes it.
"""

from __future__ import annotations

import enum
from typing import Any, ClassVar, Dict, Mapping, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field


class ParameterReference:
    """The ``{$param: <name>}`` spelling of a value that an assembly parameter supplies.

    ``$`` is outside the identifier grammar, so no configuration field or parameter can collide
    with the key (``assemblies_spec.md`` §2.6). The reference is kept as the raw one-key mapping
    wherever it is written and recognised here, in one place.
    """

    #: The one key of a parameter reference.
    KEY: ClassVar[str] = "$param"

    #: Keys of the other ``$`` spellings the mockup uses, none of which step 1a lowers, mapped to
    #: the step that delivers each: a fact read (``$fact``), a value switched by a parameter
    #: (``$switch``), and a value derived per instance (``$derived``, dry-run gap G12).
    UNLOWERED_KEYS: ClassVar[Mapping[str, str]] = {
        "$fact": "hisim-lt0b.2 (fact ports)",
        "$switch": "hisim-lt0b.2 (fact ports)",
        "$derived": "no step yet (dry-run gap G12: a value derived per instance)",
        "$observes": "hisim-lt0b.3 (observe and actuate selectors)",
    }

    @classmethod
    def name_of(cls, value: Any) -> Optional[str]:
        """Returns the parameter a value refers to, or ``None`` for any other value."""
        if isinstance(value, Mapping) and len(value) == 1 and cls.KEY in value:
            name = value[cls.KEY]
            return name if isinstance(name, str) else None
        return None

    @classmethod
    def unlowered_key_of(cls, value: Any) -> Optional[str]:
        """Returns the ``$`` key of a value this step does not lower, or ``None``."""
        if isinstance(value, Mapping) and len(value) >= 1:
            for key in value:
                if key in cls.UNLOWERED_KEYS:
                    return str(key)
        return None


class BindingVerbs(BaseModel):
    """The three binding verbs one site entry or one import carries (``assemblies_spec.md`` §3.1, D8).

    ``bind: {port: partner}`` binds a port to a partner that must exist; ``optional-bind: {port:
    partner}`` binds it if the partner exists in the expanded system and leaves it unbound
    otherwise; ``none: [port]`` declines a port although a partner exists. A partner is written
    ``<component>`` (a site entry), ``<import>`` or ``<import>.<instance>``, optionally followed
    by ``.<port>`` naming the partner import's port.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The verbs' wire spellings, in canonical order.
    KEYS: ClassVar[Tuple[str, ...]] = ("bind", "optional-bind", "none")

    bind: Mapping[str, str] = Field(default_factory=dict)
    optional_bind: Mapping[str, str] = Field(default_factory=dict)
    none: Tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        """Whether no verb is written."""
        return not (self.bind or self.optional_bind or self.none)

    def verb_for(self, port: str) -> Optional[Tuple[str, Optional[str]]]:
        """Returns the verb written for a port and its partner (``None`` for ``none``), if any."""
        if port in self.bind:
            return "bind", self.bind[port]
        if port in self.optional_bind:
            return "optional-bind", self.optional_bind[port]
        if port in self.none:
            return "none", None
        return None

    def ports(self) -> Tuple[str, ...]:
        """Every port a verb is written for, in the order bind, optional-bind, none."""
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


class PortPlaceholder(BaseModel):
    """``{$port: <port>}`` in an ``inputs`` list: where a bound port's lowered items land.

    The expansion replaces it by the bare partner name (the member class's default connections
    from the partner's class), by the explicit wires ``wires:`` names, or by nothing when the port
    is inactive, declined or left unbound by ``optional-bind:``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The key that marks the placeholder.
    KEY: ClassVar[str] = "$port"

    port: str
    wires: Optional[Mapping[str, str]] = None

    def to_document(self) -> Dict[str, Any]:
        """The placeholder as the file writes it."""
        document: Dict[str, Any] = {self.KEY: self.port}
        if self.wires is not None:
            document["wires"] = dict(self.wires)
        return document


class ObservesPlaceholder(BaseModel):
    """``{$observes: <observer port>}`` in an ``inputs`` list: where an observer's feeds land.

    Parsed so that a file using it can be read; the expansion refuses it, because observer ports
    are lowered by hisim-lt0b.3.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The key that marks the placeholder.
    KEY: ClassVar[str] = "$observes"

    observer: str

    def to_document(self) -> Dict[str, Any]:
        """The placeholder as the file writes it."""
        return {self.KEY: self.observer}


#: Either placeholder.
AnyPlaceholder = Union[PortPlaceholder, ObservesPlaceholder]


class PlacedPlaceholder(BaseModel):
    """A placeholder of a site entry, with its position in the entry's written ``inputs`` list.

    A site entry keeps its ordinary input items in ``inputs`` — every later stage reads them —
    and its placeholders beside them, each with the index it was written at, so that the expansion
    can put the lowered items exactly where the author put the placeholder.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    position: int
    placeholder: AnyPlaceholder


class PortKind(enum.Enum):
    """What a port is, decided by the keys it carries (``assemblies_spec.md`` §3.2)."""

    NEED = "need"
    PROVIDED = "provided"
    REEXPORT = "re-export"
    INTERNAL = "internal"
    CIRCUIT = "circuit"
    CARRIER = "carrier"
    FACT = "fact"
    OBSERVER = "observer"
    ACTUATES = "actuates"

    @property
    def lowered_in_step_1a(self) -> bool:
        """Whether this step lowers ports of this kind."""
        return self in (PortKind.NEED, PortKind.PROVIDED, PortKind.REEXPORT, PortKind.INTERNAL)

    @property
    def delivering_step(self) -> str:
        """The bead that lowers ports of a kind this step does not."""
        if self in (PortKind.CIRCUIT, PortKind.CARRIER, PortKind.FACT):
            return "hisim-lt0b.2 (circuit ports, carrier needs, fact ports)"
        return "hisim-lt0b.3 (observe and actuate selectors, controller lowering)"


class PortState(enum.Enum):
    """A port's state in one import instance or site entry, as the expansion decides and records it (§3.1).

    The first three are the requirement states the file's ``optional``, ``required_when`` and
    ``active_when`` give a port before it is decided; the others say how it was decided.
    """

    #: A partner must be found, by a verb or the default rule.
    REQUIRED = "required"
    #: It may stay unbound, but only with a verb saying so when a candidate exists.
    OPTIONAL = "optional"
    #: ``active_when`` or ``required_when`` switches it off for these parameters; no verb may name it.
    INACTIVE = "inactive"
    #: An inner import's port its importer re-exports (``from:``).
    REEXPORTED = "re-exported"
    #: Bound by an ``internal:`` entry of the importing assembly.
    INTERNAL = "internal"
    #: Declined with ``none:``.
    DECLINED = "declined"
    #: A provided output, which a need binds to.
    PROVIDED = "provided"
    #: A kind of port this step of the assemblies work does not lower.
    NOT_LOWERED = "not lowered"


class Port(BaseModel):
    """One port of an assembly's interface or of a site entry (``assemblies_spec.md`` §3).

    The fields every kind shares are typed; what only an unlowered kind uses (a circuit's
    ``member``, a carrier's ``outputs``, a fact's ``many``, a provided output's ``controllable``)
    stays in ``raw``, which also keeps the port's whole written block for the import record.

    Attributes:
        name: The port's key.
        section: Where it is written: ``needs``, ``provides``, ``internal``, ``observes``,
            ``actuates`` (an assembly's interface) or ``ports`` (a site entry).
        kind: What it is.
        into: The members a need lowers into.
        partner: The partner classes of a need, by class name.
        wires: Explicit wires ``{input: output}`` a need lowers to instead of default connections.
        output: ``Member.Output`` of a provided port.
        reexports: ``<inner import>.<port>`` of a re-exported port.
        ends: The two ends ``[sender, receiver]`` of an internal port.
        optional: Whether the port may stay unbound (with a verb saying so).
        required_when: Parameter to values for which the port is required; conjunctive.
        active_when: Parameter to values outside which the port is inactive; conjunctive.
        raw: The block as written.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    section: str
    kind: PortKind
    into: Tuple[str, ...] = ()
    partner: Tuple[str, ...] = ()
    wires: Optional[Mapping[str, str]] = None
    output: Optional[str] = None
    reexports: Optional[str] = None
    ends: Tuple[str, ...] = ()
    optional: bool = False
    required_when: Mapping[str, Tuple[Any, ...]] = Field(default_factory=dict)
    active_when: Mapping[str, Tuple[Any, ...]] = Field(default_factory=dict)
    raw: Mapping[str, Any] = Field(default_factory=dict)

    @property
    def output_member(self) -> Optional[str]:
        """The member half of ``output``."""
        return self.output.split(".", 1)[0] if self.output else None

    @property
    def output_name(self) -> Optional[str]:
        """The output half of ``output``."""
        if self.output is None:
            return None
        parts = self.output.split(".", 1)
        return parts[1] if len(parts) == 2 else None


class InstanceEntry(BaseModel):
    """One named instance of an import (``assemblies_spec.md`` §2.2).

    Written either as the parameter mapping itself (``east: {azimuth_in_degree: 90}``) or, when it
    carries the reserved per-instance fields of D18/D22, in the long form ``{parameters: {...},
    preset: ..., installation_year: ..., quote: ...}``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The keys that make an instance mapping the long form.
    LONG_FORM_KEYS: ClassVar[Tuple[str, ...]] = ("preset", "parameters", "installation_year", "quote")

    name: str
    preset: Optional[Any] = None
    parameters: Mapping[str, Any] = Field(default_factory=dict)
    installation_year: Optional[Any] = None
    quote: Optional[Any] = None
    long_form: bool = False

    def to_document(self) -> Dict[str, Any]:
        """The instance as the file writes it."""
        if not self.long_form:
            return dict(self.parameters)
        document: Dict[str, Any] = {}
        if self.preset is not None:
            document["preset"] = self.preset
        if self.parameters:
            document["parameters"] = dict(self.parameters)
        if self.installation_year is not None:
            document["installation_year"] = self.installation_year
        if self.quote is not None:
            document["quote"] = self.quote
        return document


class ImportEntry(BaseModel):
    """One import: a slot that an assembly fills, once or as several named instances (§2.2).

    The same block is written at the top of an energy-system file and under ``imports`` of an
    assembly (an inner import, §2.5), where a parameter value may be ``{$param: <name>}`` of the
    importing assembly.

    Attributes:
        name: The import key.
        assembly: The assembly's library path, ``<family>/<name>``.
        preset: A named parameter set of the assembly, or ``None``.
        parameters: Parameter values; overriding the preset's.
        instances: The named instances, or ``None`` for an import of one.
        verbs: The binding verbs written on the import.
        order: The import's place in its level's evaluation order, or ``None``.
        observes: An ``observes:`` selection (parsed; lowered by hisim-lt0b.3).
        actuates: An ``actuates:`` block (parsed; lowered by hisim-lt0b.3).
        installation_year: The reserved economics field of D18, recorded, never read here.
        quote: The reserved economics field of D22, recorded, never read here.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The keys an import may carry, in canonical order.
    IMPORT_KEYS: ClassVar[Tuple[str, ...]] = (
        "order",
        "assembly",
        "preset",
        "parameters",
        "instances",
        "bind",
        "optional-bind",
        "none",
        "observes",
        "actuates",
        "installation_year",
        "quote",
    )

    name: str
    assembly: str
    preset: Optional[Any] = None
    parameters: Mapping[str, Any] = Field(default_factory=dict)
    instances: Optional[Mapping[str, InstanceEntry]] = None
    verbs: BindingVerbs = Field(default_factory=BindingVerbs)
    order: Optional[int] = None
    observes: Optional[Any] = None
    actuates: Optional[Any] = None
    installation_year: Optional[Any] = None
    quote: Optional[Any] = None

    def to_document(self) -> Dict[str, Any]:
        """The import as the file writes it, in canonical key order."""
        document: Dict[str, Any] = {}
        if self.order is not None:
            document["order"] = self.order
        document["assembly"] = self.assembly
        if self.preset is not None:
            document["preset"] = self.preset
        if self.parameters:
            document["parameters"] = dict(self.parameters)
        if self.instances is not None:
            document["instances"] = {name: instance.to_document() for name, instance in self.instances.items()}
        document.update(self.verbs.to_document())
        for key, value in (
            ("observes", self.observes),
            ("actuates", self.actuates),
            ("installation_year", self.installation_year),
            ("quote", self.quote),
        ):
            if value is not None:
                document[key] = value
        return document
