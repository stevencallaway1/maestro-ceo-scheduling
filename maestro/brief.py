"""Daily Brief: the CEO-facing summary, rendered as markdown. Deterministic.

Sections: today's decisions with their rationales, anything the Critic flagged,
conflicts and how they were resolved, optimizer findings, what is waiting for
approval, and the current trust snapshot. The brief is rebuilt on demand from
live state, so an approval or override made in the UI shows up on the next
refresh.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from . import evals, optimizer, store
from .models import OUTCOME_LABEL

DECISIONS_FILE = "decisions.json"
APPROVALS_FILE = "approvals.json"

_WEEKDAY = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _brief_date(cal: dict[str, Any]) -> str:
    """The brief is dated from the calendar's pinned 'now', not the wall clock."""
    now = datetime.fromisoformat(cal["demo_now"])
    return f"{_WEEKDAY[now.weekday()]}, {now.strftime('%b')} {now.day}"


def _decision_lines(decisions: list[dict[str, Any]]) -> list[str]:
    lines = []
    for d in decisions:
        label = OUTCOME_LABEL.get(d["outcome"], d["outcome"])
        flag = " *(Critic: revise)*" if d.get("critic_verdict") == "revise" else ""
        flag = " *(Critic: blocked)*" if d.get("critic_verdict") == "block" else flag
        lines.append(f"- **{label}** - {d['requester']}: {d['summary']}{flag}")
        lines.append(f"  - *Why:* {d['rationale']}")
    return lines


def generate() -> dict[str, Any]:
    """Build the Daily Brief. Returns markdown plus the structured findings."""
    decisions: list[dict[str, Any]] = store.load(DECISIONS_FILE)
    approvals: list[dict[str, Any]] = store.load(APPROVALS_FILE)
    pending = [a for a in approvals if a["status"] == "pending"]
    executed = [a for a in approvals if a["status"] in ("approved", "overridden")]
    cal = store.load("calendar.json")
    findings = optimizer.run(cal)
    report = evals.full_report()

    md: list[str] = [f"# Daily Brief - {_brief_date(cal)}", ""]

    md.append("## Decisions today")
    md.extend(_decision_lines(decisions) or ["- No decisions yet today."])
    md.append("")

    flagged = [a for a in approvals if a.get("critic_verdict") in ("revise", "block")]
    if flagged:
        md.append("## Flagged by the Critic")
        for a in flagged:
            for f in a.get("critic_findings", []):
                md.append(f"- **{a['requester']}** ({f['check']}): {f['message']}")
        md.append("")

    md.append("## Conflicts detected & resolution")
    conflicts = []
    for d in decisions:
        if d["outcome"] == "defer" and ("deep-work" in d["rationale"].lower()
                                        or "protected" in d["rationale"].lower()):
            conflicts.append(
                f"- {d['requester']}'s ask collided with protected time - resolved by "
                f"offering policy-clean alternatives in the same reply."
            )
    for w in findings["cascade_warnings"]:
        conflicts.append(f"- Cascade risk flagged (not acted on): {w}")
    md.extend(conflicts or ["- No conflicts today."])
    md.append("")

    md.append("## Optimizer findings")
    for p in findings["patterns"]:
        md.append(f"- **Pattern:** {p}")
    for b in findings["deep_work"]["blocks"]:
        if b["at_risk"]:
            eaters = "; ".join(f"{e['title']} ({e['when']})" for e in b["eaten_by"])
            md.append(f"- Deep-work block *{b['block']}* ({b['when']}) eaten by: {eaters}")
    for s in findings["batching_suggestions"]:
        md.append(f"- **Batching:** {s}")
    md.append("")

    md.append("## Awaiting your approval")
    if pending:
        for a in pending:
            md.append(f"- [{a['category']}] {a['summary']} - *{a['requester']}*")
    else:
        md.append("- Queue is clear.")
    md.append("")

    if executed:
        md.append("## Actioned by you today")
        for a in executed:
            verb = "Approved" if a["status"] == "approved" else "Overridden"
            md.append(f"- **{verb}** - {a['summary']} (*{a['requester']}*)")
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
