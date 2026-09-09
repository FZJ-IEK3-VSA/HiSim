"""Tests for the CI resource monitoring: what it measures, what it flags and what it refuses to.

Two pieces are under test. The probe (``scripts/ci_resource_probe.py``) runs inside every CI job
and turns cgroup counters into a record; the collector (``scripts/ci_usage_report.py``) sweeps the
GitHub API, joins the probe's records onto the jobs that produced them and decides which jobs have
regressed.

The probe is tested for real rather than against a fake, because a fake would only prove that the
arithmetic is right and the arithmetic was never the risk: the risk is that a counter is unreadable
or misread on some machine. So one test allocates a known amount of memory between a start and a
report call and asks that the measured peak reflect it. The remaining tests cover the decisions,
where the failure modes are quieter -- a regression rule that fires on noise makes the report
useless, and one that never fires makes it pointless.

Each test states the failure mode it catches.
"""

# clean

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, List

import pytest

# Fast and dependency-free, so they run in the base tier that every pull request waits for.
pytestmark = pytest.mark.base


def _load(module_name: str) -> ModuleType:
    """Import one of the scripts by path.

    ``scripts/`` is not a package and is not importable by name from the test session, so the
    modules are loaded from their files. Cached in ``sys.modules`` under their own names so the
    dataclass-free module state is shared with anything else that loads them.
    """
    if module_name in sys.modules:
        return sys.modules[module_name]
    path = Path(__file__).resolve().parent.parent / "scripts" / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


probe = _load("ci_resource_probe")
collector = _load("ci_usage_report")


class TestProbeMeasures:
    """The probe has to produce numbers that move with what the job actually did."""

    # Large enough that neither the interpreter's own footprint nor a sampler's timing can
    # explain it away, small enough to allocate anywhere the tests run.
    ALLOCATION_BYTES = 256 * 1024 ** 2
    # How much of the allocation the peak must reflect; a cgroup that cannot be reset reports a
    # peak including the baseline, so the assertion is on the rise above the baseline.
    MINIMUM_OBSERVED_FRACTION = 0.6

    def test_peak_memory_follows_a_real_allocation(self, tmp_path: Path) -> None:
        """Catches a probe that reports a peak unrelated to the memory the job used.

        The whole point of the monitoring is that a job growing from 8 GB to 14 GB shows up. A
        probe whose peak does not move when 256 MB is allocated would report a flat line
        forever and nobody would notice until a job was killed.
        """
        instrument = probe.ResourceProbe(tmp_path)
        state = instrument.start()
        ballast = bytearray(self.ALLOCATION_BYTES)
        ballast[::4096] = b"\x01" * len(ballast[::4096])  # touch pages so they are really charged
        record = instrument.report(scope="unit-test")
        del ballast

        assert record["peak_memory_bytes"] is not None, "no memory counter was readable at all"
        baseline = state["baseline_memory_bytes"] or 0
        observed_rise = record["peak_memory_bytes"] - baseline
        assert observed_rise >= self.ALLOCATION_BYTES * self.MINIMUM_OBSERVED_FRACTION, (
            f"peak rose by {observed_rise} bytes for a {self.ALLOCATION_BYTES}-byte allocation"
        )

    def test_report_records_cpu_time_and_its_source(self, tmp_path: Path) -> None:
        """Catches a probe that silently reports a sampled number as an exact one.

        Durations from two different measurement methods are not comparable, so every record
        says which method produced it. A record that lost that label would let the collector
        build a trend line out of incomparable numbers.
        """
        instrument = probe.ResourceProbe(tmp_path)
        instrument.start()
        sum(index * index for index in range(200000))
        record = instrument.report(scope="unit-test")

        assert record["cpu_seconds"] is not None and record["cpu_seconds"] >= 0
        assert record["cpu_method"] in {method.value for method in probe.MeasurementMethod}
        assert record["peak_memory_method"] in {method.value for method in probe.MeasurementMethod}
        assert record["wall_seconds"] >= 0

    def test_a_broken_probe_never_fails_the_job(self, tmp_path: Path) -> None:
        """Catches the worst possible regression: monitoring that can turn a green job red.

        The probe runs in every job of every workflow. Reporting without a start call is the
        easiest way to break it, and it must still exit 0.
        """
        exit_code = probe.main(["report", "--state-dir", str(tmp_path / "never-written"),
                                "--out", str(tmp_path / "out.json")])
        assert exit_code == 0

    def test_summary_survives_a_record_with_nothing_in_it(self) -> None:
        """Catches a summary renderer that crashes on the gaps it is meant to describe."""
        rendered = probe.ResourceProbe.render_summary({"warnings": ["nothing was readable"]})
        assert "n/a" in rendered
        assert "nothing was readable" in rendered


