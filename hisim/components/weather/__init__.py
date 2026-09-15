"""Weather module.

Provides the thermal and solar conditions a simulation runs under: air temperature, the three
irradiance components and the extraterrestrial one, sun position, wind speed and pressure, read from a
weather data file and resampled to the simulation's timestep. Almost every other component depends on
it -- the building through its solar gains and its heat losses, the PV system, the heat pump, the solar
thermal collector -- which is why its series is the one artifact everything else is keyed against.

Note on the package layout:
    This package was split mechanically (zero behaviour change) from a single ``weather.py`` and the
    producer module beside it, following the ``hisim.components.building`` precedent:

    - ``calculation.py`` -- the static producer: the readers, the resampling, the sun position and the
      daily average, plus :class:`WeatherDataSourceEnum`, which names the reader and is therefore key
      material. It is a pure calculation with a deliberately tiny import closure, because that closure
      is hashed into the cache key (``roadmap/cache_service_spec.md`` §3). That is why the enum lives
      there and not in ``config.py``: a configuration module has to import ``hisim.config``, and the
      closure of ``hisim.config`` -- which is computed by reading source, so every ``import`` inside a
      function body counts as much as one at the top -- spans 45 modules and reaches ``hisim.component``,
      the post-processing and the repository. Hashing those into every weather key would throw the
      cached series away on edits that cannot change a single number in it.
    - ``config.py`` -- :class:`WeatherConfig`, the station catalogue :class:`LocationEnum` and the
      ``weather_identity`` sizing fact.
    - ``weather.py`` -- the :class:`Weather` component: outputs, the cache lookup, the repository
      entries and the 24 h forecast.

Note on the lazy facade:
    The names below resolve on first access (PEP 562), so that importing
    ``hisim.components.weather.calculation`` -- what a prewarm run filling the cache does, and what the
    layering rule exists to keep cheap -- does not drag the component, the simulator and the
    post-processing in through this ``__init__``. ``hisim.components.weather.Weather`` still works, for
    plain imports as much as for ``importlib.import_module`` plus ``getattr`` on a recorded class path,
    for ``hisim energy-system describe`` and for pickling; the only difference is when the submodule is
    imported.

Note on ``Weather.__module__``:
    It is pinned to this package, in ``weather.py`` beside the class rather than here, so that the class
    carries its public name however it was reached -- the pin must hold for
    ``from hisim.components.weather.weather import Weather`` too, and a lazy facade would not have run
    yet. ``get_full_classname`` and the energy-system recorder derive a component's class path from
    ``__module__``, and 33 recorded twins plus the generated JSON schema spell the weather
    ``hisim.components.weather.Weather``.
"""

# clean

# The annotations below are never evaluated (PEP 563), which keeps the typing names they use out of
# this module's namespace: a facade whose ``dir()`` lists ``Dict`` beside ``Weather`` is a worse facade.
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Dict, List

    # For the type checker only. It follows imports, not module-level ``__getattr__``, so without these
    # every ``weather.Weather`` in the code base would be typed ``Any`` and no annotation on it would
    # mean anything. At runtime the names below resolve through ``__getattr__``; nothing is imported here.
    from hisim.components.weather.calculation import (
        WeatherDataSourceEnum,
        calculate_direct_normal_irradiance_in_watt_per_square_meter,
        get_coordinates,
    )
    from hisim.components.weather.config import LocationEnum, WeatherConfig
    from hisim.components.weather.weather import Weather

__authors__ = "Vitor Hugo Bellotto Zago, Noah Pflugradt"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Noah Pflugradt"

#: The names this package answers to and the module each of them comes from. Every one of them was a
#: name of the single ``weather.py`` this package replaced, so an importer that predates the split
#: still resolves. The producer's own vocabulary -- its DTO, its artifact kind, its entry point -- is
#: deliberately not here: it belongs to ``hisim.components.weather.calculation``, and re-exporting it
#: would invite an import of the producer through a facade that also knows the component.
_MODULE_PER_NAME: Dict[str, str] = {
    "Weather": "hisim.components.weather.weather",
    "WeatherConfig": "hisim.components.weather.config",
    "LocationEnum": "hisim.components.weather.config",
    "WeatherDataSourceEnum": "hisim.components.weather.calculation",
    "get_coordinates": "hisim.components.weather.calculation",
    "calculate_direct_normal_irradiance_in_watt_per_square_meter": "hisim.components.weather.calculation",
}

#: Spelled out rather than derived from the mapping above, because a type checker reads ``__all__`` to
#: decide what this package re-exports and cannot evaluate a comprehension. ``tests/test_weather.py``
#: asserts the two lists say the same thing.
__all__ = [
    "LocationEnum",
    "Weather",
    "WeatherConfig",
    "WeatherDataSourceEnum",
    "calculate_direct_normal_irradiance_in_watt_per_square_meter",
    "get_coordinates",
]


def __getattr__(name: str) -> Any:
    """Import the module a public name lives in, the first time the name is asked for (PEP 562).

    Args:
        name: the attribute asked of this package.

    Returns:
        Any: the class, enum or function of that name.

    Raises:
        AttributeError: if this package has no such name, as a plain module would.
    """
    module_name = _MODULE_PER_NAME.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module_name), name)
    # Bind it, so the next access is an ordinary attribute lookup rather than another import call.
    globals()[name] = value
    return value


def __dir__() -> List[str]:
    """List the lazy names beside the ones already bound, so completion and ``dir()`` see them.

    Returns:
        List[str]: the package's attribute names.
    """
    return sorted(set(globals()) | set(_MODULE_PER_NAME))
