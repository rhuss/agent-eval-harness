"""MLflow experiment management utilities."""

import os
import shutil
import subprocess
import sys
from typing import Optional


def get_experiment_id(experiment_name: str) -> Optional[str]:
    """Get MLflow experiment ID by name.

    Returns:
        Experiment ID string, or None if not found or MLflow unavailable.
    """
    try:
        import mlflow
        exp = mlflow.get_experiment_by_name(experiment_name)
        return exp.experiment_id if exp else None
    except Exception:
        return None


def setup_experiment(experiment_name: str, tracking_uri: Optional[str] = None):
    """Create or set the MLflow experiment.

    Args:
        experiment_name: Name for the experiment.
        tracking_uri: MLflow tracking URI (default: from env or local).
    """
    try:
        import mlflow
    except ImportError:
        print("MLflow not installed. Install with: pip install 'mlflow[genai]'",
              file=sys.stderr)
        return

    if tracking_uri:
        mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(experiment_name)


def ensure_server(port: int = 5000) -> bool:
    """Check if MLflow server is reachable.

    Checks MLFLOW_TRACKING_URI if set, otherwise localhost:port.

    Returns:
        True if server is available.
    """
    import os
    import urllib.request
    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI", "")
    if tracking_uri and tracking_uri.startswith("http"):
        url = tracking_uri.rstrip("/") + "/api/2.0/mlflow/experiments/search"
    else:
        url = f"http://127.0.0.1:{port}/api/2.0/mlflow/experiments/search"
    try:
        urllib.request.urlopen(url, timeout=3)
        return True
    except Exception:
        return False


def setup_autolog(project_dir: str, tracking_uri: str = "http://127.0.0.1:5000",
                  experiment_name: str = ""):
    """Configure MLflow autolog for Claude Code in a project directory.

    Runs `mlflow autolog claude` to set up the Stop hook in .claude/settings.json.
    """
    cmd = ["python3", "-m", "mlflow", "autolog", "claude", project_dir,
           "-u", tracking_uri]
    if experiment_name:
        cmd.extend(["-n", experiment_name])

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Failed to setup MLflow autolog: {result.stderr}", file=sys.stderr)
        return False

    # MLflow generates the hook with bare "python" which may not exist outside
    # a virtualenv (e.g. macOS only has "python3"). Replace with the absolute
    # path to python3, which preserves the virtualenv if one is active at
    # setup time and works regardless of the hook's shell environment.
    python_path = shutil.which("python3") or shutil.which("python")
    settings_path = os.path.join(project_dir, ".claude", "settings.json")
    if python_path and os.path.exists(settings_path):
        with open(settings_path) as f:
            content = f.read()
        fixed = content.replace('"python -c', f'"{python_path} -c')
        if fixed != content:
            with open(settings_path, "w") as f:
                f.write(fixed)

    return True


def log_feedback(trace_id: str, name: str, value, source_type: str = "CODE",
                 source_id: str = "agent-eval", rationale: str = ""):
    """Log feedback to a trace."""
    try:
        import mlflow
        from mlflow.entities.assessment import AssessmentSource

        mlflow.log_feedback(
            trace_id=trace_id,
            name=name,
            value=value,
            source=AssessmentSource(source_type=source_type, source_id=source_id),
            rationale=rationale if rationale else None,
        )
    except Exception:
        pass
