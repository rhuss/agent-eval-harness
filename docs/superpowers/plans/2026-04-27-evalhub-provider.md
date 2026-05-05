# EvalHub Provider for Agent Evaluation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Build a custom EvalHub provider that runs Claude Code skill evaluations via the EvalHub job framework, mapping agent-eval-harness capabilities to EvalHub's native orchestration, MLflow integration, and result aggregation.

**Architecture:** A `FrameworkAdapter` subclass receives a `JobSpec` from EvalHub containing model config, benchmark parameters, and S3 test data references. It stages a workspace, invokes the agent (Claude Code CLI), runs judges, and returns `JobResults` with per-case metrics. The adapter reuses `agent_eval.agent` and `agent_eval.config` from the existing codebase. It ships as a UBI9 container image registered as an EvalHub provider.

**Tech Stack:** Python 3.11+, `eval-hub-sdk[adapter]`, existing `agent_eval` package, Claude Code CLI (in container), S3 for test data

---

## File Structure

```
agent_eval/
  evalhub/                          # NEW — EvalHub integration package
    __init__.py                     # Package init
    adapter.py                      # FrameworkAdapter subclass (core)
    config_translator.py            # eval.yaml → EvalHub provider/benchmark config
    s3_dataset.py                   # Download test cases from S3 to local workspace
    results_mapper.py               # RunResult + judge scores → JobResults
    stubs.py                        # Shared fallback stubs for eval-hub-sdk types
  agent/
    base.py                         # EXISTING — EvalRunner ABC, RunResult (no changes)
    claude_code.py                  # EXISTING — ClaudeCodeRunner (no changes)
  config.py                         # EXISTING — EvalConfig (no changes)

provider/                           # NEW — Provider definition + container build
  provider.yaml                     # EvalHub provider definition (benchmarks, metrics, runtime)
  Containerfile                     # UBI9 container image build
  entrypoint.py                     # Container entrypoint (loads config, runs adapter)

tests/
  test_config_translator.py         # NEW — eval.yaml → provider config translation
  test_results_mapper.py            # NEW — RunResult → JobResults mapping
  test_s3_dataset.py                # NEW — S3 dataset download
  test_adapter.py                   # NEW — Adapter integration (mocked runner)
```

---

### Task 1: Add eval-hub-sdk dependency

**Files:**
- Modify: `pyproject.toml`

- [x] **Step 1: Add evalhub optional dependency group**

```toml
[project.optional-dependencies]
mlflow = ["mlflow[genai]>=3.5"]
anthropic = ["anthropic[vertex]>=0.40"]
evalhub = ["eval-hub-sdk[adapter]>=0.1", "boto3>=1.34"]
all = [
    "mlflow[genai]>=3.5",
    "anthropic[vertex]>=0.40",
    "eval-hub-sdk[adapter]>=0.1",
    "boto3>=1.34",
]
test = ["pytest>=8.0"]
```

In `pyproject.toml`, add `evalhub` to `[project.optional-dependencies]` and update `all` to include both new packages. `boto3` is needed for S3 dataset access.

- [x] **Step 2: Verify installation**

Run: `uv pip install -e ".[evalhub,test]"`
Expected: Installs eval-hub-sdk and boto3 without errors.

- [x] **Step 3: Verify imports**

Run: `python3 -c "from evalhub.adapter import FrameworkAdapter, JobSpec, JobCallbacks, JobResults, EvaluationResult; print('OK')"`
Expected: `OK`

- [x] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "feat: add eval-hub-sdk and boto3 dependencies for EvalHub provider"
```

---

### Task 2: Config translator — eval.yaml to EvalHub provider definition

Translates an `EvalConfig` (from eval.yaml) into the EvalHub provider YAML format. This is the bridge between agent-eval-harness's config model and EvalHub's provider/benchmark schema.

**Files:**
- Create: `agent_eval/evalhub/__init__.py`
- Create: `agent_eval/evalhub/config_translator.py`
- Create: `tests/test_config_translator.py`

- [x] **Step 1: Create package init**

```python
# agent_eval/evalhub/__init__.py
```

Empty file — just marks the directory as a package.

- [x] **Step 2: Write the failing test for provider config generation**

```python
# tests/test_config_translator.py
import pytest
from agent_eval.config import EvalConfig, JudgeConfig, OutputConfig
from agent_eval.evalhub.config_translator import eval_config_to_provider


def _make_config(**overrides):
    defaults = {
        "name": "rfe-review-eval",
        "description": "Evaluate RFE review skill",
        "skill": "rfe-creator:rfe-review",
    }
    defaults.update(overrides)
    cfg = EvalConfig(**defaults)
    return cfg


def test_basic_provider_generation():
    cfg = _make_config()
    cfg.judges = [
        JudgeConfig(name="completeness", check="return (True, 'ok')"),
        JudgeConfig(name="quality", prompt="Rate the output quality 1-5"),
    ]
    provider = eval_config_to_provider(cfg)

    assert provider["name"] == "agent-eval"
    assert provider["id"] == "agent-eval"
    assert len(provider["benchmarks"]) == 1

    bench = provider["benchmarks"][0]
    assert bench["id"] == "rfe-review-eval"
    assert bench["name"] == "rfe-review-eval"
    # Each judge becomes a metric
    metric_names = [m["name"] for m in bench["metrics"]]
    assert "completeness" in metric_names
    assert "quality" in metric_names
    # Built-in metrics always present
    assert "exit_code" in metric_names
    assert "duration_seconds" in metric_names
    assert "cost_usd" in metric_names


