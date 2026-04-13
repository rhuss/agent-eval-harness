"""Claude Code CLI runner implementation."""

import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from .base import EvalRunner, RunResult
from .stream_capture import (
    make_prompt_event, inject_timestamp, extract_usage, SubagentCapture
)

_print_lock = threading.Lock()


class ClaudeCodeRunner(EvalRunner):
    """Runs skills using the Claude Code CLI in non-interactive mode."""

    def __init__(
        self,
        permissions: Optional[dict] = None,
        runner_options: Optional[dict] = None,
        subagent_model: Optional[str] = None,
        plugin_dirs: Optional[list] = None,
        mlflow_experiment: Optional[str] = None,
        env_strip: Optional[list] = None,
        log_prefix: Optional[str] = None,
    ):
        opts = runner_options or {}
        self._permissions = permissions or {}
        self._subagent_model = subagent_model
        self._plugin_dirs = plugin_dirs or opts.get("plugin_dirs", [])
        self._mlflow_experiment = mlflow_experiment
        self._env_strip = env_strip or opts.get("env_strip", [])
        self._log_prefix = log_prefix
        self._settings = opts.get("settings")
        self._max_budget = opts.get("max_budget_usd")
        self._system_prompt = opts.get("system_prompt")

    @property
    def name(self) -> str:
        return "claude-code"

    @property
    def version(self) -> str:
        """Get the Claude Code CLI version."""
        try:
            result = subprocess.run(
                ["claude", "--version"], capture_output=True, text=True, timeout=5)
            return result.stdout.strip() if result.returncode == 0 else ""
        except Exception:
            return ""

    def run_skill(
        self,
        skill_name: str,
        args: str,
        workspace: Path,
        model: str,
        settings_path: Optional[Path] = None,
        system_prompt: Optional[str] = None,
        max_budget_usd: float = 5.0,
        timeout_s: int = 600,
    ) -> RunResult:
        cmd = [
            "claude",
            "--print",
            "--model", model,
            "--output-format", "stream-json" if self._log_prefix else "json",
            "--max-budget-usd", str(max_budget_usd),
        ]
        # Keep session persistence ON so subagent .jsonl files survive
        # long enough for the runner to capture them on task_notification.
        if self._log_prefix:
            cmd.append("--verbose")

        for plugin_dir in self._plugin_dirs:
            cmd.extend(["--plugin-dir", str(plugin_dir)])

        if settings_path:
            cmd.extend(["--settings", str(settings_path)])

        effective_prompt = system_prompt or self._system_prompt
        if effective_prompt:
            cmd.extend(["--append-system-prompt", effective_prompt])

        # Permissions: allow/deny tool patterns
        deny = self._permissions.get("deny", [])
        if deny:
            cmd.extend(["--disallowed-tools", ",".join(deny)])
        allow = self._permissions.get("allow", [])
        if allow:
            cmd.extend(["--allowed-tools", ",".join(allow)])

        # Build the skill invocation prompt (passed via stdin)
        prompt = f"/{skill_name}"
        if args:
            prompt += f" {args}"

        start = time.monotonic()
        stdout_lines = []
        deadline = start + timeout_s
        capture = SubagentCapture()

        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(workspace),
                text=True,
                env=self._build_env(),
            )

            proc.stdin.write(prompt)
            proc.stdin.close()

            # Inject synthetic user event for the prompt
            if self._log_prefix:
                stdout_lines.append(make_prompt_event(prompt))

            result_obj = None
            resolved_model = None

            for line in proc.stdout:
                if time.monotonic() > deadline:
                    raise subprocess.TimeoutExpired(cmd, timeout_s)
                line = line.rstrip("\n")
                if not line.strip():
                    stdout_lines.append(line)
                    continue
                if self._log_prefix:
                    try:
                        line = inject_timestamp(line)
                        obj = json.loads(line)
                        if (not resolved_model
                                and obj.get("type") == "system"
                                and obj.get("subtype") == "init"):
                            resolved_model = obj.get("model")
                        msg = _extract_progress(obj)
                        if msg:
                            with _print_lock:
                                print(f"  {self._log_prefix} | {msg}", flush=True)
                        if obj.get("type") == "result":
                            result_obj = obj

                        # Track and capture subagent output files
                        capture.on_event(obj)

                    except json.JSONDecodeError:
                        pass
                stdout_lines.append(line)

            # Final sweep before process exits
            capture.final_sweep()

            remaining = max(0, deadline - time.monotonic())
            stderr = proc.stderr.read()
            proc.wait(timeout=max(remaining, 5))

            # Post-exit sweep for any remaining subagent files
            capture.post_exit_sweep()

        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            duration = time.monotonic() - start
            token_usage, cost_usd, num_turns, models_seen = extract_usage(stdout_lines)
            return RunResult(
                exit_code=-1,
                stdout="\n".join(stdout_lines),
                stderr=f"Timed out after {timeout_s}s",
                duration_s=duration,
                token_usage=token_usage,
                cost_usd=cost_usd,
                num_turns=num_turns,
                resolved_model=resolved_model,
                models_used=sorted(models_seen) if models_seen else None,
            )
        except Exception as e:
            duration = time.monotonic() - start
            return RunResult(
                exit_code=-1, stdout="", stderr=str(e), duration_s=duration,
            )

        duration = time.monotonic() - start
        stdout_text = "\n".join(stdout_lines)

        # Extract usage from collected stream-json lines
        raw_output = result_obj
        if not result_obj and stdout_text.strip():
            try:
                result_obj = json.loads(stdout_text)
                raw_output = result_obj
            except json.JSONDecodeError:
                pass

        token_usage, cost_usd, num_turns, models_seen = extract_usage(stdout_lines)
        if not cost_usd and isinstance(result_obj, dict):
            cost_usd = result_obj.get("total_cost_usd")

        return RunResult(
            exit_code=proc.returncode,
            stdout=stdout_text,
            stderr=stderr or "",
            duration_s=duration,
            token_usage=token_usage,
            cost_usd=cost_usd,
            num_turns=num_turns,
            resolved_model=resolved_model,
            models_used=sorted(models_seen) if models_seen else None,
            raw_output=raw_output,
            subagent_outputs=capture.outputs or None,
        )

    # Environment keys safe to forward to evaluated skills
    _SAFE_ENV_KEYS = {
        "PATH", "HOME", "USER", "SHELL", "LANG", "LC_ALL", "TERM",
        "ANTHROPIC_API_KEY", "ANTHROPIC_VERTEX_PROJECT_ID", "CLOUD_ML_REGION",
        "CLAUDE_CODE_USE_VERTEX",
        "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT",
        "CLOUDSDK_CONFIG", "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE",
        "MLFLOW_TRACKING_URI", "MLFLOW_EXPERIMENT_NAME",
        "CLAUDE_CODE_SUBAGENT_MODEL",
        "AGENT_EVAL_RUNS_DIR",
    }

    def _build_env(self):
        """Build subprocess environment with allowlisted keys only."""
        env = {k: v for k, v in os.environ.items() if k in self._SAFE_ENV_KEYS}
        for key in self._env_strip:
            env.pop(key, None)
        if self._subagent_model:
            env["CLAUDE_CODE_SUBAGENT_MODEL"] = self._subagent_model
        if self._mlflow_experiment:
            env["MLFLOW_EXPERIMENT_NAME"] = self._mlflow_experiment
        return env


def _extract_progress(obj: dict) -> str:
    """Extract a human-readable progress message from a stream-json event."""
    t = obj.get("type")

    if t == "assistant":
        msg = obj.get("message", {})
        for block in msg.get("content", []):
            if block.get("type") == "tool_use":
                tool = block.get("name", "")
                inp = block.get("input", {})
                if tool == "Skill":
                    return f"Invoking /{inp.get('skill', '?')}"
                elif tool == "Bash":
                    cmd = inp.get("command", "")[:60]
                    return f"Running: {cmd}"
                elif tool in ("Write", "Edit"):
                    path = inp.get("file_path", "")
                    return f"{tool}: {path.split('/')[-1] if path else '?'}"
                elif tool == "Read":
                    path = inp.get("file_path", "")
                    return f"Reading: {path.split('/')[-1] if path else '?'}"
                else:
                    return f"Tool: {tool}"
            elif block.get("type") == "text":
                text = block.get("text", "").strip()
                if text and len(text) < 100:
                    return text
    elif t == "result":
        cost = obj.get("total_cost_usd", 0)
        turns = obj.get("num_turns", 0)
        return f"Done ({turns} turns, ${cost:.2f})"

    return ""
