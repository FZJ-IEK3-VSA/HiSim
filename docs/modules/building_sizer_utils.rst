:orphan:

Building Sizer Utilities
========================

The ``hisim.building_sizer_utils`` package provides the configuration layer
for sizing and comparing residential energy-system setups — the reusable
"modular household" framework driven by parameterised dataclasses and used
in evolutionary-optimisation runs.

Working Principle
-----------------

The package is organised as a single subpackage,
:mod:`hisim.building_sizer_utils.interface_configs`, which contains four
dataclass-based configuration modules:

* **Archetype config** —
  :class:`~hisim.building_sizer_utils.interface_configs.archetype_config.ArcheTypeConfig`
  describes the household framework: building identity and construction type,
  conditioned floor area, household composition (via LPG household profiles),
  weather-data source and location, building geolocation, and PV-panel
  orientation (``pv_azimuth`` clockwise from north in degrees,
  ``pv_tilt`` from the horizontal in degrees).  It also carries rooftop PV
  capacity/generation estimates, norm heating load, and neighbourhood density
  parameters used for solar-potential calculations.

* **Energy system config** —
  :class:`~hisim.building_sizer_utils.interface_configs.system_config.EnergySystemConfig`
  describes the technological equipment: heating-system type
  (:class:`~hisim.loadtypes.HeatingSystems`), heat-distribution system
  (:class:`~hisim.loadtypes.ComponentType`), rooftop PV share
  (``share_of_maximum_pv_potential``), and whether a battery with
  energy-management system is enabled (``use_battery_and_ems``).  The
  :meth:`~hisim.building_sizer_utils.interface_configs.system_config.EnergySystemConfig.get_default_config`
  classmethod creates ready-made defaults for any heating-system variant and is
  the single open extension point (adding a new
  :class:`~hisim.loadtypes.HeatingSystems` member needs no new method).  The
  per-system ``get_default_config_for_energy_system_*`` methods are deprecated
  convenience aliases that delegate to it and are scheduled for removal in a
  future release.

* **Modular household config** —
  :class:`~hisim.building_sizer_utils.interface_configs.modular_household_config.ModularHouseholdConfig`
  bundles an ``archetype_config_`` and an ``energy_system_config_`` into a
  single simulation-ready configuration.  It inherits from
  :class:`~hisim.system_setup_configuration.SystemSetupConfigBase`, gaining
  JSON-serialisation support.  Convenience classmethods such as
  :meth:`~hisim.building_sizer_utils.interface_configs.modular_household_config.ModularHouseholdConfig.get_default_config_for_household_gas`
  pair a specific heating system with a default archetype.  Module-level
  helpers :func:`~hisim.building_sizer_utils.interface_configs.modular_household_config.write_config`
  and :func:`~hisim.building_sizer_utils.interface_configs.modular_household_config.read_in_configs`
  serialise and deserialise the configuration to / from JSON, and
  :meth:`~hisim.building_sizer_utils.interface_configs.modular_household_config.ModularHouseholdConfig.get_hash`
  provides a stable hash for caching and deduplication.

* **KPI config** —
  :class:`~hisim.building_sizer_utils.interface_configs.kpi_config.KPIConfig`
  collects the key performance indicators produced by post-processing
  (costs, emissions, self-sufficiency, indoor temperatures).  The companion
  :class:`~hisim.building_sizer_utils.interface_configs.kpi_config.KPIForRatingInOptimization`
  enum selects which KPI is used as the fitness function in evolutionary
  optimisation.  The
  :meth:`~hisim.building_sizer_utils.interface_configs.kpi_config.KPIConfig.get_kpi_for_rating`
  method resolves an enum value to the corresponding measured metric.

API Reference
-------------

Full autodoc-generated documentation for every class and function in this
module is available on the :doc:`../modularexampleinterfaces` page.
