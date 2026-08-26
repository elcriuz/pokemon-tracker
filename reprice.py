#!/usr/bin/env python3
"""Aendert den Preis eines eigenen Cardmarket-Angebots.

Cardmarket verschleiert seine Aktions-URLs (`jcp('%23%11%10...')`), deshalb wird
nicht der Endpunkt aufgerufen, sondern die Oberflaeche bedient: Bearbeiten-Dialog
oeffnen, Preisfeld setzen, absenden. Das ist langsamer, bleibt aber gueltig, wenn
sich die Verschleierung aendert.

Laeuft ueber die angemeldete Sitzung des `cardmarket-browser` (CDP).

  python3 reprice.py --article 2137355061 --price 0.25
  python3 reprice.py --article 2137355061 --price 0.25 --dry-run
"""
from __future__ import annotations

import argparse
import logging
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
DB_PATH = ROOT / "data" / "tracker.db"

CDP_URL = "http://localhost:9222"
STOCK_URL = "https://www.cardmarket.com/de/{game}/Stock/Offers/Singles"
MAX_PAGES = 15

log = logging.getLogger("reprice")


class RepriceError(RuntimeError):
    pass


class ChallengeError(RepriceError):
    """Cloudflare will einen menschlichen Klick sehen."""


CF_MARKERS = ("just a moment", "cloudflare", "attention required", "security verification")
NOVNC = "http://192.168.1.91:6080/vnc.html"


def check_blocked(page) -> None:
    """Eine Bot-Pruefung sieht wie eine leere Seite aus — ohne diesen Test
    meldet das Skript faelschlich 'Angebot nicht gefunden'."""
    head = (page.title() or "").lower() + " " + page.inner_text("body")[:300].lower()
    if any(m in head for m in CF_MARKERS):
        raise ChallengeError(
            f"Cloudflare verlangt eine Bestaetigung. Bitte einmal unter {NOVNC} "
            f"im Browser klicken, danach laeuft es wieder."
        )


def find_row_page(page, game: str, article_id: str) -> int:
    """Sucht die Bestandsseite, auf der das Angebot steht.

    Die Gesamtseitenzahl wird auf Seite 1 gelesen, statt blind bis MAX_PAGES zu
    blaettern — zu viele Aufrufe hintereinander loesen die Bot-Pruefung aus.
    """
    total = None
    for site in range(1, MAX_PAGES + 1):
        url = STOCK_URL.format(game=game) + (f"?site={site}" if site > 1 else "")
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(1800)
        check_blocked(page)
        if "Account/Login" in page.url:
            raise RepriceError("Nicht angemeldet — bitte ueber noVNC einloggen")

        if f"stockRow{article_id}" in page.content():
            return site

        if total is None:
            m = re.search(r"Seite\s+\d+\s+von\s+(\d+)",
                          re.sub(r"\s+", " ", page.inner_text("body")))
            total = int(m.group(1)) if m else 1
            if total == 1 and "stockRow" not in page.content():
                raise RepriceError(
                    f"Bestandsliste fuer {game} ist leer — stimmt die Adresse "
                    f"{STOCK_URL.format(game=game)}?")
        if site >= total:
            break
    raise RepriceError(f"Angebot {article_id} im Bestand von {game} nicht gefunden")


def read_price(page, article_id: str) -> float | None:
    row = page.locator(f"#stockRow{article_id}")
    if not row.count():
        return None
    m = re.search(r"([\d.]+,\d{2})\s*€", row.inner_text())
    return float(m.group(1).replace(".", "").replace(",", ".")) if m else None


