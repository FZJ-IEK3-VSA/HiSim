"""Tests for the advanced fuel cell controller sensor-index precomputation.

These tests cover the changes made for issue #1717: the
``ExtendedControllerSimulation`` precomputes the tank-layer indices for the
upper/lower temperature sensors once in ``__init__`` instead of scanning
``heights_in_tank`` every timestep, and validates each sensor height
individually at construction time (closing the previous ``or``-short-circuit
bug that never checked the lower sensors).
"""

# clean

# These tests deliberately exercise the private index-precomputation helpers
# and precomputed attributes of ``ExtendedControllerSimulation``.
# pylint: disable=protected-access

import pytest

from hisim.components import advanced_fuel_cell as chp
from hisim.components.advanced_fuel_cell_controller import ExtendedControllerSimulation
from hisim.components.configuration import (
    CHPControllerConfig,
    GasControllerConfig,
)

HEIGHTS_IN_TANK: list[int] = CHPControllerConfig.heights_in_tank  # [0, 20, 40, 60, 80, 100]


def _make_controller() -> ExtendedControllerSimulation:
    """Build an ExtendedControllerSimulation using class-level config constants."""
    return ExtendedControllerSimulation()


@pytest.mark.base
def test_sensor_index_returns_correct_index() -> None:
    """_sensor_index returns the position of a valid sensor height."""
    assert ExtendedControllerSimulation._sensor_index(HEIGHTS_IN_TANK, 0, "x", "upper") == 0
    assert ExtendedControllerSimulation._sensor_index(HEIGHTS_IN_TANK, 20, "x", "upper") == 1
    assert ExtendedControllerSimulation._sensor_index(HEIGHTS_IN_TANK, 60, "x", "lower") == 3
    assert ExtendedControllerSimulation._sensor_index(HEIGHTS_IN_TANK, 100, "x", "lower") == 5


@pytest.mark.base
def test_sensor_index_raises_for_invalid_height() -> None:
    """_sensor_index raises a self-describing ValueError for a bad sensor height."""
    with pytest.raises(ValueError, match="not in"):
        ExtendedControllerSimulation._sensor_index(HEIGHTS_IN_TANK, 50, "CHP", "lower")


@pytest.mark.base
def test_init_precomputes_default_sensor_indices() -> None:
    """__init__ precomputes the correct tank-layer indices for the default config."""
    ctrl = _make_controller()
    # CHP: upper=20 % -> idx 1, lower=60 % -> idx 3
    assert ctrl._chp_upper_idx == 1
    assert ctrl._chp_lower_idx == 3
    # Gas: upper=20 % -> idx 1, lower=80 % -> idx 4
    assert ctrl._gas_upper_idx == 1
    assert ctrl._gas_lower_idx == 4
    # The indices match the old per-timestep enumerate loop.
    assert ctrl._chp_upper_idx == HEIGHTS_IN_TANK.index(CHPControllerConfig.height_upper_sensor)
    assert ctrl._chp_lower_idx == HEIGHTS_IN_TANK.index(CHPControllerConfig.height_lower_sensor)
    assert ctrl._gas_upper_idx == HEIGHTS_IN_TANK.index(GasControllerConfig.height_upper_sensor)
    assert ctrl._gas_lower_idx == HEIGHTS_IN_TANK.index(GasControllerConfig.height_lower_sensor)


@pytest.mark.base
def test_init_raises_for_misconfigured_chp_lower_sensor(monkeypatch: pytest.MonkeyPatch) -> None:
    """A misconfigured CHP lower sensor height raises at init.

    The old ``(upper or lower) not in heights`` check never validated the lower
    sensor because Python's ``or`` returns the first truthy operand (the upper
    sensor).  This test confirms the lower sensor is now validated independently.
    """
    monkeypatch.setattr(CHPControllerConfig, "height_lower_sensor", 50)
    with pytest.raises(ValueError, match="CHP lower"):
        ExtendedControllerSimulation()


@pytest.mark.base
def test_init_raises_for_misconfigured_gas_lower_sensor(monkeypatch: pytest.MonkeyPatch) -> None:
    """A misconfigured gas heater lower sensor height raises at init."""
    monkeypatch.setattr(GasControllerConfig, "height_lower_sensor", 50)
    with pytest.raises(ValueError, match="gas heater lower"):
        ExtendedControllerSimulation()


@pytest.mark.base
def test_init_raises_for_misconfigured_upper_sensor(monkeypatch: pytest.MonkeyPatch) -> None:
    """A misconfigured upper sensor height raises at init."""
    monkeypatch.setattr(CHPControllerConfig, "height_upper_sensor", 50)
    with pytest.raises(ValueError, match="CHP upper"):
        ExtendedControllerSimulation()


@pytest.mark.base
def test_regulate_gas_heater_uses_upper_sensor_index() -> None:
    """regulate_gas_heater switches on via the precomputed upper-sensor index.

    Only the upper-sensor position (idx 1, 20 %) is below ``switch_on`` (55 C);
    all others are above, so a wrong index would not trigger the switch-on.
    """
    ctrl = _make_controller()
    temperatures = [60.0, 50.0, 60.0, 60.0, 60.0, 60.0]
    state, runtime = ctrl.regulate_gas_heater(
        temperatures, previous_state=0, runtime_counter=0, seconds_per_timestep=60
    )
    assert state == 1
    assert runtime == 1


