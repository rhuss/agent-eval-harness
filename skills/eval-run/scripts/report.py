#!/usr/bin/env python3
"""Generate an HTML report from eval run results.

Reads summary.yaml, run_result.json, eval.yaml, and optionally
review.yaml + a baseline run to produce a self-contained HTML report.
Works with any skill — reads judges, thresholds, and outputs dynamically.

Usage:
    python3 ${CLAUDE_SKILL_DIR}/scripts/report.py \\
        --run-id <id> \\
        --config eval.yaml \\
        [--baseline <baseline-id>] \\
        [--open]
"""

import argparse
import difflib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml


# ---------------------------------------------------------------------------
# Helpers (ported from rfe-creator eval/reporting/report.py)
# ---------------------------------------------------------------------------

def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _pct(val) -> str:
    if val is None:
        return "?"
    return f"{val * 100:.0f}%" if isinstance(val, float) else str(val)


def _pairwise_badge(winner: str) -> str:
    badges = {"A": ("pw-win", "WIN"), "B": ("pw-loss", "LOSS"),
              "tie": ("pw-tie", "TIE")}
    cls, label = badges.get(winner, ("pw-error", "ERR"))
    return f'<span class="pw-badge {cls}">{label}</span>'


def _word_diff_markup(old_line: str, new_line: str):
    sm = difflib.SequenceMatcher(None, old_line.split(), new_line.split())
    left_parts, right_parts = [], []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        old_words = " ".join(old_line.split()[i1:i2])
        new_words = " ".join(new_line.split()[j1:j2])
        if op == "equal":
            left_parts.append(_esc(old_words))
            right_parts.append(_esc(new_words))
        elif op == "replace":
            left_parts.append(f'<span class="wdel">{_esc(old_words)}</span>')
            right_parts.append(f'<span class="wadd">{_esc(new_words)}</span>')
        elif op == "delete":
            left_parts.append(f'<span class="wdel">{_esc(old_words)}</span>')
        elif op == "insert":
            right_parts.append(f'<span class="wadd">{_esc(new_words)}</span>')
    return " ".join(left_parts), " ".join(right_parts)


def _side_by_side_diff(a: str, b: str, left_label: str = "",
                       right_label: str = "", context: int = 3) -> str:
    a_lines, b_lines = a.splitlines(), b.splitlines()
    sm = difflib.SequenceMatcher(None, a_lines, b_lines)
    rows = [f'<tr class="hdr"><td class="ln"></td>'
            f'<td class="left">{_esc(left_label)}</td>'
            f'<td class="sep"></td>'
            f'<td class="ln"></td>'
            f'<td class="right">{_esc(right_label)}</td></tr>']

    for group in sm.get_grouped_opcodes(context):
        rows.append('<tr class="hdr"><td class="ln" colspan="2">...</td>'
                    '<td class="sep"></td>'
                    '<td class="ln" colspan="2">...</td></tr>')
        for op, i1, i2, j1, j2 in group:
            if op == "equal":
                for i, j in zip(range(i1, i2), range(j1, j2)):
                    rows.append(
                        f'<tr><td class="ln">{i+1}</td>'
                        f'<td class="left">{_esc(a_lines[i])}</td>'
                        f'<td class="sep"></td>'
                        f'<td class="ln">{j+1}</td>'
                        f'<td class="right">{_esc(b_lines[j])}</td></tr>')
            elif op == "replace":
                for k in range(max(i2 - i1, j2 - j1)):
                    ai = i1 + k if i1 + k < i2 else None
                    bj = j1 + k if j1 + k < j2 else None
                    if ai is not None and bj is not None:
                        lh, rh = _word_diff_markup(a_lines[ai], b_lines[bj])
                        rows.append(
                            f'<tr class="mod"><td class="ln">{ai+1}</td>'
                            f'<td class="left">{lh}</td><td class="sep"></td>'
                            f'<td class="ln">{bj+1}</td>'
                            f'<td class="right">{rh}</td></tr>')
                    elif ai is not None:
                        rows.append(
                            f'<tr class="del"><td class="ln">{ai+1}</td>'
                            f'<td class="left">{_esc(a_lines[ai])}</td>'
                            f'<td class="sep"></td><td class="ln"></td>'
                            f'<td class="right"></td></tr>')
                    elif bj is not None:
                        rows.append(
                            f'<tr class="add"><td class="ln"></td>'
                            f'<td class="left"></td><td class="sep"></td>'
                            f'<td class="ln">{bj+1}</td>'
                            f'<td class="right">{_esc(b_lines[bj])}</td></tr>')
            elif op == "delete":
                for i in range(i1, i2):
                    rows.append(
                        f'<tr class="del"><td class="ln">{i+1}</td>'
                        f'<td class="left">{_esc(a_lines[i])}</td>'
                        f'<td class="sep"></td><td class="ln"></td>'
                        f'<td class="right"></td></tr>')
            elif op == "insert":
                for j in range(j1, j2):
                    rows.append(
                        f'<tr class="add"><td class="ln"></td>'
                        f'<td class="left"></td><td class="sep"></td>'
                        f'<td class="ln">{j+1}</td>'
                        f'<td class="right">{_esc(b_lines[j])}</td></tr>')

    return f'<table class="diff-table">{"".join(rows)}</table>'


