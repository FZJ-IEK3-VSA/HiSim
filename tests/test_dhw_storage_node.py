"""The hot-water tank is a fully mixed node with a thermostatic tap valve.

``SimpleDHWStorage`` integrates its temperature over the step with :meth:`hisim.hydronics.MixedNode.step`: the supply
of each charging circuit, the mains water that refills what the tap valve lets out and the standby loss to the room.
These tests step the tank on its own, with fake inputs, and check what the node promises:

* its balance closes on every step: the heat of the circuits, minus the tap's heat and the loss, is ``C (T_end - T0)``;
* the tap valve draws exactly the household's demand while the step mean ``T̄`` is above the tap temperature, passes
  all of the demand unmixed and books the shortfall as unmet below it, and draws nothing below the mains temperature;
* the step mean and the end temperature never leave the range of the temperatures the tank mixes;
* a tank without water is refused when it is built;
* the tank publishes ``T̄`` to its charging circuits and its start temperature ``T0`` to the controllers, from its
  inputs and its saved state only, so every pass of a step with the same inputs publishes the same values.

The run tests of the recorded twins are in ``tests/test_dhw_chain_twins.py``.
"""

import dataclasses
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

    #: (start temperature in °C, primary flow in kg/s and supply in °C, secondary flow and supply, demand in kg/s):
    #: a boiler charge, a solar preheat with a gas top-up, a shower from a hot tank, a shower from a lukewarm one,
    #: standby only.
    CASES = [
        (50.0, (0.2, 70.0), (0.0, 50.0), 0.0),
        (45.0, (0.05, 65.0), (0.15, 72.0), 0.02),
        (60.0, (0.0, 60.0), (0.0, 60.0), 0.15),
        (35.0, (0.0, 35.0), (0.0, 35.0), 0.15),
        (55.0, (0.0, 55.0), (0.0, 55.0), 0.0),
    ]

    @staticmethod
    def build(seconds_per_timestep: int, volume_in_liter: float = 250.0) -> simple_water_storage.SimpleDHWStorage:
        """Return a tank of the given volume for a day at the given resolution."""
        parameters = SimulationParameters.one_day_only(2021, seconds_per_timestep)
        config = simple_water_storage.SimpleDHWStorageConfig(
            component_id=ComponentID(name="DHWStorage"), volume_heating_water_storage_in_liter=volume_in_liter
        )
        storage: simple_water_storage.SimpleDHWStorage = simple_water_storage.SimpleDHWStorage(
            my_simulation_parameters=parameters, config=config
        )
        return storage

    @staticmethod
    def step(
        storage: simple_water_storage.SimpleDHWStorage,
        start_temperature_in_celsius: float,
        inflows: List[hydronics.Inflow],
        demand_in_kg_per_second: float,
    ) -> simple_water_storage.DhwTankStep:
        """Return the tank step of ``storage``'s constants from this start, these inflows and this demand."""
        step: simple_water_storage.DhwTankStep = simple_water_storage.SimpleDHWStorage.solve_tank_step(
            start_temperature_in_celsius=start_temperature_in_celsius,
            generator_inflows=inflows,
            demand_in_kg_per_second=demand_in_kg_per_second,
            tank=storage.tank,
        )
        return step

    @staticmethod
    def with_fake_inputs(seconds_per_timestep: int) -> Tuple[simple_water_storage.SimpleDHWStorage, Any, List[Any]]:
        """Return a tank whose five inputs read fake outputs, the step values and the fakes in input order.

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
        """Return the value a component set on one of its output channels in ``stsv``."""
        return float(stsv.values[channel.global_index])

    @staticmethod
    def closure_residual_in_joule(
        storage: simple_water_storage.SimpleDHWStorage,
        start_temperature_in_celsius: float,
        step: simple_water_storage.DhwTankStep,
    ) -> float:
        """Return ``sum of inflow heats - loss - C (T_end - T0)`` of one tank step, in J."""
        stored_in_joule = storage.tank.heat_capacity_in_joule_per_kelvin * (
            step.node.t_end_c - start_temperature_in_celsius
        )
        return float(sum(step.node.heat_in_j_per_inflow) - step.node.loss_j - stored_in_joule)


@pytest.mark.base
@pytest.mark.parametrize("seconds_per_timestep", [60, 900, 3600])
@pytest.mark.parametrize("start_temperature_in_celsius, primary, secondary, demand_in_kg_per_second", Tank.CASES)
def test_the_tank_closes_on_every_step(
    seconds_per_timestep: int,
    start_temperature_in_celsius: float,
    primary: Tuple[float, float],
    secondary: Tuple[float, float],
    demand_in_kg_per_second: float,
) -> None:
    """A tank whose circuits, tap and loss did not add up to its stored heat would create or lose energy."""
    storage = Tank.build(seconds_per_timestep)
    inflows = [hydronics.Inflow(*primary), hydronics.Inflow(*secondary)]
    step = Tank.step(storage, start_temperature_in_celsius, inflows, demand_in_kg_per_second)
    largest_in_joule = max(
        [abs(heat_in_joule) for heat_in_joule in step.node.heat_in_j_per_inflow]
        + [
            abs(step.node.loss_j),
            storage.tank.heat_capacity_in_joule_per_kelvin * abs(step.node.t_end_c - start_temperature_in_celsius),
            1.0,
        ]
    )
    assert abs(Tank.closure_residual_in_joule(storage, start_temperature_in_celsius, step)) <= 1e-6 * largest_in_joule


@pytest.mark.base
@pytest.mark.parametrize("seconds_per_timestep", [60, 900, 3600])
@pytest.mark.parametrize("start_temperature_in_celsius, primary, secondary, demand_in_kg_per_second", Tank.CASES)
def test_the_tank_never_leaves_the_range_of_what_it_mixes(
    seconds_per_timestep: int,
    start_temperature_in_celsius: float,
    primary: Tuple[float, float],
    secondary: Tuple[float, float],
    demand_in_kg_per_second: float,
) -> None:
    """A step mean or end temperature outside the mixed range would mean the tap solve or the node overshot."""
    storage = Tank.build(seconds_per_timestep)
    inflows = [hydronics.Inflow(*primary), hydronics.Inflow(*secondary)]
    step = Tank.step(storage, start_temperature_in_celsius, inflows, demand_in_kg_per_second)
    mixed_range = hydronics.TemperatureRange.of_node(
        t0_c=start_temperature_in_celsius,
        inflows=inflows,
        fixed_temperatures_c=(
            storage.tank.mains_water_temperature_in_celsius,
            storage.tank.ambient_temperature_in_celsius,
        ),
    )
    for temperature_in_celsius in (step.node.t_mean_c, step.node.t_end_c):
        assert mixed_range.low_c - 1e-9 <= temperature_in_celsius <= mixed_range.high_c + 1e-9


@pytest.mark.base
@pytest.mark.parametrize("seconds_per_timestep", [60, 900, 3600])
def test_the_tap_draws_exactly_the_demand_above_the_tap_temperature(seconds_per_timestep: int) -> None:
    """A tap solve that stopped short of the step mean would deliver more or less heat than the household asks for.

    The demand is ``m_d c (T_warm - T_cold) dt`` with ``T_warm = 40 °C`` and ``T_cold = 10 °C``; the heat drawn is
    ``m_hot c (T̄ - T_cold) dt``.
    """
    storage = Tank.build(seconds_per_timestep)
    demand_in_kg_per_second = 0.05
    step = Tank.step(
        storage, 60.0, [hydronics.Inflow(0.0, 60.0), hydronics.Inflow(0.0, 60.0)], demand_in_kg_per_second
    )
    demand_heat_in_joule = (
        hydronics.hot_water_demand_power_w(
            demand_kg_per_s=demand_in_kg_per_second,
            t_warm_c=storage.tank.tap_temperature_in_celsius,
            t_cold_c=storage.tank.mains_water_temperature_in_celsius,
        )
        * seconds_per_timestep
    )
    drawn_in_joule = -step.node.heat_in_j_per_inflow[2]
    assert step.node.t_mean_c > storage.tank.tap_temperature_in_celsius
    assert step.hot_water_mass_flow_in_kg_per_second < demand_in_kg_per_second
    assert drawn_in_joule == pytest.approx(demand_heat_in_joule, rel=1e-9)
    assert step.unmet_fraction == 0.0


@pytest.mark.base
def test_the_tap_passes_the_demand_unmixed_below_the_tap_temperature_and_books_the_shortfall() -> None:
    """A lukewarm tank whose delivered and unmet heat did not add up to the demand would lose or invent heat."""
    storage = Tank.build(900)
    demand_in_kg_per_second = 0.15
    step = Tank.step(
        storage, 35.0, [hydronics.Inflow(0.0, 35.0), hydronics.Inflow(0.0, 35.0)], demand_in_kg_per_second
    )
    demand_heat_in_joule = (
        hydronics.hot_water_demand_power_w(
            demand_kg_per_s=demand_in_kg_per_second,
            t_warm_c=storage.tank.tap_temperature_in_celsius,
            t_cold_c=storage.tank.mains_water_temperature_in_celsius,
        )
        * 900
    )
    drawn_in_joule = -step.node.heat_in_j_per_inflow[2]
    assert step.hot_water_mass_flow_in_kg_per_second == demand_in_kg_per_second
    assert 0.0 < step.unmet_fraction < 1.0
    assert drawn_in_joule + step.unmet_fraction * demand_heat_in_joule == pytest.approx(demand_heat_in_joule, rel=1e-9)


@pytest.mark.base
def test_a_tank_at_the_mains_temperature_gives_nothing() -> None:
    """A tank at the mains temperature that let water out would book heat it does not have."""
    storage = Tank.build(900)
    tank_in_a_cold_room = dataclasses.replace(storage.tank, ambient_temperature_in_celsius=10.0)
    step = simple_water_storage.SimpleDHWStorage.solve_tank_step(
        start_temperature_in_celsius=10.0,
        generator_inflows=[hydronics.Inflow(0.0, 10.0), hydronics.Inflow(0.0, 10.0)],
        demand_in_kg_per_second=0.1,
        tank=tank_in_a_cold_room,
    )
    assert step.hot_water_mass_flow_in_kg_per_second == 0.0
    assert step.unmet_fraction == 1.0
    assert step.node.heat_in_j_per_inflow[2] == 0.0


@pytest.mark.base
def test_a_tank_held_at_its_hottest_inflow_is_solved_at_the_top_of_its_range() -> None:
    """A tank held at 70 °C by a large charging flow must be solved at 70 °C, the top end of its bracket.

    Without a loss and with a tiny draw, the step mean at the hottest mixed temperature is that temperature to within
    the solve tolerance, so the solve returns the evaluation at the bracket's top end without iterating; a wrong early
    return would publish a step mean outside the bracket or a tap draw for another temperature.
    """
    storage = Tank.build(900)
    lossless_tank = dataclasses.replace(storage.tank, loss_coefficient_in_watt_per_kelvin=0.0)
    demand_in_kg_per_second = 1e-12
    step = simple_water_storage.SimpleDHWStorage.solve_tank_step(
        start_temperature_in_celsius=70.0,
        generator_inflows=[hydronics.Inflow(1.0, 70.0), hydronics.Inflow(0.0, 0.0)],
        demand_in_kg_per_second=demand_in_kg_per_second,
        tank=lossless_tank,
    )
    assert step.node.t_mean_c == pytest.approx(70.0, abs=1e-9)
    # the valve answered the top of the bracket, 70 °C: m_hot = m_d (40 - 10) / (70 - 10)
    assert step.hot_water_mass_flow_in_kg_per_second == demand_in_kg_per_second * 30.0 / 60.0


@pytest.mark.base
def test_one_hour_without_a_draw_equals_four_quarter_hours() -> None:
    """A node step that depended on the step length would make the tank's results depend on the resolution."""
    inflows = [hydronics.Inflow(0.1, 65.0), hydronics.Inflow(0.0, 40.0)]
    hour = Tank.step(Tank.build(3600), 40.0, inflows, 0.0)
    quarter = Tank.build(900)
    temperature_in_celsius = 40.0
    for _ in range(4):
        temperature_in_celsius = Tank.step(quarter, temperature_in_celsius, inflows, 0.0).node.t_end_c
    assert temperature_in_celsius == pytest.approx(hour.node.t_end_c, abs=1e-9)


