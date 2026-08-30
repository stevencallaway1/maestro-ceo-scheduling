# Maestro

## Why it's built this way

A founder-CEO who lives in the details will never delegate to a black box, so
Maestro never asks for trust it hasn't earned. Every decision ships with a full
context dossier and a written rationale that cites the exact facts and policy
rules behind it; every action lands in an append-only audit log; nothing is
ever sent without human approval. Autonomy is granted per category on an
explicit trust ladder (L0 suggest-only → L3 bounded negotiation), promoted only
by measured acceptance rates and demoted automatically on any critical miss.
The architecture serves that principle: seven small inspectable stages, a
hand-editable policy file, and an eval loop that turns every human override
into training signal.

An AI scheduling system for the Office of the CEO, built for a founder-CEO who
is obsessed with context and slow to delegate trust. Maestro never operates as a
black box: **every decision ships with an inspectable context dossier and a
written rationale**, every action lands in an audit log, and autonomy is earned
one trust-ladder level at a time.

> Tagline honored throughout the UI: **"No decision without a dossier."**

## Quick start

```bash
pip install -r requirements.txt   # fastapi, uvicorn, pytest
make run                          # or: python app.py
# open http://localhost:8000
```

Zero config, zero database, **zero network calls at runtime**. All "AI"
reasoning is simulated deterministically (rule evaluation + templates) behind an
`LLMProvider` interface, so nothing can fail live; a real model (e.g. the
Claude API) can be swapped in later without touching pipeline code.

## How to demo (5 lines)

