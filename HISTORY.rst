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
