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

Beside every entry the same write lands a companion ``.meta`` file holding the string the entry's
name was hashed from, which makes the cache self-describing: an entry can be read and understood
without the code that wrote it, and validating one is a matter of recomputing the hash and comparing
it with the filename. See :class:`CacheEntryMetadata`.
"""

# clean

import hashlib
import os
import tempfile
from contextlib import contextmanager
from typing import ClassVar, Iterator

__authors__ = "Noah Pflugradt"
__copyright__ = "Copyright 2021-2026, FZJ-IEK-3 "
__license__ = "MIT"
__version__ = "1"
__maintainer__ = "Noah Pflugradt"
__email__ = "n.pflugradt@fz-juelich.de"
__status__ = "development"


class CacheEntryMetadata:
    """Describes a cache entry in a file beside it, so the entry can be validated and understood.

    A cache entry used to be an opaque blob under a name nobody could read: the filename holds a
    sha256 of a configuration JSON, and nothing anywhere recorded what that JSON had been. Two things
    followed. Nobody could tell what an entry was for without re-deriving the hash by hand, and when
    the key scheme changed the only safe response was to delete the whole directory, because a
    surviving entry could not be told apart from a current one.

    The companion file fixes both, and one thing more. It is written from the inputs that actually
    produced the bytes rather than from the ones that were requested, so validating an entry is a
    comparison: recompute the hash from the metadata and check it against the filename. Agreement
    proves the contents belong to the key. Disagreement is a poisoned entry -- something ran that was
    not what the key describes -- and is found automatically rather than by clearing everything. It is
    also tamper-evident, since the metadata cannot be edited into agreement without recomputing the
    hash it is checked against.

    What it does **not** do is make an incomplete key complete. If a key omits the weather, so does
    the metadata, and the two agree happily; ``roadmap/pylpg_flakiness.md`` F7 is the fix for that.
    This makes migration systematic and auditing possible, nothing more.
    """

    SUFFIX: ClassVar[str] = ".meta"

    #: The extension of the data file beside the metadata. The one definition; the client's filenames
    #: and the parsing below both use it.
    DATA_SUFFIX: ClassVar[str] = ".cache"

    @staticmethod
    def hash_of(cache_key_string: str) -> str:
        """Hashes the string a cache entry is named after, in the one place that defines the scheme.

        Both the writer of an entry's name and the validator of an existing entry have to agree on
        the digest exactly, so the algorithm lives here rather than being spelled out at each site.
        :meth:`hisim.caching.keys.CacheKey.digest` repeats the expression (that module imports the
        standard library only); the two must stay identical.

        Args:
            cache_key_string: the raw string that identifies the entry's inputs.

        Returns:
            str: the hexadecimal sha256 digest that appears in the entry's filename.
        """
        return hashlib.sha256(cache_key_string.encode("utf-8")).hexdigest()

    @classmethod
    def metadata_filepath(cls, cache_filepath: str) -> str:
        """Returns the path of the companion metadata file for a cache entry.

        Args:
            cache_filepath: the path of the data file, ``<prefix>_<hash>.cache``.

        Returns:
            str: the same path with :attr:`SUFFIX` appended, which keeps the pair adjacent in a
                listing and keeps the entry's identity visible in the metadata's own name.
        """
        return cache_filepath + cls.SUFFIX

    @staticmethod
    def hash_in_filename(cache_filepath: str) -> str:
        """Extracts the digest a cache entry is filed under from its filename.

        Entry names are ``<component_key>_<hash>.cache`` and a component key may itself contain
        underscores, so the digest is taken from the last separator rather than the first.

        Args:
            cache_filepath: the path of the data file.

        Returns:
            str: the digest portion of the filename, or an empty string if the name does not have
                that shape -- which is itself a reason to treat the entry as unvalidatable.
        """
        filename = os.path.basename(cache_filepath)
        if not filename.endswith(CacheEntryMetadata.DATA_SUFFIX) or "_" not in filename:
            return ""
        return filename[: -len(CacheEntryMetadata.DATA_SUFFIX)].rsplit("_", 1)[1]

    @classmethod
    def describes(cls, cache_filepath: str) -> bool:
        """Says whether the entry's metadata exists and agrees with the name the entry is filed under.

        Args:
            cache_filepath: the path of the data file.

        Returns:
            bool: True if the metadata file is present and its hash equals the one in the filename.
        """
        metadata_filepath = cls.metadata_filepath(cache_filepath)
        if not os.path.isfile(metadata_filepath):
            return False
        try:
            with open(metadata_filepath, encoding="utf-8") as metadata_file:
                recorded_inputs = metadata_file.read()
        except OSError:
            return False
        return cls.hash_of(recorded_inputs) == cls.hash_in_filename(cache_filepath)

    @classmethod
    def discard(cls, cache_filepath: str) -> None:
        """Deletes an entry and its metadata, so the next lookup misses and recomputes.

        An entry that cannot be validated is not merely unknown, it is unusable: either it predates
        the metadata scheme, or its contents came from something other than what its key describes.
        Deleting it on sight is what makes the migration self-executing -- every entry written before
        this landed clears itself the first time it is looked up -- and it is also the response to a
        poisoning, because keeping such an entry means serving it again.

        Failures to delete are ignored. A concurrent process may have removed the same entry for the
        same reason, and the caller has already decided to treat the lookup as a miss either way.

        Args:
            cache_filepath: the path of the data file; its metadata goes with it.
        """
        for path in (cls.metadata_filepath(cache_filepath), cache_filepath):
            try:
                os.remove(path)
            except OSError:
                pass


@contextmanager
def atomic_cache_write(cache_filepath: str, effective_cache_key_string: str) -> Iterator[str]:
    """Yields a temporary path to write a cache entry to, then moves it and its metadata into place.

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

    The companion metadata of :class:`CacheEntryMetadata` lands in the same operation, and the order
    is load-bearing. ``get_cache_file`` decides an entry exists by looking at the *data* file, so the
    metadata is renamed into place first and the data second. A crash between the two then leaves an
    orphan metadata file, which is harmless and ignored, rather than a data file that nothing can
    validate -- which is exactly the state the deletion rule exists to clean up, and which there is no
    reason to manufacture.

    The string recorded is the **effective** one: the inputs that actually produced these bytes, not
    the ones the caller asked for. Where the two can differ, recording the request would make a
    poisoned entry look sound, because its metadata would agree with a filename the contents do not
    belong to.

    Note what this deliberately does not do. The ``exists`` flag returned by
    ``hisim.utils.get_cache_file`` stays advisory: two processes may still both miss the cache and
    both compute the same entry, and both then write it. That wastes work but is harmless once the
    landing is atomic, because the two results are equal by construction -- they are a pure function
    of the key. Locking the key to prevent the duplicate work would buy very little and would add a
    failure mode of its own, so it is not done here.

    Args:
        cache_filepath: the final path the entry must appear at. Its directory is created if missing.
        effective_cache_key_string: the raw string describing the inputs that produced the entry.
            Its hash must equal the digest in ``cache_filepath``, or the entry it names will be
            discarded as poisoned the next time it is looked up.

    Yields:
        str: the temporary path to write to. It is replaced onto ``cache_filepath`` when the ``with``
            block exits normally.

    Raises:
        OSError: if a temporary file cannot be created or cannot be renamed into place.
    """
    cache_directory = os.path.dirname(os.path.abspath(cache_filepath))
    os.makedirs(cache_directory, exist_ok=True)
    temporary_data_filepath = _make_temporary_file(cache_filepath, cache_directory)
    try:
        yield temporary_data_filepath
        temporary_metadata_filepath = _make_temporary_file(cache_filepath, cache_directory)
        try:
            with open(temporary_metadata_filepath, "w", encoding="utf-8") as metadata_file:
                metadata_file.write(effective_cache_key_string)
            # Metadata first, data second: see the docstring. An orphan metadata file is ignorable,
            # an unvalidatable data file is not.
            os.replace(temporary_metadata_filepath, CacheEntryMetadata.metadata_filepath(cache_filepath))
        finally:
            if os.path.exists(temporary_metadata_filepath):
                os.remove(temporary_metadata_filepath)
        os.replace(temporary_data_filepath, cache_filepath)
    finally:
        # A successful replace has already consumed the temporary file, so this only ever fires when
        # the caller's write raised or the block was abandoned.
        if os.path.exists(temporary_data_filepath):
            os.remove(temporary_data_filepath)


def _make_temporary_file(cache_filepath: str, cache_directory: str) -> str:
    """Creates an empty, world-readable temporary file next to the cache entry being written.

    The file has to sit in the target's own directory, because a rename across filesystems is a copy
    and would stop being atomic. ``mkstemp`` creates it 0600, which would make a shared cache
    directory unreadable to the other users the spec expects to share it, so the mode is widened back
    to what the direct writes this helper replaces used to produce.

    Args:
        cache_filepath: the entry the temporary file will become; its basename seeds the prefix so a
            stray temporary is traceable to the entry that produced it.
        cache_directory: the directory to create the temporary file in.

    Returns:
        str: the path of the new empty file.
    """
    file_descriptor, temporary_filepath = tempfile.mkstemp(
        prefix=os.path.basename(cache_filepath) + ".", suffix=".partial", dir=cache_directory
    )
    os.close(file_descriptor)
    os.chmod(temporary_filepath, 0o644)
    return temporary_filepath
