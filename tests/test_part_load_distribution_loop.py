"""The buffer-less distribution loop, quasi-steady above its turnover criterion.

Above the part-load threshold and the turnover criterion ``a = m_design dt / M_pipe > 1`` the heat distribution system
computes its loop in the same step: while heating or cooling is on, the emitters exchange the building's demand with
the supply, the pipe water ends at the mean of supply and return, and the return the generator sees is the step mean
that closes the loop's balance, so a substation regulating its supply bills the delivered heat plus the pipe water's
change of stored heat. While both are off the loop stands. Below the criterion the loop keeps its lagged behaviour.
"""

import random
from pathlib import Path
from typing import Any, List, Tuple

import numpy as np
import pandas as pd
import pytest
import yaml

from hisim import component as cp
from hisim import loadtypes as lt
from hisim.components.generic_district_heating import DistrictHeating
from hisim.components.heat_distribution_system import (
    HeatDistribution,
    HeatDistributionConfig,
    HeatDistributionSystemType,
    PositionHotWaterStorageInSystemSetup,
)
from hisim.config import ComponentID
from hisim.energy_system.executor import run_energy_system
from tests import functions_for_testing as fft
from tests.part_load_rigs import Parameters


class Loop:
    """A floor-heating loop of 121.2 m² without a buffer, pumped by the distribution system itself."""

    #: The loop's design flow, kg/s.
    DESIGN_FLOW_KG_PER_S: float = 0.3

    @classmethod
    def build(
        cls,
        seconds_per_timestep: int,
        flow_kg_per_s: float = DESIGN_FLOW_KG_PER_S,
        position: PositionHotWaterStorageInSystemSetup = PositionHotWaterStorageInSystemSetup.NO_STORAGE_MASS_FLOW_FIX,
        **extra: Any,
    ) -> HeatDistribution:
        """The heat distribution system at a resolution."""
        config = HeatDistributionConfig(
            component_id=ComponentID("HeatDistributionSystem"),
            heating_system=HeatDistributionSystemType.FLOORHEATING,
            water_mass_flow_rate_in_kg_per_second=flow_kg_per_s,
            absolute_conditioned_floor_area_in_m2=121.2,
            position_hot_water_storage_in_system=position,
        )
        loop: HeatDistribution = HeatDistribution(
            config=config, my_simulation_parameters=Parameters.one_day(seconds_per_timestep, **extra)
        )
        return loop

    @staticmethod
    def wired(loop: HeatDistribution) -> Tuple[Any, List[Any]]:
        """The loop on fake inputs: controller state, demand, supply, room temperature."""
        channels = [
            (loop.state_channel, lt.LoadTypes.ANY, lt.Units.ANY),
            (loop.theoretical_thermal_building_demand_channel, lt.LoadTypes.HEATING, lt.Units.WATT),
            (loop.water_temperature_input_channel, lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS),
            (loop.residence_temperature_input_channel, lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS),
        ]
        fakes = []
        for number, (channel, load_type, unit) in enumerate(channels):
            fake = cp.ComponentOutput(
                f"Fake{number}", f"Fake{number}", load_type, unit, component_id=ComponentID(f"Fake{number}")
            )
            channel.source_output = fake
            fakes.append(fake)
        stsv = cp.SingleTimeStepValues(fft.get_number_of_outputs([*fakes, loop]))
        fft.add_global_index_of_components([*fakes, loop])
        return stsv, fakes


@pytest.mark.base
@pytest.mark.parametrize(
    "seconds_per_timestep, flow_kg_per_s, extra, quasi_steady",
    [
        (60, 0.3, {}, False),
        (600, 0.3, {}, False),
        (900, 0.3, {}, True),
        (3600, 0.3, {}, True),
        (900, 0.2, {}, False),
        (900, 0.3, {"part_load_above_seconds": 900}, False),
    ],
)
def test_the_loop_is_quasi_steady_above_the_threshold_and_the_turnover_criterion(
    seconds_per_timestep: int, flow_kg_per_s: float, extra: Any, quasi_steady: bool
) -> None:
    """``a = m dt / M_pipe``: 213 kg of pipe water turn over 1.27 times in 900 s at 0.3 kg/s, 0.85 times at 0.2 kg/s."""
    loop = Loop.build(seconds_per_timestep, flow_kg_per_s, **extra)
    assert loop.mass_of_pipe_water_in_kg() == pytest.approx(212.73, abs=0.01)
    assert loop.loop_turnover_ratio() == pytest.approx(
        flow_kg_per_s * seconds_per_timestep / loop.mass_of_pipe_water_in_kg(), rel=1e-15
    )
    assert loop.loop_is_quasi_steady is quasi_steady


