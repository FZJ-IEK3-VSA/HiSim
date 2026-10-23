"""What is checked on an isolation run and across a sweep (``assemblies_spec.md`` §9.4, D24).

Every check raises :class:`AssemblyCheckFailure` naming the assembly, the sample, the check and its
subject — ``pv/array sample s003 bounds PVSystem.ElectricityOutput [WATT] in [-100, 33000]: …`` —
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
- **The heat**, on every component of the system (:class:`HeatDelivery`): a heat input a component of
  the system serves by default is connected, so no heat the system makes is lost; and at the defaults
  a heated building keeps its room air, averaged over the day, within 1 K of its set heating
  temperature.

**A refused sample.** A run whose error was raised from a member's
:class:`~hisim.config.ConfigurationRefusedError` (:attr:`IsolationRun.refusal`) is a parameter
combination the component declines by design, such as a W55 SCOP above the W35 one. Every check of
it raises :class:`SampleRefused` instead of a failure; pytest reports the test as skipped with the
refusal. A monotone sweep drops the points a member refuses and checks the rest; when fewer than two
remain, the sweep raises :class:`SampleRefused` as well. Any other error of a run stays a failure.

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
from hisim.component import Component, ServedHeatInputs
from hisim.components.building.building import Building
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


class SampleRefused(Exception):
    """A sample, or a whole sweep, a member's component refuses at construction: handled, not failed.

    Example: ``air_source_heat_pump sample h007 refused by a member: HeatPump: the W55 SCOP 4.1 is
    above the W35 SCOP 3.2; …``. It is no ``AssertionError``, so a caller cannot mistake it for a
    failed check; the contract tests turn it into a skip that names the refusal.
    """


def refused(run: IsolationRun) -> SampleRefused:
    """The :class:`SampleRefused` of a run whose member refused its configuration."""
    return SampleRefused(f"{run.label} refused by a member: {run.refusal}")


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
        SampleRefused: When a member refused the sample's configuration at construction.
        AssemblyCheckFailure: When the run raised anything else.
        IsolationRunError: When the run was released, or has no results although it raised nothing (a harness bug).
    """
    if run.refusal is not None:
        raise refused(run)
    if run.error is not None:
        raise failure(run.label, check, subject, f"the run did not finish ({raised(run.error)})")
    if run.released or run.results is None:
        why = "was released" if run.released else "has no results although it raised nothing"
        raise IsolationRunError(f"{run.label}: the check '{check} {subject}' reads a run that {why}.")
    return run


def check_run(run: IsolationRun) -> None:
    """The run raised nothing; an open energy balance is :func:`check_energy_balance`'s finding.

    Raises:
        SampleRefused: When a member refused the sample's configuration at construction.
        AssemblyCheckFailure: When the run raised anything but that or an ``EnergyBalanceError``.
    """
    if run.refusal is not None:
        raise refused(run)
    if run.error is not None and not isinstance(run.error, EnergyBalanceError):
        raise failure(run.label, "run", "the isolation run", raised(run.error))


def check_energy_balance(run: IsolationRun) -> None:
    """The energy balance closed (``EnergyBalanceError``, raised by every run's post-processing)."""
    if run.refusal is not None:
        raise refused(run)
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
) -> List[Any]:
    """One ``monotone`` entry from one base: runs each sweep point, reads its KPI, releases the run, compares.

    A point a member refuses at construction (:attr:`IsolationRun.refusal`) is dropped, and the
    remaining points are compared. Example: the sweep of a heat pump's W35 SCOP from a base with
    W55 at 4.5 drops the points with W35 below 4.5.

    Returns:
        The swept parameter's values at the dropped points, rising; empty when none was refused.

    Raises:
        SamplerError: When the base admits no sweep of the parameter (the caller chose the base).
        SampleRefused: When refusals leave fewer than two points: the sweep is skipped.
        AssemblyCheckFailure: When a point's KPI cannot be read, or the series moves the wrong way.
    """
    parameter = declaration.parameter
    points = sweep(space, base, parameter)
    if points is None:
        raise SamplerError(f"the sample {base.sample_id} of '{space.model.name}' admits no sweep of '{parameter}'.")
    label = f"{space.model.name} sweep of {parameter} from {base.sample_id}"
    subject = f"{declaration.kpi} of {declaration.member or 'the system'}"
    series: List[Tuple[Any, float]] = []
    dropped: List[Any] = []
    for index, values in enumerate(points):
        run = run_factory(index, values, f"{label}, point {index}")
        try:
            if run.refusal is not None:
                dropped.append(values[parameter])
                continue
            series.append((values[parameter], kpi_value(run, "monotone", subject, declaration.kpi, declaration.member)))
        finally:
            run.release()
    if dropped and len(series) < 2:
        raise SampleRefused(
            f"{label}: a member refuses {len(dropped)} of {len(points)} points ({parameter} at "
            f"{', '.join(repr(value) for value in dropped)}), which leaves fewer than two to compare"
        )
    check_monotone(label, declaration, series)
    return dropped


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


