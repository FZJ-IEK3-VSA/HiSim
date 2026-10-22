"""Tests for ``building.expanded.yaml``: the building as the run computes with it.

The file is only worth having if it states exactly what ``BuildingInformation`` holds, so every
test here compares the block with the attributes it was read from, field by field, rather than
with numbers written into the test.
"""

from pathlib import Path
from typing import Any, List

import pytest
import yaml

from hisim.components.building.building import Building
from hisim.components.building.config import BuildingConfig
from hisim.components.building.information import BuildingInformation
from hisim.energy_system.building_record import BUILDING_RECORD_FILENAME, expanded_building_block
from hisim.energy_system.executor import build_energy_system, write_records
from tests.test_energy_system_record import Fixtures


def _config(code: str = "", **fields: Any) -> BuildingConfig:
    """The default building, or one for a TABULA code, with the given fields set."""
    if code:
        config = BuildingConfig.for_tabula_code("Building", building_code=code)
    else:
        config = BuildingConfig.preset_german_single_family_home("Building")
    config.heating_reference_temperature_in_celsius = -7.0
    for key, value in fields.items():
        setattr(config, key, value)
    return config


CONFIGS = {
    "default": _config(),
    "austrian": _config(
        "AT.N.SFH.04.Gen.ReEx.001.001",
        absolute_conditioned_floor_area_in_m2=115.0,
        facade_u_value_in_watt_per_m2_per_kelvin=0.25,
    ),
    "floor_factor_half": _config("AT.N.SFH.02.Gen.ReEx.001.001"),
}


def _floats(value: Any) -> List[Any]:
    """Every number in a block, so their types can be checked."""
    if isinstance(value, dict):
        return [number for item in value.values() for number in _floats(item)]
    if isinstance(value, list):
        return [number for item in value for number in _floats(item)]
    return [value] if isinstance(value, (int, float)) and not isinstance(value, bool) else []


@pytest.mark.base
@pytest.mark.parametrize("case", list(CONFIGS))
def test_the_block_states_the_attributes_of_building_information(case: str) -> None:
    """Catches a block that recomputes, rounds or reorders what the building holds."""
    config = CONFIGS[case]
    information = BuildingInformation(config=config)

    block = expanded_building_block("Building", information, config)

    assert block["name"] == "Building"
    assert block["hisim_building_code"] == config.building_code
    assert block["building_type"] == "detached"
    assert block["conditioned_floor_area_in_m2"] == information.scaled_conditioned_floor_area_in_m2
    assert block["gross_volume_in_m3"] is None
    assert block["heat_capacity_class"] == information.building_heat_capacity_class
    assert block["dwelling_units"] == {
        "value": information.number_of_apartments,
        "configured": config.number_of_apartments is not None,
    }
    assert list(block["elements"]) == ["floor", "wall", "roof", "window", "door"]
    for key, element in block["elements"].items():
        values = information.element_values()[key]
        area_attribute = "facade_area_in_m2" if key == "wall" else f"{key}_area_in_m2"
        assert element == {
            "area_in_m2": getattr(information, area_attribute),
            "u_value_in_watt_per_m2_per_kelvin": values.u_value_in_watt_per_m2_per_kelvin,
            "u_value_origin": values.u_value_origin,
            "adjustment_factor": values.adjustment_factor,
        }
    assert list(block["windows_by_direction"]) == [d.lower() for d in BuildingInformation.WINDOWS_DIRECTIONS]
    assert list(block["windows_by_direction"].values()) == information.scaled_window_areas_in_m2
    assert block["glazing"] == {
        "g_value": information.total_solar_energy_transmittance_for_perpedicular_radiation,
        "frame_factor": information.reduction_factor_for_frame_area_fraction_of_window,
        "non_perpendicular_factor": information.reduction_factor_for_non_perpedicular_radiation,
        "external_shading_factor": information.reduction_factor_for_external_vertical_shading,
    }
    assert (
        block["thermal_bridging_surcharge_in_watt_per_m2_per_kelvin"]
        == information.thermal_bridging_surcharge_in_watt_per_m2_per_kelvin
    )
    assert block["site"] == {"climate_region": None, "elevation_in_m": None}
    assert all(type(number) in (float, int) for number in _floats(block))
    assert type(block["dwelling_units"]["value"]) is int  # pylint: disable=unidiomatic-typecheck
    assert yaml.safe_load(yaml.safe_dump(block, sort_keys=False, allow_unicode=True)) == block


@pytest.mark.base
def test_the_block_names_what_the_config_set() -> None:
    """Catches ``configured_fields`` and the origins missing a value the author configured."""
    default = expanded_building_block("Building", BuildingInformation(CONFIGS["default"]), CONFIGS["default"])
    austrian = expanded_building_block("Building", BuildingInformation(CONFIGS["austrian"]), CONFIGS["austrian"])
    half = expanded_building_block(
        "Building", BuildingInformation(CONFIGS["floor_factor_half"]), CONFIGS["floor_factor_half"]
    )

    assert default["configured_fields"] == ["absolute_conditioned_floor_area_in_m2"]
    assert austrian["configured_fields"] == [
        "absolute_conditioned_floor_area_in_m2",
        "facade_u_value_in_watt_per_m2_per_kelvin",
    ]
    assert austrian["conditioned_floor_area_in_m2"] == 115.0
    assert austrian["elements"]["wall"]["u_value_in_watt_per_m2_per_kelvin"] == 0.25
    assert austrian["elements"]["wall"]["u_value_origin"] == "configured"
    assert half["configured_fields"] == []
    assert half["elements"]["floor"]["adjustment_factor"] == 0.5
    assert not default["dwelling_units"]["configured"]


@pytest.mark.base
def test_a_code_of_an_unlisted_typology_gets_no_building_type() -> None:
    """Catches an unlisted typology refusing the block, which would stop a run HiSim can compute."""
    config = _config("AT.N.SFH-TH.01-02.Gen.SyAv.001.001")

    block = expanded_building_block("Building", BuildingInformation(config), config)

    assert block["building_type"] is None
    assert block["hisim_building_code"] == "AT.N.SFH-TH.01-02.Gen.SyAv.001.001"


@pytest.mark.base
def test_write_records_writes_the_expanded_building(tmp_path: Path) -> None:
    """Catches ``write_records`` not writing the file, or writing a building other than the built one."""
    # The fixture is a pinned record; without its metadata block it builds as an authored file.
    source = yaml.safe_load(Fixtures.GOLDEN.read_text(encoding="utf-8"))
    del source["metadata"]
    authored = tmp_path / "uc1.energy_system.yaml"
    authored.write_text(yaml.safe_dump(source, sort_keys=False, allow_unicode=True), encoding="utf-8")
    built = build_energy_system(authored, Fixtures.parameters(tmp_path), simulation_parameters_path=Fixtures.PARAMETERS)

    write_records(built, str(tmp_path / "record"))

    path = tmp_path / "record" / BUILDING_RECORD_FILENAME
    assert path.is_file()
    written = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert written["schema"] == "building-expanded/1"
    [building] = [
        wrapped.my_component
        for wrapped in built.simulator.wrapped_components
        if isinstance(wrapped.my_component, Building)
    ]
    assert written["buildings"] == [
        expanded_building_block(
            building.component_name, building.my_building_information, building.buildingconfig
        )
    ]
