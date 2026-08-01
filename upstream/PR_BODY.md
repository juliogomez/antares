# Submitted as PR #3

**Live:** https://github.com/cisco-foundation-ai/vulnerability-localization-benchmark/pull/3  
**Fixes:** https://github.com/cisco-foundation-ai/vulnerability-localization-benchmark/issues/4

## Change

Quote-aware split on `|` in `validate_command`. Unterminated quoting falls back to the old split so behaviour is never stricter than today.

## Unchanged

- `ALLOWED_CMDS`, `DENIED_PATTERNS`
- Real pipelines still require each stage to be allowlisted
- Quoted `||` still rejected

## Tests

New `tests/test_validate_command.py`, 21 cases:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

## Measurement (Antares full sweep, 499 pairs, mlx-lm on Mac)

Replay on Arm 1 traces:

- 13,653 commands emitted
- 4,744 flipped rejected→accepted (35% of commands; 69% of rejections)
- 0 newly rejected by fix
- 977 / 998 tasks lost ≥1 call to misparse

Score impact Arm 1 vs Arm 2 (same model):

| metric | official | quote-aware | delta |
|---|---|---|---|
| Phase A mean F1 | 0.168 | 0.176 | +0.008 |
| hit@any | 0.317 | 0.329 | +0.012 |
| Phase B harness TNR | 0.048 | 0.046 | −0.002 |
| identical repeat (paired) | 0.397 | 0.262 | −0.135 |

Frequency depends on which commands the model emits; score delta depends on both. Defect and fix are pure Python.
