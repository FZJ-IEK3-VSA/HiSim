"""Test for the L2 XTP fuel cell EMS controller.

Guards the demand input in ``i_simulate`` against non-finite (NaN/inf) values
coming from an upstream component, which would otherwise silently propagate
through ``abs``/division and the downstream control logic.
"""

import pytest

from hisim import component as cp
from hisim.components import controller_l2_xtp_fuel_cell_ems
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from tests import functions_for_testing as fft


def _build_controller(operation_mode: str = "StandbyLoad") -> "controller_l2_xtp_fuel_cell_ems.XTPController":
    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only(
        2021, seconds_per_timestep
    )
    config = controller_l2_xtp_fuel_cell_ems.XTPControllerConfig(
        building_name="BUI1",
        name="L2XTPController",
        nom_output=10.0,  # kW
        min_output=2.0,  # kW
        max_output=10.0,  # kW
        standby_load=1.0,  # kW
        operation_mode=operation_mode,
    )
    return controller_l2_xtp_fuel_cell_ems.XTPController(
        my_simulation_parameters=my_simulation_parameters,
        config=config,
    )


def _set_demand(
    controller: controller_l2_xtp_fuel_cell_ems.XTPController,
    raw_value: float,
) -> cp.SingleTimeStepValues:
    """Build a SingleTimeStepValues wired to a fake demand source set to raw_value."""
    load_input = cp.ComponentOutput(
        "FakeDemandLoad", "DemandLoad", lt.LoadTypes.ELECTRICITY, lt.Units.WATT
    )
    number_of_outputs = fft.get_number_of_outputs([controller, load_input])
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)
    controller.demand_input.source_output = load_input
    fft.add_global_index_of_components([controller, load_input])
    stsv.values[load_input.global_index] = raw_value
    return stsv


@pytest.mark.base
def test_finite_demand_is_processed() -> None:
    """A finite demand input flows through normally (regression guard)."""
    controller = _build_controller()
    stsv = _set_demand(controller, 5000.0)  # 5 kW after /1000

    controller.i_restore_state()
    controller.i_simulate(timestep=0, stsv=stsv, force_convergence=False)

    # 5000 W == 5 kW, within [min_output=2, max_output=10] => demand_to_system = demand_load
    assert stsv.values[controller.demand_to_system.global_index] == pytest.approx(5.0)
    assert stsv.values[controller.load_from_battery.global_index] == pytest.approx(0.0)


@pytest.mark.base
def test_nan_demand_input_raises() -> None:
    """A NaN demand input must be rejected explicitly, not silently propagated."""
    controller = _build_controller()
    stsv = _set_demand(controller, float("nan"))

    controller.i_restore_state()
    with pytest.raises(AssertionError, match="Non-finite demand input"):
        controller.i_simulate(timestep=0, stsv=stsv, force_convergence=False)


@pytest.mark.base
def test_pos_inf_demand_input_raises() -> None:
    """A +inf demand input must be rejected (would otherwise yield inf downstream)."""
    controller = _build_controller()
    stsv = _set_demand(controller, float("inf"))

    controller.i_restore_state()
    with pytest.raises(AssertionError, match="Non-finite demand input"):
        controller.i_simulate(timestep=0, stsv=stsv, force_convergence=False)


@pytest.mark.base
def test_neg_inf_demand_input_raises() -> None:
    """A -inf demand input must be rejected."""
    controller = _build_controller()
    stsv = _set_demand(controller, float("-inf"))

    controller.i_restore_state()
    with pytest.raises(AssertionError, match="Non-finite demand input"):
        controller.i_simulate(timestep=0, stsv=stsv, force_convergence=False)
