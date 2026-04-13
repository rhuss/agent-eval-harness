# Data Pipeline Reference

How data flows from dataset cases through execution, collection, and scoring.

## 1. Dataset → Workspace

**Input**: `dataset.path` from eval.yaml pointing to a directory of case subdirectories.

**What workspace.py does**:
- Reads each case directory. Finds the first `.yaml`/`.yml`/`.json` file and loads its full content.
- Builds `batch.yaml` — a list of one entry per case, each entry being the full parsed content of the input file. No field extraction — the entire input file content is included.
- Builds `case_order.yaml` — maps position to case ID (`[{case_id: "case-001-name"}, ...]`)
- Creates output directories from `outputs[].path` in eval.yaml
- Symlinks project resources (`.claude/`, `CLAUDE.md`, `scripts/`, `skills/`, `.context/`)
- If `inputs.tools` configured, generates `tool_handlers.yaml` and `.claude/settings.json` with hooks

**Workspace structure**:
```
/tmp/agent-eval/{run-id}/
  batch.yaml              # Full input data per case
  case_order.yaml         # Position → case ID mapping
  {output_dirs}/          # Empty dirs for skill outputs
  .claude/settings.json   # Generated hook config (if inputs.tools)
  hooks/tools.py          # Hook script (if inputs.tools)
  tool_handlers.yaml      # Handler config (if inputs.tools)
  scripts/ → symlink
  .claude/ → symlink (or generated)
  CLAUDE.md → symlink
  skills/ → symlink
```

## 2. Workspace → Execution

**What execute.py does**:
- Invokes the skill via the configured runner (e.g., `claude --print`)
- Passes `batch.yaml` content as the skill prompt via stdin
- Captures stdout (stream-json events) and stderr
- Writes `stdout.log` and `stderr.log` to `$AGENT_EVAL_RUNS_DIR/{id}/`
- Parses the final `result` event for token usage and cost
- Writes `run_result.json` with exit_code, duration_s, token_usage, cost_usd

**What the skill sees**:
- Working directory: the workspace
- Input: batch.yaml content (via skill prompt)
- Output directories: created and ready
- Hooks: if configured, AskUserQuestion gets auto-answered, external services get checked

## 3. Execution → Collection

**What collect.py does**:
- Reads `case_order.yaml` to know which case is at which position
- For each `outputs[].path` in eval.yaml, scans the workspace output directory
- Groups files by detected prefix pattern (e.g., "RFE-001", "RFE-002") or by position
- Copies each group's files to `$AGENT_EVAL_RUNS_DIR/{id}/cases/{case-id}/{output-path}/`
- Writes `collection.json` with per-case artifact counts

**Per-case output structure**:
```
$AGENT_EVAL_RUNS_DIR/{id}/
  run_result.json         # Execution metadata
  stdout.log              # Full stdout
  stderr.log              # Full stderr
  collection.json         # Per-case artifact counts
  cases/
    case-001-name/
      artifacts/          # Files from outputs[0].path
        RFE-001-slug.md
      artifacts/reviews/  # Files from outputs[1].path
        RFE-001-review.md
    case-002-name/
      artifacts/
        RFE-002-slug.md
```

## 4. Collection → Scoring

**What score.py's `load_case_record()` builds for each case**:

```python
{
    # --- File artifacts (from outputs with path) ---
    "files": {
        "artifacts/RFE-001-slug.md": "<full file content>",
        "artifacts/reviews/RFE-001-review.md": "<full file content>",
    },
    "artifacts_content": "<content of first file in artifacts/>",
    "artifacts_file": "/path/to/artifacts/RFE-001-slug.md",
    "reviews_content": "<content of first file in reviews/>",

    # --- Tool calls (from outputs with tool) ---
    "tool_calls": [
        {
            "name": "mcp__atlassian__create_issue",
            "input": {"title": "...", "description": "..."}
        }
    ],

    # --- Execution metadata (if traces.metrics enabled) ---
    "exit_code": 0,
    "duration_s": 45.2,
    "token_usage": {"input": 5000, "output": 2000},
    "cost_usd": 0.15,
    "num_turns": 12,

    # --- Logs (if traces.stdout/stderr enabled) ---
    "stdout": "<full stdout.log content>",
    "stderr": "<full stderr.log content>",

    # --- Context ---
    "case_dir": "/absolute/path/to/case"
}
```

**Key naming convention for convenience keys**: for an output with `path: "artifacts/rfe-tasks"`, the convenience key is `rfe-tasks_content` (the last directory component + `_content`). For `path: "."`, the key is `main_content`.

## 5. Scoring → Judges

### Three judge types

**Inline check** (`check` field in eval.yaml):
- Python snippet wrapped in a function by `score.py`
- Receives the full record dict as `outputs` parameter
- Must return `(bool, str)` — pass/fail + rationale
- Example accessing file content: `outputs["artifacts_content"]`
- Example accessing traces: `outputs.get("cost_usd", 0)`
- Example accessing tool calls: `outputs.get("tool_calls", [])`

**LLM judge** (`prompt` or `prompt_file` field):
- Created via `mlflow.genai.judges.make_judge()`
- Receives the record as `outputs` kwarg
- `context` files are appended to the prompt
- `feedback_type` is optional — MLflow infers from response

**External code judge** (`module` + `function` field):
- Imported via `importlib` from the project
- Receives the record as `outputs` kwarg
- Can return `Feedback` object, `(bool, str)` tuple, or primitive

### How aggregation works

For each judge across all cases:
- **Boolean values**: aggregated as `pass_rate` (fraction True)
- **Numeric values**: aggregated as `mean`
- Results written to `summary.yaml` with `per_case` and `aggregated` sections

## Traces Configuration

| `traces` field | What it controls | Where data comes from | What judges access |
|----------------|------------------|-----------------------|-------------------|
| `stdout: true` | Capture full stdout | `$AGENT_EVAL_RUNS_DIR/{id}/stdout.log` | `outputs["stdout"]` |
| `stderr: true` | Capture full stderr | `$AGENT_EVAL_RUNS_DIR/{id}/stderr.log` | `outputs["stderr"]` |
| `events: false` | Capture stream-json events | Parsed from stdout | `outputs["tool_calls"]` (via `outputs.tool` config) |
| `metrics: true` | Capture execution metadata | `$AGENT_EVAL_RUNS_DIR/{id}/run_result.json` | `outputs["exit_code"]`, `["duration_s"]`, `["cost_usd"]`, `["num_turns"]`, `["token_usage"]` |

Note: `events` being false doesn't prevent tool call extraction — tool calls are extracted from stdout regardless if `outputs` has `tool:` entries. The `events` flag controls whether the full event stream is stored.
