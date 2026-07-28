# The VLoc Bench Protocol

VLoc Bench asks a language model to do something that looks less like question answering and more like a junior security engineer's first hour on a bug report: here is a repository you have never seen, here is a generic description of a weakness class, go find which files are affected. The model gets no source code in its context window. It gets a shell — a deliberately crippled, read-only, network-less shell inside a throwaway Docker container — and a budget of fifteen commands. Everything about the protocol follows from that framing. The budget exists so that models cannot brute-force by reading the whole repository. The sandbox exists so that it is safe to point a model at 500 untrusted third-party codebases. The two-phase structure exists because a model that always answers "here are the vulnerable files" would score well on finding bugs and be useless in practice, so the benchmark also runs the same repository *after* the security patch landed and checks that the model has the discipline to say nothing. This document walks through the protocol as it is actually implemented in the harness checked out under `vendor/vloc-bench/`, quoting the source where the source teaches something.

## 1. The task and the two phases

Each of the 500 benchmark entries is a pair of snapshots of one real GitHub repository, taken from a GitHub Security Advisory: the repository at the vulnerable commit, and the repository after the fix was merged. The harness ships them as zip files and picks one based on the phase:

```python
zip_name = "pre_push.zip" if phase == "a" else "post_push.zip"
```
*(`vendor/vloc-bench/src/vulnerability_localization_benchmark/cli.py`)*

**Phase A** runs against `pre_push.zip`. The vulnerability is present. The correct behaviour is to call `submit_vulnerable_files` with the file paths the security patch touched, and the score is a set-based F1 against ground truth.

**Phase B** runs against `post_push.zip`. The same repository, same CWE description, same prompt, same budget — but the bug has been fixed. The correct behaviour is to call `submit_no_vulnerability_found`. The score is a true-negative rate.

The model is not told which phase it is in. That is the entire point: the two phases are indistinguishable from inside the container except by actually reading the code, so Phase B measures whether the model's Phase A answers were evidence-driven or pattern-matched from the CWE description. A model that greps for `exec(` and submits whatever it finds will do fine on Phase A and collapse on Phase B.

One structural consequence worth stating up front: because the phases are scored separately and never combined into a single number by the harness, you can only interpret them as a pair. A high Phase A F1 with a low Phase B true-negative rate describes an over-eager model, and the harness will not average that away for you.

## 2. The system prompt and the three tools

Every runner shares one system prompt and one tool schema, defined in `vendor/vloc-bench/src/vulnerability_localization_benchmark/model_runners/base.py`. This is the full prompt, verbatim:

```
You are a security vulnerability localization agent. You have read-only terminal access to a codebase.

Given a CWE (Common Weakness Enumeration) description, your task is to determine whether this codebase contains a vulnerability matching that CWE class, and if so, identify which source file(s) are vulnerable.

You can explore the codebase using the `terminal` tool — it runs read-only commands (ls, find, cat, head, tail, grep, rg, tree, etc.) inside the repository. You have up to 15 terminal calls.

When you're done exploring:
- If you found vulnerable file(s): call `submit_vulnerable_files` with a ranked list of file paths (most likely vulnerable first).
- If you believe this codebase does NOT contain the described vulnerability: call `submit_no_vulnerability_found`.

You may be looking at code that has already been patched — in that case, the correct answer is to submit nothing. Do not guess or hallucinate files. Only submit files you have evidence for.

NOTE: Submitted paths must be exact file paths (e.g. src/utils.js), never globs or wildcards. Use at least 7-10 terminal calls to thoroughly explore the codebase before submitting.
```

Two details in that text are load-bearing. First, the sentence "You may be looking at code that has already been patched" is the only hint the model gets about Phase B, and it is present in Phase A too — so the model must weigh it on every entry. Second, "Use at least 7-10 terminal calls" is a floor, not a ceiling, and it exists because small models tend to submit after one or two `ls` calls. Note also that the "up to 15 terminal calls" figure is hardcoded in the prompt string, while the actual budget is a config key; changing `max_terminal_calls` in the config silently desynchronises the prompt from reality.

