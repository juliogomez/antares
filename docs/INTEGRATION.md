# Antares as a Localizer role in a Foundry-style pipeline

> **Status: designed and pre-registered, not yet run.** The measurement below requires paid
> frontier-model calls for the Detector arms and none have been made. Only the plumbing has
> been exercised, against a mock LLM backend. This document exists so that the metrics are
> fixed *before* any result exists, which is the only way the comparison can be honest.

## The problem this solves

The [Foundry Security Spec](https://github.com/juliogomez/foundry-poc-demo) organises an
agentic security evaluation into roles with explicit contracts between them: an Orchestrator
drives the pipeline, an Indexer decides what code is in scope, a Detector looks for issues,
a Triager filters them, a Reporter writes them up. The expensive stage is the Detector,
because it is the one that sends code to a frontier model, and its cost scales with the
number of files the Indexer hands it.

In practice, scope is set by hand. Someone writes `include_globs` in `config.yaml` based on
a hunch about where the interesting code lives. That hand-written glob is doing real
security work — it decides what can possibly be found — and it is unreviewed, static, and
unrelated to the weakness class being hunted.

A **Localizer** replaces that hunch with a cheap local model. It sits upstream of the
Indexer, reads the repository with the same read-only shell protocol described in
[PROTOCOL.md](PROTOCOL.md), and proposes a scope conditioned on the CWE actually being
looked for. Nothing leaves the machine, and it costs nothing per token.

```
                        ┌──────────────┐
   CWE + repository ───►│  Localizer   │  Antares-1B, local, free
                        │ (new role)   │  15 shell calls, read-only sandbox
                        └──────┬───────┘
                               │ proposed scope: ranked file list
                        ┌──────▼───────┐
                        │   Indexer    │  existing role: units, batching
                        └──────┬───────┘
                        ┌──────▼───────┐
                        │   Detector   │  existing role: frontier model, paid
                        └──────────────┘
```

## Why this needs measuring rather than asserting

The obvious pitch — "we scanned 15 files instead of 378, so we saved 96% of the cost" — is
not a result. It is half of a result. Narrowing scope always saves tokens; the only question
is what it costs in findings. A Localizer that saves 96% of the tokens and silently drops
the one finding that mattered is worse than no Localizer, because it saves money while
quietly removing the reason the pipeline exists.

So the comparison needs three arms, and the expensive one is not optional.

| arm | scope comes from | why it is needed |
|---|---|---|
| **wide baseline** | every first-party backend file (378 in the reference target) | the ground truth for findings. Without it, "findings retained" has no denominator. |
| **heuristic control** | a dumb, defensible rule — e.g. the *n* largest source files, or a fixed keyword grep for the CWE | separates "Antares is smart" from "any narrowing helps". This is the arm most such comparisons omit, and the omission is usually what makes them look impressive. |
| **Localizer** | Antares-1B, CWE-conditioned | the thing being evaluated |

The heuristic control is the load-bearing one. If Antares does not beat a keyword grep, then
what the pipeline needs is a keyword grep.

## Pre-registered metrics

Fixed before any arm is run.

* **Tokens sent to the Detector**, per arm. Measured, not estimated, from the backend's
  usage accounting.
* **Findings retained** — the fraction of the wide baseline's findings that the narrowed arm
  also produces. This is recall against the baseline, not against absolute truth; the
  baseline itself misses things, and that limit is inherited.
* **Cost per retained finding** — the number that decides whether the role is worth having.
* **Scope precision** — the fraction of files the Localizer proposed that contributed any
  finding. Distinguishes "narrowed well" from "narrowed luckily".
* **Localizer wall-clock** — local inference is not free in time even when free in money,
  and a scoping step that takes longer than the scan it replaces is not a saving.

Reported alongside, not folded in: the number of baseline findings that *no* narrowed arm
retained, since that is the honest cost of the whole approach.

## Reading it against Phase 1

The Phase 1 results in [RESULTS.md](RESULTS.md) already constrain what to expect here, and
the constraint is the useful part of doing Phase 1 first.

Antares is over-eager: on patched code it keeps naming files rather than abstaining. For a
Localizer that is a much more benign failure than it is for a Detector. A scoping stage is
judged on recall, and a stage that suggests a few too many files costs some tokens
downstream, whereas one that abstains incorrectly removes the file from consideration
entirely and the finding is unrecoverable. The failure mode that would sink this design is
the opposite one, and Antares does not have it.

The sandbox validator defect measured in Phase 1 — around a quarter of the exploration
budget rejected because `validate_command` splits on `|` inside quoted patterns — is worth
carrying over as a lesson rather than a bug. Any Localizer implementation should parse
commands with `shlex` before validating them, so that a model asking for
`rg -n "exec|system|popen" src` gets an answer instead of a rejection. Phase 1 suggests
Antares's measured ability is an underestimate for exactly this reason, and a correctly
implemented harness should do better than the benchmark numbers imply.

## Implementation notes

The integration point in the reference pipeline is the Orchestrator's `index_target` step.
The Localizer produces a ranked file list; the Indexer consumes it in place of the
configured `include_globs`. Keeping the contract to "a list of repository-relative paths"
means the role is swappable — the heuristic control arm implements the same interface, which
is what makes the three arms comparable at all.

Two constraints carry over from Phase 1 and should not be relaxed:

* The Localizer runs its shell commands in a container with `--network none` and a read-only
  mount, as in [`minimal_agent.py`](../minimal_agent.py). It is executing text a language
  model wrote, over code from a third party.
* Its output is a *hypothesis*, and must be labelled as such wherever it appears in a report.
  A scope proposal is not a finding, and the moment the two are conflated in a UI, the
  pipeline's credibility depends on a 1B model's confidence.