def test_primary_score_uses_first_judge():
    cfg = _make_config()
    cfg.judges = [
        JudgeConfig(name="accuracy", check="return (True, 'ok')"),
        JudgeConfig(name="style", prompt="Rate style"),
    ]
    provider = eval_config_to_provider(cfg)
    bench = provider["benchmarks"][0]
    assert bench["primary_score"]["metric"] == "accuracy"
    assert bench["primary_score"]["lower_is_better"] is False


def test_pass_criteria_from_thresholds():
    cfg = _make_config()
    cfg.judges = [JudgeConfig(name="accuracy", check="return (True, 'ok')")]
    cfg.thresholds = {"accuracy": {"min_pass_rate": 0.8}}
    provider = eval_config_to_provider(cfg)
    bench = provider["benchmarks"][0]
    assert bench["pass_criteria"]["threshold"] == 0.8


def test_no_judges_still_has_built_in_metrics():
    cfg = _make_config()
    provider = eval_config_to_provider(cfg)
    bench = provider["benchmarks"][0]
    metric_names = [m["name"] for m in bench["metrics"]]
    assert "exit_code" in metric_names
    assert "duration_seconds" in metric_names
```

- [x] **Step 3: Run test to verify it fails**

Run: `python3 -m pytest tests/test_config_translator.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent_eval.evalhub'`

- [x] **Step 4: Implement config_translator.py**

```python
# agent_eval/evalhub/config_translator.py
"""Translate EvalConfig (eval.yaml) to EvalHub provider definition."""

from agent_eval.config import EvalConfig


# Metrics that are always reported regardless of judges
_BUILTIN_METRICS = [
    {"name": "exit_code", "type": "integer"},
    {"name": "duration_seconds", "type": "float"},
    {"name": "cost_usd", "type": "float"},
    {"name": "num_turns", "type": "integer"},
    {"name": "num_examples_evaluated", "type": "integer"},
]


def eval_config_to_provider(config: EvalConfig) -> dict:
    """Convert an EvalConfig to an EvalHub provider definition dict.

    Returns a dict matching the EvalHub provider YAML schema:
    name, id, description, benchmarks[].
    """
    metrics = list(_BUILTIN_METRICS)
    for judge in config.judges:
        if not judge.name:
            continue
        metric_type = "boolean" if judge.check else "float"
        metrics.append({"name": judge.name, "type": metric_type})

    primary_score = {"metric": "exit_code", "lower_is_better": True}
    if config.judges:
        first_judge = config.judges[0]
        if first_judge.name:
            primary_score = {
                "metric": first_judge.name,
                "lower_is_better": False,
            }

    benchmark = {
        "id": config.name,
        "name": config.name,
        "description": config.description or f"Evaluate {config.skill}",
        "category": "agent-evaluation",
        "metrics": metrics,
        "primary_score": primary_score,
    }

    # Map thresholds to pass_criteria
    if config.thresholds:
        first_threshold = next(iter(config.thresholds.values()), {})
        threshold_val = (
            first_threshold.get("min_pass_rate")
            or first_threshold.get("min_mean")
            or first_threshold.get("min_win_rate")
        )
        if threshold_val is not None:
            benchmark["pass_criteria"] = {"threshold": threshold_val}

    return {
        "name": "agent-eval",
        "id": "agent-eval",
        "description": "Agent skill evaluation via agent-eval-harness",
        "benchmarks": [benchmark],
    }
```

- [x] **Step 5: Run test to verify it passes**

Run: `python3 -m pytest tests/test_config_translator.py -v`
Expected: All 4 tests PASS

- [x] **Step 6: Commit**

```bash
git add agent_eval/evalhub/__init__.py agent_eval/evalhub/config_translator.py tests/test_config_translator.py
git commit -m "feat: config translator — eval.yaml to EvalHub provider definition"
```

---

### Task 3: S3 dataset handler — download test cases from S3

EvalHub provides test data via S3 references in the `JobSpec.parameters`. The adapter downloads cases to a local directory matching the expected `dataset_path` layout.

**Files:**
- Create: `agent_eval/evalhub/s3_dataset.py`
- Create: `tests/test_s3_dataset.py`

- [x] **Step 1: Write the failing test**

```python
# tests/test_s3_dataset.py
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from agent_eval.evalhub.s3_dataset import download_dataset, DatasetInfo


def test_download_creates_case_dirs(tmp_path):
    """Verify S3 objects are downloaded into case subdirectories."""
    mock_s3 = MagicMock()
    # Simulate S3 listing: two cases, each with input.yaml
    mock_s3.list_objects_v2.return_value = {
        "Contents": [
            {"Key": "dataset/case-001/input.yaml"},
            {"Key": "dataset/case-001/annotations.yaml"},
            {"Key": "dataset/case-002/input.yaml"},
        ],
        "IsTruncated": False,
    }

    def fake_download(bucket, key, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(f"# from {key}")

    mock_s3.download_file.side_effect = fake_download

    info = download_dataset(
        s3_client=mock_s3,
        bucket="eval-data",
        prefix="dataset/",
        dest=tmp_path / "cases",
    )

    assert info.num_cases == 2
    assert (tmp_path / "cases" / "case-001" / "input.yaml").exists()
    assert (tmp_path / "cases" / "case-002" / "input.yaml").exists()
    assert (tmp_path / "cases" / "case-001" / "annotations.yaml").exists()


def test_download_empty_bucket(tmp_path):
    mock_s3 = MagicMock()
    mock_s3.list_objects_v2.return_value = {"IsTruncated": False}

    info = download_dataset(
        s3_client=mock_s3,
        bucket="eval-data",
        prefix="empty/",
        dest=tmp_path / "cases",
    )
    assert info.num_cases == 0
```

