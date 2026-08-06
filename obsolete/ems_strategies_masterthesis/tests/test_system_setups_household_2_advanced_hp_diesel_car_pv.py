"""Tests for the household_2 advanced system setup (heat pump, diesel car, PV).

Runs a one-day simulation of the ``household_2_advanced_hp_diesel_car_pv``
system setup via ``hisim_main.main`` and asserts that the full simulation
pipeline completes and writes output artifacts to the results directory.
"""
# clean
import os
from pathlib import Path
import pytest

from hisim import hisim_main
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim import utils
from hisim.postprocessingoptions import PostProcessingOptions


# @pytest.mark.system_setups
@pytest.mark.utsp
@utils.measure_execution_time
def test_basic_household() -> None:
    """Single day.

    Runs a one-day simulation of the household_2_advanced_hp_diesel_car_pv system
    setup (advanced heat pump, diesel car, and PV) and asserts that the simulation
    completed and wrote its output artifacts to the results directory, rather than
    merely failing to raise.
    """

    config_filename = "household_2_advanced_hp_diesel_car_pv_config.json"
    if Path(config_filename).is_file():
        os.remove(config_filename)

    system_setup_path = "../system_setups/household_2_advanced_hp_diesel_car_pv.py"
    simulation_parameters = SimulationParameters.one_day_only(year=2019, seconds_per_timestep=60)
    simulation_parameters.post_processing_options.append(PostProcessingOptions.MAKE_NETWORK_CHARTS)
    hisim_main.main(system_setup_path, simulation_parameters)
    log.information(os.getcwd())

    # hisim_main.main returns None; the simulator instead records the directory it
    # wrote to on the simulation parameters object (see
    # Simulator.prepare_simulation_directory) and writes a ``finished.flag`` file
    # at the very end of run_all_timesteps, after post-processing. Asserting on
    # both confirms the full simulation pipeline ran to completion and produced
    # output artifacts rather than merely failing to raise.
    results_dir = Path(simulation_parameters.result_directory)
    assert results_dir.is_dir(), f"Result directory was not created: {results_dir}"
    assert (results_dir / "finished.flag").is_file(), (
        f"finished.flag was not written to {results_dir}"
    )
    assert any(results_dir.iterdir()), f"Results directory is empty: {results_dir}"
