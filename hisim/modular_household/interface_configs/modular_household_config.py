from dataclasses import dataclass
from typing import Optional
from hisim.modular_household.interface_configs import archetype_config, system_config
from dataclasses_json import dataclass_json


@dataclass_json
@dataclass
class ModularHouseholdConfig:
    system_config_: Optional[system_config.SystemConfig] = None
    archetype_config_: Optional[archetype_config.ArcheTypeConfig] = None
