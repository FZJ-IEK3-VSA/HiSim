"""``hisim energy-system describe <family>/<name>``: what an assembly offers, in one page (§9.3).

The page answers the questions an author importing the assembly asks: which ports it has, which
partner classes each binds to and in which requirement state; which parameters it takes, with
their units, descriptions, ranges, defaults and allowed values; the constraints across them; the
presets; the members, internal variants and inner imports; and the test contract it carries.
Nothing is decided here: the page is the assembly file read through the same reader the expansion
uses, and nothing is constructed. The page therefore states the interface as the file declares it
and claims nothing about the member classes' default connections, inputs or outputs: those exist
only on constructed components, and a build verifies them (the post-construction port check,
:mod:`hisim.energy_system.assemblies.port_check`), which the page says in one line.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, TextIO, Tuple

from hisim.energy_system.assemblies.model import NO_DEFAULT, AssemblyFile, ParameterDeclaration
from hisim.energy_system.assemblies.reader import AssemblyReader
from hisim.energy_system.assemblies.resolver import AssemblyResolver, ResolvedAssembly
from hisim.energy_system.imports_model import Port, PortKind


class AssemblyDescription:
    """Renders one assembly's description."""

    #: Width of the first column; a longer name is followed by two spaces instead.
    NAME_WIDTH = 24

    #: The line under the ports saying what the page does not know.
    VERIFIED_AT_BUILD = (
        "as declared in the file; the members' default connections from the partner classes, the wired "
        "inputs and outputs and the provided outputs are verified on the constructed components when a "
        "system is built"
    )

    @classmethod
    def _pad(cls, name: str, width: int = NAME_WIDTH) -> str:
        """A name padded to the first column, never touching what follows it."""
        return name.ljust(width) if len(name) < width else f"{name}  "

    @classmethod
    def is_assembly_path(cls, text: str) -> bool:
        """Whether ``describe``'s argument names an assembly rather than a class.

        An assembly is named by a library path, ``<family>/<name>`` (a slash, which no dotted class
        path carries), or by its file, ``….assembly.yaml``.
        """
        return "/" in text or text.endswith(AssemblyReader.SUFFIX)

    @classmethod
    def resolve(cls, text: str, resolver: AssemblyResolver) -> ResolvedAssembly:
        """Resolves the argument: a file read directly, or a library path through the resolver."""
        if text.endswith(AssemblyReader.SUFFIX) and Path(text).is_file():
            path = Path(text)
            import hashlib  # noqa: PLC0415  # only this branch hashes a file of its own

            data = path.read_bytes()
            model, lines = AssemblyReader.read(path)
            return ResolvedAssembly(
                path=model.name or path.name[: -len(AssemblyReader.SUFFIX)],
                file=path,
                sha256=hashlib.sha256(data).hexdigest(),
                model=model,
                lines=lines,
            )
        return resolver.resolve(text, text)

    @classmethod
    def render(cls, assembly: ResolvedAssembly, out: TextIO) -> None:
        """Writes the description."""
        model = assembly.model
        print(f"{assembly.path} — {model.description or '(no description)'}", file=out)
        print(f"  file {assembly.file}, sha256 {assembly.sha256}", file=out)
        cls._interface(model, out)
        cls._parameters(model, out)
        cls._heading("constraints", out)
        for constraint in model.constraints:
            cls._line(constraint.text(), out)
        if not model.constraints:
            cls._line("none", out)
        cls._heading("presets", out)
        for name, values in model.presets.items():
            written = ", ".join(f"{key}={value!r}" for key, value in values.items()) or "the defaults"
            cls._line(f"{cls._pad(name)}{written}", out)
        if not model.presets:
            cls._line("none", out)
        cls._members(model, out)
        cls._heading("inner imports", out)
        for key, entry in model.imports.items():
            verbs = "; ".join(f"{verb} {port}: {partner}" for verb, port, partner in cls._verbs(entry.verbs))
            parameters = ", ".join(f"{name}={value!r}" for name, value in entry.parameters.items())
            details = "; ".join(part for part in (parameters, verbs) if part)
            cls._line(f"{cls._pad(key)}{entry.assembly}" + (f" ({details})" if details else ""), out)
        if not model.imports:
            cls._line("none", out)
        cls._tests(model, out)

    @staticmethod
    def _verbs(verbs: Any) -> List[Tuple[str, str, str]]:
        """The verbs of an import as ``(verb, port, partner)``."""
        written = [("bind", port, partner) for port, partner in verbs.bind.items()]
        written += [("optional-bind", port, partner) for port, partner in verbs.optional_bind.items()]
        written += [("none", port, "") for port in verbs.none]
        return written

    @staticmethod
    def _heading(title: str, out: TextIO) -> None:
        """A section heading."""
        print(f"\n{title}", file=out)

    @staticmethod
    def _line(text: str, out: TextIO) -> None:
        """One indented line."""
        print(f"  {text}", file=out)

    @classmethod
    def state_of(cls, port: Port) -> str:
        """A port's requirement state as the page states it (§3.1)."""
        if port.kind == PortKind.PROVIDED or port.section == "provides":
            state = "provided"
        elif port.required_when:
            state = "required when " + cls._conditions(port.required_when)
        elif port.optional:
            state = "optional (bind:, optional-bind: or none:)"
        else:
            state = "required"
        if port.active_when:
            state += ", active when " + cls._conditions(port.active_when)
        return state

    @staticmethod
    def _conditions(conditions: Any) -> str:
        """``{p: [a, b]}`` as ``p in [a, b]``, conjunctive."""
        return " and ".join(f"{parameter} in {list(values)}" for parameter, values in conditions.items())

    @classmethod
    def _interface(cls, model: AssemblyFile, out: TextIO) -> None:
        """The ports, by section."""
        cls._heading("interface", out)
        if not model.ports:
            cls._line("no ports", out)
        for section in AssemblyFile.INTERFACE_SECTIONS:
            ports = [port for port in model.ports.values() if port.section == section]
            if not ports:
                continue
            cls._line(f"{section}:", out)
            for port in ports:
                cls._line(f"  {cls._pad(port.name, cls.NAME_WIDTH - 2)}{cls._port_text(port)}", out)
        if model.ports:
            cls._line(f"({cls.VERIFIED_AT_BUILD})", out)

    @classmethod
    def _port_text(cls, port: Port) -> str:
        """One port's line."""
        if port.kind == PortKind.NEED:
            text = f"need from {' | '.join(port.partner)} into {', '.join(port.into)}"
            if port.wires is not None:
                text += f", wires {dict(port.wires)}"
        elif port.kind == PortKind.PROVIDED:
            text = f"provides {port.output}"
        elif port.kind == PortKind.REEXPORT:
            text = f"re-exports {port.reexports}"
        elif port.kind == PortKind.INTERNAL:
            return f"internal {port.ends[0]} → {port.ends[1]}"
        else:
            text = f"{port.kind.value} (not lowered in step 1a: {port.kind.delivering_step})"
        return f"{text}; {cls.state_of(port)}"

    @classmethod
    def _parameters(cls, model: AssemblyFile, out: TextIO) -> None:
        """The parameters, one line each."""
        cls._heading("parameters", out)
        for name, declaration in model.parameters.items():
            cls._line(f"{cls._pad(name)}{cls._parameter_text(declaration)}", out)
        if not model.parameters:
            cls._line("none", out)

    @staticmethod
    def _parameter_text(declaration: ParameterDeclaration) -> str:
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

    @classmethod
    def _members(cls, model: AssemblyFile, out: TextIO) -> None:
        """The members and the internal variants."""
        cls._heading("members", out)
        for name, member in model.components.items():
            cls._line(f"{cls._pad(name)}{member.entry.class_path}", out)
        for variant in model.variants.values():
            cls._line(f"variant {variant.name}, selected by {variant.selected_by}:", out)
            for option in variant.options.values():
                members = ", ".join(option.components) or "nothing"
                cls._line(f"  {option.name} when {list(option.when)}: {members}", out)

    @classmethod
    def _tests(cls, model: AssemblyFile, out: TextIO) -> None:
        """The test contract (§9.4)."""
        tests = model.tests
        if tests is None:
            cls._heading("test contract: none (the library check refuses an assembly without one)", out)
            return
        cls._heading(
            f"test contract: {len(tests.bounds)} bounds, {len(tests.monotone)} monotone, {len(tests.expect)} expect",
            out,
        )
        for bounds in tests.bounds:
            unit = f" [{bounds.unit}]" if bounds.unit else ""
            cls._line(f"bounds    {bounds.subject}{unit}: {cls._band(bounds.min, bounds.max)}", out)
        for monotone in tests.monotone:
            cls._line(
                f"monotone  {monotone.parameter} rises: {monotone.kpi} of {monotone.member} {monotone.direction.value}",
                out,
            )
        for expect in tests.expect:
            cls._line(
                f"expect    preset {expect.preset}: {expect.kpi} of {expect.member} "
                f"{cls._band(expect.min, expect.max)}",
                out,
            )

    @staticmethod
    def _band(low: Any, high: Any) -> str:
        """A band, either side open."""
        return f"{'-inf' if low is None else f'{low:g}'} … {'inf' if high is None else f'{high:g}'}"
