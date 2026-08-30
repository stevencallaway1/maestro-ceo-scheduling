"""Reset demo state.

Copies the pristine snapshots in data/seed/ back over the five runtime-mutable
files (approvals, decisions, overrides, trust, audit log) so every rehearsal
and the live demo start from the same clean state. Hand-edited seed files:
edit data/seed/* and re-run.

Usage: python scripts/reset_demo.py   (or ``make reset``)
"""
from __future__ import annotations

import shutil
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
SEED = DATA / "seed"
RUNTIME_FILES = [
    "approvals.json",
    "decisions.json",
    "overrides.json",
    "trust.json",
    "audit_log.jsonl",
]


def main() -> None:
    for name in RUNTIME_FILES:
        src = SEED / name
        if not src.exists():
            raise SystemExit(f"Missing seed file: {src}")
        shutil.copyfile(src, DATA / name)
        print(f"  reset {name}")
    print("Demo state restored.")


if __name__ == "__main__":
    main()
