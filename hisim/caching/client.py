"""The cache client: looks an entry up, says where it lives, and lands a new one atomically.

``roadmap/cache_service_spec.md`` §4 describes ``CacheClient`` as the object that walks the tiers --
local directory, server, shared directory -- and falls back to computing. This phase implements the
local tier only. It is already shaped as the client, and ``hisim.utils.get_cache_file`` already
delegates to it, so that the later tiers can be added behind :meth:`CacheClient.lookup` without any
component changing.

The local tier does what ``get_cache_file`` used to do inline: derive the filename from the component
key and the hash of the key material, create the directory, and count an existing file as a hit only if
its ``.meta`` companion hashes to the filename. A file that fails that check is deleted and reported as
a miss, which is how entries from an older key scheme clean themselves up.

This module imports the standard library, ``hisim.log`` and its own package only.
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
    """The result of one lookup: where the entry is, whether it exists, and what identifies it.

    Use :meth:`writing` to create the entry; never write to :attr:`path` directly, because a reader may
    see a half-written file. ``exists`` is advisory: two processes can both miss the same key and both
    compute the entry, which wastes work but is harmless, since the result is a pure function of the key.
    """

    #: The absolute path the entry is, or will be, filed at.
    path: str

    #: Whether a validated entry was found at :attr:`path`.
    exists: bool

    #: The raw string the entry's name is hashed from; recorded beside the entry when it is written.
    key_material: str

    @contextmanager
    def writing(self) -> Iterator[str]:
        """Yield a temporary path to write into; on exit, move it and its ``.meta`` file into place atomically.

        A wrapper around :func:`hisim.caching.atomic_cache_write` that fills in this entry's own path and
        key material.

        Yields:
            str: the temporary path to write to.
        """
        with atomic_cache_write(self.path, self.key_material) as temporary_path:
            yield temporary_path


class CacheClient:
    """Looks cache entries up in the configured tiers.

    One tier exists today (the local directory). The remote and shared-directory tiers are added behind
    :meth:`lookup` in later phases.
    """

    #: The extension every local entry carries.
    SUFFIX: ClassVar[str] = ".cache"

    def __init__(self, settings: CacheSettings) -> None:
        """Bind the client to one set of settings.

        Args:
            settings: the settings to use, normally from :meth:`CacheSettings.from_environment`.
        """
        self.settings = settings

    @classmethod
    def from_environment(cls) -> "CacheClient":
        """Create a client from the process environment.

        Returns:
            CacheClient: the client.
        """
        return cls(CacheSettings.from_environment())

    @classmethod
    def entry_filename(cls, component_key: str, digest: str) -> str:
        """Return the filename of an entry: ``<component_key>_<digest>.cache``.

        Args:
            component_key: the filename prefix, today the component's instance name.
            digest: the sha256 hex digest of the key material.

        Returns:
            str: the filename.
        """
        return f"{component_key}_{digest}{cls.SUFFIX}"

    def lookup(self, component_key: str, key_material: str, default_directory: str) -> CacheEntry:
        """Look an entry up in the local directory.

        Creates the directory if needed. An existing file counts as a hit only if its ``.meta`` companion
        hashes to the filename; otherwise it is deleted and the lookup is a miss.

        Args:
            component_key: the filename prefix.
            key_material: the string that identifies the entry's inputs; its hash is the filename's digest.
            default_directory: the directory to use unless ``HISIM_CACHE_DIR`` overrides it.

        Returns:
            CacheEntry: with ``exists`` True only for a validated hit.
        """
        directory = self.settings.resolve_local_directory(default_directory)
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, self.entry_filename(component_key, CacheEntryMetadata.hash_of(key_material)))
        return CacheEntry(path=path, exists=self._validated_local_hit(path), key_material=key_material)

    @staticmethod
    def _validated_local_hit(path: str) -> bool:
        """Return True if ``path`` is a file whose ``.meta`` companion hashes to its own name.

        A file that fails the check is deleted and a warning is logged. It either predates the metadata
        scheme or was written under a different key scheme; in both cases recomputing is the right answer.

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
        """Return one line naming the tiers this client uses, for logs.

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
    """Return a client for the given settings, or for the environment when none are given.

    Args:
        settings: settings to use; ``None`` reads the environment now.

    Returns:
        CacheClient: the client.
    """
    return CacheClient(settings) if settings is not None else CacheClient.from_environment()
