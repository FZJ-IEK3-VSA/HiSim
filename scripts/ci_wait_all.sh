#!/usr/bin/env bash
# Wait for prerequisite workflows to finish for a commit SHA, failing FAST as soon
# as any of them concludes non-success. Used by golden-year.yml so the heavy
# full-year golden check is only started once quality + tests + golden-check are
# green — and, if one of them fails, golden-year fails immediately with an
# explanatory message instead of burning the full-year compute.
#
# Usage: ci_wait_all.sh <head_sha> [workflow.yml ...]
# Defaults to the golden Tier-2 prerequisites: quality, tests, golden-check.
#
# Exit 0  -> every prerequisite completed successfully.
# Exit 1  -> a prerequisite failed/was cancelled, or the wait timed out (a GitHub
#            ::error:: annotation explains which).
#
# Requires the GitHub CLI (`gh`) with GH_TOKEN set and `actions: read` permission.
# Tunables (env): CI_WAIT_POLL_SECONDS (default 20), CI_WAIT_TIMEOUT_SECONDS (3600).
set -euo pipefail

SHA="${1:?usage: ci_wait_all.sh <head_sha> [workflow.yml ...]}"
shift || true
WORKFLOWS=("$@")
if [ "${#WORKFLOWS[@]}" -eq 0 ]; then
  WORKFLOWS=("quality.yml" "tests.yml" "golden-check.yml")
fi

POLL_SECONDS="${CI_WAIT_POLL_SECONDS:-20}"
DEADLINE=$(( $(date +%s) + ${CI_WAIT_TIMEOUT_SECONDS:-3600} ))

# Newest run of a workflow at this SHA, printed as "STATUS CONCLUSION".
# "none " when no run exists yet; CONCLUSION is empty until the run completes.
newest() {
  gh run list --workflow "$1" --commit "$SHA" --limit 20 \
    --json status,conclusion,createdAt \
    --jq 'sort_by(.createdAt) | last | if . == null then "none " else (.status + " " + (.conclusion // "")) end' \
    2>/dev/null || echo "none "
}

echo "Waiting for prerequisites of $SHA: ${WORKFLOWS[*]}"
while :; do
  pending=()
  for wf in "${WORKFLOWS[@]}"; do
    read -r status conclusion <<<"$(newest "$wf")" || true
    if [ "${status:-none}" = "completed" ]; then
      if [ "${conclusion:-}" != "success" ]; then
        echo "::error title=golden-year skipped::Prerequisite '$wf' concluded '${conclusion:-unknown}' for $SHA, so the full-year golden check was NOT run. Fix that check and re-run."
        exit 1
      fi
    else
      pending+=("$wf(${status:-none})")
    fi
  done
  if [ "${#pending[@]}" -eq 0 ]; then
    break
  fi
  if [ "$(date +%s)" -ge "$DEADLINE" ]; then
    echo "::error title=golden-year prerequisites timed out::Timed out after ${CI_WAIT_TIMEOUT_SECONDS:-3600}s waiting for: ${pending[*]} (SHA $SHA)."
    exit 1
  fi
  echo "  still pending: ${pending[*]} — retrying in ${POLL_SECONDS}s"
  sleep "$POLL_SECONDS"
done

echo "All prerequisites succeeded — proceeding with the full-year golden check."
exit 0
