"""Up to the part-load threshold the space-heating buffer is an iterated mixed node, above it a start-of-step node.

The two-mode buffer of ``roadmap/two_mode_buffer_spec.md`` (§3.1, §3.3, §4, §7.1; owner decisions Q2 and Q6) chooses its
model once, when it is built, from ``SimulationParameters.runs_part_load()``. These tests step one buffer on fake
inputs, pass by pass as the simulator does (restore the state, simulate), with the owner's example system of §3: a
200 kg buffer at 35 °C, a heat pump bringing 0.3 kg/s at 10 K above the temperature it reads, a floor heating returning
0.5 kg/s at 4 K below the temperature it reads, and 2 W/K of standby loss to a 20 °C room. They check:

* the pass-by-pass example: at 60 s five passes reach 1e-4 K and the error shrinks by 0.111 per pass; at 600 s twenty
  passes and 0.621 per pass; at the fixed point of the 60 s step the heat pump brings 752.40 kJ, the floor heating
  takes 501.60 kJ and the buffer ends at 35.29782 °C;
* the balance closes on every pass, not only at the fixed point;
* the switch at the threshold: at 600 s the circuit ports carry the step mean and the sensor the start temperature, at
  720 s every port carries the start temperature, and a 600 s buffer with a 300 s threshold runs the start-of-step
  node;
* with the iterated buffer the three end temperatures without a circuit carry the end temperature (decision Q2).
"""

import dataclasses
from typing import ClassVar, Dict, List, Tuple

import pytest

from hisim import hydronics
from hisim.components.simple_water_storage import SimpleHotWaterStorage, SimpleHotWaterStorageConfig
from tests.part_load_rigs import AllInputsRig, Parameters


class ExampleSystem:
    """The owner's example system of §3 around one buffer, iterated pass by pass on fake inputs."""

    #: The buffer's water mass, kg.
    MASS_IN_KG: ClassVar[float] = 200.0
    #: The buffer's standby loss coefficient, W/K.
    HEAT_LOSS_COEFFICIENT_IN_WATT_PER_KELVIN: ClassVar[float] = 2.0
    #: The heat pump's flow, kg/s, and the lift it holds above the temperature it reads, K.
    GENERATOR_MASS_FLOW_IN_KG_PER_SECOND: ClassVar[float] = 0.3
    GENERATOR_LIFT_IN_KELVIN: ClassVar[float] = 10.0
    #: The floor heating's flow, kg/s, and the drop it holds below the temperature it reads, K.
    DISTRIBUTION_MASS_FLOW_IN_KG_PER_SECOND: ClassVar[float] = 0.5
    DISTRIBUTION_DROP_IN_KELVIN: ClassVar[float] = 4.0
    #: The simulator's convergence tolerance, K.
    TOLERANCE_IN_KELVIN: ClassVar[float] = 1e-4
    #: Joules per watt hour.
    JOULES_PER_WATT_HOUR: ClassVar[float] = 3600.0

    def __init__(self, seconds_per_timestep: int, **extra: float) -> None:
        """Build the buffer at a step length, sized and insulated to the example's 200 kg and 2 W/K.

        Args:
            seconds_per_timestep: The step length, s.
            **extra: Further arguments of the simulation parameters, such as ``part_load_above_seconds``.
        """
        volume_in_liter = self.MASS_IN_KG / hydronics.Water.DENSITY_KG_PER_LITER
        probe = SimpleHotWaterStorage(
            Parameters.one_day(seconds_per_timestep, **extra),
            dataclasses.replace(
                SimpleHotWaterStorageConfig.preset_buffer("Buffer"),
                volume_heating_water_storage_in_liter=volume_in_liter,
            ),
        )
        config = dataclasses.replace(
            SimpleHotWaterStorageConfig.preset_buffer("Buffer"),
            volume_heating_water_storage_in_liter=volume_in_liter,
            heat_transfer_coefficient_in_watt_per_m2_per_kelvin=self.HEAT_LOSS_COEFFICIENT_IN_WATT_PER_KELVIN
            / probe.storage_surface_in_m2,
        )
        self.seconds_per_timestep = seconds_per_timestep
        self.buffer = SimpleHotWaterStorage(Parameters.one_day(seconds_per_timestep, **extra), config)
        self.stsv, self.fakes = AllInputsRig.build(self.buffer)
        self.buffer.i_save_state()

    def one_pass(self, read_in_celsius: float) -> Dict[str, float]:
        """Run one pass of the step, both circuits holding their difference to the temperature they read.

        Args:
            read_in_celsius: The temperature the heat pump reads as its return and the floor heating as its supply.

        Returns:
            Every output of the buffer by field name.
        """
        self.buffer.i_restore_state()
        return AllInputsRig.step(
            self.buffer,
            self.stsv,
            self.fakes,
            {
                SimpleHotWaterStorage.WaterMassFlowRateFromHeatGenerator: self.GENERATOR_MASS_FLOW_IN_KG_PER_SECOND,
                SimpleHotWaterStorage.WaterTemperatureFromHeatGenerator: read_in_celsius
                + self.GENERATOR_LIFT_IN_KELVIN,
                SimpleHotWaterStorage.WaterMassFlowRateFromHeatDistributionSystem: (
                    self.DISTRIBUTION_MASS_FLOW_IN_KG_PER_SECOND
                ),
                SimpleHotWaterStorage.WaterTemperatureFromHeatDistribution: read_in_celsius
                - self.DISTRIBUTION_DROP_IN_KELVIN,
            },
        )

    def iterate(self) -> Tuple[List[float], Dict[str, float]]:
        """Iterate the step from the guess ``T0`` until the published temperature moves by at most 1e-4 K.

        Returns:
            The temperature published on the generator's return after each pass, and the outputs of the last pass.
        """
        read_in_celsius = SimpleHotWaterStorage.INITIAL_TEMPERATURE_IN_CELSIUS
        published: List[float] = []
        while True:
            outputs = self.one_pass(read_in_celsius)
            published.append(outputs[SimpleHotWaterStorage.WaterTemperatureToHeatGenerator])
            if abs(published[-1] - read_in_celsius) <= self.TOLERANCE_IN_KELVIN or len(published) > 100:
                return published, outputs
            read_in_celsius = published[-1]


