"""Supply-side energy-system configuration for HiSim's modular household framework.

This module defines :class:`EnergySystemConfig`, the *supply-side* half of the
modular-household boundary contract (see
:mod:`hisim.building_sizer_utils.interface_configs`). It configures only the
technological equipment that supplies and converts energy for a household --
the heat source (HVAC), the heat-distribution emitters, on-site photovoltaic
generation, and the battery storage together with its energy-management
controller. It deliberately excludes the demand-side parameters (building
envelope, climate, and occupancy via LPG profiles), which live in the
:class:`~hisim.building_sizer_utils.interface_configs.archetype_config.ArcheTypeConfig`.
The two halves are bundled by
:class:`~hisim.building_sizer_utils.interface_configs.modular_household_config.ModularHouseholdConfig`.

Configurable components
-----------------------

The four fields of :class:`EnergySystemConfig` select the supply-side hardware:

- ``heating_system`` (:class:`~hisim.loadtypes.HeatingSystems`): the heat
  source for space heating and domestic hot water. Members range from
  combustion systems (gas, oil, pellet, wood-chip, hydrogen) through district
  heating and direct electric heating, to heat pumps and solar-thermal hybrids
  (gas + solar thermal, heat pump + solar thermal).
- ``heat_distribution_system`` (:class:`~hisim.loadtypes.ComponentType`): the
  emitter that delivers heat to the building. Valid values are
  :attr:`~hisim.loadtypes.ComponentType.HEAT_DISTRIBUTION_SYSTEM_FLOORHEATING`
  and
  :attr:`~hisim.loadtypes.ComponentType.HEAT_DISTRIBUTION_SYSTEM_RADIATOR`;
  the consuming system setups reject any other value.
- ``share_of_maximum_pv_potential`` (``float``): the fraction of the rooftop's
  maximum photovoltaic capacity to install, in the closed range ``[0.0, 1.0]``.
  ``0.0`` installs no PV; ``1.0`` (the default) installs the full rooftop
  potential derived downstream from the building's roof area.
- ``use_battery_and_ems`` (``bool``): a single flag that enables *both* the
  electrical battery storage and the L2 energy-management-system (EMS)
  controller that dispatches it. There is no separate battery-without-EMS or
  EMS-without-battery mode.

Data flow and dependencies
--------------------------

:class:`EnergySystemConfig` is a JSON-serialisable
:mod:`dataclasses_json` dataclass and the exclusive typed representation of the
supply side at every integration boundary of the modular-household framework:

* **Building Sizer optimiser** -- the optimiser proposes an
  :class:`EnergySystemConfig` inside a
  :class:`~hisim.building_sizer_utils.interface_configs.modular_household_config.ModularHouseholdConfig`;
  HiSim reads it back and runs the matching simulation.
* **RenoVisor translator** (:mod:`hisim.renovisor.mapping`) -- builds an
  :class:`EnergySystemConfig` from a RenoVisor home-inventory request.
* **Modular-household system setups** (``system_setups/*_building_sizer.py``)
  -- consume the config in-process to wire the concrete HiSim components.

Inside a system setup the config is translated to live components: the
``heating_system`` selects the heat-source component (and the setup asserts it
matches the file's intended system), ``heat_distribution_system`` selects the
heat-distribution type, ``share_of_maximum_pv_potential`` drives
:class:`~hisim.components.generic_pv_system.PVSystemConfig` scaling against the
building's roof area, and the battery is scaled to the resulting PV peak power
through ``BatteryConfig.get_scaled_battery``.

Control logic and sizing constraints
------------------------------------

The EMS and battery are coupled to PV generation: the system setups add them
*only* when ``share_of_maximum_pv_potential != 0`` **and**
``use_battery_and_ems`` is true. With no PV installed the battery and EMS are
omitted regardless of the flag, so ``use_battery_and_ems`` alone does not
guarantee a battery exists.

PV capacity is sized as a share of the rooftop potential rather than as an
absolute value; the absolute peak power is computed downstream from the
building's roof area and ``share_of_maximum_pv_potential``. The battery
capacity in turn follows the PV peak power, so storage scales with generation
rather than with demand.

Valid component combinations
----------------------------

Valid combinations are enforced by the consuming system setups rather than by
this dataclass. Each ``heating_system`` value is realised by a dedicated
``system_setups/household_<system>_building_sizer.py`` file that asserts the
configured ``heating_system`` matches its intended heat source and raises
:class:`ValueError` otherwise, so a given :class:`EnergySystemConfig` is only
valid for the setup that matches its ``heating_system``. The
``heat_distribution_system`` accepts only
:attr:`~hisim.loadtypes.ComponentType.HEAT_DISTRIBUTION_SYSTEM_FLOORHEATING` and
:attr:`~hisim.loadtypes.ComponentType.HEAT_DISTRIBUTION_SYSTEM_RADIATOR`; any
other value raises :class:`ValueError` in the consuming setup. Both emitters may
be combined with every supported ``heating_system``.

Efficiency models and control strategies
----------------------------------------

The four fields only *select* hardware; the concrete physical models live in
the consuming components and system setups, never in this config. For
traceability the downstream behaviour is:

* **Building thermal model** -- the demand-side envelope is integrated with a
  dynamic single-node 5R1C resistor-capacitor model following EN ISO 13790
  (implemented in :class:`~hisim.components.building.Building` and documented
  in :mod:`hisim.building_sizer_utils.interface_configs.archetype_config`).
  The simulation is therefore *dynamic*, not steady-state: the building's
  thermal mass is integrated over each time step, and this config exposes no
  envelope or thermal-mass overrides.
* **Heat-source efficiency** -- the efficiency models live in the consuming
  components and are not parameterised here:

  - **Combustion boilers** -- gas, oil, pellet, wood-chip, and hydrogen all
    use :class:`~hisim.components.generic_boiler.GenericBoiler`, whose
    combustion efficiency is interpolated linearly between a minimum and
    maximum value across the boiler's thermal-power range. Only the modulating
    boilers (gas, oil, hydrogen) traverse this curve via continuous part-load
    control; the non-modulating pellet and wood-chip boilers run on/off at
    full power and therefore only exercise the maximum-efficiency endpoint.
  - **Heat pumps** -- the coefficient of performance (COP) is derived from a
    temperature-dependent performance model fitted to manufacturer/database
    reference points
    (:class:`~hisim.components.more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLib`
    and :class:`~hisim.components.generic_heat_pump.GenericHeatPump`).
  - **Direct electric heating** -- a fixed efficiency (default 1.0)
    (:class:`~hisim.components.generic_electric_heating.ElectricHeating`).
  - **Solar-thermal collectors** -- the standard flat-plate collector
    efficiency curve (``eta_0``, ``a_1``, ``a_2`` via :mod:`oemof.thermal`).
  - **District heating** -- heat is supplied directly from the network with
    no on-site conversion losses, and the deliverable thermal power is capped
    at the connection's maximum load
    (:class:`~hisim.components.generic_district_heating.DistrictHeating`).
* **Setpoint and control strategies** -- the heat-source controllers switch
  the heat source on and off against the flow-temperature setpoint with
  hysteresis; modulating boilers (gas, oil, hydrogen) emit a continuous
  control signal between minimum and full power with enforced minimum run and
  idle times, while non-modulating boilers (pellet, wood-chip) run on/off at
  full power
  (:class:`~hisim.components.generic_boiler.GenericBoilerController`); the
  heat-pump controller
  (:class:`~hisim.components.more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLibControllerSpaceHeating`)
  switches the heat pump between heating and off against the flow-temperature
  setpoint with hysteresis; the heat-distribution controller
  (:class:`~hisim.components.heat_distribution_system.HeatDistributionController`)
  derives the heating flow/return setpoint temperatures from a heating curve on
  the daily average outside temperature and the heating-threshold outside
  temperature from the building's specific heating load; the L2
  energy-management system
  (:class:`~hisim.components.controller_l2_energy_management_system.L2GenericEnergyManagementSystem`)
  is an iterative surplus controller that dispatches flexible loads by source
  weight against the PV-generation-versus-consumption electricity balance.

Because none of these physical parameters is carried here,
``EnergySystemConfig`` is a coarse *selector* rather than a full component
parameterisation: changing an efficiency curve, a setpoint, or a controller
gain requires editing the corresponding component or system setup, not this
dataclass.

This module does not perform the sizing itself: it carries the parameters, and
the concrete sizing and component wiring happen in the consuming system setups
and the PV/battery component factories.
"""

