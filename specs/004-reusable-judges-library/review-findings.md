# Deep Review Findings

**Date:** 2026-05-23
**Branch:** 004-reusable-judges-library
**Rounds:** 1
**Gate Outcome:** PASS
**Invocation:** quality-gate

## Summary

| Severity | Found | Fixed | Remaining |
|----------|-------|-------|-----------|
| Critical | 0 | 0 | 0 |
| Important | 5 | 5 | 0 |
| Minor | 12 | 0 | 12 |
| **Total** | **17** | **5** | **12** |

**Agents completed:** 5/5 (+ 1 external tool)
**Agents failed:** none

## Findings

### FINDING-1
- **Severity:** Important
- **Confidence:** 90
- **File:** skills/eval-run/scripts/score.py:551-552
- **Category:** correctness
- **Source:** correctness-agent
- **Round found:** 1
- **Resolution:** fixed (round 1)

**What is wrong:**
Jinja2 `arguments` rendering was only applied when `jc.prompt_file` was set, not when inline `prompt` was used with `arguments`. The condition `if jc.arguments and jc.prompt_file` meant a judge configured with `prompt:` (inline) plus `arguments:` would have unresolved `{{ arguments.xxx }}` placeholders.

**How it was resolved:**
Changed condition from `if jc.arguments and jc.prompt_file:` to `if jc.arguments:` so Jinja2 template rendering applies regardless of prompt source.

### FINDING-2
- **Severity:** Important
- **Confidence:** 85
- **File:** agent_eval/judges/__init__.py:41-49
- **Category:** architecture
- **Source:** architecture-agent (also reported by: coderabbit)
- **Round found:** 1
- **Resolution:** fixed (round 1)

**What is wrong:**
`discover()` didn't validate that loaded Python modules actually have a `judge` function, and didn't check for `None` from `spec_from_file_location`. Errors would surface later with confusing `AttributeError`.

**How it was resolved:**
Added null check for spec/loader and `hasattr` validation that the module defines a callable `judge` function. Errors now fail fast at discovery time with clear messages.

### FINDING-3
- **Severity:** Important
- **Confidence:** 85
- **File:** skills/eval-run/scripts/score.py:349-372,529-558
- **Category:** architecture
- **Source:** architecture-agent
- **Round found:** 1
- **Resolution:** resolved (by design)

**What is wrong:**
Two distinct LLM judge paths: builtin judges use `_call_llm_judge_for_bool` (expects `{"passed": bool}`), regular judges use `_make_anthropic_llm_judge` (expects `{"score": int}`).

**How it was resolved:**
This is by design. Builtin LLM judges produce pass/fail results (FR-002a), while regular LLM judges produce numeric scores for the existing evaluation pipeline. Different output schemas serve different purposes.

### FINDING-4
- **Severity:** Important
- **Confidence:** 90
- **File:** skills/eval-run/scripts/score.py:455-465
- **Category:** production-readiness
- **Source:** prodready-agent
- **Round found:** 1
- **Resolution:** fixed (round 1)

**What is wrong:**
Unhandled exception from `future.result()` in `score_cases` would crash the entire scoring run. One poisoned case directory would mean zero results.

**How it was resolved:**
Wrapped `future.result()` in try/except. On exception, the case is recorded as an error result with all judges showing the error, and scoring continues for remaining cases.

### FINDING-5
- **Severity:** Important
- **Confidence:** 90
- **File:** tests/test_score_builtin.py
- **Category:** test-quality
- **Source:** testquality-agent
- **Round found:** 1
- **Resolution:** fixed (round 1)

**What is wrong:**
No integration test for LLM builtin judges through `load_judges`. The entire code path for LLM builtin scorer creation (`_make_builtin_scorer` with `kind == "llm"`) had zero test coverage.

**How it was resolved:**
Added `test_builtin_llm_judge_creates_scorer` with mocked `_call_llm_judge_for_bool` that verifies template rendering with arguments produces the expected prompt content. Also added missing `test_mutual_exclusivity_prompt_file` test.

## Remaining Minor Findings

1. `__version__` metadata in judge files is not read by any code (dead metadata)
2. `tool_call_validation.py` error string heuristic (`"error" in result.lower()[:50]`) is fragile
3. `list_names()` returns flat names only, not FQN with category
4. LLM judge templates have no truncation strategy for large outputs
5. `tool_call_validation` silently skips non-dict entries but counts them as successes
6. CodeRabbit: `output_completeness.md` "Required fields" comment lists `conversation, files` but template uses `outputs`
7. CodeRabbit: `tool_call_validation.py` docstring lists `events` as required but implementation only uses `tool_calls`
8. Anthropic client created per invocation instead of reused (performance, not correctness)
9. `eval()` sandbox via `__builtins__: {}` is bypassable (mitigated by trust model)
10. Jinja2 `Environment()` could use `SandboxedEnvironment` for defense-in-depth
11. Name collision error message unclear for same-category collisions
12. Hardcoded `len(names) == 4` in registry test is brittle
