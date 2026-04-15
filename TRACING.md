# MLflow Tracing for Claude Code Skills

The agent-eval-harness builds rich, hierarchical MLflow traces from Claude Code's stream-json output. The same tracing works outside the eval pipeline — use `claude-trace` to instrument any skill invocation in CI, cron jobs, or ad-hoc runs.

## Why: matching production and eval traces

Eval traces and production traces should look the same. When you debug a production failure, the trace should have the same structure (agent spans, tool calls, subagent nesting) as the eval trace you used to validate the skill. `claude-trace` produces identical traces to `/eval-mlflow`, so you can compare them directly in the MLflow UI.

Both `claude-trace` (production) and `/eval-mlflow` (eval pipeline) use the same `build_trace()` function from `agent_eval/mlflow/trace_builder.py`.

## Install

```bash
pip install -e "./agent-eval-harness[mlflow]"
```

This registers the `claude-trace` command and installs MLflow.

## Basic Usage

`claude-trace` is a drop-in replacement for `claude --print`. It passes all flags through to Claude Code and adds stream-json capture + MLflow tracing automatically.

```bash
# Before (no tracing)
echo "/rfe.speedrun --input batch.yaml --headless" | \
  claude --print --model opus

# After (same behavior + MLflow trace)
echo "/rfe.speedrun --input batch.yaml --headless" | \
  claude-trace --model opus
```

Or with the prompt as an argument:

```bash
claude-trace --model opus -p "/rfe.create 'Users need GPU autoscaling'"
```

## CI / Cron Job

```bash
#!/bin/bash
export MLFLOW_TRACKING_URI=https://mlflow.example.com
export MLFLOW_EXPERIMENT_NAME=rfe-creator-prod

claude-trace --model opus \
  -p "/rfe.speedrun --input batch.yaml --headless --dry-run" \
  --trace-dir runs/$(date +%Y%m%d)
```

Logs are saved to `runs/YYYYMMDD/`:
- `stdout.log` — full stream-json event log
- `run_result.json` — execution metadata (exit code, duration, cost, tokens, model, per-model usage)
- `subagents/*.jsonl` — background agent conversation files

The trace is pushed to MLflow automatically.

## Flags

| Flag | Description |
|------|-------------|
| `-p "<prompt>"` | Prompt as argument (alternative to stdin pipe) |
| `--experiment <name>` | MLflow experiment name (overrides `$MLFLOW_EXPERIMENT_NAME`) |
| `--trace-dir <path>` | Where to save logs (default: `tmp/trace-runs/<timestamp>`) |
| `--no-mlflow` | Capture logs but don't push trace to MLflow |
| All other flags | Passed through to `claude --print` (e.g., `--model`, `--max-budget-usd`) |

## Offline Capture + Later Push

Capture without an MLflow server, push later:

```bash
# Capture
echo "/rfe.speedrun --input batch.yaml" | \
  claude-trace --model opus --no-mlflow --trace-dir /tmp/my-run

# Push later (when MLflow is available)
python3 -m agent_eval.mlflow.trace_builder \
  --stdout /tmp/my-run/stdout.log \
  --run-result /tmp/my-run/run_result.json \
  --experiment rfe-creator-prod
```

## What the Trace Captures

The trace is a hierarchical span tree matching the full execution:

```
Root AGENT (skill invocation)
├── LLM (reasoning: "I'll create the RFE...")
├── TOOL Bash (python3 scripts/next_rfe_id.py)
├── TOOL Write (artifacts/rfe-tasks/RFE-001.md)
├── TASK "3 parallel agents"
│   ├── AGENT (review agent 1)
│   │   ├── LLM (scoring reasoning)
│   │   └── TOOL Write (RFE-001-review.md)
│   ├── AGENT (review agent 2)
│   └── AGENT (feasibility agent)
├── LLM (revision reasoning)
├── TOOL Edit (artifacts/rfe-tasks/RFE-001.md)
└── TOOL Bash (python3 scripts/submit.py)
```

