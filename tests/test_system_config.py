"""Tests for the EnergySystemConfig dataclass and its default-factory classmethods."""

import pytest

from hisim.building_sizer_utils.interface_configs.system_config import (
    EnergySystemConfig,
)
from hisim.loadtypes import ComponentType, HeatingSystems


# Mapping of factory classmethod name -> expected HeatingSystems value.
# Pinned from the public contract consumed by modular_household_config.py.
FACTORY_HEATING_SYSTEMS: dict[str, HeatingSystems] = {
    "get_default_config_for_energy_system_gas": HeatingSystems.GAS_HEATING,
    "get_default_config_for_energy_system_oil": HeatingSystems.OIL_HEATING,
    "get_default_config_for_energy_system_heatpump": HeatingSystems.HEAT_PUMP,
    "get_default_config_for_energy_system_district_heating": HeatingSystems.DISTRICT_HEATING,
    "get_default_config_for_energy_system_pellet_heating": HeatingSystems.PELLET_HEATING,
    "get_default_config_for_energy_system_wood_chip_heating": HeatingSystems.WOOD_CHIP_HEATING,
    "get_default_config_for_energy_system_hydrogen": HeatingSystems.HYDROGEN_HEATING,
    "get_default_config_for_energy_system_electric": HeatingSystems.ELECTRIC_HEATING,
    "get_default_config_for_energy_system_gas_solar_thermal": HeatingSystems.GAS_SOLAR_THERMAL,
    "get_default_config_for_energy_system_heatpump_solar_thermal": HeatingSystems.HEAT_PUMP_SOLAR_THERMAL,
}


@pytest.mark.base
def test_default_constructor() -> None:
    """EnergySystemConfig() uses the dataclass field defaults on lines 11-13."""
    cfg: EnergySystemConfig = EnergySystemConfig()
    assert cfg.heating_system == HeatingSystems.DISTRICT_HEATING
    assert cfg.heat_distribution_system == ComponentType.HEAT_DISTRIBUTION_SYSTEM_FLOORHEATING
    assert cfg.share_of_maximum_pv_potential == 1.0
    assert cfg.use_battery_and_ems is True


@pytest.mark.base
@pytest.mark.parametrize("factory_name,expected_heating_system", list(FACTORY_HEATING_SYSTEMS.items()))
def test_factory_heating_system(factory_name: str, expected_heating_system: HeatingSystems) -> None:
    """Each factory returns the documented HeatingSystems value."""
    factory = getattr(EnergySystemConfig, factory_name)
    cfg: EnergySystemConfig = factory()
    assert cfg.heating_system == expected_heating_system


@pytest.mark.base
@pytest.mark.parametrize("factory_name", list(FACTORY_HEATING_SYSTEMS.keys()))
def test_factory_shared_non_heating_defaults(factory_name: str) -> None:
    """Only heating_system varies across factories; the other three fields are invariant."""
    factory = getattr(EnergySystemConfig, factory_name)
    cfg: EnergySystemConfig = factory()
    assert cfg.heat_distribution_system == ComponentType.HEAT_DISTRIBUTION_SYSTEM_FLOORHEATING
    assert cfg.share_of_maximum_pv_potential == 1.0
    assert cfg.use_battery_and_ems is True


@pytest.mark.base
@pytest.mark.parametrize("factory_name", list(FACTORY_HEATING_SYSTEMS.keys()))
def test_factory_instance_independence(factory_name: str) -> None:
    """Two calls to the same factory return equal but distinct objects (no shared mutable default).

    Mutating one instance must not affect the other, confirming the factories do not
    hand out references to a shared underlying object.
    """
    factory = getattr(EnergySystemConfig, factory_name)
    a: EnergySystemConfig = factory()
    b: EnergySystemConfig = factory()
    assert a == b
    assert a is not b

    # Capture original values, then mutate `a` and confirm `b` is untouched.
    original_heating = b.heating_system
    original_share = b.share_of_maximum_pv_potential
    original_ems = b.use_battery_and_ems
    original_dist = b.heat_distribution_system

    a.heating_system = HeatingSystems.ELECTRIC_HEATING
    a.share_of_maximum_pv_potential = 0.25
    a.use_battery_and_ems = False
    a.heat_distribution_system = ComponentType.HEAT_DISTRIBUTION_SYSTEM_RADIATOR

    assert b.heating_system == original_heating
    assert b.share_of_maximum_pv_potential == original_share
    assert b.use_battery_and_ems is original_ems
    assert b.heat_distribution_system == original_dist
    assert a != b


@pytest.mark.base
@pytest.mark.parametrize("value", [0.0, 0.5, 1.0])
def test_share_of_maximum_pv_potential_boundary_values(value: float) -> None:
    """share_of_maximum_pv_potential accepts boundary values without coercion.

    Constructing with 0.0 and 0.5 (and 1.0) must round-trip exactly, confirming the
    float field is not silently clamped or converted by the dataclass.
    """
    cfg: EnergySystemConfig = EnergySystemConfig(share_of_maximum_pv_potential=value)
    assert cfg.share_of_maximum_pv_potential == value
    assert isinstance(cfg.share_of_maximum_pv_potential, float)


@pytest.mark.base
def test_round_trip_via_dataclass_json() -> None:
    """to_dict / from_dict round-trip preserves the @dataclass_json contract."""
    cfg: EnergySystemConfig = EnergySystemConfig.get_default_config_for_energy_system_gas()
    restored: EnergySystemConfig = EnergySystemConfig.from_dict(cfg.to_dict())
    assert restored == cfg


@pytest.mark.base
def test_partial_override_preserves_other_defaults() -> None:
    """Overriding a single field leaves the other three at their dataclass defaults.

    Confirms that ``share_of_maximum_pv_potential=0.0`` and
    ``use_battery_and_ems=False`` are not silently coerced or clamped, and that
    constructing with one overridden field does not disturb the remaining defaults.
    """
    cfg_pv: EnergySystemConfig = EnergySystemConfig(share_of_maximum_pv_potential=0.0)
    assert cfg_pv.share_of_maximum_pv_potential == 0.0
    assert cfg_pv.heating_system == HeatingSystems.DISTRICT_HEATING
    assert cfg_pv.heat_distribution_system == ComponentType.HEAT_DISTRIBUTION_SYSTEM_FLOORHEATING
    assert cfg_pv.use_battery_and_ems is True

    cfg_ems: EnergySystemConfig = EnergySystemConfig(use_battery_and_ems=False)
    assert cfg_ems.use_battery_and_ems is False
    assert cfg_ems.heating_system == HeatingSystems.DISTRICT_HEATING
    assert cfg_ems.heat_distribution_system == ComponentType.HEAT_DISTRIBUTION_SYSTEM_FLOORHEATING
    assert cfg_ems.share_of_maximum_pv_potential == 1.0


@pytest.mark.base
def test_factory_heating_systems_are_all_distinct() -> None:
    """The ten factories must produce ten distinct HeatingSystems values.

    Guards against two factories accidentally returning the same enum member,
    which would silently collapse two energy-system variants into one.
    """
    produced: set[HeatingSystems] = {
        getattr(EnergySystemConfig, factory_name)().heating_system
        for factory_name in FACTORY_HEATING_SYSTEMS
    }
    assert len(produced) == len(FACTORY_HEATING_SYSTEMS) == 10
    assert produced == set(FACTORY_HEATING_SYSTEMS.values())
