"""Unit tests for the RenoVisor request -> setup + ModularHouseholdConfig mapping (spec section 4)."""

import copy
import json
from pathlib import Path

import pytest

from hisim.loadtypes import ComponentType, HeatingSystems
from hisim.renovisor.mapping import build_mapping_report_dict, translate
from hisim.renovisor.schema import RequestValidationError, parse_translator_input
from hisim.renovisor.tabula_ie import select_building_code

pytestmark = pytest.mark.base

EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "hisim" / "renovisor" / "examples"


def _load_example(name: str) -> dict:
    """Load one of the example request files shipped with the package."""
    data: dict = json.loads((EXAMPLES_DIR / name).read_text(encoding="utf-8"))
    return data


@pytest.fixture(name="example_gas")
def fixture_example_gas() -> dict:
    """Example 1: gas-heated 1968 detached house getting heat pump + PV + roof insulation."""
    return _load_example("example_1_gas_to_heatpump_pv_insulation.json")


def test_example_1_base_variant_maps_to_gas_setup(example_gas: dict) -> None:
    """The baseline of example 1 selects the gas setup and the unrenovated 1968 SFH archetype."""
    parsed = parse_translator_input(example_gas)
    result = translate(parsed.request, "base", parsed.job_id)

    assert result.setup_filename == "household_gas_building_sizer.py"
    energy_system = result.modular_household_config.energy_system_config_
    archetype = result.modular_household_config.archetype_config_
    assert energy_system is not None and archetype is not None
    assert energy_system.heating_system == HeatingSystems.GAS_HEATING
    assert energy_system.heat_distribution_system == ComponentType.HEAT_DISTRIBUTION_SYSTEM_RADIATOR
    assert energy_system.share_of_maximum_pv_potential == 0.0
    assert energy_system.use_battery_and_ems is False
    # 1968 detached, unrenovated -> band 05 (1967-1977) matches but lacks door geometry in
    # TABULA (would crash the Building component), so the nearest usable band 04 is used.
    assert archetype.building_code == "IE.N.SFH.04.Gen.ReEx.001.001"
    year_entry = next(e for e in result.report.to_list() if e["path"] == "homeInputs.constructionYear")
    assert year_entry["status"] == "approximated"
    assert "door/window geometry" in year_entry["note"]
    assert archetype.weather_location == "IE"
    assert archetype.conditioned_floor_area_in_m2 == 157
    assert archetype.lpg_households == ["CHR01_Couple_both_at_Work"]
    assert archetype.building_id == parsed.job_id
    assert archetype.pv_rooftop_capacity_in_kilowatt is None


def test_example_1_measures_variant_maps_to_heatpump_setup(example_gas: dict) -> None:
    """With measures applied, example 1 selects the heat-pump setup, PV 8 kWp and variant .002."""
    parsed = parse_translator_input(example_gas)
    result = translate(parsed.request, "measures", parsed.job_id)

    assert result.setup_filename == "household_heatpump_building_sizer.py"
    energy_system = result.modular_household_config.energy_system_config_
    archetype = result.modular_household_config.archetype_config_
    assert energy_system is not None and archetype is not None
    assert energy_system.heating_system == HeatingSystems.HEAT_PUMP
    assert energy_system.share_of_maximum_pv_potential == 1.0
    assert archetype.pv_rooftop_capacity_in_kilowatt == 8
    # One envelope measure type (roof_insulation) -> refurbishment variant floor .002;
    # band 04 because the matching band 05 lacks door geometry (see base-variant test).
    assert archetype.building_code == "IE.N.SFH.04.Gen.ReEx.001.002"


def test_mapping_report_covers_every_request_leaf(example_gas: dict) -> None:
    """Every leaf of the request appears in the report; statuses are from the allowed set."""
    parsed = parse_translator_input(example_gas)
    result = translate(parsed.request, "measures", parsed.job_id)
    report = build_mapping_report_dict(result, parsed.job_id, "measures")

    paths = {entry["path"] for entry in report["fields"]}
    assert {entry["status"] for entry in report["fields"]} <= {"used", "approximated", "defaulted", "ignored"}

    def covered(leaf: str) -> bool:
        return any(leaf == p or (leaf.startswith(p) and leaf[len(p)] in ".[") for p in paths)

    for leaf in [
        "contractVersion",
        "location.countryCode",
        "homeInputs.targetTempC",
        "homeInputs.roof.construction",
        "homeInputs.heating.seasonalEfficiencyPct",
        "homeInputs.vehicles[0].kmPerYear",
        "measures[0]",
        "measures[2]",
    ]:
        assert covered(leaf), f"request leaf '{leaf}' missing from mapping report"
    assert report["selectedSetup"] == "household_heatpump_building_sizer.py"
    assert report["moduleConfig"]["archetype_config_"]["building_code"].startswith("IE.N.SFH.04")


def test_solar_thermal_selects_dedicated_setup_or_is_dropped(example_gas: dict) -> None:
    """Gas + solar thermal uses the combined setup; oil + solar thermal drops solar thermal."""
    parsed = parse_translator_input(example_gas)
    request = copy.deepcopy(parsed.request)
    request["homeInputs"]["solarThermal"]["mode"] = "hot_water"
    combined = translate(request, "base", "job")
    assert combined.setup_filename == "household_gas_solar_thermal_building_sizer.py"

    request["homeInputs"]["heating"]["primary"] = "oil"
    dropped = translate(request, "base", "job")
    assert dropped.setup_filename == "household_oil_building_sizer.py"
    solar_entry = next(e for e in dropped.report.to_list() if e["path"] == "homeInputs.solarThermal.mode")
    assert solar_entry["status"] == "ignored"


