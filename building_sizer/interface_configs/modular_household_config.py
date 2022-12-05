from dataclasses import dataclass
from dataclasses_json import dataclass_json

from building_sizer.interface_configs import system_config
from building_sizer.interface_configs import archetype_config

@dataclass_json
@dataclass
class ModularHouseholdConfig:
    system_config: system_config.SystemConfig = None
    archetype_config: archetype_config.ArcheTypeConfig = None