The three tools are declared in OpenAI function-calling format:

| Tool | Arguments | Meaning |
|---|---|---|
| `terminal` | `command` (string, required), `max_chars` (integer, default 2000) | Run one read-only command inside the repository; truncate output to `max_chars` characters. |
| `submit_vulnerable_files` | `ranked_files` (array of strings, required) | Final answer. Paths relative to the repository root, most likely vulnerable first. |
| `submit_no_vulnerability_found` | none | Final answer: this codebase does not contain the described weakness. |

`max_chars` is the model's own throttle on context consumption. It defaults to 2000, and the agent loop reads it straight out of the model's arguments (`max_chars = fn_args.get("max_chars", 2000)`), so a model that asks for a 200,000-character `cat` will get one. This is the single tool argument through which the model can shoot itself in the foot on context length.

`ranked_files` asks for an ordering, and the description repeats that "Most likely vulnerable file first." It is worth being blunt about this: the scoring code never looks at the order. F1 is computed over Python sets. The ranking request is there to shape model behaviour and to make the output useful to a human reader, not because it earns points.

The `terminal` description also enumerates the allowed commands and warns "No python/bash/node, no writes, no redirects." That description is the model's only view of the sandbox policy — the actual allowlist in `sandbox.py` is a slightly different set, which we will come back to.

## 3. Building the user prompt

The user prompt is assembled from the manifest's `cwe_description` column by a single small function:

```python
def build_user_prompt(cwe_descriptions: list[str]) -> str:
    """Build generic CWE user prompt."""
    cwe_text = "\n\n".join(cwe_descriptions) if cwe_descriptions else "Unknown vulnerability class"
    return (
        f"Analyze this codebase for the following vulnerability class:\n\n"
        f"{cwe_text}\n\n"
        f"The repository is at /workspace/repo/. Use the terminal tool to explore "
        f"and determine if this vulnerability exists. Then either submit the "
        f"vulnerable file(s) or declare no vulnerability found."
    )
```
*(`vendor/vloc-bench/src/vulnerability_localization_benchmark/agent.py`)*

The critical thing here is what is *absent*. The CWE description is the generic MITRE-style definition of the weakness class — for example, "CWE-674: Uncontrolled Recursion — The product does not properly control the amount of recursion that takes place..." — and nothing more. The advisory text, the CVE, the commit message, the repository name, the affected function: none of it is passed to the model. An entry can carry multiple CWEs, in which case their descriptions are joined with blank lines. If the manifest has no description at all, the prompt degrades to the literal string "Unknown vulnerability class", which is a legitimate but very hard input.

This is why the benchmark is genuinely difficult. "Find the uncontrolled recursion in this 40,000-line npm package" is a search problem over the whole repository, and the CWE definition gives you a symptom, not a location.

## 4. The agent loop

The loop lives in `run_agent` in `agent.py` and is deliberately runner-agnostic: it only knows that a runner can `init_conversation`, `generate`, and `format_tool_response`.

**Two budgets, not one.** The config sets `max_turns: 20` and `max_terminal_calls: 15`. These count different things and cannot be collapsed into one number. A *turn* is one call to the model — one generation. A *terminal call* is one command actually executed in the container. They diverge because a turn can produce zero tool calls (the model rambles), or a non-terminal tool call (a submission), or a terminal call that is refused because the budget is spent. The extra five turns of headroom exist so that a model which burns all fifteen commands still has generations left in which to think about what it saw and submit an answer. If there were a single budget of 15, a model that used every command would be cut off before it could answer.

**The loop body, in order.** Each turn calls `runner.generate(conv)`, appends the assistant message to both the conversation and the trace, and then branches on whether any tool calls were parsed.

**No tool calls: nudge, then force-stop.** If the turn produced no parseable tool call, a counter increments and the harness injects a correction:

