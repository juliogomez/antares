# Troubleshooting

Everything here was hit for real while building this repository. Each entry states the
symptom you will actually see, the cause, and the fix.

---

## Serving: mlx-lm cannot load the checkpoint

### 1. `TypeError: ModelArgs.__init__() missing 1 required positional argument: 'rope_theta'`

**Cause.** Antares was serialized with transformers 5.x, which nests RoPE settings:

```json
"rope_parameters": { "rope_theta": 10000000, "rope_type": "default" }
```

mlx-lm 0.31.x still reads the transformers 4.x flat `rope_theta` key, finds nothing, and
fails to construct the model arguments. Nothing is wrong with the checkpoint; the two
libraries disagree about a schema.

**Fix.** Copy the value to the flat key. `scripts/build_mlx_shim.py` does this, and refuses
to proceed if `rope_type` is anything other than `"default"`, because a scaled RoPE would
need a translation the script does not implement rather than a copy.

### 2. `ValueError: Received 1 parameters not in model: lm_head.weight`

**Cause.** The config declares `tie_word_embeddings: true`, so mlx-lm builds no separate
output projection and has nowhere to put the `lm_head.weight` tensor that the checkpoint
nevertheless ships.

**Fix.** Set `tie_word_embeddings: false` so the head is loaded explicitly. Do not just
delete the tensor and hope. The shim script compares `lm_head.weight` against
`model.embed_tokens.weight` element-wise and reports the result: in revision `10417eb3`
they are bit-identical, which is what makes loading the head explicitly provably
equivalent to the tied path. If a future revision ships a genuinely different head, the
script says so loudly, because in that case the published `tie_word_embeddings: true`
would be wrong and the tied path would produce incorrect logits.

### 3. The model appears to make no tool calls at all

**Symptom.** `/v1/completions` returns text with the `<tool_call>` blocks missing, while
`finish_reason` is `"tool_calls"`. The benchmark's Antares runner parses tool calls out of
raw text with a regex, so it sees nothing, nudges the model, and gives up after three
turns. Every entry scores zero and Phase B looks suspiciously perfect.

**Cause.** mlx-lm infers a tool parser from the chat template:

```python
tool_parser_type = tokenizer_config.get("tool_parser_type", _infer_tool_parser(tokenizer.chat_template))
```

For this checkpoint it infers `json_tools`, then intercepts the markup and moves it into a
structured field — helpful for the chat API, fatal for a runner that wants raw text.

**Fix.** Set `"tool_parser_type": null` in `tokenizer_config.json`. Because the lookup is a
`dict.get` with a default, an explicit `null` suppresses the inference. The shim script
verifies both halves: that the unpatched checkpoint *would* have inferred a parser (so the
workaround is still load-bearing) and that the patched one reports
`has_tool_calling == False`.

All three fixes are applied and verified by:

```bash
python scripts/build_mlx_shim.py
```

The script fails loudly rather than guessing if a future mlx-lm or checkpoint revision
breaks an assumption. That is the point of it.

---

## Network: corporate TLS interception

### `SSLError: certificate verify failed: unable to get local issuer certificate`

**Symptom.** `curl` works, Python does not. `huggingface_hub`, `pip`, `requests` and `git`
all fail against hosts that a browser loads happily.

**Cause.** A TLS-inspecting proxy (in our case Cisco Secure Access) terminates the
connection and re-issues certificates from a private root CA. macOS ships that root in the
system keychain, so `curl` and Safari trust it. Python does not use the keychain; it uses
`certifi`'s bundle, which does not contain it.

**Fix.** Build a bundle that contains both, and point Python at it. Never disable
verification — that trades a broken build for a silently insecure one.

```bash
mkdir -p ~/.config/corp-ca
security find-certificate -a -p /System/Library/Keychains/SystemRootCertificates.keychain \
  > ~/.config/corp-ca/system-roots.pem
security find-certificate -a -p /Library/Keychains/System.keychain \
  >> ~/.config/corp-ca/system-roots.pem
cat "$(python -m certifi)" ~/.config/corp-ca/system-roots.pem \
  > ~/.config/corp-ca/ca-bundle.pem

export SSL_CERT_FILE="$HOME/.config/corp-ca/ca-bundle.pem"
export REQUESTS_CA_BUNDLE="$SSL_CERT_FILE"
export GIT_SSL_CAINFO="$SSL_CERT_FILE"
```

