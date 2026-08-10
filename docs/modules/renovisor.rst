:orphan:

RenoVisor Translator
====================

.. automodule:: hisim.renovisor
   :members:
   :undoc-members:
   :show-inheritance:

The :mod:`hisim.renovisor` package is a CLI-driven translator that takes a
JSON request containing a home inventory (per the RenoVisor contract), selects
a matching ``*_building_sizer`` system setup, generates a
:class:`~hisim.building_sizer_utils.interface_configs.modular_household_config.ModularHouseholdConfig`
parameter file, runs the simulation, and submits selected result files to a
server via REST.

Working Principle
-----------------

The translator follows a linear pipeline with explicit exit codes for each
failure mode:

1. **Validate** (:mod:`hisim.renovisor.schema`) — parse and validate the
   wrapper envelope and the RenoVisor request fields the translator depends on.
   :func:`~hisim.renovisor.schema.parse_translator_input` returns a
   :class:`~hisim.renovisor.schema.TranslatorInput` or raises
   :class:`~hisim.renovisor.schema.RequestValidationError`, which the CLI
   maps to exit code 2. Unknown keys are ignored for forward compatibility.

2. **Translate** (:mod:`hisim.renovisor.mapping`) —
   :func:`~hisim.renovisor.mapping.translate` performs pure logic (no I/O
   beyond the cached TABULA index) to map the request to a system-setup file
   and a :class:`~hisim.building_sizer_utils.interface_configs.modular_household_config.ModularHouseholdConfig`,
   returning a :class:`~hisim.renovisor.mapping.TranslationResult`.
   The :mod:`hisim.renovisor.tabula_ie` submodule resolves the TABULA building
   code from country, dwelling type, construction year, and refurbishment
   variant via :func:`~hisim.renovisor.tabula_ie.select_building_code`.
   When the ``measures`` variant is requested,
   :func:`~hisim.renovisor.measures.apply_measures` applies renovation measures
   to a deep copy of the home inventory before mapping.
   :func:`~hisim.renovisor.mapping.build_mapping_report_dict` serialises the
   :class:`~hisim.renovisor.mapping.MappingReport` alongside the results.

3. **Run** (:mod:`hisim.renovisor.runner`) —
   :func:`~hisim.renovisor.runner.build_simulation_parameters` constructs
   :class:`~hisim.simulationparameters.SimulationParameters` from defaults
   plus any overrides, then :func:`~hisim.renovisor.runner.run_simulation`
   writes the module config and invokes the selected setup in-process through
   :func:`~hisim.hisim_main.main`. Simulation failures map to exit code 3,
   with a failure report posted to the submission URL.

4. **Upload** (:mod:`hisim.renovisor.uploader`) —
   :func:`~hisim.renovisor.uploader.match_result_files` collects result files
   matching the requested glob patterns (the mapping report is always included
   by :mod:`hisim.renovisor.__main__`), then
   :func:`~hisim.renovisor.uploader.post_success` POSTs them as a single
   ``multipart/form-data`` request. Network errors and 5xx responses are
   retried with exponential backoff; 4xx responses are contract errors and
   fail immediately with :class:`~hisim.renovisor.uploader.UploadError`.
   Upload failures map to exit code 4.

Submodules
----------

* :mod:`hisim.renovisor.schema` — parsing and validation of the translator
  input (wrapper envelope + RenoVisor request).
  :func:`~hisim.renovisor.schema.parse_translator_input` validates the
  structure and returns a :class:`~hisim.renovisor.schema.TranslatorInput`
  containing :class:`~hisim.renovisor.schema.SubmissionConfig`,
  :class:`~hisim.renovisor.schema.SimulationOverrides`, and the raw request
  dict. Validation failures raise
  :class:`~hisim.renovisor.schema.RequestValidationError` (CLI exit code 2).
  Unknown keys are ignored for forward compatibility.

