"""Tests for the household building-sizer system setups.

Each setup is run for a single day with the post-processing options the building
sizer relies on (OpEx/CapEx/KPI computation and writing the KPIs to JSON). Beyond
confirming that the simulation does not raise, every test also asserts that the
run actually produced its result artefacts in the result directory: the
``finished.flag`` completion marker and the ``all_kpis.json`` file written by
``PostProcessingOptions.WRITE_KPIS_TO_JSON``.  The ``all_kpis.json`` is then
loaded and checked to contain finite numeric KPI values, including the
``Total electricity consumption`` KPI that every household setup produces, so the
tests verify real post-processing output rather than only that the run did not
crash.

Two test families in this file run no simulation at all. Every building-sizer setup
refuses a zero rooftop photovoltaic share while the energy manager is switched on, and
every one of them refuses an LPG household name the profile registry does not define
instead of quietly dropping it. Both refusals are raised before any component is built,
so each is checked by initializing every setup once and catching the error.
"""
# clean
import json
import math
import os
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from hisim import hisim_main
from hisim import log
from hisim import utils
from hisim.building_sizer_utils.interface_configs.modular_household_config import (
    ModularHouseholdConfig,
    read_in_configs,
)
from hisim.postprocessingoptions import PostProcessingOptions
from hisim.simulationparameters import SimulationParameters
from tests.testing_utils import TestingUtils


def _make_building_sizer_simulation_parameters(result_directory: str) -> SimulationParameters:
    """Build one-day simulation parameters with the building-sizer post-processing options.

    The option set matches what the building sizer expects: scenario-evaluation
    outputs, OpEx/CapEx/KPI computation and writing the KPIs to JSON. Results are
    written to ``result_directory`` so each test can verify its own artefacts in
    isolation.
    """
    my_simulation_parameters = SimulationParameters.one_day_only(
        year=2024, seconds_per_timestep=60 * 15
    )
    my_simulation_parameters.result_directory = result_directory
    my_simulation_parameters.post_processing_options.append(
        PostProcessingOptions.PREPARE_OUTPUTS_FOR_SCENARIO_EVALUATION
    )
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_OPEX)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_CAPEX)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_KPIS)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.WRITE_KPIS_TO_JSON)
    return my_simulation_parameters


