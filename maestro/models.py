"""Core typed artifacts that flow through the Maestro pipeline.

Every stage of the pipeline consumes and produces one of these dataclasses.
They are all JSON-serializable via ``dataclasses.asdict`` so the UI can render
each artifact in full - no decision without an inspectable object behind it.

Stage order: RequestObject -> Dossier -> PolicyResult -> Decision + Draft
(the Planner's plan) -> Critique -> approval -> ExecutionResult.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

# The five outcomes a request can end in, and how they are named everywhere a
# human reads them. One definition so the UI, the brief, and the approval queue
# never disagree.
OUTCOME_LABEL = {
    "accept": "Scheduled",
    "decline": "Declined",
    "delegate": "Delegated",
    "defer": "Deferred",
    "escalate_to_human": "Escalated to human",
}


@dataclass
class Requester:
    """The person asking for time, as parsed from the raw payload."""

    name: str
    email: str
    role: str = "unknown"
    org: str = "unknown"
    internal: bool = False


@dataclass
class RequestObject:
    """Structured scheduling request produced by request normalization."""

    id: str
    requester: Requester
    requested_duration_minutes: int
    urgency: str  # "high" | "normal" | "low"
    urgency_signal: str  # what the urgency was inferred from
    topic: str
    meeting_type: str
    channel: str
    proposed_start: str | None  # ISO timestamp if the requester named a time
    raw_source_text: str
    received_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Interaction:
    """One historical touchpoint between the CEO and the requester."""

    date: str
    topic: str
    outcome: str


@dataclass
class Dossier:
    """Context Dossier: everything Maestro knows before it decides.

    Tagline honored in the UI: "No decision without a dossier."
    """

    requester_email: str
    requester_name: str
    relationship_summary: str
    last_interactions: list[Interaction]
    open_threads: list[str]
    strategic_relevance: int  # 0-100
    relevance_justification: str
    sensitive_category: str | None
    timezone: str
    vip: bool
    interaction_count: int
    known_person: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FiredRule:
    """Record of a policy rule that matched this request."""

    id: str
    priority: int
    plain_english: str
    action: str
    decisive: bool  # True if this rule set the outcome


@dataclass
class PolicyResult:
    """Outcome of the Policy Engine over one request + dossier."""

    outcome: str  # accept | decline | delegate | defer | escalate_to_human
    deciding_rule_id: str
    fired_rules: list[FiredRule]
    constraints: list[dict[str, Any]]
    delegate_to: str | None = None
    route_to: list[str] = field(default_factory=list)
    hard_locked: bool = False  # sensitive-category lockout engaged

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Slot:
    """A proposed meeting slot, rendered in both parties' timezones."""

    start: str  # ISO, CEO timezone
    end: str
    ceo_local: str  # human string, e.g. "Mon Aug 31, 12:30-1:00 PM PDT"
    requester_local: str


@dataclass
class Decision:
    """The Planner's call on a request, with its written rationale."""

    request_id: str
    outcome: str
    proposed_slots: list[Slot]
    rationale: str
    trust_level: str  # trust level of the category at decision time
    trust_note: str
    delegate_to: str | None = None
    route_to: list[str] = field(default_factory=list)
    hard_locked: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Draft:
    """Outbound reply drafted in the CEO's voice. Never auto-sent."""

    request_id: str
    kind: str  # "external_reply" | "internal_note"
    to: str
    subject: str
    body: str
    approval_required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Finding:
    """One issue the Critic raised against the plan."""

    check: str  # which check produced it, e.g. "commitment_coverage"
    severity: str  # "block" | "revise" | "note"
    message: str


@dataclass
class Critique:
    """The Critic's review of the Planner's decision + draft.

    ``verdict`` is the worst severity found: ``pass`` when the plan is clean,
    ``revise`` when a human should look before it goes out, ``block`` when the
    plan must not execute as written. Anything other than ``pass`` forces the
    approval queue regardless of trust level.
    """

    request_id: str
    verdict: str  # "pass" | "revise" | "block"
    findings: list[Finding]
    checks_run: list[str]
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExecutionResult:
    """What the calendar adapter did after a human approved the plan.

    In the demo the adapter is simulated: the payload it would send is built
    and recorded in full, but no external API is called.
    """

    approval_id: str
    request_id: str | None
    action: str  # "calendar_hold" | "route_only" | "no_action"
    simulated: bool
    summary: str
    event: dict[str, Any] | None = None
    executed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
