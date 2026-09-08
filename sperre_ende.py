#!/usr/bin/env python3
"""Prueft behutsam, ob eine Cardmarket-Ratensperre vorbei ist.

Genau EIN Abruf pro Versuch, per curl statt Browser: eine Browserseite haelt
Verbindungen offen und laedt bei einer Sperrseite nach, ein einzelner Abruf
nicht. Ist noch gesperrt, wird die Frist um zwei Stunden verlaengert und
spaeter erneut geprueft — ein Dauerklopfen setzt die Sperre sonst immer neu.

Erst wenn die Seite normal antwortet, faehrt der Browser hoch.
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from cardmarket_guard import Gesperrt, sperre_aufheben, sperre_pruefen, sperre_setzen

NOVNC = "http://192.168.1.91:6080/vnc.html"
TEST_URL = "https://www.cardmarket.com/de/Pokemon"
NAECHSTER_VERSUCH_H = 2


def einmal_anklopfen() -> tuple[bool, str]:
    """True, wenn die Seite normal antwortet. Ein Abruf, kein Nachladen."""
    r = subprocess.run(
        ["curl", "-sS", "--max-time", "25", "-o", "-", "-w", "\n%{http_code}",
         "-A", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
         TEST_URL],
        capture_output=True, text=True, timeout=40)
    body = r.stdout or ""
    code = body.rsplit("\n", 1)[-1].strip()
    if "Error 1015" in body or "rate limited" in body.lower():
        return False, "noch gesperrt (1015)"
    if code in ("200", "403") and "cardmarket" in body.lower():
        # 403 mit Cardmarket-Inhalt = Bot-Pruefung, aber keine Ratensperre.
        return True, f"frei (HTTP {code})"
    return False, f"unklar (HTTP {code or '?'})"


def main() -> int:
    try:
        sperre_pruefen()
    except Gesperrt as e:
        print(f"{datetime.now():%d.%m. %H:%M} — Frist laeuft noch: {str(e)[:70]}")
        return 1

    frei, wie = einmal_anklopfen()
    print(f"{datetime.now():%d.%m. %H:%M} — Test: {wie}")

    if not frei:
        sperre_setzen(f"Nachtest {datetime.now():%H:%M}: {wie}", stunden=NAECHSTER_VERSUCH_H)
        naechster = datetime.now() + timedelta(hours=NAECHSTER_VERSUCH_H)
        print(f"  naechster Versuch: {naechster:%H:%M}")
        return 1

    sperre_aufheben()
    subprocess.run(["systemctl", "start", "cardmarket-browser"], timeout=60)
    print("  Sperre vorbei, Browser gestartet")
    try:
        from scrape_brightdata import send_telegram
        send_telegram(
            "✅ <b>Cardmarket wieder frei</b>\n"
            "Der Browser laeuft und steht auf der Parkseite.\n"
            f'<a href="{NOVNC}">Dort anmelden</a> — ein Klick auf den gelben Knopf, '
            "danach haelt die Sitzung auch ueber Neustarts."
        )
        print("  Telegram verschickt")
    except Exception as e:
        print(f"  Telegram fehlgeschlagen: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