def _assert_kpi_artefacts_written(result_directory: str, path: str) -> None:
    """Assert the run produced its result artefacts and valid KPI content.

    Beyond confirming that the ``finished.flag`` completion marker and the
    ``all_kpis.json`` file exist, this loads the KPI JSON written by
    ``PostProcessingOptions.WRITE_KPIS_TO_JSON`` (see
    ``PostProcessor.write_kpis_to_json_file`` in
    ``hisim/postprocessing/postprocessing_main.py``) and verifies that it holds
    finite numeric KPI values, including the ``Total electricity consumption``
    KPI that every household setup produces (created in
    ``KpiPreparation.compute_electricity_consumption_and_production_and_battery_kpis``
    with the tag ``"General"``).

    The ``all_kpis.json`` structure has been verified by code tracing of
    ``PostProcessor.write_kpis_to_json_file`` and
    ``KpiGenerator.sort_kpi_collection_according_to_kpi_tags`` (the tests are
    marked ``@pytest.mark.system_setups`` and are not part of the fast ``base``
    gate, so they are not executed in the sandbox).  It is serialised as::

        {building_object: {kpi_tag: {kpi_name: {name, unit, value, ...}}}}

    where ``building_object`` is the building name (e.g. ``"BUI1"``), ``kpi_tag``
    is a tag string such as ``"General"`` or ``"Costs"``, and each KPI entry is a
    ``KpiEntry.to_dict()`` mapping with the keys ``name``, ``unit``, ``value``,
    ``description``, ``tag`` and ``nameOfSourceComponent``.  The structure is
    built by ``KpiGenerator.sort_kpi_collection_according_to_kpi_tags`` in
    ``hisim/postprocessing/kpi_computation/compute_kpis.py``.

    This turns the test from a mere "does not crash" check into a meaningful
    smoke test of the full simulation and KPI post-processing pipeline.
    """
    results_dir = Path(result_directory)
    assert results_dir.is_dir(), f"results directory was not created for {path}"
    assert (results_dir / "finished.flag").is_file(), (
        f"simulation did not write finished.flag for {path}"
    )
    # Load the KPI JSON written by WRITE_KPIS_TO_JSON and verify it contains
    # finite numeric KPI values.  The explicit all_kpis.json check below is
    # stricter than a generic "*kpi*.json" glob (it pins the exact file name
    # produced by PostProcessor.write_kpis_to_json_file), so no separate glob
    # assertion is needed.
    all_kpis_path = results_dir / "all_kpis.json"
    assert all_kpis_path.is_file(), f"all_kpis.json was not written for {path}"
    with all_kpis_path.open("r", encoding="utf-8") as kpi_file:
        kpi_data = json.load(kpi_file)
    assert isinstance(kpi_data, dict) and len(kpi_data) > 0, (
        f"all_kpis.json is empty or not a JSON object for {path}"
    )

    finite_value_count = 0
    all_kpi_names: list[str] = []
    found_total_consumption = False
    # Track entries that do not match the expected dict structure so the
    # assertion message can report them if no finite values are found.
    skipped_entry_count = 0
    for building_object, tag_dict in kpi_data.items():
        assert isinstance(tag_dict, dict), (
            f"expected a tag dict for building {building_object!r} "
            f"in all_kpis.json for {path}"
        )
        for kpi_entries in tag_dict.values():
            if not isinstance(kpi_entries, dict):
                skipped_entry_count += 1
                continue
            for kpi_entry in kpi_entries.values():
                if not isinstance(kpi_entry, dict):
                    skipped_entry_count += 1
                    continue
                name = kpi_entry.get("name")
                if isinstance(name, str):
                    all_kpi_names.append(name)
                value = kpi_entry.get("value")
                # Skip non-numeric values (e.g. None for KPIs that do not apply
                # to a given setup); bool is a subclass of int, so exclude it
                # explicitly.
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    continue
                assert math.isfinite(value), (
                    f"non-finite KPI value {value!r} for "
                    f"{name!r} in all_kpis.json for {path}"
                )
                finite_value_count += 1
                if name == "Total electricity consumption":
                    found_total_consumption = True
    assert finite_value_count > 0, (
        f"no finite numeric KPI values found in all_kpis.json for {path}; "
        f"top-level keys: {list(kpi_data.keys())}; "
        f"skipped {skipped_entry_count} non-dict entries"
    )
    assert found_total_consumption, (
        f"'Total electricity consumption' KPI (with a finite numeric value) was not "
        f"found in all_kpis.json for {path}. "
        f"KPI names present: {all_kpi_names}"
    )
    # Only presence and finiteness are asserted here.  A physical range check
    # (e.g. non-negativity) is deliberately deferred: "Total electricity
    # consumption" is the consumption with battery round-trip losses included
    # (charging - discharging, see KpiPreparation.compute_battery_kpis), which
    # can in principle reduce the value, so a hard bound needs human-confirmed
    # expected ranges per setup.


@pytest.fixture(name="building_sizer_result_directory")
def fixture_building_sizer_result_directory() -> Iterator[str]:
    """Yield a fresh, isolated result directory for a building-sizer test.

    Uses :func:`TestingUtils.get_result_directory` so each test gets its own
    ``results/test/<test_name>`` directory (deterministic, git-ignored and inside
    the project's results root). The directory is removed again on teardown so no
    artefacts are left behind even when a test fails.
    """
    result_directory = TestingUtils.get_result_directory()
    if Path(result_directory).is_dir():
        shutil.rmtree(result_directory)
    Path(result_directory).mkdir(parents=True, exist_ok=True)
    try:
        yield result_directory
    finally:
        shutil.rmtree(result_directory, ignore_errors=True)


