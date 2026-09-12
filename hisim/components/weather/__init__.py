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
      is hashed into the cache key (``roadmap/cache_service_spec.md`` §3); the layering rule that keeps
      it tiny is the reason the enum lives there and not in ``config.py``, whose ``hisim.config`` import
      reaches the component machinery a producer may not.
    - ``config.py`` -- :class:`WeatherConfig`, the station catalogue :class:`LocationEnum` and the
      ``weather_identity`` sizing fact.
    - ``weather.py`` -- the :class:`Weather` component: outputs, the cache lookup, the repository
      entries and the 24 h forecast.

    This ``__init__`` re-exports every public name, so ``hisim.components.weather.Weather`` and the
    other classes keep resolving exactly as before, both for Python imports and for the
    fully-qualified class-name strings in the recorded energy-system files.

Note on ``Weather.__module__``:
    It is pinned to this package below. ``get_full_classname`` and the energy-system recorder derive a
    component's class path from ``__module__``, and 33 recorded twins plus the generated JSON schema
    spell the weather ``hisim.components.weather.Weather``. Without the pin the split would rewrite all
    of them to ``hisim.components.weather.weather.Weather`` -- a change of the public wire format for
    no gain, in a commit whose whole point is that nothing changes. The one cost is that
    ``inspect.getsourcelines(Weather)`` no longer finds the class body (the overview generator already
    handles that case and reports a zero line count); imports, pickling and ``describe`` are unaffected.
"""

# clean

from hisim.components.weather.calculation import (
    ARTIFACT_KIND,
    WeatherDataSourceEnum,
    WeatherSeriesInputs,
    WeatherSourceFiles,
    calculate_direct_normal_irradiance_in_watt_per_square_meter,
    get_coordinates,
    produce_weather_series,
)
from hisim.components.weather.config import LocationEnum, WeatherConfig
from hisim.components.weather.weather import Weather

__authors__ = "Vitor Hugo Bellotto Zago, Noah Pflugradt"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Noah Pflugradt"

# The public name of this class is the package, not the submodule; see the note above.
Weather.__module__ = __name__

__all__ = [
    "ARTIFACT_KIND",
    "LocationEnum",
    "Weather",
    "WeatherConfig",
    "WeatherDataSourceEnum",
    "WeatherSeriesInputs",
    "WeatherSourceFiles",
    "calculate_direct_normal_irradiance_in_watt_per_square_meter",
    "get_coordinates",
    "produce_weather_series",
]
