"""Pipeline: orchestrates one scheduling request end-to-end.

Intake -> Context Dossier -> Policy Engine -> Decision -> Draft -> Approval
queue, with an audit entry per stage. Returns every stage's full artifact so
the UI can render the whole chain of reasoning — nothing is a black box.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from . import audit, brief, calendarlib, context, evals, intake, negotiate, optimizer, policy, prioritize, store, trust


class Pipeline:
    """Deterministic orchestrator over the seven Maestro components."""

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

        # 1. Intake
        request = intake.parse(payload, self.people)
        audit.record("intake", f"Parsed {request.channel} request from {request.requester.name}: "
                     f"{request.meeting_type}, {request.requested_duration_minutes} min, urgency {request.urgency}",
                     request_id=request.id)

        # 2. Context dossier — no decision without a dossier.
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

        # 3. Policy engine
        policy_result = policy.evaluate(request, dossier, self.rules, derived)
        fired = ", ".join(r.id for r in policy_result.fired_rules)
        audit.record("policy", f"Outcome {policy_result.outcome} via {policy_result.deciding_rule_id} "
                     f"(fired: {fired})", request_id=request.id)

        # 4. Decision + rationale
        trust_state = trust.load_state()
        decision = prioritize.decide(request, dossier, policy_result, self.calendar,
                                     trust_state["categories"], derived)
        audit.record("decision", f"Decision: {decision.outcome} at trust {decision.trust_level}"
                     + (f", {len(decision.proposed_slots)} slots proposed" if decision.proposed_slots else ""),
                     request_id=request.id)

        # 5. Draft (never auto-sent) -> approval queue
        draft = negotiate.draft(request, dossier, decision, self.voice)
        approval = self._enqueue(request, dossier, decision, draft)
        audit.record("approval_queue", f"Draft queued for approval ({draft.kind}) — id {approval['id']}",
                     request_id=request.id)
        self._log_decision(request, dossier, decision)

        return {
            "request": request.to_dict(),
            "dossier": dossier.to_dict(),
            "policy": policy_result.to_dict(),
            "decision": decision.to_dict(),
            "draft": draft.to_dict(),
            "approval_id": approval["id"],
            "derived": derived,
            "banner": {
                "sensitive_lockout": policy_result.hard_locked,
                "text": ("Sensitive category detected — this request is hard-locked to human "
                         "review (L0, permanent). Maestro built the dossier, then stopped.")
                if policy_result.hard_locked else None,
            },
        }

    def _enqueue(self, request: Any, dossier: Any, decision: Any, draft: Any) -> dict[str, Any]:
        """Add the draft to the approval queue (idempotent per request)."""
        approvals = store.load("approvals.json")
        approvals = [a for a in approvals if a.get("request_id") != request.id]
        entry = {
            "id": f"appr-{request.id}",
            "created_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "request_id": request.id,
            "category": request.meeting_type,
            "requester": dossier.requester_name,
            "kind": draft.kind,
            "outcome": decision.outcome,
            "vip": dossier.vip,
            "sensitive_category": dossier.sensitive_category,
            "summary": f"{decision.outcome.replace('_', ' ').title()}: {request.topic}",
            "draft": draft.body,
            "rationale": decision.rationale,
            "status": "pending",
        }
        approvals.append(entry)
        store.save("approvals.json", approvals)
        return entry

    def _log_decision(self, request: Any, dossier: Any, decision: Any) -> None:
        """Record the decision for the Daily Brief (idempotent per request)."""
        decisions = store.load("decisions.json")
        decisions = [d for d in decisions if d.get("request_id") != request.id]
        decisions.append({
            "decided_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "request_id": request.id,
            "requester": dossier.requester_name,
            "category": request.meeting_type,
            "outcome": decision.outcome,
            "summary": request.topic,
            "rationale": decision.rationale,
        })
        store.save("decisions.json", decisions)
