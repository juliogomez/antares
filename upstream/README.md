# Issues

Material for [cisco-foundation-ai/vulnerability-localization-benchmark](https://github.com/cisco-foundation-ai/vulnerability-localization-benchmark).

## Submitted

| Topic | Links |
|---|---|
| Quoted-pipe validator bug | [Issue #4](https://github.com/cisco-foundation-ai/vulnerability-localization-benchmark/issues/4), [PR #3](https://github.com/cisco-foundation-ai/vulnerability-localization-benchmark/pull/3) |
| Phase B silence = true negative | [Issue #5](https://github.com/cisco-foundation-ai/vulnerability-localization-benchmark/issues/5), [PR #6](https://github.com/cisco-foundation-ai/vulnerability-localization-benchmark/pull/6) |
| Local copies | [ISSUE.md](ISSUE.md), [ISSUE-phase-b-scoring.md](ISSUE-phase-b-scoring.md) |
| Patches | [0001-quote-aware-pipeline-split.patch](0001-quote-aware-pipeline-split.patch), [0002-phase-b-episode-termination.patch](0002-phase-b-episode-termination.patch) |

Validator score impact (Antares, 499 pairs): Phase A F1 **0.168 → 0.176**; identical repeat **0.397 → 0.262**. Replay: **35%** of commands wrongly rejected on old validator.

Keep validator and Phase B as **separate** public issues. [SECURITY-report-draft.md](SECURITY-report-draft.md) is **private** (allowlist bypasses).

## Measurements disclaimer

Our numbers use Antares-1B on mlx-lm on Apple Silicon, not reference vLLM. The harness changes are pure Python; absolute scores may differ from the public leaderboard.

Arm 2 in this repo (`runs/antares-fixed`) used validator commit `e00ec11`. Phase B PR branch: `fix/phase-b-episode-termination` (`d262466`).
