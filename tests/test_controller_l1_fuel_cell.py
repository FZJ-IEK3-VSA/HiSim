"""Tests for the L1 fuel cell controller.

Focuses on the resolution-independence of the ``PowerNotProvided`` energy
accumulator: the deficit must be scaled by the timestep duration so the
cumulative value is an energy (kWh) rather than a raw sum of instantaneous
power values (kW) that drifts with the simulation resolution.
"""
# clean

from typing import NamedTuple

import pytest

from hisim import component as cp
from hisim.components import controller_l1_fuel_cell as fc
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from tests import functions_for_testing as fft


class _ControllerSetup(NamedTuple):
    """A wired-up fuel cell controller plus its stsv buffer."""

    controller: fc.FuelCellController
    stsv: cp.SingleTimeStepValues
    demand: cp.ComponentOutput


def build_controller(seconds_per_timestep: int = 3600) -> _ControllerSetup:
    """Build and wire a ``FuelCellController`` with a fake demand input.

    Constructs a controller from the default config (min 10 kW, max 110 kW,
    standby 10 kW) for the requested timestep resolution, connects a fake
    ``ComponentOutput`` source to its ``DemandProfile`` input channel,
    allocates a ``SingleTimeStepValues`` buffer, and assigns global indices.
    """
    my_simulation_parameters = SimulationParameters.one_day_only(
        2017, seconds_per_timestep
    )
    config = fc.FuelCellControllerConfig.get_default_fuel_cell_controller_config()
    controller = fc.FuelCellController(
        my_simulation_parameters=my_simulation_parameters, config=config
    )

    demand = cp.ComponentOutput(
        "FakeDemand", "Demand", lt.LoadTypes.ELECTRICITY, lt.Units.KILOWATT
    )
    controller.demand_profile.source_output = demand

    components = [demand, controller]
    number_of_outputs = fft.get_number_of_outputs(components)
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)
    fft.add_global_index_of_components(components)

    return _ControllerSetup(controller=controller, stsv=stsv, demand=demand)


@pytest.mark.base
def test_power_not_provided_output_unit_is_kwh() -> None:
    """The PowerNotProvided output must be declared in kWh (energy), not kW."""
    setup = build_controller()
    assert setup.controller.power_not_provided.unit == lt.Units.KWH


@pytest.mark.base
def test_power_not_provided_scales_with_timestep() -> None:
    """The deficit energy is resolution-independent.

    A constant 10 kW deficit sustained for one hour must accumulate to 10 kWh
    regardless of whether the simulation uses one 3600 s step or sixty 60 s
    steps.  Before the dt fix the 60 s run summed raw kW and produced 600.
    """
    # One 3600 s step: demand 120 kW, max_output 110 kW => 10 kW deficit.
    setup_hour = build_controller(seconds_per_timestep=3600)
    c = setup_hour.controller
    c.load_check(120.0, c.min_output, c.max_output, c.standby_load)
    assert c.power_not_provided_count == pytest.approx(10.0)

    # Sixty 60 s steps: same physical deficit over one hour.
    setup_minute = build_controller(seconds_per_timestep=60)
    c = setup_minute.controller
    for _ in range(60):
        c.load_check(120.0, c.min_output, c.max_output, c.standby_load)
    assert c.power_not_provided_count == pytest.approx(10.0)


@pytest.mark.base
def test_power_not_provided_standby_branch_scales() -> None:
    """The standby branch deficit is also scaled to kWh."""
    setup = build_controller(seconds_per_timestep=1800)
    c = setup.controller
    # demand 15 kW falls in [standby_load=10, min_output=10)? No: 15 > 10.
    # Use min_output=20 to create a standby window [10, 20).
    c.min_output = 20.0
    # 15 kW demand, standby delivers 10 kW => 5 kW deficit for 0.5 h => 2.5 kWh.
    c.load_check(15.0, c.min_output, c.max_output, c.standby_load)
    assert c.power_not_provided_count == pytest.approx(2.5)


@pytest.mark.base
def test_power_not_provided_off_branch_scales() -> None:
    """The OFF branch (demand below standby) deficit is also scaled to kWh."""
    setup = build_controller(seconds_per_timestep=3600)
    c = setup.controller
    # demand 5 kW < standby_load 10 => full 5 kW deficit for 1 h => 5 kWh.
    c.load_check(5.0, c.min_output, c.max_output, c.standby_load)
    assert c.power_not_provided_count == pytest.approx(5.0)


@pytest.mark.base
def test_power_not_provided_within_range_adds_zero() -> None:
    """When demand is within [min_output, max_output] no deficit is recorded."""
    setup = build_controller(seconds_per_timestep=3600)
    c = setup.controller
    c.load_check(50.0, c.min_output, c.max_output, c.standby_load)
    assert c.power_not_provided_count == pytest.approx(0.0)


@pytest.mark.base
def test_power_not_provided_via_i_simulate() -> None:
    """i_simulate writes the scaled deficit to the stsv output buffer.

    Drives the full integration path rather than calling ``load_check``
    directly: a demand value is placed in the stsv input buffer, ``i_simulate``
    runs ``load_check`` (wrapping the input through ``abs``) and writes the
    accumulated ``power_not_provided`` output back to stsv.  A 120 kW demand
    against a 110 kW max for one 3600 s step yields a 10 kWh deficit.
    """
    setup = build_controller(seconds_per_timestep=3600)
    setup.stsv.values[setup.demand.global_index] = 120.0
    setup.controller.i_simulate(0, setup.stsv, False)
    result = setup.stsv.values[setup.controller.power_not_provided.global_index]
    assert result == pytest.approx(10.0)


@pytest.mark.base
def test_power_not_provided_via_i_simulate_resolution_independent() -> None:
    """The stsv output is resolution-independent through the full i_simulate path.

    Sixty 60 s steps with the same 10 kW deficit produce the same 10 kWh as a
    single 3600 s step, exercising the dt scaling end to end.
    """
    setup = build_controller(seconds_per_timestep=60)
    for _ in range(60):
        setup.stsv.values[setup.demand.global_index] = 120.0
        setup.controller.i_simulate(0, setup.stsv, False)
    result = setup.stsv.values[setup.controller.power_not_provided.global_index]
    assert result == pytest.approx(10.0)


@pytest.mark.base
def test_power_not_provided_via_i_simulate_negative_demand() -> None:
    """i_simulate wraps demand through abs(), so negative demand is treated as positive.

    A demand of -120 kW is wrapped to 120 kW by ``abs`` inside ``i_simulate``,
    producing the same 10 kWh deficit as a positive 120 kW demand.
    """
    setup = build_controller(seconds_per_timestep=3600)
    setup.stsv.values[setup.demand.global_index] = -120.0
    setup.controller.i_simulate(0, setup.stsv, False)
    result = setup.stsv.values[setup.controller.power_not_provided.global_index]
    assert result == pytest.approx(10.0)
