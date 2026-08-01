# Checkpoint and Mac serving

From `fdtn-ai/antares-1b` config and `scripts/build_mlx_shim.py`.

## Architecture

- 40-layer dense transformer (no MoE/Mamba despite `granitemoehybrid` label in config)
- Hidden 2048, 16 query / 4 KV heads, 128k context
- Base: [Granite 4.0 1B](https://huggingface.co/ibm-granite/granite-4.0-1b) + Cisco **SFT then GRPO** on VLoc trajectories (learns multi-turn search policy, not one-shot retrieval). [Technical report](https://cisco-foundation-ai.github.io/antares/technical-report.pdf).

## Tool calling

Emits `<tool_call>{json}</tool_call>` in completion text. No special API mode. Protocol: [PROTOCOL.md](PROTOCOL.md).

## Serving on Mac

`mlx_lm.server` + shim from `build_mlx_shim.py`:

1. Flat `rope_theta` (mlx-lm vs nested transformers config)
2. Untied `lm_head` (config said tied but weights exist)
3. `tool_parser_type: null` (else mlx-lm strips tool calls)

One server at a time on 24 GB RAM. Issues: [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

Runners talk HTTP to `/v1/completions`. Names like `vllm_antares` refer to the API shape, not the vLLM Python package.

Performance on 499 repos: [RESULTS.md](RESULTS.md).