class TestRegressionRules:
    """The rules decide what lands in the report, so they are tested from both sides."""

    WORKFLOW = "tests"
    JOB = "pytest (base)"
    BASELINE_SECONDS = 240.0
    GIB = 1024 ** 3

    def _statistics(self, baseline: List[float], recent: List[float],
                    conclusion: str = "success") -> Any:
        """Build a JobStatistics from two lists of durations in seconds."""
        stats = collector.JobStatistics(self.WORKFLOW, self.JOB)
        stats.baseline = [{"wall_seconds": value, "conclusion": conclusion} for value in baseline]
        stats.recent = [{"wall_seconds": value, "conclusion": conclusion} for value in recent]
        return stats

    def test_a_job_that_doubled_is_reported(self) -> None:
        """Catches rules so lax that the regression the whole report exists for slips through."""
        stats = self._statistics([240.0] * 6, [500.0] * 4)
        finding = collector.RegressionRules.duration_regression(stats)

        assert finding is not None
        assert finding["job_name"] == self.JOB
        assert finding["factor"] == pytest.approx(500.0 / 240.0)

    def test_ordinary_noise_is_not_reported(self) -> None:
        """Catches rules so tight that every busy afternoon fills the report with false alarms.

        Shared runners vary by tens of percent between runs for reasons nobody controls. A
        report that flags that is a report people stop opening, and then the real regression
        goes unread too.
        """
        stats = self._statistics([240.0, 260.0, 230.0, 250.0, 245.0, 255.0],
                                 [265.0, 250.0, 270.0])
        assert collector.RegressionRules.duration_regression(stats) is None

    def test_a_large_relative_jump_on_a_fast_job_is_not_reported(self) -> None:
        """Catches a rule that reports proportion without magnitude.

        A four-second job becoming a nine-second one has more than doubled and is worth
        nobody's morning; the absolute threshold is what keeps such jobs out.
        """
        stats = self._statistics([4.0] * 6, [9.0] * 4)
        assert collector.RegressionRules.duration_regression(stats) is None

    def test_too_few_runs_are_not_reported(self) -> None:
        """Catches a rule that calls a regression from one or two runs.

        A branch pushed twice yesterday is not evidence of anything, and a report that treats
        it as evidence is wrong more often than it is right.
        """
        stats = self._statistics([240.0] * 6, [600.0])
        assert collector.RegressionRules.duration_regression(stats) is None

    def test_cancelled_runs_do_not_move_the_baseline(self) -> None:
        """Catches a baseline poisoned by jobs that stopped early for unrelated reasons.

        Cancellation is routine here: pushing again cancels the previous run mid-flight. Those
        short durations say nothing about cost, and counting them would make a busy day look
        like an improvement and the next quiet day look like a regression.
        """
        stats = self._statistics([240.0] * 6, [500.0] * 4)
        stats.baseline += [{"wall_seconds": 3.0, "conclusion": "cancelled"} for _ in range(20)]

        assert stats.baseline_duration() == pytest.approx(self.BASELINE_SECONDS)

    def test_memory_growth_is_reported_in_its_own_right(self) -> None:
        """Catches a report that only watches the clock.

        A job can hold its duration exactly while its memory climbs towards the runner's
        ceiling, and the first symptom of crossing it is a job killed without a usable message.
        """
        stats = collector.JobStatistics(self.WORKFLOW, self.JOB)
        stats.baseline = [{"peak_memory_bytes": 4 * self.GIB, "conclusion": "success"}] * 6
        stats.recent = [{"peak_memory_bytes": 9 * self.GIB, "conclusion": "success"}] * 4

        finding = collector.RegressionRules.memory_regression(stats)
        assert finding is not None
        assert finding["factor"] == pytest.approx(2.25)

    def test_a_finding_names_the_branches_it_came_from(self) -> None:
        """Catches a report that cannot distinguish a landed regression from a WIP branch.

        Medians are taken across every branch, so a job flagged as slower may have regressed on
        main or may just be one person pushing a slow branch repeatedly. Without the branch
        names the reader has to open the runs to find out which.
        """
        stats = self._statistics([240.0] * 6, [500.0] * 4)
        for index, record in enumerate(stats.recent):
            record["branch"] = "feature-x" if index else "main"

        finding = collector.RegressionRules.duration_regression(stats)
        assert finding is not None
        assert finding["branches"][0] == "feature-x"
        assert "main" in finding["branches"]


