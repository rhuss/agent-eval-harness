"""Config schema parsing tests."""

import sys
from pathlib import Path

import pytest

# Ensure agent_eval is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from agent_eval.config import EvalConfig, JudgeConfig, ModelsConfig
from score import _resolve_judge_model


def _write(tmp_path, body):
    p = tmp_path / "eval.yaml"
    p.write_text(body)
    return p


def test_execution_block_parses(tmp_path):
    cfg = EvalConfig.from_yaml(_write(tmp_path, """
name: t
skill: s
execution:
  mode: batch
  arguments: "--in batch.yaml"
  timeout: 1800
  max_budget_usd: 25.5
"""))
    assert cfg.execution.mode == "batch"
    assert cfg.execution.arguments == "--in batch.yaml"
    assert cfg.execution.timeout == 1800
    assert cfg.execution.max_budget_usd == 25.5


def test_runner_block_parses(tmp_path):
    cfg = EvalConfig.from_yaml(_write(tmp_path, """
name: t
skill: s
runner:
  type: claude-code
  plugin_dirs:
    - /tmp/p
  env_strip:
    - FOO
  settings:
    a: 1
  system_prompt: "be careful"
"""))
    assert cfg.runner.type == "claude-code"
    assert cfg.runner.plugin_dirs == ["/tmp/p"]
    assert cfg.runner.env_strip == ["FOO"]
    assert cfg.runner.settings == {"a": 1}
    assert cfg.runner.system_prompt == "be careful"


def test_runner_type_default(tmp_path):
    cfg = EvalConfig.from_yaml(_write(tmp_path, "name: t\nskill: s\n"))
    assert cfg.runner.type == "claude-code"


def test_models_block_defaults(tmp_path):
    cfg = EvalConfig.from_yaml(_write(tmp_path, """
name: t
skill: s
models:
  skill: claude-opus-4-7
  judge: claude-opus-4-7
"""))
    assert cfg.models.skill == "claude-opus-4-7"
    assert cfg.models.subagent is None
    assert cfg.models.judge == "claude-opus-4-7"


def test_mlflow_block_parses(tmp_path):
    cfg = EvalConfig.from_yaml(_write(tmp_path, """
name: t
skill: s
mlflow:
  experiment: e1
  tracking_uri: sqlite:///x.db
  tags:
    team: ml
"""))
    assert cfg.mlflow.experiment == "e1"
    assert cfg.mlflow.tracking_uri == "sqlite:///x.db"
    assert cfg.mlflow.tags == {"team": "ml"}


def test_mlflow_experiment_defaults_to_name_when_block_present(tmp_path):
    """`mlflow:` block present but no `experiment:` → fall back to eval name."""
    cfg = EvalConfig.from_yaml(_write(tmp_path, """
name: my-eval
skill: s
mlflow:
  tracking_uri: sqlite:///x.db
"""))
    assert cfg.mlflow.experiment == "my-eval"


def test_mlflow_disabled_when_block_absent(tmp_path):
    """No `mlflow:` block → experiment empty, MLflow logging off."""
    cfg = EvalConfig.from_yaml(_write(tmp_path, "name: my-eval\nskill: s\n"))
    assert cfg.mlflow.experiment == ""


def test_judge_model_resolution_precedence(tmp_path, monkeypatch):
    """Per-judge `model:` > config.models.judge > EVAL_JUDGE_MODEL > error."""
    cfg = EvalConfig(name="t", skill="s")

    # 1. Per-judge model wins
    jc = JudgeConfig(name="j", model="per-judge-model")
    cfg.models = ModelsConfig(judge="config-judge")
    monkeypatch.setenv("EVAL_JUDGE_MODEL", "env-model")
    assert _resolve_judge_model(jc, cfg) == "per-judge-model"

    # 2. config.models.judge used when per-judge unset
    jc = JudgeConfig(name="j")
    assert _resolve_judge_model(jc, cfg) == "config-judge"

    # 3. env var used when both unset
    cfg.models = ModelsConfig()
    assert _resolve_judge_model(jc, cfg) == "env-model"

    # 4. error when nothing set
    monkeypatch.delenv("EVAL_JUDGE_MODEL", raising=False)
    with pytest.raises(RuntimeError, match="No model configured"):
        _resolve_judge_model(jc, cfg)