The bundle is a local file and is deliberately excluded by `.gitignore`. Do not commit it;
it is environment-specific and says things about your network you may not want published.
`scripts/fetch_benchmark.sh` and `scripts/run_eval.py` pick it up automatically if present.

### Hugging Face downloads fail with `403 Forbidden`

**Cause.** The Hub's Xet content-addressed transfer protocol uses a separate CDN that some
inspecting proxies reject, even when the main API works and you are authenticated.

**Fix.** Fall back to plain HTTP range downloads:

```bash
export HF_HUB_DISABLE_XET=1
```

It is slower and entirely reliable. Our scripts set it by default.

### `fdtn-ai/antares-1b` returns 401 or 403

The repository is gated. Accept the terms on the model page, then `hf auth login`. The
token lands in `~/.cache/huggingface/token` and is picked up automatically. Do not pass
tokens on the command line, where they end up in shell history, and never put one in a file
inside this repository.

---

## Running the sweep

### Every entry takes exactly 180 seconds and reports zero tool calls

**This is the most dangerous failure in the whole project, because it does not look like a
failure.** The runners use `requests(..., timeout=180)`. When a request times out, the
agent loop catches the exception, breaks, and returns a perfectly well-formed result with
`n_terminal_calls: 0` and `submitted_nothing: true`. In Phase A that is recorded as an
abstention. In Phase B it is recorded as a **true negative**. A completely broken serving
setup therefore produces a Phase B score of 1.0.

**Cause, in our case.** We ran two `mlx_lm.server` instances concurrently to evaluate both
models at once. Each one holds a prompt cache that defaults to ten KV cache entries and
grew to 2.4 GB. Two servers, plus Docker Desktop's 8.3 GB VM, on a 24 GB machine, put the
host into memory pressure; throughput collapsed from 15 seconds per entry to over three
minutes, every request blew through the timeout, and the harness recorded 74 clean-looking
but entirely fictitious results.

**Fix.** Serve one model at a time, and cap the cache:

```bash
python -m mlx_lm server --model models/antares-1b-shim --port 8080 \
  --prompt-cache-size 2 --prompt-cache-bytes 1500000000
```

With `workers: 1` only one conversation is ever live, so two cached sequences is plenty.
`scripts/serve.sh` applies these flags.

**Detection.** `scripts/analyze.py` reports the zero-tool-call rate. If it is not close to
what you saw in the smoke set, distrust the run before you distrust the model. We deleted
all 74 poisoned results and re-ran rather than trying to filter them.

### Background jobs die when the terminal goes away

`nohup ... & disown` is not sufficient on macOS: the child stays in the launching process
group and dies with it, and macOS has no `setsid(1)`. Use `scripts/daemon.py`, which
double-forks and calls `os.setsid()`:

```bash
python scripts/daemon.py --name sweep-antares -- python scripts/run_eval.py --model antares --subset full
python scripts/daemon.py --status sweep-antares
python scripts/daemon.py --stop   sweep-antares
```

### `aggregate.json` disagrees with the number of result files

It is not wrong, it is partial. The harness aggregates only entries executed inside the
process that wrote the file, so any run started with `--resume` produces an aggregate
covering just the newly executed entries. Use `scripts/analyze.py`, which recomputes from
the per-entry files.

### One entry always fails with `missing pre_push.zip`

`wyfFZf7k` (GHSA-4999-659w-mq36) is the single dataset entry whose upstream repository was
deleted. The downloader expects a mirrored archive under
`vendor/vloc-bench/data/deleted-repo-mirrors/`, which is not published in the repository.
499 of 500 pairs download and verify; this one cannot. Our results exclude it.

### Docker

`open -a Docker` if the daemon is not running. The sandbox image is built from the
benchmark's own Dockerfile:

```bash
docker build -t vulnerability-localization-benchmark-sandbox vendor/vloc-bench
```

If containers linger after an interrupted run:

```bash
docker ps -aq --filter "name=vulnerability_localization_benchmark_" | xargs -r docker rm -f
```

---

## Reproducibility caveat: sampling is not seeded

The protocol runs at temperature 0.3, but no seed is passed by the harness or by our
wrapper, and `mlx_lm.server` does not reseed per request. Repeated runs on this machine
have been stable in practice, but nothing guarantees bit-identical output across restarts
or library versions, and there is no way to separate model variance from sampling variance
without re-running with explicit seeds. Do not read any spread we report as a confidence
interval.
