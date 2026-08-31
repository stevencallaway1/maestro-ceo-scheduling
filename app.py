"""Maestro demo server.

FastAPI backend plus a static single-page UI. Run with ``python app.py`` (or
``make run``) and open http://localhost:8000 - zero config, zero network calls
at runtime.
"""
from __future__ import annotations

import threading
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from maestro import audit, brief, evals, execute, store, trust
from maestro.pipeline import Pipeline

ROOT = Path(__file__).resolve().parent
app = FastAPI(title="Maestro", docs_url=None, redoc_url=None)
pipeline = Pipeline()

# Serializes the human-verdict transition. The endpoints below are sync, so the
# server runs them in a threadpool and two clicks can land at once. Deciding
# whether an item is still pending and then acting on it has to be one step: a
# read-check-write that interleaves records the same verdict twice in the audit
# log and loses one of the two writes to overrides.json, which is the file the
# trust ladder is computed from. One lock is enough - the transition is short,
# in-process, and every deployment of this app is single-instance by design.
_verdict_lock = threading.Lock()


class OverrideBody(BaseModel):
    """One-line human reason for reversing a Maestro decision."""

    reason: str


class ApproveBody(BaseModel):
    """Approval payload. ``acknowledge_critic`` is the Critic gate.

    A plan the Critic marked ``revise`` cannot be approved without it. The flag
    means a human read the findings and chose to send anyway, and that choice is
    recorded rather than assumed.
    """

    acknowledge_critic: bool = False


@app.get("/api/status")
def status() -> dict[str, Any]:
    """What is real and what is simulated in this deployment."""
    return {
        "model_calls": "simulated",
        "calendar_writes": "simulated",
        "state_persistence": "disk" if store.disk_writable() else "memory",
        "model_call_cap": 2,
        "note": ("Planner and Critic run on deterministic templates behind the model seam; "
                 "the calendar adapter builds the event payload but makes no external call. "
                 "Hard-locked requests skip both model stages entirely, so they cost zero "
                 "calls - each run reports its own count."),
    }


@app.get("/api/requests")
def list_requests() -> list[dict[str, Any]]:
    """The mock inbox."""
    return pipeline.inbox()