- [x] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_s3_dataset.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [x] **Step 3: Implement s3_dataset.py**

```python
# agent_eval/evalhub/s3_dataset.py
"""Download test case datasets from S3 for EvalHub jobs."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class DatasetInfo:
    """Summary of downloaded dataset."""
    num_cases: int
    case_ids: list
    dest: Path


def download_dataset(
    s3_client,
    bucket: str,
    prefix: str,
    dest: Path,
) -> DatasetInfo:
    """Download test cases from S3 into local case directories.

    Expects S3 layout: {prefix}/{case_id}/{file}
    Creates local layout: {dest}/{case_id}/{file}
    """
    dest.mkdir(parents=True, exist_ok=True)
    prefix = prefix.rstrip("/") + "/"

    objects = _list_all_objects(s3_client, bucket, prefix)
    case_ids = set()

    for key in objects:
        rel = key[len(prefix):]
        parts = rel.split("/", 1)
        if len(parts) < 2 or not parts[1]:
            continue
        case_id, filename = parts
        case_ids.add(case_id)
        local_path = dest / case_id / filename
        local_path.parent.mkdir(parents=True, exist_ok=True)
        s3_client.download_file(bucket, key, str(local_path))

    sorted_ids = sorted(case_ids)
    return DatasetInfo(
        num_cases=len(sorted_ids),
        case_ids=sorted_ids,
        dest=dest,
    )


def _list_all_objects(s3_client, bucket: str, prefix: str) -> list:
    """List all object keys under a prefix, handling pagination."""
    keys = []
    kwargs = {"Bucket": bucket, "Prefix": prefix}
    while True:
        resp = s3_client.list_objects_v2(**kwargs)
        for obj in resp.get("Contents", []):
            keys.append(obj["Key"])
        if not resp.get("IsTruncated"):
            break
        kwargs["ContinuationToken"] = resp["NextContinuationToken"]
    return keys
```

- [x] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_s3_dataset.py -v`
Expected: All 2 tests PASS

- [x] **Step 5: Commit**

```bash
git add agent_eval/evalhub/s3_dataset.py tests/test_s3_dataset.py
git commit -m "feat: S3 dataset handler for EvalHub test case download"
```

---

### Task 4: Results mapper — RunResult + judge scores to JobResults

Maps agent-eval-harness's `RunResult` and judge score dicts into EvalHub's `JobResults` and `EvaluationResult` objects.

**Files:**
- Create: `agent_eval/evalhub/results_mapper.py`
- Create: `tests/test_results_mapper.py`

- [x] **Step 1: Write the failing test**

```python
# tests/test_results_mapper.py
import pytest
from agent_eval.agent.base import RunResult
from agent_eval.evalhub.results_mapper import map_to_job_results


def test_basic_mapping():
    run_result = RunResult(
        exit_code=0,
        stdout="output",
        stderr="",
        duration_s=45.2,
        cost_usd=0.15,
        num_turns=12,
        token_usage={"input": 5000, "output": 2000},
    )
    judge_scores = {
        "accuracy": {"mean": 0.85, "pass_rate": None, "values": [0.8, 0.9]},
        "completeness": {"mean": None, "pass_rate": 1.0, "values": [True, True]},
    }
    job_results = map_to_job_results(
        job_id="job-123",
        benchmark_id="rfe-review-eval",
        model_name="claude-opus-4-7",
        run_result=run_result,
        judge_scores=judge_scores,
        num_cases=2,
    )

    assert job_results.id == "job-123"
    assert job_results.benchmark_id == "rfe-review-eval"
    assert job_results.model_name == "claude-opus-4-7"
    assert job_results.num_examples_evaluated == 2
    assert job_results.duration_seconds == pytest.approx(45.2)

    metric_map = {r.metric_name: r.metric_value for r in job_results.results}
    assert metric_map["exit_code"] == 0
    assert metric_map["cost_usd"] == pytest.approx(0.15)
    assert metric_map["duration_seconds"] == pytest.approx(45.2)
    assert metric_map["num_turns"] == 12
    assert metric_map["accuracy"] == pytest.approx(0.85)
    assert metric_map["completeness"] == pytest.approx(1.0)


def test_failed_run():
    run_result = RunResult(
        exit_code=1, stdout="", stderr="error", duration_s=5.0,
    )
    job_results = map_to_job_results(
        job_id="job-fail",
        benchmark_id="test",
        model_name="sonnet",
        run_result=run_result,
        judge_scores={},
        num_cases=0,
    )
    metric_map = {r.metric_name: r.metric_value for r in job_results.results}
    assert metric_map["exit_code"] == 1
    assert job_results.num_examples_evaluated == 0
```

- [x] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_results_mapper.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [x] **Step 3: Implement results_mapper.py**

```python
# agent_eval/evalhub/results_mapper.py
"""Map agent-eval-harness results to EvalHub JobResults."""

from agent_eval.agent.base import RunResult

try:
    from evalhub.adapter import JobResults, EvaluationResult
