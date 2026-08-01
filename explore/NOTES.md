# Official Cisco Antares CLI — exploration notes

Scratch work for deciding whether to adopt the HF-packaged CLI alongside this repo’s `minimal_agent.py` / VLoc harness. **Not linked from main docs yet.**

## Source

- ZIP: `assets/antares-cli.zip` on [fdtn-ai/antares-1b](https://huggingface.co/fdtn-ai/antares-1b) (already in local HF cache).
- Extracted to `explore/antares-cli/` for inspection.
- Installed in project venv: `pip install -e explore/antares-cli` → `antares` 1.0.0.

## Quick try (same mlx-lm server as this repo)

Terminal A (if not running):

```bash
./scripts/serve.sh antares
```

Terminal B:

```bash
git clone --depth 1 https://github.com/tokio-rs/slab /tmp/slab   # once

antares query /tmp/slab \
  --cwe CWE-119 \
  --endpoint "http://127.0.0.1:8080/v1/completions" \
  --model "/Users/jgomez2/dev/antares/models/antares-1b-shim" \
  --format json \
  --no-report
```

Optional profile in `~/.antares/profiles.toml` (see CLI README); flags above are enough for a one-off.

## MBP smoke test (2026-07-31)

| | Official CLI | This repo (step 0 trace / harness) |
|---|---|---|
| Repo | `/tmp/slab` | tokio-rs/slab |
| CWE | `CWE-119` (ID → built-in CWE DB text) | Full CWE-119 description in manifest |
| Endpoint | mlx-lm `:8080/v1/completions` | same |
| Finding | **`src/lib.rs`** (rank 1) | **`src/lib.rs`** (F1 1.0) |
| Tool calls | 15 (budget exhausted) | 13 terminal |
| Failed tool calls | 3 | includes validator `&&` / `\|` rejects in trace |
| Wall time | ~41 s | ~17.5 s agent time in stored trace |
| Output | JSON + optional SARIF (`explore/runs/cli-slab-report/report.sarif`) | `*_phase_a.json` + trace |

Same headline file on the same weights and endpoint — good sign for Foundry / local demos.

## What the official CLI adds

| Feature | CLI | This repo |
|---|---|---|
| **SARIF 2.1.0** | Yes (`report.sarif`, GitHub Code Scanning shape) | No (paths in JSON only) |
| **Human report** | Rich TUI + `report.md` | Terminal / trace JSON |
| **`antares sweep`** | Repo-aware multi-CWE TUI workflow | Manual `--cwe` or benchmark manifest |
| **CWE input** | `--cwe CWE-119` (+ optional `--query`) | Full description string or manifest |
| **CI** | `--fail-on-findings` | Harness scoring only |
| **Trace export** | `--export` redacted `.tar.gz` | `--save-trace`, `export_trace.py` |
| **Prompt-injection quarantine** | Scans tool output | No |
| **Sensitive path blocks** | `.ssh`, `.docker`, etc. | Harness allowlist only |

## Important differences (not cosmetic)

1. **Isolation** — CLI copies repo to a **temp read-only snapshot** and runs allowlisted shell on the **host** (`readonly_workspace.py`, `shell_exec.py`). This repo uses a **Docker** sandbox (`network=none`) for both `minimal_agent.py` and the harness. Same allowlist idea; different trust boundary.

2. **Inference contract** — CLI requires **`POST /v1/completions`** (not chat). Model ID must **exact** match server (`/Users/.../antares-1b-shim`). Same as our shim setup.

3. **Generation defaults** — CLI remote backend uses **max_tokens 4096**; harness / `minimal_agent.py` use **16384**. Can affect long thinking + tool-call turns.

4. **System prompt** — Same Antares tool markup and core task text; CLI omits harness line *“Use at least 7-10 terminal calls…”* and uses `{terminal_call_budget}` in the tool description. User prompt built from **embedded CWE database** (includes likelihood-of-exploit metadata).

5. **Benchmark scoring** — Official CLI is **not** VLoc Bench. No `file_f1`, hit@any, or manifest/Phase B. For published numbers, keep `run_eval.py`.

## What we’re running instead (recap)

| Job | Tool |
|---|---|
| Readable local demo | `minimal_agent.py` |
| Scored 499-repo study | `run_eval.py` → `vulnerability_localization_benchmark.cli` |
| Inference | `scripts/serve.sh` + `models/antares-1b-shim` |

## When official CLI might be worth adopting

- **Foundry Localizer** — SARIF/JSON findings + `--fail-on-findings` without writing an emitter.
- **Product-shaped demos** — `antares sweep`, reports under `~/.antares` or `--output`.
- **Cross-check** — same endpoint, compare file lists to harness on a few repos.

## When to stay on this repo’s runners

- Reproducing **stored sweeps** and validator A/B arms.
- **Protocol/debug** — `minimal_agent.py` + `test_protocol_fidelity.py` vs harness source.
- **Docker-only** safety preference for untrusted code.

## Open questions

- Does 4096 max_tokens change scores on harder repos vs harness 16384?
- Sweep-selected CWEs vs single `--cwe` — behavior for Foundry (advisory → CWE mapping).
- Pin CLI version alongside harness commit in provenance?

## Smoke subset A/B (5 repos, 2026-07-31)

Script: `explore/compare_smoke.py` → `explore/runs/compare-smoke/comparison.{json,md}`

Same mlx-lm endpoint, same benchmark zips (`configs/smoke_ids.txt`), Phase-A vulnerable trees.

| | Docker (`minimal_agent.py`) | Official CLI |
|---|---|---|
| **Total wall (5 repos)** | **87.0 s** | 92.5 s |
| **hit@any** | 5/5 | 5/5 |
| **Per-repo delta** | — | −0.9s to +4.9s vs container (mixed) |

**Conclusion:** On this Mac smoke run, **CLI is not faster overall**—inference dominates; one container per episode + `docker exec` per command is only a few seconds across five small repos. CLI was ~5 s slower total; container won 3/5 on wall time, CLI won 2/5.

First run failed: stale Docker image metadata (rebuild fixed it).

## Artifacts from this exploration

- `explore/runs/cli-slab-cwe119.json` — stdout JSON from query
- `explore/runs/cli-slab-report/report.sarif` — SARIF sample