class TestArtifactJoining:
    """A measurement joined onto the wrong job is worse than a measurement that was dropped."""

    RUN_ID = 99

    def _records(self) -> List[Dict[str, Any]]:
        """Return two sibling matrix jobs of one run, as the API describes them."""
        return [
            {"run_id": self.RUN_ID, "job_id": 1, "job_name": "pytest (base)", "runner_name": "runner-a"},
            {"run_id": self.RUN_ID, "job_id": 2, "job_name": "pytest (utsp)", "runner_name": "runner-b"},
        ]

    def test_the_runner_identifies_the_job(self) -> None:
        """Catches a join that ignores the one identifier both sides actually share."""
        metrics = {"runner_name": "runner-b", "job": "pytest", "scope": "utsp"}
        matched = collector.ResourceArtifactJoiner.match(metrics, self._records())
        assert matched is not None and matched["job_id"] == 2

    def test_the_name_identifies_the_job_when_the_runner_does_not(self) -> None:
        """Catches a join that gives up whenever the runner name is missing or reused.

        The API reports a rendered display name, ``pytest (base)``, while the probe knows only
        the job's YAML id and its scope. Matching on word content is what bridges the two.
        """
        metrics = {"runner_name": None, "job": "pytest", "scope": "base"}
        matched = collector.ResourceArtifactJoiner.match(metrics, self._records())
        assert matched is not None and matched["job_id"] == 1

    def test_an_ambiguous_measurement_is_dropped_rather_than_guessed(self) -> None:
        """Catches a join that attributes a measurement to whichever job it happened to see first.

        A wrongly attributed peak invents a memory regression in a job that never grew, and the
        person who investigates it finds nothing. Dropping it is counted and reported instead.
        """
        metrics = {"runner_name": "runner-c", "job": "pytest", "scope": ""}
        assert collector.ResourceArtifactJoiner.match(metrics, self._records()) is None


class TestIndexBookkeeping:
    """The index is what keeps a nightly sweep affordable, so its arithmetic has to hold."""

    WINDOW_DAYS = 30

    @staticmethod
    def _record(created: datetime, run_id: int = 1, **extra: Any) -> Dict[str, Any]:
        """Return a minimal indexable record created at ``created``."""
        record = {
            "run_id": run_id,
            "job_id": run_id * 10,
            "created_at": collector.Timestamps.format(created),
            "workflow": "tests",
            "job_name": "pytest (base)",
            "conclusion": "success",
            "wall_seconds": 100.0,
        }
        record.update(extra)
        return record

    def test_records_outside_the_window_are_pruned(self, tmp_path: Path) -> None:
        """Catches an index that grows without bound until the nightly job runs out of memory."""
        now = collector.Timestamps.now()
        index = collector.UsageIndex()
        index.add(self._record(now, run_id=1))
        index.add(self._record(now - timedelta(days=90), run_id=2))

        index.save(str(tmp_path / "index.json"), now - timedelta(days=self.WINDOW_DAYS))
        reloaded = collector.UsageIndex.load(str(tmp_path / "index.json"))
        assert [record["run_id"] for record in reloaded.values()] == [1]

    def test_the_sweep_starts_before_the_watermark(self) -> None:
        """Catches a sweep that skips runs which were still in flight when it last ran.

        A run created before the watermark but finished after it would never be recorded at
        all, and the longest runs -- the ones most worth tracking -- are exactly the ones in
        flight when a nightly job starts.
        """
        now = collector.Timestamps.now()
        index = collector.UsageIndex()
        index.add(self._record(now - timedelta(hours=1)))

        sweep_start = index.sweep_start(now - timedelta(days=self.WINDOW_DAYS))
        assert sweep_start < now - timedelta(hours=1)

    def test_a_cold_start_sweeps_the_whole_window(self) -> None:
        """Catches a first run, or one after the index expired, that silently reports nothing."""
        window_start = collector.Timestamps.now() - timedelta(days=self.WINDOW_DAYS)
        assert collector.UsageIndex().sweep_start(window_start) == window_start

    def test_a_corrupt_index_is_treated_as_no_index(self, tmp_path: Path) -> None:
        """Catches a nightly job that dies on a truncated artifact instead of starting over."""
        broken = tmp_path / "index.json"
        broken.write_text("{not json", encoding="utf-8")
        assert not list(collector.UsageIndex.load(str(broken)).values())

    def test_resource_data_survives_a_re_sweep(self) -> None:
        """Catches an overlap that discards measurements whose artifacts have since expired.

        The sweep re-reads the last few hours every night. If the fresh API record simply
        replaced the indexed one, every re-read would drop the memory figures joined onto it,
        and re-downloading them is the most expensive thing the collector can do.
        """
        now = collector.Timestamps.now()
        existing = self._record(now, peak_memory_bytes=5 * 1024 ** 3, cpu_seconds=42.0)
        fresh = self._record(now)
        collector.JobRecord.carry_resources(existing, fresh)

        assert fresh["peak_memory_bytes"] == 5 * 1024 ** 3
        assert fresh["cpu_seconds"] == 42.0

    def test_a_job_that_never_started_is_not_recorded_as_instant(self) -> None:
        """Catches skipped and queued jobs being folded in as zero-second successes.

        They would drag every median towards zero and invent a regression the next time the
        job actually ran.
        """
        run = {"id": 1, "name": "tests", "created_at": "2026-09-01T00:00:00Z"}
        skipped = {"id": 2, "name": "pytest (base)", "started_at": None, "completed_at": None}
        assert collector.JobRecord.from_api(run, skipped) is None


