"""The private report of a suite run (spec §10, decisions D22 and D23).

``BESTEST_output/`` already holds the parameters, the time series and one summary per case and time
step, which the runner writes. This module reads those summaries back, compares the ranges of each
time step (``compare``) and writes beside them:

- ``results.csv``: one row per case, time step and output: the ranges with their bounds and status,
  and HiSim's diagnostics with the status ``info``;
- ``summary.json``: the metadata, the rows, the group accounting and the register;
- ``summary.md``: the same for a human reader, with the time-step consistency of spec §8.4;
- ``deviations.md``: the deviation register and the outputs the suite does not report.

The report is private: it carries the range bounds, the statuses and the group accounting (D22).
It is written below the directory the command was pointed at, never into a repository; there is
no public variant yet (D23). Because it is built from the summaries on disk, it covers every case
run into that directory, and it names the commits they came from.
"""

import csv
import datetime
import json
import platform
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from hisim.energy_system.metadata import RunMetadata
from hisim.hisim_commit import HiSimCommit
from hisim.verification import compare, metrics, runner
from hisim.verification.suite import Suite

#: The report's folder below the output directory, and the folder of the runner's summaries in it.
REPORT_DIRECTORY = "BESTEST_output"
SUMMARY_DIRECTORY = "summary"

#: The columns of ``results.csv``. Spec §10 also lists ``ref_value`` and ``deviation_*``, which
#: belong to single reference values (the deferred ISO suite); a range reports its position instead.
RESULT_COLUMNS = (
    "suite",
    "group",
    "case",
    "parent_case",
    "timestep_s",
    "output",
    "unit",
    "value",
    "ref_min",
    "ref_max",
    "criterion",
    "position_in_range",
    "distance_to_nearer_bound",
    "status",
    "note",
)

INFO = "info"
RANGE_CRITERION = "Annex A3 range"


@dataclass(frozen=True)
class ResultRow:
    """One row of ``results.csv``."""

    suite: str
    group: str
    case: str
    parent_case: str
    timestep_s: int
    output: str
    unit: str
    value: Optional[float]
    ref_min: Optional[float]
    ref_max: Optional[float]
    criterion: str
    position_in_range: Optional[float]
    distance_to_nearer_bound: Optional[float]
    status: str
    note: str


@dataclass(frozen=True)
class Report:
    """What the four files say: metadata, rows, accounts per time step and the summaries read."""

    suite: Suite
    metadata: Dict[str, Any]
    rows: Tuple[ResultRow, ...]
    accounts: Tuple[compare.GroupAccount, ...]
    summaries: Dict[int, Dict[str, Dict[str, Any]]]


def load_summaries(output_directory: Path) -> Dict[int, Dict[str, Dict[str, Any]]]:
    """The runner's summaries below the output directory, by time step and case."""
    summaries: Dict[int, Dict[str, Dict[str, Any]]] = {}
    for path in sorted((output_directory / REPORT_DIRECTORY / SUMMARY_DIRECTORY).glob("*.json")):
        with open(path, encoding="utf-8") as summary_file:
            summary = json.load(summary_file)
        summaries.setdefault(int(summary["seconds_per_timestep"]), {})[str(summary["case"])] = summary
    return summaries


def _when(found: Optional[Mapping[str, Any]]) -> str:
    """Month, day and hour of an occurrence, Hour 1 being 00:00 to 01:00."""
    if not found:
        return ""
    return f"{found['month']:02d}-{found['day']:02d} hour {found['hour']}"


def _groups_of_cases(suite: Suite) -> Dict[str, str]:
    """The group each case's ranges belong to; a free-float case belongs to none."""
    groups: Dict[str, str] = {}
    for range_case in suite.range_cases:
        for case in (range_case.case, range_case.parent):
            if case is not None:
                groups.setdefault(case, range_case.group)
    return groups


