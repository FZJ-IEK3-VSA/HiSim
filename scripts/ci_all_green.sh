#!/usr/bin/env bash
# Gate helper for golden-year.yml: print "true" iff every prerequisite workflow
# concluded successfully for the given commit SHA, else "false".
#
# Usage: ci_all_green.sh <head_sha> [workflow.yml ...]
# Defaults to the golden Tier-2 prerequisites: quality, tests, golden-check.
#
# Requires the GitHub CLI (`gh`) with GH_TOKEN set. Never exits non-zero on a
# "not green" result — it communicates via stdout so the caller can branch.
set -euo pipefail

SHA="${1:?usage: ci_all_green.sh <head_sha> [workflow.yml ...]}"
shift || true
WORKFLOWS=("$@")
if [ "${#WORKFLOWS[@]}" -eq 0 ]; then
  WORKFLOWS=("quality.yml" "tests.yml" "golden-check.yml")
fi

for wf in "${WORKFLOWS[@]}"; do
  # Conclusion of the most recent completed run of this workflow for this SHA.
  conclusion="$(gh run list --workflow "$wf" --commit "$SHA" \
      --json status,conclusion \
      --jq '[.[] | select(.status=="completed")][0].conclusion' 2>/dev/null || echo "")"
  if [ "$conclusion" != "success" ]; then
    echo "false"
    exit 0
  fi
done

echo "true"