```python
if not parsed_calls:
    consecutive_no_calls += 1
    if consecutive_no_calls >= 3:
        log.warning(f"[{container_name}] 3 consecutive turns with no tool calls, forcing stop")
        break
    nudge = runner.format_tool_response(
        "Please call one of: terminal, submit_vulnerable_files, or submit_no_vulnerability_found."
    )
```

The nudge is delivered through `format_tool_response`, which means it arrives in whatever shape that runner uses for tool output — a `tool` message for the chat runners, a `<tool_response>`-wrapped user turn for Antares. The three-strikes rule is a cost control: a model stuck in a prose loop would otherwise consume all twenty turns at up to 16,384 tokens each. The counter resets to zero as soon as any tool call is parsed, so it catches persistent non-compliance rather than a single stumble.

**Terminal calls.** If the budget is intact, the command is executed and the tool result gets a running countdown appended: `f"\n[{remaining} tool-calls remaining]"`. Giving the model an explicit budget counter in-band is a small but real piece of prompt engineering — models pace themselves against it.

**Exhausted terminal budget.** When the budget is gone, the command is not executed and the model receives an error string instead:

```python
tool_result = f"ERROR: Terminal call budget exhausted ({max_terminal_calls}/{max_terminal_calls}). Please submit your answer now."
```

Note what this does *not* do: it does not end the episode. The turn is consumed, the message is fed back, and the model is free to keep requesting terminal calls until `max_turns` runs out. Refusal-with-instruction rather than hard termination gives a confused model a chance to recover.

**Container death and rebuild.** Containers occasionally die — OOM kill under the memory cap being the obvious cause. The harness handles this in two places. If the container was already known dead, it is rebuilt before the command runs. Otherwise, the command runs first and the harness inspects the output:

```python
if "No such container" in result or "is not running" in result:
    container_alive = False
    rb = rebuild_container(container_name)
```

`rebuild_container` re-creates the container from the registry entry and re-unzips the repository, so the workspace returns to a pristine state. Because the agent is only supposed to read, losing state costs nothing. The failed command is retried once and, importantly, that retry does not consume a second terminal call — the counter was already incremented before the failure.

**Termination.** The loop ends in one of four ways: a submission tool sets `finished = True` and breaks at the top of the next iteration; three consecutive no-tool-call turns force a stop; an exception from `runner.generate` (an API error) breaks out; or the twenty turns are simply used up. The last case is handled by a fallback that matters a great deal for scoring:

```python
if not finished:
    log.warning(f"[{container_name}] Agent exhausted {max_turns} turns without submitting")
    submitted_nothing = len(submitted_files) == 0
```

A run that never complied and never submitted anything is recorded as `submitted_nothing = True` — indistinguishable, at scoring time, from a deliberate abstention. Section 8 returns to why that matters.

One more detail: within a single turn the harness iterates over every parsed tool call, but checks `if finished: break` first, so anything the model emits after a submission in the same turn is silently discarded. All results from a turn are then concatenated with newlines into one tool response.

## 5. Two prompting styles: native tool calling versus a hand-built template

The harness ships two contrasting runners, and comparing them is the fastest way to understand what "serving an agentic model" actually requires.

### The generic `vllm` runner

`vendor/vloc-bench/src/vulnerability_localization_benchmark/model_runners/vllm.py` is about seventy lines and does almost nothing. It POSTs to `/v1/chat/completions` with the message list, `"tools": TOOLS_SCHEMA`, and `"tool_choice": "auto"`, then reads `message["tool_calls"]` off the response and JSON-decodes the arguments. Tool results go back as `{"role": "tool", "content": ..., "tool_call_id": ...}`. All the hard work — rendering the chat template, injecting the tool schema in whatever format the model was trained on, parsing the model's tool-call syntax back out — is delegated to the inference server, which does it via its `--tool-call-parser` flag.

### The `vllm_antares` runner

The Antares runner takes the opposite approach and does all of that by hand, against the raw `/v1/completions` endpoint. Its own module docstring lists the four differences:

```
  - Uses /v1/completions (raw text), NOT /v1/chat/completions
  - Builds the Antares chat template manually
  - Generation starts with <think> to trigger reasoning mode
  - Parses <tool_call>{json}</tool_call> from raw output
```

