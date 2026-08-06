"""Autoscaler for the single-core worker fleet (spec §13.1).

The control law is **incremental** — it sizes the submission step, not the absolute
fleet, so cores freed later keep being picked up while backlog remains::

    capacity_gap = max(0, work - current)
    to_submit    = min(available_cores, capacity_gap)
    if available_cores < standby_floor:            # cluster momentarily full:
        to_submit = max(to_submit, min(standby_floor - slurm_queued, capacity_gap))
    to_submit    = clamp(to_submit, 0, max_workers - current)

Fleet accounting tracks submitted-but-unregistered workers via ``squeue`` polling of
the recorded job ids (a worker queued in Slurm for hours still counts, and is never
blindly resubmitted); the ``registration_grace_s`` wall-clock fallback applies only
when squeue itself is unavailable.
"""

import logging
import os
import shutil
import subprocess
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from hpc_harness import db
from hpc_harness.config import AutoscaleConfig, WorkerProfileConfig

LOGGER = logging.getLogger(__name__)

# Slurm states that mean a submission is still on its way to registering.
_SLURM_PENDING = {"PENDING", "PD", "CONFIGURING", "CF"}
_SLURM_RUNNING = {"RUNNING", "R", "COMPLETING", "CG"}


def compute_to_submit(
    work: int,
    current: int,
    available_cores: int,
    slurm_queued: int,
    standby_floor: int,
    max_workers: int,
) -> int:
    """The pure control law (spec §13.1); table-tested."""
    capacity_gap = max(0, work - current)
    to_submit = min(available_cores, capacity_gap)
    if available_cores < standby_floor:
        to_submit = max(to_submit, min(standby_floor - slurm_queued, capacity_gap))
    return max(0, min(to_submit, max_workers - current))


def parse_sinfo_cpus(text: str) -> int:
    """Sum the *idle* field of ``sinfo -h -o %C`` output (``allocated/idle/other/total``)."""
    idle = 0
    for line in text.splitlines():
        parts = line.strip().split("/")
        if len(parts) == 4:
            try:
                idle += int(parts[1])
            except ValueError:
                continue
    return idle


def default_capacity_probe(partition: Optional[str]) -> int:
    """Idle cores from ``sinfo``, restricted to ``partition`` when set."""
    cmd = ["sinfo", "-h", "-o", "%C"]
    if partition:
        cmd += ["-p", partition]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=True)
    return parse_sinfo_cpus(out.stdout)


def shell_capacity_probe(command: str) -> int:
    """Custom probe: any command whose stdout is the idle-core integer."""
    out = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=30, check=True)
    return int(out.stdout.strip())


