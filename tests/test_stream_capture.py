"""Tests for agent_eval.agent.stream_capture — turn counting and deduplication."""

from conftest import make_assistant, make_result, to_jsonl

from agent_eval.agent.stream_capture import count_subagent_turns, extract_usage


class TestExtractUsage:
    def test_returns_seen_ids(self, post_2_1_108_stream):
        """extract_usage returns a 6-tuple including the seen_msg_ids set."""
        result = extract_usage(post_2_1_108_stream["lines"])
        assert len(result) == 6
        _, _, num_turns, seen_msg_ids, _, _ = result
        assert isinstance(seen_msg_ids, set)
        assert len(seen_msg_ids) == num_turns

    def test_includes_subagent_ids_post_2_1_108(self, post_2_1_108_stream):
        """Post-2.1.108: seen_msg_ids includes foreground subagent message IDs."""
        _, _, num_turns, seen_ids, _, _ = extract_usage(post_2_1_108_stream["lines"])
        assert num_turns == 7  # 5 root + 2 foreground subagent
        assert "msg_001" in seen_ids
        assert "msg_sub_001" in seen_ids
        assert "msg_sub_002" in seen_ids

    def test_prefers_model_usage(self):
        """When modelUsage is present in result, token_usage comes from it."""
        events = [
            make_assistant("msg_001", input_tokens=100, output_tokens=50),
            make_result(model_usage={
                "claude-sonnet-4-5": {
                    "inputTokens": 9000,
                    "outputTokens": 3000,
                    "cacheReadInputTokens": 500,
                    "cacheCreationInputTokens": 200,
                    "costUSD": 0.42,
                },
            }),
        ]
        token_usage, _, _, _, _, per_model = extract_usage(to_jsonl(events))
        # Should use modelUsage totals, not assistant event usage
        assert token_usage["input"] == 9000
        assert token_usage["output"] == 3000
        assert per_model["claude-sonnet-4-5"]["cost_usd"] == 0.42


class TestCountSubagentTurns:
    def test_deduplicates_with_already_seen(self, tmp_path, post_2_1_108_stream):
        """With already_seen, only genuinely new IDs are counted."""
        already_seen = {"msg_sub_001", "msg_sub_002"}
        new = count_subagent_turns(
            post_2_1_108_stream["subagent_dir"], already_seen=already_seen)
        assert new == 1  # only msg_sub_003

    def test_counts_all_without_already_seen(self, post_2_1_108_stream):
        """Without already_seen, counts all unique assistant IDs."""
        total = count_subagent_turns(post_2_1_108_stream["subagent_dir"])
        assert total == 3

    def test_returns_zero_for_missing_dir(self, tmp_path):
        """Non-existent directory returns 0."""
        assert count_subagent_turns(tmp_path / "nonexistent") == 0


class TestCombinedTurnCount:
    def test_no_double_count_post_2_1_108(self, post_2_1_108_stream):
        """Post-2.1.108: combined count deduplicates overlapping IDs."""
        _, _, stream_turns, stream_ids, _, _ = extract_usage(
            post_2_1_108_stream["lines"])
        new_turns = count_subagent_turns(
            post_2_1_108_stream["subagent_dir"], already_seen=stream_ids)
        total = stream_turns + new_turns
        assert total == post_2_1_108_stream["expected_total"]  # 8, not 10

    def test_no_overlap_pre_2_1_108(self, pre_2_1_108_stream):
        """Pre-2.1.108: no overlap, sum is correct without dedup changing anything."""
        _, _, stream_turns, stream_ids, _, _ = extract_usage(
            pre_2_1_108_stream["lines"])
        new_turns = count_subagent_turns(
            pre_2_1_108_stream["subagent_dir"], already_seen=stream_ids)
        total = stream_turns + new_turns
        assert total == pre_2_1_108_stream["expected_total"]  # 8
        assert stream_turns == 5
        assert new_turns == 3