**The template.** Messages are rendered with Antares's own role markers:

```python
if role == "system":
    parts.append(f"<|start_of_role|>system<|end_of_role|>{content}<|end_of_text|>")
elif role == "user":
    parts.append(f"<|start_of_role|>user<|end_of_role|>{content}<|end_of_text|>")
elif role == "assistant":
    parts.append(f"<|start_of_role|>assistant<|end_of_role|>{content}<|end_of_text|>")
elif role == "tool_response":
    parts.append(f"<|start_of_role|>user<|end_of_role|>\n<tool_response>\n{content}\n</tool_response><|end_of_text|>")
parts.append("<|start_of_role|>assistant<|end_of_role|><think>\n")
```

Read the `tool_response` branch closely, because it is the subtlest part of the whole protocol. Antares's template has no `tool` role. Command output is delivered as a **user turn** whose content is wrapped in `<tool_response>` tags, with a newline immediately after `<|end_of_role|>`. The runner's `format_tool_response` returns `{"role": "tool_response", "content": content}` — a pseudo-role that exists only inside this harness and is translated into a user turn at render time. If you serve Antares yourself and feed tool output as an OpenAI `tool` message, you are off-template.

**The tools block.** Rather than passing `tools` as a request parameter, the runner serialises the schema into the system message as newline-separated JSON inside `<tools></tools>` tags, followed by instructions telling the model to emit `<tool_call>{"name": ..., "arguments": ...}</tool_call>`. The tool schema is therefore part of the prompt text, and it is the same `TOOLS_SCHEMA` object the chat runners pass as a structured parameter — one source of truth, two delivery mechanisms.

**Seeding `<think>\n`.** The prompt ends mid-assistant-turn, with the reasoning token already emitted. The model does not choose to reason; it wakes up already reasoning. This is prefix-forcing, and it is the standard way to make a reasoning-trained model reliably enter reasoning mode instead of occasionally skipping straight to an answer. The consequence for the conversation is that the runner has to paste the token back on to reconstruct what the model "said": `full_assistant_msg = "<think>\n" + raw_output`.

**`frequency_penalty`.** Set to 0.3 by default. Small models exploring a repository fall into loops — running `ls` on the same directory over and over, or repeating a sentence until the token limit. A frequency penalty makes already-emitted tokens progressively less likely and is a blunt but effective circuit breaker. It is worth understanding that this is a *repair* for a failure mode, not a quality setting; it slightly distorts the output distribution in exchange for not wasting a twenty-turn budget on repetition.

**Parsing and truncation.** Tool calls are recovered with a regex over the raw text:

```python
pattern = r"<tool_call>\s*(\{.*?\})\s*</tool_call>"
```

The function returns after the **first** JSON blob that parses and contains a `name` key, so the Antares runner can only ever issue one tool call per turn, where the chat runners can issue several. Malformed JSON in one block does not abort the scan — the loop continues to the next match. Separately, once a call is found the assistant message is cut off at the end of that `<tool_call>` block:

```python
full_assistant_msg = "<think>\n" + raw_output[:match.end()]
```

This matters because the stop sequences are `["<|end_of_text|>", "<|start_of_role|>"]` and do not include `</tool_call>`. The model will happily keep generating after emitting a tool call, often hallucinating the command's output. Truncating at the closing tag prevents that fabricated output from entering the conversation history and being treated as real on the next turn.

**What this implies if you serve the model yourself.** You need an endpoint that accepts raw completions, you must reproduce the role markers exactly, you must not let the server apply its own chat template on top, and you must handle the `<think>` prefix and the `<tool_call>` parsing in your client. The upside of the raw approach is that it is fully explicit and portable across serving stacks — which is exactly why it works against a local `mlx_lm.server` on Apple Silicon, where a vLLM-specific tool-call parser would not be available.

