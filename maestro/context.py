"""Context: build the Context Dossier for a request.

Deterministic assembly, not retrieval-by-guess. The dossier is a first-class
artifact - "No decision without a dossier" - joined from data/people.json
(relationship, flags, relevance) and data/history.json (interactions, open
threads), and rendered in full in the UI before any decision is shown.
"""
from __future__ import annotations

from typing import Any

from .models import Dossier, Interaction, RequestObject


def build(request: RequestObject, people: dict[str, Any], history: dict[str, Any]) -> Dossier:
    """Assemble the dossier for the requester behind ``request``.

    Unknown senders get an explicit "no relationship on file" dossier with a
    conservative relevance score, never a silent guess.
    """
    email = request.requester.email
    person = people.get(email)
    hist = history.get(email, {"interactions": [], "open_threads": []})
    interactions = [Interaction(**i) for i in hist.get("interactions", [])][:3]

    if person is None:
        return Dossier(
            requester_email=email,
            requester_name=request.requester.name,
            relationship_summary=(
                "No relationship on file. First contact from this address; "
                "treat as unsolicited external outreach."
            ),
            last_interactions=[],
            open_threads=[],
            strategic_relevance=10,
            relevance_justification="Unknown sender with no history; conservative default score.",
            sensitive_category=None,
            timezone="America/Los_Angeles",
            vip=False,
            interaction_count=0,
            known_person=False,
        )

    return Dossier(
        requester_email=email,
        requester_name=person["name"],
        relationship_summary=person["relationship"],
        last_interactions=interactions,
        open_threads=list(hist.get("open_threads", [])),
        strategic_relevance=int(person["strategic_relevance"]),
        relevance_justification=person["relevance_justification"],
        sensitive_category=person.get("sensitive_category"),
        timezone=person.get("timezone", "America/Los_Angeles"),
        vip=bool(person.get("vip", False)),
        interaction_count=len(hist.get("interactions", [])),
        known_person=True,
    )
