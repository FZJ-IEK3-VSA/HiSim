from dataclasses import dataclass
from typing import Optional
from hisim.modular_household.interface_configs import archetype_config, system_config
from dataclasses_json import dataclass_json
from hisim import log
from hisim.system_setup_configuration import SystemSetupConfigBase
import json


@dataclass
class ModularHouseholdConfig(SystemSetupConfigBase):
    #: configuration of the technological equipment of the household
    system_config_: Optional[system_config.SystemConfig] = None
    #: configuration of the framework of the household (climate, house type, mobility behaviour, heating system, etc. )
    archetype_config_: Optional[archetype_config.ArcheTypeConfig] = None

    @classmethod
    def get_default(cls):
        """Get default ModularHouseholdConfig."""
        system_config_ = system_config.SystemConfig()
        archetype_config_ = archetype_config.ArcheTypeConfig()
        household_config = ModularHouseholdConfig(
            system_config_=system_config_, archetype_config_=archetype_config_
        )
        return household_config


def write_config(config: ModularHouseholdConfig) -> None:
    with open("modular_example_config.json", "w", encoding="utf-8") as f:
        f.write(config.to_json())  # type: ignore
