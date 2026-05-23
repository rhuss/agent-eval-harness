"""Integration tests for builtin judge resolution in the scoring pipeline."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent_eval.config import EvalConfig, JudgeConfig, ModelsConfig
from score import load_judges


class TestLoadJudgesBuiltin:

    def test_builtin_python_judge(self):
        config = EvalConfig(name="test", skill="test")
        config.models = ModelsConfig(judge="claude-sonnet-4-6")
        config.judges = [
            JudgeConfig(name="budget", builtin="cost_budget",
                        arguments={"max_cost_usd": 0.50}),
        ]
        judges = load_judges(config)
        assert len(judges) == 1
        name, scorer, condition, judge_type = judges[0]
        assert name == "budget"
        assert judge_type == "builtin"
        assert condition == ""

        # Test the scorer
        result = scorer(outputs={"cost_usd": 0.30})
        assert isinstance(result, tuple)
        assert result[0] is True
        assert "$0.30" in result[1]

    def test_builtin_python_judge_fail(self):
        config = EvalConfig(name="test", skill="test")
        config.models = ModelsConfig(judge="claude-sonnet-4-6")
        config.judges = [
            JudgeConfig(name="budget", builtin="cost_budget",
                        arguments={"max_cost_usd": 0.10}),
        ]
        judges = load_judges(config)
        _, scorer, _, _ = judges[0]
        result = scorer(outputs={"cost_usd": 0.50})
        assert result[0] is False
        assert "exceeds" in result[1]

    def test_builtin_fqn_resolution(self):
        config = EvalConfig(name="test", skill="test")
        config.models = ModelsConfig(judge="claude-sonnet-4-6")
        config.judges = [
            JudgeConfig(name="budget", builtin="efficiency/cost_budget"),
        ]
        judges = load_judges(config)
        assert len(judges) == 1
        assert judges[0][3] == "builtin"

    def test_unknown_builtin_raises(self):
        config = EvalConfig(name="test", skill="test")
        config.judges = [
            JudgeConfig(name="bad", builtin="nonexistent_judge"),
        ]
        with pytest.raises(ValueError, match="Unknown builtin judge"):
            load_judges(config)

    def test_mutual_exclusivity_check(self):
        config = EvalConfig(name="test", skill="test")
        config.judges = [
            JudgeConfig(name="bad", builtin="cost_budget",
                        check="return (True, 'ok')"),
        ]
        with pytest.raises(ValueError, match="mutually exclusive.*check"):
            load_judges(config)

    def test_mutual_exclusivity_prompt(self):
        config = EvalConfig(name="test", skill="test")
        config.judges = [
            JudgeConfig(name="bad", builtin="cost_budget",
                        prompt="evaluate this"),
        ]
        with pytest.raises(ValueError, match="mutually exclusive.*prompt"):
            load_judges(config)

    def test_mutual_exclusivity_prompt_file(self):
        config = EvalConfig(name="test", skill="test")
        config.judges = [
            JudgeConfig(name="bad", builtin="cost_budget",
                        prompt_file="some/file.md"),
        ]
        with pytest.raises(ValueError, match="mutually exclusive.*prompt_file"):
            load_judges(config)

    def test_mutual_exclusivity_module(self):
        config = EvalConfig(name="test", skill="test")
        config.judges = [
            JudgeConfig(name="bad", builtin="cost_budget",
                        module="some.module", function="judge"),
        ]
        with pytest.raises(ValueError, match="mutually exclusive.*module, function"):
            load_judges(config)

    def test_arguments_passed_to_python_judge(self):
        config = EvalConfig(name="test", skill="test")
        config.models = ModelsConfig(judge="claude-sonnet-4-6")
        config.judges = [
            JudgeConfig(name="budget", builtin="cost_budget",
                        arguments={"max_cost_usd": 2.0}),
        ]
        judges = load_judges(config)
        _, scorer, _, _ = judges[0]
        result = scorer(outputs={"cost_usd": 1.50})
        assert result[0] is True
        assert "$2.00" in result[1]


    def test_builtin_llm_judge_creates_scorer(self):
        config = EvalConfig(name="test", skill="test")
        config.models = ModelsConfig(judge="claude-sonnet-4-6")
        config.judges = [
            JudgeConfig(name="safety", builtin="no_harmful_content",
                        arguments={"categories": ["malware"]}),
        ]
        judges = load_judges(config)
        assert len(judges) == 1
        name, scorer, condition, judge_type = judges[0]
        assert name == "safety"
        assert judge_type == "builtin"

        with patch("score._call_llm_judge_for_bool",
                   return_value=(True, "ok")) as mock_call:
            result = scorer(outputs={"conversation": "test", "files": {}})
            assert result == (True, "ok")
            rendered_prompt = mock_call.call_args[0][0]
            assert "malware" in rendered_prompt
            assert "test" in rendered_prompt


class TestLoadJudgesDuplicateValidation:

    def test_duplicate_names_raise(self):
        config = EvalConfig(name="test", skill="test")
        config.judges = [
            JudgeConfig(name="same_name", check="return (True, 'ok')"),
            JudgeConfig(name="same_name", check="return (False, 'bad')"),
        ]
        with pytest.raises(ValueError, match="Duplicate judge name 'same_name'"):
            load_judges(config)

    def test_unique_names_ok(self):
        config = EvalConfig(name="test", skill="test")
        config.judges = [
            JudgeConfig(name="judge_a", check="return (True, 'ok')"),
            JudgeConfig(name="judge_b", check="return (True, 'ok')"),
        ]
        judges = load_judges(config)
        assert len(judges) == 2


class TestLoadJudgesTypes:

    def test_check_judge_returns_4_tuple(self):
        config = EvalConfig(name="test", skill="test")
        config.judges = [
            JudgeConfig(name="test_check", check="return (True, 'ok')"),
        ]
        judges = load_judges(config)
        assert len(judges) == 1
        name, scorer, condition, judge_type = judges[0]
        assert name == "test_check"
        assert judge_type == "check"

    def test_check_judge_with_arguments(self):
        config = EvalConfig(name="test", skill="test")
        config.judges = [
            JudgeConfig(
                name="size_check",
                check='limit = arguments.get("max_chars", 10000)\nreturn (len(outputs.get("content", "")) <= limit, "ok")',
                arguments={"max_chars": 5},
            ),
        ]
        judges = load_judges(config)
        _, scorer, _, _ = judges[0]
        result = scorer(outputs={"content": "hi"})
        assert result[0] is True

        result = scorer(outputs={"content": "this is too long"})
        assert result[0] is False


class TestJudgeTypeMetadata:

    def test_builtin_type_in_4tuple(self):
        config = EvalConfig(name="test", skill="test")
        config.models = ModelsConfig(judge="claude-sonnet-4-6")
        config.judges = [
            JudgeConfig(name="budget", builtin="cost_budget"),
        ]
        judges = load_judges(config)
        assert judges[0][3] == "builtin"

    def test_check_type_in_4tuple(self):
        config = EvalConfig(name="test", skill="test")
        config.judges = [
            JudgeConfig(name="test", check="return (True, 'ok')"),
        ]
        judges = load_judges(config)
        assert judges[0][3] == "check"

    def test_mixed_types_distinguishable(self):
        config = EvalConfig(name="test", skill="test")
        config.models = ModelsConfig(judge="claude-sonnet-4-6")
        config.judges = [
            JudgeConfig(name="budget", builtin="cost_budget"),
            JudgeConfig(name="inline", check="return (True, 'ok')"),
        ]
        judges = load_judges(config)
        types = {name: jtype for name, _, _, jtype in judges}
        assert types["budget"] == "builtin"
        assert types["inline"] == "check"


class TestVendoringPattern:

    def test_vendored_python_judge(self, tmp_path):
        """A copied Python judge works via module/function."""
        import shutil
        src = (Path(__file__).parent.parent / "agent_eval" / "judges"
               / "efficiency" / "cost_budget.py")
        vendor_dir = tmp_path / "eval" / "judges"
        vendor_dir.mkdir(parents=True)
        (vendor_dir.parent / "__init__.py").write_text("")
        (vendor_dir / "__init__.py").write_text("")
        shutil.copy(src, vendor_dir / "cost_budget.py")

        config = EvalConfig(name="test", skill="test")
        config.judges = [
            JudgeConfig(name="vendored_budget",
                        module="eval.judges.cost_budget",
                        function="judge",
                        arguments={"max_cost_usd": 5.0}),
        ]
        judges = load_judges(config, project_root=tmp_path)
        assert len(judges) == 1
        _, scorer, _, judge_type = judges[0]
        assert judge_type == "code"
        result = scorer(outputs={"cost_usd": 3.0})
        assert result[0] is True
        assert "$5.00" in result[1]
