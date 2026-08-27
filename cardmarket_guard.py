#!/usr/bin/env python3
"""Notbremse fuer den eingeloggten Cardmarket-Zugriff.

Hintergrund: Am 26.08.2026 hat eine Entwicklungssitzung mit Dutzenden
Seitenaufrufen in wenigen Minuten eine Cloudflare-Sperre ausgeloest (Error
1015). Schlimmer als die Sperre selbst war, dass der Code munter weiter
angefragt hat — jeder Zugriff waehrend der Sperre setzt die Frist neu, aus
Minuten wurden zehn Stunden.

Zwei Regeln daraus:
  1. Beim ersten 1015 sofort aufhoeren.
  2. Danach eine Weile gar nicht erst anklopfen.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPERRE = ROOT / "data" / "cardmarket_sperre.json"

# Wie lange nach einer Sperre gar nicht angefragt wird. Lieber zu lang als zu
# kurz: ein verfrueher Versuch verlaengert die Sperre.
PAUSE_STUNDEN = 6
# Mindestabstand zwischen zwei eingeloggten Aufrufen.
ABSTAND_S = 2.5

log = logging.getLogger("guard")

CF_1015 = ("error 1015", "rate limited", "banned you temporarily")
CF_CHALLENGE = ("just a moment", "cloudflare", "security verification",
                "attention required")
NOVNC = "http://192.168.1.91:6080/vnc.html"


class Gesperrt(RuntimeError):
    """Cardmarket blockt — nicht weiter anfragen."""


class Challenge(RuntimeError):
    """Bot-Pruefung, braucht einen menschlichen Klick."""


class NichtAngemeldet(RuntimeError):
    pass


def sperre_setzen(grund: str, stunden: float = PAUSE_STUNDEN) -> None:
    SPERRE.parent.mkdir(parents=True, exist_ok=True)
    bis = datetime.now() + timedelta(hours=stunden)
    SPERRE.write_text(json.dumps({
        "seit": datetime.now().isoformat(timespec="seconds"),
        "bis": bis.isoformat(timespec="seconds"),
        "grund": grund,
    }))
    log.error("Zugriff bis %s ausgesetzt (%s)", bis.strftime("%d.%m. %H:%M"), grund)


def sperre_pruefen() -> None:
    """Vor dem ersten Aufruf. Wirft, wenn wir noch pausieren sollen."""
    if not SPERRE.exists():
        return
    try:
        d = json.loads(SPERRE.read_text())
        bis = datetime.fromisoformat(d["bis"])
    except Exception:
        SPERRE.unlink(missing_ok=True)
        return
    if datetime.now() < bis:
        rest = bis - datetime.now()
        raise Gesperrt(
            f"Cardmarket hat uns gesperrt ({d.get('grund', '?')}). Noch "
            f"{int(rest.total_seconds() // 60)} Minuten Pause — jeder Versuch "
            f"davor verlaengert die Sperre.")
    SPERRE.unlink(missing_ok=True)
    log.info("Sperrfrist abgelaufen, Zugriff wieder frei")


def sperre_aufheben() -> None:
    """Nach einem erfolgreichen Zugriff — dann war die Sorge unbegruendet."""
    SPERRE.unlink(missing_ok=True)


def seite_pruefen(page) -> None:
    """Nach jedem Aufruf. Unterscheidet die drei Zustaende, die im HTML alle
    wie eine leere Seite aussehen."""
    titel = (page.title() or "").lower()
    body = page.inner_text("body")[:400].lower()

    if any(m in body for m in CF_1015):
        sperre_setzen("Error 1015 — zu viele Zugriffe")
        raise Gesperrt(
            "Cardmarket hat uns wegen zu vieler Zugriffe gesperrt (Error 1015). "
            f"Weitere Versuche verlaengern sie — Pause bis in {PAUSE_STUNDEN} Stunden.")

    if any(m in titel or m in body for m in CF_CHALLENGE):
        raise Challenge(f"Bot-Pruefung — einmal unter {NOVNC} bestaetigen")

    if "anmeldung" in titel or "Account/Login" in page.url:
        raise NichtAngemeldet(f"Nicht angemeldet — bitte unter {NOVNC} einloggen")


class Takt:
    """Haelt den Mindestabstand zwischen Aufrufen ein.

    Bewusst seriell statt parallel: acht gleichzeitige Anfragen sind genau das
    Muster, auf das die Ratenbegrenzung anspringt.
    """

    def __init__(self, abstand: float = ABSTAND_S):
        self.abstand = abstand
        self._zuletzt = 0.0

    def warten(self) -> None:
        rest = self.abstand - (time.monotonic() - self._zuletzt)
        if rest > 0:
            time.sleep(rest)
        self._zuletzt = time.monotonic()
