"""The ordered cache directories of one calculation: read in order, write to the first.

A container maps a persistent volume and a baked-in seed directory in and passes both as
``SimulationParameters.cache_directories`` (hisim-epc.22): a read finds an entry in any of them,
in order, and a new entry is written to the first, so the seed is never written to and the volume
accumulates what the seed lacks. The cache is content-keyed and deterministic, which is what
makes reading across directories safe: an entry found in a later directory is the same artifact
the first would have produced.

This module belongs to the cache package and imports only the standard library and ``hisim.log``
-- the same layer rule the rest of the package follows, checked in a fresh interpreter.
"""

from __future__ import annotations

import os
from typing import Optional, Sequence, Tuple

from hisim import log


class CacheLocations:

    """The ordered cache directories of one calculation: read in order, write to the first.

    Built from :meth:`hisim.simulationparameters.SimulationParameters.cache_locations`, which
    resolves the parameter list against the single legacy path: an empty list means the one
    ``cache_dir_path``, exactly the behaviour every caller had before the list existed.
    """

    def __init__(self, directories: Sequence[str]) -> None:
        """Store the directories in the given order.

        Args:
            directories: At least one directory, in read-and-write priority order.

        Raises:
            ValueError: When the sequence is empty, because there would be nowhere to write.
        """
        if not directories:
            raise ValueError(
                "cache_directories is empty; a calculation needs at least one cache directory"
            )
        self._directories: Tuple[str, ...] = tuple(directories)

    @property
    def directories(self) -> Tuple[str, ...]:
        """The directories, in priority order."""
        return self._directories

    def read(self, path_relative: str) -> Optional[str]:
        """Return the full path of the first directory holding the file, or ``None``.

        Args:
            path_relative: The cache entry's name, relative to a cache directory.

        Returns:
            The path of the first hit in read order, or ``None`` when no directory holds it.
        """
        for directory in self._directories:
            candidate = os.path.join(directory, path_relative)
            if os.path.isfile(candidate):
                return candidate
        return None

    def write_directory(self) -> str:
        """The directory new entries are written to: the first, whatever the read order found."""
        return self._directories[0]

    def announce(self) -> None:
        """Log the directories once, so a run's report says where its cache was."""
        log.information(f"Cache directories, read in order, written to the first: {list(self._directories)}")

    def __repr__(self) -> str:
        """The directories, for the run manifest and the log."""
        return f"CacheLocations({list(self._directories)!r})"