@pytest.mark.base
def test_a_loop_with_a_buffer_or_a_generator_pump_stays_dynamic() -> None:
    """Only the loop the distribution system pumps itself, without a buffer, is computed quasi-steady."""
    for position in (
        PositionHotWaterStorageInSystemSetup.PARALLEL,
        PositionHotWaterStorageInSystemSetup.NO_STORAGE_MASS_FLOW_FROM_HEAT_GENERATOR,
    ):
        assert not Loop.build(3600, position=position).loop_is_quasi_steady


@pytest.mark.base
def test_the_generator_brings_the_delivered_heat_plus_the_pipe_waters_heat_increase() -> None:
    """``m c (T_sup - T_ret_mean) dt = Q_delivered dt + C_pipe (T_pipe,end - T_pipe,0)`` on random steps."""
    rng = random.Random(20261009)
    for seconds_per_timestep in (900, 3600):
        loop = Loop.build(seconds_per_timestep)
        specific_heat_j_per_kg_k = loop.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius
        for _ in range(500):
            supply_c = rng.uniform(10.0, 70.0)
            step = loop.quasi_steady_loop_step(
                supply_temperature_in_celsius=supply_c,
                mass_flow_in_kg_per_second=0.3,
                heat_demand_in_watt=rng.uniform(-2000.0, 9000.0),
                room_temperature_in_celsius=rng.uniform(18.0, 23.0),
                exchanges_with_building=rng.random() < 0.8,
                pipe_water_start_temperature_in_celsius=rng.uniform(20.0, 60.0),
            )
            generator_j = (
                0.3
                * specific_heat_j_per_kg_k
                * (supply_c - step.step_mean_return_temperature_in_celsius)
                * seconds_per_timestep
            )
            assert generator_j == pytest.approx(
                step.delivered_power_in_watt * seconds_per_timestep + step.pipe_water_heat_increase_in_joule,
                rel=1e-9,
                abs=1e-3,
            )


@pytest.mark.base
def test_the_quasi_steady_loop_publishes_this_steps_values_and_keeps_its_pipe_water() -> None:
    """At 900 s the outputs are the step's own (no lag), and the pipe water's end temperature is the next start."""
    loop = Loop.build(900)
    stsv, fakes = Loop.wired(loop)
    for fake, value in zip(fakes, (1.0, 3000.0, 50.0, 20.0)):
        stsv.set_output_value(fake, value)
    loop.state.pipe_water_temperature_in_celsius = 48.0
    loop.i_save_state()
    loop.i_simulate(0, stsv, False)
    expected = loop.quasi_steady_loop_step(
        supply_temperature_in_celsius=50.0,
        mass_flow_in_kg_per_second=0.3,
        heat_demand_in_watt=3000.0,
        room_temperature_in_celsius=20.0,
        exchanges_with_building=True,
        pipe_water_start_temperature_in_celsius=48.0,
    )
    assert (
        stsv.values[loop.water_temperature_outlet_channel.global_index]
        == expected.step_mean_return_temperature_in_celsius
    )
    assert stsv.values[loop.thermal_power_delivered_channel.global_index] == pytest.approx(3000.0)
    assert stsv.values[loop.pipe_water_heat_increase_in_watt_hour_channel.global_index] == pytest.approx(
        expected.pipe_water_heat_increase_in_joule / 3600.0
    )
    assert loop.state.pipe_water_temperature_in_celsius == expected.pipe_water_temperature_in_celsius


@pytest.mark.base
def test_the_documented_heating_step() -> None:
    """The docstring's example: 0.3 kg/s at 50 °C into 3 kW, pipe water from 48 °C, over 900 s."""
    step = Loop.build(900).quasi_steady_loop_step(
        supply_temperature_in_celsius=50.0,
        mass_flow_in_kg_per_second=0.3,
        heat_demand_in_watt=3000.0,
        room_temperature_in_celsius=20.0,
        exchanges_with_building=True,
        pipe_water_start_temperature_in_celsius=48.0,
    )
    assert step.return_temperature_in_celsius == pytest.approx(47.6, abs=0.01)
    assert step.delivered_power_in_watt == pytest.approx(3000.0)
    assert step.pipe_water_temperature_in_celsius == pytest.approx(48.8, abs=0.01)
    assert step.step_mean_return_temperature_in_celsius == pytest.approx(47.0, abs=0.1)