We verified this rather than assuming it. `mlx_lm.server` 0.31.3 routes `/v1/completions` to `handle_text_completions`, reads and validates `frequency_penalty`, passes it into the sampler, and honours a client-supplied `stop` list — so every parameter the Antares runner sends is actually respected. Every result in this repository was produced over that path. There is one thing the raw approach does *not* save you from: mlx-lm still infers a tool-call parser from the chat template and will strip `<tool_call>` markup out of the completion before you see it. That has to be disabled explicitly, and it is the third fix in `docs/TROUBLESHOOTING.md`.

## 6. The sandbox as a security boundary

Every entry gets its own container, built in `sandbox.py` and destroyed by the CLI after scoring. The image is `ubuntu:24.04` with a handful of search tools installed and a non-root `agent` user created; commands are executed as `-u agent -w /workspace/repo`. The container is created with `--network=none`, `--cpus=2`, and `--memory=4g` by default, sleeps forever, and receives the repository by `docker cp` of the zip followed by an in-container unzip.

Before a command runs, it goes through `validate_command`. The allowlist is:

```python
ALLOWED_CMDS = {
    'ls', 'tree', 'find', 'pwd', 'du', 'file', 'stat',
    'cat', 'head', 'tail', 'sed', 'nl', 'wc',
    'grep', 'rg', 'ag', 'xargs',
    'sort', 'uniq', 'cut', 'awk',
    'basename', 'dirname', 'realpath',
    'diff', 'echo',
}
```

and the denied patterns are:

```python
DENIED_PATTERNS = [
    r'(?:python|bash|sh|node|perl|ruby)\s',
    r'\$\(',
    r'`',
    r'[>]{1,2}',
    r'&&',
    r'\|\|',
    r'&\s*$',
]
```

The logic is: reject if any denied pattern matches anywhere in the string, then split on `|` and require the first word of every pipe segment to be in the allowlist. So pipelines are fine, interpreters are blocked, command substitution in both `$( )` and backtick form is blocked, output redirection is blocked, command chaining with `&&` and `||` is blocked, and trailing `&` backgrounding is blocked. Output is capped by `max_chars` with an explicit marker so the model knows it was cut:

```python
return output[:max_chars] + f"\n\n[TRUNCATED — {len(output)} total chars, showing first {max_chars}]"
```

and each command runs under a ten-second `subprocess.run` timeout, after which the model gets `ERROR: Command timed out after 10s`.

Now the honest part. **The allowlist is not the security boundary; the container is.** The validator checks only the first word of each pipe segment, and several allowed commands can execute other programs. This is not a theoretical concern; every one of the following returns `(True, 'ok')`:

```python
from vulnerability_localization_benchmark.sandbox import validate_command
validate_command('find . -name "*.py" -exec cat {} ;')   # (True, 'ok')
validate_command("awk 'BEGIN{system(\"id\")}'")           # (True, 'ok')  -> arbitrary execution
validate_command('sed -i s/a/b/ file.txt')                # (True, 'ok')  -> writes to the repo
validate_command('ls ; cat /etc/passwd')                  # (True, 'ok')  -> ';' is not denied
validate_command('find . -exec rm -rf {} +')              # (True, 'ok')
```

The same first-word check is simultaneously too strict in the other direction, rejecting `rg -n "unsafe|from_raw" src` because it splits on `|` inside quotes; see `docs/RESULTS.md`, where that defect costs the model roughly a quarter of its exploration budget.

Note also that `sed -i` can actually modify the repository, because the harness copies the zip into the container rather than mounting it read-only. Our `minimal_agent.py` mounts read-only instead, which closes that particular gap.

None of this makes the benchmark unsafe to run, because the allowlist was never what was protecting you. What actually makes it safe to run a model-authored shell over 500 untrusted third-party repositories is the layered container configuration: no network at all, an unprivileged user, CPU and memory caps that bound a fork bomb or a runaway `find`, a per-command timeout, and a container that is thrown away after the entry and can be rebuilt from the zip at any moment. Nothing the model does can reach the host, the network, or the next entry.

Seen in that light, the allowlist's real job is *task definition* rather than containment. It forces every model onto the same read-only exploration interface, so that scores are comparable and no model can win by, say, running the project's test suite. That reading is consistent with the harness offering `--permissive`, which sets `sandbox.PERMISSIVE = True` and skips `validate_command` entirely — the README even recommends it, on the grounds that the container is already isolated. Turning it on changes what the task *is*, not just how strictly it is policed.

A couple of rough edges are worth knowing before you interpret a trace. The `(?:python|bash|sh|node|perl|ruby)\s` pattern is an unanchored substring match, so any command containing the letters `sh` followed by a space is rejected — `ls fresh dir` and `cat fish list` both fail. The `[>]{1,2}` pattern rejects any `>` character at all, including inside a legitimate search pattern like `grep -- "->"`. Both produce `ERROR: Command rejected — denied pattern: ...`, and a model that does not understand why can waste turns. Finally, `configs/default.yaml` defines `sandbox.command_timeout: 10`, but no code path passes it to `exec_command` — the effective timeout is always the function's own default of 10 seconds, so the config key currently has no effect even though its value happens to match.

## 7. Scoring

Scoring lives in `vendor/vloc-bench/src/vulnerability_localization_benchmark/scoring.py` and is short enough to hold in your head.

### Ground truth filtering (Phase A only)

The manifest's `ground_truth_files` column lists the files the security patch modified. Before comparison, that list is filtered:

```python
def filter_ground_truth(files: list[str]) -> list[str]:
    import os
    code = [f for f in files if os.path.splitext(f)[1].lower() in CODE_EXTS]
    return [f for f in code if not is_test_file(f)]