# ---------------------------------------------------------------------------
# Data loading (standalone — no agent_eval imports)
# ---------------------------------------------------------------------------

def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path) as f:
        return json.load(f)


def _read_text(path: Path, max_lines: int = 200) -> str:
    try:
        lines = path.read_text().splitlines()
        if len(lines) > max_lines:
            return "\n".join(lines[:max_lines]) + f"\n\n... truncated ({len(lines)} lines total)"
        return "\n".join(lines)
    except (UnicodeDecodeError, OSError):
        return ""


def _read_case_input(dataset_path: str, case_id: str) -> str:
    """Read the input file from a dataset case directory."""
    case_dir = Path(dataset_path) / case_id
    if not case_dir.exists():
        return ""
    for suffix in (".yaml", ".yml", ".json"):
        candidate = case_dir / f"input{suffix}"
        if candidate.is_file():
            return _read_text(candidate, max_lines=100)
    # Fallback: first parseable file
    for f in sorted(case_dir.iterdir()):
        if f.is_file() and f.suffix in (".yaml", ".yml", ".json"):
            return _read_text(f, max_lines=100)
    return ""


# ---------------------------------------------------------------------------
# HTML section generators
# ---------------------------------------------------------------------------

CSS = """
body { font-family: -apple-system, sans-serif; max-width: 100%; margin: 2em auto; padding: 0 1em; color: #1a1a1a; }
h1 { border-bottom: 2px solid #333; padding-bottom: 0.3em; }
h2 { margin-top: 1.5em; }
table { border-collapse: collapse; width: 100%; margin: 1em 0; }
th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
th { background: #f5f5f5; }
.pass { color: #16a34a; font-weight: bold; }
.fail { color: #dc2626; font-weight: bold; }
.skip { color: #9ca3af; }
.warn { color: #d97706; font-weight: bold; }
.metric-row td:last-child { font-family: monospace; }
details.case { margin: 1em 0; border: 1px solid #d0d0d0; border-radius: 8px; padding: 1em; background: #fafafa; }
details.case > summary { cursor: pointer; font-weight: bold; padding: 0.3em 0; font-size: 1.05em; }
details.case > summary:hover { color: #2563eb; }
.info-box { background: #f0f4ff; border: 1px solid #c0d0f0; border-radius: 4px; padding: 0.8em; margin: 0.5em 0; font-size: 0.9em; }
.feedback-box { background: #fffbeb; border: 1px solid #f0e0a0; border-radius: 4px; padding: 0.8em; margin: 0.5em 0; font-size: 0.9em; }
.file-badge { display: inline-block; font-family: monospace; font-size: 0.85em; background: #e8eef4; border: 1px solid #c0cfe0; border-radius: 4px; padding: 3px 10px; margin: 1em 0 0.5em 0; color: #2c3e50; }
.pw-badge { display: inline-block; font-size: 0.8em; font-weight: bold; padding: 1px 8px; border-radius: 3px; margin-left: 8px; }
.pw-win { background: #d4edda; color: #155724; }
.pw-loss { background: #f8d7da; color: #721c24; }
.pw-tie { background: #fff3cd; color: #856404; }
.pw-error { background: #e2e3e5; color: #6c757d; }
.diff-table { width: 100%; border-collapse: collapse; font-family: monospace; font-size: 0.82em; table-layout: fixed; }
.diff-table td { padding: 1px 6px; vertical-align: top; white-space: pre-wrap; word-wrap: break-word; border: 1px solid #e0e0e0; }
.diff-table .ln { width: 35px; min-width: 35px; color: #999; text-align: right; background: #fafafa; user-select: none; white-space: nowrap; }
.diff-table .left { width: calc(50% - 35px); background: #fff; }
.diff-table .right { width: calc(50% - 35px); background: #fff; }
.diff-table .sep { width: 1px; padding: 0; background: #ccc; }
.diff-table tr.mod .left { background: #ffeef0; }
.diff-table tr.mod .right { background: #e6ffec; }
.diff-table tr.add .right { background: #e6ffec; }
.diff-table tr.add .left { background: #fafafa; }
.diff-table tr.del .left { background: #ffeef0; }
.diff-table tr.del .right { background: #fafafa; }
.diff-table tr.hdr td { background: #f0f0f0; color: #666; font-weight: bold; }
.diff-table .wdel { background: #fdb8c0; border-radius: 2px; }
.diff-table .wadd { background: #acf2bd; border-radius: 2px; }
pre.output { background: #f8f8f8; border: 1px solid #e0e0e0; border-radius: 4px; padding: 0.8em; font-size: 0.82em; overflow-x: auto; white-space: pre-wrap; word-wrap: break-word; }
.html-preview { width: 100%; border: 1px solid #d0d0d0; border-radius: 4px; margin: 0.5em 0; background: #fff; }
.analysis { background: #f8fafc; border: 1px solid #d0dae8; border-radius: 8px; padding: 1.2em; margin: 1.5em 0; }
.analysis h2 { margin-top: 0; }
.analysis h3 { margin-top: 1em; color: #334155; }
.analysis li { margin: 0.3em 0; line-height: 1.5; }
.analysis code { background: #e2e8f0; padding: 1px 5px; border-radius: 3px; font-size: 0.9em; }
"""


