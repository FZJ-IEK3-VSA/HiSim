"""Node / cgroup sampling and admission gates (spec §4.2, §9)."""

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

import psutil

LOGGER = logging.getLogger(__name__)

GB = 1024 ** 3
# cgroup v1 reports "no limit" as a huge number; treat anything above this as unlimited.
_CGROUP_NO_LIMIT = 1 << 60


def node_metrics(running_jobs: int = 0, free_slots: int = 0) -> Dict[str, Any]:
    """One heartbeat sample of node-level state."""
    vmem = psutil.virtual_memory()
    try:
        load1 = os.getloadavg()[0]
    except (AttributeError, OSError):
        load1 = None
    return {
        "cpu_percent": psutil.cpu_percent(interval=None),
        "mem_used_gb": round(vmem.used / GB, 2),
        "mem_available_gb": round(vmem.available / GB, 2),
        "load1": load1,
        "running_jobs": running_jobs,
        "free_slots": free_slots,
    }


def cgroup_limits() -> Optional[Dict[str, float]]:
    """Detect an enforced memory cgroup around this process (v2 then v1).

    Returns ``{"mem_limit_gb", "mem_current_gb"}`` or None when no enforced limit is
    found — the ``node_gate: auto`` selector then falls back to the observed gate,
    which is the required insurance on clusters that do not enforce cgroups.
    """
    try:
        cgroup_line = Path("/proc/self/cgroup").read_text(encoding="utf-8")
    except OSError:
        return None
    # cgroup v2: single "0::<path>" line.
    for line in cgroup_line.splitlines():
        parts = line.split(":", 2)
        if len(parts) == 3 and parts[0] == "0":
            base = Path("/sys/fs/cgroup") / parts[2].lstrip("/")
            limit = _read_cgroup_bytes(base / "memory.max")
            current = _read_cgroup_bytes(base / "memory.current")
            if limit is not None and current is not None:
                return {"mem_limit_gb": limit / GB, "mem_current_gb": current / GB}
    # cgroup v1 memory controller.
    for line in cgroup_line.splitlines():
        parts = line.split(":", 2)
        if len(parts) == 3 and "memory" in parts[1].split(","):
            base = Path("/sys/fs/cgroup/memory") / parts[2].lstrip("/")
            limit = _read_cgroup_bytes(base / "memory.limit_in_bytes")
            current = _read_cgroup_bytes(base / "memory.usage_in_bytes")
            if limit is not None and current is not None:
                return {"mem_limit_gb": limit / GB, "mem_current_gb": current / GB}
    return None


def _read_cgroup_bytes(path: Path) -> Optional[int]:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if text == "max":
        return None
    try:
        value = int(text)
    except ValueError:
        return None
    return None if value >= _CGROUP_NO_LIMIT else value


def whole_node_gate(
    per_job_mem_gb: float,
    min_headroom_gb: float,
    running_jobs: int,
    cores: int,
    cores_per_job: int,
    reserved_cores: int,
) -> bool:
    """Self-accounting admission for an exclusive node (spec §4.2): memory AND cores."""
    if psutil.virtual_memory().available < (per_job_mem_gb + min_headroom_gb) * GB:
        return False
    usable_cores = cores - reserved_cores
    return (running_jobs + 1) * cores_per_job <= usable_cores


def single_core_gate(
    node_gate: str,
    per_job_mem_gb: float,
    max_node_cpu_percent: float,
    node_safety_buffer_gb: float,
) -> bool:
    """Admission for a shared-node single-core worker (spec §4.2).

    ``cgroup`` mode gates on the worker's own allocation; ``observed`` mode is the
    insurance for clusters without enforced cgroups and gates on node-wide state.
    ``auto`` picks cgroup when an enforced limit is detected.
    """
    if node_gate == "off":
        return True
    limits = cgroup_limits() if node_gate in ("auto", "cgroup") else None
    if limits is not None:
        headroom_gb = limits["mem_limit_gb"] - limits["mem_current_gb"]
        return headroom_gb >= per_job_mem_gb * 0.9  # small tolerance: the job IS the budget
    if node_gate == "cgroup":
        LOGGER.warning("node_gate=cgroup but no enforced cgroup found; admitting")
        return True
    # Observed node-wide gate (rogue jobs / zombies / over-consuming neighbours).
    if psutil.cpu_percent(interval=None) >= max_node_cpu_percent:
        return False
    return psutil.virtual_memory().available >= (per_job_mem_gb + node_safety_buffer_gb) * GB
