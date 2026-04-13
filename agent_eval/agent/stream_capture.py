"""Stream-JSON capture utilities for Claude Code output.

Reusable functions and classes for processing the stream-json output
from ``claude --print --output-format stream-json``.  Used by both
:class:`ClaudeCodeRunner` (eval pipeline) and ``claude-trace`` (standalone).
"""

import json
import os
import re
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


# ── Subagent capture ─────────────────────────────────────────────────

class SubagentCapture:
    """Track and capture background agent .jsonl files in-flight.

    Background agents write their conversation to .jsonl files that
    Claude Code deletes when the session ends.  This class tracks
    output file paths from tool_result events and reads them before
    they're cleaned up.

    Usage::

        capture = SubagentCapture()
        for event in stream_events:
            capture.on_event(event)
        capture.final_sweep()
        # after process exits:
        capture.post_exit_sweep()
        subagent_files = capture.outputs
    """

    def __init__(self):
        self._output_files = {}   # tool_use_id -> (agent_id, file_path)
        self._agent_outputs = {}  # agent_id -> file_content

    def on_event(self, event: dict) -> None:
        """Process a stream-json event for subagent tracking."""
        # Track output file paths from "async launched" tool_results
        if event.get("type") == "user":
            for blk in event.get("message", {}).get("content", []):
                if isinstance(blk, dict) and blk.get("type") == "tool_result":
                    c = blk.get("content", "")
                    txt = (c if isinstance(c, str)
                           else " ".join(
                               x.get("text", "") for x in c
                               if isinstance(x, dict)))
                    m_id = re.search(r"agentId:\s*(\w+)", txt)
                    m_file = re.search(r"output_file:\s*(\S+)", txt)
                    if m_id and m_file:
                        tuid = blk.get("tool_use_id", "")
                        self._output_files[tuid] = (
                            m_id.group(1), m_file.group(1))

        # Read output file immediately on completion notification
        if (event.get("type") == "system"
                and event.get("subtype") == "task_notification"
                and event.get("status") == "completed"):
            tuid = event.get("tool_use_id", "")
            if tuid in self._output_files:
                aid, path = self._output_files[tuid]
                if aid not in self._agent_outputs:
                    self._read_file(aid, path)

    def final_sweep(self) -> None:
        """Read any remaining output files before process exit."""
        for _tuid, (aid, path) in self._output_files.items():
            if aid not in self._agent_outputs:
                self._read_file(aid, path)

    def post_exit_sweep(self) -> None:
        """Try to recover output files after process has exited.

        Resolves symlinks and searches the .claude/projects/ session
        directory as a last resort.
        """
        if len(self._agent_outputs) >= len(self._output_files):
            return
        for _tuid, (aid, path) in self._output_files.items():
            if aid in self._agent_outputs:
                continue
            # Try resolved symlink
            try:
                real = os.path.realpath(path)
                if os.path.exists(real):
                    self._agent_outputs[aid] = open(real).read()
                    continue
            except (OSError, UnicodeDecodeError):
                pass
            # Search session subagents dir
            session_dir = Path.home() / ".claude" / "projects"
            for sd in session_dir.glob(f"*/*/subagents/agent-{aid}.jsonl"):
                try:
                    self._agent_outputs[aid] = sd.read_text()
                    break
                except (OSError, UnicodeDecodeError):
                    pass

    @property
    def outputs(self) -> dict:
        """Agent ID → file content for all captured subagent outputs."""
        return dict(self._agent_outputs)

    def _read_file(self, agent_id: str, path: str) -> None:
        try:
            with open(path) as f:
                self._agent_outputs[agent_id] = f.read()
        except (OSError, UnicodeDecodeError):
            pass
