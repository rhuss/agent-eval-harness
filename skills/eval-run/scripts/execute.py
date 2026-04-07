#!/usr/bin/env python3
"""Execute a skill headlessly via the configured agent runner.

Delegates to the agent_eval.agent abstraction so the same script works
with Claude Code, OpenCode, Agent SDK, or any other registered runner.

Usage:
    python3 ${CLAUDE_SKILL_DIR}/scripts/execute.py \\
        --workspace /tmp/agent-eval/test-001 \\
        --skill rfe.speedrun \\
        --skill-args "--input batch.yaml --headless --dry-run" \\
        --model opus \\
        --output eval/runs/test-001 \\
        [--agent claude-code] \\
        [--subagent-model sonnet] \\
        [--max-budget 100] \\
        [--timeout 3600] \\
        [--mlflow-experiment my-eval]
"""

import argparse
import json
import sys
from pathlib import Path

from agent_eval.agent import RUNNERS


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--skill", required=True)
    parser.add_argument("--skill-args", default=None,
                        help="Skill arguments (default: from eval.yaml skill_args)")
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--config", default="eval.yaml",
                        help="Path to eval.yaml (for permissions and runner_options)")
    parser.add_argument("--agent", default=None,
                        help="Agent runner override (default: from config)")
    parser.add_argument("--subagent-model", default=None)
    parser.add_argument("--max-budget", type=float, default=100.0)
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--mlflow-experiment", default=None)
    args = parser.parse_args()

    # Load config for permissions and runner_options
    from agent_eval.config import EvalConfig
    config = EvalConfig.from_yaml(args.config)

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Resolve skill args: CLI override > config > empty
    skill_args = args.skill_args if args.skill_args is not None else config.arguments

    # Resolve {prompt} placeholder from batch.yaml
    if skill_args and "{prompt}" in skill_args:
        batch_path = Path(args.workspace) / "batch.yaml"
        if batch_path.exists():
            import yaml as _yaml
            with open(batch_path) as _f:
                batch = _yaml.safe_load(_f) or []
            if isinstance(batch, list) and batch:
                entry = batch[0]
                prompt_text = entry.get("prompt", "") if isinstance(entry, dict) else str(entry)
                skill_args = skill_args.replace("{prompt}", prompt_text.strip())

    # Build runner
    agent = args.agent or config.runner
    if agent not in RUNNERS:
        print(f"ERROR: unknown runner '{agent}'. Available: {list(RUNNERS.keys())}",
              file=sys.stderr)
        sys.exit(1)
    runner_cls = RUNNERS[agent]

    runner = runner_cls(
        permissions=config.permissions,
        runner_options=config.runner_options,
        subagent_model=args.subagent_model,
        mlflow_experiment=args.mlflow_experiment,
        log_prefix="eval",
    )

    print(f"Executing: /{args.skill} {skill_args}", file=sys.stderr)
    print(f"Agent: {runner.name} | Model: {args.model}", file=sys.stderr)
    print(f"Workspace: {args.workspace}", file=sys.stderr)

    # Use workspace settings if generated (for tool interception hooks)
    workspace_settings = Path(args.workspace) / ".claude" / "settings.json"
    settings_path = workspace_settings if workspace_settings.exists() else None

    # Run via the abstraction
    result = runner.run_skill(
        skill_name=args.skill,
        args=skill_args,
        workspace=Path(args.workspace),
        model=args.model,
        settings_path=settings_path,
        max_budget_usd=args.max_budget,
        timeout_s=args.timeout,
    )

    # Save results
    if result.stdout:
        (output_dir / "stdout.log").write_text(result.stdout)
    if result.stderr:
        (output_dir / "stderr.log").write_text(result.stderr)

    # Extract the full model ID from the result event if available
    full_model = args.model
    if result.raw_output and isinstance(result.raw_output, dict):
        full_model = result.raw_output.get("model", args.model)

    run_meta = {
        "exit_code": result.exit_code,
        "duration_s": round(result.duration_s, 1),
        "token_usage": result.token_usage,
        "cost_usd": result.cost_usd,
        "num_turns": result.num_turns,
        "model": full_model,
        "subagent_model": args.subagent_model or "",
        "agent": runner.name,
    }
    with open(output_dir / "run_result.json", "w") as f:
        json.dump(run_meta, f, indent=2)

    print(f"EXIT: {result.exit_code}")
    print(f"DURATION: {result.duration_s:.0f}s")
    if result.cost_usd:
        print(f"COST: ${result.cost_usd:.2f}")

    sys.exit(result.exit_code)


if __name__ == "__main__":
    main()