def _render_header(config, run_id, run_result):
    name = config.get("name", "Eval")
    skill = config.get("skill", "")
    date = run_result.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"))
    title = f"{name}" if name else "Eval Report"
    subtitle = f"Skill: {skill} | Run: {run_id}" if skill else f"Run: {run_id}"
    return f"<h1>{_esc(title)}</h1>\n<p>{_esc(subtitle)} | {_esc(str(date)[:19])}</p>\n"


def _render_run_config(run_result, baseline_result=None):
    has_bl = baseline_result is not None
    fields = [
        ("Model", "model"),
        ("Agent", "agent"),
        ("Duration", "duration_s"),
        ("Cost", "cost_usd"),
        ("Turns", "num_turns"),
        ("Exit Code", "exit_code"),
    ]

    html = "<h2>Run Configuration</h2>\n<table>\n"
    html += f"<tr><th></th><th>Current Run</th>{'<th>Baseline</th>' if has_bl else ''}</tr>\n"

    for label, key in fields:
        val = run_result.get(key, "")
        if key == "duration_s" and val:
            val = f"{val:.0f}s"
        elif key == "cost_usd" and val:
            val = f"${val:.2f}"
        bl_val = ""
        if has_bl:
            bl_val = baseline_result.get(key, "")
            if key == "duration_s" and bl_val:
                bl_val = f"{bl_val:.0f}s"
            elif key == "cost_usd" and bl_val:
                bl_val = f"${bl_val:.2f}"
        html += f"<tr><th>{label}</th><td>{_esc(str(val))}</td>"
        if has_bl:
            html += f"<td>{_esc(str(bl_val))}</td>"
        html += "</tr>\n"

    # Token usage — include cache tokens in total input
    tokens = run_result.get("token_usage", {})
    if tokens:
        total_in = (tokens.get("input", 0)
                    + tokens.get("cache_read", 0)
                    + tokens.get("cache_create", 0))
        total_out = tokens.get("output", 0)
        t_str = f"in: {total_in:,} | out: {total_out:,}"
        if tokens.get("cache_read"):
            t_str += f" | cache: {tokens['cache_read']:,}"
        bl_t_str = ""
        if has_bl:
            bl_tokens = baseline_result.get("token_usage", {})
            if bl_tokens:
                bl_in = (bl_tokens.get("input", 0)
                         + bl_tokens.get("cache_read", 0)
                         + bl_tokens.get("cache_create", 0))
                bl_out = bl_tokens.get("output", 0)
                bl_t_str = f"in: {bl_in:,} | out: {bl_out:,}"
                if bl_tokens.get("cache_read"):
                    bl_t_str += f" | cache: {bl_tokens['cache_read']:,}"
        html += f"<tr><th>Tokens</th><td>{t_str}</td>"
        if has_bl:
            html += f"<td>{bl_t_str}</td>"
        html += "</tr>\n"

    html += "</table>\n"
    return html


