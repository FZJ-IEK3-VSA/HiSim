"""Test for webtool results."""

from numbers import Number
from pathlib import Path
import json

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
            "2021-01-01", 'UTSPConnector - ElectricityOutput [Electricity - W]'
        ],
        Number,
    )

    # Read summary results
    with open(Path(my_simulation_parameters.result_directory).joinpath("results_for_webtool.json"), "rb") as handle:
        results_for_webtool = json.load(handle)

    # Test single values
    assert isinstance(
        results_for_webtool["components"]["AdvancedHeatPumpHPLib"]["operation"]["ThermalOutputPower"]["value"],
        Number,
    )

    # Test quantity
    print(results_for_webtool["components"]["AdvancedHeatPumpHPLib"]["configuration"]["flow_temperature_in_celsius"])
    __import__('ipdb').set_trace()
    assert isinstance(
        results_for_webtool["components"]["AdvancedHeatPumpHPLib"]["configuration"]["flow_temperature_in_celsius"]["unit"]["symbol"],
        str,
    )

    # Test KPIs
    assert isinstance(
        results_for_webtool["kpis"]["Costs and Emissions"]["System operational costs for simulated period"]["value"],
        Number,
    )

    # Read profiles
    with open(
        Path(my_simulation_parameters.result_directory).joinpath("results_daily_operation_for_webtool.json"), "rb"
    ) as handle:
        profiles_for_webtool = json.load(handle)

    assert isinstance(
        profiles_for_webtool['UTSPConnector - ElectricityOutput [Electricity - W]']["2021-01-01T00:00:00.000"],
        Number,
    )
