#!/usr/bin/env python3
"""PreToolUse hook script for intercepting tools during headless eval.

Reads tool_handlers.yaml from the workspace. Handlers contain resolved
patterns and runtime checks (from natural language `match` and `prompt`
in eval.yaml, resolved by eval-run at workspace setup time).

Supports:
- Auto-answering AskUserQuestion via per-case overrides
- Blocking tools based on env var checks (e.g., production Jira)
- Filtering Bash commands by content patterns
"""

import json
import os
import re
import sys
from pathlib import Path

import yaml


def main():
    input_data = json.load(sys.stdin)
    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {})

    # Load handler config from workspace
    config_path = Path("tool_handlers.yaml")
    if not config_path.exists():
        sys.exit(0)

    with open(config_path) as f:
        config = yaml.safe_load(f) or {}

    # Find matching handler
    handler = _find_handler(tool_name, tool_input, config.get("handlers", []))
    if not handler:
        sys.exit(0)  # No handler — pass through

    # --- AskUserQuestion: auto-answer ---
    if tool_name == "AskUserQuestion":
        _handle_ask_user(tool_input, config, handler)
        return

    # --- Env checks: block if environment doesn't match ---
    env_checks = handler.get("env_checks", {})
    if env_checks:
        for var_name, check in env_checks.items():
            value = os.environ.get(var_name, "")
            must_contain = check.get("must_contain", [])
            if must_contain and not any(m in value.lower() for m in must_contain):
                _deny(f"Env {var_name}='{value}' doesn't match required: {must_contain}")
                return
        # All env checks passed — allow
        sys.exit(0)

    # --- Default for matched tools without specific handling: block ---
    _deny(f"Blocked by eval harness: {handler.get('match', 'matched handler')}")


def _find_handler(tool_name, tool_input, handlers):
    """Find the first handler that matches the tool call."""
    for h in handlers:
        patterns = h.get("patterns", [])
        input_filters = h.get("input_filters", [])

        # For Bash with input_filters: must match BOTH pattern AND filter
        if tool_name == "Bash" and "Bash" in patterns and input_filters:
            command = tool_input.get("command", "")
            if any(re.search(f, command, re.IGNORECASE) for f in input_filters):
                return h
            continue  # Bash matched pattern but not filter — skip

        # For other tools: match by pattern only
        for pattern in patterns:
            if pattern == tool_name:
                return h
            if pattern.endswith("*") and tool_name.startswith(pattern[:-1]):
                return h

    return None


def _handle_ask_user(tool_input, config, handler):
    """Auto-answer AskUserQuestion using case overrides or first option."""
    case_overrides = config.get("case_overrides", {})
    answers = {}
    for q in tool_input.get("questions", []):
        text = q.get("question", "")
        answer = case_overrides.get(text)
        if answer is None:
            options = q.get("options", [])
            answer = options[0]["label"] if options else "yes"
        answers[text] = answer

    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": {
                "questions": tool_input["questions"],
                "answers": answers,
            },
        }
    }
    json.dump(output, sys.stdout)


def _deny(reason):
    """Deny the tool call with a reason."""
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "reason": reason,
        }
    }
    json.dump(output, sys.stdout)


if __name__ == "__main__":
    main()
