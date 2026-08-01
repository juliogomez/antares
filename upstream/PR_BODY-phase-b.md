# Submitted as PR #6

**Live:** https://github.com/cisco-foundation-ai/vulnerability-localization-benchmark/pull/6  
**Fixes:** https://github.com/cisco-foundation-ai/vulnerability-localization-benchmark/issues/5

## Problem

Phase B `true_negative = submitted_nothing` conflates deliberate abstention, protocol failure, and API errors. Example: an untrained base model scored **99.6%** harness TNR with **0%** Phase A F1 by stopping without submit.

## Change

Adds **`episode_termination`** to each result:

`submitted_files | declared_none | no_tool_calls | turns_exhausted | api_error`

**Unchanged:** `true_negative_rate` (leaderboard compatible).

**New in Phase B aggregates:**

- `deliberate_true_negative_rate` (only `declared_none`)
- `termination_counts`

Per-entry Phase B JSON includes `deliberate_true_negative` (bool).

## Tests

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

7 cases in `tests/test_episode_termination.py`.

## Follow-up

A later PR could score Phase B true negatives only on `declared_none`. This PR adds visibility first.
