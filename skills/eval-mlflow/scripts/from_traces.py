#!/usr/bin/env python3
"""Extract inputs from MLflow traces for dataset generation.

Searches production traces from the configured experiment and extracts
skill invocation inputs. Outputs YAML to stdout for the eval-dataset
agent to create case directories from.

Usage:
    python3 ${CLAUDE_SKILL_DIR}/scripts/from_traces.py \\
        --config eval.yaml \\
        [--experiment my-experiment] \\
        [--count 10] \\
        [--min-duration 5]
"""

import argparse
import os
import sys

import yaml

try:
    import mlflow
except ImportError:
    print("MLflow not installed. Install with: pip install 'mlflow[genai]'",
          file=sys.stderr)
    sys.exit(0)

mlflow.set_tracking_uri(os.environ.get("MLFLOW_TRACKING_URI", "http://127.0.0.1:5000"))

from agent_eval.config import EvalConfig
from agent_eval.mlflow.experiment import get_experiment_id
from agent_eval.mlflow.traces import extract_trace_inputs


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="eval.yaml")
    parser.add_argument("--experiment", default=None,
                        help="Experiment name (default: from eval.yaml)")
    parser.add_argument("--count", type=int, default=10,
                        help="Max traces to extract")
    args = parser.parse_args()

    config = EvalConfig.from_yaml(args.config)
    experiment_name = args.experiment or config.mlflow_experiment or config.name

    if not experiment_name:
        print("ERROR: no experiment name (set mlflow_experiment in eval.yaml "
              "or pass --experiment)", file=sys.stderr)
        sys.exit(1)

    # Search traces
    exp_id = get_experiment_id(experiment_name)
    if not exp_id:
        print(f"ERROR: experiment '{experiment_name}' not found", file=sys.stderr)
        sys.exit(2)

    try:
        traces = mlflow.search_traces(
            experiment_ids=[exp_id],
            max_results=args.count * 2,  # fetch extra in case some are filtered
        )
    except Exception as e:
        print(f"ERROR: failed to search traces: {e}", file=sys.stderr)
        sys.exit(1)

    if not traces:
        print(f"No traces found in experiment '{experiment_name}'", file=sys.stderr)
        sys.exit(2)

    # Extract inputs
    extracted = extract_trace_inputs(traces, max_results=args.count)

    if not extracted:
        print("No usable inputs extracted from traces", file=sys.stderr)
        sys.exit(2)

    # Output as YAML
    yaml.dump(extracted, sys.stdout, default_flow_style=False,
              allow_unicode=True, width=120)

    print(f"\n# Extracted {len(extracted)} inputs from {len(traces)} traces",
          file=sys.stderr)


if __name__ == "__main__":
    main()
