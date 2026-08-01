# What Antares is for

Public vulnerability feeds (NVD, OSV, GHSA) tell you *what* broke. Antares helps with *where* to look: given a repo and a CWE class, it runs an **iterative search** over the tree and returns a ranked file shortlist—all locally, without sending code to a cloud API.

That shortlist speeds triage; it is not proof of a bug. A human, test, or SAST still confirms.

## How it works (vs one-shot search)

Antares is trained (SFT + GRPO on VLoc trajectories) to **search like an investigator**, not to answer in one shot:

1. Start from the CWE description.
2. Issue read-only shell commands (`find`, `rg`, `grep`, …) over many turns (up to 15).
3. Read results, **change strategy** when a path fails (sandbox rejections, empty hits, wrong directory).
4. Narrow toward files that plausibly match the weakness class.
5. Submit a ranked file list—or, in principle, declare no match (`submit_no_vulnerability_found`).

The tools look mundane; the value is the **multi-turn policy**: when to broaden, when to refine, when to backtrack. Step 0 in [GETTING_STARTED.md](GETTING_STARTED.md) shows a real run: wasted turn, rejected regex, recovery, correct submit.

Reasoning appears in each turn’s thinking block (see trace JSON or `minimal_agent.py --show-thinking`). VLoc Bench scores **file paths only**, not that narrative—but reviewers can use it.

## Input and output

**Input:** repo path + **CWE description** (generic class text).

Antares does not take CVE/GHSA IDs or raw advisory prose as the harness prompt. Typical workflow: read the advisory → identify the CWE → pass the CWE description (and optional context you write) to the model.

| Input | Output |
|---|---|
| Repo + CWE description | Ranked file paths; reasoning in thinking blocks / traces |

**What the benchmark grades:** `submit_vulnerable_files` with ranked paths.  
**What Cisco’s demos also show:** path + natural-language chain (flow, lines, fix hints). That richer output is for human review; this repo surfaces it via traces and `--show-thinking`, not via F1.

## When it helps

- **Advisory-driven triage:** “GHSA mentions CWE-79 in this service—which files first?” (after you map to CWE text)
- **Shift-left / CI smoke:** small `--subset` harness runs as a pipeline check ([GETTING_STARTED.md](GETTING_STARTED.md))
- **Air-gapped or laptop review:** weights and repo stay on your machine ([INTERNALS.md](INTERNALS.md))
- **Scope reduction:** shortlist before a heavier indexer, detector, or larger model ([Foundry localizer](#planned-foundry-localizer) below)

Cisco positions the **Antares family** (350M, 1B, 3B) for different hardware tiers. **This repo runs Antares-1B only** on Apple Silicon via mlx-lm.

## What we measured (499-repo Phase A)

Honest numbers from stored sweeps in [RESULTS.md](RESULTS.md):

| Metric | Our run | How to read it |
|---|---|---|
| **hit@any ~33%** | At least one ground-truth file appears in the submitted list on ~1/3 of repos | **File localization**, useful for triage shortlists even when the list is noisy |
| **Mean file F1 ~0.18** | Low when the list adds wrong files or omits vulnerable files | Penalizes over-broad or incomplete lists |
| **Abstain ~5%** | Episode ended without submitting | Model usually commits to some list |
| **~13–14 terminal calls** | Uses most of the search budget | Consistent with iterative retrieval, not one grep |

[Cisco reports 0.209 mean file F1](https://huggingface.co/fdtn-ai/antares-1b) on the same benchmark under **vLLM on GPU**, averaged runs, and without our harness validator bug. We report **0.168–0.176** on Mac (Arm 1 vs quote-aware Arm 2). Same weights; not leaderboard-identical conditions—see [RESULTS.md](RESULTS.md#vs-ciscos-0209).

## Limits

- **Localization, not detection:** file names vs patch ground truth—not line-accurate proof, dataflow, or exploitability.
- **Noisy lists:** hit@any > F1 means the right file often arrives with wrong ones.
- **Patched / unknown trees:** on fixed code the model still often submits files; do not treat a weak list as “clean.” Our sweep rarely calls `submit_no_vulnerability_found` ([PROTOCOL.md](PROTOCOL.md#phase-b-benchmark-only)).
- **Harness friction:** ~35% of commands wrongly rejected when regex contains `|` inside quotes (Arm 1); hurts scores and exploration.
- **Not a CVE scanner:** needs a CWE-scoped question; won’t sweep all weakness classes alone.

Treat every path as a hypothesis.

## What Cisco ships that this repo does not cover

This POC reproduces the **VLoc agent + benchmark** path, not the full product surface:

| Cisco / model card | In this repo |
|---|---|
| Official **CLI**, SARIF, container workflows | `minimal_agent.py`, `run_eval.py` only |
| **350M / 3B** variants | **1B** only |
| Advisory text as native prompt | **CWE description** (you map advisories) |
| Frontier model comparisons (Gemini, GPT, …) | Antares vs **Granite** control only |
| H100 / vLLM reference serving | **mlx-lm** on Mac |

### Why not Cisco’s official CLI for benchmarking?

Cisco ships an **Antares CLI** (ZIP on Hugging Face) for product use: `antares query`, SARIF/JSON reports, repo sweeps, CI hooks. We use it for integration experiments ([`explore/NOTES.md`](../explore/NOTES.md)); **this repo does not replace the harness with it** because:

- **No VLoc scorer** — the CLI returns findings for human or pipeline review; it does not run the 499-repo manifest, ground-truth patch files, or **file F1 / hit@any**. Those metrics come only from [`run_eval.py`](../scripts/run_eval.py) → `vulnerability_localization_benchmark.cli`.
- **Not the same eval contract** — CLI takes `--cwe CWE-119` and built-in CWE text; the benchmark uses manifest **CWE descriptions**, Docker sandbox rules, and harness agent defaults (e.g. 16384 max tokens). Scores would not be comparable to stored [`runs/`](../runs/) or Cisco’s published **0.209** without a full re-baseline.
- **This repo’s job is reproduction** — pinned harness commit, provenance, validator A/B arms, and protocol fidelity ([`minimal_agent.py`](../minimal_agent.py), [`test_protocol_fidelity.py`](../tests/test_protocol_fidelity.py)) are tied to **VLoc Bench**, not the product CLI.

Use the **CLI** for Foundry-style local scans and SARIF; use **`run_eval.py`** when the question is benchmark performance.

Links: [model card](https://huggingface.co/fdtn-ai/antares-1b), [technical report](https://cisco-foundation-ai.github.io/antares/technical-report.pdf), [Issues](../upstream/README.md) we filed on the harness.

## What it is not

- Replacement for human review, SAST, or DAST
- Proof that listed files are vulnerable
- A guarantee of privacy beyond your own deployment (you control where weights and code run)

## Planned: Foundry Localizer

**Not implemented.** In [foundry-poc-demo](https://github.com/juliogomez/foundry-poc-demo), scan scope is hand-written (`include_globs`). A **Localizer** would run first:

    CWE + repo → Antares file shortlist → Indexer / Detector / Triager on that smaller scope.
