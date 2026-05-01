"""Tests for _extract_progress and _extract_denial_list in claude_code.py."""

from conftest import make_assistant, make_result, make_user

from agent_eval.agent.claude_code import _extract_denial_list, _extract_progress


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


def test_permission_denial_detected():
    """User event with is_error and denial text surfaces a warning."""
    event = make_user(tool_results=[
        ("tu_001", "The user denied this tool call. Reason: not in allow list", True),
    ])
    result = _extract_progress(event)
    assert result.startswith("PERMISSION DENIED:")
    assert "denied" in result


def test_non_permission_error_ignored():
    """User event with is_error but non-permission text returns empty."""
    event = make_user(tool_results=[
        ("tu_001", "Error: file not found /tmp/missing.txt", True),
    ])
    assert _extract_progress(event) == ""


def test_normal_tool_result_ignored():
    """User event with a normal tool_result (no error) returns empty."""
    event = make_user(tool_results=[("tu_001", "file contents here")])
    assert _extract_progress(event) == ""


# ── _extract_denial_list tests ──────────────────────────────────────


def test_denial_list_from_result_event():
    """Structured permission_denials from result event are preferred."""
    denials = [
        {"tool_name": "Write", "tool_use_id": "tu_001", "tool_input": {}},
        {"tool_name": "Bash", "tool_use_id": "tu_002", "tool_input": {}},
    ]
    result_obj = make_result(permission_denials=denials)
    assert _extract_denial_list(result_obj, 0) == denials


def test_denial_list_result_takes_precedence_over_streaming():
    """Result event denials win even when streaming counter disagrees."""
    denials = [{"tool_name": "Write", "tool_use_id": "tu_001", "tool_input": {}}]
    result_obj = make_result(permission_denials=denials)
    assert _extract_denial_list(result_obj, 5) == denials


def test_denial_list_fallback_to_streaming_count():
    """When result has no denials, fall back to streaming counter."""
    result_obj = make_result()
    result = _extract_denial_list(result_obj, 3)
    assert len(result) == 3
    assert all(d["tool_name"] == "unknown" for d in result)


def test_denial_list_no_result_obj_with_streaming():
    """When result_obj is None (timeout), use streaming counter."""
    result = _extract_denial_list(None, 2)
    assert len(result) == 2


def test_denial_list_empty_when_no_denials():
    """No denials anywhere returns None."""
    result_obj = make_result(permission_denials=[])
    assert _extract_denial_list(result_obj, 0) is None


def test_denial_list_none_when_both_absent():
    """No result_obj and no streaming count returns None."""
    assert _extract_denial_list(None, 0) is None
