#!/usr/bin/env python3
"""Haelt einen sichtbaren Browser auf dem noVNC-Display offen.

Dient dem einmaligen Anmelden bei Cardmarket: das Profil unter
data/patchright-profile ist dasselbe, das der Scraper spaeter benutzt — wer sich
hier anmeldet, ist auch dort angemeldet.

Bewusst ueber Patchright und `channel="chrome"` gestartet, nicht ueber das
Playwright-eigene Chromium: nur so laeuft es in diesem Container stabil (das
mitgelieferte Chromium stirbt mit SIGTRAP an der fehlenden GPU).

Laeuft als systemd-Dienst `cardmarket-browser`. Erreichbar unter
http://192.168.1.91:6080/vnc.html
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
START_URL = os.environ.get("START_URL", "https://www.cardmarket.com/de/Pokemon")
CDP_PORT = int(os.environ.get("CDP_PORT", "9222"))

os.environ.setdefault("DISPLAY", ":99")

from patchright.sync_api import sync_playwright  # noqa: E402


def main() -> int:
    profile_dir = BASE_DIR / "data" / "patchright-profile"
    profile_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            channel="chrome",
            headless=False,
            no_viewport=True,
            args=[
                "--window-size=1280,900",
                "--window-position=0,0",
                "--no-first-run",
                "--disable-session-crashed-bubble",
                "--disable-dev-shm-usage",
                # Damit andere Skripte die angemeldete Sitzung mitbenutzen koennen,
                # ohne das Profil zu sperren. Nur auf localhost erreichbar.
                f"--remote-debugging-port={CDP_PORT}",
            ],
        )
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.goto(START_URL, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print(f"Startseite nicht geladen: {e}", flush=True)

        print(f"Browser offen (CDP auf :{CDP_PORT}). "
              f"Anmelden ueber http://192.168.1.91:6080/vnc.html", flush=True)

        # Offen halten, bis der Dienst gestoppt wird. Schliesst jemand das letzte
        # Fenster im noVNC, beenden wir uns — systemd startet dann neu.
        while True:
            time.sleep(10)
            if not context.pages:
                print("Kein Fenster mehr offen — beende mich.", flush=True)
                break
        context.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
