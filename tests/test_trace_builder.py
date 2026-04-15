"""Tests for agent_eval.mlflow.trace_builder — trace construction and span hierarchy."""

import json

from conftest import (
    make_assistant, make_result, make_system_init, make_user,
)

from agent_eval.mlflow.trace_builder import build_trace, make_span


def _write_stream(tmp_path, events):
    """Write events to a stdout.log file and return the path."""
    stdout = tmp_path / "stdout.log"
    stdout.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    return stdout


def _basic_run_result():
    return {
        "exit_code": 0,
        "duration_s": 10.0,
        "cost_usd": 0.10,
        "model": "claude-sonnet-4-5",
        "token_usage": {"input": 100, "output": 50,
                        "cache_read": 0, "cache_create": 0},
    }


def _get_span_type(span):
    """Extract mlflow.spanType from a span's attributes."""
    return json.loads(span["attributes"].get("mlflow.spanType", '"?"'))


class TestMakeSpan:
    def test_required_fields(self):
        """make_span returns a dict with all required trace span fields."""
        span = make_span(
            trace_id="tr-001", parent_id="sp-parent",
            name="test", span_type="TOOL",
            start_ns=1000, end_ns=2000,
        )
        assert span["trace_id"] == "tr-001"
        assert span["parent_span_id"] == "sp-parent"
        assert span["name"] == "test"
        assert span["start_time_unix_nano"] == 1000
        assert span["end_time_unix_nano"] == 2000
        assert len(span["span_id"]) == 16  # hex of 8 bytes
        assert _get_span_type(span) == "TOOL"


class TestBuildTrace:
    def test_returns_dict_with_spans(self, tmp_path):
        """build_trace returns a trace dict with info and non-empty spans."""
        events = [
            make_system_init(),
            make_assistant("msg_001",
                           tools=[("Bash", "tu_001", {"command": "ls"})]),
            make_user(tool_results=[("tu_001", "file1.txt")]),
            make_result(cost_usd=0.10, num_turns=1),
        ]
        stdout = _write_stream(tmp_path, events)
        trace = build_trace(stdout, _basic_run_result(),
                            run_id="test-run", experiment_id="exp-001")
        assert trace is not None
        assert "info" in trace
        assert trace["info"]["trace_id"]
        spans = trace["data"]["spans"]
        assert len(spans) > 0
        # Root span should be AGENT type
        root = spans[0]
        assert _get_span_type(root) == "AGENT"

    def test_skips_subagent_in_top_segments(self, tmp_path):
        """Foreground subagent messages do not create top-level tool spans."""
        events = [
            make_system_init(),
            make_assistant("msg_001",
                           tools=[("Bash", "tu_001", {"command": "ls"})]),
            make_user(tool_results=[("tu_001", "output")]),
            # Foreground subagent message — should NOT appear as a top-level segment
            make_assistant("msg_sub_001",
                           parent_tool_use_id="tu_agent_x",
                           tools=[("Read", "tu_sub", {"file_path": "/sub"})]),
            make_result(cost_usd=0.10, num_turns=2),
        ]
        stdout = _write_stream(tmp_path, events)
        trace = build_trace(stdout, _basic_run_result(),
                            run_id="test-run", experiment_id="exp-001")
        assert trace is not None
        spans = trace["data"]["spans"]
        # Find all TOOL-type spans at the non-root level
        tool_spans = [s for s in spans if _get_span_type(s) == "TOOL"]
        tool_names = [s["name"] for s in tool_spans]
        # Bash should be present as a top-level tool span
        assert any("Bash" in n for n in tool_names)
        # The subagent's Read should NOT be a top-level tool span
        # (it should be nested under an Agent span, if at all)
        top_level_reads = [
            s for s in tool_spans
            if "Read" in s["name"]
            and s["parent_span_id"] == spans[0]["span_id"]  # direct child of root
        ]
        assert len(top_level_reads) == 0

    def test_returns_none_for_missing_file(self, tmp_path):
        """build_trace returns None if stdout file doesn't exist."""
        result = build_trace(tmp_path / "nonexistent.log",
                             _basic_run_result(),
                             run_id="x", experiment_id="e")
        assert result is None
