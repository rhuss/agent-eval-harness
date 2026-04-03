# eval.yaml Template

Use this template when generating eval.yaml. Fill in every field from what you observed in the skill analysis and dataset exploration — never use placeholder text.

## Full Structure

```yaml
name: <project-name>
description: <one line: what is being evaluated>
skill: <skill-name>
arguments: <arguments passed to the skill invocation, from SKILL.md $ARGUMENTS>
runner: claude-code

# Permissions for headless execution
permissions:
  allow: []     # Tool patterns to allow (empty = all)
  deny: []      # Tool patterns to block (e.g., "mcp__*")

# Runner-specific options (ignored by other runners)
runner_options:
  # settings: eval/config/settings.json
  # env_strip: [JIRA_TOKEN]

# MLflow experiment (optional)
mlflow_experiment: <project>-eval

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
  - name: <descriptive_name>
    description: |
      <what this judge evaluates>
    prompt: |
      <evaluation instructions — define what each score level means>
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
  #   model: claude-sonnet-4-6

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

Keep check scripts short (under 15 lines). They receive an `outputs` dict with file contents, execution metadata (`exit_code`, `duration_s`, `cost_usd`, `num_turns`), tool calls (`tool_calls` list), and logs (`stdout`, `stderr`).

**LLM `prompt` judges** assess quality — things that need understanding:
- Completeness: does the output cover all requirements?
- Accuracy: is the content correct?
- Relevance: does it address the input?

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
