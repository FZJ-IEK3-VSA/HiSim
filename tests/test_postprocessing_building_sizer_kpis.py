"""Tests for the building-sizer KPI export in the post processor.

``PostProcessor.write_kpis_to_json_for_building_sizer`` looks up KPIs by name. Two of
those names are not constant: the building writes its indoor-temperature-deviation KPIs
as ``... below/above set temperature {set_temperature} Celsius`` (see
``Building.get_building_temperature_deviation_from_set_temperatures`` in
``hisim/components/building.py``), so the name depends on the building config. These
tests drive the export with a synthetic KPI collection instead of a full simulation and
assert that non-default set temperatures are still found.
"""
# clean
import json
import shutil
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Dict

import pytest

from hisim.postprocessing.postprocessing_main import PostProcessor
from hisim.postprocessingoptions import PostProcessingOptions
from tests.testing_utils import TestingUtils


# KPIs the building-sizer export reads with a constant name. The values are arbitrary but
# non-zero, so a division by the conditioned floor area cannot mask a lookup failure.
_CONSTANT_KPI_VALUES: Dict[str, float] = {
    "Conditioned floor area": 120.0,
    "Total costs for simulated period": 5000.0,
    "Investment costs for equipment per simulated period": 3000.0,
    "Investment costs for equipment per simulated period minus subsidies": 2500.0,
    "Investment costs upfront for equipment period minus subsidies": 20000.0,
    "Energy grid costs for simulated period": 1200.0,
    "Costs of grid electricity for simulated period": 800.0,
    "Costs of grid gas for simulated period": 300.0,
    "Costs of other heating fuels for simulated period": 100.0,
    "Maintenance costs for simulated period": 200.0,
    "Total CO2 emissions for simulated period": 2000.0,
    "CO2 footprint for equipment per simulated period": 500.0,
    "CO2 footprint of grid electricity for simulated period": 900.0,
    "CO2 footprint of grid gas for simulated period": 400.0,
    "CO2 footprint of other heating fuels for simulated period": 200.0,
    "Self-sufficiency rate according to solar htw berlin": 35.0,
    "Total energy self-suffiency rate": 25.0,
    "Purchased energy consumption for simulated period": 9000.0,
    "Total energy to grid": 1500.0,
    "Total energy from grid": 4000.0,
    "Minimum building indoor air temperature reached": 18.5,
    "Maximum building indoor air temperature reached": 29.5,
}

_BELOW_SET_TEMPERATURE_PREFIX = (
    "Temperature deviation of building indoor air temperature being below set temperature"
)
_ABOVE_SET_TEMPERATURE_PREFIX = (
    "Temperature deviation of building indoor air temperature being above set temperature"
)


def _make_kpi_collection_dict(
    set_heating_temperature: str,
    set_cooling_temperature: str,
    deviation_below_in_celsius_hour: float,
    deviation_above_in_celsius_hour: float,
) -> Dict:
    """Build a KPI collection in the structure the post processor consumes.

    ``ppdt.kpi_collection_dict`` is ``{building_object: {kpi_tag: {kpi_name: entry}}}`` as
    produced by ``KpiGenerator.sort_kpi_collection_according_to_kpi_tags``. Only the
    ``value`` key of an entry is read by the building-sizer export.
    """
    general_kpis = {name: {"value": value} for name, value in _CONSTANT_KPI_VALUES.items()}
    building_kpis = {
        f"{_BELOW_SET_TEMPERATURE_PREFIX} {set_heating_temperature} Celsius": {
            "value": deviation_below_in_celsius_hour
        },
        f"{_ABOVE_SET_TEMPERATURE_PREFIX} {set_cooling_temperature} Celsius": {
            "value": deviation_above_in_celsius_hour
        },
    }
    return {"BUI1": {"General": general_kpis, "Building": building_kpis}}


def _make_ppdt(kpi_collection_dict: Dict, result_directory: str) -> SimpleNamespace:
    """Build the minimal post-processing data transfer surface the export touches."""
    return SimpleNamespace(
        post_processing_options=[
            PostProcessingOptions.COMPUTE_KPIS,
            PostProcessingOptions.WRITE_KPIS_TO_JSON_FOR_BUILDING_SIZER,
        ],
        kpi_collection_dict=kpi_collection_dict,
        simulation_parameters=SimpleNamespace(result_directory=result_directory),
    )


@pytest.fixture(name="result_directory")
def fixture_result_directory() -> Iterator[str]:
    """Yield a fresh, isolated result directory that is removed again on teardown."""
    result_directory = TestingUtils.get_result_directory()
    if Path(result_directory).is_dir():
        shutil.rmtree(result_directory)
    Path(result_directory).mkdir(parents=True, exist_ok=True)
    try:
        yield result_directory
    finally:
        shutil.rmtree(result_directory, ignore_errors=True)


def _write_and_load_kpi_config(
    set_heating_temperature: str,
    set_cooling_temperature: str,
    result_directory: str,
) -> Dict:
    """Run the building-sizer export and return the written KPI config."""
    ppdt = _make_ppdt(
        _make_kpi_collection_dict(
            set_heating_temperature=set_heating_temperature,
            set_cooling_temperature=set_cooling_temperature,
            deviation_below_in_celsius_hour=42.0,
            deviation_above_in_celsius_hour=7.0,
        ),
        result_directory,
    )
    PostProcessor().write_kpis_to_json_for_building_sizer(ppdt, ["BUI1"])  # type: ignore[arg-type]

    written_file = Path(result_directory) / "BUI1_kpi_config_for_building_sizer.json"
    assert written_file.is_file(), "the building sizer KPI config was not written"
    with written_file.open("r", encoding="utf-8") as config_file:
        return json.load(config_file)


@pytest.mark.base
def test_building_sizer_kpis_with_default_set_temperatures(result_directory: str) -> None:
    """The export works for a building with the default 20.0 / 25.0 set temperatures."""
    kpi_config = _write_and_load_kpi_config("20.0", "25.0", result_directory)

    assert kpi_config["deviation_from_min_indoor_temperature_in_celsius_hour"] == 42.0
    assert kpi_config["deviation_from_max_indoor_temperature_in_celsius_hour"] == 7.0


@pytest.mark.base
def test_building_sizer_kpis_with_custom_set_temperatures(result_directory: str) -> None:
    """The export works for set temperatures other than the defaults.

    The temperature deviation KPI names carry the configured set temperatures, so looking
    them up by an exact name that assumes 20.0 / 25.0 raises a ``KeyError`` for every
    building configured differently.
    """
    kpi_config = _write_and_load_kpi_config("21", "28", result_directory)

    assert kpi_config["deviation_from_min_indoor_temperature_in_celsius_hour"] == 42.0
    assert kpi_config["deviation_from_max_indoor_temperature_in_celsius_hour"] == 7.0


@pytest.mark.base
def test_building_sizer_kpis_raise_when_kpi_is_missing(result_directory: str) -> None:
    """A genuinely missing KPI is still reported instead of silently defaulting."""
    kpi_collection_dict = _make_kpi_collection_dict(
        set_heating_temperature="21",
        set_cooling_temperature="28",
        deviation_below_in_celsius_hour=42.0,
        deviation_above_in_celsius_hour=7.0,
    )
    del kpi_collection_dict["BUI1"]["General"]["Conditioned floor area"]
    ppdt = _make_ppdt(kpi_collection_dict, result_directory)

    with pytest.raises(KeyError, match="Conditioned floor area"):
        PostProcessor().write_kpis_to_json_for_building_sizer(ppdt, ["BUI1"])  # type: ignore[arg-type]
