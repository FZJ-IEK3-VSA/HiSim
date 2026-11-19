"""The acceptance ranges of Normative Annex A3 and the group accounting (spec §8.1, FR11, FR16).

Each range of the suite file takes one value from the annual loads of a time step: the case's own
annual heating or sensible cooling, or its difference to the parent case. The value is compared with
the range of the private reference file: ``pass`` within the bounds, ``fail`` outside, or
``known_deviation`` outside with an entry of the human in the register. A range of a case the suite
does not run is ``not_modelled``; one whose cases did not run or whose bounds are missing is
``skipped``. Per group and time step the accounting counts the ranges; a range not attempted counts as
not met, and the formal result is ``pass`` only when the group meets its minimum (Table A3-14).

The bounds are private: nothing here logs them, and every error names a range by key and metric only.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from hisim.verification.suite import METRICS, RangeCase, Suite

PASS = "pass"
FAIL = "fail"
KNOWN_DEVIATION = "known_deviation"
SKIPPED = "skipped"
NOT_MODELLED = "not_modelled"

#: The formal result of a group that does not meet its minimum.
NOT_PASSED = "not_passed"

#: The units the reference file must state; the annual loads are compared in them.
REFERENCE_UNITS = "MWh/year"

#: The summary keys of the annual loads a range takes its value from.
ANNUAL_KEYS = {"heating": "annual_heating_in_megawatt_hour", "cooling": "annual_sensible_cooling_in_megawatt_hour"}


class ReferenceFileError(ValueError):
    """The reference file cannot be read as ranges; the message names the range, never a bound."""


@dataclass(frozen=True)
class ReferenceRanges:
    """The bounds of the reference file by key and metric; None where the tables state no range.

    The bounds stay out of the representation, so that no traceback or test report shows them.
    """

    source: str
    ranges: Dict[str, Dict[str, Optional[Tuple[float, float]]]] = field(repr=False)

    @classmethod
    def load(cls, path: Path) -> "ReferenceRanges":
        """Read the private reference file."""
        with open(path, encoding="utf-8") as reference_file:
            mapping = json.load(reference_file)
        if not isinstance(mapping, dict):
            raise ReferenceFileError(f"{path} is not a reference file")
        return cls.from_mapping(mapping)

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any]) -> "ReferenceRanges":
        """Build the ranges, checking units, metrics and that every lower bound lies below its upper one."""
        if mapping.get("units") != REFERENCE_UNITS:
            raise ReferenceFileError(f"the reference file must state its units as '{REFERENCE_UNITS}'")
        entries = mapping.get("ranges")
        if not isinstance(entries, dict):
            raise ReferenceFileError("the reference file has no 'ranges' mapping")
        ranges: Dict[str, Dict[str, Optional[Tuple[float, float]]]] = {}
        for key, metrics in entries.items():
            if not isinstance(metrics, dict) or set(metrics) - set(METRICS):
                raise ReferenceFileError(f"range {key}: the metrics must be among {METRICS}")
            ranges[str(key)] = {}
            for metric in METRICS:
                bounds = metrics.get(metric)
                if bounds is None:
                    ranges[str(key)][metric] = None
                    continue
                if not isinstance(bounds, list) or len(bounds) != 2 or any(isinstance(bound, bool) or not isinstance(bound, (int, float)) for bound in bounds):
                    raise ReferenceFileError(f"range {key}, {metric}: the bounds must be two numbers or null")
                if bounds[0] > bounds[1]:
                    raise ReferenceFileError(f"range {key}, {metric}: the lower bound lies above the upper one")
                ranges[str(key)][metric] = (float(bounds[0]), float(bounds[1]))
        return cls(source=str(mapping.get("source", "")), ranges=ranges)

    def bounds(self, range_case: RangeCase) -> Optional[Tuple[float, float]]:
        """The bounds of a range, or None when the file has none for it."""
        return self.ranges.get(range_case.key, {}).get(range_case.metric)


@dataclass(frozen=True)
class RangeResult:
    """The outcome of one range at one time step.

    ``position`` is where the value lies in the range, 0 at the lower and 1 at the upper bound (below 0
    or above 1 outside); ``distance_to_nearer_bound`` is positive inside and negative outside, in
    MWh/year. Both are None when the range was not compared. The bounds, the position and the distance
    stay out of the representation: with the value, each of them gives a bound back.
    """

    group: str
    case: str
    parent: Optional[str]
    reverse: bool
    metric: str
    key: str
    timestep_s: int
    value: Optional[float]
    lower: Optional[float] = field(repr=False)
    upper: Optional[float] = field(repr=False)
    status: str
    position: Optional[float] = field(repr=False)
    distance_to_nearer_bound: Optional[float] = field(repr=False)
    note: str


def range_value(range_case: RangeCase, annual: Mapping[str, Mapping[str, float]]) -> Optional[float]:
    """The value a range takes from the annual loads by case, or None when a case it needs did not run."""
    own = annual.get(range_case.case, {}).get(range_case.metric)
    if range_case.parent is None:
        return own
    parent = annual.get(range_case.parent, {}).get(range_case.metric)
    if own is None or parent is None:
        return None
    return parent - own if range_case.reverse else own - parent


def compare_range(
    range_case: RangeCase,
    annual: Mapping[str, Mapping[str, float]],
    references: Optional[ReferenceRanges],
    suite: Suite,
    timestep_s: int,
) -> RangeResult:
    """Compare one range at one time step."""
    if range_case.not_modelled:
        return _result(range_case, timestep_s, NOT_MODELLED, "case not modelled (spec §2.3)")
    value = range_value(range_case, annual)
    if value is None:
        needed = [range_case.case] if range_case.parent is None else [range_case.case, range_case.parent]
        missing = [case for case in needed if range_case.metric not in annual.get(case, {})]
        return _result(range_case, timestep_s, SKIPPED, f"not run: case {', '.join(missing)}")
    bounds = None if references is None else references.bounds(range_case)
    if bounds is None:
        note = f"Reference data '{suite.reference_file}' not found (set HISIM_VERIFICATION_DATA)" if references is None else "no range in the reference file"
        return _result(range_case, timestep_s, SKIPPED, note, value=value)
    lower, upper = bounds
    if lower <= value <= upper:
        status, note = PASS, ""
    else:
        entry = suite.known_deviation(range_case.case, range_case.output, timestep_s)
        status, note = (FAIL, "") if entry is None else (KNOWN_DEVIATION, f"register entry {entry.entry_id}")
    return _result(range_case, timestep_s, status, note, value=value, bounds=bounds)


def _result(range_case: RangeCase, timestep_s: int, status: str, note: str, value: Optional[float] = None, bounds: Optional[Tuple[float, float]] = None) -> RangeResult:
    """The result of a range; with bounds, the value's position in the range and its distance to the nearer bound."""
    position: Optional[float] = None
    distance: Optional[float] = None
    if value is not None and bounds is not None:
        lower, upper = bounds
        width = upper - lower
        position = (value - lower) / width if width > 0 else (0.0 if value == lower else float("nan"))
        distance = min(value - lower, upper - value)
    return RangeResult(
        group=range_case.group,
        case=range_case.case,
        parent=range_case.parent,
        reverse=range_case.reverse,
        metric=range_case.metric,
        key=range_case.key,
        timestep_s=timestep_s,
        value=value,
        lower=None if bounds is None else bounds[0],
        upper=None if bounds is None else bounds[1],
        status=status,
        position=position,
        distance_to_nearer_bound=distance,
        note=note,
    )


