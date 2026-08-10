"""Configuration for the *modular household* framework used by the Building Sizer.

A modular household is assembled from two independently configured modules that the
simulator wires together to produce a household's final time-resolved load profile:

- **Demand side -- archetype config**
  (:class:`~hisim.building_sizer_utils.interface_configs.archetype_config.ArcheTypeConfig`):
  describes the building envelope and its occupants. The occupancy, appliance set and
  activity schedule are not listed field by field but selected through
  ``lpg_households`` -- a list of Load Profile Generator (LPG) household profile names.
  Each name (e.g. the default ``"CHR01_Couple_both_at_Work"``) stands for a complete
  predefined household: its residents, their daily activity schedules, and the
  associated appliance set. Listing several names stacks those households in one
  building. The archetype also fixes the climate (``weather_location``,
  ``coordinates_latitude`` / ``coordinates_longitude``) and the building geometry
  (``building_code``, ``conditioned_floor_area_in_m2`` in m^2, ``construction_year``,
  ``norm_heating_load_in_kilowatt`` in kW) that drive the space-heating and
  domestic-hot-water demand.

- **Supply side -- energy system config**
  (:class:`~hisim.building_sizer_utils.interface_configs.system_config.EnergySystemConfig`):
  describes the technology that serves the demand: the ``heating_system``
  (a :class:`~hisim.loadtypes.HeatingSystems` member) that covers both space heating
  and domestic hot water, the ``heat_distribution_system``, the rooftop PV size as
  ``share_of_maximum_pv_potential`` (dimensionless fraction in ``[0.0, 1.0]``, default
  ``1.0`` = full rooftop potential), and the ``use_battery_and_ems`` flag (``bool``,
  default ``True``) that enables both the battery and the energy-management system.

The two modules are combined in a :class:`ModularHouseholdConfig`. At simulation time
the LPG household profile(s) generate the occupancy-driven electricity load (appliances
and activities), the building envelope together with the climate is passed to the
thermal load calculator -- the downstream :class:`~hisim.components.building.Building`
component, which resolves the archetype envelope from the EPISCOPE/TABULA typology and
integrates a single-node RC model (EN ISO 13790) to compute the space-heating and
domestic-hot-water demand -- and the energy system supplies both through its heating,
PV, battery and EMS components; the resulting component interactions produce the
household's final electricity and heat load profiles.

The defaults are not free parameters but draw on empirical datasets: the LPG profiles
encode occupancy and appliance use derived from time-use surveys, while the building
envelope defaults (U-values, thermal mass, normative heating load) are resolved
downstream from the EPISCOPE/TABULA building-typology database, an empirical
classification of the European building stock. The ``get_default_config_for_household_*``
classmethods pair such a default archetype with each available heating system.
Configurations are exchanged as :mod:`dataclasses_json` JSON objects whose structure
mirrors the nested ``energy_system_config_`` and ``archetype_config_`` fields:
:func:`write_config` serializes a configuration to the ``modular_example_config.json``
file, and :func:`read_in_configs` reads a configuration from a caller-supplied JSON path.
"""
from __future__ import annotations

# clean

from typing import Optional
import json
from dataclasses import dataclass
from dataclasses_json import dataclass_json
from hisim.building_sizer_utils.interface_configs import archetype_config, system_config
from hisim import log
from hisim.loadtypes import HeatingSystems
from hisim.system_setup_configuration import SystemSetupConfigBase


@dataclass
class ModularHouseholdOptions:
    """Set options for the system setup."""

    pass


