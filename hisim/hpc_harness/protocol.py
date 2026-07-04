"""MPI message types exchanged between the head rank and the node-agents.

Messages are plain dicts sent with mpi4py's pickle-based ``comm.send``/``comm.recv``.
Only small status records ever travel over MPI; simulation result files are written
directly to the shared filesystem by each subprocess.

The on-the-wire shapes are documented below as :class:`TypedDict` definitions so
that the head (``dispatcher.py``) and the workers (``node_agent.py``) share a single
typed contract instead of reconstructing each dict with bare string literals. The
``type`` discriminator of every message is one of the string constants
(``REQUEST``/``REPORT``/``GRANT``/``NO_WORK_AVAILABLE``/``SHUTDOWN``) defined in this
module; matching it against the ``Literal`` annotation of each message TypedDict is
the first thing every receiver does.
"""

from typing import List, Literal, Optional, TypedDict

# --- agent -> head -------------------------------------------------------------
REQUEST: str = "REQUEST"
# Agent asks for work: {"type": "REQUEST", "host": ..., "rank": ..., "num_free_slots": ...}.


class RequestMessage(TypedDict):
    """Agent -> head: ask for up to ``num_free_slots`` tasks to run on this rank."""

    type: Literal["REQUEST"]
    host: str
    rank: int
    num_free_slots: int


REPORT: str = "REPORT"
# Agent reports finished tasks: {"type": "REPORT", "reports": [...]}.
#
# Each report: {"id": ..., "status": ..., "exit_code": ..., "duration_s": ...,
# "peak_mem_mb": ..., "result_dir": ..., "host": ..., "started_at": ...,
# "finished_at": ..., "error": ...}.


class TaskReport(TypedDict):
    """One finished-task record carried inside a :class:`ReportMessage`.

    ``id`` and ``status`` are always present; the remaining fields mirror the
    nullable columns of the ``attempts``/``tasks`` tables in ``db.py`` and are
    read with ``dict.get`` by :func:`db.record_report`, so consumers must tolerate
    ``None``.
    """

    id: int
    status: str
    exit_code: Optional[int]
    duration_s: Optional[float]
    peak_mem_mb: Optional[float]
    result_dir: Optional[str]
    host: Optional[str]
    started_at: Optional[float]
    finished_at: Optional[float]
    error: Optional[str]


class ReportMessage(TypedDict):
    """Agent -> head: deliver a batch of finished-task reports."""

    type: Literal["REPORT"]
    reports: List[TaskReport]


# --- head -> agent -------------------------------------------------------------
GRANT: str = "GRANT"
# Head hands out work: {"type": "GRANT", "tasks": [{"id": ..., "scenario_path": ...}, ...]}.


class GrantTask(TypedDict):
    """A task leased to a worker: just enough to launch the subprocess."""

    id: int
    scenario_path: str


class GrantMessage(TypedDict):
    """Head -> agent: hand out a batch of leased tasks."""

    type: Literal["GRANT"]
    tasks: List[GrantTask]


NO_WORK_AVAILABLE: str = "NO_WORK_AVAILABLE"
# No work available right now, but the run is not finished. Keep polling.

SHUTDOWN: str = "SHUTDOWN"
# Queue is fully drained and nothing is outstanding. Exit once the local pool idles.

# Single MPI tag for all harness traffic.
TAG: int = 0
