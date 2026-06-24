#!/bin/bash
# Example Slurm batch script for the HiSim MPI HPC harness.
#
# One MPI rank per node; each rank manages a memory-gated pool of HiSim simulation
# subprocesses. The job is requeue-able: if it is killed or loses a node, resubmit
# (or rely on --requeue) and the run resumes from the SQLite database.
#
#SBATCH --job-name=hisim-mpi
#SBATCH --nodes=64
#SBATCH --ntasks-per-node=1          # exactly one rank (node-agent) per node
#SBATCH --exclusive                  # whole nodes; the agent uses all cores/memory
#SBATCH --requeue                    # let Slurm requeue on node failure / preemption
#SBATCH --time=24:00:00
#SBATCH --output=hisim-mpi-%j.out

set -euo pipefail

# --- environment --------------------------------------------------------------
# Load the cluster MPI that mpi4py was built against, then activate your venv.
# module load OpenMPI
# source /path/to/venv/bin/activate

RUN_DIR=/scratch/myproject/run123
CONFIG="${RUN_DIR}/harness.json"

# --- one-time task import (safe to re-run; it is idempotent) ------------------
# Do this once on the login node before submitting, or uncomment here:
# python -m hisim.hpc_harness import --db "${RUN_DIR}/tasks.db" \
#        --scenario-dir "${RUN_DIR}/scenarios" --glob '*.scenario.json'

# --- launch -------------------------------------------------------------------
# srun starts one task (rank) per node; rank 0 becomes the dispatcher.
srun --kill-on-bad-exit=1 python -m hisim.hpc_harness run --config "${CONFIG}"

# On completion (or after a requeue resumes and finishes), inspect results:
# python -m hisim.hpc_harness status --db "${RUN_DIR}/tasks.db"
