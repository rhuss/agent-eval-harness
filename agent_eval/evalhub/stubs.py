"""Fallback stubs for evalhub SDK types when eval-hub-sdk is not installed.

These are used in testing/CI and must match the real SDK's constructor
signatures closely enough that code using them works identically.
"""

from datetime import datetime
from typing import Any


class EvaluationResult:
    def __init__(
        self,
        metric_name: str = "",
        metric_value: float | int | str | bool = 0,
        metric_type: str = "",
        confidence_interval: tuple[float, float] | None = None,
        num_samples: int | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs,
    ):
        self.metric_name = metric_name
        self.metric_value = metric_value
        self.metric_type = metric_type
        self.confidence_interval = confidence_interval
        self.num_samples = num_samples
        self.metadata = metadata or {}


class JobResults:
    def __init__(
        self,
        id: str = "",  # noqa: A002 - mirrors SDK interface
        benchmark_id: str = "",
        benchmark_index: int = 0,
        model_name: str = "",
        results: list | None = None,
        overall_score: float | None = None,
        num_examples_evaluated: int = 0,
        duration_seconds: float = 0.0,
        completed_at: datetime | None = None,
        evaluation_metadata: dict[str, Any] | None = None,
        oci_artifact: Any | None = None,
        mlflow_run_id: str | None = None,
        **kwargs,
    ):
        self.id = id
        self.benchmark_id = benchmark_id
        self.benchmark_index = benchmark_index
        self.model_name = model_name
        self.results = results or []
        self.overall_score = overall_score
        self.num_examples_evaluated = num_examples_evaluated
        self.duration_seconds = duration_seconds
        self.completed_at = completed_at or datetime.now()
        self.evaluation_metadata = evaluation_metadata or {}
        self.oci_artifact = oci_artifact
        self.mlflow_run_id = mlflow_run_id


class MessageInfo:
    def __init__(self, message: str = "", message_code: str = "", **kwargs):
        self.message = message
        self.message_code = message_code


class ModelConfig:
    def __init__(self, url: str = "", name: str = "", auth: Any = None, **kwargs):
        self.url = url
        self.name = name
        self.auth = auth


class JobStatus:
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class JobPhase:
    INITIALIZING = "INITIALIZING"
    LOADING_DATA = "LOADING_DATA"
    RUNNING_EVALUATION = "RUNNING_EVALUATION"
    POST_PROCESSING = "POST_PROCESSING"
    PERSISTING_ARTIFACTS = "PERSISTING_ARTIFACTS"
    COMPLETED = "COMPLETED"


class JobStatusUpdate:
    def __init__(
        self,
        status=None,
        phase=None,
        progress=None,
        message=None,
        current_step=None,
        total_steps=None,
        completed_steps=None,
        error=None,
        timestamp=None,
    ):
        self.status = status
        self.phase = phase
        self.progress = progress
        self.message = message
        self.current_step = current_step
        self.total_steps = total_steps
        self.completed_steps = completed_steps
        self.error = error
        self.timestamp = timestamp


class JobSpec:
    def __init__(
        self,
        id="",  # noqa: A002 - mirrors SDK interface
        provider_id="",
        benchmark_id="",
        benchmark_index=0,
        model=None,
        parameters=None,
        callback_url="",
        num_examples=None,
        experiment_name="",
        tags=None,
        exports=None,
    ):
        self.id = id
        self.provider_id = provider_id
        self.benchmark_id = benchmark_id
        self.benchmark_index = benchmark_index
        self.model = model or ModelConfig()
        self.parameters = parameters or {}
        self.callback_url = callback_url
        self.num_examples = num_examples
        self.experiment_name = experiment_name
        self.tags = tags or {}
        self.exports = exports or {}


class _MlflowOpsStub:
    def save(self, results, job_spec, artifacts=None):
        return None


class JobCallbacks:
    def __init__(self):
        self.mlflow = _MlflowOpsStub()

    def report_status(self, update):
        pass

    def report_results(self, results):
        pass

    def create_oci_artifact(self, spec):
        return None


class FrameworkAdapter:
    def run_benchmark_job(self, config, callbacks):
        raise NotImplementedError
