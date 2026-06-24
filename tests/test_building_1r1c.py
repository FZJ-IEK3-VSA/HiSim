"""Tests for the building_1r1c module."""

import math
import numpy as np

from hisim.components.building_1r1c import (
    Building1R1C,
    Building1R1CConfig,
    SolarGainsCalculator,
)
from hisim.simulationparameters import SimulationParameters


def make_standard_config():
    # simple, consistent config for tests
    thermal_capacity = Building1R1C.get_heat_capacity_classes()["medium"] * 50.0
    u_values = {
        "floor": 0.2,
        "wall": 0.3,
        "roof": 0.2,
        "windows": 1.1,
        "door": 1.0,
    }
    areas = {
        "floor": 60.0,
        "wall": 100.0,
        "roof": 80.0,
        "windows": 100.0 * 0.2,
        "door": 2.0,
    }
    config = Building1R1CConfig(
        thermal_capacity=thermal_capacity,
        u_values=u_values,  # type: ignore
        areas=areas,  # type: ignore
        use_correction_factor=True,
        building_name="test",
        name="Building1R1C",
        target_temperature=20.0,
        solar_gain_reduction_factor=0.3,
        air_volume=200.0,
        air_exchange_rate=0.5,
    )
    return config


def test_heat_capacity_class_lookup():
    """Tests if the heat capacity classes can be loaded successfully."""
    classes = Building1R1C.get_heat_capacity_classes()
    assert "medium" in classes
    assert math.isclose(classes["medium"], 1.65e5)


def test_calc_k_c_bounds_and_monotonic():
    """Tests if the correction factor values make general sense."""
    # k_c should be positive and generally < 1 (correction reducing losses)
    k_short = Building1R1C.calc_k_c(3600.0)  # tau 1 hour
    k_long = Building1R1C.calc_k_c(3600.0 * 24 * 10)  # tau 10 days
    assert 0 < k_short <= 1.0
    assert 0 < k_long <= 1.0
    # for larger tau the correction should be closer to 1 (less correction)
    assert k_long >= k_short


def test_solar_gains_zero_when_no_radiation():
    """Tests if the solar gains return zero if no radiation exists."""
    # If both direct and diffuse are zero, result must be zero
    out = SolarGainsCalculator.calc_total_solar_gains(
        dhi=0.0, dni=0.0, dni_e=1367.0, z=0.0, a=10.0
    )
    assert out == 0.0


def test_direct_irrad_positive_when_sun_up():
    """Tests if the direct irradiation is positive when the sun is up."""
    # convert degrees to radians for zenith approx 45 deg elevation -> z = 45 deg
    z_deg = 45.0
    dni = 800.0
    direct = SolarGainsCalculator.calc_direct_irrad(dni, np.radians(z_deg))
    assert direct > 0.0


def test_total_heat_transfer_coefficient_matches_manual():
    """Tests if the total heat transfer coefficient is correctly calculated."""
    config = make_standard_config()
    params = SimulationParameters.one_day_only(2020, 3600)
    b = Building1R1C(params, config)
    # compute expected manually using same rule as implementation
    expected = 0.0
    for hp in ["floor", "wall", "roof", "windows", "door"]:
        if hp == "floor":
            expected += config.areas[hp] * config.u_values[hp] / 2.0
        else:
            expected += config.areas[hp] * config.u_values[hp]
    heat_capacity = config.air_heat_cap * config.air_volume
    expected += heat_capacity * config.air_exchange_rate
    assert math.isclose(b.get_total_heat_transfer_coefficient(), expected, rel_tol=1e-9)


def test_calc_new_internal_temperature():
    """Tests whether the calculation of new internal temperature is sensible."""
    config = make_standard_config()
    params = SimulationParameters.one_day_only(2020, 3600)
    b = Building1R1C(params, config)
    t0 = 18.0
    t_out = 0.0
    # no internal gains
    p_in = 0.0
    ta = b.calc_new_internal_temperature(p_in, t0, t_out, params.seconds_per_timestep, method="analytical")
    tt = b.calc_new_internal_temperature(p_in, t0, t_out, params.seconds_per_timestep, method="trapezoidal_rule")
    # results should be close
    assert abs(ta - tt) < 0.5
    # temperature should also drop in the absense of internal gains and with a cold outside
    assert ta < 18.0
    assert tt < 18.0
