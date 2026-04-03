You are analyzing a skill to understand what it does, what inputs it expects, and what artifacts it produces. This analysis will be used to generate an eval.yaml configuration.

## What to read

1. **The target SKILL.md** — read it completely
2. **Sub-skills** — if the skill invokes other skills (via the Skill tool or `/skill-name` references), read each sub-skill's SKILL.md recursively. Follow the full chain until you find the skills that actually produce the final output artifacts.
3. **Scripts** — read any Python scripts called via Bash
4. **Prompts and templates** — read any prompt files or templates referenced
5. **Test cases** — if there's a dataset directory, read one sample case to understand the input structure

The recursive part matters: a top-level skill might just orchestrate sub-skills, and the actual outputs (the files judges will score) come from a skill several levels deep. Follow sub-skill chains until you find the skills that produce the final artifacts — this is typically 2-5 levels. Cap at 5 levels to avoid circular references; if the chain goes deeper, summarize what you know. If a referenced sub-skill can't be found (maybe it's in a different plugin or the path is wrong), note it as unresolved in the `sub_skills` list and describe what you can infer from the reference — don't fabricate its contents.

## Report format

Report your findings as structured YAML between ```yaml markers, followed by a narrative explanation.

```yaml
purpose: "<one sentence describing what the full pipeline does>"

inputs:
  description: |
    <natural language description of what the skill expects as input —
     how cases are structured, what files or fields they contain>
  invocation: "<how the skill is invoked: /skill-name args>"

outputs:
  # File artifacts written to disk
  - path: "<directory where the pipeline writes final outputs>"
    description: |
      <what is produced here — file types, naming patterns, content
       structure. Describe what you actually observed, not generic patterns.>
  # Tool call side effects (if the skill calls external APIs)
  # - tool: "<tool name pattern, e.g. mcp__atlassian__create_issue>"
  #   description: |
  #     <what this tool call does and what fields in its input/output matter>

sub_skills:
  - name: "<sub-skill name>"
    purpose: "<what it does in the pipeline>"
    produces: "<what artifacts it writes, if any>"
  # List all sub-skills in pipeline order

flags:
  supported:
    - "--flag: what it does"
  headless: <true|false>
  dry_run: <true|false>

pipeline:
  - step: "<what happens first — which skill/script runs>"
  - step: "<what happens next>"
  # Trace through sub-skills, not just top-level steps

quality_criteria:
  deterministic:
    - "<things that can be checked with code — file existence, field validation, value ranges>"
  llm_judgment:
    - "<things that need LLM assessment — quality, completeness, accuracy>"

suggested_judges:
  - name: "<judge name>"
    type: "<check|llm>"
    description: |
      <what this judge evaluates and how>
    # For check type, include a working inline script:
    check: |
      <python snippet that takes outputs dict, returns (bool, str)>
    # For llm type, include evaluation instructions:
    prompt: |
      <what to evaluate and how to score>
```

## Narrative

After the YAML block, explain:
1. How the pipeline flows end-to-end (across all skill levels)
2. What a "good" output looks like vs a "bad" one
3. Any edge cases or failure modes you noticed
4. What evaluation criteria would be most valuable
5. Which sub-skill's outputs are the ones that matter for scoring
6. Which tools and scripts interact with external services — look for AskUserQuestion (needs auto-answers), MCP tools calling external APIs (Jira, Slack, etc.), AND Python scripts that import API clients or call external URLs. These all need `inputs.tools` entries in eval.yaml so headless eval can intercept them.

Be thorough but concise. Reference actual file paths and field names you observed — don't invent generic examples.
