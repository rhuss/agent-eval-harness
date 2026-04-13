#!/usr/bin/env python3
"""Log eval run results to MLflow.

Reads summary.yaml and run_result.json, logs params, metrics,
artifacts, per-case results table, and creates the main orchestrator
trace from stdout.log.  Also links all experiment traces to the run.

Usage:
    python3 ${CLAUDE_SKILL_DIR}/scripts/log_results.py \\
        --run-id <id> \\
        --config eval.yaml
"""

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml

try:
    import mlflow
    from mlflow import MlflowClient
except ImportError:
    print("MLflow not installed. Install with: pip install 'mlflow[genai]'",
          file=sys.stderr)
    sys.exit(0)

from agent_eval.config import EvalConfig

# Ensure tracking URI is set — default to localhost server (same as tracing hook)
_tracking_uri = os.environ.get("MLFLOW_TRACKING_URI", "http://127.0.0.1:5000")
mlflow.set_tracking_uri(_tracking_uri)


# ── Trace builder ────────────────────────────────────────────────────

def _iso_to_ns(ts_str):
    """Convert ISO 8601 timestamp string to nanoseconds since epoch."""
    from dateutil.parser import parse as _dt_parse
    return int(_dt_parse(ts_str).timestamp() * 1e9)


def _make_span(trace_id, parent_id, name, span_type, start_ns, end_ns,
               inputs=None, outputs=None, extra_attrs=None):
    """Create a span dict for the trace."""
    span_id = uuid.uuid4().bytes[:8].hex()
    attrs = {
        "mlflow.traceRequestId": json.dumps(trace_id),
        "mlflow.spanType": json.dumps(span_type),
    }
    if inputs is not None:
        attrs["mlflow.spanInputs"] = json.dumps(inputs)
    if outputs is not None:
        attrs["mlflow.spanOutputs"] = json.dumps(outputs)
    if extra_attrs:
        attrs.update(extra_attrs)
    return {
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_span_id": parent_id,
        "name": name,
        "start_time_unix_nano": start_ns,
        "end_time_unix_nano": end_ns,
        "events": [],
        "status": {"code": "STATUS_CODE_OK", "message": ""},
        "attributes": attrs,
    }


