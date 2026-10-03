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
from typing import Any, Dict, Optional, Sequence

import pandas as pd
import pytest

from hisim import log
from hisim.config import ComponentID, DisplayConfig
from hisim.postprocessing.kpi_computation.kpi_address import KpiAddress
from hisim.postprocessing.kpi_computation.kpi_structure import KpiEntry, KpiSource, KpiTagEnumClass
from hisim.postprocessing.postprocessing_datatransfer import PostProcessingDataTransfer
from hisim.postprocessing.postprocessing_main import PostProcessor
from hisim.postprocessingoptions import PostProcessingOptions
from hisim.simulationparameters import SimulationParameters


BUILDING_OBJECT = "BUI1"
CONDITIONED_FLOOR_AREA_IN_M2 = 120.0
TOTAL_COSTS_IN_EURO = 2400.0

#: Every KPI the writer reads apart from the building's own ones, each with a distinct value
#: so a wrongly wired field shows up as a wrong number rather than as a coincidence. These are
#: derived KPIs: keyed by their bare name, without a source.
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
}

#: The electricity meter's KPIs the writer reads: component KPIs, keyed with their source.
METER_KPIS: Dict[str, float] = {
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


def _entries(
    values: Dict[str, float], tag: KpiTagEnumClass, component: Optional[ComponentID] = None
) -> Dict[str, Any]:
    """One tag's entries as the sorted collection holds them, keyed by their address.

    Args:
        values: KPI name -> value.
        tag: The tag they are filed under.
        component: The component they are reported for; ``None`` for derived KPIs.
    """
    source = None if component is None else KpiSource.for_component(component, DisplayConfig())
    entries: Dict[str, Any] = {}
    for name, value in values.items():
        entry = KpiEntry(
            name=name,
            unit="-",
            value=value,
            tag=tag,
            source=source,
            name_of_source_component=None if source is None else source.name,
        )
        entries[KpiAddress.key_for(name, source)] = entry.to_dict()
    return entries


def _kpi_collection(with_building: bool, meters: Sequence[str] = ("ElectricityMeter",)) -> Dict[str, Any]:
    """One building's tag-sorted collection: the derived KPIs, the meters' and optionally the Building's.

    Args:
        with_building: Whether a Building component reported its own KPIs.
        meters: The electricity meters of the building, each reporting :data:`METER_KPIS`.
    """
    collection: Dict[str, Any] = {
        KpiTagEnumClass.GENERAL.value: _entries(COST_AND_ENERGY_KPIS, KpiTagEnumClass.GENERAL)
    }
    meter_entries: Dict[str, Any] = {}
    for meter in meters:
        meter_entries.update(_entries(METER_KPIS, KpiTagEnumClass.ELECTRICITY_METER, ComponentID(meter)))
    collection[KpiTagEnumClass.ELECTRICITY_METER.value] = meter_entries
    if with_building:
        collection[KpiTagEnumClass.BUILDING.value] = _entries(
            BUILDING_KPIS, KpiTagEnumClass.BUILDING, ComponentID("Building")
        )
    return collection


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
    ppdt = _data_transfer(tmp_path, _kpi_collection(with_building=False))

    PostProcessor().write_kpis_to_json_for_building_sizer(ppdt, [BUILDING_OBJECT])

    assert not list(tmp_path.glob("*_kpi_config_for_building_sizer.json"))
    printed = capsys.readouterr().out
    assert "Skipping the building-sizer KPI JSON for BUI1" in printed
    assert "no Building component" in printed


@pytest.mark.base
@pytest.mark.usefixtures("log_into_tmp_path")
def test_a_run_with_a_building_still_writes_the_sizer_json(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """The guard must not skip a building object whose Building did compute its KPIs."""
    ppdt = _data_transfer(tmp_path, _kpi_collection(with_building=True))

    PostProcessor().write_kpis_to_json_for_building_sizer(ppdt, [BUILDING_OBJECT])

    written = list(tmp_path.glob("*_kpi_config_for_building_sizer.json"))
    assert [path.name for path in written] == [f"{BUILDING_OBJECT}_kpi_config_for_building_sizer.json"]
    kpi_config = json.loads(written[0].read_text(encoding="utf-8"))
    assert kpi_config["annualized_total_costs_in_euro_per_m2"] == TOTAL_COSTS_IN_EURO / CONDITIONED_FLOOR_AREA_IN_M2
    assert kpi_config["minimum_indoor_temperature_in_celsius"] == BUILDING_KPIS[
        "Minimum building indoor air temperature reached"
    ]
    assert kpi_config["annualized_electricity_to_grid_in_kwh_per_m2"] == (
        METER_KPIS["Total energy to grid"] / CONDITIONED_FLOOR_AREA_IN_M2
    )
    assert "Skipping the building-sizer KPI JSON" not in capsys.readouterr().out


@pytest.mark.base
@pytest.mark.usefixtures("log_into_tmp_path")
def test_two_entries_of_one_name_fail_by_name_rather_than_one_standing_for_both(tmp_path: Path) -> None:
    """Two electricity meters both report "Total energy to grid"; the writer must not pick one.

    The lookup used to match whole key strings and let the last match win, so a second meter
    silently replaced the first meter's grid export in the sizer's input. It now resolves the
    entry by name and refuses an ambiguous one, naming both candidates.
    """
    ppdt = _data_transfer(tmp_path, _kpi_collection(with_building=True, meters=("MeterA", "MeterB")))

    with pytest.raises(ValueError, match=r"Total energy to grid \(MeterA\)[\s\S]*Total energy to grid \(MeterB\)"):
        PostProcessor().write_kpis_to_json_for_building_sizer(ppdt, [BUILDING_OBJECT])
    assert not list(tmp_path.glob("*_kpi_config_for_building_sizer.json"))
