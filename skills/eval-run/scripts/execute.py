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

_HARNESS_SYSTEM_PROMPT = (
    "You are running inside an evaluation harness. Tool interception hooks "
    "and permission controls are in place for safety. You MUST NOT: "
    "rename, copy, or move scripts to bypass tool filters; "
    "modify .claude/settings.json or hook scripts; "
    "disable or work around any harness controls; "
    "use alternative paths or commands to avoid blocked operations. "
    "If a tool call is blocked, report it and continue with the remaining "
    "pipeline steps. Do not attempt workarounds."
)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--skill", required=True)
    parser.add_argument("--skill-args", default=None,
                        help="Skill arguments (default: from eval.yaml execution.arguments)")
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--config", default="eval.yaml",
                        help="Path to eval.yaml (for permissions and runner_options)")
    parser.add_argument("--agent", default=None,
                        help="Agent runner override (default: from config)")
    parser.add_argument("--subagent-model", default=None)
    parser.add_argument("--max-budget", type=float, default=None)
    parser.add_argument("--timeout", type=int, default=None)
    parser.add_argument("--mlflow-experiment", default=None)
    args = parser.parse_args()

    # Load config for permissions and runner_options
    from agent_eval.config import EvalConfig
    config = EvalConfig.from_yaml(args.config)

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Resolve skill args: CLI override > config > empty
    skill_args = args.skill_args if args.skill_args is not None else config.execution.arguments

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

    # Resolve timeout and budget: CLI override > runner_options > defaults
    opts = config.runner_options or {}
    timeout_s = args.timeout or opts.get("timeout", 3600)
    max_budget = args.max_budget or opts.get("max_budget_usd", 100.0)

    # Compose system prompt: runner_options.system_prompt (if any) + harness prompt.
    existing_prompt = ""
    if isinstance(config.runner_options, dict):
        existing_prompt = str(config.runner_options.get("system_prompt", "")).strip()
    system_prompt = "\n\n".join(p for p in [existing_prompt, _HARNESS_SYSTEM_PROMPT] if p)

    # ── Per-case execution ───────────────────────────────────────
    if config.execution.mode == "case":
        _execute_per_case(args, config, runner, output_dir, max_budget, timeout_s,
                          system_prompt)
        return

    # ── Batch execution (below) ──────────────────────────────────
    print(f"Executing: /{args.skill} {skill_args}", file=sys.stderr)
    print(f"Agent: {runner.name} | Model: {args.model}", file=sys.stderr)
    print(f"Workspace: {args.workspace}", file=sys.stderr)

    # Set MLflow environment in the workspace settings
    if args.mlflow_experiment:
        from agent_eval.mlflow.experiment import inject_tracing_env
        inject_tracing_env(args.workspace, project_root=Path.cwd(),
                           experiment_name=args.mlflow_experiment)

    workspace_settings = Path(args.workspace) / ".claude" / "settings.json"
    settings_path = workspace_settings if workspace_settings.exists() else None


    result = runner.run_skill(
        skill_name=args.skill,
        args=skill_args,
        workspace=Path(args.workspace),
        model=args.model,
        settings_path=settings_path,
        system_prompt=system_prompt,
        max_budget_usd=max_budget,
        timeout_s=timeout_s,
    )

    _save_result(result, args, output_dir, runner)
    sys.exit(result.exit_code)


def _resolve_arguments(template, case_data):
    """Resolve {field} and {field?} placeholders from case input data."""
    import re

    missing = []

    def _replace(m):
        field = m.group(1)
        optional = field.endswith("?")
        if optional:
            field = field[:-1]
        if field not in case_data:
            if optional:
                return ""
            missing.append(field)
            return ""
        value = case_data[field]
        if value is None or (isinstance(value, str) and not value.strip()):
            if optional:
                return ""
            missing.append(field)
            return ""
        return str(value).strip()

    result = re.sub(r'\{(\w+\??)\}', _replace, template).strip()
    if missing:
        raise ValueError(
            f"Missing required fields in input.yaml: {', '.join(missing)}. "
            f"Template: {template}")
    return result


