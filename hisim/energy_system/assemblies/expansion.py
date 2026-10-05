"""Expansion of imports: turning a file that imports assemblies into one flat energy-system file.

``assemblies_spec.md`` §2.3. One more pure stage in front of the group expansion: it builds a new
file and never mutates its input, it is idempotent, and a file that imports nothing comes back as
the very same object, so every committed file and every golden behaves exactly as before.

The expansion works **innermost first**. For each import and each of its instances it

1. resolves the assembly (:mod:`.resolver`), runs the library check on it (:mod:`.library`, the
   same check the library test runs, units of the parameters included), applies the preset and
   checks the parameters (:mod:`.parameters`);
2. evaluates every port's ``required_when``/``active_when`` and selects the internal variants;
3. substitutes the parameters and gives every member its structured address
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

**What this step does not lower** — circuit ports, carrier needs and fact ports (hisim-lt0b.2),
observer and actuator selectors and controllable outputs (hisim-lt0b.3), and the ``$fact``,
``$switch`` and ``$derived`` values — is listed in the import record and refused as a whole with
``EF-7L``, never ignored.

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
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Sequence, Set, Tuple

from hisim.config import AddressStep, ComponentID
from hisim.energy_system.assemblies.model import MemberTemplate
from hisim.energy_system.assemblies.library import require_valid
from hisim.energy_system.assemblies.parameters import (
    ParameterResolver,
    ParameterSubstitution,
    ResolvedParameters,
)
from hisim.energy_system.assemblies.record import (
    ImportRecord,
    InstanceRecord,
    LoweredKind,
    LoweredPort,
    NotLowered,
    PortRecord,
    SourceMapEntry,
    short_class_name,
)
from hisim.energy_system.assemblies.resolver import AssemblyResolver, ResolvedAssembly
from hisim.energy_system.classes import ClassBinder
from hisim.energy_system.errors import (
    EnergySystemAssemblyError,
    EnergySystemErrorId,
)
from hisim.energy_system.imports_model import (
    BindingVerbs,
    ImportEntry,
    ObservesPlaceholder,
    ParameterReference,
    Port,
    PortKind,
    PortPlaceholder,
    PortState,
)
from hisim.energy_system.model import (
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


@dataclass
class Handle:
    """One port of an expanded instance or of a site entry, as its importer sees it.

    Attributes:
        owner: How messages name the owner: ``import 'dhw'`` or ``component 'Building'``.
        owner_path: The owner's import path (``pv[east]``) or a site entry's name.
        verb_site: Where a verb for this port is written, for the paste-ready line.
        port: The port's declaration.
        state: Its requirement state, :class:`PortState` ``REQUIRED``, ``OPTIONAL`` or ``INACTIVE``.
        landings: Where a need's items land; resolved through re-exports.
        provider: ``(component, output)`` of a provided output.
        own_units: The expanded names inside the owner, which are never its partners.
        chain: The source map of the owner's port.
        record: The port's record in the import record.
        decided: Whether a verb, an internal entry, a re-export or the default rule has decided it.
        hint: The candidates and the paste-ready verb lines a refusal of this port prints.
    """

    owner: str
    owner_path: str
    verb_site: str
    port: Port
    state: PortState
    landings: List[Landing]
    provider: Optional[Tuple[str, str]]
    own_units: FrozenSet[str]
    chain: Tuple[SourceLocation, ...]
    record: Dict[str, Any] = field(default_factory=dict)
    decided: bool = False
    hint: str = ""

    @property
    def partner_text(self) -> str:
        """The partner classes as a message lists them."""
        return ", ".join(self.port.partner) or "-"

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
class Level:
    """One level of binding: the top of a file, or the inside of one assembly instance.

    Attributes:
        units: Every unit in scope, nested ones included, in production order.
        references: Expanded name to how a verb at this level names it (``control``,
            ``pv.east``, a member's or site entry's own name).
        targets: How a verb at this level names a component or an import instance, to the
            units and the handles behind it.
        where_verbs: For a message: where the verbs of this level are written.
    """

    units: List[Unit]
    references: Dict[str, str]
    targets: Dict[str, Tuple[List[Unit], Dict[str, Handle]]]
    where_verbs: str


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
        self.check_unique_names(all_units)
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
        for verbs, handle in handles:
            self._decide(handle, verbs, level, top=True)
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

    def check_unique_names(self, units: Sequence[Unit]) -> None:
        """Refuses two components whose names collide, before anything is keyed by a name.

        A member's name is the serialization of its structured address, which drops the step
        boundaries: ``pv`` with the instance ``east`` and ``pv`` importing ``east`` both serialize
        to ``pv-east-…`` (:class:`~hisim.config.AddressStep`). Import keys are unique per level and an
        import has instances or none, so two members of one file do not collide today; the check
        refuses any collision by name rather than letting the record's address table and the
        simulator's component names silently keep one of the two.

        Raises:
            EnergySystemAssemblyError: ``EF-52`` naming both components and the import paths they
                came from.
        """
        seen: Dict[str, Unit] = {}
        for unit in units:
            earlier = seen.get(unit.name)
            if earlier is not None:
                raise self.error(
                    EnergySystemErrorId.DUPLICATE_NAME,
                    f"components.{unit.name}",
                    f"two components serialize to the name '{unit.name}': {self._origin_text(earlier)} and "
                    f"{self._origin_text(unit)}.",
                    remedy="Rename an import, an instance or a member so that their addresses differ.",
                )
            seen[unit.name] = unit

    @staticmethod
    def _origin_text(unit: Unit) -> str:
        """Where a unit comes from, for a message: its import path and member, or the site entry."""
        if unit.identity is None:
            return f"the component '{unit.name}' of the file"
        return f"the member '{unit.identity.name}' of import {unit.import_path}"

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
            require_valid(assembly, self.resolver)
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
        states = {name: self._state(port, parameters.resolved) for name, port in model.ports.items()}
        for name, port in model.ports.items():
            if states[name] != PortState.INACTIVE:
                self._note_unlowered_port(import_path, port)
        units = {
            name: self._member_unit(
                assembly, template, parameters, path, chain, selected, dropped, f"{where}, {source}"
            )
            for name, template in selected.items()
        }
        for name, unit in units.items():
            record.members[unit.name] = {"member": name}
            display = self._display(selected[name], parameters, assembly, where)
            if display is not None:
                record.members[unit.name]["display_name"] = display
        inner: Dict[str, List[Tuple[Optional[str], Instance]]] = {}
        for key, entry in model.imports.items():
            inner[key] = self._expand_inner_import(assembly, key, entry, parameters, path, chain, stack)
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
        handles = self._own_handles(assembly, units, inner, states, path, chain)
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
        parameters: ResolvedParameters,
        path: Tuple[AddressStep, ...],
        chain: Tuple[SourceLocation, ...],
        stack: Tuple[str, ...],
    ) -> List[Tuple[Optional[str], Instance]]:
        """Expands one inner import of an assembly, its parameters substituted from the outer ones."""
        self._note_import_selectors(f"{self.path_text(path)} → {key}", entry)
        inner_assembly = self.resolver.resolve(entry.assembly, f"{assembly.label}: imports.{key}")
        substitution = ParameterSubstitution(parameters.resolved)
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

    def _state(self, port: Port, values: Mapping[str, Any]) -> PortState:
        """A port's requirement state for one instance, or for a site entry with no values (§3.1).

        The reader refuses ``required_when``/``active_when`` on a site entry's port, so a site
        entry's state follows from ``optional`` alone, by the same rule.
        """
        if port.active_when and not self._holds(port.active_when, values):
            return PortState.INACTIVE
        if port.required_when:
            return PortState.REQUIRED if self._holds(port.required_when, values) else PortState.INACTIVE
        if port.kind in (PortKind.PROVIDED,) or port.optional:
            return PortState.OPTIONAL
        return PortState.REQUIRED

    def _note_unlowered_port(self, import_path: str, port: Port) -> None:
        """Notes an active port this step does not lower."""
        if not port.kind.lowered_in_step_1a:
            self.not_lowered(f"{import_path}: port {port.name} ({port.kind.value})", port.kind.delivering_step)
        if port.kind == PortKind.PROVIDED and "controllable" in port.raw:
            self.not_lowered(
                f"{import_path}: port {port.name} (controllable)",
                "hisim-lt0b.3 (observe and actuate selectors, controller lowering)",
            )

    def _member_unit(
        self,
        assembly: ResolvedAssembly,
        template: MemberTemplate,
        parameters: ResolvedParameters,
        path: Tuple[AddressStep, ...],
        chain: Tuple[SourceLocation, ...],
        selected: Mapping[str, MemberTemplate],
        dropped: FrozenSet[str],
        where: str,
    ) -> Unit:
        """One member of an instance: parameters substituted, address given (the library check checked the units)."""
        entry = template.entry
        name = entry.name
        identity = ComponentID(name=name, path=path, assembly=assembly.path)
        location = f"{assembly.label}: {'.'.join(template.source_path)}"
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

        substitution = ParameterSubstitution(parameters.resolved)
        config = substitution.apply(dict(entry.config))
        constructor = entry.constructor
        if constructor is not None:
            constructor = constructor.model_copy(update={"arguments": substitution.apply(dict(constructor.arguments))})
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
        states: Mapping[str, PortState],
        path: Tuple[AddressStep, ...],
        chain: Tuple[SourceLocation, ...],
    ) -> Dict[str, Handle]:
        """The ports an instance offers its importer, resolved to landings and providers."""
        import_path = self.path_text(path)
        own_units = frozenset(unit.name for unit in units.values()) | frozenset(
            unit.name for results in inner.values() for _key, instance in results for unit in instance.units
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
            if port.kind == PortKind.NEED:
                for member in port.into:
                    unit = units.get(member)
                    if unit is None:
                        continue
                    positions = unit.placeholder_positions(name)
                    for position, placeholder in positions:
                        handle.landings.append(Landing(unit=unit, position=position, placeholder=placeholder))
                if handle.state != PortState.INACTIVE and not handle.landings:
                    raise self.error(
                        EnergySystemErrorId.PORT_CONTRACT,
                        f"{assembly.label}: interface.{port.section}.{name}",
                        f"the port '{name}' is active, but none of its members {', '.join(port.into)} exists with "
                        f"these parameters or carries a '{{$port: {name}}}' placeholder {handle.source_text()}.",
                    )
            elif port.kind == PortKind.PROVIDED:
                provider_member, output = port.output_member, port.output_name
                unit = units.get(provider_member or "")
                if unit is None:
                    if handle.state != PortState.INACTIVE:
                        raise self.error(
                            EnergySystemErrorId.PORT_CONTRACT,
                            f"{assembly.label}: interface.{port.section}.{name}",
                            f"the provided port '{name}' names the member '{provider_member}', which does not exist "
                            f"with these parameters {handle.source_text()}.",
                        )
                else:
                    handle.provider = (unit.name, output or "")
                    if handle.state != PortState.INACTIVE:
                        self._note_lowered(handle, LoweredKind.PROVIDED, unit, "", output=output or "")
            elif port.kind == PortKind.REEXPORT:
                inner_key, inner_port = (port.reexports or ".").split(".", 1)
                results = inner.get(inner_key)
                if results is None:
                    raise self.error(
                        EnergySystemErrorId.PORT_CONTRACT,
                        f"{assembly.label}: interface.{port.section}.{name}",
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
                            f"{assembly.label}: interface.{port.section}.{name}",
                            f"the port '{name}' re-exports '{port.reexports}', but the inner import '{inner_key}' "
                            f"has no port '{inner_port}' {handle.source_text()}.",
                            alternatives=tuple(instance.handles),
                            alternatives_label="ports",
                        )
                    inner_handle.decided = True
                    inner_handle.record = {"state": PortState.REEXPORTED, "partner": f"as {import_path}.{name}"}
                    handle.landings.extend(inner_handle.landings)
                    handle.provider = handle.provider or inner_handle.provider
                    handle.port = handle.port.model_copy(
                        update={
                            "kind": inner_handle.port.kind,
                            "partner": inner_handle.port.partner,
                            "wires": inner_handle.port.wires,
                            "output": inner_handle.port.output,
                        }
                    )
                    if handle.state != PortState.INACTIVE:
                        handle.state = (
                            PortState.INACTIVE
                            if inner_handle.state == PortState.INACTIVE
                            else (PortState.OPTIONAL if port.optional else inner_handle.state)
                        )
            handles[name] = handle
        return handles

    def _site_handles(self, unit: Unit) -> Dict[str, Handle]:
        """The ports of one site entry: needs whose landing is the entry itself."""
        if unit.name in self._site_handles_cache:
            return self._site_handles_cache[unit.name]
        handles: Dict[str, Handle] = {}
        for name, port in unit.entry.ports.items():
            handle = Handle(
                owner=f"component {unit.name}",
                owner_path=unit.name,
                verb_site=f"the component '{unit.name}'",
                port=port,
                state=self._state(port, {}),
                landings=[],
                provider=None,
                own_units=frozenset({unit.name}),
                chain=(self.lines.location("components", unit.name, "ports", name),),
            )
            if not port.kind.lowered_in_step_1a:
                self.not_lowered(f"component {unit.name}: port {name} ({port.kind.value})", port.kind.delivering_step)
            for position, placeholder in unit.placeholder_positions(name):
                handle.landings.append(Landing(unit=unit, position=position, placeholder=placeholder))
            if port.kind == PortKind.NEED and not handle.landings:
                raise self.error(
                    EnergySystemErrorId.PORT_CONTRACT,
                    f"components.{unit.name}.ports.{name}",
                    f"the port '{name}' of '{unit.name}' has no '{{$port: {name}}}' placeholder in the entry's "
                    f"inputs, so there is nowhere for its items to land {handle.source_text()}.",
                )
            handles[name] = handle
        for placed in unit.entry.placeholders:
            if isinstance(placed.placeholder, PortPlaceholder) and placed.placeholder.port not in unit.entry.ports:
                raise self.error(
                    EnergySystemErrorId.PORT_CONTRACT,
                    f"components.{unit.name}.inputs[{placed.position}]",
                    f"'{unit.name}' carries a placeholder for the port '{placed.placeholder.port}', which its "
                    "'ports' block does not declare.",
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
        for local, unit in members.items():
            references[unit.name] = local
            targets[local] = ([unit], self._site_handles_cache.get(unit.name, {}) if unit.identity is None else {})
        for key, results in imports.items():
            entry = import_entries[key]
            for instance_key, instance in results:
                reference = key if instance_key is None else f"{key}.{instance_key}"
                for unit in instance.units:
                    references[unit.name] = reference
                targets[reference] = (instance.units, instance.handles)
            if entry.instances is not None and len(results) == 1:
                targets.setdefault(key, targets[f"{key}.{results[0][0]}"])
        return Level(units=units, references=references, targets=targets, where_verbs=where_verbs)

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
                f"port '{handle.port.name}' (partner {handle.partner_text}) is bound to '{target}', which holds no "
                f"component of that class (it holds {', '.join(unit.name for unit in units)}) {handle.source_text()}.",
                remedy=handle.hint or None,
            )
        raise self.error(
            EnergySystemErrorId.PORT_AMBIGUOUS,
            handle.owner,
            f"port '{handle.port.name}' (partner {handle.partner_text}) is bound to '{target}', which holds "
            f"{len(matching)} components of that class: {', '.join(unit.name for unit in matching)} "
            f"{handle.source_text()}.",
            remedy="Name the partner's provided port instead: bind: {" + handle.port.name + ": " + head + ".<port>}.",
        )

    def _paste_lines(self, handle: Handle, candidates: Sequence[Unit], level: Level, optional: bool) -> str:
        """The paste-ready verb lines for a refusal."""
        references = list(dict.fromkeys(level.references.get(unit.name, unit.name) for unit in candidates))
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
        if handle.decided:
            return
        port = handle.port
        written = verbs.verb_for(port.name)
        if handle.state == PortState.INACTIVE:
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
            handle.record = {"state": PortState.INACTIVE}
            handle.decided = True
            return
        if not port.kind.lowered_in_step_1a or port.kind == PortKind.PROVIDED:
            if written is not None and port.kind == PortKind.PROVIDED:
                raise self.error(
                    EnergySystemErrorId.PORT_CONTRACT,
                    handle.owner,
                    f"port '{port.name}' is a provided output; a verb binds a need, and the need's owner binds to "
                    f"this output ({handle.verb_site} writes '{written[0]}') {handle.source_text()}.",
                )
            handle.record = {"state": PortState.PROVIDED if port.kind == PortKind.PROVIDED else PortState.NOT_LOWERED}
            handle.decided = True
            return
        candidates = self._candidates(handle, level.units)
        handle.hint = (
            f"Candidates: {', '.join(unit.name for unit in candidates) or 'none'}; "
            + self._paste_lines(handle, candidates, level, optional=handle.state == PortState.OPTIONAL)
            + "."
        )
        if written is not None:
            verb, target = written
            if verb == "none":
                if handle.state == PortState.REQUIRED:
                    raise self.error(
                        EnergySystemErrorId.REQUIRED_PORT_DECLINED,
                        handle.owner,
                        f"port '{port.name}' (partner {handle.partner_text}) is required, yet {handle.verb_site} "
                        "declines it with 'none:'; candidates: "
                        f"{', '.join(unit.name for unit in candidates) or 'none'} "
                        f"{handle.source_text()}.",
                        remedy=self._paste_lines(handle, candidates, level, optional=False)
                        + "; a required port cannot be declined.",
                    )
                handle.record = {"state": PortState.DECLINED, "verb": "none"}
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
                    f"port '{port.name}' (partner {handle.partner_text}) is bound to '{target}', but {absent}; "
                    f"candidates: {', '.join(unit.name for unit in candidates) or 'none'} {handle.source_text()}.",
                    remedy=self._paste_lines(handle, candidates, level, optional=handle.state == PortState.OPTIONAL)
                    + (
                        " (optional-bind: binds only when the partner exists)"
                        if handle.state == PortState.OPTIONAL
                        else ""
                    ),
                )
            self._lower(handle, partner, provider, verb)
            return
        if len(candidates) == 1 and handle.state == PortState.REQUIRED:
            self._lower(handle, candidates[0], None, "default")
            return
        if len(candidates) > 1:
            raise self.error(
                EnergySystemErrorId.PORT_AMBIGUOUS,
                handle.owner,
                f"port '{port.name}' (partner {handle.partner_text}) has {len(candidates)} candidates "
                f"{', '.join(unit.name for unit in candidates)} and no verb {handle.source_text()}.",
                remedy=self._paste_lines(handle, candidates, level, optional=handle.state == PortState.OPTIONAL),
            )
        if handle.state == PortState.OPTIONAL and candidates:
            raise self.error(
                EnergySystemErrorId.OPTIONAL_PORT_UNDECIDED,
                handle.owner,
                f"optional port '{port.name}' (partner {handle.partner_text}) has 1 candidate {candidates[0].name} "
                f"and no verb {handle.source_text()}.",
                remedy=self._paste_lines(handle, candidates, level, optional=True),
            )
        if not top:
            raise self.error(
                EnergySystemErrorId.INNER_PORT_UNRESOLVED,
                handle.owner,
                f"{handle.state.value} port '{port.name}' (partner {handle.partner_text}) of the inner import is "
                "neither bound "
                f"nor re-exported inside {level.where_verbs}; candidates: none {handle.source_text()}.",
                remedy=(
                    f"Re-export it from the importing assembly's interface (`{port.name}: {{from: "
                    f"{handle.owner_path.split(' → ')[-1].split('[')[0]}.{port.name}}}`), bind it with "
                    + self._paste_lines(handle, candidates, level, optional=handle.state == PortState.OPTIONAL).replace(
                        "add to ", "a line on "
                    )
                    + ", or decline it."
                ),
            )
        if handle.state == PortState.OPTIONAL:
            handle.record = {"state": PortState.OPTIONAL, "partner": "not bound: no candidate"}
            handle.decided = True
            return
        raise self.error(
            EnergySystemErrorId.PORT_WITHOUT_PARTNER,
            handle.owner,
            f"required port '{port.name}' (partner {handle.partner_text}) has no partner in "
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
        bound_output = provider[1] if provider is not None else ""
        lowered: List[str] = []
        for landing in handle.landings:
            wires = landing.placeholder.wires if landing.placeholder.wires is not None else port.wires
            if wires is not None:
                if bound_output and bound_output not in wires.values():
                    raise self.error(
                        EnergySystemErrorId.PORT_CONTRACT,
                        handle.owner,
                        f"port '{port.name}' is bound to the provided output '{bound_output}' of {partner.name}, but "
                        f"its wires into {landing.unit.name} read {', '.join(sorted(set(wires.values())))} "
                        f"{handle.source_text()}.",
                        remedy=f"Name '{bound_output}' in the wires, or bind the port to the partner itself.",
                    )
                items: List[AnyInputItem] = [
                    ExplicitWire(source=partner.name, input=target, output=output) for target, output in wires.items()
                ]
                for target, output in wires.items():
                    self._note_lowered(handle, LoweredKind.WIRE, landing.unit, verb, partner, target, output)
            else:
                self._note_lowered(
                    handle, LoweredKind.DEFAULT, landing.unit, verb, partner, bound_output=bound_output
                )
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

    @staticmethod
    def _item_text(item: AnyInputItem) -> str:
        """An input item as the record lists it."""
        if isinstance(item, ExplicitWire):
            return f"{{input: {item.input}, from: {item.source}.{item.output}}}"
        return item.source

    def _note_lowered(
        self,
        handle: Handle,
        kind: str,
        member: Unit,
        verb: str,
        partner: Optional[Unit] = None,
        target: str = "",
        output: str = "",
        *,
        bound_output: str = "",
    ) -> None:
        """Writes one lowered item into the port-provenance table, which a wiring refusal of it reads."""
        self.record.port_provenance.append(
            LoweredPort(
                kind=kind,
                owner=handle.owner,
                import_path=handle.owner_path,
                port=handle.port.name,
                verb=verb,
                member=member.name,
                member_class=member.class_path,
                partner=partner.name if partner is not None else "",
                partner_class=partner.class_path if partner is not None else "",
                input=target,
                output=output,
                chain=tuple(location.text for location in handle.chain),
                bound_output=bound_output,
            )
        )

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
        for key, results in inner.items():
            entry = model.imports[key]
            for _instance_key, instance in results:
                self._check_verbs_name_ports(entry.verbs, instance.handles, f"{assembly.label}: imports.{key}")
                for handle in instance.handles.values():
                    if handle.decided:
                        continue
                    handle.verb_site = f"the inner import '{key}' in {assembly.label}"
                    self._decide(handle, entry.verbs, level, top=False)

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
                sender_handle.record = {"state": PortState.INTERNAL, "partner": f"{receiver} (internal {name})"}
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
            if receiver_handle.state == PortState.INACTIVE:
                raise self.error(
                    EnergySystemErrorId.VERB_ON_INACTIVE_PORT,
                    location,
                    f"the internal port '{name}' binds '{receiver}', which is inactive with these parameters "
                    f"{receiver_handle.source_text()}.",
                )
            self._lower(receiver_handle, partner, None, f"internal {name}")
            receiver_handle.record["state"] = PortState.INTERNAL
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
            state=PortState.REQUIRED,
            landings=[
                Landing(unit=member, position=position, placeholder=placeholder) for position, placeholder in positions
            ],
            provider=None,
            own_units=frozenset(),
            chain=(assembly.lines.location("interface", "internal", name),),
        )
        self._lower(handle, partner, None, f"internal {name}")
        sender_handle.decided = True
        sender_handle.record = {"state": PortState.INTERNAL, "partner": f"{receiver} (internal {name})"}

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
                    (port.kind in (PortKind.NEED, PortKind.FACT) and member in port.into)
                    or (port.kind == PortKind.INTERNAL and port.ends and port.ends[1] == member)
                    or port.kind in (PortKind.CIRCUIT, PortKind.CARRIER)
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
            state=record.get("state", handle.state),
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
            location = unit.lines.location(*unit.block_path, "sizing_sources", fact)
            source_map.add(
                SourceMapEntry(
                    unit.name, f"sizing_sources.{fact}", unit.import_path, unit.entry.name, prefix + (location,)
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
