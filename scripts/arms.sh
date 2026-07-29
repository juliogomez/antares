#!/usr/bin/env bash
# Run the remaining evaluation arms unattended, one model server at a time.
#
# This replaces the original overnight.sh queue. Two things changed after we looked at the
# data:
#
#  1. The third Antares arm (patched validator) was promoted ahead of the full Granite
#     sweep, because it answers a question we actually have — what the quote-splitting bug
#     costs — whereas the Granite sweep has a confound we have not yet resolved.
#
#  2. Arm 3 deliberately runs against the *same live server process* as arm 1. The only
#     difference between the two arms is a client-side git checkout of the harness, so no
#     server restart, no reload, no requantisation, nothing that could drift.
#
# Order:
#     arm 1  antares, upstream validator      (already running when this starts)
#     mop-up antares, upstream validator      the 5 pairs lost to an orphaned process
#     arm 3  antares, patched validator       same weights, same shim, same server
#     diag   granite serving-stack diagnostic decides whether a Granite arm is meaningful
#
#   python scripts/daemon.py --name arms -- bash scripts/arms.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${ROOT}/.venv/bin/python"
BENCH="${ROOT}/vendor/vloc-bench"
PR_BRANCH="fix/quote-aware-pipeline-split"
# Single source of truth for the pinned ref: the fetch script.
PINNED_REF="$(sed -n 's/^PINNED_REF="\(.*\)"$/\1/p' "${ROOT}/scripts/fetch_benchmark.sh")"
cd "${ROOT}"

export HF_HUB_DISABLE_XET=1
if [[ -f "${HOME}/.config/corp-ca/ca-bundle.pem" ]]; then
  export SSL_CERT_FILE="${HOME}/.config/corp-ca/ca-bundle.pem"
fi

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
count() { ls "$1"/*_phase_*.json 2>/dev/null | grep -vc trace; }

wait_for_job() {
  while "${PY}" scripts/daemon.py --status "$1" | grep -q "running"; do sleep 60; done
}

# Refuse to carry on if a run is full of infrastructure failures rather than model
# behaviour. Both times we were burned, the results looked fine and the guard did not exist.
health_gate() {
  if ! "${PY}" scripts/run_health.py "$1"; then
    log "ABORT: $1 contains infrastructure failures; not proceeding to the next arm"
    exit 1
  fi
}

# ---- arm 1: antares, upstream validator -------------------------------------------
if "${PY}" scripts/daemon.py --status sweep-antares | grep -q "running"; then
  log "arm 1 (antares, upstream validator) already running; waiting"
  wait_for_job sweep-antares
else
  log "arm 1 not running; starting it"
  ./scripts/serve.sh antares || exit 1
  "${PY}" scripts/run_eval.py --model antares --subset full --phases ab --out runs/antares-full
fi
log "arm 1 finished: $(count runs/antares-full) results"

# ---- mop-up: the pairs an orphaned process fabricated and we deleted ---------------
# Must run before the branch switch: these belong to arm 1 and need the upstream validator.
if [[ -s configs/mopup_ids.txt ]]; then
  log "mopping up $(wc -l < configs/mopup_ids.txt | tr -d ' ') pairs on the upstream validator"
  "${PY}" scripts/run_eval.py --model antares --subset mopup --phases ab --out runs/antares-full
  log "arm 1 complete: $(count runs/antares-full) results"
fi
health_gate runs/antares-full

# ---- arm 3: antares, patched validator --------------------------------------------
# Client-side change only. The server keeps running, untouched, between the two arms.
# Check out the resolved commit, detached, rather than the branch name. The branch is also
# checked out in the vendor/vloc-bench-pr worktree where the patch was developed, and git
# refuses to have one branch checked out in two worktrees. A detached checkout of the same
# commit is identical code, and provenance.json records the sha either way.
PR_SHA="$(git -C "${BENCH}" rev-parse "${PR_BRANCH}" 2>/dev/null)"
if [[ -z "${PR_SHA}" ]]; then
  log "ABORT: cannot resolve ${PR_BRANCH} in ${BENCH}"
  exit 1
fi
log "switching harness to ${PR_BRANCH} (${PR_SHA:0:9}), detached (editable install picks it up in-place)"
git -C "${BENCH}" checkout -q --detach "${PR_SHA}" || { log "ABORT: checkout failed"; exit 1; }
git -C "${BENCH}" log --oneline -1

if ! "${PY}" -c "
from vulnerability_localization_benchmark.sandbox import validate_command
ok, why = validate_command('rg -n \"unsafe|from_raw\"')
raise SystemExit(0 if ok else f'patched validator not active: {why}')
"; then
  log "ABORT: the patched validator is not what the venv imports"
  git -C "${BENCH}" checkout -q "${PINNED_REF}"
  exit 1
fi
log "patched validator confirmed active"

"${PY}" scripts/run_eval.py --model antares --subset full --phases ab --out runs/antares-fixed
log "arm 3 finished: $(count runs/antares-fixed) results"
health_gate runs/antares-fixed

log "restoring the harness to the pinned upstream ref"
git -C "${BENCH}" checkout -q "${PINNED_REF}"

# ---- comparison -------------------------------------------------------------------
log "measuring what the bug cost, by replaying every emitted command through both validators"
"${PY}" scripts/measure_validator_bug.py runs/antares-full \
  --json runs/validator_bug.json > runs/validator-bug-summary.txt 2>&1

log "comparing the two Antares arms"
"${PY}" scripts/analyze.py runs/antares-full runs/antares-fixed \
  --json-out runs/analysis-arms.json > runs/analysis-arms.txt 2>&1

# ---- granite serving-stack diagnostic ---------------------------------------------
log "stopping the antares server (never serve two models at once)"
"${PY}" scripts/daemon.py --stop serve-antares
sleep 10

log "starting granite for the serving-stack diagnostic"
./scripts/serve.sh granite || exit 1
"${PY}" scripts/granite_diagnostic.py --out runs/granite-diagnostic.json \
  > runs/granite-diagnostic.txt 2>&1
log "granite diagnostic written; a full Granite arm is gated on reading it"
"${PY}" scripts/daemon.py --stop serve-granite

log "done."
