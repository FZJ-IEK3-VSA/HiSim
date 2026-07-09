""" Tests for the basic household system setup. """
# clean
import os
import pytest

from hisim import hisim_main
from hisim.simulationparameters import SimulationParameters
from hisim import utils


@pytest.mark.system_setups
@utils.measure_execution_time
def test_basic_household() -> None:
    """Run the basic household setup for one day and verify it produced results.

    The setup requests weather data and wires an occupancy, photovoltaic system,
    building, heat pump and electricity meter together. Running it for a single
    day must complete without raising and leave behind the result directory HiSim
    creates, together with the ``finished.flag`` marker that HiSim writes only
    after post-processing has finished.
    """
    pytest.importorskip("wetterdienst")
    path = "../system_setups/basic_household_with_weather_data_request.py"
    mysimpar = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)

    # hisim_main.main returns None, so call its two internal steps directly in order to
    # keep a handle on the simulator and inspect the result directory it populated.
    my_sim = hisim_main.initialize_from_python(
        path_to_module=path,
        my_simulation_parameters=mysimpar,
    )
    hisim_main.run_simulation(my_sim, path_to_module=path)

    result_directory = my_sim.get_simulation_parameters().result_directory
    assert result_directory, "Simulation did not populate a result directory."
    assert os.path.isdir(result_directory), (
        f"Expected result directory was not created: {result_directory}"
    )

    # HiSim writes finished.flag as the very last action of run_all_timesteps, so its
    # presence confirms the simulation ran every timestep and finished post-processing.
    finished_flag = os.path.join(result_directory, "finished.flag")
    assert os.path.isfile(finished_flag), (
        f"finished.flag missing in result directory: {result_directory}"
    )
