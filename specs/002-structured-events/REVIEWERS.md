# Review Guide: Structured Event Stream for Judges

**Generated**: 2026-05-08 | **Spec**: [spec.md](spec.md)

## Why This Change

The eval harness has four independent JSONL parsers that all iterate the same `stdout.log` stream, each extracting different data at different times. This causes three problems:

1. **Repeated work**: `_extract_assistant_text()` runs once per LLM judge per case. With 5 judges and 20 cases, that's 100 redundant parses of the same file.
2. **Fragile extraction**: Each parser reimplements its own subset of JSONL field matching. Bugs get fixed in one parser but not the others.
3. **No structured access**: Judges that want to evaluate process quality (tool usage patterns, conversation flow, subagent behavior) have to write their own JSONL parsing from scratch.

The fix: parse once at collection time, produce a structured `events.json` per case, and give judges a clean `outputs["events"]` list instead of raw JSONL.

## What Changes

A single shared parser (`agent_eval/events.py`) replaces the scattered extraction logic. It runs once when `collect.py` processes results, writes `events.json` alongside existing artifacts, and loads the events into the case record for all judges.

The event list uses a **flat-with-tags structure**: root and subagent events in one ordered chronological list. Subagent events carry `parent_tool_use_id` and `agent_id` tags. Judges filter to root-only with `if not e.get("parent_tool_use_id")`. Subagent transcript files (`subagents/*.jsonl`) are merged into the event list, deduplicated by message ID against events already streamed in stdout.

For LLM judges, a new `{{ conversation }}` template variable renders root-level assistant text from events. Check judges access `outputs["events"]` as a typed list they can filter and iterate.

`outputs["stdout"]` is no longer included in the case record. Judges access execution data exclusively through `record["events"]`.

## Key Decisions

