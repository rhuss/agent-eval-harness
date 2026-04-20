# eval.yaml Template

Use this template when generating eval.yaml. Fill in every field from what you observed in the skill analysis and dataset exploration — never use placeholder text.

## Full Structure

```yaml
name: <project-name>
description: <one line: what is being evaluated>
skill: <skill-name>

# Execution — how the skill processes test cases (runner-agnostic)
#
# How to choose the mode:
# - case (default): the skill expects ONE input and runs a pipeline on it.
#   Signs: $ARGUMENTS is a single value (Jira key, prompt, file path),
#   the skill runs phases sequentially on that input, then exits.
#   Examples: /test-plan.create RHAISTRAT-1520, /rfe.create "problem..."
#
# - batch: the skill accepts a BATCH FILE and processes multiple items
#   in one invocation. Signs: the skill has --input flag that takes a
#   YAML list, it iterates over entries internally, it has batch-size
#   or parallelism controls (--batch-size, --parallel, --concurrency).
#   Parallelism options are a strong signal — if the skill manages
#   concurrency itself, it's doing batch processing.
#   Examples: /rfe.speedrun --input batch.yaml, /rfe.auto-fix --input ids.yaml
#
# When in doubt, use case — it's safer (each case gets full isolation).
execution:
  mode: case
  arguments: <argument template with {field} placeholders from input.yaml>
  # Case examples: "{prompt}", "{strat_key} {adr_file?}"
  # Batch example: "--input batch.yaml --headless --dry-run"
  # timeout: 3600           # Per-invocation wall-clock timeout (seconds)
  # max_budget_usd: 5.0     # Per-invocation cost cap

# Runner — agent harness + runner-specific knobs
runner:
  type: claude-code         # Discriminator: claude-code, opencode, etc.
  # settings: {}            # Runner-specific settings overrides
  # plugin_dirs: []         # Plugin dirs the evaluated skill needs
  # env_strip: [JIRA_TOKEN] # Env vars to remove before launching the runner
  # system_prompt: ""       # Appended to harness system prompt
  # effort: high            # Claude Code reasoning effort: low | medium | high | xhigh | max

# Models — defaults for each role (CLI flags override)
models:
  skill: <model-id>         # Required (or pass --model)
  # subagent: <model-id>    # Defaults to skill model
  judge: <model-id>         # Used by LLM and pairwise judges

# Permissions for headless execution
# The Skill tool requires explicit permission in --print mode.
# If the skill under test invokes sub-skills via the Skill tool
# (check its allowed-tools frontmatter for "Skill"), add "Skill"
# to the allow list — otherwise nested skill calls silently fail.
permissions:
  allow: []     # Tool patterns to allow (e.g., "Skill", "Write(artifacts/**)")
  deny: []      # Tool patterns to block (e.g., "mcp__*")

# MLflow logging target (optional)
mlflow:
  experiment: <project>-eval
  # tracking_uri: sqlite:///mlflow.db   # Override env var for self-contained runs
  # tags: { team: ml }

# Dataset — describe what you actually observed in the sample case
dataset:
  path: <path to cases directory>
  schema: |
    <natural language description of each case's structure>

# Inputs — tool interception for headless execution
inputs:
  tools:
    # Auto-answer user questions
    # - match: Questions asked to the user via AskUserQuestion.
    #   prompt: |
    #     Answer based on the test case context.
    #     Default: pick the first option or answer "yes".

    # Control external service access (MCP tools AND scripts)
    # - match: |
    #     Any interaction with Jira — whether via MCP tools
    #     or Bash scripts calling the Jira API.
    #   prompt: |
    #     Only allow if targeting a test instance or emulator.

# Outputs — what the skill produces (files on disk or tool calls)
outputs:
  # File artifacts on disk
  - path: <output directory>
    schema: |
      <natural language description of artifacts in this directory>
    # batch_pattern: "PREFIX-{n:03d}"  # For batch execution: {n} = 1-based case index
    #                                   # Use "*" for shared dirs (copied to all cases)

  # Tool call outputs (for side effects like API calls)
  # - tool: <tool_name_pattern>
  #   schema: |
  #     <what this tool call represents and what fields matter>

# Traces — execution data to capture for judges
traces:
  stdout: true     # Capture stdout.log
  stderr: true     # Capture stderr.log
  events: false    # Execution events: tool calls, reasoning, results (verbose)
  metrics: true    # Capture exit code, tokens, cost, duration

# Judges — evaluate output quality
judges:
  # Inline check: validate structure with code
  - name: <descriptive_name>
    description: |
      <what this judge checks and why it matters>
    check: |
      <python snippet — receives outputs dict, returns (bool, str)>

  # LLM judge: assess quality with a prompt
  # IMPORTANT: include {{ outputs }} so the LLM can see the skill's output files
  - name: <descriptive_name>
    description: |
      <what this judge evaluates>
    prompt: |
      <preamble — what to evaluate>
      {{ outputs }}
      <scoring criteria — define what each score level means>
    # Optional: supplementary context files
    # context:
    #   - eval/prompts/scoring-rubric.md

  # LLM judge with external prompt file
  # - name: <name>
  #   description: <what it checks>
  #   prompt_file: eval/prompts/quality-judge.md
  #   context:
  #     - eval/prompts/domain-guidelines.md

  # External code judge (for complex validation)
  # - name: <name>
  #   description: <what it checks>
  #   module: eval.judges.my_checker
  #   function: check_quality

  # Pairwise comparison (used with score.py pairwise --baseline <id>)
  # - name: pairwise
  #   description: Compare two runs and pick the better output
  #   prompt_file: eval/prompts/comparison-judge.md
  #   # model: <model-id>   # Optional override; default is models.judge

# Thresholds for regression detection
thresholds:
  <judge_name>:
    min_pass_rate: 1.0     # for boolean judges (check)
    # min_mean: 3.5        # for numeric judges (llm)
```