def _execute_per_case(args, config, runner, output_dir, max_budget, timeout_s,
                      system_prompt=""):
    """Execute the skill once per case with case-specific arguments."""
    import yaml as _yaml

    workspace = Path(args.workspace)
    case_order_path = workspace / "case_order.yaml"
    if not case_order_path.exists():
        print("ERROR: no case_order.yaml in workspace", file=sys.stderr)
        sys.exit(1)

    with open(case_order_path) as f:
        case_order = _yaml.safe_load(f) or []

    print(f"Executing: /{args.skill} (per-case, {len(case_order)} cases)",
          file=sys.stderr)
    print(f"Agent: {runner.name} | Model: {args.model}", file=sys.stderr)

    case_results = {}
    worst_exit = 0

    for i, entry in enumerate(case_order, 1):
        case_id = entry["case_id"] if isinstance(entry, dict) else entry
        case_ws = workspace / "cases" / case_id

        if not case_ws.exists():
            print(f"  [{i}/{len(case_order)}] {case_id}: SKIP (workspace missing)",
                  file=sys.stderr)
            continue

        # Resolve per-case arguments from input.yaml
        case_args = config.execution.arguments
        input_path = case_ws / "input.yaml"
        if input_path.exists() and case_args:
            case_data = _yaml.safe_load(input_path.read_text()) or {}
            if isinstance(case_data, dict):
                case_args = _resolve_arguments(case_args, case_data)

        # Set MLflow environment per case workspace
        if args.mlflow_experiment:
            from agent_eval.mlflow.experiment import inject_tracing_env
            inject_tracing_env(str(case_ws), project_root=Path.cwd(),
                               experiment_name=args.mlflow_experiment)

        case_settings = case_ws / ".claude" / "settings.json"
        settings_path = case_settings if case_settings.exists() else None

        print(f"  [{i}/{len(case_order)}] {case_id}: /{args.skill} {case_args}",
              file=sys.stderr)

        result = runner.run_skill(
            skill_name=args.skill,
            args=case_args,
            workspace=case_ws,
            model=args.model,
            settings_path=settings_path,
            system_prompt=system_prompt,
            max_budget_usd=max_budget,
            timeout_s=timeout_s,
        )

        # Save per-case outputs
        case_output = output_dir / "cases" / case_id
        case_output.mkdir(parents=True, exist_ok=True)
        if result.stdout:
            (case_output / "stdout.log").write_text(result.stdout)
        if result.stderr:
            (case_output / "stderr.log").write_text(result.stderr)

        # Copy subagent transcripts
        ws_subagents = case_ws / "subagents"
        if ws_subagents.exists() and ws_subagents.is_dir():
            import shutil
            out_subagents = case_output / "subagents"
            out_subagents.mkdir(exist_ok=True)
            for f in ws_subagents.iterdir():
                if f.is_file() and not f.is_symlink() and f.suffix == ".jsonl":
                    shutil.copy2(f, out_subagents / f.name)

        case_results[case_id] = {
            "exit_code": result.exit_code,
            "duration_s": round(result.duration_s, 1),
            "token_usage": result.token_usage,
            "cost_usd": result.cost_usd,
            "num_turns": result.num_turns,
        }

        # Write per-case run_result.json so score.py can read
        # execution metadata per case (not just aggregate)
        with open(case_output / "run_result.json", "w") as f:
            json.dump(case_results[case_id], f, indent=2)
            f.write("\n")
        worst_exit = max(worst_exit, result.exit_code)

        status = "OK" if result.exit_code == 0 else f"FAIL (exit {result.exit_code})"
        print(f"    → {status} | {result.duration_s:.0f}s | "
              f"${result.cost_usd or 0:.2f}", file=sys.stderr)

    # Write aggregated run_result.json
    total_duration = sum(r["duration_s"] for r in case_results.values())
    total_cost = sum(r.get("cost_usd") or 0 for r in case_results.values())
    run_meta = {
        "exit_code": worst_exit,
        "duration_s": round(total_duration, 1),
        "cost_usd": round(total_cost, 2),
        "num_cases": len(case_results),
        "model": args.model,
        "agent": runner.name,
        "agent_version": getattr(runner, "version", ""),
        "execution_mode": "case",
        "per_case": case_results,
    }
    with open(output_dir / "run_result.json", "w") as f:
        json.dump(run_meta, f, indent=2)
        f.write("\n")

    print(f"EXIT: {worst_exit}")
    print(f"DURATION: {total_duration:.0f}s total")
    print(f"COST: ${total_cost:.2f} total")
    print(f"CASES: {len(case_results)} "
          f"({sum(1 for r in case_results.values() if r['exit_code'] == 0)} OK, "
          f"{sum(1 for r in case_results.values() if r['exit_code'] != 0)} FAIL)")

    sys.exit(worst_exit)


def _save_result(result, args, output_dir, runner):
    """Save batch execution results (stdout, stderr, run_result.json)."""
    if result.stdout:
        (output_dir / "stdout.log").write_text(result.stdout)
    if result.stderr:
        (output_dir / "stderr.log").write_text(result.stderr)

    # Copy subagent transcripts captured by the SubagentStop hook.
    # Only copy regular .jsonl files — reject symlinks (CWE-59).
    ws_subagents = Path(args.workspace) / "subagents"
    if ws_subagents.exists() and ws_subagents.is_dir():
        import shutil
        out_subagents = output_dir / "subagents"
        out_subagents.mkdir(exist_ok=True)
        for f in ws_subagents.iterdir():
            if f.is_file() and not f.is_symlink() and f.suffix == ".jsonl":
                shutil.copy2(f, out_subagents / f.name)

    full_model = result.resolved_model or args.model
    models_used = result.models_used or []
    subagent_models = [m for m in models_used if m != full_model]
    subagent_model_str = ", ".join(subagent_models) if subagent_models else full_model

    run_meta = {
        "exit_code": result.exit_code,
        "duration_s": round(result.duration_s, 1),
        "token_usage": result.token_usage,
        "cost_usd": result.cost_usd,
        "per_model_usage": result.per_model_usage,
        "num_turns": result.num_turns,
        "model": full_model,
        "subagent_model": subagent_model_str,
        "agent": runner.name,
        "agent_version": getattr(runner, "version", ""),
        "execution_mode": "batch",
    }
    run_result_path = output_dir / "run_result.json"
    with open(run_result_path, "w") as f:
        json.dump(run_meta, f, indent=2)
        f.write("\n")

    # Verify the file is valid JSON.

    with open(run_result_path) as f:
        json.load(f)

    print(f"EXIT: {result.exit_code}")
    print(f"DURATION: {result.duration_s:.0f}s")
    if result.cost_usd:
        print(f"COST: ${result.cost_usd:.2f}")


if __name__ == "__main__":
    main()
