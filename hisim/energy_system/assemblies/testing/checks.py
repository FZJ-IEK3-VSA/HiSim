"""What is checked on an isolation run and across a sweep (``assemblies_spec.md`` §9.4, D24).

Every check raises :class:`AssemblyCheckFailure` naming the assembly, the sample, the check and its
subject — ``mock/pv_array sample s003 bounds PVSystem.ElectricityOutput [WATT] in [0, 20000]: …`` —
and a check that needs a finished run fails, naming why, when the run did not finish.

- **The run**: it raised nothing but an ``EnergyBalanceError`` (a failure names the innermost raising
  location, ``file.py:line``, so the reader sees whether the assembly's members or the harness raised);
  the energy balance closed (the error every run's post-processing raises); every numeric result
  column is finite.
- **The member contract**, on the constructed members (D24): every energy-carrying or temperature
  output has a ``tests.bounds`` entry, a bounds entry's unit is its output's, and every KPI a
  declaration names with a member is one that member reports.
- **Declarations**: ``bounds`` on an output (every step of the member's column within ``[min, max]``)
  (a column that is not numeric, or holds a value that is not finite, fails by name) or on a KPI,
  ``expect`` (a KPI at the defaults within its band), both checked exactly; ``monotone`` (a KPI over a
  sweep non-decreasing, non-increasing or constant, :func:`evaluate_monotone` running the sweep). A
  KPI is found by the finder by name, import, member and assembly, never by key string; without a
  member it is the one derived KPI of that name (``source: null``), unambiguous in the isolation
  system's single building.

**Tolerance of monotone.** Two neighbouring values of a sweep are equal when they differ by at most
the golden gate's relative tolerance times the largest magnitude of the series, plus
:data:`MONOTONE_ABS_FLOOR`; scaling by the series keeps a KPI that is nominally zero at one point
comparable with the rest, and the floor absorbs the float noise a sum over a day of steps leaves on
a KPI that is zero everywhere. ``increasing`` and ``decreasing`` accept a step that stays equal;
``constant`` nothing else.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Callable, ClassVar, Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from hisim import loadtypes as lt
from hisim.energy_system.assemblies.describe import band
from hisim.energy_system.assemblies.model import (
    BoundsDeclaration,
    ExpectDeclaration,
    MonotoneDeclaration,
    MonotoneDirection,
    TestContract,
)
from hisim.energy_system.assemblies.testing.isolation import SUBJECT, IsolationRun, IsolationRunError
from hisim.energy_system.assemblies.testing.samples import ParameterSpace, Sample, SamplerError, sweep
from hisim.postprocessing.energy_balance.check import EnergyBalanceError
from hisim.postprocessing.kpi_computation.tolerances import REL_TOL

#: The absolute floor of a monotone comparison: far below any KPI an assembly declares, far above
#: the float noise of a sum over a day of steps.
MONOTONE_ABS_FLOOR = 1e-9


class AssemblyCheckFailure(AssertionError):
    """A check of an assembly's test contract that does not hold, named by assembly, sample, check and subject."""


def failure(label: str, check: str, subject: str, problem: str) -> AssemblyCheckFailure:
    """The failure of one check: ``<assembly> sample <id> <check> <subject>: <problem>``."""
    return AssemblyCheckFailure(f"{label} {check} {subject}: {problem}")


def raised(error: BaseException) -> str:
    """An error as a failure names it: ``Type: message (raised at file.py:line)``, the innermost frame of its traceback.

    An error that was never raised (no traceback) is named without a location.
    """
    trace = error.__traceback__
    if trace is None:
        return f"{type(error).__name__}: {error}"
    while trace.tb_next is not None:
        trace = trace.tb_next
    return (
        f"{type(error).__name__}: {error} (raised at {Path(trace.tb_frame.f_code.co_filename).name}:{trace.tb_lineno})"
    )