@pytest.mark.base
@pytest.mark.parametrize("seconds_per_timestep", [900, 3600])
def test_a_cooling_demand_is_exchanged_like_a_heating_one(seconds_per_timestep: int) -> None:
    """With cooling on, 0.3 kg/s at 16 °C take 2 kW from a 24 °C room: the return rises, the generator draws heat.

    A loop that clipped the demand at zero would deliver no cooling above the part-load threshold, while the same loop
    below it cools.
    """
    loop = Loop.build(seconds_per_timestep)
    specific_heat_j_per_kg_k = loop.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius
    step = loop.quasi_steady_loop_step(
        supply_temperature_in_celsius=16.0,
        mass_flow_in_kg_per_second=0.3,
        heat_demand_in_watt=-2000.0,
        room_temperature_in_celsius=24.0,
        exchanges_with_building=True,
        pipe_water_start_temperature_in_celsius=17.0,
    )
    assert step.delivered_power_in_watt == pytest.approx(-2000.0)
    assert step.return_temperature_in_celsius == pytest.approx(16.0 + 2000.0 / (0.3 * specific_heat_j_per_kg_k))
    assert step.step_mean_return_temperature_in_celsius > 16.0
    generator_w = 0.3 * specific_heat_j_per_kg_k * (16.0 - step.step_mean_return_temperature_in_celsius)
    assert generator_w < 0.0
    assert generator_w * seconds_per_timestep == pytest.approx(
        step.delivered_power_in_watt * seconds_per_timestep + step.pipe_water_heat_increase_in_joule, rel=1e-9
    )


@pytest.mark.base
def test_a_cooling_step_of_a_quasi_steady_loop_cools_on_the_dynamic_path() -> None:
    """At 900 s with cooling on the loop exchanges the cooling demand one step lagged, as at 60 s; it never drops it.

    A loop that clipped the demand at zero above the part-load threshold would deliver no cooling there.
    """
    loop = Loop.build(900)
    assert loop.loop_is_quasi_steady
    stsv, fakes = Loop.wired(loop)
    for fake, value in zip(fakes, (float(HeatDistribution.COOLING_STATE), -2000.0, 16.0, 24.0)):
        stsv.set_output_value(fake, value)
    loop.i_simulate(0, stsv, False)
    assert stsv.values[loop.thermal_power_delivered_channel.global_index] == 0.0  # last step's, as on the lagged path
    assert loop.state.thermal_power_delivered_in_watt == pytest.approx(-2000.0)
    assert loop.state.pipe_water_temperature_in_celsius == pytest.approx(
        (loop.state.water_input_temperature_in_celsius + loop.state.water_output_temperature_in_celsius) / 2.0
    )


@pytest.mark.base
def test_with_heating_off_the_loop_stands_and_bills_nothing() -> None:
    """Off, the pipe water keeps its temperature, nothing is exchanged, and the generator sees its own supply back.

    A loop that let its pipe water follow the supply while off would have the generator bill heat that reaches nobody.
    """
    step = Loop.build(900).quasi_steady_loop_step(
        supply_temperature_in_celsius=55.0,
        mass_flow_in_kg_per_second=0.3,
        heat_demand_in_watt=3000.0,
        room_temperature_in_celsius=20.0,
        exchanges_with_building=False,
        pipe_water_start_temperature_in_celsius=35.0,
    )
    assert step.delivered_power_in_watt == 0.0
    assert step.pipe_water_temperature_in_celsius == 35.0
    assert step.pipe_water_heat_increase_in_joule == 0.0
    assert step.step_mean_return_temperature_in_celsius == step.return_temperature_in_celsius == 55.0


@pytest.mark.base
def test_the_loop_starts_with_its_pipe_water_at_its_initial_supply_and_return_temperature() -> None:
    """The pipe water starts at the loop's initial supply and return temperature, not at a value of its own."""
    loop = Loop.build(900)
    assert loop.state.pipe_water_temperature_in_celsius == HeatDistribution.INITIAL_WATER_TEMPERATURE_IN_CELSIUS
    assert loop.state.water_input_temperature_in_celsius == HeatDistribution.INITIAL_WATER_TEMPERATURE_IN_CELSIUS
    assert loop.state.water_output_temperature_in_celsius == HeatDistribution.INITIAL_WATER_TEMPERATURE_IN_CELSIUS


