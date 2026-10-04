"""Expansion of imports: turning a file that imports assemblies into one flat energy-system file.

``assemblies_spec.md`` §2.3. One more pure stage in front of the group expansion: it builds a new
file and never mutates its input, it is idempotent, and a file that imports nothing comes back as
the very same object, so every committed file and every golden behaves exactly as before.

The expansion works **innermost first**. For each import and each of its instances it

1. resolves the assembly (:mod:`.resolver`), applies the preset and checks the parameters
   (:mod:`.parameters`);
2. evaluates every port's ``required_when``/``active_when`` and selects the internal variants;
3. substitutes the parameters — checking a numeric parameter's unit against the unit the fed
   config field declares (D16 b) — and gives every member its structured address
   (``ComponentID`` with ``path``), rewriting the references between members to the serialized
   names;
4. expands the inner imports and binds their ports inside the assembly — by the verbs on the inner
   import, by ``internal:`` entries, by re-exports (``from:``) and otherwise by the default rule —
   and offers the assembly's own ports to its importer;

and at the top level it binds every port of every import and of every site entry, lowering a need
to the bare partner name (the member's default connections from the partner's class) or to the
explicit wires the port names, exactly where the member's ``{$port: …}`` placeholder stands.

**What is decided here, and what after construction.** Every entry states its class, so the
expansion decides from the files alone which component is a candidate partner, whether a port has
none, several or a verb to decide it, and whether the members a port names exist; those refusals
name every candidate and end in a paste-ready ``bind:`` line. What it writes are the items a
hand-written file writes — bare names and wires — and whether the constructed components accept
them the wiring stage checks, like every other connection: a component creates its ports and
default connections in its constructor, and no second declaration of them exists. The expansion
writes one entry per lowered item into the port-provenance table of the import record
(:class:`~hisim.energy_system.assemblies.record.PortProvenance`), and a wiring refusal of such an
item is restated with the import, the port, the member, the partner and the files and lines it
came from (``EF-7H``, ``EF-7J``).

**The default rule** mirrors the sizing engine's: a port binds to the one component in scope whose
class is one of its partner classes, and a verb decides every other case. In scope are, at the top
level, the site entries and every member of every import at any depth, and inside an assembly its
own members and every member of its inner imports; a port never binds into its own instance.

**Circuits, carriers and facts** (hisim-lt0b.2). A circuit end binds the one other end of its
circuit in scope; every member at one end that carries the circuit's placeholder takes a bare name
of each member at the other end, which the wiring expands through the reader's default connections
from the owner's class, like any bare name (§3.2, §11.1). A carrier need binds the one provider of
its carrier: a fuel lowers to a bare name of each consuming member in its provider's meter, which
the wiring expands through the default feeds the meter declares, electricity to nothing but the
check that exactly one provider exists (§5); the wiring also checks each consuming output's carrier
and that the meter feeds exactly the named outputs. A fact need lowers to a
``sizing_sources`` line naming the provider (§6). ``{$switch: …}`` values are resolved with the
parameters.

**What the expansion does not lower** — observer and actuator selectors and controllable outputs
(hisim-lt0b.3), many-reads and fact exports (hisim-lt0b.4), and the ``$fact`` and ``$derived``
values — is listed in the import record and refused as a whole with ``EF-7L``, never ignored.

**Evaluation order** (D23): ``order:`` on a top-level component or import positions it, a member's
relative ``order:`` positions it inside its assembly, an import's instances follow their written
order, and the order paths are compared element by element as integers. A level numbers all its
entries or none; a repeated number at one level is refused. Without any ``order:`` the sequence is
the site's components in written order followed by the imports, and inside an assembly the file
position of its members and inner imports. The expanded file's components are written in that
sequence, which is the order the simulator adds them in.
"""

from __future__ import annotations

import string
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, FrozenSet, List, Mapping, Optional, Sequence, Set, Tuple

from hisim.config import AddressStep, ComponentID
from hisim.config.contributions import declared_facts_of
from hisim.config.sizing import declared_field_unit
from hisim.energy_system.assemblies.model import MemberTemplate, ParameterDeclaration
from hisim.energy_system.assemblies.library import CheckStrength, require_valid
from hisim.energy_system.assemblies.parameters import (
    ParameterResolver,
    ParameterSubstitution,
    ResolvedParameters,
)
from hisim.energy_system.assemblies.record import (
    CarrierConsumer,
    CarrierRecord,
    CircuitEndRecord,
    CircuitRecord,
    ImportRecord,
    InstanceRecord,
    LoweredKind,
    LoweredPort,
    NotLowered,
    PortRecord,
    SourceMapEntry,
    short_class_name,
)
from hisim.energy_system.bindings import facts_read_by
from hisim.energy_system.assemblies.resolver import AssemblyResolver, ResolvedAssembly
from hisim.energy_system.classes import ClassBinder
from hisim.energy_system.errors import (
    EnergySystemAssemblyError,
    EnergySystemErrorId,
)
from hisim.energy_system.imports_model import (
    BindingVerbs,
    Carriers,
    CircuitNaming,
    ImportEntry,
    ObservesPlaceholder,
    ParameterReference,
    Port,
    PortKind,
    PortPlaceholder,
)
from hisim.energy_system.model import (
    AggregatorFeed,
    AnyInputItem,
    ComponentEntry,
    DefaultInputs,
    EnergySystemFile,
    ExplicitWire,
    SourceReference,
)
from hisim.energy_system.source_lines import LineIndex, SourceLocation

#: The nesting depth beyond which the expansion refuses (§2.5, D7): a safeguard, not a model limit.
MAXIMUM_DEPTH = 4


class ClassFacts:
    """Component classes and configuration classes, imported once per path."""

    def __init__(self) -> None:
        """Starts with empty caches."""
        self._components: Dict[str, type] = {}
        self._configs: Dict[str, type] = {}

    def component_class(self, class_path: str, location: str, name: str) -> type:
        """The component class of a dotted path (``EF-10`` when it does not import)."""
        if class_path not in self._components:
            self._components[class_path] = ClassBinder.component_class_of(class_path, location, name)
        return self._components[class_path]

    def config_class(self, class_path: str, location: str, name: str) -> type:
        """The configuration class the component of a dotted path takes."""
        if class_path not in self._configs:
            component = self.component_class(class_path, location, name)
            self._configs[class_path] = ClassBinder.configuration_class_of(component, location, name)
        return self._configs[class_path]


@dataclass
class Unit:
    """One component of the expanded system while it is being built: an assembly member or a site entry.

    Attributes:
        name: Its expanded name (a site entry's own name).
        entry: Its entry, parameters substituted, local names not yet rewritten.
        identity: Its structured address, ``None`` for a site entry.
        chain: The files and lines it came from, outermost first.
        import_path: ``pv[east]``/``dhw → generator``; empty for a site entry.
        local_names: Local member name to expanded name, for rewriting its references.
        dropped_names: Local names of members its assembly's selected variants left out.
        order_path: Its order path, relative to the level that holds it.
        lowered: Placeholder position to the items its port lowered to.
        notes: Placeholder position to what produced those items.
        lines: The line index of the file it was written in, and the key path of its block.
    """

    name: str
    entry: ComponentEntry
    identity: Optional[ComponentID]
    chain: Tuple[SourceLocation, ...]
    import_path: str
    local_names: Dict[str, str]
    dropped_names: FrozenSet[str]
    order_path: Tuple[int, ...]
    lines: LineIndex
    block_path: Tuple[str, ...]
    lowered: Dict[int, List[AnyInputItem]] = field(default_factory=dict)
    notes: Dict[int, str] = field(default_factory=dict)
    lowered_sizing: Dict[str, SourceReference] = field(default_factory=dict)
    sizing_notes: Dict[str, str] = field(default_factory=dict)

    @property
    def class_path(self) -> str:
        """The dotted class path of the component."""
        return self.entry.class_path

    def placeholder_positions(self, port: str) -> List[Tuple[int, PortPlaceholder]]:
        """The positions of the placeholders for one port in the written input list."""
        return [
            (placed.position, placed.placeholder)
            for placed in self.entry.placeholders
            if isinstance(placed.placeholder, PortPlaceholder) and placed.placeholder.port == port
        ]


@dataclass
class Landing:
    """Where a need port's items land: one placeholder of one unit."""

    unit: Unit
    position: int
    placeholder: PortPlaceholder


@dataclass(eq=False)
class CircuitEndState:
    """One end of a hydronic circuit while the expansion binds it (§3.2, §11.1).

    Shared between an inner import's circuit handle and the outer handle that re-exports it, so a
    binding seen from either is seen from both.

    Attributes:
        circuit: The circuit's name, its medium.
        owner: The import path or site component holding the end.
        port: The circuit port's name at the owner.
        units: The members at this end.
        bound_to: The other end, once bound.
        declined: Whether ``none:`` declined it.
    """

    circuit: str
    owner: str
    port: str
    units: List[Unit]
    bound_to: Optional["CircuitEndState"] = None
    declined: bool = False

    @property
    def label(self) -> str:
        """How messages and the record name the end: ``heating.dhw``, ``HeatDistribution.sh``."""
        return f"{self.owner}.{self.port}"


@dataclass(eq=False)
class Provision:
    """One provider of a carrier while the expansion binds the needs to it (§5.1).

    Attributes:
        carrier: The carrier, an ``lt.EnergyBalanceCarrier`` value.
        owner: The import path or site component providing it.
        port: The providing port.
        meter: The member metering a fuel, ``None`` for electricity.
        landing: Where the consumers' feeds land in the meter, ``None`` for electricity.
        record: The provider's record.
    """

    carrier: str
    owner: str
    port: str
    meter: Optional[Unit]
    landing: Optional["Landing"]
    record: CarrierRecord

    @property
    def label(self) -> str:
        """How messages name the provider: ``gas.connection``."""
        return f"{self.owner}.{self.port}"

    @property
    def is_electricity(self) -> bool:
        """Whether this is the electricity provider, which has no link end."""
        return self.carrier == Carriers.ELECTRICITY


@dataclass
class Handle:
    """One port of an expanded instance or of a site entry, as its importer sees it.

    Attributes:
        owner: How messages name the owner: ``import 'dhw'`` or ``component 'Building'``.
        owner_path: The owner's import path (``pv[east]``) or a site entry's name.
        verb_site: Where a verb for this port is written, for the paste-ready line.
        port: The port's declaration.
        state: ``required``, ``optional`` or ``inactive``.
        landings: Where a need's items land; resolved through re-exports.
        provider: ``(component, output)`` of a provided output.
        own_units: The expanded names inside the owner, which are never its partners.
        chain: The source map of the owner's port.
        record: The port's record in the import record.
        decided: Whether a verb, an internal entry, a re-export or the default rule has decided it.
        end: A circuit port's end.
        provision: A carrier provision's provider.
        carrier: A carrier port's carrier, resolved.
        consumer_outputs: A carrier need's consuming outputs, as ``(unit, output)``.
        fact: A fact port's fact, resolved.
        fact_into: The units a fact need lowers into.
        hint: The candidates and the paste-ready verb lines a refusal of this port prints.
    """

    owner: str
    owner_path: str
    verb_site: str
    port: Port
    state: str
    landings: List[Landing]
    provider: Optional[Tuple[str, str]]
    own_units: FrozenSet[str]
    chain: Tuple[SourceLocation, ...]
    record: Dict[str, Any] = field(default_factory=dict)
    decided: bool = False
    hint: str = ""
    end: Optional[CircuitEndState] = None
    provision: Optional[Provision] = None
    carrier: Optional[str] = None
    consumer_outputs: List[Tuple[Unit, str]] = field(default_factory=list)
    fact: Optional[str] = None
    fact_into: List[Unit] = field(default_factory=list)

    @property
    def partner_text(self) -> str:
        """The partner classes as a message lists them."""
        return ", ".join(self.port.partner) or "-"

    @property
    def partner_label(self) -> str:
        """What the port binds to, as a message names it: ``partner MockWeather``, ``circuit dhw``."""
        if self.port.kind == PortKind.CIRCUIT:
            return f"circuit {self.end.circuit if self.end is not None else self.port.circuit}"
        if self.port.kind == PortKind.CARRIER:
            return f"carrier {self.carrier}"
        if self.port.kind == PortKind.FACT:
            return f"fact {self.fact}"
        return f"partner {self.partner_text}"

    def source_text(self) -> str:
        """The handle's source map for a message."""
        return f"({self.owner}, {' → '.join(location.text for location in self.chain)})"


@dataclass
class Instance:
    """One expanded import instance: its units at every depth and the ports it offers its importer."""

    import_path: str
    units: List[Unit]
    handles: Dict[str, Handle]
    record: InstanceRecord


@dataclass
class Candidate:
    """One partner a circuit end, a fuel need or a fact need may bind to.

    Attributes:
        reference: How a verb names it (``heating.dhw``, ``gas``, ``Building``).
        label: How a message names it.
        payload: What the lowering takes: the other circuit end's handle, the provider's handle,
            or the providing component's expanded name.
    """

    reference: str
    label: str
    payload: Any


@dataclass
class CrossRule:
    """How one port kind finds its candidates, resolves a verb's partner and lowers a binding.

    Attributes:
        candidates: The partners the default rule chooses from.
        resolve: A verb's partner reference to the payload, or ``None`` with why it is absent.
        lower: Lowers the binding to a payload under a verb (``default`` for the default rule).
        ambiguous: The error a port with several candidates and no verb is refused with.
        missing: The error, problem and remedy of a required port without a candidate.
    """

    candidates: List[Candidate]
    resolve: Callable[[str], Tuple[Optional[Any], str]]
    lower: Callable[[Any, str], None]
    ambiguous: EnergySystemErrorId
    missing: Callable[[], Tuple[EnergySystemErrorId, str, str]]


@dataclass
class Level:
    """One level of binding: the top of a file, or the inside of one assembly instance.

    Attributes:
        units: Every unit in scope, nested ones included, in production order.
        references: Expanded name to how a verb at this level names it (``control``,
            ``pv.east``, a member's or site entry's own name).
        targets: How a verb at this level names a component or an import instance, to the
            units and the handles behind it.
        where_verbs: For a message: where the verbs of this level are written.
        members: The references that name one component of the level itself — a site entry at the
            top, a member inside an assembly — rather than an import instance.
        handles: Every port in scope at this level with the reference its owner is named by: the
            site entries' ports at the top, and every import instance's.
    """

    units: List[Unit]
    references: Dict[str, str]
    targets: Dict[str, Tuple[List[Unit], Dict[str, Handle]]]
    where_verbs: str
    members: FrozenSet[str] = frozenset()
    handles: List[Tuple[str, Handle]] = field(default_factory=list)


