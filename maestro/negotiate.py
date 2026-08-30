"""Negotiation Agent: draft the outbound reply in the CEO's voice.

Drafts are built through the ``LLMProvider`` seam from data/voice.json voice
notes. Proposed times are always rendered in the REQUESTER'S timezone
(timezone fairness), with the CEO-local time alongside for the approver.
Drafts are never auto-sent - every one enters the approval queue.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from .llm import DEFAULT_PROVIDER, LLMProvider
from .models import Decision, Dossier, Draft, RequestObject


def _first_name(full: str) -> str:
    return full.split(" ")[0]


def _slot_lines(decision: Decision) -> str:
    """Bullet the proposed slots, requester-local first (timezone fairness)."""
    lines = []
    for s in decision.proposed_slots:
        if s.requester_local == s.ceo_local:
            lines.append(f"  - {s.requester_local}")
        else:
            lines.append(f"  - {s.requester_local} (that's {s.ceo_local} for Zeb)")
    return "\n".join(lines)


def _draft_template(ctx: dict[str, Any]) -> str:
    """Deterministic CEO-voice draft rendering for MockProvider."""
    request: RequestObject = ctx["request"]
    dossier: Dossier = ctx["dossier"]
    decision: Decision = ctx["decision"]
    voice: dict[str, Any] = ctx["voice"]
    name = _first_name(dossier.requester_name)
    signoff = voice.get("signoff", "- Z")

    if decision.outcome == "accept":
        topic_hook = ""
        if dossier.open_threads:
            # Compress the open thread to its leading noun phrase for the hook.
            short = re.split(r"\s*[:(]| - | owed | promised ", dossier.open_threads[0])[0].strip()
            if len(short) > 1 and short[1].islower():
                short = short[0].lower() + short[1:]
            topic_hook = f" I'll bring the {short} too."
        return (
            f"{name} - yes, let's do it.{topic_hook} Three options, your time:\n\n"
            f"{_slot_lines(decision)}\n\n"
            f"Pick one and my EA will send the invite.\n\n{signoff}"
        )

    if decision.outcome == "defer":
        when = "that time"
        if request.proposed_start:
            dt = datetime.fromisoformat(request.proposed_start)
            day = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][dt.weekday()]
            when = f"{day} {'morning' if dt.hour < 12 else 'afternoon'}"
        return (
            f"{name} - yes to the conversation, no to {when}. That block is protected "
            f"focus time. Any of these work instead, same {request.requested_duration_minutes} minutes:\n\n"
            f"{_slot_lines(decision)}\n\n"
            f"Grab whichever and it's yours.\n\n{signoff}"
        )

    if decision.outcome == "delegate":
        owner = decision.delegate_to or "the right owner"
        return (
            f"{name} - thanks for the note. {owner.split(',')[0]} owns this area and is the right "
            f"first conversation; I'm copying him here so you two can find time. If it turns into "
            f"something strategic, he'll pull me in.\n\n{signoff}"
        )

    if decision.outcome == "decline":
        return (
            f"{name} - I'm going to pass for now; the calendar doesn't have room for this one. "
            f"If the situation changes I'll reach out.\n\n{signoff}"
        )

    # escalate_to_human: an internal routing note, never an external reply.
    if decision.hard_locked:
        return (
            f"[INTERNAL - not for sending] Routed directly to Zeb.\n\n"
            f"{dossier.requester_name} ({dossier.requester_email}) raised a "
            f"{dossier.sensitive_category}-category topic: \"{request.topic}\". "
            f"Maestro does not read into, schedule, or reply to sensitive-category requests. "
            f"No hold was created and no reply was drafted. Full dossier attached for context."
        )
    routed = ", ".join(decision.route_to) if decision.route_to else "Chief of Staff"
    return (
        f"[INTERNAL - not for sending] Routed to: {routed}.\n\n"
        f"{dossier.requester_name} ({dossier.requester_email}) "
        f"requested: \"{request.topic}\" - deadline signals: {request.urgency}. "
        f"Recommend comms decides interview vs. written statement; Maestro will schedule "
        f"whatever they choose."
    )


DEFAULT_PROVIDER.register("draft", _draft_template)


def draft(request: RequestObject, dossier: Dossier, decision: Decision,
          voice: dict[str, Any], provider: LLMProvider = DEFAULT_PROVIDER) -> Draft:
    """Produce the outbound Draft artifact for a decision. Never auto-sent."""
    body = provider.render("draft", {
        "request": request, "dossier": dossier, "decision": decision, "voice": voice,
    })
    internal = decision.outcome == "escalate_to_human"
    return Draft(
        request_id=request.id,
        kind="internal_note" if internal else "external_reply",
        to="Zeb + " + (", ".join(decision.route_to) if decision.route_to else "Chief of Staff")
        if internal else f"{dossier.requester_name} <{dossier.requester_email}>",
        subject=("Routing: " if internal else "Re: ") + request.topic,
        body=body,
        approval_required=True,
    )
