# Antares-1B on a laptop

Local run of [Cisco Antares-1B](https://huggingface.co/fdtn-ai/antares-1b) on [VLoc Bench](https://github.com/cisco-foundation-ai/vulnerability-localization-benchmark). Antares performs **iterative vulnerability retrieval**: multi-turn read-only search over a repo from a CWE description, then a ranked file shortlist—on your machine, no cloud API.

Use the shortlist to decide where to review first; confirm with humans, tests, or SAST.

## Headline results

On 499 benchmark repos, **Phase A**—each snapshot is **known to contain the bug**. The score is **which file to inspect**, not whether the repo is vulnerable.

**hit@any -> 33%** — Did Antares name at least one of the right files? On about **one repo in three**, its submitted list includes **at least one** file the benchmark marks as vulnerable. Extra wrong files in the list still count as a hit.

**Mean file F1 -> ~0.18** — How accurate is the whole list? Stays low because Antares often submits a list with additional non-vulnerable files on top of the vulnerable ones. A long list with one correct file scores well on hit@any but poorly here. Same thing happens if Antares misses vulnerable files and does not submit the complete list.

Interpretation and limits: [VALUE.md](docs/VALUE.md). Full tables, validator replay, and Cisco comparison: [RESULTS.md](docs/RESULTS.md).

## Docs

| If you want to… | Read |
|---|---|
| Try or run Antares | [GETTING_STARTED.md](docs/GETTING_STARTED.md) |
| Fit, capabilities, and limits | [VALUE.md](docs/VALUE.md) |
| Understand scores and metrics | [PROTOCOL.md](docs/PROTOCOL.md) then [RESULTS.md](docs/RESULTS.md) |
| Fix setup | [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md), [INTERNALS.md](docs/INTERNALS.md) |
| Upstream benchmark issues we filed | [Issues](upstream/README.md) |

## What’s in the repo

- `minimal_agent.py`: readable agent loop; `--show-thinking` prints the model’s search narrative
- `scripts/run_eval.py`: official benchmark wrapper (not Cisco’s product CLI—see [VALUE.md](docs/VALUE.md#why-not-ciscos-official-cli-for-benchmarking))
- `runs/antares-full` (Arm 1), `runs/antares-fixed` (Arm 2): stored 499-repo sweeps
- `docs/traces/`: saved Phase A trace JSON for `tokio-rs/slab` (walkthrough in GETTING_STARTED step 0)
- `configs/*_ids.txt`: benchmark subsets (`demo`, `lab`, `smoke`)

## Note on Cisco’s published 0.209 F1

Same benchmark and weights; different stack. Cisco: vLLM on GPU, averaged runs. Here: mlx-lm on Mac, one sweep, plus a harness validator bug that rejected ~35% of regex commands ([PR #3](https://github.com/cisco-foundation-ai/vulnerability-localization-benchmark/pull/3)). Arm 2 (fixed validator) reaches **0.176** F1. Details: [RESULTS.md](docs/RESULTS.md#vs-ciscos-0209).

## Licence

MIT (this repo). Benchmark Apache-2.0 (fetched at runtime). Weights Cisco, gated on Hugging Face.
