# Contract: Builtin Judge in eval.yaml

## eval.yaml Schema Extension

```yaml
judges:
  - name: <user_defined_name>         # Required: user-defined identifier for thresholds/reports
    builtin: <builtin_judge_name>     # Required: triggers resolution of the builtin judge
    description: <string>             # Optional: overrides the judge's default description
    if: <python_expr>                 # Optional: skip judge if expression returns False
    model: <model_id>                 # Optional: override models.judge for LLM builtins
    config:                           # Optional: passed to judge (Python: second arg, LLM: template var)
      <key>: <value>
```

**Trust boundary**: `eval.yaml` is a repository-controlled, trusted configuration file. The `if` field is evaluated using `eval(expr, {"__builtins__": {}}, {"annotations": ..., "outputs": ...})`, which disables all Python builtins. Only the `annotations` and `outputs` dicts are available as local variables. No builtin functions (`len`, `str`, `int`, etc.) are accessible. This is an existing behavior shared with all judge types. Do not accept eval.yaml from untrusted sources.

## Examples

### Minimal Python builtin (no config)
```yaml
judges:
  - name: safety_check
    builtin: no_harmful_content
```

### Python builtin with config
```yaml
judges:
  - name: budget_check
    builtin: cost_budget
    config:
      max_cost_usd: 0.50
```

### LLM builtin with model override
```yaml
judges:
  - name: completeness
    builtin: output_completeness
    model: claude-sonnet-4-6
    config:
      strictness: high
```

### With condition
```yaml
judges:
  - name: tool_validation
    builtin: tool_call_validation
    if: "outputs.get('tool_calls', [])"
```

### Mixed with other judge types
```yaml
judges:
  - name: safety_check
    builtin: no_harmful_content

  - name: has_output
    check: |
      content = outputs.get("main_content", "")
      return (len(content) > 0, f"Content length: {len(content)}")

  - name: quality
    prompt: "Rate the output quality 1-5."
    model: claude-sonnet-4-6

thresholds:
  safety_check:
    min_pass_rate: 1.0
  quality:
    min_mean: 3.5
```

## Python Judge Function Contract

```python
def judge(outputs: dict, config: dict | None = None) -> tuple[bool, str]:
    """
    Args:
        outputs: Case record dict containing files, tool_calls, events,
                 annotations, execution metrics, and conversation text.
        config: Optional configuration dict from eval.yaml judge entry.
                None if no config specified.

    Returns:
        Tuple of (passed: bool, rationale: str).
        - passed: True if the case meets the judge's criteria
        - rationale: Human-readable explanation of the result
    """
```

## LLM Judge Prompt Contract

LLM judge files are Jinja2 templates (`.md`) with these variables available:

| Variable | Type | Description |
|----------|------|-------------|
| `outputs` | dict | Full case record (same as Python judge `outputs` argument) |
| `config` | dict | Configuration from eval.yaml (empty dict if not specified) |

The template is rendered, sent to the LLM (using `model` field or `models.judge` default), and the response is parsed into a `(bool, str)` result.

**Required output format**: The LLM response MUST contain a JSON object with `passed` (bool) and `rationale` (string) fields. The harness extracts the first JSON object from the response.

**Available Jinja filters**: Standard Jinja2 filters plus `tojson` for serializing dicts.

## Vendoring

To customize a builtin judge, copy the file and reference it using the appropriate field:

```yaml
# Vendored Python judge (was: builtin: cost_budget)
judges:
  - name: custom_budget
    module: eval/judges/cost_budget
    function: judge

# Vendored LLM judge (was: builtin: output_completeness)
judges:
  - name: custom_completeness
    prompt_file: eval/judges/output_completeness.md
```

## Error Conditions

| Condition | Error Message Pattern |
|-----------|----------------------|
| Unknown builtin name | `Unknown builtin judge '{name}'. Available: {sorted_names}` |
| Duplicate judge name | `Duplicate judge name '{name}' in eval.yaml` |
| Name collision across categories | `Builtin judge name collision: '{name}' found in both {cat1}/ and {cat2}/` |
| Mutually exclusive fields | `Judge '{name}': 'builtin' is mutually exclusive with {conflicting_fields}` (where `{conflicting_fields}` lists only the fields actually set) |
