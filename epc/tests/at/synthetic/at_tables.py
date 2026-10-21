"""Synthetic Austrian tables for the tests: the contract's shape with hand-chosen values, no norm value (R8.5).

DISCLAIMER - SYNTHETIC TEST DATA. Every table value in this module is invented to test the code,
and any building in it is a placeholder or a HiSim test record: no value comes from an OENORM
table, and no number computed here is the heating demand of any real building. Never use them for
a study or a real calculation. The standards themselves (OENORM B 8110-5, B 8110-6-1, B 8110-6-2,
H 5050-1, H 5056-1, edition 2024-03) can be purchased from Austrian Standards International:
https://www.austrian-standards.at

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
from epc.engines.at.hot_water_tables import HOT_WATER_FILE, HOT_WATER_KEYS, OPERATION_FILE

#: The first line of every file :func:`write` writes.
SYNTHETIC_HEADER = "# SYNTHETIC TEST DATA - invented, not from any OENORM table"

MONTHS = range(1, 13)
B_BANDS = ("<=100", "100<h<=200", ">200")
E_BANDS = (("E1", "", "10"), ("E2", "10", "20"), ("E3", "20", "30"), ("E4", "30", "40"), ("E5", "40", "50"),
           ("E6", "50", "60"), ("E7", "60", ""))
SIGNED_AZIMUTHS = [22.5 * step for step in range(-7, 9)]

#: The synthetic hot-water values that are temperatures, in °C, and the scale of BF, in m²; every other key
#: of the contract takes :func:`hot_water_value`'s small distinct number.
HOT_WATER_TEMPERATURES = {
    "temperature_circulation_other_buildings": 70.0, "temperature_circulation_single_family_otherwise": 65.0,
    "temperature_no_circulation_base": 50.0, "temperature_no_circulation_span": 10.0,
    "temperature_no_circulation_scale_bf": 100.0,
    "storage_temperature_solar_heat_pump_compact": 41.0,
    "storage_temperature_solar_heat_pump_compact_single_family": 42.0,
    "storage_temperature_indirect": 43.0, "storage_temperature_indirect_single_family": 44.0,
    "storage_temperature_direct": 45.0,
}

#: The synthetic usage days d_Nutz of each month and the daily operating hours t_h,d, in every class.
USAGE_DAYS = tuple(10.0 + month for month in MONTHS)
OPERATING_HOURS = 20.0


def _number(value: float) -> str:
    """A float as the CSV writes it."""
    return repr(float(value))


def f_t(azimuth_step: int, tilt_step: int) -> float:
    """The synthetic transposition factor of an azimuth step (22.5°) and a tilt step (15°), in every band."""
    return 1 + tilt_step / 100 + azimuth_step / 1000


def f_h(row: str) -> float:
    """The synthetic heating-case factor of a Tab. 3–5 row: different for every row."""
    return 0.1 + CORRECTION_FACTOR_ROWS.index(row) / 100


def hot_water_value(key: str) -> float:
    """The synthetic value of a key of the hot-water table: a temperature as above, else different for every key."""
    return HOT_WATER_TEMPERATURES.get(key, round(1.01 + HOT_WATER_KEYS.index(key) / 100, 2))


def synthetic_tables() -> Dict[str, List[str]]:
    """Complete tables in the contract's shape, as lines per file, with hand-chosen values."""
    reference = ["month,tilt_deg,azimuth_deg,value_kwh_per_m2,theta_e_c"]
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
             "1-1,31.1,0.11,1.1,1.0", "2-5,32.2,0.22,2.2,2.0", "6+,33.3,0.33,3.3,3.0"]
    factors = ["boundary,f_h,f_c,table,row_text"]
    factors += [f"{key},{_number(f_h(key))},0.456,4,synthetic row" for key in CORRECTION_FACTOR_ROWS]
    hot_water = ["table,key,value,unit,row_text"]
    hot_water += [f"0,{key},{_number(hot_water_value(key))},-,synthetic row" for key in HOT_WATER_KEYS]
    operation = ["class,month,d_nutz_d,t_h_d_h"]
    operation += [f"{label},{month},{_number(USAGE_DAYS[month - 1])},{_number(OPERATING_HOURS)}"
                  for label in ("1-1", "2-5", "6+") for month in MONTHS]
    return {
        REFERENCE_CLIMATE_FILE: reference,
        TEMPERATURE_COEFFICIENTS_FILE: temperature,
        RADIATION_COEFFICIENTS_FILE: radiation,
        TRANSPOSITION_FACTORS_FILE: transposition,
        USAGE_PROFILES_FILE: usage,
        TEMPERATURE_CORRECTION_FACTORS_FILE: factors,
        HOT_WATER_FILE: hot_water,
        OPERATION_FILE: operation,
    }


def write(folder: Path, tables: Dict[str, List[str]]) -> Path:
    """Writes the tables into ``folder``, each headed by :data:`SYNTHETIC_HEADER`, and returns it."""
    for file, lines in tables.items():
        (folder / file).write_text("\n".join([SYNTHETIC_HEADER, *lines]) + "\n", encoding="utf-8")
    return folder
