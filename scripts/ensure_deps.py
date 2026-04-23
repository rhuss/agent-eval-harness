#!/usr/bin/env python3
"""Install Python dependencies based on what the project actually needs.

Checks eval.yaml (if it exists) to decide which optional deps to install:
- pyyaml: always required
- mlflow[genai]: if a mlflow block is present in eval.yaml
- anthropic[vertex]: if LLM judges or pairwise comparison are configured

Caches a stamp file in CLAUDE_PLUGIN_DATA so installs only run once
(or when eval.yaml changes).
"""

import hashlib
import subprocess
import sys
from pathlib import Path


def main():
    plugin_data = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    plugin_root = Path(__file__).parent.parent

    try:
        import yaml  # noqa: F401
    except ImportError:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", "pyyaml>=6.0"],
            check=False,
        )

    eval_yaml = _find_eval_yaml(plugin_root)

    deps = [("pyyaml>=6.0", "yaml")]
    needs_mlflow = False
    needs_anthropic = False

    if eval_yaml and eval_yaml.exists():
        content = eval_yaml.read_text()
        try:
            import yaml
            config = yaml.safe_load(content) or {}
        except Exception as e:
            print(f"ensure_deps: failed to parse {eval_yaml}: {e}", file=sys.stderr)
            config = {}

        if not isinstance(config, dict):
            print(f"ensure_deps: expected mapping in {eval_yaml}, got {type(config).__name__}",
                  file=sys.stderr)
            config = {}

        mlflow_block = config.get("mlflow")
        if mlflow_block is not None:
            needs_mlflow = True

        judges = config.get("judges", [])
        if isinstance(judges, list):
            for j in judges:
                if not isinstance(j, dict):
                    continue
                if j.get("prompt") or j.get("prompt_file") or j.get("pairwise"):
                    needs_anthropic = True
                    break

    if needs_mlflow:
        deps.append(("mlflow[genai]>=3.5", "mlflow"))
    if needs_anthropic:
        deps.append(("anthropic[vertex]>=0.40", "anthropic"))

    stamp = _compute_stamp([spec for spec, _ in deps])

    stamp_file = None
    if plugin_data:
        plugin_data.mkdir(parents=True, exist_ok=True)
        stamp_file = plugin_data / "deps.stamp"

    missing = []
    for spec, import_name in deps:
        try:
            __import__(import_name)
        except ImportError:
            missing.append(spec)

    if not missing and stamp_file:
        stamp_file.write_text(stamp)

    if not missing:
        return

    if not missing:
        if plugin_data:
            stamp_file.write_text(stamp)
        return

    print(f"Installing: {', '.join(missing)}")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q", *missing],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"pip install failed: {result.stderr}", file=sys.stderr)
        return

    if stamp_file:
        stamp_file.write_text(stamp)


def _find_eval_yaml(plugin_root):
    cwd = Path.cwd()
    for candidate in [cwd / "eval.yaml", plugin_root / "eval.yaml"]:
        if candidate.exists():
            return candidate
    return None


def _compute_stamp(deps):
    return hashlib.sha256("|".join(sorted(deps)).encode()).hexdigest()[:12]


if __name__ == "__main__":
    main()
