"""Tests for the ``BuildingPVWeatherConfig`` of the household hp/hws/hds/pv/battery/ems setup.

The ``BuildingPVWeatherConfig`` dataclass is decorated with both
``@dataclass_json`` (from ``dataclasses_json``) and ``JSONWizard``
(from ``dataclass_wizard``).  The ``@dataclass_json`` decorator injects
``to_json``/``from_json``/``to_dict``/``from_dict`` directly onto the class,
so these are the methods exercised by ``setup_function`` when a config file
path is supplied (see ``from_json`` at line ~117 and ``to_dict`` at lines
~120/~124 of the setup module).  These tests cover the pure
``get_default()`` factory and the JSON serialization round-trip contract.
"""

# clean

import pytest

from hisim import log

from system_setups.household_hp_hws_hds_pv_battery_ems import BuildingPVWeatherConfig


@pytest.mark.base
def test_get_default_field_values() -> None:
    """``BuildingPVWeatherConfig.get_default()`` returns the documented defaults."""
    cfg = BuildingPVWeatherConfig.get_default()

    assert cfg.name == "BuildingPVConfig"
    assert cfg.pv_azimuth == 180
    assert cfg.pv_tilt == 30
    assert cfg.pv_rooftop_capacity_in_kilowatt is None
    assert cfg.share_of_maximum_pv_potential == 1
    assert cfg.building_code == "DE.N.SFH.05.Gen.ReEx.001.002"
    assert cfg.conditioned_floor_area_in_m2 == 121.2
    assert cfg.number_of_dwellings_per_building == 1
    assert cfg.norm_heating_load_in_kilowatt is None
    assert cfg.lpg_households == ["CHR01_Couple_both_at_Work"]
    assert cfg.weather_location == "AACHEN"

    log.information("BuildingPVWeatherConfig default field values verified.")


@pytest.mark.base
def test_get_default_return_type() -> None:
    """``get_default()`` returns a ``BuildingPVWeatherConfig`` instance."""
    cfg = BuildingPVWeatherConfig.get_default()
    assert isinstance(cfg, BuildingPVWeatherConfig)


@pytest.mark.base
def test_get_default_determinism() -> None:
    """Calling ``get_default()`` twice yields instances with equal field values."""
    first = BuildingPVWeatherConfig.get_default()
    second = BuildingPVWeatherConfig.get_default()
    # Content equality (dataclass __eq__) - not necessarily the same object.
    assert first == second
    # Explicit field-by-field check guards against a future custom __eq__.
    assert first.name == second.name
    assert first.pv_azimuth == second.pv_azimuth
    assert first.pv_tilt == second.pv_tilt
    assert first.pv_rooftop_capacity_in_kilowatt == second.pv_rooftop_capacity_in_kilowatt
    assert first.share_of_maximum_pv_potential == second.share_of_maximum_pv_potential
    assert first.building_code == second.building_code
    assert first.conditioned_floor_area_in_m2 == second.conditioned_floor_area_in_m2
    assert first.number_of_dwellings_per_building == second.number_of_dwellings_per_building
    assert first.norm_heating_load_in_kilowatt == second.norm_heating_load_in_kilowatt
    assert first.lpg_households == second.lpg_households
    assert first.weather_location == second.weather_location


@pytest.mark.base
def test_json_round_trip_from_default() -> None:
    """Serializing and deserializing the default config preserves all fields."""
    cfg = BuildingPVWeatherConfig.get_default()
    serialized = cfg.to_json()
    assert isinstance(serialized, str)

    cfg2 = BuildingPVWeatherConfig.from_json(serialized)
    assert isinstance(cfg2, BuildingPVWeatherConfig)

    # Field-by-field equality (round-trip may widen ints to floats, e.g.
    # 180 -> 180.0, which compare equal, so compare value-by-value rather
    # than relying on a strict-type equality).
    assert cfg2.name == cfg.name
    assert cfg2.pv_azimuth == cfg.pv_azimuth
    assert cfg2.pv_tilt == cfg.pv_tilt
    assert cfg2.pv_rooftop_capacity_in_kilowatt is cfg.pv_rooftop_capacity_in_kilowatt
    assert cfg2.share_of_maximum_pv_potential == cfg.share_of_maximum_pv_potential
    assert cfg2.building_code == cfg.building_code
    assert cfg2.conditioned_floor_area_in_m2 == cfg.conditioned_floor_area_in_m2
    assert cfg2.number_of_dwellings_per_building == cfg.number_of_dwellings_per_building
    assert cfg2.norm_heating_load_in_kilowatt is cfg.norm_heating_load_in_kilowatt
    assert cfg2.lpg_households == cfg.lpg_households
    assert cfg2.weather_location == cfg.weather_location

    # And the dataclass equality should hold as well.
    assert cfg2 == cfg


@pytest.mark.base
def test_json_round_trip_preserves_none_fields() -> None:
    """``None`` optional fields survive a JSON round-trip (not coerced to 0 / 'null')."""
    cfg = BuildingPVWeatherConfig(
        name="BuildingPVConfig",
        pv_azimuth=180,
        pv_tilt=30,
        pv_rooftop_capacity_in_kilowatt=None,
        share_of_maximum_pv_potential=1,
        building_code="DE.N.SFH.05.Gen.ReEx.001.002",
        conditioned_floor_area_in_m2=121.2,
        number_of_dwellings_per_building=1,
        norm_heating_load_in_kilowatt=None,
        lpg_households=["CHR01_Couple_both_at_Work"],
        weather_location="AACHEN",
    )

    serialized = cfg.to_json()
    cfg2 = BuildingPVWeatherConfig.from_json(serialized)

    assert cfg2.pv_rooftop_capacity_in_kilowatt is None
    assert cfg2.norm_heating_load_in_kilowatt is None
    assert cfg2 == cfg


@pytest.mark.base
def test_json_round_trip_preserves_multi_element_lpg_households() -> None:
    """A multi-element ``lpg_households`` list survives a JSON round-trip intact."""
    households = ["CHR01_Couple_both_at_Work", "CHR02_Family_with_one_Child"]
    cfg = BuildingPVWeatherConfig(
        name="BuildingPVConfig",
        pv_azimuth=180,
        pv_tilt=30,
        pv_rooftop_capacity_in_kilowatt=10.0,
        share_of_maximum_pv_potential=1,
        building_code="DE.N.SFH.05.Gen.ReEx.001.002",
        conditioned_floor_area_in_m2=121.2,
        number_of_dwellings_per_building=1,
        norm_heating_load_in_kilowatt=5.5,
        lpg_households=households,
        weather_location="AACHEN",
    )

    serialized = cfg.to_json()
    cfg2 = BuildingPVWeatherConfig.from_json(serialized)

    assert cfg2.lpg_households == households
    assert isinstance(cfg2.lpg_households, list)
    assert len(cfg2.lpg_households) == 2
    assert cfg2 == cfg
