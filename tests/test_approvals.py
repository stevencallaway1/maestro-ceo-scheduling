"""Approval endpoint tests: the Critic gate and one-verdict-per-plan.

These call the endpoint functions directly rather than over HTTP. The rules
being tested - what may be approved, and how many times - live in the function
bodies, so exercising them this way keeps the suite dependency-free while
still covering the behavior that matters.
"""
from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path

import pytest
from fastapi import HTTPException

import app as server
from maestro import store
from maestro.pipeline import Pipeline

PROJECT_DATA = Path(__file__).resolve().parent.parent / "data"


@pytest.fixture()
def api(tmp_path, monkeypatch):
    """A server whose state lives in a sandboxed copy of /data."""
    dst = tmp_path / "data"
    shutil.copytree(PROJECT_DATA, dst)
    monkeypatch.setattr(store, "DATA_DIR", dst)
    store._cache.clear()
    store.reset()
    monkeypatch.setattr(server, "pipeline", Pipeline())
    yield server
    store._cache.clear()


@pytest.fixture()
def slow_writes(monkeypatch):
    """Widen the read-check-write window so an unserialized transition loses.

    Without this the threads finish too fast to interleave reliably and the
    concurrency tests would pass against broken code.
    """
    real_save = store.save

    def save(name, data):
        time.sleep(0.02)
        return real_save(name, data)

    monkeypatch.setattr(store, "save", save)
    yield


def _live_overrides() -> list[dict]:
    """Only the verdicts recorded by this run, not the seeded history."""
    return [o for o in store.load("overrides.json") if o["id"].startswith("ov-live-")]


def _human_reviews() -> list[dict]:
    return [e for e in store.read_jsonl("audit_log.jsonl") if e["stage"] == "human_review"]


def _queue(approval_id: str) -> dict:
    return next(a for a in store.load("approvals.json") if a["id"] == approval_id)


class TestCriticGate:
    """A verdict the UI can ignore is not a gate. This one lives on the server."""

    def test_revise_cannot_be_approved_without_acknowledgment(self, api):
        api.pipeline.run("req-001")  # Dana: unacknowledged commitment -> revise
        assert _queue("appr-req-001")["critic_verdict"] == "revise"

        with pytest.raises(HTTPException) as exc:
            api.approve("appr-req-001")

        assert exc.value.status_code == 409
        assert exc.value.detail["error"] == "critic_revise"
        assert exc.value.detail["findings"], "the refusal has to say what was wrong"
        # Nothing moved: no execution, no verdict, still pending.
        assert _queue("appr-req-001")["status"] == "pending"
        assert store.load("executions.json") == []
        assert _live_overrides() == []

    def test_acknowledged_revise_goes_through_and_is_recorded_as_such(self, api):
        api.pipeline.run("req-001")
        res = api.approve("appr-req-001", server.ApproveBody(acknowledge_critic=True))

        assert res["ok"] and res["approval"]["status"] == "approved"
        row = _live_overrides()[0]
        assert row["human_action"] == "approved"
        assert row["critic_override"] is True
        assert row["critic_verdict"] == "revise"
        assert "APPROVED OVER CRITIC" in _human_reviews()[0]["summary"]

    def test_block_cannot_be_approved_at_all(self, api):
        api.pipeline.run("req-006")
        approvals = store.load("approvals.json")
        entry = next(a for a in approvals if a["id"] == "appr-req-006")
        entry["critic_verdict"] = "block"
        entry["critic_findings"] = [{"check": "decision_match", "severity": "block",
                                     "message": "The reply does not contain any of the "
                                                "proposed times."}]
        store.save("approvals.json", approvals)

        # Acknowledgment is not an escape hatch for a block.
        for ack in (False, True):
            with pytest.raises(HTTPException) as exc:
                api.approve("appr-req-006", server.ApproveBody(acknowledge_critic=ack))
            assert exc.value.status_code == 409
            assert exc.value.detail["error"] == "critic_block"

        assert _queue("appr-req-006")["status"] == "pending"
        assert store.load("executions.json") == []

    def test_clean_plan_needs_no_acknowledgment(self, api):
        api.pipeline.run("req-006")  # Grace: clean pass
        res = api.approve("appr-req-006")
        assert res["approval"]["status"] == "approved"
        assert _live_overrides()[0]["critic_override"] is False


class TestVerdictIsRecordedOnce:
    """A duplicated verdict silently skews the metric that moves the ladder."""

    def test_approving_twice_records_one_verdict_and_one_execution(self, api):
        api.pipeline.run("req-006")
        first = api.approve("appr-req-006")
        second = api.approve("appr-req-006")

        assert second["idempotent"] is True
        assert "Already approved" in second["detail"]
        assert len(_live_overrides()) == 1
        assert len(_human_reviews()) == 1
        assert len(store.load("executions.json")) == 1
        # The replay answers from the record, not from a second booking.
        assert second["execution"]["executed_at"] == first["execution"]["executed_at"]

    def test_overriding_twice_demotes_once(self, api):
        api.pipeline.run("req-006")  # Grace is a VIP: reversal is a critical miss
        body = server.OverrideBody(reason="Wrong week, she is mid-diligence.")

        first = api.override("appr-req-006", body)
        second = api.override("appr-req-006", body)

        assert first["critical_miss"] is True and first["demotion"]
        assert second["idempotent"] is True
        assert "demotion" not in second
        assert len(_live_overrides()) == 1
        assert len(_human_reviews()) == 1

        # One new demotion on top of the seeded history, not two.
        history = store.load("trust.json")["categories"]["external_partner"]["history"]
        assert sum(1 for h in history if body.reason in h["reason"]) == 1

    def test_simultaneous_approvals_resolve_to_one_verdict(self, api, slow_writes):
        """Two clicks can land at once; the endpoints are sync, so they run in
        parallel threads. Deciding an item is pending and then acting on it has
        to be one atomic step, or the audit log gets a line per thread and one
        of the two writes to overrides.json is silently lost."""
        api.pipeline.run("req-006")

        results, errors = [], []

        def approve():
            try:
                results.append(api.approve("appr-req-006"))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=approve) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(_live_overrides()) == 1
        assert len(_human_reviews()) == 1
        assert len(store.load("executions.json")) == 1
        # Exactly one thread performed the transition; the rest saw the record.
        assert sum(1 for r in results if r.get("idempotent")) == 4

    def test_simultaneous_overrides_demote_once(self, api, slow_writes):
        api.pipeline.run("req-006")
        body = server.OverrideBody(reason="Mid-diligence, do not move her.")

        def override():
            api.override("appr-req-006", body)

        threads = [threading.Thread(target=override) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(_live_overrides()) == 1
        assert len(_human_reviews()) == 1
        history = store.load("trust.json")["categories"]["external_partner"]["history"]
        assert sum(1 for h in history if body.reason in h["reason"]) == 1

    def test_an_overridden_plan_cannot_later_be_approved(self, api):
        api.pipeline.run("req-006")
        api.override("appr-req-006", server.OverrideBody(reason="Not this week."))

        res = api.approve("appr-req-006")
        assert res["idempotent"] is True
        assert _queue("appr-req-006")["status"] == "overridden"
        assert store.load("executions.json") == []
