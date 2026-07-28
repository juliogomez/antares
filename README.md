# Antares-1B: a proof of concept you can run on a laptop

Cisco released [Antares-1B](https://huggingface.co/fdtn-ai/antares-1b), an open-weight
1-billion-parameter model post-trained to do one job: given a repository and a description
of a weakness class, work out which files contain the vulnerability, using a read-only
shell and a budget of fifteen commands.

This repository is an attempt to understand that model honestly. It reproduces Cisco's own
evaluation locally on an Apple Silicon Mac, compares Antares against the base model it was
trained from, and writes down what actually happened — including the parts that are less
flattering than the launch material, and the parts where the benchmark itself turns out to
measure something other than what it appears to.

Nothing here needs a GPU, a cloud account, or a paid API key.

## What you get

* **A working local evaluation.** The official [VLoc Bench](https://github.com/cisco-foundation-ai/vulnerability-localization-benchmark)
  harness, fetched at a pinned commit and run unmodified, against 499 real
  vulnerable/patched repository pairs, served by `mlx_lm.server`.
* **A controlled comparison.** Antares-1B against `ibm-granite/granite-4.0-1b`, the base
  instruct model it was post-trained from: same size, same architecture, same protocol.
* **[`minimal_agent.py`](minimal_agent.py)** — the whole agent loop in one readable file,
  which you can point at any directory on your machine. A test asserts it is byte-identical
  to the official protocol.
* **[Annotated traces](docs/traces/)** of the model succeeding, the model failing, and the
  control arm failing differently.
* **A reproducible serving shim.** Antares does not load in mlx-lm as published; three
  things must be fixed. [`scripts/build_mlx_shim.py`](scripts/build_mlx_shim.py) fixes and
  *verifies* each one, and fails loudly rather than guessing.

## What we found

Three things, in increasing order of how much they should change your reading of published
benchmark numbers.

**The post-training is doing real work.** On the same protocol and hardware, Antares
localises vulnerabilities the base Granite model does not. Granite explores a little, loses
the thread, and never commits to an answer.

**Antares does not know when to stop.** Phase B of the benchmark runs the model against the
same repository *after* the security patch has been merged, where the correct answer is to
declare nothing found. Antares almost never does. In the smoke set, for 80% of the pairs
where it submitted in both phases, it submitted the *identical file list* — it is matching
the CWE description to a plausible-looking file rather than reading the patch. See
[docs/METHODOLOGY.md](docs/METHODOLOGY.md).

**Phase B's headline metric does not measure abstention.** The harness scores a Phase B
true negative as "the model submitted nothing", full stop. A model that produces no valid
tool call, rambles, and gets stopped by the loop scores a *perfect* true-negative rate.
Granite scored 1.0 on the smoke set and 100% of that credit came from runs in which it
achieved nothing at all. Any Phase B number read on its own is uninterpretable, so this
repository scores the two phases jointly, per repository.

There is also a fourth finding, about the harness rather than the models: **41% of Antares's
terminal calls were rejected by the sandbox validator**, and two-thirds of those rejections
come from a quoting bug — `validate_command` splits on `|` without respecting quotes, so an
ordinary `rg -n "unsafe|from_raw" src` is parsed as a pipeline and refused. That destroys
roughly a quarter of every model's exploration budget, and depresses every published score.

Full numbers, once the sweep completes, in [docs/RESULTS.md](docs/RESULTS.md).

## Quick start

Requires macOS on Apple Silicon, Python 3.13, Docker, and access to the
[gated model repository](https://huggingface.co/fdtn-ai/antares-1b).

```bash
python -m venv .venv && .venv/bin/pip install mlx-lm huggingface_hub requests pyyaml tqdm

hf auth login                              # accept the gate on the model page first
python scripts/build_mlx_shim.py           # fix + verify the three serving problems
./scripts/serve.sh antares                 # mlx_lm.server on :8080

# watch it work, on any directory you like
python minimal_agent.py --repo ~/code/something --cwe "CWE-22: Path Traversal"
```

To reproduce the evaluation:

```bash
./scripts/fetch_benchmark.sh                                    # pinned upstream harness
docker build -t vulnerability-localization-benchmark-sandbox vendor/vloc-bench
python vendor/vloc-bench/data/downloader_and_verifier.py \
    --output-dir data/ghsa-vulns --workers 12 --resume          # ~14 GB, content-verified

python scripts/run_eval.py --model antares --subset smoke       # 5 pairs, a few minutes
python scripts/analyze.py runs/antares-smoke --markdown
```

The full sweep is 1000 agent episodes per model and takes several hours, so run it detached:

```bash
python scripts/daemon.py --name sweep-antares -- \
    python scripts/run_eval.py --model antares --subset full
python scripts/daemon.py --status sweep-antares
```

Serve one model at a time. Running two `mlx_lm.server` instances on a 24 GB machine put ours
into memory pressure, every request silently blew through the runner's 180-second timeout,
and the harness recorded a thousand well-formed, entirely fictitious results. That failure
mode and its detection are in [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

## Documentation

| | |
|---|---|
| [VALUE.md](docs/VALUE.md) | why a 1B security model is interesting, and what it is not for |
| [INTERNALS.md](docs/INTERNALS.md) | what the checkpoint actually contains, and why `granitemoehybrid` is a misleading name |
| [PROTOCOL.md](docs/PROTOCOL.md) | the official agent protocol, walked through against the source |
| [METHODOLOGY.md](docs/METHODOLOGY.md) | pre-registered metrics, and why Phase B needs decomposing |
| [RESULTS.md](docs/RESULTS.md) | the numbers |
| [traces/](docs/traces/) | annotated real traces, success and failure |
| [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | every wall we hit: TLS interception, Xet 403s, the three shim fixes, the silent timeout |
| [INTEGRATION.md](docs/INTEGRATION.md) | using Antares as a Localizer role in a Foundry-style pipeline |

## Layout

```
minimal_agent.py          the agent loop, standalone and readable
scripts/
  build_mlx_shim.py       make the checkpoint loadable in mlx-lm, verifying each fix
  fetch_benchmark.sh      clone the harness at a pinned commit (never vendored)
  serve.sh                start mlx_lm.server, detached, with a capped prompt cache
  run_eval.py             run the benchmark and record provenance for every run
  analyze.py              recompute scores; decompose Phase B; measure wasted budget
  export_trace.py         scrub a trace of paths and secrets before publishing it
  daemon.py               start long jobs that survive the terminal
configs/                  evaluation configs and the documented smoke subset
tests/                    asserts minimal_agent.py matches the official protocol
docs/                     everything above
```

`vendor/`, `models/`, `data/` and `runs/` are generated and gitignored. Nothing secret,
no tokens, and no CA bundles are committed; see `.gitignore`.

## Licence and attribution

This repository is MIT licensed. It contains no upstream code: the benchmark is fetched at
runtime from
[cisco-foundation-ai/vulnerability-localization-benchmark](https://github.com/cisco-foundation-ai/vulnerability-localization-benchmark)
(Apache-2.0), and the model weights are Cisco's, under their own licence and access gate.
Short excerpts of the harness's prompts and schemas are reproduced in `minimal_agent.py`
and in the documentation for teaching purposes, with attribution.
