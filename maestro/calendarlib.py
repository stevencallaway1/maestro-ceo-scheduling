"""Calendar math shared by the Planner and the Calendar Optimizer.

Deterministic, pure functions over data/calendar.json: free-slot search under
policy constraints, protected-block collision checks, and timezone-fair
rendering. All times use stdlib ``zoneinfo`` - no network, no third-party
timezone data.

``find_slots`` is the reason the Planner can never invent a time: it only ever
returns slots that are provably free, inside business hours, and compliant
with every constraint the policy engine attached.
"""
from __future__ import annotations

from datetime import datetime, timedelta, time
from typing import Any
from zoneinfo import ZoneInfo

from .models import Slot

WEEKDAY_ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
LEAD_TIME_MINUTES = 60  # never offer a slot starting sooner than this
CANDIDATE_STEP_MINUTES = 30


def parse_ts(iso: str) -> datetime:
    """Parse an ISO timestamp with offset into an aware datetime."""
    return datetime.fromisoformat(iso)


def _sorted_events(cal: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(cal["events"], key=lambda e: e["start"])


def _day_events(cal: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Group events by calendar date string (in the CEO timezone)."""
    days: dict[str, list[dict[str, Any]]] = {}
    for ev in _sorted_events(cal):
        days.setdefault(parse_ts(ev["start"]).date().isoformat(), []).append(ev)
    return days


def fmt_local(dt: datetime, tz: str) -> str:
    """Human-readable local rendering, e.g. 'Tue Sep 1, 2:30 PM AEST'."""
    local = dt.astimezone(ZoneInfo(tz))
    day = WEEKDAY_ABBR[local.weekday()]
    hour = local.strftime("%I:%M %p").lstrip("0")
    tzname = local.tzname() or tz
    return f"{day} {local.strftime('%b')} {local.day}, {hour} {tzname}"


def in_protected_block(cal: dict[str, Any], start_iso: str, duration_minutes: int) -> bool:
    """True if [start, start+duration) overlaps any protected event."""
    start = parse_ts(start_iso)
    end = start + timedelta(minutes=duration_minutes)
    for ev in cal["events"]:
        if not ev.get("protected"):
            continue
        ev_start, ev_end = parse_ts(ev["start"]), parse_ts(ev["end"])
        if start < ev_end and end > ev_start:
            return True
    return False


def _free_gaps(cal: dict[str, Any]) -> list[tuple[datetime, datetime]]:
    """All free intervals within business hours across the loaded week.

    Every event, protected blocks included, counts as busy, so protected
    deep-work time is never offered proactively to anyone.
    """
    tz = ZoneInfo(cal["timezone"])
    bh_start = time.fromisoformat(cal["business_hours"]["start"])
    bh_end = time.fromisoformat(cal["business_hours"]["end"])
    gaps: list[tuple[datetime, datetime]] = []
    for day_iso, events in _day_events(cal).items():
        day = datetime.fromisoformat(day_iso).date()
        cursor = datetime.combine(day, bh_start, tzinfo=tz)
        day_end = datetime.combine(day, bh_end, tzinfo=tz)
        for ev in events:
            ev_start, ev_end = parse_ts(ev["start"]), parse_ts(ev["end"])
            if ev_start > cursor:
                gaps.append((cursor, min(ev_start, day_end)))
            cursor = max(cursor, ev_end)
        if cursor < day_end:
            gaps.append((cursor, day_end))
    return [(s, e) for s, e in gaps if e > s]


def _round_up(dt: datetime, minutes: int) -> datetime:
    """Round a datetime up to the next boundary of ``minutes``."""
    excess = (dt.minute % minutes, dt.second, dt.microsecond)
    if excess == (0, 0, 0):
        return dt
    return dt.replace(second=0, microsecond=0) + timedelta(minutes=minutes - dt.minute % minutes)


def _in_prefer_window(dt: datetime, windows: list[dict[str, str]]) -> bool:
    for w in windows:
        if WEEKDAY_ABBR[dt.weekday()] != w["weekday"]:
            continue
        if time.fromisoformat(w["start"]) <= dt.time() < time.fromisoformat(w["end"]):
            return True
    return False


def find_slots(cal: dict[str, Any], duration_minutes: int, constraints: list[dict[str, Any]],
               requester_tz: str, limit: int = 3) -> list[Slot]:
    """Search free calendar gaps for slots that satisfy every constraint.

    Constraint types (attached by fired policy rules):
      - within_hours: slot must start within N hours of demo_now
      - no_start_before_local: no starts before HH:00 CEO-local
      - requester_hours: slot must land inside the requester's local day
      - prefer_windows: matching slots sort first (batching policy)
      - exclude_protected: honored implicitly (protected time is never free)
    """
    now = parse_ts(cal["demo_now"])
    earliest = now + timedelta(minutes=LEAD_TIME_MINUTES)
    deadline: datetime | None = None
    min_local_hour: int | None = None
    req_hours: tuple[int, int] | None = None
    prefer_windows: list[dict[str, str]] = []
    for c in constraints:
        if c["type"] == "within_hours":
            deadline = now + timedelta(hours=c["hours"])
        elif c["type"] == "no_start_before_local":
            min_local_hour = c["hour"]
        elif c["type"] == "requester_hours":
            req_hours = (c["start_hour"], c["end_hour"])
        elif c["type"] == "prefer_windows":
            prefer_windows = c["windows"]

    req_zone = ZoneInfo(requester_tz)
    candidates: list[tuple[bool, datetime]] = []
    for gap_start, gap_end in _free_gaps(cal):
        cursor = _round_up(max(gap_start, earliest), CANDIDATE_STEP_MINUTES)
        while cursor + timedelta(minutes=duration_minutes) <= gap_end:
            ok = True
            if deadline is not None and cursor > deadline:
                ok = False
            if ok and min_local_hour is not None and cursor.time() < time(min_local_hour, 0):
                ok = False
            if ok and req_hours is not None:
                local = cursor.astimezone(req_zone)
                local_end = (cursor + timedelta(minutes=duration_minutes)).astimezone(req_zone)
                if not (req_hours[0] <= local.hour < req_hours[1]) or \
                        local_end.time() > time(req_hours[1], 0):
                    ok = False
            if ok:
                candidates.append((_in_prefer_window(cursor, prefer_windows), cursor))
            cursor += timedelta(minutes=CANDIDATE_STEP_MINUTES)

    # Selection prefers the policy's batching windows, then earliest; one slot
    # per cluster (never offer 15:00 and 15:30 out of the same open block).
    candidates.sort(key=lambda c: (not c[0], c[1]))
    picked: list[datetime] = []
    for _, start in candidates:
        if any(abs((start - p).total_seconds()) < 5400 for p in picked):
            continue
        picked.append(start)
        if len(picked) == limit:
            break

    # Presentation is always chronological, whatever order they were picked in.
    picked.sort()

    ceo_tz = cal["timezone"]
    return [
        Slot(
            start=s.isoformat(),
            end=(s + timedelta(minutes=duration_minutes)).isoformat(),
            ceo_local=fmt_local(s, ceo_tz),
            requester_local=fmt_local(s, requester_tz),
        )
        for s in picked
    ]