def range_rows(suite: Suite, results: List[compare.RangeResult]) -> List[ResultRow]:
    """The ranges of one time step as rows; a difference range's output ends in ``_diff``."""
    return [
        ResultRow(
            suite=suite.name,
            group=result.group,
            case=result.case,
            parent_case=result.parent or "",
            timestep_s=result.timestep_s,
            output=f"annual_{result.metric}" if result.parent is None else f"annual_{result.metric}_diff",
            unit="MWh",
            value=result.value,
            ref_min=result.lower,
            ref_max=result.upper,
            criterion=RANGE_CRITERION,
            position_in_range=result.position,
            distance_to_nearer_bound=result.distance_to_nearer_bound,
            status=result.status,
            note=result.note,
        )
        for result in results
    ]


def info_rows(suite: Suite, case: str, timestep_s: int, summary: Mapping[str, Any]) -> List[ResultRow]:
    """The diagnostics of one case and time step: loads and peaks, or the free-float temperatures (7.3.1, 7.3.6)."""
    group = _groups_of_cases(suite).get(case, "")
    own_ranges = {range_case.output for range_case in suite.range_cases if range_case.case == case and range_case.parent is None}
    quantities: List[Tuple[str, str, Optional[float], str]] = []
    if "annual_heating_in_megawatt_hour" in summary:
        for output, key in (("annual_heating", "annual_heating_in_megawatt_hour"), ("annual_cooling", "annual_sensible_cooling_in_megawatt_hour")):
            if output not in own_ranges:
                quantities.append((output, "MWh", summary[key], ""))
        for output, key in (("peak_heating", "annual_peak_heating_in_kilowatt"), ("peak_cooling", "annual_peak_sensible_cooling_in_kilowatt")):
            found = summary.get(key)
            quantities.append((output, "kW", None if found is None else found["value"], _when(found)))
    else:
        air = summary[metrics.ZONE_AIR_TEMPERATURE_KEY]
        quantities.append(("zone_air_temperature_mean", "degC", air["mean"], ""))
        quantities.append(("zone_air_temperature_min", "degC", air["min"]["value"], _when(air["min"])))
        quantities.append(("zone_air_temperature_max", "degC", air["max"]["value"], _when(air["max"])))
    return [ResultRow(suite.name, group, case, "", timestep_s, output, unit, value, None, None, "", None, None, INFO, note) for output, unit, value, note in quantities]


def build_report(output_directory: Path, suite: Suite, references: Optional[compare.ReferenceRanges]) -> Report:
    """Read the summaries below the output directory and compare each time step.

    Args:
        output_directory: the directory the runs were written to.
        suite: the suite file.
        references: the private ranges, or None when the reference file is not available.

    Returns:
        Report: everything the four files say.

    Raises:
        FileNotFoundError: when no summary is found.
    """
    summaries = load_summaries(output_directory)
    if not summaries:
        raise FileNotFoundError(f"no case summaries below {output_directory / REPORT_DIRECTORY / SUMMARY_DIRECTORY}")
    rows: List[ResultRow] = []
    accounts: List[compare.GroupAccount] = []
    for timestep_s in sorted(summaries, reverse=True):
        results, timestep_accounts = compare.evaluate(suite, summaries[timestep_s], references, timestep_s)
        rows.extend(range_rows(suite, results))
        for case in sorted(summaries[timestep_s]):
            rows.extend(info_rows(suite, case, timestep_s, summaries[timestep_s][case]))
        accounts.extend(timestep_accounts)
    commits = sorted({str(summary.get("hisim_commit", HiSimCommit.UNKNOWN)) for by_case in summaries.values() for summary in by_case.values()})
    metadata = {
        "report": "private: Annex A3 range bounds, statuses and group accounting (decision D22); not a claim of conformity with Standard 140",
        "suite": suite.name,
        "standard": suite.standard,
        "hisim_version": RunMetadata.hisim_version(),
        "hisim_commit_of_the_report": HiSimCommit.or_unknown(),
        "hisim_commits_of_the_runs": commits,
        "created": datetime.datetime.now().isoformat(timespec="seconds"),
        "python_version": platform.python_version(),
        "timesteps_s": sorted(summaries, reverse=True),
        "cases_by_timestep": {str(timestep_s): sorted(by_case) for timestep_s, by_case in sorted(summaries.items(), reverse=True)},
        "passes": runner.PASSES,
        "aggregation": metrics.AGGREGATION,
        "weather": "HiSim's weather pipeline; register entries weather_interpolation and weather_start_padding",
        "reference_sources": list(suite.reference_sources),
        "reference_file_found": references is not None,
        "gating": suite.gating,
    }
    return Report(suite=suite, metadata=metadata, rows=tuple(rows), accounts=tuple(accounts), summaries=summaries)