@pytest.mark.base
def test_a_tank_without_water_is_refused() -> None:
    """A tank of zero volume would divide by a zero heat capacity on its first step; it must fail when built."""
    with pytest.raises(ValueError, match="volume_heating_water_storage_in_liter"):
        Tank.build(900, volume_in_liter=0.0)


@pytest.mark.base
def test_the_tank_publishes_its_step_mean_to_the_circuits_and_its_start_temperature_to_the_controllers() -> None:
    """``T̄`` must reach both charging circuits, ``T0`` the controllers, and ``T_end`` become the next ``T0``.

    The circuit's heat booked by the tank is the heat both ends derive from the three published values; a tank that
    published another temperature than it booked against would unbalance the circuit.
    """
    storage, stsv, fakes = Tank.with_fake_inputs(900)
    stsv.set_output_value(fakes[1], 70.0)
    stsv.set_output_value(fakes[2], 0.2)
    storage.state = simple_water_storage.DhwTankState(temperature_at_start_of_step_in_celsius=50.0)
    storage.i_save_state()
    storage.i_restore_state()
    storage.i_simulate(0, stsv, False)

    step = Tank.step(storage, 50.0, [hydronics.Inflow(0.2, 70.0), hydronics.Inflow(0.0, 0.0)], 0.0)
    for channel in (
        storage.water_temperature_to_heat_generator_channel,
        storage.water_temperature_secondary_heat_generator_output_channel,
    ):
        assert Tank.output(stsv, channel) == step.node.t_mean_c
    assert Tank.output(stsv, storage.water_temperature_at_start_of_step_channel) == 50.0
    assert Tank.output(stsv, storage.water_temperature_at_end_of_step_in_celsius_channel) == step.node.t_end_c
    assert storage.state.temperature_at_start_of_step_in_celsius == step.node.t_end_c
    heat_in_watt_hour = Tank.output(stsv, storage.thermal_energy_from_heat_generator_channel)
    assert heat_in_watt_hour * hydronics.UnitConversion.JOULES_PER_WATT_HOUR == pytest.approx(
        hydronics.circuit_heat_j(mass_flow_kg_per_s=0.2, t_supply_c=70.0, t_return_c=step.node.t_mean_c, dt_s=900),
        rel=1e-12,
    )


