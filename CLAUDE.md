# Agent Eval Harness

Generic evaluation framework for Claude Code skills projects. Uses MLflow as the backbone for tracing, evaluation, datasets, and reporting.

## Project Status

Phase 1 (core framework) and Phase 2 (scoring integration) are implemented. See `eval/plans/agent-eval-harness-design.md` in the rfe-creator project for the full design doc.

## Architecture

```
agent_eval/              # Python package (config, runner, state)
  config.py              # EvalConfig from eval.yaml
  state.py               # Shared state persistence (key-value store)
  agent/
    base.py              # EvalRunner ABC + RunResult
    claude_code.py       # Claude Code CLI runner (claude --print)
  mlflow/
    experiment.py        # MLflow experiment setup, server check, feedback logging
    datasets.py          # Dataset create/sync utilities
    traces.py            # Trace search and input extraction

skills/eval-setup/       # Skill: environment setup
  SKILL.md               # Dependencies, MLflow, API keys, directories
  scripts/
    check_env.py         # Preflight environment checks

skills/eval-analyze/     # Skill: bootstrap eval config
  SKILL.md               # Analyze skill, generate eval.yaml + eval.md
  scripts/
    find_skills.py       # Skill discovery (reads plugin.json for paths)
    validate_eval.py     # Config and memory validation
  prompts/
    analyze-skill.md     # Skill analysis prompt
    generate-eval-md.md  # eval.md generation prompt
  references/
    eval-yaml-template.md # Full eval.yaml template for generation

skills/eval-dataset/     # Skill: generate test cases
  SKILL.md               # Bootstrap, expand, or extract cases from traces

skills/eval-run/         # Skill: execute eval suite
  SKILL.md               # Prepare, execute, collect, score, report
  scripts/
    workspace.py         # Workspace creation, batch.yaml, symlinks
    execute.py           # Skill execution via agent runner
    collect.py           # Artifact collection + case mapping
    score.py             # Scoring: inline checks, LLM judges, pairwise, regression
    tools.py             # PreToolUse hook for tool interception
  prompts/
    analyze-results.md   # Results interpretation prompt
    comparison-judge.md  # Pairwise comparison judge prompt
  references/
    data-pipeline.md     # Dataset → workspace → execution → scoring flow
    tool-interception.md # Tool interception format and field reference

skills/eval-review/      # Skill: interactive human review
  SKILL.md               # Present results, collect feedback, propose changes
  prompts/
    review-results.md    # Analysis framework for feedback patterns

skills/eval-mlflow/      # Skill: MLflow integration
  SKILL.md               # Dataset sync, result logging, trace feedback
  scripts/
    sync_dataset.py      # Push cases to MLflow dataset registry
    log_results.py       # Log run params, metrics, artifacts to MLflow
    attach_feedback.py   # Push/pull feedback between harness and traces
    from_traces.py       # Extract inputs from production traces

skills/eval-optimize/    # Skill: automated refinement loop
  SKILL.md               # Composes with /eval-run via Skill tool
```

## How It Works

Skills projects create an `eval.yaml` config file with:
- `skill` — skill to evaluate
- `arguments` — arguments string passed to the skill invocation
- `runner` — agent runner (`claude-code`, etc.), `runner_options` for runner-specific settings
- `permissions` — `allow`/`deny` tool patterns for headless execution
- `dataset` — `path` to test cases directory, `schema` describing case structure in natural language
- `inputs.tools` — tool interception for headless eval: `match` describes what to intercept, `prompt` how to handle it
- `outputs` — list of artifact dirs (`path`) and/or tool calls (`tool`) with natural language schemas
- `traces` — execution data to capture: stdout/stderr, events, metrics (exit code, tokens, cost)
- `judges` — inline `check` scripts, LLM `prompt`/`prompt_file`, external `module`/`function`
- `thresholds` — regression detection per judge

Runs are stored in `$AGENT_EVAL_RUNS_DIR` (default `eval/runs`), configured during `/eval-setup`.

The `schema` descriptions are documentation for the LLM agents and judges. Scripts operate on file paths from eval.yaml directly — no extraction spec, no hardcoded field names.

## Usage

```
/eval-setup                            # Setup: dependencies, MLflow, API keys
/eval-analyze --skill my-skill         # Analyze: understand skill, generate eval.yaml
/eval-dataset                          # Dataset: generate test cases
/eval-run --model opus                 # Run: execute eval suite
/eval-review --run-id <id>             # Review: interactive human feedback + changes
/eval-mlflow --run-id <id>             # MLflow: sync dataset, log results
/eval-optimize --model opus            # Optimize: automated refinement loop
```

## Key Design Decisions

1. **Schema-driven** — dataset and output structures described in natural language in eval.yaml; agents and judges interpret them, scripts just move files
2. **Agent-agnostic runner** — `EvalRunner` ABC with `--agent` flag on execute.py; Claude Code included, extensible to OpenCode/Agent SDK
3. **Three judge types** — inline `check` scripts, LLM `prompt`/`prompt_file`, external `module`/`function`
4. **MLflow as separate skill** — `/eval-mlflow` handles dataset sync, result logging, trace feedback; eval-run works without it

## Remaining Work

- Skills and refinement loop (`/eval-optimize` implementation)
- MLflow tracing integration (extended transcript parser with subagent hierarchy)
- CI integration patterns
- Testing and documentation
- Publish to PyPI or marketplace
