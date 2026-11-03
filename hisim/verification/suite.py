"""The suite file: cases, the structure of the acceptance ranges, the outputs and the deviation register.

``suites/<name>/suite.yaml`` is public and carries structure only (spec §7.3): which cases run, how
the ranges of Normative Annex A3 are grouped and which value each range takes (a case's own annual
load, or its difference to a parent case), which outputs of Section 7.3 the suite reports and which
HiSim cannot produce, and the register of every deviation from the standard (spec §8.3). The range
values themselves are private; ``compare`` reads them.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

import yaml

#: Where the suite folders sit.
SUITES_DIRECTORY = Path(__file__).parent / "suites"

#: The two quantities a range of Annex A3 is stated for: annual heating and annual sensible cooling.
METRICS = ("heating", "cooling")

#: The categories of the deviation register (spec §8.3).
CATEGORIES = ("model", "input_mapping", "known_deviation")

#: The hourly quantities of Table 7-47 the suite can report: the zone load (heating positive,
#: sensible cooling negative) and the zone air temperature.
DAILY_QUANTITIES = ("load", "zone_air_temperature")

_DAY = re.compile(r"^(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])$")


class SuiteFileError(ValueError):
    """The suite file states something this module cannot take; the message names the entry."""


def _entries(mapping: Mapping[str, Any], key: str, where: str) -> List[Mapping[str, Any]]:
    """Return a list of mappings, naming the field when it is not one."""
    entries = mapping.get(key, [])
    if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
        raise SuiteFileError(f"{where}: '{key}' must be a list of mappings")
    return entries


def _case_ids(entry: Mapping[str, Any], where: str) -> Tuple[str, ...]:
    """Return the entry's case ids as text."""
    cases = entry.get("cases")
    if not isinstance(cases, list) or not cases:
        raise SuiteFileError(f"{where}: 'cases' must be a non-empty list")
    return tuple(str(case) for case in cases)


@dataclass(frozen=True)
class RangeCase:
    """One acceptance range: the case, the metric and, for a difference, the parent case.

    The value of a range is the case's annual load, or its difference to the parent: case minus
    parent, or parent minus case where the tables state it that way round (``reverse``, Case 910).
    A range marked ``not_modelled`` belongs to a case the suite does not run (spec §2.3).
    """

    group: str
    case: str
    metric: str
    parent: Optional[str]
    reverse: bool
    not_modelled: bool

    @classmethod
    def from_mapping(cls, group: str, mapping: Mapping[str, Any], where: str) -> "RangeCase":
        """Build a range from its suite-file entry."""
        status = mapping.get("status")
        if status not in (None, "not_modelled"):
            raise SuiteFileError(f"{where}: the only status a range can carry is 'not_modelled', not {status!r}")
        range_case = cls(
            group=group,
            case=str(mapping.get("case", "")),
            metric=str(mapping.get("metric", "")),
            parent=None if mapping.get("parent") is None else str(mapping["parent"]),
            reverse=bool(mapping.get("reverse", False)),
            not_modelled=status == "not_modelled",
        )
        if not range_case.case:
            raise SuiteFileError(f"{where}: a range names no case")
        if range_case.metric not in METRICS:
            raise SuiteFileError(f"{where}: range of case {range_case.case}: the metric must be one of {METRICS}")
        if range_case.reverse and range_case.parent is None:
            raise SuiteFileError(f"{where}: range of case {range_case.case}: a reversed range needs a parent")
        return range_case

    @property
    def key(self) -> str:
        """The range's key in the reference file: the case, or minuend and subtrahend of the difference."""
        if self.parent is None:
            return self.case
        if self.reverse:
            return f"{self.parent}-{self.case}"
        return f"{self.case}-{self.parent}"

    @property
    def output(self) -> str:
        """The output a known deviation names for this range (spec §8.3)."""
        return f"annual_{self.metric}"


@dataclass(frozen=True)
class Group:
    """A group of ranges and how many of them a program must meet (Table A3-14)."""

    name: str
    tables: Tuple[str, ...]
    ranges_total: int
    minimum_required: int
    range_cases: Tuple[RangeCase, ...]

    @classmethod
    def from_mapping(cls, name: str, mapping: Mapping[str, Any]) -> "Group":
        """Build a group from its suite-file entry, checking its counts against its ranges."""
        where = f"group {name}"
        group = cls(
            name=name,
            tables=tuple(str(table) for table in mapping.get("tables", [])),
            ranges_total=int(mapping.get("ranges_total", -1)),
            minimum_required=int(mapping.get("minimum_required", -1)),
            range_cases=tuple(RangeCase.from_mapping(name, entry, where) for entry in _entries(mapping, "range_cases", where)),
        )
        if group.ranges_total != len(group.range_cases):
            raise SuiteFileError(f"{where}: ranges_total is {group.ranges_total}, the group lists {len(group.range_cases)} ranges")
        if not 0 <= group.minimum_required <= group.ranges_total:
            raise SuiteFileError(f"{where}: minimum_required must lie within 0 and ranges_total")
        keys = [(range_case.key, range_case.metric) for range_case in group.range_cases]
        if len(set(keys)) != len(keys):
            raise SuiteFileError(f"{where}: a range is listed twice")
        return group


