"""Critic: the second and last model-backed stage.

The Critic re-reads the Planner's work before a human ever sees it and answers
one question: is this plan safe and complete enough to put in front of the CEO?

It reviews judgment, not arithmetic. Slot legality is already guaranteed by
construction - ``calendarlib.find_slots`` only ever returns times that are
free, inside business hours, and compliant with every constraint the policy
engine attached - so there is nothing for a reviewer to recompute. What a
reviewer is genuinely for is the class of mistake that deterministic code
cannot see:

  decision_match       Does the reply actually do what the decision said?
  sensitive_handling   Did a locked topic leak into an external message?
  voice_compliance     Does it sound like Zeb, and does it end with a next step?
  commitment_coverage  Does it acknowledge what Zeb already owes this person?

Each finding carries a severity. The verdict is the worst one found:

  pass    clean; at L2+ this is the only verdict eligible to execute unattended
  revise  a human must look before this goes out, whatever the trust level
  block   the plan must not execute as written

In the seeded demo every category in the request path sits at L0 or L1, so
every plan waits for a human regardless. The verdict is what the approver sees
first, and it is the gate that governs autonomy as categories climb the ladder.

One model call per request, through :mod:`maestro.llm`.
"""
from __future__ import annotations

import re
from typing import Any

from .llm import DEFAULT_PROVIDER, ModelProvider
from .models import Critique, Decision, Dossier, Draft, Finding, PolicyResult, RequestObject

CHECKS = ["decision_match", "sensitive_handling", "voice_compliance", "commitment_coverage"]

_SEVERITY_RANK = {"note": 0, "revise": 1, "block": 2}

# Phrases the CEO's voice guide rules out. Kept here rather than in voice.json
# because they are review criteria, not generation instructions.
_BANNED_PHRASES = [
    "hope this finds you well",
    "circle back",
    "touch base",
    "at your earliest convenience",
    "please advise",
    "synergy",
]

# An open thread is a standing commitment if it reads like one.
_COMMITMENT_SIGNALS = ("owed", "owes", "promised", "not yet sent", "outstanding",
                       "overdue", "committed to")

# A reply that grants time has to tell the person what happens next.
_NEXT_STEP_SIGNALS = ("pick one", "grab whichever", "my ea will send", "copying",
                      "i'll reach out", "grab it")


def _lead_phrase(thread: str) -> str:
    """The leading noun phrase of an open thread, used to test acknowledgment."""
    return re.split(r"\s*[:(]| - | owed | owes | promised | committed to ", thread)[0].strip()


def _check_decision_match(decision: Decision, draft: Draft) -> list[Finding]:
    """The reply must do what the decision said it would do."""
    findings: list[Finding] = []
    grants_time = decision.outcome in ("accept", "defer")

    if grants_time and not decision.proposed_slots:
        findings.append(Finding(
            check="decision_match", severity="block",
            message=f"Outcome is '{decision.outcome}' but no times were found. A reply that "
                    "grants time without offering any is worse than a deferral.",
        ))
    if not grants_time and decision.proposed_slots:
        findings.append(Finding(
            check="decision_match", severity="block",
            message=f"Outcome is '{decision.outcome}' but the plan carries proposed slots. "
                    "Maestro must not offer calendar time on a request it declined to take.",
        ))
    if grants_time and decision.proposed_slots:
        offered = [s for s in decision.proposed_slots if s.requester_local in draft.body]
        if not offered:
            findings.append(Finding(
                check="decision_match", severity="block",
                message="The reply does not contain any of the proposed times.",
            ))
    if decision.outcome == "escalate_to_human" and draft.kind != "internal_note":
        findings.append(Finding(
            check="decision_match", severity="block",
            message="Escalations must produce an internal routing note, never an outbound reply. "
                    "Auto-replying to an escalation is itself a judgment call.",
        ))
    return findings


def _check_sensitive_handling(dossier: Dossier, request: RequestObject,
                              policy: PolicyResult, decision: Decision,
                              draft: Draft) -> list[Finding]:
    """Locked topics must never reach an outbound message or the calendar."""
    if not policy.hard_locked:
        return []
    findings: list[Finding] = []
    if draft.kind != "internal_note":
        findings.append(Finding(
            check="sensitive_handling", severity="block",
            message=f"Sensitive category '{dossier.sensitive_category}' produced an external "
                    "reply. Locked topics are routed to a human, never answered.",
        ))
    if decision.proposed_slots:
        findings.append(Finding(
            check="sensitive_handling", severity="block",
            message="A hard-locked request must not carry calendar holds or proposed times.",
        ))
    if request.topic and request.topic.lower() in draft.body.lower():
        findings.append(Finding(
            check="sensitive_handling", severity="revise",
            message="The routing note restates the confidential subject line verbatim. "
                    "Reference the dossier instead of repeating the topic.",
        ))
    return findings


