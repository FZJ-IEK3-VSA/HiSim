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
    def closure_residual_in_joule(
        storage: simple_water_storage.SimpleDHWStorage,
        start_temperature_in_celsius: float,
        step: simple_water_storage.DhwTankStep,
    ) -> float:
        """``sum of inflow heats - loss - C (T_end - T0)`` of one tank step, in J."""
        stored_in_joule = storage.heat_capacity_in_joule_per_kelvin * (step.node.t_end_c - start_temperature_in_celsius)
        return float(sum(step.node.heat_in_j_per_inflow) - step.node.loss_j - stored_in_joule)


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
@pytest.mark.parametrize("start_temperature_in_celsius, primary, secondary, demand_in_kg_per_second", CASES)
def test_the_tank_closes_on_every_step(
    seconds_per_timestep: int,
    start_temperature_in_celsius: float,
    primary: Tuple[float, float],
    secondary: Tuple[float, float],
    demand_in_kg_per_second: float,
) -> None:
    """The circuits' heat, minus the tap's and the loss, is the heat the tank stores, to a millionth of the largest."""
    storage = Tank.build(seconds_per_timestep)
    inflows = [hydronics.Inflow(*primary), hydronics.Inflow(*secondary)]
    step = storage.solve_tank_step(start_temperature_in_celsius, inflows, demand_in_kg_per_second)
    largest_in_joule = max(
        [abs(heat_in_joule) for heat_in_joule in step.node.heat_in_j_per_inflow]
        + [
            abs(step.node.loss_j),
            storage.heat_capacity_in_joule_per_kelvin * abs(step.node.t_end_c - start_temperature_in_celsius),
            1.0,
        ]
    )
    assert abs(Tank.closure_residual_in_joule(storage, start_temperature_in_celsius, step)) <= 1e-6 * largest_in_joule


@pytest.mark.base
@pytest.mark.parametrize("seconds_per_timestep", [60, 900, 3600])
@pytest.mark.parametrize("start_temperature_in_celsius, primary, secondary, demand_in_kg_per_second", CASES)
def test_the_tank_never_leaves_the_range_of_what_it_mixes(
    seconds_per_timestep: int,
    start_temperature_in_celsius: float,
    primary: Tuple[float, float],
    secondary: Tuple[float, float],
    demand_in_kg_per_second: float,
) -> None:
    """The step mean and the end temperature lie between the coldest and the hottest temperature the tank mixes."""
    storage = Tank.build(seconds_per_timestep)
    step = storage.solve_tank_step(
        start_temperature_in_celsius,
        [hydronics.Inflow(*primary), hydronics.Inflow(*secondary)],
        demand_in_kg_per_second,
    )
    mixed_in_celsius = [
        start_temperature_in_celsius,
        storage.drain_water_temperature,
        storage.ambient_temperature_in_celsius,
    ]
    mixed_in_celsius += [
        temperature_in_celsius
        for flow_in_kg_per_second, temperature_in_celsius in (primary, secondary)
        if flow_in_kg_per_second > 0.0
    ]
    for temperature_in_celsius in (step.node.t_mean_c, step.node.t_end_c):
        assert min(mixed_in_celsius) - 1e-9 <= temperature_in_celsius <= max(mixed_in_celsius) + 1e-9


@pytest.mark.base
@pytest.mark.parametrize("seconds_per_timestep", [60, 900, 3600])
def test_the_tap_draws_exactly_the_demand_above_the_tap_temperature(seconds_per_timestep: int) -> None:
    """A tank well above 40 °C lets out less than the demand, and its heat is the demand's to the solve tolerance.

    The demand is ``m_d c (T_warm - T_cold) dt`` with ``T_warm = 40 °C`` and ``T_cold = 10 °C``; the heat drawn is
    ``m_hot c (T̄ - T_cold) dt``.
    """
    storage = Tank.build(seconds_per_timestep)
    demand_in_kg_per_second = 0.05
    step = storage.solve_tank_step(
        60.0, [hydronics.Inflow(0.0, 60.0), hydronics.Inflow(0.0, 60.0)], demand_in_kg_per_second
    )
    span_in_kelvin = storage.warm_water_temperature - storage.drain_water_temperature
    demand_heat_in_joule = (
        demand_in_kg_per_second * hydronics.WATER_SPECIFIC_HEAT_J_PER_KG_K * span_in_kelvin * seconds_per_timestep
    )
    drawn_in_joule = -step.node.heat_in_j_per_inflow[2]
    assert step.node.t_mean_c > storage.warm_water_temperature
    assert step.hot_water_mass_flow_in_kg_per_second < demand_in_kg_per_second
    assert drawn_in_joule == pytest.approx(demand_heat_in_joule, rel=1e-9)
    assert step.unmet_fraction == 0.0