@dataclass(frozen=True)
class RegisterEntry:
    """One entry of the deviation register (spec §8.3).

    A ``known_deviation`` also names the case, the output (``annual_heating`` or ``annual_cooling``)
    and optionally the time step it applies to; only the human adds one.
    """

    entry_id: str
    category: str
    standard_clause: str
    description: str
    hisim_treatment: str
    approved_by: Optional[str]
    approved_on: Optional[str]
    case: Optional[str]
    output: Optional[str]
    timestep_s: Optional[int]

    FIELDS = ("id", "category", "standard_clause", "description", "hisim_treatment", "approved_by")
    KNOWN_DEVIATION_FIELDS = ("case", "output")

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any]) -> "RegisterEntry":
        """Build an entry from the register, checking its fields by category."""
        where = f"register entry {mapping.get('id', '?')}"
        missing = [name for name in cls.FIELDS if name not in mapping]
        if missing:
            raise SuiteFileError(f"{where}: the fields {missing} are missing")
        category = mapping["category"]
        if category not in CATEGORIES:
            raise SuiteFileError(f"{where}: the category must be one of {CATEGORIES}, not {category!r}")
        is_known_deviation = category == "known_deviation"
        if is_known_deviation and any(mapping.get(name) is None for name in cls.KNOWN_DEVIATION_FIELDS):
            raise SuiteFileError(f"{where}: a known deviation names its case and its output")
        if not is_known_deviation and any(name in mapping for name in (*cls.KNOWN_DEVIATION_FIELDS, "timestep_s")):
            raise SuiteFileError(f"{where}: only a known deviation names a case, an output or a time step")
        timestep = mapping.get("timestep_s")
        return cls(
            entry_id=str(mapping["id"]),
            category=str(category),
            standard_clause=str(mapping["standard_clause"]),
            description=str(mapping["description"]),
            hisim_treatment=str(mapping["hisim_treatment"]),
            approved_by=None if mapping["approved_by"] is None else str(mapping["approved_by"]),
            approved_on=None if mapping.get("approved_on") is None else str(mapping["approved_on"]),
            case=None if mapping.get("case") is None else str(mapping["case"]),
            output=None if mapping.get("output") is None else str(mapping["output"]),
            timestep_s=None if timestep is None else int(timestep),
        )

    def covers(self, case: str, output: str, timestep_s: int) -> bool:
        """Whether this is a known deviation of that case's output at that time step."""
        return self.category == "known_deviation" and self.case == case and self.output == output and (self.timestep_s is None or self.timestep_s == timestep_s)


@dataclass(frozen=True)
class DailyHourlyOutput:
    """A row of Table 7-47 the suite reports: 24 hourly values of a quantity on each of the days."""

    cases: Tuple[str, ...]
    quantity: str
    days: Tuple[str, ...]

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any], where: str) -> "DailyHourlyOutput":
        """Build a row from its suite-file entry; a day is written MM-DD."""
        output = cls(cases=_case_ids(mapping, where), quantity=str(mapping.get("quantity", "")), days=tuple(str(day) for day in mapping.get("days", [])))
        if output.quantity not in DAILY_QUANTITIES:
            raise SuiteFileError(f"{where}: the quantity must be one of {DAILY_QUANTITIES}, not {output.quantity!r}")
        if not output.days or not all(_DAY.match(day) for day in output.days):
            raise SuiteFileError(f"{where}: the days must be written MM-DD")
        return output


@dataclass(frozen=True)
class MissingOutput:
    """An output of Section 7.3 the suite does not report, with the reason."""

    clause: str
    cases: Tuple[str, ...]
    quantity: str
    reason: str

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any], where: str) -> "MissingOutput":
        """Build the entry from the suite file."""
        return cls(
            clause=str(mapping.get("clause", "")), cases=_case_ids(mapping, where), quantity=str(mapping.get("quantity", "")), reason=str(mapping.get("reason", ""))
        )


@dataclass(frozen=True)
class RequestedOutputs:
    """What a case reports beyond the annual quantities: the hourly days and the temperature bins."""

    daily_hourly: Tuple[Tuple[str, Tuple[str, ...]], ...]
    temperature_bins_in_celsius: Optional[Tuple[int, int]]


