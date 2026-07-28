#!/usr/bin/env bash
# Run the remaining sweeps unattended, one model at a time.
#
# Serving both models at once is what broke our first attempt: two mlx_lm.server processes
# plus Docker's VM exhausted a 24 GB machine, every request blew through the runner's
# 180-second timeout, and the harness recorded a thousand plausible-looking but entirely
# fabricated results. So this script strictly serialises: one server up at a time, the
# previous one stopped before the next starts.
#
# If an Antares sweep is already running it waits for it rather than competing with it.
# Both sweeps use --resume, so re-running this after a crash picks up where it stopped.
#
#   python scripts/daemon.py --name overnight -- bash scripts/overnight.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${ROOT}/.venv/bin/python"
cd "${ROOT}"

export HF_HUB_DISABLE_XET=1
if [[ -f "${HOME}/.config/corp-ca/ca-bundle.pem" ]]; then
  export SSL_CERT_FILE="${HOME}/.config/corp-ca/ca-bundle.pem"
fi

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

wait_for_job() {
  local name="$1"
  while "${PY}" scripts/daemon.py --status "${name}" | grep -q "running"; do
    sleep 60
  done
}

# ---- Antares ---------------------------------------------------------------------
if "${PY}" scripts/daemon.py --status sweep-antares | grep -q "running"; then
  log "antares sweep already running; waiting for it"
  wait_for_job sweep-antares
else
  log "starting antares sweep"
  ./scripts/serve.sh antares || exit 1
  "${PY}" scripts/run_eval.py --model antares --subset full --phases ab --out runs/antares-full
fi
log "antares sweep finished: $(ls runs/antares-full/*_phase_*.json 2>/dev/null | grep -vc trace) results"

# ---- Granite ---------------------------------------------------------------------
log "stopping the antares server before starting granite (never serve both)"
"${PY}" scripts/daemon.py --stop serve-antares
sleep 10

log "starting granite sweep"
./scripts/serve.sh granite || exit 1
"${PY}" scripts/run_eval.py --model granite --subset full --phases ab --out runs/granite-full
log "granite sweep finished: $(ls runs/granite-full/*_phase_*.json 2>/dev/null | grep -vc trace) results"

"${PY}" scripts/daemon.py --stop serve-granite

# ---- Analysis --------------------------------------------------------------------
log "writing analysis"
"${PY}" scripts/analyze.py runs/antares-full runs/granite-full \
  --json-out runs/analysis.json > runs/analysis-summary.txt 2>&1
"${PY}" scripts/analyze.py runs/antares-full runs/granite-full \
  --markdown > runs/results-tables.md 2>&1

log "done. Tables in runs/results-tables.md; fold them into docs/RESULTS.md."
