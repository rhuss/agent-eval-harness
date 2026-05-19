# Quickstart: Using Built-in Judges

## Add a built-in judge to your eval.yaml

```yaml
judges:
  - name: safety_check
    builtin: no_harmful_content

  - name: budget_check
    builtin: cost_budget
    config:
      max_cost_usd: 0.50

  - name: tool_validation
    builtin: tool_call_validation

  - name: completeness
    builtin: output_completeness
    model: claude-sonnet-4-6
    config:
      strictness: high

thresholds:
  safety_check:
    min_pass_rate: 1.0
  budget_check:
    min_pass_rate: 1.0
```

## Available judges

| Name | Category | Type | What it checks |
|------|----------|------|----------------|
| `no_harmful_content` | safety | Python | Agent output for harmful or dangerous content |
| `tool_call_validation` | process | Python | Tool calls complete successfully without errors |
| `cost_budget` | efficiency | Python | Execution cost within configurable threshold |
| `output_completeness` | quality | LLM | Output completeness and coverage via LLM evaluation |

## Customize a built-in judge

Copy the judge file and reference it using the standard field for its type:

**Python judges** (copy `.py`, use `module`/`function`):
```yaml
judges:
  - name: my_custom_cost_check
    module: eval.judges.cost_budget
    function: judge
```

**LLM judges** (copy `.md`, use `prompt_file`):
```yaml
judges:
  - name: my_custom_completeness
    prompt_file: eval/judges/output_completeness.md
    model: claude-sonnet-4-6
```
