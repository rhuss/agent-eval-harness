#!/usr/bin/env python3
"""Install Python dependencies based on what the project actually needs.

Checks eval.yaml (if it exists) to decide which optional deps to install:
- pyyaml: always required
- mlflow[genai]: if mlflow.experiment is configured
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

    eval_yaml = _find_eval_yaml(plugin_root)

    deps = ["pyyaml>=6.0"]
    needs_mlflow = False
    needs_anthropic = False

    if eval_yaml and eval_yaml.exists():
        content = eval_yaml.read_text()
        try:
            import yaml
            config = yaml.safe_load(content) or {}
        except ImportError:
            config = {}
        except Exception:
            config = {}

        mlflow_cfg = config.get("mlflow", {})
        if isinstance(mlflow_cfg, dict) and mlflow_cfg.get("experiment"):
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
        deps.append("mlflow[genai]>=3.5")
    if needs_anthropic:
        deps.append("anthropic[vertex]>=0.40")

    stamp = _compute_stamp(deps)

    if plugin_data:
        plugin_data.mkdir(parents=True, exist_ok=True)
        stamp_file = plugin_data / "deps.stamp"
        if stamp_file.exists() and stamp_file.read_text().strip() == stamp:
            return

    missing = []
    for dep in deps:
        pkg = dep.split("[")[0].split(">")[0].split("=")[0]
        import_name = pkg.replace("-", "_").replace("[", "").replace("]", "")
        if import_name == "pyyaml":
            import_name = "yaml"
        try:
            __import__(import_name)
        except ImportError:
            missing.append(dep)

    if not missing:
        if plugin_data:
            stamp_file.write_text(stamp)
        return

    print(f"Installing: {', '.join(missing)}")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q"] + missing,
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"pip install failed: {result.stderr}", file=sys.stderr)
        return

    if plugin_data:
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
