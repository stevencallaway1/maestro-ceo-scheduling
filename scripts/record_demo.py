"""Record a backup demo video (dev-only; requires playwright + chromium).

Launches the Maestro server, drives the scripted demo sequence in a headless
Chromium at 1280x720 with human-paced delays, and writes demo_backup.webm to
the repo root. Run via ``make record``. The run mutates demo state (it
performs a live override), so run ``make reset`` afterwards — ``make record``
does this automatically.

Not needed for ``make run``: the app itself has zero browser/automation deps.
"""
from __future__ import annotations

import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
VIDEO_OUT = ROOT / "demo_backup.webm"
BASE = "http://127.0.0.1:8000"
SIZE = {"width": 1280, "height": 720}


def wait_for_server(timeout: float = 15.0) -> None:
    """Poll until the app answers."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(BASE + "/api/requests", timeout=1)
            return
        except Exception:
            time.sleep(0.3)
    raise SystemExit("Maestro server did not come up on :8000")


def pause(seconds: float) -> None:
    time.sleep(seconds)


def slow_scroll(page: Page, total_px: int, step: int = 250, delay: float = 0.45) -> None:
    """Scroll the window down gradually so content is readable on video."""
    scrolled = 0
    while scrolled < total_px:
        page.mouse.wheel(0, step)
        scrolled += step
        time.sleep(delay)


def run_request(page: Page, request_id: str) -> None:
    """Select an inbox item and run the animated pipeline to completion."""
    page.locator(f'.req-card[data-id="{request_id}"]').click()
    pause(2.0)
    page.locator("#run-btn").click()
    page.wait_for_selector("#stage-draft.done", timeout=30000)


def main() -> None:
    server = subprocess.Popen(
        [sys.executable, str(ROOT / "app.py")],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    started = time.time()
    try:
        wait_for_server()
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            context = browser.new_context(
                viewport=SIZE, record_video_dir=str(ROOT / ".video_tmp"),
                record_video_size=SIZE,
            )
            page = context.new_page()
            page.goto(BASE)
            pause(2.5)

            # --- a) Request #1: board member, full animated pipeline ---
            run_request(page, "req-001")
            pause(2.0)
            # Hold on the dossier, then walk down to the rationale and draft.
            page.locator("#stage-dossier").scroll_into_view_if_needed()
            pause(4.0)
            slow_scroll(page, 900)
            page.locator("#stage-decision .rationale").scroll_into_view_if_needed()
            pause(4.5)
            page.locator("#stage-draft .email").scroll_into_view_if_needed()
            pause(3.5)

            # --- b) Request #5: sensitive-category lockout ---
            page.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
            pause(1.5)
            run_request(page, "req-005")
            page.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
            page.locator("#lockout-banner").wait_for(state="visible")
            pause(4.0)  # hold on the red lockout banner
            page.locator("#stage-decision .rationale").scroll_into_view_if_needed()
            pause(3.5)

            # --- c) Daily Brief: read, then override one queued draft ---
            page.locator('.tab[data-panel="brief"]').click()
            page.wait_for_selector(".approval")
            pause(3.0)
            slow_scroll(page, 1400)
            pause(1.5)
            card = page.locator('.approval[data-id="appr-req-005"]')
            card.scroll_into_view_if_needed()
            pause(2.0)
            card.locator(".act-override").click()
            pause(1.5)
            card.locator("input").type("Prefer to take this one myself", delay=45)
            pause(1.0)
            card.locator(".act-confirm").click()
            pause(3.5)  # toast: critical miss -> automatic demotion

            # --- d) Trust panel: demotion visible, then the audit log ---
            page.locator('.tab[data-panel="trust"]').click()
            page.wait_for_selector(".ladder-row")
            pause(3.0)
            row = page.locator(".ladder-row", has_text="External partners")
            row.scroll_into_view_if_needed()
            row.locator("summary").click()  # expand promotion/demotion history
            pause(4.5)
            audit = page.locator("#audit-feed")
            audit.scroll_into_view_if_needed()
            pause(2.0)
            for _ in range(5):
                page.locator(".audit-panel").evaluate("el => el.scrollBy({top: 220, behavior: 'smooth'})")
                pause(1.2)
            pause(2.0)

            video = page.video
            context.close()
            if video:
                video.save_as(str(VIDEO_OUT))
            browser.close()
    finally:
        server.terminate()
        server.wait()

    duration = time.time() - started
    size_mb = VIDEO_OUT.stat().st_size / 1e6
    print(f"Recorded {VIDEO_OUT.name}: {size_mb:.1f} MB, ~{duration:.0f}s of footage.")
    if duration < 60:
        raise SystemExit("Recording came out under 60 seconds — check pacing.")


if __name__ == "__main__":
    main()
