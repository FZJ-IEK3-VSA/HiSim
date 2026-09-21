"""The ordered cache directories of one calculation: read in order, write to the first writable one.

A container maps a persistent volume and a baked-in seed directory in and passes both as
``SimulationParameters.cache_directories`` (hisim-epc.22): a read finds an entry in any of them,
in order, and a new entry is written to the first directory that can take it, so a read-only seed
is never written to and the volume accumulates what the seed lacks. The cache is content-keyed and
deterministic, which is what makes reading across directories safe: an entry found in a later
directory is the same artifact the first would have produced.

This module belongs to the cache package and imports only the standard library and ``hisim.log``
-- the same layer rule the rest of the package follows, checked in a fresh interpreter.
"""

from __future__ import annotations

import os
from typing import Optional, Sequence, Tuple

from hisim import log


class CacheLocationsError(ValueError):

    """Raised when a calculation has nowhere to write its cache.

    Every listed directory is either read-only or cannot be created, so a computed artifact
    could not be kept for the next run. Failing here names the directories, which is more useful
    than the ``PermissionError`` the first write would otherwise raise from deep inside a component.
    """


class CacheLocations:

    """The ordered cache directories of one calculation: read in order, write to the first writable one.

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

        The walk creates nothing: a listed directory that does not exist is simply skipped, so a
        read-only mount point that has not been populated yet is not an error.

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
        """The directory new entries are written to: the first one that can take a write.

        An existing directory qualifies when the process may write into it; a missing one
        qualifies when it can be created, and is created here so the answer is a directory that
        exists. A read-only seed listed first is therefore skipped for writes while it stays
        first for reads.

        Returns:
            The first writable directory, in list order.

        Raises:
            CacheLocationsError: When no listed directory is writable or creatable.
        """
        for directory in self._directories:
            if os.path.isdir(directory):
                if os.access(directory, os.W_OK):
                    return directory
                continue
            try:
                os.makedirs(directory, exist_ok=True)
            except OSError:
                continue
            return directory
        raise CacheLocationsError(
            f"none of the cache directories {list(self._directories)!r} is writable or can be created, "
            "so a computed cache entry would have nowhere to go"
        )

    def announce(self) -> None:
        """Log the directories once, so a run's report says where its cache was."""
        log.information(
            f"Cache directories, read in order, written to the first writable one: {list(self._directories)}"
        )

    def __repr__(self) -> str:
        """The directories, for the run manifest and the log."""
        return f"CacheLocations({list(self._directories)!r})"