def _build_main_trace(stdout_path, run_result, run_id, experiment_id):
    """Build a hierarchical MLflow Trace from the stream-json stdout log.

    Structure:
      root AGENT
        ├── LLM (text response)
        ├── TOOL (single sequential tool call)
        ├── TASK "N parallel agents" (group of parallel calls)
        │   ├── AGENT (subagent 1)
        │   ├── AGENT (subagent 2)
        │   └── ...
        ├── LLM (text response)
        └── ...

    Returns a dict suitable for Trace.from_dict(), or None.
    """
    if not stdout_path.exists():
        return None

    events = []
    with open(stdout_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if not events:
        return None

    # ── Extract metadata from events ────────────────────────────
    session_id = None
    prompt = ""
    final_response = ""

    for e in events:
        if not session_id:
            session_id = e.get("session_id")

    # Prompt: prefer first user text message (the skill invocation).
    # Fall back to first assistant text if no user text is found
    # (older runs without the synthetic prompt event).
    for e in events:
        if e.get("type") == "user":
            content = e.get("message", {}).get("content", "")
            if isinstance(content, str) and content.strip():
                prompt = content.strip()
                break
            elif isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "text":
                        text = b.get("text", "").strip()
                        if text:
                            prompt = text
                            break
                if prompt:
                    break
    if not prompt:
        for e in events:
            if e.get("type") == "assistant":
                for b in e.get("message", {}).get("content", []):
                    if isinstance(b, dict) and b.get("type") == "text":
                        text = b.get("text", "").strip()
                        if text:
                            prompt = text
                            break
                if prompt:
                    break

    # Final response: last assistant text
    for e in reversed(events):
        if e.get("type") == "assistant":
            for b in e.get("message", {}).get("content", []):
                if isinstance(b, dict) and b.get("type") == "text":
                    text = b.get("text", "").strip()
                    if text:
                        final_response = text
                        break
            if final_response:
                break

    # ── Build tool_result timestamp lookup ──────────────────────
    tool_result_ns = {}  # tool_use_id -> timestamp_ns
    for e in events:
        if e.get("type") != "user":
            continue
        ts = e.get("timestamp")
        if not ts:
            continue
        ts_ns = _iso_to_ns(ts)
        content = e.get("message", {}).get("content", [])
        if isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    tuid = b.get("tool_use_id", "")
                    if tuid:
                        tool_result_ns[tuid] = ts_ns

    # ── Override timestamps for background agents ───────────────
    # Background agents return an immediate "async launched" tool_result,
    # but their real completion time is in task_notification events.
    # Use the task_notification timestamp as the true end time.
    for e in events:
        if (e.get("type") == "system"
                and e.get("subtype") == "task_notification"
                and e.get("status") == "completed"):
            tuid = e.get("tool_use_id", "")
            ts = e.get("timestamp")
            if tuid and ts:
                tool_result_ns[tuid] = _iso_to_ns(ts)

    # ── Build result_indices for parallel detection ─────────────
    result_indices = set()
    for i, e in enumerate(events):
        if e.get("type") == "user":
            content = e.get("message", {}).get("content", [])
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        result_indices.add(i)

    # ── Build subagent child lookup ──────────────────────────────
    # Maps parent Agent tool_use_id → list of child spans (tool calls
    # AND LLM reasoning segments).
    # Sources: (1) inline via parent_tool_use_id in the main stream,
    #          (2) background agent output files referenced in tool_results.
    # Each child is a tuple:
    #   ("tool", tuid, name, input)  — tool call
    #   ("llm", None, text, {})     — LLM reasoning text
    subagent_children = {}  # parent_tuid -> [("tool"|"llm", ...), ...]
    subagent_tuids = set()  # tool_use_ids that belong to subagents

    # Source 1: inline children (foreground subagents)
    for e in events:
        ptui = e.get("parent_tool_use_id")
        if not ptui or e.get("type") != "assistant":
            continue
        for b in e.get("message", {}).get("content", []):
            if isinstance(b, dict) and b.get("type") == "tool_use":
                tuid = b.get("id", "")
                subagent_children.setdefault(ptui, []).append(
                    ("tool", tuid, b.get("name", "unknown"), b.get("input", {})))
                subagent_tuids.add(tuid)
            elif isinstance(b, dict) and b.get("type") == "text":
                text = b.get("text", "").strip()
                if text:
                    subagent_children.setdefault(ptui, []).append(
                        ("llm", None, text, {}))

    # Source 2: background agent output files
    # Parse tool_results to map agentId → parent tool_use_id and find
    # output file paths, then read each file for its tool calls.
    import re as _re
    _agent_to_parent = {}  # agentId -> parent_tool_use_id
    _agent_output_files = {}  # agentId -> output_file_path

    for e in events:
        if e.get("type") != "user":
            continue
        content = e.get("message", {}).get("content", [])
        if not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict) or b.get("type") != "tool_result":
                continue
            tuid = b.get("tool_use_id", "")
            text = ""
            c = b.get("content", "")
            if isinstance(c, list):
                text = " ".join(x.get("text", "") for x in c
                                if isinstance(x, dict))
            elif isinstance(c, str):
                text = c
            m_id = _re.search(r"agentId:\s*(\w+)", text)
            m_file = _re.search(r"output_file:\s*(\S+)", text)
            if m_id:
                _agent_to_parent[m_id.group(1)] = tuid
            if m_id and m_file:
                _agent_output_files[m_id.group(1)] = m_file.group(1)

    # Resolve subagent output directory: saved copies from execute.py
    # live alongside stdout.log in <run_dir>/subagents/.
    _subagent_dir = stdout_path.parent / "subagents"

    for agent_id, output_path in _agent_output_files.items():
        parent_tuid = _agent_to_parent.get(agent_id)
        if not parent_tuid or parent_tuid in subagent_children:
            continue  # already have inline children
        # Try original path first, then fall back to saved copy,
        # then try resolving the symlink target, then search the
        # .claude/projects/ session directory.
        if not os.path.exists(output_path):
            fallback = _subagent_dir / f"{agent_id}.jsonl"
            if fallback.exists():
                output_path = str(fallback)
            else:
                # Try resolving symlink (may point to .claude/projects/)
                try:
                    _real = os.path.realpath(output_path)
                    if os.path.exists(_real):
                        output_path = _real
                    else:
                        # Search .claude/projects/ for the file
                        from pathlib import Path as _P
                        _found = False
                        for _sd in _P.home().glob(
                                f".claude/projects/*/*/subagents/"
                                f"agent-{agent_id}.jsonl"):
                            output_path = str(_sd)
                            _found = True
                            break
                        if not _found:
                            continue
                except OSError:
                    continue
        try:
            with open(output_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        se = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if se.get("type") != "assistant":
                        continue
                    for b in se.get("message", {}).get("content", []):
                        if isinstance(b, dict) and b.get("type") == "tool_use":
                            child_tuid = b.get("id", "")
                            subagent_children.setdefault(
                                parent_tuid, []).append(
                                ("tool", child_tuid,
                                 b.get("name", "unknown"),
                                 b.get("input", {})))
                            subagent_tuids.add(child_tuid)
                        elif (isinstance(b, dict)
                              and b.get("type") == "text"):
                            text = b.get("text", "").strip()
                            if text:
                                subagent_children.setdefault(
                                    parent_tuid, []).append(
                                    ("llm", None, text, {}))
            # Also extract timestamps from the output file for timing
            with open(output_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        se = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts = se.get("timestamp")
                    if not ts:
                        continue
                    ts_ns = _iso_to_ns(ts)
                    if se.get("type") == "user":
                        sc = se.get("message", {}).get("content", [])
                        if isinstance(sc, list):
                            for sb in sc:
                                if (isinstance(sb, dict)
                                        and sb.get("type") == "tool_result"):
                                    stuid = sb.get("tool_use_id", "")
                                    if stuid:
                                        tool_result_ns[stuid] = ts_ns
        except (OSError, UnicodeDecodeError):
            continue

    # ── Parse events into ordered segments ──────────────────────
    # Only top-level tool calls (no parent_tool_use_id) go into segments.
    # Subagent children are nested under their Agent span later.
    # A segment is either:
    #   ("llm", text, timestamp, context)  — context = preceding tool names
    #   ("batch", [(event_idx, tool_use_id, name, input), ...])
    segments = []
    current_batch = []
    # Track recent tool calls for LLM context (what ran before this LLM call)
    _recent_tools = []  # list of (name, summary_str)

    def _tool_one_liner(name, inp):
        """Short summary of a tool call for LLM context."""
        if name == "Bash":
            cmd = inp.get("command", "")
            # Extract script name from command
            for part in cmd.split():
                if part.endswith(".py") or part.endswith(".sh"):
                    return f"Bash({part.split('/')[-1]})"
            return f"Bash({cmd[:60]})"
        elif name == "Read":
            path = inp.get("file_path", "")
            return f"Read({path.split('/')[-1]})"
        elif name == "Write":
            path = inp.get("file_path", "")
            return f"Write({path.split('/')[-1]})"
        elif name == "Edit":
            path = inp.get("file_path", "")
            return f"Edit({path.split('/')[-1]})"
        elif name == "Skill":
            return f"Skill(/{inp.get('skill', '?')})"
        elif name == "Agent":
            return f"Agent({inp.get('description', '?')[:40]})"
        elif name in ("Glob", "Grep"):
            return f"{name}({inp.get('pattern', '')[:30]})"
        else:
            return name

    def _flush_batch():
        if current_batch:
            segments.append(("batch", list(current_batch)))
            for _, _, name, inp in current_batch:
                _recent_tools.append(_tool_one_liner(name, inp))
            current_batch.clear()

    for i, e in enumerate(events):
        if e.get("type") == "assistant":
            ptui = e.get("parent_tool_use_id")
            if ptui:
                continue  # skip subagent events
            content = e.get("message", {}).get("content", [])
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text" and b.get("text", "").strip():
                    _flush_batch()
                    context = "; ".join(_recent_tools) if _recent_tools else ""
                    segments.append(("llm", b["text"].strip(),
                                     e.get("timestamp"), context))
                    _recent_tools.clear()
                elif b.get("type") == "tool_use":
                    # Check if a tool_result appeared between previous
                    # tool_use and this one → batch boundary
                    if current_batch:
                        prev_idx = current_batch[-1][0]
                        if any(ri > prev_idx and ri < i
                               for ri in result_indices):
                            _flush_batch()
                    current_batch.append((
                        i, b.get("id", ""), b.get("name", "unknown"),
                        b.get("input", {}),
                    ))
    _flush_batch()

    # ── Derive timing from event timestamps ─────────────────────
    all_event_ts = [_iso_to_ns(e["timestamp"])
                    for e in events if e.get("timestamp")]

    duration_s = run_result.get("duration_s", 0)
    duration_ns = int(duration_s * 1e9)
    duration_ms = int(duration_s * 1000)

    if all_event_ts:
        trace_start = min(all_event_ts)
        trace_end = max(all_event_ts)
        if trace_end - trace_start < duration_ns:
            trace_end = trace_start + duration_ns
    else:
        now_ns = int(datetime.now(timezone.utc).timestamp() * 1e9)
        trace_start = now_ns - duration_ns
        trace_end = now_ns

    # ── Build spans ─────────────────────────────────────────────
    trace_id = f"tr-{uuid.uuid4().hex}"
    root_span_id = uuid.uuid4().bytes[:8].hex()
    token_usage = run_result.get("token_usage", {})
    cost_usd = run_result.get("cost_usd")
    model = run_result.get("model", "")

    # Count tools
    tool_counts = {}
    for seg_type, seg_data, *_ in segments:
        if seg_type == "batch":
            for _, _, name, _ in seg_data:
                tool_counts[name] = tool_counts.get(name, 0) + 1

    response_summary = json.dumps({
        "exit_code": run_result.get("exit_code"),
        "cost_usd": cost_usd,
        "duration_s": duration_s,
        "num_turns": run_result.get("num_turns"),
        "model": model,
        "tool_counts": tool_counts,
    })

    # Root span
    root_attrs = {
        "mlflow.traceRequestId": json.dumps(trace_id),
        "mlflow.spanType": json.dumps("AGENT"),
        "mlflow.spanInputs": json.dumps({"prompt": prompt}),
        "mlflow.spanOutputs": json.dumps({
            "response": final_response,
            "exit_code": run_result.get("exit_code"),
        }),
        "run_id": json.dumps(run_id),
        "model": json.dumps(model),
    }
    if cost_usd:
        root_attrs["mlflow.llm.cost"] = json.dumps({
            "input_cost": 0, "output_cost": 0, "total_cost": cost_usd,
        })
    if model:
        root_attrs["mlflow.llm.model"] = json.dumps(model)

    spans = [{
        "trace_id": trace_id,
        "span_id": root_span_id,
        "parent_span_id": None,
        "name": f"rfe.speedrun ({run_id})",
        "start_time_unix_nano": trace_start,
        "end_time_unix_nano": trace_end,
        "events": [],
        "status": {"code": "STATUS_CODE_OK", "message": ""},
        "attributes": root_attrs,
    }]

    # ── Group segments into agent steps ───────────────────────────
    # Each step = one LLM reasoning output + the tool actions it triggered.
    # Steps are direct children of root; tools are nested inside steps.
    #
    # Segments before the first LLM text (e.g. initial tool calls from
    # the skill setup) are grouped into a "Setup" step.
    #
    # Status-update texts (e.g. "RFE-014 created. 1/20 complete...")
    # are NOT new steps — they are progress notifications from background
    # agents and get merged into the preceding dispatch step.
    _STATUS_RE = _re.compile(
        r"^(RFE-\d+|RHAIRFE-\d+)\s+(created|submitted|reviewed|processed)"
        r".*\d+/\d+\s+(complete|done)",
        _re.IGNORECASE,
    )
    # Also catch "waiting" texts that are just polling updates
    _WAITING_RE = _re.compile(
        r"(waiting for|agents? (are |is )?(still )?(running|creating|processing))",
        _re.IGNORECASE,
    )

    steps = []  # list of (llm_text, llm_ts, llm_context, [batch_segments])
    current_llm = None
    current_ts = None
    current_context = []
    current_batches = []
    # Track whether the current step launched background agents
    _has_bg_agents = False

    def _is_status_update(text):
        """Detect LLM texts that are just background agent status updates."""
        first_line = text.split("\n")[0].strip()
        return bool(_STATUS_RE.match(first_line) or _WAITING_RE.search(first_line))

    for seg_type, seg_data, *rest in segments:
        if seg_type == "llm":
            if _has_bg_agents and _is_status_update(seg_data):
                # Merge status update into the current dispatch step
                continue
            # Save previous step
            if current_llm is not None or current_batches:
                steps.append((current_llm, current_ts, current_context,
                              current_batches))
                current_batches = []
                _has_bg_agents = False
            current_llm = seg_data
            current_ts = rest[0] if rest else None
            current_context = rest[1] if len(rest) > 1 else []
        elif seg_type == "batch":
            current_batches.append(seg_data)
            # Detect if this batch contains Agent calls (potential bg agents)
            if any(name == "Agent" for _, _, name, _ in seg_data):
                _has_bg_agents = True
    # Flush final step
    if current_llm is not None or current_batches:
        steps.append((current_llm, current_ts, current_context,
                      current_batches))

    # ── Build spans from steps ──────────────────────────────────
    cursor_ns = trace_start

    for step_idx, (llm_text, llm_ts, llm_context, batches) in enumerate(steps):
        # Compute step timing from its children
        step_start = _iso_to_ns(llm_ts) if llm_ts else cursor_ns
        step_end = step_start

        # Pre-compute batch timing to find step_end
        batch_timings = []
        for batch in batches:
            batch_ends = [tool_result_ns.get(tuid)
                          for _, tuid, _, _ in batch]
            valid_ends = [t for t in batch_ends if t]
            # Batch start: use the event timestamp of the tool_use call
            # (when the tools were launched), not derived from completion.
            launch_times = [_iso_to_ns(events[eidx].get("timestamp"))
                            for eidx, _, _, _ in batch
                            if events[eidx].get("timestamp")]
            if launch_times:
                b_start = min(launch_times)
            elif valid_ends:
                b_start = max(min(valid_ends) - int(1e9), trace_start)
            else:
                b_start = cursor_ns
            b_end = max(valid_ends) if valid_ends else b_start + int(1e9)
            batch_timings.append((b_start, b_end, batch, batch_ends))
            step_end = max(step_end, b_end)

        if step_end <= step_start:
            step_end = step_start + int(1e9)

        # Step label from first line of LLM text
        if llm_text:
            first_line = llm_text.split("\n")[0].strip()
            # Strip markdown headers
            step_name = first_line.lstrip("#").strip()[:80]
        else:
            step_name = "Setup"

        step_span = _make_span(
            trace_id, root_span_id,
            name=step_name,
            span_type="AGENT",
            start_ns=step_start,
            end_ns=step_end,
            inputs={"step": step_idx + 1},
        )
        step_span_id = step_span["span_id"]
        spans.append(step_span)

        # LLM span inside the step
        if llm_text:
            llm_start = _iso_to_ns(llm_ts) if llm_ts else step_start
            llm_end = llm_start + int(0.5e9)
            # Input: preceding tool results that informed this LLM call
            llm_inputs = {"model": model}
            if llm_context:
                llm_inputs["context"] = llm_context
            spans.append(_make_span(
                trace_id, step_span_id,
                name="LLM",
                span_type="LLM",
                start_ns=llm_start,
                end_ns=llm_end,
                inputs=llm_inputs,
                outputs={"text": llm_text},
                extra_attrs=({"mlflow.llm.model": json.dumps(model)}
                             if model else None),
            ))

        # Tool batches inside the step
        for b_start, b_end, batch, batch_ends in batch_timings:
            is_parallel = len(batch) > 1

            if is_parallel:
                names = set(n for _, _, n, _ in batch)
                if names == {"Agent"}:
                    group_name = f"{len(batch)} parallel agents"
                else:
                    group_name = f"{len(batch)} parallel calls"

                group_span = _make_span(
                    trace_id, step_span_id,
                    name=group_name,
                    span_type="TASK",
                    start_ns=b_start,
                    end_ns=b_end,
                    inputs={"count": len(batch)},
                )
                spans.append(group_span)
                parent_for_children = group_span["span_id"]
            else:
                parent_for_children = step_span_id

            for (_, tuid, name, inp), end_ns in zip(batch, batch_ends):
                child_end = end_ns if end_ns else b_end
                span_type = "AGENT" if name == "Agent" else "TOOL"
                tool_span = _make_span(
                    trace_id, parent_for_children,
                    name=name,
                    span_type=span_type,
                    start_ns=b_start,
                    end_ns=child_end,
                    inputs=_summarize_tool_input(name, inp),
                )
                spans.append(tool_span)

                # Nest subagent children under Agent spans
                if name == "Agent" and tuid in subagent_children:
                    agent_span_id = tool_span["span_id"]
                    children_data = subagent_children[tuid]
                    # Derive the subagent's time window from its tool
                    # children's timestamps (LLM spans don't have tuids).
                    child_timestamps = [
                        tool_result_ns[ct]
                        for ctype, ct, _, _ in children_data
                        if ctype == "tool" and ct in tool_result_ns]
                    if child_timestamps:
                        sa_start = min(child_timestamps) - int(1e9)
                        sa_start = max(sa_start, b_start)
                    else:
                        sa_start = b_start
                    # Also update the Agent span itself to cover its children
                    if child_timestamps:
                        tool_span["start_time_unix_nano"] = sa_start
                        tool_span["end_time_unix_nano"] = max(
                            max(child_timestamps), child_end or 0)

                    _llm_idx = 0
                    for c_type_tag, c_tuid, c_name, c_inp in children_data:
                        if c_type_tag == "llm":
                            # LLM reasoning span
                            _llm_idx += 1
                            llm_text = c_name  # text stored in name slot
                            spans.append(_make_span(
                                trace_id, agent_span_id,
                                name="LLM",
                                span_type="LLM",
                                start_ns=sa_start,
                                end_ns=sa_start + int(0.5e9),
                                inputs={"text": llm_text[:500]},
                            ))
                            sa_start += int(0.5e9)
                        else:
                            # Tool call span
                            c_end = tool_result_ns.get(
                                c_tuid, child_end)
                            c_start = max(
                                sa_start,
                                c_end - int(1e9)) if c_end else sa_start
                            c_type = ("AGENT" if c_name == "Agent"
                                      else "TOOL")
                            spans.append(_make_span(
                                trace_id, agent_span_id,
                                name=c_name,
                                span_type=c_type,
                                start_ns=c_start,
                                end_ns=max(c_end,
                                           c_start + int(0.1e9)),
                                inputs=_summarize_tool_input(
                                    c_name, c_inp),
                            ))
                            sa_start = max(
                                sa_start,
                                c_end + int(0.1e9)) if c_end else (
                                sa_start + int(1e9))

        cursor_ns = step_end

    # ── Trace metadata ──────────────────────────────────────────
    trace_metadata = {}
    if cost_usd:
        trace_metadata["mlflow.trace.cost"] = json.dumps({
            "input_cost": 0, "output_cost": 0, "total_cost": cost_usd,
        })
    if token_usage:
        input_tokens = (token_usage.get("input", 0)
                        + token_usage.get("cache_create", 0))
        output_tokens = token_usage.get("output", 0)
        trace_metadata["mlflow.trace.tokenUsage"] = json.dumps({
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        })
    if session_id:
        trace_metadata["mlflow.trace.session"] = session_id

    return {
        "info": {
            "trace_id": trace_id,
            "trace_location": {
                "type": "MLFLOW_EXPERIMENT",
                "mlflow_experiment": {"experiment_id": experiment_id},
            },
            "request_time": (datetime.fromtimestamp(
                trace_start / 1e9, tz=timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%S.000Z")),
            "trace_metadata": trace_metadata,
            "state": "OK",
            "execution_duration_ms": duration_ms,
            "request_preview": prompt[:200],
            "response_preview": final_response[:200],
            "tags": {
                "eval_run_id": run_id,
                "source": "stream-json",
                "mlflow.traceName": f"rfe.speedrun ({run_id})",
            },
        },
        "data": {"spans": spans},
    }


def _summarize_tool_input(tool_name, tool_input):
    """One-line summary of a tool call for span display."""
    if tool_name == "Bash":
        return {"command": tool_input.get("command", "")[:200]}
    elif tool_name in ("Write", "Edit", "Read"):
        return {"file_path": tool_input.get("file_path", "")}
    elif tool_name == "Agent":
        return {"description": tool_input.get("description", "")}
    elif tool_name == "Skill":
        return {"skill": tool_input.get("skill", "")}
    elif tool_name in ("Glob", "Grep"):
        return {"pattern": tool_input.get("pattern", "")}
    else:
        s = json.dumps(tool_input)
        return {"input": s[:200]}


# ── Main ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--config", default="eval.yaml")
    args = parser.parse_args()

    config = EvalConfig.from_yaml(args.config)
    runs_dir = Path(os.environ.get("AGENT_EVAL_RUNS_DIR", "eval/runs"))
    run_dir = runs_dir / args.run_id

    # Load summary
    summary_path = run_dir / "summary.yaml"
    if not summary_path.exists():
        print(f"ERROR: no summary found at {summary_path}", file=sys.stderr)
        sys.exit(1)

    with open(summary_path) as f:
        summary = yaml.safe_load(f) or {}

    # Load execution metadata
    run_result = {}
    run_result_path = run_dir / "run_result.json"
    if run_result_path.exists():
        with open(run_result_path) as f:
            run_result = json.load(f)

    # Set experiment
    experiment_name = config.mlflow_experiment or config.name
    mlflow.set_experiment(experiment_name)
    client = MlflowClient()

    # Resolve experiment ID
    exp = mlflow.get_experiment_by_name(experiment_name)
    experiment_id = exp.experiment_id if exp else "0"

    with mlflow.start_run(run_name=args.run_id) as run:
        mlflow_run_id = run.info.run_id

        # ── Params ───────────────────────────────────────────────
        params = {
            "skill": config.skill,
            "runner": config.runner,
            "run_id": args.run_id,
            "model": run_result.get("model", ""),
        }
        if run_result.get("agent"):
            params["agent"] = run_result["agent"]
        for key, value in params.items():
            if value:
                mlflow.log_param(key, value)

        # ── Execution metrics ────────────────────────────────────
        if run_result.get("duration_s"):
            mlflow.log_metric("duration_s", run_result["duration_s"])
        if run_result.get("cost_usd"):
            mlflow.log_metric("cost_usd", run_result["cost_usd"])
        if run_result.get("num_turns"):
            mlflow.log_metric("num_turns", run_result["num_turns"])
        token_usage = run_result.get("token_usage", {})
        if token_usage:
            for key in ("input", "output", "cache_read", "cache_create"):
                val = token_usage.get(key)
                if val is not None:
                    mlflow.log_metric(f"tokens/{key}", val)

        # ── Judge metrics ────────────────────────────────────────
        judges = summary.get("judges", {})
        metric_count = 0
        for judge_name, agg in judges.items():
            if isinstance(agg, dict):
                if agg.get("pass_rate") is not None:
                    mlflow.log_metric(f"{judge_name}/pass_rate", agg["pass_rate"])
                    metric_count += 1
                if agg.get("mean") is not None:
                    mlflow.log_metric(f"{judge_name}/mean", agg["mean"])
                    metric_count += 1

        # ── Tags ─────────────────────────────────────────────────
        has_regressions = False
        if config.thresholds:
            for judge_name, threshold in config.thresholds.items():
                agg = judges.get(judge_name, {})
                if not isinstance(agg, dict):
                    continue
                if "min_pass_rate" in threshold:
                    rate = agg.get("pass_rate")
                    if rate is not None and rate < threshold["min_pass_rate"]:
                        has_regressions = True
                if "min_mean" in threshold:
                    mean = agg.get("mean")
                    if mean is not None and mean < threshold["min_mean"]:
                        has_regressions = True
        mlflow.set_tag("regressions_detected", "yes" if has_regressions else "no")
        mlflow.set_tag("num_judges", str(len(judges)))

        # ── Artifact ─────────────────────────────────────────────
        if summary_path.exists():
            mlflow.log_artifact(str(summary_path))

        # ── Per-case results table ───────────────────────────────
        per_case = summary.get("per_case", {})
        if per_case:
            table_rows = []
            for case_id, case_results in per_case.items():
                if not isinstance(case_results, dict):
                    continue
                for judge_name, result in case_results.items():
                    if not isinstance(result, dict):
                        continue
                    table_rows.append({
                        "case_id": case_id,
                        "judge": judge_name,
                        "value": result.get("value"),
                        "rationale": str(result.get("rationale", ""))[:500],
                    })
            if table_rows:
                columns = {}
                for key in table_rows[0]:
                    columns[key] = [row[key] for row in table_rows]
                mlflow.log_table(columns, artifact_file="per_case_results.json")

    # ── Main orchestrator trace from stdout.log ──────────────────
    stdout_path = run_dir / "stdout.log"
    main_trace_id = None
    if stdout_path.exists() and run_result:
        from mlflow.entities.trace import Trace

        trace_dict = _build_main_trace(stdout_path, run_result, args.run_id,
                                       experiment_id)
        if trace_dict:
            try:
                trace = Trace.from_dict(trace_dict)
                client._log_trace(trace)
                main_trace_id = trace_dict["info"]["trace_id"]
                num_spans = len(trace_dict["data"]["spans"])
                duration_s = run_result.get("duration_s", 0)
                print(f"TRACE: {main_trace_id} ({num_spans} spans, {duration_s:.0f}s)")
            except Exception as e:
                print(f"WARNING: failed to create main trace: {e}",
                      file=sys.stderr)

    # ── Link traces to run ───────────────────────────────────────
    trace_ids = []
    if main_trace_id:
        trace_ids.append(main_trace_id)
    try:
        all_traces = client.search_traces(experiment_ids=[experiment_id],
                                          max_results=500)
        for t in all_traces:
            tags = t.info.tags or {}
            if tags.get("eval_run_id") == args.run_id:
                if t.info.trace_id not in trace_ids:
                    trace_ids.append(t.info.trace_id)
        if trace_ids:
            client.link_traces_to_run(run_id=mlflow_run_id, trace_ids=trace_ids)
            print(f"LINKED: {len(trace_ids)} traces to run {mlflow_run_id}")
    except Exception as e:
        print(f"WARNING: failed to link traces: {e}", file=sys.stderr)

    print(f"EXPERIMENT: {experiment_name}")
    print(f"RUN: {mlflow_run_id}")
    print(f"PARAMS: {len(params)}")
    print(f"METRICS: {metric_count}")
    print(f"TABLE: per_case_results ({len(per_case)} cases)")


if __name__ == "__main__":
    main()
