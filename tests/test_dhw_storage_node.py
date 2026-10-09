"""The hot-water tank is a fully mixed node with a thermostatic tap valve (hydronic coupling spec §4.1-§4.3).

``SimpleDHWStorage`` integrates its temperature over the step with :meth:`hisim.hydronics.MixedNode.step`: the supply
of each charging circuit, the mains water that refills what the tap valve lets out and the standby loss to the room.
These tests step the tank on its own, with fake inputs, and check what the node promises:

* its balance closes on every step: the heat of the circuits, minus the tap's heat and the loss, is ``C (T_end - T0)``;
* the tap valve draws exactly the household's demand while the step mean ``T̄`` is above the tap temperature, passes
  all of the demand unmixed and books the shortfall as unmet below it, and draws nothing below the mains temperature;
* the step mean and the end temperature never leave the range of the temperatures the tank mixes;
* a tank without water is refused when it is built;
* the tank publishes ``T̄`` to its charging circuits and its start temperature ``T0`` to the controllers, starting
  every step's iteration from ``T0``.

Composability case 3 of the spec (§8, the tap valve) is the tap tests here; the run tests of the recorded twins are in
``tests/test_dhw_chain_twins.py``.
"""

from typing import Any, List, Tuple

import pytest

from hisim import component as cp
from hisim import hydronics
from hisim import loadtypes as lt
from hisim.components import simple_water_storage
from hisim.config import ComponentID
from hisim.simulationparameters import SimulationParameters
from tests import functions_for_testing as fft


class Tank:
    """A 250 l hot-water tank and the arithmetic the tests check it against."""

    #: The tank's water: 250 l at 0.992 kg/l.
    MASS_IN_KG: float = 248.0

    @staticmethod
    def build(seconds_per_timestep: int, volume_in_liter: float = 250.0) -> simple_water_storage.SimpleDHWStorage:
        """A tank of the given volume for a day at the given resolution."""
        parameters = SimulationParameters.one_day_only(2021, seconds_per_timestep)
        config = simple_water_storage.SimpleDHWStorageConfig(
            component_id=ComponentID(name="DHWStorage"), volume_heating_water_storage_in_liter=volume_in_liter
        )
        storage: simple_water_storage.SimpleDHWStorage = simple_water_storage.SimpleDHWStorage(
            my_simulation_parameters=parameters, config=config
        )
        return storage

    @staticmethod
    def with_fake_inputs(seconds_per_timestep: int) -> Tuple[simple_water_storage.SimpleDHWStorage, Any, List[Any]]:
        """A tank whose five inputs read fake outputs, the step values and the fakes in input order.

        The order is: water consumption (l per step), primary supply temperature, primary mass flow, secondary
        supply temperature, secondary mass flow.
        """
        storage = Tank.build(seconds_per_timestep)
        channels = [
            (storage.water_consumption_channel, lt.LoadTypes.WARM_WATER, lt.Units.LITER),
            (storage.water_temperature_heat_generator_input_channel, lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS),
            (storage.water_mass_flow_rate_heat_generator_input_channel, lt.LoadTypes.WARM_WATER, lt.Units.KG_PER_SEC),
            (
                storage.water_temperature_secondary_heat_generator_input_channel,
                lt.LoadTypes.TEMPERATURE,
                lt.Units.CELSIUS,
            ),
            (
                storage.water_mass_flow_rate_secondary_heat_generator_input_channel,
                lt.LoadTypes.WARM_WATER,
                lt.Units.KG_PER_SEC,
            ),
        ]
        fakes: List[Any] = []
        for number, (channel, load_type, unit) in enumerate(channels):
            fake = cp.ComponentOutput(
                f"Fake{number}", f"Fake{number}", load_type, unit, component_id=ComponentID(f"Fake{number}")
            )
            channel.source_output = fake
            fakes.append(fake)
        stsv = cp.SingleTimeStepValues(fft.get_number_of_outputs([*fakes, storage]))
        fft.add_global_index_of_components([*fakes, storage])
        return storage, stsv, fakes

    @staticmethod
    def output(stsv: Any, channel: Any) -> float:
        """The value a component set on one of its output channels in ``stsv``."""
        return float(stsv.values[channel.global_index])

    @staticmethod
    def closure_residual_j(
        storage: simple_water_storage.SimpleDHWStorage, t0_c: float, step: simple_water_storage.DhwTankStep
    ) -> float:
        """``sum of inflow heats - loss - C (T_end - T0)`` of one tank step, in J."""
        stored = storage.heat_capacity_in_joule_per_kelvin * (step.node.t_end_c - t0_c)
        return float(sum(step.node.heat_in_j_per_inflow) - step.node.loss_j - stored)


#: (start temperature, primary flow and supply, secondary flow and supply, demand kg/s): a boiler charge, a solar
#: preheat with a gas top-up, a shower from a hot tank, a shower from a lukewarm one, standby only.
CASES = [
    (50.0, (0.2, 70.0), (0.0, 50.0), 0.0),
    (45.0, (0.05, 65.0), (0.15, 72.0), 0.02),
    (60.0, (0.0, 60.0), (0.0, 60.0), 0.15),
    (35.0, (0.0, 35.0), (0.0, 35.0), 0.15),
    (55.0, (0.0, 55.0), (0.0, 55.0), 0.0),
]


