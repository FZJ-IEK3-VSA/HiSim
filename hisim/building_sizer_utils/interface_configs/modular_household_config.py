"""Modular household config module."""

# clean

from typing import Optional
import json
from dataclasses import dataclass

from hisim.modular_household.interface_configs import archetype_config, system_config
from hisim import log
from hisim.system_setup_configuration import SystemSetupConfigBase


@dataclass
class ModularHouseholdOptions:

    """Set options for the system setup."""

    pass


@dataclass
class ModularHouseholdConfig(SystemSetupConfigBase):

    """Modular Household Config class."""

    # configuration of the technological equipment of the household
    system_config_: Optional[system_config.SystemConfig] = None
    # configuration of the framework of the household (climate, house type, mobility behaviour, heating system, etc. )
    archetype_config_: Optional[archetype_config.ArcheTypeConfig] = None

    @classmethod
    def get_default(cls):
        """Get default ModularHouseholdConfig."""
        system_config_ = system_config.SystemConfig()
        archetype_config_ = archetype_config.ArcheTypeConfig()
        household_config = ModularHouseholdConfig(system_config_=system_config_, archetype_config_=archetype_config_)
        return household_config


def write_config(config: ModularHouseholdConfig) -> None:
    """Writes config."""
    with open("modular_example_config.json", "w", encoding="utf-8") as file:
        file.write(config.to_json())  # type: ignore


def read_in_configs(pathname: str) -> ModularHouseholdConfig:
    """Reads in ModularHouseholdConfig file and loads default if file cannot be found."""
    try:
        with open(pathname, encoding="utf8") as config_file:
            household_config_dict = json.load(config_file)  # type: ignore
            household_config: ModularHouseholdConfig = ModularHouseholdConfig.from_dict(
                household_config_dict.get("system_setup_config")
            )  # type: ignore
        log.information(f"Read modular household config from {pathname}")
        if (household_config.system_config_ is None) and (household_config.archetype_config_ is None):
            raise ValueError()
    except Exception:
        household_config = ModularHouseholdConfig()
        log.warning(f"Could not read the modular household config from '{pathname}'. Using a default config instead.")

    # set default configs
    if household_config.system_config_ is None:
        household_config.system_config_ = system_config.SystemConfig()
    if household_config.archetype_config_ is None:
        household_config.archetype_config_ = archetype_config.ArcheTypeConfig()

    return household_config