import warnings

from dataclasses import dataclass
from dataclasses_json import dataclass_json
from hisim.loadtypes import HeatingSystems, ComponentType


@dataclass_json
@dataclass(kw_only=True)
class EnergySystemConfig:
    """Defines the configuration and sizing of all energy system components considered in a household.

    All fields are keyword-only: callers must name each field explicitly so that the
    ``use_battery_and_ems`` flag (which enables *both* the battery and the EMS) is
    never set by accident via a trailing positional bool.
    """

    heating_system: HeatingSystems = HeatingSystems.DISTRICT_HEATING
    heat_distribution_system: ComponentType = ComponentType.HEAT_DISTRIBUTION_SYSTEM_FLOORHEATING
    share_of_maximum_pv_potential: float = 1.0
    use_battery_and_ems: bool = True

    @classmethod
    def get_default_config(cls, heating_system: HeatingSystems) -> "EnergySystemConfig":
        """Get the default energy system config for a given heating system.

        Only the ``heating_system`` field is set to the requested value; every
        other field keeps its :class:`EnergySystemConfig` dataclass default
        (floor-heating distribution, the full rooftop PV potential
        (``share_of_maximum_pv_potential = 1.0``), and an enabled battery and
        energy-management system (``use_battery_and_ems = True``)).

        This is the single open extension point for default energy-system
        configs: adding a new :class:`HeatingSystems` member needs no new
        method here. Callers that want to select a default programmatically
        (e.g. by iterating over :class:`HeatingSystems`) should use this
        method directly. The ``get_default_config_for_energy_system_*``
        methods below are deprecated convenience aliases that delegate here and
        are scheduled for removal in a future release; prefer
        :meth:`get_default_config` for new code.

        Args:
            heating_system: The heating system to configure.

        Returns:
            A default :class:`EnergySystemConfig` for ``heating_system``.
        """
        return cls(heating_system=heating_system)

    @classmethod
    def get_default_config_for_energy_system_gas(cls) -> "EnergySystemConfig":
        """Deprecated alias for :meth:`get_default_config` with :attr:`HeatingSystems.GAS_HEATING`.

        .. deprecated::
            Use :meth:`get_default_config` with :attr:`HeatingSystems.GAS_HEATING` instead;
            this per-system wrapper is scheduled for removal in a future release.
        """
        warnings.warn(
            "EnergySystemConfig.get_default_config_for_energy_system_gas is deprecated; use "
            "EnergySystemConfig.get_default_config(HeatingSystems.GAS_HEATING) instead. "
            "Scheduled for removal in a future release.",
            DeprecationWarning,
            stacklevel=2,
        )
        return cls.get_default_config(HeatingSystems.GAS_HEATING)

    @classmethod
    def get_default_config_for_energy_system_oil(cls) -> "EnergySystemConfig":
        """Deprecated alias for :meth:`get_default_config` with :attr:`HeatingSystems.OIL_HEATING`.

        .. deprecated::
            Use :meth:`get_default_config` with :attr:`HeatingSystems.OIL_HEATING` instead;
            this per-system wrapper is scheduled for removal in a future release.
        """
        warnings.warn(
            "EnergySystemConfig.get_default_config_for_energy_system_oil is deprecated; use "
            "EnergySystemConfig.get_default_config(HeatingSystems.OIL_HEATING) instead. "
            "Scheduled for removal in a future release.",
            DeprecationWarning,
            stacklevel=2,
        )
        return cls.get_default_config(HeatingSystems.OIL_HEATING)

    @classmethod
    def get_default_config_for_energy_system_heatpump(cls) -> "EnergySystemConfig":
        """Deprecated alias for :meth:`get_default_config` with :attr:`HeatingSystems.HEAT_PUMP`.

        .. deprecated::
            Use :meth:`get_default_config` with :attr:`HeatingSystems.HEAT_PUMP` instead;
            this per-system wrapper is scheduled for removal in a future release.
        """
        warnings.warn(
            "EnergySystemConfig.get_default_config_for_energy_system_heatpump is deprecated; use "
            "EnergySystemConfig.get_default_config(HeatingSystems.HEAT_PUMP) instead. "
            "Scheduled for removal in a future release.",
            DeprecationWarning,
            stacklevel=2,
        )
        return cls.get_default_config(HeatingSystems.HEAT_PUMP)

    @classmethod
    def get_default_config_for_energy_system_district_heating(cls) -> "EnergySystemConfig":
        """Deprecated alias for :meth:`get_default_config` with :attr:`HeatingSystems.DISTRICT_HEATING`.

        .. deprecated::
            Use :meth:`get_default_config` with :attr:`HeatingSystems.DISTRICT_HEATING` instead;
            this per-system wrapper is scheduled for removal in a future release.
        """
        warnings.warn(
            "EnergySystemConfig.get_default_config_for_energy_system_district_heating is deprecated; use "
            "EnergySystemConfig.get_default_config(HeatingSystems.DISTRICT_HEATING) instead. "
            "Scheduled for removal in a future release.",
            DeprecationWarning,
            stacklevel=2,
        )
        return cls.get_default_config(HeatingSystems.DISTRICT_HEATING)

    @classmethod
    def get_default_config_for_energy_system_pellet_heating(cls) -> "EnergySystemConfig":
        """Deprecated alias for :meth:`get_default_config` with :attr:`HeatingSystems.PELLET_HEATING`.

        .. deprecated::
            Use :meth:`get_default_config` with :attr:`HeatingSystems.PELLET_HEATING` instead;
            this per-system wrapper is scheduled for removal in a future release.
        """
        warnings.warn(
            "EnergySystemConfig.get_default_config_for_energy_system_pellet_heating is deprecated; use "
            "EnergySystemConfig.get_default_config(HeatingSystems.PELLET_HEATING) instead. "
            "Scheduled for removal in a future release.",
            DeprecationWarning,
            stacklevel=2,
        )
        return cls.get_default_config(HeatingSystems.PELLET_HEATING)

    @classmethod
    def get_default_config_for_energy_system_wood_chip_heating(cls) -> "EnergySystemConfig":
        """Deprecated alias for :meth:`get_default_config` with :attr:`HeatingSystems.WOOD_CHIP_HEATING`.

        .. deprecated::
            Use :meth:`get_default_config` with :attr:`HeatingSystems.WOOD_CHIP_HEATING` instead;
            this per-system wrapper is scheduled for removal in a future release.
        """
        warnings.warn(
            "EnergySystemConfig.get_default_config_for_energy_system_wood_chip_heating is deprecated; use "
            "EnergySystemConfig.get_default_config(HeatingSystems.WOOD_CHIP_HEATING) instead. "
            "Scheduled for removal in a future release.",
            DeprecationWarning,
            stacklevel=2,
        )
        return cls.get_default_config(HeatingSystems.WOOD_CHIP_HEATING)

    @classmethod
    def get_default_config_for_energy_system_hydrogen(cls) -> "EnergySystemConfig":
        """Deprecated alias for :meth:`get_default_config` with :attr:`HeatingSystems.HYDROGEN_HEATING`.

        .. deprecated::
            Use :meth:`get_default_config` with :attr:`HeatingSystems.HYDROGEN_HEATING` instead;
            this per-system wrapper is scheduled for removal in a future release.
        """
        warnings.warn(
            "EnergySystemConfig.get_default_config_for_energy_system_hydrogen is deprecated; use "
            "EnergySystemConfig.get_default_config(HeatingSystems.HYDROGEN_HEATING) instead. "
            "Scheduled for removal in a future release.",
            DeprecationWarning,
            stacklevel=2,
        )
        return cls.get_default_config(HeatingSystems.HYDROGEN_HEATING)

    @classmethod
    def get_default_config_for_energy_system_electric(cls) -> "EnergySystemConfig":
        """Deprecated alias for :meth:`get_default_config` with :attr:`HeatingSystems.ELECTRIC_HEATING`.

        .. deprecated::
            Use :meth:`get_default_config` with :attr:`HeatingSystems.ELECTRIC_HEATING` instead;
            this per-system wrapper is scheduled for removal in a future release.
        """
        warnings.warn(
            "EnergySystemConfig.get_default_config_for_energy_system_electric is deprecated; use "
            "EnergySystemConfig.get_default_config(HeatingSystems.ELECTRIC_HEATING) instead. "
            "Scheduled for removal in a future release.",
            DeprecationWarning,
            stacklevel=2,
        )
        return cls.get_default_config(HeatingSystems.ELECTRIC_HEATING)

    @classmethod
    def get_default_config_for_energy_system_gas_solar_thermal(cls) -> "EnergySystemConfig":
        """Deprecated alias for :meth:`get_default_config` with :attr:`HeatingSystems.GAS_SOLAR_THERMAL`.

        .. deprecated::
            Use :meth:`get_default_config` with :attr:`HeatingSystems.GAS_SOLAR_THERMAL` instead;
            this per-system wrapper is scheduled for removal in a future release.
        """
        warnings.warn(
            "EnergySystemConfig.get_default_config_for_energy_system_gas_solar_thermal is deprecated; use "
            "EnergySystemConfig.get_default_config(HeatingSystems.GAS_SOLAR_THERMAL) instead. "
            "Scheduled for removal in a future release.",
            DeprecationWarning,
            stacklevel=2,
        )
        return cls.get_default_config(HeatingSystems.GAS_SOLAR_THERMAL)

    @classmethod
    def get_default_config_for_energy_system_heatpump_solar_thermal(cls) -> "EnergySystemConfig":
        """Deprecated alias for :meth:`get_default_config` with :attr:`HeatingSystems.HEAT_PUMP_SOLAR_THERMAL`.

        .. deprecated::
            Use :meth:`get_default_config` with :attr:`HeatingSystems.HEAT_PUMP_SOLAR_THERMAL` instead;
            this per-system wrapper is scheduled for removal in a future release.
        """
        warnings.warn(
            "EnergySystemConfig.get_default_config_for_energy_system_heatpump_solar_thermal is deprecated; use "
            "EnergySystemConfig.get_default_config(HeatingSystems.HEAT_PUMP_SOLAR_THERMAL) instead. "
            "Scheduled for removal in a future release.",
            DeprecationWarning,
            stacklevel=2,
        )
        return cls.get_default_config(HeatingSystems.HEAT_PUMP_SOLAR_THERMAL)
