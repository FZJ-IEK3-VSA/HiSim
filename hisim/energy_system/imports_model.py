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

from hisim import loadtypes as lt
from hisim.component_interface import DeclaredFeed


class ParameterReference:
    """The ``{$param: <name>}`` spelling of a value that an assembly parameter supplies.

    ``$`` is outside the identifier grammar, so no configuration field or parameter can collide
    with the key (``assemblies_spec.md`` §2.6). The reference is kept as the raw one-key mapping
    wherever it is written and recognised here, in one place.
    """

    #: The one key of a parameter reference.
    KEY: ClassVar[str] = "$param"

    #: Keys of the other ``$`` spellings the mockup uses that the expansion does not lower, mapped
    #: to what would deliver each. ``$fact`` — "this field's value is the named sizing fact" — has
    #: no clean lowering: the sizing engine binds a fact only to a field whose class declares a law
    #: reading it (``sized_field(rule=...)``), and a file cannot give a field a law; a field that
    #: should follow a fact gets a law in its class, which a fact port then feeds. ``$derived`` is a
    #: value derived per instance (dry-run gap G12). ``$switch`` is lowered (:class:`SwitchValue`).
    UNLOWERED_KEYS: ClassVar[Mapping[str, str]] = {
        "$fact": (
            "no step: the sizing engine reads a fact only through a law its class declares on the field "
            "(sized_field(rule=...)); give the field a law and feed it with a fact port"
        ),
        "$derived": "no step yet (dry-run gap G12: a value derived per instance)",
        "$observes": "no step: {$observes: <observer port>} is a placeholder of an inputs list, never a value",
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


class SwitchValue:
    """``{$switch: <selector>, <case>: <value>, …}``: a value chosen by a parameter or an internal variant.

    The selector names a parameter of the assembly — the case keys are then its allowed values —
    or one of its internal variants — the case keys are then the variant's option names. The
    expansion replaces the mapping by the value of the case the instance's parameters select;
    the library check refuses a selector that names neither (or both), and cases that do not
    cover every allowed value exactly once, as it refuses a variant whose ``when:`` lists do
    not partition its selector (``assemblies_spec.md`` §2.6). A case's value may itself be any
    value, a ``{$param: …}`` included. Example, from the mockup's PV array: ``fact: {$switch:
    mounted_on, roof: roof_area_in_m2, facade: facade_area_in_m2}``.
    """

    #: The key that marks a switch and names its selector.
    KEY: ClassVar[str] = "$switch"

    @classmethod
    def is_switch(cls, value: Any) -> bool:
        """Whether a value is written as a switch."""
        return isinstance(value, Mapping) and cls.KEY in value

    @classmethod
    def selector_of(cls, value: Mapping[str, Any]) -> Any:
        """The selector a switch names."""
        return value[cls.KEY]

    @classmethod
    def cases_of(cls, value: Mapping[str, Any]) -> Dict[Any, Any]:
        """The cases of a switch: case key to value."""
        return {key: item for key, item in value.items() if key != cls.KEY}


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

    In an assembly member it names the observer port of the interface that lowers into the member;
    on a site entry, whose selection is its own ``observes:`` key, it is written
    ``{$observes: observes}`` (:attr:`SITE_PORT`). The expansion replaces it by one aggregator feed
    per output the selection matches (``assemblies_spec.md`` §4.2).
    """

    #: The observer port a site entry's own ``observes:`` selection is known by.
    SITE_PORT: ClassVar[str] = "observes"

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
    def is_selector(self) -> bool:
        """Whether ports of this kind are lowered by the selection pass rather than by a binding (§4)."""
        return self in (PortKind.OBSERVER, PortKind.ACTUATES)


class CircuitNaming:
    """The hydronic naming convention a circuit port lowers by (``assemblies_spec.md`` §11.1).

    A circuit ``<c>`` — its medium, written in snake case (``dhw``, ``sh``, ``solar_dhw``) — is
    carried by three outputs (``roadmap/hydronic_coupling_spec.md`` §3.1): ``MassFlow<C>``
    (kg/s, by the pump owner), ``SupplyTemperature<C>`` and ``ReturnTemperature<C>`` (°C, each by
    the end the water leaves), where ``<C>`` is the circuit's name in camel case (``Dhw``, ``Sh``,
    ``SolarDhw``). Each output is owned by exactly one end and read by the other.
    """

    #: The three quantities of a circuit, in the order they are checked and listed.
    QUANTITIES: ClassVar[Tuple[str, ...]] = ("MassFlow", "SupplyTemperature", "ReturnTemperature")

    @classmethod
    def suffix(cls, circuit: str) -> str:
        """The circuit's name as its outputs carry it: ``solar_dhw`` → ``SolarDhw``."""
        return "".join(part[:1].upper() + part[1:] for part in circuit.split("_") if part)

    @classmethod
    def outputs(cls, circuit: str) -> Tuple[str, ...]:
        """The three outputs of a circuit: ``MassFlowDhw``, ``SupplyTemperatureDhw``, ``ReturnTemperatureDhw``."""
        return tuple(f"{quantity}{cls.suffix(circuit)}" for quantity in cls.QUANTITIES)