except ImportError:
    # Stub for testing without eval-hub-sdk installed
    from dataclasses import dataclass, field
    from typing import List, Optional

    @dataclass
    class EvaluationResult:
        metric_name: str = ""
        metric_value: float = 0.0
        metric_type: str = ""

    @dataclass
    class JobResults:
        id: str = ""
        benchmark_id: str = ""
        benchmark_index: int = 0
        model_name: str = ""
        results: list = field(default_factory=list)
        overall_score: float = 0.0
        num_examples_evaluated: int = 0
        duration_seconds: float = 0.0
        oci_artifact: str = None


def map_to_job_results(
    job_id: str,
    benchmark_id: str,
    model_name: str,
    run_result: RunResult,
    judge_scores: dict,
    num_cases: int,
    benchmark_index: int = 0,
) -> JobResults:
    """Convert RunResult + judge scores into an EvalHub JobResults object."""
    results = []

    # Built-in execution metrics
    results.append(EvaluationResult(
        metric_name="exit_code",
        metric_value=run_result.exit_code,
        metric_type="integer",
    ))
    results.append(EvaluationResult(
        metric_name="duration_seconds",
        metric_value=run_result.duration_s,
        metric_type="float",
    ))
    if run_result.cost_usd is not None:
        results.append(EvaluationResult(
            metric_name="cost_usd",
            metric_value=run_result.cost_usd,
            metric_type="float",
        ))
    if run_result.num_turns is not None:
        results.append(EvaluationResult(
            metric_name="num_turns",
            metric_value=run_result.num_turns,
            metric_type="integer",
        ))
    results.append(EvaluationResult(
        metric_name="num_examples_evaluated",
        metric_value=num_cases,
        metric_type="integer",
    ))

    # Judge metrics
    overall_score = 0.0
    judge_count = 0
    for judge_name, scores in judge_scores.items():
        value = scores.get("mean") or scores.get("pass_rate")
        if value is not None:
            results.append(EvaluationResult(
                metric_name=judge_name,
                metric_value=value,
                metric_type="float",
            ))
            overall_score += value
            judge_count += 1

    if judge_count > 0:
        overall_score /= judge_count

    return JobResults(
        id=job_id,
        benchmark_id=benchmark_id,
        benchmark_index=benchmark_index,
        model_name=model_name,
        results=results,
        overall_score=overall_score,
        num_examples_evaluated=num_cases,
        duration_seconds=run_result.duration_s,
    )
```

- [x] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_results_mapper.py -v`
Expected: All 2 tests PASS

- [x] **Step 5: Commit**

```bash
git add agent_eval/evalhub/results_mapper.py tests/test_results_mapper.py
git commit -m "feat: results mapper — RunResult + judge scores to EvalHub JobResults"
```

---

### Task 5: FrameworkAdapter implementation — the core adapter

The adapter orchestrates the full eval loop: download dataset from S3, stage workspace, execute skill via `ClaudeCodeRunner`, run judges via `score.py` functions, and return `JobResults`.

**Files:**
- Create: `agent_eval/evalhub/adapter.py`
- Create: `tests/test_adapter.py`

- [x] **Step 1: Write the failing test**

```python
# tests/test_adapter.py
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from agent_eval.agent.base import RunResult
from agent_eval.evalhub.adapter import AgentEvalAdapter


def _write_case(case_dir, input_data):
    case_dir.mkdir(parents=True, exist_ok=True)
    with open(case_dir / "input.yaml", "w") as f:
        yaml.dump(input_data, f)


def _make_eval_yaml(tmp_path, overrides=None):
    config = {
        "name": "test-eval",
        "skill": "test-skill",
        "execution": {"mode": "case", "arguments": "--id {id}"},
        "runner": {"type": "claude-code"},
        "dataset": {"path": "cases", "schema": "Each case has an id field"},
        "outputs": [{"path": "output", "schema": "Output files"}],
        "judges": [{"name": "basic", "check": "return (outputs.get('exit_code', 1) == 0, 'pass/fail')"}],
    }
    if overrides:
        config.update(overrides)
    path = tmp_path / "eval.yaml"
    with open(path, "w") as f:
        yaml.dump(config, f)
    return path


@patch("agent_eval.evalhub.adapter.ClaudeCodeRunner")
@patch("agent_eval.evalhub.adapter.download_dataset")
def test_adapter_runs_skill_and_scores(mock_download, mock_runner_cls, tmp_path):
    # Set up dataset
    cases_dir = tmp_path / "cases"
    _write_case(cases_dir / "case-001", {"id": "RFE-001"})
    _write_case(cases_dir / "case-002", {"id": "RFE-002"})

    from agent_eval.evalhub.s3_dataset import DatasetInfo
    mock_download.return_value = DatasetInfo(
        num_cases=2, case_ids=["case-001", "case-002"], dest=cases_dir
    )

    # Mock runner
    mock_runner = MagicMock()
    mock_runner.name = "claude-code"
    mock_runner.run_skill.return_value = RunResult(
        exit_code=0, stdout="done", stderr="", duration_s=30.0,
        cost_usd=0.10, num_turns=8,
    )
    mock_runner_cls.return_value = mock_runner

    # Create eval.yaml
    eval_yaml = _make_eval_yaml(tmp_path)

    # Create adapter
    adapter = AgentEvalAdapter(eval_config_path=str(eval_yaml))

    # Create mock JobSpec and JobCallbacks
    mock_spec = MagicMock()
    mock_spec.id = "job-123"
    mock_spec.benchmark_id = "test-eval"
    mock_spec.benchmark_index = 0
    mock_spec.model = "claude-opus-4-7"
    mock_spec.parameters = {
        "s3_bucket": "eval-data",
        "s3_prefix": "dataset/",
    }

    mock_callbacks = MagicMock()

    results = adapter.run_benchmark_job(mock_spec, mock_callbacks)

    assert results.id == "job-123"
    assert results.model_name == "claude-opus-4-7"
    assert results.num_examples_evaluated == 2
    assert results.duration_seconds > 0

    # Verify runner was called for each case
    assert mock_runner.run_skill.call_count == 2

    # Verify status was reported
    assert mock_callbacks.report_status.call_count >= 2


@patch("agent_eval.evalhub.adapter.ClaudeCodeRunner")
@patch("agent_eval.evalhub.adapter.download_dataset")
def test_adapter_handles_runner_failure(mock_download, mock_runner_cls, tmp_path):
    cases_dir = tmp_path / "cases"
    _write_case(cases_dir / "case-001", {"id": "RFE-001"})

    from agent_eval.evalhub.s3_dataset import DatasetInfo
    mock_download.return_value = DatasetInfo(
        num_cases=1, case_ids=["case-001"], dest=cases_dir
    )

    mock_runner = MagicMock()
    mock_runner.name = "claude-code"
    mock_runner.run_skill.return_value = RunResult(
        exit_code=1, stdout="", stderr="crashed", duration_s=2.0,
    )
    mock_runner_cls.return_value = mock_runner

    eval_yaml = _make_eval_yaml(tmp_path)
    adapter = AgentEvalAdapter(eval_config_path=str(eval_yaml))

    mock_spec = MagicMock()
    mock_spec.id = "job-fail"
    mock_spec.benchmark_id = "test-eval"
    mock_spec.benchmark_index = 0
    mock_spec.model = "sonnet"
    mock_spec.parameters = {"s3_bucket": "b", "s3_prefix": "p/"}

    mock_callbacks = MagicMock()

    results = adapter.run_benchmark_job(mock_spec, mock_callbacks)

    metric_map = {r.metric_name: r.metric_value for r in results.results}
    assert metric_map["exit_code"] == 1
```

