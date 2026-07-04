#!/bin/bash
# Queue server inside a Slurm "service" allocation — FALLBACK profile only (spec §4.1).
# Primary deployment is a plain service on a login node/VM:
#   nohup python -m hpc_harness server --config server.json &
# In this fallback profile the core DB must live on the shared FS: set
# "journal_mode": "DELETE" and a shared-FS "db_path" in server.json (spec §6.5).
#SBATCH --job-name=harness-server
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=7-00:00:00

set -euo pipefail

# Adjust to your site: module load Python / activate the venv that has hisim + [hpc] extras.
# module load Python
# source /project/venvs/hisim/bin/activate

export PYTHONPATH="${SLURM_SUBMIT_DIR}/..:${PYTHONPATH:-}"   # scripts/ on the path
exec python -m hpc_harness server --config "${HARNESS_SERVER_CONFIG:-server.json}"
