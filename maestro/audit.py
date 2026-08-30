"""Audit trail. Every action Maestro takes is appended here and shown in the UI.

The log is an append-only JSON-lines file (data/audit_log.jsonl) so it survives
restarts and can be tailed, diffed, or inspected by hand.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import store

AUDIT_FILE = "audit_log.jsonl"


def record(stage: str, summary: str, request_id: str | None = None,
           actor: str = "maestro", detail: dict[str, Any] | None = None) -> dict[str, Any]:
    """Append one audit entry and return it."""
    entry: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "actor": actor,
        "stage": stage,
        "request_id": request_id,
        "summary": summary,
    }
    if detail:
        entry["detail"] = detail
    store.append_jsonl(AUDIT_FILE, entry)
    return entry


def tail(limit: int = 100) -> list[dict[str, Any]]:
    """Return the most recent audit entries, newest first."""
    entries = store.read_jsonl(AUDIT_FILE)
    return list(reversed(entries[-limit:]))