def set_price(page, article_id: str, new_price: float, dry_run: bool = False) -> dict:
    """Oeffnet den Dialog, setzt den Preis, sendet ab und prueft das Ergebnis."""
    row = page.locator(f"#stockRow{article_id}")
    if not row.count():
        raise RepriceError(f"Zeile {article_id} nicht auf dieser Seite")

    before = read_price(page, article_id)

    # Der Bearbeiten-Knopf ist ein <a class="btn btn-secondary">, kein <button>.
    row.locator("a.btn-secondary").click()
    page.wait_for_timeout(2500)
    check_blocked(page)

    modal = page.locator("#modal")
    if not modal.is_visible():
        raise RepriceError("Bearbeiten-Dialog ist nicht aufgegangen")

    field = modal.locator("input[name='price']")
    if not field.count():
        raise RepriceError("Kein Preisfeld im Dialog — Layout geaendert?")

    # Gegenprobe: bearbeiten wir wirklich den gemeinten Artikel?
    id_field = modal.locator("input[name='idArticle']")
    if id_field.count() and id_field.input_value() != str(article_id):
        raise RepriceError(f"Dialog gehoert zu Artikel {id_field.input_value()}, "
                           f"nicht zu {article_id}")

    old_field = field.input_value()
    formatted = f"{new_price:.2f}"

    if dry_run:
        page.keyboard.press("Escape")
        page.wait_for_timeout(600)
        return {"ok": True, "dry_run": True, "before": before,
                "field_before": old_field, "would_set": formatted}

    field.fill(formatted)
    modal.get_by_role("button", name=re.compile("ändern", re.I)).click()
    page.wait_for_timeout(4000)

    # Ergebnis an der Zeile pruefen, nicht am Dialog — nur was in der Liste steht,
    # ist wirklich gespeichert.
    page.reload(wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2000)
    after = read_price(page, article_id)
    ok = after is not None and abs(after - new_price) < 0.005
    return {"ok": ok, "before": before, "after": after, "target": new_price}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--article", required=True, help="Cardmarket-Artikel-ID")
    ap.add_argument("--price", required=True, type=float)
    ap.add_argument("--game", default=None, help="sonst aus der DB")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s",
                        datefmt="%H:%M:%S")

    if args.price <= 0:
        log.error("Preis muss groesser als 0 sein")
        return 2

    db = sqlite3.connect(DB_PATH)
    row = db.execute("""SELECT game, product_name, price FROM listings
                        WHERE cm_article_id = ? AND active = 1""", (args.article,)).fetchone()
    game = args.game or (row[0] if row else "Pokemon")
    name = row[1] if row else args.article
    known = row[2] if row else None

    # Schutz vor Zahlendrehern: eine Verzehnfachung oder Zehntelung ist fast immer
    # ein Fehler, kein Repricing.
    if known and (args.price > known * 5 or args.price < known / 5):
        log.error("Sprung von %.2f € auf %.2f € sieht nach Zahlendreher aus — abgebrochen",
                  known, args.price)
        log.error("Wenn das gewollt ist: erst in kleineren Schritten.")
        return 2

    from patchright.sync_api import sync_playwright
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP_URL)
        except Exception as e:
            log.error("Kein angemeldeter Browser (%s): %s", CDP_URL, e)
            return 2
        page = browser.contexts[0].new_page()
        try:
            site = find_row_page(page, game, args.article)
            log.info("%s — gefunden auf Bestandsseite %d", name, site)
            res = set_price(page, args.article, args.price, args.dry_run)
            if res.get("dry_run"):
                log.info("Testlauf: Feld steht auf %s, wuerde auf %s gesetzt",
                         res["field_before"], res["would_set"])
            elif res["ok"]:
                log.info("Preis geaendert: %.2f € → %.2f €", res["before"], res["after"])
                db.execute("""UPDATE listings SET price = ? WHERE cm_article_id = ?""",
                           (res["after"], args.article))
                db.commit()
            else:
                log.error("Nicht uebernommen — Zeile zeigt %s, erwartet %.2f",
                          res["after"], res["target"])
                return 1
        except ChallengeError as e:
            log.error("%s", e)
            try:
                from scrape_brightdata import send_telegram
                send_telegram(f"\u26a0\ufe0f <b>Cardmarket: Bot-Pruefung</b>\n"
                              f"Preisaenderung gestoppt.\n"
                              f'<a href="{NOVNC}">Im Browser bestaetigen</a>')
            except Exception:
                pass
            return 3
        except RepriceError as e:
            log.error("%s", e)
            return 1
        finally:
            page.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
