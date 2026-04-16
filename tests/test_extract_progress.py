"""Tests for _extract_progress in claude_code.py — progress display filtering."""

from conftest import make_assistant, make_result

from agent_eval.agent.claude_code import _extract_progress


def test_root_assistant_shows_tool():
    """Root assistant event with Bash tool shows the command."""
    event = make_assistant("msg_001",
                           tools=[("Bash", "tu_001", {"command": "ls -la"})])
    assert _extract_progress(event) == "Running: ls -la"


def test_subagent_msg_returns_empty():
    """Foreground subagent message (with parent_tool_use_id) returns empty."""
    event = make_assistant("msg_sub_001",
                           parent_tool_use_id="tu_agent_x",
                           tools=[("Bash", "tu_sub", {"command": "echo hi"})])
    assert _extract_progress(event) == ""


def test_result_event_shows_summary():
    """Result event shows turn count and cost."""
    event = make_result(cost_usd=0.15, num_turns=10)
    assert _extract_progress(event) == "Done (10 turns, $0.15)"