@pytest.mark.system_setups
@utils.measure_execution_time
def test_household_gas(building_sizer_result_directory: str) -> None:
    """Test household gas building sizer setup for a single day."""
    path = "../system_setups/household_gas_building_sizer.py"
    hisim_main.main(path, _make_building_sizer_simulation_parameters(building_sizer_result_directory))
    log.information(os.getcwd())
    _assert_kpi_artefacts_written(building_sizer_result_directory, path)


@pytest.mark.system_setups
@utils.measure_execution_time
def test_household_oil(building_sizer_result_directory: str) -> None:
    """Test household oil building sizer setup for a single day."""
    path = "../system_setups/household_oil_building_sizer.py"
    hisim_main.main(path, _make_building_sizer_simulation_parameters(building_sizer_result_directory))
    log.information(os.getcwd())
    _assert_kpi_artefacts_written(building_sizer_result_directory, path)


@pytest.mark.system_setups
@utils.measure_execution_time
def test_household_heatpump(building_sizer_result_directory: str) -> None:
    """Test household heat pump building sizer setup for a single day."""
    path = "../system_setups/household_heatpump_building_sizer.py"
    hisim_main.main(path, _make_building_sizer_simulation_parameters(building_sizer_result_directory))
    log.information(os.getcwd())
    _assert_kpi_artefacts_written(building_sizer_result_directory, path)


@pytest.mark.system_setups
@utils.measure_execution_time
def test_household_pellet_heating(building_sizer_result_directory: str) -> None:
    """Test household pellet heating building sizer setup for a single day."""
    path = "../system_setups/household_pellets_building_sizer.py"
    hisim_main.main(path, _make_building_sizer_simulation_parameters(building_sizer_result_directory))
    log.information(os.getcwd())
    _assert_kpi_artefacts_written(building_sizer_result_directory, path)


@pytest.mark.system_setups
@utils.measure_execution_time
def test_household_district_heating(building_sizer_result_directory: str) -> None:
    """Test household district heating building sizer setup for a single day."""
    path = "../system_setups/household_district_heating_building_sizer.py"
    hisim_main.main(path, _make_building_sizer_simulation_parameters(building_sizer_result_directory))
    log.information(os.getcwd())
    _assert_kpi_artefacts_written(building_sizer_result_directory, path)


@pytest.mark.system_setups
@utils.measure_execution_time
def test_household_wood_chips_heating(building_sizer_result_directory: str) -> None:
    """Test household wood chips heating building sizer setup for a single day."""
    path = "../system_setups/household_wood_chips_building_sizer.py"
    hisim_main.main(path, _make_building_sizer_simulation_parameters(building_sizer_result_directory))
    log.information(os.getcwd())
    _assert_kpi_artefacts_written(building_sizer_result_directory, path)


@pytest.mark.system_setups
@utils.measure_execution_time
def test_household_hydrogen_heating(building_sizer_result_directory: str) -> None:
    """Test household hydrogen boiler building sizer setup for a single day."""
    path = "../system_setups/household_hydrogen_boiler_building_sizer.py"
    hisim_main.main(path, _make_building_sizer_simulation_parameters(building_sizer_result_directory))
    log.information(os.getcwd())
    _assert_kpi_artefacts_written(building_sizer_result_directory, path)


@pytest.mark.system_setups
@utils.measure_execution_time
def test_household_electric_heating(building_sizer_result_directory: str) -> None:
    """Test household electric heating building sizer setup for a single day."""
    path = "../system_setups/household_electric_heating_building_sizer.py"
    hisim_main.main(path, _make_building_sizer_simulation_parameters(building_sizer_result_directory))
    log.information(os.getcwd())
    _assert_kpi_artefacts_written(building_sizer_result_directory, path)


