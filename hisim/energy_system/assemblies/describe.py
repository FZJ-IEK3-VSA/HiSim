"""``hisim energy-system describe <family>/<name>``: what an assembly offers, in one page (§9.3).

The page answers what an author importing the assembly asks: its ports, the partner classes each
binds to and in which requirement state; its parameters with units, ranges, defaults, allowed values
and descriptions; the constraints; the members and internal variants; and the test contract. It is
the assembly file read through the reader the expansion uses; nothing is constructed, so the page
states the interface as the file declares it, and says that the members' default connections, inputs
and outputs are checked when a system is built.
"""

from __future__ import annotations

from typing import Any, Mapping, TextIO, Tuple

from hisim.energy_system.assemblies.model import NO_DEFAULT, ParameterDeclaration
from hisim.energy_system.assemblies.resolver import ResolvedAssembly
from hisim.energy_system.imports_model import Port, PortKind


class AssemblyDescription:
    """Renders one assembly's description."""

    #: The width of the name column.
    NAME_WIDTH = 24

    @classmethod
    def is_assembly_path(cls, text: str) -> bool:
        """Whether ``describe``'s argument names an assembly (``<family>/<name>``) rather than a dotted class."""
        return "/" in text

    @classmethod
    def render(cls, assembly: ResolvedAssembly, out: TextIO) -> None:
        """Writes the description."""
        model = assembly.model
        print(f"{assembly.path} — {model.description or '(no description)'}", file=out)
        print(f"  file {assembly.file}, sha256 {assembly.sha256}", file=out)
        print("\ninterface", file=out)
        for port in model.ports.values():
            cls._line(f"{port.section}: {port.name}", cls._port(port), out)
        if not model.ports:
            print("  no ports", file=out)
        print("  (the members' default connections, inputs and outputs are checked when a system is built)", file=out)
        print("\nparameters", file=out)
        for name, declaration in model.parameters.items():
            cls._line(name, cls._parameter(declaration), out)
        print("\nconstraints", file=out)
        for names in model.exactly_one_of:
            print(f"  exactly_one_of: [{', '.join(names)}]", file=out)
        if not model.exactly_one_of:
            print("  none", file=out)
        print("\nmembers", file=out)
        for name, member in model.components.items():
            cls._line(name, member.entry.class_path, out)
        for variant in model.variants.values():
            print(f"  variant {variant.name}, selected by {variant.selected_by}:", file=out)
            for option in variant.options.values():
                members = ", ".join(f"{name} ({member.entry.class_path})" for name, member in option.components.items())
                print(f"    {option.name} when {list(option.when)}: {members or 'nothing'}", file=out)
        tests = model.tests
        if tests is None:
            print("\ntest contract: none (the library check refuses an assembly without one)", file=out)
            return
        print(
            f"\ntest contract: {len(tests.bounds)} bounds, {len(tests.monotone)} monotone, {len(tests.expect)} expect",
            file=out,
        )
        for bounds in tests.bounds:
            subject = bounds.output or f"{bounds.kpi} of {bounds.member or 'the building (derived)'}"
            unit = f" [{bounds.unit}]" if bounds.unit else ""
            print(f"  bounds    {subject}{unit}: {cls._band(bounds.min, bounds.max)}", file=out)
        for monotone in tests.monotone:
            subject = f"{monotone.kpi} of {monotone.member or 'the building (derived)'}"
            print(f"  monotone  {monotone.parameter} rises: {subject} {monotone.direction.value}", file=out)
        for expect in tests.expect:
            subject = f"{expect.kpi} of {expect.member or 'the building (derived)'}"
            print(f"  expect    at the defaults: {subject} {cls._band(expect.min, expect.max)}", file=out)

    @classmethod
    def _line(cls, name: str, text: str, out: TextIO) -> None:
        """One line: a padded name and its text."""
        print(f"  {name.ljust(cls.NAME_WIDTH) if len(name) < cls.NAME_WIDTH else name + '  '}{text}", file=out)

    @classmethod
    def _port(cls, port: Port) -> str:
        """One port's line: what it is and its requirement state (§3.1)."""
        if port.kind == PortKind.NEED:
            text = f"need from {' | '.join(port.partner)} into {', '.join(port.into)}"
            text += f", wires {dict(port.wires)}" if port.wires is not None else ""
        elif port.kind == PortKind.PROVIDED:
            controllable = ", ".join(f"{key} {value}" for key, value in port.controllable.items())
            text = f"provides {port.output}" + (f" (controllable: {controllable})" if controllable else "")
        elif port.kind == PortKind.CIRCUIT:
            text = f"circuit {port.circuit} end at {', '.join(port.members)}"
        elif port.kind == PortKind.CARRIER:
            text = f"carrier {port.carrier}: " + (
                f"consumes {', '.join(port.outputs)}"
                if port.outputs
                else f"provided, metered by {port.meter or 'none'}"
            )
        elif port.kind == PortKind.FACT:
            text = f"fact {port.fact} " + (
                f"from {port.members[0]}"
                if port.is_provision
                else f"into {', '.join(port.into)}" + (", many" if port.many else "")
            )
        else:
            text = f"observes {port.selection.text() if port.selection else ''} into {', '.join(port.into)}"
        if port.is_provision:
            state = "provided"
        elif port.required_when:
            state = "required when " + cls._conditions(port.required_when)
        else:
            state = "optional (bind:, optional-bind: or none:)" if port.optional else "required"
        if port.active_when:
            state += ", active when " + cls._conditions(port.active_when)
        return f"{text}; {state}"

    @staticmethod
    def _conditions(conditions: Mapping[str, Tuple[Any, ...]]) -> str:
        """``{p: [a, b]}`` as ``p in [a, b]``, conjunctive."""
        return " and ".join(f"{parameter} in {list(values)}" for parameter, values in conditions.items())

    @staticmethod
    def _parameter(declaration: ParameterDeclaration) -> str:
        """One parameter's line."""
        parts = [declaration.type.value]
        if declaration.unit is not None:
            parts.append(declaration.unit)
        if declaration.range is not None:
            parts.append(f"range [{declaration.range[0]:g}, {declaration.range[1]:g}]")
        if declaration.values is not None:
            parts.append("values " + ", ".join(str(value) for value in declaration.values))
        parts.append("no default" if declaration.default is NO_DEFAULT else f"default {declaration.default!r}")
        return f"{'  '.join(parts)} — {declaration.description or '(no description)'}"

    @staticmethod
    def _band(low: Any, high: Any) -> str:
        """A band, either side open."""
        return f"{'-inf' if low is None else f'{low:g}'} … {'inf' if high is None else f'{high:g}'}"