def finished(run: IsolationRun, check: str, subject: str) -> IsolationRun:
    """The run, when it finished; the failure of a check that needs it, when it did not.

    Raises:
        AssemblyCheckFailure: When the run raised.
        IsolationRunError: When the run was released, or has no results although it raised nothing (a harness bug).
    """
    if run.error is not None:
        raise failure(run.label, check, subject, f"the run did not finish ({raised(run.error)})")
    if run.released or run.results is None:
        why = "was released" if run.released else "has no results although it raised nothing"
        raise IsolationRunError(f"{run.label}: the check '{check} {subject}' reads a run that {why}.")
    return run


def check_run(run: IsolationRun) -> None:
    """The run raised nothing; an open energy balance is :func:`check_energy_balance`'s finding."""
    if run.error is not None and not isinstance(run.error, EnergyBalanceError):
        raise failure(run.label, "run", "the isolation run", raised(run.error))


def check_energy_balance(run: IsolationRun) -> None:
    """The energy balance closed (``EnergyBalanceError``, raised by every run's post-processing)."""
    if isinstance(run.error, EnergyBalanceError):
        raise failure(run.label, "energy balance", "the isolation run", str(run.error))
    finished(run, "energy balance", "the isolation run")


def check_finite(run: IsolationRun) -> None:
    """Every numeric result column is finite at every step; a column of text has no finiteness to check."""
    results = finished(run, "finite", "every result column").results
    assert results is not None
    found: List[str] = []
    for column in results.columns:
        if pd.api.types.is_numeric_dtype(results[column]):
            values = results[column].to_numpy(dtype=float)
            bad = ~np.isfinite(values)
            if bad.any():
                first = int(np.argmax(bad))
                found.append(f"{column} is {values[first]} at step {first} ({int(bad.sum())} steps not finite)")
    if found:
        raise failure(run.label, "finite", "every result column", "; ".join(found))


def kpi_value(run: IsolationRun, check: str, subject: str, kpi: str, member: Optional[str]) -> float:
    """One KPI of the assembly under test: a member's by the finder's address fields, or the derived one."""
    finder = finished(run, check, subject).finder()
    try:
        if member is None:
            value = finder.value(name=kpi, derived=True)
        else:
            value = finder.value(name=kpi, import_key=SUBJECT, member=member, assembly=run.assembly)
    except ValueError as error:
        raise failure(run.label, check, subject, str(error)) from error
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise failure(run.label, check, subject, f"the KPI is {value!r}, no finite number")
    return float(value)


def band_problem(value: float, minimum: Optional[float], maximum: Optional[float]) -> Optional[str]:
    """Why a value lies outside a band, or ``None``."""
    if minimum is not None and value < minimum:
        return f"{value:g} lies below {minimum:g}"
    if maximum is not None and value > maximum:
        return f"{value:g} lies above {maximum:g}"
    return None


def _kpi_band(
    run: IsolationRun, check: str, kpi: str, member: Optional[str], minimum: Optional[float], maximum: Optional[float]
) -> None:
    """A KPI of the run within a band, the KPI half of ``bounds`` and all of ``expect``."""
    subject = f"{kpi} of {member or 'the system'} in {band(minimum, maximum)}"
    problem = band_problem(kpi_value(run, check, subject, kpi, member), minimum, maximum)
    if problem is not None:
        raise failure(run.label, check, subject, problem)


