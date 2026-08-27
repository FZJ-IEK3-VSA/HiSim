"""Round-trip proof that every enum in a component config serializes as a readable string.

HiSim writes component configurations to JSON (scenario files, webtool results,
result directories) through dataclasses_json, which encodes an enum by its
``value`` and decodes a typed enum field by calling ``EnumClass(value)``. Giving
each enum a string value therefore makes the on-disk representation
self-describing without any custom encoder or decoder.

The tests here pin down two separate promises. First, that every enum which was
converted away from an integer encoding now carries its own member name as its
value, so a config JSON reads ``"DWD_TRY"`` rather than ``1``. Second, that
every config class holding an enum-typed field survives a full
``to_dict`` -> ``json.dumps`` -> ``json.loads`` -> ``from_dict`` cycle unchanged,
which is exactly the path the JSON scenario executor walks. Enums whose values
were already meaningful strings (``LoadTypes``, ``FluidMediaType``,
``LpgDataAcquisitionMode``, ...) are deliberately exempt from the first promise
but still covered by the second.
"""

# clean

import dataclasses
import enum
import importlib
import json
import pkgutil
import typing
from typing import Any, Dict, List, Tuple, Type

import pytest

import hisim.components
from hisim.config import ConfigBase, SizingContext
from hisim.components import generic_boiler
from hisim.components import generic_pv_system
from hisim.components import heat_distribution_system
from hisim.components import more_advanced_heat_pump_hplib
from hisim.components import simple_heat_source
from hisim.components import simple_water_storage
from hisim.components import weather


class ConfigEnumSerializationCases:
    """Registry of the enums and config classes exercised by this module.

    Keeping the tables inside a class rather than at module level follows the
    project convention of not carrying loose module-level state. ``STRING_NAMED_ENUMS``
    lists the enums that were converted from integer to member-name values in the
    string-valued-enums cutover; ``CONFIG_FACTORIES`` pairs each config class that
    owns an enum-typed field with a zero-argument callable producing a realistic
    default instance of it.
    """

    #: Enums that must serialize as their own member name (the converted set).
    STRING_NAMED_ENUMS: Tuple[Type[enum.Enum], ...] = (
        weather.WeatherDataSourceEnum,
        heat_distribution_system.HeatDistributionSystemType,
        heat_distribution_system.PositionHotWaterStorageInSystemSetup,
        more_advanced_heat_pump_hplib.PositionHotWaterStorageInSystemSetup,
        simple_water_storage.PositionHotWaterStorageInSystemSetup,
        simple_water_storage.HotWaterStorageSizingEnum,
        simple_heat_source.SimpleHeatSourceType,
        generic_pv_system.PVLibModuleAndInverterEnum,
        generic_boiler.BoilerType,
    )

    #: Config classes owning at least one enum-typed field, with a default factory.
    CONFIG_FACTORIES: Dict[str, Any] = {
        "WeatherConfig": lambda: weather.WeatherConfig.get_default(weather.LocationEnum.AACHEN),
        "PVSystemConfig": generic_pv_system.PVSystemConfig.get_default_pv_system,
        "GenericBoilerConfig": lambda: (
            generic_boiler.GenericBoilerConfig.preset_condensing_gas_12kw("CondensingGasBoiler")
        ),
        "GenericOilBoilerConfig": lambda: generic_boiler.GenericBoilerConfig.preset_oil_12kw("ConventionalOilBoiler"),
        "SimpleHotWaterStorageConfig": (
            simple_water_storage.SimpleHotWaterStorageConfig.get_default_simplehotwaterstorage_config
        ),
        "MoreAdvancedHeatPumpHPLibConfig": (
            more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLibConfig.get_default_generic_advanced_hp_lib
        ),
        "SimpleHeatSourceConfig": simple_heat_source.SimpleHeatSourceConfig.get_default_config_const_power,
        "HeatDistributionConfig": lambda: (
            heat_distribution_system.HeatDistributionConfig.preset_standard("HeatDistributionSystem").resolve(
                SizingContext(
                    water_mass_flow_rate_in_kg_per_second=0.5,
                    conditioned_floor_area_in_m2=120.0,
                    heat_distribution_system_type=heat_distribution_system.HeatDistributionSystemType.RADIATOR,
                )
            )
        ),
        "HeatDistributionControllerConfig": lambda: (
            heat_distribution_system.HeatDistributionControllerConfig
            .get_default_heat_distribution_controller_config(
                heating_load_of_building_in_watt=8000.0,
                set_heating_temperature_for_building_in_celsius=20.0,
                set_cooling_temperature_for_building_in_celsius=25.0,
            )
        ),
    }

    #: The subset of CONFIG_FACTORIES whose enum fields all survive the plain
    #: ``json.dumps(config.to_dict())`` path (every enum member is a ``str``
    #: subclass). ``SimpleHeatSourceConfig`` joined once ``SimpleHeatSourceType``
    #: and ``FluidMediaType`` gained the ``str`` mixin. ``FluidMediaType`` values
    #: are pygfunction fluid identifiers and deliberately differ from the member
    #: names, so that enum stays out of ``STRING_NAMED_ENUMS`` — the encodability
    #: guarantee applies to it, the name-equality guarantee does not.
    CONVERTED_CONFIG_CASES: Tuple[str, ...] = (
        "GenericBoilerConfig",
        "GenericOilBoilerConfig",
        "HeatDistributionConfig",
        "HeatDistributionControllerConfig",
        "MoreAdvancedHeatPumpHPLibConfig",
        "PVSystemConfig",
        "SimpleHeatSourceConfig",
        "SimpleHotWaterStorageConfig",
        "WeatherConfig",
    )


def _enum_fields_of(config: ConfigBase) -> List[Tuple[str, enum.Enum]]:
    """Return the (field name, enum member) pairs of every enum-valued field of a config.

    The declared annotation is ignored in favour of the runtime value, because a
    field may be annotated ``Optional[SomeEnum]`` and legitimately hold ``None``.
    Only fields actually holding an enum member are interesting for the
    serialization promise under test.
    """
    pairs: List[Tuple[str, enum.Enum]] = []
    for field in dataclasses.fields(config):
        value = getattr(config, field.name)
        if isinstance(value, enum.Enum):
            pairs.append((field.name, value))
    return pairs


@pytest.mark.base
@pytest.mark.parametrize(
    "enum_class",
    ConfigEnumSerializationCases.STRING_NAMED_ENUMS,
    ids=lambda e: f"{e.__module__.rsplit('.', 1)[-1]}.{e.__name__}",
)
def test_converted_enums_use_their_member_name_as_value(enum_class: Type[enum.Enum]) -> None:
    """Every member of a converted enum carries its own name as its value.

    This is the property that makes the serialized form readable: dataclasses_json
    writes ``member.value``, so ``value == name`` means the JSON spells the member
    out. It also guarantees the decode direction, since ``EnumClass(name)`` then
    resolves back to the same member.
    """
    for member in enum_class:
        assert isinstance(member.value, str), f"{enum_class.__name__}.{member.name} is not string-valued"
        assert member.value == member.name, f"{enum_class.__name__}.{member.name} has value {member.value!r}"


@pytest.mark.base
@pytest.mark.parametrize(
    "enum_class",
    ConfigEnumSerializationCases.STRING_NAMED_ENUMS,
    ids=lambda e: f"{e.__module__.rsplit('.', 1)[-1]}.{e.__name__}",
)
def test_converted_enums_reject_legacy_integer_values(enum_class: Type[enum.Enum]) -> None:
    """Looking a converted enum up by a legacy integer code raises ValueError.

    The cutover was deliberately made without a backward-compatibility shim: no
    ``_missing_`` hook and no int-tolerant decoder were left behind. This test
    keeps such a shim from creeping back in unnoticed.
    """
    for legacy_code in range(1, len(enum_class) + 1):
        with pytest.raises(ValueError):
            enum_class(legacy_code)


@pytest.mark.base
@pytest.mark.parametrize("case_name", sorted(ConfigEnumSerializationCases.CONFIG_FACTORIES))
def test_config_enum_fields_serialize_as_readable_strings(case_name: str) -> None:
    """A default config writes every enum field as a readable string, never a number.

    The config is encoded through the dataclasses_json encoder and the resulting
    JSON value of each enum-typed field is checked to be a string. For the
    converted enums the string must be the member name; for the enums that always
    had meaningful string values (``LoadTypes``, ``FluidMediaType``, ...) the
    pre-existing value is accepted as is.
    """
    config = ConfigEnumSerializationCases.CONFIG_FACTORIES[case_name]()
    encoded = json.loads(config.to_json())
    enum_fields = _enum_fields_of(config)
    assert enum_fields, f"{case_name} was registered as enum-bearing but holds no enum member"
    for field_name, member in enum_fields:
        serialized = encoded[field_name]
        assert isinstance(serialized, str), f"{case_name}.{field_name} serialized as {serialized!r}, not a string"
        assert serialized == member.value
        if type(member) in ConfigEnumSerializationCases.STRING_NAMED_ENUMS:
            assert serialized == member.name, f"{case_name}.{field_name} serialized as {serialized!r}"


@pytest.mark.base
@pytest.mark.parametrize("case_name", sorted(ConfigEnumSerializationCases.CONFIG_FACTORIES))
def test_config_round_trips_through_json_unchanged(case_name: str) -> None:
    """A default config survives encode-then-decode with every enum field intact.

    The decoded config must compare equal to the original, and each enum field
    must come back as the identical enum member rather than as the raw string
    that was written out - which is the property the JSON scenario executor
    relies on when it rebuilds components from a scenario file.
    """
    config = ConfigEnumSerializationCases.CONFIG_FACTORIES[case_name]()
    encoded = json.loads(config.to_json())
    decoded = type(config).from_dict(encoded)
    assert decoded == config, f"{case_name} did not survive the JSON round trip"
    for field_name, member in _enum_fields_of(config):
        decoded_value = getattr(decoded, field_name)
        assert isinstance(decoded_value, type(member)), (
            f"{case_name}.{field_name} decoded as {decoded_value!r} of type {type(decoded_value).__name__}"
        )
        assert decoded_value is member


@pytest.mark.base
@pytest.mark.parametrize("case_name", ConfigEnumSerializationCases.CONVERTED_CONFIG_CASES)
def test_config_round_trips_through_to_dict_and_plain_json_dump(case_name: str) -> None:
    """A default config also survives HiSim's own ``to_dict`` plus ``json.dump`` path.

    ``hisim.json_generator.convert_component_to_json`` writes scenario files by
    calling ``to_dict`` and handing the result to the JSON encoder, without going
    through ``to_json``. That only works when every enum member is itself a string
    subclass, which is precisely what the ``str`` mixin on the converted enums
    buys; this test guards that second, less obvious encode path.
    """
    config = ConfigEnumSerializationCases.CONFIG_FACTORIES[case_name]()
    encoded = json.loads(json.dumps(config.to_dict()))
    for field_name, member in _enum_fields_of(config):
        assert encoded[field_name] == member.value
    assert type(config).from_dict(encoded) == config


@pytest.mark.base
def test_no_config_field_is_annotated_with_an_int_valued_enum() -> None:
    """No dataclass field of any imported config class points at an int-valued enum.

    This is the sweep that keeps the cutover complete: it walks every
    :class:`ConfigBase` subclass reachable from the imported component modules,
    resolves each field annotation (including through ``Union``/``Optional``) and
    fails if any enum behind it still carries non-string values. A newly added
    ``IntEnum`` in a config would be caught here rather than in a broken JSON file.
    """
    # Import every component module first, so that the ConfigBase subclass registry
    # below covers the whole component library and not just this module's imports.
    # Modules whose optional third-party dependencies are absent are skipped.
    for module_info in pkgutil.iter_modules(hisim.components.__path__):
        try:
            importlib.import_module("hisim.components." + module_info.name)
        except ImportError:
            continue

    offenders: List[str] = []
    seen: List[Type[ConfigBase]] = []

    def collect(cls: Type[ConfigBase]) -> None:
        for subclass in cls.__subclasses__():
            if subclass not in seen:
                seen.append(subclass)
                collect(subclass)

    collect(ConfigBase)

    def enums_in(annotation: Any) -> List[Type[enum.Enum]]:
        origin = typing.get_origin(annotation)
        if origin is not None:
            found: List[Type[enum.Enum]] = []
            for arg in typing.get_args(annotation):
                found.extend(enums_in(arg))
            return found
        if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
            return [annotation]
        return []

    # No ``is_dataclass`` guard here: ``ConfigBase`` is itself a dataclass, so every
    # subclass inherits ``__dataclass_fields__`` and passes such a check whether or not
    # it carries its own ``@dataclass`` decorator. The guard would never fire, and mypy
    # (which knows the same thing) rejects its dead fall-through as unreachable.
    for config_class in seen:
        try:
            hints = typing.get_type_hints(config_class)
        except Exception:  # noqa: BLE001  # unresolvable forward refs are not this test's concern
            continue
        for field in dataclasses.fields(config_class):
            for enum_class in enums_in(hints.get(field.name, field.type)):
                if any(not isinstance(member.value, str) for member in enum_class):
                    offenders.append(f"{config_class.__name__}.{field.name} -> {enum_class.__name__}")

    assert not offenders, "config fields still typed with non-string enums: " + ", ".join(sorted(set(offenders)))
