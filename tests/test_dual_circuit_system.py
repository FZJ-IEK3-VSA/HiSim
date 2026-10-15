"""The hot-water rules the dual-circuit generators share: the diverter valve, the lift a charge asks for, and the return.

The electric heater and the district-heating substation, and their controllers, call these functions instead of
keeping a copy each; the tests pin the rules once for both.
"""

from typing import Optional

import pytest

from hisim import component as cp
from hisim import loadtypes as lt
from hisim.components.dual_circuit_system import DiverterValve, DualCircuitHotWater, HeatingMode, SetTemperatureConfig
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


class TestDiverterValve:

    """The diverter valve's two-point hot-water control with its latch, and the mode it sets beside space heating."""

    SET_TEMPERATURE_IN_CELSIUS = 60.0
    HYSTERESIS_IN_KELVIN = 15.0

    #: The hot-water transition table: the mode at the start of the step, the tank temperature in °C, and whether a
    #: charge runs in this step. The switch-on point is 60 - 15 = 45 °C.
    CHARGE_TRANSITIONS = [
        (HeatingMode.OFF, 44.9, True),  # below the switch-on point: a charge starts
        (HeatingMode.OFF, 45.0, False),  # exactly at the switch-on point: no charge starts
        (HeatingMode.OFF, 50.0, False),
        (HeatingMode.SPACE_HEATING, 44.9, True),
        (HeatingMode.SPACE_HEATING, 50.0, False),  # space heating alone does not latch a charge
        (HeatingMode.DOMESTIC_HOT_WATER, 50.0, True),  # a running charge goes on above the switch-on point
        (HeatingMode.DOMESTIC_HOT_WATER, 59.99, True),
        (HeatingMode.DOMESTIC_HOT_WATER, 60.0, False),  # at the set temperature the charge ends
        (HeatingMode.SPACE_HEATING_AND_DOMESTIC_HOT_WATER_IN_PARALLEL, 44.9, True),
        (HeatingMode.SPACE_HEATING_AND_DOMESTIC_HOT_WATER_IN_PARALLEL, 45.0, True),  # the latch holds in parallel
        (HeatingMode.SPACE_HEATING_AND_DOMESTIC_HOT_WATER_IN_PARALLEL, 59.99, True),
        (HeatingMode.SPACE_HEATING_AND_DOMESTIC_HOT_WATER_IN_PARALLEL, 60.0, False),
    ]

    @pytest.mark.base
    @pytest.mark.parametrize(("controller_mode", "storage_temperature_in_celsius", "expected"), CHARGE_TRANSITIONS)
    def test_every_transition_of_the_hot_water_charge(
        self, controller_mode: HeatingMode, storage_temperature_in_celsius: float, expected: bool
    ) -> None:
        """Catches a charge that ends at the switch-on point in one of its modes, or one that starts above it.

        A charge beside space heating that ended at 45 °C instead of 60 °C would top the tank up after every draw.
        """
        assert (
            DiverterValve.hot_water_charge_needed(
                controller_mode=controller_mode,
                storage_temperature_in_celsius=storage_temperature_in_celsius,
                set_temperature_in_celsius=self.SET_TEMPERATURE_IN_CELSIUS,
                hysteresis_in_kelvin=self.HYSTERESIS_IN_KELVIN,
            )
            is expected
        )

    #: The mode transitions with the parallel option on a heating day: the mode at the start of the step, the tank
    #: temperature in °C, the space-heating water temperature in °C (set 30 °C, so space heating is needed below 45 °C),
    #: and the mode of the step.
    MODE_TRANSITIONS_WITH_PARALLEL_OPTION = [
        (HeatingMode.SPACE_HEATING, 44.0, 25.0, HeatingMode.SPACE_HEATING_AND_DOMESTIC_HOT_WATER_IN_PARALLEL),
        (HeatingMode.SPACE_HEATING_AND_DOMESTIC_HOT_WATER_IN_PARALLEL, 50.0, 25.0, HeatingMode.SPACE_HEATING_AND_DOMESTIC_HOT_WATER_IN_PARALLEL),
        (HeatingMode.SPACE_HEATING_AND_DOMESTIC_HOT_WATER_IN_PARALLEL, 60.0, 25.0, HeatingMode.SPACE_HEATING),
        (HeatingMode.SPACE_HEATING_AND_DOMESTIC_HOT_WATER_IN_PARALLEL, 50.0, 46.0, HeatingMode.DOMESTIC_HOT_WATER),
        (HeatingMode.DOMESTIC_HOT_WATER, 50.0, 25.0, HeatingMode.SPACE_HEATING_AND_DOMESTIC_HOT_WATER_IN_PARALLEL),
        (HeatingMode.SPACE_HEATING, 50.0, 25.0, HeatingMode.SPACE_HEATING),
        (HeatingMode.OFF, 50.0, 46.0, HeatingMode.OFF),
    ]

    @pytest.mark.base
    @pytest.mark.parametrize(
        ("current_mode", "storage_temperature_in_celsius", "space_heating_water_temperature_in_celsius", "expected_mode"),
        MODE_TRANSITIONS_WITH_PARALLEL_OPTION,
    )
    def test_every_mode_transition_with_the_parallel_option(
        self,
        current_mode: HeatingMode,
        storage_temperature_in_celsius: float,
        space_heating_water_temperature_in_celsius: float,
        expected_mode: HeatingMode,
    ) -> None:
        """Catches a parallel charge that is dropped above the switch-on point, or a mode that ignores one of the circuits."""
        mode = DiverterValve.determine_operating_mode(
            with_domestic_hot_water_preparation=True,
            current_controller_mode=current_mode,
            is_heating_season=True,
            water_temperature_input_sh_in_celsius=space_heating_water_temperature_in_celsius,
            water_temperature_input_dhw_in_celsius=storage_temperature_in_celsius,
            set_temperatures=SetTemperatureConfig(
                set_temperature_space_heating_in_celsius=30.0,
                set_temperature_dhw_in_celsius=self.SET_TEMPERATURE_IN_CELSIUS,
                hysteresis_water_temperature_offset_in_celsius=self.HYSTERESIS_IN_KELVIN,
            ),
            parallel_space_heating_and_dhw_option=True,
        )
        assert mode is expected_mode

    @pytest.mark.base
    def test_without_the_parallel_option_a_running_charge_keeps_priority_over_space_heating(self) -> None:
        """Catches a charge that gives way to space heating before the tank reaches its set temperature."""
        mode = DiverterValve.determine_operating_mode(
            with_domestic_hot_water_preparation=True,
            current_controller_mode=HeatingMode.DOMESTIC_HOT_WATER,
            is_heating_season=True,
            water_temperature_input_sh_in_celsius=25.0,
            water_temperature_input_dhw_in_celsius=50.0,
            set_temperatures=SetTemperatureConfig(
                set_temperature_space_heating_in_celsius=30.0,
                set_temperature_dhw_in_celsius=self.SET_TEMPERATURE_IN_CELSIUS,
                hysteresis_water_temperature_offset_in_celsius=self.HYSTERESIS_IN_KELVIN,
            ),
            parallel_space_heating_and_dhw_option=False,
        )
        assert mode is HeatingMode.DOMESTIC_HOT_WATER