* :mod:`hisim.renovisor.mapping` — pure translation of the request into a
  system-setup choice and a
  :class:`~hisim.building_sizer_utils.interface_configs.modular_household_config.ModularHouseholdConfig`,
  returning a :class:`~hisim.renovisor.mapping.TranslationResult` with a
  :class:`~hisim.renovisor.mapping.MappingReport` tracking how each input
  field was handled (used, approximated, defaulted, or ignored).
  :func:`~hisim.renovisor.mapping.build_mapping_report_dict` serialises the
  report for inclusion in the result directory. Unknown countries or missing
  TABULA data raise :class:`~hisim.renovisor.mapping.MappingError`.

* :mod:`hisim.renovisor.measures` —
  :func:`~hisim.renovisor.measures.apply_measures` applies renovation measures
  to a deep copy of the home inventory, returning a
  :class:`~hisim.renovisor.measures.MeasureApplication`. Envelope measures
  (listed in :data:`~hisim.renovisor.measures.ENVELOPE_MEASURE_TYPES`) are
  collected by distinct type and later folded into the TABULA refurbishment
  variant.

* :mod:`hisim.renovisor.tabula_ie` — TABULA building-code lookup.
  :func:`~hisim.renovisor.tabula_ie.select_building_code` builds an
  in-memory index of the generic example codes from the processed TABULA CSV
  (via :func:`~hisim.renovisor.tabula_ie._load_index`) and selects the code
  matching a country, building type, construction year, and refurbishment
  variant, returning a :class:`~hisim.renovisor.tabula_ie.BuildingCodeSelection`
  with nearest-neighbour fallbacks when the exact match is missing.
  :func:`~hisim.renovisor.tabula_ie.available_countries` lists all supported
  countries. Lookups fail with
  :class:`~hisim.renovisor.tabula_ie.TabulaLookupError`.

* :mod:`hisim.renovisor.runner` — in-process execution of the selected system
  setup. :func:`~hisim.renovisor.runner.build_simulation_parameters` creates
  :class:`~hisim.simulationparameters.SimulationParameters` from defaults plus
  overrides. :func:`~hisim.renovisor.runner.resolve_setup_path` locates the
  setup file in the ``system_setups`` directory, and
  :func:`~hisim.renovisor.runner.run_simulation` writes the module config and
  invokes the setup through :func:`~hisim.hisim_main.main`.

* :mod:`hisim.renovisor.uploader` — REST submission of lifecycle events and
  result files. :func:`~hisim.renovisor.uploader.match_result_files` collects
  files matching glob patterns. :func:`~hisim.renovisor.uploader.post_started`,
  :func:`~hisim.renovisor.uploader.post_failure`, and
  :func:`~hisim.renovisor.uploader.post_success` handle the respective
  multipart uploads with exponential-backoff retry on network errors and 5xx
  responses. Failures raise
  :class:`~hisim.renovisor.uploader.UploadError`.

* :mod:`hisim.renovisor.__main__` — command-line interface. Provides the
  ``run`` subcommand with ``--variant``, ``--result-dir``, ``--no-upload``,
  and ``--keep-files`` options. :func:`~hisim.renovisor.__main__.main` is the
  entry point. Exit codes: 0 (success), 2 (validation failed), 3 (simulation
  failed), 4 (upload failed).

API Reference
-------------

hisim.renovisor.schema module
-----------------------------

.. automodule:: hisim.renovisor.schema
   :members:
   :undoc-members:
   :show-inheritance:

hisim.renovisor.mapping module
------------------------------

.. automodule:: hisim.renovisor.mapping
   :members:
   :undoc-members:
   :show-inheritance:

hisim.renovisor.measures module
-------------------------------

.. automodule:: hisim.renovisor.measures
   :members:
   :undoc-members:
   :show-inheritance:

hisim.renovisor.tabula_ie module
--------------------------------

.. automodule:: hisim.renovisor.tabula_ie
   :members:
   :undoc-members:
   :show-inheritance:

hisim.renovisor.runner module
-----------------------------

.. automodule:: hisim.renovisor.runner
   :members:
   :undoc-members:
   :show-inheritance:

hisim.renovisor.uploader module
-------------------------------

.. automodule:: hisim.renovisor.uploader
   :members:
   :undoc-members:
   :show-inheritance:

hisim.renovisor.__main__ module
-------------------------------

.. automodule:: hisim.renovisor.__main__
   :members:
   :undoc-members:
   :show-inheritance:
