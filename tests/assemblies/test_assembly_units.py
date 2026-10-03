"""Units on the real configuration fields the mockup assemblies feed (``assemblies_spec.md`` §2.6, D16 b, G10).

Every config field an assembly parameter feeds through ``{$param: …}`` declares its unit, and the
parameter's unit must be that one; the name suffix is documentation, not the source. The walk below
goes over every assembly of the mockup snapshot, every member's config and constructor, and checks
every ``{$param}`` that reaches a field of a class that exists today. What the mockup cannot satisfy
yet is pinned by name, so the lists shrink when the mockup or the classes catch up: the parameters
the mockup declares without a unit (each feeds a dimensionless field that declares ``ANY``), and the
classes that do not exist yet (†).
"""

import dataclasses
import io
import json
from contextlib import redirect_stdout
from typing import Any, Dict, Iterator, List, Set, Tuple

import pytest
import yaml

from hisim import loadtypes as lt
from hisim.cli import main
from hisim.config.sizing import declared_field_unit
from hisim.energy_system.assemblies.parameters import ParameterSubstitution
from hisim.energy_system.classes import ClassBinder
from hisim.energy_system.errors import EnergySystemError
from hisim.energy_system.schema_classes import ComponentClassScan
from hisim.energy_system.schema_export import SchemaBuilder, build_schema
from tests.assemblies.fixture_components import FakeBatteryConfig
from tests.assemblies.helpers import Fixtures

#: Numeric parameters the mockup declares without a unit, by ``(assembly, parameter)``: none since the
#: mockup states ``unit: ANY`` on the parameters that feed a dimensionless field (docs/assemblies
#: d40ff770). The loader refuses a numeric parameter whose unit differs from the field's (D16 b).
PARAMETERS_WITHOUT_UNIT: Set[Tuple[str, str]] = set()

#: Classes the mockup names that do not exist yet (†), whose fields therefore cannot be checked.
CLASSES_NOT_YET_WRITTEN: Set[str] = {
    "hisim.components.gas_cooker.GasCooker",
    "hisim.components.immersion_heater.ImmersionHeater",
    "hisim.components.mechanical_ventilation.MechanicalVentilation",
    "hisim.components.wood_stove.WoodStove",
}


def members_of(document: Dict[str, Any]) -> Iterator[Tuple[str, Dict[str, Any]]]:
    """Every member of an assembly document, those of every variant option included."""
    yield from (document.get("components") or {}).items()
    for variant in (document.get("variants") or {}).values():
        for option in variant["options"].values():
            yield from (option.get("components") or {}).items()


def fed_fields() -> List[Tuple[str, str, str, str, Dict[str, Any]]]:
    """Every ``(assembly, member class, field, parameter, declaration)`` a mockup ``{$param}`` feeds."""
    fed = []
    for path in sorted(Fixtures.MOCKUP.rglob("*.assembly.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        parameters = document.get("parameters") or {}
        for _name, member in members_of(document):
            for parameter, value_path in ParameterSubstitution.references_in(member.get("config") or {}):
                fed.append((document["name"], member["class"], value_path[0], parameter, parameters[parameter]))
    return fed


def numeric(declaration: Dict[str, Any]) -> bool:
    """Whether a parameter carries a unit at all: a number."""
    return declaration.get("type") in ("float", "int")


@pytest.mark.base
def test_every_field_a_mockup_parameter_feeds_declares_the_parameters_unit() -> None:
    """D16 b on the real classes: the field declares a unit, and it is the parameter's."""
    checked: List[str] = []
    without_unit: Set[Tuple[str, str]] = set()
    missing_classes: Set[str] = set()
    for assembly, class_path, field_name, parameter, declaration in fed_fields():
        if not numeric(declaration):
            continue
        try:
            component = ClassBinder.component_class_of(class_path, "test", "member")
        except EnergySystemError:
            missing_classes.add(class_path)
            continue
        config_class = ClassBinder.configuration_class_of(component, "test", "member")
        assert field_name in {field.name for field in dataclasses.fields(config_class)}, (class_path, field_name)
        unit = declared_field_unit(config_class, field_name)
        assert isinstance(unit, lt.Units), f"{config_class.__name__}.{field_name} declares no unit"
        if declaration.get("unit") is None:
            without_unit.add((assembly, parameter))
            assert unit == lt.Units.ANY, (config_class.__name__, field_name, unit)
        else:
            assert unit.name == declaration["unit"], (assembly, parameter, config_class.__name__, field_name, unit)
        checked.append(f"{config_class.__name__}.{field_name}")

    assert without_unit == PARAMETERS_WITHOUT_UNIT
    assert missing_classes == CLASSES_NOT_YET_WRITTEN
    for expected in (
        "PVSystemConfig.azimuth",
        "PVSystemConfig.tilt",
        "PVSystemConfig.power_in_watt",
        "BatteryConfig.custom_battery_capacity_generic_in_kilowatt_hour",
        "BatteryConfig.custom_pv_inverter_power_generic_in_watt",
        "EMSConfig.building_indoor_temperature_offset_value",
        "SolarThermalSystemConfig.area_m2",
        "SimpleAirConditionerConfig.nominal_cooling_power_w",
        "MoreAdvancedHeatPumpHPLibConfig.set_thermal_output_power_in_watt",
        "SimpleDHWStorageConfig.volume_heating_water_storage_in_liter",
        "SimpleHotWaterStorageConfig.volume_heating_water_storage_in_liter",
    ):
        assert expected in checked, expected


@pytest.mark.base
def test_a_unit_on_a_sized_field_survives_the_codec_the_schema_and_describe() -> None:
    """``sized_field(unit=…)`` is metadata: the wire form is unchanged, the schema and ``describe`` state it."""
    config = FakeBatteryConfig.preset_sized_to_pv("Battery")
    decoded = FakeBatteryConfig.from_json(config.to_json())
    assert decoded == config
    assert json.loads(config.to_json())["capacity_in_kwh"] == "AUTO"
    assert declared_field_unit(FakeBatteryConfig, "capacity_in_kwh") == lt.Units.KWH

    schema = build_schema(ComponentClassScan.collect())
    pv_branch = next(
        branch
        for branch in schema["$defs"]["entry"]["allOf"]
        if "hisim.components.generic_pv_system.PVSystem" in json.dumps(branch["if"])
    )
    fields = pv_branch["then"]["properties"]["config"]["properties"]
    assert fields["power_in_watt"][SchemaBuilder.UNIT_KEYWORD] == "WATT"
    assert fields["azimuth"][SchemaBuilder.UNIT_KEYWORD] == "DEGREES"
    assert SchemaBuilder.UNIT_KEYWORD not in fields["location"]

    out = io.StringIO()
    with redirect_stdout(out):
        code = main(["energy-system", "describe", "hisim.components.generic_pv_system.PVSystem"])
    assert code == 0
    text = out.getvalue()
    assert "[sizable]  [unit WATT]" in text and "[unit DEGREES]" in text
