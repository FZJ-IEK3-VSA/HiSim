"""Public API of the HiSim cache service client.

The package is the home of everything the simulation knows about caching, as laid out in
``roadmap/cache_service_spec.md`` §4: local paths and atomic writes, key construction, the remote
HTTP tier, curated-dataset retrieval and the client that orchestrates them. Phase 1 of §10 is what
exists: the local tier, the settings, the key scheme and a client with one tier behind it. The remote
and shared-directory tiers and the dataset retrieval arrive with the later phases and are re-exported
from here when they do.

Importing this package must stay cheap and free of side effects, because it sits at the same layer as
``hisim/config/`` and is imported by both components and ``hisim/utils.py``. It therefore imports the
standard library and ``hisim.log`` only -- never a component, the simulator, the simulation parameters
or the singleton repository. A test pins that rule.
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
