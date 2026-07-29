# DRAFT — not submitted

Target repo: `cisco-foundation-ai/vulnerability-localization-benchmark`
Proposed title: **Phase B credits a true negative whenever an episode ends without submitting, including on API failure**

Reviewer notes, not part of the issue text:

- Separate from the `validate_command` quoting issue; they share no code.
- Measured on the pinned ref `000c19c`.
- This is a scoring-validity concern, not a security one, so it belongs in a public issue
  under `CONTRIBUTING.md`.

---

## Summary

Phase B scores specificity as `true_negative = submitted_nothing`:

```python
# scoring.py
def score_phase_b(submitted: list[str], submitted_nothing: bool) -> dict:
    true_negative = submitted_nothing
```

and `submitted_nothing` is set for **any** episode that ends without an explicit submission:

```python
# agent.py, after the turn loop
    if not finished:
        log.warning(f"[{container_name}] Agent exhausted {max_turns} turns without submitting")
        submitted_nothing = len(submitted_files) == 0
```

Three different things reach that line, and only one of them is a judgement about the code:

1. the model called `submit_no_vulnerability_found` — a real abstention;
2. the model ran out of turns, or emitted three consecutive turns with no tool call and was
   stopped by the three-strikes rule — it never closed;
3. `runner.generate` raised, and the loop broke:

```python
        try:
            assistant_msg, parsed_calls = runner.generate(conv)
        except Exception as e:
            log.error(f"API error on turn {turn}: {e}")
            break
```

Case 3 includes read timeouts, connection refusals, and any other transport error. So a
transient failure — or a model server that is not running at all — is scored as correct
behaviour on the patched tree. The failure mode is silent: the result file it produces is
well-formed and, apart from `n_terminal_calls`, looks like an ordinary abstention.

Phase A is affected too, though less dangerously: the same episode is recorded there as an
abstention and scores F1 0.0, which at least penalises rather than rewards.

## Why this is more than a theoretical edge case

We hit all three cases in one week of running the benchmark locally.

**A dead server scores perfectly.** We stopped a sweep, but our own process manager killed only
the wrapper and orphaned the harness process, which kept running after the model server it
depended on was gone. Every request failed instantly with connection refused, the loop broke
on each one, and the harness wrote **960 result files in four minutes — every one of them a
Phase B true negative**. Nothing in the output distinguished them from real abstentions; we
found them by noticing that `agent_time_s` had a median of 0.00 seconds. Had we not looked,
we would have reported a Phase B specificity of 1.0 for a model that was never asked a single
question. That is our bug, not yours, but the harness turned it into a perfect score instead
of an error.

**Transient timeouts silently become true negatives.** In a single healthy 499-pair Antares
sweep — one server, no memory pressure, `workers: 1` — the log contains **33 `API error on
turn N: Read timed out (read timeout=180)`** entries. Each one truncated an episode that was
midway through exploring, with terminal-call budget still unspent, and each was recorded as
`submitted_nothing=True`. In Phase B, 16 of 412 entries (3.9%) were credited as true
negatives this way. The cause is benign — one long generation exceeding the client's read
timeout — but the effect is that a slow generation is scored as a correct security judgement.

**It contaminates good models, not just broken ones.** This is the part we found most
persuasive. Decomposing our Antares-1B Phase B results (412 entries, all health-checked):

| | count | rate |
|---|---|---|
| harness true-negative rate | 18 | 4.4% |
| — called `submit_no_vulnerability_found` | **0** | **0.0%** |
| — episode truncated by an API error | 16 | 3.9% |
| — never closed (unknown tool, gave up) | 2 | 0.5% |

Antares-1B did not deliberately abstain once in 412 Phase B entries. Its entire reported
specificity is artifact. A reader comparing 4.4% against another model's 4.4% would be
comparing two numbers that may share no behavioural content at all.

And a weak model can top the metric. Running `ibm-granite/granite-4.0-1b` through the generic
`vllm` runner on our stack, it scored a Phase B true-negative rate of **1.0** on a 5-pair
smoke set — 5 of 5 — and in all 5 it called no submission tool at all (inferred from
`n_tool_calls == n_terminal_calls`, since the generic runner records only `content` in the
trace and Granite's assistant turns came back empty). Across both phases that was 9 of 10
entries. The model that does nothing wins Phase B outright.

## Suggested direction

We are not sure what you would prefer here, and the right answer depends on what Phase B is
meant to certify, so this is a suggestion rather than a patch.

The distinction the scorer needs is already available at the point of scoring: `agent_result`
knows whether a submission tool was actually called. Something like recording the terminating
condition explicitly —

```python
# agent.py
"termination": "submitted_files" | "declared_none" | "turns_exhausted"
               | "no_tool_calls" | "api_error",
```

— would let `score_phase_b` credit a true negative only for `declared_none`, and let
everything else be reported separately rather than silently folded into specificity. That also
makes a failed sweep visibly failed: a run whose results are 90% `api_error` would be obvious
at a glance instead of scoring 0.9 specificity.

If you would rather not change the scoring semantics, even surfacing the counts in
`aggregate.json` would help a lot, since a reader could then see how much of a specificity
figure is behaviour.

We are happy to open a PR for whichever shape you prefer, including just the `termination`
field with the scoring left alone. We would rather agree the semantics with you first than
guess.

## Our setup, and what depends on it

We ran `fdtn-ai/antares-1b` and `ibm-granite/granite-4.0-1b` locally on Apple Silicon through
`mlx_lm.server` at bf16, not the reference vLLM deployment, so our absolute scores are not
comparable to your published numbers and we are not presenting them as such.

Separating the claims by how much they depend on that:

- **The scoring path does not depend on it at all.** `agent.py` and `scoring.py` are the same
  code regardless of how the model is served, and the three routes to `submitted_nothing` are
  readable from the source.
- **The 960-row incident does not depend on it either**, beyond our own process-management
  mistake being the trigger. Any transport error reaches the same `break`.
- **The 33 timeouts are stack-specific in frequency.** The 180-second read timeout is the
  runner's, but how often a generation exceeds it depends on tokens/second, and MLX on a
  laptop is slower than a server GPU. On faster hardware this would be rarer. It would not be
  zero: the timeout is a fixed constant and `max_tokens` defaults to 16384.
- **The Antares decomposition is the claim most exposed to our stack**, since a different
  serving setup might yield a different deliberate-abstention rate. But the direction is hard
  to explain away: 0 of 412, and the 16 truncations are attributable to timeouts we can point
  at in the log.

Every run behind these numbers was checked for wholesale infrastructure failure before being
scored, after the 960-row incident taught us to.