def _render_scoring_summary(summary, config, baseline_summary=None):
    judges = summary.get("judges", {})
    thresholds = config.get("thresholds", {})
    bl_judges = baseline_summary.get("judges", {}) if baseline_summary else {}
    has_bl = bool(bl_judges)

    html = "<h2>Scoring Summary</h2>\n<table>\n"
    html += f"<tr><th>Judge</th><th>Metric</th><th>Value</th>"
    if has_bl:
        html += "<th>Baseline</th>"
    html += "<th>Threshold</th><th>Status</th></tr>\n"

    for judge_name, agg in sorted(judges.items()):
        if not isinstance(agg, dict):
            continue
        # Determine metric type and value
        pass_rate = agg.get("pass_rate")
        mean = agg.get("mean")

        if pass_rate is not None:
            metric_name = "pass_rate"
            metric_val = f"{pass_rate:.0%}"
        elif mean is not None:
            metric_name = "mean"
            metric_val = f"{mean:.2f}"
        else:
            metric_name = "—"
            metric_val = "—"

        # Baseline
        bl_val = ""
        if has_bl and judge_name in bl_judges:
            bl_agg = bl_judges[judge_name]
            if isinstance(bl_agg, dict):
                bl_pr = bl_agg.get("pass_rate")
                bl_mn = bl_agg.get("mean")
                if bl_pr is not None:
                    bl_val = f"{bl_pr:.0%}"
                elif bl_mn is not None:
                    bl_val = f"{bl_mn:.2f}"

        # Threshold and status
        thresh = thresholds.get(judge_name, {})
        thresh_str = "—"
        status_cls = "skip"
        status_label = "—"

        if isinstance(thresh, dict):
            if "min_pass_rate" in thresh and pass_rate is not None:
                thresh_str = f"&ge; {_pct(thresh['min_pass_rate'])}"
                ok = pass_rate >= thresh["min_pass_rate"]
                status_cls = "pass" if ok else "fail"
                status_label = "PASS" if ok else "FAIL"
            elif "min_mean" in thresh and mean is not None:
                thresh_str = f"&ge; {thresh['min_mean']}"
                ok = mean >= thresh["min_mean"]
                status_cls = "pass" if ok else "fail"
                status_label = "PASS" if ok else "FAIL"

        html += f'<tr class="metric-row"><td>{_esc(judge_name)}</td>'
        html += f"<td>{metric_name}</td><td>{metric_val}</td>"
        if has_bl:
            html += f"<td>{bl_val}</td>"
        html += f'<td>{thresh_str}</td>'
        html += f'<td class="{status_cls}">{status_label}</td></tr>\n'

    html += "</table>\n"
    return html


def _render_regressions(summary, config):
    judges = summary.get("judges", {})
    thresholds = config.get("thresholds", {})
    regressions = []

    for judge_name, thresh in thresholds.items():
        agg = judges.get(judge_name, {})
        if not isinstance(agg, dict) or not isinstance(thresh, dict):
            continue
        if "min_pass_rate" in thresh:
            rate = agg.get("pass_rate")
            if rate is not None and rate < thresh["min_pass_rate"]:
                regressions.append((judge_name, "pass_rate",
                                    f">= {_pct(thresh['min_pass_rate'])}", _pct(rate)))
        if "min_mean" in thresh:
            mean = agg.get("mean")
            if mean is not None and mean < thresh["min_mean"]:
                regressions.append((judge_name, "mean",
                                    f">= {thresh['min_mean']}", f"{mean:.2f}"))

    if not regressions:
        return ""

    html = "<h2>Regressions</h2>\n<table>\n"
    html += "<tr><th>Judge</th><th>Metric</th><th>Threshold</th><th>Actual</th></tr>\n"
    for judge, metric, expected, actual in regressions:
        html += (f'<tr><td>{_esc(judge)}</td><td>{metric}</td>'
                 f'<td>{expected}</td><td class="fail">{actual}</td></tr>\n')
    html += "</table>\n"
    return html


