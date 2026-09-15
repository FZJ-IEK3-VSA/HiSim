#!/usr/bin/env bash
# One-shot classifier for golden-year.yml's gate: are the prerequisite workflows
# of a commit done, and did they pass? Exactly one `gh run list` query per
# workflow — no polling, no sleeping, no runner held open. The wait itself is the
# workflow_run trigger; this script only reads where things stand right now.
#
# Usage: ci_prereqs_done.sh <head_sha> [workflow.yml ...]
# Defaults to the golden Tier-2 prerequisites: quality, tests, golden-check.
#
# Prints one verdict line. The first word is the verdict; any words after it name
# the workflows concerned, with their status or conclusion in brackets:
#
#   ready                          every prerequisite has a completed run, all successful
#   pending quality.yml(none) ...  at least one has no completed run yet — a later
#                                  completion of that workflow will fire this one again
#   failed tests.yml(failure) ...  at least one completed non-success
#
# Exits 0 for all three verdicts: "not green" is an answer, not an error. It exits
# non-zero only when its own arguments are missing.
#
# Requires the GitHub CLI (`gh`) with GH_TOKEN set and `actions: read` permission.
set -euo pipefail

SHA="${1:?usage: ci_prereqs_done.sh <head_sha> [workflow.yml ...]}"
shift || true
WORKFLOWS=("$@")
if [ "${#WORKFLOWS[@]}" -eq 0 ]; then
  WORKFLOWS=("quality.yml" "tests.yml" "golden-check.yml")
fi

# Newest run of a workflow at this SHA, printed as "STATUS CONCLUSION".
# "none " when no run exists yet; CONCLUSION is empty until the run completes.
# Newest rather than first: a re-run in flight means the answer is "not yet".
newest() {
  gh run list --workflow "$1" --commit "$SHA" --limit 20 \
    --json status,conclusion,createdAt \
    --jq 'sort_by(.createdAt) | last | if . == null then "none " else (.status + " " + (.conclusion // "")) end' \
    2>/dev/null || echo "none "
}

pending=()
failed=()
for wf in "${WORKFLOWS[@]}"; do
  read -r status conclusion <<<"$(newest "$wf")" || true
  if [ "${status:-none}" = "completed" ]; then
    if [ "${conclusion:-}" != "success" ]; then
      failed+=("$wf(${conclusion:-unknown})")
    fi
  else
    pending+=("$wf(${status:-none})")
  fi
done

# A failure outranks a pending one: there is no point waiting for the rest when the
# full-year matrix is already not going to run.
if [ "${#failed[@]}" -gt 0 ]; then
  echo "failed ${failed[*]}"
elif [ "${#pending[@]}" -gt 0 ]; then
  echo "pending ${pending[*]}"
else
  echo "ready"
fi
