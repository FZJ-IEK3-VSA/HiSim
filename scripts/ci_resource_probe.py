#!/usr/bin/env python3
"""Measure one GitHub Actions job's wall time, CPU time and peak memory.

Called twice per job by ``.github/actions/resource-monitor``: once as ``start`` before the
job's real work, once as ``report`` after it. ``start`` records a baseline in a state file;
``report`` reads that baseline back, computes the deltas and writes a JSON record plus a
short table into the job summary::

    python3 scripts/ci_resource_probe.py start  --state-dir "$RUNNER_TEMP/ci-resource-monitor"
    python3 scripts/ci_resource_probe.py report --state-dir "$RUNNER_TEMP/ci-resource-monitor" \\
        --scope base --out metrics.json

The numbers come from cgroup v2, which the kernel maintains for free: ``memory.peak`` is a
high-water mark that catches allocation spikes a sampler would sleep straight through, and
``cpu.stat``'s ``usage_usec`` is real CPU time rather than the wall time GitHub charges for.
The gap between the two is the interesting part -- a forty-minute job pinning one of four
cores is forty runner-minutes spent for ten cores-minutes of work. Where the cgroup files
are not readable the probe falls back to sampling ``/proc/meminfo`` and ``/proc/stat``, and
records which method produced each number so a reader can tell an exact peak from a sampled
one.

Deliberately standard-library only, and deliberately incapable of failing the job: it runs
in every job of every workflow, before dependencies are installed, and a monitoring probe
that can break a build is worse than no monitoring at all. Every error is caught, recorded
in the record's ``warnings`` and reported with exit status 0 unless ``--strict`` is given.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class ProbeMode(str, Enum):
    """The two points in a job's life at which the probe runs.

    ``START`` runs before the job's work and only records where the counters stood;
    ``REPORT`` runs after it -- under ``if: always()``, so a failed job is measured too --
    and turns the difference into the record. ``SAMPLE`` is not called by the action: it is
    the background loop :class:`FallbackSampler` re-executes this script as when the cgroup
    files are unreadable.
    """

    START = "start"
    REPORT = "report"
    SAMPLE = "sample"


class MeasurementMethod(str, Enum):
    """How a given number was obtained, recorded alongside it in the JSON record.

    A consumer needs this to know what it is comparing: ``CGROUP_PEAK`` is an exact
    kernel-maintained high-water mark for this job alone, while ``SAMPLED_MEMINFO`` is the
    largest value a twice-a-second sampler happened to observe for the whole machine, so it
    both misses short spikes and includes the runner agent. Mixing the two in one trend line
    would invent regressions that never happened.
    """

    CGROUP_PEAK = "cgroup.memory.peak"
    CGROUP_CURRENT = "cgroup.memory.current.sampled"
    SAMPLED_MEMINFO = "proc.meminfo.sampled"
    CGROUP_CPU_STAT = "cgroup.cpu.stat"
    PROC_STAT = "proc.stat"
    UNAVAILABLE = "unavailable"


class CgroupV2Reader:
    """Reads the memory and CPU counters of the cgroup this process belongs to.

    Resolves the cgroup directory once and then serves ``memory.peak``, ``memory.current``
    and ``cpu.stat`` from it. Two layouts have to work. In a container job the container has
    its own cgroup namespace, ``/proc/self/cgroup`` reads ``0::/`` and the mounted root is
    already the container's own cgroup, which does carry the memory files. On a plain runner
    the process sits in a named sub-cgroup of the host, whose path ``/proc/self/cgroup``
    spells out; the real root is skipped there because the kernel exposes no memory
    controller files on it.

    Attributes:
        path: the resolved cgroup directory, or None when no usable one was found.
    """

    SYSFS_ROOT = Path("/sys/fs/cgroup")
    PROC_SELF_CGROUP = Path("/proc/self/cgroup")
    # The file whose presence marks a directory as a cgroup with the memory controller
    # enabled; the real root of a cgroup v2 hierarchy has cpu.stat but never this.
    MEMORY_PROBE_FILE = "memory.current"

    def __init__(self) -> None:
        self.path: Optional[Path] = self._resolve_path()

    @classmethod
    def _resolve_path(cls) -> Optional[Path]:
        """Return the cgroup directory to read, or None if none carries the memory files."""
        candidates: List[Path] = []
        try:
            for line in cls.PROC_SELF_CGROUP.read_text(encoding="utf-8").splitlines():
                hierarchy, _, cgroup_path = line.partition("::")
                if hierarchy == "0" and cgroup_path:
                    candidates.append(cls.SYSFS_ROOT / cgroup_path.lstrip("/"))
        except OSError:
            pass
        candidates.append(cls.SYSFS_ROOT)
        for candidate in candidates:
            if (candidate / cls.MEMORY_PROBE_FILE).exists():
                return candidate
        return None

    def _read_int(self, filename: str) -> Optional[int]:
        """Return the single integer in ``filename``, or None if it cannot be read.

        ``memory.peak`` and ``memory.current`` can also hold the literal ``max``, which is
        not a measurement and is reported as unavailable rather than as a number.
        """
        if self.path is None:
            return None
        try:
            return int((self.path / filename).read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None

    def peak_memory_bytes(self) -> Optional[int]:
        """Return the cgroup's memory high-water mark in bytes, or None if unreadable."""
        return self._read_int("memory.peak")

    def current_memory_bytes(self) -> Optional[int]:
        """Return the cgroup's current memory charge in bytes, or None if unreadable."""
        return self._read_int("memory.current")

    def cpu_usage_seconds(self) -> Optional[float]:
        """Return cumulative CPU time of the cgroup in seconds, or None if unreadable.

        Reads ``usage_usec`` from ``cpu.stat``, which counts user and system time of every
        process in the cgroup since it was created, so only the difference between two reads
        is meaningful.
        """
        if self.path is None:
            return None
        try:
            for line in (self.path / "cpu.stat").read_text(encoding="utf-8").splitlines():
                key, _, value = line.partition(" ")
                if key == "usage_usec":
                    return int(value) / 1e6
        except (OSError, ValueError):
            return None
        return None

    def reset_peak(self) -> bool:
        """Try to zero ``memory.peak`` so the reported peak covers only this job.

        Kernels from 6.8 accept a write here as "forget the old high-water mark". Where the
        write is refused -- an older kernel, or a read-only mount in a container -- the peak
        still includes whatever the runner agent touched before the job started, which is why
        ``start`` also records ``memory.current`` as a baseline for the reader to judge by.

        Returns:
            True if the reset was accepted, False otherwise.
        """
        if self.path is None:
            return False
        try:
            (self.path / "memory.peak").write_text("", encoding="utf-8")
            return True
        except OSError:
            return False


