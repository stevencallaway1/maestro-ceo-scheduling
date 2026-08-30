"""Policy Engine: a human-readable, hand-editable ruleset (data/policies.json)
evaluated deterministically against each request + dossier.

Rules are evaluated in ascending ``priority`` order. The first fired rule with
a decisive action (accept / decline / delegate / defer / escalate_to_human)
sets the outcome. Rules with action ``constrain`` never decide; they attach
slot-selection constraints. Every fired rule — decisive or not — is recorded
so the decision rationale can cite exactly which policies applied.
"""
from __future__ import annotations

from typing import Any

from .models import Dossier, FiredRule, PolicyResult, RequestObject

DECISIVE_ACTIONS = {"accept", "decline", "delegate", "defer", "escalate_to_human"}
SENSITIVE_CATEGORIES = {"legal", "m_and_a", "personnel", "board_governance"}


def _resolve(field: str, ctx: dict[str, Any]) -> Any:
    """Resolve a dotted field path (e.g. ``dossier.vip``) against the context."""
    node: Any = ctx
    for part in field.split("."):
        if isinstance(node, dict):
            node = node.get(part)
        else:
            node = getattr(node, part, None)
    return node


def _eval_condition(cond: dict[str, Any], ctx: dict[str, Any]) -> bool:
    """Evaluate one structured condition node (leaf, ``all`` or ``any``)."""
    if "all" in cond:
        return all(_eval_condition(c, ctx) for c in cond["all"])
    if "any" in cond:
        return any(_eval_condition(c, ctx) for c in cond["any"])
    value = _resolve(cond["field"], ctx)
    op, target = cond["op"], cond.get("value")
    if op == "eq":
        return value == target
    if op == "ne":
        return value != target
    if op == "in":
        return value in target
    if op == "not_in":
        return value not in target
    if op == "gte":
        return value is not None and value >= target
    if op == "lte":
        return value is not None and value <= target
    if op == "lt":
        return value is not None and value < target
    if op == "gt":
        return value is not None and value > target
    if op == "contains":
        return isinstance(value, (str, list)) and target in value
    raise ValueError(f"Unknown condition op: {op}")


def evaluate(request: RequestObject, dossier: Dossier, rules: list[dict[str, Any]],
             derived: dict[str, Any] | None = None) -> PolicyResult:
    """Run the full ruleset over one request.

    ``derived`` carries facts computed from the calendar (e.g. whether a
    requester-proposed time lands inside a protected block).
    """
    ctx = {
        "request": {
            "id": request.id,
            "meeting_type": request.meeting_type,
            "urgency": request.urgency,
            "requested_duration_minutes": request.requested_duration_minutes,
            "requester_internal": request.requester.internal,
            "channel": request.channel,
        },
        "dossier": dossier.to_dict(),
        "derived": derived or {},
    }

    fired: list[FiredRule] = []
    constraints: list[dict[str, Any]] = []
    outcome: str | None = None
    deciding_rule_id = ""
    delegate_to: str | None = None
    route_to: list[str] = []
    hard_locked = False

    for rule in sorted(rules, key=lambda r: r["priority"]):
        if not _eval_condition(rule["condition"], ctx):
            continue
        action = rule["action"]
        decisive = action in DECISIVE_ACTIONS and outcome is None
        fired.append(FiredRule(
            id=rule["id"], priority=rule["priority"],
            plain_english=rule["plain_english"], action=action, decisive=decisive,
        ))
        if "constraint" in rule:
            constraints.append({**rule["constraint"], "rule_id": rule["id"]})
        if decisive:
            outcome = action
            deciding_rule_id = rule["id"]
            delegate_to = rule.get("delegate_to")
            route_to = list(rule.get("route_to", []))
            if rule["id"] == "R-SENSITIVE-LOCK":
                hard_locked = True

    return PolicyResult(
        outcome=outcome or "defer",
        deciding_rule_id=deciding_rule_id or "R-DEFAULT-DEFER",
        fired_rules=fired,
        constraints=constraints,
        delegate_to=delegate_to,
        route_to=route_to,
        hard_locked=hard_locked,
    )