def _render_pairwise(summary):
    pw = summary.get("pairwise")
    if not pw:
        return ""

    html = "<h2>Pairwise Comparison</h2>\n"
    html += (f"<p><strong>{_esc(str(pw.get('run_a', 'A')))}</strong> vs "
             f"<strong>{_esc(str(pw.get('run_b', 'B')))}</strong> "
             f"({pw.get('cases_compared', '?')} cases)</p>\n")
    html += (f"<p>Wins: {pw.get('wins_a', 0)} | "
             f"Losses: {pw.get('wins_b', 0)} | "
             f"Ties: {pw.get('ties', 0)}")
    if pw.get("errors"):
        html += f" | Errors: {pw['errors']}"
    html += "</p>\n"

    per_case = pw.get("per_case", [])
    if per_case:
        html += "<p>"
        for pc in per_case:
            cid = pc.get("case_id", "?")
            winner = pc.get("winner", "error")
            html += f"{_esc(cid)} {_pairwise_badge(winner)} "
        html += "</p>\n"

    return html


def _render_analysis(run_dir):
    """Render the agent's analysis (key findings + recommendations) if saved."""
    analysis_path = run_dir / "analysis.md"
    if not analysis_path.exists():
        return ""

    content = analysis_path.read_text().strip()
    if not content:
        return ""

    return '<div class="analysis">\n' + _md_to_html(content) + "\n</div>\n"


def _md_to_html(md_text):
    """Convert markdown to HTML. Handles headers, lists, tables, code blocks,
    bold, italic, inline code, and links."""
    import re

    lines = md_text.splitlines()
    out = []
    i = 0
    list_stack = []  # stack of "ul" or "ol"

    def _close_lists():
        while list_stack:
            out.append(f"</{list_stack.pop()}>")

    def _inline(text):
        """Apply inline formatting: bold, italic, code, links."""
        text = _esc(text)
        text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
        text = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'<em>\1</em>', text)
        text = re.sub(r'`(.+?)`', r'<code>\1</code>', text)
        text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)
        return text

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Fenced code block
        if stripped.startswith("```"):
            _close_lists()
            lang = stripped[3:].strip()
            code_lines = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            out.append(f'<pre class="output">{_esc(chr(10).join(code_lines))}</pre>')
            i += 1
            continue

        # Table (line starts with |)
        if stripped.startswith("|") and "|" in stripped[1:]:
            _close_lists()
            table_lines = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_lines.append(lines[i].strip())
                i += 1
            out.append(_md_table_to_html(table_lines))
            continue

        # Headers
        if stripped.startswith("### "):
            _close_lists()
            out.append(f"<h4>{_inline(stripped[4:])}</h4>")
            i += 1
            continue
        if stripped.startswith("## "):
            _close_lists()
            out.append(f"<h3>{_inline(stripped[3:])}</h3>")
            i += 1
            continue
        if stripped.startswith("# "):
            _close_lists()
            out.append(f"<h2>{_inline(stripped[2:])}</h2>")
            i += 1
            continue

        # Unordered list
        if stripped.startswith("- "):
            if not list_stack or list_stack[-1] != "ul":
                if list_stack and list_stack[-1] == "ol":
                    out.append(f"</{list_stack.pop()}>")
                list_stack.append("ul")
                out.append("<ul>")
            out.append(f"<li>{_inline(stripped[2:])}</li>")
            i += 1
            continue

        # Ordered list
        m = re.match(r'^(\d+)\.\s(.+)', stripped)
        if m:
            if not list_stack or list_stack[-1] != "ol":
                if list_stack and list_stack[-1] == "ul":
                    out.append(f"</{list_stack.pop()}>")
                list_stack.append("ol")
                out.append("<ol>")
            out.append(f"<li>{_inline(m.group(2))}</li>")
            i += 1
            continue

        # Blank line
        if not stripped:
            _close_lists()
            i += 1
            continue

        # Paragraph
        _close_lists()
        out.append(f"<p>{_inline(stripped)}</p>")
        i += 1

    _close_lists()
    return "\n".join(out)


