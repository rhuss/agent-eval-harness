You are analyzing evaluation results combined with human feedback to identify actionable skill improvements.

## Input

You will be given:
1. Per-case judge scores (pass/fail with rationale)
2. Per-case human feedback (qualitative comments from the user)
3. Transcript analysis (if available — process issues, tool usage patterns)
4. The skill's SKILL.md content

## Analysis Framework

### 1. Judge-Human Alignment

For each case with human feedback:
- Did judges flag the same issues? (alignment — judges are working)
- Did the user flag issues judges missed? (gap — need new judges or better prompts)
- Did judges fail but the user said it's fine? (false positive — judge may be too strict)

### 2. Pattern Detection

Across all feedback:
- **Systematic issues**: Same complaint across multiple cases → skill-level fix needed
- **Edge cases**: One-off issues → may not warrant a skill change
- **Process issues**: Transcript shows the skill working inefficiently even when output is OK → instructions need clarifying

### 3. Improvement Suggestions

For each pattern, propose:
- **What to change**: Specific lines or sections in SKILL.md
- **Why**: Which cases and feedback support this change
- **Risk**: Could this change cause regressions on other cases?
- **New judges**: If the user consistently flagged something, should a judge check for it?

## Output Format

Present as a structured report:

1. **Summary**: N cases reviewed, M had feedback, K patterns identified
2. **Patterns**: Each pattern with supporting evidence (case IDs + quotes)
3. **Proposed changes**: Ranked by impact, each with before/after and reasoning
4. **Judge gaps**: Things the user caught that no judge checks for
