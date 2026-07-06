"""Tests for the household_4a system setup with car priority.

Runs the advanced heat-pump / EV / PV setup defined in
``system_setups/household_4a_with_car_priority_advanced_hp_ev_pv.py``
for a single simulated day and asserts that the run produced its
output artifacts. This test is marked ``utsp`` and is therefore not
part of the fast ``base`` gate.
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

    Runs a one-day simulation of the household_4a (car-priority) advanced
    HP / EV / PV system setup and asserts that the simulation completed and
    wrote its output artifacts to the results directory, rather than merely
    failing to raise.
    """

    # The config file is removed so that a stale file from a previous run is
    # not picked up by HouseholdAdvancedHPEvPvConfig.load_from_json.  It is
    # NOT regenerated during the run — the setup function calls get_default()
    # when no module config is supplied (the create_configuration call that
    # used to write it is commented out), so we do not assert on its presence.
    config_filename = "household_4_advanced_hp_ev_pv_config.json"
    if Path(config_filename).is_file():
        os.remove(config_filename)

    path = "../system_setups/household_4a_with_car_priority_advanced_hp_ev_pv.py"

    mysimpar = SimulationParameters.one_day_only(year=2019, seconds_per_timestep=60)
    mysimpar.post_processing_options.append(PostProcessingOptions.MAKE_NETWORK_CHARTS)
    hisim_main.main(path, mysimpar)
    log.information(os.getcwd())

    # hisim_main.main returns None; the simulator records the result directory
    # on the simulation parameters object and writes a ``finished.flag`` file
    # at the end of run_all_timesteps, after post-processing.
    results_dir = Path(mysimpar.result_directory)
    assert results_dir.is_dir(), f"Result directory was not created: {results_dir}"
    assert (results_dir / "finished.flag").is_file(), (
        f"finished.flag was not written to {results_dir}"
    )
    # CSV exports require the EXPORT_TO_CSV post-processing option, which is
    # not enabled here.  However, component_connections.json is written
    # unconditionally during connect_input() calls in the setup function, so
    # checking for at least one JSON artifact confirms the simulation
    # progressed beyond initialisation and produced real output.
    assert any(results_dir.rglob("*.json")), (
        f"No JSON output artifacts found in {results_dir}"
    )