@pytest.mark.base
def test_the_tap_passes_the_demand_unmixed_below_the_tap_temperature_and_books_the_shortfall() -> None:
    """A lukewarm tank lets out all of the demand; the heat it gives plus the unmet heat is the demand."""
    storage = Tank.build(900)
    demand_in_kg_per_second = 0.15
    step = storage.solve_tank_step(
        35.0, [hydronics.Inflow(0.0, 35.0), hydronics.Inflow(0.0, 35.0)], demand_in_kg_per_second
    )
    span_in_kelvin = storage.warm_water_temperature - storage.drain_water_temperature
    demand_heat_in_joule = demand_in_kg_per_second * hydronics.WATER_SPECIFIC_HEAT_J_PER_KG_K * span_in_kelvin * 900
    drawn_in_joule = -step.node.heat_in_j_per_inflow[2]
    assert step.hot_water_mass_flow_in_kg_per_second == demand_in_kg_per_second
    assert 0.0 < step.unmet_fraction < 1.0
    assert drawn_in_joule + step.unmet_fraction * demand_heat_in_joule == pytest.approx(demand_heat_in_joule, rel=1e-9)


@pytest.mark.base
def test_a_tank_at_the_mains_temperature_gives_nothing() -> None:
    """At 10 °C the valve lets nothing out and the whole demand is unmet."""
    storage = Tank.build(900)
    storage.ambient_temperature_in_celsius = 10.0
    step = storage.solve_tank_step(10.0, [hydronics.Inflow(0.0, 10.0), hydronics.Inflow(0.0, 10.0)], 0.1)
    assert step.hot_water_mass_flow_in_kg_per_second == 0.0
    assert step.unmet_fraction == 1.0
    assert step.node.heat_in_j_per_inflow[2] == 0.0


@pytest.mark.base
def test_one_hour_without_a_draw_equals_four_quarter_hours() -> None:
    """With constant inflows the node step is exact, so one 3600 s step and four 900 s steps end alike."""
    inflows = [hydronics.Inflow(0.1, 65.0), hydronics.Inflow(0.0, 40.0)]
    hour = Tank.build(3600).solve_tank_step(40.0, inflows, 0.0)
    quarter = Tank.build(900)
    temperature_in_celsius = 40.0
    for _ in range(4):
        temperature_in_celsius = quarter.solve_tank_step(temperature_in_celsius, inflows, 0.0).node.t_end_c
    assert temperature_in_celsius == pytest.approx(hour.node.t_end_c, abs=1e-9)


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
    assert Tank.output(stsv, storage.water_temperature_at_end_of_step_in_celsius_channel) == step.node.t_end_c
    assert storage.state.mean_water_temperature_in_celsius == step.node.t_end_c
    # the circuit's heat as both ends derive it from the three published values
    heat_in_watt_hour = Tank.output(stsv, storage.thermal_energy_from_heat_generator_channel)
    assert heat_in_watt_hour * 3600.0 == pytest.approx(
        hydronics.circuit_heat_j(0.2, 70.0, step.node.t_mean_c, 900), rel=1e-12
    )


@pytest.mark.base
def test_a_step_mean_within_the_deadband_of_the_last_published_one_is_published_unchanged() -> None:
    """A computed mean within 1e-9 K of the one published last leaves the published float as it was."""
    storage, stsv, fakes = Tank.with_fake_inputs(900)
    stsv.set_output_value(fakes[1], 70.0)
    stsv.set_output_value(fakes[2], 0.2)
    storage.state.mean_water_temperature_in_celsius = 50.0
    storage.i_save_state()
    storage.i_restore_state()
    storage.i_simulate(0, stsv, False)
    storage.i_restore_state()
    storage.i_simulate(0, stsv, False)
    converged_in_celsius = Tank.output(stsv, storage.water_temperature_to_heat_generator_channel)
    storage.last_published_step_mean_in_celsius = converged_in_celsius + 5e-10
    storage.i_restore_state()
    storage.i_simulate(0, stsv, False)
    assert Tank.output(stsv, storage.water_temperature_to_heat_generator_channel) == converged_in_celsius + 5e-10