class HeatDelivery(ServedHeatInputs):
    """Whether the heat an isolation system makes reaches what it heats (checks ``heat delivered``, ``room heated``).

    The rule for lost heat is the simulator's own (:class:`~hisim.component.ServedHeatInputs`, which a run applies
    when it connects the components): a component's heat input (load type ``HEATING``) is *served* by every other
    component of the system whose class its class's default connections name for that input. A served input left
    unconnected loses that heat. Example: beside ``heating/electric_resistive`` the ``Building``'s optional
    ``ThermalPowerDelivered`` is served by the subject's heater (``ElectricHeating.ThermalOutputShPower``);
    unconnected, the heater follows the building's demand all day while the room air falls from 20.7 to 8.9 °C
    (hisim-fob3). Because the input is optional, the mandatory-input check does not refuse that system; the run
    refuses it when it connects the components (:class:`~hisim.component.LostHeatError`), which the ``run`` check
    names, and this check applies the same rule to the components of a run that finished.

    A building is *heated* when its heat input is connected or served. At the defaults (the members sized for the
    house) its room air averaged over the day lies within :attr:`TOLERANCE_IN_KELVIN` of its set heating
    temperature. The day's mean, not every step: a boiler that charges the hot-water cylinder with priority lets the
    room air dip about 2 K for one step, which is the system's behaviour, not lost heat; a house without the heat
    averages about 6 K below its set temperature.
    """

    #: How far the day's mean room air may lie from the set heating temperature: the building's own tolerance of
    #: underheating (1 K), below which its comfort KPI counts the hours as cold.
    TOLERANCE_IN_KELVIN: ClassVar[float] = Building.UNDERHEATING_TOLERANCE_IN_KELVIN

    @classmethod
    def heated_buildings(cls, components: Mapping[str, Component]) -> List[str]:
        """The runtime names of the buildings whose heat input is connected or served by a component of the system."""
        by_class = cls.by_class(components)
        heated: List[str] = []
        for name, component in components.items():
            if not isinstance(component, Building):
                continue
            item = component.thermal_power_delivered_channel
            if item.src_object_name is not None or cls.sources(name, component, item, by_class):
                heated.append(name)
        return heated


def check_heat_delivered(run: IsolationRun) -> None:
    """No component of the system leaves a heat input unconnected that another component serves (:class:`HeatDelivery`).

    Raises:
        SampleRefused: When a member refused the sample's configuration at construction.
        AssemblyCheckFailure: When a served heat input is not connected, naming it and its sources.
    """
    found = HeatDelivery.lost(finished(run, "heat delivered", "every heat input").components)
    if found:
        raise failure(run.label, "heat delivered", "every heat input", "; ".join(found) + ": the heat never arrives")


def check_room_heated(run: IsolationRun) -> None:
    """Every heated building's room air, averaged over the day, within 1 K of its set heating temperature.

    Checked on the run at the defaults, whose members are sized for the house (:class:`HeatDelivery`).

    Raises:
        SampleRefused: When a member refused the sample's configuration at construction.
        AssemblyCheckFailure: When the day's mean room air of a heated building lies further from its set heating
            temperature, naming the mean, the set temperature and the range of the room air.
    """
    subject = f"the room air of every heated building within {HeatDelivery.TOLERANCE_IN_KELVIN:g} K"
    results = finished(run, "room heated", subject).results
    assert results is not None
    for name in HeatDelivery.heated_buildings(run.components):
        building = run.components[name]
        column = next(
            item.get_pretty_name()
            for item in run.outputs
            if item.component_name == name and item.field_name == Building.TemperatureIndoorAir
        )
        air_in_celsius = results[column].to_numpy(dtype=float)
        mean_in_celsius = float(air_in_celsius.mean())
        set_in_celsius = building.set_heating_temperature_in_celsius
        if abs(mean_in_celsius - set_in_celsius) > HeatDelivery.TOLERANCE_IN_KELVIN:
            raise failure(
                run.label,
                "room heated",
                subject,
                f"the room air of {name} averages {mean_in_celsius:.2f} °C over the day, "
                f"{mean_in_celsius - set_in_celsius:+.2f} K from its set heating temperature {set_in_celsius:g} °C "
                f"(it runs from {air_in_celsius.min():.2f} to {air_in_celsius.max():.2f} °C)",
            )
