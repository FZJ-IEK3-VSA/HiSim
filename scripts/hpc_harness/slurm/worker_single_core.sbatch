#!/bin/bash
# Single-core worker (spec 4.2/13): one core on a shared node, one job at a time.
#
# IMPORTANT: --mem-per-cpu must cover per_job_mem_gb + ~2 GB interpreter overhead.
# On cgroup-enforcing clusters the job is killed at this limit no matter what the
# admission gate decides -- the allocation must fit the job.
#SBATCH --job-name=harness-worker-1c
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem-per-cpu=12G
#SBATCH --requeue
#SBATCH --time=24:00:00
# Per-worker logs land on the shared FS (server + all nodes see this dir). The autoscaler
# overrides these with worker-<jobid>.{out,err} via sbatch CLI args; kept here so a manual
# `sbatch worker_single_core.sbatch` also logs to the same place.
#SBATCH --output=/projects2/2026_n-pflugradt_building_simulations/slurm_logs/worker-%j.out
#SBATCH --error=/projects2/2026_n-pflugradt_building_simulations/slurm_logs/worker-%j.err

set -euo pipefail

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] worker: $*"; }

log "=== harness worker (single_core) starting ==="
log "job_id=${SLURM_JOB_ID:-?} node=$(hostname) submit_dir=${SLURM_SUBMIT_DIR:-?}"

# Adjust to your site: module load Python / activate the venv that has hisim installed.
# module load Python
VENV="/projects2/2026_n-pflugradt_building_simulations/HiSim/hisimvenv/bin/activate"
if [[ ! -f "$VENV" ]]; then
    log "FATAL: venv activate script not found: $VENV"
    exit 1
fi
# shellcheck disable=SC1090
source "$VENV"
log "venv activated: python=$(command -v python) ($(python --version 2>&1))"

export PYTHONPATH="${SLURM_SUBMIT_DIR}/..:${PYTHONPATH:-}"   # scripts/ on the path

# Absolute default so the config is found regardless of the job's working directory;
# HARNESS_WORKER_CONFIG (exported by the autoscaler) overrides it when set.
WORKER_CONFIG="${HARNESS_WORKER_CONFIG:-/projects2/2026_n-pflugradt_building_simulations/HiSim/scripts/hpc_harness/worker.iek2012.json}"
if [[ ! -f "$WORKER_CONFIG" ]]; then
    log "FATAL: worker config not found: $WORKER_CONFIG"
    exit 1
fi
log "PYTHONPATH=${PYTHONPATH}"
log "config=${WORKER_CONFIG} — handing off to the worker process (its logs follow)"
exec python -m hpc_harness worker --config "$WORKER_CONFIG" --mode single_core
