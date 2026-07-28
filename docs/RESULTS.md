# Results

> **Interim. The full sweep is still running.** The numbers below come from the first ~33
> completed pairs of the 499-pair sweep and from the 5-pair smoke set. They are enough to
> establish the qualitative findings, which have been stable from the very first probe, but
> the point estimates will move. Regenerate at any time with:
>
> ```bash
> python scripts/analyze.py runs/antares-full runs/granite-full --markdown
> ```
>
> Metrics were fixed before the sweep; see [METHODOLOGY.md](METHODOLOGY.md).

## Headline

Three findings, none of which needs the final digits to be settled.

1. **The post-training works.** Antares localises vulnerabilities; the base Granite model of
   the same size and architecture does not reach an answer at all.
2. **Antares essentially never abstains.** Across 32 completed Phase B runs it has called
   `submit_no_vulnerability_found` exactly **zero** times.
3. **Phase B's headline metric cannot detect this**, because it credits silence and failure
   identically. Both models score non-zero true-negative rates made up entirely of runs that
   achieved nothing.

## Phase A — localisation on the vulnerable tree

| run | n | mean file F1 | hit@any | exact set | abstain rate | mean terminal calls |
|---|---|---|---|---|---|---|
| antares, full sweep (partial) | 33 | 0.233 | 0.455 | 0.061 | 0.182 | 11.6 |
| antares, smoke set | 5 | 0.806 | 1.000 | 0.600 | 0.000 | 13.8 |
| granite, smoke set | 5 | 0.000 | 0.000 | 0.000 | 1.000 | 4.6 |

The smoke set is five deliberately tiny repositories and is *not* a measurement — it is
included to show how much subset choice matters. The same model scores 0.81 on five small
repositories and 0.23 on a random slice of the real distribution. Treat any headline F1
quoted without its sample definition with suspicion, including ours.

`hit@any` is roughly twice `mean file F1`, which tells you something specific: Antares often
identifies a correct file while also naming others, so F1 penalises it for imprecision
rather than for looking in the wrong place. For a triage funnel that hands candidates to a
more expensive stage, `hit@any` is the more decision-relevant number, and it is the reason
[INTEGRATION.md](INTEGRATION.md) treats the Localizer role as viable despite a modest F1.

Granite, given the chat API with native tool calling that it was built for, explores about
four commands deep, stops producing tool calls, and is terminated by the loop's
three-strikes rule without ever submitting. It is not that it cannot call tools — it can, and
does. It does not persist, and it does not commit to an answer. That gap is what the
post-training bought.

## Phase B — behaviour on the patched tree

| run | n | TNR (harness) | deliberate abstention | protocol failure | share of TN credit that was failure |
|---|---|---|---|---|---|
| antares, full sweep (partial) | 32 | 0.125 | **0.000** | 0.125 | 1.00 |
| antares, smoke set | 5 | 0.000 | 0.000 | 0.000 | — |
| granite, smoke set | 5 | **1.000** | **0.000** | 1.000 | 1.00 |

Read the third and sixth columns together. Granite's *perfect* true-negative rate is
composed entirely of runs in which it never submitted anything because it had stopped
functioning. Antares's smaller true-negative rate is likewise entirely protocol failure. In
neither case did either model ever deliberately decide that the patched code was clean.

The harness's definition is `true_negative = submitted_nothing`
(`vendor/vloc-bench/.../scoring.py`). Under that definition a model that emits no tokens at
all scores 1.0. This is not a criticism of the benchmark's intent — Phase B is asking exactly
the right question — but the metric as implemented answers a different one, and a Phase B
number quoted on its own tells you nothing about judgement.

## Paired — did it tell the two trees apart?

| run | pairs | discrimination | discrimination & correct | conditional TNR given correct in A | identical repeat submissions |
|---|---|---|---|---|---|
| antares, full sweep (partial) | 32 | 0.000 | 0.000 | 0.000 | 0.308 |
| antares, smoke set | 5 | 0.000 | 0.000 | 0.000 | 0.800 |
| granite, smoke set | 5 | 0.000 | 0.000 | — | — |

**Discrimination** is the fraction of repositories where the model submitted on the
vulnerable tree and withheld on the patched one — the behaviour the two-phase design exists
to reward. Antares scores zero. So does Granite, for the opposite reason: one always
submits, the other never does, and neither distinguishes.

The last column is the most direct evidence. Among pairs where Antares submitted in both
phases, it named the *identical file list* both times in 31% of cases in the sweep so far and
80% in the smoke set. The security patch had been applied in between. It made no difference
to the answer.

The mechanism is visible in the [annotated traces](traces/): on `tokio-rs/slab`, Antares runs
a near-identical command sequence on both trees, converges on the file containing the
`unsafe` blocks, and submits it twice. It is matching the CWE description to a plausible
file, not reading the patch.

## A finding about the harness, not the models

| | Antares, full sweep (partial) |
|---|---|
| tool responses observed | 644 |
| rejected by the sandbox validator | 266 (**41.3%**) |
| — token inside a quoted regex pattern | 178 |
| — `&&` | 44 |
| — `cd` | 44 |

Two-thirds of the rejections are a quoting defect. `validate_command` splits a command on
`|` to find pipeline stages without respecting quotes, so a perfectly ordinary alternation
is parsed as a chain of commands:

```python
from vulnerability_localization_benchmark.sandbox import validate_command
validate_command('rg -n "unsafe|from_raw" src')
# (False, 'command not allowed: from_raw')
```

Regex alternation is the most natural way to search for any of a set of dangerous patterns,
which is the entire task. The effect is that roughly a quarter of every model's fifteen-call
exploration budget is destroyed before it can be used, and every published VLoc Bench score
is depressed by it. Fixing it means parsing with `shlex` before validating.

The remaining rejections are a model habit rather than a harness defect: Antares opens almost
every episode with `cd /workspace/repo && ls`, which fails twice — once for `&&`, once for
`cd` — even though the sandbox already executes with the repository as the working directory.
That is about 3 of 15 calls lost to a reflex that was never needed.

## What is still missing

* The full 499-pair sweep for both models, both phases. Running; roughly four hours for
  Antares, then Granite.
* The **same-protocol control arm**: Granite driven through Antares's raw-completions
  template rather than the chat API. This separates "cannot follow the protocol" from
  "cannot do the task". Earlier probing showed Granite emits prose and markdown fences rather
  than `<tool_call>` markup when forced through that path, but that observation predates this
  repository's runs and no trace of it is committed here, so it is stated as an expectation
  rather than a result.
* Repeats. Every entry has been run once, unseeded, so per-entry outcomes carry sampling
  noise. See the reproducibility section of [METHODOLOGY.md](METHODOLOGY.md).
