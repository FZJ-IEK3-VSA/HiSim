"""T-6: the Austrian loader reads a complete table set and refuses a missing, incomplete or malformed one.

Every table here is synthetic (:mod:`epc.tests.at_tables`): no value of any norm (R8.5).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Dict, List

import pytest

from epc.engines.at import data_tables
from epc.engines.at.data_tables import (
    CORRECTION_FACTOR_ROWS,
    RADIATION_COEFFICIENTS_FILE,
    REFERENCE_CLIMATE_FILE,
    TABLE_FILES,
    TEMPERATURE_COEFFICIENTS_FILE,
    TEMPERATURE_CORRECTION_FACTORS_FILE,
    TRANSPOSITION_FACTORS_FILE,
    USAGE_PROFILES_FILE,
    load_tables,
)
from epc.model.errors import EpcError
from epc.tests import support
from epc.tests.at_tables import B_BANDS, E_BANDS, MONTHS, f_t, synthetic_tables, write

pytestmark = pytest.mark.base


def _refusal(folder: Path) -> EpcError:
    """The refusal of loading a folder."""
    with pytest.raises(EpcError) as refusal:
        load_tables(folder)
    return refusal.value


def test_a_complete_folder_loads_with_the_values_and_bands_it_states(tmp_path: Path) -> None:
    """Every table is read into its typed structure, the bands and classes from the files themselves."""
    tables = load_tables(write(tmp_path, synthetic_tables()))
    assert tables.reference_climate.temperatures_c == tuple(month - 0.5 for month in MONTHS)
    assert tables.reference_climate.horizontal_radiation_kwh_per_m2 == tuple(10.0 * month for month in MONTHS)
    assert tables.reference_climate.radiation_kwh_per_m2[(45.0, -22.5)][2] == 45 - 0.225 + 3
    assert len(tables.reference_climate.radiation_kwh_per_m2) == 16 * 6
    assert [band.band for band in tables.temperature_coefficients["N/SO"]] == list(B_BANDS)
    assert tables.temperature_coefficients["ZA"][0].b[9] == -0.1
    assert tables.radiation_coefficients["SB"].a0[11] == 32.0
    assert [band.band for band in tables.transposition_bands] == [band for band, _, _ in E_BANDS]
    assert tables.transposition_bands[3].factors[(157.5, 30.0)] == (f_t(7, 2),) * 12
    assert [(row.dwelling_units_from, row.dwelling_units_up_to) for row in tables.usage_profiles] == [
        (1, 1), (2, 5), (6, None)]
    assert set(tables.temperature_correction_factors) == set(CORRECTION_FACTOR_ROWS)
    assert tables.temperature_correction_factors["OUTSIDE_AIR"].f_c == 0.456
    assert tables.digests == {
        file: f"sha256:{hashlib.sha256((tmp_path / file).read_bytes()).hexdigest()}" for file in TABLE_FILES
    }


@pytest.mark.parametrize(
    "elevation, temperature_band, transposition_band",
    [(0.0, "<=100", "E1"), (10.0, "<=100", "E1"), (10.5, "<=100", "E2"), (100.0, "<=100", "E7"),
     (100.25, "100<h<=200", "E7"), (200.0, "100<h<=200", "E7"), (5000.0, ">200", "E7")],
)
def test_the_band_of_an_elevation_includes_its_upper_limit(
    tmp_path: Path, elevation: float, temperature_band: str, transposition_band: str
) -> None:
    """A band runs from above its lower limit up to and including its upper limit."""
    tables = load_tables(write(tmp_path, synthetic_tables()))
    assert data_tables.temperature_coefficients_for(tables, "W", elevation).band == temperature_band
    assert data_tables.transposition_band_for(tables, elevation).band == transposition_band


@pytest.mark.parametrize("units, label", [(1, "1-1"), (2, "2-5"), (5, "2-5"), (6, "6+"), (400, "6+")])
def test_the_usage_class_of_a_dwelling_unit_count(tmp_path: Path, units: int, label: str) -> None:
    """The classes are read from their labels, the last one open above."""
    tables = load_tables(write(tmp_path, synthetic_tables()))
    assert data_tables.usage_profile_for(tables, units).label == label


def test_an_empty_folder_is_refused_naming_the_folder_and_the_contract(tmp_path: Path) -> None:
    """E-11 names the country, the folder, the first missing file and the data contract."""
    error = _refusal(tmp_path)
    assert error.error_id == "E-11"
    assert error.message == (
        f"The AT engine needs its tables under '{tmp_path}' ({REFERENCE_CLIMATE_FILE} is missing); "
        "see epc/data/README.md."
    )


@pytest.mark.parametrize("missing", TABLE_FILES)
def test_each_missing_file_is_refused(tmp_path: Path, missing: str) -> None:
    """Any one file missing refuses with E-11 naming it."""
    write(tmp_path, synthetic_tables())
    (tmp_path / missing).unlink()
    error = _refusal(tmp_path)
    assert error.error_id == "E-11"
    assert f"({missing} is missing)" in error.message


@pytest.mark.parametrize(
    "file, dimension, expected",
    [
        (REFERENCE_CLIMATE_FILE, "12 months × (1 horizontal surface + 16 orientations × 6 tilts)", 1164),
        (TEMPERATURE_COEFFICIENTS_FILE, "7 regions × 3 elevation bands × 12 months", 252),
        (RADIATION_COEFFICIENTS_FILE, "7 regions × 12 months", 84),
        (TRANSPOSITION_FACTORS_FILE, "7 elevation bands × 12 months × 9 azimuths × 7 tilts", 5292),
        (USAGE_PROFILES_FILE, "the residential dwelling-unit classes", 3),
        (TEMPERATURE_CORRECTION_FACTORS_FILE, "the rows of the contract", 14),
    ],
)
def test_a_table_missing_its_last_row_is_refused_with_the_count(
    tmp_path: Path, file: str, dimension: str, expected: int
) -> None:
    """E-12 names the file, the dimension, the expected and the found row count."""
    tables = synthetic_tables()
    tables[file] = tables[file][:-1]
    error = _refusal(write(tmp_path, tables))
    assert error.error_id == "E-12"
    assert error.message == f"{file}: expected {expected} rows for {dimension}, found {expected - 1}."


def _replace(tables: Dict[str, List[str]], file: str, old: str, new: str) -> Dict[str, List[str]]:
    """Replaces the start ``old`` of the first line of a file that begins with it by ``new``."""
    lines = tables[file]
    index = next(number for number, line in enumerate(lines) if line.startswith(old))
    lines[index] = new + lines[index][len(old):]
    return tables


def _relabel(tables: Dict[str, List[str]], file: str, old: str, new: str) -> Dict[str, List[str]]:
    """Replaces a band or class label in every row of a file."""
    tables[file] = [line.replace(old, new) for line in tables[file]]
    return tables


MALFORMED = [
    ("a wrong header", REFERENCE_CLIMATE_FILE, "month,tilt_deg,", "month,tilt,", "expected the columns"),
    ("a row of the wrong width", RADIATION_COEFFICIENTS_FILE, "W,1,", "W,1,1,", "expected 5 values, found 6"),
    ("a text where a number belongs", RADIATION_COEFFICIENTS_FILE, "W,1,1e-06", "W,1,abc", "must be a finite number"),
    ("a non-finite number", RADIATION_COEFFICIENTS_FILE, "W,1,1e-06", "W,1,nan", "must be a finite number"),
    ("a second row for one key", RADIATION_COEFFICIENTS_FILE, "W,2,", "W,1,", "a second row for ('W', 1)"),
    ("an unknown region", RADIATION_COEFFICIENTS_FILE, "W,1,", "X,1,", "region 'X' is not one of"),
    ("a month outside 1 … 12", RADIATION_COEFFICIENTS_FILE, "W,1,", "W,13,", "month 13 is not one of"),
    ("an azimuth off the grid", TRANSPOSITION_FACTORS_FILE, "E1,,10,1,22.5,", "E1,,10,1,20.0,", "azimuth_deg 20.0"),
    ("a second range for a band", TRANSPOSITION_FACTORS_FILE, "E2,10,20,1,0.0,0,", "E2,10,25,1,0.0,0,",
     "band E2 states a second elevation range"),
    ("an azimuth on the horizontal surface", REFERENCE_CLIMATE_FILE, "1,0,,", "1,0,0.0,", "has no azimuth"),
    ("a temperature on a tilted surface", REFERENCE_CLIMATE_FILE, "1,15,-157.5,14.425,", "1,15,-157.5,14.425,5",
     "theta_e_c belongs to the horizontal rows only"),
    ("a reference azimuth off the grid", REFERENCE_CLIMATE_FILE, "1,15,-157.5,", "1,15,-180.0,", "azimuth_deg -180.0"),
    ("a band label of another form", TEMPERATURE_COEFFICIENTS_FILE, "W,<=100,1,", "W,0-100,1,", "band '0-100'"),
    ("a class label of another form", USAGE_PROFILES_FILE, "1-1,", "1 to 1,", "class '1 to 1'"),
    ("a factor for the conditioned neighbour", TEMPERATURE_CORRECTION_FACTORS_FILE, "OUTSIDE_AIR,",
     "CONDITIONED_NEIGHBOUR,", "row 'CONDITIONED_NEIGHBOUR' is not one of the rows of the contract"),
    ("a factor above one", TEMPERATURE_CORRECTION_FACTORS_FILE, "OUTSIDE_AIR,0.1,", "OUTSIDE_AIR,1.5,",
     "lies outside 0 … 1"),
]


@pytest.mark.parametrize("case, file, old, new, problem", MALFORMED, ids=[row[0] for row in MALFORMED])
def test_a_malformed_cell_or_row_is_refused_naming_file_and_line(
    tmp_path: Path, case: str, file: str, old: str, new: str, problem: str
) -> None:
    """Each malformation is refused with E-19, the file, the line and the reason, never read past."""
    assert case
    error = _refusal(write(tmp_path, _replace(synthetic_tables(), file, old, new)))
    assert error.error_id == "E-19"
    assert error.message.startswith(f"{file}, line ")
    assert problem in error.message


def test_the_line_of_a_malformed_cell_is_the_line_in_the_file(tmp_path: Path) -> None:
    """The first data row of a file without comment lines is line 2."""
    error = _refusal(write(tmp_path, _replace(synthetic_tables(), RADIATION_COEFFICIENTS_FILE, "W,1,1e-06", "W,1,x")))
    assert error.message == f"{RADIATION_COEFFICIENTS_FILE}, line 2: a2 must be a finite number, found 'x'."


GAPS = [
    ("a gap between Annex B bands", TEMPERATURE_COEFFICIENTS_FILE, ",100<h<=200,", ",150<h<=200,"),
    ("an Annex B band closed below", TEMPERATURE_COEFFICIENTS_FILE, ",<=100,", ",50<h<=100,"),
    ("an inverted Annex B band", TEMPERATURE_COEFFICIENTS_FILE, ",>200,", ",200<h<=150,"),
    ("a gap between Annex E bands", TRANSPOSITION_FACTORS_FILE, "E3,20,30,", "E3,25,30,"),
    ("an Annex E band closed above", TRANSPOSITION_FACTORS_FILE, "E7,60,,", "E7,60,70,"),
    ("a gap between usage classes", USAGE_PROFILES_FILE, "2-5,", "3-5,"),
    ("usage classes not starting at one", USAGE_PROFILES_FILE, "1-1,", "0-1,"),
    ("a usage class open above before the last", USAGE_PROFILES_FILE, "2-5,", "2+,"),
]


@pytest.mark.parametrize("case, file, old, new", GAPS, ids=[row[0] for row in GAPS])
def test_bands_and_classes_must_cover_every_value_once(
    tmp_path: Path, case: str, file: str, old: str, new: str
) -> None:
    """Elevation bands and dwelling-unit classes follow one another without gap or overlap, open at the ends."""
    assert case
    error = _refusal(write(tmp_path, _relabel(synthetic_tables(), file, old, new)))
    assert error.error_id == "E-19"
    assert error.message.startswith(f"{file}, line ")


def test_the_default_folder_is_epc_data_at() -> None:
    """The engine reads epc/data/at/ unless told otherwise."""
    assert data_tables.DEFAULT_DATA_FOLDER == support.REPOSITORY_ROOT / "epc" / "data" / "at"