class SystemFacts:
    """Whole-machine readings used both as fallbacks and as denominators.

    The memory total turns a peak in bytes into the fraction of the runner it consumed,
    which is the form worth alerting on: a standard GitHub-hosted Ubuntu runner has 16 GB,
    and a job at 90% of it is one dataset away from being killed with no useful message.
    """

    PROC_MEMINFO = Path("/proc/meminfo")
    PROC_STAT = Path("/proc/stat")
    KIBIBYTE = 1024

    @classmethod
    def _meminfo(cls) -> Dict[str, int]:
        """Return ``/proc/meminfo`` as a mapping of field name to bytes."""
        values: Dict[str, int] = {}
        try:
            for line in cls.PROC_MEMINFO.read_text(encoding="utf-8").splitlines():
                key, _, rest = line.partition(":")
                parts = rest.split()
                if parts and parts[0].isdigit():
                    values[key] = int(parts[0]) * cls.KIBIBYTE
        except OSError:
            pass
        return values

    @classmethod
    def memory_total_bytes(cls) -> Optional[int]:
        """Return the machine's total RAM in bytes, or None if it cannot be read."""
        return cls._meminfo().get("MemTotal")

    @classmethod
    def memory_used_bytes(cls) -> Optional[int]:
        """Return memory in use machine-wide, as total minus available, in bytes.

        ``MemAvailable`` is the kernel's own estimate of what a new allocation could get
        without swapping, so this counts the caches that would be evicted as free -- the same
        arithmetic ``free`` reports and the closest whole-machine analogue of a cgroup charge.
        """
        info = cls._meminfo()
        total, available = info.get("MemTotal"), info.get("MemAvailable")
        if total is None or available is None:
            return None
        return total - available

    @classmethod
    def cpu_seconds(cls) -> Optional[float]:
        """Return cumulative busy CPU time of the whole machine in seconds, or None.

        Sums every field of the aggregate ``cpu`` line of ``/proc/stat`` except idle and
        iowait. Machine-wide, so on a shared machine it would count strangers; on a
        single-job runner VM the only stranger is the runner agent itself.
        """
        try:
            for line in cls.PROC_STAT.read_text(encoding="utf-8").splitlines():
                if not line.startswith("cpu "):
                    continue
                fields = [int(value) for value in line.split()[1:]]
                if len(fields) < 5:
                    return None
                busy = fields[0] + fields[1] + fields[2] + sum(fields[5:])
                return busy / os.sysconf("SC_CLK_TCK")
        except (OSError, ValueError):
            return None
        return None

    @classmethod
    def cpu_count(cls) -> int:
        """Return the number of CPUs the job can schedule on, at least 1."""
        try:
            return len(os.sched_getaffinity(0)) or 1
        except (AttributeError, OSError):
            return os.cpu_count() or 1


