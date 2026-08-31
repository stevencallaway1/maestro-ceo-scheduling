"""State store. Every piece of Maestro state is a human-readable JSON file
under /data, held in memory while the process runs.

No database by design: the demo owner can hand-edit any input file before a
run, and a reviewer can read the entire system state without tooling.

Reads are served from an in-process cache keyed by data directory, so tests
that point ``DATA_DIR`` at a sandbox get a clean view. Writes update the cache
first and then try to persist to disk. On a read-only filesystem - which is
what a serverless host gives you - the disk write is skipped and the run
continues against memory. That is what lets the deployed demo be fully
interactive: state is real for the life of the instance and resets on a cold
start or when a reviewer hits Reset demo.
"""
from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SEED_DIRNAME = "seed"

# Files the running system writes. Everything else in /data is a static input.
RUNTIME_FILES = [
    "approvals.json",
    "decisions.json",
    "overrides.json",
    "trust.json",
    "executions.json",
    "audit_log.jsonl",
]

_cache: dict[tuple[str, str], Any] = {}
_disk_writable: bool | None = None


def path_for(name: str) -> Path:
    """Absolute path for a data file name (e.g. ``people.json``)."""
    return DATA_DIR / name


def _key(name: str) -> tuple[str, str]:
    return (str(DATA_DIR), name)


def disk_writable() -> bool:
    """Whether state persists to disk here, or lives only in memory."""
    global _disk_writable
    if _disk_writable is None:
        probe = DATA_DIR / ".write-probe"
        try:
            probe.write_text("", encoding="utf-8")
            probe.unlink()
            _disk_writable = True
        except OSError:
            _disk_writable = False
    return _disk_writable


def load(name: str) -> Any:
    """Load a JSON file from /data. Returns a copy the caller may mutate."""
    key = _key(name)
    if key not in _cache:
        path = path_for(name)
        if not path.exists():
            _cache[key] = []
        else:
            _cache[key] = json.loads(path.read_text(encoding="utf-8"))
    return copy.deepcopy(_cache[key])


def save(name: str, payload: Any) -> None:
    """Write a JSON file, pretty-printed so it stays hand-editable."""
    _cache[_key(name)] = copy.deepcopy(payload)
    if not disk_writable():
        return
    try:
        path_for(name).write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    except OSError:
        pass


def read_jsonl(name: str) -> list[dict[str, Any]]:
    """Read all records from a JSON-lines file (empty list if missing)."""
    key = _key(name)
    if key not in _cache:
        path = path_for(name)
        if not path.exists():
            _cache[key] = []
        else:
            _cache[key] = [json.loads(line)
                           for line in path.read_text(encoding="utf-8").splitlines()
                           if line.strip()]
    return copy.deepcopy(_cache[key])


def append_jsonl(name: str, record: dict[str, Any]) -> None:
    """Append one record to a JSON-lines file."""
    key = _key(name)
    if key not in _cache:
        read_jsonl(name)
    _cache[key].append(copy.deepcopy(record))
    if not disk_writable():
        return
    try:
        with path_for(name).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def reset() -> list[str]:
    """Restore every runtime file from data/seed/. Returns the files reset.

    Clears the in-process cache first so a running server picks the seed up
    immediately, then mirrors it to disk when the filesystem allows.
    """
    seed = DATA_DIR / SEED_DIRNAME
    done: list[str] = []
    for name in RUNTIME_FILES:
        src = seed / name
        if not src.exists():
            continue
        _cache.pop(_key(name), None)
        if name.endswith(".jsonl"):
            records = [json.loads(line) for line in src.read_text(encoding="utf-8").splitlines()
                       if line.strip()]
            _cache[_key(name)] = records
        else:
            _cache[_key(name)] = json.loads(src.read_text(encoding="utf-8"))
        if disk_writable():
            try:
                shutil.copyfile(src, path_for(name))
            except OSError:
                pass
        done.append(name)
    return done