@dataclass(frozen=True)
class Suite:
    """A suite file, read and checked."""

    name: str
    standard: str
    reference_file: str
    cases: Tuple[str, ...]
    gating: bool
    groups: Tuple[Group, ...]
    register: Tuple[RegisterEntry, ...]
    daily_hourly: Tuple[DailyHourlyOutput, ...]
    temperature_bin_cases: Tuple[str, ...]
    temperature_bins_in_celsius: Tuple[int, int]
    not_available: Tuple[MissingOutput, ...]
    not_applicable: Tuple[MissingOutput, ...]

    @classmethod
    def load(cls, name: str = "ashrae140") -> "Suite":
        """Read the suite file of the named suite."""
        path = SUITES_DIRECTORY / name / "suite.yaml"
        with open(path, encoding="utf-8") as suite_file:
            mapping = yaml.safe_load(suite_file)
        if not isinstance(mapping, dict):
            raise SuiteFileError(f"{path} is not a suite file")
        return cls.from_mapping(mapping)

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any]) -> "Suite":
        """Build the suite from its file content.

        Raises:
            SuiteFileError: for a group whose counts do not match its ranges, a range naming an
                unknown metric, a register entry with missing or misplaced fields, or an output
                entry naming a case the suite does not run.
        """
        name = str(mapping.get("suite", ""))
        groups_entry = mapping.get("groups")
        outputs = mapping.get("outputs", {})
        if not name or not isinstance(groups_entry, dict) or not isinstance(outputs, dict):
            raise SuiteFileError("a suite file names its suite and has 'groups' and 'outputs' mappings")
        bins = outputs.get("temperature_bins", {})
        suite = cls(
            name=name,
            standard=str(mapping.get("standard", "")),
            reference_file=str(mapping.get("reference_file", "")),
            cases=tuple(str(case) for case in mapping.get("cases", [])),
            gating=bool(mapping.get("gating", False)),
            groups=tuple(Group.from_mapping(group_name, entry) for group_name, entry in groups_entry.items()),
            register=tuple(RegisterEntry.from_mapping(entry) for entry in _entries(mapping, "deviation_register", "deviation_register")),
            daily_hourly=tuple(DailyHourlyOutput.from_mapping(entry, "outputs.daily_hourly") for entry in _entries(outputs, "daily_hourly", "outputs")),
            temperature_bin_cases=tuple(str(case) for case in bins.get("cases", [])),
            temperature_bins_in_celsius=(int(bins.get("lowest_in_celsius", 0)), int(bins.get("highest_in_celsius", 0))),
            not_available=tuple(MissingOutput.from_mapping(entry, "outputs.not_available") for entry in _entries(outputs, "not_available", "outputs")),
            not_applicable=tuple(MissingOutput.from_mapping(entry, "outputs.not_applicable") for entry in _entries(outputs, "not_applicable", "outputs")),
        )
        if not suite.reference_file:
            raise SuiteFileError(f"suite {name}: 'reference_file' names no file")
        if suite.temperature_bins_in_celsius[0] > suite.temperature_bins_in_celsius[1]:
            raise SuiteFileError(f"suite {name}: the lowest temperature bin lies above the highest")
        named = [case for output in suite.daily_hourly for case in output.cases] + list(suite.temperature_bin_cases)
        unknown = sorted(set(named) - set(suite.cases))
        if unknown:
            raise SuiteFileError(f"suite {name}: the outputs name cases the suite does not run: {unknown}")
        return suite

    @property
    def range_cases(self) -> Tuple[RangeCase, ...]:
        """Every range of every group, in file order."""
        return tuple(range_case for group in self.groups for range_case in group.range_cases)

    def outputs_for(self, case_id: str) -> RequestedOutputs:
        """The hourly days and the temperature bins a case reports."""
        daily = tuple((output.quantity, output.days) for output in self.daily_hourly if case_id in output.cases)
        bins = self.temperature_bins_in_celsius if case_id in self.temperature_bin_cases else None
        return RequestedOutputs(daily_hourly=daily, temperature_bins_in_celsius=bins)

    def known_deviation(self, case: str, output: str, timestep_s: int) -> Optional[RegisterEntry]:
        """The register's known deviation for that case, output and time step, if the human entered one."""
        for entry in self.register:
            if entry.covers(case, output, timestep_s):
                return entry
        return None

    def reference_template(self) -> Dict[str, Dict[str, Optional[List[None]]]]:
        """The ranges of the reference file by key and metric, every value null (spec §7.3).

        A key with only one metric among the ranges has the other set to null as well: the tables
        state no range for it (Case 650 and 950 have cooling only).
        """
        template: Dict[str, Dict[str, Optional[List[None]]]] = {}
        for range_case in self.range_cases:
            entry = template.setdefault(range_case.key, {metric: None for metric in METRICS})
            entry[range_case.metric] = [None, None]
        return template
