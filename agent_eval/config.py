"""Evaluation suite configuration loaded from eval.yaml files."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


def _validate_relative_path(value: str, field_name: str,
                            reject_root: bool = False) -> str:
    """Reject absolute or parent-traversing paths.

    Args:
        reject_root: If True, also reject "." (current directory).
            Used for output paths where "." would mean the project root
            and cleaning it would delete the entire project.
    """
    if not value:
        return value
    p = Path(value)
    if p.is_absolute() or ".." in p.parts:
        raise ValueError(f"{field_name} must be a relative path without '..': {value}")
    if reject_root and str(p) == ".":
        raise ValueError(
            f"{field_name} cannot be '.' (project root) — use a subdirectory. "
            f"Outputs must be in a named subdirectory so the harness can "
            f"identify, collect, and clean them without affecting the project.")
    return value


@dataclass
class OutputConfig:
    """One output source with a natural language schema.

    Output types (determined by which field is set):
    - path: file artifacts in a directory on disk
    - tool: tool calls to capture from stream-json events

    Batch collection (optional):
    - batch_pattern: maps output files to cases when the skill processes
      all cases in a single invocation.  Uses {n} as a 1-based batch
      index (e.g. "RFE-{n:03d}" → "RFE-001", "RFE-002").  Files whose
      name starts with the expanded prefix are assigned to that case.
      Use "*" for shared directories (copied to every case).
    """
    path: str = ""       # File artifacts directory
    tool: str = ""       # Tool call name/pattern to capture
    schema: str = ""
    batch_pattern: str = ""  # Batch collection pattern (empty = auto-detect)


@dataclass
class TracesConfig:
    """What execution traces to capture and make available to judges."""
    stdout: bool = True   # Capture stdout.log
    stderr: bool = True   # Capture stderr.log
    events: bool = False  # Capture full stream-json events
    metrics: bool = True  # Capture run_result.json metrics


@dataclass
class ToolInputConfig:
    """Handler for intercepting a tool during eval execution.

    The `match` field describes what to intercept in natural language.
    eval-analyze populates this based on skill analysis. eval-run resolves
    it to concrete patterns at workspace setup time.
    """
    match: str = ""           # Natural language: what to intercept (tools, scripts, APIs)
    prompt: str = ""          # Natural language instruction for how to handle
    prompt_file: str = ""     # External file with detailed instructions


@dataclass
class InputsConfig:
    """Tool interception configuration for headless execution."""
    tools: list = field(default_factory=list)  # List of ToolInputConfig


@dataclass
class ExecutionConfig:
    """How the skill is invoked against test cases.

    Modes:
    - case (default): one skill invocation per case, with case-specific
      arguments resolved from input.yaml fields via {field} placeholders.
    - batch: all cases in one invocation via batch.yaml.

    Arguments template placeholders:
    - {field} → substitutes the value of 'field' from input.yaml
    - {field?} → substitutes if present, omitted if missing
    """
    mode: str = "case"
    arguments: str = ""


@dataclass
class JudgeConfig:
    """Configuration for a single judge.

    Judge types (determined by which fields are set):
    - Inline check: `check` contains a Python snippet
    - LLM judge: `prompt` or `prompt_file` contains evaluation instructions
    - External code: `module` and `function` reference a Python callable
    """
    name: str = ""
    description: str = ""  # What this judge checks (context for LLM judges)
    # Inline code check (returns (bool, str))
    check: str = ""
    # LLM judge / pairwise
    prompt: str = ""
    prompt_file: str = ""
    context: list = field(default_factory=list)  # File paths loaded as supplementary context
    feedback_type: str = ""  # Optional: int, float, bool, str. Inferred if omitted.
    model: str = ""  # Override model for this judge (pairwise, LLM)
    # External code judge
    module: str = ""
    function: str = ""


@dataclass
class EvalConfig:
    """Complete evaluation suite configuration.

    Structure is schema-driven: dataset and output structures are described
    in natural language. The harness interprets these descriptions via LLM
    (once, cached) to drive prepare, collect, and score steps.
    """
    name: str = ""
    description: str = ""
    skill: str = ""
    runner: str = "claude-code"
    runner_options: dict = field(default_factory=dict)
    permissions: dict = field(default_factory=dict)
    mlflow_experiment: str = ""

    # Execution — how the skill is invoked
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)

    # Dataset — natural language schema + path
    dataset_path: str = ""
    dataset_schema: str = ""

    # Outputs — file artifacts and/or tool calls
    outputs: list = field(default_factory=list)

    # Inputs — tool interception for headless execution
    inputs: InputsConfig = field(default_factory=InputsConfig)

    # Traces — execution metadata to capture
    traces: TracesConfig = field(default_factory=TracesConfig)

    # Judges (inline checks, LLM, pairwise, external code)
    judges: list = field(default_factory=list)

    # Regression thresholds
    thresholds: dict = field(default_factory=dict)

    # Runtime overrides (set by CLI or skill, not config file)
    model: str = ""
    subagent_model: str = ""
    run_id: str = ""
    baseline: str = ""

    @classmethod
    def from_yaml(cls, path: str | Path) -> "EvalConfig":
        """Load config from a YAML file."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Config not found: {path}")

        with open(path) as f:
            raw = yaml.safe_load(f) or {}

        # Dataset
        dataset = raw.get("dataset", {})

        # Execution config
        exec_raw = raw.get("execution", {})
        execution = ExecutionConfig(
            mode=exec_raw.get("mode", "case"),
            arguments=exec_raw.get("arguments", ""),
        )

        config = cls(
            name=raw.get("name", path.stem),
            description=raw.get("description", ""),
            skill=raw.get("skill", ""),
            runner=raw.get("runner", "claude-code"),
            runner_options=raw.get("runner_options", {}),
            permissions=raw.get("permissions", {}),
            mlflow_experiment=raw.get("mlflow_experiment", raw.get("name", "")),
            execution=execution,
            dataset_path=_validate_relative_path(
                dataset.get("path", ""), "dataset.path"),
            dataset_schema=dataset.get("schema", ""),
        )

        # Outputs (path or tool)
        for i, o in enumerate(raw.get("outputs", [])):
            config.outputs.append(OutputConfig(
                path=_validate_relative_path(
                    o.get("path", ""), f"outputs[{i}].path",
                    reject_root=True),
                tool=o.get("tool", ""),
                schema=o.get("schema", ""),
                batch_pattern=o.get("batch_pattern", ""),
            ))

        # Inputs (tool interception)
        inputs_raw = raw.get("inputs", {})
        for t in (inputs_raw.get("tools") or []):
            config.inputs.tools.append(ToolInputConfig(
                match=t.get("match", ""),
                prompt=t.get("prompt", ""),
                prompt_file=t.get("prompt_file", ""),
            ))

        # Traces
        traces = raw.get("traces", {})
        if traces:
            config.traces = TracesConfig(
                stdout=traces.get("stdout", True),
                stderr=traces.get("stderr", True),
                events=traces.get("events", False),
                metrics=traces.get("metrics", True),
            )

        # Judges
        for j in raw.get("judges", []):
            config.judges.append(JudgeConfig(
                name=j.get("name", ""),
                description=j.get("description", ""),
                check=j.get("check", ""),
                prompt=j.get("prompt", ""),
                prompt_file=j.get("prompt_file", ""),
                context=j.get("context", []),
                feedback_type=j.get("feedback_type", ""),
                model=j.get("model", ""),
                module=j.get("module", ""),
                function=j.get("function", ""),
            ))

        # Thresholds
        config.thresholds = raw.get("thresholds", {})

        return config

    @property
    def project_root(self) -> Path:
        """Project root (where eval.yaml lives)."""
        return Path.cwd()
