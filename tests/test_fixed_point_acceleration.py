"""The simulator accelerates the fixed-point iteration of the outputs a component declares accelerated.

The pure rules of :mod:`hisim.fixed_point_acceleration` are tested first: the secant step, the under-relaxation of an
oscillation, the fallbacks, the deadband and the refusals. The last tests run a toy loop through the simulator: a
water node whose step mean answers a generator that holds a 15 K lift above it. The plain iteration of that loop
needs more passes than the simulator allows before it forces convergence; with the node's step mean declared
accelerated it converges within them, to the same answer.
"""

import dataclasses
import math
from pathlib import Path
from typing import Callable, ClassVar, Dict, List, Sequence, Tuple

import pytest
from dataclasses_json import dataclass_json

from hisim import hydronics
from hisim import loadtypes as lt
from hisim.component import Component, ComponentInput, ComponentOutput, SingleTimeStepValues
from hisim.config import ComponentID, ConfigBase, DisplayConfig
from hisim.fixed_point_acceleration import (
    AccelerationHistoryError,
    SecantAcceleration,
    StepAcceleration,
    accelerated_iterate,
    held_value,
)
from hisim.simulationparameters import SimulationParameters
from hisim.simulator import Simulator

pytestmark = pytest.mark.base


def plain_history(
    fixed_point_map: Callable[[float], float], start: float, count: int
) -> Tuple[List[float], List[float]]:
    """Return ``count`` plain iterates of ``fixed_point_map`` from ``start``: the published and computed values."""
    published, computed = [], []
    value = start
    for _ in range(count):
        published.append(value)
        value = fixed_point_map(value)
        computed.append(value)
    return published, computed


def test_the_acceleration_waits_six_iterations() -> None:
    """An extrapolation from a short history is noisy; the first six passes must publish the plain iterate."""
    for count in range(1, SecantAcceleration.AFTER_ITERATIONS + 1):
        published, computed = plain_history(lambda x: 0.5 * x + 20.0, 10.0, count)
        assert accelerated_iterate(published_values=published, computed_values=computed) == computed[-1]


@pytest.mark.parametrize("theta", [0.05, 0.57, 0.89])
def test_the_secant_lands_on_the_fixed_point_of_a_contraction(theta: float) -> None:
    """For a linear contraction ``F(x) = theta x + b`` the secant step is the fixed point; a wrong formula misses."""
    fixed_point = 55.0

    def contraction(x: float) -> float:
        """Return the contraction's image of ``x``."""
        return theta * x + (1.0 - theta) * fixed_point

    published, computed = plain_history(contraction, 20.0, SecantAcceleration.AFTER_ITERATIONS + 1)
    accelerated = accelerated_iterate(published_values=published, computed_values=computed)
    assert accelerated == pytest.approx(fixed_point, abs=1e-9)
    assert abs(accelerated - fixed_point) < abs(computed[-1] - fixed_point) or computed[-1] == fixed_point


def test_an_oscillation_is_under_relaxed() -> None:
    """A residual that changes sign must be damped with weight 1/2; a secant step there would diverge."""
    fixed_point = 50.0

    def oscillating(x: float) -> float:
        """Return the image of an oscillating map with its fixed point at 50."""
        return -0.9 * x + 1.9 * fixed_point

    published, computed = plain_history(oscillating, 40.0, SecantAcceleration.AFTER_ITERATIONS + 1)
    accelerated = accelerated_iterate(published_values=published, computed_values=computed)
    residual = computed[-1] - published[-1]
    assert accelerated == published[-1] + SecantAcceleration.UNDER_RELAXATION_WEIGHT * residual
    assert abs(accelerated - fixed_point) <= 0.05 * abs(published[-1] - fixed_point) + 1e-12


def test_a_non_contracting_estimate_falls_back_to_the_plain_iterate() -> None:
    """A secant step on an expanding map would jump away from the fixed point; the plain iterate is kept."""
    published, computed = plain_history(lambda x: 1.2 * x - 5.0, 30.0, SecantAcceleration.AFTER_ITERATIONS + 1)
    assert accelerated_iterate(published_values=published, computed_values=computed) == computed[-1]


