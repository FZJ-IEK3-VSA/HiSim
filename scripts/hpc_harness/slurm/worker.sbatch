#!/bin/bash
# Whole-node worker (spec 13): exclusive node, one worker per node, no MPI world.
#SBATCH --job-name=harness-worker
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --exclusive
#SBATCH --requeue
#SBATCH --time=24:00:00

set -euo pipefail

# Adjust to your site: module load Python / activate the venv that has hisim installed.
# module load Python
source /projects2/2026_n-pflugradt_building_simulations/HiSim/hisimvenv/bin/activate


echo "Using python: $(command -v python)"
export PYTHONPATH="${SLURM_SUBMIT_DIR}/..:${PYTHONPATH:-}"   # scripts/ on the path
exec python -m hpc_harness worker --config "${HARNESS_WORKER_CONFIG:-worker.json}" --mode whole_node