@dataclass(frozen=True)
class GroupAccount:
    """The accounting of one group at one time step (spec §8.1, FR16)."""

    group: str
    timestep_s: int
    ranges_total: int
    minimum_required: int
    attempted: int
    met: int
    failed: int
    known_deviation: int
    skipped: int
    not_modelled: int
    formal_result: str

    @property
    def headline(self) -> str:
        """The headline of the verification: how many of the attempted ranges were met."""
        return f"attempted ranges met: {self.met} of {self.attempted}"


def account(suite: Suite, group_name: str, results: List[RangeResult], timestep_s: int) -> GroupAccount:
    """Count the ranges of a group at a time step; a range not attempted counts as not met."""
    group = next(group for group in suite.groups if group.name == group_name)
    statuses = [result.status for result in results if result.group == group_name and result.timestep_s == timestep_s]
    if len(statuses) != group.ranges_total:
        raise ValueError(f"group {group_name} at {timestep_s} s: {len(statuses)} results for {group.ranges_total} ranges")
    met = statuses.count(PASS)
    failed = statuses.count(FAIL)
    known = statuses.count(KNOWN_DEVIATION)
    return GroupAccount(
        group=group_name,
        timestep_s=timestep_s,
        ranges_total=group.ranges_total,
        minimum_required=group.minimum_required,
        attempted=met + failed + known,
        met=met,
        failed=failed,
        known_deviation=known,
        skipped=statuses.count(SKIPPED),
        not_modelled=statuses.count(NOT_MODELLED),
        formal_result=PASS if met >= group.minimum_required else NOT_PASSED,
    )


def annual_loads(summaries: Mapping[str, Mapping[str, Any]]) -> Dict[str, Dict[str, float]]:
    """The annual loads by case and metric from the summaries of one time step; free-float cases have none."""
    annual: Dict[str, Dict[str, float]] = {}
    for case, summary in summaries.items():
        loads = {metric: float(summary[key]) for metric, key in ANNUAL_KEYS.items() if key in summary}
        if loads:
            annual[case] = loads
    return annual


def evaluate(
    suite: Suite, summaries: Mapping[str, Mapping[str, Any]], references: Optional[ReferenceRanges], timestep_s: int
) -> Tuple[List[RangeResult], List[GroupAccount]]:
    """Compare every range of the suite at one time step and account for each group.

    Args:
        suite: the suite file.
        summaries: the summaries of the cases run at this time step, by case id.
        references: the private ranges, or None when the reference file is not available.
        timestep_s: the time step.

    Returns:
        Tuple[List[RangeResult], List[GroupAccount]]: one result per range in suite order, one account
            per group.
    """
    annual = annual_loads(summaries)
    results = [compare_range(range_case, annual, references, suite, timestep_s) for range_case in suite.range_cases]
    accounts = [account(suite, group.name, results, timestep_s) for group in suite.groups]
    return results, accounts