@pytest.mark.parametrize("theta", [0.91, 0.95, 0.999])
def test_a_contraction_above_the_maximal_factor_falls_back_to_the_plain_iterate(theta: float) -> None:
    """A secant step is the residual over ``1 - theta``; above 0.9 it would be more than ten times the residual."""
    fixed_point = 55.0

    def contraction(x: float) -> float:
        """Return the contraction's image of ``x``."""
        return theta * x + (1.0 - theta) * fixed_point

    published, computed = plain_history(contraction, 20.0, SecantAcceleration.AFTER_ITERATIONS + 1)
    assert accelerated_iterate(published_values=published, computed_values=computed) == computed[-1]


def test_a_noisy_contraction_estimate_near_one_does_not_jump() -> None:
    """Two passes 1 mK apart whose residuals differ by 1 µK estimate theta = 0.999; the secant would jump 10 K to 65 °C."""
    published = [50.0, 51.0, 52.0, 53.0, 54.0, 55.000, 55.001]
    computed = [51.0, 52.0, 53.0, 54.0, 55.0, 55.0100, 55.010999]
    accelerated = accelerated_iterate(published_values=published, computed_values=computed)
    assert accelerated == 55.010999
    assert abs(accelerated - published[-1]) <= 10.0 * abs(computed[-1] - published[-1])


def test_coincident_iterates_fall_back_to_the_plain_iterate() -> None:
    """Two equal published values give the secant no slope; dividing by it would fail or return nonsense."""
    published = [30.0, 40.0, 45.0, 47.0, 48.0, 49.0, 49.5, 49.5]
    computed = [40.0, 45.0, 47.0, 48.0, 49.0, 49.5, 49.7, 49.8]
    assert accelerated_iterate(published_values=published, computed_values=computed) == 49.8


def test_the_acceleration_never_moves_a_converged_fixed_point() -> None:
    """A zero residual must return the published value, whatever came before; moving it would never converge."""
    published = [30.0, 40.0, 45.0, 47.0, 48.0, 48.5, 49.0, 50.0]
    computed = [40.0, 45.0, 47.0, 48.0, 48.5, 49.0, 50.0, 50.0]
    assert accelerated_iterate(published_values=published, computed_values=computed) == 50.0


def test_the_acceleration_reads_only_the_last_two_pairs() -> None:
    """Only the last two pairs are read and validated; checking the whole history would cost a pass's time."""
    published, computed = plain_history(lambda x: 0.5 * x + 20.0, 10.0, SecantAcceleration.AFTER_ITERATIONS + 1)
    expected = accelerated_iterate(published_values=published, computed_values=computed)
    published[0] = computed[0] = math.nan
    assert accelerated_iterate(published_values=published, computed_values=computed) == expected


@pytest.mark.parametrize(
    ("published", "computed"),
    [([], []), ([1.0, 2.0], [2.0]), ([1.0, math.nan], [2.0, 3.0]), ([1.0, 2.0], [2.0, math.inf])],
)
def test_an_unusable_history_is_refused(published: Sequence[float], computed: Sequence[float]) -> None:
    """An empty, unpaired or non-finite history would extrapolate nonsense into the step values."""
    with pytest.raises(AccelerationHistoryError):
        accelerated_iterate(published_values=published, computed_values=computed)


def test_a_residual_that_overflows_is_refused() -> None:
    """Finite iterates whose residual overflows must be refused, naming the residual, not returned as inf."""
    published = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, -1e308, 1e308]
    computed = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 1e308, 1e308]
    with pytest.raises(AccelerationHistoryError, match="previous residual"):
        accelerated_iterate(published_values=published, computed_values=computed)


@pytest.mark.parametrize(
    ("candidate_value", "expected"),
    [(55.0 + 0.5e-9, 55.0), (55.0 - 1e-9, 55.0), (55.0 + 2e-9, 55.0 + 2e-9), (55.001, 55.001)],
)
def test_a_value_within_the_deadband_keeps_the_published_value(candidate_value: float, expected: float) -> None:
    """Without the deadband an iteration alternating between two neighbouring floats would never settle."""
    assert held_value(candidate_value=candidate_value, published_value=55.0) == expected


class FakeStepValues:

    """Step values of a fixed length, as the simulator hands them to a component."""

    def __init__(self, values: List[float]) -> None:
        """Hold these values."""
        self.values = values


