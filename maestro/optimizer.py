"""Calendar Optimizer: background pass over the week's calendar.

Produces four kinds of findings, all computed deterministically:
  1. Deep-work blocks at risk (which meetings ate them)
  2. Per-day fragmentation score (sub-30-minute gaps between meetings)
  3. Batching suggestions (scattered external 1:1s -> one open block)
  4. Cascade warnings (meetings whose move would displace downstream meetings)
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from .calendarlib import WEEKDAY_ABBR, fmt_local, parse_ts, _day_events, _free_gaps


def _deep_work_at_risk(cal: dict[str, Any]) -> dict[str, Any]:
    """Find protected blocks with meetings booked inside them."""
    blocks = []
    total_intrusions = 0
    for block in [e for e in cal["events"] if e.get("protected")]:
        b_start, b_end = parse_ts(block["start"]), parse_ts(block["end"])
        eaters = [
            {"title": ev["title"], "when": fmt_local(parse_ts(ev["start"]), cal["timezone"])}
            for ev in cal["events"]
            if ev["id"] != block["id"] and not ev.get("protected")
            and parse_ts(ev["start"]) < b_end and parse_ts(ev["end"]) > b_start
        ]
        total_intrusions += len(eaters)
        blocks.append({
            "block": block["title"],
            "when": fmt_local(b_start, cal["timezone"]),
            "at_risk": bool(eaters),
            "eaten_by": eaters,
        })
    return {
        "blocks": blocks,
        "at_risk_count": sum(1 for b in blocks if b["at_risk"]),
        "intrusion_count": total_intrusions,
    }


def _fragmentation(cal: dict[str, Any]) -> list[dict[str, Any]]:
    """Score each day: sub-30-minute gaps are dead time, not usable time."""
    days = []
    for day_iso, events in _day_events(cal).items():
        gaps = []
        for prev, nxt in zip(events, events[1:]):
            gap_min = (parse_ts(nxt["start"]) - parse_ts(prev["end"])).total_seconds() / 60
            if 0 < gap_min < 30:
                gaps.append({"after": prev["title"], "before": nxt["title"], "minutes": int(gap_min)})
        wasted = sum(g["minutes"] for g in gaps)
        score = max(0, 100 - 15 * len(gaps) - wasted // 2)
        weekday = WEEKDAY_ABBR[parse_ts(events[0]["start"]).weekday()]
        days.append({
            "date": day_iso, "weekday": weekday, "meetings": len(events),
            "sub30_gaps": gaps, "wasted_minutes": wasted, "score": score,
        })
    return days


def _batching(cal: dict[str, Any]) -> list[str]:
    """Suggest consolidating scattered external 1:1s into the largest open block."""
    suggestions = []
    externals = [e for e in cal["events"] if e["type"] == "external_partner"
                 and (parse_ts(e["end"]) - parse_ts(e["start"])) <= timedelta(minutes=45)]
    scattered_days = {parse_ts(e["start"]).date() for e in externals}
    if len(externals) >= 3 and len(scattered_days) >= 2:
        gaps = sorted(_free_gaps(cal), key=lambda g: g[0] - g[1])  # longest first
        if gaps:
            g_start, g_end = gaps[0]
            open_desc = (f"{WEEKDAY_ABBR[g_start.weekday()]} "
                         f"{g_start.strftime('%H:%M')}-{g_end.strftime('%H:%M')}")
            titles = ", ".join(e["title"].replace("External 1:1 - ", "") for e in externals[:3])
            suggestions.append(
                f"{len(externals)} external 1:1s ({titles}) are scattered across "
                f"{len(scattered_days)} days. They could consolidate into the open "
                f"{open_desc} block, freeing contiguous time elsewhere."
            )
    return suggestions


def _cascades(cal: dict[str, Any]) -> list[str]:
    """Warn about meetings whose downstream dependency chains make moves expensive."""
    warnings = []
    for ev in cal["events"]:
        downstream = ev.get("downstream", [])
        if downstream:
            warnings.append(
                f'Moving "{ev["title"]}" ({fmt_local(parse_ts(ev["start"]), cal["timezone"])}) '
                f"would displace {len(downstream)} downstream meetings: {', '.join(downstream)}."
            )
    return warnings


def run(cal: dict[str, Any]) -> dict[str, Any]:
    """Run the full optimizer pass and return all findings."""
    deep_work = _deep_work_at_risk(cal)
    frag = _fragmentation(cal)
    return {
        "deep_work": deep_work,
        "fragmentation": frag,
        "batching_suggestions": _batching(cal),
        "cascade_warnings": _cascades(cal),
        "patterns": _patterns(deep_work, frag),
    }


def _patterns(deep_work: dict[str, Any], frag: list[dict[str, Any]]) -> list[str]:
    """Week-level patterns worth a CEO's attention, phrased plainly."""
    patterns = []
    if deep_work["intrusion_count"]:
        patterns.append(
            f"Focus blocks eaten {deep_work['intrusion_count']}x this week "
            f"({deep_work['at_risk_count']} of {len(deep_work['blocks'])} protected blocks compromised)."
        )
    worst = min(frag, key=lambda d: d["score"], default=None)
    if worst and worst["score"] < 70:
        patterns.append(
            f"{worst['weekday']} is the most fragmented day (score {worst['score']}/100, "
            f"{len(worst['sub30_gaps'])} sub-30-minute gaps = {worst['wasted_minutes']} unusable minutes)."
        )
    return patterns
