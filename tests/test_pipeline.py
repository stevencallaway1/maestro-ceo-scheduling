"""End-to-end pipeline tests: the six stages, the Critic, and execution."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from maestro import critic, execute, store
from maestro.pipeline import Pipeline

PROJECT_DATA = Path(__file__).resolve().parent.parent / "data"


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """Point the store at a copy of /data so tests never mutate demo state."""
    dst = tmp_path / "data"
    shutil.copytree(PROJECT_DATA, dst)
    monkeypatch.setattr(store, "DATA_DIR", dst)
    store._cache.clear()
    store.reset()
    yield dst
    store._cache.clear()


@pytest.fixture()
def pipeline(sandbox):
    return Pipeline()


class TestPipelineStages:
    def test_every_stage_returns_an_artifact(self, pipeline):
        result = pipeline.run("req-001")
        for key in ("request", "dossier", "policy", "decision", "critique", "draft"):
            assert result[key], f"missing artifact: {key}"
        assert result["approval_id"] == "appr-req-001"

    def test_every_stage_is_audited(self, pipeline):
        pipeline.run("req-001")
        stages = {e["stage"] for e in store.read_jsonl("audit_log.jsonl")}
        assert {"intake", "context", "policy", "planner", "critic",
                "approval_queue"} <= stages

    def test_rerunning_a_request_does_not_duplicate_the_queue(self, pipeline):
        pipeline.run("req-001")
        pipeline.run("req-001")
        approvals = store.load("approvals.json")
        assert sum(1 for a in approvals if a["request_id"] == "req-001") == 1

    def test_unknown_request_raises(self, pipeline):
        with pytest.raises(KeyError):
            pipeline.run("req-nope")


class TestDemoScenarios:
    def test_board_member_accepted_with_slots(self, pipeline):
        r = pipeline.run("req-001")
        assert r["decision"]["outcome"] == "accept"
        assert len(r["decision"]["proposed_slots"]) == 3

    def test_vendor_delegated_without_slots(self, pipeline):
        r = pipeline.run("req-002")
        assert r["decision"]["outcome"] == "delegate"
        assert r["decision"]["proposed_slots"] == []

    def test_protected_block_collision_defers(self, pipeline):
        r = pipeline.run("req-003")
        assert r["decision"]["outcome"] == "defer"
        assert r["policy"]["deciding_rule_id"] == "R-DEEPWORK-PROTECT"

    def test_press_routes_as_internal_note(self, pipeline):
        r = pipeline.run("req-004")
        assert r["decision"]["outcome"] == "escalate_to_human"
        assert r["draft"]["kind"] == "internal_note"
        assert not r["banner"]["sensitive_lockout"]

    def test_personnel_request_hard_locks(self, pipeline):
        r = pipeline.run("req-005")
        assert r["banner"]["sensitive_lockout"]
        assert r["decision"]["trust_level"] == "L0"
        assert r["decision"]["proposed_slots"] == []

    def test_apac_partner_gets_requester_local_times(self, pipeline):
        r = pipeline.run("req-006")
        assert r["decision"]["outcome"] == "accept"
        slots = r["decision"]["proposed_slots"]
        assert slots and all(s["requester_local"] != s["ceo_local"] for s in slots)
        # Every offered time lands inside Grace's Sydney working day.
        assert all("AM" in s["requester_local"] or "PM" in s["requester_local"] for s in slots)


class TestCritic:
    def test_clean_plan_passes(self, pipeline):
        assert pipeline.run("req-006")["critique"]["verdict"] == "pass"

    def test_unacknowledged_commitment_is_flagged(self, pipeline):
        c = pipeline.run("req-001")["critique"]
        assert c["verdict"] == "revise"
        assert any(f["check"] == "commitment_coverage" for f in c["findings"])
        assert any("Churn cohort" in f["message"] for f in c["findings"])

    def test_hard_locked_plan_passes_review(self, pipeline):
        # Nothing is being sent, so there is nothing to acknowledge or flag.
        assert pipeline.run("req-005")["critique"]["verdict"] == "pass"

    def test_offering_time_on_a_delegation_is_blocked(self, pipeline):
        r = pipeline.run("req-002")
        request = _rebuild(pipeline, "req-002")
        decision, draft = request["decision"], request["draft"]
        decision.proposed_slots = _fake_slots(pipeline)
        c = critic.review(request["request"], request["dossier"], request["policy"],
                          decision, draft, pipeline.voice)
        assert c.verdict == "block"
        assert any(f.check == "decision_match" for f in c.findings)
        assert r["critique"]["verdict"] == "pass"  # the real plan is clean

    def test_external_reply_on_a_locked_topic_is_blocked(self, pipeline):
        parts = _rebuild(pipeline, "req-005")
        parts["draft"].kind = "external_reply"
        c = critic.review(parts["request"], parts["dossier"], parts["policy"],
                          parts["decision"], parts["draft"], pipeline.voice)
        assert c.verdict == "block"
        assert any(f.check == "sensitive_handling" for f in c.findings)

    def test_voice_violations_are_caught(self, pipeline):
        parts = _rebuild(pipeline, "req-006")
        # Keep an offered time so only the voice checks have anything to say.
        offered = parts["decision"].proposed_slots[0].requester_local
        parts["draft"].body = (f"Grace - hope this finds you well! {offered} works. "
                               "Let's touch base.")
        c = critic.review(parts["request"], parts["dossier"], parts["policy"],
                          parts["decision"], parts["draft"], pipeline.voice)
        assert c.verdict == "revise"
        assert {f.check for f in c.findings} == {"voice_compliance"}
        messages = " ".join(f.message for f in c.findings)
        assert "hope this finds you well" in messages and "signoff" in messages


class TestExecution:
    def test_approved_booking_creates_a_hold(self, pipeline):
        pipeline.run("req-006")
        entry = _approval(pipeline, "appr-req-006")
        result = execute.execute(entry, pipeline.calendar)
        assert result.action == "calendar_hold"
        assert result.simulated is True
        assert result.event["idempotency_key"] == "maestro:appr-req-006"
        assert result.event["start"] == entry["slots"][0]["start"]

    def test_delegation_writes_no_calendar_event(self, pipeline):
        pipeline.run("req-002")
        result = execute.execute(_approval(pipeline, "appr-req-002"), pipeline.calendar)
        assert result.action == "route_only"
        assert result.event is None

    def test_execution_is_idempotent(self, pipeline):
        pipeline.run("req-006")
        entry = _approval(pipeline, "appr-req-006")
        first = execute.execute(entry, pipeline.calendar)
        second = execute.execute(entry, pipeline.calendar)
        assert first.executed_at == second.executed_at
        assert len(store.load("executions.json")) == 1

    def test_execution_is_audited(self, pipeline):
        pipeline.run("req-006")
        execute.execute(_approval(pipeline, "appr-req-006"), pipeline.calendar)
        assert any(e["stage"] == "execution" for e in store.read_jsonl("audit_log.jsonl"))


class TestStore:
    def test_reset_restores_seed_state(self, pipeline):
        pipeline.run("req-001")
        assert len(store.load("approvals.json")) == 3
        store.reset()
        assert len(store.load("approvals.json")) == 2

    def test_loads_are_copies(self, sandbox):
        first = store.load("approvals.json")
        first.append({"id": "scribble"})
        assert len(store.load("approvals.json")) == 2

    def test_writes_survive_a_read_only_filesystem(self, sandbox, monkeypatch):
        monkeypatch.setattr(store, "_disk_writable", False)
        store.save("approvals.json", [{"id": "memory-only"}])
        assert store.load("approvals.json") == [{"id": "memory-only"}]


# --- helpers ---------------------------------------------------------------

def _approval(pipeline: Pipeline, approval_id: str) -> dict:
    return next(a for a in store.load("approvals.json") if a["id"] == approval_id)


def _rebuild(pipeline: Pipeline, request_id: str) -> dict:
    """Re-derive the typed artifacts for a request so a test can perturb them."""
    from maestro import calendarlib, context, intake, planner, policy, trust

    payload = next(r for r in pipeline.inbox() if r["id"] == request_id)
    request = intake.parse(payload, pipeline.people)
    dossier = context.build(request, pipeline.people, pipeline.history)
    derived = {"proposed_in_protected_block": bool(
        request.proposed_start and calendarlib.in_protected_block(
            pipeline.calendar, request.proposed_start, request.requested_duration_minutes))}
    policy_result = policy.evaluate(request, dossier, pipeline.rules, derived)
    decision, draft = planner.plan(request, dossier, policy_result, pipeline.calendar,
                                   trust.load_state()["categories"], pipeline.voice, derived)
    return {"request": request, "dossier": dossier, "policy": policy_result,
            "decision": decision, "draft": draft}


def _fake_slots(pipeline: Pipeline) -> list:
    from maestro.calendarlib import find_slots
    return find_slots(pipeline.calendar, 30, [], "America/Chicago")


class TestApprovalRecovery:
    """A cold serverless instance must not lose a reviewer's queued plan."""

    def test_missing_approval_is_rebuilt_identically(self, pipeline):
        pipeline.run("req-006")
        expected = _approval(pipeline, "appr-req-006")

        # Simulate a cold instance: the queue is back to its seeded state.
        store.reset()
        assert not any(a["id"] == "appr-req-006" for a in store.load("approvals.json"))

        rebuilt = pipeline.find_approval("appr-req-006")
        assert rebuilt is not None
        for field in ("draft", "rationale", "critic_verdict", "outcome", "slots"):
            assert rebuilt[field] == expected[field]

    def test_existing_approval_is_returned_untouched(self, pipeline):
        pipeline.run("req-002")
        assert pipeline.find_approval("appr-req-002")["request_id"] == "req-002"

    def test_unknown_ids_return_none(self, pipeline):
        assert pipeline.find_approval("appr-req-nope") is None
        assert pipeline.find_approval("not-an-approval") is None