- [x] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [x] **Step 3: Implement adapter.py**

```python
# agent_eval/evalhub/adapter.py
"""EvalHub FrameworkAdapter for agent skill evaluation."""

import os
import tempfile
import time
from pathlib import Path
from typing import Optional

import yaml

from agent_eval.agent.base import RunResult
from agent_eval.agent.claude_code import ClaudeCodeRunner
from agent_eval.config import EvalConfig
from agent_eval.evalhub.results_mapper import map_to_job_results
from agent_eval.evalhub.s3_dataset import download_dataset

try:
    import boto3
except ImportError:
    boto3 = None

try:
    from evalhub.adapter import (
        FrameworkAdapter,
        JobSpec,
        JobCallbacks,
        JobResults,
        JobStatusUpdate,
        JobStatus,
    )
except ImportError:
    # Stubs for testing without eval-hub-sdk
    class FrameworkAdapter:
        pass

    class JobSpec:
        pass

    class JobCallbacks:
        pass

    class JobResults:
        pass

    class JobStatusUpdate:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    class JobStatus:
        RUNNING = "running"
        COMPLETED = "completed"
        FAILED = "failed"


class AgentEvalAdapter(FrameworkAdapter):
    """EvalHub adapter that runs agent skill evaluations.

    Reuses agent_eval.agent runners and agent_eval.config for the
    eval loop. The adapter:
    1. Downloads test cases from S3
    2. Loads eval.yaml config
    3. Runs the skill against each case via ClaudeCodeRunner
    4. Scores outputs with configured judges
    5. Returns JobResults with metrics
    """

    def __init__(self, eval_config_path: str = "eval.yaml"):
        self._eval_config_path = eval_config_path

    def run_benchmark_job(self, config: JobSpec, callbacks: JobCallbacks) -> JobResults:
        start_time = time.monotonic()
        params = config.parameters or {}

        # Load eval config
        eval_config = EvalConfig.from_yaml(self._eval_config_path)

        # Report: starting
        callbacks.report_status(JobStatusUpdate(
            status=JobStatus.RUNNING,
            progress=0.0,
            message="Downloading dataset from S3",
        ))

        # Download dataset from S3
        with tempfile.TemporaryDirectory() as work_dir:
            work_path = Path(work_dir)
            cases_dir = work_path / "cases"

            s3_client = self._get_s3_client(params)
            dataset_info = download_dataset(
                s3_client=s3_client,
                bucket=params.get("s3_bucket", ""),
                prefix=params.get("s3_prefix", ""),
                dest=cases_dir,
            )

            if dataset_info.num_cases == 0:
                callbacks.report_status(JobStatusUpdate(
                    status=JobStatus.FAILED,
                    progress=0.0,
                    message="No test cases found in S3",
                ))
                return map_to_job_results(
                    job_id=config.id,
                    benchmark_id=config.benchmark_id,
                    model_name=config.model,
                    run_result=RunResult(exit_code=1, stdout="", stderr="No cases", duration_s=0),
                    judge_scores={},
                    num_cases=0,
                    benchmark_index=config.benchmark_index,
                )

            # Create runner
            runner = ClaudeCodeRunner(
                permissions=eval_config.permissions,
                effort=eval_config.runner.effort,
                plugin_dirs=eval_config.runner.plugin_dirs,
                env_strip=eval_config.runner.env_strip,
                system_prompt=eval_config.runner.system_prompt,
            )

            # Execute skill per case
            all_run_results = []
            for i, case_id in enumerate(dataset_info.case_ids):
                callbacks.report_status(JobStatusUpdate(
                    status=JobStatus.RUNNING,
                    progress=(i / dataset_info.num_cases),
                    message=f"Running case {i + 1}/{dataset_info.num_cases}: {case_id}",
                ))

                case_dir = cases_dir / case_id
                workspace = work_path / "workspaces" / case_id
                workspace.mkdir(parents=True, exist_ok=True)

                # Resolve arguments from input.yaml
                args = self._resolve_args(eval_config.execution.arguments, case_dir)

                run_result = runner.run_skill(
                    skill_name=eval_config.skill,
                    args=args,
                    workspace=workspace,
                    model=config.model,
                    timeout_s=eval_config.execution.timeout or 600,
                    max_budget_usd=eval_config.execution.max_budget_usd or 5.0,
                )
                all_run_results.append((case_id, run_result))

            # Aggregate results
            total_duration = time.monotonic() - start_time
            total_cost = sum(r.cost_usd or 0 for _, r in all_run_results)
            total_turns = sum(r.num_turns or 0 for _, r in all_run_results)
            exit_codes = [r.exit_code for _, r in all_run_results]

            aggregate_result = RunResult(
                exit_code=max(exit_codes) if exit_codes else 1,
                stdout="",
                stderr="",
                duration_s=total_duration,
                cost_usd=total_cost,
                num_turns=total_turns,
            )

            # Score with judges (import here to avoid circular deps)
            judge_scores = self._run_judges(eval_config, cases_dir, dataset_info.case_ids)

            callbacks.report_status(JobStatusUpdate(
                status=JobStatus.COMPLETED,
                progress=1.0,
                message=f"Completed {dataset_info.num_cases} cases",
            ))

            return map_to_job_results(
                job_id=config.id,
                benchmark_id=config.benchmark_id,
                model_name=config.model,
                run_result=aggregate_result,
                judge_scores=judge_scores,
                num_cases=dataset_info.num_cases,
                benchmark_index=config.benchmark_index,
            )

    def _resolve_args(self, template: str, case_dir: Path) -> str:
        """Resolve {field} placeholders from input.yaml."""
        if not template:
            return ""
        input_path = case_dir / "input.yaml"
        if not input_path.exists():
            return template
        with open(input_path) as f:
            data = yaml.safe_load(f) or {}
        result = template
        for key, value in data.items():
            result = result.replace(f"{{{key}}}", str(value))
        # Remove optional placeholders that weren't resolved
        import re
        result = re.sub(r"\{[^}]+\?\}", "", result)
        return result.strip()

    def _run_judges(self, config: EvalConfig, cases_dir: Path, case_ids: list) -> dict:
        """Run judges against collected case outputs.

        Returns dict of {judge_name: {mean, pass_rate, values}}.
        """
        try:
            # Import scoring functions from eval-run scripts
            import sys
            scripts_dir = Path(__file__).parent.parent.parent / "skills" / "eval-run" / "scripts"
            if str(scripts_dir) not in sys.path:
                sys.path.insert(0, str(scripts_dir))
            from score import load_judges, score_cases

            case_dirs = [cases_dir / cid for cid in case_ids if (cases_dir / cid).exists()]
            judges = load_judges(config)
            if not judges or not case_dirs:
                return {}
            results = score_cases(judges, case_dirs, config)
            return results.get("aggregated", {})
        except Exception:
            return {}

    @staticmethod
    def _get_s3_client(params: dict):
        """Create an S3 client from job parameters or environment."""
        if boto3 is None:
            raise RuntimeError("boto3 is required for S3 dataset access")
        kwargs = {}
        if params.get("s3_endpoint"):
            kwargs["endpoint_url"] = params["s3_endpoint"]
        if params.get("s3_region"):
            kwargs["region_name"] = params["s3_region"]
        return boto3.client("s3", **kwargs)
```

