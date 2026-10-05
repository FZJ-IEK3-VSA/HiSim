"""What the harness checks on a run and across runs (``assemblies_spec.md`` §9.4).

On every run, in this order: that it raised no exception, that every result column is finite, that
the energy balance closed (``EnergyBalanceError``, raised by every run's post-processing), and then
the assembly's declarations — ``bounds`` on an output (every timestep of the member's result column
within ``[min, max]``) and on a KPI (found by the KPI finder by name, import, member and assembly,
never by key string). Across runs: ``expect`` (a preset's KPI within its band) and ``monotone`` (a
KPI over a sweep of one parameter non-decreasing, non-increasing or constant).

**Tolerance.** ``monotone`` compares two neighbouring KPI values of a sweep as equal when they
differ by at most the golden gate's relative tolerance (:data:`~hisim.postprocessing.kpi_computation
.tolerances.REL_TOL`) times the largest magnitude of the sweep's series, plus the absolute floor
:data:`MONOTONE_ABS_FLOOR`. Scaling by the series rather than by the pair keeps a KPI that is
nominally zero at one point comparable with the rest, and the floor absorbs the float noise a sum
of per-step values leaves on a KPI that is zero everywhere (``0.0`` against ``1e-15``), where a
relative tolerance alone collapses. ``increasing`` accepts a step that stays equal and ``constant``
accepts nothing else. A band (``bounds``, ``expect``) is the modeller's plausibility judgement and
is checked exactly.
"""

from __future__ import annotations

import math
from typing import Any, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from hisim.energy_system.assemblies.model import (
    BoundsDeclaration,
    ExpectDeclaration,
    MonotoneDeclaration,
    MonotoneDirection,
)
from hisim.postprocessing.kpi_computation.kpi_address import KpiFinder
from hisim.postprocessing.kpi_computation.tolerances import REL_TOL

#: The absolute floor of a monotone comparison: far below any KPI an assembly declares (a kWh, a
#: kg, a share), far above the float noise of a sum over a day of steps.
MONOTONE_ABS_FLOOR = 1e-9


class DeclarationText:
    """How the report names a declaration."""

    @staticmethod
    def band(minimum: Optional[float], maximum: Optional[float]) -> str:
        """``[min, max]`` with an open end written as ``…``."""
        low = "…" if minimum is None else f"{minimum:g}"
        high = "…" if maximum is None else f"{maximum:g}"
        return f"[{low}, {high}]"

    @classmethod
    def bounds(cls, declaration: BoundsDeclaration) -> str:
        """``bounds Heater.ThermalPower [WATT] in [0, 6000]``."""
        unit = f" [{declaration.unit}]" if declaration.unit else ""
        return f"bounds {declaration.subject}{unit} in {cls.band(declaration.min, declaration.max)}"

    @staticmethod
    def monotone(declaration: MonotoneDeclaration) -> str:
        """``monotone power_in_watt rises: Heater energy of Heater increasing``."""
        return f"monotone {declaration.parameter} rises: {declaration.subject} {declaration.direction.value}"

    @classmethod
    def expect(cls, declaration: ExpectDeclaration) -> str:
        """``expect preset south: PV production of PVSystem in [0, 100]``."""
        return (
            f"expect preset {declaration.preset}: {declaration.kpi} of {declaration.member} in "
            f"{cls.band(declaration.min, declaration.max)}"
        )


def nonfinite_columns(results: pd.DataFrame) -> List[str]:
    """Every numeric result column holding a NaN or an infinity, with its first offending timestep.

    A column of another type (a state written as text) has no finiteness to check and is skipped.
    """
    found: List[str] = []
    for column in results.columns:
        if not pd.api.types.is_numeric_dtype(results[column]):
            continue
        values = results[column].to_numpy(dtype=float)
        bad = ~np.isfinite(values)
        if bad.any():
            first = int(np.argmax(bad))
            found.append(f"{column} is {values[first]} at step {first} ({int(bad.sum())} steps not finite)")
    return found