def test_a_step_acceleration_rewrites_only_the_accelerated_values_it_is_given() -> None:
    """A step acceleration that rewrote other outputs, or forgot its history, would corrupt the iteration."""
    step_acceleration = StepAcceleration()
    stsv = FakeStepValues([0.0, 10.0])
    published = list(step_acceleration.published_values_of([1], stsv))  # type: ignore[arg-type]  # a stand-in
    stsv.values[0], stsv.values[1] = 7.0, 30.0
    step_acceleration.accelerate([1], stsv, published)  # type: ignore[arg-type]  # a stand-in for the step values
    assert stsv.values == [7.0, 30.0]
    assert step_acceleration.published_by_index == {1: [10.0]}
    assert step_acceleration.computed_by_index == {1: [30.0]}


# --- the toy loop through the simulator ------------------------------------------------------------------------


@dataclass_json
@dataclasses.dataclass
class LoopStubConfig(ConfigBase):
    """Configuration of the two stub components of the toy loop: the identity, and whether the node accelerates."""

    @classmethod
    def get_main_classname(cls) -> str:
        """Return the full class name of the node stub; the config belongs to a test double."""
        return LoopNode.get_full_classname()

    component_id: ComponentID
    accelerated: bool = False


class LoopNode(Component):
    """A 135 kg water node charged at 0.3 kg/s and drained by a 0.1 kg/s return at 30 °C, which publishes its step mean.

    It counts its passes per time step, so a test can read how many passes the simulator needed.
    """

    MODELS_NO_DEVICE: ClassVar[bool] = True

    #: The input of the charging circuit's supply temperature, in °C.
    SupplyTemperatureInCelsius: ClassVar[str] = "SupplyTemperatureInCelsius"
    #: The output of the node's step mean, the charging circuit's return, in °C.
    StepMeanTemperatureInCelsius: ClassVar[str] = "StepMeanTemperatureInCelsius"

    def __init__(self, my_simulation_parameters: SimulationParameters, config: LoopStubConfig) -> None:
        """Build the node, declare its ports and start at 45 °C."""
        self.config: LoopStubConfig = config
        super().__init__(
            name=config.component_id.key,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=DisplayConfig(),
        )
        self.supply_input: ComponentInput = self.add_input(
            self.component_name, self.SupplyTemperatureInCelsius, lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS, True
        )
        self.step_mean_output: ComponentOutput = self.add_output(
            self.component_name,
            self.StepMeanTemperatureInCelsius,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            output_description="The node's step mean.",
            is_accelerated=config.accelerated,
        )
        self.start_temperature_in_celsius = 45.0
        self.saved_start_temperature_in_celsius = 45.0
        #: The passes the simulator ran on each time step.
        self.passes_by_step: Dict[int, int] = {}

    def i_save_state(self) -> None:
        """Save the start temperature of the step."""
        self.saved_start_temperature_in_celsius = self.start_temperature_in_celsius

    def i_restore_state(self) -> None:
        """Restore the start temperature of the step."""
        self.start_temperature_in_celsius = self.saved_start_temperature_in_celsius

    def i_prepare_simulation(self) -> None:
        """Prepare nothing."""

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Check nothing."""

    def write_to_report(self) -> List[str]:
        """Return no report lines."""
        return []

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Step the node with the supply it reads, publish its step mean and count the pass."""
        self.passes_by_step[timestep] = self.passes_by_step.get(timestep, 0) + 1
        step = hydronics.MixedNode.step(
            t0_c=self.start_temperature_in_celsius,
            inflows=[hydronics.Inflow(0.3, stsv.get_input_value(self.supply_input)), hydronics.Inflow(0.1, 30.0)],
            ua_w_per_k=1.5,
            t_amb_c=20.0,
            dt_s=self.my_simulation_parameters.seconds_per_timestep,
            heat_capacity_j_per_k=135.0 * hydronics.Water.SPECIFIC_HEAT_J_PER_KG_K,
        )
        stsv.set_output_value(self.step_mean_output, step.t_mean_c)
        self.start_temperature_in_celsius = step.t_end_c


