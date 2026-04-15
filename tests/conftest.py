"""Shared fixtures and factory functions for stream-json event testing."""

import json
import sys
from pathlib import Path

import pytest

# Add skills script directories to sys.path so tests can import them
_repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(_repo_root / "skills" / "eval-run" / "scripts"))
sys.path.insert(0, str(_repo_root / "skills" / "eval-mlflow" / "scripts"))


# ---------------------------------------------------------------------------
# Factory functions — build individual stream-json events
# ---------------------------------------------------------------------------

def make_tool(name, tool_use_id, input_dict=None):
    """Build a tool_use content block."""
    return {
        "type": "tool_use",
        "id": tool_use_id,
        "name": name,
        "input": input_dict or {},
    }


def make_assistant(msg_id, tools=None, text=None, model="claude-sonnet-4-5",
                   parent_tool_use_id=None, input_tokens=100, output_tokens=50):
    """Build an assistant event.

    Args:
        tools: list of (name, tool_use_id, input_dict) tuples.
        parent_tool_use_id: set this to make it a foreground subagent message.
    """
    content = []
    if text:
        content.append({"type": "text", "text": text})
    for name, tuid, inp in (tools or []):
        content.append(make_tool(name, tuid, inp))

    event = {
        "type": "assistant",
        "message": {
            "id": msg_id,
            "role": "assistant",
            "model": model,
            "content": content,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        },
        "timestamp": "2026-04-14T20:00:00.000Z",
    }
    if parent_tool_use_id:
        event["parent_tool_use_id"] = parent_tool_use_id
    return event


def make_result(cost_usd=0.15, num_turns=10, model_usage=None):
    """Build a result event."""
    event = {
        "type": "result",
        "total_cost_usd": cost_usd,
        "num_turns": num_turns,
    }
    if model_usage:
        event["modelUsage"] = model_usage
    return event


def make_user(tool_results=None, text=None):
    """Build a user event.

    Args:
        tool_results: list of (tool_use_id, content_text) tuples.
    """
    if tool_results:
        content = [
            {"type": "tool_result", "tool_use_id": tuid, "content": txt}
            for tuid, txt in tool_results
        ]
    elif text:
        content = text
    else:
        content = "hello"
    return {
        "type": "user",
        "message": {"role": "user", "content": content},
        "timestamp": "2026-04-14T20:00:00.000Z",
    }


def make_system_init(model="claude-sonnet-4-5"):
    """Build a system init event."""
    return {
        "type": "system",
        "subtype": "init",
        "model": model,
        "timestamp": "2026-04-14T20:00:00.000Z",
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def to_jsonl(events):
    """Convert event dicts to a list of JSON strings (what extract_usage expects)."""
    return [json.dumps(e) for e in events]


def write_transcripts(tmp_path, transcripts):
    """Write subagent transcript JSONL files.

    Args:
        tmp_path: pytest tmp_path fixture.
        transcripts: dict mapping agent_id → list of (msg_id, role) tuples.
            Each tuple becomes one JSONL line.

    Returns:
        Path to the subagents directory.
    """
    subdir = tmp_path / "subagents"
    subdir.mkdir(exist_ok=True)
    for agent_id, messages in transcripts.items():
        lines = []
        for msg_id, role in messages:
            lines.append(json.dumps({
                "message": {
                    "role": role,
                    "id": msg_id,
                    "content": [{"type": "text", "text": "..."}],
                },
            }))
        (subdir / f"agent-{agent_id}.jsonl").write_text("\n".join(lines) + "\n")
    return subdir


# ---------------------------------------------------------------------------
# Composite fixtures — pre-2.1.108 and post-2.1.108 stream shapes
# ---------------------------------------------------------------------------

def _root_events():
    """5 root assistant messages with tool calls."""
    return [
        make_system_init(),
        make_assistant("msg_001", tools=[("Bash", "tu_001", {"command": "ls"})]),
        make_user(tool_results=[("tu_001", "file1.txt")]),
        make_assistant("msg_002", tools=[("Read", "tu_002", {"file_path": "/f"})]),
        make_user(tool_results=[("tu_002", "content")]),
        make_assistant("msg_003", tools=[("Write", "tu_003", {"file_path": "/g", "content": "x"})]),
        make_user(tool_results=[("tu_003", "ok")]),
        make_assistant("msg_004", text="thinking..."),
        make_assistant("msg_005", tools=[("Bash", "tu_005", {"command": "echo done"})]),
        make_user(tool_results=[("tu_005", "done")]),
    ]


def _subagent_transcripts():
    """3 subagent assistant messages in transcript files."""
    return {
        "001": [
            ("msg_sub_001", "assistant"),
            ("msg_sub_001_u", "user"),
        ],
        "002": [
            ("msg_sub_002", "assistant"),
            ("msg_sub_002_u", "user"),
            ("msg_sub_003", "assistant"),
        ],
    }


@pytest.fixture
def pre_2_1_108_stream(tmp_path):
    """Pre-2.1.108: no subagent messages in stdout stream.

    5 root assistant msgs + 3 subagent msgs in transcripts only.
    Expected total turns: 8.
    """
    events = _root_events() + [make_result(cost_usd=0.15, num_turns=5)]
    subagent_dir = write_transcripts(tmp_path, _subagent_transcripts())
    return {
        "lines": to_jsonl(events),
        "events": events,
        "subagent_dir": subagent_dir,
        "expected_stream_turns": 5,
        "expected_subagent_new": 3,
        "expected_total": 8,
    }


@pytest.fixture
def post_2_1_108_stream(tmp_path):
    """Post-2.1.108: foreground subagent messages appear in stdout stream.

    5 root + 2 foreground subagent msgs in stream.
    3 subagent msgs in transcripts (2 overlap with stream).
    Expected total turns: 8 (not 10 — dedup removes the 2 overlapping).
    """
    events = _root_events()
    # Insert foreground subagent messages (these overlap with transcripts)
    events += [
        make_assistant("msg_sub_001", parent_tool_use_id="tu_agent_x",
                       tools=[("Read", "tu_sub_001", {"file_path": "/sub"})]),
        make_assistant("msg_sub_002", parent_tool_use_id="tu_agent_x",
                       text="subagent reasoning"),
    ]
    events.append(make_result(cost_usd=0.15, num_turns=5))
    subagent_dir = write_transcripts(tmp_path, _subagent_transcripts())
    return {
        "lines": to_jsonl(events),
        "events": events,
        "subagent_dir": subagent_dir,
        "expected_stream_turns": 7,  # 5 root + 2 foreground subagent
        "expected_subagent_new": 1,  # only msg_sub_003 is new
        "expected_total": 8,
    }
