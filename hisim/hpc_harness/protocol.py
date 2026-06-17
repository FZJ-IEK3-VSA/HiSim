"""MPI message types exchanged between the head rank and the node-agents.

Messages are plain dicts sent with mpi4py's pickle-based ``comm.send``/``comm.recv``.
Only small status records ever travel over MPI; simulation result files are written
directly to the shared filesystem by each subprocess.
"""

# --- agent -> head -------------------------------------------------------------
REQUEST = "REQUEST"
"""Agent asks for work: {"type", "host", "rank", "n_free"}."""

REPORT = "REPORT"
"""Agent reports finished tasks: {"type", "reports": [report, ...]}.

Each report: {"id", "status", "exit_code", "duration_s", "peak_mem_mb",
"result_dir", "host", "started_at", "finished_at", "error"}.
"""

# --- head -> agent -------------------------------------------------------------
GRANT = "GRANT"
"""Head hands out work: {"type", "tasks": [{"id", "scenario_path"}, ...]}."""

NONE = "NONE"
"""No work available right now, but the run is not finished. Keep polling."""

SHUTDOWN = "SHUTDOWN"
"""Queue is fully drained and nothing is outstanding. Exit once the local pool idles."""

# Single MPI tag for all harness traffic.
TAG = 0
