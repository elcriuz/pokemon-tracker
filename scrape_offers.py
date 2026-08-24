#!/usr/bin/env python3
"""Liest die eigenen Cardmarket-Angebote und schreibt sie nach listings.

Die Verkaeufer-Angebotsseite ist OEFFENTLICH — kein Login, kein Captcha-Risiko.
Geholt wird sie ueber denselben Bright-Data-Web-Unlocker wie die Preise
(Zone aus settings.brightdata_zone).

  python3 scrape_offers.py              # alle Kategorien
  python3 scrape_offers.py --dry-run    # nur parsen und anzeigen
"""
from __future__ import annotations

import argparse
import html as html_mod
import json
import logging
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "tracker.db"

BASE = "https://www.cardmarket.com"
# Cardmarket fuehrt je Spiel einen eigenen Shop unter eigener URL.
# Spiele ohne Angebote liefern eine leere Seite und werden uebersprungen.
GAMES = ["Pokemon", "Magic"]
CATEGORIES = ["Singles", "Sealed"]
PAGE_DELAY_S = 4.0          # hoeflich bleiben, es sind nur wenige Seiten
MAX_PAGES = 25              # Sicherheitsnetz gegen Endlosschleifen

log = logging.getLogger("offers")


# ---------------------------------------------------------------- Parsing

ROW_SPLIT_RE = re.compile(r'<div id="stockRow(\d+)"')
# Der Spielname steckt im Pfad (/de/Magic/Products/...) — deshalb nicht fest verdrahten.
PRODUCT_RE = re.compile(r'<a href="(/[a-z]{2}/\w+/Products/[^"]+)"[^>]*>([^<]+)</a>')
EXPANSION_RE = re.compile(r'href="/[a-z]{2}/\w+/Expansions/[^"]*"[^>]*?aria-label="([^"]+)"')
CONDITION_RE = re.compile(r'article-condition\s+condition-(\w+)')
# Preis und Menge stehen doppelt im HTML (mobile + desktop) -> immer nur der erste Treffer.
# Zwischen Betrag und € steht mal ein normales, mal ein geschuetztes Leerzeichen —
# darum [^<]* statt einer festen Trennerangabe.
PRICE_RE = re.compile(r'<span class="color-primary[^"]*">\s*([\d.]+,\d{2})[^<]*</span>')
COUNT_RE = re.compile(r'<span class="item-count[^"]*">\s*(\d+)\s*</span>')
COMMENT_RE = re.compile(r'fst-italic small">([^<]*)</span>')
TOTAL_RE = re.compile(r'<span class="total-count">(\d+)</span>')
PAGES_RE = re.compile(r'Seite\s+\d+\s+von\s+(\d+)')

# Der Sprach-Span traegt aria-label; Rarity/Expansion nutzen dasselbe Attribut,
# darum wird gegen eine feste Liste gematcht statt "irgendein aria-label".
LANGUAGES = {
    "Deutsch": "de", "Englisch": "en", "Französisch": "fr", "Spanisch": "es",
    "Italienisch": "it", "Chinesisch": "zh", "Japanisch": "ja", "Portugiesisch": "pt",
    "Russisch": "ru", "Koreanisch": "ko", "Traditionelles Chinesisch": "zh-t",
}
LANG_RE = re.compile(r'aria-label="(' + "|".join(map(re.escape, LANGUAGES)) + r')"')

CONDITION_MAP = {
    "mt": "MT", "nm": "NM", "ex": "EX", "gd": "GD",
    "lp": "LP", "pl": "PL", "po": "PO",
}


def parse_de_price(s: str) -> float | None:
    """'1.234,56' -> 1234.56"""
    try:
        return float(s.replace(".", "").replace(",", "."))
    except (ValueError, AttributeError):
        return None


