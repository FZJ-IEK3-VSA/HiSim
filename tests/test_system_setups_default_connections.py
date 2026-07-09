"""Test for system setup default connections."""

from pathlib import Path

import pytest

from hisim import hisim_main
from hisim import utils
from hisim.postprocessingoptions import PostProcessingOptions
from hisim.simulationparameters import SimulationParameters


@pytest.mark.system_setups
@utils.measure_execution_time
def test_basic_household_with_default_connections() -> None:
    """Test that the basic household system setup runs with default component connections.

    Verifies that the system setup defined in `../system_setups/default_connections.py`
    executes successfully using default connection configurations for a one-day simulation
    with 60-second timesteps. Beyond merely running without raising, the test pins the
    concrete outcome: the simulator populates the result directory, writes its
    ``finished.flag`` marker after all timesteps and post-processing have completed, and
    exports the simulation results to a CSV file. Checking for the exported CSV (in addition
    to the ``finished.flag`` marker) confirms the run actually produced result data rather
    than only writing a completion flag, which distinguishes a real, completed run from a
    silent no-op.
    """
    path = "../system_setups/default_connections.py"

    simulation_parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    # ``one_day_only`` enables no post-processing options by default, so no result artifacts
    # (CSV/JSON/PDF) would be written. Enable CSV export (combined into a single file) so the
    # test can assert that the simulation actually emitted result data, not just a
    # ``finished.flag`` marker.
    simulation_parameters.post_processing_options.extend(
        [
            PostProcessingOptions.EXPORT_TO_CSV,
            PostProcessingOptions.EXPORT_RESULTS_IN_ONE_FILE,
        ]
    )
    hisim_main.main(path, simulation_parameters)

    # The simulator populates ``result_directory`` while preparing the run (it mutates the
    # same SimulationParameters instance passed in above) and writes a ``finished.flag`` file
    # once all timesteps and post-processing are complete. Guard against an unset directory
    # first so a failure here produces a clear assertion error instead of a TypeError from
    # ``Path(None)``.
    assert simulation_parameters.result_directory, (
        "Simulation did not populate result_directory on the SimulationParameters instance."
    )
    results_dir = Path(simulation_parameters.result_directory)
    assert results_dir.is_dir(), f"Result directory was not created: {results_dir}"
    assert (results_dir / "finished.flag").is_file(), (
        f"finished.flag was not written to {results_dir}"
    )
    # EXPORT_TO_CSV together with EXPORT_RESULTS_IN_ONE_FILE writes a single
    # ``all_results.csv`` containing every component output for the simulated period. Its
    # presence is a stronger signal than ``finished.flag`` alone that post-processing
    # actually emitted result data.
    all_results_csv = results_dir / "all_results.csv"
    assert all_results_csv.is_file(), f"all_results.csv was not written to {results_dir}"