- [x] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_adapter.py -v`
Expected: All 2 tests PASS

- [x] **Step 5: Run all tests to check for regressions**

Run: `python3 -m pytest tests/ -v`
Expected: All tests PASS (existing + new)

- [x] **Step 6: Commit**

```bash
git add agent_eval/evalhub/adapter.py tests/test_adapter.py
git commit -m "feat: EvalHub FrameworkAdapter — agent skill evaluation provider"
```

---

### Task 6: Provider definition and container build

The provider YAML and Containerfile package the adapter for deployment on EvalHub.

**Files:**
- Create: `provider/provider.yaml`
- Create: `provider/Containerfile`
- Create: `provider/entrypoint.py`

- [x] **Step 1: Create provider definition**

```yaml
# provider/provider.yaml
name: agent-eval
id: agent-eval
title: Agent Skill Evaluation
description: >
  Evaluate AI coding agent skills (Claude Code, Agent SDK) against test case
  datasets with configurable judges. Measures output quality, execution cost,
  token usage, and regression detection.
version: "0.1.0"

benchmarks:
  - id: skill-eval
    name: skill-eval
    description: "Generic agent skill evaluation benchmark"
    category: agent-evaluation
    parameters:
      s3_bucket:
        type: string
        description: S3 bucket containing test cases
        required: true
      s3_prefix:
        type: string
        description: S3 key prefix for test case directory
        required: true
      eval_config:
        type: string
        description: Path to eval.yaml inside the container (default /config/eval.yaml)
        default: /config/eval.yaml
    metrics:
      - name: exit_code
        type: integer
      - name: duration_seconds
        type: float
      - name: cost_usd
        type: float
      - name: num_turns
        type: integer
      - name: num_examples_evaluated
        type: integer
    primary_score:
      metric: exit_code
      lower_is_better: true

