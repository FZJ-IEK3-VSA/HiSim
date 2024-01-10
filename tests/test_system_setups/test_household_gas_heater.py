""" Tests for the household with gas heater. """
import os
import subprocess
import shutil
from pathlib import Path
import pytest

from hisim import utils, hisim_main, log
from hisim.simulationparameters import SimulationParameters
from hisim.postprocessingoptions import PostProcessingOptions


@pytest.mark.system_setups
@utils.measure_execution_time
def test_household_gas_heater_main():
    """Execute setup with default values with hisim main."""

    path = "../../system_setups/household_gas_heater.py"

    mysimpar = SimulationParameters.one_day_only(year=2019, seconds_per_timestep=60)
    mysimpar.post_processing_options.append(PostProcessingOptions.MAKE_NETWORK_CHARTS)
    hisim_main.main(path, mysimpar)
    log.information(os.getcwd())


@pytest.mark.system_setups
@utils.measure_execution_time
def test_household_gas_heater_system_setup_starter():
    """Execute setup with hisim system setup starter."""
    config_json = "test_system_setups/configs/household_gas_heater.json"
    result_directory = "test_system_setups/results/test_household_gas_heater_with_system_setup_starter"
    if Path(result_directory).is_dir():
        shutil.rmtree(result_directory)
    Path(result_directory).mkdir(parents=True, exist_ok=True)

    subprocess.check_output(["python", "../hisim/system_setup_starter.py", config_json, result_directory])

    assert Path(result_directory).joinpath("finished.flag").is_file()

    shutil.rmtree(result_directory)
