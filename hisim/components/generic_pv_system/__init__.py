"""PV system module.

Simulates the electricity generation of a photovoltaic installation: the AC power of the array for
every timestep, computed from the weather series, the array's geometry and the module and inverter
parameters of its pvlib database, and scaled by the installed peak power.

Note on the package layout:
    This package was split mechanically (zero behaviour change) from a single ``generic_pv_system.py``
    and the producer module beside it, following the ``hisim.components.weather`` and
    ``hisim.components.building`` precedent -- one component and its helpers, one directory:

    - ``calculation.py`` -- the static producer: the database readers, the two performance models and
      the plane-of-array irradiance, plus :class:`PVLibModuleAndInverterEnum`, which names the database
      and is therefore key material. It is a pure calculation with a deliberately tiny import closure,
      because that closure is hashed into the cache key (``roadmap/cache_service_spec.md`` §3); the
      layering rule that keeps it tiny is the reason the enum lives there and not in ``config.py``,
      whose ``hisim.config`` import reaches the component machinery a producer may not.
    - ``config.py`` -- :class:`PVSystemConfig`, its factories and the ``pv_peak_power_in_watt`` sizing
      fact.
    - ``pv_system.py`` -- the :class:`PVSystem` component: inputs and outputs, the cache lookup, the
      cost and KPI declarations and the predictive forecast.

    This ``__init__`` re-exports every public name, so ``hisim.components.generic_pv_system.PVSystem``
    and the other names keep resolving exactly as before, both for Python imports and for the
    fully-qualified class-name strings in the recorded energy-system files.

Note on ``PVSystem.__module__``:
    It is pinned to this package below. ``get_full_classname`` and the energy-system recorder derive a
    component's class path from ``__module__``, and 27 recorded twins plus the generated JSON schema
    spell the PV system ``hisim.components.generic_pv_system.PVSystem``. Without the pin the split
    would rewrite all of them to ``hisim.components.generic_pv_system.pv_system.PVSystem`` -- a change
    of the public wire format for no gain, in a commit whose whole point is that nothing changes. The
    one cost is that ``inspect.getsourcelines(PVSystem)`` no longer finds the class body (the overview
    generator already handles that case and reports a zero line count); imports, pickling and
    ``describe`` are unaffected.
"""

# clean

from hisim.components.generic_pv_system.calculation import (
    ARTIFACT_KIND,
    OUTPUT_COLUMN,
    PVLibModuleAndInverterEnum,
    PvSeriesInputs,
    PvWeatherSeries,
    content_hash,
    produce_pv_series,
)
from hisim.components.generic_pv_system.config import PVSystemConfig
from hisim.components.generic_pv_system.pv_system import PVSystem

__authors__ = "Vitor Hugo Bellotto Zago, Kristina Dabrock"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt", "Kristina Dabrock"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Kristina Dabrock"

# The public name of this class is the package, not the submodule; see the note above.
PVSystem.__module__ = __name__

__all__ = [
    "ARTIFACT_KIND",
    "OUTPUT_COLUMN",
    "PVLibModuleAndInverterEnum",
    "PVSystem",
    "PVSystemConfig",
    "PvSeriesInputs",
    "PvWeatherSeries",
    "content_hash",
    "produce_pv_series",
]