class ImportExpander:
    """Expands the imports of one energy-system file. Single use; :func:`expand_imports` drives it."""

    def __init__(self, model: EnergySystemFile, resolver: AssemblyResolver, lines: Optional[LineIndex] = None) -> None:
        """Prepares the expansion of one file.

        Args:
            model: The file as read.
            resolver: Finds the assemblies its imports name.
            lines: The file's line index, for the source map; an empty one when the file exists
                only in memory.
        """
        self.model = model
        self.resolver = resolver
        self.lines = lines or LineIndex.empty("<energy system>")
        self.classes = ClassFacts()
        self.record = ImportRecord()
        self._site_handles_cache: Dict[str, Dict[str, Handle]] = {}
        self._provisions: List[Tuple[Provision, Handle]] = []
        self._carrier_checks: List[Tuple[str, str, str]] = []
        self._checked: Set[str] = set()
        self._recorded: List[Tuple[InstanceRecord, Mapping[str, Handle]]] = []

    # ------------------------------------------------------------------------------------------ errors

    def error(
        self, error_id: EnergySystemErrorId, location: str, problem: str, **kwargs: Any
    ) -> EnergySystemAssemblyError:
        """One refusal of the expansion."""
        return EnergySystemAssemblyError(error_id, location, problem, **kwargs)

    def not_lowered(self, where: str, step: str) -> None:
        """Notes a construct this step does not lower; :meth:`expand` refuses them all at the end."""
        item = NotLowered(where=where, step=step)
        if item not in self.record.not_lowered:
            self.record.not_lowered.append(item)

    # --------------------------------------------------------------------------------------- top level

    def expand(self) -> Tuple[EnergySystemFile, ImportRecord]:
        """Expands the file.

        Returns:
            The flat file and the import record.

        Raises:
            EnergySystemAssemblyError: For any condition of the ``EF-7x`` band.
        """
        model = self.model
        self._check_top_names()
        site_units = [self._site_unit(name, entry) for name, entry in model.components.items()]
        for unit in site_units:
            self._site_handles(unit)
        instances: Dict[str, List[Tuple[Optional[str], Instance]]] = {}
        for key, entry in model.imports.items():
            instances[key] = self._expand_top_import(key, entry)
        all_units: List[Unit] = list(site_units)
        for results in instances.values():
            for _instance_key, instance in results:
                all_units.extend(instance.units)
        self._check_unique_names(all_units)
        level = self._level(
            units=all_units,
            members={unit.name: unit for unit in site_units},
            imports=instances,
            import_entries=model.imports,
            where_verbs="the file",
        )
        handles: List[Tuple[BindingVerbs, Handle]] = []
        for unit in site_units:
            site_handles = self._site_handles(unit)
            self._check_verbs_name_ports(unit.entry.verbs, site_handles, f"components.{unit.name}")
            for handle in site_handles.values():
                handles.append((unit.entry.verbs, handle))
        for key, results in instances.items():
            entry = model.imports[key]
            for _instance_key, instance in results:
                self._check_verbs_name_ports(entry.verbs, instance.handles, f"imports.{key}")
                for handle in instance.handles.values():
                    handles.append((entry.verbs, handle))
        for verbs, handle in self._decision_order(handles):
            self._decide(handle, verbs, level, top=True)
        self._check_provisions()
        self._order_top(site_units, instances)
        for record, instance_handles in self._recorded:
            record.ports = [self._port_record(handle) for handle in instance_handles.values()]
        if self.record.not_lowered:
            raise self.error(
                EnergySystemErrorId.NOT_LOWERED_IN_THIS_STEP,
                "imports",
                "the file uses constructs this step of the assemblies work does not lower: "
                + "; ".join(item.text() for item in self.record.not_lowered)
                + ".",
                remedy="They are parsed and recorded, never ignored; the steps named deliver them.",
            )
        return self._assemble(site_units, all_units), self.record

    def _check_top_names(self) -> None:
        """Refuses an import key that is also a site component's name: a verb could mean either."""
        for key in self.model.imports:
            if key in self.model.all_components() or key in self.model.groups or key in self.model.variants:
                raise self.error(
                    EnergySystemErrorId.DUPLICATE_NAME,
                    f"imports.{key}",
                    f"the import '{key}' has the name of a component, a group or a variant of the file.",
                    remedy="A verb names a partner by a bare name, so an import key and a site name may not share one.",
                )

    def _check_unique_names(self, units: Sequence[Unit]) -> None:
        """Postcondition: every expanded name is unique (the address grammar guarantees it)."""
        seen: Set[str] = set()
        for unit in units:
            assert unit.name not in seen, f"the expansion produced '{unit.name}' twice"
            seen.add(unit.name)

    def _site_unit(self, name: str, entry: ComponentEntry) -> Unit:
        """A site entry as a unit of the top level."""
        for placed in entry.placeholders:
            if isinstance(placed.placeholder, ObservesPlaceholder):
                self.not_lowered(
                    f"component {name}: placeholder $observes {placed.placeholder.observer}",
                    "hisim-lt0b.3 (observe and actuate selectors)",
                )
        return Unit(
            name=name,
            entry=entry,
            identity=None,
            chain=(self.lines.location("components", name),),
            import_path="",
            local_names={},
            dropped_names=frozenset(),
            order_path=(),
            lines=self.lines,
            block_path=("components", name),
        )

    def _expand_top_import(self, key: str, entry: ImportEntry) -> List[Tuple[Optional[str], Instance]]:
        """Expands one top-level import, each of its instances."""
        self._note_import_selectors(key, entry)
        results: List[Tuple[Optional[str], Instance]] = []
        location = f"imports.{key}"
        assembly = self.resolver.resolve(entry.assembly, location)
        chain_root = self.lines.location("imports", key)
        for instance_key, preset, given, chain_tail in self._instances_of(entry, ("imports", key), self.lines):
            references = ParameterSubstitution.references_in({"preset": preset, "parameters": dict(given)})
            if references:
                raise self.error(
                    EnergySystemErrorId.PARAMETER_INVALID,
                    location,
                    f"the import '{key}' writes {{$param: {references[0][0]}}} at {'.'.join(references[0][1])}, "
                    "but an energy-system file has no parameters; only an assembly's inner imports take them.",
                )
            step = AddressStep(import_key=key, instance=instance_key)
            instance = self._expand_instance(
                assembly,
                preset=preset,
                given=given,
                path=(step,),
                chain=(chain_root,) if instance_key is None else (chain_tail,),
                stack=(),
                reserved=self._reserved_of(entry, instance_key),
            )
            results.append((instance_key, instance))
        return results

    @staticmethod
    def _reserved_of(entry: ImportEntry, instance_key: Optional[str]) -> Dict[str, Any]:
        """The reserved economics fields an import or instance carries, as written (D18, D22)."""
        reserved: Dict[str, Any] = {}
        source: Any = entry
        if instance_key is not None and entry.instances is not None:
            source = entry.instances[instance_key]
        for key in ("installation_year", "quote"):
            value = getattr(source, key, None)
            if value is not None:
                reserved[key] = value
        return reserved

    def _note_import_selectors(self, key: str, entry: ImportEntry) -> None:
        """Notes an import's ``observes:``/``actuates:``, which hisim-lt0b.3 lowers."""
        if entry.observes is not None:
            self.not_lowered(f"import {key}: observes", "hisim-lt0b.3 (observe and actuate selectors)")
        if entry.actuates is not None:
            self.not_lowered(f"import {key}: actuates", "hisim-lt0b.3 (observe and actuate selectors)")

    @staticmethod
    def _instances_of(
        entry: ImportEntry, block_path: Tuple[str, ...], lines: LineIndex
    ) -> List[Tuple[Optional[str], Optional[Any], Mapping[str, Any], SourceLocation]]:
        """The instances of an import as ``(instance key, preset, parameters, location)``."""
        if entry.instances is None:
            return [(None, entry.preset, entry.parameters, lines.location(*block_path))]
        return [
            (name, instance.preset, instance.parameters, lines.location(*block_path, "instances", name))
            for name, instance in entry.instances.items()
        ]

    # ------------------------------------------------------------------------------------ one instance

    @staticmethod
    def path_text(path: Tuple[AddressStep, ...]) -> str:
        """An import path as messages and the record name it: ``dhw → generator``, ``pv[east]``."""
        return " → ".join(
            f"{step.import_key}[{step.instance}]" if step.instance is not None else step.import_key for step in path
        )

    def _expand_instance(
        self,
        assembly: ResolvedAssembly,
        *,
        preset: Optional[Any],
        given: Mapping[str, Any],
        path: Tuple[AddressStep, ...],
        chain: Tuple[SourceLocation, ...],
        stack: Tuple[str, ...],
        reserved: Mapping[str, Any],
    ) -> Instance:
        """Expands one instance of one assembly, its inner imports first."""
        import_path = self.path_text(path)
        where = f"import {import_path}"
        if assembly.path in stack:
            raise self.error(
                EnergySystemErrorId.ASSEMBLY_CYCLE,
                where,
                f"the assembly '{assembly.path}' imports itself: {' → '.join(stack + (assembly.path,))}.",
            )
        if len(path) > MAXIMUM_DEPTH:
            raise self.error(
                EnergySystemErrorId.ASSEMBLY_TOO_DEEP,
                where,
                f"the imports nest {len(path)} deep ({' → '.join(stack + (assembly.path,))}); at most "
                f"{MAXIMUM_DEPTH} are allowed.",
            )
        if assembly.path not in self._checked:
            require_valid(assembly, self.resolver, CheckStrength.EXPANSION)
            self._checked.add(assembly.path)
        model = assembly.model
        source = " → ".join(location.text for location in chain)
        parameters = ParameterResolver(model, assembly.path, where, lambda: f"({where}, {source})").resolve(
            preset if isinstance(preset, str) or preset is None else str(preset), given
        )
        record = InstanceRecord(
            path=import_path,
            assembly=assembly.path,
            file=assembly.label,
            sha256=assembly.sha256,
            preset=parameters.preset,
            parameters_given=dict(parameters.given),
            parameters_resolved=dict(parameters.resolved),
            reserved=dict(reserved),
        )
        selected, dropped = self._select_variants(assembly, parameters, record, where)
        selections = {**dict(parameters.resolved), **record.variants}
        states = {name: self._state(port, parameters.resolved) for name, port in model.ports.items()}
        for name, port in model.ports.items():
            if states[name] != "inactive":
                self._note_unlowered_port(import_path, port)
        units = {
            name: self._member_unit(
                assembly, template, parameters, selections, path, chain, selected, dropped, f"{where}, {source}"
            )
            for name, template in selected.items()
        }
        for name, unit in units.items():
            record.members[unit.name] = {"member": name}
            if unit.identity is not None and unit.identity.display_name is not None:
                record.members[unit.name]["display_name"] = unit.identity.display_name
        inner: Dict[str, List[Tuple[Optional[str], Instance]]] = {}
        for key, entry in model.imports.items():
            inner[key] = self._expand_inner_import(
                assembly,
                key,
                entry,
                ParameterSubstitution(parameters.resolved, selections=selections),
                path,
                chain,
                stack,
            )
        all_units: List[Unit] = list(units.values())
        for results in inner.values():
            for _key, instance in results:
                all_units.extend(instance.units)
        level = self._level(
            units=all_units,
            members=units,
            imports=inner,
            import_entries=model.imports,
            where_verbs=f"'{assembly.label}'",
        )
        handles = self._own_handles(assembly, units, inner, states, selections, path, chain)
        self._bind_inside(assembly, level, inner, units, import_path)
        self._check_placeholders(assembly, units, where)
        self._order_level(assembly, units, inner, where)
        for unit in all_units:
            if unit.identity is not None:
                self.record.addresses[unit.name] = unit.identity
        self._recorded.append((record, handles))
        self.record.instances.append(record)
        return Instance(import_path=import_path, units=all_units, handles=handles, record=record)

    def _expand_inner_import(
        self,
        assembly: ResolvedAssembly,
        key: str,
        entry: ImportEntry,
        substitution: ParameterSubstitution,
        path: Tuple[AddressStep, ...],
        chain: Tuple[SourceLocation, ...],
        stack: Tuple[str, ...],
    ) -> List[Tuple[Optional[str], Instance]]:
        """Expands one inner import of an assembly, its parameters substituted from the outer ones."""
        self._note_import_selectors(f"{self.path_text(path)} → {key}", entry)
        inner_assembly = self.resolver.resolve(entry.assembly, f"{assembly.label}: imports.{key}")
        results: List[Tuple[Optional[str], Instance]] = []
        for instance_key, preset, given, location in self._instances_of(entry, ("imports", key), assembly.lines):
            results.append(
                (
                    instance_key,
                    self._expand_instance(
                        inner_assembly,
                        preset=substitution.apply(preset),
                        given=substitution.apply(dict(given)),
                        path=path + (AddressStep(import_key=key, instance=instance_key),),
                        chain=chain + (location,),
                        stack=stack + (assembly.path,),
                        reserved=self._reserved_of(entry, instance_key),
                    ),
                )
            )
        return results

    def _select_variants(
        self, assembly: ResolvedAssembly, parameters: ResolvedParameters, record: InstanceRecord, where: str
    ) -> Tuple[Dict[str, MemberTemplate], FrozenSet[str]]:
        """The members of the selected world, and the names the other options would have added."""
        model = assembly.model
        selected: Dict[str, MemberTemplate] = dict(model.components)
        offered: Set[str] = set()
        for variant in model.variants.values():
            value = parameters.resolved.get(variant.selected_by)
            option = variant.option_for(value)
            if option is None:
                raise self.error(
                    EnergySystemErrorId.ASSEMBLY_LIBRARY_CHECK,
                    where,
                    f"the internal variant '{variant.name}' of '{assembly.path}' has no option for "
                    f"{variant.selected_by}={value!r}; its when: lists must partition the parameter's values.",
                )
            record.variants[variant.name] = option.name
            for other in variant.options.values():
                offered.update(other.components)
            selected.update(option.components)
        return selected, frozenset(offered - set(selected))

    @staticmethod
    def _holds(conditions: Mapping[str, Tuple[Any, ...]], values: Mapping[str, Any]) -> bool:
        """Whether a conjunctive ``{parameter: [values]}`` condition holds."""
        return all(values.get(parameter) in allowed for parameter, allowed in conditions.items())

    def _state(self, port: Port, values: Mapping[str, Any]) -> str:
        """A port's requirement state for one instance (§3.1)."""
        if port.active_when and not self._holds(port.active_when, values):
            return "inactive"
        if port.required_when:
            return "required" if self._holds(port.required_when, values) else "inactive"
        if port.kind == PortKind.PROVIDED or port.section == "provides" or port.is_provision or port.optional:
            return "optional"
        return "required"

    #: The bead that delivers sizing over all providers and fact exports (§6, §13 step 3).
    MANY_SIZING_STEP = "hisim-lt0b.4 (Many with Sum, fact exports; spec §6, §13 step 3)"

    def _note_unlowered_port(self, import_path: str, port: Port) -> None:
        """Notes an active port, or a part of one, the expansion does not lower yet."""
        if not port.kind.is_lowered:
            self.not_lowered(f"{import_path}: port {port.name} ({port.kind.value})", port.kind.delivering_step)
        if port.kind == PortKind.PROVIDED and "controllable" in port.raw:
            self.not_lowered(
                f"{import_path}: port {port.name} (controllable)",
                "hisim-lt0b.3 (observe and actuate selectors, controller lowering)",
            )
        if port.kind == PortKind.FACT and port.many:
            self.not_lowered(f"{import_path}: port {port.name} (fact, many: true)", self.MANY_SIZING_STEP)
        if port.kind == PortKind.FACT and "export" in port.raw:
            self.not_lowered(f"{import_path}: port {port.name} (fact export)", self.MANY_SIZING_STEP)

    def _member_unit(
        self,
        assembly: ResolvedAssembly,
        template: MemberTemplate,
        parameters: ResolvedParameters,
        selections: Mapping[str, Any],
        path: Tuple[AddressStep, ...],
        chain: Tuple[SourceLocation, ...],
        selected: Mapping[str, MemberTemplate],
        dropped: FrozenSet[str],
        where: str,
    ) -> Unit:
        """One member of an instance: parameters and switches substituted, units checked, address given.

        The member's identity carries its address and the English display name its ``display:``
        template renders over the resolved parameters (§2.4), which reaches the component through
        its configuration's ``component_id`` and from there every KPI source.
        """
        entry = template.entry
        name = entry.name
        identity = ComponentID(
            name=name,
            path=path,
            assembly=assembly.path,
            display_name=self._display(template, parameters, assembly, where),
        )
        location = f"{assembly.label}: {'.'.join(template.source_path)}"
        declarations = assembly.model.parameters
        for key, value_path in ParameterSubstitution.unlowered_in(
            {"config": dict(entry.config), "arguments": dict(entry.constructor.arguments) if entry.constructor else {}}
        ):
            self.not_lowered(
                f"{self.path_text(path)}: member {name} {'.'.join(value_path)} ({key})",
                ParameterReference.UNLOWERED_KEYS[key],
            )
        for placed in entry.placeholders:
            if isinstance(placed.placeholder, ObservesPlaceholder):
                self.not_lowered(
                    f"{self.path_text(path)}: member {name} placeholder $observes {placed.placeholder.observer}",
                    "hisim-lt0b.3 (observe and actuate selectors)",
                )

        def check_config_unit(parameter: str, value_path: Tuple[str, ...]) -> None:
            self._check_unit(declarations[parameter], entry.class_path, value_path, location, name, where, assembly)

        def check_argument_unit(parameter: str, value_path: Tuple[str, ...]) -> None:
            declaration = declarations[parameter]
            if declaration.type.is_numeric:
                raise self.error(
                    EnergySystemErrorId.FED_FIELD_WITHOUT_UNIT,
                    location,
                    f"the numeric parameter '{parameter}' feeds the constructor argument "
                    f"'{'.'.join(value_path)}' of the member '{name}', and a constructor argument declares no "
                    f"unit ({where}).",
                    remedy="Feed a configuration field that declares its unit instead (D16 b).",
                )

        config = ParameterSubstitution(parameters.resolved, check_config_unit, selections).apply(dict(entry.config))
        constructor = entry.constructor
        if constructor is not None:
            arguments = ParameterSubstitution(parameters.resolved, check_argument_unit, selections).apply(
                dict(constructor.arguments)
            )
            constructor = constructor.model_copy(update={"arguments": arguments})
        preset = entry.preset
        if template.preset_parameter is not None:
            preset = parameters.resolved[template.preset_parameter]
            if not isinstance(preset, str):
                raise self.error(
                    EnergySystemErrorId.PARAMETER_INVALID,
                    location,
                    f"the member '{name}' takes its preset from '{template.preset_parameter}', which resolves to "
                    f"{preset!r}, not a preset name ({where}).",
                )
        substituted = entry.model_copy(update={"config": config, "constructor": constructor, "preset": preset})
        local_names = {member: ComponentID(name=member, path=path).address for member in selected}
        return Unit(
            name=identity.address,
            entry=substituted,
            identity=identity,
            chain=chain + (assembly.lines.location(*template.source_path),),
            import_path=self.path_text(path),
            local_names=local_names,
            dropped_names=dropped,
            order_path=(),
            lines=assembly.lines,
            block_path=template.source_path,
        )

    def _check_unit(
        self,
        declaration: ParameterDeclaration,
        class_path: str,
        value_path: Tuple[str, ...],
        location: str,
        member: str,
        where: str,
        assembly: ResolvedAssembly,
    ) -> None:
        """Checks a parameter's unit against the config field it feeds (§2.6, D16 b).

        A numeric parameter may only feed a top-level field that declares a unit, and its own
        unit must be that one; a mismatch is a load error, never a conversion. A parameter of any
        other type carries no unit and feeds without the check.
        """
        if not declaration.type.is_numeric:
            return
        field_name = value_path[0]
        config_class = self.classes.config_class(class_path, location, member)
        if len(value_path) != 1:
            raise self.error(
                EnergySystemErrorId.FED_FIELD_WITHOUT_UNIT,
                location,
                f"the numeric parameter '{declaration.name}' feeds '{'.'.join(value_path)}' inside the field "
                f"'{field_name}' of '{member}' ({config_class.__name__}); a nested value declares no unit ({where}).",
                remedy="Feed a top-level configuration field that declares its unit.",
            )
        field_unit = declared_field_unit(config_class, field_name)
        if field_unit is None:
            raise self.error(
                EnergySystemErrorId.FED_FIELD_WITHOUT_UNIT,
                location,
                f"the parameter '{declaration.name}' of '{assembly.path}' feeds the field '{field_name}' of "
                f"{config_class.__name__} (member '{member}'), which declares no unit ({where}).",
                remedy=(
                    f"Declare it on the field: sized_field(..., unit=lt.Units.<UNIT>) or "
                    f"field(metadata={{'unit': lt.Units.<UNIT>}}) on {config_class.__name__}.{field_name}."
                ),
            )
        field_unit_name = getattr(field_unit, "name", str(field_unit))
        if declaration.unit != field_unit_name:
            raise self.error(
                EnergySystemErrorId.PARAMETER_UNIT_MISMATCH,
                location,
                f"the parameter '{declaration.name}' of '{assembly.path}' is in {declaration.unit or 'no unit'}, "
                f"but the field '{field_name}' of {config_class.__name__} (member '{member}') it feeds is in "
                f"{field_unit_name} ({where}).",
                remedy="A unit mismatch is never converted; state the field's unit on the parameter.",
            )

    def _display(
        self, template: MemberTemplate, parameters: ResolvedParameters, assembly: ResolvedAssembly, where: str
    ) -> Optional[str]:
        """The member's English display name from its ``display:`` template (§2.4), or ``None``."""
        if template.display is None:
            return None
        names = [name for _text, name, _spec, _conversion in string.Formatter().parse(template.display) if name]
        unknown = [name for name in names if name not in parameters.resolved]
        if unknown:
            raise self.error(
                EnergySystemErrorId.PORT_CONTRACT,
                f"{assembly.label}: {'.'.join(template.source_path)}.display",
                f"the display template of '{template.name}' names {', '.join(unknown)}, which "
                f"{'is' if len(unknown) == 1 else 'are'} no parameter of '{assembly.path}' ({where}).",
                alternatives=tuple(parameters.resolved),
                alternatives_label="parameters",
            )
        return template.display.format(**parameters.resolved)

    # ------------------------------------------------------------------------------------------- ports

    def _own_handles(
        self,
        assembly: ResolvedAssembly,
        units: Mapping[str, Unit],
        inner: Mapping[str, List[Tuple[Optional[str], Instance]]],
        states: Mapping[str, str],
        selections: Mapping[str, Any],
        path: Tuple[AddressStep, ...],
        chain: Tuple[SourceLocation, ...],
    ) -> Dict[str, Handle]:
        """The ports an instance offers its importer, resolved to landings, ends, providers and facts."""
        import_path = self.path_text(path)
        own_units = frozenset(unit.name for unit in units.values()) | frozenset(
            unit.name for results in inner.values() for _key, instance in results for unit in instance.units
        )
        substitution = ParameterSubstitution(
            {name: value for name, value in selections.items() if name in assembly.model.parameters},
            selections=selections,
        )
        handles: Dict[str, Handle] = {}
        for name, port in assembly.model.ports.items():
            if port.kind == PortKind.INTERNAL:
                continue
            port_chain = chain + (assembly.lines.location("interface", port.section, name),)
            handle = Handle(
                owner=f"import {import_path}",
                owner_path=import_path,
                verb_site=f"the import '{path[-1].import_key}'",
                port=port,
                state=states[name],
                landings=[],
                provider=None,
                own_units=own_units,
                chain=port_chain,
            )
            location = f"{assembly.label}: interface.{port.section}.{name}"
            if port.kind == PortKind.NEED:
                for member in port.into:
                    unit = units.get(member)
                    if unit is None:
                        continue
                    positions = unit.placeholder_positions(name)
                    for position, placeholder in positions:
                        handle.landings.append(Landing(unit=unit, position=position, placeholder=placeholder))
                if handle.state != "inactive" and not handle.landings:
                    raise self.error(
                        EnergySystemErrorId.PORT_CONTRACT,
                        location,
                        f"the port '{name}' is active, but none of its members {', '.join(port.into)} exists with "
                        f"these parameters or carries a '{{$port: {name}}}' placeholder {handle.source_text()}.",
                    )
            elif port.kind == PortKind.PROVIDED:
                provider_member, output = port.output_member, port.output_name
                unit = units.get(provider_member or "")
                if unit is None:
                    if handle.state != "inactive":
                        raise self.error(
                            EnergySystemErrorId.PORT_CONTRACT,
                            location,
                            f"the provided port '{name}' names the member '{provider_member}', which does not exist "
                            f"with these parameters {handle.source_text()}.",
                        )
                else:
                    handle.provider = (unit.name, output or "")
                    if handle.state != "inactive":
                        self._note_lowered(handle, LoweredKind.PROVIDED, unit, "", output=output or "")
            elif port.kind == PortKind.CIRCUIT:
                self._circuit_handle(handle, [units[member] for member in port.members if member in units], location)
            elif port.kind == PortKind.CARRIER:
                self._carrier_handle(handle, assembly, units, substitution.apply(port.carrier), location)
            elif port.kind == PortKind.FACT:
                self._fact_handle(handle, units, substitution.apply(port.fact), location)
            elif port.kind == PortKind.REEXPORT:
                self._reexport(handle, assembly, inner, import_path, location)
            handles[name] = handle
        return handles

    def _reexport(
        self,
        handle: Handle,
        assembly: ResolvedAssembly,
        inner: Mapping[str, List[Tuple[Optional[str], Instance]]],
        import_path: str,
        location: str,
    ) -> None:
        """Resolves a re-exported port (``from: <inner import>.<port>``) to what the inner port resolved to."""
        port = handle.port
        name = port.name
        inner_key, inner_port = (port.reexports or ".").split(".", 1)
        results = inner.get(inner_key)
        if results is None:
            raise self.error(
                EnergySystemErrorId.PORT_CONTRACT,
                location,
                f"the port '{name}' re-exports '{port.reexports}', but '{inner_key}' is no inner import of "
                f"'{assembly.path}' {handle.source_text()}.",
                alternatives=tuple(inner),
                alternatives_label="inner imports",
            )
        for _instance_key, instance in results:
            inner_handle = instance.handles.get(inner_port)
            if inner_handle is None:
                raise self.error(
                    EnergySystemErrorId.PORT_CONTRACT,
                    location,
                    f"the port '{name}' re-exports '{port.reexports}', but the inner import '{inner_key}' "
                    f"has no port '{inner_port}' {handle.source_text()}.",
                    alternatives=tuple(instance.handles),
                    alternatives_label="ports",
                )
            inner_handle.decided = True
            inner_handle.record = {"state": "re-exported", "partner": f"as {import_path}.{name}"}
            handle.landings.extend(inner_handle.landings)
            handle.provider = handle.provider or inner_handle.provider
            handle.end = handle.end or inner_handle.end
            handle.provision = handle.provision or inner_handle.provision
            handle.carrier = handle.carrier or inner_handle.carrier
            handle.consumer_outputs.extend(inner_handle.consumer_outputs)
            handle.fact = handle.fact or inner_handle.fact
            handle.fact_into.extend(inner_handle.fact_into)
            handle.port = handle.port.model_copy(
                update={
                    "kind": inner_handle.port.kind,
                    "partner": inner_handle.port.partner,
                    "wires": inner_handle.port.wires,
                    "output": inner_handle.port.output,
                    "circuit": inner_handle.port.circuit,
                    "members": inner_handle.port.members,
                    "carrier": inner_handle.port.carrier,
                    "outputs": inner_handle.port.outputs,
                    "meter": inner_handle.port.meter,
                    "fact": inner_handle.port.fact,
                    "many": inner_handle.port.many,
                }
            )
            if handle.state != "inactive":
                handle.state = (
                    "inactive"
                    if inner_handle.state == "inactive"
                    else ("optional" if port.optional else inner_handle.state)
                )
        if handle.end is not None:
            # The end is the inner one, seen from outside under the outer port's name.
            handle.end.owner, handle.end.port = import_path, name

    def _circuit_handle(self, handle: Handle, members: List[Unit], location: str) -> None:
        """Gives a circuit port its end: its members and the placeholders their partner items land at."""
        port = handle.port
        name = port.name
        if handle.state != "inactive" and not members:
            raise self.error(
                EnergySystemErrorId.PORT_CONTRACT,
                location,
                f"the circuit port '{name}' is active, but none of its members {', '.join(port.members)} exists "
                f"with these parameters {handle.source_text()}.",
            )
        for unit in members:
            positions = unit.placeholder_positions(name)
            if len(positions) > 1:
                raise self.error(
                    EnergySystemErrorId.PORT_CONTRACT,
                    location,
                    f"'{unit.name}' carries {len(positions)} placeholders for the circuit port '{name}'; a circuit "
                    f"lands its partner items once {handle.source_text()}.",
                )
            for position, placeholder in positions:
                handle.landings.append(Landing(unit=unit, position=position, placeholder=placeholder))
        handle.end = CircuitEndState(
            circuit=port.circuit or "", owner=handle.owner_path, port=name, units=list(members)
        )

    def _carrier_handle(
        self,
        handle: Handle,
        assembly: Optional[ResolvedAssembly],
        units: Mapping[str, Unit],
        carrier: Any,
        location: str,
    ) -> None:
        """Gives a carrier port its carrier, and a need its consuming outputs or a provision its meter (§5.1)."""
        port = handle.port
        name = port.name
        if handle.state == "inactive":
            return
        if not Carriers.is_carrier(carrier):
            raise self.error(
                EnergySystemErrorId.CARRIER_MISMATCH,
                location,
                f"the carrier port '{name}' resolves to the carrier {carrier!r}, which is no energy carrier "
                f"{handle.source_text()}.",
                alternatives=Carriers.names(),
                alternatives_label="carriers (lt.EnergyBalanceCarrier values)",
                offending_value=str(carrier),
            )
        handle.carrier = carrier
        if port.is_provision:
            electricity = carrier == Carriers.ELECTRICITY
            meter = units.get(port.meter) if port.meter is not None else None
            if electricity and port.meter is not None:
                raise self.error(
                    EnergySystemErrorId.CARRIER_PROVIDER,
                    location,
                    f"the electricity provision '{name}' names the meter '{port.meter}'; electricity has no link, "
                    f"so nothing lands in a meter through this port {handle.source_text()}.",
                    remedy="Drop 'meter:'; the meter's selection is written as observes: (hisim-lt0b.3).",
                )
            landing: Optional[Landing] = None
            if not electricity:
                if meter is None:
                    raise self.error(
                        EnergySystemErrorId.CARRIER_PROVIDER,
                        location,
                        f"the provision of '{carrier}' ('{name}') names no meter that exists with these parameters; a "
                        f"fuel's consumers are fed into its provider's meter {handle.source_text()}.",
                    )
                positions = meter.placeholder_positions(name)
                if len(positions) != 1:
                    raise self.error(
                        EnergySystemErrorId.PORT_CONTRACT,
                        location,
                        f"the meter '{meter.name}' of the provision '{name}' carries {len(positions)} "
                        f"'{{$port: {name}}}' placeholders; its consumers' feeds land at exactly one "
                        f"{handle.source_text()}.",
                    )
                landing = Landing(unit=meter, position=positions[0][0], placeholder=positions[0][1])
            record = CarrierRecord(
                carrier=carrier, provider=handle.owner_path, port=name, meter=meter.name if meter else None
            )
            handle.provision = Provision(
                carrier=carrier, owner=handle.owner_path, port=name, meter=meter, landing=landing, record=record
            )
            self._provisions.append((handle.provision, handle))
            return
        for item in port.outputs:
            if "." in item:
                member, output = item.split(".", 1)
            elif assembly is None:
                member, output = next(iter(units)), item
            else:
                provided = assembly.model.ports.get(item)
                if provided is None or provided.kind != PortKind.PROVIDED:
                    raise self.error(
                        EnergySystemErrorId.PORT_CONTRACT,
                        location,
                        f"the carrier need '{name}' names '{item}', which is no provided output of "
                        f"'{assembly.path}' {handle.source_text()}.",
                    )
                member, output = provided.output_member or "", provided.output_name or ""
            unit = units.get(member)
            if unit is None:
                raise self.error(
                    EnergySystemErrorId.PORT_CONTRACT,
                    location,
                    f"the carrier need '{name}' is active, but its consuming output '{item}' names the member "
                    f"'{member}', which does not exist with these parameters {handle.source_text()}.",
                )
            handle.consumer_outputs.append((unit, output))

    def _fact_handle(self, handle: Handle, units: Mapping[str, Unit], fact: Any, location: str) -> None:
        """Gives a fact port its fact, and a need the members it lowers into or a provision its provider (§6)."""
        port = handle.port
        name = port.name
        if handle.state == "inactive" or port.many or (port.is_provision and not port.members):
            # A many-read and a fact export are step 3's; the expansion refuses them (EF-7L) at the end.
            handle.fact = fact if isinstance(fact, str) else None
            return
        if not isinstance(fact, str) or not fact:
            raise self.error(
                EnergySystemErrorId.PORT_CONTRACT,
                location,
                f"the fact port '{name}' resolves to {fact!r}, which is no fact name {handle.source_text()}.",
            )
        handle.fact = fact
        if port.is_provision:
            unit = units.get(port.members[0])
            if unit is None:
                raise self.error(
                    EnergySystemErrorId.PORT_CONTRACT,
                    location,
                    f"the provided fact '{name}' names the member '{port.members[0]}', which does not exist with "
                    f"these parameters {handle.source_text()}.",
                )
            self._require_contribution(handle, unit, fact, location)
            handle.provider = (unit.name, fact)
            return
        handle.fact_into = [units[member] for member in port.into if member in units]
        if not handle.fact_into:
            raise self.error(
                EnergySystemErrorId.PORT_CONTRACT,
                location,
                f"the fact port '{name}' is active, but none of its members {', '.join(port.into)} exists with "
                f"these parameters {handle.source_text()}.",
            )

    def _require_contribution(self, handle: Handle, unit: Unit, fact: str, location: str) -> None:
        """Refuses a provided fact that the member's class does not declare in ``SIZING_CONTRIBUTIONS``."""
        config_class = self.classes.config_class(unit.class_path, f"components.{unit.name}", unit.name)
        declared = declared_facts_of(config_class)
        if fact not in declared:
            raise self.error(
                EnergySystemErrorId.PORT_CONTRACT,
                location,
                f"the provided fact '{handle.port.name}' names '{fact}', which {config_class.__name__} (member "
                f"{unit.name}) does not declare in its SIZING_CONTRIBUTIONS {handle.source_text()}.",
                alternatives=declared,
                alternatives_label=f"facts {config_class.__name__} contributes",
                offending_value=fact,
            )

    def _site_handles(self, unit: Unit) -> Dict[str, Handle]:
        """The ports of one site entry: needs and circuit ends at the entry itself, carriers it needs or provides."""
        if unit.name in self._site_handles_cache:
            return self._site_handles_cache[unit.name]
        handles: Dict[str, Handle] = {}
        for name, port in unit.entry.ports.items():
            state = "optional" if port.optional or port.is_provision else "required"
            handle = Handle(
                owner=f"component {unit.name}",
                owner_path=unit.name,
                verb_site=f"the component '{unit.name}'",
                port=port,
                state=state,
                landings=[],
                provider=None,
                own_units=frozenset({unit.name}),
                chain=(self.lines.location("components", unit.name, "ports", name),),
            )
            location = f"components.{unit.name}.ports.{name}"
            if not port.kind.is_lowered:
                self.not_lowered(f"component {unit.name}: port {name} ({port.kind.value})", port.kind.delivering_step)
            if port.kind == PortKind.CIRCUIT:
                self._circuit_handle(handle, [unit], location)
            elif port.kind == PortKind.CARRIER:
                self._carrier_handle(handle, None, {unit.name: unit}, port.carrier, location)
            else:
                for position, placeholder in unit.placeholder_positions(name):
                    handle.landings.append(Landing(unit=unit, position=position, placeholder=placeholder))
            if port.kind == PortKind.NEED and not handle.landings:
                raise self.error(
                    EnergySystemErrorId.PORT_CONTRACT,
                    location,
                    f"the port '{name}' of '{unit.name}' has no '{{$port: {name}}}' placeholder in the entry's "
                    f"inputs, so there is nowhere for its items to land {handle.source_text()}.",
                )
            handles[name] = handle
        for placed in unit.entry.placeholders:
            if isinstance(placed.placeholder, PortPlaceholder):
                declared = unit.entry.ports.get(placed.placeholder.port)
                if declared is None or (declared.kind == PortKind.CARRIER and not declared.is_provision):
                    raise self.error(
                        EnergySystemErrorId.PORT_CONTRACT,
                        f"components.{unit.name}.inputs[{placed.position}]",
                        f"'{unit.name}' carries a placeholder for the port '{placed.placeholder.port}', which its "
                        "'ports' block does not declare"
                        + (" as one that lands in its inputs" if declared is not None else "")
                        + ".",
                        alternatives=tuple(unit.entry.ports),
                        alternatives_label="ports",
                    )
        self._site_handles_cache[unit.name] = handles
        return handles

    def _check_verbs_name_ports(self, verbs: BindingVerbs, handles: Mapping[str, Handle], location: str) -> None:
        """Refuses a verb naming a port its owner does not have (``EF-7M``)."""
        for port in verbs.ports():
            if port not in handles:
                raise self.error(
                    EnergySystemErrorId.UNKNOWN_PORT,
                    location,
                    f"a verb names the port '{port}', which {location} does not have.",
                    alternatives=tuple(handles),
                    alternatives_label="ports",
                    offending_value=port,
                )

    def _level(
        self,
        *,
        units: List[Unit],
        members: Mapping[str, Unit],
        imports: Mapping[str, List[Tuple[Optional[str], Instance]]],
        import_entries: Mapping[str, ImportEntry],
        where_verbs: str,
    ) -> Level:
        """Builds one binding level: who is in scope and how a verb names each."""
        references: Dict[str, str] = {}
        targets: Dict[str, Tuple[List[Unit], Dict[str, Handle]]] = {}
        handles: List[Tuple[str, Handle]] = []
        for local, unit in members.items():
            references[unit.name] = local
            own = self._site_handles_cache.get(unit.name, {}) if unit.identity is None else {}
            targets[local] = ([unit], own)
            handles.extend((local, handle) for handle in own.values())
        for key, results in imports.items():
            entry = import_entries[key]
            for instance_key, instance in results:
                reference = key if instance_key is None else f"{key}.{instance_key}"
                for unit in instance.units:
                    references[unit.name] = reference
                targets[reference] = (instance.units, instance.handles)
                handles.extend((reference, handle) for handle in instance.handles.values())
            if entry.instances is not None and len(results) == 1:
                targets.setdefault(key, targets[f"{key}.{results[0][0]}"])
        return Level(
            units=units,
            references=references,
            targets=targets,
            where_verbs=where_verbs,
            members=frozenset(members),
            handles=handles,
        )

    def _candidates(self, handle: Handle, units: Sequence[Unit]) -> List[Unit]:
        """The units in scope whose class is one of the port's partner classes."""
        return [
            unit
            for unit in units
            if unit.name not in handle.own_units and short_class_name(unit.class_path) in handle.port.partner
        ]

    def _resolve_target(
        self, target: str, handle: Handle, level: Level
    ) -> Tuple[Optional[Unit], Optional[Tuple[str, str]], str]:
        """Resolves a verb's partner reference at one level.

        Returns:
            The partner unit (or ``None``), the provider ``(component, output)`` when the reference
            names a provided port, and a sentence explaining an absent partner.
        """
        parts = target.split(".")
        for length in range(len(parts), 0, -1):
            head = ".".join(parts[:length])
            if head in level.targets:
                break
        else:
            return None, None, f"'{target}' names no component and no import of {level.where_verbs}"
        units, handles = level.targets[head]
        rest = parts[length:]
        if rest:
            port_name = rest[0]
            partner_handle = handles.get(port_name)
            if partner_handle is None or len(rest) > 1:
                return None, None, f"'{head}' has no port '{'.'.join(rest)}'"
            if partner_handle.port.kind != PortKind.PROVIDED or partner_handle.provider is None:
                return None, None, f"the port '{target}' provides no output a need can bind to"
            provider_unit = next(unit for unit in units if unit.name == partner_handle.provider[0])
            return provider_unit, partner_handle.provider, ""
        matching = [
            unit
            for unit in units
            if short_class_name(unit.class_path) in handle.port.partner and unit.name not in handle.own_units
        ]
        if len(matching) == 1:
            return matching[0], None, ""
        if not matching:
            if len(units) == 1:
                # The partner exists but is of another class: _lower refuses it with EF-7H.
                return units[0], None, ""
            raise self.error(
                EnergySystemErrorId.PARTNER_WITHOUT_DEFAULT_CONNECTIONS,
                handle.owner,
                f"port '{handle.port.name}' ({handle.partner_label}) is bound to '{target}', which holds no "
                f"component of that class (it holds {', '.join(unit.name for unit in units)}) {handle.source_text()}.",
                remedy=handle.hint or None,
            )
        raise self.error(
            EnergySystemErrorId.PORT_AMBIGUOUS,
            handle.owner,
            f"port '{handle.port.name}' ({handle.partner_label}) is bound to '{target}', which holds "
            f"{len(matching)} components of that class: {', '.join(unit.name for unit in matching)} "
            f"{handle.source_text()}.",
            remedy="Name the partner's provided port instead: bind: {" + handle.port.name + ": " + head + ".<port>}.",
        )

    def _paste_lines(self, handle: Handle, candidates: Sequence[Unit], level: Level, optional: bool) -> str:
        """The paste-ready verb lines for a refusal."""
        references = list(dict.fromkeys(level.references.get(unit.name, unit.name) for unit in candidates))
        return self._paste_references(handle, references, optional)

    @staticmethod
    def _paste_references(handle: Handle, references: Sequence[str], optional: bool) -> str:
        """The paste-ready verb lines for a refusal, from the references a verb would write."""
        references = list(dict.fromkeys(references))
        port = handle.port.name
        if not references:
            references = ["<partner>"]
        lines = [f"`bind: {{{port}: {reference}}}`" for reference in references]
        if optional:
            lines = (
                [f"`optional-bind: {{{port}: {reference}}}`" for reference in references]
                + lines
                + [f"`none: [{port}]`"]
            )
        return f"add to {handle.verb_site} one of " + ", ".join(lines)

    def _decide(  # pylint: disable=too-many-return-statements  # one return per decided state of §3.1
        self, handle: Handle, verbs: BindingVerbs, level: Level, *, top: bool
    ) -> None:
        """Decides one port by its verb or by the default rule, and lowers it (§3.1, §3.3)."""
        port = handle.port
        written = verbs.verb_for(port.name)
        if handle.decided:
            if port.kind == PortKind.CIRCUIT and written is not None:
                self._confirm_circuit_verb(handle, written, level)
            return
        if handle.state == "inactive":
            if written is not None:
                raise self.error(
                    EnergySystemErrorId.VERB_ON_INACTIVE_PORT,
                    handle.owner,
                    f"port '{port.name}' is inactive with these parameters (active_when/required_when), yet "
                    f"{handle.verb_site} writes '{written[0]}' for it {handle.source_text()}.",
                    remedy=(
                        f"Remove the '{written[0]}' line for '{port.name}', or change the parameters that make it "
                        "inactive."
                    ),
                )
            handle.record = {"state": "inactive"}
            handle.decided = True
            return
        if port.is_provision:
            if written is not None:
                raise self.error(
                    EnergySystemErrorId.PORT_CONTRACT,
                    handle.owner,
                    f"port '{port.name}' is a provided {self._provision_text(port)}; a verb binds a need, and the "
                    f"need's owner binds to this port ({handle.verb_site} writes '{written[0]}') "
                    f"{handle.source_text()}.",
                )
            handle.record = {"state": "provided"}
            handle.decided = True
            return
        if not port.kind.is_lowered or (port.kind == PortKind.FACT and port.many):
            handle.record = {"state": "not lowered"}
            handle.decided = True
            return
        if port.kind == PortKind.CIRCUIT:
            self._decide_cross(handle, written, level, top, self._circuit_rule(handle, level))
            return
        if port.kind == PortKind.CARRIER:
            if handle.carrier == Carriers.ELECTRICITY:
                self._decide_electricity(handle, written, level, top)
            else:
                self._decide_cross(handle, written, level, top, self._fuel_rule(handle, level))
            return
        if port.kind == PortKind.FACT:
            self._decide_cross(handle, written, level, top, self._fact_rule(handle, level))
            return
        candidates = self._candidates(handle, level.units)
        handle.hint = (
            f"Candidates: {', '.join(unit.name for unit in candidates) or 'none'}; "
            + self._paste_lines(handle, candidates, level, optional=handle.state == "optional")
            + "."
        )
        if written is not None:
            verb, target = written
            if verb == "none":
                if handle.state == "required":
                    raise self.error(
                        EnergySystemErrorId.REQUIRED_PORT_DECLINED,
                        handle.owner,
                        f"port '{port.name}' ({handle.partner_label}) is required, yet {handle.verb_site} "
                        "declines it with 'none:'; candidates: "
                        f"{', '.join(unit.name for unit in candidates) or 'none'} "
                        f"{handle.source_text()}.",
                        remedy=self._paste_lines(handle, candidates, level, optional=False)
                        + "; a required port cannot be declined.",
                    )
                handle.record = {"state": "declined", "verb": "none"}
                handle.decided = True
                return
            assert target is not None
            partner, provider, absent = self._resolve_target(target, handle, level)
            if partner is None:
                if verb == "optional-bind":
                    handle.record = {
                        "state": handle.state,
                        "verb": "optional-bind",
                        "partner": f"not bound: partner absent ({absent})",
                    }
                    handle.decided = True
                    return
                raise self.error(
                    EnergySystemErrorId.BOUND_PARTNER_ABSENT,
                    handle.owner,
                    f"port '{port.name}' ({handle.partner_label}) is bound to '{target}', but {absent}; "
                    f"candidates: {', '.join(unit.name for unit in candidates) or 'none'} {handle.source_text()}.",
                    remedy=self._paste_lines(handle, candidates, level, optional=handle.state == "optional")
                    + (" (optional-bind: binds only when the partner exists)" if handle.state == "optional" else ""),
                )
            self._lower(handle, partner, provider, verb)
            return
        if len(candidates) == 1 and handle.state == "required":
            self._lower(handle, candidates[0], None, "default")
            return
        if len(candidates) > 1:
            raise self.error(
                EnergySystemErrorId.PORT_AMBIGUOUS,
                handle.owner,
                f"port '{port.name}' ({handle.partner_label}) has {len(candidates)} candidates "
                f"{', '.join(unit.name for unit in candidates)} and no verb {handle.source_text()}.",
                remedy=self._paste_lines(handle, candidates, level, optional=handle.state == "optional"),
            )
        if handle.state == "optional" and candidates:
            raise self.error(
                EnergySystemErrorId.OPTIONAL_PORT_UNDECIDED,
                handle.owner,
                f"optional port '{port.name}' ({handle.partner_label}) has 1 candidate {candidates[0].name} "
                f"and no verb {handle.source_text()}.",
                remedy=self._paste_lines(handle, candidates, level, optional=True),
            )
        if not top:
            raise self.error(
                EnergySystemErrorId.INNER_PORT_UNRESOLVED,
                handle.owner,
                f"{handle.state} port '{port.name}' ({handle.partner_label}) of the inner import is neither "
                "bound "
                f"nor re-exported inside {level.where_verbs}; candidates: none {handle.source_text()}.",
                remedy=(
                    f"Re-export it from the importing assembly's interface (`{port.name}: {{from: "
                    f"{handle.owner_path.split(' → ')[-1].split('[')[0]}.{port.name}}}`), bind it with "
                    + self._paste_lines(handle, candidates, level, optional=handle.state == "optional").replace(
                        "add to ", "a line on "
                    )
                    + ", or decline it."
                ),
            )
        if handle.state == "optional":
            handle.record = {"state": "optional", "partner": "not bound: no candidate"}
            handle.decided = True
            return
        raise self.error(
            EnergySystemErrorId.PORT_WITHOUT_PARTNER,
            handle.owner,
            f"required port '{port.name}' ({handle.partner_label}) has no partner in "
            f"{level.where_verbs}; candidates: none {handle.source_text()}.",
            remedy=self._paste_lines(handle, candidates, level, optional=False)
            + f" naming a component of class {handle.partner_text}, after adding one.",
        )

    def _lower(self, handle: Handle, partner: Unit, provider: Optional[Tuple[str, str]], verb: str) -> None:
        """Lowers a decided need into its landings: a bare name, or the explicit wires it names."""
        port = handle.port
        partner_class = short_class_name(partner.class_path)
        if partner_class not in port.partner and provider is None:
            raise self.error(
                EnergySystemErrorId.PARTNER_WITHOUT_DEFAULT_CONNECTIONS,
                handle.owner,
                f"port '{port.name}' is bound to {partner.name} ({partner_class}), which is not of its partner "
                f"class {handle.partner_text} {handle.source_text()}.",
                remedy=handle.hint or None,
            )
        lowered: List[str] = []
        for landing in handle.landings:
            wires = landing.placeholder.wires if landing.placeholder.wires is not None else port.wires
            if wires is not None:
                items: List[AnyInputItem] = [
                    ExplicitWire(source=partner.name, input=target, output=output) for target, output in wires.items()
                ]
                for target, output in wires.items():
                    self._note_lowered(handle, LoweredKind.WIRE, landing.unit, verb, partner, target, output)
            else:
                self._note_lowered(handle, LoweredKind.DEFAULT, landing.unit, verb, partner)
                items = [DefaultInputs(source=partner.name)]
            if landing.position in landing.unit.lowered:
                raise self.error(
                    EnergySystemErrorId.PORT_CONTRACT,
                    handle.owner,
                    f"the placeholder of '{landing.unit.name}' at inputs[{landing.position}] is filled twice "
                    f"{handle.source_text()}.",
                )
            landing.unit.lowered[landing.position] = items
            landing.unit.notes[landing.position] = f"port {port.name} bound to {partner.name} ({verb})"
            lowered.extend(f"{landing.unit.name}.inputs: {self._item_text(item)}" for item in items)
        handle.record = {"state": handle.state, "partner": partner.name, "verb": verb, "lowered_to": tuple(lowered)}
        handle.decided = True
        self.record.decisions.append(f"{handle.owner_path}.{port.name} -> {partner.name} ({verb})")

    # ------------------------------------------------------------------ circuits, carriers and facts

    @staticmethod
    def _provision_text(port: Port) -> str:
        """What a provision provides, for a message."""
        if port.kind == PortKind.CARRIER:
            return "carrier"
        if port.kind == PortKind.FACT:
            return "fact"
        return "output"

    @staticmethod
    def _split_target(target: str, level: Level) -> Tuple[Optional[str], List[str]]:
        """Splits a verb's partner reference into the component or instance it names and the rest."""
        parts = target.split(".")
        for length in range(len(parts), 0, -1):
            head = ".".join(parts[:length])
            if head in level.targets:
                return head, parts[length:]
        return None, parts

    def _decide_cross(
        self, handle: Handle, written: Optional[Tuple[str, Optional[str]]], level: Level, top: bool, rule: "CrossRule"
    ) -> None:
        """Decides a circuit end, a fuel need or a fact need by its verb or the default rule (§3.1, §3.3)."""
        port = handle.port
        optional = handle.state == "optional"
        listed = ", ".join(candidate.label for candidate in rule.candidates) or "none"
        references = [candidate.reference for candidate in rule.candidates] or ["<partner>"]
        handle.hint = f"Candidates: {listed}; " + self._paste_references(handle, references, optional) + "."
        if written is not None:
            verb, target = written
            if verb == "none":
                if handle.state == "required":
                    raise self.error(
                        EnergySystemErrorId.REQUIRED_PORT_DECLINED,
                        handle.owner,
                        f"port '{port.name}' ({handle.partner_label}) is required, yet {handle.verb_site} declines it "
                        f"with 'none:'; candidates: {listed} {handle.source_text()}.",
                        remedy=self._paste_references(handle, references, False) + "; a required port cannot be "
                        "declined.",
                    )
                handle.record = {"state": "declined", "verb": "none"}
                if handle.end is not None:
                    handle.end.declined = True
                handle.decided = True
                return
            assert target is not None
            payload, absent = rule.resolve(target)
            if payload is None:
                if verb == "optional-bind":
                    handle.record = {
                        "state": handle.state,
                        "verb": "optional-bind",
                        "partner": f"not bound: partner absent ({absent})",
                    }
                    handle.decided = True
                    return
                raise self.error(
                    EnergySystemErrorId.BOUND_PARTNER_ABSENT,
                    handle.owner,
                    f"port '{port.name}' ({handle.partner_label}) is bound to '{target}', but {absent}; candidates: "
                    f"{listed} {handle.source_text()}.",
                    remedy=self._paste_references(handle, references, optional),
                )
            rule.lower(payload, verb)
            return
        if len(rule.candidates) == 1 and handle.state == "required":
            rule.lower(rule.candidates[0].payload, "default")
            return
        if len(rule.candidates) > 1:
            raise self.error(
                rule.ambiguous,
                handle.owner,
                f"port '{port.name}' ({handle.partner_label}) has {len(rule.candidates)} candidates {listed} and no "
                f"verb {handle.source_text()}.",
                remedy=self._paste_references(handle, references, optional),
            )
        if optional and rule.candidates:
            raise self.error(
                EnergySystemErrorId.OPTIONAL_PORT_UNDECIDED,
                handle.owner,
                f"optional port '{port.name}' ({handle.partner_label}) has 1 candidate {listed} and no verb "
                f"{handle.source_text()}.",
                remedy=self._paste_references(handle, references, True),
            )
        if not top:
            self._refuse_unresolved_inner(handle, level, references)
        if optional:
            handle.record = {"state": "optional", "partner": "not bound: no candidate"}
            handle.decided = True
            return
        error_id, problem, remedy = rule.missing()
        raise self.error(error_id, handle.owner, f"{problem} {handle.source_text()}.", remedy=remedy)

    def _refuse_unresolved_inner(self, handle: Handle, level: Level, references: Sequence[str]) -> None:
        """Refuses an inner import's port that nothing inside its importer binds (``EF-7G``, §2.5)."""
        port = handle.port
        raise self.error(
            EnergySystemErrorId.INNER_PORT_UNRESOLVED,
            handle.owner,
            f"{handle.state} port '{port.name}' ({handle.partner_label}) of the inner import is neither bound nor "
            f"re-exported inside {level.where_verbs}; candidates: none {handle.source_text()}.",
            remedy=(
                f"Re-export it from the importing assembly's interface (`{port.name}: {{from: "
                f"{handle.owner_path.split(' → ')[-1].split('[')[0]}.{port.name}}}`), bind it with "
                + self._paste_references(handle, references, handle.state == "optional").replace(
                    "add to ", "a line on "
                )
                + ", or decline it."
            ),
        )

    # -------------------------------------------------------------------------------------- circuits

    def _circuit_rule(self, handle: Handle, level: Level) -> "CrossRule":
        """The default rule, the verb resolution and the lowering of one circuit end (§3.2, §11.1)."""
        end = handle.end
        assert end is not None
        candidates = [
            Candidate(reference=f"{reference}.{other.port.name}", label=other.end.label, payload=other)
            for reference, other in level.handles
            if other is not handle
            and other.port.kind == PortKind.CIRCUIT
            and other.end is not None
            and other.end is not end
            and not other.decided
            and other.state != "inactive"
            and other.end.circuit == end.circuit
            and other.owner_path != handle.owner_path
            and other.end.bound_to is None
            and not other.end.declined
        ]
        outputs = ", ".join(CircuitNaming.outputs(end.circuit))

        def missing() -> Tuple[EnergySystemErrorId, str, str]:
            return (
                EnergySystemErrorId.PORT_WITHOUT_PARTNER,
                f"required port '{handle.port.name}' (circuit {end.circuit}) has no other end of the circuit "
                f"{end.circuit} in {level.where_verbs}; candidates: none",
                f"Add an import or a site entry with a '{end.circuit}' circuit port (its members own or read "
                f"{outputs}), or decline the port if it is optional.",
            )

        return CrossRule(
            candidates=candidates,
            resolve=lambda target: self._resolve_circuit(handle, target, level),
            lower=lambda other, verb: self._lower_circuit(handle, other, verb),
            ambiguous=EnergySystemErrorId.PORT_AMBIGUOUS,
            missing=missing,
        )

    def _require_circuit_end(self, handle: Handle, other: Handle, target: str) -> None:
        """Refuses a bound partner that is no free end of the same circuit (``EF-7N``)."""
        end = handle.end
        assert end is not None
        if other.port.kind != PortKind.CIRCUIT or other.end is None:
            raise self.error(
                EnergySystemErrorId.CIRCUIT_ENDS_DO_NOT_FIT,
                handle.owner,
                f"circuit port '{handle.port.name}' (circuit {end.circuit}) is bound to '{target}', which is no "
                f"circuit end but a {other.port.kind.value} port {handle.source_text()}.",
                remedy=handle.hint or None,
            )
        if other.end.circuit != end.circuit:
            raise self.error(
                EnergySystemErrorId.CIRCUIT_ENDS_DO_NOT_FIT,
                handle.owner,
                f"circuit port '{handle.port.name}' (circuit {end.circuit}) is bound to '{target}', an end of the "
                f"circuit {other.end.circuit}; a circuit binds only an end of its own medium, and "
                f"{', '.join(CircuitNaming.outputs(other.end.circuit))} are not "
                f"{', '.join(CircuitNaming.outputs(end.circuit))} {handle.source_text()}.",
                remedy=handle.hint or None,
            )
        if other.owner_path == handle.owner_path or other.end is end:
            raise self.error(
                EnergySystemErrorId.CIRCUIT_ENDS_DO_NOT_FIT,
                handle.owner,
                f"circuit port '{handle.port.name}' is bound to '{target}', an end of its own instance; a circuit "
                f"joins two imports or an import and a site entry {handle.source_text()}.",
            )
        if other.end.bound_to is not None and other.end.bound_to is not end:
            raise self.error(
                EnergySystemErrorId.CIRCUIT_ENDS_DO_NOT_FIT,
                handle.owner,
                f"circuit port '{handle.port.name}' (circuit {end.circuit}) is bound to '{target}', whose circuit "
                f"already joins {other.end.bound_to.label}; a circuit has exactly two ends, and a split is a valve "
                f"assembly with one circuit per branch {handle.source_text()}.",
            )

    def _resolve_circuit(self, handle: Handle, target: str, level: Level) -> Tuple[Optional[Handle], str]:
        """Resolves a verb's partner reference to the other end of a circuit."""
        end = handle.end
        assert end is not None
        head, rest = self._split_target(target, level)
        if head is None:
            return None, f"'{target}' names no component and no import of {level.where_verbs}"
        _units, handles = level.targets[head]
        if rest:
            other = handles.get(rest[0]) if len(rest) == 1 else None
            if other is None:
                return None, f"'{head}' has no port '{'.'.join(rest)}'"
            self._require_circuit_end(handle, other, target)
            if other.state == "inactive":
                return None, f"the port '{target}' is inactive with its parameters"
            return other, ""
        ends = [
            other for other in handles.values() if other.port.kind == PortKind.CIRCUIT and other.state != "inactive"
        ]
        same = [other for other in ends if other.end is not None and other.end.circuit == end.circuit]
        if len(same) == 1:
            self._require_circuit_end(handle, same[0], target)
            return same[0], ""
        if not same:
            if ends:
                raise self.error(
                    EnergySystemErrorId.CIRCUIT_ENDS_DO_NOT_FIT,
                    handle.owner,
                    f"circuit port '{handle.port.name}' (circuit {end.circuit}) is bound to '{target}', whose "
                    "circuit ends are of other circuits: "
                    + ", ".join(f"{other.port.name} ({other.end.circuit if other.end else '?'})" for other in ends)
                    + f"; a circuit binds only an end of its own medium {handle.source_text()}.",
                    remedy=handle.hint or None,
                )
            return None, f"'{head}' has no end of the circuit {end.circuit}"
        raise self.error(
            EnergySystemErrorId.PORT_AMBIGUOUS,
            handle.owner,
            f"circuit port '{handle.port.name}' (circuit {end.circuit}) is bound to '{target}', which has "
            f"{len(same)} ends of that circuit: {', '.join(other.port.name for other in same)} "
            f"{handle.source_text()}.",
            remedy=self._paste_references(handle, [f"{head}.{other.port.name}" for other in same], False),
        )

    def _confirm_circuit_verb(self, handle: Handle, written: Tuple[str, Optional[str]], level: Level) -> None:
        """Checks the verb of a circuit end the other end's verb already bound: both must name each other."""
        end = handle.end
        if end is None or end.bound_to is None:
            return
        verb, target = written
        if verb != "none" and target is not None:
            other, _absent = self._resolve_circuit(handle, target, level)
            if other is not None and other.end is end.bound_to:
                return
        raise self.error(
            EnergySystemErrorId.CIRCUIT_ENDS_DO_NOT_FIT,
            handle.owner,
            f"circuit port '{handle.port.name}' (circuit {end.circuit}) is bound to {end.bound_to.label} by that "
            f"end's verb, yet {handle.verb_site} writes '{verb}{': ' + target if target else ''}' for it "
            f"{handle.source_text()}.",
            remedy="Write the binding on one end, or make both name each other.",
        )

    def _lower_circuit(self, handle: Handle, other: Handle, verb: str) -> None:
        """Lowers a bound circuit: every placeholder at one end takes a bare name of each member of the other end.

        The file decides the binding — the two ends of one circuit, their members and which members
        carry the circuit's placeholder. Which member owns and which reads each of the circuit's three
        outputs the constructed members say: the wiring expands every bare name written here through the
        reader's default connections from the owner's class and checks the result like any connection —
        a reader without defaults from that class, an output owned twice (an input fed twice), a load
        type or unit that differs, a reader left without its circuit inputs (§3.2, §11.1, hydronic
        spec §3.1). Every item is written into the port-provenance table, so a refusal names the circuit
        port it came from.
        """
        end, other_end = handle.end, other.end
        assert end is not None and other_end is not None
        circuit = end.circuit
        if not handle.landings and not other.landings:
            raise self.error(
                EnergySystemErrorId.CIRCUIT_ENDS_DO_NOT_FIT,
                handle.owner,
                f"the circuit {circuit} between {end.label} and {other_end.label} has no '{{$port: …}}' placeholder at "
                f"either end, so no member reads any of {', '.join(CircuitNaming.outputs(circuit))} "
                f"{handle.source_text()}.",
                remedy="A member that reads the circuit's outputs carries the circuit port's placeholder.",
            )
        lowered: List[str] = []
        for this_end, this_handle, far_end in ((end, handle, other_end), (other_end, other, end)):
            for landing in this_handle.landings:
                unit = landing.unit
                if landing.position in unit.lowered:
                    raise self.error(
                        EnergySystemErrorId.PORT_CONTRACT,
                        this_handle.owner,
                        f"the placeholder of '{unit.name}' at inputs[{landing.position}] is filled twice "
                        f"{this_handle.source_text()}.",
                    )
                items: List[AnyInputItem] = [DefaultInputs(source=source.name) for source in far_end.units]
                unit.lowered[landing.position] = items
                unit.notes[landing.position] = (
                    f"circuit {circuit}: port {this_handle.port.name} bound to {far_end.label} ({verb})"
                )
                lowered.extend(f"{unit.name}.inputs: {self._item_text(item)}" for item in items)
                for source in far_end.units:
                    self._note_lowered(
                        this_handle,
                        LoweredKind.CIRCUIT,
                        unit,
                        verb,
                        source,
                        circuit=circuit,
                        end=this_end.label,
                        other_end=far_end.label,
                    )
        end.bound_to, other_end.bound_to = other_end, end
        for this_handle, far_end in ((handle, other_end), (other, end)):
            this_handle.record = {
                "state": this_handle.state,
                "partner": far_end.label,
                "verb": verb,
                "lowered_to": tuple(item for item in lowered),
            }
            this_handle.decided = True
        self.record.circuits.append(
            CircuitRecord(
                circuit=circuit,
                ends=(
                    CircuitEndRecord(end.owner, end.port, tuple(unit.name for unit in end.units)),
                    CircuitEndRecord(other_end.owner, other_end.port, tuple(unit.name for unit in other_end.units)),
                ),
                verb=verb,
                lowered_to=tuple(lowered),
            )
        )
        self.record.decisions.append(f"{end.label} <-> {other_end.label} (circuit {circuit}, {verb})")

    # -------------------------------------------------------------------------------------- carriers

    def _provisions_at(self, level: Level, handle: Handle) -> List[Tuple[str, Handle]]:
        """The active carrier provisions in scope of a need, one per provider, outside its own instance."""
        found: List[Tuple[str, Handle]] = []
        seen: Set[int] = set()
        for reference, other in level.handles:
            provision = other.provision
            if (
                provision is None
                or other.state == "inactive"
                or other.record.get("state") == "re-exported"
                or other.owner_path == handle.owner_path
                or id(provision) in seen
            ):
                continue
            seen.add(id(provision))
            found.append((reference, other))
        return found

    def _fuel_rule(self, handle: Handle, level: Level) -> "CrossRule":
        """The default rule, the verb resolution and the lowering of one fuel need (§3.2, §5.1)."""
        carrier = handle.carrier or ""
        providers = [
            (reference, other)
            for reference, other in self._provisions_at(level, handle)
            if other.provision is not None and other.provision.carrier == carrier
        ]
        per_reference: Dict[str, int] = {}
        for reference, _other in providers:
            per_reference[reference] = per_reference.get(reference, 0) + 1
        candidates = [
            Candidate(
                reference=reference if per_reference[reference] == 1 else f"{reference}.{other.port.name}",
                label=other.provision.label if other.provision is not None else other.port.name,
                payload=other,
            )
            for reference, other in providers
        ]

        def missing() -> Tuple[EnergySystemErrorId, str, str]:
            return (
                EnergySystemErrorId.CARRIER_PROVIDER,
                f"required carrier need '{handle.port.name}' has no provider of {carrier} in {level.where_verbs}; "
                "candidates: none",
                f"No provider of {carrier}: add the import of `{Carriers.supply_for(carrier)}` (or a site entry "
                f"providing {carrier}); the format never adds one (§5.2).",
            )

        return CrossRule(
            candidates=candidates,
            resolve=lambda target: self._resolve_provider(handle, target, level),
            lower=lambda other, verb: self._lower_carrier(handle, other, verb),
            ambiguous=EnergySystemErrorId.CARRIER_PROVIDER,
            missing=missing,
        )

    def _resolve_provider(self, handle: Handle, target: str, level: Level) -> Tuple[Optional[Handle], str]:
        """Resolves a verb's partner reference to a provider of the need's carrier."""
        carrier = handle.carrier
        head, rest = self._split_target(target, level)
        if head is None:
            return None, f"'{target}' names no component and no import of {level.where_verbs}"
        _units, handles = level.targets[head]
        if rest:
            other = handles.get(rest[0]) if len(rest) == 1 else None
            if other is None:
                return None, f"'{head}' has no port '{'.'.join(rest)}'"
            if other.port.kind != PortKind.CARRIER or not other.port.is_provision:
                raise self.error(
                    EnergySystemErrorId.CARRIER_PROVIDER,
                    handle.owner,
                    f"carrier need '{handle.port.name}' (carrier {carrier}) is bound to '{target}', which provides "
                    f"no carrier {handle.source_text()}.",
                    remedy=handle.hint or None,
                )
            if other.state == "inactive" or other.provision is None:
                return None, f"the port '{target}' is inactive with its parameters"
            self._require_carrier(handle, other, target)
            return other, ""
        provisions = [other for other in handles.values() if other.provision is not None and other.state != "inactive"]
        same = [other for other in provisions if other.provision is not None and other.provision.carrier == carrier]
        if len(same) == 1:
            return same[0], ""
        if not same:
            if provisions:
                self._require_carrier(handle, provisions[0], target)
            return None, f"'{head}' provides no carrier"
        raise self.error(
            EnergySystemErrorId.PORT_AMBIGUOUS,
            handle.owner,
            f"carrier need '{handle.port.name}' (carrier {carrier}) is bound to '{target}', which provides it "
            f"{len(same)} times: {', '.join(other.port.name for other in same)} {handle.source_text()}.",
            remedy=self._paste_references(handle, [f"{head}.{other.port.name}" for other in same], False),
        )

    def _require_carrier(self, handle: Handle, other: Handle, target: str) -> None:
        """Refuses a bound provider of another carrier than the need's (``EF-7Q``)."""
        provision = other.provision
        assert provision is not None
        if provision.carrier != handle.carrier:
            raise self.error(
                EnergySystemErrorId.CARRIER_MISMATCH,
                handle.owner,
                f"carrier need '{handle.port.name}' (carrier {handle.carrier}) is bound to '{target}', which "
                f"provides {provision.carrier} {handle.source_text()}.",
                remedy=handle.hint or None,
            )

    def _decide_electricity(
        self, handle: Handle, written: Optional[Tuple[str, Optional[str]]], level: Level, top: bool
    ) -> None:
        """Decides an electricity need: no verb, and exactly one electricity provider in scope (§3.2, §4.3)."""
        port = handle.port
        if written is not None:
            raise self.error(
                EnergySystemErrorId.CARRIER_PROVIDER,
                handle.owner,
                f"carrier need '{port.name}' (carrier electricity) carries the verb '{written[0]}', but electricity "
                f"has no link end: the need only checks that the system has exactly one electricity provider, and "
                f"no verb binds it (§3.3, §4.3) {handle.source_text()}.",
                remedy=f"Remove the '{written[0]}' line for '{port.name}' from {handle.verb_site}.",
            )
        providers = [
            (reference, other)
            for reference, other in self._provisions_at(level, handle)
            if other.provision is not None and other.provision.is_electricity
        ]
        if len(providers) == 1:
            self._lower_carrier(handle, providers[0][1], "default")
            return
        if len(providers) > 1:
            raise self.error(
                EnergySystemErrorId.CARRIER_PROVIDER,
                handle.owner,
                f"carrier need '{port.name}' (carrier electricity) finds {len(providers)} electricity providers: "
                f"{', '.join(other.provision.label for _reference, other in providers if other.provision)}; a "
                f"system has exactly one grid connection {handle.source_text()}.",
                remedy="Remove all but one electricity provider.",
            )
        if not top:
            self._refuse_unresolved_inner(handle, level, ["<partner>"])
        if handle.state == "optional":
            handle.record = {"state": "optional", "partner": "not bound: no electricity provider"}
            handle.decided = True
            return
        raise self.error(
            EnergySystemErrorId.CARRIER_PROVIDER,
            handle.owner,
            f"required carrier need '{port.name}' has no provider of electricity in {level.where_verbs} "
            f"{handle.source_text()}.",
            remedy=(
                f"Add the import of `{Carriers.supply_for(Carriers.ELECTRICITY)}`; the format never adds one (§5.2)."
            ),
        )

    def _lower_carrier(self, handle: Handle, provider: Handle, verb: str) -> None:
        """Lowers a bound carrier need: the provider's meter observes the consuming outputs (§3.2, §5.1).

        For a fuel the meter takes a bare name of every consuming member, which the wiring expands
        through the default feeds the meter declares from that member's class — the tags and the
        weight a recorded twin writes; for electricity nothing is written. Every bare name and every
        consuming output is written into the port-provenance table: whether an output exists, carries
        the need's carrier by its energy port, and is exactly what the meter's default feeds from its
        class observe, the constructed components say, and the wiring stage checks it with the bare
        names it expands.
        """
        provision = provider.provision
        assert provision is not None
        carrier = handle.carrier or ""
        meter = provision.meter
        outputs: List[str] = []
        consumers: Dict[str, Unit] = {}
        for unit, output in handle.consumer_outputs:
            outputs.append(f"{unit.name}.{output}")
            self._note_lowered(handle, LoweredKind.FEED, meter, verb, unit, output=output, carrier=carrier)
            if meter is not None:
                consumers.setdefault(unit.name, unit)
        items: List[AnyInputItem] = [DefaultInputs(source=name) for name in consumers]
        lowered: List[str] = []
        landing = provision.landing
        if items and landing is not None:
            filled = landing.unit.lowered.setdefault(landing.position, [])
            items = [item for item in items if item not in filled]
            filled.extend(items)
            for item in items:
                self._note_lowered(
                    handle, LoweredKind.DEFAULT, landing.unit, verb, consumers[item.source], carrier=carrier
                )
            note = (
                f"carrier {carrier}: port {handle.port.name} of {handle.owner_path} bound to {provision.label} ({verb})"
            )
            previous = landing.unit.notes.get(landing.position)
            landing.unit.notes[landing.position] = f"{previous}; {note}" if previous else note
            lowered = [f"{landing.unit.name}.inputs: {self._item_text(item)}" for item in items]
        provision.record.consumers.append(
            CarrierConsumer(
                owner=handle.owner_path,
                port=handle.port.name,
                outputs=tuple(outputs),
                verb=verb,
                lowered_to=tuple(lowered),
            )
        )
        handle.record = {
            "state": handle.state,
            "partner": provision.label + (f" (meter {meter.name})" if meter is not None else " (no link)"),
            "verb": verb,
            "lowered_to": tuple(lowered),
        }
        handle.decided = True
        self.record.decisions.append(f"{handle.owner_path}.{handle.port.name} -> {provision.label} ({carrier}, {verb})")

    def _check_provisions(self) -> None:
        """Refuses a fuel provider no need is bound to (§5.2), and records every provider's consumers."""
        seen: Set[int] = set()
        for provision, handle in self._provisions:
            if id(provision) in seen:
                continue
            seen.add(id(provision))
            self.record.carriers.append(provision.record)
            consumers = [f"{consumer.owner}.{consumer.port}" for consumer in provision.record.consumers]
            handle.record = {**handle.record, "partner": ", ".join(consumers)} if consumers else handle.record
            if not provision.is_electricity and not consumers:
                raise self.error(
                    EnergySystemErrorId.CARRIER_PROVIDER,
                    handle.owner,
                    f"the provider of {provision.carrier} {provision.label} has no bound consumer; an idle "
                    f"connection is refused, never kept (§5.2) {handle.source_text()}.",
                    remedy=f"Remove the provider, or bind a need of {provision.carrier} to it.",
                )

    # ----------------------------------------------------------------------------------------- facts

    def _fact_rule(self, handle: Handle, level: Level) -> "CrossRule":
        """The default rule, the verb resolution and the lowering of one fact need (§3.2, §6)."""
        fact = handle.fact or ""
        candidates: List[Candidate] = []
        for reference in sorted(level.members, key=list(level.targets).index):
            unit = level.targets[reference][0][0]
            if unit.name in handle.own_units:
                continue
            if fact in declared_facts_of(
                self.classes.config_class(unit.class_path, f"components.{unit.name}", unit.name)
            ):
                candidates.append(Candidate(reference=reference, label=unit.name, payload=unit.name))
        for reference, other in level.handles:
            provider = self._fact_provider(other, fact)
            if (
                provider is not None
                and other.owner_path != handle.owner_path
                and other.record.get("state") != "re-exported"
                and all(candidate.payload != provider for candidate in candidates)
            ):
                candidates.append(
                    Candidate(reference=f"{reference}.{other.port.name}", label=provider, payload=provider)
                )

        def missing() -> Tuple[EnergySystemErrorId, str, str]:
            return (
                EnergySystemErrorId.FACT_NOT_PROVIDED,
                f"required fact port '{handle.port.name}' finds no provider of the fact {fact} in "
                f"{level.where_verbs}; candidates: none",
                f"Add a component whose class contributes {fact} (SIZING_CONTRIBUTIONS), or an import providing it "
                "through a fact port.",
            )

        return CrossRule(
            candidates=candidates,
            resolve=lambda target: self._resolve_fact(handle, target, level),
            lower=lambda provider, verb: self._lower_fact(handle, provider, verb),
            ambiguous=EnergySystemErrorId.SIZING_AMBIGUOUS,
            missing=missing,
        )

    @staticmethod
    def _fact_provider(other: Handle, fact: str) -> Optional[str]:
        """The component an active provided-fact port offers a fact from, or ``None``."""
        port = other.port
        if port.kind != PortKind.FACT or not port.is_provision or other.state == "inactive" or other.fact != fact:
            return None
        return other.provider[0] if other.provider is not None else None

    def _resolve_fact(self, handle: Handle, target: str, level: Level) -> Tuple[Optional[str], str]:
        """Resolves a verb's partner reference to the component providing a fact."""
        fact = handle.fact or ""
        head, rest = self._split_target(target, level)
        if head is None:
            return None, f"'{target}' names no component and no import of {level.where_verbs}"
        units, handles = level.targets[head]
        if rest:
            other = handles.get(rest[0]) if len(rest) == 1 else None
            if other is None:
                return None, f"'{head}' has no port '{'.'.join(rest)}'"
            if other.state == "inactive":
                return None, f"the port '{target}' is inactive with its parameters"
            if (
                other.port.kind != PortKind.FACT
                or not other.port.is_provision
                or other.fact != fact
                or not other.provider
            ):
                raise self.error(
                    EnergySystemErrorId.FACT_NOT_PROVIDED,
                    handle.owner,
                    f"fact port '{handle.port.name}' (fact {fact}) is bound to '{target}', which provides no fact "
                    f"{fact} {handle.source_text()}.",
                    remedy=handle.hint or None,
                )
            return other.provider[0], ""
        if head in level.members:
            unit = units[0]
            config_class = self.classes.config_class(unit.class_path, f"components.{unit.name}", unit.name)
            declared = declared_facts_of(config_class)
            if fact not in declared:
                raise self.error(
                    EnergySystemErrorId.FACT_NOT_PROVIDED,
                    handle.owner,
                    f"fact port '{handle.port.name}' (fact {fact}) is bound to '{target}' ({config_class.__name__}), "
                    f"which does not contribute {fact} (it contributes {', '.join(declared) or 'no fact'}) "
                    f"{handle.source_text()}.",
                    remedy=handle.hint or None,
                )
            return unit.name, ""
        provided = [
            other
            for other in handles.values()
            if other.port.kind == PortKind.FACT
            and other.port.is_provision
            and other.provider is not None
            and other.state != "inactive"
            and other.fact == fact
        ]
        if len(provided) == 1:
            return provided[0].provider[0] if provided[0].provider else None, ""
        if not provided:
            raise self.error(
                EnergySystemErrorId.FACT_NOT_PROVIDED,
                handle.owner,
                f"fact port '{handle.port.name}' (fact {fact}) is bound to '{target}', which provides no fact {fact} "
                f"through a port {handle.source_text()}.",
                remedy=handle.hint or None,
            )
        raise self.error(
            EnergySystemErrorId.PORT_AMBIGUOUS,
            handle.owner,
            f"fact port '{handle.port.name}' (fact {fact}) is bound to '{target}', which provides it "
            f"{len(provided)} times: {', '.join(other.port.name for other in provided)} {handle.source_text()}.",
            remedy=self._paste_references(handle, [f"{head}.{other.port.name}" for other in provided], False),
        )

    def _lower_fact(self, handle: Handle, provider: str, verb: str) -> None:
        """Lowers a bound fact need to a ``sizing_sources`` line on each member it names (§3.2, §6)."""
        fact = handle.fact or ""
        lowered: List[str] = []
        for unit in handle.fact_into:
            config_class = self.classes.config_class(unit.class_path, f"components.{unit.name}", unit.name)
            readable = facts_read_by(config_class)
            if fact not in readable:
                raise self.error(
                    EnergySystemErrorId.PORT_CONTRACT,
                    handle.owner,
                    f"fact port '{handle.port.name}' lowers {fact} into {unit.name}, whose class "
                    f"{config_class.__name__} reads no such fact {handle.source_text()}.",
                    alternatives=readable,
                    alternatives_label=f"facts {config_class.__name__} reads",
                    offending_value=fact,
                )
            if fact in unit.entry.sizing_sources or fact in unit.lowered_sizing:
                raise self.error(
                    EnergySystemErrorId.PORT_CONTRACT,
                    handle.owner,
                    f"fact port '{handle.port.name}' lowers {fact} into {unit.name}, which already names a source "
                    f"for it {handle.source_text()}.",
                )
            unit.lowered_sizing[fact] = SourceReference(component=provider, fact=fact)
            unit.sizing_notes[fact] = f"port {handle.port.name} bound to {provider} ({verb})"
            lowered.append(f"{unit.name}.sizing_sources.{fact}: {provider}.{fact}")
        handle.record = {"state": handle.state, "partner": provider, "verb": verb, "lowered_to": tuple(lowered)}
        handle.decided = True
        self.record.decisions.append(f"{handle.owner_path}.{handle.port.name} -> {provider} (fact {fact}, {verb})")

    @staticmethod
    def _item_text(item: AnyInputItem) -> str:
        """An input item as the record lists it."""
        if isinstance(item, ExplicitWire):
            return f"{{input: {item.input}, from: {item.source}.{item.output}}}"
        if isinstance(item, AggregatorFeed):
            return f"{{from: {item.source}.{item.output}, tags: [{', '.join(item.tags)}], weight: {item.weight}}}"
        return item.source

    def _note_lowered(
        self,
        handle: Handle,
        kind: str,
        member: Optional[Unit],
        verb: str,
        partner: Optional[Unit] = None,
        target: str = "",
        output: str = "",
        **context: Any,
    ) -> None:
        """Writes one lowered item into the port-provenance table, which a wiring refusal of it reads.

        ``context`` carries the fields of a circuit item or a feed (:class:`LoweredPort`).
        """
        self.record.port_provenance.append(
            LoweredPort(
                kind=kind,
                owner=handle.owner,
                import_path=handle.owner_path,
                port=handle.port.name,
                verb=verb,
                member=member.name if member is not None else "",
                member_class=member.class_path if member is not None else "",
                partner=partner.name if partner is not None else "",
                partner_class=partner.class_path if partner is not None else "",
                input=target,
                output=output,
                chain=tuple(location.text for location in handle.chain),
                **context,
            )
        )

    @staticmethod
    def _decision_order(handles: Sequence[Tuple[BindingVerbs, Handle]]) -> List[Tuple[BindingVerbs, Handle]]:
        """The order ports are decided in: every port but the circuit ends as written, then the circuit ends.

        A circuit binds two ends at once, so the ends whose verbs say what they bind go first, the
        required ends next and the optional ones last: an optional end is never taken by the default
        rule from a required end that has only it as its candidate.
        """

        def rank(item: Tuple[BindingVerbs, Handle]) -> int:
            verbs, handle = item
            if handle.port.kind != PortKind.CIRCUIT:
                return 0
            if verbs.verb_for(handle.port.name) is not None:
                return 1
            return 2 if handle.state == "required" else 3

        return sorted(handles, key=rank)

    def _bind_inside(
        self,
        assembly: ResolvedAssembly,
        level: Level,
        inner: Mapping[str, List[Tuple[Optional[str], Instance]]],
        units: Mapping[str, Unit],
        import_path: str,
    ) -> None:
        """Binds the inner imports' ports inside one assembly: internal entries, verbs, the default rule."""
        model = assembly.model
        for name, port in model.ports_of(PortKind.INTERNAL).items():
            sender, receiver = port.ends
            self._bind_internal(assembly, name, sender, receiver, level, inner, units, import_path)
        pending: List[Tuple[BindingVerbs, Handle]] = []
        for key, results in inner.items():
            entry = model.imports[key]
            for _instance_key, instance in results:
                self._check_verbs_name_ports(entry.verbs, instance.handles, f"{assembly.label}: imports.{key}")
                for handle in instance.handles.values():
                    if handle.decided and handle.port.kind != PortKind.CIRCUIT:
                        continue
                    handle.verb_site = f"the inner import '{key}' in {assembly.label}"
                    pending.append((entry.verbs, handle))
        for verbs, handle in self._decision_order(pending):
            self._decide(handle, verbs, level, top=False)

    def _bind_internal(
        self,
        assembly: ResolvedAssembly,
        name: str,
        sender: str,
        receiver: str,
        level: Level,
        inner: Mapping[str, List[Tuple[Optional[str], Instance]]],
        units: Mapping[str, Unit],
        import_path: str,
    ) -> None:
        """Lowers one ``internal:`` entry: a member to an inner import's port, or the reverse (§2.5)."""
        location = f"{assembly.label}: interface.internal.{name}"

        def inner_handle(reference: str) -> Optional[Handle]:
            if "." not in reference:
                return None
            key, port_name = reference.split(".", 1)
            results = inner.get(key)
            if not results:
                raise self.error(
                    EnergySystemErrorId.PORT_CONTRACT,
                    location,
                    f"the internal port '{name}' names '{reference}', but '{key}' is no inner import of "
                    f"'{assembly.path}'.",
                    alternatives=tuple(inner),
                    alternatives_label="inner imports",
                )
            if len(results) != 1:
                raise self.error(
                    EnergySystemErrorId.PORT_CONTRACT,
                    location,
                    f"the internal port '{name}' names '{reference}', but the inner import '{key}' has several "
                    "instances.",
                )
            found = results[0][1].handles.get(port_name)
            if found is None:
                raise self.error(
                    EnergySystemErrorId.PORT_CONTRACT,
                    location,
                    f"the internal port '{name}' names '{reference}', but '{key}' has no port '{port_name}'.",
                    alternatives=tuple(results[0][1].handles),
                    alternatives_label="ports",
                )
            return found

        sender_handle, receiver_handle = inner_handle(sender), inner_handle(receiver)
        if sender_handle is None and receiver_handle is None:
            raise self.error(
                EnergySystemErrorId.PORT_CONTRACT,
                location,
                f"the internal port '{name}' binds two members, {sender} and {receiver}; an internal entry binds a "
                "member to an inner import's port, and two members are wired by a bare name in the receiver's inputs.",
            )
        if receiver_handle is not None:
            if sender_handle is not None:
                if sender_handle.provider is None:
                    raise self.error(
                        EnergySystemErrorId.PORT_CONTRACT,
                        location,
                        f"the internal port '{name}' sends from '{sender}', which provides no output.",
                    )
                partner = next(unit for unit in level.units if unit.name == sender_handle.provider[0])
                sender_handle.decided = True
                sender_handle.record = {"state": "internal", "partner": f"{receiver} (internal {name})"}
            else:
                member = units.get(sender)
                if member is None:
                    raise self.error(
                        EnergySystemErrorId.PORT_CONTRACT,
                        location,
                        f"the internal port '{name}' sends from '{sender}', which is no member of '{assembly.path}' "
                        "with these parameters.",
                        alternatives=tuple(units),
                        alternatives_label="members",
                    )
                partner = member
            if receiver_handle.decided:
                raise self.error(
                    EnergySystemErrorId.PORT_CONTRACT,
                    location,
                    f"the internal port '{name}' binds '{receiver}', which another entry of '{assembly.path}' already "
                    "binds.",
                )
            if receiver_handle.state == "inactive":
                raise self.error(
                    EnergySystemErrorId.VERB_ON_INACTIVE_PORT,
                    location,
                    f"the internal port '{name}' binds '{receiver}', which is inactive with these parameters "
                    f"{receiver_handle.source_text()}.",
                )
            self._lower(receiver_handle, partner, None, f"internal {name}")
            receiver_handle.record["state"] = "internal"
            return
        assert sender_handle is not None
        member = units.get(receiver)
        if member is None:
            raise self.error(
                EnergySystemErrorId.PORT_CONTRACT,
                location,
                f"the internal port '{name}' lands in '{receiver}', which is no member of '{assembly.path}' with "
                "these parameters.",
                alternatives=tuple(units),
                alternatives_label="members",
            )
        if sender_handle.provider is None:
            raise self.error(
                EnergySystemErrorId.PORT_CONTRACT,
                location,
                f"the internal port '{name}' sends from '{sender}', which provides no output.",
            )
        partner = next(unit for unit in level.units if unit.name == sender_handle.provider[0])
        positions = member.placeholder_positions(name)
        if not positions:
            raise self.error(
                EnergySystemErrorId.PORT_CONTRACT,
                location,
                f"the internal port '{name}' lands in '{receiver}', which carries no '{{$port: {name}}}' placeholder.",
            )
        handle = Handle(
            owner=f"import {import_path}",
            owner_path=import_path,
            verb_site=f"'{assembly.label}'",
            port=Port(
                name=name,
                section="internal",
                kind=PortKind.NEED,
                into=(receiver,),
                partner=(short_class_name(partner.class_path),),
            ),
            state="required",
            landings=[
                Landing(unit=member, position=position, placeholder=placeholder) for position, placeholder in positions
            ],
            provider=None,
            own_units=frozenset(),
            chain=(assembly.lines.location("interface", "internal", name),),
        )
        self._lower(handle, partner, None, f"internal {name}")
        sender_handle.decided = True
        sender_handle.record = {"state": "internal", "partner": f"{receiver} (internal {name})"}

    def _check_placeholders(self, assembly: ResolvedAssembly, units: Mapping[str, Unit], where: str) -> None:
        """Refuses a member placeholder naming no port that lowers into that member (contract)."""
        ports = assembly.model.ports
        for member, unit in units.items():
            for placed in unit.entry.placeholders:
                placeholder = placed.placeholder
                if not isinstance(placeholder, PortPlaceholder):
                    continue
                port = ports.get(placeholder.port)
                fits = port is not None and (
                    (port.kind == PortKind.NEED and member in port.into)
                    or (port.kind == PortKind.INTERNAL and port.ends and port.ends[1] == member)
                    or (port.kind == PortKind.CIRCUIT and member in port.members)
                    or (port.kind == PortKind.CARRIER and port.is_provision and port.meter == member)
                )
                if not fits:
                    raise self.error(
                        EnergySystemErrorId.PORT_CONTRACT,
                        f"{assembly.label}: {'.'.join(unit.block_path)}.inputs[{placed.position}]",
                        f"the member '{member}' carries a placeholder for '{placeholder.port}', which is no port "
                        f"lowering into it ({where}).",
                        alternatives=tuple(ports),
                        alternatives_label="ports",
                    )

    def _port_record(self, handle: Handle) -> PortRecord:
        """The record of one decided (or undecided) port."""
        record = handle.record or {"state": handle.state}
        return PortRecord(
            port=handle.port.name,
            kind=handle.port.kind.value,
            state=str(record.get("state", handle.state)),
            partner=str(record.get("partner", "")),
            verb=str(record.get("verb", "")),
            lowered_to=tuple(record.get("lowered_to", ())),
        )

    # ------------------------------------------------------------------------------------------- order

    def _order_level(
        self,
        assembly: ResolvedAssembly,
        units: Mapping[str, Unit],
        inner: Mapping[str, List[Tuple[Optional[str], Instance]]],
        where: str,
    ) -> None:
        """Gives the members and inner imports of one assembly level their order paths."""
        model = assembly.model
        entries: List[Tuple[str, Optional[int], int, int]] = []
        for index, (member, unit) in enumerate(units.items()):
            entries.append((f"member {member}", unit.entry.order, assembly.lines.line(*unit.block_path), index))
        offset = len(entries)
        for index, key in enumerate(inner):
            entries.append(
                (f"import {key}", model.imports[key].order, assembly.lines.line("imports", key), offset + index)
            )
        numbers = self._numbers(entries, f"{assembly.label} ({where})", by_line=True)
        for member, unit in units.items():
            unit.order_path = (numbers[f"member {member}"],)
        for key, results in inner.items():
            base = numbers[f"import {key}"]
            for index, (instance_key, instance) in enumerate(results, start=1):
                prefix = (base, index) if instance_key is not None else (base,)
                for unit in instance.units:
                    unit.order_path = prefix + unit.order_path

    def _numbers(
        self, entries: Sequence[Tuple[str, Optional[int], int, int]], where: str, *, by_line: bool
    ) -> Dict[str, int]:
        """Numbers one level: the declared orders, or positions; all or none, no repeats (D23)."""
        declared = [entry for entry in entries if entry[1] is not None]
        if declared and len(declared) != len(entries):
            missing = [label for label, order, _line, _index in entries if order is None]
            raise self.error(
                EnergySystemErrorId.ORDER_INVALID,
                where,
                f"a level numbers all its entries or none, but {', '.join(missing)} carr"
                f"{'ies' if len(missing) == 1 else 'y'} no order: while the others do.",
            )
        if declared:
            seen: Dict[int, str] = {}
            for label, order, _line, _index in entries:
                assert order is not None
                if order in seen:
                    raise self.error(
                        EnergySystemErrorId.ORDER_INVALID,
                        where,
                        f"{seen[order]} and {label} both declare order: {order}; a number is used once per level.",
                    )
                seen[order] = label
            return {label: int(order) for label, order, _line, _index in entries if order is not None}
        ordered = sorted(entries, key=lambda entry: (entry[2], entry[3]) if by_line else entry[3])
        return {label: position for position, (label, _order, _line, _index) in enumerate(ordered, start=1)}

    def _order_top(
        self, site_units: Sequence[Unit], instances: Mapping[str, List[Tuple[Optional[str], Instance]]]
    ) -> None:
        """Numbers the top level: the site's components and the imports (D23)."""
        entries: List[Tuple[str, Optional[int], int, int]] = []
        for index, unit in enumerate(site_units):
            entries.append((f"component {unit.name}", unit.entry.order, 0, index))
        offset = len(entries)
        for index, key in enumerate(instances):
            entries.append((f"import {key}", self.model.imports[key].order, 0, offset + index))
        uses_order = any(order is not None for _label, order, _line, _index in entries)
        if (
            uses_order
            and any(group.components for group in self.model.groups.values())
            or (uses_order and self.model.variants)
        ):
            raise self.error(
                EnergySystemErrorId.ORDER_INVALID,
                "components",
                "the file positions its components with order:, but groups or variants hold components that no "
                "order: can position; a file using order: writes every component at the top level.",
            )
        numbers = self._numbers(entries, "the file's top level", by_line=False)
        for unit in site_units:
            unit.order_path = (numbers[f"component {unit.name}"],)
        for key, results in instances.items():
            base = numbers[f"import {key}"]
            for index, (instance_key, instance) in enumerate(results, start=1):
                prefix = (base, index) if instance_key is not None else (base,)
                for unit in instance.units:
                    unit.order_path = prefix + unit.order_path

    # ---------------------------------------------------------------------------------------- assembly

    def _final_entry(self, unit: Unit) -> ComponentEntry:
        """The unit's entry as the flat file writes it: references rewritten, placeholders lowered."""
        entry = unit.entry
        written = len(entry.inputs) + len(entry.placeholders)
        placeholder_positions = {placed.position for placed in entry.placeholders}
        ordinary = iter(entry.inputs)
        inputs: List[AnyInputItem] = []
        origins: List[Tuple[str, Any]] = []
        for position in range(written):
            if position in placeholder_positions:
                for item in unit.lowered.get(position, []):
                    inputs.append(item)
                    origins.append(("lowered", position))
                continue
            item = next(ordinary)
            rewritten = self._rewrite_item(unit, item, position)
            if rewritten is not None:
                inputs.append(rewritten)
                origins.append(("written", position))
        sizing = {fact: self._rewrite_sizing(unit, fact, value) for fact, value in entry.sizing_sources.items()}
        sizing.update(unit.lowered_sizing)
        final = entry.model_copy(
            update={
                "name": unit.name,
                "inputs": tuple(inputs),
                "sizing_sources": sizing,
                "order": None,
                "ports": {},
                "verbs": BindingVerbs(),
                "placeholders": (),
            }
        )
        self._map_sources(unit, final, origins)
        return final

    def _rewrite_item(self, unit: Unit, item: AnyInputItem, position: int) -> Optional[AnyInputItem]:
        """Rewrites one written input item of a member to the serialized names (``None`` drops it)."""
        if unit.identity is None:
            return item
        source = item.source
        if source in unit.dropped_names:
            return None
        rewritten = unit.local_names.get(source)
        if rewritten is None:
            raise self.error(
                EnergySystemErrorId.PORT_CONTRACT,
                f"{unit.lines.origin}: {'.'.join(unit.block_path)}.inputs[{position}]",
                f"the member '{unit.entry.name}' takes an input from '{source}', which is no member of its "
                f"assembly (import {unit.import_path}); a member names only other members, and everything that "
                "crosses the boundary crosses through a port.",
                alternatives=tuple(unit.local_names),
                alternatives_label="members",
                offending_value=source,
            )
        return item.model_copy(update={"source": rewritten})

    def _rewrite_sizing(self, unit: Unit, fact: str, value: Any) -> Any:
        """Rewrites one ``sizing_sources`` line of a member to the serialized names."""
        if unit.identity is None:
            return value

        def one(reference: SourceReference) -> Optional[SourceReference]:
            if reference.component in unit.dropped_names:
                return None
            rewritten = unit.local_names.get(reference.component)
            if rewritten is None:
                raise self.error(
                    EnergySystemErrorId.PORT_CONTRACT,
                    f"{unit.lines.origin}: {'.'.join(unit.block_path)}.sizing_sources.{fact}",
                    f"the member '{unit.entry.name}' takes '{fact}' from '{reference.component}', which is no "
                    f"member of its assembly (import {unit.import_path}).",
                    alternatives=tuple(unit.local_names),
                    alternatives_label="members",
                )
            return reference.model_copy(update={"component": rewritten})

        if isinstance(value, SourceReference):
            rewritten = one(value)
            if rewritten is None:
                raise self.error(
                    EnergySystemErrorId.DISABLED_SIZING_SOURCE,
                    f"{unit.lines.origin}: {'.'.join(unit.block_path)}.sizing_sources.{fact}",
                    f"the member '{unit.entry.name}' takes '{fact}' from '{value.component}', which the selected "
                    f"internal variants leave out (import {unit.import_path}).",
                )
            return rewritten
        return tuple(reference for reference in (one(item) for item in value) if reference is not None)

    def _map_sources(self, unit: Unit, final: ComponentEntry, origins: Sequence[Tuple[str, Any]]) -> None:
        """Adds the source-map entries of one produced or modified component."""
        source_map = self.record.source_map
        if unit.identity is not None:
            source_map.add(SourceMapEntry(unit.name, "component", unit.import_path, unit.entry.name, unit.chain))
        prefix = unit.chain[:-1]
        for index, (kind, position) in enumerate(origins):
            if kind == "lowered" or unit.identity is not None:
                location = unit.lines.location(*unit.block_path, "inputs", position)
                note = unit.notes.get(position, "") if kind == "lowered" else ""
                source_map.add(
                    SourceMapEntry(
                        unit.name, f"inputs[{index}]", unit.import_path, unit.entry.name, prefix + (location,), note
                    )
                )
        if unit.identity is None:
            return
        for fact in final.sizing_sources:
            if fact in unit.lowered_sizing:
                location = unit.lines.location(*unit.block_path)
                note = unit.sizing_notes.get(fact, "")
            else:
                location = unit.lines.location(*unit.block_path, "sizing_sources", fact)
                note = ""
            source_map.add(
                SourceMapEntry(
                    unit.name, f"sizing_sources.{fact}", unit.import_path, unit.entry.name, prefix + (location,), note
                )
            )
        for key in final.config:
            location = unit.lines.location(*unit.block_path, "config", key)
            source_map.add(
                SourceMapEntry(unit.name, f"config.{key}", unit.import_path, unit.entry.name, prefix + (location,))
            )

    def _assemble(self, site_units: Sequence[Unit], all_units: Sequence[Unit]) -> EnergySystemFile:
        """Writes the flat file: every component in the evaluation sequence, schema version 3."""
        ordered = sorted(enumerate(all_units), key=lambda pair: (pair[1].order_path, pair[0]))
        components: Dict[str, ComponentEntry] = {}
        for _index, unit in ordered:
            components[unit.name] = self._final_entry(unit)
            self.record.sequence.append((unit.name, unit.order_path))
        position = len(self.record.sequence)
        for group in self.model.groups.values():
            for name in group.components:
                position += 1
                self.record.sequence.append((name, (position,)))
        for variant in self.model.variants.values():
            for name in variant.selected_components():
                position += 1
                self.record.sequence.append((name, (position,)))
        for unit in site_units:
            for handle in self._site_handles_cache.get(unit.name, {}).values():
                self.record.site_ports.append((unit.name, self._port_record(handle)))
        return self.model.model_copy(
            update={
                "schema_version": EnergySystemFile.SUPPORTED_SCHEMA_VERSION,
                "components": components,
                "imports": {},
                "addresses": {**dict(self.model.addresses), **self.record.addresses},
            }
        )


def expand_imports(
    model: EnergySystemFile,
    resolver: Optional[AssemblyResolver] = None,
    *,
    lines: Optional[LineIndex] = None,
) -> Tuple[EnergySystemFile, ImportRecord]:
    """Expands every import of a file into the flat file it stands for (``assemblies_spec.md`` §2.3).

    The function is pure and idempotent. A version-3 file — every committed file, and every file an
    expansion produced — is returned as the very same object with an empty record, so expanding it
    changes nothing at all; a version-4 file comes back as a version-3 one.

    Args:
        model: The file as read.
        resolver: Finds the assemblies; this machine's default search path when omitted, which is
            only consulted when the file imports something.
        lines: The file's line index, for the source map.

    Returns:
        The flat file and the import record.

    Raises:
        EnergySystemAssemblyError: For any condition of the ``EF-7x`` band.
    """
    if not model.uses_assemblies and model.schema_version == EnergySystemFile.SUPPORTED_SCHEMA_VERSION:
        return model, ImportRecord()
    return ImportExpander(model, resolver if resolver is not None else AssemblyResolver.default(), lines).expand()
