"""Maestro demo server.

FastAPI backend + static single-page UI. Run with ``python app.py`` (or
``make run``) and open http://localhost:8000 — zero config, zero network
dependencies at runtime.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from maestro import audit, brief, evals, optimizer, store, trust
from maestro.pipeline import Pipeline

ROOT = Path(__file__).resolve().parent
app = FastAPI(title="Maestro", docs_url=None, redoc_url=None)
pipeline = Pipeline()


class OverrideBody(BaseModel):
    """One-line human reason for reversing a Maestro decision."""

    reason: str


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


@app.get("/api/optimizer")
def optimizer_findings() -> dict[str, Any]:
    """Raw optimizer findings for the current calendar."""
    return optimizer.run(store.load("calendar.json"))


@app.get("/api/trust")
def trust_report() -> dict[str, Any]:
    """Trust ladder state, eval metrics, and promotion progress."""
    return evals.full_report()


@app.get("/api/audit")
def audit_log(limit: int = 200) -> list[dict[str, Any]]:
    """Most recent audit entries, newest first."""
    return audit.tail(limit)


def _find_approval(approval_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    approvals = store.load("approvals.json")
    entry = next((a for a in approvals if a["id"] == approval_id), None)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Unknown approval id: {approval_id}")
    return approvals, entry


def _record_human_action(entry: dict[str, Any], action: str, reason: str | None,
                         critical: bool) -> None:
    """Append the human's verdict to overrides.json — the eval flywheel."""
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
    })
    store.save("overrides.json", overrides)


@app.post("/api/approvals/{approval_id}/approve")
def approve(approval_id: str) -> dict[str, Any]:
    """Human approves a queued draft. Feeds acceptance metrics."""
    approvals, entry = _find_approval(approval_id)
    entry["status"] = "approved"
    store.save("approvals.json", approvals)
    _record_human_action(entry, "approved", None, critical=False)
    audit.record("human_review", f"APPROVED: {entry['summary']}", actor="human",
                 request_id=entry.get("request_id"))
    return {"ok": True, "approval": entry}


@app.post("/api/approvals/{approval_id}/override")
def override(approval_id: str, body: OverrideBody) -> dict[str, Any]:
    """Human overrides a queued draft with a one-line reason.

    Writes to overrides.json (eval flywheel) and, if the reversal hits a
    sensitive category or VIP, triggers an automatic trust-ladder demotion.
    """
    approvals, entry = _find_approval(approval_id)
    entry["status"] = "overridden"
    entry["override_reason"] = body.reason
    store.save("approvals.json", approvals)

    state = trust.load_state()
    critical = trust.is_critical_miss(
        entry["category"], bool(entry.get("vip")), entry.get("sensitive_category"), state)
    _record_human_action(entry, "overridden", body.reason, critical=critical)
    audit.record("human_review", f"OVERRIDDEN: {entry['summary']} — \"{body.reason}\"",
                 actor="human", request_id=entry.get("request_id"))

    demotion = trust.record_critical_miss(entry["category"], body.reason) if critical else None
    return {"ok": True, "approval": entry, "critical_miss": critical, "demotion": demotion}


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/")
def index() -> FileResponse:
    """Serve the single-page UI."""
    return FileResponse(ROOT / "static" / "index.html")


if __name__ == "__main__":
    print("\n  Maestro — AI scheduling for the Office of the CEO")
    print("  http://localhost:8000\n")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