@pytest.mark.system_setups
@utils.measure_execution_time
def test_household_gas_solar_thermal_heating(building_sizer_result_directory: str) -> None:
    """Test household gas with solar thermal building sizer setup for a single day."""
    path = "../system_setups/household_gas_solar_thermal_building_sizer.py"
    hisim_main.main(path, _make_building_sizer_simulation_parameters(building_sizer_result_directory))
    log.information(os.getcwd())
    _assert_kpi_artefacts_written(building_sizer_result_directory, path)


@pytest.mark.system_setups
@utils.measure_execution_time
def test_household_heatpump_solar_thermal_heating(building_sizer_result_directory: str) -> None:
    """Test household heat pump with solar thermal building sizer setup for a single day."""
    path = "../system_setups/household_heatpump_solar_thermal_building_sizer.py"
    hisim_main.main(path, _make_building_sizer_simulation_parameters(building_sizer_result_directory))
    log.information(os.getcwd())
    _assert_kpi_artefacts_written(building_sizer_result_directory, path)


@pytest.mark.system_setups
@utils.measure_execution_time
def test_household_heatpump_car(building_sizer_result_directory: str) -> None:
    """Test household heat pump with EV building sizer setup for a single day."""
    path = "../system_setups/household_heatpump_car_building_sizer.py"
    hisim_main.main(path, _make_building_sizer_simulation_parameters(building_sizer_result_directory))
    log.information(os.getcwd())
    _assert_kpi_artefacts_written(building_sizer_result_directory, path)


#: The eleven building-sizer setups paired with the ``ModularHouseholdConfig`` factory whose
#: heating system each one accepts. Every setup refuses a heating system other than its own before
#: it reaches anything else, so a test that wants to drive a setup past that check has to start
#: from this setup's own default configuration. Shared by the two configuration-refusal tests
#: below so a new setup is added to both at once.
BUILDING_SIZER_SETUPS_AND_DEFAULT_CONFIGS: list[tuple[str, str]] = [
    ("household_district_heating_building_sizer.py", "get_default_config_for_household_district_heating"),
    ("household_electric_heating_building_sizer.py", "get_default_config_for_household_electric_heating"),
    ("household_gas_building_sizer.py", "get_default_config_for_household_gas"),
    ("household_gas_solar_thermal_building_sizer.py", "get_default_config_for_household_gas_solar_thermal"),
    ("household_heatpump_building_sizer.py", "get_default_config_for_household_heatpump"),
    ("household_heatpump_car_building_sizer.py", "get_default_config_for_household_heatpump"),
    (
        "household_heatpump_solar_thermal_building_sizer.py",
        "get_default_config_for_household_heatpump_solar_thermal",
    ),
    ("household_hydrogen_boiler_building_sizer.py", "get_default_config_for_household_hydrogen"),
    ("household_oil_building_sizer.py", "get_default_config_for_household_oil"),
    ("household_pellets_building_sizer.py", "get_default_config_for_household_pellet"),
    ("household_wood_chips_building_sizer.py", "get_default_config_for_household_wood_chips"),
]


