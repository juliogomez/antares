# Troubleshooting

## mlx-lm won’t load Antares

Run `python scripts/build_mlx_shim.py`. It fixes `rope_theta`, untied `lm_head`, and `tool_parser_type` for tool calls. Details: [INTERNALS.md](INTERNALS.md).

## `fetch_benchmark.sh` fails on `vllm>=0.19`

vLLM is for Cisco’s GPU stack; this Mac POC uses mlx-lm over HTTP only. The script installs the harness with `--no-deps`. If it still fails:

```bash
pip install -e vendor/vloc-bench --no-deps
pip install "requests>=2.31" "pyyaml>=6.0" "tqdm>=4.66"
```

## Hugging Face / TLS

- **TLS errors on VPN:** set `SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE`. See `scripts/fetch_benchmark.sh`.
- **403 on Xet:** `export HF_HUB_DISABLE_XET=1`
- **401 on model:** accept gate on Hugging Face, then `hf auth login`

## Docker sandbox image

```bash
docker build -t vulnerability-localization-benchmark-sandbox vendor/vloc-bench
```

**Timeout pulling `ubuntu:24.04`:** Docker Hub unreachable (common on VPN). Test with `docker pull ubuntu:24.04`; try off VPN or configure Docker Desktop proxy. Without this image, `minimal_agent.py` and `run_eval.py` cannot run. [GETTING_STARTED.md](GETTING_STARTED.md) step 0 needs no Docker.

Stale containers: `docker ps -aq --filter "name=vulnerability_localization_benchmark_" | xargs docker rm -f`

## Timeouts during a sweep

Symptom: ~180 s failures, zero terminal calls. Scored as abstain; treat as bad data.

- One `mlx_lm.server` at a time: `./scripts/serve.sh antares`
- Long jobs: `python scripts/daemon.py --name … -- …`
- Check health: `python scripts/run_health.py runs/<dir>`
- If server died mid-run: restart server, delete bad JSONs, resume with `--resume` (on by default in `run_eval.py`)

## Other

- **Missing zip `wyfFZf7k`:** one deleted upstream repo; 499 pairs work
- **After Arm 2 harness patch:** restore pin with `./scripts/fetch_benchmark.sh`
- **`aggregate.json` vs `analyze.py`:** use `analyze.py` on per-entry JSON for full stats ([RESULTS.md](RESULTS.md))
- **Stale `runs/logs/*.pid`:** harmless after `daemon.py --stop`

Setup: [GETTING_STARTED.md](GETTING_STARTED.md).
