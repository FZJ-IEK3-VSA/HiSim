"""Test for controller l1 generic electrolyzer."""

import pytest
from tests import functions_for_testing as fft
from hisim import component as cp
from hisim.components import controller_l1_electrolyzer_h2
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from hisim import log


@pytest.mark.base
def test_electrolyzer_controller():
    """Test electrolzyer controller."""
    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only(
        2021, seconds_per_timestep
    )

    name: str = "Test-Controller"
    nom_load: float = 800.0  # [kW]
    min_load: float = 300.0  # [kW]
    max_load: float = 1000.0  # [kW]
    warm_start_time: float = 70.0  # [s]
    cold_start_time: float = 1800.0  # [s]

    timestep = 1
    force_convergence = False

    # ===================================================================================================================
    # Setup Electrolyzer

    my_controller_config = (
        controller_l1_electrolyzer_h2.ElectrolyzerControllerConfig(
            name=name,
            nom_load=nom_load,
            min_load=min_load,
            max_load=max_load,
            warm_start_time=warm_start_time,
            cold_start_time=cold_start_time,
            standby_load=5.0
        )
    )
    my_controller = controller_l1_electrolyzer_h2.ElectrolyzerController(
        config=my_controller_config, my_simulation_parameters=my_simulation_parameters
    )

    # ===================================================================================================================
    # Set Fake Inputs
    load_input = cp.ComponentOutput(
        "FakeProvidedLoad", "Provided Load", lt.LoadTypes.ELECTRICITY, lt.Units.KILOWATT
    )

    number_of_outputs = fft.get_number_of_outputs([my_controller, load_input])
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    # Link inputs and outputs
    my_controller.load_input.source_output = load_input

    # Add Global Index and set values for fake Inputs
    fft.add_global_index_of_components([my_controller, load_input])
    stsv.values[load_input.global_index] = 1700.0

    # Simulate
    my_controller.i_restore_state()
    my_controller.i_simulate(timestep, stsv, force_convergence)
    log.information(str(stsv.values))

    # input load to current load
    # assert stsv.values[my_controller.distributed_load.global_index] == 0
    assert stsv.values[my_controller.curtailed_load.global_index] == 700.0

    # python -m pytest ../tests/test_controller_l1_generic_electrolyzer.py
