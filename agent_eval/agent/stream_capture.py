"""Stream-JSON capture utilities for Claude Code output.

Reusable functions and classes for processing the stream-json output
from ``claude --print --output-format stream-json``.  Used by both
:class:`ClaudeCodeRunner` (eval pipeline) and ``claude-trace`` (standalone).
"""

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path


# ── Synthetic event injection ────────────────────────────────────────

def make_prompt_event(prompt: str) -> str:
    """Create a synthetic user event JSON line for the prompt.

    ``claude --print`` doesn't emit the stdin prompt as an event, so
    this injects it for downstream consumers (trace builder, MLflow).
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    return json.dumps({
        "type": "user",
        "message": {"role": "user", "content": prompt},
        "timestamp": ts,
    })


def inject_timestamp(line: str) -> str:
    """Add a receive timestamp to a stream-json line that lacks one.

    Assistant events from Claude Code don't have timestamps.  This
    injects wall-clock time so traces have real timing for every event.
    Returns the original line unchanged if it already has a timestamp
    or can't be parsed as JSON.
    """
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return line
    if "timestamp" not in obj:
        obj["timestamp"] = (datetime.now(timezone.utc)
                            .strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z")
        return json.dumps(obj)
    return line


# ── Usage extraction ─────────────────────────────────────────────────

def extract_usage(stdout_lines):
    """Extract token usage, cost, turns, and models from stream-json events.

    - Token totals: from ``result.modelUsage`` (includes all subagents).
      Falls back to summing ``assistant`` event usage if modelUsage is absent.
    - Cost: from the last ``result`` event (cumulative in Claude Code).
    - Turns: count of ``assistant`` events (includes subagent turns).
    - Models: all distinct models observed in ``assistant`` events.

    Returns:
        Tuple of (token_usage, cost_usd, num_turns, models_seen).
    """
    num_turns = 0
    cost_usd = None
    models_seen = set()
    model_usage = None
    fb_input = fb_output = fb_cache_read = fb_cache_create = 0
    for line in stdout_lines:
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if obj.get("type") == "assistant":
            num_turns += 1
            u = obj.get("message", {}).get("usage", {})
            fb_input += u.get("input_tokens", 0)
            fb_output += u.get("output_tokens", 0)
            fb_cache_read += u.get("cache_read_input_tokens", 0)
            fb_cache_create += u.get("cache_creation_input_tokens", 0)
            model = obj.get("message", {}).get("model")
            if model:
                models_seen.add(model)
        elif obj.get("type") == "result":
            cost_usd = obj.get("total_cost_usd", cost_usd)
            mu = obj.get("modelUsage")
            if isinstance(mu, dict):
                model_usage = mu

    token_usage = None
    if model_usage:
        total_input = total_output = total_cache_read = total_cache_create = 0
        for stats in model_usage.values():
            total_input += stats.get("inputTokens", 0)
            total_output += stats.get("outputTokens", 0)
            total_cache_read += stats.get("cacheReadInputTokens", 0)
            total_cache_create += stats.get("cacheCreationInputTokens", 0)
        token_usage = {
            "input": total_input, "output": total_output,
            "cache_read": total_cache_read, "cache_create": total_cache_create,
        }
    elif fb_input or fb_output:
        token_usage = {
            "input": fb_input, "output": fb_output,
            "cache_read": fb_cache_read, "cache_create": fb_cache_create,
        }
    return token_usage, cost_usd, num_turns or None, models_seen


# ── Subagent capture via hooks ───────────────────────────────────────

def setup_subagent_hook(settings: dict, subagent_dir: str) -> None:
    """Add a SubagentStop hook to settings that copies transcripts.

    The SubagentStop hook fires synchronously when a subagent finishes,
    while the transcript file still exists. The hook copies it to a
    known location before Claude Code cleans up the session.

    This replaces the old in-flight capture approach (SubagentCapture)
    which required session persistence and complex multi-phase reads.

    Args:
        settings: The workspace .claude/settings.json dict (modified in place).
        subagent_dir: Absolute path to the directory where transcripts
            should be copied (e.g., /tmp/agent-eval/run-001/subagents).
    """
    hook_script = (
        f'mkdir -p {subagent_dir} && '
        f'cp "$AGENT_TRANSCRIPT_PATH" {subagent_dir}/ 2>/dev/null; true'
    )
    hooks = settings.setdefault("hooks", {})
    hooks.setdefault("SubagentStop", []).append({
        "hooks": [{
            "type": "command",
            "command": f"bash -c '{hook_script}'",
        }],
    })
