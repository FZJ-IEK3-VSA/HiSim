"""Public API of the HiSim cache client (``roadmap/cache_service_spec.md`` §4).

Phase 1 of §10 is implemented: the local tier (``local``), the settings (``settings``), the key scheme
(``keys``) and a client with one tier behind it (``client``). The remote and shared-directory tiers and
the dataset retrieval come with later phases and will be re-exported from here.

This package sits at the same layer as ``hisim/config/`` and is imported by components and by
``hisim/utils.py``. It therefore imports only the standard library and ``hisim.log`` -- never a component,
the simulator, the simulation parameters or the singleton repository. A test checks this in a fresh
interpreter.
"""

# clean

from hisim.caching.client import CacheClient, CacheEntry, default_client
from hisim.caching.keys import (
    CacheKey,
    CacheKeyError,
    CanonicalJson,
    Fingerprints,
    ImportClosure,
    KeyMaterial,
    ProducerLayering,
    ProducerLayeringError,
)
from hisim.caching.local import CacheEntryMetadata, atomic_cache_write
from hisim.caching.settings import CacheNetworkMode, CacheSettings, CacheSettingsError

__all__ = [
    "CacheClient",
    "CacheEntry",
    "CacheEntryMetadata",
    "CacheKey",
    "CacheKeyError",
    "CacheNetworkMode",
    "CacheSettings",
    "CacheSettingsError",
    "CanonicalJson",
    "Fingerprints",
    "ImportClosure",
    "KeyMaterial",
    "ProducerLayering",
    "ProducerLayeringError",
    "atomic_cache_write",
    "default_client",
]
