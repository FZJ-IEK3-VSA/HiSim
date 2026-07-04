#!/bin/bash
# Single-core worker (spec §4.2/§13): one core on a shared node, one job at a time.
#
# IMPORTANT: --mem-per-cpu must cover per_job_mem_gb + ~2 GB interpreter overhead.
# On cgroup-enforcing clusters the job is killed at this limit no matter what the
# admission gate decides — the allocation must fit the job.
#SBATCH --job-name=harness-worker-1c
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem-per-cpu=12G
#SBATCH --requeue
#SBATCH --time=24:00:00

set -euo pipefail

# Adjust to your site: module load Python / activate the venv that has hisim + [hpc] extras.
# module load Python
# source /project/venvs/hisim/bin/activate

export PYTHONPATH="${SLURM_SUBMIT_DIR}/..:${PYTHONPATH:-}"   # scripts/ on the path
exec python -m hpc_harness worker --config "${HARNESS_WORKER_CONFIG:-worker.json}" --mode single_core