def check_bounds(run: IsolationRun, declaration: BoundsDeclaration) -> None:
    """One ``bounds`` entry: every step of an output's column, or a KPI, within its band.

    A column that is not numeric, or holds a value that is not finite, fails by name: no band holds it.
    """
    if declaration.output is None:
        _kpi_band(run, "bounds", declaration.kpi or "", declaration.member, declaration.min, declaration.max)
        return
    subject = f"{declaration.output} [{declaration.unit}] in {band(declaration.min, declaration.max)}"
    member, output = declaration.output.split(".", 1)
    results = finished(run, "bounds", subject).results
    assert results is not None
    column = next(
        (
            item.get_pretty_name()
            for item in run.outputs
            if item.component_name == run.runtime.get(member) and item.field_name == output
        ),
        None,
    )
    if column is None or column not in results:
        raise failure(run.label, "bounds", subject, f"'{member}' has no result column for {output}")
    if not pd.api.types.is_numeric_dtype(results[column]):
        raise failure(
            run.label, "bounds", subject, f"the column {column} holds {results[column].dtype} values, not numbers"
        )
    values = results[column].to_numpy(dtype=float)
    bad = ~np.isfinite(values)
    if bad.any():
        first = int(np.argmax(bad))
        raise failure(
            run.label,
            "bounds",
            subject,
            f"the column {column} is {values[first]} at step {first} ({int(bad.sum())} steps not finite)",
        )
    outside = np.zeros(len(values), dtype=bool)
    if declaration.min is not None:
        outside |= values < declaration.min
    if declaration.max is not None:
        outside |= values > declaration.max
    if outside.any():
        first = int(np.argmax(outside))
        raise failure(
            run.label,
            "bounds",
            subject,
            f"{int(outside.sum())} of {len(values)} steps leave the band; the first is step {first} with "
            f"{values[first]:g}; the column runs from {values.min():g} to {values.max():g}",
        )


def check_expect(run: IsolationRun, declaration: ExpectDeclaration) -> None:
    """One ``expect`` entry on the run at the defaults: the KPI within its band."""
    _kpi_band(run, "expect", declaration.kpi, declaration.member, declaration.min, declaration.max)


def equal(first: float, second: float, scale: float) -> bool:
    """Two values of a monotone series equal within ``REL_TOL`` of the series' magnitude ``scale`` plus the floor."""
    return abs(first - second) <= REL_TOL * scale + MONOTONE_ABS_FLOOR


def offending_pair(values: Sequence[float], direction: MonotoneDirection) -> Optional[Tuple[int, int]]:
    """The first pair of neighbours moving the wrong way, as indices; ``None`` when none does."""
    scale = max((abs(value) for value in values), default=0.0)
    for index, (first, second) in enumerate(zip(values, values[1:])):
        if equal(first, second, scale):
            continue
        if direction == MonotoneDirection.CONSTANT or (second > first) != (direction == MonotoneDirection.INCREASING):
            return index, index + 1
    return None


def check_monotone(label: str, declaration: MonotoneDeclaration, points: Sequence[Tuple[Any, float]]) -> None:
    """One sweep: the KPI over the parameter's rising values ``(parameter value, KPI)`` moves the declared way."""
    subject = (
        f"{declaration.parameter} rises: {declaration.kpi} of {declaration.member or 'the system'} "
        f"{declaration.direction.value}"
    )
    pair = offending_pair([value for _, value in points], declaration.direction)
    if pair is not None:
        (low, first), (high, second) = points[pair[0]], points[pair[1]]
        raise failure(
            label,
            "monotone",
            subject,
            f"{declaration.parameter} {low!r} → {high!r} moves {declaration.kpi} {first:.6g} → {second:.6g}, which is "
            f"not {declaration.direction.value}",
        )


#: Makes the isolation run of one sweep point: ``(point index, parameter values, label)`` to the run.
RunFactory = Callable[[int, Mapping[str, Any], str], IsolationRun]