runtime:
  k8s:
    image: quay.io/rhoai/agent-eval-provider:latest
    cpu_request: "500m"
    memory_request: "1Gi"
    cpu_limit: "2"
    memory_limit: "4Gi"
```

- [x] **Step 2: Create container entrypoint**

```python
#!/usr/bin/env python3
# provider/entrypoint.py
"""Container entrypoint for the agent-eval EvalHub provider."""

import sys
from pathlib import Path

from evalhub.adapter import JobSpec, DefaultCallbacks
from agent_eval.evalhub.adapter import AgentEvalAdapter


def main():
    config_path = "/config/job.json"
    if len(sys.argv) > 1:
        config_path = sys.argv[1]

    spec = JobSpec.from_file(config_path)
    eval_config = spec.parameters.get("eval_config", "/config/eval.yaml")

    adapter = AgentEvalAdapter(eval_config_path=eval_config)
    callbacks = DefaultCallbacks()

    results = adapter.run_benchmark_job(spec, callbacks)
    print(f"Completed: {results.num_examples_evaluated} examples, "
          f"overall_score={results.overall_score:.2f}")


if __name__ == "__main__":
    main()
```

- [x] **Step 3: Create Containerfile**

```dockerfile
# provider/Containerfile
FROM registry.access.redhat.com/ubi9/python-311:latest

USER 0

# Install Claude Code CLI
RUN curl -fsSL https://claude.ai/install.sh | sh

# Install Node.js (required by Claude Code)
RUN dnf install -y nodejs && dnf clean all

USER 1001

WORKDIR /app

# Install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir ".[evalhub]"

# Copy application code
COPY agent_eval/ agent_eval/
COPY provider/entrypoint.py .

ENTRYPOINT ["python3", "entrypoint.py"]
```

- [x] **Step 4: Commit**

```bash
git add provider/
git commit -m "feat: EvalHub provider definition and container build"
```

---

### Task 7: Integration test with mocked EvalHub job lifecycle

End-to-end test that exercises the full adapter flow with mocked S3 and runner, verifying the complete lifecycle from JobSpec to JobResults.

**Files:**
- Create: `tests/test_adapter_integration.py`

- [x] **Step 1: Write the integration test**

```python
# tests/test_adapter_integration.py
"""End-to-end adapter test with mocked infrastructure."""

from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest
import yaml

from agent_eval.agent.base import RunResult
from agent_eval.evalhub.adapter import AgentEvalAdapter


def _setup_eval_project(tmp_path):
    """Create a minimal eval project with config and dataset."""
    # eval.yaml
    config = {
        "name": "integration-test",
        "skill": "test-skill",
        "execution": {"mode": "case", "arguments": "--input {query}"},
        "dataset": {"path": "cases", "schema": "query field"},
        "outputs": [{"path": "output", "schema": "Result files"}],
        "judges": [
            {
                "name": "ran_successfully",
                "check": "return (outputs.get('exit_code', 1) == 0, 'exit check')",
            }
        ],
    }
    eval_path = tmp_path / "eval.yaml"
    with open(eval_path, "w") as f:
        yaml.dump(config, f)

    # Dataset cases
    for i, query in enumerate(["what is AI?", "explain TDD"], start=1):
        case_dir = tmp_path / "cases" / f"case-{i:03d}"
        case_dir.mkdir(parents=True)
        with open(case_dir / "input.yaml", "w") as f:
            yaml.dump({"query": query}, f)

    return eval_path


@patch("agent_eval.evalhub.adapter.ClaudeCodeRunner")
@patch("agent_eval.evalhub.adapter.download_dataset")
def test_full_lifecycle(mock_download, mock_runner_cls, tmp_path):
    eval_path = _setup_eval_project(tmp_path)

    from agent_eval.evalhub.s3_dataset import DatasetInfo
    cases_dir = tmp_path / "cases"
    mock_download.return_value = DatasetInfo(
        num_cases=2,
        case_ids=["case-001", "case-002"],
        dest=cases_dir,
    )

    mock_runner = MagicMock()
    mock_runner.name = "claude-code"
    mock_runner.run_skill.return_value = RunResult(
        exit_code=0, stdout="done", stderr="", duration_s=20.0,
        cost_usd=0.08, num_turns=6,
    )
    mock_runner_cls.return_value = mock_runner

    adapter = AgentEvalAdapter(eval_config_path=str(eval_path))

    mock_spec = MagicMock()
    mock_spec.id = "integration-job"
    mock_spec.benchmark_id = "integration-test"
    mock_spec.benchmark_index = 0
    mock_spec.model = "claude-sonnet-4-6"
    mock_spec.parameters = {"s3_bucket": "test", "s3_prefix": "cases/"}

    mock_callbacks = MagicMock()
    results = adapter.run_benchmark_job(mock_spec, mock_callbacks)

    # Verify results structure
    assert results.id == "integration-job"
    assert results.model_name == "claude-sonnet-4-6"
    assert results.num_examples_evaluated == 2
    assert results.duration_seconds > 0

    metric_map = {r.metric_name: r.metric_value for r in results.results}
    assert "exit_code" in metric_map
    assert "duration_seconds" in metric_map
    assert "cost_usd" in metric_map

    # Verify runner received correct arguments
    assert mock_runner.run_skill.call_count == 2
    first_call = mock_runner.run_skill.call_args_list[0]
    assert first_call.kwargs["skill_name"] == "test-skill"
    assert first_call.kwargs["model"] == "claude-sonnet-4-6"
    assert "what is AI?" in first_call.kwargs["args"]

    # Verify progress was reported
    status_calls = mock_callbacks.report_status.call_args_list
    assert len(status_calls) >= 3  # start + per-case + completion