@app.post("/api/pipeline/run/{request_id}")
def run_pipeline(request_id: str) -> dict[str, Any]:
    """Run one request end-to-end and return every stage artifact."""
    try:
        return pipeline.run(request_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/brief")
def daily_brief() -> dict[str, Any]:
    """The Daily Brief: markdown + optimizer findings + approval queue."""
    return brief.generate()


@app.get("/api/trust")
def trust_report() -> dict[str, Any]:
    """Trust ladder state, eval metrics, and promotion progress."""
    return evals.full_report()


@app.get("/api/audit")
def audit_log(limit: int = 200) -> list[dict[str, Any]]:
    """Most recent audit entries, newest first."""
    return audit.tail(limit)


def _find_approval(approval_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Look up a queued approval, or 404. Rebuilds after a cold start."""
    if pipeline.find_approval(approval_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown approval id: {approval_id}")
    approvals = store.load("approvals.json")
    entry = next(a for a in approvals if a["id"] == approval_id)
    return approvals, entry


def _record_human_action(entry: dict[str, Any], action: str, reason: str | None,
                         critical: bool, critic_override: bool = False) -> None:
    """Append the human's verdict to overrides.json - the eval loop's input.

    Called exactly once per approval. Both endpoints refuse to act twice on the
    same queue item, because a duplicated verdict would silently skew the
    acceptance rate that promotes and demotes categories on the trust ladder.
    """
    overrides = store.load("overrides.json")
    overrides.append({
        "id": f"ov-live-{len(overrides) + 1:03d}",
        "date": date.today().isoformat(),
        "category": entry["category"],
        "summary": entry["summary"],
        "system_decision": entry.get("outcome", "accept"),
        "human_action": action,
        "reason": reason,
        "critical_miss": critical,
        "critic_verdict": entry.get("critic_verdict"),
        # True when a human sent a plan the Critic had flagged for revision.
        "critic_override": critic_override,
    })
    store.save("overrides.json", overrides)


def _settled(entry: dict[str, Any]) -> dict[str, Any] | None:
    """The stored result if this item was already actioned, else None.

    Approving or overriding the same plan twice must not record two human
    verdicts, two audit lines, or two trust demotions. Replays are answered
    from the record instead.
    """
    if entry.get("status") == "pending":
        return None
    return {
        "ok": True,
        "idempotent": True,
        "approval": entry,
        "execution": entry.get("execution"),
        "detail": f"Already {entry.get('status')}. No second verdict recorded.",
    }


def _critic_gate(entry: dict[str, Any], acknowledged: bool) -> None:
    """Refuse to execute a plan the Critic did not clear. Raises or returns.

    This is the gate, and it lives on the server so it holds whatever the UI
    does. ``block`` is not approvable at all. ``revise`` is approvable only by a
    human who has seen the findings and says so.
    """
    verdict = entry.get("critic_verdict")
    findings = [f["message"] for f in entry.get("critic_findings", [])]
    if verdict == "block":
        raise HTTPException(status_code=409, detail={
            "error": "critic_block",
            "message": "The Critic blocked this plan; it cannot be approved as written. "
                       "Override it instead.",
            "findings": findings,
        })
    if verdict == "revise" and not acknowledged:
        raise HTTPException(status_code=409, detail={
            "error": "critic_revise",
            "message": "The Critic flagged this plan for revision. Approving it requires "
                       "acknowledging the findings first.",
            "findings": findings,
        })


@app.post("/api/approvals/{approval_id}/approve")
def approve(approval_id: str, body: ApproveBody = ApproveBody()) -> dict[str, Any]:
    """Human approves a plan. Executes it, then feeds the acceptance metrics.

    Two things have to be true before anything executes: the Critic cleared the
    plan (or a human acknowledged its findings), and this item has not already
    been actioned. The whole transition is serialized, so simultaneous clicks
    resolve to one verdict rather than racing each other.
    """
    with _verdict_lock:
        approvals, entry = _find_approval(approval_id)
        settled = _settled(entry)
        if settled is not None:
            return settled
        _critic_gate(entry, body.acknowledge_critic)

        over_critic = entry.get("critic_verdict") == "revise"
        entry["status"] = "approved"
        _record_human_action(entry, "approved", None, critical=False,
                             critic_override=over_critic)
        audit.record("human_review",
                     (f"APPROVED OVER CRITIC (revise): {entry['summary']}" if over_critic
                      else f"APPROVED: {entry['summary']}"),
                     actor="human", request_id=entry.get("request_id"))

        result = execute.execute(entry, pipeline.calendar)
        entry["execution"] = result.to_dict()
        store.save("approvals.json", approvals)
        return {"ok": True, "approval": entry, "execution": result.to_dict()}


@app.post("/api/approvals/{approval_id}/override")
def override(approval_id: str, body: OverrideBody) -> dict[str, Any]:
    """Human overrides a plan with a one-line reason. Nothing is executed.

    Writes to overrides.json (the eval loop) and, if the reversal hits a
    sensitive category or a VIP, triggers an automatic trust-ladder demotion.
    Overriding the same item twice is a no-op: one reversal, one demotion.
    """
    with _verdict_lock:
        approvals, entry = _find_approval(approval_id)
        settled = _settled(entry)
        if settled is not None:
            return settled

        entry["status"] = "overridden"
        entry["override_reason"] = body.reason
        store.save("approvals.json", approvals)

        state = trust.load_state()
        critical = trust.is_critical_miss(
            entry["category"], bool(entry.get("vip")), entry.get("sensitive_category"), state)
        _record_human_action(entry, "overridden", body.reason, critical=critical)
        audit.record("human_review", f"OVERRIDDEN: {entry['summary']} - \"{body.reason}\"",
                     actor="human", request_id=entry.get("request_id"))

        demotion = trust.record_critical_miss(entry["category"], body.reason) if critical else None
        return {"ok": True, "approval": entry, "critical_miss": critical, "demotion": demotion}


@app.post("/api/reset")
def reset_demo() -> dict[str, Any]:
    """Restore the seeded demo state so the next reviewer starts clean."""
    files = store.reset()
    audit.record("reset", "Demo state restored from seed.", actor="human")
    return {"ok": True, "reset": files}


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/")
def index() -> FileResponse:
    """Serve the single-page UI."""
    return FileResponse(ROOT / "static" / "index.html")


if __name__ == "__main__":
    import uvicorn

    print("\n  Maestro - AI scheduling for the Office of the CEO")
    print("  http://localhost:8000\n")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
