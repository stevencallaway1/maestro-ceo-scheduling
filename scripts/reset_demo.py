"""Reset demo state.

Restores the pristine snapshots in data/seed/ over the runtime-mutable files so
every run starts from the same place. Same code path as the Reset demo button
in the UI (``POST /api/reset``); use this one between local runs, and the
button while a server is up.

Usage: python scripts/reset_demo.py   (or ``make reset``)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from maestro import store  # noqa: E402


def main() -> None:
    for name in store.reset():
        print(f"  reset {name}")
    print("Demo state restored.")


if __name__ == "__main__":
    main()
