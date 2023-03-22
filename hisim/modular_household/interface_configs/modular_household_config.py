from dataclasses import dataclass
from typing import Optional
from hisim.modular_household.interface_configs import archetype_config, system_config
from dataclasses_json import dataclass_json
from hisim import log
import json


@dataclass_json
@dataclass
class ModularHouseholdConfig:
    #: configuration of the technological equipment of the household
    system_config_: Optional[system_config.SystemConfig] = None
    #: configuration of the framework of the household (climate, house type, mobility behaviour, heating system, etc. )
    archetype_config_: Optional[archetype_config.ArcheTypeConfig] = None


def write_config(config: ModularHouseholdConfig):
    with open("modular_example_config.json", "w", encoding="utf-8") as f:
        f.write(config.to_json())


def read_in_configs(pathname: str) -> ModularHouseholdConfig:
    """Reads in ModularHouseholdConfig file and loads default if file cannot be found. """
    try:
        with open(pathname, encoding="utf8") as config_file:
            household_config: ModularHouseholdConfig = ModularHouseholdConfig.from_json(config_file.read())  # type: ignore
        log.information(f"Read modular household config from {pathname}")
    except Exception:
        household_config = ModularHouseholdConfig()
        log.warning(
            f"Could not read the modular household config from '{pathname}'. Using a default config instead."
        )

    # set default configs
    if household_config.system_config_ is None:
        household_config.system_config_ = system_config.SystemConfig()
    if household_config.archetype_config_ is None:
        household_config.archetype_config_ = archetype_config.ArcheTypeConfig()

    return household_config
