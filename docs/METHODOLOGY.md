# Methodology

This document says what we measured, how, and what the numbers are not allowed to mean.
It was written before the full sweep finished, so that the metrics could not be chosen to
flatter the result.

## The question

Antares-1B is a 1-billion-parameter open-weight model that Cisco post-trained specifically
to localise vulnerabilities in a repository, using shell commands, inside a fixed agent
protocol. Two things are worth establishing before anyone builds on it:

1. **Does the post-training do the work?** A 1B model is small. If a stock instruct model
   of the same size and family scores similarly, the interesting artifact is the harness,
   not the model.
2. **Does it know when to say nothing?** A localiser that always names a file is not a
   security tool, it is a random file picker with good manners. VLoc Bench's Phase B
   exists to test exactly this, and as we show below, its headline metric does not
   actually measure it.

## Setup

| | |
|---|---|
| Benchmark | [VLoc Bench](https://github.com/cisco-foundation-ai/vulnerability-localization-benchmark), Apache-2.0, pinned at commit `000c19cd` |
| Harness code | fetched, never vendored or edited; see `scripts/fetch_benchmark.sh` |
| Dataset | 499 of 500 vulnerable/patched repository pairs, ~14 GB |
| Treatment | `fdtn-ai/antares-1b`, revision `10417eb3`, bf16, unquantized |
| Control | `ibm-granite/granite-4.0-1b`, bf16, unquantized — the base model Antares was trained from |
| Serving | `mlx_lm.server` on Apple Silicon, one model at a time |
| Agent config | copied unchanged from the harness's `configs/default.yaml` |

Every agent parameter — 20 turns, 15 terminal calls, temperature 0.3, `max_tokens` 16384,
`frequency_penalty` 0.3 — is upstream's. We did not tune the agent for our model. Tuning
the protocol and then reporting the score would measure our patience, not the model.

One pair, `wyfFZf7k` (GHSA-4999-659w-mq36), is excluded because its repository has been
deleted from GitHub and the mirror archive that upstream's downloader expects is not
published in the repository. 499 of 500 pairs remain. The exclusion is not related to
model behaviour and applies identically to both arms.

### Why the two arms use different runners

Antares runs through the `vllm_antares` runner: raw `/v1/completions`, hand-built prompt
template, `<think>` seeding, and tool calls parsed out of raw text. Granite runs through
the generic `vllm` runner: `/v1/chat/completions` with native OpenAI tool calling.

This asymmetry is deliberate and it favours the control. Granite ships a chat template with
native tool-calling support, so giving it the API it was built for is its best shot. Forcing
it through Antares's hand-built format would produce a large gap that measured formatting
compatibility rather than security ability. We would rather understate the difference than
manufacture it.

One correction to that reasoning, since it changed our plan. We assumed the
`vllm_antares` template was Antares-specific. It is not: `_build_prompt` emits
`<|start_of_role|>…<|end_of_role|>`, which are **Granite's own special tokens**, inherited
because Antares was post-trained from Granite 4.0. The only things Antares-specific about
that path are the tool-instruction text and the `<think>\n` prefill. So a same-protocol
Granite arm is much less unfair than we assumed — it is Granite's own tokenizer format with
a different system prompt, not a foreign one.

### Arms, and why they run in this order

| Arm | Model | Harness | Purpose |
|---|---|---|---|
| 1 | Antares-1B | pinned `000c19c` | Headline, comparable to published numbers |
| 3 | Antares-1B | `000c19c` + quote-aware validator | What the validator defect costs |
| — | Granite-4.0-1B | pinned `000c19c`, native runner | Control. **Deferred**, see below |

Arm 3 runs immediately after arm 1, against **the same live server process**. The only
difference between the arms is a client-side `git checkout` of the harness: same weights,
same config shim, same server, same sampling settings, same pairs in the same order. No
restart, no reload, nothing that could drift. Arm 3's `provenance.json` records the patched
commit, so the two runs are distinguishable after the fact.

Arm 3 is not a deterministic replay. The harness default `temperature: 0.3` is used with no
seed, so part of any per-task difference is sampling noise. That is why the comparison is
paired over 499 pairs and why we will not read much into a small delta.

### Why the full Granite arm is deferred, not skipped

We had budgeted roughly fourteen hours for a 499-pair Granite sweep. We are not spending it
yet, because the smoke set surfaced a confound that would make the result uninterpretable.

What we can say from the smoke set is narrow but solid. Granite ended 10 of 10 tasks with
`submitted_nothing=True`. In 9 of them `n_tool_calls == n_terminal_calls`, meaning it never
called a submission tool at all — it was stopped by the loop's three-strikes rule after
three turns that contained no tool call. Before going quiet it issued 3–8 **valid** terminal
calls and received real output, and it never came close to the 15-call budget.

So this is **not** a formatting failure in the obvious sense: something in that pipeline was
emitting well-formed, correctly-named tool calls for several turns. What went wrong is
closure — it stopped calling tools and never reached a submission.

But "it stopped calling tools" has two explanations that produce identical scores, and we
cannot yet separate them:

1. Granite genuinely produced nothing useful once the task got harder, or
2. `mlx_lm.server` recognised a tool call in Granite's output, failed to convert it into the
   OpenAI `tool_calls` field, and returned an empty message — in which case the arm measures
   our server rather than the model.

Explanation 2 is live rather than paranoid: we already hit exactly this class of bug with
Antares, where the server's inferred tool parser silently swallowed `<tool_call>` blocks
until we set `tool_parser_type: null` (see `docs/TROUBLESHOOTING.md`). The traces cannot
settle it either, because the native runner records only `content`, and `content` is empty
in every assistant turn of every Granite trace we have.

`scripts/granite_diagnostic.py` settles it cheaply, in minutes rather than hours: it asks
the same server the same question through `chat/completions` with tools, through
`chat/completions` without tools, and through raw `/v1/completions` with Granite's own chat
template rendered locally so nothing server-side can intercept the output. If the raw path
shows tool calls that the chat path dropped, the native-runner arm is measuring our stack
and should not be run as-is; the same-protocol arm becomes the primary control instead. If
the chat path does return tool calls, then Granite's collapse is its own and the full arm is
worth its fourteen hours.

Deciding first and running second costs us half an hour. Running first would have cost
fourteen hours for a number we would then have had to caveat into uselessness. Until the
diagnostic is read, the claim this repository makes about Granite is deliberately limited
to: *under the harness's own generic runner on our serving stack, it never closed.* We do
not yet claim that a 1B base model cannot drive this loop.

## Pre-registered metrics

Fixed after the 5-pair smoke set and before the full sweep. The smoke set was used only to
confirm the plumbing worked and to expose the Phase B measurement problem described below;
no headline number is quoted from it.

### Phase A — localisation on the vulnerable tree

* **mean file F1** — the harness's headline. Set-level precision and recall of submitted
  paths against the patch's ground-truth code files.
* **hit@any** — fraction of entries where at least one submitted file is in the ground
  truth. F1 punishes a model for naming three files when the patch touched one, even if
  one of the three is correct. For a triage tool that hands work to a human, "pointed at
  the right file among a few" is often the operationally relevant question, so we report
  both and let them disagree.
* **exact-set rate** — submitted set equals ground-truth set exactly.
* **abstain rate** — fraction where nothing was submitted.
* **protocol compliance** — fraction of runs that produced at least one valid tool call.
* Breakdowns by ecosystem, severity, and number of ground-truth files.

### Phase B — behaviour on the patched tree

The harness scores Phase B as `true_negative = submitted_nothing`
(`vendor/vloc-bench/src/vulnerability_localization_benchmark/scoring.py`). That is the whole
definition. It has a consequence that matters enormously:

> **A model that never submits anything scores a perfect Phase B true-negative rate of 1.0
> and a Phase A file F1 of 0.0.** Silence, incoherence, and a failure to emit a parseable
> tool call are all credited identically to a considered decision to withhold.

This is not hypothetical. In our smoke set, Granite scored a true-negative rate of **1.0** —
and in 9 of those 10 runs it never called a submission tool at all (`n_tool_calls ==
n_terminal_calls`), having been stopped by the loop's three-strikes rule. It did not abstain;
it failed to close, and the metric applauded.

Nor is the problem confined to weak models. The credit is awarded by
`agent.py`, which sets `submitted_nothing = len(submitted_files) == 0` for **any** episode
that ends without an explicit submission — exhausted turns, three consecutive turns with no
tool call, or an API exception:

```python
    if not finished:
        log.warning(f"[{container_name}] Agent exhausted {max_turns} turns without submitting")
        submitted_nothing = len(submitted_files) == 0
```

An exception is caught with `break`, so a model server that is simply *down* produces a
Phase B specificity of 1.0. We know because it happened to us twice, and the second time it
wrote 960 fabricated results in four minutes (see `docs/TROUBLESHOOTING.md`). This is why
`scripts/run_health.py` exists and why every arm is gated on it: it fingerprints the timing
of zero-tool-call runs — milliseconds means a refused connection, exactly the timeout means
a hung server, neither looks like generation — and refuses to score a run that contains
them. Any Phase B number in this repository has passed that gate.

So we report the harness number for comparability, and then decompose it:

* **true-negative rate (harness)** — reported as-is, for comparison with published figures.
* **deliberate abstention rate** — the model actually called
  `submit_no_vulnerability_found`. Detected from the trace, which records the tool result
  string `Recorded: no vulnerability found.` for both runners.
* **protocol-failure rate** — no submission tool was ever called; the loop gave up.
* **share of true-negative credit that was protocol failure** — how much of the headline
  number is an artifact. For a model with real judgement this should be near zero.

### Paired metrics — the ones that cannot be gamed

Phase A and Phase B are the *same repository* before and after the security patch. Scoring
them separately throws away the only comparison that actually tests discrimination. So for
each pair we ask what the model did on both trees:

* **discrimination rate** — submitted on the vulnerable tree and withheld on the patched
  tree. This is the behaviour the two-phase design is trying to reward, and neither
  degenerate policy can score on it: always-submit gets 0, never-submit gets 0.
* **discrimination-and-correct rate** — as above, but the Phase A submission also had
  F1 > 0. Being right for the right reason.
* **conditional TNR given correct in A** — among repositories where the model genuinely
  localised something, how often did it then withhold on the patched version? This strips
  out the free credit a silent model collects.
* **identical-repeat rate** — among pairs where it submitted in both phases, how often was
  the Phase B answer the *same file list* as Phase A? A high value is direct evidence that
  the model is pattern-matching the CWE description to a plausible filename and never
  reading the patch at all.

### Reference points

Two degenerate policies bound the space, and any honest reading of Phase B has to place a
model against them rather than against 1.0:

| policy | Phase A F1 | Phase B TNR | discrimination |
|---|---|---|---|
| always submit something | > 0 | 0.0 | 0.0 |
| never submit anything | 0.0 | **1.0** | 0.0 |

## Results we threw away, and why

Every number in this repository comes from a run that passed `scripts/run_health.py`. That
gate exists because we twice produced large volumes of plausible, well-formed, entirely
fabricated results, and we would rather publish the failures than the impression of a clean
first attempt.

**74 results, discarded.** Two `mlx_lm.server` instances on a 24 GB machine, each holding a
2.4 GB prompt cache, plus Docker's VM, put the host into memory pressure. Every request blew
through the runner's 180-second timeout, and the loop recorded each timeout as a completed
zero-tool-call episode. Fixed by serving one model at a time with a capped cache.

**960 results, discarded.** Worse, because it needed no memory pressure — only for us to stop
a job. `scripts/daemon.py --stop` signalled the pid it had recorded, which is the
`run_eval.py` wrapper, not the benchmark CLI that wrapper spawns. The CLI was orphaned to
init, invisible to `--status`, and kept running for nearly five hours after we believed it
stopped. It outlived the model server it depended on, so every request failed instantly with
connection refused, and it wrote 960 result files in four minutes — every one of them scored
as a Phase B true negative, because a dead server abstains perfectly. It also wrote 9 results
into the replacement sweep's directory during the four seconds around relaunch, which is how
an otherwise clean run ended up with 5 poisoned pairs.

Fixes: `stop()` now signals the whole process group and verifies with `ps -g`;
`scripts/run_health.py` classifies zero-tool-call results by timing, since a refused
connection returns in milliseconds and a hung server returns at exactly the timeout, and
neither is something token generation can do; and `scripts/arms.sh` gates every arm on it.
The 5 poisoned pairs were re-run on the upstream validator as part of arm 1, so arm 1 is 499
complete pairs with no gaps. Full postmortem in
[TROUBLESHOOTING.md](TROUBLESHOOTING.md#stopping-a-sweep-leaves-an-orphan-that-fabricates-results).

**35 truncated episodes, kept but excluded from behavioural rates.** A partial version of the
same disease, and the one we did not anticipate. In the healthy arm 1 sweep the log contains
33 `API error on turn N: Read timed out (read timeout=180)` entries: a single long generation
exceeded the client's read timeout, `agent.py` caught the exception, broke out of the loop,
and recorded `submitted_nothing=True` — a Phase B true negative — for an episode that was
midway through exploring with budget unspent. `run_health.py` flags these as `truncated`
rather than `ok`, and `analyze.py` excludes them from the denominator of any rate that claims
to describe behaviour, while still reporting the harness's own figure.

We deliberately do **not** re-run them. Re-rolling only the episodes that failed, and keeping
whichever outcome came back, biases the result in a way that is hard to see and impossible to
undo. Excluding them, by the same rule in every arm, is the honest treatment. It costs us 4.2%
of entries and buys a number that means what it says.

## Reproducibility, and where it leaks

* Every run directory contains `effective_config.yaml` and `provenance.json` recording the
  benchmark commit, model revision, package versions, and host platform.
* Results are recomputed from per-entry files by `scripts/analyze.py`, not read from the
  harness's `aggregate.json`. That file is built only from entries executed in the process
  that wrote it, so any resumed run leaves a silently partial aggregate behind.
* **Sampling is not seeded per request.** The protocol runs at temperature 0.3, but neither
  the harness nor our wrapper passes a seed, and `mlx_lm.server` does not reseed per
  request. Runs are therefore reproducible in practice on this machine but are not
  guaranteed bit-identical across restarts or mlx-lm versions, and we cannot decompose the
  results into model variance versus sampling variance without re-running with explicit
  seeds. No variance interval we report should be read as a confidence interval over
  sampling noise.
* Serving differs from the reference implementation. Upstream evaluates on vLLM with CUDA;
  we use mlx-lm on Metal, with a shim documented in `docs/TROUBLESHOOTING.md`. The shim's
  one substantive change — loading `lm_head` explicitly rather than tied — is verified
  bit-identical to the tied path by `scripts/build_mlx_shim.py`, so it cannot alter logits.
  Numerical differences between Metal bf16 and CUDA bf16 kernels remain possible and we
  make no claim of exact parity with published figures.

## Known threats to validity

1. **Single run per entry.** No repeats, so per-entry outcomes carry sampling noise. Group
   differences of a few points should not be treated as meaningful.
2. **Contamination is plausible and unquantified.** These are public CVEs in public
   repositories. Both models may have seen the patches during pretraining. This inflates
   both arms, but not necessarily equally.
3. **Ground truth is the patch, not the vulnerability.** `filter_ground_truth` keeps only
   non-test code files touched by the security patch. A patch that also refactors will
   include files a perfect localiser would not name, and a vulnerability whose fix landed
   in a test helper disappears from the ground truth entirely.
4. **Phase B assumes the patch worked.** An incomplete fix makes a "false positive" the
   correct answer, and we have no way to detect that case.
5. **The 180-second client timeout is a silent failure mode.** The runner's `requests`
   timeout is 180 s, and a timeout is recorded as a completed run with zero tool calls —
   which then counts as an abstention in Phase A and a *true negative* in Phase B. Our
   first sweep attempt hit this on every entry because two model servers exhausted the
   machine's memory. We discarded those results entirely and re-ran. `scripts/analyze.py`
   reports the zero-tool-call rate so this can never hide again; if you see it spike,
   suspect your serving setup before you conclude anything about the model.
