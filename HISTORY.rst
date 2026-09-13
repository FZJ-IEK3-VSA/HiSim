=======
History
=======

0.1.0 (2021-09-29)
------------------

* First release.

0.1.1 (2026-05-16)
------------------

* Added a reusable NightSetbackController component for temperature setback during configurable night hours.

0.1.2 (2026-09-13)
------------------

* The last two keys of the process-global ``SingletonSimRepository`` are gone:
  ``RESULT_SCENARIO_NAME`` and ``DESCRIPTION`` are now ``Simulator.scenario_name`` and
  ``Simulator.description``, two plain attributes of the run, carried to post-processing on the
  ``PostProcessingDataTransfer``. An external script that wrote either key has to set the
  attribute on its simulator instead; the enum members no longer exist.
* A declarative run now carries the energy-system file's ``name`` as its pyam scenario — with
  the option every variant selected appended, as ``Household gas + solar [collector=large]`` —
  and the file's ``description`` in ``scenario.json``. A Python run that never names itself is
  named after its module file instead of publishing an empty scenario.
* ``hisim.components.controller_l1_chp`` is gone; the L1 CHP controller lives in ``hisim.components.generic_chp`` (import it from the package).
* The weather series is produced by ``hisim.components.weather.calculation`` and cached under a key
  that carries a fingerprint of that module's own source, so an edit to the calculation can no longer
  be served from an entry written before it.
* Breaking, for importers of the reader functions: the seven readers of the old
  ``hisim/components/weather.py`` are no longer importable from ``hisim.components.weather``. Six of
  them -- ``read_dwd_try_data``, ``read_nsrdb_data``, ``read_nsrdb_15min_data``,
  ``read_dwd_10min_data``, ``read_dwd_15min_data`` and ``read_era5_data`` -- live in
  ``hisim.components.weather.calculation`` and can be imported from there; the seventh,
  ``read_test_reference_year_data``, is gone, and ``produce_weather_series`` of that module is what
  produces the processed series now. The component's own names
  (``Weather``, ``WeatherConfig``, ``LocationEnum``, ``WeatherDataSourceEnum``, ``get_coordinates`` and
  ``calculate_direct_normal_irradiance_in_watt_per_square_meter``) resolve from the package as before.
