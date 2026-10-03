"""What the harness checks on a run and across runs (``assemblies_spec.md`` §9.4).

On every run, in this order: that it raised no exception, that every result column is finite, that
the energy balance closed (``EnergyBalanceError``, raised by every run's post-processing), and then
the assembly's declarations — ``bounds`` on an output (every timestep of the member's result column
within ``[min, max]``) and on a KPI (found by the KPI finder by name, import, member and assembly,
never by key string). Across runs: ``expect`` (a preset's KPI within its band) and ``monotone`` (a
KPI over a sweep of one parameter non-decreasing, non-increasing or constant).

**Tolerance.** ``monotone`` compares two KPI values with the golden gate's numeric tolerance,
:data:`REL_TOL` and :data:`ABS_TOL`, the values of ``scripts/golden_kpis.py`` (a test keeps the
two equal): two values within it are equal, so ``increasing`` accepts a step that stays equal
and ``constant`` accepts nothing else. A band (``bounds``, ``expect``) is the modeller's
plausibility judgement and is checked exactly.
"""

from __future__ import annotations

import math
from typing import Any, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from hisim.energy_system.assemblies.model import (
    BoundsDeclaration,
    ExpectDeclaration,
    MonotoneDeclaration,
    MonotoneDirection,
)
from hisim.postprocessing.kpi_computation.kpi_address import KpiFinder

#: The golden gate's relative tolerance (``scripts/golden_kpis.py`` ``REL_TOL``).
REL_TOL = 1e-9

#: The golden gate's absolute tolerance (``scripts/golden_kpis.py`` ``ABS_TOL``).
ABS_TOL = 0.0


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
        return (
            f"monotone {declaration.parameter} rises: {declaration.kpi} of {declaration.member} "
            f"{declaration.direction.value}"
        )

    @classmethod
    def expect(cls, declaration: ExpectDeclaration) -> str:
        """``expect preset south: PV production of PVSystem in [0, 100]``."""
        return (
            f"expect preset {declaration.preset}: {declaration.kpi} of {declaration.member} in "
            f"{cls.band(declaration.min, declaration.max)}"
        )


def nonfinite_columns(results: pd.DataFrame) -> List[str]:
    """Every result column holding a NaN or an infinity, with its first offending timestep."""
    found: List[str] = []
    for column in results.columns:
        values = pd.to_numeric(results[column], errors="coerce").to_numpy(dtype=float)
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


def kpi_value(finder: KpiFinder, kpi: str, import_key: str, member: str, assembly: str) -> float:
    """One KPI of one member of the assembly under test, by the finder's address fields.

    Raises:
        ValueError: When no KPI or several match, or the value is no finite number.
    """
    value = finder.value(name=kpi, import_key=import_key, member=member, assembly=assembly)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
        raise ValueError(f"the KPI '{kpi}' of '{member}' is {value!r}, no finite number")
    return float(value)


class MonotoneEvaluation:
    """Whether a KPI series over a rising parameter moves the declared way, within the gate's tolerance."""

    @staticmethod
    def equal(first: float, second: float) -> bool:
        """Two KPI values equal within the golden gate's tolerance."""
        return math.isclose(first, second, rel_tol=REL_TOL, abs_tol=ABS_TOL)

    @classmethod
    def holds(cls, first: float, second: float, direction: MonotoneDirection) -> bool:
        """Whether one step from ``first`` to ``second`` moves the declared way."""
        if cls.equal(first, second):
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
        for index in range(len(values) - 1):
            if not cls.holds(values[index], values[index + 1], direction):
                return index, index + 1
        return None


def parameters_text(values: Mapping[str, Any]) -> str:
    """A parameter set in one line, for a failure message."""
    return ", ".join(f"{name}={value!r}" for name, value in values.items())
