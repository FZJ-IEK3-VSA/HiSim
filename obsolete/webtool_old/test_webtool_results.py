"""Tests for webtool result generation."""

# """Test for webtool results."""

# from numbers import Number
# from pathlib import Path
# import json

# import pandas as pd
# import pytest

# from hisim.component import SimulationParameters
# from hisim.hisim_main import main
# from hisim.postprocessingoptions import PostProcessingOptions


# @pytest.mark.base
# def test_webtool_results():
#     """Check if results for webtool are created."""
#     path = "../system_setups/household_heat_pump.py"
#     my_simulation_parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
#     my_simulation_parameters.post_processing_options = [
#         PostProcessingOptions.COMPUTE_CAPEX,
#         PostProcessingOptions.COMPUTE_OPEX,
#         PostProcessingOptions.COMPUTE_KPIS,
#         PostProcessingOptions.WRITE_KPIS_TO_JSON,
#         PostProcessingOptions.MAKE_RESULT_JSON_FOR_WEBTOOL,
#         PostProcessingOptions.MAKE_OPERATION_RESULTS_FOR_WEBTOOL,
#     ]
#     main(str(path), my_simulation_parameters)

#     # Read operational results
#     with open(
#         Path(my_simulation_parameters.result_directory).joinpath("results_daily_operation_for_webtool.json"), "rb"
#     ) as handle:
#         results_daily_operation_for_webtool = pd.read_json(handle)

#     assert isinstance(
#         results_daily_operation_for_webtool.loc[
#             "2021-01-01", "AdvancedHeatPumpHPLib - ThermalOutputPower [Heating - W]"
#         ],
#         Number,
#     )

#     # Read summary results
#     with open(Path(my_simulation_parameters.result_directory).joinpath("results_for_webtool.json"), "rb") as handle:
#         results_for_webtool = json.load(handle)

#     # Test single values
#     assert isinstance(
#         results_for_webtool["components"]["AdvancedHeatPumpHPLib"]["operation"]["ThermalOutputPower"]["value"],
#         Number,
#     )

#     # Test quantity
#     assert isinstance(
#         results_for_webtool["components"]["AdvancedHeatPumpHPLib"]["configuration"]["flow_temperature_in_celsius"]["unit"]["symbol"],
#         str,
#     )

#     # Test KPIs
#     assert isinstance(
#         results_for_webtool["kpis"]["BUI1"]["Costs"]["Maintenance costs for simulated period"]["value"],
#         Number,
#     )

#     # Read profiles
#     with open(
#         Path(my_simulation_parameters.result_directory).joinpath("results_daily_operation_for_webtool.json"), "rb"
#     ) as handle:
#         profiles_for_webtool = json.load(handle)

#     assert isinstance(
#         profiles_for_webtool["ElectricityMeter - ElectricityToGrid [Electricity - Wh]"]["2021-01-01T00:00:00.000"],
#         Number,
#     )


from typing import Any

import pytest

from hisim.postprocessing.webtool_entries import WebtoolDict


def _make_webtool_dict(component_names: list[str]) -> WebtoolDict:
    """Create a WebtoolDict with a minimal ``components`` dict, bypassing the heavy ``__init__``.

    Only ``components`` is populated; ``kpis`` is left unset because
    ``add_opex_capex_results`` does not access it.
    """
    wd: WebtoolDict = WebtoolDict.__new__(WebtoolDict)
    wd.components = {
        name: {"economics": {}, "operation": {}, "configuration": {}}
        for name in component_names
    }
    return wd


def test_add_opex_capex_results_splits_on_in_format() -> None:
    """Keys using 'Name in Unit' are split on ' in ' into name and unit for both OPEX and CAPEX."""
    wd = _make_webtool_dict(["MyComponent"])
    computed_opex: list[list[Any]] = [
        ["Component", "Consumption in kWh", "Costs in EUR", "Maintenance in EUR"],
        ["MyComponent", 100.0, 50.0, 10.0],
    ]
    computed_capex: list[list[Any]] = [
        ["Component", "Investment in EUR", "Subsidy in EUR", "Lifetime in a"],
        ["MyComponent", 1000.0, 200.0, 20.0],
    ]
    wd.add_opex_capex_results(computed_opex, computed_capex)

    economics = wd.components["MyComponent"]["economics"]
    operation = wd.components["MyComponent"]["operation"]
    # OPEX results: categories are ["economics", "operation", "economics"]
    assert economics["Consumption"].name == "Consumption"
    assert economics["Consumption"].unit == "kWh"
    assert economics["Consumption"].value == 100.0
    assert operation["Costs"].name == "Costs"
    assert operation["Costs"].unit == "EUR"
    assert operation["Costs"].value == 50.0
    assert economics["Maintenance"].unit == "EUR"
    assert economics["Maintenance"].value == 10.0
    # CAPEX results
    assert economics["Investment"].name == "Investment"
    assert economics["Investment"].unit == "EUR"
    assert economics["Investment"].value == 1000.0
    assert operation["Subsidy"].unit == "EUR"
    assert operation["Subsidy"].value == 200.0
    assert economics["Lifetime"].unit == "a"
    assert economics["Lifetime"].value == 20.0


def test_add_opex_capex_results_bracket_format_fallback() -> None:
    """Keys using 'Name [Unit]' fall back to regex extraction when ' in ' split fails."""
    wd = _make_webtool_dict(["MyComponent"])
    computed_opex: list[list[Any]] = [
        ["Component", "Consumption [kWh]", "Costs [EUR]", "Maintenance [EUR]"],
        ["MyComponent", 100.0, 50.0, 10.0],
    ]
    computed_capex: list[list[Any]] = [["Component", "Investment [EUR]", "Subsidy [EUR]", "Lifetime [a]"]]
    wd.add_opex_capex_results(computed_opex, computed_capex)

    economics = wd.components["MyComponent"]["economics"]
    operation = wd.components["MyComponent"]["operation"]
    assert economics["Consumption"].name == "Consumption"
    assert economics["Consumption"].unit == "kWh"
    assert operation["Costs"].name == "Costs"
    assert operation["Costs"].unit == "EUR"
    assert economics["Maintenance"].unit == "EUR"


def test_add_opex_capex_results_unparseable_key_raises_value_error() -> None:
    """A key with neither ' in ' nor '[Unit]' format raises ValueError with a clear message."""
    wd = _make_webtool_dict(["MyComponent"])
    computed_opex: list[list[Any]] = [
        ["Component", "NoUnitHere", "Costs in EUR", "Maintenance in EUR"],
        ["MyComponent", 100.0, 50.0, 10.0],
    ]
    computed_capex: list[list[Any]] = [["Component", "Investment in EUR", "Subsidy in EUR", "Lifetime in a"]]
    with pytest.raises(ValueError, match="cannot be reformatted"):
        wd.add_opex_capex_results(computed_opex, computed_capex)


def test_add_opex_capex_results_multiple_in_raises_value_error() -> None:
    """A key with multiple ' in ' separators raises ValueError (too many values to unpack)."""
    wd = _make_webtool_dict(["MyComponent"])
    computed_opex: list[list[Any]] = [
        ["Component", "Price in EUR in total", "Costs in EUR", "Maintenance in EUR"],
        ["MyComponent", 100.0, 50.0, 10.0],
    ]
    computed_capex: list[list[Any]] = [["Component", "Investment in EUR", "Subsidy in EUR", "Lifetime in a"]]
    with pytest.raises(ValueError, match="cannot be reformatted"):
        wd.add_opex_capex_results(computed_opex, computed_capex)
