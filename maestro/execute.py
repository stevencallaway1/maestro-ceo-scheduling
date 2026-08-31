"""Calendar execution adapter. Deterministic, and the only stage that touches
the outside world.

Nothing here decides anything. By the time a plan reaches this module a human
has approved it, and execution is a mechanical translation of the approved
decision into one of three actions:

  provisional_hold  the offered times become a tentative hold on the CEO's
                    calendar, and nothing else
  route_only        the ask was delegated or escalated; no calendar write
  no_action         the request was declined

**Execution never books more than the reply promised.** An accepted request
offers the requester three times and asks them to pick one. So the hold is
provisional and sits on the CEO's calendar alone: the requester is not added as
an attendee and receives no invite, because they have not chosen a time yet.
Confirming the pick is a separate, later step - a human one in this build. The
alternative, silently booking the first option and inviting the requester to it,
would make the outbound message a lie.

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
    """The event body the adapter would send to the calendar provider.

    Tentative, and on the CEO's calendar only. The requester is deliberately
    not an attendee: the reply offered them options and asked them to pick, so
    inviting them to one of those options would book something nobody agreed to.
    """
    slots = approval.get("slots") or [slot]
    return {
        "calendar_id": calendar.get("calendar_id", "zeb@arcadia.com"),
        "title": f"HOLD (awaiting confirmation): {approval['requester']} - "
                 f"{approval.get('topic', approval['summary'])}",
        "start": slot["start"],
        "end": slot["end"],
        "timezone": calendar["timezone"],
        # CEO only. No invite goes out until the requester picks a time.
        "attendees": [calendar.get("calendar_id", "zeb@arcadia.com")],
        "status": "tentative",
        "pending_confirmation": True,
        "offered_slots": [s.get("requester_local") for s in slots],
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
        first_name = approval["requester"].split(" ")[0]
        result = ExecutionResult(
            approval_id=approval["id"], request_id=approval.get("request_id"),
            action="provisional_hold", simulated=True,
            summary=f"Tentative hold on Zeb's calendar only: {slot['ceo_local']} "
                    f"({slot['requester_local']} their time), the first of "
                    f"{len(slots)} option(s) offered. No invite sent - "
                    f"{first_name} picks a time, then it is confirmed.",
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
