"""Expansion of imports: turning a file that imports assemblies into one flat energy-system file.

``assemblies_spec.md`` §2.3, lean v1 (§13.1). One more pure stage in front of the group expansion: it
builds a new file and never mutates its input, it is idempotent, and a file of schema version 3 —
every committed file — comes back as the very same object. For a file of version 4 it

1. resolves every imported assembly (:mod:`.resolver`), and the assembly rules run the full library
   check on it (``hisim/setup_rules/assemblies.py``);
2. checks each import's (or instance's) parameters and selects its internal variants
   (:mod:`.parameters`);
3. gives every member its structured address and substitutes its parameters (:mod:`.addresses`);
4. binds every port of every import and site entry and lowers it to bare names, wires and sizing
   lines, and hands the wiring the consuming outputs and the observers to select for
   (:mod:`.binding`, :mod:`.selection`);
5. writes the flat file in the sequence a flat ``order:`` sets (:func:`.addresses.sequence`) — the
   entries and imports carrying one ascending, then the others in file order, site entries first,
   each import one block — and the import record with that sequence and its source map (:mod:`.record`).

Each step records what it cannot do (:class:`~hisim.energy_system.errors.ImportRefusal`) and goes on
with the next import, parameter or port; the assembly rules judge the records at two points, the
assemblies found (``ImportedAssemblies``) and the rest (``ExpandedImports``), and refuse every fault
at once as :class:`~hisim.energy_system.errors.EnergySystemImportsError`. A step that recorded a
refusal is the last one run: the next would read what the refusal left undecided.

Everything downstream sees ordinary components; whether the constructed components accept the
lowered items the wiring stage checks, like every other connection.
"""

from __future__ import annotations

import dataclasses
from typing import Dict, List, Mapping, Optional, Sequence, Tuple, Union

from hisim.config import AddressStep
from hisim.energy_system.address_table import AddressTable
from hisim.energy_system.assemblies.addresses import Block, Unit, final_entry, member_units, sequence, site_unit
from hisim.energy_system.assemblies.binding import Owner, PortBinder
from hisim.energy_system.assemblies.parameters import SITE, resolve_selection
from hisim.energy_system.assemblies.record import ImportRecord, InstanceRecord
from hisim.energy_system.assemblies.resolver import AssemblyResolver, ResolvedAssembly
from hisim.energy_system.errors import EnergySystemAssemblyError, EnergySystemErrorId, ImportRefusal
from hisim.energy_system.assemblies.selection import Observer
from hisim.energy_system.groups import enabled_component_names
from hisim.energy_system.imports_model import BindingVerbs, InstanceEntry, PortKind
from hisim.energy_system.model import ComponentEntry, EnergySystemFile, Group, VariantOption
from hisim.energy_system.source_lines import LineIndex
from hisim.setup_rules.registry import SetupRuleRegistry
from hisim.setup_rules.rule import RuleStage
from hisim.setup_rules.subjects import ExpandedImports, ImportedAssemblies


