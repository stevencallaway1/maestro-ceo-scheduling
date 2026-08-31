"""Planner: the first of two model-backed stages.

The Planner receives a normalized request, the context dossier, the policy
engine's verdict, and a set of candidate slots that deterministic calendar
math has already proven legal. From that it writes the two pieces of judgment
work in one call:

  * the **rationale** - 2-4 sentences of plain English citing dossier facts
    and the fired rules by name, the way a sharp chief of staff explains their
    thinking, and
  * the **reply draft** - the outbound message in the CEO's voice.

What the Planner does NOT do is decide the outcome or invent times. The
outcome comes from the policy engine and the slots come from
``calendarlib.find_slots``. The model writes the reasoning and the prose; the
deterministic layer owns the decision boundary. That split is what makes the
system safe to point at a real calendar.

One model call per request, through :mod:`maestro.llm` - except on the
sensitive-category lockout path, which never reaches the seam at all.
``plan`` refuses a hard-locked request outright; ``locked_plan`` produces the
routing note in deterministic code instead. That refusal is the lockout: a
sensitive topic is not summarized, paraphrased, or drafted around by a model,
because it is never sent to one.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from .calendarlib import find_slots
from .llm import DEFAULT_PROVIDER, ModelProvider
from .models import Decision, Dossier, Draft, PolicyResult, RequestObject, Slot


# ---------------------------------------------------------------------------
# Template rendering. In production this whole block is replaced by one prompt
# plus a JSON schema; the call site and the returned shape do not change.
# ---------------------------------------------------------------------------

def _first_name(full: str) -> str:
    return full.split(" ")[0]


def _rule_citation(policy: PolicyResult) -> str:
    deciding = next((r for r in policy.fired_rules if r.decisive), None)
    return f'{policy.deciding_rule_id} ("{deciding.plain_english}")' if deciding else "the default policy"


def _opening(dossier: Dossier) -> str:
    return f"{dossier.requester_name} - {dossier.relationship_summary.split('.')[0].strip()}."


def _rationale(ctx: dict[str, Any]) -> str:
    """The written 'why' behind the decision."""
    request: RequestObject = ctx["request"]
    dossier: Dossier = ctx["dossier"]
    policy: PolicyResult = ctx["policy"]
    slots: list[Slot] = ctx["slots"]
    rule_cite = _rule_citation(policy)

    sentences = [_opening(dossier)]

    if policy.outcome == "escalate_to_human":
        routed = " and ".join(policy.route_to) if policy.route_to else "the right owners"
        sentences.append(f"Per {rule_cite}, this routes to {routed} rather than the calendar.")
        sentences.append(
            f"Relevance is {dossier.strategic_relevance}/100 ({dossier.relevance_justification.rstrip('.')}), "
            "so speed matters but the decision is not Maestro's to make."
        )
    elif policy.outcome == "delegate":
        sentences.append(
            f"Strategic relevance is {dossier.strategic_relevance}/100: {dossier.relevance_justification}"
        )
        sentences.append(
            f"{rule_cite} applies, so the ask moves to {policy.delegate_to} with a warm redirect - "
            "routed, not rejected."
        )
    elif policy.outcome == "defer":
        if ctx.get("derived", {}).get("proposed_in_protected_block"):
            sentences.append(
                f"The proposed time collides with a protected deep-work block, and {rule_cite} "
                "keeps those intact for everything short of board or legal."
            )
        else:
            sentences.append(
                f"No policy claims this confidently enough to act, so {rule_cite} sends it to "
                f"the Chief of Staff with the full dossier attached - relevance sits at "
                f"{dossier.strategic_relevance}/100 and a human should set the precedent."
            )
        if dossier.open_threads:
            sentences.append(
                f"The topic itself is a priority ({dossier.open_threads[0].rstrip('.')}), so "
                f"{len(slots)} alternative slots are offered in the same reply."
            )
    elif policy.outcome == "accept":
        facts = []
        if dossier.last_interactions:
            li = dossier.last_interactions[0]
            facts.append(f"last touchpoint {li.date} ({li.topic.rstrip('.')})")
        if dossier.open_threads:
            facts.append(f"open thread: {dossier.open_threads[0].rstrip('.')}")
        if facts:
            sentences.append(f"Relevance {dossier.strategic_relevance}/100; {'; '.join(facts)}.")
        sentences.append(f"{rule_cite} decides it: accept.")
        if slots:
            tz_note = ""
            if dossier.timezone != "America/Los_Angeles":
                city = dossier.timezone.split("/")[-1].replace("_", " ")
                tz_note = f", all landing inside {_first_name(dossier.requester_name)}'s working day in {city}"
            sentences.append(f"Offering {len(slots)} policy-clean slots{tz_note}.")
    else:  # decline
        sentences.append(
            f"{rule_cite} decides it: decline. Relevance is {dossier.strategic_relevance}/100 "
            f"({dossier.relevance_justification.rstrip('.')}), so the courteous close-out "
            "protects the calendar without burning the relationship."
        )

    return " ".join(sentences)


def _slot_lines(slots: list[Slot]) -> str:
    """Bullet the proposed slots, requester-local first (timezone fairness)."""
    lines = []
    for s in slots:
        if s.requester_local == s.ceo_local:
            lines.append(f"  - {s.requester_local}")
        else:
            lines.append(f"  - {s.requester_local} (that's {s.ceo_local} for Zeb)")
    return "\n".join(lines)


def _reply_body(ctx: dict[str, Any]) -> str:
    """The outbound message, written in the CEO's voice."""
    request: RequestObject = ctx["request"]
    dossier: Dossier = ctx["dossier"]
    policy: PolicyResult = ctx["policy"]
    slots: list[Slot] = ctx["slots"]
    voice: dict[str, Any] = ctx["voice"]
    name = _first_name(dossier.requester_name)
    signoff = voice.get("signoff", "- Z")

    if policy.outcome == "accept":
        topic_hook = ""
        if dossier.open_threads:
            # Compress the open thread to its leading noun phrase for the hook.
            short = re.split(r"\s*[:(]| - | owed | promised ", dossier.open_threads[0])[0].strip()
            if len(short) > 1 and short[1].islower():
                short = short[0].lower() + short[1:]
            topic_hook = f" I'll bring the {short} too."
        return (
            f"{name} - yes, let's do it.{topic_hook} Three options, your time:\n\n"
            f"{_slot_lines(slots)}\n\n"
            f"Pick one and my EA will send the invite.\n\n{signoff}"
        )

    if policy.outcome == "defer":
        when = "that time"
        if request.proposed_start:
            dt = datetime.fromisoformat(request.proposed_start)
            day = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
                   "Saturday", "Sunday"][dt.weekday()]
            when = f"{day} {'morning' if dt.hour < 12 else 'afternoon'}"
        return (
            f"{name} - yes to the conversation, no to {when}. That block is protected "
            f"focus time. Any of these work instead, same {request.requested_duration_minutes} minutes:\n\n"
            f"{_slot_lines(slots)}\n\n"
            f"Grab whichever and it's yours.\n\n{signoff}"
        )

    if policy.outcome == "delegate":
        owner = policy.delegate_to or "the right owner"
        return (
            f"{name} - thanks for the note. {owner.split(',')[0]} owns this area and is the right "
            f"first conversation; I'm copying him here so you two can find time. If it turns into "
            f"something strategic, he'll pull me in.\n\n{signoff}"
        )

    if policy.outcome == "decline":
        return (
            f"{name} - I'm going to pass for now; the calendar doesn't have room for this one. "
            f"If the situation changes I'll reach out.\n\n{signoff}"
        )

    # escalate_to_human: an internal routing note, never an external reply.
    routed = ", ".join(policy.route_to) if policy.route_to else "Chief of Staff"
    return (
        f"[INTERNAL - not for sending] Routed to: {routed}.\n\n"
        f"{dossier.requester_name} ({dossier.requester_email}) "
        f"requested: \"{request.topic}\" - deadline signals: {request.urgency}. "
        f"Recommend comms decides interview vs. written statement; Maestro will schedule "
        f"whatever they choose."
    )


