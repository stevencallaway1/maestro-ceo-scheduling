"""Intake: normalize a raw inbound payload (email / Slack / ClickUp task) into
a structured :class:`~maestro.models.RequestObject`.

Deterministic. Classification is keyword and signal matching over the request
text, with the requester record as a fallback, so the same input always
produces the same object. The raw source text is preserved verbatim: intake
narrows what the request looks like, and never decides what happens to it.
"""
from __future__ import annotations

import re
from typing import Any

from .models import Requester, RequestObject

# Ordered: first match wins. (type, keywords in body/subject)
_TYPE_SIGNALS: list[tuple[str, list[str]]] = [
    ("press", ["reporter", "journalist", "press", "comment on", "article", "piece on", "deadline"]),
    ("board", ["board", "metrics review", "q3 review"]),
    ("vendor", ["demo", "personalized demo", "cut", "spend", "grab 30 minutes on your calendar", "pricing plan"]),
    ("investor", ["fund", "investor", "term sheet", "round"]),
    ("external_partner", ["partnership", "co-sell", "agreement", "final terms", "alliance"]),
    ("personal", ["dinner", "family", "personal"]),
]

_URGENCY_HIGH = ["asap", "urgent", "today", "deadline", "time-sensitive", "this week", "before"]
_URGENCY_LOW = ["no rush", "whenever", "next month", "sometime"]

_DURATION_RE = re.compile(r"(\d{1,3})\s*(?:min|minutes|mins)", re.IGNORECASE)


def _classify_type(text: str, requester_internal: bool, requester_role: str) -> str:
    """Classify meeting type from text signals, falling back on requester facts."""
    lowered = text.lower()
    for mtype, keywords in _TYPE_SIGNALS:
        if any(k in lowered for k in keywords):
            # A board member asking about board topics outranks other signals.
            if mtype == "board" and "board" not in requester_role.lower() and not requester_internal:
                continue
            return mtype
    if requester_internal:
        role = requester_role.lower()
        if any(t in role for t in ("vp", "chief", "coo", "cfo", "cto", "head of")):
            return "exec_1on1"
        return "internal_team"
    return "external_partner"


def _infer_urgency(text: str) -> tuple[str, str]:
    """Return (urgency, the phrase that triggered it)."""
    lowered = text.lower()
    for kw in _URGENCY_HIGH:
        if kw in lowered:
            return "high", f'matched "{kw}" in the request text'
    for kw in _URGENCY_LOW:
        if kw in lowered:
            return "low", f'matched "{kw}" in the request text'
    return "normal", "no explicit urgency signal; defaulted to normal"


def _extract_duration(text: str, default: int = 30) -> int:
    m = _DURATION_RE.search(text)
    return int(m.group(1)) if m else default


def parse(payload: dict[str, Any], people: dict[str, Any]) -> RequestObject:
    """Parse one inbound payload into a RequestObject.

    ``people`` (from data/people.json) supplies requester role/org when the
    address is known; unknown senders are treated as external with role/org
    parsed as "unknown".
    """
    email = payload["from_email"]
    person = people.get(email, {})
    requester = Requester(
        name=payload.get("from_name", person.get("name", "Unknown")),
        email=email,
        role=person.get("role", "unknown"),
        org=person.get("org", email.split("@")[-1]),
        internal=bool(person.get("internal", False)),
    )
    raw = f"{payload.get('subject', '')}\n{payload.get('body', '')}".strip()
    urgency, signal = _infer_urgency(raw)
    # Sensitive-topic hint from the person record refines classification:
    # a personnel-flagged requester talking about "confidential ... personnel"
    # is not a normal external_partner ask.
    meeting_type = _classify_type(raw, requester.internal, requester.role)
    return RequestObject(
        id=payload["id"],
        requester=requester,
        requested_duration_minutes=_extract_duration(raw),
        urgency=urgency,
        urgency_signal=signal,
        topic=payload.get("subject", "(no subject)"),
        meeting_type=meeting_type,
        channel=payload.get("channel", "email"),
        proposed_start=payload.get("proposed_start"),
        raw_source_text=raw,
        received_at=payload.get("received_at", ""),
    )