@dataclass_json
@dataclass
class ModularHouseholdConfig(SystemSetupConfigBase):
    """Configuration for a modular household, pairing an energy system with an archetype setup.

    A :class:`ModularHouseholdConfig` bundles two parts:

    - ``energy_system_config_``: an
      :class:`~hisim.building_sizer_utils.interface_configs.system_config.EnergySystemConfig`
      describing the household's technological equipment (heating system, heat distribution,
      PV, battery and EMS).
    - ``archetype_config_``: an
      :class:`~hisim.building_sizer_utils.interface_configs.archetype_config.ArcheTypeConfig`
      describing the household framework (climate, house type, mobility behaviour, etc.).

    Every ``get_default_config_for_household_*`` classmethod below pairs such an
    :class:`~hisim.building_sizer_utils.interface_configs.system_config.EnergySystemConfig` with a
    default :class:`~hisim.building_sizer_utils.interface_configs.archetype_config.ArcheTypeConfig`.
    The returned :class:`~hisim.building_sizer_utils.interface_configs.system_config.EnergySystemConfig`
    always uses the same non-heating defaults — floor heating distribution, the full rooftop PV
    potential (``share_of_maximum_pv_potential = 1.0``), and an enabled battery and
    energy-management system (``use_battery_and_ems = True``); only the ``heating_system`` field
    varies between the variants (see
    :class:`~hisim.building_sizer_utils.interface_configs.system_config.EnergySystemConfig`).
    """

    # configuration of the technological equipment of the household
    energy_system_config_: Optional[system_config.EnergySystemConfig] = None
    # configuration of the framework of the household (climate, house type, mobility behaviour, heating system, etc. )
    archetype_config_: Optional[archetype_config.ArcheTypeConfig] = None

    @classmethod
    def get_default_config_for_household_gas(cls) -> ModularHouseholdConfig:
        """Create a default :class:`ModularHouseholdConfig` for a household with a gas heating system.

        The returned configuration pairs an
        :class:`~hisim.building_sizer_utils.interface_configs.system_config.EnergySystemConfig`
        configured for gas heating (``HeatingSystems.GAS_HEATING``) with a default
        :class:`~hisim.building_sizer_utils.interface_configs.archetype_config.ArcheTypeConfig`.
        The non-heating energy-system defaults are shared by all household variants; see the
        :class:`ModularHouseholdConfig` class docstring.

        Returns:
            ModularHouseholdConfig: Default modular household configuration with a gas heating system.
        """
        energy_system_config_ = system_config.EnergySystemConfig.get_default_config(HeatingSystems.GAS_HEATING)
        archetype_config_ = archetype_config.ArcheTypeConfig()
        household_config = ModularHouseholdConfig(
            energy_system_config_=energy_system_config_, archetype_config_=archetype_config_
        )
        return household_config

    @classmethod
    def get_default_config_for_household_oil(cls) -> ModularHouseholdConfig:
        """Create a default :class:`ModularHouseholdConfig` for a household with an oil heating system.

        The returned configuration pairs an
        :class:`~hisim.building_sizer_utils.interface_configs.system_config.EnergySystemConfig`
        configured for oil heating (``HeatingSystems.OIL_HEATING``) with a default
        :class:`~hisim.building_sizer_utils.interface_configs.archetype_config.ArcheTypeConfig`.
        The non-heating energy-system defaults are shared by all household variants; see the
        :class:`ModularHouseholdConfig` class docstring.

        Returns:
            ModularHouseholdConfig: Default modular household configuration with an oil heating system.
        """
        energy_system_config_ = system_config.EnergySystemConfig.get_default_config(HeatingSystems.OIL_HEATING)
        archetype_config_ = archetype_config.ArcheTypeConfig()
        household_config = ModularHouseholdConfig(
            energy_system_config_=energy_system_config_, archetype_config_=archetype_config_
        )
        return household_config

    @classmethod
    def get_default_config_for_household_heatpump(cls) -> ModularHouseholdConfig:
        """Create a default :class:`ModularHouseholdConfig` for a household with a heat pump heating system.

        The returned configuration pairs an
        :class:`~hisim.building_sizer_utils.interface_configs.system_config.EnergySystemConfig`
        configured for heat-pump heating (``HeatingSystems.HEAT_PUMP``) with a default
        :class:`~hisim.building_sizer_utils.interface_configs.archetype_config.ArcheTypeConfig`.
        The non-heating energy-system defaults are shared by all household variants; see the
        :class:`ModularHouseholdConfig` class docstring.

        Returns:
            ModularHouseholdConfig: Default modular household configuration with a heat pump heating system.
        """
        energy_system_config_ = system_config.EnergySystemConfig.get_default_config(HeatingSystems.HEAT_PUMP)
        archetype_config_ = archetype_config.ArcheTypeConfig()
        household_config = ModularHouseholdConfig(
            energy_system_config_=energy_system_config_, archetype_config_=archetype_config_
        )
        return household_config

    @classmethod
    def get_default_config_for_household_district_heating(cls) -> ModularHouseholdConfig:
        """Create a default :class:`ModularHouseholdConfig` for a household connected to a district heating network.

        The returned configuration pairs an
        :class:`~hisim.building_sizer_utils.interface_configs.system_config.EnergySystemConfig`
        configured for district heating (``HeatingSystems.DISTRICT_HEATING``) with a default
        :class:`~hisim.building_sizer_utils.interface_configs.archetype_config.ArcheTypeConfig`.
        The non-heating energy-system defaults are shared by all household variants; see the
        :class:`ModularHouseholdConfig` class docstring.

        Returns:
            ModularHouseholdConfig: Default modular household configuration with a district heating system.
        """
        energy_system_config_ = system_config.EnergySystemConfig.get_default_config(HeatingSystems.DISTRICT_HEATING)
        archetype_config_ = archetype_config.ArcheTypeConfig()
        household_config = ModularHouseholdConfig(
            energy_system_config_=energy_system_config_, archetype_config_=archetype_config_
        )
        return household_config

    @classmethod
    def get_default_config_for_household_pellet(cls) -> ModularHouseholdConfig:
        """Create a default :class:`ModularHouseholdConfig` for a household with a pellet heating system.

        The returned configuration pairs an
        :class:`~hisim.building_sizer_utils.interface_configs.system_config.EnergySystemConfig`
        configured for pellet heating (``HeatingSystems.PELLET_HEATING``) with a default
        :class:`~hisim.building_sizer_utils.interface_configs.archetype_config.ArcheTypeConfig`.
        The non-heating energy-system defaults are shared by all household variants; see the
        :class:`ModularHouseholdConfig` class docstring.

        Returns:
            ModularHouseholdConfig: Default modular household configuration with a pellet heating system.
        """
        energy_system_config_ = system_config.EnergySystemConfig.get_default_config(HeatingSystems.PELLET_HEATING)
        archetype_config_ = archetype_config.ArcheTypeConfig()
        household_config = ModularHouseholdConfig(
            energy_system_config_=energy_system_config_, archetype_config_=archetype_config_
        )
        return household_config

    @classmethod
    def get_default_config_for_household_wood_chips(cls) -> ModularHouseholdConfig:
        """Create a default :class:`ModularHouseholdConfig` for a household with a wood chip heating system.

        The returned configuration pairs an
        :class:`~hisim.building_sizer_utils.interface_configs.system_config.EnergySystemConfig`
        configured for wood chip heating (``HeatingSystems.WOOD_CHIP_HEATING``) with a default
        :class:`~hisim.building_sizer_utils.interface_configs.archetype_config.ArcheTypeConfig`.
        The non-heating energy-system defaults are shared by all household variants; see the
        :class:`ModularHouseholdConfig` class docstring.

        Returns:
            ModularHouseholdConfig: Default modular household configuration with a wood chip heating system.
        """
        energy_system_config_ = system_config.EnergySystemConfig.get_default_config(HeatingSystems.WOOD_CHIP_HEATING)
        archetype_config_ = archetype_config.ArcheTypeConfig()
        household_config = ModularHouseholdConfig(
            energy_system_config_=energy_system_config_, archetype_config_=archetype_config_
        )
        return household_config

    @classmethod
    def get_default_config_for_household_hydrogen(cls) -> ModularHouseholdConfig:
        """Create a default :class:`ModularHouseholdConfig` for a household with a hydrogen heating system.

        The returned configuration pairs an
        :class:`~hisim.building_sizer_utils.interface_configs.system_config.EnergySystemConfig`
        configured for hydrogen heating (``HeatingSystems.HYDROGEN_HEATING``) with a default
        :class:`~hisim.building_sizer_utils.interface_configs.archetype_config.ArcheTypeConfig`.
        The non-heating energy-system defaults are shared by all household variants; see the
        :class:`ModularHouseholdConfig` class docstring.

        Returns:
            ModularHouseholdConfig: Default modular household configuration with a hydrogen heating system.
        """
        energy_system_config_ = system_config.EnergySystemConfig.get_default_config(HeatingSystems.HYDROGEN_HEATING)
        archetype_config_ = archetype_config.ArcheTypeConfig()
        household_config = ModularHouseholdConfig(
            energy_system_config_=energy_system_config_, archetype_config_=archetype_config_
        )
        return household_config

    @classmethod
    def get_default_config_for_household_electric_heating(cls) -> ModularHouseholdConfig:
        """Create a default :class:`ModularHouseholdConfig` for a household with an electric heating system.

        The returned configuration pairs an
        :class:`~hisim.building_sizer_utils.interface_configs.system_config.EnergySystemConfig`
        configured for electric heating (``HeatingSystems.ELECTRIC_HEATING``) with a default
        :class:`~hisim.building_sizer_utils.interface_configs.archetype_config.ArcheTypeConfig`.
        The non-heating energy-system defaults are shared by all household variants; see the
        :class:`ModularHouseholdConfig` class docstring.

        Returns:
            ModularHouseholdConfig: Default modular household configuration with an electric heating system.
        """
        energy_system_config_ = system_config.EnergySystemConfig.get_default_config(HeatingSystems.ELECTRIC_HEATING)
        archetype_config_ = archetype_config.ArcheTypeConfig()
        household_config = ModularHouseholdConfig(
            energy_system_config_=energy_system_config_, archetype_config_=archetype_config_
        )
        return household_config

    @classmethod
    def get_default_config_for_household_gas_solar_thermal(cls) -> ModularHouseholdConfig:
        """Create a default :class:`ModularHouseholdConfig` for a household with a gas heating system combined with a solar thermal system.

        The returned configuration pairs an
        :class:`~hisim.building_sizer_utils.interface_configs.system_config.EnergySystemConfig`
        configured for gas heating with solar thermal support
        (``HeatingSystems.GAS_SOLAR_THERMAL``) with a default
        :class:`~hisim.building_sizer_utils.interface_configs.archetype_config.ArcheTypeConfig`.
        The non-heating energy-system defaults are shared by all household variants; see the
        :class:`ModularHouseholdConfig` class docstring.

        Returns:
            ModularHouseholdConfig: Default modular household configuration with a gas and solar thermal heating system.
        """
        energy_system_config_ = system_config.EnergySystemConfig.get_default_config(HeatingSystems.GAS_SOLAR_THERMAL)
        archetype_config_ = archetype_config.ArcheTypeConfig()
        household_config = ModularHouseholdConfig(
            energy_system_config_=energy_system_config_, archetype_config_=archetype_config_
        )
        return household_config

    @classmethod
    def get_default_config_for_household_heatpump_solar_thermal(cls) -> ModularHouseholdConfig:
        """Create a default :class:`ModularHouseholdConfig` for a household with a heat pump heating system combined with a solar thermal system.

        The returned configuration pairs an
        :class:`~hisim.building_sizer_utils.interface_configs.system_config.EnergySystemConfig`
        configured for heat-pump heating with solar thermal support
        (``HeatingSystems.HEAT_PUMP_SOLAR_THERMAL``) with a default
        :class:`~hisim.building_sizer_utils.interface_configs.archetype_config.ArcheTypeConfig`.
        The non-heating energy-system defaults are shared by all household variants; see the
        :class:`ModularHouseholdConfig` class docstring.

        Returns:
            ModularHouseholdConfig: Default modular household configuration with a heat pump and solar thermal heating system.
        """
        energy_system_config_ = system_config.EnergySystemConfig.get_default_config(
            HeatingSystems.HEAT_PUMP_SOLAR_THERMAL
        )
        archetype_config_ = archetype_config.ArcheTypeConfig()
        household_config = ModularHouseholdConfig(
            energy_system_config_=energy_system_config_, archetype_config_=archetype_config_
        )
        return household_config

    def get_hash(self) -> int:
        """Generate a hash for ModularHouseholdConfig."""
        household_config = ModularHouseholdConfig(
            energy_system_config_=self.energy_system_config_, archetype_config_=self.archetype_config_
        )
        config_str = json.dumps(household_config.to_dict())
        config_str_hash = hash(config_str)
        return config_str_hash


