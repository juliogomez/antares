# Issue #4 (submitted upstream)

**Repo:** `cisco-foundation-ai/vulnerability-localization-benchmark`  
**Live:** https://github.com/cisco-foundation-ai/vulnerability-localization-benchmark/issues/4  
**PR:** https://github.com/cisco-foundation-ai/vulnerability-localization-benchmark/pull/3

## Summary

`validate_command` splits on `|` to validate each pipeline stage. It does not respect quotes. So `rg -n "unsafe|from_raw"` is treated as two commands and rejected, even though `rg` is allowed and bash would run one command.

Rejections still use one of the agent’s 15 terminal calls and return no repo output.

## Reproduce (no Docker)

```python
from vulnerability_localization_benchmark.sandbox import validate_command
validate_command('rg -n "unsafe|from_raw"')  # False, 'command not allowed: from_raw"'
validate_command('rg -n "unsafe"')             # True
```

## How often (replay of emitted commands, Arm 1 sweep)

| | count |
|---|---|
| commands replayed | 13,653 |
| rejected (old validator) | 6,875 (~50%) |
| misparse due to quoted `\|` | 4,744 (~35% of all commands) |
| real allowlist violations (`&&`, `cd`, …) | 2,131 |

About **4.8 calls lost per task** on average from the misparse alone (977/998 tasks affected).

## Score impact (Arm 1 vs Arm 2, same 499 pairs)

Same model weights, same traces except validator; Arm 2 used patched harness checkout.

| metric | official | quote-aware | delta |
|---|---|---|---|
| Phase A mean F1 | 0.168 | 0.176 | +0.008 |
| hit@any | 0.317 | 0.329 | +0.012 |
| Phase B harness TNR | 0.048 | 0.046 | −0.002 |
| identical repeat (paired) | 0.397 | 0.262 | −0.135 |

Measured with Antares-1B on mlx-lm (Mac), not vLLM. The bug is in Python; the frequency depends on which commands the model writes; score delta depends on both.

## Suggested fix

Split on `|` only outside quotes; if quoting is broken, fall back to today’s naive split so you never accept *fewer* commands than now. Patch + tests ready in our fork export (`upstream/0001-...patch`). `DENIED_PATTERNS` unchanged.

## Why fix it

Models searching for vulnerability patterns naturally use regex alternation. Penalising that under the exploration budget measures the validator, not localisation skill.