class Carriers:
    """The carrier vocabulary of carrier ports (``assemblies_spec.md`` §5).

    A carrier is written as the value of :class:`~hisim.loadtypes.EnergyBalanceCarrier` — the
    spelling an energy port serializes (``natural_gas``, ``electricity``, ``heating_oil``) — so a
    carrier need, its provider and the ``EnergyPort`` of every consuming output name one carrier
    the same way.
    """

    #: The carrier that has no link end (§3.2, §4.3): a need only checks that its provider exists.
    ELECTRICITY: ClassVar[str] = lt.EnergyBalanceCarrier.ELECTRICITY.value

    #: The assembly that provides each carrier, for the paste-ready line of a missing provider (§5.1).
    SUPPLY_ASSEMBLIES: ClassVar[Mapping[str, str]] = {
        lt.EnergyBalanceCarrier.ELECTRICITY.value: "supply/electricity_grid",
        lt.EnergyBalanceCarrier.NATURAL_GAS.value: "supply/gas_connection",
        lt.EnergyBalanceCarrier.HEATING_OIL.value: "supply/delivered_fuel",
        lt.EnergyBalanceCarrier.PELLETS.value: "supply/delivered_fuel",
        lt.EnergyBalanceCarrier.WOOD_CHIPS.value: "supply/delivered_fuel",
        lt.EnergyBalanceCarrier.DISTRICT_HEAT.value: "supply/district_heating_substation",
    }

    @classmethod
    def names(cls) -> Tuple[str, ...]:
        """Every carrier a port may name."""
        return tuple(member.value for member in lt.EnergyBalanceCarrier)

    @classmethod
    def is_carrier(cls, value: Any) -> bool:
        """Whether a value names a carrier."""
        return isinstance(value, str) and value in cls.names()

    @classmethod
    def supply_for(cls, carrier: str) -> str:
        """The assembly a missing provider of a carrier is added with."""
        return cls.SUPPLY_ASSEMBLIES.get(carrier, f"a supply assembly providing {carrier}")