@pytest.mark.base
def test_the_standstill_time_constant_is_the_pipe_waters_capacity_over_the_screeds_conductance() -> None:
    """``tau = M c (h / lambda + 1 / alpha) / A_outer``: about 2970 s for 121.2 m² of floor heating."""
    loop = Loop.build(900)
    resistance = 0.04 / 1.4 + 1 / 5.8
    surface_m2 = 3.141592653589793 * 0.018 * 8.8 * 121.2
    expected_s = loop.mass_of_pipe_water_in_kg() * loop.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius
    assert loop.pipe_water_time_constant_in_seconds() == pytest.approx(expected_s * resistance / surface_m2, rel=1e-12)
    assert loop.pipe_water_time_constant_in_seconds() == pytest.approx(2970.0, rel=0.01)


@pytest.mark.base
def test_below_the_criterion_the_loop_keeps_its_lagged_outputs() -> None:
    """At 60 s the loop publishes last step's values, as before, and no pipe-water heat."""
    loop = Loop.build(60)
    stsv, fakes = Loop.wired(loop)
    for fake, value in zip(fakes, (1.0, 3000.0, 50.0, 20.0)):
        stsv.set_output_value(fake, value)
    loop.i_simulate(0, stsv, False)
    assert stsv.values[loop.water_temperature_outlet_channel.global_index] == 21.0
    assert stsv.values[loop.thermal_power_delivered_channel.global_index] == 0.0
    assert stsv.values[loop.pipe_water_heat_increase_in_watt_hour_channel.global_index] == 0.0
    assert loop.state.pipe_water_temperature_in_celsius == 21.0


class DistrictHeatingTwin:
    """Three winter weeks of the district-heating twin, and its columns."""

    #: The repository's root.
    ROOT = Path(__file__).resolve().parents[1]

    #: The twin.
    TWIN: str = "household_district_heating_building_sizer"

    @classmethod
    def run(cls, work: Path, seconds_per_timestep: int) -> pd.DataFrame:
        """Run the twin over the first three weeks of 2021 with the predefined occupancy profile."""
        text = (cls.ROOT / "energy_systems" / f"{cls.TWIN}.energy_system.yaml").read_text(encoding="utf-8")
        energy_system = work / f"{cls.TWIN}.energy_system.yaml"
        energy_system.write_text(text.replace("USE_LOCAL_LPG", "USE_PREDEFINED_PROFILE"), encoding="utf-8")
        values = {
            "start_date": "2021-01-01T00:00:00",
            "end_date": "2021-01-22T00:00:00",
            "seconds_per_timestep": seconds_per_timestep,
            "country": "DE",
            "logging_level": 3,
            "post_processing_options": [],
        }
        path = work / "window.simulation.yaml"
        path.write_text(yaml.safe_dump(values, sort_keys=False), encoding="utf-8")
        built = run_energy_system(energy_system, path, result_directory=str(work / "results"))
        return built.simulator.results_data_frame

    @staticmethod
    def column(frame: pd.DataFrame, prefix: str) -> np.ndarray:
        """The one column whose name starts with ``prefix``."""
        names = [name for name in frame.columns if name.startswith(prefix)]
        assert len(names) == 1, (prefix, names)
        values: np.ndarray = frame[names[0]].to_numpy(dtype=float)
        return values


@pytest.mark.extendedbase2
@pytest.mark.parametrize("seconds_per_timestep", [900, 3600])
def test_district_heating_bills_the_delivered_heat_plus_the_pipe_waters_change(
    seconds_per_timestep: int, tmp_path: Path
) -> None:
    """On every step the substation's space-heating heat is the loop's delivered heat plus the pipe water's increase.

    The two ends meet to what the simulator's convergence tolerance leaves open (1e-4 K on the return, about 0.1 W at
    0.3 kg/s), so the check allows 1 W per step; over three weeks the sums agree to 1e-4.
    """
    frame = DistrictHeatingTwin.run(tmp_path, seconds_per_timestep)
    bill_w = DistrictHeatingTwin.column(frame, f"DistrictHeating - {DistrictHeating.ThermalOutputShPower} [")
    delivered_w = DistrictHeatingTwin.column(
        frame, f"HeatDistributionSystem - {HeatDistribution.ThermalPowerDelivered} ["
    )
    stored_wh = DistrictHeatingTwin.column(
        frame, f"HeatDistributionSystem - {HeatDistribution.ThermalEnergyIncreaseOfPipeWaterInWattHour} ["
    )
    stored_w = stored_wh * 3600.0 / seconds_per_timestep
    assert np.max(np.abs(bill_w - delivered_w - stored_w)) < 1.0
    assert bill_w.sum() == pytest.approx(delivered_w.sum() + stored_w.sum(), rel=1e-4)
    assert delivered_w.sum() > 0.0