1. `make run`, open http://localhost:8000. Panel 1: click **Dana Whitfield**, hit **Run pipeline**; walk the five stages (intake → dossier → policy → decision → draft, board member accepted inside 48h, times shown in her timezone).
2. Click **Jordan Ellis**, run it: the sensitive-category lockout banner fires. Personnel topics are hard-locked to human review; Maestro builds the dossier and stops.
3. Click **Grace Okafor**, run it: accepted with Sydney-fair slots (her mornings, Zeb's afternoons).
4. Switch to **Daily Brief**: rationales on every decision, optimizer patterns ("focus blocks eaten 3x"), then **Override** Grace's draft with any reason. A VIP reversal is a critical miss and auto-demotes external partners L1 → L0 live.
5. Switch to **Trust & Audit**: the demotion is already on the ladder, metrics moved, and the audit log shows every step you just took. `make reset` restores clean state between rehearsals.

## Architecture

```
                                ┌──────────────────────────────────────────┐
  data/requests.json            │              Pipeline (pipeline.py)      │
  (mock email/Slack/ClickUp) ──▶│                                          │
                                │  1. intake.py ──── RequestObject         │
  data/people.json ────────────▶│  2. context.py ─── Context Dossier ──────┼──▶ "No decision
  data/history.json ───────────▶│  3. policy.py ──── PolicyResult          │     without a dossier"
  data/policies.json ──────────▶│       (fired rules, hard locks,          │
                                │        slot constraints)                 │
  data/calendar.json ──────────▶│  4. prioritize.py ─ Decision + rationale │
  data/trust.json ─────────────▶│  5. negotiate.py ── Draft (CEO voice,    │
  data/voice.json ─────────────▶│        requester-local times) ───────────┼──▶ approval queue
                                └───────────────┬──────────────────────────┘    (never auto-sent)
                                                │ every stage
                                                ▼
                                     data/audit_log.jsonl ◀── every human action too
Background / feedback loops:
  optimizer.py ── weekly calendar pass: deep-work at risk, fragmentation,
                  batching, cascade warnings
  brief.py ────── Daily Brief (markdown): decisions + rationales, conflicts,
                  optimizer patterns, pending approvals
  evals.py ────── compares decisions to human verdicts (data/overrides.json):
                  acceptance rate, per-category accuracy
  trust.py ────── the ladder: promotions earned from eval metrics; demotion is
                  AUTOMATIC on any critical miss; sensitive categories locked L0

  llm.py ──────── LLMProvider seam: MockProvider (deterministic templates) now,
                  real model later, same interface, zero pipeline changes.
```

**Frontend**: one static page (`static/`), vanilla HTML/CSS/JS, no build step,
no CDN. Three panels: Request Pipeline, Daily Brief, Trust & Audit.

**Storage**: human-readable JSON in `/data`: edit anything by hand before the
demo. `data/seed/` holds pristine snapshots; `make reset` restores them.

## The trust ladder

| Level | Meaning | Promotion gate |
|-------|---------|----------------|
| L0 | Suggest only; human executes everything | - |
| L1 | Drafts replies + holds; human approves each | ≥95% acceptance over 2 weeks |
| L2 | Auto-handles low-stakes internal moves, logs all | ≥98% accuracy at L1, zero critical misses |
| L3 | Negotiates external scheduling within policy | Explicit human sign-off per category |

Demotion is **automatic** on any critical miss (a human reversal on a sensitive
category or VIP). Sensitive categories (board governance, legal, M&A,
personnel) are hard-locked to L0 forever by design.

## Tests

```bash
make test    # 33 tests: policy engine + trust ladder / eval loop
```

## Backup demo recording (dev-only)

```bash
pip install -r requirements-dev.txt   # playwright - NOT needed for make run
python -m playwright install chromium # one-time browser download
make record                           # writes demo_backup.webm (~77s, 1280x720)
```

`make record` drives the full scripted demo (board request, sensitive lockout,
brief + live override, trust demotion, audit scroll) and resets demo state when
done. If `ffmpeg` is on PATH it also converts the recording to
`demo_backup.mp4` (H.264, previews in Drive/email clients). The video files are
**excluded from git** (regenerable, binary); keep your local copy handy as the
screen-share fallback.

## Decisions I made (ambiguities resolved)

1. **Board scheduling vs. board sensitivity.** "Board members always get time
   within 48 hours" and "board is a sensitive category" conflict if read
   literally. Resolution: routine board *scheduling* proceeds under the 48h rule,
   but the board *category* is trust-locked at L0 (human executes every
   suggestion), and board-*governance topics* trigger the sensitive lockout.
   A sharp CoS books the board member fast and still never lets software decide
   governance matters.
2. **Request #5's meeting type.** The recruiter's request classifies as
   `external_partner` at intake (that's what the text looks like); the
   *dossier* carries the `personnel` sensitivity flag, and the lockout rule
   fires on the dossier, demonstrating why intake alone must never decide.
3. **Protected blocks are never offered proactively to anyone**, including
   board/legal; the board/legal exemption only governs *displacement* (a
   requester-proposed time inside a block).
4. **The eval window anchors to the newest overrides.json entry**, not the wall
   clock, so the mocked two-week history stays meaningful whenever the demo runs.
5. **"~12 rules" shipped as 13**, split into decisive rules (set the outcome)
   and `constrain` rules (shape slot selection); the engine records both kinds
   when fired, and the rationale cites the decisive one verbatim.
6. **demo "now"** is pinned in `data/calendar.json` (`demo_now`) so slot math
   is deterministic and hand-tunable.
7. **Escalations produce an internal routing note instead of an external
   reply**: escalating a journalist or a confidential personnel matter with an
   auto-reply would itself be a judgment call the system shouldn't make.
8. The only `http://` string in the codebase outside localhost is the SVG
   XML-namespace identifier in the favicon data URI: an identifier, not a
   request. Verify: `grep -rn "http" --include="*.py" --include="*.js" .`

## Repo layout

```
app.py                FastAPI server + approval/override endpoints
maestro/              the seven components + pipeline, models, store, audit, llm
data/                 all mock data (hand-editable JSON) + seed/ snapshots
static/               single-page UI (index.html, styles.css, app.js)
tests/                pytest suites for policy engine + trust ladder
scripts/reset_demo.py restore pristine demo state (make reset)
```
