"""Trust ladder tests: promotion gates, automatic demotion, hard locks, evals."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from maestro import evals, store, trust

PROJECT_DATA = Path(__file__).resolve().parent.parent / "data"


@pytest.fixture()
def sandbox_data(tmp_path, monkeypatch):
    """Copy /data into a tmp dir so tests never mutate real demo state."""
    dst = tmp_path / "data"
    shutil.copytree(PROJECT_DATA, dst)
    monkeypatch.setattr(store, "DATA_DIR", dst)
    store._cache.clear()
    yield dst
    store._cache.clear()


def metrics(samples: int, rate: float, misses: int = 0) -> dict:
    return {"cat": {"samples": samples, "rate": rate, "critical_misses": misses}}


def state_for(level: str, locked: bool = False) -> dict:
    return {"categories": {"cat": {"level": level, "locked": locked, "history": []}},
            "sensitive_topics_locked": ["personnel", "legal", "m_and_a", "board_governance"]}


class TestPromotionGates:
    def test_l0_promotes_at_95_percent(self):
        status = trust.promotion_status("cat", state_for("L0"), metrics(10, 0.96))
        assert status["next_level"] == "L1"
        assert status["progress_pct"] == 100
        assert "Eligible" in status["reason"]

    def test_l0_blocked_below_95(self):
        status = trust.promotion_status("cat", state_for("L0"), metrics(10, 0.90))
        assert status["progress_pct"] < 100
        assert "Eligible" not in status["reason"]

    def test_l1_requires_98_percent(self):
        assert "Eligible" in trust.promotion_status("cat", state_for("L1"), metrics(10, 0.99))["reason"]
        assert "Eligible" not in trust.promotion_status("cat", state_for("L1"), metrics(10, 0.96))["reason"]

    def test_critical_miss_resets_progress(self):
        status = trust.promotion_status("cat", state_for("L1"), metrics(10, 1.0, misses=1))
        assert status["progress_pct"] == 0
        assert "Critical miss" in status["reason"]

    def test_insufficient_samples_blocks(self):
        status = trust.promotion_status("cat", state_for("L0"), metrics(2, 1.0))
        assert status["progress_pct"] < 100
        assert "Needs" in status["reason"]

    def test_l2_to_l3_requires_human_signoff(self):
        status = trust.promotion_status("cat", state_for("L2"), metrics(10, 1.0))
        assert status["next_level"] == "L3"
        assert "sign-off" in status["reason"]

    def test_locked_category_never_promotes(self):
        status = trust.promotion_status("cat", state_for("L0", locked=True), metrics(50, 1.0))
        assert status["locked"] is True
        assert status["next_level"] is None
        assert "never promoted" in status["reason"]


class TestCriticalMiss:
    def test_vip_reversal_is_critical(self):
        assert trust.is_critical_miss("external_partner", vip=True,
                                      sensitive_category=None, state=state_for("L1"))

    def test_sensitive_reversal_is_critical(self):
        assert trust.is_critical_miss("external_partner", vip=False,
                                      sensitive_category="personnel", state=state_for("L1"))

    def test_board_reversal_is_critical(self):
        assert trust.is_critical_miss("board", vip=False,
                                      sensitive_category=None, state=state_for("L0"))

    def test_ordinary_reversal_is_not_critical(self):
        assert not trust.is_critical_miss("internal_team", vip=False,
                                          sensitive_category=None, state=state_for("L2"))


class TestAutomaticDemotion:
    def test_demotion_drops_one_level_and_persists(self, sandbox_data):
        before = trust.load_state()["categories"]["internal_team"]["level"]
        assert before == "L2"
        entry = trust.record_critical_miss("internal_team", "reversed a VIP move")
        assert entry is not None and entry["from"] == "L2" and entry["to"] == "L1"
        after = trust.load_state()["categories"]["internal_team"]
        assert after["level"] == "L1"
        assert "AUTOMATIC DEMOTION" in after["history"][-1]["reason"]

    def test_demotion_is_audited(self, sandbox_data):
        trust.record_critical_miss("exec_1on1", "wrong call")
        entries = store.read_jsonl("audit_log.jsonl")
        assert any(e["stage"] == "trust_ladder" and "demotion" in e["summary"].lower()
                   for e in entries)

    def test_locked_category_cannot_be_demoted(self, sandbox_data):
        assert trust.record_critical_miss("board", "n/a") is None
        assert trust.load_state()["categories"]["board"]["level"] == "L0"

    def test_l0_has_nothing_to_demote(self, sandbox_data):
        assert trust.record_critical_miss("press", "n/a") is None


class TestEvalWindow:
    def test_window_anchors_to_latest_entry(self):
        overrides = [
            {"date": "2026-08-01", "category": "a", "human_action": "approved", "critical_miss": False},
            {"date": "2026-08-28", "category": "a", "human_action": "approved", "critical_miss": False},
            {"date": "2026-08-27", "category": "a", "human_action": "overridden", "critical_miss": True},
        ]
        m = evals.window_metrics(overrides)
        assert m["anchor"] == "2026-08-28"
        assert m["overall"]["samples"] == 2  # Aug 1 falls outside the 14-day window
        assert m["overall"]["critical_misses"] == 1
        assert m["categories"]["a"]["rate"] == 0.5

    def test_empty_overrides(self):
        m = evals.window_metrics([])
        assert m["overall"]["samples"] == 0

    def test_seeded_data_produces_meaningful_metrics(self):
        m = evals.window_metrics(json.loads((PROJECT_DATA / "overrides.json").read_text()))
        assert m["overall"]["samples"] >= 30
        assert 0.8 < m["overall"]["rate"] < 1.0
        assert m["categories"]["vendor"]["rate"] == 1.0  # promotion-eligible in the demo
        assert m["categories"]["external_partner"]["critical_misses"] == 1
