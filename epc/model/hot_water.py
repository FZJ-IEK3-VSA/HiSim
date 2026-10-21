"""A building's domestic hot-water system as a description states it, for the recoverable losses of stage 1b.

The recoverable hot-water losses Q_TW,beh of OENORM H 5056-1:2024-03 Eq. (15) are the losses of the
hot-water system that stay in heated rooms: the emission losses of the fittings, the pipes in the
conditioned area and the stub lines, and a storage that stands in the conditioned area. They enter the
heating days and through them the heating demand under the site climate (D44). This module only
describes the system; what the engine assumes for a building that states none is the engine's
default (D45), and the HiSim record states none.

The vocabulary is the norm's: the insulation of a pipe is the ratio of insulation thickness to pipe
diameter of Tab. 9 and 10, the stub material that of Tab. 7, the fittings and the metering those of
Tab. 3 and 4, the storage kinds those of Tab. 15. Circulation lines lie and are insulated like the
distribution and risers they run beside.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

#: The ratio of insulation thickness to pipe diameter, H 5056-1 Tab. 9 and 10; "0" is uninsulated.
PIPE_INSULATIONS: Tuple[str, ...] = ("2", "1.5", "1", "2/3", "1/3", "0")

#: The stub-line materials of H 5056-1 Tab. 7.
STUB_MATERIALS: Tuple[str, ...] = ("steel_or_unknown", "copper", "plastic")

#: The fittings of H 5056-1 Tab. 3 and the metering of Tab. 4.
ARMATURES: Tuple[str, ...] = ("two_handle", "single_lever", "thermostatic")
METERINGS: Tuple[str, ...] = ("individual", "flat_rate")

#: The storage kinds of H 5056-1 Tab. 15, and "none" for a system without a storage.
STORAGE_KINDS: Tuple[str, ...] = ("solar_heat_pump_compact", "indirect", "direct", "none")


@dataclass(frozen=True)
class PipeRun:
    """The distribution or the risers of the hot-water system, with the circulation lines beside them.

    Attributes:
        conditioned: Whether the run lies in the conditioned area.
        insulation: Insulation thickness relative to the pipe diameter, one of :data:`PIPE_INSULATIONS`.
        fittings_insulated: Whether fittings, pumps and the like of the run are insulated (Tab. 10).
        length_in_m: The run's length as stated; ``None`` takes the default of Tab. 6.
    """

    conditioned: bool
    insulation: str
    fittings_insulated: bool
    length_in_m: Optional[float] = None


@dataclass(frozen=True)
class HotWaterSystem:
    """What the recoverable hot-water losses of H 5056-1:2024-03 Eq. (15) are computed from.

    Attributes:
        circulation: Whether the system has a circulation line.
        distribution: The distribution lines.
        risers: The risers.
        stub_material: The material of the stub lines, one of :data:`STUB_MATERIALS`.
        storage_kind: The storage, one of :data:`STORAGE_KINDS`.
        storage_conditioned: Whether the storage stands in the conditioned area.
        armatures: The fittings, one of :data:`ARMATURES`.
        metering: The consumption metering, one of :data:`METERINGS`.
        stub_length_in_m: The stub lines' length as stated; ``None`` takes the default of Tab. 6.
    """

    circulation: bool
    distribution: PipeRun
    risers: PipeRun
    stub_material: str
    storage_kind: str
    storage_conditioned: bool
    armatures: str = "two_handle"
    metering: str = "individual"
    stub_length_in_m: Optional[float] = None


def checks(system: HotWaterSystem) -> List[Tuple[str, Any, bool, str]]:
    """The value checks of a stated system, as (field, value, admissible, rule) for the description (E-18)."""
    rows: List[Tuple[str, Any, bool, str]] = []
    for name, run in (("distribution", system.distribution), ("risers", system.risers)):
        rows += [
            (f"hot_water.{name}.insulation", run.insulation, run.insulation in PIPE_INSULATIONS,
             f"one of {', '.join(PIPE_INSULATIONS)}"),
            (f"hot_water.{name}.length_in_m", run.length_in_m, _length(run.length_in_m),
             "null or a finite number >= 0"),
        ]
    rows += [
        ("hot_water.stub_material", system.stub_material, system.stub_material in STUB_MATERIALS,
         f"one of {', '.join(STUB_MATERIALS)}"),
        ("hot_water.storage_kind", system.storage_kind, system.storage_kind in STORAGE_KINDS,
         f"one of {', '.join(STORAGE_KINDS)}"),
        ("hot_water.storage_conditioned", system.storage_conditioned,
         system.storage_kind != "none" or not system.storage_conditioned, "false without a storage"),
        ("hot_water.armatures", system.armatures, system.armatures in ARMATURES, f"one of {', '.join(ARMATURES)}"),
        ("hot_water.metering", system.metering, system.metering in METERINGS, f"one of {', '.join(METERINGS)}"),
        ("hot_water.stub_length_in_m", system.stub_length_in_m, _length(system.stub_length_in_m),
         "null or a finite number >= 0"),
    ]
    return rows


def _length(value: Optional[float]) -> bool:
    """Whether a stated length is absent or a finite number of at least 0."""
    return value is None or (math.isfinite(value) and value >= 0)
