#!/usr/bin/env python3
"""Assert that minimal_agent.py speaks exactly the same protocol as the official harness.

minimal_agent.py exists to be read, so it copies the system prompt, the tool schema and the
prompt template out of the benchmark rather than importing them. Copies rot. If upstream
changes a single character of the system prompt, the teaching artifact silently starts
describing a protocol that no longer exists, and anyone learning from it learns something
false. This test compares the rendered bytes.

Run:  python tests/test_protocol_fidelity.py
"""

from __future__ import annotations

import difflib
import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_minimal_agent():
    spec = importlib.util.spec_from_file_location("minimal_agent", REPO_ROOT / "minimal_agent.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def compare(name: str, ours: str, theirs: str) -> bool:
    if ours == theirs:
        print(f"ok    {name}")
        return True
    print(f"FAIL  {name}")
    diff = difflib.unified_diff(theirs.splitlines(), ours.splitlines(),
                                "harness", "minimal_agent", lineterm="")
    print("\n".join(list(diff)[:40]))
    return False


def test_protocol_matches_harness() -> bool:
    try:
        from vulnerability_localization_benchmark.agent import build_user_prompt as harness_user_prompt
        from vulnerability_localization_benchmark.model_runners import vllm_antares as harness
    except ImportError:
        print("skip  benchmark not installed; run scripts/fetch_benchmark.sh")
        return True

    ma = load_minimal_agent()
    cwe = "CWE-22: Improper Limitation of a Pathname to a Restricted Directory"

    ok = compare("system prompt + tool schema", ma.build_system_content(), harness._build_system_content())
    ok &= compare("user prompt", ma.build_user_prompt(cwe), harness_user_prompt([cwe]))

    conversation = [
        {"role": "system", "content": ma.build_system_content()},
        {"role": "user", "content": ma.build_user_prompt(cwe)},
        {"role": "assistant", "content": "<think>\nlook around\n</think>"},
        {"role": "tool_response", "content": "src\ntests\n[14 tool-calls remaining]"},
    ]
    ok &= compare("rendered completion prompt", ma.build_prompt(conversation), harness._build_prompt(conversation))

    sample = '<think>\nfind it\n</think>\n<tool_call>\n{"name": "terminal", "arguments": {"command": "ls"}}\n</tool_call>'
    ok &= compare("parsed tool calls", repr(ma.parse_tool_calls(sample)), repr(harness._parse_tool_calls(sample)))
    return ok


if __name__ == "__main__":
    sys.exit(0 if test_protocol_matches_harness() else 1)