class FeedOverride(BaseModel):
    """A selector's ``feed:``: the tags and weight its matches are fed with instead of the declared ones (§4.1).

    Attributes:
        component_type: An ``lt.ComponentType`` member name, or ``None`` to keep the declared one.
        tags: ``lt.InandOutputType`` member names, or ``None`` to keep the declared ones.
        weight: The weight, or ``None`` to keep the declared one; a controller derives its weights
            from its priorities and refuses one written here (§4.4).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The keys of a ``feed:`` block.
    KEYS: ClassVar[Tuple[str, ...]] = ("component_type", "tags", "weight")

    component_type: Optional[str] = None
    tags: Optional[Tuple[str, ...]] = None
    weight: Optional[int] = None

    def to_document(self) -> Dict[str, Any]:
        """The block as the file writes it."""
        document: Dict[str, Any] = {}
        if self.component_type is not None:
            document["component_type"] = self.component_type
        if self.tags is not None:
            document["tags"] = list(self.tags)
        if self.weight is not None:
            document["weight"] = self.weight
        return document


class Selector(BaseModel):
    """One selector of an ``observes:`` list or of a controller's ``priorities`` (``assemblies_spec.md`` §4.1).

    A selector matches by the runtime tags the meter and the EMS already find their inputs by —
    ``component_type`` (``lt.ComponentType`` member names) and ``flow`` (``lt.InandOutputType``
    member names) — or by an output's name; the keys it writes hold together (a selector with
    ``component_type`` and ``flow`` matches a feed carrying both), and a list of selectors observes
    the union of its matches. What is matched is never an output itself but an output *together
    with the observer's declaration for it* (:class:`~hisim.component_interface.DeclaredFeed`): an
    output the observer's class declares no dynamic default connection from is no candidate.

    Attributes:
        component_types: The component types any of which a match carries.
        flows: The flow tags any of which a match carries.
        output: The name a match's output has.
        feed: The tags and weight to feed the matches with instead of the declared ones.
        required: Whether a selection matching nothing through this selector is refused.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The keys a selector may carry, in canonical order.
    KEYS: ClassVar[Tuple[str, ...]] = ("component_type", "flow", "output", "feed", "required")

    #: The keys that select (a selector writes at least one).
    MATCH_KEYS: ClassVar[Tuple[str, ...]] = ("component_type", "flow", "output")

    component_types: Tuple[str, ...] = ()
    flows: Tuple[str, ...] = ()
    output: Optional[str] = None
    feed: Optional[FeedOverride] = None
    required: bool = False

    def matches(self, declared: DeclaredFeed) -> bool:
        """Whether a declared feed (an output with the observer's declaration for it) is a match."""
        if self.component_types and declared.component_type not in self.component_types:
            return False
        if self.flows and not any(flow in declared.tags for flow in self.flows):
            return False
        return self.output is None or declared.output == self.output

    def text(self) -> str:
        """The selector as the file writes it, in flow style."""
        parts = []
        for key, value in self.to_document().items():
            if isinstance(value, list):
                rendered = value[0] if len(value) == 1 else "[" + ", ".join(str(item) for item in value) + "]"
            elif isinstance(value, dict):
                rendered = "{" + ", ".join(f"{name}: {item}" for name, item in value.items()) + "}"
            else:
                rendered = str(value).lower() if isinstance(value, bool) else str(value)
            parts.append(f"{key}: {rendered}")
        return "{" + ", ".join(parts) + "}"

    def to_document(self) -> Dict[str, Any]:
        """The selector as the file writes it."""
        document: Dict[str, Any] = {}
        if self.component_types:
            document["component_type"] = list(self.component_types)
        if self.flows:
            document["flow"] = list(self.flows)
        if self.output is not None:
            document["output"] = self.output
        if self.feed is not None:
            document["feed"] = self.feed.to_document()
        if self.required:
            document["required"] = True
        return document


class Selection(BaseModel):
    """What one observer observes: every output its class declares (``declared``) or a list of selectors.

    Attributes:
        declared: Whether the selection is ``declared``, every candidate.
        selectors: The selectors of a written list.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The spelling of the selection of every candidate.
    DECLARED: ClassVar[str] = "declared"

    declared: bool = False
    selectors: Tuple[Selector, ...] = ()

    def text(self) -> str:
        """The selection as the file writes it."""
        if self.declared:
            return self.DECLARED
        return "[" + ", ".join(selector.text() for selector in self.selectors) + "]"


class ImportObserves(BaseModel):
    """An import's ``observes:``: the selection that replaces the default of its assembly's observer port.

    Written as a selection — a list of selectors, or ``declared`` — for the assembly's one observer
    port, or as a mapping from observer port to selection for an assembly with several.

    Attributes:
        selection: The selection of the one observer port, or ``None`` for the mapping form.
        by_port: Observer port to selection, in the mapping form.
        raw: The block as written.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    selection: Optional[Selection] = None
    by_port: Mapping[str, Selection] = Field(default_factory=dict)
    raw: Any = None


