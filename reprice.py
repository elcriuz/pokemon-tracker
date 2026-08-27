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


from cardmarket_guard import (Gesperrt, Challenge, NichtAngemeldet, Takt, NOVNC,
                              seite_pruefen, sperre_pruefen, sperre_aufheben)

takt = Takt()


class ChallengeError(RepriceError):
    """Beibehalten fuer bestehende Aufrufer."""


def check_blocked(page) -> None:
    """Duenne Huelle um die gemeinsame Pruefung, damit die Fehlertypen dieses
    Moduls erhalten bleiben."""
    try:
        seite_pruefen(page)
    except Gesperrt as e:
        raise RepriceError(str(e)) from None
    except Challenge as e:
        raise ChallengeError(str(e)) from None
    except NichtAngemeldet as e:
        raise RepriceError(str(e)) from None


def find_row_page(page, game: str, article_id: str) -> int:
    """Sucht die Bestandsseite, auf der das Angebot steht.

    Die Gesamtseitenzahl wird auf Seite 1 gelesen, statt blind bis MAX_PAGES zu
    blaettern — zu viele Aufrufe hintereinander loesen die Bot-Pruefung aus.
    """
    total = None
    for site in range(1, MAX_PAGES + 1):
        url = STOCK_URL.format(game=game) + (f"?site={site}" if site > 1 else "")
        takt.warten()
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


def run_batch(page, db: sqlite3.Connection, dry_run: bool = False) -> dict:
    """Arbeitet die Warteschlange ab und laedt dabei jede Bestandsseite nur einmal.

    Einzeln ausgefuehrt kostet jede Karte einen kompletten Durchlauf durch den
    Bestand — bei zehn Karten also bis zu vierzig Seitenaufrufe. Gebuendelt sind
    es so viele, wie der Bestand Seiten hat.
    """
    rows = db.execute("""
        SELECT q.id, q.listing_id, q.target_price, l.cm_article_id, l.game,
               l.product_name, l.price
        FROM reprice_queue q JOIN listings l ON l.id = q.listing_id
        WHERE q.done_at IS NULL AND l.active = 1
        ORDER BY l.game, l.product_name
    """).fetchall()
    if not rows:
        return {"total": 0, "ok": 0, "failed": 0, "blocked": False}

    by_game: dict[str, list] = {}
    for r in rows:
        by_game.setdefault(r[4] or "Pokemon", []).append(r)

    stats = {"total": len(rows), "ok": 0, "failed": 0, "blocked": False, "pages": 0}
    now = datetime.now().isoformat(timespec="seconds")

    for game, items in by_game.items():
        pending = {str(r[3]): r for r in items}
        log.info("%s: %d Aenderungen vorgemerkt", game, len(pending))
        total_pages = None
        site = 1

        while pending and site <= MAX_PAGES:
            url = STOCK_URL.format(game=game) + (f"?site={site}" if site > 1 else "")
            takt.warten()
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(1800)
            try:
                check_blocked(page)
            except ChallengeError as e:
                # Warteschlange bleibt stehen — nach dem Klick einfach neu starten.
                log.error("%s", e)
                stats["blocked"] = True
                return stats
            stats["pages"] += 1

            if total_pages is None:
                m = re.search(r"Seite\s+\d+\s+von\s+(\d+)",
                              re.sub(r"\s+", " ", page.inner_text("body")))
                total_pages = int(m.group(1)) if m else 1

            html = page.content()
            here = [a for a in list(pending) if f"stockRow{a}" in html]
            for article in here:
                qid, listing_id, target, _a, _g, name, old = pending.pop(article)
                try:
                    res = set_price(page, article, target, dry_run)
                    if dry_run:
                        log.info("  [Test] %-32s %.2f € → %.2f €", name[:32], old, target)
                        stats["ok"] += 1
                        continue
                    if res["ok"]:
                        log.info("  %-32s %.2f € → %.2f €", name[:32], res["before"], res["after"])
                        db.execute("UPDATE listings SET price = ? WHERE id = ?",
                                   (res["after"], listing_id))
                        db.execute("""UPDATE reprice_queue SET done_at = ?, old_price = ?,
                                      error = NULL WHERE id = ?""", (now, res["before"], qid))
                        db.execute("""UPDATE signals SET applied_at = ?
                                      WHERE id = (SELECT signal_id FROM reprice_queue WHERE id = ?)""",
                                   (now, qid))
                        stats["ok"] += 1
                    else:
                        raise RepriceError(f"Zeile zeigt {res['after']}, erwartet {target}")
                except ChallengeError as e:
                    log.error("%s", e)
                    stats["blocked"] = True
                    db.commit()
                    return stats
                except RepriceError as e:
                    log.error("  %-32s FEHLER: %s", name[:32], e)
                    db.execute("UPDATE reprice_queue SET error = ? WHERE id = ?", (str(e), qid))
                    stats["failed"] += 1
                db.commit()

                # Nach dem Speichern steht die Seite neu — Rest dieser Seite erneut suchen.
                html = page.content()

            if total_pages and site >= total_pages:
                break
            site += 1

        for rest in pending.values():
            log.warning("  %-32s nicht im Bestand gefunden", rest[5][:32])
            db.execute("UPDATE reprice_queue SET error = ? WHERE id = ?",
                       ("Im Bestand nicht gefunden", rest[0]))
            stats["failed"] += 1
        db.commit()

    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", action="store_true", help="vorgemerkte Aenderungen abarbeiten")
    ap.add_argument("--article", help="Cardmarket-Artikel-ID (Einzelaenderung)")
    ap.add_argument("--price", type=float)
    ap.add_argument("--game", default=None, help="sonst aus der DB")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s",
                        datefmt="%H:%M:%S")

    if not args.batch and (not args.article or args.price is None):
        log.error("Entweder --batch oder --article mit --price angeben")
        return 2
    if args.price is not None and args.price <= 0:
        log.error("Preis muss groesser als 0 sein")
        return 2

    if args.batch:
        return run_batch_main(args.dry_run)

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
            sperre_pruefen()
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


def run_batch_main(dry_run: bool) -> int:
    db = sqlite3.connect(DB_PATH)
    from patchright.sync_api import sync_playwright
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP_URL)
        except Exception as e:
            log.error("Kein angemeldeter Browser (%s): %s", CDP_URL, e)
            return 2
        page = browser.contexts[0].new_page()
        try:
            sperre_pruefen()
            st = run_batch(page, db, dry_run)
        except Gesperrt as e:
            log.error("%s", e)
            page.close()
            return 3
        finally:
            page.close()

    if st["total"] == 0:
        log.info("Nichts vorgemerkt")
        return 0
    log.info("Fertig: %d von %d geaendert, %d Fehler, %d Seitenaufrufe",
             st["ok"], st["total"], st["failed"], st.get("pages", 0))
    if not st["blocked"]:
        sperre_aufheben()
    if st["blocked"]:
        log.error("Wegen Bot-Pruefung abgebrochen — Rest bleibt vorgemerkt")
        try:
            from scrape_brightdata import send_telegram
            send_telegram(f"\u26a0\ufe0f <b>Cardmarket: Bot-Pruefung</b>\n"
                          f"{st['ok']} Preise geaendert, Rest wartet.\n"
                          f'<a href="{NOVNC}">Bestaetigen</a>, danach erneut ausfuehren.')
        except Exception:
            pass
        return 3
    return 0 if st["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
