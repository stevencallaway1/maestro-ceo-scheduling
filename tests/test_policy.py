"""Policy Engine tests: rule evaluation, ordering, and the six demo scenarios."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from maestro import policy
from maestro.models import Dossier, Requester, RequestObject

DATA = Path(__file__).resolve().parent.parent / "data"
RULES = json.loads((DATA / "policies.json").read_text())["rules"]


def make_request(meeting_type: str, internal: bool = False, **kw) -> RequestObject:
    return RequestObject(
        id=kw.get("id", "req-test"),
        requester=Requester(name="Test", email="t@x.com", internal=internal),
        requested_duration_minutes=30,
        urgency="normal",
        urgency_signal="test",
        topic="Test topic",
        meeting_type=meeting_type,
        channel="email",
        proposed_start=kw.get("proposed_start"),
        raw_source_text="raw",
        received_at="2026-08-31T07:00:00-07:00",
    )


def make_dossier(relevance: int = 50, vip: bool = False, sensitive: str | None = None,
                 interactions: int = 3) -> Dossier:
    return Dossier(
        requester_email="t@x.com", requester_name="Test",
        relationship_summary="Test person.", last_interactions=[], open_threads=[],
        strategic_relevance=relevance, relevance_justification="test",
        sensitive_category=sensitive, timezone="America/New_York", vip=vip,
        interaction_count=interactions, known_person=True,
    )


class TestConditionEvaluator:
    def test_leaf_ops(self):
        ctx = {"a": {"b": 5, "s": "hello", "flag": True}}
        assert policy._eval_condition({"field": "a.b", "op": "eq", "value": 5}, ctx)
        assert policy._eval_condition({"field": "a.b", "op": "gte", "value": 5}, ctx)
        assert policy._eval_condition({"field": "a.b", "op": "lt", "value": 6}, ctx)
        assert policy._eval_condition({"field": "a.s", "op": "in", "value": ["hello", "x"]}, ctx)
        assert not policy._eval_condition({"field": "a.b", "op": "ne", "value": 5}, ctx)
        assert not policy._eval_condition({"field": "a.missing", "op": "gte", "value": 1}, ctx)

    def test_all_any(self):
        ctx = {"x": 1, "y": 2}
        cond_all = {"all": [{"field": "x", "op": "eq", "value": 1},
                            {"field": "y", "op": "eq", "value": 2}]}
        cond_any = {"any": [{"field": "x", "op": "eq", "value": 9},
                            {"field": "y", "op": "eq", "value": 2}]}
        assert policy._eval_condition(cond_all, ctx)
        assert policy._eval_condition(cond_any, ctx)

    def test_unknown_op_raises(self):
        with pytest.raises(ValueError):
            policy._eval_condition({"field": "x", "op": "regex", "value": ".*"}, {"x": 1})


class TestDemoScenarios:
    def test_board_member_accepted_within_48h(self):
        result = policy.evaluate(make_request("board"), make_dossier(95, vip=True), RULES)
        assert result.outcome == "accept"
        assert result.deciding_rule_id == "R-BOARD-48H"
        assert any(c["type"] == "within_hours" and c["hours"] == 48 for c in result.constraints)

    def test_vendor_delegated_with_redirect(self):
        result = policy.evaluate(make_request("vendor"), make_dossier(12, interactions=0), RULES)
        assert result.outcome == "delegate"
        assert result.deciding_rule_id == "R-VENDOR-DELEGATE"
        assert result.delegate_to and "Marcus Webb" in result.delegate_to

    def test_exec_collision_with_deep_work_defers(self):
        result = policy.evaluate(
            make_request("exec_1on1", internal=True), make_dossier(85, vip=True), RULES,
            derived={"proposed_in_protected_block": True})
        assert result.outcome == "defer"
        assert result.deciding_rule_id == "R-DEEPWORK-PROTECT"

    def test_exec_without_collision_accepts(self):
        result = policy.evaluate(
            make_request("exec_1on1", internal=True), make_dossier(85), RULES,
            derived={"proposed_in_protected_block": False})
        assert result.outcome == "accept"
        assert result.deciding_rule_id == "R-EXEC-ACCEPT"

    def test_board_may_displace_deep_work(self):
        result = policy.evaluate(
            make_request("board"), make_dossier(95, vip=True), RULES,
            derived={"proposed_in_protected_block": True})
        assert result.outcome == "accept"  # deep-work rule exempts board

    def test_press_escalates_to_comms(self):
        result = policy.evaluate(make_request("press"), make_dossier(55, sensitive="press"), RULES)
        assert result.outcome == "escalate_to_human"
        assert result.deciding_rule_id == "R-PRESS-ROUTE"
        assert result.route_to  # comms + CoS
        assert not result.hard_locked

    def test_personnel_hits_sensitive_lockout(self):
        result = policy.evaluate(
            make_request("external_partner"), make_dossier(60, sensitive="personnel"), RULES)
        assert result.outcome == "escalate_to_human"
        assert result.deciding_rule_id == "R-SENSITIVE-LOCK"
        assert result.hard_locked

    def test_sensitive_lockout_outranks_everything(self):
        # Even a VIP board-governance topic locks: priority 1 wins.
        result = policy.evaluate(
            make_request("board"), make_dossier(99, vip=True, sensitive="board_governance"), RULES)
        assert result.hard_locked
        assert result.deciding_rule_id == "R-SENSITIVE-LOCK"

    def test_vip_partner_accepted_with_tz_fairness(self):
        result = policy.evaluate(make_request("external_partner"), make_dossier(82, vip=True), RULES)
        assert result.outcome == "accept"
        assert result.deciding_rule_id == "R-PARTNER-VIP"
        assert any(c["type"] == "requester_hours" for c in result.constraints)
        assert any(c["type"] == "no_start_before_local" for c in result.constraints)


class TestEngineMechanics:
    def test_all_fired_rules_recorded(self):
        result = policy.evaluate(make_request("external_partner"), make_dossier(82), RULES)
        ids = [r.id for r in result.fired_rules]
        assert "R-DEFAULT-DEFER" in ids  # fallback fires but never decides
        assert sum(1 for r in result.fired_rules if r.decisive) == 1

    def test_priority_order_respected(self):
        result = policy.evaluate(make_request("board"), make_dossier(95), RULES)
        priorities = [r.priority for r in result.fired_rules]
        assert priorities == sorted(priorities)

    def test_default_defer_when_nothing_claims(self):
        result = policy.evaluate(make_request("personal", internal=True), make_dossier(40), RULES)
        assert result.outcome == "defer"
        assert result.deciding_rule_id == "R-DEFAULT-DEFER"
