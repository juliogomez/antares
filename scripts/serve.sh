#!/usr/bin/env bash
# Serve a model locally with mlx_lm.server.
#
#   scripts/serve.sh antares    # shimmed Antares-1B on :8080  (raw /v1/completions)
#   scripts/serve.sh granite    # historical only; see configs/granite.yaml
#
# Both are served in bf16 with no quantization, so the weights are exactly what the
# checkpoint publishes. Logs go to runs/logs/serve-<name>.log; the PID to runs/logs/.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WHICH="${1:-antares}"

export HF_HUB_DISABLE_XET=1
if [[ -f "${HOME}/.config/corp-ca/ca-bundle.pem" ]]; then
  export SSL_CERT_FILE="${HOME}/.config/corp-ca/ca-bundle.pem"
fi

case "${WHICH}" in
  antares)
    MODEL="${ROOT}/models/antares-1b-shim"
    PORT=8080
    if [[ ! -d "${MODEL}" ]]; then
      echo "shim missing. Run: python scripts/build_mlx_shim.py" >&2
      exit 1
    fi
    ;;
  granite)
    # Served straight from the HF cache: Granite needs no shim, and we deliberately
    # leave mlx-lm's tool parser enabled so its native tool calls are parsed for us.
    MODEL="ibm-granite/granite-4.0-1b"
    PORT=8081
    ;;
  *)
    echo "usage: $0 [antares|granite]" >&2
    exit 2
    ;;
esac

JOB="serve-${WHICH}"
LOG="${ROOT}/runs/logs/${JOB}.log"

echo "==> serving ${MODEL} on 127.0.0.1:${PORT}"
# Detached via scripts/daemon.py: an overnight sweep must not die with its terminal.
#
# The prompt cache is capped deliberately. Left at its defaults, mlx_lm.server retains
# ten KV caches and grew to 2.4 GB per server during our first sweep attempt; two servers
# plus Docker's VM exhausted a 24 GB machine, every request then blew through the runner's
# 180 s timeout, and the harness happily recorded those as zero-tool-call results. A
# sweep only needs one live conversation at a time, so two cached sequences is plenty.
# See docs/TROUBLESHOOTING.md.
"${ROOT}/.venv/bin/python" "${ROOT}/scripts/daemon.py" --name "${JOB}" -- \
  "${ROOT}/.venv/bin/python" -m mlx_lm server \
  --model "${MODEL}" --host 127.0.0.1 --port "${PORT}" \
  --prompt-cache-size 2 --prompt-cache-bytes 1500000000

for _ in $(seq 1 180); do
  if curl -sf "http://127.0.0.1:${PORT}/v1/models" >/dev/null 2>&1; then
    echo "==> ready on port ${PORT}, log: ${LOG}"
    exit 0
  fi
  sleep 1
done

echo "server did not become ready in 120s; see ${LOG}" >&2
tail -20 "${LOG}" >&2
exit 1
