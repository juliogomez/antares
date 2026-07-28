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
manufacture it. The same-protocol comparison is reported separately as a secondary result,
where it answers a different and narrower question: how much of Antares's advantage is
protocol adherence as opposed to judgement.

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
and every single one of those true negatives was a run in which it never emitted a valid
submission at all and was stopped by the loop's three-strikes rule. It did not abstain; it
failed, and the metric applauded.

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
