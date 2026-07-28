# DRAFT — not submitted

Target repo: `cisco-foundation-ai/vulnerability-localization-benchmark`
Proposed title: **`validate_command` splits on quoted pipes, rejecting regex alternations and consuming the terminal-call budget**

Reviewer notes, not part of the issue text:

- Everything below is measured on the pinned ref `000c19c`.
- The score-impact section is filled in from the third arm; see the placeholder.
- Deliberately excluded: the permissive direction of the same first-word check (`awk 'BEGIN{system(...)}'`,
  `find -exec`, `;` chaining). `CONTRIBUTING.md` directs security matters to private reporting under
  `SECURITY.md`, so that belongs in a private report, not a public issue.
- Also excluded, as a separate concern worth its own issue: `agent.py` records any episode that ends
  without an explicit submit — exhausted turns, three consecutive no-tool-call turns, or an API
  exception — as `submitted_nothing=True`, which `score_phase_b` counts as a true negative.

---

## Summary

`validate_command` splits the candidate command on every `|` so that it can check each pipeline stage
against `ALLOWED_CMDS`. The split is not quote-aware, so a pipe inside a quoted argument is treated as
a pipeline separator. Regex alternations are the common case:

```python
>>> from vulnerability_localization_benchmark.sandbox import validate_command
>>> validate_command('rg -n "unsafe|from_raw"')
(False, 'command not allowed: from_raw"')
```

The command is a single invocation of `rg`, which is on the allowlist. It is rejected because the
validator reads `from_raw"` as the program at the second stage of a pipeline.

Commands are executed with `bash -c` (`sandbox.exec_command`), and bash does not treat a quoted pipe as
a pipe. So the validator's parse disagrees with the shell that actually runs the command: it refuses
commands that, had they run, would have been a single allowlisted program.

The practical cost is that each rejection still consumes one of the agent's 15 terminal calls and
returns no information about the repository.

## Reproduction

Against `000c19c`, no container required:

```python
from vulnerability_localization_benchmark.sandbox import validate_command

# Rejected, but each is one allowlisted program:
validate_command('rg -n "unsafe|from_raw"')          # (False, 'command not allowed: from_raw"')
validate_command("rg -n 'malloc|free|realloc'")      # (False, "command not allowed: free")
validate_command('grep -rE "jwt|cert|certutil" .')   # (False, 'command not allowed: cert')
validate_command("awk -F'|' '{print $2}' data.txt")  # (False, "command not allowed: '")
validate_command('find . -maxdepth 2 | grep -E "scanner|test_scanner"')
                                                      # (False, 'command not allowed: test_scanner"')

# Accepted, for contrast — same commands without an alternation:
validate_command('rg -n "unsafe"')                    # (True, 'ok')
validate_command('find . -maxdepth 2 | grep -E "scanner"')  # (True, 'ok')
```

The last pair is the clearest illustration: adding one alternation to an otherwise identical, accepted
command flips it to rejected.

## Measured frequency

We replayed every command emitted by `fdtn-ai/antares-1b` in a Phase A + Phase B sweep through both the
validator at `000c19c` and a quote-aware version, and counted the verdicts that differ. This is a
replay of recorded command strings through a pure function, so it does not depend on how the model was
served.

Over the first 167 of 998 tasks (Phase A and B, 499 pairs; the sweep is still running and we will
update these figures on completion):

| | count | share of commands |
|---|---|---|
| commands emitted | 2289 | — |
| rejected by the validator | 1105 | 48.3% |
| &nbsp;&nbsp;of which: quoted-pipe misparse | 769 | 33.6% |
| &nbsp;&nbsp;of which: genuine allowlist violations | 336 | 14.7% |
| executed | 1184 | 51.7% |

So roughly a third of all commands the model emitted were rejected for this one reason, and it accounts
for about 70% of all rejections. The remaining 336 are the model's own doing and are correctly refused —
mostly `cd /workspace/repo && ls` (`&&` is a denied pattern) and `cd` (not on the allowlist).

Per task, out of a 15-call budget:

- mean 4.6 calls lost to the misparse
- 166 of 167 tasks lose at least one call
- 71 of 167 tasks lose five or more
- 36 of 167 tasks are left with five or fewer calls that returned any output

The genuine allowlist violations are worth mentioning separately: 167 `&&` rejections and 164 `cd`
rejections across 167 tasks is close to one of each per task, which suggests models reliably open with
`cd /workspace/repo && ls` even though the prompt says the repository is at `/workspace/repo/` and
`exec_command` already sets `-w /workspace/repo`. That is a prompt or documentation matter rather than a
bug, and we are happy to file it separately if useful.

## Measured score impact

<!-- FILLED FROM THE THIRD ARM — placeholder until it completes -->

We ran the same 499 pairs twice, changing only the validator, and holding the model, weights, serving
process and sampling configuration fixed:

| metric | `000c19c` | quote-aware | delta |
|---|---|---|---|
| Phase A file-F1 (mean) | _pending_ | _pending_ | _pending_ |
| Phase A hit@any | _pending_ | _pending_ | _pending_ |
| command rejection rate | _pending_ | _pending_ | _pending_ |
| terminal calls returning output | _pending_ | _pending_ | _pending_ |

## How this was measured, and what that does and does not support

We should be explicit about our setup, because it is not yours.

We ran `fdtn-ai/antares-1b` locally on Apple Silicon through `mlx_lm.server` at bf16, using the
`vllm_antares` runner against `/v1/completions`, rather than the reference vLLM deployment. Serving
through MLX also required three local workarounds to the checkpoint's config (flattening
`rope_parameters` to `rope_theta`, untying `lm_head` after checking it is bit-identical to the
embeddings, and disabling the server's own tool parser). Absolute scores from our runs are therefore
not comparable to your published leaderboard numbers, and we are not presenting them as such.

Our serving stack is a possible confound, so to be clear about which claims depend on it:

- **The defect itself does not.** `validate_command` is pure Python with no dependency on how the model
  is served, and the reproduction above is a handful of direct calls.
- **The frequency measurement barely does.** We replay recorded command strings through both versions
  of the function. What the serving stack could influence is *which* commands the model emits, so the
  33.6% figure is specific to this model on this stack; a different stack, sampler or model would give a
  different number. The mechanism, and the fact that alternations are common in code search, are not
  stack-specific.
- **The score impact is the claim most exposed to it.** We controlled what we could: the two arms use
  the same weights, the same config shim, the same server process, the same sampling settings, the same
  pairs in the same order, and differ only by a client-side checkout of this repository. We did not
  control sampling noise: the harness default `temperature: 0.3` is used with no seed, so the two arms
  are not deterministic replays and part of any difference is noise. We report the paired difference
  over 499 pairs for that reason, and would not read much into a small delta.

We have not verified the frequency on vLLM, and it would be reasonable to want that before accepting
the score figures.

## Suggested fix

Split only on pipes that bash would treat as pipes, tracking single quotes, double quotes and
backslash escapes inside double quotes. If a command ends inside an unterminated quote, fall back to
splitting on every pipe as before, so the change can never accept something the current logic refuses.

We have a patch with tests ready to open as a pull request if you would like it. It leaves the
allowlist, `DENIED_PATTERNS` and the requirement that every real pipeline stage be an allowed program
untouched, and it deliberately does not make `DENIED_PATTERNS` quote-aware, so a quoted `||` is still
refused exactly as it is today. The tests assert the newly accepted commands, the cases that must stay
rejected, and the invariant that the new check accepts a superset of what the current one accepts.

## Why we think it is worth fixing rather than documenting

The benchmark measures how well a model localises a vulnerability within a fixed exploration budget. A
model that writes an idiomatic multi-pattern search — arguably the right instinct for this task — is
charged a call and told `command not allowed: from_raw"`, which does not describe anything it did
wrong. Since the rate depends on how often a given model reaches for alternation, it also introduces a
difference between models that has nothing to do with their ability to find vulnerabilities.