def evaluate_monotone(
    run_factory: RunFactory, space: ParameterSpace, base: Sample, declaration: MonotoneDeclaration
) -> None:
    """One ``monotone`` entry from one base: runs each sweep point, reads its KPI, releases the run, compares.

    Raises:
        SamplerError: When the base admits no sweep of the parameter (the caller chose the base).
        AssemblyCheckFailure: When a point's KPI cannot be read, or the series moves the wrong way.
    """
    parameter = declaration.parameter
    points = sweep(space, base, parameter)
    if points is None:
        raise SamplerError(f"the sample {base.sample_id} of '{space.model.name}' admits no sweep of '{parameter}'.")
    label = f"{space.model.name} sweep of {parameter} from {base.sample_id}"
    subject = f"{declaration.kpi} of {declaration.member or 'the system'}"
    series: List[Tuple[Any, float]] = []
    for index, values in enumerate(points):
        run = run_factory(index, values, f"{label}, point {index}")
        try:
            series.append((values[parameter], kpi_value(run, "monotone", subject, declaration.kpi, declaration.member)))
        finally:
            run.release()
    check_monotone(label, declaration, series)


class MemberContract:
    """The parts of the test contract only the constructed members can check (D24)."""

    #: The units in which an output carries energy: a power or an energy, as an ``EnergyPort`` accepts.
    ENERGY_UNITS: ClassVar[FrozenSet[lt.Units]] = frozenset(
        {
            lt.Units.WATT,
            lt.Units.KILOWATT,
            lt.Units.WATT_HOUR,
            lt.Units.KWH,
            lt.Units.KWH_PER_TIMESTEP,
            lt.Units.JOULE,
            lt.Units.KILOJOULE,
        }
    )

    @classmethod
    def carries_energy_or_temperature(cls, load_type: lt.LoadTypes, unit: lt.Units) -> bool:
        """Whether an output carries energy (a power or energy unit) or a temperature (load type, °C or K)."""
        temperature = unit in (lt.Units.CELSIUS, lt.Units.KELVIN) or load_type == lt.LoadTypes.TEMPERATURE
        return unit in cls.ENERGY_UNITS or temperature

    @classmethod
    def violations(cls, run: IsolationRun, tests: TestContract) -> List[str]:
        """Every violation on the members this run constructed, each named by member and output or KPI."""
        bounded: Dict[Tuple[str, str], Optional[str]] = {}
        for bounds in tests.bounds:
            if bounds.output is not None:
                member, output = bounds.output.split(".", 1)
                bounded[(member, output)] = bounds.unit
        found: List[str] = []
        for member, component in run.members.items():
            outputs = {output.field_name: output for output in component.outputs}
            # An aggregator's dispatch output is named by the participant it steers, which the system decides.
            dispatch = {item.source_component_label for item in getattr(component, "my_component_outputs", ())}
            for name, output in outputs.items():
                if name in dispatch or (member, name) in bounded:
                    continue
                if cls.carries_energy_or_temperature(output.load_type, output.unit):
                    found.append(
                        f"the {output.unit.name} output {member}.{name} ({output.load_type.name}) has no bounds"
                    )
            for (bounded_member, name), unit in bounded.items():
                if bounded_member == member and name not in outputs:
                    found.append(
                        f"the bounds entry names {member}.{name}, which is no output of the constructed member"
                    )
                elif bounded_member == member and outputs[name].unit.name != unit:
                    found.append(
                        f"the bounds entry states {unit} for {member}.{name}, whose unit is {outputs[name].unit.name}"
                    )
        named: List[Tuple[Optional[str], str]] = [(item.member, item.kpi) for item in tests.bounds if item.kpi]
        named += [(item.member, item.kpi) for item in tests.monotone]
        named += [(item.member, item.kpi) for item in tests.expect]
        for reporter, kpi in dict.fromkeys(named):
            component = run.members.get(reporter or "")
            if component is None:
                continue
            reported = [entry.name for entry in component.get_component_kpi_entries(run.outputs, run.results)]
            if kpi not in reported:
                found.append(f"'{reporter}' reports no KPI '{kpi}' (it reports: {', '.join(reported) or 'none'})")
        return found


def check_member_contract(run: IsolationRun, tests: TestContract) -> None:
    """The member contract on the members of one finished run."""
    found = MemberContract.violations(finished(run, "member contract", "the constructed members"), tests)
    if found:
        raise failure(run.label, "member contract", "the constructed members", "; ".join(found))
