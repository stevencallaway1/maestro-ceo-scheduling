"""Trust Ladder: autonomy is earned, never assumed.

Levels (per category, persisted in data/trust.json):
  L0  Suggest only - human executes everything
  L1  Drafts replies + calendar holds - human approves each one
  L2  Auto-handles low-stakes INTERNAL moves, logs everything
  L3  Negotiates external scheduling within policy bounds

Promotion gates:
  L0 -> L1: >=95% suggestion acceptance over 2 weeks (min sample size)
  L1 -> L2: >=98% accuracy at L1 AND zero critical misses in the window
  L2 -> L3: explicit human sign-off per category (never automatic)

Demotion is AUTOMATIC on any critical miss - a decision the human reverses
on a sensitive category or a VIP requester. Sensitive categories are
hard-locked to L0 forever by design.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import audit, store

LEVELS = ["L0", "L1", "L2", "L3"]
MIN_SAMPLES = 6
GATE_L0_L1 = 0.95
GATE_L1_L2 = 0.98

TRUST_FILE = "trust.json"


def load_state() -> dict[str, Any]:
    """Load the persisted trust ladder state."""
    return store.load(TRUST_FILE)


def promotion_status(category: str, state: dict[str, Any],
                     metrics: dict[str, Any]) -> dict[str, Any]:
    """Compute promotion readiness for one category from eval metrics.

    Returns current level, the next level, progress toward it (0-100), and a
    plain-English reason - the exact text shown on the Trust panel.
    """
    cat = state["categories"].get(category, {"level": "L0", "locked": False})
    level, locked = cat["level"], cat.get("locked", False)
    m = metrics.get(category, {"samples": 0, "rate": 0.0, "critical_misses": 0})
    samples, rate, misses = m["samples"], m["rate"], m["critical_misses"]

    if locked:
        return {"category": category, "level": level, "locked": True, "next_level": None,
                "progress_pct": 0, "reason": "Hard-locked to L0 by design. Sensitive categories are never promoted."}
    if level == "L3":
        return {"category": category, "level": level, "locked": False, "next_level": None,
                "progress_pct": 100, "reason": "At maximum autonomy for this category."}

    next_level = LEVELS[LEVELS.index(level) + 1]
    if level == "L2":
        return {"category": category, "level": level, "locked": False, "next_level": "L3",
                "progress_pct": 100 if rate >= GATE_L1_L2 and misses == 0 and samples >= MIN_SAMPLES else 50,
                "reason": ("Metrics clear the bar; L3 requires explicit human sign-off - awaiting Zeb."
                           if rate >= GATE_L1_L2 and misses == 0 and samples >= MIN_SAMPLES
                           else "L3 requires sustained accuracy plus explicit human sign-off.")}

    gate = GATE_L0_L1 if level == "L0" else GATE_L1_L2
    if misses > 0:
        return {"category": category, "level": level, "locked": False, "next_level": next_level,
                "progress_pct": 0,
                "reason": f"Critical miss in the last 14 days resets promotion progress ({misses} recorded)."}
    if samples < MIN_SAMPLES:
        pct = int(100 * samples / MIN_SAMPLES * min(1.0, rate / gate)) if gate else 0
        return {"category": category, "level": level, "locked": False, "next_level": next_level,
                "progress_pct": pct,
                "reason": f"Needs {MIN_SAMPLES} decisions in the window ({samples} so far) at >={gate:.0%} acceptance."}
    pct = min(100, int(100 * rate / gate))
    eligible = rate >= gate
    return {"category": category, "level": level, "locked": False, "next_level": next_level,
            "progress_pct": pct,
            "reason": (f"Eligible for promotion to {next_level}: {rate:.1%} acceptance over "
                       f"{samples} decisions (gate {gate:.0%})." if eligible
                       else f"{rate:.1%} acceptance over {samples} decisions; gate for {next_level} is {gate:.0%}.")}


def is_critical_miss(category: str, vip: bool, sensitive_category: str | None,
                     state: dict[str, Any]) -> bool:
    """A critical miss = human reversal on a sensitive category or a VIP."""
    if vip:
        return True
    if sensitive_category and sensitive_category in state.get("sensitive_topics_locked", []):
        return True
    return category in ("board",)


def record_critical_miss(category: str, reason: str) -> dict[str, Any] | None:
    """Automatically demote a category one level after a critical miss.

    Persists the new level + history entry to trust.json and writes an audit
    record. Locked categories stay at L0 (nothing to demote). Returns the
    history entry, or None if no demotion happened.
    """
    state = load_state()
    cat = state["categories"].setdefault(category, {"level": "L0", "locked": False, "history": []})
    if cat.get("locked") or cat["level"] == "L0":
        return None
    old = cat["level"]
    new = LEVELS[LEVELS.index(old) - 1]
    entry = {
        "date": datetime.now(timezone.utc).astimezone().date().isoformat(),
        "from": old, "to": new,
        "reason": f"AUTOMATIC DEMOTION: critical miss - {reason}",
    }
    cat["level"] = new
    cat.setdefault("history", []).append(entry)
    store.save(TRUST_FILE, state)
    audit.record("trust_ladder", f"Automatic demotion: {category} {old} -> {new} (critical miss)",
                 detail={"reason": reason})
    return entry
