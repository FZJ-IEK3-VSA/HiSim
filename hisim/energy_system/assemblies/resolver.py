"""Finding an assembly by its library path (``assemblies_spec.md`` §9.3).

An import names its assembly by a library path, ``pv/array``, never by a file. The resolver turns
that path into one file along a search path: the repository's ``energy_systems/assemblies/`` first,
then every directory the environment variable ``HISIM_ASSEMBLY_PATH`` names (``os.pathsep``
separated). A path found in two places is refused rather than shadowed: which of two files ran must
never depend on the order of a variable. Every resolved assembly carries the sha256 of its file's
bytes, which the import record keeps (§9.1, D4).
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Dict, List, Sequence, Tuple

from hisim.energy_system.assemblies.model import AssemblyFile
from hisim.energy_system.assemblies.reader import AssemblyReader
from hisim.energy_system.errors import EnergySystemAssemblyError, EnergySystemErrorId
from hisim.energy_system.source_lines import LineIndex


@dataclass(frozen=True)
class ResolvedAssembly:
    """One assembly file, found, hashed and read.

    Attributes:
        path: The library path, ``pv/array``.
        file: The file it resolved to.
        sha256: The sha256 of the file's bytes.
        model: The file's model.
        lines: The file's line index; its origin, ``pv/array.assembly.yaml``, is the label messages use.
    """

    path: str
    file: Path
    sha256: str
    model: AssemblyFile
    lines: LineIndex

    @property
    def label(self) -> str:
        """How messages name the file: its library path with the suffix."""
        return self.lines.origin


class AssemblyResolver:
    """Resolves library paths to assembly files along a search path, reading each file once."""

    #: The environment variable naming further library directories, ``os.pathsep``-separated.
    ENVIRONMENT_VARIABLE: ClassVar[str] = "HISIM_ASSEMBLY_PATH"

    def __init__(self, directories: Sequence[Path]) -> None:
        """Prepares a resolver over an explicit search path.

        Args:
            directories: The library directories in search order.

        Raises:
            EnergySystemAssemblyError: ``EF-71`` when a directory does not exist.
        """
        for directory in directories:
            if not Path(directory).is_dir():
                raise EnergySystemAssemblyError(
                    EnergySystemErrorId.ASSEMBLY_NOT_FOUND,
                    str(directory),
                    f"the assembly library directory '{directory}' does not exist.",
                    remedy=f"Fix {self.ENVIRONMENT_VARIABLE}, or the directories handed to the resolver.",
                )
        self.directories: Tuple[Path, ...] = tuple(Path(directory) for directory in directories)
        self._cache: Dict[str, ResolvedAssembly] = {}

    @classmethod
    def default(cls) -> "AssemblyResolver":
        """The resolver of this machine: the repository's library (when it exists), then ``HISIM_ASSEMBLY_PATH``."""
        directories: List[Path] = []
        repository = Path(__file__).resolve().parents[3] / "energy_systems" / "assemblies"
        if repository.is_dir():
            directories.append(repository)
        written = os.environ.get(cls.ENVIRONMENT_VARIABLE, "")
        directories.extend(Path(part) for part in written.split(os.pathsep) if part)
        return cls(directories)

    def resolve(self, library_path: str, location: str) -> ResolvedAssembly:
        """Finds, hashes and reads one assembly.

        Args:
            library_path: ``<family>/<name>`` as an import writes it.
            location: Where the import is written, for the message.

        Returns:
            The resolved assembly.

        Raises:
            EnergySystemAssemblyError: ``EF-71`` for a malformed path or one found nowhere, ``EF-72``
                for one found in two directories.
            EnergySystemFormatError: For a file that is not a well-formed assembly.
        """
        cached = self._cache.get(library_path)
        if cached is not None:
            return cached
        if not AssemblyFile.LIBRARY_PATH_PATTERN.match(library_path):
            raise EnergySystemAssemblyError(
                EnergySystemErrorId.ASSEMBLY_NOT_FOUND,
                location,
                f"'{library_path}' is not an assembly path; an assembly is named '<family>/<name>', identifiers "
                "joined by '/', without the file suffix.",
            )
        candidates = [
            directory / f"{library_path}{AssemblyReader.SUFFIX}"
            for directory in self.directories
            if (directory / f"{library_path}{AssemblyReader.SUFFIX}").is_file()
        ]
        if not candidates:
            raise EnergySystemAssemblyError(
                EnergySystemErrorId.ASSEMBLY_NOT_FOUND,
                location,
                f"the assembly '{library_path}' is in none of the library directories "
                f"({', '.join(str(directory) for directory in self.directories) or 'none configured'}).",
                alternatives=self.available(),
                alternatives_label="assemblies",
                offending_value=library_path,
                remedy=f"Add its directory to {self.ENVIRONMENT_VARIABLE}, or correct the path.",
            )
        if len(candidates) > 1:
            raise EnergySystemAssemblyError(
                EnergySystemErrorId.ASSEMBLY_FOUND_TWICE,
                location,
                f"the assembly '{library_path}' is found in two places, {candidates[0]} and {candidates[1]}; "
                "one name, one file.",
                remedy="Remove or rename one of them; a library path is never shadowed.",
            )
        data = candidates[0].read_bytes()
        model, lines = AssemblyReader.read_text(data.decode("utf-8"), f"{library_path}{AssemblyReader.SUFFIX}")
        resolved = ResolvedAssembly(library_path, candidates[0], hashlib.sha256(data).hexdigest(), model, lines)
        self._cache[library_path] = resolved
        return resolved

    def available(self) -> Tuple[str, ...]:
        """Every library path the search path offers, for a "did you mean" list."""
        found: List[str] = []
        for directory in self.directories:
            for file in sorted(directory.rglob(f"*{AssemblyReader.SUFFIX}")):
                found.append(file.relative_to(directory).as_posix()[: -len(AssemblyReader.SUFFIX)])
        return tuple(dict.fromkeys(found))
