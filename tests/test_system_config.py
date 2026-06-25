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
    """Two calls to the same factory return equal but distinct objects (no shared mutable default)."""
    factory = getattr(EnergySystemConfig, factory_name)
    a: EnergySystemConfig = factory()
    b: EnergySystemConfig = factory()
    assert a == b
    assert a is not b


@pytest.mark.base
def test_round_trip_via_dataclass_json() -> None:
    """to_dict / from_dict round-trip preserves the @dataclass_json contract."""
    cfg: EnergySystemConfig = EnergySystemConfig.get_default_config_for_energy_system_gas()
    restored: EnergySystemConfig = EnergySystemConfig.from_dict(cfg.to_dict())
    assert restored == cfg