1. **Parse at collection, not scoring**: Events are parsed once and stored as `events.json`. This eliminates repeated parsing at scoring time and makes events inspectable on disk. (Alternative: lazy parsing in `load_case_record()`, rejected because events wouldn't be inspectable.)

2. **Clean removal of `record["stdout"]`**: No deprecation path. `stdout` was never a shipped API surface, so a clean cut avoids the complexity of maintaining error handling for a variable nobody uses.

3. **Flat event list with subagent tags**: All events (root + subagent) in one chronological list, subagent events tagged with `parent_tool_use_id` and `agent_id`. Matches Claude Code's native streaming format (>= 2.1.108). (Alternatives rejected: nesting forces recursion for simple queries; separate lists by agent loses chronological ordering.)

4. **Tool results included with size cap**: 50K chars default (configurable via `traces.event_result_cap`). Truncated results include `truncated: true` and `original_length` metadata so judges can detect truncation. Non-UTF-8 content gets a binary placeholder.

5. **`{{ conversation }}` naming**: Describes what it renders (root-level assistant conversation text). Distinct from `{{ events }}` (full event list) and `{{ transcript }}` (could confuse with subagent transcripts).

6. **Transcript deduplication by message ID**: Reuses the existing `seen_msg_ids` pattern from `stream_capture.py`. Events from subagent transcripts that were already streamed in stdout are skipped.

## Areas Needing Attention

- **stdout removal impact**: `outputs["stdout"]` is no longer populated. Since `{{ stdout }}` was never shipped as a public API, no migration path is needed, but judges using it in development will silently get `None` from `.get()` calls.

- **Event schema as contract**: Once `events.json` is written by collect.py and consumed by score.py, the schema becomes a versioning concern. Older files from previous runs may not be compatible if the schema evolves.

- **50K tool result cap**: Generous but arbitrary. Judges evaluating large file contents could lose data beyond the cap. The configurable `event_result_cap` mitigates this, but the default could surprise users.

- **`traces.events` defaulting to `true`**: Old eval runs without `events.json` will have `record["events"]` as an empty list when re-scored. Judges should handle this gracefully.

- **Subagent deduplication edge cases**: Claude Code >= 2.1.108 streams foreground subagent messages in stdout, but background agents only appear in transcript files. Both get `parent_tool_use_id` and `agent_id` tags. The distinction is transparent to judges, but deduplication must handle both cases correctly.

- **Atomic writes and schema validation**: `events.json` is written atomically (temp + rename) to prevent corruption from crashes. On load, malformed files fall back to `[]` with a warning rather than crashing the scorer.

## Scope Boundaries

**In scope**:
- New `agent_eval/events.py` shared parser module
- Collection-time parsing into `events.json` per case
- Subagent events in flat list with `parent_tool_use_id` and `agent_id` tags
- Subagent transcript file merging with deduplication
- Loading events into `record["events"]` for all judge types
- New `{{ conversation }}` template variable for LLM judges
- Removal of `record["stdout"]` from the case record (clean cut, no deprecation path)
- Replacement of `_extract_tool_calls()` and `_extract_assistant_text()` with event lookups
- Tool result content capping (configurable via `traces.event_result_cap`)
- `traces.events` config flag (default: true)

**Out of scope**:
- `trace_builder.py` refactoring (different shape, MLflow-specific)
- `extract_usage()` in `stream_capture.py` (runs at execution time, different lifecycle)
- Changes to raw `stdout.log` file retention on disk (stays as-is)

## Open Questions

- Whether `extract_usage()` should eventually adopt the shared parser (different lifecycle, runs at execution time)

## Review Checklist

- [ ] Key decisions are justified
- [ ] stdout removal is clean (no residual error handling or deprecation code)
- [ ] Event schema is stable enough to be a contract
- [ ] Scope matches the stated boundaries
- [ ] Success criteria are achievable
- [ ] No unstated assumptions
- [ ] Backward compatibility for `outputs["tool_calls"]` is maintained
- [ ] Edge cases (empty stdout, non-JSONL, subagent filtering, deduplication) are tested
- [ ] Subagent events correctly tagged with `parent_tool_use_id` and `agent_id`
- [ ] `{{ conversation }}` renders root-only text (no subagent text)
- [ ] Atomic writes for events.json (temp + rename)
- [ ] Schema validation on events.json load (malformed -> [] with warning)
- [ ] Non-UTF-8 tool results handled (binary placeholder, no crash)
- [ ] Truncation metadata (`truncated`, `original_length`) present on capped results

---

## Updates from Spec Review

Changes made based on reviewer feedback on PR #58 (2026-05-09):

### Reviewer: @astefanutti

1. **`events` naming confirmed** (line 12): Antonin asked whether `outputs["turns"]` would be closer conceptually. Decision: keep `events`. The list contains system init events, tool results, and result summaries, not just conversational turns. `turns` implies alternating user/assistant exchanges, but our data model includes non-turn items. `events` also matches Claude Code's stream-json terminology.

2. **`{{ stdout }}` error handling removed** (line 49): Antonin suggested we may not need to cover the `{{ stdout }}` error case. Agreed: `{{ stdout }}` was only introduced in the latest commit and nobody is using it. Removed FR-006, SC-006, the acceptance scenario, T011, and T013 entirely. Clean cut with no migration path or error handling for something that never shipped.

3. **Self-hosting eval noted for future** (line 20): Antonin asked whether this is the time to use agent-eval-harness for its own e2e tests. Out of scope for this PR, but structured events would make this easier since judges could inspect the harness's own tool call patterns. Noted as a future opportunity.

### Summary of spec changes

| Item | Before | After |
|------|--------|-------|
| FR-004 | MUST raise KeyError on `outputs["stdout"]` | MUST NOT include `record["stdout"]` |
| FR-006 | `{{ stdout }}` MUST raise error with migration message | Removed |
| SC-006 | Using `{{ stdout }}` produces clear error | Removed |
| US2 acceptance scenario 2 | `{{ stdout }}` raises error | Removed |
| T011 | Test for `{{ stdout }}` error | Removed |
| T013 | Implement `{{ stdout }}` error | Removed |
| T032 | Remove old test file | Removed |
| Total tasks | 32 | 29 |

<!-- Code phase sections are appended below this line by the phase-manager command -->
