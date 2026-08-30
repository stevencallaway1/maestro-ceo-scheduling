"""Prioritization Agent: combine dossier + policy result into a Decision.

The written rationale is the heart of the product: 2-4 sentences of plain
English that cite dossier facts and the fired rules by name, like a sharp
chief of staff explaining their thinking. Rationale text is produced through
the ``LLMProvider`` seam (deterministic ``MockProvider`` templates in the
demo; a real model in production).
"""
from __future__ import annotations

from typing import Any

from .calendarlib import find_slots
from .llm import DEFAULT_PROVIDER, LLMProvider
from .models import Decision, Dossier, PolicyResult, RequestObject, Slot


def _rationale_template(ctx: dict[str, Any]) -> str:
    """Deterministic rationale rendering for MockProvider."""
    request: RequestObject = ctx["request"]
    dossier: Dossier = ctx["dossier"]
    policy: PolicyResult = ctx["policy"]
    slots: list[Slot] = ctx["slots"]
    deciding = next((r for r in policy.fired_rules if r.decisive), None)
    rule_cite = f'{policy.deciding_rule_id} ("{deciding.plain_english}")' if deciding else "the default policy"

    who = f"{dossier.requester_name} — {dossier.relationship_summary.split('.')[0].strip()}."
    sentences = [who]

    if policy.outcome == "escalate_to_human":
        if policy.hard_locked:
            sentences.append(
                f"The request touches a sensitive category ({dossier.sensitive_category}), "
                f"so {rule_cite} hard-locks it: Maestro takes no scheduling action and routes "
                "it straight to Zeb."
            )
            if dossier.open_threads:
                sentences.append(
                    f"Context for when he picks it up: {dossier.open_threads[-1].rstrip('.')}."
                )
        else:
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
            f"{rule_cite} applies, so the ask moves to {policy.delegate_to} with a warm redirect — "
            "routed, not rejected."
        )
    elif policy.outcome == "defer":
        blocked = ctx.get("derived", {}).get("proposed_in_protected_block")
        if blocked:
            sentences.append(
                f"The proposed time collides with a protected deep-work block, and {rule_cite} "
                "keeps those intact for everything short of board or legal."
            )
        else:
            sentences.append(
                f"No policy claims this confidently enough to act, so {rule_cite} sends it to "
                f"the Chief of Staff with the full dossier attached — relevance sits at "
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
            if dossier.timezone not in ("America/Los_Angeles",):
                tz_note = f", all landing inside {dossier.requester_name.split(' ')[0]}'s working day in {dossier.timezone.split('/')[-1].replace('_', ' ')}"
            sentences.append(f"Offering {len(slots)} policy-clean slots{tz_note}.")
    else:  # decline
        sentences.append(
            f"{rule_cite} decides it: decline. Relevance is {dossier.strategic_relevance}/100 "
            f"({dossier.relevance_justification.rstrip('.')}), so the courteous close-out "
            "protects the calendar without burning the relationship."
        )

    return " ".join(sentences)


DEFAULT_PROVIDER.register("rationale", _rationale_template)


def _trust_note(level: str, outcome: str, hard_locked: bool) -> str:
    """One line explaining what the current trust level lets Maestro do here."""
    if hard_locked:
        return "Sensitive category: hard-locked to L0 by design. Suggest-only, forever."
    notes = {
        "L0": "L0 (suggest only): Maestro recommends; a human executes everything.",
        "L1": "L1: Maestro drafts the reply and calendar hold; each one waits for approval.",
        "L2": "L2: Maestro may auto-handle low-stakes internal moves and logs everything.",
        "L3": "L3: Maestro may negotiate external scheduling within policy bounds.",
    }
    return notes.get(level, notes["L0"])


def decide(request: RequestObject, dossier: Dossier, policy_result: PolicyResult,
           calendar: dict[str, Any], trust_categories: dict[str, Any],
           derived: dict[str, Any] | None = None,
           provider: LLMProvider = DEFAULT_PROVIDER) -> Decision:
    """Produce the final Decision, including proposed slots and rationale."""
    slots: list[Slot] = []
    if policy_result.outcome in ("accept", "defer") and not policy_result.hard_locked:
        slots = find_slots(
            calendar,
            duration_minutes=request.requested_duration_minutes,
            constraints=policy_result.constraints,
            requester_tz=dossier.timezone,
        )

    category = trust_categories.get(request.meeting_type, {})
    level = "L0" if policy_result.hard_locked else category.get("level", "L0")

    rationale = provider.render("rationale", {
        "request": request, "dossier": dossier, "policy": policy_result,
        "slots": slots, "derived": derived or {},
    })

    return Decision(
        request_id=request.id,
        outcome=policy_result.outcome,
        proposed_slots=slots,
        rationale=rationale,
        trust_level=level,
        trust_note=_trust_note(level, policy_result.outcome, policy_result.hard_locked),
        delegate_to=policy_result.delegate_to,
        route_to=policy_result.route_to,
        hard_locked=policy_result.hard_locked,
    )