def _number(value: Optional[float], digits: int = 3) -> str:
    """A number for the Markdown tables; empty for none."""
    return "" if value is None else f"{value:.{digits}f}"


def _table(header: List[str], lines: List[List[str]]) -> List[str]:
    """A Markdown table."""
    return ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)] + ["| " + " | ".join(line) + " |" for line in lines]


def summary_markdown(report: Report) -> str:
    """The private report for a human reader."""
    data = report.metadata
    text = [
        f"# Verification report: {report.suite.name} (private)",
        "",
        "Private: this report carries the Annex A3 range bounds, the statuses and the group accounting",
        "(decision D22). Do not publish it. It is not a claim of conformity with Standard 140.",
        "",
    ]
    text += _table(
        ["Item", "Value"],
        [
            ["Standard", data["standard"]],
            ["HiSim", f"{data['hisim_version']}, commit of the report {data['hisim_commit_of_the_report']}, of the runs {', '.join(data['hisim_commits_of_the_runs'])}"],
            ["Created", data["created"]],
            ["Time steps", ", ".join(f"{timestep_s} s" for timestep_s in data["timesteps_s"])],
            ["Passes per run", str(data["passes"])],
            ["Aggregation", data["aggregation"]],
            ["Reference file", "found" if data["reference_file_found"] else "not found: every range is skipped"],
            ["Gating", "on" if data["gating"] else "off"],
        ],
    )
    if len(data["hisim_commits_of_the_runs"]) > 1:
        text += ["", "**The runs come from more than one commit.** Run into a fresh directory to compare like with like."]

    text += ["", "## Group accounting", ""]
    text += _table(
        ["Time step", "Group", "Total", "Minimum", "Attempted", "Met", "Failed", "Known deviation", "Skipped", "Not modelled", "Formal result", "Headline"],
        [
            [
                f"{account.timestep_s} s",
                account.group,
                str(account.ranges_total),
                str(account.minimum_required),
                str(account.attempted),
                str(account.met),
                str(account.failed),
                str(account.known_deviation),
                str(account.skipped),
                str(account.not_modelled),
                account.formal_result,
                account.headline,
            ]
            for account in report.accounts
        ],
    )

    for timestep_s in data["timesteps_s"]:
        rows = [row for row in report.rows if row.timestep_s == timestep_s]
        text += ["", f"## Ranges at {timestep_s} s", ""]
        text += _table(
            ["Group", "Range", "Output", "Value [MWh]", "Lower", "Upper", "Position", "Status", "Note"],
            [
                [
                    row.group,
                    row.case if not row.parent_case else f"{row.case} vs {row.parent_case}",
                    row.output,
                    _number(row.value),
                    _number(row.ref_min),
                    _number(row.ref_max),
                    _number(row.position_in_range, 2),
                    row.status,
                    row.note,
                ]
                for row in rows
                if row.criterion == RANGE_CRITERION
            ],
        )
        text += ["", f"## Diagnostics at {timestep_s} s", ""]
        text += _table(
            ["Case", "Output", "Value", "Unit", "When"],
            [[row.case, row.output, _number(row.value), row.unit, row.note] for row in rows if row.status == INFO],
        )

    text += ["", "## Time-step consistency (spec §8.4)", ""]
    text += _table(["Case", "Quantity", *[f"{timestep_s} s" for timestep_s in data["timesteps_s"]], "Largest difference [%]"], _consistency(report))

    text += ["", "## Deviation register", "", "Each entry in full in `deviations.md`.", ""]
    text += _table(
        ["Entry", "Category", "Clause", "Approved by"],
        [[entry.entry_id, entry.category, entry.standard_clause, entry.approved_by or "not yet"] for entry in report.suite.register],
    )
    return "\n".join(text) + "\n"


