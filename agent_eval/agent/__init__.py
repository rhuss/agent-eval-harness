"""Agent runner abstraction for eval harness."""

from .base import EvalRunner, RunResult
from .claude_code import ClaudeCodeRunner

RUNNERS = {
    "claude-code": ClaudeCodeRunner,
}

__all__ = ["EvalRunner", "RunResult", "ClaudeCodeRunner", "RUNNERS"]
