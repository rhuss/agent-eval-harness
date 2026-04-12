#!/usr/bin/env python3
"""Pre-execution check that project artifact directories are clean.

Skills write artifacts to the project directory (not the workspace).
Between eval runs, stale artifacts from previous runs contaminate
results — wrong IDs, stale run reports, inflated file counts.

This script checks all output paths from eval.yaml plus common state
directories, reports what it finds, and optionally cleans them.

Usage:
    # Check only (exit 1 if dirty)
    python3 ${CLAUDE_SKILL_DIR}/scripts/preflight.py --config eval.yaml

    # Check and clean
    python3 ${CLAUDE_SKILL_DIR}/scripts/preflight.py --config eval.yaml --clean

    # Also check if eval/runs/<id> already exists
    python3 ${CLAUDE_SKILL_DIR}/scripts/preflight.py --config eval.yaml --run-id 2026-04-11-opus
"""

import argparse
import os
import shutil
import sys
from pathlib import Path

import yaml

from agent_eval.config import EvalConfig


# State directories that skills write to (not in eval.yaml outputs)
STATE_DIRS = ["tmp"]


def _find_files(directory: Path) -> list[Path]:
    """Recursively find all files in a directory."""
    if not directory.exists():
        return []
    if directory.is_file():
        return [directory]
    return sorted(f for f in directory.rglob("*") if f.is_file())


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--config", default="eval.yaml")
    parser.add_argument("--clean", action="store_true",
                        help="Remove all found artifacts and state files")
    parser.add_argument("--run-id", default=None,
                        help="Also check if eval/runs/<id> already exists")
    args = parser.parse_args()

    config = EvalConfig.from_yaml(args.config)
    project_root = Path.cwd()
    runs_dir = Path(os.environ.get("AGENT_EVAL_RUNS_DIR", "eval/runs"))

    # Collect all paths to check
    dirty = {}  # path_label -> list of files

    # 1. Output paths from eval.yaml
    for output in config.outputs:
        if not output.path:
            continue
        out_path = project_root / output.path
        files = _find_files(out_path)
        if files:
            dirty[output.path] = files

    # 2. State directories
    for state_dir in STATE_DIRS:
        state_path = project_root / state_dir
        files = _find_files(state_path)
        if files:
            dirty[state_dir] = files

    # 3. Check for existing run output
    run_exists = False
    if args.run_id:
        run_path = project_root / runs_dir / args.run_id
        if run_path.exists() and any(run_path.iterdir()):
            run_exists = True

    # Report
    if not dirty and not run_exists:
        print("CLEAN")
        sys.exit(0)

    print("DIRTY")
    total = 0
    for label, files in sorted(dirty.items()):
        count = len(files)
        total += count
        # Show a few example filenames
        examples = [f.name for f in files[:5]]
        suffix = f" (+{count - 5} more)" if count > 5 else ""
        print(f"  {label}: {count} files — {', '.join(examples)}{suffix}")

    if run_exists:
        run_path = project_root / runs_dir / args.run_id
        run_files = _find_files(run_path)
        print(f"  {runs_dir}/{args.run_id}: {len(run_files)} files (previous run output)")

    if not args.clean:
        print(f"\n{total} artifact files from previous run(s).")
        sys.exit(1)

    # Clean
    for label, files in dirty.items():
        path = project_root / label
        if path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
            path.mkdir(parents=True, exist_ok=True)
        print(f"  cleaned: {label}")

    if run_exists and args.run_id:
        run_path = project_root / runs_dir / args.run_id
        shutil.rmtree(run_path)
        print(f"  cleaned: {runs_dir}/{args.run_id}")

    print("CLEAN")


if __name__ == "__main__":
    main()
