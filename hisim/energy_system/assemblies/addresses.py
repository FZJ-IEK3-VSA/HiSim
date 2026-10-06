"""Every member's structured address and its entry, substituted, rewritten and lowered (§2.4, §9.2).

One import instance's members become **units**: entries with the parameters substituted (``{$param:
…}`` in config values and constructor arguments, the preset a ``{$param}`` names), the structured
address ``ComponentID(name, path=(AddressStep(import, instance),), assembly, display_name)`` whose
serialization ``pv-east-PVSystem`` is the component's name in the flat file, and their place in the
file's order: members in their assembly's written order. A site entry is a unit too, without an
address. Once the ports are bound (:mod:`.binding`), :func:`final_entry` writes each unit's entry as
the flat file holds it — references between members rewritten to the serialized names, placeholders
replaced by what their ports lowered to — and its source-map entries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from hisim.config import AddressStep, ComponentID
from hisim.energy_system.assemblies.parameters import Selection
from hisim.energy_system.assemblies.record import SourceMap, SourceMapEntry
from hisim.energy_system.assemblies.resolver import ResolvedAssembly
from hisim.energy_system.errors import EnergySystemAssemblyError, EnergySystemErrorId
from hisim.energy_system.imports_model import BindingVerbs, ParameterReference, PortPlaceholder
from hisim.energy_system.model import AnyInputItem, ComponentEntry, SourceReference
from hisim.energy_system.source_lines import LineIndex, SourceLocation


@dataclass
class Unit:
    """One component of the expanded system while it is being built: an assembly member or a site entry.

    Attributes:
        name: Its expanded name (a site entry's own name).
        entry: Its entry, parameters substituted, local names not yet rewritten.
        identity: Its structured address, ``None`` for a site entry.
        chain: The import's location (empty for a site entry), then the entry's own.
        local_names: Local member name to expanded name, for rewriting its references.
        dropped: Local names of members its assembly's selected variants leave out.
        lines: The line index of the file it was written in.
        block_path: The key path of its block in that file.
        lowered: Placeholder position to the items its port lowered to.
        notes: Placeholder position to what produced those items.
    """

    name: str
    entry: ComponentEntry
    identity: Optional[ComponentID]
    chain: Tuple[SourceLocation, ...]
    local_names: Dict[str, str]
    dropped: Tuple[str, ...]
    lines: LineIndex
    block_path: Tuple[Any, ...]
    lowered: Dict[int, List[AnyInputItem]] = field(default_factory=dict)
    notes: Dict[int, str] = field(default_factory=dict)

    @property
    def class_name(self) -> str:
        """The class's short name, which a port's partner names."""
        return self.entry.class_path.rsplit(".", 1)[-1]

    def placeholder_positions(self, port: str) -> List[int]:
        """The positions of the ``{$port: <port>}`` placeholders in the written input list."""
        return [
            placed.position
            for placed in self.entry.placeholders
            if isinstance(placed.placeholder, PortPlaceholder) and placed.placeholder.port == port
        ]

    def source(self, item: Tuple[Any, ...], note: str = "") -> SourceMapEntry:
        """The source-map entry of one of its items, at a key path below its block."""
        step = self.identity.path[0] if self.identity is not None else None
        return SourceMapEntry(
            import_key=step.import_key if step is not None else "",
            instance=step.instance if step is not None else None,
            member=self.identity.name if self.identity is not None else self.name,
            chain=self.chain[:-1] + (self.lines.location(*self.block_path, *item),),
            note=note,
        )


def site_unit(name: str, entry: ComponentEntry, lines: LineIndex) -> Unit:
    """A site entry as a unit."""
    return Unit(name, entry, None, (lines.location("components", name),), {}, (), lines, ("components", name))


def member_units(
    assembly: ResolvedAssembly, selection: Selection, step: AddressStep, origin: SourceLocation
) -> List[Unit]:
    """The members of one import instance, in their assembly's written order.

    Args:
        assembly: The resolved assembly.
        selection: The instance's resolved parameters and selected members.
        step: The instance's address step.
        origin: Where the import (or instance) is written in the energy-system file.

    Returns:
        One unit per selected member.

    Raises:
        EnergySystemAssemblyError: ``EF-76`` when a member's preset parameter does not resolve to a
            name or its display template does not render with the resolved values.
    """
    local_names = {name: ComponentID(name=name, path=(step,)).address for name in selection.members}
    units: List[Unit] = []
    for name, template in selection.members.items():
        location = f"{assembly.label}: {'.'.join(template.source_path)}"
        entry = template.entry
        preset = entry.preset
        if template.preset_parameter is not None:
            preset = selection.resolved[template.preset_parameter]
            if not isinstance(preset, str):
                raise EnergySystemAssemblyError(
                    EnergySystemErrorId.PARAMETER_INVALID,
                    location,
                    f"'{name}' takes its preset from '{template.preset_parameter}', which resolves to {preset!r}.",
                )
        display = None
        if template.display is not None:
            try:
                display = template.display.format(**selection.resolved)
            except (TypeError, ValueError) as error:
                raise EnergySystemAssemblyError(
                    EnergySystemErrorId.PARAMETER_INVALID,
                    f"{location}.display",
                    f"the display template '{template.display}' does not render with these parameters: {error}.",
                ) from error
        constructor = entry.constructor
        if constructor is not None:
            arguments = ParameterReference.substitute(dict(constructor.arguments), selection.resolved)
            constructor = constructor.model_copy(update={"arguments": arguments})
        identity = ComponentID(name=name, path=(step,), assembly=assembly.path, display_name=display)
        substituted = entry.model_copy(
            update={
                "config": ParameterReference.substitute(dict(entry.config), selection.resolved),
                "constructor": constructor,
                "preset": preset,
            }
        )
        units.append(
            Unit(
                name=identity.address,
                entry=substituted,
                identity=identity,
                chain=(origin, assembly.lines.location(*template.source_path)),
                local_names=local_names,
                dropped=selection.dropped,
                lines=assembly.lines,
                block_path=template.source_path,
            )
        )
    return sorted(units, key=lambda unit: unit.lines.location(*unit.block_path).line)


def final_entry(unit: Unit, source_map: SourceMap) -> ComponentEntry:
    """The unit's entry as the flat file writes it, with its source-map entries.

    A member's references to other members are rewritten to their serialized names; a reference to
    a member its selected variants leave out is dropped with it, as a disabled group's are. Every
    placeholder is replaced by the items its port lowered to, at its written position.

    Raises:
        EnergySystemAssemblyError: ``EF-42`` for a sizing source naming a member the variants left out.
    """
    entry = unit.entry
    placed = {placed.position for placed in entry.placeholders}
    ordinary = iter(entry.inputs)
    inputs: List[AnyInputItem] = []
    for position in range(len(entry.inputs) + len(entry.placeholders)):
        if position in placed:
            for item in unit.lowered.get(position, []):
                source_map.add(
                    unit.name, f"inputs[{len(inputs)}]", unit.source(("inputs", position), unit.notes[position])
                )
                inputs.append(item)
            continue
        item = next(ordinary)
        if unit.identity is not None:
            if item.source in unit.dropped:
                continue
            source_map.add(unit.name, f"inputs[{len(inputs)}]", unit.source(("inputs", position)))
            item = item.model_copy(update={"source": unit.local_names[item.source]})
        inputs.append(item)
    sizing: Dict[str, Any] = dict(entry.sizing_sources)
    if unit.identity is not None:
        source_map.add(unit.name, "component", unit.source(()))
        for fact, value in entry.sizing_sources.items():
            sizing[fact] = _rewrite_sizing(unit, fact, value)
            source_map.add(unit.name, f"sizing_sources.{fact}", unit.source(("sizing_sources", fact)))
        for key in entry.config:
            source_map.add(unit.name, f"config.{key}", unit.source(("config", key)))
    return entry.model_copy(
        update={
            "name": unit.name,
            "inputs": tuple(inputs),
            "sizing_sources": sizing,
            "ports": {},
            "verbs": BindingVerbs(),
            "placeholders": (),
        }
    )


def _rewrite_sizing(unit: Unit, fact: str, value: Any) -> Any:
    """Rewrites one ``sizing_sources`` line of a member to the serialized names."""
    references = value if isinstance(value, tuple) else (value,)
    rewritten: List[SourceReference] = []
    for reference in references:
        if reference.component in unit.dropped:
            raise EnergySystemAssemblyError(
                EnergySystemErrorId.DISABLED_SIZING_SOURCE,
                f"{unit.lines.origin}: {'.'.join(map(str, unit.block_path))}.sizing_sources.{fact}",
                f"'{unit.entry.name}' takes '{fact}' from '{reference.component}', which the selected internal "
                "variants leave out.",
            )
        rewritten.append(reference.model_copy(update={"component": unit.local_names[reference.component]}))
    return tuple(rewritten) if isinstance(value, tuple) else rewritten[0]
