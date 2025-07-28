"""Modular household config module."""

# clean

from typing import Optional
import json
from dataclasses import dataclass
from dataclasses_json import dataclass_json
from hisim.building_sizer_utils.interface_configs import archetype_config, system_config
from hisim import log
from hisim.system_setup_configuration import SystemSetupConfigBase


@dataclass
class ModularHouseholdOptions:

    """Set options for the system setup."""

    pass


@dataclass_json
@dataclass
class ModularHouseholdConfig(SystemSetupConfigBase):

    """Modular Household Config class."""

    # configuration of the technological equipment of the household
    energy_system_config_: Optional[system_config.EnergySystemConfig] = None
    # configuration of the framework of the household (climate, house type, mobility behaviour, heating system, etc. )
    archetype_config_: Optional[archetype_config.ArcheTypeConfig] = None

    @classmethod
    def get_default_config_for_household_gas(cls):
        """Get default ModularHouseholdConfig."""
        energy_system_config_ = system_config.EnergySystemConfig.get_default_config_for_energy_system_gas()
        archetype_config_ = archetype_config.ArcheTypeConfig()
        household_config = ModularHouseholdConfig(
            energy_system_config_=energy_system_config_, archetype_config_=archetype_config_
        )
        return household_config

    @classmethod
    def get_default_config_for_household_oil(cls):
        """Get default ModularHouseholdConfig."""
        energy_system_config_ = system_config.EnergySystemConfig.get_default_config_for_energy_system_oil()
        archetype_config_ = archetype_config.ArcheTypeConfig()
        household_config = ModularHouseholdConfig(
            energy_system_config_=energy_system_config_, archetype_config_=archetype_config_
        )
        return household_config

    @classmethod
    def get_default_config_for_household_heatpump(cls):
        """Get default ModularHouseholdConfig."""
        energy_system_config_ = system_config.EnergySystemConfig.get_default_config_for_energy_system_heatpump()
        archetype_config_ = archetype_config.ArcheTypeConfig()
        household_config = ModularHouseholdConfig(
            energy_system_config_=energy_system_config_, archetype_config_=archetype_config_
        )
        return household_config

    @classmethod
    def get_default_config_for_household_district_heating(cls):
        """Get default ModularHouseholdConfig."""
        energy_system_config_ = system_config.EnergySystemConfig.get_default_config_for_energy_system_district_heating()
        archetype_config_ = archetype_config.ArcheTypeConfig()
        household_config = ModularHouseholdConfig(
            energy_system_config_=energy_system_config_, archetype_config_=archetype_config_
        )
        return household_config

    @classmethod
    def get_default_config_for_household_pellet(cls):
        """Get default ModularHouseholdConfig."""
        energy_system_config_ = system_config.EnergySystemConfig.get_default_config_for_energy_system_pellet_heating()
        archetype_config_ = archetype_config.ArcheTypeConfig()
        household_config = ModularHouseholdConfig(
            energy_system_config_=energy_system_config_, archetype_config_=archetype_config_
        )
        return household_config

    @classmethod
    def get_default_config_for_household_wood_chips(cls):
        """Get default ModularHouseholdConfig."""
        energy_system_config_ = system_config.EnergySystemConfig.get_default_config_for_energy_system_wood_chip_heating()
        archetype_config_ = archetype_config.ArcheTypeConfig()
        household_config = ModularHouseholdConfig(
            energy_system_config_=energy_system_config_, archetype_config_=archetype_config_
        )
        return household_config

    @classmethod
    def get_default_config_for_household_hydrogen(cls):
        """Get default ModularHouseholdConfig."""
        energy_system_config_ = system_config.EnergySystemConfig.get_default_config_for_energy_system_hydrogen()
        archetype_config_ = archetype_config.ArcheTypeConfig()
        household_config = ModularHouseholdConfig(
            energy_system_config_=energy_system_config_, archetype_config_=archetype_config_
        )
        return household_config

    @classmethod
    def get_default_config_for_household_electric_heating(cls):
        """Get default ModularHouseholdConfig."""
        energy_system_config_ = system_config.EnergySystemConfig.get_default_config_for_energy_system_electric()
        archetype_config_ = archetype_config.ArcheTypeConfig()
        household_config = ModularHouseholdConfig(
            energy_system_config_=energy_system_config_, archetype_config_=archetype_config_
        )
        return household_config

    @classmethod
    def get_default_config_for_household_gas_solar_thermal(cls):
        """Get default ModularHouseholdConfig."""
        energy_system_config_ = system_config.EnergySystemConfig.get_default_config_for_energy_system_gas_solar_thermal()
        archetype_config_ = archetype_config.ArcheTypeConfig()
        household_config = ModularHouseholdConfig(
            energy_system_config_=energy_system_config_, archetype_config_=archetype_config_
        )
        return household_config

    @classmethod
    def get_default_config_for_household_heatpump_solar_thermal(cls):
        """Get default ModularHouseholdConfig."""
        energy_system_config_ = system_config.EnergySystemConfig.get_default_config_for_energy_system_heatpump_solar_thermal()
        archetype_config_ = archetype_config.ArcheTypeConfig()
        household_config = ModularHouseholdConfig(
            energy_system_config_=energy_system_config_, archetype_config_=archetype_config_
        )
        return household_config

    def get_hash(self):
        """Generate a hash for ModularHouseholdConfig."""
        household_config = ModularHouseholdConfig(
            energy_system_config_=self.energy_system_config_, archetype_config_=self.archetype_config_
        )
        config_str = json.dumps(household_config.to_dict())
        config_str_hash = hash(config_str)
        return config_str_hash


def write_config(config: ModularHouseholdConfig) -> None:
    """Writes config."""
    with open("modular_example_config.json", "w", encoding="utf-8") as file:
        file.write(config.to_json())  # type: ignore


def read_in_configs(pathname: str) -> Optional[ModularHouseholdConfig]:
    """Reads in ModularHouseholdConfig file and loads default if file cannot be found."""
    # try to read modular household config from path
    household_config: Optional[ModularHouseholdConfig]
    try:
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