@pytest.mark.base
@pytest.mark.parametrize(
    "setup_file_name, default_config_getter_name",
    BUILDING_SIZER_SETUPS_AND_DEFAULT_CONFIGS,
    ids=[setup_file_name for setup_file_name, _ in BUILDING_SIZER_SETUPS_AND_DEFAULT_CONFIGS],
)
def test_building_sizer_setup_refuses_zero_pv_share_with_energy_manager(
    setup_file_name: str, default_config_getter_name: str, tmp_path: Path
) -> None:
    """Test that a zero rooftop share combined with the energy manager is refused up front.

    ``share_of_maximum_pv_potential = 0`` used to switch off the battery and the energy management
    system as a side effect, so the run quietly became the metered household while the module
    configuration still asked for the managed one. Every building-sizer setup now refuses the
    combination before it builds a single component, which is why this test needs no simulation: for
    each setup it takes that setup's own default ``ModularHouseholdConfig`` (named by
    ``default_config_getter_name``), sets the zero share and leaves the manager on, hands the written
    file to ``initialize_from_python`` and asserts that the refusal arrives and names both fields the
    caller has to decide between.
    """
    household_config = getattr(ModularHouseholdConfig, default_config_getter_name)()
    assert household_config.energy_system_config_ is not None
    household_config.energy_system_config_.share_of_maximum_pv_potential = 0.0
    household_config.energy_system_config_.use_battery_and_ems = True
    config_path = tmp_path / "zero_pv_share_with_ems.json"
    config_path.write_text(json.dumps(household_config.to_dict()), encoding="utf8")

    # read_in_configs now refuses a config it cannot read instead of answering None, but a config it
    # reads only partially would still leave the setup with different values than this test wrote.
    # Reading the file back confirms the two fields under test really reach the setup.
    written_config = read_in_configs(str(config_path))
    assert written_config is not None and written_config.energy_system_config_ is not None
    assert written_config.energy_system_config_.share_of_maximum_pv_potential == 0.0
    assert written_config.energy_system_config_.use_battery_and_ems is True

    setup_path = Path(__file__).resolve().parents[1] / "system_setups" / setup_file_name
    with pytest.raises(ValueError) as refusal:
        hisim_main.initialize_from_python(
            str(setup_path),
            my_simulation_parameters=SimulationParameters.one_day_only(2021, 60 * 15),
            my_module_config=str(config_path),
        )
    message = str(refusal.value)
    assert "share_of_maximum_pv_potential" in message
    assert "use_battery_and_ems" in message


@pytest.mark.base
@pytest.mark.parametrize(
    "setup_file_name, default_config_getter_name",
    BUILDING_SIZER_SETUPS_AND_DEFAULT_CONFIGS,
    ids=[setup_file_name for setup_file_name, _ in BUILDING_SIZER_SETUPS_AND_DEFAULT_CONFIGS],
)
def test_building_sizer_setup_refuses_an_unknown_lpg_household(
    setup_file_name: str, default_config_getter_name: str, tmp_path: Path
) -> None:
    """Test that a misspelled LPG household name is refused instead of quietly dropped.

    The occupancy block of every building-sizer setup used to skip any name the LPG household
    registry did not define, so a config asking for two households and misspelling one of them
    built a one-household building without a word about it. The name is now resolved by
    ``ArcheTypeConfig.resolve_lpg_households`` for all eleven setups, which refuses the unknown
    name. The refusal is raised before any component is built, so this test needs no simulation:
    it takes each setup's own default config (its heating system is checked first, and a foreign
    one would be refused before the households are read), adds a misspelled household beside a
    real one, and asserts the refusal names the typo and the registry the legal names live in.
    """
    household_config = getattr(ModularHouseholdConfig, default_config_getter_name)()
    assert household_config.archetype_config_ is not None
    household_config.archetype_config_.lpg_households = [
        "CHR01_Couple_both_at_Work",
        "CHR01_Couple_both_at_Wrok",
    ]
    config_path = tmp_path / "unknown_lpg_household.json"
    config_path.write_text(json.dumps(household_config.to_dict()), encoding="utf8")

    setup_path = Path(__file__).resolve().parents[1] / "system_setups" / setup_file_name
    with pytest.raises(ValueError) as refusal:
        hisim_main.initialize_from_python(
            str(setup_path),
            my_simulation_parameters=SimulationParameters.one_day_only(2021, 60 * 15),
            my_module_config=str(config_path),
        )
    message = str(refusal.value)
    assert "CHR01_Couple_both_at_Wrok" in message
    assert "utspclient.helpers.lpgdata.Households" in message
