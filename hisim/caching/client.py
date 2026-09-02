"""The client that decides whether a cache entry exists, where it lives, and how a new one lands.

``roadmap/cache_service_spec.md`` §4 describes ``CacheClient`` as the orchestrator of the tiers:
local directory, then server, then shared directory, then compute. This phase ships the first tier
only, and it ships it as the client rather than as a helper for one reason -- every component already
talks to the cache through ``hisim.utils.get_cache_file``, and the way to make the later tiers reach
every component at once is to have that function delegate here now, while there is only one tier to
delegate to. When the remote tier arrives it slots in behind :meth:`CacheClient.lookup` and no
component changes.

What the local tier does is exactly what ``get_cache_file`` used to do inline: derive the filename from
the component key and the digest of the key material, create the directory, and count an existing file
as a hit only if its companion metadata hashes to the name it is filed under -- otherwise discard it and
report a miss, which is what makes a scheme change or a poisoning self-repairing. The behaviour moved;
it did not change.

This module imports the standard library, ``hisim.log`` and its own package, never a component or the
simulation, so the client stays at the layer of ``hisim/config/``.
"""

# clean

import os
from contextlib import contextmanager
from dataclasses import dataclass
from typing import ClassVar, Iterator, Optional

from hisim import log
from hisim.caching.local import CacheEntryMetadata, atomic_cache_write
from hisim.caching.settings import CacheSettings

__authors__ = "Noah Pflugradt"
__copyright__ = "Copyright 2021-2026, FZJ-IEK-3 "
__license__ = "MIT"
__version__ = "1"
__maintainer__ = "Noah Pflugradt"
__email__ = "n.pflugradt@fz-juelich.de"
__status__ = "development"


@dataclass(frozen=True)
class CacheEntry:
    """One looked-up entry: whether it exists, where it is, and what identifies it.

    ``exists`` is advisory, as it always was: two processes may both miss the same key and both compute
    the entry, which wastes work and nothing more, because the contents are a pure function of the key
    and the landing is atomic. Writing straight to :attr:`path` is the one thing a caller must never
    do; :meth:`writing` is how an entry lands.
    """

    #: The absolute path the entry is, or will be, filed at.
    path: str

    #: Whether a validated entry was found at :attr:`path`.
    exists: bool

    #: The raw string the entry's name is hashed from; recorded beside the entry when it is written.
    key_material: str

    @contextmanager
    def writing(self) -> Iterator[str]:
        """Yields a temporary path to write the entry to, then moves it and its metadata into place.

        Thin wrapper over :func:`hisim.caching.atomic_cache_write` that supplies this entry's own path
        and key material, so a caller cannot pair the wrong two.

        Yields:
            str: the temporary path to write to.
        """
        with atomic_cache_write(self.path, self.key_material) as temporary_path:
            yield temporary_path


class CacheClient:
    """Looks entries up across the configured tiers and hands back where to read or write them.

    One tier today. The class is built so that the second and third are additions to
    :meth:`lookup`, not changes to its callers.
    """

    #: The extension every local entry carries.
    SUFFIX: ClassVar[str] = ".cache"

    def __init__(self, settings: CacheSettings) -> None:
        """Binds the client to one set of settings.

        Args:
            settings: what the environment said, read once by the caller.
        """
        self.settings = settings

    @classmethod
    def from_environment(cls) -> "CacheClient":
        """Builds a client from the process environment.

        Returns:
            CacheClient: the client.
        """
        return cls(CacheSettings.from_environment())

    @classmethod
    def entry_filename(cls, component_key: str, digest: str) -> str:
        """Names an entry the way every reader and writer expects: ``<component_key>_<digest>.cache``.

        Args:
            component_key: the filename prefix, today the component's instance name.
            digest: the sha256 hex digest of the key material.

        Returns:
            str: the filename.
        """
        return f"{component_key}_{digest}{cls.SUFFIX}"

    def lookup(self, component_key: str, key_material: str, default_directory: str) -> CacheEntry:
        """Finds an entry in the local tier, creating the directory and discarding what cannot be trusted.

        Args:
            component_key: the filename prefix.
            key_material: the raw string identifying the entry's inputs.
            default_directory: the directory to use unless the environment overrides it, normally the
                simulation's ``cache_dir_path``.

        Returns:
            CacheEntry: with ``exists`` True only for a validated hit.
        """
        directory = self.settings.resolve_local_directory(default_directory)
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, self.entry_filename(component_key, CacheEntryMetadata.hash_of(key_material)))
        return CacheEntry(path=path, exists=self._validated_local_hit(path), key_material=key_material)

    @staticmethod
    def _validated_local_hit(path: str) -> bool:
        """Says whether a file at ``path`` is an entry that can be shown to belong to its key.

        An entry whose metadata is missing or disagrees with its own filename either predates the
        metadata scheme or was produced by something other than what the key describes; the file alone
        cannot tell the two apart, so both are deleted and reported as a miss.

        Args:
            path: the entry's path.

        Returns:
            bool: True for a validated hit.
        """
        if not os.path.isfile(path):
            return False
        if CacheEntryMetadata.describes(path):
            return True
        log.warning(
            f"Discarding the cache entry {path}: its metadata is missing or does not hash to the name it is "
            f"filed under, so its contents cannot be shown to belong to this key. It will be recomputed."
        )
        CacheEntryMetadata.discard(path)
        return False

    def describe(self) -> str:
        """One line saying which tiers this client will use, for logs and diagnostics.

        Returns:
            str: the description.
        """
        if self.settings.is_standalone:
            return "local cache directory only"
        parts = ["local cache directory"]
        if self.settings.shared_directory is not None:
            parts.append(f"shared directory {self.settings.shared_directory}")
        parts.append(f"network {self.settings.network.value} (not yet active in this phase)")
        return ", ".join(parts)


def default_client(settings: Optional[CacheSettings] = None) -> CacheClient:
    """Returns a client for the given settings, or for the environment when none are given.

    Exists so that ``hisim.utils.get_cache_file`` has one obvious thing to call.

    Args:
        settings: pre-read settings, or ``None`` to read the environment now.

    Returns:
        CacheClient: the client.
    """
    return CacheClient(settings) if settings is not None else CacheClient.from_environment()
