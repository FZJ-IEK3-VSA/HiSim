"""Tests for the building-sizer KPI JSON writer.

The writer normalizes almost every field it exports by the "Conditioned floor area" KPI,
which only a ``Building`` component produces. These tests pin the two halves of that rule:
a run without a Building is skipped with a log line and leaves no file behind, while a run
with a Building still writes the file with the values the sizer expects.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any, Dict

import pandas as pd
import pytest

from hisim import log
from hisim.postprocessing.postprocessing_datatransfer import PostProcessingDataTransfer
from hisim.postprocessing.postprocessing_main import PostProcessor
from hisim.postprocessingoptions import PostProcessingOptions
from hisim.simulationparameters import SimulationParameters


BUILDING_OBJECT = "BUI1"
CONDITIONED_FLOOR_AREA_IN_M2 = 120.0
TOTAL_COSTS_IN_EURO = 2400.0

#: Every KPI the writer reads apart from the building's own ones, each with a distinct value
#: so a wrongly wired field shows up as a wrong number rather than as a coincidence.
COST_AND_ENERGY_KPIS: Dict[str, float] = {
    "Total costs for simulated period": TOTAL_COSTS_IN_EURO,
    "Investment costs for equipment per simulated period": 1000.0,
    "Investment costs for equipment per simulated period minus subsidies": 900.0,
    "Investment costs upfront for equipment period minus subsidies": 8000.0,
    "Energy grid costs for simulated period": 700.0,
    "Costs of grid electricity for simulated period": 400.0,
    "Costs of grid gas for simulated period": 200.0,
    "Costs of other heating fuels for simulated period": 100.0,
    "Maintenance costs for simulated period": 60.0,
    "Total CO2 emissions for simulated period": 1200.0,
    "CO2 footprint for equipment per simulated period": 300.0,
    "CO2 footprint of grid electricity for simulated period": 500.0,
    "CO2 footprint of grid gas for simulated period": 250.0,
    "CO2 footprint of other heating fuels for simulated period": 150.0,
    "Self-sufficiency rate according to solar htw berlin": 45.0,
    "Total energy self-suffiency rate": 35.0,
    "Purchased energy consumption for simulated period": 9000.0,
    "Total energy to grid": 2000.0,
    "Total energy from grid": 5000.0,
}

#: The KPIs that only a ``Building`` component computes.
BUILDING_KPIS: Dict[str, float] = {
    "Conditioned floor area": CONDITIONED_FLOOR_AREA_IN_M2,
    "Minimum building indoor air temperature reached": 18.5,
    "Maximum building indoor air temperature reached": 26.5,
    "Temperature deviation of building indoor air temperature being below set temperature 20.0 Celsius": 12.0,
    "Temperature deviation of building indoor air temperature being above set temperature 25.0 Celsius": 3.0,
}


def _kpi_collection(values: Dict[str, float]) -> Dict[str, Any]:
    """Wrap plain numbers in the ``{"value": ...}`` shape the KPI collection uses."""
    return {name: {"value": value} for name, value in values.items()}


def _data_transfer(result_directory: Path, kpi_collection: Dict[str, Any]) -> PostProcessingDataTransfer:
    """Build the smallest data transfer object the sizer writer reads from."""
    simulation_parameters = SimulationParameters(
        start_date=datetime.datetime(2021, 1, 1),
        end_date=datetime.datetime(2021, 1, 2),
        seconds_per_timestep=60,
        result_directory=str(result_directory),
        post_processing_options=[PostProcessingOptions.COMPUTE_KPIS],
    )
    return PostProcessingDataTransfer(
        results=pd.DataFrame(),
        all_outputs=[],
        simulation_parameters=simulation_parameters,
        wrapped_components=[],
        mode=1,
        setup_function="setup_function",
        module_filename="test_building_sizer_kpi_writer",
        module_config=None,
        execution_time_in_s=0.0,
        results_monthly=None,
        results_hourly=None,
        results_cumulative=None,
        results_daily=None,
        kpi_collection_dict={BUILDING_OBJECT: kpi_collection},
    )


@pytest.fixture(name="log_into_tmp_path")
def fixture_log_into_tmp_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the log file HiSim writes alongside its printed lines inside the test's directory."""
    monkeypatch.setattr(log.logger, "logging_path", str(tmp_path))


@pytest.mark.base
@pytest.mark.usefixtures("log_into_tmp_path")
def test_a_run_without_a_building_writes_no_sizer_json_and_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """A KPI collection without the building's own floor area is skipped, not crashed on."""
    ppdt = _data_transfer(tmp_path, _kpi_collection(COST_AND_ENERGY_KPIS))

    PostProcessor().write_kpis_to_json_for_building_sizer(ppdt, [BUILDING_OBJECT])

    assert not list(tmp_path.glob("*_kpi_config_for_building_sizer.json"))
    printed = capsys.readouterr().out
    assert "Skipping the building-sizer KPI JSON for BUI1" in printed
    assert "no Building component" in printed


@pytest.mark.base
@pytest.mark.usefixtures("log_into_tmp_path")
def test_a_run_with_a_building_still_writes_the_sizer_json(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """The guard must not skip a building object whose Building did compute its KPIs."""
    ppdt = _data_transfer(tmp_path, _kpi_collection({**COST_AND_ENERGY_KPIS, **BUILDING_KPIS}))

    PostProcessor().write_kpis_to_json_for_building_sizer(ppdt, [BUILDING_OBJECT])

    written = list(tmp_path.glob("*_kpi_config_for_building_sizer.json"))
    assert [path.name for path in written] == [f"{BUILDING_OBJECT}_kpi_config_for_building_sizer.json"]
    kpi_config = json.loads(written[0].read_text(encoding="utf-8"))
    assert kpi_config["annualized_total_costs_in_euro_per_m2"] == TOTAL_COSTS_IN_EURO / CONDITIONED_FLOOR_AREA_IN_M2
    assert kpi_config["minimum_indoor_temperature_in_celsius"] == BUILDING_KPIS[
        "Minimum building indoor air temperature reached"
    ]
    assert "Skipping the building-sizer KPI JSON" not in capsys.readouterr().out