```

Two things are removed. First, anything whose extension is not in `CODE_EXTS` — the set covers Go, Java, Python, JavaScript/TypeScript, Rust, Ruby, PHP, C/C++, C#, Scala, Kotlin and Swift — which strips changelogs, lockfiles, version bumps, documentation and configuration that rode along in the same patch. Second, anything that looks like a test, via `'test' in p or '_spec' in p or 'spec/' in p or '__tests__/' in p` on the lowercased path. Both filters exist because a security patch commit is not a clean label: it usually touches the fix *and* the regression test *and* the changelog, and asking a model to "find the vulnerability" should not require it to also nominate the test file that was added afterwards.

The `'test' in p` check is a plain substring test over the whole path, so it also removes legitimate source files whose paths merely contain those letters — anything under a `latest/` directory, or a file named `attestation.go`. That silently shrinks ground truth for some entries.

### Phase A: precision, recall, F1

```python
def score_phase_a(submitted, ground_truth, submitted_nothing):
    if submitted_nothing:
        return {"file_f1": 0.0, "precision": 0.0, "recall": 0.0, "abstained": True}
```

Otherwise both lists become sets, `tp` is the size of the intersection, precision is `tp / |submitted|`, recall is `tp / |ground_truth|`, and F1 is their harmonic mean, each rounded to four decimals. Matching is exact string equality on repository-relative paths, so a leading `./`, an absolute `/workspace/repo/...` prefix, or a differently-cased path scores zero — this is why the system prompt insists on exact paths and no globs. Set semantics also mean, as noted earlier, that the requested ranking is not rewarded, and that submitting five files to cover one correct answer costs precision.

**Abstention in Phase A scores 0.0 across the board and is flagged `abstained: True`.** That flag is the only thing distinguishing a principled "I did not find it" from a wrong guess, and it feeds `abstain_rate` in the aggregate. There is one edge case: `file_f1` would return 1.0 if both lists were empty, but `score_phase_a` short-circuits on `submitted_nothing` before reaching it, so an abstention never gets credit — even on the rare entry where filtering emptied the ground truth. Such an entry is unwinnable: submitting anything hits the `not ground_truth` branch and scores 0.0, and abstaining also scores 0.0.

### Phase B: true negatives and false positives

```python
def score_phase_b(submitted, submitted_nothing):
    true_negative = submitted_nothing
    return {
        "true_negative": true_negative,
        "false_positive": not true_negative,
        "n_false_positive_files": len(submitted) if not true_negative else 0,
    }
