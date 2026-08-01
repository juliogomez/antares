# Issue #5 (submitted upstream)

**Repo:** `cisco-foundation-ai/vulnerability-localization-benchmark`  
**Live:** https://github.com/cisco-foundation-ai/vulnerability-localization-benchmark/issues/5  
**PR:** https://github.com/cisco-foundation-ai/vulnerability-localization-benchmark/pull/6

Separate from the quoted-pipe validator ([#4](https://github.com/cisco-foundation-ai/vulnerability-localization-benchmark/issues/4) / [PR #3](https://github.com/cisco-foundation-ai/vulnerability-localization-benchmark/pull/3)).

## Summary

Phase B sets `true_negative = submitted_nothing`.

`submitted_nothing` is true whenever the episode ends without `submit_vulnerable_files` or `submit_no_vulnerability_found`. That includes:

- real abstention (`submit_no_vulnerability_found`)
- loop stop (no tool call 3 times, turns exhausted)
- **API errors** (timeout, connection refused, etc.)

So infrastructure failure can look like perfect Phase B specificity.

## What we saw locally (full sweeps, 499 pairs)

1. **Dead model server:** orphan benchmark process. 960 bogus Phase B true negatives.
2. **Read timeouts:** ~45 Antares episodes per arm at 180 s timeout.
3. **Antares:** 0 calls to `submit_no_vulnerability_found`; harness TNR ~5% (over-submits on patched trees).
4. **Historical:** an untrained base-model sweep scored **99.6%** harness TNR with **0%** Phase A F1; silence counted as specificity.

## PR approach

PR #6 adds `episode_termination` to each result and `deliberate_true_negative_rate` + `termination_counts` to Phase B aggregates. **Does not change** existing `true_negative_rate` (leaderboard compatible).

Patch export: [0002-phase-b-episode-termination.patch](0002-phase-b-episode-termination.patch)