Each span includes:
- **Timing** — wall-clock start/end from stream-json timestamps
- **Inputs** — tool arguments (commands, file paths, skill names)
- **Outputs** — tool results (truncated to 500 chars)
- **Metadata** — model, tokens, cost, session ID

## How It Works

```
claude-trace --model opus -p "/rfe.speedrun ..."
  │
  ├─ Separates its own flags (--experiment, --trace-dir, --no-mlflow)
  │  from claude flags (--model, -p, --max-budget-usd, etc.)
  │
  ├─ Adds: --print --output-format stream-json --verbose
  │
  ├─ Launches: claude --print --model opus --output-format stream-json ...
  │  └─ Prompt via stdin or -p (passed through)
  │
  ├─ Injects SubagentStop hook into .claude/settings.json
  │  └─ Hook copies each subagent's .jsonl transcript to trace-dir/subagents/
  │
  ├─ Captures stream-json output line by line:
  │  ├─ Injects synthetic user event (prompt — claude --print doesn't emit it)
  │  └─ Injects wall-clock timestamps on assistant events
  │
  ├─ After process exits:
  │  ├─ Saves stdout.log, run_result.json, subagents/*.jsonl
  │  ├─ Builds hierarchical MLflow trace via build_trace()
  │  └─ Pushes trace to MLflow (unless --no-mlflow)
  │
  └─ Exits with same exit code as claude
```

### How subagent transcripts are captured

Skills that use background agents (Agent tool) produce separate conversation files per subagent. Claude Code deletes these when the session ends. We use a **SubagentStop hook** to capture them:

1. `claude-trace` (and the eval pipeline) injects a `SubagentStop` hook into `.claude/settings.json`
2. When each subagent finishes, the hook fires synchronously and copies its `.jsonl` transcript to a known directory
3. The trace builder reads these files to create nested agent spans with tool calls and LLM reasoning

This is simpler than the alternative (in-flight file reading while the process runs). Session persistence stays on so transcript files exist when the hook fires; the session directory is cleaned up after execution.

### Why not the MLflow Stop hook?

Claude Code has a Stop hook mechanism (`mlflow autolog claude`) that creates traces automatically. We don't use it because:

1. **Fragmented traces** — the Stop hook fires once per Claude Code session. Skills with background agents produce N+1 fragmented traces instead of one consolidated trace.
2. **Missing prompt** — `claude --print` doesn't record the stdin prompt in the session.
3. **Missing timestamps** — assistant events lack wall-clock timestamps.

Instead, `claude-trace` captures the full stream-json output, injects synthetic events and timestamps, and builds one consolidated trace post-hoc.

## Architecture

```
agent_eval/
  agent/
    stream_capture.py      # Reusable stream-json processing
      ├─ make_prompt_event()    — synthetic user event for stdin prompt
      ├─ inject_timestamp()     — wall-clock timestamps on assistant events
      ├─ extract_usage()        — tokens, cost, turns, models, per-model breakdown
      ├─ count_subagent_turns() — count turns from captured subagent transcripts
      └─ setup_subagent_hook()  — SubagentStop hook for transcript capture
  mlflow/
    trace_builder.py       # Hierarchical trace builder
      ├─ build_trace()          — stream-json → MLflow trace dict
      ├─ log_trace()            — submit trace to MLflow server
      └─ make_span(), iso_to_ns(), summarize_tool_input()
  cli/
    trace_run.py           # claude-trace CLI entry point
```

The eval pipeline uses the same modules:
- `ClaudeCodeRunner` (in `agent_eval/agent/claude_code.py`) imports from `stream_capture`
- `/eval-mlflow` (in `skills/eval-mlflow/scripts/log_results.py`) imports from `trace_builder`

This means eval and production traces are structurally identical — same span hierarchy, same metadata, same timing approach.

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `MLFLOW_TRACKING_URI` | MLflow server URL | `http://127.0.0.1:5000` |
| `MLFLOW_EXPERIMENT_NAME` | Default experiment name | `Default` |
| `ANTHROPIC_API_KEY` | API key for Claude | — |
