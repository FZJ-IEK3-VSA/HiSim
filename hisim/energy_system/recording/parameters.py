"""Which simulation-parameters file a recording points at, and when a new one is written.

A recorded energy-system file says what a household *is*; a simulation-parameters file says what
to do with it. Twenty-two setups would otherwise produce twenty-two near-identical parameter
files, so this module answers the question the recorder asks once per setup: does a file that
already exists say the same thing? If one does, the recording references it and nothing is
written. Only when nothing matches is a file written, and the same comparison covers the files
written earlier in the same run, so two setups needing identical parameters share one file rather
than getting a twin each.

The comparison is semantic, not textual. Two files agree when their period, resolution, sorted
option set, logging level, country and year agree, whatever order their keys happen to be in and
whatever they say about anything else. What "anything else" means matters more than what is kept:
``cache_dir_path`` above all, which eleven setups point at a cluster directory behind an
``os.path.exists`` probe. Keeping it would make the answer depend on which machine the recorder
ran on, which defeats the sharing and breaks the promise that recording is byte-identical
everywhere. It is therefore absent from the comparison and from anything written.

A file this module writes is named for its content — the horizon, the resolution and what its
option set is for — and never for the setup that first needed it, because it is shared from the
moment a second setup matches it. That is also why the name has to be derived rather than
invented: two runs of the recorder on the same fleet must produce the same file names.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Sequence, Tuple

from hisim.energy_system.parameters_format import (
    ParameterFileName,
    ParameterFileWriter,
    ParameterNormalisation,
)
from hisim.simulationparameters import SimulationParameters

# Re-exported: the comparison, the naming and the rendering moved down to
# :mod:`hisim.energy_system.parameters_format` when a run began writing the parameters it used
# beside its realized record, so that the run path does not import the recorder to describe
# itself. They are part of this module's interface as they always were.
__all__ = [
    "ParameterFileLibrary",
    "ParameterFileName",
    "ParameterFileWriter",
    "ParameterNormalisation",
    "ParameterReference",
    "normalise_parameters",
]


@dataclass(frozen=True)
class ParameterReference:
    """What one recording's parameters resolved to: a file, and whether it had to be written.

    Both halves are needed by different callers and neither can be derived from the other. The
    recorder writes the path into its header; the driver reports how many files a fleet-wide run
    added, which is the number a reviewer checks against the diff.
    """

    path: Path
    written: bool


class ParameterFileLibrary:
    """The parameter files that exist, and the one a recording should point at.

    One library serves one run of the recorder, however many setups it records. It reads the files
    that are already committed once, remembers every file it writes, and answers the same question
    against both, which is what makes two setups needing identical parameters share one file
    instead of getting a twin each.

    The search directory and the write directory are separate because they legitimately differ: a
    caller recording into a temporary directory still means to reference the committed parameter
    files, and only a genuinely new parameter set should land in the directory it asked for.
    """

    #: Glob matching every simulation-parameters file of this format.
    PATTERN: ClassVar[str] = "*.simulation.yaml"

    def __init__(self, search: Sequence[Path], write_to: Path) -> None:
        """Reads the existing parameter files a recording may reference.

        Args:
            search: Directories holding files a recording may reference, in priority order; a
                directory that does not exist contributes nothing.
            write_to: Directory a newly written file goes into.
        """
        self.write_to = Path(write_to)
        self.known: List[Tuple[Path, Dict[str, Any]]] = []
        seen = set()
        for directory in search:
            resolved = Path(directory).resolve()
            if resolved in seen or not resolved.is_dir():
                continue
            seen.add(resolved)
            for path in sorted(Path(directory).glob(self.PATTERN)):
                self.known.append((path, self.read(path)))

    @classmethod
    def read(cls, path: Path) -> Dict[str, Any]:
        """Normalises one parameter file on disk.

        Args:
            path: The file to read.

        Returns:
            Its normalised content.
        """
        from hisim.energy_system.executor import SimulationParametersReader  # noqa: PLC0415

        return ParameterNormalisation.normalise(SimulationParametersReader.read(path))

    def reference(self, parameters: SimulationParameters) -> ParameterReference:
        """Finds the file a recording of these parameters should point at, writing one if needed.

        Args:
            parameters: The effective parameters of the run being recorded.

        Returns:
            The file to reference and whether this call created it.
        """
        normalised = ParameterNormalisation.normalise(parameters)
        match = self.match(normalised)
        if match is not None:
            return ParameterReference(path=match, written=False)
        return ParameterReference(path=self.write(normalised), written=True)

    def match(self, normalised: Mapping[str, Any]) -> Optional[Path]:
        """Finds an existing file whose content normalises equal to the given parameters.

        Args:
            normalised: The parameter set to look for.

        Returns:
            The first file that says the same thing, or ``None``.
        """
        for path, content in self.known:
            if content == normalised:
                return path
        return None

    def write(self, normalised: Mapping[str, Any]) -> Path:
        """Writes one new parameter file and adds it to what later recordings may match.

        The name comes from the content; a stem already taken by a file saying something *else*
        gains a numeric discriminator, which happens only when two genuinely different option
        sets share a horizon, a resolution and a purpose word. A file already saying the *same*
        thing is adopted rather than discriminated: two recorder children running in parallel and
        needing the same new parameter set both converge on one file, whichever of them created
        it, because the name is a function of the content and the creation is exclusive.

        Args:
            normalised: The parameter set to write.

        Returns:
            The path written, or the equal file that already existed.
        """
        self.write_to.mkdir(parents=True, exist_ok=True)
        text = ParameterFileWriter.text(normalised)
        stem = ParameterFileName.stem(normalised)
        attempt = 1
        while True:
            name = stem if attempt == 1 else f"{stem}{ParameterFileName.SEPARATOR}{attempt}"
            path = self.write_to / f"{name}{ParameterFileName.SUFFIX}"
            if self._create_exclusively(path, text) or self.read(path) == dict(normalised):
                self.known.append((path, dict(normalised)))
                return path
            attempt += 1

    @staticmethod
    def _create_exclusively(path: Path, text: str) -> bool:
        """Atomically creates a file with its whole content, or reports that one already exists.

        The content is written to a temporary sibling first and hard-linked into place, so the
        file at ``path`` either does not exist or is complete — a concurrent reader can never see
        a half-written parameter file, and of two writers racing to the same name exactly one
        wins while the other sees ``False`` and looks at what the winner wrote.

        Args:
            path: The file to create.
            text: Its full content.

        Returns:
            ``True`` when this call created the file, ``False`` when it already existed.
        """
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False
        ) as handle:
            handle.write(text)
        try:
            os.link(handle.name, path)
            return True
        except FileExistsError:
            return False
        finally:
            os.unlink(handle.name)

    @classmethod
    def duplicates(cls, directory: Path) -> List[Tuple[Path, Path]]:
        """Finds pairs of committed parameter files that say the same thing.

        R8.6 turns "never two files with the same content" into something a job asserts rather
        than something the recorder merely avoids, because a duplicate can also be added by hand.

        Args:
            directory: The directory to check.

        Returns:
            Every pair of files whose normalised content is equal, each pair once.
        """
        entries = [(path, cls.read(path)) for path in sorted(Path(directory).glob(cls.PATTERN))]
        pairs: List[Tuple[Path, Path]] = []
        for index, (path, content) in enumerate(entries):
            for other, other_content in entries[index + 1:]:
                if content == other_content:
                    pairs.append((path, other))
        return pairs


def normalise_parameters(parameters: SimulationParameters) -> Mapping[str, Any]:
    """Reduces one parameter set to the mapping the recorder compares parameter files through.

    The public spelling of :meth:`ParameterNormalisation.normalise`, given a function of its own
    because it is the part of this module other packages have a reason to call and because the
    class name says how it works rather than what it is for.

    Args:
        parameters: The effective parameters of a run.

    Returns:
        The normalised mapping; two runs share a parameter file exactly when these are equal.
    """
    return ParameterNormalisation.normalise(parameters)