@pytest.mark.base
def test_every_pass_with_the_same_inputs_publishes_the_same_step_mean() -> None:
    """A tank that remembered its earlier passes would publish a different value for the same inputs.

    The step mean is a function of the inputs and the saved state: a second and a third pass on the same inputs,
    each after a restore, publish the bits of the first.
    """
    storage, stsv, fakes = Tank.with_fake_inputs(900)
    stsv.set_output_value(fakes[0], 30.0)
    stsv.set_output_value(fakes[1], 70.0)
    stsv.set_output_value(fakes[2], 0.2)
    storage.state = simple_water_storage.DhwTankState(temperature_at_start_of_step_in_celsius=50.0)
    storage.i_save_state()
    published_in_celsius = []
    for _ in range(3):
        storage.i_restore_state()
        storage.i_simulate(0, stsv, False)
        published_in_celsius.append(Tank.output(stsv, storage.water_temperature_to_heat_generator_channel))
    assert published_in_celsius[1] == published_in_celsius[0]
    assert published_in_celsius[2] == published_in_celsius[0]


@pytest.mark.base
def test_the_step_mean_outputs_ask_the_simulator_to_accelerate_them() -> None:
    """Without the flag the simulator would iterate the tank's fixed point plainly, past its forced-convergence limit."""
    storage = Tank.build(900)
    assert storage.water_temperature_to_heat_generator_channel.is_accelerated
    assert storage.water_temperature_secondary_heat_generator_output_channel.is_accelerated
    assert not storage.water_temperature_at_start_of_step_channel.is_accelerated


