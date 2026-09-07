#!/usr/bin/env python3
"""Nach Ablauf einer Cardmarket-Sperre: Browser hochfahren und Bescheid geben.

Laeuft als einmaliger Cron-Eintrag. Prueft erst, ob die Frist wirklich vorbei
ist — ein verfruehter Start wuerde die Sperre nur verlaengern.
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from cardmarket_guard import SPERRE, Gesperrt, sperre_pruefen

NOVNC = "http://192.168.1.91:6080/vnc.html"


def main() -> int:
    try:
        sperre_pruefen()
    except Gesperrt as e:
        print(f"{datetime.now():%d.%m. %H:%M} — Frist laeuft noch: {e}")
        return 1

    subprocess.run(["systemctl", "start", "cardmarket-browser"], timeout=60)
    print(f"{datetime.now():%d.%m. %H:%M} — Sperre abgelaufen, Browser gestartet")

    try:
        from scrape_brightdata import send_telegram
        send_telegram(
            "✅ <b>Cardmarket: Sperre abgelaufen</b>\n"
            "Der Browser laeuft wieder und steht auf der Anmeldeseite.\n"
            f'Bitte einmal <a href="{NOVNC}">anmelden</a> — danach haelt die '
            "Sitzung auch ueber Neustarts."
        )
        print("Telegram verschickt")
    except Exception as e:
        print(f"Telegram fehlgeschlagen: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
