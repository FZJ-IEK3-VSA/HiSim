"""Tests for the pure ``DistrictConfig.get_default`` factory.

``DistrictConfig.get_default`` (in ``system_setups/district_system_setup/simple_district.py``)
is the only side-effect-free function in that module: it takes a single optional
string argument and returns a fully-populated ``DistrictConfig`` dataclass without
any I/O. These tests pin its default values, the computed product fields, the
location-propagation behaviour and its determinism. The ``setup_function`` in the
same module builds simulator components and mutates ``my_sim`` and is therefore
intentionally not covered here.
"""

from __future__ import annotations

import datetime

import pytest

from hisim import loadtypes
from hisim.components import controller_l2_district_energy_management_system
from hisim.components import generic_pv_system
from hisim.components.weather import WeatherDataSourceEnum
from system_setups.district_system_setup.simple_district import DistrictConfig


@pytest.mark.base
def test_get_default_scalar_fields() -> None:
    """``get_default`` populates the scalar defaults documented in the issue."""
    cfg = DistrictConfig.get_default()

    assert cfg.number_of_buildings == 2
    assert cfg.seconds_per_timestep == 60 * 60
    assert cfg.latitude_district == 50.775
    assert cfg.longitude_district == 6.083
    assert cfg.azimuth_pv_district == 180
    assert cfg.tilt_pv_district == 30
    assert cfg.share_of_maximum_pv_potential_pv_district == 1.0
    assert cfg.lifetime_pv_district == 25
    assert cfg.ems_district_existing is False
    assert cfg.ems_district_limit_to_shave == 0
    assert cfg.prediction_horizon_pv_district is None


@pytest.mark.base
def test_get_default_datetime_fields() -> None:
    """``get_default`` returns the expected start/end datetimes."""
    cfg = DistrictConfig.get_default()

    assert cfg.start_date == datetime.datetime(2023, 1, 1, 0, 0, 0)
    assert cfg.end_date == datetime.datetime(2023, 2, 1, 0, 0, 0)


@pytest.mark.base
def test_get_default_enum_fields() -> None:
    """``get_default`` selects the documented enum members and district name."""
    cfg = DistrictConfig.get_default()

    assert cfg.weather_data_source_district == WeatherDataSourceEnum.DWD_TRY
    assert (
        cfg.module_database_pv_district
        == generic_pv_system.PVLibModuleAndInverterEnum.SANDIA_MODULE_DATABASE
    )
    assert (
        cfg.inverter_database_pv_district
        == generic_pv_system.PVLibModuleAndInverterEnum.SANDIA_INVERTER_DATABASE
    )
    assert (
        cfg.ems_district_strategy
        == controller_l2_district_energy_management_system.EMSControlStrategy.DISTRICT_OPTIMIZECONSUMPTION_PARALLEL
    )
    assert cfg.district_name == loadtypes.DistrictNames.DISTRICT.value


@pytest.mark.base
def test_get_default_computed_product_fields() -> None:
    """The CO2 footprint and investment costs are products of the documented factors."""
    cfg = DistrictConfig.get_default()

    assert cfg.co2_footprint_pv_district == pytest.approx(15000 * 1e-3 * 330.51)
    assert cfg.investment_costs_in_euro_pv_district == pytest.approx(
        15000 * 1e-3 * 794.41
    )


@pytest.mark.base
def test_get_default_location_propagation_default() -> None:
    """The default call propagates ``"AACHEN"`` to both location fields."""
    cfg = DistrictConfig.get_default()

    assert cfg.location_district == "AACHEN"
    assert cfg.location_pv_district == "AACHEN"


@pytest.mark.base
def test_get_default_location_propagation_custom() -> None:
    """A custom location is propagated to both ``location_district`` and ``location_pv_district``."""
    cfg = DistrictConfig.get_default(location_district="BERLIN")

    assert cfg.location_district == "BERLIN"
    assert cfg.location_pv_district == "BERLIN"


@pytest.mark.base
def test_get_default_location_empty_string() -> None:
    """An empty-string location is propagated verbatim (no validation is performed)."""
    cfg = DistrictConfig.get_default(location_district="")

    assert cfg.location_district == ""
    assert cfg.location_pv_district == ""


@pytest.mark.base
def test_get_default_is_deterministic_and_independent() -> None:
    """Repeated calls return equal but distinct instances that do not share state."""
    cfg1 = DistrictConfig.get_default()
    cfg2 = DistrictConfig.get_default()

    assert cfg1 == cfg2
    assert cfg1 is not cfg2

    # Mutating one instance must not affect the other.
    original = cfg2.number_of_buildings
    cfg1.number_of_buildings = original + 1
    assert cfg2.number_of_buildings == original
