#!/bin/bash
# Whole-node worker (spec §13): exclusive node, one worker per node, no MPI world.
#SBATCH --job-name=harness-worker
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --exclusive
#SBATCH --requeue
#SBATCH --time=24:00:00

set -euo pipefail

# Adjust to your site: module load Python / activate the venv that has hisim + [hpc] extras.
# module load Python
# source /project/venvs/hisim/bin/activate

export PYTHONPATH="${SLURM_SUBMIT_DIR}/..:${PYTHONPATH:-}"   # scripts/ on the path
exec python -m hpc_harness worker --config "${HARNESS_WORKER_CONFIG:-worker.json}" --mode whole_node