@pytest.mark.base
@pytest.mark.parametrize("seconds_per_timestep", [60, 900, 3600])
@pytest.mark.parametrize("t0_c, primary, secondary, demand_kg_per_s", CASES)
def test_the_tank_closes_on_every_step(
    seconds_per_timestep: int, t0_c: float, primary: Tuple[float, float], secondary: Tuple[float, float],
    demand_kg_per_s: float,
) -> None:
    """The circuits' heat, minus the tap's and the loss, is the heat the tank stores, to a millionth of the largest."""
    storage = Tank.build(seconds_per_timestep)
    inflows = [hydronics.Inflow(*primary), hydronics.Inflow(*secondary)]
    step = storage.solve_tank_step(t0_c, inflows, demand_kg_per_s)
    largest = max(
        [abs(heat) for heat in step.node.heat_in_j_per_inflow]
        + [abs(step.node.loss_j), storage.heat_capacity_in_joule_per_kelvin * abs(step.node.t_end_c - t0_c), 1.0]
    )
    assert abs(Tank.closure_residual_j(storage, t0_c, step)) <= 1e-6 * largest


@pytest.mark.base
@pytest.mark.parametrize("seconds_per_timestep", [60, 900, 3600])
@pytest.mark.parametrize("t0_c, primary, secondary, demand_kg_per_s", CASES)
def test_the_tank_never_leaves_the_range_of_what_it_mixes(
    seconds_per_timestep: int, t0_c: float, primary: Tuple[float, float], secondary: Tuple[float, float],
    demand_kg_per_s: float,
) -> None:
    """The step mean and the end temperature lie between the coldest and the hottest temperature the tank mixes."""
    storage = Tank.build(seconds_per_timestep)
    step = storage.solve_tank_step(
        t0_c, [hydronics.Inflow(*primary), hydronics.Inflow(*secondary)], demand_kg_per_s
    )
    mixed = [t0_c, storage.drain_water_temperature, storage.ambient_temperature_in_celsius]
    mixed += [temperature for flow, temperature in (primary, secondary) if flow > 0.0]
    for temperature in (step.node.t_mean_c, step.node.t_end_c):
        assert min(mixed) - 1e-9 <= temperature <= max(mixed) + 1e-9


@pytest.mark.base
@pytest.mark.parametrize("seconds_per_timestep", [60, 900, 3600])
def test_the_tap_draws_exactly_the_demand_above_the_tap_temperature(seconds_per_timestep: int) -> None:
    """A tank well above 40 °C lets out less than the demand, and its heat is the demand's to the solve tolerance.

    The demand is ``m_d c (T_warm - T_cold) dt`` with ``T_warm = 40 °C`` and ``T_cold = 10 °C``; the heat drawn is
    ``m_hot c (T̄ - T_cold) dt``.
    """
    storage = Tank.build(seconds_per_timestep)
    demand = 0.05
    step = storage.solve_tank_step(60.0, [hydronics.Inflow(0.0, 60.0), hydronics.Inflow(0.0, 60.0)], demand)
    span = storage.warm_water_temperature - storage.drain_water_temperature
    demand_heat = demand * hydronics.WATER_SPECIFIC_HEAT_J_PER_KG_K * span * seconds_per_timestep
    drawn = -step.node.heat_in_j_per_inflow[2]
    assert step.node.t_mean_c > storage.warm_water_temperature
    assert step.hot_water_mass_flow_kg_per_s < demand
    assert drawn == pytest.approx(demand_heat, rel=1e-9)
    assert step.unmet_fraction == 0.0


@pytest.mark.base
def test_the_tap_passes_the_demand_unmixed_below_the_tap_temperature_and_books_the_shortfall() -> None:
    """A lukewarm tank lets out all of the demand; the heat it gives plus the unmet heat is the demand."""
    storage = Tank.build(900)
    demand = 0.15
    step = storage.solve_tank_step(35.0, [hydronics.Inflow(0.0, 35.0), hydronics.Inflow(0.0, 35.0)], demand)
    span = storage.warm_water_temperature - storage.drain_water_temperature
    demand_heat = demand * hydronics.WATER_SPECIFIC_HEAT_J_PER_KG_K * span * 900
    drawn = -step.node.heat_in_j_per_inflow[2]
    assert step.hot_water_mass_flow_kg_per_s == demand
    assert 0.0 < step.unmet_fraction < 1.0
    assert drawn + step.unmet_fraction * demand_heat == pytest.approx(demand_heat, rel=1e-9)


@pytest.mark.base
def test_a_tank_at_the_mains_temperature_gives_nothing() -> None:
    """At 10 °C the valve lets nothing out and the whole demand is unmet."""
    storage = Tank.build(900)
    storage.ambient_temperature_in_celsius = 10.0
    step = storage.solve_tank_step(10.0, [hydronics.Inflow(0.0, 10.0), hydronics.Inflow(0.0, 10.0)], 0.1)
    assert step.hot_water_mass_flow_kg_per_s == 0.0
    assert step.unmet_fraction == 1.0
    assert step.node.heat_in_j_per_inflow[2] == 0.0


