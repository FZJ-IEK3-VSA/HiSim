"""Synthetic Austrian tables for the tests: the contract's shape with hand-chosen values, no norm value (R8.5).

The numbers, the elevation bands, the dwelling-unit classes and the factors are chosen by hand so that a
test can compute its expectation; only the files, columns, grids and row counts are the contract's.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from epc.engines.at.climate_tables import CLIMATE_REGIONS
from epc.engines.at.data_tables import (
    CORRECTION_FACTOR_ROWS,
    RADIATION_COEFFICIENTS_FILE,
    REFERENCE_CLIMATE_FILE,
    TEMPERATURE_COEFFICIENTS_FILE,
    TEMPERATURE_CORRECTION_FACTORS_FILE,
    TRANSPOSITION_FACTORS_FILE,
    USAGE_PROFILES_FILE,
)

MONTHS = range(1, 13)
B_BANDS = ("<=100", "100<h<=200", ">200")
E_BANDS = (("E1", "", "10"), ("E2", "10", "20"), ("E3", "20", "30"), ("E4", "30", "40"), ("E5", "40", "50"),
           ("E6", "50", "60"), ("E7", "60", ""))
SIGNED_AZIMUTHS = [22.5 * step for step in range(-7, 9)]


def _number(value: float) -> str:
    """A float as the CSV writes it."""
    return repr(float(value))


def f_t(azimuth_step: int, tilt_step: int) -> float:
    """The synthetic transposition factor of an azimuth step (22.5°) and a tilt step (15°), in every band."""
    return 1 + tilt_step / 100 + azimuth_step / 1000


def f_h(row: str) -> float:
    """The synthetic heating-case factor of a Tab. 3–5 row: different for every row."""
    return 0.1 + CORRECTION_FACTOR_ROWS.index(row) / 100


def synthetic_tables() -> Dict[str, List[str]]:
    """Complete tables in the contract's shape, as lines per file, with hand-chosen values."""
    reference = ["# synthetic", "month,tilt_deg,azimuth_deg,value_kwh_per_m2,theta_e_c"]
    for month in MONTHS:
        reference.append(f"{month},0,,{_number(10 * month)},{_number(month - 0.5)}")
        for tilt in (15, 30, 45, 60, 75, 90):
            for azimuth in SIGNED_AZIMUTHS:
                reference.append(f"{month},{tilt},{_number(azimuth)},{_number(tilt + azimuth / 100 + month)},")
    temperature = ["region,band,month,a,b"]
    temperature += [f"{region},{band},{month},{_number(month)},{_number(-0.01 * month)}"
                    for region in CLIMATE_REGIONS for band in B_BANDS for month in MONTHS]
    radiation = ["region,month,a2,a1,a0"]
    radiation += [f"{region},{month},1e-06,{_number(0.01 * month)},{_number(20 + month)}"
                  for region in CLIMATE_REGIONS for month in MONTHS]
    transposition = ["band,elevation_above_m,elevation_up_to_m,month,azimuth_deg,tilt_deg,f_t"]
    transposition += [f"{band},{above},{up_to},{month},{_number(22.5 * az)},{15 * tilt},{_number(f_t(az, tilt))}"
                      for band, above, up_to in E_BANDS for month in MONTHS for az in range(9) for tilt in range(7)]
    usage = ["class,theta_i_h_c,n_l_hyg_per_h,q_i_h_w_per_m2,wwwb_wh_per_m2_d",
             "1-1,11.1,0.11,1.1,1.0", "2-5,12.2,0.22,2.2,2.0", "6+,13.3,0.33,3.3,3.0"]
    factors = ["boundary,f_h,f_c,table,row_text"]
    factors += [f"{key},{_number(f_h(key))},0.456,4,synthetic row" for key in CORRECTION_FACTOR_ROWS]
    return {
        REFERENCE_CLIMATE_FILE: reference,
        TEMPERATURE_COEFFICIENTS_FILE: temperature,
        RADIATION_COEFFICIENTS_FILE: radiation,
        TRANSPOSITION_FACTORS_FILE: transposition,
        USAGE_PROFILES_FILE: usage,
        TEMPERATURE_CORRECTION_FACTORS_FILE: factors,
    }


def write(folder: Path, tables: Dict[str, List[str]]) -> Path:
    """Writes the tables into ``folder`` and returns it."""
    for file, lines in tables.items():
        (folder / file).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return folder
