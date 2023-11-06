"""System setup configuration module."""

# clean

import json
from dataclasses import dataclass
from dataclass_wizard import JSONWizard
from typing_extensions import Self

from hisim import log, utils
from hisim.components import building


@dataclass
class SystemSetupConfigBase(JSONWizard):

    """Base class for system setup."""

    @classmethod
    def load_from_json(cls, module_config_path: str) -> Self:
        """Populate config from JSON."""

        log.information(f"Read module config from {module_config_path}.")

        with open(module_config_path, "r", encoding="utf8") as file:
            module_config_dict = json.loads(file.read())

        # Read building config
        building_config_dict = module_config_dict.pop("building_config", {})
        if building_config_dict:
            log.information("Using `building_config` for scaling.")
            building_config = building.BuildingConfig.from_dict(building_config_dict)
        else:
            building_config = None

        # Load (scaled) default values for system setup configuration
        if building_config:
            my_config = cls.get_scaled_default(building_config)
        else:
            my_config = cls.get_default()

        # Read setup config
        setup_config_dict = module_config_dict.pop("system_setup_config", {})
        if setup_config_dict:
            log.information("Using `system_setup_config` to overwrite defaults.")
            utils.set_attributes_of_dataclass_from_dict(my_config, setup_config_dict)
        else:
            log.information(
                "Did not find `system_setup_config` in JSON. Using defaults."
            )

        return my_config

    @classmethod
    def get_default(cls) -> Self:
        """Get default."""
        raise NotImplementedError

    @classmethod
    def get_scaled_default(cls, building_config: building.BuildingConfig) -> Self:
        """Get scaled default."""
        raise NotImplementedError
