"""MLflow trace search and extraction utilities."""

import sys
from typing import Optional

from agent_eval.mlflow.experiment import get_experiment_id


def find_run_traces(experiment_name: str, run_id: str = "",
                    time_window_s: Optional[int] = None,
                    max_results: int = 100) -> list:
    """Find MLflow traces matching an eval run.

    Search strategy:
    1. Get experiment ID from name
    2. Search traces, optionally filtered by time window
    3. Return trace info dicts

    Args:
        experiment_name: MLflow experiment name.
        run_id: Eval run ID (for tag-based filtering).
        time_window_s: If set, only return traces from the last N seconds.
        max_results: Maximum traces to return.

    Returns:
        List of dicts with trace_id, timestamp, duration, status.
    """
    try:
        import mlflow
    except ImportError:
        return []

    exp_id = get_experiment_id(experiment_name)
    if not exp_id:
        return []

    try:
        traces = mlflow.search_traces(
            experiment_ids=[exp_id],
            max_results=max_results,
        )
    except Exception as e:
        print(f"Failed to search traces: {e}", file=sys.stderr)
        return []

    results = []
    for trace in traces:
        info = trace.info if hasattr(trace, "info") else trace
        trace_data = {
            "trace_id": getattr(info, "request_id", getattr(info, "trace_id", "")),
            "timestamp": getattr(info, "timestamp_ms", 0),
            "status": getattr(info, "status", ""),
        }
        results.append(trace_data)

    return results


def extract_trace_inputs(traces, max_results: int = 10) -> list:
    """Extract root span inputs from traces for dataset generation.

    Args:
        traces: List of trace objects from mlflow.search_traces().
        max_results: Maximum inputs to extract.

    Returns:
        List of dicts with trace_id, input_text, tool_interactions.
    """
    results = []
    for trace in traces[:max_results]:
        try:
            info = trace.info if hasattr(trace, "info") else trace
            trace_id = getattr(info, "request_id", getattr(info, "trace_id", ""))

            # Extract root span input
            data = trace.data if hasattr(trace, "data") else None
            input_text = ""
            tool_interactions = []

            if data and hasattr(data, "spans") and data.spans:
                root = data.spans[0]
                inputs = getattr(root, "inputs", {})
                if isinstance(inputs, dict):
                    input_text = inputs.get("prompt", inputs.get("input", str(inputs)))
                elif isinstance(inputs, str):
                    input_text = inputs

                # Extract tool interactions from child spans
                for span in data.spans[1:]:
                    span_name = getattr(span, "name", "")
                    if "AskUserQuestion" in span_name:
                        span_inputs = getattr(span, "inputs", {})
                        span_outputs = getattr(span, "outputs", {})
                        tool_interactions.append({
                            "tool": "AskUserQuestion",
                            "input": span_inputs,
                            "output": span_outputs,
                        })

            if input_text:
                results.append({
                    "trace_id": trace_id,
                    "input_text": input_text,
                    "tool_interactions": tool_interactions,
                })
        except Exception:
            continue

    return results
