"""Finding the checkout a file belongs to, and spelling a path the same way on every machine.

Several parts of the recorder have to write a path into a file that is then compared byte for byte
against the same file recorded elsewhere: the header of a recorded twin names its setup and its
parameters, a grouping decision names the probe list it follows, and a workbook remembers which
setup it was written for. An absolute path in any of them would make two machines disagree about a
file that describes the same system, so all of them spell the path relative to the checkout — and
they have to agree on where the checkout begins.

That agreement is what lives here, and it is one class with two questions: where is the root, and
how is this path spelled from it. Holding it in a module of its own rather than on the recording
session is what lets the light parts of the pass — the probe list, the command line's path
defaults — ask it without importing the recorder and, with it, HiSim's whole component tree.
"""

# clean

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Tuple


class RepositoryLayout:
    """Where the checkout begins, and how a path inside it is written down portably.

    The root is found by walking up from a path the caller already has rather than from this
    module, so that an editable checkout and an installed package answer the same question about
    the same file. A path outside any checkout falls back to its bare name, which is the least
    misleading thing to write when there is no root to be relative to.
    """

    #: Files that mark the root of a HiSim checkout. Two of them rather than one, because either
    #: alone appears in enough unrelated directories to be found by accident.
    ROOT_MARKERS: ClassVar[Tuple[str, ...]] = ("setup.py", "hisim")

    @classmethod
    def root(cls, near: Path) -> Path:
        """Finds the checkout one path lies in.

        Args:
            near: Any path inside the checkout; it does not have to exist.

        Returns:
            The root directory, or the current working directory when the path lies outside one.
        """
        for parent in Path(near).resolve().parents:
            if all((parent / marker).exists() for marker in cls.ROOT_MARKERS):
                return parent
        return Path.cwd()

    @classmethod
    def relative(cls, path: Path) -> str:
        """Spells one path relative to the checkout it lies in.

        Args:
            path: The path to spell.

        Returns:
            A forward-slash path that is the same string on every machine, or the bare file name
            when the path lies outside any checkout.
        """
        resolved = Path(path).resolve()
        for parent in resolved.parents:
            if all((parent / marker).exists() for marker in cls.ROOT_MARKERS):
                return resolved.relative_to(parent).as_posix()
        return resolved.name