@pytest.mark.parametrize(
    "primary,expected_setup",
    [
        ("wood", "household_pellets_building_sizer.py"),
        ("solid_fuel", "household_pellets_building_sizer.py"),
        ("direct_electric", "household_electric_heating_building_sizer.py"),
        ("district", "household_district_heating_building_sizer.py"),
    ],
    ids=["wood", "solid_fuel", "electric", "district"],
)
def test_setup_selection_table(example_gas: dict, primary: str, expected_setup: str) -> None:
    """The remaining heating primaries map to the setups from spec section 4.1."""
    parsed = parse_translator_input(example_gas)
    request = copy.deepcopy(parsed.request)
    request["homeInputs"]["heating"]["primary"] = primary
    assert translate(request, "base", "job").setup_filename == expected_setup


@pytest.mark.parametrize(
    "occupants,expected",
    [
        (1, "CHR07_Single_with_work"),
        (2, "CHR01_Couple_both_at_Work"),
        (3, "CHR03_Family_1_child_both_at_work"),
        (4, "CHR27_Family_both_at_work_2_children"),
        (7, "CHR41_Family_with_3_children_both_at_work"),
    ],
)
def test_occupancy_lookup_table(example_gas: dict, occupants: int, expected: str) -> None:
    """Occupant counts map to the fixed LPG household table (spec section 4.4)."""
    parsed = parse_translator_input(example_gas)
    request = copy.deepcopy(parsed.request)
    request["homeInputs"]["occupants"] = occupants
    result = translate(request, "base", "job")
    archetype = result.modular_household_config.archetype_config_
    assert archetype is not None and archetype.lpg_households == [expected]


def test_east_west_orientation_and_apartment_archetype(example_gas: dict) -> None:
    """east_west PV maps to azimuth 90 (flagged); apartments map to the AB archetype."""
    parsed = parse_translator_input(example_gas)
    request = copy.deepcopy(parsed.request)
    request["homeInputs"]["pv"] = {"kWp": 5, "orientation": "east_west"}
    request["homeInputs"]["dwellingType"] = "apartment"
    request["homeInputs"]["constructionYear"] = 1985
    result = translate(request, "base", "job")
    archetype = result.modular_household_config.archetype_config_
    assert archetype is not None
    assert archetype.pv_azimuth == 90.0
    assert archetype.building_code == "IE.N.AB.07.Gen.ReEx.001.001"
    orientation_entry = next(e for e in result.report.to_list() if e["path"] == "homeInputs.pv.orientation")
    assert orientation_entry["status"] == "approximated"


def test_tabula_fallbacks_for_missing_bands_and_variants() -> None:
    """Age-band gaps and unusable TABULA rows fall back to the nearest usable entry."""
    # Irish apartments have no band for 1900 -> nearest usable is band 04 (1950-1966).
    early_apartment = select_building_code("IE", "AB", 1900, 1)
    assert early_apartment.building_code == "IE.N.AB.04.Gen.ReEx.001.001"
    assert early_apartment.notes
    # IE.N.AB.08-10 all lack door geometry (unusable) -> nearest usable band is 07 (1983-1993).
    new_apartment = select_building_code("IE", "AB", 2015, 2)
    assert new_apartment.building_code == "IE.N.AB.07.Gen.ReEx.001.002"
    assert any("door/window geometry" in note for note in new_apartment.notes)
    # SFH band 05 exists for 1967-1977 but is unusable -> falls back to band 04.
    detached_1968 = select_building_code("IE", "SFH", 1968, 1)
    assert detached_1968.building_code == "IE.N.SFH.04.Gen.ReEx.001.001"
    assert any("door/window geometry" in note for note in detached_1968.notes)


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda data: data["job"].pop("jobId"), "job.jobId"),
        (lambda data: data["job"]["submission"].pop("url"), "job.submission.url"),
        (lambda data: data["request"].pop("measures"), "request.measures"),
        (lambda data: data["request"]["homeInputs"]["heating"].update(primary="fusion"), "heating.primary"),
        (lambda data: data["request"].update(contractVersion="2.0.0"), "contractVersion"),
        (
            lambda data: data["job"].update(simulationOverrides={"postProcessingOptions": ["NOT_AN_OPTION"]}),
            "NOT_AN_OPTION",
        ),
    ],
    ids=["job-id", "url", "measures", "primary", "contract-version", "pp-option"],
)
def test_validation_errors(example_gas: dict, mutate, match: str) -> None:
    """Structural problems raise RequestValidationError naming the offending field."""
    broken = copy.deepcopy(example_gas)
    mutate(broken)
    with pytest.raises(RequestValidationError, match=match):
        parse_translator_input(broken)


def test_unknown_country_raises_mapping_error(example_gas: dict) -> None:
    """A country without weather data cannot be mapped (validation failure, exit code 2)."""
    parsed = parse_translator_input(example_gas)
    request = copy.deepcopy(parsed.request)
    request["location"]["countryCode"] = "ZZ"
    with pytest.raises(RequestValidationError, match="ZZ"):
        translate(request, "base", "job")