def _md_table_to_html(table_lines):
    """Convert markdown table lines to an HTML table."""
    if len(table_lines) < 2:
        return ""

    def _parse_row(line):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        return cells

    headers = _parse_row(table_lines[0])

    # Skip separator row (line with dashes/colons)
    data_start = 1
    if len(table_lines) > 1 and all(
            c.strip().replace("-", "").replace(":", "").replace(" ", "") == ""
            for c in table_lines[1].strip().strip("|").split("|")):
        data_start = 2

    html = "<table>\n<tr>"
    for h in headers:
        html += f"<th>{_esc(h)}</th>"
    html += "</tr>\n"

    for row_line in table_lines[data_start:]:
        cells = _parse_row(row_line)
        html += "<tr>"
        for c in cells:
            # Color-code PASS/FAIL cells
            cls = ""
            if c.strip() == "PASS":
                cls = ' class="pass"'
            elif c.strip() == "FAIL":
                cls = ' class="fail"'
            html += f"<td{cls}>{_esc(c)}</td>"
        html += "</tr>\n"

    html += "</table>"
    return html


def _render_per_case(summary, run_dir, config, baseline_dir, review):
    per_case = summary.get("per_case", {})
    if not per_case:
        return ""

    dataset_path = config.get("dataset", {}).get("path", "")
    output_paths = [o.get("path", ".") for o in config.get("outputs", []) if o.get("path")]
    feedback = review.get("feedback", {}) if review else {}
    cases_dir = run_dir / "cases"
    bl_cases_dir = baseline_dir / "cases" if baseline_dir else None

    html = "<h2>Per-Case Details</h2>\n"
    if baseline_dir:
        html += (f"<p>Comparing <strong>{run_dir.name}</strong> vs "
                 f"<strong>{baseline_dir.name}</strong></p>\n")

    for case_id in sorted(per_case.keys()):
        case_results = per_case[case_id]
        if not isinstance(case_results, dict):
            continue

        case_dir = cases_dir / case_id
        label = case_id

        # Count pass/fail
        passed = sum(1 for r in case_results.values()
                     if isinstance(r, dict) and r.get("value") is True)
        failed = sum(1 for r in case_results.values()
                     if isinstance(r, dict) and r.get("value") is False)
        total = len(case_results)
        status = "pass" if failed == 0 else "fail"

        html += (f'<details open class="case"><summary>'
                 f'<span class="{status}">{label}</span> '
                 f'<span class="skip">({passed}/{total} pass)</span></summary>\n')

        # Judge results table
        html += '<table><tr><th>Judge</th><th>Value</th><th>Rationale</th></tr>\n'
        for jname, jresult in sorted(case_results.items()):
            if not isinstance(jresult, dict):
                continue
            val = jresult.get("value")
            rat = str(jresult.get("rationale", ""))[:300]
            err = jresult.get("error", "")

            if val is True:
                val_html = '<span class="pass">PASS</span>'
            elif val is False:
                val_html = '<span class="fail">FAIL</span>'
            elif isinstance(val, (int, float)):
                val_html = str(val)
            else:
                val_html = _esc(str(val)[:100])

            if err:
                rat = f"ERROR: {err}"

            html += (f'<tr><td>{_esc(jname)}</td><td>{val_html}</td>'
                     f'<td style="font-size:0.85em">{_esc(rat)}</td></tr>\n')
        html += "</table>\n"

        # Human feedback
        case_feedback = feedback.get(case_id, "")
        if case_feedback:
            html += (f'<div class="feedback-box"><strong>Human feedback:</strong> '
                     f'{_esc(str(case_feedback))}</div>\n')

        # Input data
        if dataset_path:
            input_text = _read_case_input(dataset_path, case_id)
            if input_text:
                html += (f'<details open><summary>Input</summary>'
                         f'<pre class="output">{_esc(input_text)}</pre></details>\n')

        # Output files
        if case_dir.exists():
            files = sorted(f for f in case_dir.rglob("*") if f.is_file())
            if files:
                html += "<details open><summary>Output files</summary>\n"
                for f in files:
                    rel = f.relative_to(case_dir)
                    if f.suffix == ".html":
                        # Render HTML files inline in a sandboxed iframe
                        try:
                            html_content = f.read_text()
                            # Escape for srcdoc attribute (double-escape quotes)
                            srcdoc = (html_content
                                      .replace("&", "&amp;")
                                      .replace('"', "&quot;"))
                            html += (f'<div class="file-badge">{_esc(str(rel))}</div>\n'
                                     f'<iframe class="html-preview" srcdoc="{srcdoc}" '
                                     f'sandbox="allow-same-origin" '
                                     f'onload="this.style.height=this.contentDocument.documentElement.scrollHeight+20+\'px\'"'
                                     f'></iframe>\n')
                        except (UnicodeDecodeError, OSError):
                            html += (f'<div class="file-badge">{_esc(str(rel))} '
                                     f'<span class="skip">(could not read)</span></div>\n')
                    else:
                        content = _read_text(f, max_lines=200)
                        if content:
                            html += (f'<div class="file-badge">{_esc(str(rel))}</div>\n'
                                     f'<pre class="output">{_esc(content)}</pre>\n')
                        else:
                            size = f.stat().st_size
                            html += (f'<div class="file-badge">{_esc(str(rel))} '
                                     f'<span class="skip">({size} bytes, binary)</span></div>\n')
                html += "</details>\n"

        # Baseline diff
        if bl_cases_dir and (bl_cases_dir / case_id).exists():
            bl_case_dir = bl_cases_dir / case_id
            diffs = []
            for out_path in output_paths:
                curr_dir = case_dir / out_path if out_path != "." else case_dir
                base_dir = bl_case_dir / out_path if out_path != "." else bl_case_dir
                if not curr_dir.exists() or not base_dir.exists():
                    continue
                curr_files = {f.name: f for f in curr_dir.iterdir() if f.is_file()}
                base_files = {f.name: f for f in base_dir.iterdir() if f.is_file()}
                for name in sorted(set(curr_files) | set(base_files)):
                    try:
                        ct = curr_files[name].read_text() if name in curr_files else ""
                        bt = base_files[name].read_text() if name in base_files else ""
                    except (UnicodeDecodeError, OSError):
                        continue
                    if ct != bt:
                        diff_html = _side_by_side_diff(
                            bt, ct,
                            left_label=f"baseline/{out_path}/{name}",
                            right_label=f"current/{out_path}/{name}")
                        diffs.append((f"{out_path}/{name}", diff_html))

            if diffs:
                html += "<details open><summary>Baseline diff</summary>\n"
                for fname, diff_html in diffs:
                    html += f'<div class="file-badge">{_esc(fname)}</div>\n{diff_html}\n'
                html += "</details>\n"

        html += "</details>\n"

    return html


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------

