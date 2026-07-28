# Annotated traces

Three real traces from this repository's runs, scrubbed of absolute paths by
`scripts/export_trace.py`. All three are the same repository — `tokio-rs/slab`, entry
`Kv0mIk0C`, CWE-119 (improper restriction of operations within the bounds of a memory
buffer) — so the comparisons are like for like.

| file | model | phase | outcome |
|---|---|---|---|
| `antares-slab-phase-a.json` | Antares-1B | A (vulnerable tree) | submitted `src/lib.rs`, file F1 **1.0** |
| `antares-slab-phase-b.json` | Antares-1B | B (patched tree) | submitted `src/lib.rs` again — false positive |
| `granite-slab-phase-a.json` | Granite-4.0-1B | A (vulnerable tree) | never submitted anything |

---

## Antares, Phase A: a clean success with a lot of wasted budget

**Turn 1.** The reasoning block is short. This is characteristic: Antares does not produce
long chain-of-thought, it produces a one-line intention and then acts.

```
<think>
Start by identifying the project structure and key source files.
</think>
<tool_call>
{"name": "terminal", "arguments": {"command": "cd /workspace/repo && ls"}}
</tool_call>
```

Note the format. There is no constrained decoding and no grammar in the serving stack; the
model simply learned to emit this markup, and the harness recovers the call with a regex.
That is the whole tool-calling mechanism.

**Turns 1–3 are thrown away.** The response to that first call is:

```
ERROR: Command rejected — denied pattern: &&
```

The sandbox forbids `&&`. The model retries with `cd /workspace/repo; ls` and is told
`command not allowed: cd`. Two of its fifteen calls are gone before it has seen a single
file, and both were spent on a habit that was never necessary: the sandbox already executes
with `-w /workspace/repo`, so the working directory was correct from the start. Across our
sweep this pattern costs about 1.6 calls per episode for `&&` and another 1.6 for `cd`.

**Turn 4 works** and the model gets the tree. Then on turn 5 it writes what any engineer
would write:

```
rg -n "unsafe|from_raw|ptr::|copy_|copy_nonoverlapping|slice::from_raw|offset|add_unchecked|add\(" src tests
```

and receives:

```
ERROR: Command rejected — command not allowed: from_raw
```

**This is a harness bug, not a model error.** `validate_command` splits the command string
on `|` to find pipeline stages, without regard for quoting, so the regex alternation inside
the quoted pattern is read as a chain of eight commands and the second "command" is
`from_raw`. Verify it yourself:

```python
from vulnerability_localization_benchmark.sandbox import validate_command
validate_command('rg -n "unsafe|from_raw" src')   # (False, 'command not allowed: from_raw')
```

Alternation is the single most natural idiom for "search for any of these dangerous
patterns", which is precisely the task. Across the sweep, **41% of all Antares terminal
calls were rejected, and two-thirds of those rejections were this quoting defect** — around
a quarter of the total exploration budget. Every published VLoc Bench score is depressed by
it, for every model.

**Turns 6–13** show the model recovering: it falls back to single-term searches, finds the
`unsafe` blocks in `src/lib.rs`, reads around line 836, and searches for `get_unchecked`.
It also repeats `grep -RIn "ptr::" src tests` twice despite `frequency_penalty: 0.3`, which
is a reminder that the penalty discourages repetition without preventing it.

**Turn 14** submits `src/lib.rs`. Ground truth is `src/lib.rs`. Precision 1.0, recall 1.0,
F1 1.0.

---

## Antares, Phase B: the same answer on the patched tree

Phase B hands the model the *same repository after the security patch has been merged*. The
correct behaviour is to call `submit_no_vulnerability_found`.

Compare the two traces and the resemblance is uncomfortable. The model opens with the same
`cd /workspace/repo && ls`, hits the same rejections, runs a near-identical sequence of
searches, arrives at the same file, and submits `src/lib.rs` again.

It is not comparing the code against a notion of "already fixed". It is matching the CWE
description to the most plausible-looking file, which on both trees is the one containing
the `unsafe` blocks. Across the smoke set, in 80% of the pairs where it submitted twice, the
Phase B answer was the *identical file list* as Phase A.

That is the core finding of this project, and it is why `docs/METHODOLOGY.md` scores the two
phases jointly rather than separately.

---

## Granite, Phase A: how a control arm fails

Granite-4.0-1B is the base instruct model Antares was post-trained from, given the API it
was built for: `/v1/chat/completions` with native OpenAI tool calling.

First, an artifact of the harness worth knowing about: every assistant message in this trace
has empty content. That is not the model being silent. The agent loop stores only
`message["content"]` in the trace and discards the structured `tool_calls` field, so for the
generic runner the tool calls vanish from the record even though they were made. You can
only see them indirectly, through the tool results that follow.

What Granite actually does is explore reasonably and then stop:

```
ls -R                       ->  full tree
ls                          ->  top level again
cat Cargo.toml              ->  package metadata
grep -R "builder" Cargo.toml
```

Then it produces three consecutive turns with no tool call, the loop's three-strikes rule
fires, and the episode ends with nothing submitted. Four calls of its fifteen used, no
hypothesis about the vulnerability, no submission.

Two things follow. First, the difference the post-training makes is not mainly "can it call
tools" — Granite calls tools — it is *persistence and closure*: knowing to keep searching
and to commit to an answer. Second, and more importantly for anyone reading benchmark
numbers:

> This run, in which the model achieved nothing, is scored in Phase B as a **true
> negative**. `score_phase_b` is `true_negative = submitted_nothing`. Granite scored a
> perfect 1.0 true-negative rate on the smoke set, and 100% of that credit came from runs
> exactly like this one.

Which is why the analysis in this repository separates deliberate abstention from protocol
failure, and reports both.