class ImportExpander:
    """Expands the imports of one energy-system file. Single use; :func:`expand_imports` drives it."""

    def __init__(self, model: EnergySystemFile, resolver: AssemblyResolver, lines: LineIndex, file: Optional[str] = None) -> None:
        """Prepares the expansion of one file.

        Args:
            model: The file as read.
            resolver: Finds the assemblies its imports name.
            lines: The file's line index, for the source map.
            file: The file's path, which the faults of the assembly rules carry; ``None`` for a file read from text.
        """
        self.model = model
        self.resolver = resolver
        self.lines = lines
        self.file = file
        self.record = ImportRecord()
        #: What the expansion could not do, in the order it was found; the assembly rules refuse it.
        self.refusals: List[ImportRefusal] = []

    def expand(self) -> Tuple[EnergySystemFile, ImportRecord]:
        """Expands the file.

        Returns:
            The flat file and the import record.

        Raises:
            EnergySystemImportsError: Listing every fault the assembly rules found in what the expansion recorded:
                first every assembly found nowhere or twice and every library check that fails, then every
                parameter, constraint or member of an import that does not fit, or every port that cannot be bound,
                or every two blocks of one evaluation order.
            EnergySystemAssemblyError: ``EF-52`` for an import named like a component, a group or a variant.
        """
        self._check_names()
        assemblies = self._find_assemblies()
        self.judge(ImportedAssemblies(self.model, assemblies, tuple(self.refusals), self.file))
        sites: Dict[str, Owner] = {}
        blocks: List[Block] = []
        for name, site_entry in self.model.components.items():
            unit = site_unit(name, site_entry, self.lines)
            location = self.lines.location("components", name).text
            blocks.append(Block(f"component '{name}'", location, site_entry.order, [unit], ("components", name)))
            sites[name] = self._site_owner(unit)
        absent = self._switched_components(sites)
        imports: Dict[str, List[Owner]] = {}
        for key, entry in self.model.imports.items():
            members = Block(f"import '{key}'", self.lines.location("imports", key).text, entry.order, [], ("imports", key))
            blocks.append(members)
            assembly = assemblies[key]
            instances: Sequence[Tuple[Optional[str], Optional[InstanceEntry]]] = (
                tuple(entry.instances.items()) if entry.instances is not None else ((None, None),)
            )
            for instance_key, instance in instances:
                given = instance.parameters if instance is not None else entry.parameters
                written = instance if instance is not None else entry
                reserved = written.model_dump(include={"installation_year", "quote"}, exclude_none=True)
                block = ("imports", key) + (("instances", instance_key) if instance_key is not None else ())
                origin = self.lines.location(*block)
                label = f"import '{key}'" + (f" (instance '{instance_key}')" if instance_key is not None else "")
                reference = key if instance_key is None else f"{key}.{instance_key}"
                observers = [port for port in assembly.model.ports.values() if port.kind == PortKind.OBSERVER]
                if entry.observes is not None and len(observers) != 1:
                    self.refusals.append(
                        ImportRefusal(
                            EnergySystemErrorId.PORT_CONTRACT,
                            f"{label}, {origin.text}",
                            f"the import writes 'observes:', which replaces the default of its assembly's one observer "
                            f"port, but '{assembly.path}' has {len(observers)}.",
                            key_path=block,
                            owner=reference,
                        )
                    )
                selection, refused = resolve_selection(assembly.model, assembly.label, given, f"{label}, {origin.text}")
                self.refusals.extend(dataclasses.replace(refusal, key_path=block, owner=reference) for refusal in refused)
                if selection is None:
                    continue
                units, refused = member_units(assembly, selection, AddressStep(key, instance_key), origin)
                self.refusals.extend(dataclasses.replace(refusal, key_path=block) for refusal in refused)
                members.units.extend(units)
                imports.setdefault(key, []).append(
                    Owner(
                        reference=reference,
                        label=f"{label}, {origin.text}",
                        verb_site=f"the import '{key}'",
                        verbs=entry.verbs,
                        units={unit.identity.name: unit for unit in units if unit.identity is not None},
                        ports=assembly.model.ports,
                        states={name: selection.state(port) for name, port in assembly.model.ports.items()},
                        observes=entry.observes,
                        key_path=block,
                    )
                )
                self.record.instances.append(
                    InstanceRecord(
                        import_key=key,
                        instance=instance_key,
                        assembly=assembly.path,
                        file=assembly.label,
                        sha256=assembly.sha256,
                        parameters_given=dict(given),
                        parameters_resolved=dict(selection.resolved),
                        variants=dict(selection.variants),
                        members=tuple(unit.name for unit in units),
                        reserved=reserved,
                    )
                )
        if self.refusals:
            self.judge(self.expanded_imports())
        for name, site_entry in self.model.components.items():
            if site_entry.observes is not None:
                self.record.selection.observers.append(Observer(name, site_entry.observes, name))
        binder = PortBinder(sites, imports, self.record, absent)
        records = binder.bind()
        self.refusals.extend(binder.refusals)
        if self.refusals:
            self.judge(self.expanded_imports())
        for instance_record in self.record.instances:
            reference = instance_record.import_key + (
                f".{instance_record.instance}" if instance_record.instance else ""
            )
            instance_record.ports = records[reference]
        self.record.site_ports = {name: records[name] for name in sites if records[name]}
        ordered, refused = sequence(blocks)
        self.refusals.extend(refused)
        self.judge(self.expanded_imports())
        flat = self._assemble(ordered)
        self.record.sequence = enabled_component_names(flat)
        return flat, self.record

    def expanded_imports(self) -> ExpandedImports:
        """The subject of the assembly rules after the selection, the binding or the sequence: the file and every refusal so far."""
        return ExpandedImports(self.model, tuple(self.refusals), self.file)

    @staticmethod
    def judge(subject: Union[ImportedAssemblies, ExpandedImports]) -> None:
        """Runs the assembly rules on what the expansion found, and refuses every fault they report at once.

        The rules of the stage ``BUILD`` that read this subject run, for the kind the file declares; a subject with
        no refusal and assemblies that pass the library check passes. Example: a file whose imports name two
        assemblies that do not exist raises one ``EnergySystemImportsError`` with two ``EF-71`` faults.

        Args:
            subject: The assemblies found, or the refusals of a later step.

        Raises:
            EnergySystemImportsError: Listing every fault the assembly rules found.
            RuntimeError: If a refusal was recorded that no rule refused, which would let the expansion go on from
                a step it could not finish: a code no assembly rule reads.
        """
        SetupRuleRegistry.collect().run(RuleStage.BUILD, subject, subject.model.setup_kind)
        if subject.refusals:
            raise RuntimeError(
                "the expansion of imports recorded refusals that no assembly rule refused: "
                + " | ".join(refusal.message for refusal in subject.refusals)
            )

    def _find_assemblies(self) -> Dict[str, ResolvedAssembly]:
        """Looks up every import's assembly; one found nowhere or twice is recorded (``EF-71``, ``EF-72``) and left out."""
        assemblies: Dict[str, ResolvedAssembly] = {}
        for key, entry in self.model.imports.items():
            found = self.resolver.find(entry.assembly, f"imports.{key}")
            if isinstance(found, ImportRefusal):
                self.refusals.append(dataclasses.replace(found, key_path=("imports", key), owner=key))
            else:
                assemblies[key] = found
        return assemblies

    def _switched_components(self, sites: Dict[str, Owner]) -> Dict[str, str]:
        """Adds the live group and variant components to ``sites`` as partners; returns the others, with why."""
        groups = self.model.groups.items()
        places: List[Tuple[Tuple[str, ...], bool, str, Union[Group, VariantOption]]] = [
            (("groups", name), group.enabled, f"group {name}", group) for name, group in groups
        ]
        places += [
            (("variants", name, "options", key), key == chosen.selected, f"variant {name} ({chosen.selected})", option)
            for name, chosen in self.model.variants.items()
            for key, option in chosen.options.items()
        ]
        absent: Dict[str, str] = {}
        for path, live, switch, holder in places:
            for name, entry in holder.components.items():
                if not live:
                    absent[name] = f"disabled by {switch}"
                    continue
                unit = site_unit(name, entry, self.lines, path + ("components", name))
                label = f"component '{name}', {unit.chain[0].text}"
                sites[name] = Owner(
                    name, label, f"the component '{name}'", BindingVerbs(), {name: unit}, {}, {}, key_path=path + ("components", name)
                )
        return {name: reason for name, reason in absent.items() if name not in sites}

    def _check_names(self) -> None:
        """Refuses an import key that is also a component, group or variant name: a verb could mean either."""
        for key in self.model.imports:
            if key in self.model.declared_components() or key in self.model.groups or key in self.model.variants:
                raise EnergySystemAssemblyError(
                    EnergySystemErrorId.DUPLICATE_NAME,
                    f"imports.{key}",
                    f"the import '{key}' has the name of a component, a group or a variant of the file; a verb names "
                    "a partner by its bare name.",
                )

    def _site_owner(self, unit: Unit) -> Owner:
        """A site entry as an owner of ports; every need port lands at a placeholder in its own inputs.

        A placeholder for a port the entry does not declare as a need, a circuit end or a fuel it provides, and a
        need or fuel provision without a placeholder, are recorded (``EF-7J``).
        """
        name, entry = unit.name, unit.entry
        for placed in entry.placeholders:
            port = entry.ports.get(placed.placeholder.port)
            if port is None or not (port.kind in (PortKind.NEED, PortKind.CIRCUIT) or port.is_fuel_provision):
                self.refusals.append(
                    ImportRefusal(
                        EnergySystemErrorId.PORT_CONTRACT,
                        f"components.{name}.inputs[{placed.position}]",
                        f"'{name}' carries a placeholder for '{placed.placeholder.port}', which its 'ports' block does not "
                        "declare as a need, a circuit end or a fuel it provides.",
                        alternatives=tuple(entry.ports),
                        alternatives_label="ports",
                        key_path=("components", name, "inputs", placed.position),
                        owner=name,
                    )
                )
        for port_name, port in entry.ports.items():
            if (port.kind == PortKind.NEED or port.is_fuel_provision) and not unit.placeholder_positions(port_name):
                self.refusals.append(
                    ImportRefusal(
                        EnergySystemErrorId.PORT_CONTRACT,
                        f"components.{name}.ports.{port_name}",
                        f"the port '{port_name}' of '{name}' has no '{{$port: {port_name}}}' placeholder in the entry's "
                        "inputs, so its items have nowhere to land.",
                        key_path=("components", name, "ports", port_name),
                        owner=name,
                    )
                )
        return Owner(
            reference=name,
            label=f"component '{name}', {self.lines.location('components', name).text}",
            verb_site=f"the component '{name}'",
            verbs=entry.verbs,
            units={name: unit},
            ports=entry.ports,
            states={port_name: SITE.state(port) for port_name, port in entry.ports.items()},
            key_path=("components", name),
        )

    def _assemble(self, units: List[Unit]) -> EnergySystemFile:
        """Writes the flat file, schema version 3: site entries first, then each import's members."""
        components: Dict[str, ComponentEntry] = {}
        for unit in units:
            components[unit.name] = final_entry(unit, self.record.source_map)
            if unit.identity is not None:
                self.record.addresses[unit.name] = unit.identity
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
    file: Optional[str] = None,
) -> Tuple[EnergySystemFile, ImportRecord]:
    """Expands every import of a file into the flat file it stands for (``assemblies_spec.md`` §2.3).

    The function is pure and idempotent. A file of schema version 3 — every committed file, and every
    file an expansion produced — is returned as the very same object with an empty record, once the
    assembly rules found the sequence its import record states, if it carries one, intact; a file of
    version 4 comes back as a version-3 one.

    Args:
        model: The file as read.
        resolver: Finds the assemblies; this machine's search path when omitted, which is consulted
            only when the file imports something.
        lines: The file's line index, for the source map; an empty one when the file exists only in memory.
        file: The file's path, which the faults of the assembly rules carry; ``None`` for a file read from text.

    Returns:
        The flat file and the import record.

    Raises:
        EnergySystemImportsError: Listing every fault the assembly rules found (the ``EF-7x`` band).
        EnergySystemAssemblyError: ``EF-52`` for an import named like a component, a group or a variant.
    """
    if model.schema_version == EnergySystemFile.SUPPORTED_SCHEMA_VERSION:
        refused = recorded_sequence_refusal(model)
        ImportExpander.judge(ExpandedImports(model, (refused,) if refused is not None else (), file))
        return model, ImportRecord()
    if resolver is None:
        resolver = AssemblyResolver.default() if model.imports else AssemblyResolver(())
    return ImportExpander(model, resolver, lines or LineIndex.empty("<energy system>"), file).expand()


def recorded_sequence_refusal(model: EnergySystemFile) -> Optional[ImportRefusal]:
    """The refusal (``EF-7P``) of a record whose components no longer stand in the sequence its import record states, or ``None``.

    Example: a realized record whose ``metadata.imports.sequence`` lists ``[Weather, Building]`` while its components
    stand as ``Building, Weather`` gets the refusal; a record without an import record, or in its sequence, gets
    ``None``.
    """
    recorded = (model.metadata or {}).get(AddressTable.IMPORTS_KEY)
    stated = recorded.get(ImportRecord.SEQUENCE_KEY) if isinstance(recorded, Mapping) else None
    if stated is None or list(enabled_component_names(model)) == list(stated):
        return None
    return ImportRefusal(
        EnergySystemErrorId.ORDER_INVALID,
        f"metadata.{AddressTable.IMPORTS_KEY}.{ImportRecord.SEQUENCE_KEY}",
        f"the record's components stand in the order {list(enabled_component_names(model))}, not in the sequence "
        f"{list(stated)} its import record states; a re-run would add them in another order.",
        key_path=("metadata", AddressTable.IMPORTS_KEY, ImportRecord.SEQUENCE_KEY),
    )
