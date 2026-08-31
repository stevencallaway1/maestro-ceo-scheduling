# Maestro

**An AI scheduling system for the Office of the CEO.**
Steven Callaway · ClickUp Chief of Staff case study

## The problem

A CEO's calendar is a judgment problem wearing a scheduling costume. Every ask carries a question
no availability tool answers: does this person deserve this hour, this week, ahead of the forty
others who want it? Answering it takes context that today lives in one chief of staff's head, which
is a bottleneck when they are busy and a single point of failure when they leave.

What has kept software out is not capability. It is trust. A founder who lives in the details will
not delegate their calendar to something that cannot show its work.

## The system

Maestro turns one inbound request into a decision, a written justification, and a drafted reply,
then stops and waits for a human.

> **Intake → Context → Policy → Planner → Critic → Approval → Calendar**

**No decision without a dossier.** Every output carries the context that produced it and the policy
rule that decided it, cited by name. A CEO can disagree with the reasoning in three seconds because
the reasoning is on the page.

**Autonomy is earned per category.** Each meeting type sits on a ladder from L0 (suggest only) to
L3 (bounded negotiation). Promotion requires measured acceptance over a rolling window. Demotion is
automatic on any critical miss, meaning a reversal on a VIP or a sensitive topic. Legal, M&A,
personnel, and board governance are locked at L0 permanently.

**Every override is the input, not a failure.** Each human verdict is scored, and those scores are
what move a category up or down the ladder.

## What the model does, and what it never does

Two of the six stages call a model, once each: the **Planner**, which writes the rationale and the
reply, and the **Critic**, which reviews them. Everything that decides, books, or records is
deterministic code: normalization, context assembly, the policy engine, calendar math, approval
routing, execution, and the audit trail.

On a hard-locked topic that count is zero. The pipeline stops at the policy engine and the routing
note is written in code, so a sensitive request is never sent to a model at all. Every call through
the seam is logged and each run reports its own count, which makes that a number on the screen
rather than a promise in a document.

That split is the whole design. The policy engine sets the outcome, so a model cannot talk its way
into a meeting the rules forbid. Free-slot search runs *before* the Planner, so it can only write
about times that are provably free and cannot invent an hour that does not exist. Language is the
part that genuinely needs judgment, so language is the only part a model touches.

The Critic catches what code cannot: a reply that does not match the decision, a locked topic
leaking into an outbound message, the wrong voice, or an unmet promise. On the board member in the
demo, it flags that the reply never mentions a churn analysis promised two months ago and never
sent. Its verdict is a gate, not a label: a blocked plan cannot be approved at all, and a flagged
one only by a human who acknowledges the findings first.

## Approvals and the record

Policy is a plain-English file of 13 rules, not code, so a chief of staff can edit it without an
engineer. Nothing is sent or booked without a human, and approval books only what the reply
promised: a message offering three times places a tentative hold on the CEO's calendar and sends
no invite, because the requester has not picked one yet. Each queue item takes exactly one human
verdict, even under simultaneous clicks, so a double click cannot skew the metric that moves the
trust ladder. Overriding requires
a one-line reason. Every stage and every verdict lands in an append-only audit log. There is no
path through this system that leaves no trace.

## Why this shape

The obvious build is an agent that reads the calendar and books things. It demos well and fails the
first time it schedules a reporter or replies to a confidential personnel matter, and it fails
without an explanation. One incident like that ends the project.

Maestro is built for that failure mode. It starts with almost no authority, earns more only where
it has a measured record, and structurally excludes the topics where a mistake is unrecoverable. It
is useful at L0 on day one, because a drafted reply with the reasoning already attached saves time.

## What is simulated

The pipeline, policy engine, trust ladder, eval loop, and audit log all run for real, under 74
tests. Two edges are simulated and labelled in the interface: the two model calls run on
deterministic templates behind the model interface, and the calendar adapter builds the real event
payload and idempotency key without sending it. Production registers a Claude-backed provider and
implements one send method. No pipeline code changes.

---

**Live demo:** https://maestro-ceo-scheduling.vercel.app
**Architecture:** https://maestro-ceo-scheduling.vercel.app/static/architecture.svg
**Repository:** https://github.com/stevencallaway1/maestro-ceo-scheduling
