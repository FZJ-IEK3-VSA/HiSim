"""The hot-water rules the dual-circuit generators share: the lift a charge asks for, and the circuit's return.

The electric heater and the district-heating substation, and their controllers, call these functions instead of
keeping a copy each; the tests pin the rules once for both.
"""

from typing import Optional

import pytest

from hisim import component as cp
from hisim import loadtypes as lt
from hisim.components.dual_circuit_system import DualCircuitHotWater
from hisim.config import ComponentID


@pytest.mark.base
@pytest.mark.parametrize(
    ("aim_temperature_in_celsius", "storage_temperature_in_celsius", "hysteresis_in_kelvin", "expected_lift_in_kelvin"),
    [
        (60.0, 50.0, 15.0, 25.0),  # below the aim: the distance plus the hysteresis
        (60.0, 62.0, 15.0, 15.0),  # above the aim: the hysteresis only, the generator cannot cool
        (60.0, 60.0, 10.0, 10.0),  # exactly at the aim
        (60.0, 50.0, 0.0, 10.0),  # no hysteresis
    ],
)
def test_a_hot_water_charge_asks_for_the_tank_s_distance_below_the_aim_plus_the_hysteresis(
    aim_temperature_in_celsius: float,
    storage_temperature_in_celsius: float,
    hysteresis_in_kelvin: float,
    expected_lift_in_kelvin: float,
) -> None:
    """A lift without the hysteresis would end a charge at the aim; a negative lift would ask a heater to cool."""
    assert (
        DualCircuitHotWater.lift_in_kelvin(
            aim_temperature_in_celsius=aim_temperature_in_celsius,
            storage_temperature_in_celsius=storage_temperature_in_celsius,
            hysteresis_in_kelvin=hysteresis_in_kelvin,
        )
        == expected_lift_in_kelvin
    )


@pytest.mark.base
@pytest.mark.parametrize(("has_tank", "expected_return_in_celsius"), [(True, 52.3), (False, 0.0)])
def test_the_hot_water_return_is_the_tank_s_step_mean_or_the_marker_without_a_tank(
    has_tank: bool, expected_return_in_celsius: float
) -> None:
    """A generator that read a return without a tank would fail; one that ignored its input would heat the wrong water."""
    source = cp.ComponentOutput("Tank", "StepMean", lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS, component_id=ComponentID("Tank"))
    source.global_index = 0
    channel: Optional[cp.ComponentInput] = None
    if has_tank:
        channel = cp.ComponentInput("Heater", "ReturnTemperature", lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS, True)
        channel.source_output = source
    stsv = cp.SingleTimeStepValues(1)
    stsv.values[0] = 52.3
    assert DualCircuitHotWater.return_temperature_in_celsius(stsv, channel) == expected_return_in_celsius
