# Results

499 repos, Phase A (vulnerable snapshots). Stored runs also include Phase B for full VLoc reproduction. Phase B can be ignored unless you are reproducing the full benchmark.

**Regenerate from stored runs** (Arm 1 = [`runs/antares-full/`](../runs/antares-full/), Arm 2 = [`runs/antares-fixed/`](../runs/antares-fixed/)):

```bash
python scripts/analyze.py runs/antares-full runs/antares-fixed --markdown
python scripts/measure_validator_bug.py runs/antares-full --json runs/validator_bug.json
```

**New Phase A-only sweep:** `python scripts/run_eval.py --model antares --subset full --phases a`

## Summary

1. **Iterative search runs:** ~13–14 terminal calls per repo on average—the model uses the multi-turn budget, not a single grep.
2. **File localization (hit@any):** **32–33%** of repos—at least one ground-truth file appears somewhere in the submitted list (Phase A; bug already present). Mean file F1 **0.17–0.18** because lists are often over-broad or incomplete.
3. **Training matters:** Granite-4.0-1b control on the same protocol: **0% hit@any** (`runs/granite-full`).
4. Phase A numbers assume the bug is present; on patched trees expect false paths ([VALUE.md](VALUE.md)).
5. Validator bug: ~35% of commands wrongly rejected (quoted `|` in regex). Arm 2 (fixed validator) adds **+0.008** F1 and **+0.012** hit@any.

## Phase A

| Run | Mean F1 | hit@any | Abstain | Terminal calls | Wall time |
|---|---|---|---|---|---|
| Arm 1 (official validator) | 0.168 | 0.317 | 0.052 | 13.6 | ~18 h* |
| Arm 2 (fixed validator) | 0.176 | 0.329 | 0.052 | 13.3 | ~10 h* |

\*Arms 1–2 used `--phases ab`; Phase A ≈ half the wall time.

## Validator replay (Arm 1)

| | Count |
|---|---|
| Commands emitted | 13,653 |
| Rejected | 6,875 (50%) |
| Quoted `\|` misparse | 4,744 (35%) |
| Tasks losing ≥1 call to bug | 977 / 998 |

[Issue #4](https://github.com/cisco-foundation-ai/vulnerability-localization-benchmark/issues/4), [PR #3](https://github.com/cisco-foundation-ai/vulnerability-localization-benchmark/pull/3).

## Health

~45 truncated episodes per arm. Run `python scripts/run_health.py runs/<dir>` before trusting a sweep. Bad timeout/orphan JSONs were deleted; see [Study design](#study-design) below.

## Phase B (reproducers only)

Not used for headline conclusions. Antares submits on most patched trees; 0% deliberate `submit_no_vulnerability_found`. Full tables via `analyze.py` on `runs/antares-full`.

## vs Cisco’s 0.209

[Cisco reports 0.209 mean file F1](https://huggingface.co/fdtn-ai/antares-1b) on VLoc Bench for Antares-1B (vLLM on GPU, averaged runs). Our reproduction:

| Factor | Cisco reference | This repo (Arm 1 → Arm 2) |
|---|---|---|
| Serving | vLLM, H100-class GPU | mlx-lm, Apple Silicon |
| Runs averaged | Multiple | One sweep each |
| Harness validator | Official | Official → quote-aware fix |
| Phase A mean F1 | **0.209** (published) | **0.168 → 0.176** |
| Phase A hit@any | (see model card) | **0.317 → 0.329** |

Same open weights and benchmark task—not identical leaderboard conditions. Arm 2 isolates how much the quoted-pipe validator bug cost; stack differences explain part of the remaining gap. [INTERNALS.md](INTERNALS.md).

## Study design

Metrics were fixed before the full sweep finished.

**Questions**

1. How well does Antares localise on real repos? (Phase A, 499 vulnerable snapshots)
2. How much does the sandbox quoted-pipe validator bug cost? (Arm 2 = quote-aware fix, same model)

Default eval uses `--phases a`. Stored sweeps used `--phases ab` for full VLoc reproduction.

**Arms (July 2026)** — two Antares sweeps on the same 499 repo pairs; only the harness validator differs. Arm 2 uses the quote-aware fix from [upstream PR #3](https://github.com/cisco-foundation-ai/vulnerability-localization-benchmark/pull/3).

| Arm | Harness | Output | Wall time |
|---|---|---|---|
| 1 | Official validator | `runs/antares-full` | ~18 h (A+B) |
| 2 | Quote-aware validator | `runs/antares-fixed` | ~10 h (A+B) |

Same Antares weights; only harness checkout differs.

**Metrics**

- **Phase A (primary):** mean file F1, hit@any, abstain rate, mean terminal calls. Ground truth = patch files (tests excluded).
- **Phase B / paired:** reproducers only. See `scripts/analyze.py`.

Benchmark subsets: [GETTING_STARTED.md](GETTING_STARTED.md).

**Removed bad data**

- 74 + 960 timeout/orphan JSONs (dead server scored as abstain)
- 37 Arm 2 timeouts after mlx-lm crash
- ~45 truncated episodes per arm (`run_health.py`)

**Limits**

- Temperature 0.3, one run per entry, mlx-lm on Apple Silicon (not Cisco’s vLLM/H100)
- Public CVE repos may appear in pretraining

Granite control run archived in `runs/granite-full`. Not part of the arms above.