```

- [x] **Step 2: Run the integration test**

Run: `python3 -m pytest tests/test_adapter_integration.py -v`
Expected: PASS

- [x] **Step 3: Run full test suite**

Run: `python3 -m pytest tests/ -v`
Expected: All tests PASS

- [x] **Step 4: Commit**

```bash
git add tests/test_adapter_integration.py
git commit -m "test: end-to-end adapter integration test"
```

---

### Task 8: Documentation — update CLAUDE.md and add provider README

**Files:**
- Modify: `CLAUDE.md`
- Create: `provider/README.md`

- [x] **Step 1: Add EvalHub section to CLAUDE.md**

Add the following after the `## Remaining Work` section in CLAUDE.md:

```markdown
## EvalHub Integration

The `agent_eval.evalhub` package provides a custom EvalHub provider that runs
agent skill evaluations on Red Hat OpenShift AI. The adapter:

- Implements `FrameworkAdapter` from `eval-hub-sdk`
- Downloads test cases from S3 via `s3_dataset.py`
- Translates `eval.yaml` to EvalHub provider definitions via `config_translator.py`
- Maps `RunResult` + judge scores to `JobResults` via `results_mapper.py`
- Ships as a UBI9 container image (`provider/Containerfile`)

### Local skills (unchanged)
eval-analyze, eval-dataset, eval-optimize, eval-review — authoring workflows

### EvalHub-managed (via provider)
Execution, MLflow tracking, result storage, regression detection, OCI export
```

- [x] **Step 2: Create provider README**

```markdown
# Agent Eval — EvalHub Provider

Custom EvalHub provider for evaluating AI coding agent skills.

## Build

```bash
podman build -f provider/Containerfile -t quay.io/rhoai/agent-eval-provider:latest .
```

## Register Provider

```bash
curl -X POST $EVALHUB_URL/api/v1/evaluations/providers \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d @provider/provider.yaml
```

## Submit Job

```bash
evalhub eval run --config job-config.yaml
```

## Configuration

The provider expects:
- `eval.yaml` mounted at `/config/eval.yaml` (via ConfigMap)
- Test cases in S3 (referenced via `s3_bucket` and `s3_prefix` parameters)
- Claude Code CLI available in the container
- `ANTHROPIC_API_KEY` or Vertex AI credentials
```

- [x] **Step 3: Commit**

```bash
git add CLAUDE.md provider/README.md
git commit -m "docs: add EvalHub provider documentation"
```

---

## Summary

| Task | What it builds | Tests |
|------|---------------|-------|
| 1 | Dependencies | Import check |
| 2 | Config translator | 4 unit tests |
| 3 | S3 dataset handler | 2 unit tests |
| 4 | Results mapper | 2 unit tests |
| 5 | FrameworkAdapter | 2 unit tests |
| 6 | Container build | Manual |
| 7 | Integration test | 1 e2e test |
| 8 | Documentation | — |

Total: ~11 automated tests, 8 tasks, 6 new files in `agent_eval/evalhub/`, provider packaging.

---

## Execution Log

**Executed:** 2026-04-27 via subagent-driven development (superpowers skill)

**Approach:** Tasks 1 executed first (dependency), then 2-4 in parallel (independent), then 5 (depends on 2-4), then 6-8 in parallel (independent).

**Commits (in order):**

| SHA | Message |
|-----|---------|
| `c9b24c0` | feat: add eval-hub-sdk dependency group for EvalHub integration |
| `50e10d9` | feat: config translator — eval.yaml to EvalHub provider definition |
| `c38aff6` | feat: S3 dataset handler for EvalHub test case download |
| `3b1d236` | feat: results mapper — RunResult + judge scores to EvalHub JobResults |
| `16a4a2d` | feat: EvalHub FrameworkAdapter — agent skill evaluation provider |
| `b5ba2fe` | test: end-to-end adapter integration test |
| `f3d5f13` | docs: add EvalHub provider documentation |
| `f93f222` | refactor: simplify evalhub package — deduplicate stubs, fix aggregation |

**Post-implementation simplification (`/simplify`):**
- Extracted shared fallback stubs to `stubs.py` (was duplicated in adapter.py and results_mapper.py)
- Fixed RunResult aggregation: properly sums cost/turns/duration across all cases
- Dropped unbounded `values` list from judge metadata
- Batch `mkdir` per-case instead of per-file in S3 download
- Cached dynamic `score.py` import (was reloading on every job)

**Deviations from plan:**
- Actual SDK signatures differ from plan: `JobSpec.model` is `ModelConfig` (not `str`), `JobStatusUpdate.message` is `MessageInfo` (not `str`). Subagents adapted at implementation time.
- `FrameworkAdapter.__init__()` loads `meta/job.json` from disk — adapter.py extracts this into a patchable `_framework_adapter_init()` for testability.
- Added `stubs.py` (not in original plan) during simplification to deduplicate fallback types.

**Test results:** 51 tests pass, 0 failures, 0 regressions.