def _consistency(report: Report) -> List[List[str]]:
    """Annual loads and the mean free-float temperature per case at every time step, with their spread."""
    timesteps = report.metadata["timesteps_s"]
    cases = sorted({case for by_case in report.summaries.values() for case in by_case})
    lines: List[List[str]] = []
    quantities = (
        ("annual heating [MWh]", lambda summary: summary.get("annual_heating_in_megawatt_hour")),
        ("annual sensible cooling [MWh]", lambda summary: summary.get("annual_sensible_cooling_in_megawatt_hour")),
        ("mean zone air temperature [degC]", lambda summary: None if "annual_heating_in_megawatt_hour" in summary else summary[metrics.ZONE_AIR_TEMPERATURE_KEY]["mean"]),
    )
    for case in cases:
        for name, read in quantities:
            values = [read(report.summaries[timestep_s][case]) if case in report.summaries[timestep_s] else None for timestep_s in timesteps]
            present = [value for value in values if value is not None]
            if not present:
                continue
            spread = ""
            if len(present) > 1 and max(abs(value) for value in present) > 0:
                spread = f"{100.0 * (max(present) - min(present)) / max(abs(value) for value in present):.2f}"
            lines.append([case, name, *[_number(value) for value in values], spread])
    return lines


def deviations_markdown(suite: Suite) -> str:
    """The deviation register and the outputs the suite does not report (spec §8.3, FR18)."""
    text = [
        f"# Deviation register: {suite.name}",
        "",
        f"Every departure of the suite from {suite.standard}, with the clause, the standard's requirement,",
        "HiSim's treatment and the human's approval (spec §8.3).",
    ]
    for entry in suite.register:
        text += [
            "",
            f"## {entry.entry_id} ({entry.category})",
            "",
            f"- Clause: {entry.standard_clause}",
            f"- Requirement: {entry.description}",
            f"- HiSim: {entry.hisim_treatment}",
            f"- Approved: {entry.approved_by or 'not yet'}" + (f", {entry.approved_on}" if entry.approved_on else ""),
        ]
        if entry.category == "known_deviation":
            text.append(f"- Applies to: case {entry.case}, {entry.output}" + (f", {entry.timestep_s} s" if entry.timestep_s else ", every time step"))
    for title, missing in (("Outputs not available", suite.not_available), ("Outputs not applicable", suite.not_applicable)):
        text += ["", f"## {title}", ""]
        text += _table(["Clause", "Cases", "Quantity", "Reason"], [[item.clause, ", ".join(item.cases), item.quantity, item.reason] for item in missing])
    return "\n".join(text) + "\n"


def write_report(report: Report, output_directory: Path) -> Dict[str, Path]:
    """Write the four files of the private report below the output directory."""
    root = output_directory / REPORT_DIRECTORY
    root.mkdir(parents=True, exist_ok=True)
    files = {name: root / name for name in ("results.csv", "summary.json", "summary.md", "deviations.md")}
    with open(files["results.csv"], "w", encoding="utf-8", newline="") as results_file:
        writer = csv.DictWriter(results_file, fieldnames=RESULT_COLUMNS)
        writer.writeheader()
        for row in report.rows:
            writer.writerow(asdict(row))
    summary = {
        "metadata": report.metadata,
        "results": [asdict(row) for row in report.rows],
        "group_accounting": [{**asdict(account), "headline": account.headline} for account in report.accounts],
        "deviation_register": [asdict(entry) for entry in report.suite.register],
    }
    with open(files["summary.json"], "w", encoding="utf-8") as summary_file:
        json.dump(summary, summary_file, indent=2)
    with open(files["summary.md"], "w", encoding="utf-8") as markdown_file:
        markdown_file.write(summary_markdown(report))
    with open(files["deviations.md"], "w", encoding="utf-8") as deviations_file:
        deviations_file.write(deviations_markdown(report.suite))
    return files