@pytest.mark.base
def test_the_tap_heat_is_positive_in_energy_and_in_power() -> None:
    """A tap heat booked with two signs on one port would make every reader guess which one it got."""
    storage, stsv, fakes = Tank.with_fake_inputs(900)
    stsv.set_output_value(fakes[0], 30.0)
    storage.state = simple_water_storage.DhwTankState(temperature_at_start_of_step_in_celsius=55.0)
    storage.i_save_state()
    storage.i_restore_state()
    storage.i_simulate(0, stsv, False)
    energy_in_watt_hour = Tank.output(stsv, storage.thermal_energy_dhw_channel)
    power_in_watt = Tank.output(stsv, storage.thermal_power_dhw_channel)
    assert energy_in_watt_hour > 0.0
    assert power_in_watt * 900 / hydronics.UnitConversion.JOULES_PER_WATT_HOUR == pytest.approx(
        energy_in_watt_hour, rel=1e-12
    )


@pytest.mark.base
def test_the_unmet_hot_water_heat_is_reported_in_kilowatt_hours() -> None:
    """An unmet-heat KPI in another unit or summed over another column would misreport the comfort shortfall."""
    import pandas as pd  # pylint: disable=import-outside-toplevel  # only this test needs a result frame

    storage = Tank.build(900)
    values = {index: [0.0, 0.0] for index in range(len(storage.outputs))}
    unmet_index = storage.outputs.index(storage.thermal_energy_unmet_dhw_in_watt_hour_channel)
    values[unmet_index] = [500.0, 700.0]
    entries = storage.get_component_kpi_entries(storage.outputs, pd.DataFrame(values))
    (unmet,) = [entry for entry in entries if entry.name == "Unmet DHW heat demand"]
    assert unmet.value == 1.2
    assert unmet.unit == "kWh"