class FallbackSampler:
    """A background loop that tracks peak memory when the cgroup high-water mark is missing.

    Re-executes this script in ``sample`` mode as a detached child, which polls twice a
    second and keeps the largest reading it has seen in a file the ``report`` step reads back.
    The file is rewritten on every observation rather than at exit, so the measurement
    survives the child being killed, the job being cancelled, or the runner tearing the
    process group down without warning.

    What it measures is strictly worse than the cgroup's own counter -- it is machine-wide
    rather than job-scoped, and a spike shorter than the interval is invisible to it -- so it
    is only ever started when :class:`CgroupV2Reader` came up empty, and what it produced is
    labelled as sampled in the record.
    """

    INTERVAL_SECONDS = 0.5
    PID_FILENAME = "sampler.pid"
    PEAK_FILENAME = "sampler-peak.json"
    # How long report() waits for the sampler to write its last observation and exit.
    SHUTDOWN_TIMEOUT_SECONDS = 3.0

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.pid_file = state_dir / self.PID_FILENAME
        self.peak_file = state_dir / self.PEAK_FILENAME

    def start(self) -> Optional[int]:
        """Spawn the detached sampling child and return its pid, or None if it failed.

        The child is started in its own session so that neither the step's shell exiting nor
        the runner's process-group cleanup between steps takes it down with them. It outlives
        this call by design, which is why it is not opened as a context manager.
        """
        try:
            self.peak_file.write_text(json.dumps({"peak_bytes": None, "samples": 0}), encoding="utf-8")
            process = subprocess.Popen(  # pylint: disable=consider-using-with
                [sys.executable, str(Path(__file__).resolve()), ProbeMode.SAMPLE.value,
                 "--state-dir", str(self.state_dir)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            return None
        self.pid_file.write_text(str(process.pid), encoding="utf-8")
        return process.pid

    def run(self) -> None:
        """Poll until killed, writing the running maximum to the peak file after each read.

        Prefers the cgroup's current charge over the machine-wide figure when the controller
        is readable but its high-water mark is not, since a sampled job-scoped number still
        beats a sampled machine-wide one.
        """
        cgroup = CgroupV2Reader()
        use_cgroup = cgroup.current_memory_bytes() is not None
        method = MeasurementMethod.CGROUP_CURRENT if use_cgroup else MeasurementMethod.SAMPLED_MEMINFO
        peak: Optional[int] = None
        samples = 0
        while True:
            reading = cgroup.current_memory_bytes() if use_cgroup else SystemFacts.memory_used_bytes()
            if reading is not None:
                samples += 1
                if peak is None or reading > peak:
                    peak = reading
                try:
                    self.peak_file.write_text(
                        json.dumps({"peak_bytes": peak, "samples": samples, "method": method.value})
                    )
                except OSError:
                    pass
            time.sleep(self.INTERVAL_SECONDS)

    def stop(self) -> Tuple[Optional[int], Optional[MeasurementMethod]]:
        """Terminate the sampler and return the peak it observed and how it observed it.

        Returns:
            A ``(peak_bytes, method)`` pair, either element None when no sampler ran or it
            never managed a reading.
        """
        try:
            pid = int(self.pid_file.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            pid = 0
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
                deadline = time.monotonic() + self.SHUTDOWN_TIMEOUT_SECONDS
                while time.monotonic() < deadline:
                    try:
                        os.kill(pid, 0)
                    except OSError:
                        break
                    time.sleep(0.05)
            except OSError:
                pass
        try:
            payload = json.loads(self.peak_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None, None
        method = payload.get("method")
        return payload.get("peak_bytes"), MeasurementMethod(method) if method else None


class JobContext:
    """The GitHub Actions environment variables that identify the job being measured.

    Collected so a record can be attributed later without the collector having to guess:
    which workflow and job produced it, on which branch and commit, and in which attempt of
    which run. Everything is optional -- the probe is also runnable on a laptop, where none
    of these are set -- and missing values are simply absent from the record.
    """

    ENVIRONMENT_FIELDS = {
        "repository": "GITHUB_REPOSITORY",
        "workflow": "GITHUB_WORKFLOW",
        "job": "GITHUB_JOB",
        "run_id": "GITHUB_RUN_ID",
        "run_number": "GITHUB_RUN_NUMBER",
        "run_attempt": "GITHUB_RUN_ATTEMPT",
        "event": "GITHUB_EVENT_NAME",
        "ref": "GITHUB_REF",
        "ref_name": "GITHUB_REF_NAME",
        "head_ref": "GITHUB_HEAD_REF",
        "sha": "GITHUB_SHA",
        "actor": "GITHUB_ACTOR",
        "runner_os": "RUNNER_OS",
        "runner_arch": "RUNNER_ARCH",
        "runner_name": "RUNNER_NAME",
    }
    INTEGER_FIELDS = frozenset({"run_id", "run_number", "run_attempt"})

    @classmethod
    def collect(cls) -> Dict[str, Any]:
        """Return the set environment fields as a dict, integers parsed where they belong."""
        context: Dict[str, Any] = {}
        for field, variable in cls.ENVIRONMENT_FIELDS.items():
            value = os.environ.get(variable)
            if not value:
                continue
            if field in cls.INTEGER_FIELDS:
                try:
                    context[field] = int(value)
                except ValueError:
                    context[field] = value
                continue
            context[field] = value
        return context


class ResourceProbe:
    """Runs one half of the measurement and owns the state file the two halves share.

    ``start`` writes the state file, ``report`` consumes it. Keeping the file in
    ``RUNNER_TEMP`` rather than the workspace means a job that checks out, wipes or moves
    the workspace between the two calls still measures correctly.
    """

    SCHEMA = "hisim.ci_resource_probe/1"
    STATE_FILENAME = "state.json"
    SUMMARY_HEADING = "CI resource usage"
    BYTES_PER_GIB = 1024 ** 3

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.state_file = state_dir / self.STATE_FILENAME
        self.sampler = FallbackSampler(state_dir)

    @staticmethod
    def _now_iso() -> str:
        """Return the current UTC time as an ISO-8601 string with a trailing Z."""
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def start(self) -> Dict[str, Any]:
        """Record where the counters stand and start the sampler if one is needed.

        Returns:
            The state dict that was written, for the caller to log.
        """
        self.state_dir.mkdir(parents=True, exist_ok=True)
        cgroup = CgroupV2Reader()
        warnings: List[str] = []
        peak_reset = cgroup.reset_peak()
        has_peak = cgroup.peak_memory_bytes() is not None
        if not has_peak:
            warnings.append(
                "cgroup memory.peak is unreadable; falling back to a 0.5 s sampler, which "
                "misses shorter spikes and measures the whole machine"
            )
            self.sampler.start()
        elif not peak_reset:
            warnings.append(
                "cgroup memory.peak could not be reset; the peak may include memory in use "
                "before the job's work started (see baseline_memory_bytes)"
            )
        state: Dict[str, Any] = {
            "schema": self.SCHEMA,
            "started_at": self._now_iso(),
            "monotonic_start": time.monotonic(),
            "cgroup_path": str(cgroup.path) if cgroup.path else None,
            "cgroup_has_peak": has_peak,
            "peak_was_reset": peak_reset,
            "baseline_memory_bytes": cgroup.current_memory_bytes() or SystemFacts.memory_used_bytes(),
            "baseline_cpu_seconds": cgroup.cpu_usage_seconds(),
            "baseline_system_cpu_seconds": SystemFacts.cpu_seconds(),
            "warnings": warnings,
        }
        self.state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
        return state

    def report(self, scope: str) -> Dict[str, Any]:
        """Turn the counters' movement since ``start`` into the finished record.

        Args:
            scope: what distinguishes this job from its matrix siblings, carried into the
                record so a per-cell trend can be followed across runs.

        Returns:
            The record dict, which the caller writes to disk and renders into the summary.

        Raises:
            FileNotFoundError: if ``start`` never ran, or ran with a different state
                directory -- caught by :func:`main` and reported as a warning.
        """
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        cgroup = CgroupV2Reader()
        warnings: List[str] = list(state.get("warnings", []))
        wall_seconds = max(0.0, time.monotonic() - float(state["monotonic_start"]))

        cpu_seconds, cpu_method = self._measure_cpu(cgroup, state, warnings)
        peak_bytes, peak_method = self._measure_peak(cgroup, state, warnings)

        memory_total = SystemFacts.memory_total_bytes()
        cpu_count = SystemFacts.cpu_count()
        record: Dict[str, Any] = {
            "schema": self.SCHEMA,
            "scope": scope,
            "started_at": state["started_at"],
            "finished_at": self._now_iso(),
            "wall_seconds": round(wall_seconds, 2),
            "cpu_seconds": round(cpu_seconds, 2) if cpu_seconds is not None else None,
            "cpu_method": cpu_method.value,
            "cpu_count": cpu_count,
            "peak_memory_bytes": peak_bytes,
            "peak_memory_method": peak_method.value,
            "baseline_memory_bytes": state.get("baseline_memory_bytes"),
            "memory_total_bytes": memory_total,
            "in_container": Path("/.dockerenv").exists(),
            "warnings": warnings,
        }
        record.update(JobContext.collect())
        if cpu_seconds is not None and wall_seconds > 0:
            record["cpu_efficiency"] = round(cpu_seconds / (wall_seconds * cpu_count), 4)
        if peak_bytes is not None and memory_total:
            record["peak_memory_fraction"] = round(peak_bytes / memory_total, 4)
        return record

    def _measure_cpu(
        self, cgroup: CgroupV2Reader, state: Dict[str, Any], warnings: List[str]
    ) -> Tuple[Optional[float], MeasurementMethod]:
        """Return CPU seconds consumed since ``start`` and the method that produced them.

        Prefers the cgroup's own counter, which covers this job and nothing else. The
        machine-wide ``/proc/stat`` difference is the fallback, and on a runner VM that runs
        one job at a time it is close, with the runner agent's own work folded in.
        """
        baseline = state.get("baseline_cpu_seconds")
        current = cgroup.cpu_usage_seconds()
        if baseline is not None and current is not None:
            return max(0.0, current - float(baseline)), MeasurementMethod.CGROUP_CPU_STAT
        system_baseline = state.get("baseline_system_cpu_seconds")
        system_current = SystemFacts.cpu_seconds()
        if system_baseline is not None and system_current is not None:
            return max(0.0, system_current - float(system_baseline)), MeasurementMethod.PROC_STAT
        warnings.append("no CPU counter was readable; cpu_seconds is unavailable")
        return None, MeasurementMethod.UNAVAILABLE

    def _measure_peak(
        self, cgroup: CgroupV2Reader, state: Dict[str, Any], warnings: List[str]
    ) -> Tuple[Optional[int], MeasurementMethod]:
        """Return the job's peak memory in bytes and the method that produced it.

        The kernel's high-water mark is used whenever ``start`` found one. Otherwise the
        sampler is stopped and its largest observation used instead; if that produced nothing
        either, the last resort is the current reading, which is a floor on the peak rather
        than the peak and is labelled accordingly.
        """
        if state.get("cgroup_has_peak"):
            peak = cgroup.peak_memory_bytes()
            if peak is not None:
                return peak, MeasurementMethod.CGROUP_PEAK
        sampled_peak, sampled_method = self.sampler.stop()
        if sampled_peak is not None and sampled_method is not None:
            return int(sampled_peak), sampled_method
        current = cgroup.current_memory_bytes() or SystemFacts.memory_used_bytes()
        if current is not None:
            warnings.append("no peak was captured; reporting the memory in use at the end of the job")
            return current, MeasurementMethod.CGROUP_CURRENT
        warnings.append("no memory counter was readable; peak_memory_bytes is unavailable")
        return None, MeasurementMethod.UNAVAILABLE

    @classmethod
    def render_summary(cls, record: Dict[str, Any]) -> str:
        """Render the record as the markdown table written to the job summary.

        Kept to a handful of rows on purpose: this lands at the bottom of every job page in
        the repository, so it has to be glanceable and it has to be short.
        """
        def gib(value: Optional[int]) -> str:
            return "n/a" if value is None else f"{value / cls.BYTES_PER_GIB:.2f} GiB"

        def seconds(value: Optional[float]) -> str:
            return "n/a" if value is None else f"{value / 60:.1f} min"

        scope = record.get("scope") or ""
        title = f"{record.get('workflow', 'local')} / {record.get('job', 'job')}"
        if scope:
            title += f" ({scope})"
        efficiency = record.get("cpu_efficiency")
        efficiency_text = "n/a" if efficiency is None else f"{efficiency * 100:.0f}%"
        fraction = record.get("peak_memory_fraction")
        peak_text = gib(record.get("peak_memory_bytes"))
        if fraction is not None:
            peak_text += f" ({fraction * 100:.0f}% of runner)"
        lines = [
            f"### {cls.SUMMARY_HEADING} — {title}",
            "",
            "| metric | value |",
            "| --- | --- |",
            f"| wall time | {seconds(record.get('wall_seconds'))} |",
            f"| CPU time | {seconds(record.get('cpu_seconds'))} |",
            f"| CPU efficiency | {efficiency_text} of {record.get('cpu_count', '?')} cores |",
            f"| peak memory | {peak_text} |",
        ]
        for warning in record.get("warnings", []):
            lines.append(f"| note | {warning} |")
        lines.append("")
        return "\n".join(lines)


def _append_to_step_summary(text: str) -> None:
    """Append ``text`` to the job summary file, doing nothing when not on a runner."""
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    try:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(text)
    except OSError:
        pass


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser for the three modes."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=[mode.value for mode in ProbeMode])
    parser.add_argument("--state-dir", required=True, type=Path,
                        help="directory shared by the start and report calls of one job")
    parser.add_argument("--scope", default="",
                        help="what distinguishes this job from its matrix siblings")
    parser.add_argument("--out", type=Path, default=None,
                        help="where report mode writes the JSON record")
    parser.add_argument("--strict", action="store_true",
                        help="exit non-zero on failure instead of warning; for tests, never for CI")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Run the requested mode, swallowing every failure unless ``--strict`` was given.

    Returns:
        0 always, unless ``--strict`` was given and something went wrong.
    """
    args = build_parser().parse_args(argv)
    probe = ResourceProbe(args.state_dir)
    try:
        if args.mode == ProbeMode.SAMPLE.value:
            probe.sampler.run()
            return 0
        if args.mode == ProbeMode.START.value:
            state = probe.start()
            for warning in state["warnings"]:
                print(f"::warning::ci-resource-probe: {warning}")
            print(f"ci-resource-probe: measuring from {state['started_at']} (cgroup {state['cgroup_path']})")
            return 0
        record = probe.report(args.scope)
        if args.out is not None:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
        summary = ResourceProbe.render_summary(record)
        _append_to_step_summary(summary)
        print(summary)
        return 0
    except Exception as error:  # noqa: BLE001 - a probe must never fail the job it measures
        print(f"::warning::ci-resource-probe: {args.mode} failed: {error!r}")
        if args.strict:
            raise
        return 0


if __name__ == "__main__":
    sys.exit(main())
