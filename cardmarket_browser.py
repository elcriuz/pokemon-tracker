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
import json
import logging
import os
import subprocess
import time
from pathlib import Path

from patchright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
PROFIL = ROOT / "data" / "patchright-profile"
DIENST = "cardmarket-browser"

# Cardmarkets Anmeldung haengt an PHPSESSID — einem Sitzungs-Cookie ohne
# Ablaufdatum. Chrome wirft solche Cookies beim Beenden weg. Da jeder Lauf den
# Browser stoppt und neu startet, war das Konto danach jedes Mal abgemeldet
# (seit dem Umbau am 03.09. bei jedem einzelnen Verkaufslauf).
# Deshalb: vor dem Schliessen sichern, nach dem Start zurueckspielen.
SITZUNG = ROOT / "data" / "cm_session.json"
# Nur ein Lauf darf gleichzeitig auf das Profil zugreifen. Ueberschneiden sich
# zwei (drei Cron-Zeiten plus Handbetrieb), stolpern sie ueber dieselbe
# Profilsperre — der zweite stirbt mit TargetClosedError, und weil er abstuerzt,
# schreibt er keine Sperrnotiz. Genau so ist am 08.09. eine 1015 unbemerkt
# geblieben.
LOCK = ROOT / "data" / "cm_browser.lock"

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


# Eine gesicherte Sitzung darf nur zurueckgespielt werden, solange sie
# plausibel noch gilt. Am 08.09. lag eine 2,5 Stunden alte Datei bereit und hat
# eine frische Anmeldung ueberschrieben — danach war der Lauf abgemeldet.
SITZUNG_MAX_ALTER_S = 6 * 3600


def _angemeldet(cookies) -> bool:
    """idUser setzt Cardmarket nur fuer angemeldete Besucher.

    PHPSESSID allein sagt nichts: das bekommt auch, wer nur vorbeisurft. Genau
    so kam am 08.09. eine abgemeldete Sitzung in die Datei.
    """
    return any(c.get("name") == "idUser" and c.get("value")
               for c in cookies if "cardmarket" in c.get("domain", ""))


def _sitzung_sichern(context) -> int:
    """Nur die fluechtigen Cookies — persistente schreibt Chrome selbst weg."""
    try:
        alle = context.cookies()
        if not _angemeldet(alle):
            return 0
        fluechtig = [c for c in alle
                     if "cardmarket" in c.get("domain", "") and not c.get("expires", -1) > 0]
        if fluechtig:
            SITZUNG.write_text(json.dumps(fluechtig))
            SITZUNG.chmod(0o600)
        return len(fluechtig)
    except Exception as e:
        log.warning("Sitzung nicht gesichert: %s", e)
        return 0


def _sitzung_zurueckspielen(context) -> int:
    if not SITZUNG.exists():
        return 0
    alter = time.time() - SITZUNG.stat().st_mtime
    if alter > SITZUNG_MAX_ALTER_S:
        log.info("Gesicherte Sitzung ist %.1f h alt — nicht zurueckgespielt",
                 alter / 3600)
        return 0
    try:
        cookies = json.loads(SITZUNG.read_text())
        # Playwright verlangt entweder url oder domain+path.
        context.add_cookies([{k: c[k] for k in
                              ("name", "value", "domain", "path", "secure", "httpOnly", "sameSite")
                              if k in c} for c in cookies])
        return len(cookies)
    except Exception as e:
        log.warning("Sitzung nicht zurueckgespielt: %s", e)
        return 0


class BereitsAktiv(RuntimeError):
    """Ein anderer Lauf benutzt den Browser gerade."""


@contextlib.contextmanager
def _exklusiv():
    import fcntl
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    f = LOCK.open("w")
    try:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BereitsAktiv(
                "Ein anderer Cardmarket-Lauf ist noch aktiv — dieser Lauf wird "
                "uebersprungen, statt sich mit ihm um das Profil zu streiten.")
        f.write(str(os.getpid()))
        f.flush()
        yield
    finally:
        with contextlib.suppress(Exception):
            fcntl.flock(f, fcntl.LOCK_UN)
            f.close()


@contextlib.contextmanager
def eigener_browser(start_url: str | None = None):
    """Liefert (context, page). Stoppt den noVNC-Browser nur, wenn er lief,
    und startet ihn dann hinterher wieder — auch bei Fehlern."""
    with _exklusiv():
        yield from _browser_intern(start_url)


def _browser_intern(start_url: str | None):
    lief = _dienst_laeuft()
    if lief:
        _systemctl("stop")
        # Chrome gibt das Profil nicht sofort frei.
        for _ in range(20):
            if not (PROFIL / "SingletonLock").exists():
                break
            time.sleep(0.5)

    PROFIL.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(Exception):
        from open_browser import sitzungswiederherstellung_aus
        sitzungswiederherstellung_aus(PROFIL)
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
            n = _sitzung_zurueckspielen(context)
            if n:
                log.info("%d Sitzungs-Cookies zurueckgespielt", n)
            page = context.pages[0] if context.pages else context.new_page()
            if start_url:
                page.goto(start_url, wait_until="domcontentloaded", timeout=60000)
            yield context, page
        finally:
            with contextlib.suppress(Exception):
                _sitzung_sichern(context)
            with contextlib.suppress(Exception):
                context.close()
            if lief:
                _systemctl("start")
