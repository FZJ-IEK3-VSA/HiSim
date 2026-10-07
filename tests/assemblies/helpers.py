"""Shared helpers of the assemblies tests: the mock library, inline files and expansion in one call."""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import ClassVar, Optional, Tuple

from hisim.energy_system.assemblies.expansion import expand_imports
from hisim.energy_system.assemblies.record import ImportRecord
from hisim.energy_system.assemblies.resolver import AssemblyResolver
from hisim.energy_system.document import RawDocument
from hisim.energy_system.executor import BuiltEnergySystem, EnergySystemExecutor
from hisim.energy_system.loader import EnergySystemReader
from hisim.energy_system.model import EnergySystemFile
from hisim.energy_system.source_lines import LineIndex
from hisim.simulationparameters import SimulationParameters


class Mocks:
    """Where the mock files live."""

    ROOT: ClassVar[Path] = Path(__file__).resolve().parent / "mock_assemblies"
    LIBRARY: ClassVar[Path] = ROOT / "library"
    HOUSE: ClassVar[Path] = ROOT / "systems" / "house.energy_system.yaml"
    PARAMETERS: ClassVar[Path] = ROOT / "one_day_kpis.simulation.yaml"

    #: Dotted prefix of the mock component classes.
    CLASSES: ClassVar[str] = "tests.assemblies.mock_components"


#: The test contract of an inline assembly without a numeric parameter.
EMPTY_CONTRACT = "tests: {bounds: [], monotone: []}\n"


class Library:
    """A temporary assembly library for the files one test needs, searched after the mock library."""

    def __init__(self, directory: Path) -> None:
        """Prepares an empty library in a test's temporary directory."""
        self.directory = directory / "library"
        self.directory.mkdir(parents=True, exist_ok=True)

    def add(self, library_path: str, text: str) -> Path:
        """Writes one assembly file, dedenting the text."""
        target = self.directory / f"{library_path}.assembly.yaml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")
        return target

    def resolver(self) -> AssemblyResolver:
        """A resolver over the mock library, then this one."""
        return AssemblyResolver([Mocks.LIBRARY, self.directory])


def read_system(text: str, origin: str = "inline.energy_system.yaml") -> Tuple[EnergySystemFile, LineIndex]:
    """Reads an inline energy-system file, dedenting it, with its line index."""
    dedented = textwrap.dedent(text).lstrip()
    return EnergySystemReader.build(RawDocument.parse_text(dedented, origin), origin), LineIndex.from_text(
        dedented, origin
    )


def expand_text(text: str, resolver: Optional[AssemblyResolver] = None) -> Tuple[EnergySystemFile, ImportRecord]:
    """Reads and expands an inline energy-system file against the mock library (or a given one)."""
    model, lines = read_system(text)
    return expand_imports(model, resolver or AssemblyResolver([Mocks.LIBRARY]), lines=lines)


def site(*entries: str, imports: str = "") -> str:
    """A version-4 file with the given site entries (each an indented YAML block) and imports block."""
    body = "\n".join(textwrap.indent(textwrap.dedent(entry).strip(), "  ") for entry in entries)
    text = f"schema_version: 4\nname: inline\ncomponents:\n{body}\n"
    if imports:
        text += "imports:\n" + textwrap.indent(textwrap.dedent(imports).strip(), "  ") + "\n"
    return text


WEATHER = f"Weather: {{class: {Mocks.CLASSES}.MockWeather, preset: standard}}"
OCCUPANCY = f"Occupancy: {{class: {Mocks.CLASSES}.MockOccupancy, preset: standard}}"
EMS = f"Ems: {{class: {Mocks.CLASSES}.MockEms, preset: standard}}"


def build_text(text: str, result_directory: Path, resolver: Optional[AssemblyResolver] = None) -> BuiltEnergySystem:
    """Reads an inline file and builds it — expansion, sizing, construction and wiring — without running it."""
    model, lines = read_system(text)
    parameters = SimulationParameters.one_day_only(2021, 900)
    parameters.result_directory = str(result_directory)
    return EnergySystemExecutor(
        model, parameters, assembly_resolver=resolver or AssemblyResolver([Mocks.LIBRARY]), source_lines=lines
    ).build()


def system_text(name: str) -> str:
    """The text of one committed mock system."""
    return (Mocks.ROOT / "systems" / name).read_text(encoding="utf-8")


MOCKS = Mocks.CLASSES
