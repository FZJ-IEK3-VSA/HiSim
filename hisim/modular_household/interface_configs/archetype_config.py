"""Archetype configuration for the modular household.

Defines :class:`ArcheTypeConfigModular`, a dataclass that holds the
system-level configuration for a modular household (occupancy profile,
building code, heating systems, transportation and commuting references).

The module also provides backward-compatible deserialization: JSON configs
that still use the deprecated field names ``mobility_set`` and
``mobility_distance`` are automatically migrated to their current names
(``transportation_device_set`` and ``commuting_travel_route_set``) with a
``DeprecationWarning``.
"""

# -*- coding: utf-8 -*-
import warnings
from dataclasses import dataclass, field
from typing import Any, Optional, Type, TypeVar

from dataclasses_json import dataclass_json
from dataclasses_json.core import _decode_dataclass
from utspclient.helpers.lpgdata import TravelRouteSets
from utspclient.helpers.lpgpythonbindings import JsonReference

from hisim.loadtypes import HeatingSystems

#: Mapping of legacy JSON field names to their current names.
#:
#: ``mobility_set`` was renamed to ``transportation_device_set`` and
#: ``mobility_distance`` was renamed to ``commuting_travel_route_set`` so that
#: the field names match their ``JsonReference`` types.  Existing JSON config
#: files may still use the old keys; :func:`_migrate_legacy_field_names` maps
#: them to the new names during deserialization so the values are not silently
#: dropped.
_LEGACY_FIELD_NAMES: "dict[str, str]" = {
    "mobility_set": "transportation_device_set",
    "mobility_distance": "commuting_travel_route_set",
}

_A = TypeVar("_A")


def _migrate_legacy_field_names(kvs: Any) -> Any:
    """Rename legacy JSON keys to their current field names.

    ``ArcheTypeConfigModular`` was previously serialized with the field names
    ``mobility_set`` and ``mobility_distance``.  These were renamed to
    ``transportation_device_set`` and ``commuting_travel_route_set`` to better
    reflect their ``JsonReference`` types.  Existing JSON config files may still
    contain the old keys; this helper maps them to the new names so that the
    values are not silently dropped during deserialization.

    A :class:`DeprecationWarning` is emitted when legacy keys are encountered so
    that users are alerted to update their config files.

    Args:
        kvs: The parsed JSON value (typically a ``dict``).  Non-dict values are
            returned unchanged.

    Returns:
        When ``kvs`` is a ``dict`` a shallow copy with legacy keys renamed is
        returned (the original is not mutated); otherwise ``kvs`` is returned
        unchanged.  If both a legacy key and its replacement are present, the
        replacement value wins and the legacy value is discarded.
    """
    if not isinstance(kvs, dict):
        return kvs
    migrated = dict(kvs)
    found_legacy: "list[str]" = []
    for old_name, new_name in _LEGACY_FIELD_NAMES.items():
        if old_name in migrated:
            found_legacy.append(old_name)
            if new_name not in migrated:
                migrated[new_name] = migrated.pop(old_name)
            else:
                # New name already present -- drop the legacy key, keep the new value.
                migrated.pop(old_name)
    if found_legacy:
        warnings.warn(
            "ArcheTypeConfigModular JSON config uses deprecated field name(s) "
            + ", ".join(repr(n) for n in found_legacy)
            + ". They have been renamed to "
            + ", ".join(repr(_LEGACY_FIELD_NAMES[n]) for n in found_legacy)
            + ". Please update your config file to use the new field names.",
            DeprecationWarning,
            stacklevel=2,
        )
    return migrated


@dataclass_json
@dataclass
class ArcheTypeConfigModular:

    """Archetype config class.

    Defines the system config for the modular household.
    """

    #: modular household template of the LoadProfileGenerator, used to get the electrical- and hot water consumption profile (https://www.loadprofilegenerator.de/)
    # for an interface to the LoadProfileGenerator the UTSP is needed
    occupancy_profile_utsp: Optional[JsonReference] = None
    # field(
    #     default_factory=lambda: Households.CHR02_Couple_30_64_age_with_work  # type: ignore
    # )
    #: reference to stored electricity consumption and hot water consumption data, no interface to LoadProfileGenerator needed, no obligatory UTSP connection
    # available options: "AVG" - average consumption profile over Europe and "CHR01 Couple both at Work" - system setup output of the LPG
    occupancy_profile: Optional[str] = "AVG"
    #: building code of considered type of building originated from the Tabula data base (https://episcope.eu/building-typology/webtool/)
    building_code: str = "DE.N.SFH.05.Gen.ReEx.001.002"  # "DE.N.SFH.05.Gen.ReEx.001.002"
    #: absolute area considered for heating and cooling
    absolute_conditioned_floor_area: Optional[float] = None
    #: type of water heating system
    water_heating_system_installed: HeatingSystems = HeatingSystems.DISTRICT_HEATING
    #: type of heating system
    heating_system_installed: HeatingSystems = HeatingSystems.DISTRICT_HEATING
    #: reference to a transportation-device set from the LPG bindings, passed as input to the LoadProfileGenerator and considered to model cars
    transportation_device_set: Optional[JsonReference] = None
    # field(
    #     default_factory=lambda: TransportationDeviceSets.Bus_and_one_30_km_h_Car  # type: ignore
    #     )
    #: reference to a travel-route set for the average daily commuting distance, passed as input to the LoadProfileGenerator and considered to model consumption of cars
    commuting_travel_route_set: Optional[JsonReference] = field(
        default_factory=lambda: TravelRouteSets.Travel_Route_Set_for_15km_Commuting_Distance
    )  # type: ignore


@classmethod
def _archetype_config_modular_from_dict(
    cls: Type[_A], kvs: Any, *, infer_missing: bool = False
) -> _A:
    """Deserialize a dict into an :class:`ArcheTypeConfigModular`.

    Supports backward-compatible deserialization of JSON configs that still use
    the deprecated field names ``mobility_set`` and ``mobility_distance`` by
    mapping them to ``transportation_device_set`` and
    ``commuting_travel_route_set`` respectively before decoding.  A
    :class:`DeprecationWarning` is emitted when legacy keys are found.

    ``dataclasses_json`` overrides any ``from_dict`` defined in the class body,
    so this method is assigned to :meth:`ArcheTypeConfigModular.from_dict`
    *after* the ``@dataclass_json`` decorator has run.  ``from_json`` calls
    ``cls.from_dict`` internally, so JSON deserialization is covered as well.
    """
    kvs = _migrate_legacy_field_names(kvs)
    return _decode_dataclass(cls, kvs, infer_missing)


ArcheTypeConfigModular.from_dict = _archetype_config_modular_from_dict  # type: ignore[assignment]


# def create_archetype_config_file() -> None:
#     """Component Cost file is created."""

#     config_file=ArcheTypeConfig()
#     config_file_written = config_file.to_json()

#     with open('arche_type_config.json', 'w', encoding="utf-8") as outfile:
#         outfile.write(config_file_written)