@pytest.mark.base
def test_regulate_gas_heater_uses_lower_sensor_index() -> None:
    """regulate_gas_heater switches off via the precomputed lower-sensor index.

    Upper sensor (idx 1) below ``switch_on`` -> on; lower sensor (idx 4, 80 %)
    above ``switch_off`` (70 C) with enough runtime -> off.  All other positions
    are below ``switch_off``, so a wrong lower index would not trigger shutdown.
    """
    ctrl = _make_controller()
    temperatures = [50.0, 50.0, 50.0, 50.0, 75.0, 50.0]
    state, runtime = ctrl.regulate_gas_heater(
        temperatures, previous_state=1, runtime_counter=10_000, seconds_per_timestep=60
    )
    assert state == 0
    assert runtime == 0


@pytest.mark.base
def test_regulate_chp_mode_heat_uses_upper_sensor_index(monkeypatch: pytest.MonkeyPatch) -> None:
    """regulate_chp_mode_heat switches on via the precomputed upper-sensor index.

    Only the upper-sensor position (idx 1, 20 %) is below ``switch_on`` (60 C).
    ``chp.CHPConfig.p_el_max`` is read as a class attribute inside
    ``regulate_chp_mode_heat`` (a pre-existing access pattern in this module);
    it is supplied here as a class-level fixture so the full code path runs.
    """
    monkeypatch.setattr(chp.CHPConfig, "p_el_max", 3_000, raising=False)
    ctrl = _make_controller()
    temperatures = [70.0, 50.0, 70.0, 70.0, 70.0, 70.0]
    state, runtime, _power = ctrl.regulate_chp_mode_heat(
        temperatures,
        previous_state=0,
        runtime_chp=0,
        pv_production=200.0,
        electricity_demand_household=1000.0,
        seconds_per_timestep=60,
    )
    assert state == 1
    assert runtime == 1


@pytest.mark.base
def test_regulate_chp_mode_heat_uses_lower_sensor_index(monkeypatch: pytest.MonkeyPatch) -> None:
    """regulate_chp_mode_heat switches off via the precomputed lower-sensor index.

    Upper sensor (idx 1) below ``switch_on`` -> on; lower sensor (idx 3, 60 %)
    above ``switch_off`` (65 C) with enough runtime -> off.  All other positions
    are below ``switch_off``, so a wrong lower index would not trigger shutdown.
    """
    monkeypatch.setattr(chp.CHPConfig, "p_el_max", 3_000, raising=False)
    ctrl = _make_controller()
    temperatures = [50.0, 50.0, 50.0, 70.0, 50.0, 50.0]
    state, runtime, _power = ctrl.regulate_chp_mode_heat(
        temperatures,
        previous_state=1,
        runtime_chp=10_000,
        pv_production=200.0,
        electricity_demand_household=1000.0,
        seconds_per_timestep=60,
    )
    assert state == 0
    assert runtime == 0


@pytest.mark.base
def test_regulate_chp_mode_heat_switches_off_at_exact_minimum_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Minimum-runtime boundary is ``>=`` (issue #1792).

    At exactly ``minimum_timesteps`` active steps the CHP may switch off in heat
    mode; ``>`` would force one extra timestep every on-cycle, contradicting the
    inline comment "switch off is possible if chp has run **at least** xx min".
    """
    monkeypatch.setattr(chp.CHPConfig, "p_el_max", 3_000, raising=False)
    ctrl = _make_controller()
    seconds_per_timestep = 60
    minimum_timesteps = (CHPControllerConfig.minimum_runtime_minutes * 60) / seconds_per_timestep
    # lower sensor (idx 3) above switch_off -> off allowed once runtime permits
    temperatures = [50.0, 50.0, 50.0, 70.0, 50.0, 50.0]

    # exactly the required count -> may switch off now
    state, runtime, _ = ctrl.regulate_chp_mode_heat(
        temperatures,
        previous_state=1,
        runtime_chp=minimum_timesteps,
        pv_production=200.0,
        electricity_demand_household=1000.0,
        seconds_per_timestep=seconds_per_timestep,
    )
    assert state == 0
    assert runtime == 0

    # one step short of the required count -> must keep running
    state, runtime, _ = ctrl.regulate_chp_mode_heat(
        temperatures,
        previous_state=1,
        runtime_chp=minimum_timesteps - 1,
        pv_production=200.0,
        electricity_demand_household=1000.0,
        seconds_per_timestep=seconds_per_timestep,
    )
    assert state == 1
    assert runtime == minimum_timesteps  # counter incremented after the check


@pytest.mark.base
def test_regulate_gas_heater_switches_off_at_exact_minimum_runtime() -> None:
    """Minimum-runtime boundary is ``>=`` for the gas heater (issue #1792)."""
    ctrl = _make_controller()
    seconds_per_timestep = 60
    minimum_timesteps = (GasControllerConfig.minimum_runtime_minutes * 60) / seconds_per_timestep
    # upper sensor (idx 1) below switch_on -> on; lower sensor (idx 4) above switch_off -> off allowed
    temperatures = [50.0, 50.0, 50.0, 50.0, 75.0, 50.0]

    # exactly the required count -> may switch off now
    state, runtime = ctrl.regulate_gas_heater(
        temperatures,
        previous_state=1,
        runtime_counter=minimum_timesteps,
        seconds_per_timestep=seconds_per_timestep,
    )
    assert state == 0
    assert runtime == 0

    # one step short of the required count -> must keep running
    state, runtime = ctrl.regulate_gas_heater(
        temperatures,
        previous_state=1,
        runtime_counter=minimum_timesteps - 1,
        seconds_per_timestep=seconds_per_timestep,
    )
    assert state == 1
    assert runtime == minimum_timesteps  # counter incremented after the check