@pytest.mark.base
@pytest.mark.parametrize(
    "seconds_per_timestep, passes, contraction",
    [(60, 5, 0.111), (600, 20, 0.621)],
)
def test_the_iterated_buffer_converges_pass_by_pass_as_its_contraction_predicts(
    seconds_per_timestep: int, passes: int, contraction: float
) -> None:
    """The error shrinks by ``theta = 1 - (1 - exp(-a)) / a`` per pass: 0.111 at 60 s, 0.621 at 600 s.

    Fails if the buffer published something other than the node's step mean on its circuit ports, or if the node
    contracted differently from the spec's computed example (§3.1).
    """
    published, _ = ExampleSystem(seconds_per_timestep).iterate()
    assert len(published) == passes
    errors = [
        abs(value - published[-1]) for value in [SimpleHotWaterStorage.INITIAL_TEMPERATURE_IN_CELSIUS] + published
    ]
    for before, after in zip(errors[:3], errors[1:4]):
        assert after / before == pytest.approx(contraction, abs=0.01)


@pytest.mark.base
def test_at_the_fixed_point_each_circuit_books_its_difference_and_the_buffer_ends_on_the_balance() -> None:
    """At the 60 s fixed point the heat pump brings 1254 W/K x 10 K x 60 s and the floor heating takes 2090 x 4 x 60.

    Fails if the buffer booked its circuits on another temperature than the one it published, or if its end
    temperature missed the spec's 35.29782 °C.
    """
    _, outputs = ExampleSystem(60).iterate()
    joules_per_watt_hour = ExampleSystem.JOULES_PER_WATT_HOUR
    primary_in_kilojoule = outputs[SimpleHotWaterStorage.ThermalEnergyFromHeatGenerator] * joules_per_watt_hour / 1e3
    distribution_in_kilojoule = (
        outputs[SimpleHotWaterStorage.ThermalEnergyFromHeatDistribution] * joules_per_watt_hour / 1e3
    )
    assert primary_in_kilojoule == pytest.approx(752.40, abs=0.01)
    assert distribution_in_kilojoule == pytest.approx(-501.60, abs=0.01)
    assert outputs[SimpleHotWaterStorage.WaterTemperatureAtEndOfStepInCelsius] == pytest.approx(35.29782, abs=1e-4)


