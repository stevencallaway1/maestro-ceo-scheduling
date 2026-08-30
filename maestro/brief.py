"""Brief Agent: generate the Daily Brief as markdown.

Sections: today's decisions with rationales, conflicts detected and how they
were resolved, optimizer findings and patterns, and pending approvals. The
brief is rebuilt on demand from live state, so an override made in the UI is
reflected on the next refresh.
"""
from __future__ import annotations

from typing import Any

from . import evals, optimizer, store

DECISIONS_FILE = "decisions.json"
APPROVALS_FILE = "approvals.json"

_OUTCOME_LABEL = {
    "accept": "Scheduled",
    "decline": "Declined",
    "delegate": "Delegated",
    "defer": "Deferred",
    "escalate_to_human": "Escalated to human",
}


def _decision_lines(decisions: list[dict[str, Any]]) -> list[str]:
    lines = []
    for d in decisions:
        label = _OUTCOME_LABEL.get(d["outcome"], d["outcome"])
        lines.append(f"- **{label}** — {d['requester']}: {d['summary']}")
        lines.append(f"  - *Why:* {d['rationale']}")
    return lines


def generate() -> dict[str, Any]:
    """Build the Daily Brief. Returns markdown plus the structured findings."""
    decisions: list[dict[str, Any]] = store.load(DECISIONS_FILE)
    approvals: list[dict[str, Any]] = store.load(APPROVALS_FILE)
    pending = [a for a in approvals if a["status"] == "pending"]
    cal = store.load("calendar.json")
    findings = optimizer.run(cal)
    report = evals.full_report()

    md: list[str] = ["# Daily Brief — Monday, Aug 31", ""]

    md.append("## Decisions today")
    md.extend(_decision_lines(decisions) or ["- No decisions yet today."])
    md.append("")

    md.append("## Conflicts detected & resolution")
    conflicts = []
    for d in decisions:
        if d["outcome"] == "defer" and ("deep-work" in d["rationale"].lower() or "protected" in d["rationale"].lower()):
            conflicts.append(
                f"- {d['requester']}'s ask collided with protected time — resolved by "
                f"offering policy-clean alternatives in the same reply."
            )
    for w in findings["cascade_warnings"]:
        conflicts.append(f"- Cascade risk flagged (not acted on): {w}")
    md.extend(conflicts or ["- No conflicts today."])
    md.append("")

    md.append("## Optimizer findings")
    for p in findings["patterns"]:
        md.append(f"- **Pattern:** {p}")
    dw = findings["deep_work"]
    for b in dw["blocks"]:
        if b["at_risk"]:
            eaters = "; ".join(f"{e['title']} ({e['when']})" for e in b["eaten_by"])
            md.append(f"- Deep-work block *{b['block']}* ({b['when']}) eaten by: {eaters}")
    for s in findings["batching_suggestions"]:
        md.append(f"- **Batching:** {s}")
    md.append("")

    md.append("## Awaiting your approval")
    if pending:
        for a in pending:
            md.append(f"- [{a['category']}] {a['summary']} — *{a['requester']}*")
    else:
        md.append("- Queue is clear.")
    md.append("")

    m = report["metrics"]["overall"]
    md.append("## Trust snapshot")
    md.append(
        f"- Suggestion acceptance (rolling {report['metrics']['window_days']}d): "
        f"**{m['rate']:.1%}** over {m['samples']} decisions; "
        f"{m['critical_misses']} critical miss(es) in window."
    )

    return {
        "markdown": "\n".join(md),
        "optimizer": findings,
        "pending_approvals": pending,
        "decisions": decisions,
    }
