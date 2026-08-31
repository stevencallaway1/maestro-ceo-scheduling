"""Model seam. The only place Maestro would call a language model.

Exactly two stages are model-backed - the Planner and the Critic - and each
makes one call per request. Everything else in the pipeline is deterministic
code. Both stages talk to this ``ModelProvider`` interface, which returns a
structured object for a named task, the same shape a real model would return
under a JSON schema.

The demo ships with ``TemplateProvider``: the identical call sites and the
identical output contract, rendered from deterministic templates instead of a
network round trip. That keeps the live demo unfailable and makes the
production swap a one-line change (register a Claude-backed provider) with no
edits to pipeline code.

Every call through the seam is logged. The log is what lets the pipeline report
how many model calls a request actually cost, and what lets a test *prove* the
sensitive-category lockout stops before the seam rather than merely claiming it.
"""
from __future__ import annotations

from typing import Any, Callable, Protocol

# The two model-backed tasks. Named here so the cap is visible in one place.
TASKS = ("plan", "critique")


class ModelProvider(Protocol):
    """Interface every model backend must satisfy."""

    call_log: list[str]

    def generate(self, task: str, context: dict[str, Any]) -> dict[str, Any]:
        """Return a structured object for a named task (``plan``/``critique``)."""
        ...

    def reset_log(self) -> None:
        """Clear the call log. Called at the start of each pipeline run."""
        ...


class TemplateProvider:
    """Deterministic renderer standing in for a model. The demo default.

    ``generate`` dispatches to the template registered for the task. Templates
    receive the full structured context and return the same dict shape a real
    model would produce, which keeps output stable across runs - critical for a
    live demo, and exactly what the Critic needs to be checkable.
    """

    def __init__(self) -> None:
        self._templates: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}
        self.call_log: list[str] = []

    def register(self, task: str, fn: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        """Register a callable ``fn(context) -> dict`` for a task name."""
        if task not in TASKS:
            raise ValueError(f"Unknown model task '{task}'. Known tasks: {TASKS}")
        self._templates[task] = fn

    def reset_log(self) -> None:
        """Clear the call log, so one run's count is not another's."""
        self.call_log = []

    def generate(self, task: str, context: dict[str, Any]) -> dict[str, Any]:
        fn = self._templates.get(task)
        if fn is None:
            raise KeyError(f"No template registered for task '{task}'")
        self.call_log.append(task)
        return fn(context)


DEFAULT_PROVIDER = TemplateProvider()