@pytest.mark.base
@pytest.mark.parametrize("seconds_per_timestep", [60, 600])
def test_the_iterated_buffer_closes_its_balance_on_every_pass(seconds_per_timestep: int) -> None:
    """Circuit heats less the loss equal the change of stored heat in each pass, converged or not.

    Fails if a heat, the loss or the end temperature came from different node steps or temperatures.
    """
    example = ExampleSystem(seconds_per_timestep)
    read_in_celsius = SimpleHotWaterStorage.INITIAL_TEMPERATURE_IN_CELSIUS
    hours = seconds_per_timestep / ExampleSystem.JOULES_PER_WATT_HOUR
    for _ in range(4):
        outputs = example.one_pass(read_in_celsius)
        heats_in_watt_hour = (
            outputs[SimpleHotWaterStorage.ThermalEnergyFromHeatGenerator]
            + outputs[SimpleHotWaterStorage.ThermalEnergyFromSecondaryHeatGenerator]
            + outputs[SimpleHotWaterStorage.ThermalEnergyFromHeatDistribution]
        )
        loss_in_watt_hour = outputs[SimpleHotWaterStorage.StandbyHeatLoss] * hours
        stored_in_watt_hour = outputs[SimpleHotWaterStorage.ThermalEnergyIncreaseInStorage]
        assert heats_in_watt_hour - loss_in_watt_hour == pytest.approx(stored_in_watt_hour, rel=1e-9, abs=1e-9)
        read_in_celsius = outputs[SimpleHotWaterStorage.WaterTemperatureToHeatGenerator]


@pytest.mark.base
def test_at_the_threshold_the_circuit_ports_carry_the_step_mean_and_the_sensor_the_start() -> None:
    """A 600 s buffer (the default threshold) publishes the step mean to its circuits and ``T0`` to its controllers.

    Fails if the buffer at the threshold ran the start-of-step node, or published the step mean on the sensor.
    """
    outputs = ExampleSystem(600).one_pass(SimpleHotWaterStorage.INITIAL_TEMPERATURE_IN_CELSIUS)
    start_in_celsius = SimpleHotWaterStorage.INITIAL_TEMPERATURE_IN_CELSIUS
    mean_in_celsius = outputs[SimpleHotWaterStorage.WaterTemperatureToHeatGenerator]
    assert mean_in_celsius == pytest.approx(35.77060, abs=1e-5)
    assert outputs[SimpleHotWaterStorage.WaterTemperatureToSecondaryHeatGenerator] == mean_in_celsius
    assert outputs[SimpleHotWaterStorage.WaterTemperatureToHeatDistribution] == mean_in_celsius
    assert outputs[SimpleHotWaterStorage.WaterTemperatureAtStartOfStepInCelsius] == start_in_celsius
    assert outputs[SimpleHotWaterStorage.WaterMeanTemperatureInStorage] == start_in_celsius


@pytest.mark.base
@pytest.mark.parametrize(
    "seconds_per_timestep, extra",
    [(720, {}), (600, {"part_load_above_seconds": 300.0})],
)
def test_above_the_threshold_every_port_carries_the_start_temperature(
    seconds_per_timestep: int, extra: Dict[str, float]
) -> None:
    """Above the threshold, 720 s with the default or 600 s with a 300 s threshold, the start-of-step node runs.

    Fails if the buffer chose its model from the step length alone instead of from ``runs_part_load()``.
    """
    outputs = ExampleSystem(seconds_per_timestep, **extra).one_pass(
        SimpleHotWaterStorage.INITIAL_TEMPERATURE_IN_CELSIUS
    )
    start_in_celsius = SimpleHotWaterStorage.INITIAL_TEMPERATURE_IN_CELSIUS
    for field in (
        SimpleHotWaterStorage.WaterTemperatureToHeatGenerator,
        SimpleHotWaterStorage.WaterTemperatureToSecondaryHeatGenerator,
        SimpleHotWaterStorage.WaterTemperatureToHeatDistribution,
        SimpleHotWaterStorage.WaterTemperatureAtStartOfStepInCelsius,
    ):
        assert outputs[field] == start_in_celsius, field
    end_in_celsius = outputs[SimpleHotWaterStorage.WaterTemperatureAtEndOfStepInCelsius]
    assert outputs[SimpleHotWaterStorage.WaterTemperatureAtEndOfStepWithoutHeatGeneratorInCelsius] < end_in_celsius


@pytest.mark.base
def test_with_the_iterated_buffer_the_end_temperatures_without_a_circuit_carry_the_end_temperature() -> None:
    """Nothing reads them at or below the threshold, so they carry ``T_end`` instead of three more node steps (Q2).

    Fails if the iterated buffer left them unset or published another value on them.
    """
    outputs = ExampleSystem(60).one_pass(SimpleHotWaterStorage.INITIAL_TEMPERATURE_IN_CELSIUS)
    end_in_celsius = outputs[SimpleHotWaterStorage.WaterTemperatureAtEndOfStepInCelsius]
    for field in (
        SimpleHotWaterStorage.WaterTemperatureAtEndOfStepWithoutHeatGeneratorInCelsius,
        SimpleHotWaterStorage.WaterTemperatureAtEndOfStepWithoutSecondaryHeatGeneratorInCelsius,
        SimpleHotWaterStorage.WaterTemperatureAtEndOfStepWithoutHeatDistributionInCelsius,
    ):
        assert outputs[field] == end_in_celsius, field