@pytest.mark.base
def test_one_hour_without_a_draw_equals_four_quarter_hours() -> None:
    """With constant inflows the node step is exact, so one 3600 s step and four 900 s steps end alike."""
    inflows = [hydronics.Inflow(0.1, 65.0), hydronics.Inflow(0.0, 40.0)]
    hour = Tank.build(3600).solve_tank_step(40.0, inflows, 0.0)
    quarter = Tank.build(900)
    temperature = 40.0
    for _ in range(4):
        temperature = quarter.solve_tank_step(temperature, inflows, 0.0).node.t_end_c
    assert temperature == pytest.approx(hour.node.t_end_c, abs=1e-9)


@pytest.mark.base
def test_a_tank_without_water_is_refused() -> None:
    """A volume of zero is refused when the tank is built, naming the field to correct."""
    with pytest.raises(ValueError, match="volume_heating_water_storage_in_liter"):
        Tank.build(900, volume_in_liter=0.0)


@pytest.mark.base
def test_the_tank_publishes_its_step_mean_to_the_circuits_and_its_start_temperature_to_the_controllers() -> None:
    """``T̄`` goes to both charging circuits, ``T0`` to the controllers, ``T_end`` becomes the next ``T0``.

    The step's first iteration publishes ``T0`` to the circuits, every later one the step mean it computed.
    """
    storage, stsv, fakes = Tank.with_fake_inputs(900)
    stsv.set_output_value(fakes[1], 70.0)
    stsv.set_output_value(fakes[2], 0.2)
    storage.state.mean_water_temperature_in_celsius = 50.0
    storage.i_save_state()

    storage.i_restore_state()
    storage.i_simulate(0, stsv, False)
    assert Tank.output(stsv, storage.water_temperature_to_heat_generator_channel) == 50.0
    storage.i_restore_state()
    storage.i_simulate(0, stsv, False)

    step = storage.solve_tank_step(50.0, [hydronics.Inflow(0.2, 70.0), hydronics.Inflow(0.0, 0.0)], 0.0)
    for channel in (
        storage.water_temperature_to_heat_generator_channel,
        storage.water_temperature_secondary_heat_generator_output_channel,
    ):
        assert Tank.output(stsv, channel) == step.node.t_mean_c
    assert Tank.output(stsv, storage.water_temperature_mean_channel) == 50.0
    assert Tank.output(stsv, storage.water_temperature_at_end_of_step_channel) == step.node.t_end_c
    assert storage.state.mean_water_temperature_in_celsius == step.node.t_end_c
    # the circuit's heat as both ends derive it from the three published values
    heat_wh = Tank.output(stsv, storage.thermal_energy_from_heat_generator_channel)
    assert heat_wh * 3600.0 == pytest.approx(hydronics.circuit_heat_j(0.2, 70.0, step.node.t_mean_c, 900), rel=1e-12)


@pytest.mark.base
@pytest.mark.parametrize(
    "published, computed, cycles",
    [
        ([70.549, 70.551, 70.550], [70.552, 70.549, 70.549], True),  # +0.003, -0.002, -0.001 K: a cycle at a cell edge
        ([60.0, 60.2, 60.3], [60.2, 60.3, 60.35], False),  # one sign: a contraction
        ([60.0, 60.3, 60.1], [60.3, 60.1, 60.2], False),  # sign changes, but far from its point
        ([70.0, 70.1], [70.1, 70.0], False),  # too few iterations to tell
    ],
)
def test_the_tank_recognises_a_cycle(published: List[float], computed: List[float], cycles: bool) -> None:
    """A cycle changes the residual's sign at the size of a grid cell's jump; a contraction keeps one sign."""
    storage = Tank.build(900)
    storage.published_step_means_in_celsius = list(published)
    storage.computed_step_means_in_celsius = list(computed)
    assert storage.iteration_cycles() is cycles


@pytest.mark.base
def test_under_force_convergence_a_cycling_tank_holds_its_step_mean_for_the_rest_of_the_step() -> None:
    """With a cycling history and forced convergence the tank publishes what it published last, then keeps it."""
    storage, stsv, fakes = Tank.with_fake_inputs(900)
    stsv.set_output_value(fakes[1], 75.0)
    stsv.set_output_value(fakes[2], 0.25)
    storage.state.mean_water_temperature_in_celsius = 68.0
    storage.i_save_state()
    # the step mean of this charge is about 70.385 °C; the history hovers around it
    storage.published_step_means_in_celsius = [70.384, 70.386, 70.385]
    storage.computed_step_means_in_celsius = [70.387, 70.384, 70.384]
    storage.last_published_step_mean_in_celsius = 70.3851
    for _ in range(2):
        storage.i_restore_state()
        storage.i_simulate(0, stsv, True)
        assert Tank.output(stsv, storage.water_temperature_to_heat_generator_channel) == 70.3851
    assert storage.holding_step_mean
    storage.i_save_state()
    assert not storage.holding_step_mean
