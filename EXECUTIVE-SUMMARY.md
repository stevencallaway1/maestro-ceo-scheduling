# Maestro

**An AI scheduling system for the Office of the CEO.**
Steven Callaway · ClickUp Chief of Staff case study

---

## The problem

A CEO's calendar is not a scheduling problem. It is a judgment problem wearing a scheduling
costume. Every inbound ask carries a question no availability tool can answer: does this person
deserve this hour, this week, ahead of the other forty people who also want it?

That judgment is why the work has stayed human. A chief of staff holds context nothing else has:
who this person is to the company, what was promised to them and not yet delivered, what the
answer signals, and which conversations must never be handled by anyone but the CEO. Today that
context lives in one person's head, which makes it a bottleneck when they are busy and a single
point of failure when they leave.

The reason CEOs have not handed this to software is not capability. It is trust. A founder who
lives in the details will not delegate their calendar to something that cannot show its work.

## The system

Maestro turns one inbound request into a decision, a written justification, and a drafted reply,
then stops and waits for a human. It runs six stages:

**Intake** normalizes the request. **Context** builds a dossier: who this is to the CEO, their
strategic relevance, what is still owed to them, and whether the topic is sensitive. **Policy**
evaluates a hand-written ruleset and sets the outcome. **Planner** writes the decision rationale
and the reply in the CEO's voice. **Critic** reviews that work before anyone sees it.
**Approval** puts it in front of a human. Only then does the calendar adapter act.

Three things make it worth a CEO's trust.

**No decision without a dossier.** Every output is attached to the context that produced it and
the policy rule that decided it, cited by name. A CEO can disagree with the reasoning in three
seconds because the reasoning is on the page.

**Autonomy is earned per category, never assumed.** Each meeting type sits on a trust ladder from
L0 (suggest only) to L3 (bounded negotiation). Promotion requires measured acceptance over a
rolling window. Demotion is automatic and immediate on any critical miss, meaning a reversal on a
VIP or a sensitive topic. Sensitive categories, which is legal, M&A, personnel, and board
governance, are locked at L0 permanently and cannot be promoted by any amount of good behavior.

**Every override teaches it something.** Reversing Maestro is not a failure mode, it is the input.
Each verdict is scored, and those scores are what move a category up or down the ladder. The
system's authority is a direct function of its measured track record.

## What is model-driven and what is not

Exactly two of the six stages call a model, once each: the **Planner**, which writes the rationale
and the draft reply, and the **Critic**, which reviews them. Both go through one interface and
return structured objects.

Everything that decides, books, or records is deterministic code: request normalization, context
assembly, the policy engine, calendar math, approval routing, execution, and the audit trail.

That split is the core design decision, and it is what makes the system safe to point at a real
calendar. The policy engine sets the outcome, so a model cannot talk its way into a meeting the
rules forbid. Free-slot search runs *before* the Planner, so the Planner can only write about times
that are provably free and policy-clean, and cannot hallucinate an hour that does not exist.
Language is the part that genuinely needs judgment, so that is the only part a model touches.

The Critic reviews what deterministic code cannot see: whether the reply actually does what the
decision said, whether a locked topic leaked into an outbound message, whether it sounds like the
CEO, and whether it acknowledges what the CEO already owes this person. On the board member in the
demo, it catches that the reply never mentions a churn analysis promised two months ago and never
sent. Its verdict is the gate on autonomy: as categories climb the ladder, only a clean pass is
ever eligible to execute unattended.

## Approvals, policy, and the record

Policy is a plain-English file, not code. Thirteen rules, each with a written statement of intent,
evaluated in priority order. A chief of staff can read it, argue with it, and edit it without an
engineer, which is what keeps the system's behavior owned by the office it serves.

Nothing is ever sent or booked without a human. Approving runs the calendar adapter, which is
idempotent, so a replayed approval can never double-book. Overriding requires a one-line reason,
which is what feeds the eval loop. Both paths, and every stage before them, land in an append-only
audit log with a timestamp and an actor. There is no path through this system that leaves no trace.

## Why this is the right shape for a CEO

The obvious build is an agent that reads the calendar and books things. It demos well and fails the
first time it schedules a reporter, moves a board member, or replies to a confidential personnel
matter, and it fails without an explanation. One incident like that ends the project.

Maestro is built for the opposite failure mode. It starts with almost no authority and earns more
only where it has a measured record, one category at a time. Every action is explained, reversible,
and logged. The topics where a mistake is unrecoverable are structurally excluded rather than
merely discouraged, and no amount of accumulated trust can unlock them.

The result is a system a CEO can adopt without a leap of faith: it is useful at L0 on day one,
because a drafted reply with the reasoning attached already saves the time, and it becomes more
useful only as fast as it proves it should.

---

## What is built, and what is simulated

The pipeline, policy engine, calendar math, approval routing, trust ladder, eval loop, and audit
log all run for real, with 56 tests covering them. Two edges are simulated, deliberately and
visibly:

- **The two model calls** run on deterministic templates behind the model interface. The call
  sites, the structured contracts, and the Critic's four checks are all real; only the network
  round trip is absent. Production swaps the provider and changes no pipeline code.
- **The calendar adapter** builds the exact event payload and idempotency key it would send, and
  records it, without calling an external calendar.

The live demo labels both, and a "what runs for real" panel in the UI states it line by line.
Nothing in the interface claims a capability the code does not have.

**Live demo:** https://maestro-ceo-scheduling.vercel.app
**Architecture diagram:** https://maestro-ceo-scheduling.vercel.app/static/architecture.svg
**Repository:** https://github.com/stevencallaway1/clickup-case-study
