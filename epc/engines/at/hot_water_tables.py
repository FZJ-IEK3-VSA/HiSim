"""The local tables of the recoverable hot-water losses (stage 1b), loaded complete or refused.

Two files under ``epc/data/at/`` (licensed, D7), described in ``epc/data/README.md``:

- the values of OENORM H 5056-1:2024-03 Tab. 3–10 and 15 the recoverable losses of Eq. (15) need, one
  row per key of :data:`HOT_WATER_KEYS`: fixed and default values, and the coefficients of the
  formulas the tables print;
- the monthly usage days d_Nutz and the daily operating hours t_h,d of OENORM B 8110-5:2024-03 Tab. 4, 5
  and 8 for every residential dwelling-unit class.

A missing file is refused with ``E-11``, a wrong row count with ``E-12``, anything malformed with
``E-19``. Nothing here knows a value of a table.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Dict, Mapping, Tuple

from epc.engines.at import table_reading as reading
from epc.engines.at.table_reading import Monthly

HOT_WATER_FILE = "hot_water_h5056_1_2024_tab3_15.csv"
OPERATION_FILE = "operation_b8110_5_2024_tab4_5_8.csv"

#: The keys of the hot-water table, by the table of H 5056-1:2024-03 they come from.
HOT_WATER_KEYS: Tuple[str, ...] = (
    # Tab. 3, q_TW,WA,1 in W/m², and Tab. 4, q_TW,WA,2 in W/m²
    "armatures_two_handle", "armatures_single_lever", "armatures_thermostatic",
    "metering_individual", "metering_flat_rate",
    # Tab. 6, lengths in m: a constant and a factor of BF
    "length_distribution_constant", "length_distribution_per_bf", "length_riser_per_bf",
    "length_circulation_distribution_constant", "length_circulation_distribution_per_bf",
    "length_circulation_riser_per_bf", "length_stub_residential_per_bf",
    # Tab. 7, q_Ro,A in W/m
    "stub_loss_steel_or_unknown", "stub_loss_copper", "stub_loss_plastic",
    # Tab. 8, θ_TW,m in °C; without circulation the three coefficients of its formula
    "temperature_circulation_other_buildings", "temperature_circulation_single_family_otherwise",
    "temperature_no_circulation_base", "temperature_no_circulation_span", "temperature_no_circulation_scale_bf",
    # Tab. 9, q_Ro in W/(m · K), by the ratio of insulation thickness to pipe diameter
    "pipe_loss_insulation_2", "pipe_loss_insulation_1.5", "pipe_loss_insulation_1", "pipe_loss_insulation_2/3",
    "pipe_loss_insulation_1/3",
    # Tab. 10, f_ero,1 (distribution) and f_ero,2 (risers), by insulation ratio and fittings
    "fero_1_3/3_uninsulated", "fero_1_3/3_insulated", "fero_1_2/3_uninsulated", "fero_1_2/3_insulated",
    "fero_1_1/3_uninsulated", "fero_1_1/3_insulated", "fero_1_0_uninsulated", "fero_1_0_insulated",
    "fero_2_3/3_uninsulated", "fero_2_3/3_insulated", "fero_2_2/3_uninsulated", "fero_2_2/3_insulated",
    "fero_2_1/3_uninsulated", "fero_2_1/3_insulated", "fero_2_0_uninsulated", "fero_2_0_insulated",
    # Tab. 15, θ_TW,WS,m in °C
    "storage_temperature_solar_heat_pump_compact", "storage_temperature_solar_heat_pump_compact_single_family",
    "storage_temperature_indirect", "storage_temperature_indirect_single_family", "storage_temperature_direct",
)

#: The months of the operation table per dwelling-unit class.
MONTHS_PER_CLASS = 12


@dataclass(frozen=True)
class Operation:
    """B 8110-5 Tab. 4, 5 and 8 for one residential dwelling-unit class.

    Attributes:
        usage_days: d_Nutz of each month, January to December, in d.
        operating_hours_per_day: t_h,d in h/d.
    """

    usage_days: Monthly
    operating_hours_per_day: float


def load_hot_water(folder: Path) -> Mapping[str, float]:
    """H 5056-1 Tab. 3–10 and 15: one finite value of at least 0 per key of :data:`HOT_WATER_KEYS`."""
    file = HOT_WATER_FILE
    values: Dict[str, float] = {}
    for row in reading.read_rows(folder, file, ("table", "key", "value", "unit", "row_text")):
        key = row[1]["key"]
        if key not in HOT_WATER_KEYS:
            raise reading.malformed(file, row[0], f"key '{key}' is not one of the keys of the contract")
        value = reading.number(file, row, "value")
        if value < 0:
            raise reading.malformed(file, row[0], f"{key} must not be negative")
        reading.store(file, row, values, key, value)
    reading.require_count(file, "the keys of the contract", len(HOT_WATER_KEYS), len(values))
    return MappingProxyType(values)


def load_operation(folder: Path, classes: Tuple[str, ...]) -> Mapping[str, Operation]:
    """B 8110-5 Tab. 4, 5, 8: twelve months per dwelling-unit class of the usage profiles, one t_h,d per class.

    Args:
        folder: The folder holding the tables.
        classes: The class labels of the usage profiles, which the file must cover exactly.
    """
    file = OPERATION_FILE
    days: Dict[Tuple[str, int], float] = {}
    hours: Dict[str, Tuple[int, float]] = {}
    for row in reading.read_rows(folder, file, ("class", "month", "d_nutz_d", "t_h_d_h")):
        label, month = row[1]["class"], reading.month(file, row)
        if label not in classes:
            raise reading.malformed(file, row[0], f"class '{label}' is not a class of the usage profiles")
        usage_days, operating = reading.number(file, row, "d_nutz_d"), reading.number(file, row, "t_h_d_h")
        if not 0 <= usage_days <= 31 or not 0 < operating <= 24:
            raise reading.malformed(file, row[0], "d_Nutz must lie in 0 … 31 d and t_h,d in (0, 24] h")
        if label in hours and hours[label][1] != operating:
            raise reading.malformed(file, row[0], f"class '{label}' states two values of t_h,d")
        hours.setdefault(label, (row[0], operating))
        reading.store(file, row, days, (label, month), usage_days)
    reading.require_count(file, "the months of every class", MONTHS_PER_CLASS * len(classes), len(days))
    return MappingProxyType({
        label: Operation(reading.monthly({month: days[(label, month)] for month in reading.MONTHS}), hours[label][1])
        for label in classes
    })