def default_sbatch(
    worker_script: str,
    n: int,
    log_dir: Optional[str] = None,
    worker_config: Optional[str] = None,
    worker_runner: Optional[str] = None,
) -> List[str]:
    """Submit ``n`` worker jobs; returns the Slurm job ids actually submitted.

    When ``log_dir`` is set, the directory is created (so Slurm can open the files)
    and each job's stdout/stderr is routed to ``<log_dir>/worker-<jobid>.out`` /
    ``.err``, which the autoscaler later reads to explain a worker that died before
    registering. When ``worker_config`` is set it is passed to the job as
    ``HARNESS_WORKER_CONFIG`` so the worker loads the right config by absolute path
    instead of a relative ``worker.json`` it cannot find from its Slurm working directory.
    When ``worker_runner`` is set it is passed as ``HARNESS_WORKER_RUNNER`` so the launched
    worker serves exactly the runner its fleet is accounted for (they can never disagree).

    A total failure raises ``RuntimeError`` carrying sbatch's **stderr** (so the reason
    is visible in the log and on the autoscaler dashboard, not just an opaque exit
    code). A partial failure keeps the jobs already submitted and stops early.
    """
    if not worker_script:
        raise RuntimeError("autoscale.worker_script is not set")
    if not os.path.isfile(worker_script):
        raise RuntimeError(f"autoscale.worker_script does not exist: {worker_script}")
    if worker_config and not os.path.isfile(worker_config):
        raise RuntimeError(f"autoscale.worker_config does not exist: {worker_config}")
    log_args: List[str] = []
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
        log_args = [
            f"--output={os.path.join(log_dir, 'worker-%j.out')}",
            f"--error={os.path.join(log_dir, 'worker-%j.err')}",
        ]
    # Keep the submitter's environment (--export=ALL) and add the config/runner on top.
    exports = ["ALL"]
    if worker_config:
        exports.append(f"HARNESS_WORKER_CONFIG={worker_config}")
    if worker_runner:
        exports.append(f"HARNESS_WORKER_RUNNER={worker_runner}")
    export_args = [f"--export={','.join(exports)}"] if len(exports) > 1 else []
    job_ids: List[str] = []
    for _ in range(n):
        try:
            out = subprocess.run(
                ["sbatch", "--parsable", *log_args, *export_args, worker_script],
                capture_output=True, text=True, timeout=60, check=False,
            )
        except FileNotFoundError as exc:  # sbatch itself not on PATH
            raise RuntimeError("sbatch not found on PATH (is the server on a submit host?)") from exc
        if out.returncode != 0:
            detail = (out.stderr or out.stdout or "").strip() or f"exit {out.returncode}"
            if job_ids:  # keep the real jobs we already launched; retry the rest next tick
                LOGGER.error("sbatch failed after %d submitted: %s", len(job_ids), detail)
                break
            raise RuntimeError(f"sbatch failed: {detail}")
        job_ids.append(out.stdout.strip().split(";")[0])
    return job_ids


def read_log_tail(path: str, max_lines: int = 40, max_bytes: int = 8000) -> Optional[str]:
    """Return the last ``max_lines`` (≤ ``max_bytes``) of ``path``, or None if unreadable.

    Used to pull a dead worker's Slurm stdout/stderr onto the error page. Reads at most
    ``max_bytes`` from the end so a runaway log never blows up the error record.
    """
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
            data = fh.read()
    except OSError:
        return None
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    return "\n".join(lines[-max_lines:]).strip()


