"""System setup configuration module.

Provides the base dataclass for household system-setup configurations,
supporting JSON loading and default/scaled configuration generation.
"""

# clean

from typing import Any
import json
from pathlib import Path
from dataclasses import dataclass
from dataclass_wizard import JSONWizard
from typing_extensions import Self

from hisim import log, utils
from hisim.components import building


@dataclass
class SystemSetupConfigBase(JSONWizard):

    """Base class for system-setup configurations.

    A JSONWizard dataclass that provides JSON loading and default-value
    generation for household system setups. Subclasses must implement
    `get_default_options`, `get_default`, and `get_scaled_default`.
    """

    @classmethod
    def load_from_json(cls, module_config_path: str) -> Self:
        """Load a configuration instance from a JSON file.

        Reads the JSON file and delegates the dict-level merge/overwrite
        logic to :meth:`load_from_dict`.

        Args:
            module_config_path: Path to the JSON configuration file. If the
                path is not found, a Windows line-ending workaround is tried.

        Returns:
            A configuration instance populated from the JSON file and defaults.

        Raises:
            FileNotFoundError: If the config file cannot be found even after
                the line-ending workaround.
        """

        config_path = Path(module_config_path)
        if config_path.exists():
            with config_path.open("r", encoding="utf8") as file:
                module_config_dict: dict[str, Any] = json.loads(file.read())
        else:
            # Try with Windows line-ending workaround
            config_path_without_cr = Path(module_config_path.rstrip("\r"))
            if config_path_without_cr.exists():
                with config_path_without_cr.open("r", encoding="utf8") as file:
                    module_config_dict = json.loads(file.read())
                config_path = config_path_without_cr
            else:
                raise FileNotFoundError(f"The module config file {module_config_path} could not be found.")
        log.information(f"Read module config from {module_config_path}.")

        return cls.load_from_dict(module_config_dict)

    @classmethod
    def load_from_dict(cls, module_config_dict: dict[str, Any]) -> Self:
        """Build a configuration instance from an in-memory config dict.

        Extracts optional `building_config`, `options`, and
        `system_setup_config` sections from `module_config_dict` and builds a
        configuration from scaled or unscaled defaults combined with any
        overwrites present in the dict. The dict is consumed in place: the
        `building_config`, `options`, and `system_setup_config` keys are
        popped.

        This is the pure, filesystem-free counterpart of
        :meth:`load_from_json`, so the merge/overwrite logic can be unit tested
        without writing a JSON file to disk.

        Args:
            module_config_dict: Parsed module config dict (the contents of a
                JSON config file). May contain `building_config`, `options`,
                and `system_setup_config` keys; everything else is ignored.

        Returns:
            A configuration instance populated from the dict and defaults.

        Raises:
            ValueError: If `options` are present in the dict but no
                `building_config` is provided.
        """

        # Read building config overwrites. It is used to scale the system setup.
        building_config_dict = module_config_dict.pop("building_config", {})
        if building_config_dict:
            log.information("Using `building_config` for scaling.")
            building_config = building.BuildingConfig.from_dict(building_config_dict)
        else:
            building_config = None

        # Load option overwrites.
        options_dict = module_config_dict.pop("options", {})
        options = cls.get_default_options()
        if options_dict:
            log.information("Using `options`.")
            utils.set_attributes_of_dataclass_from_dict(options, options_dict)

        # Load (scaled) default values for system setup configuration.
        if options_dict and not building_config:
            raise ValueError("Options for default setup not yet implemented.")
        if building_config:
            my_config = cls.get_scaled_default(building_config=building_config, options=options)
        else:
            my_config = cls.get_default()

        # Read setup config overwrites
        setup_config_dict = module_config_dict.pop("system_setup_config", {})
        if setup_config_dict:
            log.information("Using `system_setup_config` to overwrite defaults.")
            utils.set_attributes_of_dataclass_from_dict(my_config, setup_config_dict)
        else:
            log.information("Did not find `system_setup_config` in JSON. Using defaults.")

        return my_config

    @classmethod
    def get_default_options(cls) -> Any:
        """Return the default options for this system setup.

        Returns:
            An object holding default options for the configuration.

        Raises:
            NotImplementedError: Always; subclasses must override.
        """
        raise NotImplementedError

    @classmethod
    def get_default(cls) -> Self:
        """Return the default (unscaled) configuration.

        Returns:
            A configuration instance with default values.

        Raises:
            NotImplementedError: Always; subclasses must override.
        """
        raise NotImplementedError

    @classmethod
    def get_scaled_default(cls, building_config: building.BuildingConfig, options: Any) -> Self:
        """Return a default configuration scaled to the given building.

        Args:
            building_config: Building configuration used to scale the setup.
            options: Options controlling how defaults are scaled.

        Returns:
            A configuration instance with scaled default values.

        Raises:
            NotImplementedError: Always; subclasses must override.
        """
        raise NotImplementedError
