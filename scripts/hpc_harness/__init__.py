"""REST job-distribution harness for running large simulation batches on HPC clusters.

One long-lived queue server (FastAPI + SQLite) hands out jobs over HTTP to a fleet of
Slurm-started workers. See ``hpc_harnes_spec.md`` at the repository root for the full
design (fenced leases, warm-child worker pools, autoscaling, dashboard).

This package is deliberately independent of HiSim except for ``runners/hisim_runner.py``
so other simulation programs at the institute can reuse it.
"""

__version__ = "2.0.0"
