#!/usr/bin/env bash
# Fetch the official VLoc Bench harness at a pinned commit.
#
# The benchmark is deliberately NOT vendored into this repository. We want readers to
# see that our numbers come from Cisco's own scoring code, unmodified, rather than from
# a fork we could have quietly adjusted. Pinning a commit keeps the run reproducible
# even though upstream `main` moves.
#
# Upstream: https://github.com/cisco-foundation-ai/vulnerability-localization-benchmark
# License:  Apache-2.0
set -euo pipefail

REPO_URL="https://github.com/cisco-foundation-ai/vulnerability-localization-benchmark.git"
# Pinned 2026-07-28. Bump deliberately and re-run the smoke set when you do.
PINNED_REF="000c19cda9ba027e1d241216768b2b6358685000"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${ROOT}/vendor/vloc-bench"

# Corporate TLS interception: git and python must both trust the local CA bundle.
# See docs/TROUBLESHOOTING.md. We never disable verification.
if [[ -f "${HOME}/.config/corp-ca/ca-bundle.pem" ]]; then
  export SSL_CERT_FILE="${HOME}/.config/corp-ca/ca-bundle.pem"
  export GIT_SSL_CAINFO="${SSL_CERT_FILE}"
  export REQUESTS_CA_BUNDLE="${SSL_CERT_FILE}"
fi

if [[ -d "${DEST}/.git" ]]; then
  echo "==> updating existing checkout at ${DEST}"
  git -C "${DEST}" fetch --quiet origin "${PINNED_REF}" || git -C "${DEST}" fetch --quiet origin
else
  echo "==> cloning ${REPO_URL}"
  mkdir -p "$(dirname "${DEST}")"
  git clone --quiet "${REPO_URL}" "${DEST}"
fi

git -C "${DEST}" checkout --quiet "${PINNED_REF}"
ACTUAL="$(git -C "${DEST}" rev-parse HEAD)"
if [[ "${ACTUAL}" != "${PINNED_REF}" ]]; then
  echo "ERROR: checked out ${ACTUAL}, expected ${PINNED_REF}" >&2
  exit 1
fi

echo "==> vloc-bench pinned at ${ACTUAL}"

VENV_PIP="${ROOT}/.venv/bin/pip"
if [[ ! -x "${VENV_PIP}" ]]; then
  echo "ERROR: project venv missing; run: python3 -m venv .venv" >&2
  exit 1
fi

# Upstream pyproject lists vllm/torch/transformers for Cisco's CUDA stack. This POC
# serves models via mlx-lm and the harness runners only use HTTP (requests). Skip GPU
# deps so pip install works on Apple Silicon Macs.
echo "==> installing harness into .venv (editable, --no-deps; Mac/laptop path)"
"${VENV_PIP}" install --quiet -e "${DEST}" --no-deps
echo "==> installing harness runtime deps (requests, pyyaml, tqdm)"
"${VENV_PIP}" install --quiet "requests>=2.31" "pyyaml>=6.0" "tqdm>=4.66"

echo "==> verifying import"
"${ROOT}/.venv/bin/python" -c \
  "from vulnerability_localization_benchmark.model_runners import get_runner; get_runner('vllm_antares')"

echo "==> done. Harness lives at ${DEST} (gitignored)."
