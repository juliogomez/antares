# What Antares-1B actually is

Everything below is read from the published checkpoint's own `config.json` and weight
files, not from marketing material. You can regenerate all of it with:

```bash
python scripts/build_mlx_shim.py --report shim-report.json
```

## The short version

A dense, 40-layer, all-attention transformer with grouped-query attention and a 128k
context window. No state-space layers. No mixture of experts. Roughly 1.6 billion
parameters. It is architecturally a Granite 4.0 1B, because that is exactly what it is: a
post-trained `ibm-granite/granite-4.0-1b`.

| property | value |
|---|---|
| layers | 40, every one of them `attention` |
| hidden size | 2048 |
| attention heads | 16 (head dim 128) |
| key/value heads | 4 → 4:1 grouped-query attention |
| feed-forward | 4096 intermediate, SiLU |
| vocabulary | 100352 |
| context | 131072 tokens |
| position encoding | RoPE, theta 10,000,000 |
| experts | 0 |
| dtype | bfloat16 |
| parameters | 1.837 B as stored, 1.632 B unique |

## `granitemoehybrid` is a misleading name

The checkpoint declares `"model_type": "granitemoehybrid"` and
`"architectures": ["GraniteMoeHybridForCausalLM"]`. Read literally, that says mixture of
experts and a hybrid attention/state-space stack. It is neither. The same `config.json`
also contains:

```json
"layer_types": ["attention", "attention", ... 40 total ...],
"num_local_experts": 0,
"num_experts_per_tok": 0,
"mamba_d_state": 256,
"mamba_n_heads": 128,
"mamba_expand": 2
```

Every layer is attention. Both expert counts are zero. The `mamba_*` fields are inert
defaults: the `GraniteMoeHybrid` class in transformers is a *container* that can express
Mamba layers and MoE layers, and this configuration instantiates neither. The class name
describes the envelope, not the letter.

This matters practically, not just pedantically. If you assume state-space layers you will
reason incorrectly about memory: an SSM has an O(1) recurrent state per layer, whereas this
model has a KV cache that grows linearly with context. At 4 KV heads × 128 head dim × 40
layers × 2 tensors × 2 bytes, that is about 82 KB per token, so a 32k-token agent
conversation costs roughly 2.6 GB of cache — comparable to the weights themselves, and the
reason serving it needs more memory than "it's only a 1B model" suggests. We learned this
the expensive way; see `docs/TROUBLESHOOTING.md`.

## Details worth noticing

**Grouped-query attention, 4:1.** Sixteen query heads share four key/value heads. This is a
memory optimisation, and for an agent that accumulates long tool outputs across fifteen
terminal calls it is the difference between comfortable and impossible.

**RoPE theta of 10 million.** A very large base, which is how the 128k context is reached.
Standard Llama-style models use 10,000. The checkpoint stores this in the transformers 5.x
nested form, `rope_parameters: {"rope_theta": 10000000, "rope_type": "default"}`, which is
the direct cause of the first shim fix.

**muP-style scaling.** The config carries `init_method: "mup"`, `embedding_multiplier: 12`,
`logits_scaling: 8`, `residual_multiplier: 0.22`, and `attention_multiplier: 0.0078125`.
That last value is 1/128, exactly `1/head_dim`, rather than the conventional
`1/sqrt(head_dim)` ≈ 0.0884. These are Granite architecture choices, inherited unchanged.
If you write your own inference code and use textbook attention scaling, you will get
subtly wrong logits and no error message.

**Tied embeddings that are not tied.** `config.json` says `tie_word_embeddings: true`, yet
the checkpoint also ships a materialised `lm_head.weight` of shape [100352, 2048]. We
verified with an element-wise comparison that it is bit-identical to
`model.embed_tokens.weight`, so it is a redundant 205M-parameter copy — which is why the
model reports 1.837 B stored but only 1.632 B unique. It is also why the naive parameter
count overstates the model by 13%.

**No sampling defaults.** `generation_config.json` contains only token ids; there is no
temperature or top-p baked into the checkpoint. Whatever your client sends is what you get.
The benchmark sends temperature 0.3 with `frequency_penalty` 0.3, and that frequency penalty
is not decoration — without it the model falls into repeated-command loops, which you can
watch happen in the traces.

## How it was trained, and why that shows up in behaviour

Cisco's technical report describes supervised fine-tuning on agentic vulnerability-
localisation trajectories, followed by reinforcement learning with GRPO against the task
reward. Two consequences are visible in our traces.

The first is that tool calling became reliable. Antares emits well-formed
`<tool_call>{...}</tool_call>` blocks with valid tool names essentially every turn, where
the base Granite model produces prose and markdown fences describing what it would like to
do. Nothing in the serving stack enforces this: there is no constrained decoding and no
grammar. The model simply learned the format, and the harness parses it with a regex. See
`docs/traces/` for the two side by side.

The second is less flattering. Optimising a reward that pays for correct localisation
teaches a model to localise. It does not obviously teach it to *decline*. Our Phase B
results are the evidence, and they are discussed in `docs/METHODOLOGY.md` and
`docs/RESULTS.md`.

## Relationship to the base model

`ibm-granite/granite-4.0-1b` and `fdtn-ai/antares-1b` are architecturally identical as far
as the configs are concerned: same layer count, same widths, same head counts, same context.
The delta is entirely in the weights and in the behaviour they encode. That is what makes
the comparison in `docs/RESULTS.md` a controlled one: same size, same shape, same family,
same serving stack, same protocol budget — only the post-training differs.
