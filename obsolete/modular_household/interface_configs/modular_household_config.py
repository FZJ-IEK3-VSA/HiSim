"""Modular household configuration module.

This module provides the :class:`ModularHouseholdConfig` dataclass, which combines
system-level equipment configuration (:class:`system_config.SystemConfig`) with
archetype-level framework configuration (:class:`archetype_config.ArcheTypeConfigModular`)
for household energy simulations.
"""

# clean

from typing import Optional
import json
from dataclasses import dataclass
from dataclass_wizard.errors import ParseError

from repositories.HiSim.obsolete.modular_household.interface_configs import system_config
from hisim import log
from repositories.HiSim.obsolete.modular_household.interface_configs import archetype_config
from repositories.HiSim.hisim.system_setup_configuration import SystemSetupConfigBase


@dataclass
class ModularHouseholdConfig(SystemSetupConfigBase):

    """Bundles system equipment and archetype framework config for a modular household.

    Attributes:
        system_config_: Technological equipment configuration of the household
            (e.g. PV, heat pump, battery). May be ``None`` to fall back to defaults.
        archetype_config_: Framework configuration of the household (climate, house
            type, mobility behaviour, heating system, etc.). May be ``None`` to fall
            back to defaults.
    """

    # configuration of the technological equipment of the household
    system_config_: Optional[system_config.SystemConfig] = None
    # configuration of the framework of the household (climate, house type, mobility behaviour, heating system, etc. )
    archetype_config_: Optional[archetype_config.ArcheTypeConfigModular] = None

    @classmethod
    def get_default(cls):
        """Return a default modular household configuration.

        Returns:
            ModularHouseholdConfig: A config with default ``SystemConfig`` and
            ``ArcheTypeConfigModular`` instances.
        """
        system_config_ = system_config.SystemConfig()
        archetype_config_ = archetype_config.ArcheTypeConfigModular()
        household_config = ModularHouseholdConfig(system_config_=system_config_, archetype_config_=archetype_config_)
        return household_config


def write_config(config: ModularHouseholdConfig) -> None:
    """Write the modular household configuration to a JSON file.

    The file is written as ``modular_example_config.json`` in the current working
    directory.

    Args:
        config: The modular household configuration to serialize.
    """
    with open("modular_example_config.json", "w", encoding="utf-8") as file:
        file.write(config.to_json())  # type: ignore


def _with_default_subconfigs(
    household_config: ModularHouseholdConfig,
) -> ModularHouseholdConfig:
    """Replace any ``None`` sub-config of *household_config* with a default instance.

    :func:`read_config` guarantees that both ``system_config_`` and
    ``archetype_config_`` are non-``None`` on the returned config.  This helper
    fills in a fresh :class:`system_config.SystemConfig` respectively
    :class:`archetype_config.ArcheTypeConfigModular` for whichever sub-configs
    are still ``None`` (e.g. when the deserialized config did not specify them).
    The argument is mutated in place and also returned for convenience.
    """
    if household_config.system_config_ is None:
        household_config.system_config_ = system_config.SystemConfig()
    if household_config.archetype_config_ is None:
        household_config.archetype_config_ = archetype_config.ArcheTypeConfigModular()
    return household_config


def _config_from_setup_dict(
    setup_config_dict: Optional[dict],
) -> ModularHouseholdConfig:
    """Deserialize a ``system_setup_config`` dict into a fully populated config.

    This is the pure, I/O-free core of :func:`read_config`.  It performs the
    same steps, in the same order, that ``read_config`` used to perform in-line,
    but without touching the filesystem so that the migration, validation, and
    default-filling logic can be unit-tested with plain dicts:

    1. migrate any deprecated field names in the nested ``archetype_config_``
       (see :func:`archetype_config.migrate_legacy_field_names`),
    2. deserialize the dict with :meth:`ModularHouseholdConfig.from_dict`,
    3. validate: if the result has neither a system nor an archetype config,
       fall back to a fresh :class:`ModularHouseholdConfig` and log a warning,
    4. fill any missing sub-config with a default instance.

    Args:
        setup_config_dict: The value of the ``system_setup_config`` key from the
            parsed JSON file (typically a ``dict``, but ``None`` when the key is
            absent).  A ``None`` or otherwise unparseable value causes
            :meth:`ModularHouseholdConfig.from_dict` to raise a
            :class:`dataclass_wizard.errors.ParseError`, which propagates to the
            caller so that :func:`read_config` can map it to the default-fallback
            branch.

    Returns:
        A :class:`ModularHouseholdConfig` whose ``system_config_`` and
        ``archetype_config_`` are both non-``None``.

    Raises:
        dataclass_wizard.errors.ParseError: If ``setup_config_dict`` cannot be
            deserialized into a :class:`ModularHouseholdConfig`.
    """
    # ``ModularHouseholdConfig.from_dict`` (dataclass_wizard) does not call
    # ``ArcheTypeConfigModular.from_dict``, so deprecated field names in the
    # nested archetype config are migrated here before deserialization.
    if isinstance(setup_config_dict, dict):
        archetype_dict = setup_config_dict.get("archetype_config_")
        if isinstance(archetype_dict, dict):
            setup_config_dict["archetype_config_"] = (
                archetype_config.migrate_legacy_field_names(archetype_dict)
            )
    household_config: ModularHouseholdConfig = ModularHouseholdConfig.from_dict(
        setup_config_dict
    )  # type: ignore
    if (household_config.system_config_ is None) and (
        household_config.archetype_config_ is None
    ):
        log.warning(
            "Modular household config contains neither a system nor an archetype "
            "config. Using a default config instead."
        )
        household_config = ModularHouseholdConfig()
    return _with_default_subconfigs(household_config)


def read_config(pathname: Optional[str]) -> ModularHouseholdConfig:
    """Read a modular household configuration from a JSON file.

    If the file cannot be found, opened, parsed, or contains no system/archetype
    config, a default ``ModularHouseholdConfig`` is returned and a warning is logged.
    Any missing sub-configs are also replaced with defaults before returning.

    This function is a thin I/O shell around :func:`_config_from_setup_dict`:
    it reads the file, extracts the ``system_setup_config`` dict, and delegates
    the migration / deserialization / validation / default-filling to the pure
    core.  Any parse or I/O error is caught and turned into a default config.

    Args:
        pathname: Path to the JSON config file, or ``None`` to trigger the default
            fallback.

    Returns:
        ModularHouseholdConfig: The loaded configuration, or a default configuration
        if loading failed.
    """
    try:
        if pathname is None:
            raise FileNotFoundError("No modular household config path was provided.")
        with open(pathname, encoding="utf8") as config_file:
            household_config_dict = json.load(config_file)  # type: ignore
        setup_config_dict = household_config_dict.get("system_setup_config")
        household_config = _config_from_setup_dict(setup_config_dict)
        log.information(f"Read modular household config from {pathname}")
        return household_config
    except (FileNotFoundError, OSError, json.JSONDecodeError, ParseError):
        log.warning(
            f"Could not read the modular household config from \'{pathname}\'. "
            f"Using a default config instead."
        )
        return _with_default_subconfigs(ModularHouseholdConfig())