def parse_offers(html: str, kind: str = "single", game: str = "Pokemon") -> list[dict]:
    """Zerlegt eine Angebotsseite in einzelne Angebote."""
    parts = ROW_SPLIT_RE.split(html)
    # parts = [prefix, id1, block1, id2, block2, ...]
    offers: list[dict] = []
    for i in range(1, len(parts) - 1, 2):
        article_id, block = parts[i], parts[i + 1]

        prod = PRODUCT_RE.search(block)
        if not prod:
            log.debug("Zeile %s ohne Produktlink — uebersprungen", article_id)
            continue

        price_m = PRICE_RE.search(block)
        count_m = COUNT_RE.search(block)
        cond_m = CONDITION_RE.search(block)
        lang_m = LANG_RE.search(block)
        exp_m = EXPANSION_RE.search(block)
        comment_m = COMMENT_RE.search(block)

        # Spiel aus dem Produktpfad lesen — verlaesslicher als der Aufruf-Parameter,
        # falls Cardmarket mal quer verlinkt.
        path_game = prod.group(1).split("/")[2] if prod.group(1).count("/") >= 3 else game

        offers.append({
            "cm_article_id": article_id,
            "game": path_game,
            "product_url": BASE + prod.group(1),
            # Cardmarket liefert &amp; / &quot; im Markup — sonst steht das roh in der UI.
            "product_name": html_mod.unescape(re.sub(r"\s+", " ", prod.group(2)).strip()),
            "expansion": html_mod.unescape(exp_m.group(1).strip()) if exp_m else "",
            "kind": kind,
            "condition": CONDITION_MAP.get(cond_m.group(1).lower(), "") if cond_m else "",
            "language": LANGUAGES.get(lang_m.group(1), "") if lang_m else "",
            "is_foil": 1 if 'aria-label="Foil"' in block or "Reverse Holo" in block else 0,
            "is_signed": 1 if 'aria-label="Signiert"' in block else 0,
            "is_playset": 1 if 'aria-label="Playset"' in block else 0,
            "price": parse_de_price(price_m.group(1)) if price_m else None,
            "quantity": int(count_m.group(1)) if count_m else 1,
            "comment": html_mod.unescape(comment_m.group(1).strip()) if comment_m else "",
        })
    return offers


# ---------------------------------------------------------------- Fetching

# Eine echte Cardmarket-Seite ist immer gross (Navigation, Footer, Skripte) — auch
# eine ohne ein einziges Angebot liegt bei ~100 KB. Alles darunter ist ein Aussetzer
# bei Bright Data, kein leerer Shop. Der Unterschied ist wichtig: "leer" wuerde
# bedeuten, dass alle Angebote verkauft sind.
MIN_REAL_PAGE = 20_000
FETCH_RETRIES = 3