def _plan_template(ctx: dict[str, Any]) -> dict[str, Any]:
    """The structured object the Planner model returns for task ``plan``."""
    return {"rationale": _rationale(ctx), "reply_body": _reply_body(ctx)}


DEFAULT_PROVIDER.register("plan", _plan_template)


# ---------------------------------------------------------------------------
# Stage entry point
# ---------------------------------------------------------------------------

def _trust_note(level: str, hard_locked: bool) -> str:
    """One line explaining what the current trust level lets Maestro do here."""
    if hard_locked:
        return "Sensitive category: hard-locked to L0 by design. Suggest-only, forever."
    return {
        "L0": "L0 (suggest only): Maestro recommends; a human executes everything.",
        "L1": "L1: Maestro drafts the reply and calendar hold; each one waits for approval.",
        "L2": "L2: Maestro may auto-handle low-stakes internal moves and logs everything.",
        "L3": "L3: Maestro may negotiate external scheduling within policy bounds.",
    }.get(level, "L0 (suggest only): Maestro recommends; a human executes everything.")


def _locked_rationale(dossier: Dossier, policy_result: PolicyResult) -> str:
    """Why the lockout fired. Written in code, not by a model."""
    sentences = [
        _opening(dossier),
        f"The request touches a sensitive category ({dossier.sensitive_category}), so "
        f"{_rule_citation(policy_result)} hard-locks it: Maestro takes no scheduling action, "
        "makes no model call, and routes it straight to Zeb.",
    ]
    if dossier.open_threads:
        sentences.append(f"Context for when he picks it up: {dossier.open_threads[-1].rstrip('.')}.")
    return " ".join(sentences)


