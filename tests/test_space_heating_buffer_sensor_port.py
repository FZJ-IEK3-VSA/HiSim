"""The space-heating buffer publishes its start-of-step temperature on a port of its own, and the controllers read it.

``SimpleHotWaterStorage.WaterTemperatureAtStartOfStepInCelsius`` is the sensor the generators' controllers (boiler,
heat pump, CHP) decide on: the buffer's temperature at the start of the step, which no pass of the step moves, so an
on/off decision cannot cycle with the temperatures the buffer publishes to its circuits. Owner decision Q1 of
``roadmap/two_mode_buffer_spec.md`` (§4.1, §8 item T1): a new output, ``T0`` in both buffer modes.

These tests check:

* with its heat exchanger the buffer publishes ``T0`` on the sensor port while its end temperature moves, at 60 s and
  at 900 s, and at 900 s the sensor equals the return it publishes to its generator, as before the port existed;
* without its heat exchanger (the stratification path) the sensor carries the end temperature, the convention of
  that path for the stage D outputs;
* the boiler's, the heat pump's and the CHP's controllers take their buffer temperature from the sensor port.
"""

import dataclasses
from typing import Dict

import pytest

from hisim.component import ComponentConnection
from hisim.components.generic_boiler import GenericBoilerController
from hisim.components.generic_chp.controller import L1CHPController
from hisim.components.more_advanced_heat_pump_hplib import MoreAdvancedHeatPumpHPLibControllerSpaceHeating
from hisim.components.simple_water_storage import SimpleHotWaterStorage, SimpleHotWaterStorageConfig
from tests.part_load_rigs import AllInputsRig, Parameters


class SensorRig:
    """A 200 l buffer stepped once on fake inputs: a generator charging it while the distribution draws."""

    #: The buffer volume, l.
    VOLUME_IN_LITER: float = 200.0

    #: The inputs of the step: the generator brings 0.3 kg/s at 45 °C, the distribution returns 0.5 kg/s at 31 °C.
    INPUTS: Dict[str, float] = {
        SimpleHotWaterStorage.WaterMassFlowRateFromHeatGenerator: 0.3,
        SimpleHotWaterStorage.WaterTemperatureFromHeatGenerator: 45.0,
        SimpleHotWaterStorage.WaterMassFlowRateFromHeatDistributionSystem: 0.5,
        SimpleHotWaterStorage.WaterTemperatureFromHeatDistribution: 31.0,
        SimpleHotWaterStorage.State: 1.0,
    }

    @classmethod
    def step(cls, seconds_per_timestep: int, *, heat_exchanger_is_present: bool = True) -> Dict[str, float]:
        """Build the buffer at a step length and return its outputs after one pass of the first step.

        Args:
            seconds_per_timestep: The step length, s.
            heat_exchanger_is_present: Whether the buffer has its heat exchanger.

        Returns:
            Every output of the buffer by field name.
        """
        config = dataclasses.replace(
            SimpleHotWaterStorageConfig.preset_buffer("Buffer"),
            volume_heating_water_storage_in_liter=cls.VOLUME_IN_LITER,
            heat_exchanger_is_present=heat_exchanger_is_present,
        )
        buffer = SimpleHotWaterStorage(Parameters.one_day(seconds_per_timestep), config)
        return AllInputsRig.step_once(buffer, cls.INPUTS)


@pytest.mark.base
@pytest.mark.parametrize("seconds_per_timestep", [60, 900])
def test_the_sensor_port_carries_the_start_temperature_while_the_end_temperature_moves(
    seconds_per_timestep: int,
) -> None:
    """The sensor is the buffer's 35 °C start temperature, whatever the step brings.

    Fails if the sensor followed the end temperature of a buffer with its heat exchanger, which the charge in the
    step lifts above 35 °C, or if it differed from the start temperature published as ``WaterMeanTemperatureInStorage``.
    """
    outputs = SensorRig.step(seconds_per_timestep)
    start_in_celsius = SimpleHotWaterStorage.INITIAL_TEMPERATURE_IN_CELSIUS
    assert outputs[SimpleHotWaterStorage.WaterTemperatureAtStartOfStepInCelsius] == start_in_celsius
    assert outputs[SimpleHotWaterStorage.WaterMeanTemperatureInStorage] == start_in_celsius
    assert outputs[SimpleHotWaterStorage.WaterTemperatureAtEndOfStepInCelsius] > start_in_celsius + 0.1


@pytest.mark.base
def test_above_the_threshold_the_sensor_equals_the_return_to_the_generator() -> None:
    """At 900 s the start-of-step buffer publishes ``T0`` on its circuit ports, so moving the sensor changes nothing.

    Fails if the start-of-step buffer published a different value on the sensor than on the return its generator
    reads, which would move the results of every recorded twin.
    """
    outputs = SensorRig.step(900)
    assert (
        outputs[SimpleHotWaterStorage.WaterTemperatureAtStartOfStepInCelsius]
        == outputs[SimpleHotWaterStorage.WaterTemperatureToHeatGenerator]
    )


@pytest.mark.base
def test_without_its_heat_exchanger_the_sensor_carries_the_end_temperature() -> None:
    """The stratification path has no start-of-step sensor of its own and publishes its end temperature on it.

    Fails if the stratification path left the sensor unset (0 °C) or published another temperature on it.
    """
    outputs = SensorRig.step(900, heat_exchanger_is_present=False)
    end_in_celsius = outputs[SimpleHotWaterStorage.WaterTemperatureAtEndOfStepInCelsius]
    assert end_in_celsius > 0.0
    assert outputs[SimpleHotWaterStorage.WaterTemperatureAtStartOfStepInCelsius] == end_in_celsius


@pytest.mark.base
@pytest.mark.parametrize(
    "controller_class, method_name, sensor_input",
    [
        (
            GenericBoilerController,
            "get_default_connections_from_simple_hot_water_storage",
            GenericBoilerController.WaterTemperatureInputFromWaterStorage,
        ),
        (
            MoreAdvancedHeatPumpHPLibControllerSpaceHeating,
            "get_default_connections_from_simple_hot_water_storage",
            MoreAdvancedHeatPumpHPLibControllerSpaceHeating.WaterTemperatureInput,
        ),
        (
            L1CHPController,
            "get_default_connections_simple_water_storage",
            L1CHPController.HotWaterStorageTemperature,
        ),
    ],
)
def test_the_generator_controllers_read_the_sensor_port(
    controller_class: type, method_name: str, sensor_input: str
) -> None:
    """Each controller's default connection from the buffer feeds its temperature input from the sensor port.

    The connection lists depend on no configuration, so they are read from an unconstructed controller.

    Fails if a controller still read ``WaterTemperatureToHeatGenerator``, which the iterated buffer of T2 moves
    within the step.
    """
    connections = getattr(object.__new__(controller_class), method_name)()
    sources: Dict[str, ComponentConnection] = {connection.target_input_name: connection for connection in connections}
    assert sources[sensor_input].source_class_name == SimpleHotWaterStorage.get_classname()
    assert sources[sensor_input].source_output_name == SimpleHotWaterStorage.WaterTemperatureAtStartOfStepInCelsius
