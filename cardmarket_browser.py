#!/usr/bin/env python3
"""Ein eigener, unauffaelliger Browser fuer den eingeloggten Cardmarket-Bereich.

Warum nicht mehr per CDP an den laufenden Browser andocken: Ein Chrome mit
offenem --remote-debugging-port ist fuer Cloudflare ein klares Automatik-
Merkmal — genau der Unterschied zu scrape.py, das seit Monaten mit demselben
Chrome und denselben IPs laeuft und kaum geprueft wird.

Deshalb: Der Dauer-Browser fuer noVNC wird fuer die Dauer eines Laufs
gestoppt, das Skript startet Chrome selbst auf demselben Profil (Anmeldung
bleibt erhalten) und gibt den Dienst danach wieder frei. Bright Data kann
diesen Teil nicht uebernehmen — getestet am 02.09.2026, Cookies werden dort
verworfen bzw. das Einschleusen ist verboten.
"""
from __future__ import annotations

import contextlib
import logging
import os
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROFIL = ROOT / "data" / "patchright-profile"
DIENST = "cardmarket-browser"

log = logging.getLogger("browser")

os.environ.setdefault("DISPLAY", ":99")


def _systemctl(*args: str) -> bool:
    try:
        r = subprocess.run(["systemctl", *args, DIENST], capture_output=True, timeout=30)
        return r.returncode == 0
    except Exception as e:  # kein systemd (z.B. lokal) -> einfach weitermachen
        log.debug("systemctl %s: %s", args, e)
        return False


def _dienst_laeuft() -> bool:
    return _systemctl("is-active", "--quiet")


@contextlib.contextmanager
def eigener_browser(start_url: str | None = None):
    """Liefert (context, page). Stoppt den noVNC-Browser nur, wenn er lief,
    und startet ihn dann hinterher wieder — auch bei Fehlern."""
    from patchright.sync_api import sync_playwright

    lief = _dienst_laeuft()
    if lief:
        _systemctl("stop")
        # Chrome gibt das Profil nicht sofort frei.
        for _ in range(20):
            if not (PROFIL / "SingletonLock").exists():
                break
            time.sleep(0.5)

    PROFIL.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFIL),
            channel="chrome",
            headless=False,
            no_viewport=True,
            args=[
                "--window-size=1280,900",
                "--window-position=0,0",
                "--no-first-run",
                "--disable-session-crashed-bubble",
                "--disable-dev-shm-usage",
            ],
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            if start_url:
                page.goto(start_url, wait_until="domcontentloaded", timeout=60000)
            yield context, page
        finally:
            with contextlib.suppress(Exception):
                context.close()
            if lief:
                _systemctl("start")
