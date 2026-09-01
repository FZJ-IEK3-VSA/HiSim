"""Public API of the HiSim cache service client.

The package is the home of everything the simulation knows about caching, as laid out in
``roadmap/cache_service_spec.md`` §4: local paths and atomic writes, key construction, the remote
HTTP tier, curated-dataset retrieval and the client that orchestrates them. Only the local tier of
§10 phase 1 has been built, so the public surface is deliberately one context manager; the remaining
modules arrive with the later phases and this file is where they will be re-exported from.

Importing this package must stay cheap and free of side effects, because it sits at the same layer as
``hisim/config/`` and is imported by both components and ``hisim/utils.py``. It therefore imports the
standard library only -- never a component, the simulator or the singleton repository.
"""

# clean

from hisim.caching.local import atomic_cache_write

__all__ = ["atomic_cache_write"]