class TestReportRendering:
    """The report is the entire user interface, so it has to render whatever the sweep produced."""

    def _index(self, records: List[Dict[str, Any]]) -> Any:
        """Return an index holding ``records``."""
        index = collector.UsageIndex()
        for record in records:
            index.add(record)
        return index

    def test_a_quiet_night_still_says_so(self) -> None:
        """Catches a report that renders an empty page when nothing regressed.

        Most mornings nothing has regressed, and a report that says nothing on those mornings
        is indistinguishable from a broken one.
        """
        now = collector.Timestamps.now()
        record = {
            "run_id": 1, "job_id": 1, "created_at": collector.Timestamps.format(now),
            "workflow": "tests", "job_name": "pytest (base)", "conclusion": "success",
            "wall_seconds": 120.0,
        }
        report = collector.UsageReport(self._index([record]), now - timedelta(days=30),
                                       now - timedelta(days=3), {"requests_made": 3})
        rendered = report.render()

        assert "No job's median duration grew" in rendered
        assert "runner-minutes" in rendered

    def test_a_partial_sweep_is_visible_in_the_report(self) -> None:
        """Catches a truncated sweep being mistaken for a quiet week.

        When the request budget runs out the report covers less than it claims to, and a reader
        comparing it against yesterday's needs to know that before concluding anything.
        """
        now = collector.Timestamps.now()
        report = collector.UsageReport(self._index([]), now - timedelta(days=30),
                                       now - timedelta(days=3),
                                       {"partial": True, "stopped_because": "budget used up",
                                        "requests_made": 800})
        assert "partial sweep" in report.render()

    def test_the_index_round_trips_through_json(self, tmp_path: Path) -> None:
        """Catches an index that cannot be written and read back, which breaks every later night."""
        now = collector.Timestamps.now()
        index = self._index([{
            "run_id": 7, "job_id": 70, "created_at": collector.Timestamps.format(now),
            "workflow": "quality", "job_name": "mypy", "conclusion": "success",
            "wall_seconds": 61.0, "peak_memory_bytes": 2 * 1024 ** 3,
        }])
        path = tmp_path / "index.json"
        index.save(str(path), now - timedelta(days=30))

        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["schema"] == collector.UsageIndex.SCHEMA
        assert payload["watermark"] is not None
        reloaded = collector.UsageIndex.load(str(path))
        assert list(reloaded.values())[0]["peak_memory_bytes"] == 2 * 1024 ** 3


class TestTimestamps:
    """Every duration in the report is a subtraction of two parsed timestamps."""

    def test_the_api_format_is_parsed_as_utc(self) -> None:
        """Catches naive datetimes, which raise the moment they meet an aware one."""
        parsed = collector.Timestamps.parse("2026-09-09T05:49:30Z")
        assert parsed == datetime(2026, 9, 9, 5, 49, 30, tzinfo=timezone.utc)

    def test_an_absent_timestamp_is_not_an_error(self) -> None:
        """Catches a sweep that dies on a job the API described without timestamps."""
        assert collector.Timestamps.parse(None) is None
        assert collector.Timestamps.parse("not a timestamp") is None
