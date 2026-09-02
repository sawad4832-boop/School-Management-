"""Regressionstest im echten Browser: Haken muessen Neustarts ueberstehen.

Hintergrund: Bei kostenlosen Hosting-Angeboten wird der Speicher des Servers
regelmaessig geleert (Schlafmodus, Neustart, neue Version). Ein abgehakter
Eintrag darf dadurch nicht wieder in der To-do-Liste auftauchen - das Handy
haelt eine eigene Kopie und setzt sie durch.

Braucht Playwright und einen Chromium; ohne beides wird der Test uebersprungen::

    pip install playwright && playwright install chromium
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import urllib.request

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright ist nicht installiert")

from playwright.sync_api import sync_playwright  # noqa: E402

PORT = 5099
URL = f"http://127.0.0.1:{PORT}"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _chromium_path() -> str | None:
    """Sucht einen vorhandenen Chromium; None -> Test wird uebersprungen."""
    for candidate in (
        os.environ.get("CHROMIUM_PATH"),
        "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        shutil.which("google-chrome"),
    ):
        if candidate and os.path.exists(candidate):
            return candidate
    return None


def _start_server(db_path: str) -> subprocess.Popen:
    env = {
        **os.environ,
        "SC_DB_PATH": db_path,
        "SECRET_KEY": "test-schluessel",
        "SC_PERSIST_SESSION": "1",
        "SC_REFRESH_MINUTES": "0",
        "SC_DASHBOARD_PIN": "",
        "PORT": str(PORT),
    }
    proc = subprocess.Popen(
        [sys.executable, os.path.join(REPO, "app.py")],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(40):
        try:
            urllib.request.urlopen(f"{URL}/api/health", timeout=1)
            return proc
        except Exception:
            time.sleep(0.5)
    proc.terminate()
    raise RuntimeError("Server ist nicht gestartet")


def test_checkmark_survives_restart_with_empty_storage(tmp_path):
    browser_path = _chromium_path()
    if not browser_path:
        pytest.skip("Kein Chromium gefunden")

    db = str(tmp_path / "e2e.sqlite3")
    server = _start_server(db)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=browser_path)
            page = browser.new_context(**p.devices["iPhone 13"]).new_page()
            page.goto(URL, wait_until="networkidle")
            page.tap("#btn-demo")
            page.wait_for_selector("#active-list li")

            title = page.locator("#active-list li:first-child h3").inner_text()
            page.tap("#active-list li:first-child .check-btn")
            page.wait_for_timeout(800)
            assert title not in page.locator("#active-list li h3").all_inner_texts()

            # Neustart des Servers mit geloeschtem Speicher
            server.terminate()
            server.wait(timeout=10)
            os.remove(db)
            server = _start_server(db)

            page.reload(wait_until="networkidle")
            page.wait_for_selector("#active-list li")
            page.wait_for_timeout(1000)
            assert title not in page.locator("#active-list li h3").all_inner_texts()

            page.tap("#toggle-archive")
            page.wait_for_timeout(400)
            archive = page.locator("#archive-list li p:first-child").all_inner_texts()
            assert any(title in entry for entry in archive)

            # Auch ein ausdrueckliches Aktualisieren holt ihn nicht zurueck
            page.tap("#btn-refresh")
            page.wait_for_timeout(1500)
            assert title not in page.locator("#active-list li h3").all_inner_texts()

            browser.close()
    finally:
        server.terminate()