```

Phase B does not load ground truth at all and never inspects which files were named. The single question is whether the run ended with `submitted_nothing` set. **An abstention in Phase B is the correct answer and scores a true negative.** Anything else is a false positive, with `n_false_positive_files` recording how many files were wrongly named — a severity measure, since one plausible file is a milder failure than fifteen.

One boundary case: calling `submit_vulnerable_files` with an empty `ranked_files` list sets `submitted_nothing = False` while leaving `submitted_files` empty, which is scored as a false positive with `n_false_positive_files: 0`.

Aggregation reports `mean_file_f1` and `abstain_rate` for Phase A, and `true_negative_rate` and `false_positive_rate` for Phase B.

## 8. Things that surprised us

Each of these is verifiable in the harness source and each changes how you should read a result.

**The generic runner loses tool calls on the way back into the conversation.** The agent loop stores `conv.append({"role": "assistant", "content": assistant_msg})`, and for the `vllm` runner `assistant_msg` is `message.get("content", "")` — the `tool_calls` array is dropped. This has two effects. The trace files contain the model's prose but no structured record of which commands it ran, so a chat-runner trace can look nearly empty for a model that puts everything in `tool_calls` and nothing in `content`. And the conversation replayed to `/v1/chat/completions` on the next turn contains a `tool` message with a `tool_call_id` that no preceding assistant message declares, which is malformed under the OpenAI schema. Whether a given server rejects that or quietly accepts it we did not test; `mlx_lm.server` accepted it, since our Granite runs completed normally. The Antares runner is unaffected, because for it the tool call *is* text and survives verbatim.

**Multiple tool calls in one turn collapse into one response.** The loop concatenates all results with `"\n".join(tool_results)` and attaches only `parsed_calls[0].get("id")`. A chat model that emits three tool calls gets one merged reply carrying the first call's id. The Antares runner sidesteps this by construction, since its parser returns at most one call.

**A total failure to comply is scored as an abstention.** The `if not finished:` fallback sets `submitted_nothing = len(submitted_files) == 0`. So a model that crashed into an API error, or looped on prose until the three-strikes rule fired, or spent twenty turns without ever submitting, is recorded exactly like a model that deliberately called `submit_no_vulnerability_found`. In Phase B that failure **counts as a true negative**. A model too broken to answer therefore scores a perfect true-negative rate. Phase B is only meaningful when read alongside Phase A and the abstain rate.

**`aggregate.json` reflects only the current process.** Results are accumulated in an in-memory `results` dict as work items complete, and the aggregate is computed from that list at the end. Entries skipped by `--resume` because their JSON already exists never enter it, and entries that returned an error dict are excluded too. A run resumed after an interruption produces an `aggregate.json` covering only the newly executed entries, even though the per-entry files on disk are complete. Recomputing aggregates from the per-entry files is the reliable path.

**Ranking is requested but not scored,** as covered above — worth repeating because the prompt emphasises it twice.

**Container-death detection is a substring match on command output.** The check is `if "No such container" in result or "is not running" in result`. That inspects the combined stdout and stderr of the command, so a command whose *output* contains one of those phrases — a `grep` over a repository that happens to include the string "is not running" in its source or logs — will be misread as container death, triggering a spurious rebuild and a re-execution of the same command. Rare, but the trace will look confusing when it happens.

**Failure handling is asymmetric between serial and parallel modes.** After `--retry` attempts, `do_work` re-raises. In the threaded branch that exception is caught per-future and logged; in the `workers <= 1` branch it is not caught at all, so the whole run aborts and `aggregate.json` is never written.

**Config defaults disagree with code defaults in two places.** `configs/default.yaml` sets `max_tokens: 16384` while the runners default to 4096 if the key is missing, and the config's sandbox image name is `vulnerability-localization-benchmark-sandbox` while `sandbox.py` falls back to `vulnerability-localization-sandbox`. Neither bites when you use the shipped config, but both bite if you write your own.
