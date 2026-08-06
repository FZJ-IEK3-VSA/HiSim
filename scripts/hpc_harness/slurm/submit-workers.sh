#!/bin/bash
# Scale the fleet by hand (spec §13): submit M worker jobs.
#   ./submit-workers.sh 20                 # 20 whole-node workers
#   ./submit-workers.sh 200 single_core    # 200 single-core workers
# Scale up mid-run by running it again; scale down with scancel — the server
# requeues anything in flight (fenced, spec §5.1).
set -euo pipefail

COUNT="${1:?usage: submit-workers.sh COUNT [whole_node|single_core]}"
MODE="${2:-whole_node}"
HERE="$(cd "$(dirname "$0")" && pwd)"

if [ "$MODE" = "single_core" ]; then
    SCRIPT="$HERE/worker_single_core.sbatch"
else
    SCRIPT="$HERE/worker.sbatch"
fi

for _ in $(seq 1 "$COUNT"); do
    sbatch "$SCRIPT"
done
echo "Submitted $COUNT $MODE worker(s)."
