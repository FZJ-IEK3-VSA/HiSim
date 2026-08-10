"""Configuration helpers for the Building Sizer, an external automated component-sizing tool.

The :mod:`hisim.building_sizer_utils` package collects the dataclasses and
helpers that describe a household energy system for the Building Sizer's
optimization loop. A
:class:`~hisim.building_sizer_utils.interface_configs.modular_household_config.ModularHouseholdConfig`
bundles a technological equipment description
(:class:`~hisim.building_sizer_utils.interface_configs.system_config.EnergySystemConfig`)
with a household framework description
(:class:`~hisim.building_sizer_utils.interface_configs.archetype_config.ArcheTypeConfig`),
and the ``get_default_config_for_household_*`` classmethods provide ready-made
presets per heating system. The
:class:`~hisim.building_sizer_utils.interface_configs.kpi_config.KPIForRatingInOptimization`
enum lists the KPIs that the Building Sizer may select as its optimization
objective. The module-level helpers
:func:`~hisim.building_sizer_utils.interface_configs.modular_household_config.write_config`
and
:func:`~hisim.building_sizer_utils.interface_configs.modular_household_config.read_in_configs`
serialise a configuration to and from JSON, and
:meth:`~hisim.building_sizer_utils.interface_configs.modular_household_config.ModularHouseholdConfig.get_hash`
provides a stable hash for caching and deduplication.

In HiSim's workflow these configs are consumed by the ``system_setups/`` setup
functions and the RenoVisor runner to assemble a simulation, while the
postprocessing stage writes the resulting KPIs back to JSON so the Building
Sizer can iterate on the component sizing. For the wider simulation context --
the simulation engine, component model, and per-timestep data flow into which
these configs feed -- see the :doc:`/architecture` guide.
"""
