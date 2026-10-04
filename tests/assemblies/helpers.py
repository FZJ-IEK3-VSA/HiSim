"""Shared helpers of the assemblies tests: the mock library, inline files and expansion in one call."""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import ClassVar, List, Optional, Tuple

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
    SYSTEMS: ClassVar[Path] = ROOT / "systems"
    HOUSE: ClassVar[Path] = SYSTEMS / "house.energy_system.yaml"
    PARAMETERS: ClassVar[Path] = ROOT / "one_day_kpis.simulation.yaml"
    SPEC_MOCKUP: ClassVar[Path] = Path(__file__).resolve().parent / "spec_mockup_snapshot"

    #: Dotted prefix of the mock component classes.
    MOCKS: ClassVar[str] = "tests.assemblies.mock_components"


class Library:
    """A temporary assembly library beside the mock library, for the files one test needs."""

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

    def resolver(self, *, with_mocks: bool = True) -> AssemblyResolver:
        """A resolver over this library, after the mock library when asked."""
        directories: List[Path] = [Mocks.LIBRARY] if with_mocks else []
        directories.append(self.directory)
        return AssemblyResolver(directories)


def mock_resolver() -> AssemblyResolver:
    """A resolver over the mock library alone."""
    return AssemblyResolver([Mocks.LIBRARY])


def read_system(text: str, origin: str = "inline.energy_system.yaml") -> Tuple[EnergySystemFile, LineIndex]:
    """Reads an inline energy-system file, dedenting it, with its line index."""
    dedented = textwrap.dedent(text).lstrip()
    model = EnergySystemReader.build(RawDocument.parse_text(dedented, origin), origin)
    return model, LineIndex.from_text(dedented, origin)


def expand_text(
    text: str, resolver: Optional[AssemblyResolver] = None, origin: str = "inline.energy_system.yaml"
) -> Tuple[EnergySystemFile, ImportRecord]:
    """Reads and expands an inline energy-system file against the mock library (or a given one)."""
    model, lines = read_system(text, origin)
    return expand_imports(model, resolver or mock_resolver(), lines=lines)


def build_text(text: str, result_directory: Path, resolver: Optional[AssemblyResolver] = None) -> BuiltEnergySystem:
    """Reads, expands and builds an inline energy-system file (one day at 900 s), constructing every component."""
    model, lines = read_system(text)
    parameters = SimulationParameters.one_day_only(2021, 900)
    parameters.result_directory = str(result_directory)
    return EnergySystemExecutor(
        model, parameters, assembly_resolver=resolver or mock_resolver(), source_lines=lines
    ).build()


def site(*entries: str) -> str:
    """The header and components block of a version-4 file with the given site entries."""
    body = "\n".join(textwrap.indent(textwrap.dedent(entry).strip(), "  ") for entry in entries)
    return f"schema_version: 4\nname: inline\ncomponents:\n{body}\n"


#: The two site entries most mock systems need.
WEATHER = f"""
Weather:
  class: {Mocks.MOCKS}.MockWeather
  preset: standard
"""
OCCUPANCY = f"""
Occupancy:
  class: {Mocks.MOCKS}.MockOccupancy
  preset: standard
"""
EMS = f"""
Ems:
  class: {Mocks.MOCKS}.MockEms
  preset: standard
"""
