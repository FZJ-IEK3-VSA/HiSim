"""Configuration layer standardizing data exchange between HiSim and external tools.

The :mod:`hisim.building_sizer_utils.interface_configs` package holds the
dataclass-based configuration objects that define the boundary contract for
HiSim's "modular household" framework.  These configs are the single typed
representation used whenever a residential energy-system setup is described
to, or read back from, another system.  Every exchange is serialised as JSON
through :mod:`dataclasses_json`, so the same dataclass serves both the
in-process simulator and the file/network interface.

Bridged systems
---------------

The package is consumed by three integration boundaries:

* **Building Sizer** (external evolutionary optimiser) -- HiSim writes the
  simulation's key performance indicators to a
  ``*_kpi_config_for_building_sizer.json`` file as a
  :class:`~hisim.building_sizer_utils.interface_configs.kpi_config.KPIConfig`,
  and reads back an optimiser-proposed
  :class:`~hisim.building_sizer_utils.interface_configs.modular_household_config.ModularHouseholdConfig`
  (via
  :func:`~hisim.building_sizer_utils.interface_configs.modular_household_config.read_in_configs`)
  describing the next setup to evaluate.  The
  :class:`~hisim.building_sizer_utils.interface_configs.kpi_config.KPIForRatingInOptimization`
  enum selects which KPI acts as the optimisation fitness function.

* **RenoVisor** (external request interface) -- the
  :mod:`hisim.renovisor.mapping` translator converts a RenoVisor home-inventory
  JSON request into a
  :class:`~hisim.building_sizer_utils.interface_configs.modular_household_config.ModularHouseholdConfig`
  for simulation.

* **Modular-household system setups** (internal) -- the
  ``system_setups/*_building_sizer.py`` entry points construct
  :class:`~hisim.building_sizer_utils.interface_configs.modular_household_config.ModularHouseholdConfig`
  instances in-process, and HiSim post-processing emits the matching
  :class:`~hisim.building_sizer_utils.interface_configs.kpi_config.KPIConfig`.

Modules
-------

* :mod:`hisim.building_sizer_utils.interface_configs.archetype_config` --
  :class:`~hisim.building_sizer_utils.interface_configs.archetype_config.ArcheTypeConfig`
  describes the household framework (building identity, construction type,
  floor area, household composition, weather/location, and PV orientation).

* :mod:`hisim.building_sizer_utils.interface_configs.system_config` --
  :class:`~hisim.building_sizer_utils.interface_configs.system_config.EnergySystemConfig`
  describes the technological equipment (heating system, heat distribution,
  PV share, battery/EMS enable flag).

* :mod:`hisim.building_sizer_utils.interface_configs.modular_household_config` --
  :class:`~hisim.building_sizer_utils.interface_configs.modular_household_config.ModularHouseholdConfig`
  bundles an archetype and an energy-system config into a single
  simulation-ready, JSON-serialisable configuration, plus the ``write_config``,
  ``read_in_configs`` and ``get_hash`` helpers.

* :mod:`hisim.building_sizer_utils.interface_configs.kpi_config` --
  :class:`~hisim.building_sizer_utils.interface_configs.kpi_config.KPIConfig`
  collects the post-processing KPIs and
  :class:`~hisim.building_sizer_utils.interface_configs.kpi_config.KPIForRatingInOptimization`
  selects the optimisation rating metric.

Simulation pipeline
-------------------

The submodules form a closed loop around the core simulator.  A
:class:`~hisim.building_sizer_utils.interface_configs.modular_household_config.ModularHouseholdConfig`
-- bundling an
:class:`~hisim.building_sizer_utils.interface_configs.archetype_config.ArcheTypeConfig`
with an
:class:`~hisim.building_sizer_utils.interface_configs.system_config.EnergySystemConfig`
-- is read by a ``system_setups/*_building_sizer.py`` entry point (via
:func:`~hisim.building_sizer_utils.interface_configs.modular_household_config.read_in_configs`),
which wires the corresponding components and runs the time-step simulation.
Post-processing then collects the results into a
:class:`~hisim.building_sizer_utils.interface_configs.kpi_config.KPIConfig`
and writes it to ``*_kpi_config_for_building_sizer.json``; the external
Building Sizer reads that file, selects the fitness metric through
:class:`~hisim.building_sizer_utils.interface_configs.kpi_config.KPIForRatingInOptimization`,
and proposes the next ``ModularHouseholdConfig`` to evaluate.

Data format
-----------

All configs are :mod:`dataclasses_json`-annotated dataclasses serialised to
JSON; ``ModularHouseholdConfig`` additionally inherits
:class:`~hisim.system_setup_configuration.SystemSetupConfigBase` for
JSON-serialisation support.  An example of the on-disk layout is provided in
``example_modular_household_config.json``.

Quick-start
-----------

A complete simulation configuration is assembled from an archetype and an
energy-system config, bundled into a
:class:`~hisim.building_sizer_utils.interface_configs.modular_household_config.ModularHouseholdConfig`,
and exchanged as JSON.  The
:class:`~hisim.building_sizer_utils.interface_configs.kpi_config.KPIConfig`
is *not* supplied as an input: it is emitted by HiSim post-processing once the
simulation has run, and
:class:`~hisim.building_sizer_utils.interface_configs.kpi_config.KPIForRatingInOptimization`
selects which of its fields the Building Sizer optimises against.  Building a
run configuration in-process and round-tripping it through the JSON contract::

    >>> from hisim.building_sizer_utils.interface_configs.archetype_config import (
    ...     ArcheTypeConfig,
    ... )
    >>> from hisim.building_sizer_utils.interface_configs.system_config import (
    ...     EnergySystemConfig,
    ... )
    >>> from hisim.building_sizer_utils.interface_configs.modular_household_config import (
    ...     ModularHouseholdConfig,
    ... )
    >>> from hisim.loadtypes import HeatingSystems
    >>> archetype = ArcheTypeConfig()  # default German SFH in Aachen
    >>> system = EnergySystemConfig(heating_system=HeatingSystems.HEAT_PUMP)
    >>> household = ModularHouseholdConfig(
    ...     archetype_config_=archetype, energy_system_config_=system
    ... )
    >>> serialized = household.to_json()  # JSON contract for the Building Sizer
    >>> restored = ModularHouseholdConfig.from_json(serialized)

For on-disk exchange, :func:`~hisim.building_sizer_utils.interface_configs.modular_household_config.write_config`
writes a ``ModularHouseholdConfig`` to ``modular_example_config.json`` and
:func:`~hisim.building_sizer_utils.interface_configs.modular_household_config.read_in_configs`
reads one back from a caller-supplied path.  A ``system_setups/*_building_sizer.py``
entry point then consumes the restored config to wire and run the matching
simulation, after which post-processing writes the resulting
:class:`~hisim.building_sizer_utils.interface_configs.kpi_config.KPIConfig`
to ``*_kpi_config_for_building_sizer.json`` for the optimiser to read.
"""
