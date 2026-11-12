"""The private report, built from synthetic summaries on disk.

The suite and the bounds are the made-up ones of ``test_compare`` (hundreds of MWh), so no number
here can be mistaken for a range of the standard.
"""

import csv
import json
import pathlib
from typing import Any, Dict, Optional

import pytest

from hisim.verification import __main__ as verification_cli
from hisim.verification import export
from hisim.verification.suite import Suite
from tests.verification.synthetic import ranges, suite_mapping

pytestmark = pytest.mark.base


def occurrence(month: int, day: int, hour: int, value: float) -> Dict[str, Any]:
    """An occurrence as the metrics write it."""
    return {"value": value, "at": "", "month": month, "day": day, "hour": hour}


def write_summary(directory: pathlib.Path, case: str, timestep_s: int, loads: Optional[tuple] = None, commit: str = "abc1234") -> None:
    """A runner summary: the annual loads and peaks of a conditioned case, or the temperatures of a free-float one."""
    summary: Dict[str, Any] = {"case": case, "seconds_per_timestep": timestep_s, "hisim_commit": commit}
    summary["zone_air_temperature_in_celsius"] = {"mean": 21.5, "min": occurrence(1, 4, 7, 9.5), "max": occurrence(7, 20, 15, 38.5)}
    if loads is not None:
        summary["annual_heating_in_megawatt_hour"], summary["annual_sensible_cooling_in_megawatt_hour"] = loads
        summary["annual_peak_heating_in_kilowatt"] = occurrence(12, 31, 24, 2.5)
        summary["annual_peak_sensible_cooling_in_kilowatt"] = None
    path = directory / export.REPORT_DIRECTORY / export.SUMMARY_DIRECTORY / f"case{case.lower()}_{timestep_s}s.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary), encoding="utf-8")


@pytest.fixture(name="suite")
def fixture_suite() -> Suite:
    """The small suite with one free-float case."""
    mapping = suite_mapping()
    mapping["cases"] = ["1", "2", "3", "1FF"]
    return Suite.from_mapping(mapping)


def test_the_report_has_a_row_per_range_and_per_diagnostic(tmp_path: pathlib.Path, suite: Suite) -> None:
    """Five ranges per time step; loads and peaks of the cases that are not a range of their own; free-float temperatures."""
    for timestep_s in (3600, 60):
        write_summary(tmp_path, "1", timestep_s, (500.0, 250.0))
        write_summary(tmp_path, "2", timestep_s, (550.0, 200.0))
        write_summary(tmp_path, "1FF", timestep_s)

    report = export.build_report(tmp_path, suite, ranges())

    at_3600 = [row for row in report.rows if row.timestep_s == 3600]
    assert [(row.case, row.output, row.status) for row in at_3600 if row.criterion == export.RANGE_CRITERION] == [
        ("1", "annual_heating", "pass"),
        ("1", "annual_cooling", "pass"),
        ("2", "annual_heating_diff", "pass"),
        ("3", "annual_heating_diff", "skipped"),
        ("4", "annual_cooling_diff", "not_modelled"),
    ]
    info = {(row.case, row.output): row for row in at_3600 if row.status == export.INFO}
    assert ("1", "annual_heating") not in info
    assert info[("2", "annual_heating")].value == 550.0
    assert info[("2", "peak_heating")].note == "12-31 hour 24"
    assert info[("2", "peak_cooling")].value is None
    assert info[("1FF", "zone_air_temperature_max")].note == "07-20 hour 15"
    assert [account.timestep_s for account in report.accounts] == [3600, 60]
    assert report.metadata["timesteps_s"] == [3600, 60]
    assert report.metadata["hisim_commits_of_the_runs"] == ["abc1234"]


def test_the_four_files_are_written(tmp_path: pathlib.Path, suite: Suite) -> None:
    """results.csv with its columns, summary.json readable, summary.md private, deviations.md with the register."""
    write_summary(tmp_path, "1", 3600, (500.0, 250.0))
    report = export.build_report(tmp_path, suite, ranges())

    files = export.write_report(report, tmp_path)

    with open(files["results.csv"], encoding="utf-8", newline="") as results_file:
        rows = list(csv.DictReader(results_file))
    assert tuple(rows[0]) == export.RESULT_COLUMNS
    assert rows[0]["ref_min"] == "400.0" and rows[0]["status"] == "pass"
    summary = json.loads(files["summary.json"].read_text(encoding="utf-8"))
    assert summary["group_accounting"][0]["headline"] == "attempted ranges met: 2 of 2"
    markdown = files["summary.md"].read_text(encoding="utf-8")
    assert "(private)" in markdown and "Do not publish it" in markdown
    assert "attempted ranges met: 2 of 2" in markdown
    assert "more than one commit" not in markdown
    assert "# Deviation register: synthetic" in files["deviations.md"].read_text(encoding="utf-8")


def test_runs_from_several_commits_are_flagged(tmp_path: pathlib.Path, suite: Suite) -> None:
    """A folder that mixes runs of two commits says so."""
    write_summary(tmp_path, "1", 3600, (500.0, 250.0), commit="abc1234")
    write_summary(tmp_path, "2", 3600, (550.0, 200.0), commit="def5678")

    report = export.build_report(tmp_path, suite, None)

    assert report.metadata["hisim_commits_of_the_runs"] == ["abc1234", "def5678"]
    assert "more than one commit" in export.summary_markdown(report)
    assert {row.status for row in report.rows if row.criterion == export.RANGE_CRITERION} == {"skipped", "not_modelled"}


def test_the_time_step_consistency_gives_the_spread(tmp_path: pathlib.Path, suite: Suite) -> None:
    """The largest difference between the time steps relative to the largest value, in percent."""
    write_summary(tmp_path, "1", 3600, (500.0, 250.0))
    write_summary(tmp_path, "1", 60, (510.0, 250.0))

    lines = export._consistency(export.build_report(tmp_path, suite, ranges()))  # pylint: disable=protected-access

    assert ["1", "annual heating [MWh]", "500.000", "510.000", "1.96"] in lines
    assert ["1", "annual sensible cooling [MWh]", "250.000", "250.000", "0.00"] in lines


def test_a_folder_without_summaries_is_refused(tmp_path: pathlib.Path, suite: Suite) -> None:
    """Nothing to report is an error, not an empty report."""
    with pytest.raises(FileNotFoundError, match="no case summaries"):
        export.build_report(tmp_path, suite, None)


def test_an_output_directory_inside_the_repository_is_refused(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The private report never lands in the checkout; a folder outside is accepted."""
    monkeypatch.setenv(verification_cli.DATA_ENVIRONMENT_VARIABLE, str(tmp_path))
    inside = verification_cli.REPOSITORY_ROOT / "verification_runs_must_not_exist"

    assert verification_cli.main(["--report-only", "--output-dir", str(inside)]) == 2
    assert not inside.exists()
    assert verification_cli.inside_repository(verification_cli.REPOSITORY_ROOT)
    assert not verification_cli.inside_repository(tmp_path)
