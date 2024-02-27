"""Test for webtool results."""

import json
from numbers import Number
from pathlib import Path

import pandas as pd
import pytest

from hisim.component import SimulationParameters
from hisim.hisim_main import main
from hisim.postprocessingoptions import PostProcessingOptions


@pytest.mark.base
def test_webtool_results():
    """Check if results for webtool are created."""
    path = "../system_setups/household_heat_pump.py"
    my_simulation_parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    my_simulation_parameters.post_processing_options = [
        PostProcessingOptions.COMPUTE_CAPEX,
        PostProcessingOptions.COMPUTE_OPEX,
        PostProcessingOptions.COMPUTE_KPIS_AND_WRITE_TO_REPORT,
        PostProcessingOptions.MAKE_RESULT_JSON_FOR_WEBTOOL,
        PostProcessingOptions.MAKE_OPERATION_RESULTS_FOR_WEBTOOL,
    ]
    main(str(path), my_simulation_parameters)

    # Read operational results
    with open(
        Path(my_simulation_parameters.result_directory).joinpath("results_daily_operation_for_webtool.json"), "rb"
    ) as handle:
        results_daily_operation_for_webtool = pd.read_json(handle)

    assert isinstance(
        results_daily_operation_for_webtool.loc[
            "2021-01-01", "'DHWHeatPump_w1 - ThermalPowerDelivered [Heating - W]'"
        ],
        Number,
    )

    # Read summary results
    with open(Path(my_simulation_parameters.result_directory).joinpath("results_for_webtool.json"), "rb") as handle:
        results_for_webtool = json.load(handle)

    assert isinstance(
        results_for_webtool["components"]["AdvancedHeatPumpHPLib"]["operation"]["ThermalOutputPower"]["value"],
        Number,
    )