def default_squeue(job_ids: List[str]) -> Optional[Dict[str, str]]:
    """{job_id: slurm_state} for the tracked ids; ids absent from squeue have ended.

    Returns None when squeue is unavailable (caller falls back to the grace timeout).
    """
    if not job_ids:
        return {}
    if shutil.which("squeue") is None:
        return None
    try:
        out = subprocess.run(
            ["squeue", "-h", "-o", "%i %T", "-j", ",".join(job_ids)],
            capture_output=True, text=True, timeout=30, check=True,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    states: Dict[str, str] = {job_id: "ended" for job_id in job_ids}
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            state = parts[1].upper()
            if state in _SLURM_PENDING:
                states[parts[0]] = "queued"
            elif state in _SLURM_RUNNING:
                states[parts[0]] = "running"
            else:
                states[parts[0]] = "ended"
    return states


def default_scancel(job_ids: List[str]) -> None:
    """Cancel still-pending surplus submissions (always safe, spec §13.1)."""
    if job_ids:
        subprocess.run(["scancel", *job_ids], capture_output=True, text=True, timeout=60, check=False)


class Autoscaler:
    """Periodic control loop; all Slurm interactions are injectable for tests."""

    def __init__(
        self,
        service: "HarnessService",  # type: ignore[name-defined]  # noqa: F821
        cfg: AutoscaleConfig,
        probe_fn: Optional[Callable[[], int]] = None,
        sbatch_fn: Optional[Callable[[int, WorkerProfileConfig], List[str]]] = None,
        squeue_fn: Optional[Callable[[List[str]], Optional[Dict[str, str]]]] = None,
        scancel_fn: Optional[Callable[[List[str]], None]] = None,
    ) -> None:
        """Wire the loop to the service; default Slurm commands unless injected.

        The fleet(s) come from ``cfg.resolved_profiles()`` — one per runner in multi-fleet
        mode, or a single (possibly catch-all) fleet from the legacy top-level fields.
        """
        self.service = service
        self.cfg = cfg
        self.profiles = cfg.resolved_profiles()
        if probe_fn is not None:
            self.probe_fn = probe_fn
        elif cfg.capacity_probe:
            self.probe_fn = lambda: shell_capacity_probe(cfg.capacity_probe)
        else:
            self.probe_fn = lambda: default_capacity_probe(cfg.partition)
        self.sbatch_fn = sbatch_fn or (
            lambda n, profile: default_sbatch(
                profile.worker_script, n, cfg.slurm_log_dir, profile.worker_config, profile.runner
            )
        )
        self.squeue_fn = squeue_fn or default_squeue
        self.scancel_fn = scancel_fn or default_scancel
        self._last_squeue = 0.0
        self._reported_mismatch: set = set()  # runners already surfaced as unserved (dedupe)
        # Live snapshot of the last control period, surfaced on the autoscaler dashboard.
        self.snapshot: Dict[str, Any] = {
            "last_tick": None,
            "action": "not yet run",
            "reason": None,
            "error": None,
            "squeue_available": None,
            "work": 0,
            "current": 0,
            "alive_workers": 0,
            "in_flight": 0,
            "slurm_queued": 0,
            "available_cores": None,
            "to_submit": 0,
            "submitted": 0,
            "cancelled": 0,
            "profiles": [],
            "unserved_runners": [],
        }

    def tick(self) -> Dict[str, int]:
        """One control period: refresh Slurm states, then size every fleet independently.

        Each configured fleet (profile) is sized from *its own* runner's pending work against
        a shared idle-core budget, so a `hisim` fleet never scales up on `hisim_setup` work.
        Pending work whose runner no fleet serves is surfaced on the error page. Every exit
        path records a full :attr:`snapshot` so the dashboard can explain what the loop did.
        """
        writer = self.service.writer
        now = time.time()
        snap: Dict[str, Any] = {
            "last_tick": now, "error": None, "reason": None, "available_cores": None,
            "to_submit": 0, "submitted": 0, "cancelled": 0, "work": 0, "current": 0,
            "slurm_queued": 0, "in_flight": 0, "alive_workers": 0,
            "profiles": [], "unserved_runners": [],
        }
        try:
            # 1. Refresh tracked submission states from squeue (or age out via grace fallback).
            submissions = self._refresh_submissions(writer, now, snap)
            # 1b. Surface workers that ended without ever registering (startup deaths).
            self._surface_startup_deaths(writer)

            # 2. Shared inputs, probed/read once and reused for every fleet.
            alive_all = [
                w for w in writer.call(db.alive_workers) if w["worker_id"] in self.service.liveness
            ]
            counts_by_runner = writer.call(db.counts_by_runner)
            paused = bool(self.service.paused)
            try:
                available = self.probe_fn()
            except Exception as exc:  # pylint: disable=broad-except
                LOGGER.exception("Capacity probe failed; skipping this period")
                self.service.record_exception("autoscaler", "capacity_probe", exc)
                snap.update({"action": "probe_failed", "error": str(exc)})
                return self._finish(snap)
            snap["available_cores"] = available

            # 3. Size each fleet against the shared idle-core budget (in config order).
            budget = available
            errored: Optional[str] = None
            for profile in self.profiles:
                psnap, used = self._process_profile(
                    profile, writer, budget, submissions, alive_all, counts_by_runner, paused
                )
                snap["profiles"].append(psnap)
                for key in ("work", "current", "slurm_queued", "in_flight", "alive_workers",
                            "to_submit", "submitted", "cancelled"):
                    snap[key] += psnap[key]
                budget = max(0, budget - used)
                if psnap["error"] and errored is None:
                    errored = psnap["error"]

            # 4. Guard: pending work whose runner no fleet serves.
            snap["unserved_runners"] = self._guard_runner_mismatch(counts_by_runner)
            snap["error"] = errored
            snap["action"] = self._summary_action(snap, paused, errored)
            return self._finish(snap)
        except Exception as exc:  # pylint: disable=broad-except
            snap.update({"action": "error", "error": str(exc)})
            self.snapshot.update(snap)
            raise

    def _refresh_submissions(self, writer: Any, now: float, snap: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Update tracked submissions from squeue; fall back to the grace timeout if it's down."""
        submissions = writer.call(db.open_submissions)
        states = self.squeue_fn([s["slurm_job_id"] for s in submissions])
        snap["squeue_available"] = states is not None
        if states is not None:
            self._last_squeue = now
            writer.call(lambda c: db.update_submission_states(c, states))
            return writer.call(db.open_submissions)
        cutoff = now - self.cfg.registration_grace_s
        expired = {s["slurm_job_id"]: "ended" for s in submissions if s["submitted_at"] < cutoff}
        if expired:
            writer.call(lambda c: db.update_submission_states(c, expired))
            return writer.call(db.open_submissions)
        return submissions

    @staticmethod
    def _work_for(runner: Optional[str], counts_by_runner: Dict[str, Dict[str, int]]) -> int:
        """Pending+leased jobs for ``runner`` (or across all runners when ``runner`` is None)."""
        if runner is None:  # catch-all fleet: all work is fair game
            buckets = counts_by_runner.values()
        else:
            buckets = [counts_by_runner.get(runner, {})]
        return sum(c.get(db.PENDING, 0) + c.get(db.LEASED, 0) for c in buckets)

    def _process_profile(
        self,
        profile: WorkerProfileConfig,
        writer: Any,
        budget: int,
        submissions: List[Dict[str, Any]],
        alive_all: List[Dict[str, Any]],
        counts_by_runner: Dict[str, Dict[str, int]],
        paused: bool,
    ) -> Tuple[Dict[str, Any], int]:
        """Size one fleet; returns its snapshot and the idle cores it consumed from the budget."""
        mode, runner = profile.worker_mode, profile.runner

        def mine(row: Dict[str, Any]) -> bool:
            return row["mode"] == mode and (runner is None or row.get("runner") == runner)

        my_subs = [s for s in submissions if mine(s)]
        alive = [w for w in alive_all if mine(w)]
        slurm_queued = sum(1 for s in my_subs if s["state"] in ("submitted", "queued"))
        in_flight = len(my_subs)
        current = len(alive) + in_flight
        work = self._work_for(runner, counts_by_runner)
        psnap: Dict[str, Any] = {
            "name": profile.name, "runner": runner, "worker_mode": mode,
            "work": work, "current": current, "alive_workers": len(alive),
            "in_flight": in_flight, "slurm_queued": slurm_queued,
            "to_submit": 0, "submitted": 0, "cancelled": 0,
            "action": "steady", "reason": None, "error": None,
        }

        # Scale down: cancel surplus still-queued submissions of this fleet (never running).
        if work < current and slurm_queued > 0:
            surplus = min(current - work, slurm_queued)
            queued_ids = [s["slurm_job_id"] for s in my_subs
                          if s["state"] in ("submitted", "queued")][:surplus]
            self.scancel_fn(queued_ids)
            writer.call(lambda c: db.update_submission_states(c, {j: "cancelled" for j in queued_ids}))
            LOGGER.info("Autoscaler cancelled %d queued surplus workers for %s", len(queued_ids), profile.name)
            psnap.update({"action": "scale_down", "cancelled": len(queued_ids)})
            return psnap, 0

        # Scale up.
        if paused or work == 0:
            psnap.update({"action": "idle", "reason": "leasing paused" if paused else "no work"})
            return psnap, 0
        to_submit = compute_to_submit(
            work, current, budget, slurm_queued, profile.standby_floor, profile.max_workers
        )
        psnap["to_submit"] = to_submit
        if to_submit <= 0:
            psnap["reason"] = "fleet covers the backlog / no free cores"
            return psnap, 0
        try:
            job_ids = self.sbatch_fn(to_submit, profile)
        except Exception as exc:  # pylint: disable=broad-except
            # A bad worker_script / rejected sbatch must not crash-loop the autoscaler;
            # surface the reason on the dashboard and try again next tick.
            LOGGER.error("Autoscaler sbatch failed for %s: %s", profile.name, exc)
            self.service.record_exception("autoscaler", f"sbatch[{profile.name}]", exc)
            psnap.update({"action": "sbatch_failed", "error": str(exc)})
            return psnap, 0
        if not job_ids:
            psnap.update({"action": "sbatch_failed", "error": "sbatch returned no job ids"})
            return psnap, 0
        writer.call(lambda c: [db.add_submission(c, j, mode, runner) for j in job_ids])
        LOGGER.info("Autoscaler submitted %d workers for %s (runner=%s work=%d current=%d)",
                    len(job_ids), profile.name, runner, work, current)
        psnap.update({"action": "scale_up", "submitted": len(job_ids)})
        return psnap, len(job_ids)

    def _guard_runner_mismatch(self, counts_by_runner: Dict[str, Dict[str, int]]) -> List[Dict[str, Any]]:
        """Surface (once) any runner with pending work that no configured fleet serves."""
        served = {p.runner for p in self.profiles if p.runner is not None}
        has_catchall = any(p.runner is None for p in self.profiles)
        unserved: List[Dict[str, Any]] = []
        for runner, c in counts_by_runner.items():
            pending = c.get(db.PENDING, 0)
            is_served = has_catchall or runner in served
            if pending > 0 and not is_served:
                unserved.append({"runner": runner, "pending": pending})
                if runner not in self._reported_mismatch:
                    self._reported_mismatch.add(runner)
                    LOGGER.warning("No autoscaler fleet serves runner %s (%d pending)", runner, pending)
                    self.service.record_error(self._mismatch_error_record(runner, pending, served))
            elif runner in self._reported_mismatch:
                self._reported_mismatch.discard(runner)  # cleared — re-report if it recurs
        return unserved

    def _mismatch_error_record(self, runner: Optional[str], pending: int, served: set) -> Dict[str, Any]:
        """Build the error-page record for pending work with no serving fleet."""
        served_list = ", ".join(sorted(s for s in served if s)) or "(none)"
        return {
            "ts": time.time(), "source": "autoscaler", "location": f"runner {runner}",
            "worker_id": None, "job_id": None, "host": None,
            "error_type": "RunnerMismatch",
            "message": (
                f"{pending} pending job(s) need runner {runner!r}, but no autoscaler fleet serves it "
                f"(fleets serve: {served_list}). Add an autoscale profile for {runner!r}, or submit "
                f"these jobs under a served runner — otherwise they will never be leased."
            )[:2000],
            "traceback": "(no traceback — autoscaler fleet/runner mismatch)",
        }

    @staticmethod
    def _summary_action(snap: Dict[str, Any], paused: bool, errored: Optional[str]) -> str:
        """Collapse the per-fleet actions into one headline action for the dashboard."""
        if errored:
            return "sbatch_failed"
        if snap["submitted"]:
            return "scale_up"
        if snap["cancelled"]:
            return "scale_down"
        if snap["unserved_runners"]:
            return "runner_mismatch"
        if paused:
            return "idle"
        return "idle" if snap["work"] == 0 else "steady"

    def _finish(self, snap: Dict[str, Any]) -> Dict[str, int]:
        """Publish the snapshot and return the tick's action tally."""
        self.snapshot.update(snap)
        return {"submitted": snap["submitted"], "cancelled": snap["cancelled"],
                "current": snap["current"]}

    def _surface_startup_deaths(self, writer: Any) -> None:
        """Post any worker that ended without registering to the error page, with its log tail.

        Each such submission is reported once and then marked 'died' so it never spams the
        error page on later ticks. When ``slurm_log_dir`` is configured, the worker's Slurm
        stdout/stderr (which lives on the shared FS the server can read) is tailed into the
        error record so the root cause is visible without opening a shell on the cluster.
        """
        deaths = writer.call(db.unregistered_deaths)
        for sub in deaths:
            job_id = sub["slurm_job_id"]
            LOGGER.warning("Worker Slurm job %s ended without ever registering", job_id)
            self.service.record_error(self._death_error_record(job_id))
            writer.call(lambda c, j=job_id: db.mark_submission_died(c, j))

    def _death_error_record(self, job_id: str) -> Dict[str, Any]:
        """Build the error-page record for a worker that died before registering."""
        log_dir = self.cfg.slurm_log_dir
        tail: Optional[str] = None
        log_note = "autoscale.slurm_log_dir is not set, so the worker's Slurm log is unavailable"
        if log_dir:
            out_path = os.path.join(log_dir, f"worker-{job_id}.out")
            err_path = os.path.join(log_dir, f"worker-{job_id}.err")
            out_tail = read_log_tail(out_path)
            err_tail = read_log_tail(err_path)
            parts = []
            if out_tail:
                parts.append(f"--- {out_path} (tail) ---\n{out_tail}")
            if err_tail:
                parts.append(f"--- {err_path} (tail) ---\n{err_tail}")
            if parts:
                tail = "\n\n".join(parts)
                log_note = f"Slurm log tail read from {log_dir}"
            else:
                log_note = (
                    f"no readable Slurm log at {out_path} / {err_path} "
                    "(the job may have been rejected by Slurm before it started)"
                )
        return {
            "ts": time.time(),
            "source": "autoscaler",
            "location": f"worker Slurm job {job_id}",
            "worker_id": None,
            "job_id": None,
            "host": None,
            "error_type": "WorkerStartupDeath",
            "message": (
                f"Worker Slurm job {job_id} ended without ever registering with the server. "
                f"{log_note}."
            )[:2000],
            "traceback": tail or "(no Slurm log captured)",
        }

    def status(self) -> Dict[str, Any]:
        """Full autoscaler state for the dashboard: config + last tick + live submissions."""
        writer = self.service.writer
        result = dict(self.snapshot)
        result.update({
            "enabled": True,
            "worker_mode": self.cfg.worker_mode,
            "period_s": self.cfg.period_s,
            "standby_floor": self.cfg.standby_floor,
            "max_workers": self.cfg.max_workers,
            "worker_script": self.cfg.worker_script,
            "worker_config": self.cfg.worker_config,
            "slurm_log_dir": self.cfg.slurm_log_dir,
            "partition": self.cfg.partition,
            "capacity_probe": self.cfg.capacity_probe or "sinfo -h -o %C (idle field)",
            "squeue_poll_s": self.cfg.squeue_poll_s,
            "registration_grace_s": self.cfg.registration_grace_s,
            "fleets": [
                {"name": p.name, "runner": p.runner, "worker_mode": p.worker_mode,
                 "worker_script": p.worker_script, "worker_config": p.worker_config,
                 "max_workers": p.max_workers, "standby_floor": p.standby_floor,
                 "partition": p.partition}
                for p in self.profiles
            ],
            "trying_to_scale": bool(result.get("to_submit") or 0),
            "submission_state_counts": writer.call(db.submission_state_counts),
            "submissions": writer.call(lambda c: db.recent_submissions(c, 100)),
        })
        return result