def bd_fetch(url: str, api_key: str, zone: str, timeout: int = 120) -> str:
    payload = json.dumps({"zone": zone, "url": url, "format": "raw"}).encode()
    last = ""
    for attempt in range(1, FETCH_RETRIES + 1):
        req = urllib.request.Request(
            "https://api.brightdata.com/request",
            data=payload,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                last = r.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError:
            raise
        except Exception as e:
            log.warning("    Abruf %d/%d fehlgeschlagen: %s", attempt, FETCH_RETRIES, e)
            last = ""
        if len(last) >= MIN_REAL_PAGE:
            return last
        if attempt < FETCH_RETRIES:
            log.warning("    Antwort zu kurz (%d Bytes) — Versuch %d/%d",
                        len(last), attempt + 1, FETCH_RETRIES)
            time.sleep(3 * attempt)
    raise RuntimeError(f"nach {FETCH_RETRIES} Versuchen keine vollstaendige Seite "
                       f"(zuletzt {len(last)} Bytes)")


def looks_blocked(html: str) -> str | None:
    """Erkennt Challenge-/Fehlerseiten, damit wir nie halb geparste Daten schreiben."""
    head = html[:4000].lower()
    for marker in ("just a moment", "cloudflare", "attention required", "access denied"):
        if marker in head:
            return marker
    if "<title>" in head and "cardmarket" not in head:
        return "unerwarteter Seitentitel"
    return None


def fetch_category(username: str, game: str, category: str, api_key: str, zone: str) -> list[dict]:
    """Holt alle Seiten einer Kategorie eines Spiels."""
    kind = "sealed" if category.lower() == "sealed" else "single"
    label = f"{game}/{category}"
    all_offers: list[dict] = []
    page = 1
    total_pages = 1

    while page <= min(total_pages, MAX_PAGES):
        url = f"{BASE}/de/{game}/Users/{username}/Offers/{category}"
        if page > 1:
            url += f"?site={page}"

        log.info("  [%s] Seite %d/%s ...", label, page, total_pages)
        try:
            html = bd_fetch(url, api_key, zone)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                log.info("  [%s] gibt es nicht (404) — uebersprungen", label)
                return []
            raise

        # Erst wenn eine vollstaendige Seite ohne jede Angebotszeile zurueckkommt,
        # bietest du in dieser Kategorie wirklich nichts an.
        if page == 1 and "stockRow" not in html:
            log.info("  [%s] keine Angebote in dieser Kategorie", label)
            return []

        blocked = looks_blocked(html)
        if blocked:
            raise RuntimeError(f"{label} Seite {page}: geblockt ({blocked})")

        if page == 1:
            m = PAGES_RE.search(html)
            total_pages = int(m.group(1)) if m else 1
            t = TOTAL_RE.search(html)
            log.info("  [%s] %s Treffer auf %d Seiten",
                     label, t.group(1) if t else "?", total_pages)

        offers = parse_offers(html, kind, game)
        if not offers and page == 1:
            log.warning("  [%s] keine Angebote gefunden — Layout geaendert?", label)
        all_offers.extend(offers)

        page += 1
        if page <= total_pages:
            time.sleep(PAGE_DELAY_S)

    return all_offers


# ---------------------------------------------------------------- Speichern

def save_offers(db: sqlite3.Connection, offers: list[dict], scanned: set[tuple[str, str]]) -> dict:
    """Schreibt Angebote nach listings. Verschwundene werden deaktiviert, nicht geloescht.

    `scanned` enthaelt nur die (Spiel, Art)-Kombinationen, die auch wirklich
    erfolgreich abgerufen wurden — nur dort darf etwas deaktiviert werden.
    """
    now = datetime.now().isoformat(timespec="seconds")
    stats = {"neu": 0, "aktualisiert": 0, "preis_geaendert": 0, "verschwunden": 0}

    for o in offers:
        row = db.execute(
            "SELECT id, price FROM listings WHERE cm_article_id = ?", (o["cm_article_id"],)
        ).fetchone()

        if row is None:
            # card_id verknuepfen, wenn die Karte im Portfolio liegt
            card = db.execute(
                "SELECT id FROM cards WHERE url = ? OR url = ?",
                (o["product_url"], o["product_url"].replace("https://www.cardmarket.com", "")),
            ).fetchone()
            db.execute(
                """INSERT INTO listings
                   (card_id, cm_article_id, game, product_url, product_name, expansion, kind,
                    condition, language, is_foil, is_signed, is_playset, price, quantity,
                    comment, first_seen, last_seen, active)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
                (card[0] if card else None, o["cm_article_id"], o["game"], o["product_url"],
                 o["product_name"], o["expansion"], o["kind"], o["condition"], o["language"],
                 o["is_foil"], o["is_signed"], o["is_playset"], o["price"], o["quantity"],
                 o["comment"], now, now),
            )
            stats["neu"] += 1
        else:
            listing_id, old_price = row
            if old_price is not None and o["price"] is not None and abs(old_price - o["price"]) > 0.001:
                stats["preis_geaendert"] += 1
            db.execute(
                """UPDATE listings SET price = ?, quantity = ?, comment = ?, condition = ?,
                   language = ?, product_name = ?, expansion = ?, last_seen = ?,
                   active = 1, gone_at = NULL WHERE id = ?""",
                (o["price"], o["quantity"], o["comment"], o["condition"], o["language"],
                 o["product_name"], o["expansion"], now, listing_id),
            )
            stats["aktualisiert"] += 1

    # Nur (Spiel, Art)-Kombinationen anfassen, die auch wirklich abgerufen wurden.
    # Sonst wuerde ein fehlgeschlagener Magic-Abruf saemtliche Magic-Angebote als
    # verkauft markieren — oder ein reiner Pokemon-Lauf den ganzen Magic-Shop loeschen.
    ids = [o["cm_article_id"] for o in offers] or ["-none-"]
    id_placeholders = ",".join("?" * len(ids))
    for game, kind in scanned:
        cur = db.execute(
            f"""UPDATE listings SET active = 0, gone_at = ?
                WHERE active = 1 AND game = ? AND kind = ?
                  AND cm_article_id NOT IN ({id_placeholders})""",
            [now, game, kind, *ids],
        )
        stats["verschwunden"] += cur.rowcount

    db.commit()
    return stats


def snapshot_listings(db: sqlite3.Connection) -> int:
    """Haelt den heutigen Eigenpreis samt Marktumfeld fest (Basis fuer Trendsignale)."""
    now = datetime.now().isoformat(timespec="seconds")
    rows = db.execute(
        """SELECT l.id, l.price, l.card_id FROM listings l WHERE l.active = 1"""
    ).fetchall()

    n = 0
    for listing_id, price, card_id in rows:
        market = (None, None, None)
        if card_id:
            m = db.execute(
                """SELECT trend, avg7, avg30 FROM prices
                   WHERE card_id = ? ORDER BY scraped_at DESC LIMIT 1""",
                (card_id,),
            ).fetchone()
            if m:
                market = m
        db.execute(
            """INSERT OR IGNORE INTO listing_snapshots
               (listing_id, captured_at, my_price, market_trend, market_avg7, market_avg30)
               VALUES (?,?,?,?,?,?)""",
            (listing_id, now, price, market[0], market[1], market[2]),
        )
        n += 1
    db.commit()
    return n


# ---------------------------------------------------------------- Main

def get_setting(db: sqlite3.Connection, key: str, default: str = "") -> str:
    row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row and row[0] else default


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="nur parsen, nichts speichern")
    ap.add_argument("--user", default=None, help="Cardmarket-Benutzername (sonst aus settings)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    db = sqlite3.connect(DB_PATH)
    username = args.user or get_setting(db, "cardmarket_user")
    api_key = get_setting(db, "brightdata_api_key")
    zone = get_setting(db, "brightdata_zone", "cardmarket")

    if not username:
        log.error("Kein Cardmarket-Benutzer gesetzt (settings.cardmarket_user)")
        return 2
    if not api_key:
        log.error("Kein Bright-Data-Key in settings")
        return 2

    games = [g.strip() for g in get_setting(db, "cardmarket_games", ",".join(GAMES)).split(",") if g.strip()]
    log.info("Angebote von @%s (%s)", username, ", ".join(games))

    all_offers: list[dict] = []
    scanned: set[tuple[str, str]] = set()
    failed: list[str] = []

    for game in games:
        for cat in CATEGORIES:
            try:
                offers = fetch_category(username, game, cat, api_key, zone)
                if offers:
                    all_offers.extend(offers)
                    scanned.add((game, "sealed" if cat.lower() == "sealed" else "single"))
            except Exception as e:
                log.error("  [%s/%s] FEHLER: %s", game, cat, e)
                failed.append(f"{game}/{cat}")

    by_game: dict[str, int] = {}
    for o in all_offers:
        by_game[o["game"]] = by_game.get(o["game"], 0) + 1
    log.info("Gefunden: %d Angebote (%s)", len(all_offers),
             ", ".join(f"{g} {n}" for g, n in sorted(by_game.items())) or "keine")

    if args.dry_run:
        for o in all_offers[:40]:
            print(f"  {o['game'][:3]:<3} {o['price']:>8.2f} €  {o['condition']:<3} "
                  f"{o['language']:<3} {'FOIL' if o['is_foil'] else '    '} "
                  f"x{o['quantity']:<3} {o['product_name'][:44]}")
        if len(all_offers) > 40:
            print(f"  ... +{len(all_offers) - 40} weitere")
        return 0

    if not all_offers and failed:
        log.error("Nichts geladen — DB bleibt unveraendert")
        return 1

    stats = save_offers(db, all_offers, scanned)
    n_snap = snapshot_listings(db)
    log.info("neu %d · aktualisiert %d · Preis geaendert %d · verschwunden %d · Snapshots %d",
             stats["neu"], stats["aktualisiert"], stats["preis_geaendert"],
             stats["verschwunden"], n_snap)
    return 0


if __name__ == "__main__":
    sys.exit(main())
