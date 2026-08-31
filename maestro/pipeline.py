"""Pipeline: runs one scheduling request end to end.

Six stages, in order, with an audit entry written at each one:

  1. Intake      deterministic  raw payload -> RequestObject
  2. Context     deterministic  who this person is -> Dossier
  3. Policy      deterministic  hand-editable rules -> PolicyResult
  4. Planner     model-backed   decision + rationale + reply draft
  5. Critic      model-backed   review of the plan before a human sees it
  6. Approval    deterministic  routes the plan to the queue

Two of the six call a model, once each. The stages that decide what is allowed,
what gets booked, and what gets recorded are all deterministic, which is what
makes the system auditable and safe to point at a real calendar.

Every stage returns its full artifact so the UI can render the whole chain of
reasoning. Nothing here is a black box.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import audit, calendarlib, context, critic, intake, planner, policy, store, trust
from .models import OUTCOME_LABEL


class Pipeline:
    """Deterministic orchestrator over the six stages."""

    def __init__(self) -> None:
        self.people = store.load("people.json")
        self.history = store.load("history.json")
        self.rules = store.load("policies.json")["rules"]
        self.calendar = store.load("calendar.json")
        self.voice = store.load("voice.json")

    def inbox(self) -> list[dict[str, Any]]:
        """The mock inbox of incoming requests."""
        return store.load("requests.json")

    def run(self, request_id: str) -> dict[str, Any]:
        """Run one request through every stage; return all artifacts."""
        payload = next((r for r in self.inbox() if r["id"] == request_id), None)
        if payload is None:
            raise KeyError(f"Unknown request id: {request_id}")

        # 1. Intake - normalize the raw payload.
        request = intake.parse(payload, self.people)
        audit.record("intake", f"Parsed {request.channel} request from {request.requester.name}: "
                     f"{request.meeting_type}, {request.requested_duration_minutes} min, "
                     f"urgency {request.urgency}", request_id=request.id)

        # 2. Context dossier - no decision without a dossier.
        dossier = context.build(request, self.people, self.history)
        audit.record("context", f"Dossier built for {dossier.requester_name} "
                     f"(relevance {dossier.strategic_relevance}/100, VIP={dossier.vip}, "
                     f"sensitive={dossier.sensitive_category or 'none'})", request_id=request.id)

        # Derived calendar facts feed the policy engine.
        derived = {
            "proposed_in_protected_block": bool(
                request.proposed_start and calendarlib.in_protected_block(
                    self.calendar, request.proposed_start, request.requested_duration_minutes)
            ),
        }

        # 3. Policy engine - what the rules allow.
        policy_result = policy.evaluate(request, dossier, self.rules, derived)
        fired = ", ".join(r.id for r in policy_result.fired_rules)
        audit.record("policy", f"Outcome {policy_result.outcome} via "
                     f"{policy_result.deciding_rule_id} (fired: {fired})", request_id=request.id)

        # 4. Planner (model call 1) - the decision, its rationale, and the draft.
        trust_state = trust.load_state()
        decision, draft = planner.plan(request, dossier, policy_result, self.calendar,
                                       trust_state["categories"], self.voice, derived)
        audit.record("planner", f"Plan: {decision.outcome} at trust {decision.trust_level}"
                     + (f", {len(decision.proposed_slots)} slots proposed"
                        if decision.proposed_slots else ""), request_id=request.id)

        # 5. Critic (model call 2) - review the plan before a human sees it.
        critique = critic.review(request, dossier, policy_result, decision, draft, self.voice)
        audit.record("critic", f"Critique: {critique.verdict} - {critique.summary}",
                     request_id=request.id)

        # 6. Approval routing - nothing is ever sent or booked without a human.
        approval = self._enqueue(request, dossier, decision, draft, critique)
        audit.record("approval_queue", f"Draft queued for approval ({draft.kind}) - "
                     f"id {approval['id']}", request_id=request.id)
        self._log_decision(request, dossier, decision, critique)

        return {
            "request": request.to_dict(),
            "dossier": dossier.to_dict(),
            "policy": policy_result.to_dict(),
            "decision": decision.to_dict(),
            "critique": critique.to_dict(),
            "draft": draft.to_dict(),
            "approval_id": approval["id"],
            "derived": derived,
            "banner": {
                "sensitive_lockout": policy_result.hard_locked,
                "text": ("Sensitive category detected - this request is hard-locked to human "
                         "review (L0, permanent). Maestro built the dossier, then stopped.")
                if policy_result.hard_locked else None,
            },
        }

    def find_approval(self, approval_id: str) -> dict[str, Any] | None:
        """Fetch a queued approval, rebuilding it if this process never saw it.

        On a serverless host state lives in the instance that created it. If a
        reviewer runs a request and then approves it after a cold start, the
        queue entry would be gone. The pipeline is deterministic, so re-running
        the originating request reproduces the identical entry and the demo
        recovers instead of failing. Nothing is invented: same input, same plan.

        Returns None if no such approval could exist.
        """
        entry = next((a for a in store.load("approvals.json") if a["id"] == approval_id), None)
        if entry is not None:
            return entry
        if not approval_id.startswith("appr-"):
            return None
        request_id = approval_id[len("appr-"):]
        if not any(r["id"] == request_id for r in self.inbox()):
            return None
        self.run(request_id)
        return next((a for a in store.load("approvals.json") if a["id"] == approval_id), None)

    def _enqueue(self, request: Any, dossier: Any, decision: Any, draft: Any,
                 critique: Any) -> dict[str, Any]:
        """Add the plan to the approval queue (idempotent per request)."""
        approvals = [a for a in store.load("approvals.json")
                     if a.get("request_id") != request.id]
        entry = {
            "id": f"appr-{request.id}",
            "created_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "request_id": request.id,
            "category": request.meeting_type,
            "requester": dossier.requester_name,
            "requester_email": dossier.requester_email,
            "topic": request.topic,
            "kind": draft.kind,
            "outcome": decision.outcome,
            "vip": dossier.vip,
            "sensitive_category": dossier.sensitive_category,
            "summary": f"{OUTCOME_LABEL.get(decision.outcome, decision.outcome)}: {request.topic}",
            "draft": draft.body,
            "rationale": decision.rationale,
            "slots": [s.__dict__ for s in decision.proposed_slots],
            "routed_to": decision.delegate_to or ", ".join(decision.route_to) or None,
            "critic_verdict": critique.verdict,
            "critic_summary": critique.summary,
            "critic_findings": [f.__dict__ for f in critique.findings],
            "status": "pending",
        }
        approvals.append(entry)
        store.save("approvals.json", approvals)
        return entry

    def _log_decision(self, request: Any, dossier: Any, decision: Any,
                      critique: Any) -> None:
        """Record the decision for the Daily Brief (idempotent per request)."""
        decisions = [d for d in store.load("decisions.json")
                     if d.get("request_id") != request.id]
        decisions.append({
            "decided_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "request_id": request.id,
            "requester": dossier.requester_name,
            "category": request.meeting_type,
            "outcome": decision.outcome,
            "summary": request.topic,
            "rationale": decision.rationale,
            "critic_verdict": critique.verdict,
        })
        store.save("decisions.json", decisions)
