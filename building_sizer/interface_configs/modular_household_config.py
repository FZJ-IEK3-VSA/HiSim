from dataclasses import dataclass
from typing import Optional

from dataclasses_json import dataclass_json

from building_sizer.interface_configs import archetype_config, system_config


@dataclass_json
@dataclass
class ModularHouseholdConfig:
    system_config: Optional[system_config.SystemConfig] = None
    archetype_config: Optional[archetype_config.ArcheTypeConfig] = None