def output_column(outputs: Sequence[Any], component: str, output: str) -> Optional[str]:
    """The result column of one component's output, by its runtime name and field name."""
    return next(
        (
            item.get_pretty_name()
            for item in outputs
            if item.component_name == component and item.field_name == output
        ),
        None,
    )


def band_violation(value: float, minimum: Optional[float], maximum: Optional[float]) -> Optional[str]:
    """Why a value lies outside a band, or ``None``."""
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        return f"the value {value!r} is no finite number"
    if minimum is not None and value < minimum:
        return f"{value:g} lies below {minimum:g}"
    if maximum is not None and value > maximum:
        return f"{value:g} lies above {maximum:g}"
    return None


def series_violation(series: pd.Series, minimum: Optional[float], maximum: Optional[float]) -> Optional[str]:
    """Why a result column leaves a band at some timestep, or ``None``; names the extreme and the first step."""
    values = pd.to_numeric(series, errors="coerce").to_numpy(dtype=float)
    below = values < minimum if minimum is not None else np.zeros(len(values), dtype=bool)
    above = values > maximum if maximum is not None else np.zeros(len(values), dtype=bool)
    outside = below | above
    if not outside.any():
        return None
    first = int(np.argmax(outside))
    return (
        f"{int(outside.sum())} of {len(values)} steps leave the band; the first is step {first} with "
        f"{values[first]:g}; the column runs from {np.nanmin(values):g} to {np.nanmax(values):g}"
    )


def kpi_value(finder: KpiFinder, kpi: str, import_key: str, member: Optional[str], assembly: str) -> float:
    """One KPI of the assembly under test: a member's by the finder's address fields, or a derived one.

    A declaration without a member names a derived KPI (``assemblies_spec.md`` §9.4, as decided): an
    entry of that name with no source, of which the isolation system — one building — holds exactly
    one. A member's KPI of the same name, which has a source, is not it.

    Raises:
        ValueError: When no KPI or several match, or the value is no finite number.
    """
    if member is None:
        derived = [entry for address, entry in finder.entries(name=kpi) if address.source is None]
        if len(derived) != 1:
            raise ValueError(
                f"{len(derived)} derived KPIs (without a source) are named '{kpi}' in the isolation run; a "
                "declaration without a member names exactly one"
            )
        value = derived[0].get("value")
    else:
        value = finder.value(name=kpi, import_key=import_key, member=member, assembly=assembly)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
        raise ValueError(f"the KPI '{kpi}' of '{member or 'the system'}' is {value!r}, no finite number")
    return float(value)


class MonotoneEvaluation:
    """Whether a KPI series over a rising parameter moves the declared way, within the gate's tolerance."""

    @staticmethod
    def equal(first: float, second: float, scale: float) -> bool:
        """Two KPI values equal within ``REL_TOL`` of the series' magnitude ``scale`` plus the floor."""
        return abs(first - second) <= REL_TOL * scale + MONOTONE_ABS_FLOOR

    @classmethod
    def holds(cls, first: float, second: float, direction: MonotoneDirection, scale: float) -> bool:
        """Whether one step from ``first`` to ``second`` moves the declared way."""
        if cls.equal(first, second, scale):
            return True
        if direction == MonotoneDirection.INCREASING:
            return second > first
        if direction == MonotoneDirection.DECREASING:
            return second < first
        return False

    @classmethod
    def offending_pair(
        cls, values: Sequence[float], direction: MonotoneDirection
    ) -> Optional[Tuple[int, int]]:
        """The first pair of neighbouring points that moves the wrong way, as indices; ``None`` when none does."""
        scale = max((abs(value) for value in values), default=0.0)
        for index in range(len(values) - 1):
            if not cls.holds(values[index], values[index + 1], direction, scale):
                return index, index + 1
        return None
