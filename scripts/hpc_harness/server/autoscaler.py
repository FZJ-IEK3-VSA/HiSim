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
from typing import Any, Callable, Dict, List, Optional

from hpc_harness import db
from hpc_harness.config import AutoscaleConfig

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


def default_sbatch(worker_script: str, n: int) -> List[str]:
    """Submit ``n`` worker jobs; returns the Slurm job ids actually submitted.

    A total failure raises ``RuntimeError`` carrying sbatch's **stderr** (so the reason
    is visible in the log and on the autoscaler dashboard, not just an opaque exit
    code). A partial failure keeps the jobs already submitted and stops early.
    """
    if not worker_script:
        raise RuntimeError("autoscale.worker_script is not set")
    if not os.path.isfile(worker_script):
        raise RuntimeError(f"autoscale.worker_script does not exist: {worker_script}")
    job_ids: List[str] = []
    for _ in range(n):
        try:
            out = subprocess.run(
                ["sbatch", "--parsable", worker_script],
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
        sbatch_fn: Optional[Callable[[int], List[str]]] = None,
        squeue_fn: Optional[Callable[[List[str]], Optional[Dict[str, str]]]] = None,
        scancel_fn: Optional[Callable[[List[str]], None]] = None,
    ) -> None:
        """Wire the loop to the service; default Slurm commands unless injected."""
        self.service = service
        self.cfg = cfg
        if probe_fn is not None:
            self.probe_fn = probe_fn
        elif cfg.capacity_probe:
            self.probe_fn = lambda: shell_capacity_probe(cfg.capacity_probe)
        else:
            self.probe_fn = lambda: default_capacity_probe(cfg.partition)
        self.sbatch_fn = sbatch_fn or (lambda n: default_sbatch(cfg.worker_script, n))
        self.squeue_fn = squeue_fn or default_squeue
        self.scancel_fn = scancel_fn or default_scancel
        self._last_squeue = 0.0
        # Live snapshot of the last control period, surfaced on the autoscaler dashboard.
        self.snapshot: Dict[str, Any] = {
            "last_tick": None,
            "action": "not yet run",
            "reason": None,
            "error": None,
            "squeue_available": None,
            "work": None,
            "current": None,
            "alive_workers": None,
            "in_flight": None,
            "slurm_queued": None,
            "available_cores": None,
            "to_submit": None,
            "submitted": 0,
            "cancelled": 0,
        }

    def tick(self) -> Dict[str, int]:
        """One control period: refresh Slurm states, compute the step, submit/cancel.

        Every exit path records a full :attr:`snapshot` so the autoscaler dashboard
        can explain exactly what the loop last saw and did.
        """
        writer = self.service.writer
        now = time.time()
        snap: Dict[str, Any] = {"last_tick": now, "error": None, "reason": None,
                                "available_cores": None, "to_submit": 0,
                                "submitted": 0, "cancelled": 0}
        try:
            # 1. Refresh tracked submission states from squeue.
            submissions = writer.call(db.open_submissions)
            tracked_ids = [s["slurm_job_id"] for s in submissions]
            states = self.squeue_fn(tracked_ids)
            snap["squeue_available"] = states is not None
            if states is not None:
                self._last_squeue = now
                writer.call(lambda c: db.update_submission_states(c, states))
                submissions = writer.call(db.open_submissions)
            else:
                # squeue unavailable: age out via the registration grace fallback.
                cutoff = now - self.cfg.registration_grace_s
                expired = {
                    s["slurm_job_id"]: "ended" for s in submissions if s["submitted_at"] < cutoff
                }
                if expired:
                    writer.call(lambda c: db.update_submission_states(c, expired))
                    submissions = writer.call(db.open_submissions)

            # 2. Fleet accounting.
            alive = [
                w for w in writer.call(db.alive_workers)
                if w["mode"] == self.cfg.worker_mode and w["worker_id"] in self.service.liveness
            ]
            slurm_queued = sum(1 for s in submissions if s["state"] in ("submitted", "queued"))
            in_flight = len(submissions)
            current = len(alive) + in_flight
            counts = self.service.status()["counts"]
            work = counts.get(db.PENDING, 0) + counts.get(db.LEASED, 0)
            snap.update({"alive_workers": len(alive), "in_flight": in_flight,
                         "slurm_queued": slurm_queued, "current": current, "work": work})

            # 3. Scale down: cancel surplus still-queued submissions (safe), never running.
            if work < current and slurm_queued > 0:
                surplus = min(current - work, slurm_queued)
                queued_ids = [s["slurm_job_id"] for s in submissions
                              if s["state"] in ("submitted", "queued")]
                to_cancel = queued_ids[:surplus]
                self.scancel_fn(to_cancel)
                writer.call(
                    lambda c: db.update_submission_states(c, {j: "cancelled" for j in to_cancel})
                )
                LOGGER.info("Autoscaler cancelled %d queued surplus workers", len(to_cancel))
                snap.update({"action": "scale_down", "cancelled": len(to_cancel)})
                return self._finish(snap, current)

            # 4. Scale up.
            if self.service.paused or work == 0:
                snap.update({"action": "idle",
                             "reason": "leasing paused" if self.service.paused else "no work"})
                return self._finish(snap, current)
            try:
                available = self.probe_fn()
            except Exception as exc:  # pylint: disable=broad-except
                LOGGER.exception("Capacity probe failed; skipping this period")
                self.service.record_exception("autoscaler", "capacity_probe", exc)
                snap.update({"action": "probe_failed", "error": str(exc)})
                return self._finish(snap, current)
            to_submit = compute_to_submit(
                work, current, available, slurm_queued, self.cfg.standby_floor, self.cfg.max_workers
            )
            snap.update({"available_cores": available, "to_submit": to_submit})
            if to_submit > 0:
                try:
                    job_ids = self.sbatch_fn(to_submit)
                except Exception as exc:  # pylint: disable=broad-except
                    # A bad worker_script / rejected sbatch must not crash-loop the
                    # autoscaler; surface the reason on the dashboard and try again next tick.
                    LOGGER.error("Autoscaler sbatch failed: %s", exc)
                    self.service.record_exception("autoscaler", "sbatch", exc)
                    snap.update({"action": "sbatch_failed", "error": str(exc)})
                    return self._finish(snap, current)
                if job_ids:
                    writer.call(
                        lambda c: [db.add_submission(c, j, self.cfg.worker_mode) for j in job_ids]
                    )
                    LOGGER.info(
                        "Autoscaler submitted %d workers (work=%d current=%d idle_cores=%d)",
                        len(job_ids), work, current, available,
                    )
                    snap.update({"action": "scale_up", "submitted": len(job_ids)})
                else:
                    snap.update({"action": "sbatch_failed", "error": "sbatch returned no job ids"})
                return self._finish(snap, current)
            snap.update({"action": "steady",
                         "reason": "fleet already covers the backlog / no free cores"})
            return self._finish(snap, current)
        except Exception as exc:  # pylint: disable=broad-except
            snap.update({"action": "error", "error": str(exc)})
            self.snapshot.update(snap)
            raise

    def _finish(self, snap: Dict[str, Any], current: int) -> Dict[str, int]:
        """Publish the snapshot and return the tick's action tally."""
        self.snapshot.update(snap)
        return {"submitted": snap["submitted"], "cancelled": snap["cancelled"], "current": current}

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
            "partition": self.cfg.partition,
            "capacity_probe": self.cfg.capacity_probe or "sinfo -h -o %C (idle field)",
            "squeue_poll_s": self.cfg.squeue_poll_s,
            "registration_grace_s": self.cfg.registration_grace_s,
            "trying_to_scale": bool(result.get("to_submit") or 0),
            "submission_state_counts": writer.call(db.submission_state_counts),
            "submissions": writer.call(lambda c: db.recent_submissions(c, 100)),
        })
        return result
