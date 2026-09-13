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
* The energy management system's dynamic dispatch ports are named ``<prefix><source weight>``
  instead of ``<prefix>Output<N>``, so a port's name no longer depends on how many unrelated
  ports were declared before it.
* An energy management system grows a dispatch port only for a device the run actually has and
  actually measures; the phantom targets absent devices used to get are gone.
* Both changes rename result columns: postprocessing that reads a dispatch column by its old
  ``...Output<N>`` name, and stored reference results holding those names, have to be updated.
