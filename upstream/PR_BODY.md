# DRAFT — not submitted, no fork pushed

Target repo: `cisco-foundation-ai/vulnerability-localization-benchmark`
Base: `000c19c` · Branch: `fix/quote-aware-pipeline-split` · Commit: see `0001-quote-aware-pipeline-split.patch`
Proposed title: **Make sandbox pipeline splitting quote-aware**

---

Fixes #<issue number>.

`validate_command` splits the command on every `|` to check each pipeline stage against
`ALLOWED_CMDS`. The split is not quote-aware, so a pipe inside a quoted argument is read as a pipeline
separator and `rg -n "unsafe|from_raw"` is rejected with `command not allowed: from_raw"`, even though
it is a single invocation of an allowlisted program.

Commands are executed with `bash -c`, which does not treat a quoted pipe as a pipe, so the validator's
parse disagrees with the shell that runs the command. Because a rejection still costs the agent one of
its 15 terminal calls, the effect is a budget cost rather than only an inconvenience: replaying the
commands from an Antares-1B sweep, this accounted for about a third of all emitted commands and roughly
70% of rejections. Details and caveats are in the issue.

## What this changes

`_split_pipeline` walks the command once, tracking single quotes, double quotes, and backslash escapes
inside double quotes, and splits only on pipes found outside quotes.

If the command ends inside an unterminated quote, its quoting cannot be trusted, so we fall back to the
previous behaviour of splitting on every pipe. That matters for more than tidiness: treating the
remainder as quoted would newly accept `ls " | rm -rf /`, which the current logic refuses. With the
fallback, **the new check accepts a superset of what the current one accepts and never rejects
something that is accepted today**, which seemed like the right property for a security check to keep.
There is a test asserting exactly that against a reimplementation of the old logic.

## What this deliberately does not change

- `ALLOWED_CMDS` is untouched.
- `DENIED_PATTERNS` is untouched, and is *not* made quote-aware. A quoted `||` is still refused, as it
  is today. Narrowing those patterns is a larger behavioural change and, we think, a separate
  conversation.
- Every real pipeline stage must still be an allowed program. That check is the reason the split exists
  and it still applies; `rg -n "a|b" | rm -rf /` is still rejected, now naming `rm` as the reason
  rather than `b"`.
- Interpreters, command substitution, redirects, `&&`, `||` and trailing `&` are all still refused.

## Tests

`tests/test_validate_command.py` is new, and is the first test file in the repository. It uses the
standard library's `unittest` so it adds no dependency:

```
python -m unittest discover -s tests -v
```

21 tests in four groups:

- **`TestQuotedPipe`** — the commands this fixes: double- and single-quoted alternations, `grep -E`,
  `awk -F'|'`, an escaped quote inside a quoted argument, and a quoted pipe combined with a real pipe.
- **`TestStillRejected`** — interpreters, `$(...)`, backticks, redirects, `&&`, `||`, trailing `&`,
  disallowed programs, a disallowed program as a later pipeline stage, empty input, and the quoted
  `||` case that is intentionally left refused.
- **`TestUnchangedAccepted`** — plain commands and real pipelines.
- **`TestNeverStricterThanBefore`** — the superset invariant over a corpus including malformed input.

All 21 pass on this branch. Run against `000c19c`, 7 fail: the six quoted-pipe cases, plus
`test_quoted_pipe_does_not_hide_a_disallowed_stage`, which passes there for the wrong reason (the
command is rejected, but for `b"` rather than for `rm`). The other 14 pass on both, which is the point:
the cases that encode the security intent are not the ones moving.

Happy to convert to pytest, split the fallback behaviour out, or drop the tests into a different layout
if you would rather structure the first test file yourself.

## How this was validated beyond the unit tests

We replayed every command emitted across 167 Phase A and Phase B tasks — 2289 commands — through both
versions of the validator. 769 changed from rejected to accepted; **none changed from accepted to
rejected**. The two commands that a stricter earlier version of this patch would have newly rejected
were both malformed (`find . | grep -E "\.go$`, an unterminated quote that bash would refuse anyway),
and are what motivated the fallback.

One caveat on our side: we measured with `fdtn-ai/antares-1b` served locally through `mlx_lm.server` on
Apple Silicon rather than the reference vLLM setup, so the frequency figures reflect that stack. The
defect and this fix do not depend on it — `validate_command` is pure Python — but the numbers do, and
we say so in the issue.