def _check_voice(draft: Draft, voice: dict[str, Any]) -> list[Finding]:
    """The draft has to sound like the CEO and end somewhere useful."""
    findings: list[Finding] = []
    body = draft.body
    lowered = body.lower()

    for phrase in _BANNED_PHRASES:
        if phrase in lowered:
            findings.append(Finding(
                check="voice_compliance", severity="revise",
                message=f'Corporate filler the voice guide rules out: "{phrase}".',
            ))
    if "!" in body:
        findings.append(Finding(
            check="voice_compliance", severity="revise",
            message="Exclamation mark in an outbound message; the voice guide rules them out.",
        ))
    if draft.kind == "external_reply":
        signoff = voice.get("signoff", "- Z")
        if not body.rstrip().endswith(signoff):
            findings.append(Finding(
                check="voice_compliance", severity="revise",
                message=f'Outbound reply does not close with the CEO signoff ("{signoff}").',
            ))
        if not any(sig in lowered for sig in _NEXT_STEP_SIGNALS):
            findings.append(Finding(
                check="voice_compliance", severity="revise",
                message="No clear next step. Every reply should end with one concrete action.",
            ))
    return findings


def _check_commitments(dossier: Dossier, draft: Draft,
                       policy: PolicyResult) -> list[Finding]:
    """Standing commitments to this person should not go unmentioned."""
    # Nothing is being sent on a locked request, so there is nothing to acknowledge.
    if policy.hard_locked or draft.kind != "external_reply":
        return []
    findings: list[Finding] = []
    body = draft.body.lower()
    for thread in dossier.open_threads:
        if not any(sig in thread.lower() for sig in _COMMITMENT_SIGNALS):
            continue
        phrase = _lead_phrase(thread)
        if phrase and phrase.lower() not in body:
            findings.append(Finding(
                check="commitment_coverage", severity="revise",
                message=f'Unmet commitment to {dossier.requester_name.split(" ")[0]} goes '
                        f'unacknowledged: "{thread.rstrip(".")}". Zeb walks in owing something '
                        "he has not mentioned.",
            ))
    return findings


def _critique_template(ctx: dict[str, Any]) -> dict[str, Any]:
    """The structured object the Critic model returns for task ``critique``."""
    request: RequestObject = ctx["request"]
    dossier: Dossier = ctx["dossier"]
    policy: PolicyResult = ctx["policy"]
    decision: Decision = ctx["decision"]
    draft: Draft = ctx["draft"]
    voice: dict[str, Any] = ctx["voice"]

    findings: list[Finding] = []
    findings += _check_decision_match(decision, draft)
    findings += _check_sensitive_handling(dossier, request, policy, decision, draft)
    findings += _check_voice(draft, voice)
    findings += _check_commitments(dossier, draft, policy)

    if not findings:
        verdict = "pass"
        summary = f"All {len(CHECKS)} checks clean. Plan is consistent with the decision and safe to queue."
    else:
        worst = max(_SEVERITY_RANK[f.severity] for f in findings)
        verdict = "block" if worst == 2 else "revise"
        noun = "issue" if len(findings) == 1 else "issues"
        summary = (f"{len(findings)} {noun} raised across "
                   f"{len({f.check for f in findings})} of {len(CHECKS)} checks.")

    return {
        "verdict": verdict,
        "summary": summary,
        "findings": [{"check": f.check, "severity": f.severity, "message": f.message}
                     for f in findings],
    }


DEFAULT_PROVIDER.register("critique", _critique_template)


def review(request: RequestObject, dossier: Dossier, policy_result: PolicyResult,
           decision: Decision, draft: Draft, voice: dict[str, Any],
           provider: ModelProvider = DEFAULT_PROVIDER) -> Critique:
    """Review one plan and return the Critique. One model call."""
    result = provider.generate("critique", {
        "request": request, "dossier": dossier, "policy": policy_result,
        "decision": decision, "draft": draft, "voice": voice,
    })
    return Critique(
        request_id=request.id,
        verdict=result["verdict"],
        findings=[Finding(**f) for f in result["findings"]],
        checks_run=list(CHECKS),
        summary=result["summary"],
    )
