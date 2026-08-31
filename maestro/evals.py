"""Eval loop: the trust mechanism's measurement half. Deterministic.

Compares Maestro's decisions to recorded human behavior (data/overrides.json)
and computes the rolling metrics the Trust Ladder promotes and demotes on:
suggestion acceptance rate, per-category accuracy, and critical-miss counts.

The rolling 14-day window is anchored to the most recent entry in the file
(not the wall clock) so the mock history stays meaningful whenever the demo
is run.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from . import store, trust

WINDOW_DAYS = 14
OVERRIDES_FILE = "overrides.json"


def load_overrides() -> list[dict[str, Any]]:
    """All recorded human approve/override events."""
    return store.load(OVERRIDES_FILE)


def window_metrics(overrides: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-category and overall acceptance metrics over the rolling window."""
    if not overrides:
        return {"anchor": None, "overall": {"samples": 0, "accepted": 0, "rate": 0.0,
                                            "critical_misses": 0}, "categories": {}}
    anchor = max(date.fromisoformat(o["date"]) for o in overrides)
    cutoff = anchor - timedelta(days=WINDOW_DAYS - 1)
    recent = [o for o in overrides if date.fromisoformat(o["date"]) >= cutoff]

    categories: dict[str, dict[str, Any]] = {}
    for o in recent:
        c = categories.setdefault(o["category"], {"samples": 0, "accepted": 0, "critical_misses": 0})
        c["samples"] += 1
        if o["human_action"] == "approved":
            c["accepted"] += 1
        if o.get("critical_miss"):
            c["critical_misses"] += 1
    for c in categories.values():
        c["rate"] = c["accepted"] / c["samples"] if c["samples"] else 0.0

    total = len(recent)
    accepted = sum(1 for o in recent if o["human_action"] == "approved")
    return {
        "anchor": anchor.isoformat(),
        "window_days": WINDOW_DAYS,
        "overall": {
            "samples": total,
            "accepted": accepted,
            "rate": accepted / total if total else 0.0,
            "critical_misses": sum(1 for o in recent if o.get("critical_miss")),
        },
        "categories": categories,
    }


def full_report() -> dict[str, Any]:
    """Metrics + per-category trust ladder recommendations, for the UI."""
    overrides = load_overrides()
    metrics = window_metrics(overrides)
    state = trust.load_state()
    ladder = [
        trust.promotion_status(category, state, metrics["categories"])
        for category in state["categories"]
    ]
    return {
        "metrics": metrics,
        "ladder": ladder,
        "trust_state": state,
        "recent_overrides": sorted(overrides, key=lambda o: o["date"], reverse=True)[:8],
    }
