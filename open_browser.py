#!/usr/bin/env python3
"""Haelt einen sichtbaren Browser auf dem noVNC-Display offen.

Dient dem einmaligen Anmelden bei Cardmarket: das Profil unter
data/patchright-profile ist dasselbe, das der Scraper spaeter benutzt — wer sich
hier anmeldet, ist auch dort angemeldet.

Kein --remote-debugging-port mehr: der ist fuer Cloudflare ein Automatik-Merkmal.
Skripte, die den eingeloggten Bereich brauchen, stoppen diesen Dienst kurz und
starten Chrome selbst auf demselben Profil (siehe cardmarket_browser.py).

Bewusst ueber Patchright und `channel="chrome"` gestartet, nicht ueber das
Playwright-eigene Chromium: nur so laeuft es in diesem Container stabil (das
mitgelieferte Chromium stirbt mit SIGTRAP an der fehlenden GPU).

Laeuft als systemd-Dienst `cardmarket-browser`. Erreichbar unter
http://192.168.1.91:6080/vnc.html
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
# Bewusst KEINE Cardmarket-Adresse: der Dauerbrowser laedt seine Startseite bei
# jedem Neustart (auch beim naechtlichen um 03:35). Landet er dabei in Cloudflares
# Bot-Pruefung, laedt die sich im Sekundentakt selbst neu — ueber Stunden ergibt
# das die Ratensperre 1015. Am 07. und 08.09. genau so passiert.
# Cardmarket wird nur noch geladen, wenn ein Mensch klickt oder ein Skript steuert.
START_URL = os.environ.get("START_URL", "")

os.environ.setdefault("DISPLAY", ":99")

from patchright.sync_api import sync_playwright  # noqa: E402


LOGIN_URL = "https://www.cardmarket.com/de/Pokemon/Account/Login"


def parkseite(page) -> None:
    """Lokale Seite mit Link — laedt nichts von Cardmarket.

    Der Link wird erst durch einen Klick zur Anfrage. So steht der Browser nie
    unbeaufsichtigt auf einer Seite, die sich selbst neu laedt.

    Die Seite liegt bewusst als Datei vor und wird ueber file:// geladen. Wird
    der Inhalt stattdessen per set_content() in ein leeres about:blank gesetzt,
    hat das Dokument keinen eigenen Ursprung — Chrome laesst aus so einem
    Dokument keinen Klick auf einen https-Link nach aussen zu. Genau das ist am
    08.09. passiert: die Seite stand da, der Knopf tat nichts.
    """
    ziel = BASE_DIR / "data" / "parkseite.html"
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_text(
        '<!doctype html><meta charset="utf-8"><title>Browser bereit</title>'
        '<body style="margin:0;display:grid;place-items:center;height:100vh;'
        'font:22px system-ui;background:#16191c;color:#e8e6e1">'
        '<div style="text-align:center;max-width:34em">'
        '<p style="color:#8d949b;font-size:15px;line-height:1.5">Browser bereit. '
        'Cardmarket wird bewusst nicht automatisch geladen &mdash; eine offene '
        'Bot-Pr&uuml;fung w&uuml;rde sich endlos neu laden und eine Sperre '
        'ausl&ouml;sen.</p>'
        f'<a href="{LOGIN_URL}" style="display:inline-block;padding:14px 26px;'
        'border-radius:8px;background:#d9a441;color:#16191c;text-decoration:none;'
        'font-weight:600">Bei Cardmarket anmelden</a>'
        '<p style="color:#6e767d;font-size:13px;margin-top:22px">Nach dem Anmelden '
        'einfach hier lassen &mdash; die Sitzung wird gesichert.</p></div></body>',
        encoding="utf-8")
    page.goto(ziel.as_uri())


def sitzungswiederherstellung_aus(profil: Path) -> None:
    """Chrome soll nach dem Beenden nichts zurueckholen.

    systemctl stop schickt SIGTERM; Chrome kommt nicht dazu, sauber zu
    schliessen, und vermerkt exit_type="Crashed". Beim naechsten Start holt er
    dann die letzten Tabs zurueck — nach einer Sperre also ausgerechnet die
    Cloudflare-Seite, die sich selbst nachlaedt. Deshalb vor jedem Start:
    Absturzvermerk loeschen und Wiederherstellung auf "leere Seite" stellen.
    """
    pref = profil / "Default" / "Preferences"
    if not pref.exists():
        return
    try:
        d = json.loads(pref.read_text())
        d.setdefault("session", {})["restore_on_startup"] = 5   # 5 = leere Seite
        d["session"]["startup_urls"] = []
        d.setdefault("profile", {})["exit_type"] = "Normal"
        d["profile"]["exited_cleanly"] = True
        pref.write_text(json.dumps(d))
    except Exception as e:
        print(f"Preferences nicht angepasst: {e}", flush=True)


def main() -> int:
    profile_dir = BASE_DIR / "data" / "patchright-profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    sitzungswiederherstellung_aus(profile_dir)

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
            ],
        )
        # Beim Start die gesicherte Anmeldung zurueckspielen, bevor die erste
        # Seite geladen wird — sonst sieht Cardmarket einen anonymen Besucher.
        try:
            from cardmarket_browser import _sitzung_zurueckspielen
            n = _sitzung_zurueckspielen(context)
            if n:
                print(f"{n} Sitzungs-Cookies zurueckgespielt", flush=True)
        except Exception as e:
            print(f"Sitzung nicht zurueckgespielt: {e}", flush=True)

        # Chrome holt nach einem harten Ende die zuletzt offenen Tabs zurueck.
        # Nach einer Sperre ist das die Cloudflare-Seite — die laedt sich selbst
        # nach und zieht die naechste Sperre. Deshalb: alle Tabs bis auf einen
        # schliessen und den auf die lokale Parkseite setzen.
        page = context.pages[0] if context.pages else context.new_page()
        for weiterer in context.pages[1:]:
            with contextlib.suppress(Exception):
                weiterer.close()
        if context.pages[1:]:
            print(f"{len(context.pages[1:])} wiederhergestellte Tabs geschlossen", flush=True)

        try:
            if START_URL:
                page.goto(START_URL, wait_until="domcontentloaded", timeout=60000)
            else:
                parkseite(page)
        except Exception as e:
            print(f"Startseite nicht geladen: {e}", flush=True)

        print("Browser offen. Anmelden ueber http://192.168.1.91:6080/vnc.html", flush=True)

        # Offen halten, bis der Dienst gestoppt wird. Schliesst jemand das letzte
        # Fenster im noVNC, beenden wir uns — systemd startet dann neu.
        #
        # Nicht dauerhaft auf der Cardmarket-Seite sitzen bleiben: deren Skripte
        # (Tracking, Cloudflare-Pruefschleifen) haben den Browser am 06./07.09.
        # auf ueber 3 GB anwachsen lassen. Nach zehn Minuten ohne Nutzung wird
        # auf eine leere Seite gewechselt — die Anmeldung liegt im Profil, nicht
        # im Fenster.
        PARKEN_NACH_S = 1800
        gestartet = time.monotonic()
        geparkt = False
        while True:
            time.sleep(10)
            if not context.pages:
                print("Kein Fenster mehr offen — beende mich.", flush=True)
                break
            if not geparkt and time.monotonic() - gestartet > PARKEN_NACH_S:
                try:
                    for weiterer in context.pages[1:]:
                        with contextlib.suppress(Exception):
                            weiterer.close()
                    if "cardmarket.com" in page.url:
                        parkseite(page)
                        print("Geparkt (Speicher) — Link zurueck steht.", flush=True)
                except Exception as e:
                    print(f"Parken nicht moeglich: {e}", flush=True)
                geparkt = True
        # Vor dem Beenden die Anmeldung sichern, damit sie den Neustart ueberlebt.
        try:
            from cardmarket_browser import _sitzung_sichern
            print(f"{_sitzung_sichern(context)} Sitzungs-Cookies gesichert", flush=True)
        except Exception as e:
            print(f"Sitzung nicht gesichert: {e}", flush=True)
        context.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
