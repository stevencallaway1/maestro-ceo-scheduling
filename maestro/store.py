"""Tiny JSON file store. All Maestro state lives in human-readable files under /data.

No database by design: the demo owner can hand-edit any file before a run.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def path_for(name: str) -> Path:
    """Return the absolute path for a data file name (e.g. ``people.json``)."""
    return DATA_DIR / name


def load(name: str) -> Any:
    """Load and parse a JSON file from /data."""
    return json.loads(path_for(name).read_text(encoding="utf-8"))


def save(name: str, payload: Any) -> None:
    """Write a JSON file to /data, pretty-printed so it stays hand-editable."""
    path_for(name).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def append_jsonl(name: str, record: dict[str, Any]) -> None:
    """Append one record to a JSON-lines file in /data."""
    with path_for(name).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_jsonl(name: str) -> list[dict[str, Any]]:
    """Read all records from a JSON-lines file (empty list if missing)."""
    p = path_for(name)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