def generate_report(config, summary, run_result, run_dir,
                    review=None, baseline_dir=None,
                    baseline_summary=None, baseline_result=None):
    name = config.get("name", "Eval")
    run_id = summary.get("run_id", run_dir.name)

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{_esc(name)} — {_esc(run_id)}</title>
<style>{CSS}</style>
</head>
<body>
"""
    html += _render_header(config, run_id, run_result)
    html += _render_run_config(run_result, baseline_result)
    html += _render_scoring_summary(summary, config, baseline_summary)
    html += _render_regressions(summary, config)
    html += _render_pairwise(summary)
    html += _render_analysis(run_dir)
    html += _render_per_case(summary, run_dir, config, baseline_dir, review)
    html += "\n</body>\n</html>\n"
    return html


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--config", default="eval.yaml")
    parser.add_argument("--baseline", default=None,
                        help="Baseline run ID for comparison")
    parser.add_argument("--open", action="store_true",
                        help="Open report in browser")
    args = parser.parse_args()

    runs_dir = Path(os.environ.get("AGENT_EVAL_RUNS_DIR", "eval/runs"))
    run_dir = runs_dir / args.run_id
    baseline_dir = runs_dir / args.baseline if args.baseline else None

    if not run_dir.exists():
        print(f"ERROR: run directory not found: {run_dir}", file=sys.stderr)
        sys.exit(1)

    config = _load_yaml(Path(args.config))
    summary = _load_yaml(run_dir / "summary.yaml")
    run_result = _load_json(run_dir / "run_result.json")
    review = _load_yaml(run_dir / "review.yaml") or None

    baseline_summary = _load_yaml(baseline_dir / "summary.yaml") if baseline_dir else None
    baseline_result = _load_json(baseline_dir / "run_result.json") if baseline_dir else None

    html = generate_report(
        config=config,
        summary=summary,
        run_result=run_result,
        run_dir=run_dir,
        review=review,
        baseline_dir=baseline_dir,
        baseline_summary=baseline_summary,
        baseline_result=baseline_result,
    )

    output_path = run_dir / "report.html"
    output_path.write_text(html)
    print(f"REPORT: {output_path}")

    if args.open:
        import webbrowser
        webbrowser.open(f"file://{output_path.resolve()}")


if __name__ == "__main__":
    main()