class Port(BaseModel):
    """One port of an assembly's interface or of a site entry (``assemblies_spec.md`` §3).

    The fields every kind shares are typed; ``raw`` keeps the port's whole written block for the
    import record.

    Attributes:
        name: The port's key.
        section: Where it is written: ``needs``, ``provides``, ``internal``, ``observes``,
            ``actuates`` (an assembly's interface) or ``ports`` (a site entry).
        kind: What it is.
        into: The members a need (or a fact need) lowers into.
        partner: The partner classes of a need, by class name.
        wires: Explicit wires ``{input: output}`` a need lowers to instead of default connections.
        output: ``Member.Output`` of a provided port.
        reexports: ``<inner import>.<port>`` of a re-exported port.
        ends: The two ends ``[sender, receiver]`` of an internal port.
        circuit: The circuit (its medium, ``dhw``, ``sh``, ``brine``) of a circuit port (§3.2, §11.1).
        members: The members of a circuit end, or the one member providing a fact.
        carrier: The carrier of a carrier port, as written: an ``lt.EnergyBalanceCarrier`` value
            (``natural_gas``) or a ``{$param: …}``/``{$switch: …}`` that resolves to one.
        outputs: The consuming outputs of a carrier need, each ``Member.Output`` or the name of a
            provided port of the same assembly.
        meter: The member that meters a provided fuel carrier, and where its feeds land.
        fact: The sizing fact of a fact port, as written (a name, or a ``{$switch: …}``).
        many: Whether a fact need reads the fact over every provider (``many: true``, step 3).
        selection: An observer port's ``default:``, the selection an import may replace (§4.1).
        controllable_target: A provided output's ``controllable: {target_input: …}``: the input of
            the output's member a controller actuates directly (the battery's, D21).
        controllable_via: A provided output's ``controllable: {via: …}``: the need, partnered with
            the controller, whose binding lowers to the member's L1 modifier (§4.4).
        controllable_optional: Whether a ``target_input`` output may stay without a controller
            (``optional: true``, an EV's L1 that charges on its own); otherwise it binds the one
            controller and is refused without one.
        priorities: An ``actuates:`` port's value as written: a list of selectors, or a
            ``{$param: …}`` resolving to one.
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
    circuit: Optional[str] = None
    members: Tuple[str, ...] = ()
    carrier: Optional[Any] = None
    outputs: Tuple[str, ...] = ()
    meter: Optional[str] = None
    fact: Optional[Any] = None
    many: bool = False
    selection: Optional[Selection] = None
    controllable_target: Optional[str] = None
    controllable_via: Optional[str] = None
    controllable_optional: bool = False
    priorities: Optional[Any] = None
    optional: bool = False
    required_when: Mapping[str, Tuple[Any, ...]] = Field(default_factory=dict)
    active_when: Mapping[str, Tuple[Any, ...]] = Field(default_factory=dict)
    raw: Mapping[str, Any] = Field(default_factory=dict)

    @property
    def is_provision(self) -> bool:
        """Whether the port offers something rather than needing it.

        A provided output, a fact an assembly provides (``provides:``) and a carrier a supply
        assembly or a site entry provides (a carrier port without ``outputs``) are provisions:
        another port binds to them, and a verb never binds them. A circuit port is neither: its two
        ends are alike.
        """
        if self.kind == PortKind.PROVIDED:
            return True
        if self.kind == PortKind.FACT:
            return self.section == "provides"
        if self.kind == PortKind.CARRIER:
            return not self.outputs
        return False

    @property
    def is_controllable(self) -> bool:
        """Whether a provided output states that a controller may actuate it (§4.4)."""
        return self.controllable_target is not None or self.controllable_via is not None

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
        observes: An ``observes:`` selection replacing its assembly's observer default (§4.1).
        actuates: An ``actuates:`` block as written; the expansion refuses it on an import, since
            a controller's priorities are its assembly's ``priorities`` parameter (§4.4).
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
    observes: Optional[ImportObserves] = None
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
            ("observes", self.observes.raw if self.observes is not None else None),
            ("actuates", self.actuates),
            ("installation_year", self.installation_year),
            ("quote", self.quote),
        ):
            if value is not None:
                document[key] = value
        return document
