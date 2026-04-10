#!/usr/bin/env python3
"""Log eval run results to MLflow.

Reads summary.yaml and run_result.json, logs params, metrics,
artifacts, and per-case results table to an MLflow run.

Usage:
    python3 ${CLAUDE_SKILL_DIR}/scripts/log_results.py \\
        --run-id <id> \\
        --config eval.yaml
"""

import argparse
import json
import os
import sys
from pathlib import Path

import yaml

try:
    import mlflow
except ImportError:
    print("MLflow not installed. Install with: pip install 'mlflow[genai]'",
          file=sys.stderr)
    sys.exit(0)

from agent_eval.config import EvalConfig


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--config", default="eval.yaml")
    args = parser.parse_args()

    config = EvalConfig.from_yaml(args.config)
    runs_dir = Path(os.environ.get("AGENT_EVAL_RUNS_DIR", "eval/runs"))
    run_dir = runs_dir / args.run_id

    # Load summary
    summary_path = run_dir / "summary.yaml"
    if not summary_path.exists():
        print(f"ERROR: no summary found at {summary_path}", file=sys.stderr)
        sys.exit(1)

    with open(summary_path) as f:
        summary = yaml.safe_load(f) or {}

    # Load execution metadata
    run_result = {}
    run_result_path = run_dir / "run_result.json"
    if run_result_path.exists():
        with open(run_result_path) as f:
            run_result = json.load(f)

    # Set experiment
    experiment_name = config.mlflow_experiment or config.name
    mlflow.set_experiment(experiment_name)

    with mlflow.start_run(run_name=args.run_id):
        # Params
        params = {
            "skill": config.skill,
            "runner": config.runner,
            "run_id": args.run_id,
            "model": run_result.get("model", ""),
        }
        if run_result.get("agent"):
            params["agent"] = run_result["agent"]
        for key, value in params.items():
            if value:
                mlflow.log_param(key, value)

        # Execution metrics
        if run_result.get("duration_s"):
            mlflow.log_metric("duration_s", run_result["duration_s"])
        if run_result.get("cost_usd"):
            mlflow.log_metric("cost_usd", run_result["cost_usd"])

        # Judge metrics
        judges = summary.get("judges", {})
        metric_count = 0
        for judge_name, agg in judges.items():
            if isinstance(agg, dict):
                if agg.get("pass_rate") is not None:
                    mlflow.log_metric(f"{judge_name}/pass_rate", agg["pass_rate"])
                    metric_count += 1
                if agg.get("mean") is not None:
                    mlflow.log_metric(f"{judge_name}/mean", agg["mean"])
                    metric_count += 1

        # Tags — detect regressions from thresholds
        has_regressions = False
        if config.thresholds:
            # Check thresholds inline: compare judge aggregates against min values
            for judge_name, threshold in config.thresholds.items():
                agg = judges.get(judge_name, {})
                if not isinstance(agg, dict):
                    continue
                if "min_pass_rate" in threshold:
                    rate = agg.get("pass_rate")
                    if rate is not None and rate < threshold["min_pass_rate"]:
                        has_regressions = True
                if "min_mean" in threshold:
                    mean = agg.get("mean")
                    if mean is not None and mean < threshold["min_mean"]:
                        has_regressions = True
        mlflow.set_tag("regressions_detected", "yes" if has_regressions else "no")
        mlflow.set_tag("num_judges", str(len(judges)))

        # Artifact
        if summary_path.exists():
            mlflow.log_artifact(str(summary_path))

        # Per-case results table
        per_case = summary.get("per_case", {})
        if per_case:
            table_rows = []
            for case_id, case_results in per_case.items():
                if not isinstance(case_results, dict):
                    continue
                for judge_name, result in case_results.items():
                    if not isinstance(result, dict):
                        continue
                    table_rows.append({
                        "case_id": case_id,
                        "judge": judge_name,
                        "value": result.get("value"),
                        "rationale": str(result.get("rationale", ""))[:500],
                    })
            if table_rows:
                # log_table expects a dict of columns, not a list of rows
                columns = {}
                for key in table_rows[0]:
                    columns[key] = [row[key] for row in table_rows]
                mlflow.log_table(columns, artifact_file="per_case_results.json")

        mlflow_run_id = mlflow.active_run().info.run_id

    print(f"EXPERIMENT: {experiment_name}")
    print(f"RUN: {mlflow_run_id}")
    print(f"PARAMS: {len(params)}")
    print(f"METRICS: {metric_count}")
    print(f"TABLE: per_case_results ({len(per_case)} cases)")


if __name__ == "__main__":
    main()
