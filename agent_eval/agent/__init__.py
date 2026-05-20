"""Agent runner abstraction for eval harness."""

from .base import EvalRunner, RunResult
from .claude_code import ClaudeCodeRunner
from .cli_runner import CliRunner
from .responses_api import ResponsesAPIRunner

RUNNERS = {
    "claude-code": ClaudeCodeRunner,
    "cli": CliRunner,
    "responses-api": ResponsesAPIRunner,
}

__all__ = [
    "EvalRunner", "RunResult",
    "ClaudeCodeRunner", "CliRunner", "ResponsesAPIRunner",
    "RUNNERS",
]
