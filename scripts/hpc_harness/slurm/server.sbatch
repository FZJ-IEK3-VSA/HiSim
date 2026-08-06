#!/bin/bash
# Queue server inside a Slurm "service" allocation -- FALLBACK profile only (spec 4.1).
# Primary deployment is a plain service on a login node/VM:
#   nohup python -m hpc_harness server --config server.json &
# In this fallback profile the core DB must live on the shared FS: set
# "journal_mode": "DELETE" and a shared-FS "db_path" in server.json (spec 6.5).
#SBATCH --job-name=harness-server
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=7-00:00:00
#SBATCH --output=/projects2/2026_n-pflugradt_building_simulations/slurm_logs/server-%j.out
#SBATCH --error=/projects2/2026_n-pflugradt_building_simulations/slurm_logs/server-%j.err

set -euo pipefail

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] server: $*"; }

log "=== harness server starting ==="
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
log "PYTHONPATH=${PYTHONPATH}"
log "config=${HARNESS_SERVER_CONFIG:-server.json} — handing off to the server process (its logs follow)"
exec python -m hpc_harness server --config "${HARNESS_SERVER_CONFIG:-server.json}"
