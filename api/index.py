"""Vercel entry point.

Vercel runs the app as a serverless function, so the whole repo root has to be
importable and every request routes here (see vercel.json). The exported
``app`` is the same ASGI application ``python app.py`` serves locally - there
is no separate production code path.

On Vercel the bundle filesystem is read only, so ``maestro.store`` keeps state
in memory for the life of the instance and resets on a cold start. The UI says
so, and the Reset demo button restores the seeded state on demand.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import app  # noqa: E402

__all__ = ["app"]
