#!/usr/bin/env python3
"""The whole Antares agent loop in one readable file.

Point it at any local directory and a CWE, and watch a 1B-parameter model explore the
tree with shell commands and then name the files it thinks are vulnerable:

    python scripts/build_mlx_shim.py
    scripts/serve.sh antares
    python minimal_agent.py --repo ~/some/project --cwe "CWE-22: Path Traversal"

This is a readable local runner, not the official evaluation. The numbers in docs/RESULTS.md come
from Cisco's own harness, run unmodified. What this file gives you is the thing the
harness hides behind abstraction layers: the exact bytes on the wire, the exact prompt
template, and the exact loop. Everything protocol-relevant is faithful to
vendor/vloc-bench/src/vulnerability_localization_benchmark/, which is the source of
truth; where this file simplifies, it says so in a comment.

Three things are worth watching when you run it:

  * The model is *told* how to call tools in the system prompt, and it emits its calls as
    <tool_call>{...}</tool_call> text that we parse with a regex. There is no special
    decoding path and no grammar constraint. Tool calling here is a learned text habit.

  * The assistant turn is seeded with a literal "<think>\\n". Antares was trained to
    reason first, and the runner forces it into that mode by pre-filling the opening tag.

  * The model has two separate budgets: turns and terminal calls. It has to decide when
    it has seen enough, which is the part a 1B model finds hardest.

  * It will look like smart ripgrep (rg/grep/find only, not SAST). See docs/GETTING_STARTED.md
    for why that is, and when rg alone is enough.

SAFETY. This runs shell commands that a language model wrote, over code you may not
trust, so it does what the benchmark does and then some:
  - commands run inside a Docker container, never on your host;
  - the container gets --network none, so nothing it runs can reach the network;
  - your directory is mounted READ-ONLY, so nothing can modify the code under review;
  - commands run as a non-root user, under CPU, memory and wall-clock limits;
  - every command is checked against an allowlist of read-only tools, and rejected if it
    contains shell metacharacters that could chain, substitute or redirect.
The allowlist is the weakest of those and is not what protects you. It checks only the
first word of each pipe segment, so `awk 'BEGIN{system("id")}'`, `find -exec`, and `;`
chaining all pass it (see docs/PROTOCOL.md). The container is the boundary: no network, no
write access, no privileges, thrown away afterwards. Keep it that way, and do not add a
`--permissive` style escape hatch here. If you want one, use the harness.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import textwrap
import time
import uuid
from pathlib import Path

import requests

# ----------------------------------------------------------------------------------
# 1. The protocol. Copied verbatim from the official harness so that what you see here
#    is what the model was evaluated against.
#    Source: vendor/vloc-bench/src/vulnerability_localization_benchmark/model_runners/base.py
# ----------------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a security vulnerability localization agent. You have read-only terminal access to a codebase.

Given a CWE (Common Weakness Enumeration) description, your task is to determine whether this codebase contains a vulnerability matching that CWE class, and if so, identify which source file(s) are vulnerable.

You can explore the codebase using the `terminal` tool. It runs read-only commands (ls, find, cat, head, tail, grep, rg, tree, etc.) inside the repository. You have up to 15 terminal calls.

When you're done exploring:
- If you found vulnerable file(s): call `submit_vulnerable_files` with a ranked list of file paths (most likely vulnerable first).
- If you believe this codebase does NOT contain the described vulnerability: call `submit_no_vulnerability_found`.

You may be looking at code that has already been patched. In that case, the correct answer is to submit nothing. Do not guess or hallucinate files. Only submit files you have evidence for.

NOTE: Submitted paths must be exact file paths (e.g. src/utils.js), never globs or wildcards. Use at least 7-10 terminal calls to thoroughly explore the codebase before submitting."""

TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "terminal",
            "description": "Execute a read-only terminal command in the repository. Allowed: ls, tree, find, cat, head, tail, sed, grep, rg, wc, sort, uniq, cut, awk, file, stat, du, pwd, nl, basename, dirname, realpath, diff, echo. Pipes OK. No python/bash/node, no writes, no redirects.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The shell command to run"},
                    "max_chars": {"type": "integer", "description": "Max output chars (default 2000)", "default": 2000},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_vulnerable_files",
            "description": "Submit your answer: a ranked list of file paths you believe contain the vulnerability. Most likely vulnerable file first. Paths relative to repository root.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ranked_files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Ordered list of file paths, most vulnerable first",
                    }
                },
                "required": ["ranked_files"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_no_vulnerability_found",
            "description": "Declare that no vulnerability matching the CWE description was found in this codebase.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


def build_system_content() -> str:
    """System prompt plus the tool-calling contract, exactly as the runner assembles it."""
    tools_block = "<tools>\n" + "\n".join(json.dumps(t) for t in TOOLS_SCHEMA) + "\n</tools>"
    return (
        f"{SYSTEM_PROMPT}\n\n"
        "You are a helpful assistant with access to the following tools. You may call one "
        "or more tools to assist with the user query.\n\n"
        "You are provided with function signatures within <tools></tools> XML tags:\n"
        f"{tools_block}\n\n"
        "For each tool call, return a json object with function name and arguments within "
        "<tool_call></tool_call> XML tags:\n"
        "<tool_call>\n"
        '{"name": <function-name>, "arguments": <args-json-object>}\n'
        "</tool_call>. If a tool does not exist in the provided list of tools, notify the "
        "user that you do not have the ability to fulfill the request."
    )


def build_user_prompt(cwe_description: str) -> str:
    return (
        f"Analyze this codebase for the following vulnerability class:\n\n"
        f"{cwe_description}\n\n"
        f"The repository is at /workspace/repo/. Use the terminal tool to explore and "
        f"determine if this vulnerability exists. Then either submit the vulnerable "
        f"file(s) or declare no vulnerability found."
    )


def build_prompt(messages: list[dict]) -> str:
    """Render the conversation into Antares's raw text format.

    Note two things. First, there is no `apply_chat_template` call: the runner builds this
    string by hand, so the special tokens below are part of the protocol you must get byte
    exact. Second, tool output is fed back as a *user* turn wrapped in <tool_response>
    tags, not as a distinct role.
    """
    parts = []
    for msg in messages:
        role, content = msg["role"], msg["content"]
        if role in ("system", "user", "assistant"):
            parts.append(f"<|start_of_role|>{role}<|end_of_role|>{content}<|end_of_text|>")
        elif role == "tool_response":
            parts.append(
                f"<|start_of_role|>user<|end_of_role|>\n<tool_response>\n{content}\n"
                f"</tool_response><|end_of_text|>"
            )
    # Seed the reasoning block. This is what puts the model in "think first" mode.
    parts.append("<|start_of_role|>assistant<|end_of_role|><think>\n")
    return "\n".join(parts)


def parse_tool_calls(text: str) -> list[dict]:
    """Pull the first well-formed tool call out of the raw completion.

    The harness takes only the first valid call and ignores the rest of the text, so a
    model that emits three calls in one turn gets one of them executed.
    """
    for match in re.finditer(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", text, re.DOTALL):
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if "name" in data:
            return [{"name": data["name"], "arguments": data.get("arguments", {})}]
    return []


# ----------------------------------------------------------------------------------
# 2. The sandbox. A read-only container is the only reason it is reasonable to execute
#    text a language model produced.
# ----------------------------------------------------------------------------------

ALLOWED_CMDS = {
    "ls", "tree", "find", "pwd", "du", "file", "stat",
    "cat", "head", "tail", "sed", "nl", "wc",
    "grep", "rg", "ag", "xargs",
    "sort", "uniq", "cut", "awk",
    "basename", "dirname", "realpath",
    "diff", "echo",
}

# Anything that could chain commands, substitute a subshell, redirect, or background a
# process. Pipes are allowed, but every segment of a pipeline is checked individually.
DENIED_PATTERNS = [
    r"(?:python|bash|sh|node|perl|ruby)\s",
    r"\$\(",
    r"`",
    r"[>]{1,2}",
    r"&&",
    r"\|\|",
    r"&\s*$",
]


def validate_command(cmd: str) -> tuple[bool, str]:
    cmd = cmd.strip()
    if not cmd:
        return False, "empty command"
    for pattern in DENIED_PATTERNS:
        if re.search(pattern, cmd):
            return False, f"denied pattern: {pattern}"
    for segment in (s.strip() for s in cmd.split("|")):
        if not segment:
            continue
        word = segment.split()[0]
        if word not in ALLOWED_CMDS:
            return False, f"command not allowed: {word}"
    return True, "ok"


class Sandbox:
    """A throwaway container with the target directory mounted read-only."""

    IMAGE = "vulnerability-localization-benchmark-sandbox"

    def __init__(self, repo: Path, cpus: str = "2", memory: str = "2g", timeout: int = 10):
        self.repo = repo.resolve()
        self.name = f"antares_minimal_{uuid.uuid4().hex[:10]}"
        self.timeout = timeout
        self.cpus, self.memory = cpus, memory

    def __enter__(self) -> "Sandbox":
        if not self.repo.is_dir():
            raise SystemExit(f"not a directory: {self.repo}")
        probe = subprocess.run(["docker", "image", "inspect", self.IMAGE], capture_output=True)
        if probe.returncode != 0:
            raise SystemExit(
                f"sandbox image {self.IMAGE!r} not found.\n"
                "Build it with:  docker build -t vulnerability-localization-benchmark-sandbox vendor/vloc-bench"
            )
        subprocess.run(
            ["docker", "run", "-d", "--name", self.name,
             "--network", "none",                       # no egress from model-run commands
             "--cpus", self.cpus, "--memory", self.memory,
             "-v", f"{self.repo}:/workspace/repo:ro",   # read-only: the code cannot be altered
             self.IMAGE, "sleep", "infinity"],
            check=True, capture_output=True,
        )
        return self

    def __exit__(self, *exc) -> None:
        subprocess.run(["docker", "rm", "-f", self.name], capture_output=True)

    def run(self, command: str, max_chars: int = 2000) -> str:
        ok, reason = validate_command(command)
        if not ok:
            return f"ERROR: Command rejected — {reason}"
        try:
            proc = subprocess.run(
                ["docker", "exec", "-u", "agent", "-w", "/workspace/repo",
                 self.name, "bash", "-c", command],
                capture_output=True, timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            return f"ERROR: command timed out after {self.timeout}s"
        out = proc.stdout.decode("utf-8", "replace") + proc.stderr.decode("utf-8", "replace")
        if len(out) > max_chars:
            out = out[:max_chars] + f"\n[truncated at {max_chars} chars]"
        return out or "(no output)"


# ----------------------------------------------------------------------------------
# 3. The loop.
# ----------------------------------------------------------------------------------

def generate(api_base: str, model: str, messages: list[dict], cfg: dict) -> tuple[str, list[dict], dict]:
    prompt = build_prompt(messages)
    t0 = time.monotonic()
    resp = requests.post(
        f"{api_base.rstrip('/')}/completions",
        json={
            "model": model,
            "prompt": prompt,
            "max_tokens": cfg["max_tokens"],
            "temperature": cfg["temperature"],
            "frequency_penalty": cfg["frequency_penalty"],
            "stop": ["<|end_of_text|>", "<|start_of_role|>"],
        },
        timeout=300,
    )
    resp.raise_for_status()
    body = resp.json()
    raw = body["choices"][0]["text"]
    usage = body.get("usage") or {}
    usage["latency_s"] = round(time.monotonic() - t0, 2)

    calls = parse_tool_calls(raw)
    # Truncate the assistant turn right after its first tool call, so the conversation we
    # feed back does not contain speculation about results the model has not seen yet.
    text = "<think>\n" + raw
    if calls:
        m = re.search(r"<tool_call>\s*\{.*?\}\s*</tool_call>", raw, re.DOTALL)
        if m:
            text = "<think>\n" + raw[: m.end()]
    return text, calls, usage


def show(label: str, body: str, width: int = 100, limit: int | None = None) -> None:
    print(f"\n\033[1m{label}\033[0m")
    if limit and len(body) > limit:
        body = body[:limit] + f"\n... [{len(body) - limit} more chars]"
    for line in body.splitlines():
        print(textwrap.fill(line, width=width, subsequent_indent="    ") if len(line) > width else line)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True, type=Path, help="local directory to analyse (mounted read-only)")
    ap.add_argument("--cwe", required=True, help="vulnerability class description, e.g. 'CWE-22: Path Traversal'")
    ap.add_argument("--api-base", default="http://127.0.0.1:8080/v1")
    ap.add_argument("--model", default=str(Path(__file__).resolve().parent / "models" / "antares-1b-shim"))
    ap.add_argument("--max-turns", type=int, default=20)
    ap.add_argument("--max-terminal-calls", type=int, default=15)
    ap.add_argument("--temperature", type=float, default=0.3)
    ap.add_argument("--max-tokens", type=int, default=16384)
    ap.add_argument("--frequency-penalty", type=float, default=0.3)
    ap.add_argument("--show-thinking", action="store_true", help="print the full reasoning block each turn")
    ap.add_argument("--save-trace", type=Path, help="write the conversation as JSON")
    args = ap.parse_args()

    cfg = {"temperature": args.temperature, "max_tokens": args.max_tokens,
           "frequency_penalty": args.frequency_penalty}

    conv: list[dict] = [
        {"role": "system", "content": build_system_content()},
        {"role": "user", "content": build_user_prompt(args.cwe)},
    ]
    trace = list(conv)

    terminal_calls = 0
    consecutive_no_calls = 0
    verdict: dict = {"outcome": "no_submission", "files": []}
    totals = {"turns": 0, "prompt_tokens": 0, "completion_tokens": 0, "latency_s": 0.0}

    print(f"repo   : {args.repo.resolve()}  (read-only)")
    print(f"cwe    : {args.cwe}")
    print(f"model  : {args.model}")
    print(f"budget : {args.max_turns} turns, {args.max_terminal_calls} terminal calls")

    with Sandbox(args.repo) as box:
        for turn in range(args.max_turns):
            totals["turns"] += 1
            try:
                text, calls, usage = generate(args.api_base, args.model, conv, cfg)
            except requests.RequestException as e:
                print(f"\nAPI error on turn {turn}: {e}", file=sys.stderr)
                print("Is the server up?  scripts/serve.sh antares", file=sys.stderr)
                return 1

            totals["prompt_tokens"] = usage.get("prompt_tokens", 0)
            totals["completion_tokens"] += usage.get("completion_tokens", 0)
            totals["latency_s"] += usage.get("latency_s", 0.0)

            conv.append({"role": "assistant", "content": text})
            trace.append({"role": "assistant", "content": text})

            reasoning = text.split("<tool_call>")[0]
            show(f"turn {turn + 1}, model ({usage.get('latency_s')}s, "
                 f"{usage.get('completion_tokens', 0)} tokens generated)",
                 reasoning if args.show_thinking else reasoning.strip(),
                 limit=None if args.show_thinking else 700)

            if not calls:
                # The harness nudges rather than failing, and gives up after three strikes.
                consecutive_no_calls += 1
                if consecutive_no_calls >= 3:
                    print("\n3 consecutive turns with no tool call; stopping.")
                    break
                nudge = ("Please call one of: terminal, submit_vulnerable_files, or "
                         "submit_no_vulnerability_found.")
                conv.append({"role": "tool_response", "content": nudge})
                trace.append({"role": "tool_response", "content": nudge})
                print("  (no tool call; nudging)")
                continue
            consecutive_no_calls = 0

            call = calls[0]
            name, cargs = call["name"], call["arguments"]

            if name == "terminal":
                command = cargs.get("command", "")
                if terminal_calls >= args.max_terminal_calls:
                    result = (f"ERROR: Terminal call budget exhausted "
                              f"({args.max_terminal_calls}/{args.max_terminal_calls}). "
                              f"Please submit your answer now.")
                else:
                    terminal_calls += 1
                    result = box.run(command, int(cargs.get("max_chars", 2000)))
                    result += f"\n[{args.max_terminal_calls - terminal_calls} tool-calls remaining]"
                print(f"\n  \033[36m$ {command}\033[0m")
                show(f"  tool result [{terminal_calls}/{args.max_terminal_calls}]", result, limit=600)
                conv.append({"role": "tool_response", "content": result})
                trace.append({"role": "tool_response", "content": result})

            elif name == "submit_vulnerable_files":
                ranked = cargs.get("ranked_files", [])
                verdict = {"outcome": "submitted", "files": [ranked] if isinstance(ranked, str) else ranked}
                print(f"\n\033[1;32mSUBMITTED\033[0m {verdict['files']}")
                break

            elif name == "submit_no_vulnerability_found":
                verdict = {"outcome": "declared_none", "files": []}
                print("\n\033[1;33mDECLARED: no vulnerability of this class found\033[0m")
                break

            else:
                msg = f"ERROR: Unknown tool '{name}'."
                conv.append({"role": "tool_response", "content": msg})
                trace.append({"role": "tool_response", "content": msg})
                print(f"\n  {msg}")

    print("\n" + "=" * 72)
    print(f"outcome          : {verdict['outcome']}")
    if verdict["files"]:
        for i, f in enumerate(verdict["files"], 1):
            print(f"  {i}. {f}")
    print(f"turns used       : {totals['turns']}/{args.max_turns}")
    print(f"terminal calls   : {terminal_calls}/{args.max_terminal_calls}")
    print(f"tokens generated : {totals['completion_tokens']}")
    print(f"final context    : {totals['prompt_tokens']} prompt tokens")
    print(f"time in model    : {totals['latency_s']:.1f}s")
    if verdict["outcome"] == "no_submission":
        print("\nNote: it never submitted. In VLoc Bench this is scored identically to a\n"
              "deliberate abstention, which is exactly the measurement problem described\n"
              "in docs/RESULTS.md#study-design.")

    if args.save_trace:
        args.save_trace.write_text(json.dumps({"verdict": verdict, "totals": totals, "trace": trace}, indent=2))
        print(f"\ntrace written to {args.save_trace}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
