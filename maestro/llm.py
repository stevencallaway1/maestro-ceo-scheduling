"""LLM provider seam.

The demo runs with ``MockProvider`` - fully deterministic, zero network calls,
so nothing can fail live. A production deployment swaps in a real provider
(e.g. the Claude API) behind the same ``LLMProvider`` protocol without touching
any pipeline code: agents ask the provider to "render" a named artifact from
structured context, and the mock does it with templates while a real model
would do it with a prompt.
"""
from __future__ import annotations

from typing import Any, Protocol


class LLMProvider(Protocol):
    """Interface every language-model backend must satisfy."""

    def render(self, task: str, context: dict[str, Any]) -> str:
        """Produce text for a named task (e.g. ``rationale``) from context."""
        ...


class MockProvider:
    """Deterministic template-based renderer. The demo default.

    ``render`` dispatches to the template registered for the task. Templates
    receive the full structured context and return plain text, which keeps the
    output stable across runs - critical for a live screen-share demo.
    """

    def __init__(self) -> None:
        self._templates: dict[str, Any] = {}

    def register(self, task: str, fn: Any) -> None:
        """Register a callable ``fn(context) -> str`` for a task name."""
        self._templates[task] = fn

    def render(self, task: str, context: dict[str, Any]) -> str:
        fn = self._templates.get(task)
        if fn is None:
            raise KeyError(f"MockProvider has no template for task '{task}'")
        return fn(context)


DEFAULT_PROVIDER = MockProvider()
