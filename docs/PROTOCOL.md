# How the benchmark works

VLoc Bench is not a chat demo. Each episode:

1. Mount a real repo in Docker (read-only, no network).
2. Give the model a CWE description, not the CVE write-up.
3. Allow up to 15 terminal commands and 20 turns.
4. Require a final call: `submit_vulnerable_files` or `submit_no_vulnerability_found`.

Phase A is the primary view ([VALUE.md](VALUE.md)). Phase B is optional benchmark material below.

## Phase A: find files on the vulnerable snapshot

- Repo checked out at the pre-fix commit.
- Ground truth = source files touched by the security patch (tests excluded).
- Model explores and submits paths; scored against ground truth.

**Metrics:**

| Metric | Meaning |
|---|---|
| File F1 | Overlap between submitted files and ground truth (precision/recall blend) |
| hit@any | At least one submitted file matched ground truth (per repo; Phase A) |
| Abstain | Episode ended with no submission |

This maps to: given CWE + repo, where should I look?

## Agent loop

1. Model outputs reasoning (`<think>`) + a tool call (`terminal` or submit).
2. Harness runs the command in the container (or records the submit).
3. Result goes back into the conversation.
4. Repeat until submit or limits.

Scoring uses **submitted file paths only**. Reasoning in the trace is not graded but is part of what makes the run reviewable ([VALUE.md](VALUE.md); step 0 in [GETTING_STARTED.md](GETTING_STARTED.md)).

If the loop stops without submitting, that counts as abstain (Phase A F1 = 0).

## Sandbox

Ubuntu container, non-root user, allowlisted commands (`ls`, `find`, `rg`, `grep`, …). Blocks `&&`, `cd`, redirects, and interpreters.

**Known bug:** `|` inside quotes is treated as a pipe, so `rg 'foo|bar'` is rejected. That hit ~35% of Antares commands in our sweep ([upstream PR #3](https://github.com/cisco-foundation-ai/vulnerability-localization-benchmark/pull/3)).

[`minimal_agent.py`](../minimal_agent.py) uses the same rules in a read-only mount for local demos.

## Antares on the wire

Raw `/v1/completions` (not chat API), Granite-style role tokens, `<think>` + `<tool_call>{json}</tool_call>`. Details: [INTERNALS.md](INTERNALS.md).

## Phase B (benchmark only)

Same CWE prompt on the patched tree. Benchmark expects “no vulnerability left.”

Antares was trained to find files, not certify fixes. In our sweep it almost always submits on Phase B and never calls `submit_no_vulnerability_found`, which matches the false-positive warning in [VALUE.md](VALUE.md).

Stored runs used `--phases ab` for full reproduction; default eval uses `--phases a`.

Reproducers: Phase B “true negative” conflates deliberate abstention with timeouts and errors. See [upstream issue #5](https://github.com/cisco-foundation-ai/vulnerability-localization-benchmark/issues/5) and [RESULTS.md](RESULTS.md#study-design).

## Example

[GETTING_STARTED.md](GETTING_STARTED.md#step-0-read-a-saved-run-no-install): Antares on `tokio-rs/slab` (CWE-119), sandbox rejections, recovery, correct submit.