def _locked_note(dossier: Dossier) -> str:
    """The internal routing note for a locked request. Fixed text, no model."""
    return (
        f"[INTERNAL - not for sending] Routed directly to Zeb.\n\n"
        f"{dossier.requester_name} ({dossier.requester_email}) raised a "
        f"{dossier.sensitive_category}-category topic. "
        f"Maestro does not read into, schedule, or reply to sensitive-category requests. "
        f"The pipeline stopped at the policy engine: no model stage ran on this request, "
        f"no hold was created, and no reply was drafted. Full dossier attached for context."
    )


def locked_plan(request: RequestObject, dossier: Dossier,
                policy_result: PolicyResult) -> tuple[Decision, Draft]:
    """The plan for a hard-locked request, built without touching the model seam.

    This is the lockout in code. A sensitive topic is not paraphrased, drafted
    around, or summarized by a model, because it is never sent to one. The
    routing note is fixed text over dossier facts the pipeline already had.
    """
    if not policy_result.hard_locked:
        raise ValueError("locked_plan() is only for hard-locked requests.")

    decision = Decision(
        request_id=request.id,
        outcome=policy_result.outcome,
        proposed_slots=[],
        rationale=_locked_rationale(dossier, policy_result),
        trust_level="L0",
        trust_note=_trust_note("L0", True),
        delegate_to=policy_result.delegate_to,
        route_to=policy_result.route_to,
        hard_locked=True,
    )
    routed = ", ".join(policy_result.route_to) if policy_result.route_to else "Chief of Staff"
    draft = Draft(
        request_id=request.id,
        kind="internal_note",
        to=f"Zeb + {routed}",
        subject=f"Routing: {request.topic}",
        body=_locked_note(dossier),
        approval_required=True,
    )
    return decision, draft


def plan(request: RequestObject, dossier: Dossier, policy_result: PolicyResult,
         calendar: dict[str, Any], trust_categories: dict[str, Any],
         voice: dict[str, Any], derived: dict[str, Any] | None = None,
         provider: ModelProvider = DEFAULT_PROVIDER) -> tuple[Decision, Draft]:
    """Produce the Decision and the Draft for one request.

    Slots are found by deterministic calendar math before the model is called,
    so the Planner can only ever write about times that are provably free and
    policy-clean. Returns ``(Decision, Draft)``; the Draft is never auto-sent.

    Raises on a hard-locked request. The seam is the line a sensitive topic
    must not cross, so crossing it is an error rather than a special case.
    """
    if policy_result.hard_locked:
        raise ValueError(
            "Hard-locked requests must never reach the model seam. Use locked_plan()."
        )

    slots: list[Slot] = []
    if policy_result.outcome in ("accept", "defer"):
        slots = find_slots(
            calendar,
            duration_minutes=request.requested_duration_minutes,
            constraints=policy_result.constraints,
            requester_tz=dossier.timezone,
        )

    category = trust_categories.get(request.meeting_type, {})
    level = category.get("level", "L0")

    # The one model call for this stage.
    written = provider.generate("plan", {
        "request": request, "dossier": dossier, "policy": policy_result,
        "slots": slots, "voice": voice, "derived": derived or {},
    })

    decision = Decision(
        request_id=request.id,
        outcome=policy_result.outcome,
        proposed_slots=slots,
        rationale=written["rationale"],
        trust_level=level,
        trust_note=_trust_note(level, False),
        delegate_to=policy_result.delegate_to,
        route_to=policy_result.route_to,
        hard_locked=False,
    )

    internal = policy_result.outcome == "escalate_to_human"
    draft = Draft(
        request_id=request.id,
        kind="internal_note" if internal else "external_reply",
        to=("Zeb + " + (", ".join(policy_result.route_to) if policy_result.route_to else "Chief of Staff"))
        if internal else f"{dossier.requester_name} <{dossier.requester_email}>",
        subject=("Routing: " if internal else "Re: ") + request.topic,
        body=written["reply_body"],
        approval_required=True,
    )
    return decision, draft
