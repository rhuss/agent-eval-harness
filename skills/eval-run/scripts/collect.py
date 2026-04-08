#!/usr/bin/env python3
"""Collect artifacts from workspace and distribute to per-case directories.

Reads output paths from eval.yaml. Maps files to cases using
case_order.yaml (positional: Nth file group → Nth case).

Usage:
    python3 ${CLAUDE_SKILL_DIR}/scripts/collect.py \\
        --config eval.yaml \\
        --workspace /tmp/agent-eval/test-001 \\
        --output eval/runs/test-001
"""

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

import yaml

from agent_eval.config import EvalConfig


def _safe_path_component(value, field):
    """Reject path components that escape the parent directory."""
    p = Path(str(value))
    if p.is_absolute() or ".." in p.parts:
        raise ValueError(f"{field} must be a relative path without '..': {value}")
    return str(p)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="eval.yaml")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    config = EvalConfig.from_yaml(args.config)
    workspace = Path(args.workspace)
    output_dir = Path(args.output)

    # Load case order
    order_path = workspace / "case_order.yaml"
    if not order_path.exists():
        print("ERROR: no case_order.yaml in workspace", file=sys.stderr)
        sys.exit(1)

    with open(order_path) as f:
        case_order = yaml.safe_load(f)

    output_dir.mkdir(parents=True, exist_ok=True)
    results = {}

    # Validate case IDs from case_order
    for entry in case_order:
        cid = entry["case_id"] if isinstance(entry, dict) else entry
        _safe_path_component(cid, "case_id")

    # Collect from each output path defined in config (directory or file)
    for output_cfg in config.outputs:
        out_path = _safe_path_component(output_cfg.path or ".", "output path")
        src = workspace / out_path
        if not src.exists():
            continue

        # Handle single-file output paths
        if src.is_file():
            files = [src]
            src_dir = src.parent
        else:
            src_dir = src
            # Get all files in the output directory (including subdirs)
            files = sorted(f for f in src_dir.rglob("*") if f.is_file())
        if not files:
            continue

        if output_cfg.batch_pattern:
            # Batch mode: use the configured pattern to map files to cases
            mapping = _collect_batch(files, case_order, output_cfg.batch_pattern)
        else:
            # Auto-detect mode: group files by common prefix
            groups = _group_files(files, len(case_order))
            mapping = {}
            for i, group in enumerate(groups):
                if i >= len(case_order):
                    break
                entry = case_order[i]
                cid = entry["case_id"] if isinstance(entry, dict) else entry
                mapping[cid] = group

        for case_id, matched_files in mapping.items():
            if not matched_files:
                continue
            case_output = output_dir / "cases" / case_id / out_path
            case_output.mkdir(parents=True, exist_ok=True)

            for src_file in matched_files:
                # Preserve subdirectory structure relative to src_dir
                rel = src_file.relative_to(src_dir)
                dest = case_output / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dest)

            results.setdefault(case_id, {})[out_path] = len(matched_files)

    # Save collection summary
    with open(output_dir / "collection.json", "w") as f:
        json.dump(results, f, indent=2)

    for case_id, info in sorted(results.items()):
        counts = ", ".join(f"{k}={v}" for k, v in info.items())
        print(f"  {case_id}: {counts}")

    if not results:
        print("WARNING: no artifacts collected", file=sys.stderr)


def _collect_batch(files, case_order, batch_pattern):
    """Map files to cases using a batch_pattern from eval.yaml.

    Args:
        files: sorted list of Path objects in the output directory
        case_order: list of {"case_id": str, "entry_count": int} dicts
        batch_pattern: pattern with {n} placeholder (1-based index),
                       or "*" for shared directories

    Returns:
        dict mapping case_id → list of matching Path objects
    """
    # Shared: all files go to every case
    if batch_pattern == "*":
        return {
            (e["case_id"] if isinstance(e, dict) else e): list(files)
            for e in case_order
        }

    result = {}
    batch_idx = 1
    for entry in case_order:
        case_id = entry["case_id"] if isinstance(entry, dict) else entry
        count = entry.get("entry_count", 1) if isinstance(entry, dict) else 1

        matching = []
        for n in range(batch_idx, batch_idx + count):
            try:
                prefix = batch_pattern.format(n=n)
            except (KeyError, ValueError):
                prefix = batch_pattern.replace("{n}", str(n))
            matching.extend(f for f in files if f.name.startswith(prefix))

        if matching:
            result[case_id] = matching
        batch_idx += count

    return result


def _group_files(files, num_cases):
    """Group files into per-case bundles.

    Tries to detect a common ID prefix pattern (e.g., "RFE-001", "TASK-002").
    If found, groups by prefix. Otherwise, distributes one file per case.
    """
    import re

    # Try to find a common prefix pattern: WORD-NNN at start of filename
    prefixes = {}
    for f in files:
        match = re.match(r'^([A-Za-z]+-\d+)', f.stem)
        if match:
            prefix = match.group(1)
            prefixes.setdefault(prefix, []).append(f)

    # If we found prefix groups matching the case count, use them
    if prefixes and len(prefixes) >= num_cases * 0.5:
        return [prefixes[k] for k in sorted(prefixes.keys())]

    # Fallback: one file per case (positional)
    return [[f] for f in files]


if __name__ == "__main__":
    main()
