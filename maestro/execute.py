"""Calendar execution adapter. Deterministic, and the only stage that touches
the outside world.

Nothing here decides anything. By the time a plan reaches this module a human
has approved it, and execution is a mechanical translation of the approved
decision into one of three actions:

  calendar_hold  the approved slot becomes a hold on the CEO's calendar
  route_only     the ask was delegated or escalated; there is no calendar write
  no_action      the request was declined

**In this build the adapter is simulated.** It constructs the exact payload it
would send, assigns the idempotency key it would send it under, and records the
result - but makes no network call. Swapping in Google Calendar or Outlook
means implementing ``_send`` against a real client; every other line, including
the audit trail and the idempotency contract, stays as it is.

Executions are appended to data/executions.json, which is the adapter's outbox.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import audit, store
from .models import ExecutionResult

EXECUTIONS_FILE = "executions.json"

# Outcomes that put time on the calendar once approved.
_BOOKING_OUTCOMES = ("accept", "defer")


def _event_payload(approval: dict[str, Any], slot: dict[str, Any],
                   calendar: dict[str, Any]) -> dict[str, Any]:
    """The event body the adapter would send to the calendar provider."""
    return {
        "calendar_id": calendar.get("calendar_id", "zeb@arcadia.com"),
        "title": f"{approval['requester']} - {approval.get('topic', approval['summary'])}",
        "start": slot["start"],
        "end": slot["end"],
        "timezone": calendar["timezone"],
        "attendees": [a for a in (calendar.get("calendar_id", "zeb@arcadia.com"),
                                  approval.get("requester_email")) if a],
        "visibility": "default",
        "source": "maestro",
        # Replaying the same approval can never double-book.
        "idempotency_key": f"maestro:{approval['id']}",
    }


def _send(payload: dict[str, Any]) -> None:
    """Where the real calendar API call goes. Simulated in this build."""
    return None


def execute(approval: dict[str, Any], calendar: dict[str, Any]) -> ExecutionResult:
    """Carry out an approved plan and record what happened.

    Idempotent per approval: re-running returns the existing result rather than
    writing a second hold.
    """
    log: list[dict[str, Any]] = store.load(EXECUTIONS_FILE)
    existing = next((e for e in log if e["approval_id"] == approval["id"]), None)
    if existing is not None:
        return ExecutionResult(**existing)

    outcome = approval.get("outcome", "accept")
    slots = approval.get("slots") or []
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    if outcome in _BOOKING_OUTCOMES and slots:
        slot = slots[0]
        payload = _event_payload(approval, slot, calendar)
        _send(payload)
        result = ExecutionResult(
            approval_id=approval["id"], request_id=approval.get("request_id"),
            action="calendar_hold", simulated=True,
            summary=f"Hold placed for {approval['requester']}: {slot['ceo_local']} "
                    f"({slot['requester_local']} their time).",
            event=payload, executed_at=now,
        )
    elif outcome in ("delegate", "escalate_to_human"):
        owner = approval.get("routed_to") or "the assigned owner"
        result = ExecutionResult(
            approval_id=approval["id"], request_id=approval.get("request_id"),
            action="route_only", simulated=True,
            summary=f"No calendar write. Handed to {owner}; the requester's thread stays with them.",
            event=None, executed_at=now,
        )
    else:
        result = ExecutionResult(
            approval_id=approval["id"], request_id=approval.get("request_id"),
            action="no_action", simulated=True,
            summary="Closed out with no calendar change.",
            event=None, executed_at=now,
        )

    log.append(result.to_dict())
    store.save(EXECUTIONS_FILE, log)
    audit.record(
        "execution",
        f"{result.action} (simulated adapter): {result.summary}",
        request_id=approval.get("request_id"),
        detail={"idempotency_key": result.event["idempotency_key"]} if result.event else None,
    )
    return result


def log_for(request_id: str | None) -> list[dict[str, Any]]:
    """Every execution recorded against one request."""
    if request_id is None:
        return []
    return [e for e in store.load(EXECUTIONS_FILE) if e.get("request_id") == request_id]