## Writing Good Schema Descriptions

The `dataset.schema` and `outputs[*].schema` fields are the most important part of eval.yaml. They drive the entire pipeline — agents and judges read them to understand the data.

**Good** — references actual file names, field names, and content:
```
Each case directory contains:
- input.yaml: YAML file with 'prompt' (the problem statement to send
  to the skill) and 'clarifying_context' (additional background).
- reference.md: Gold standard output, a markdown document with
  YAML frontmatter (title, status, priority) and sections for
  Summary, Problem Statement, and Acceptance Criteria.
- annotations.yaml: Expected scores and test metadata.
```

**Bad** — vague, no specific field names:
```
Cases contain input files and reference outputs.
```

The difference: a good schema lets judges write `outputs["main_content"]` knowing what to expect. A bad schema forces them to guess.

## Writing Good Judges

**Inline `check` judges** validate structure — things that can be verified deterministically:
- Files exist in the expected directories
- YAML/JSON fields are present and have valid values
- Counts, ranges, and formats are correct

Keep check scripts short (under 15 lines). They receive an `outputs` dict — **always use this dict to access files, never use `os.listdir()` or filesystem paths** (judges run in the project root, not the per-case output directory).

Key fields in `outputs`:
- `outputs["files"]` — dict of `{relative_path: file_content}`, e.g. `{"artifacts/rfe-tasks/RFE-001.md": "# Summary\n..."}`
- `outputs["case_dir"]` — absolute path to the per-case output directory
- `outputs["exit_code"]`, `outputs["duration_s"]`, `outputs["cost_usd"]`, `outputs["num_turns"]` — execution metadata
- `outputs["tool_calls"]` — list of captured tool calls
- `outputs["stdout"]`, `outputs["stderr"]` — captured logs

Example check judge — find files by path prefix and read their content:
```yaml
  - name: files_exist
    check: |
      files = outputs.get("files", {})
      tasks = [k for k in files if k.startswith("output_dir/") and k.endswith(".md")]
      if not tasks:
          return (False, "No output files found")
      return (True, f"{len(tasks)} files found")

  - name: valid_yaml_header
    check: |
      import yaml
      files = outputs.get("files", {})
      reviews = {k: v for k, v in files.items() if k.endswith("-review.md")}
      for fname, content in reviews.items():
          parts = content.split('---', 2)
          fm = yaml.safe_load(parts[1])
          if 'score' not in fm:
              return (False, f"{fname}: missing score")
      return (True, f"{len(reviews)} reviews valid")
```

**LLM `prompt` judges** assess quality — things that need understanding:
- Completeness: does the output cover all requirements?
- Accuracy: is the content correct?
- Relevance: does it address the input?

**IMPORTANT**: LLM judges only see what's in their prompt text. To include the skill's output files, use the `{{ outputs }}` template variable. The harness replaces it with all collected file contents (from `outputs[*].path` directories), formatted as markdown sections with file paths as headers. Without `{{ outputs }}`, the LLM receives only the raw prompt text and cannot see any output files.

Example:
```yaml
  - name: output_quality
    prompt: |
      Review the following outputs:

      {{ outputs }}

      Score on a 1-5 scale:
      ...
```

Be specific about scoring criteria. "Score 1-5" is too vague. Define what each level means:
```
Score 1: Missing most requirements, major errors
Score 2: Partially addresses the input, significant gaps
Score 3: Covers the basics but lacks depth or has minor errors
Score 4: Good coverage, well-structured, minor issues only
Score 5: Comprehensive, accurate, well-written
```

**How many judges**: aim for 2-4 inline checks + 1-2 LLM judges. Start lean — you can always add more in later iterations. Every judge needs a `description` field explaining what it checks.

**Naming**: use `snake_case` names (e.g., `files_exist`, `output_quality`). These names appear in `thresholds` and in scoring reports — keep them short and descriptive. Make sure threshold keys match judge names exactly.

## The --update Flow

When `--update` is set, preserve everything already in the file. Don't modify existing judges, schemas, thresholds, or permissions. Only add new top-level keys that don't exist yet (e.g., add `outputs` if missing, but don't touch an existing `outputs` section).
