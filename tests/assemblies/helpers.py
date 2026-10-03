"""Shared helpers of the assemblies tests: the fixture library, inline files and expansion in one call."""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import ClassVar, List, Optional, Tuple

from hisim.energy_system.assemblies.expansion import expand_imports
from hisim.energy_system.assemblies.record import ImportRecord
from hisim.energy_system.assemblies.resolver import AssemblyResolver
from hisim.energy_system.document import RawDocument
from hisim.energy_system.loader import EnergySystemReader
from hisim.energy_system.model import EnergySystemFile
from hisim.energy_system.source_lines import LineIndex


class Fixtures:
    """Where the fixture files live."""

    ROOT: ClassVar[Path] = Path(__file__).resolve().parent / "fixtures"
    LIBRARY: ClassVar[Path] = ROOT / "library"
    SYSTEMS: ClassVar[Path] = ROOT / "systems"
    HOUSE: ClassVar[Path] = SYSTEMS / "house.energy_system.yaml"
    PARAMETERS: ClassVar[Path] = ROOT / "one_day_kpis.simulation.yaml"
    MOCKUP: ClassVar[Path] = Path(__file__).resolve().parent / "mockup"

    #: Dotted prefix of the fake component classes.
    FAKES: ClassVar[str] = "tests.assemblies.fixture_components"


class Library:
    """A temporary assembly library beside the fixture library, for the files one test needs."""

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

    def resolver(self, *, with_fixtures: bool = True) -> AssemblyResolver:
        """A resolver over this library, after the fixture library when asked."""
        directories: List[Path] = [Fixtures.LIBRARY] if with_fixtures else []
        directories.append(self.directory)
        return AssemblyResolver(directories)


def fixture_resolver() -> AssemblyResolver:
    """A resolver over the fixture library alone."""
    return AssemblyResolver([Fixtures.LIBRARY])


def read_system(text: str, origin: str = "inline.energy_system.yaml") -> Tuple[EnergySystemFile, LineIndex]:
    """Reads an inline energy-system file, dedenting it, with its line index."""
    dedented = textwrap.dedent(text).lstrip()
    model = EnergySystemReader.build(RawDocument.parse_text(dedented, origin), origin)
    return model, LineIndex.from_text(dedented, origin)


def expand_text(
    text: str, resolver: Optional[AssemblyResolver] = None, origin: str = "inline.energy_system.yaml"
) -> Tuple[EnergySystemFile, ImportRecord]:
    """Reads and expands an inline energy-system file against the fixture library (or a given one)."""
    model, lines = read_system(text, origin)
    return expand_imports(model, resolver or fixture_resolver(), lines=lines)


def site(*entries: str) -> str:
    """The header and components block of a version-4 file with the given site entries."""
    body = "\n".join(textwrap.indent(textwrap.dedent(entry).strip(), "  ") for entry in entries)
    return f"schema_version: 4\nname: inline\ncomponents:\n{body}\n"


#: The two site entries most fixture systems need.
WEATHER = f"""
Weather:
  class: {Fixtures.FAKES}.FakeWeather
  preset: standard
"""
OCCUPANCY = f"""
Occupancy:
  class: {Fixtures.FAKES}.FakeOccupancy
  preset: standard
"""
EMS = f"""
Ems:
  class: {Fixtures.FAKES}.FakeEms
  preset: standard
"""