def write_config(config: ModularHouseholdConfig) -> None:
    """Write a :class:`ModularHouseholdConfig` to the JSON file ``modular_example_config.json``.

    The configuration is serialized with :meth:`ModularHouseholdConfig.to_json` and written to the
    ``modular_example_config.json`` file in the current working directory, overwriting any existing file.

    Args:
        config (ModularHouseholdConfig): The modular household configuration to serialize and write.
    """
    with open("modular_example_config.json", "w", encoding="utf-8") as file:
        file.write(config.to_json())  # type: ignore


def read_in_configs(pathname: Optional[str]) -> Optional[ModularHouseholdConfig]:
    """Read a :class:`ModularHouseholdConfig` from a JSON file at the given path.

    The file is parsed with :meth:`ModularHouseholdConfig.from_dict`. If the file is read
    successfully, the loaded configuration is returned and its presence is logged at information
    level. If the file does not exist, cannot be parsed, or yields a configuration in which *both*
    the energy system and archetype configs are ``None``, ``None`` is returned instead of raising.

    Args:
        pathname (str): Path to the JSON file containing a serialized :class:`ModularHouseholdConfig`.
            Surrounding whitespace, carriage returns, and newlines are stripped before opening.

    Returns:
        Optional[ModularHouseholdConfig]: The configuration read from ``pathname``, or ``None`` if
        the file cannot be found, fails to parse, or contains no energy system and no archetype config.
    """
    # try to read modular household config from path
    household_config: Optional[ModularHouseholdConfig]
    try:
        if pathname is None:
            raise ValueError("No modular household config path provided.")
        # use strip() in order to remove \r or \n signs from path
        with open(pathname.strip(), encoding="utf8") as config_file:
            household_config_dict = json.load(config_file)  # type: ignore
            household_config = ModularHouseholdConfig.from_dict(household_config_dict)

        log.information(f"Read modular household config from {pathname}")
        assert household_config is not None
        if (household_config.energy_system_config_ is None) and (household_config.archetype_config_ is None):
            raise ValueError("Energy system and archetype configs are None.")

    # get default modular household config
    except Exception:
        household_config = None

    return household_config