class LoopGenerator(Component):
    """A generator that holds a 15 K lift above the return it reads, the case a node's iteration converges slowest in."""

    MODELS_NO_DEVICE: ClassVar[bool] = True

    #: The input of the return temperature, the node's step mean, in °C.
    ReturnTemperatureInCelsius: ClassVar[str] = "ReturnTemperatureInCelsius"
    #: The output of the supply temperature, in °C.
    SupplyTemperatureInCelsius: ClassVar[str] = "SupplyTemperatureInCelsius"

    #: The lift the generator holds, in K.
    LIFT_IN_KELVIN: ClassVar[float] = 15.0

    def __init__(self, my_simulation_parameters: SimulationParameters, config: LoopStubConfig) -> None:
        """Build the generator and declare its ports."""
        self.config: LoopStubConfig = config
        super().__init__(
            name=config.component_id.key,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=DisplayConfig(),
        )
        self.return_input: ComponentInput = self.add_input(
            self.component_name, self.ReturnTemperatureInCelsius, lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS, True
        )
        self.supply_output: ComponentOutput = self.add_output(
            self.component_name,
            self.SupplyTemperatureInCelsius,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            output_description="The return plus the lift.",
        )

    def i_save_state(self) -> None:
        """Save nothing; the generator has no state."""

    def i_restore_state(self) -> None:
        """Restore nothing; the generator has no state."""

    def i_prepare_simulation(self) -> None:
        """Prepare nothing."""

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Check nothing."""

    def write_to_report(self) -> List[str]:
        """Return no report lines."""
        return []

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Publish the return plus the lift."""
        stsv.set_output_value(self.supply_output, stsv.get_input_value(self.return_input) + self.LIFT_IN_KELVIN)


def run_the_loop(tmp_path: Path, accelerated: bool) -> Tuple[LoopNode, float]:
    """Run two 900 s steps of the toy loop; return the node and its last published step mean.

    Args:
        tmp_path: Directory for the simulator's module directory and results.
        accelerated: Whether the node declares its step mean accelerated.
    """
    parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=900)
    parameters.result_directory = str(tmp_path / "results")
    simulator = Simulator(module_directory=str(tmp_path), module_filename="loop", my_simulation_parameters=parameters)
    simulator.set_simulation_parameters(parameters)
    node = LoopNode(parameters, LoopStubConfig(component_id=ComponentID(name="Node"), accelerated=accelerated))
    generator = LoopGenerator(parameters, LoopStubConfig(component_id=ComponentID(name="Generator")))
    node.connect_input(LoopNode.SupplyTemperatureInCelsius, generator.component_name, LoopGenerator.SupplyTemperatureInCelsius)
    generator.connect_input(LoopGenerator.ReturnTemperatureInCelsius, node.component_name, LoopNode.StepMeanTemperatureInCelsius)
    simulator.add_component(generator)
    simulator.add_component(node)
    stsv = SingleTimeStepValues(len(simulator.all_outputs))
    simulator.prepare_calculation()
    simulator.connect_all_components()
    stsv, _, _ = simulator.process_one_timestep(0, stsv)
    stsv, _, _ = simulator.process_one_timestep(1, stsv)
    return node, stsv.values[node.step_mean_output.global_index]


def test_an_accelerated_node_converges_within_the_limit_to_the_plain_answer(tmp_path: Path) -> None:
    """Without the acceleration the loop needs more than ten passes, which ends at the simulator's forced convergence.

    With it the same steps converge within ten passes, to the step mean the plain iteration reaches.
    """
    plain_node, plain_mean_in_celsius = run_the_loop(tmp_path / "plain", accelerated=False)
    fast_node, fast_mean_in_celsius = run_the_loop(tmp_path / "fast", accelerated=True)
    assert max(plain_node.passes_by_step.values()) > 10
    assert max(fast_node.passes_by_step.values()) <= 10
    assert fast_mean_in_celsius == pytest.approx(plain_mean_in_celsius, abs=1e-3)


def test_holding_keeps_the_published_value_within_the_deadband_and_extrapolates_nothing() -> None:
    """Once convergence is forced the controllers are frozen; an extrapolation from their switching would mislead."""
    stsv = FakeStepValues([55.0 + 0.5e-9, 61.0])
    StepAcceleration.hold([0, 1], stsv, [55.0, 60.0])  # type: ignore[arg-type]  # a stand-in for the step values
    assert stsv.values == [55.0, 61.0]
