"""Local tier of the HiSim cache service: entries land on disk atomically.

This module is phase 1 of the cache service described in ``roadmap/cache_service_spec.md`` §4, and
so far it is the *only* tier that exists. The eventual package holds key construction, a remote HTTP
tier, curated-dataset retrieval and the client that orchestrates them; none of that is here yet, and
nothing in this module talks to the network or reads any configuration. It is a package rather than a
single helper function because the rest of that spec will grow around it, and because the spec puts
the whole of it at the same layer as ``hisim/config/`` -- it may import the standard library and
nothing else, so a component, the simulator and the singleton repository are all off limits and the
module stays importable without starting HiSim.

The spec calls out the atomic write as the one piece worth shipping ahead of the rest, because it
repairs a bug that exists independently of the service: two processes sharing a cache directory can
read a ``.cache`` file that a third is still writing. See :func:`atomic_cache_write` for the race in
full.
"""

# clean

import os
import tempfile
from contextlib import contextmanager
from typing import Iterator

__authors__ = "Noah Pflugradt"
__copyright__ = "Copyright 2021-2026, FZJ-IEK-3 "
__license__ = "MIT"
__version__ = "1"
__maintainer__ = "Noah Pflugradt"
__email__ = "n.pflugradt@fz-juelich.de"
__status__ = "development"


@contextmanager
def atomic_cache_write(cache_filepath: str) -> Iterator[str]:
    """Yields a temporary path to write a cache entry to, then moves it into place atomically.

    Cache entries used to be written straight to their final path, which made every entry corruptible
    by concurrency. ``hisim.utils.get_cache_file`` decides that an entry exists with a plain
    ``os.path.isfile`` check, so two processes computing the same key race like this: worker A sees no
    file, computes, and starts writing ``Weather_<hash>.cache``; worker B arrives a second later, sees
    a file that exists, and reads it while it is still half written. Depending on where A had got to,
    B either fails to parse the file or -- far worse -- parses it happily and continues with truncated
    data. That is what broke one setup of a parallel scenario regeneration in CI while its near-twin,
    which shares its weather cache key, succeeded beside it; the same race is waiting on any shared
    filesystem, which is why the spec ships this tier first.

    Writing to a temporary name and renaming closes the window: ``os.replace`` is atomic on POSIX and
    on Windows, so a concurrent reader observes either the previous entry or the complete new one and
    never anything in between. The temporary file is created **in the same directory** as the target,
    because a rename across filesystems is a copy and would not be atomic. If the caller's write
    raises, the temporary file is removed so a failed write leaves no debris behind.

    Note what this deliberately does not do. The ``exists`` flag returned by
    ``hisim.utils.get_cache_file`` stays advisory: two processes may still both miss the cache and
    both compute the same entry, and both then write it. That wastes work but is harmless once the
    landing is atomic, because the two results are equal by construction -- they are a pure function
    of the key. Locking the key to prevent the duplicate work would buy very little and would add a
    failure mode of its own, so it is not done here.

    Args:
        cache_filepath: the final path the entry must appear at. Its directory is created if missing.

    Yields:
        str: the temporary path to write to. It is replaced onto ``cache_filepath`` when the ``with``
            block exits normally.

    Raises:
        OSError: if the temporary file cannot be created or cannot be renamed into place.
    """
    cache_directory = os.path.dirname(os.path.abspath(cache_filepath))
    os.makedirs(cache_directory, exist_ok=True)
    file_descriptor, temporary_filepath = tempfile.mkstemp(
        prefix=os.path.basename(cache_filepath) + ".", suffix=".partial", dir=cache_directory
    )
    os.close(file_descriptor)
    # mkstemp creates the file 0600, which would make a shared cache directory unreadable to the other
    # users the spec expects to share it; the direct writes this helper replaces produced the usual
    # umask-governed permissions.
    os.chmod(temporary_filepath, 0o644)
    try:
        yield temporary_filepath
        os.replace(temporary_filepath, cache_filepath)
    finally:
        # A successful replace has already consumed the temporary file, so this only ever fires when
        # the caller's write raised or the block was abandoned.
        if os.path.exists(temporary_filepath):
            os.remove(temporary_filepath)
