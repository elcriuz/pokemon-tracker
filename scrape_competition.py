#!/usr/bin/env python3
"""Ermittelt zu jedem eigenen Angebot die Wettbewerbsposition auf Cardmarket.

Der Kern ist der Filter: Eine Produktseite ohne Filter zeigt die 50 guenstigsten
Angebote ueber ALLE Sprachen und Zustaende. Bei einer Karte mit 300 Angeboten waren
das 50x italienisch — ein Vergleich dagegen ist wertlos, wenn man selbst eine
deutsche NM-Karte anbietet. Erst mit ?language=&minCondition= wird der Rang
aussagekraeftig.

  python3 scrape_competition.py --dry-run
  python3 scrape_competition.py --limit 20
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "tracker.db"

# Ein Abruf dauert ueber Bright Data rund eine Minute. Sequenziell waeren 121
# Angebote gut zwei Stunden — deshalb parallel. Die DB bleibt einthreadig, es
# werden nur die Seiten nebenlaeufig geholt.
MAX_PARALLEL = 8
SHOWN_LIMIT = 50          # so viele Angebote zeigt eine Produktseite maximal

log = logging.getLogger("competition")

# Sprach-IDs von Cardmarket (?language=). de=3 verifiziert am 24.08.2026.
LANGUAGE_IDS = {
    "en": 1, "fr": 2, "de": 3, "es": 4, "it": 5,
    "zh": 6, "ja": 7, "pt": 8, "ru": 9, "ko": 10, "zh-t": 11,
}
# Zustands-IDs (?minCondition=). Semantik ist "mindestens so gut" — genau das,
# was ein Kaeufer sieht, der NM sucht: MT-Angebote konkurrieren mit.
CONDITION_IDS = {"MT": 1, "NM": 2, "EX": 3, "GD": 4, "LP": 5, "PL": 6, "PO": 7}

PRICE_RE = re.compile(r'<span class="color-primary[^"]*fw-bold[^"]*">\s*([\d.,]+)\s*€\s*</span>')
COND_RE = re.compile(r'article-condition\s+condition-(\w+)')
SELLER_RE = re.compile(r'/Users/([^/"?]+)"')
COMMENT_RE = re.compile(r'fst-italic small">([^<]+)</span>')
ROW_SPLIT_RE = re.compile(r'<div id="articleRow\d+"')

# Die aufwendig gepflegten Filter des Preis-Scrapers mitbenutzen statt neu bauen:
# "nur Huelle", "ohne Karte", graded-Kommentare und UK-Einfuhraufschlag.
try:
    from scrape_brightdata import (BAD_LISTING_RE, _apply_uk_uplift,
                                   _comment_is_graded, extract_prices)
except Exception:  # pragma: no cover - Fallback, falls sich das Modul aendert
    BAD_LISTING_RE = None
    _comment_is_graded = lambda c: False
    _apply_uk_uplift = lambda p, b: (p, False)
    extract_prices = lambda h: {}


def parse_de_price(s: str) -> float | None:
    try:
        return float(s.replace(".", "").replace(",", "."))
    except (ValueError, AttributeError):
        return None


def parse_competitors(html: str) -> list[dict]:
    """Liest alle Angebote einer (gefilterten) Produktseite."""
    out = []
    for block in ROW_SPLIT_RE.split(html)[1:]:
        pm = PRICE_RE.search(block)
        if not pm:
            continue
        price = parse_de_price(pm.group(1))
        if price is None:
            continue

        cm = COMMENT_RE.search(block)
        comment = cm.group(1).strip() if cm else ""
        # Angebote, die gar nicht die Karte verkaufen, wuerden den Rang verfaelschen.
        if comment and BAD_LISTING_RE is not None and BAD_LISTING_RE.search(comment):
            continue
        if comment and _comment_is_graded(comment):
            continue

        price, _ = _apply_uk_uplift(price, block)
        sm = SELLER_RE.search(block)
        cond = COND_RE.search(block)
        out.append({
            "price": price,
            "seller": sm.group(1) if sm else "",
            "condition": cond.group(1).upper() if cond else "",
        })
    return out


def build_url(product_url: str, condition: str, language: str) -> str:
    params = []
    if language in LANGUAGE_IDS:
        params.append(f"language={LANGUAGE_IDS[language]}")
    if condition in CONDITION_IDS:
        params.append(f"minCondition={CONDITION_IDS[condition]}")
    return product_url + ("?" + "&".join(params) if params else "")


# Bright Data liefert gelegentlich eine leere Antwort. Eine echte Produktseite
# liegt bei ueber 100 KB — alles darunter ist ein Aussetzer und wird wiederholt.
MIN_REAL_PAGE = 20_000
FETCH_RETRIES = 3


def bd_fetch(url: str, api_key: str, zone: str, timeout: int = 150) -> str:
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
        except Exception:
            last = ""
        if len(last) >= MIN_REAL_PAGE:
            return last
        if attempt < FETCH_RETRIES:
            time.sleep(3 * attempt)
    raise RuntimeError(f"unvollstaendige Seite nach {FETCH_RETRIES} Versuchen "
                       f"({len(last)} Bytes)")


def rank_for(my_price: float, competitors: list[dict], me: str,
             my_condition: str = "") -> dict:
    """Zwei Sichten auf denselben Abruf.

    rank/best_price = Kaeufersicht: Wer "EX oder besser" sucht, sieht auch alle
    NM- und MT-Angebote. Dafuer ist der Rang das richtige Mass.

    best_same/competitors_same = Preisfindung: Nur Angebote im GLEICHEN Zustand.
    Ohne diese Trennung wirkt eine gespielte Karte automatisch "zu guenstig",
    weil sie gegen neuwertige verglichen wird (echter Fall: einzige EX-Karte
    unter 24 NM/MT-Angeboten, angeblich 21% zu billig).
    """
    others = [c for c in competitors if c["seller"].lower() != me.lower()]
    same = [c for c in others if my_condition and c["condition"] == my_condition]
    below = sum(1 for c in others if c["price"] < my_price)
    best = min((c["price"] for c in others), default=None)

    found_me = any(c["seller"].lower() == me.lower() for c in competitors)
    # Stehe ich nicht in den angezeigten 50 und bin teurer als alle davon,
    # ist mein echter Rang unbekannt — aber sicher schlechter als 50.
    capped = (not found_me
              and len(competitors) >= SHOWN_LIMIT
              and all(c["price"] <= my_price for c in others))
    return {
        "rank": None if capped else below + 1,
        "rank_capped": 1 if capped else 0,
        "competitors_below": below,
        "competitors_total": len(others),
        "best_price": best,
        "best_same": min((c["price"] for c in same), default=None),
        "competitors_same": len(same),
    }


def get_setting(db: sqlite3.Connection, key: str, default: str = "") -> str:
    row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row and row[0] else default


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="nur die ersten N Gruppen")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S",
    )

    db = sqlite3.connect(DB_PATH)
    me = get_setting(db, "cardmarket_user")
    api_key = get_setting(db, "brightdata_api_key")
    zone = get_setting(db, "brightdata_zone", "cardmarket")
    if not me or not api_key:
        log.error("cardmarket_user oder brightdata_api_key fehlt in settings")
        return 2

    rows = db.execute(
        """SELECT id, product_url, product_name, condition, language, price
           FROM listings WHERE active = 1 AND price IS NOT NULL"""
    ).fetchall()

    # Mehrere eigene Angebote derselben Karte in gleicher Auspraegung teilen sich
    # einen Abruf — das spart bei Doppelten spuerbar Requests.
    groups: dict[tuple[str, str, str], list] = {}
    for r in rows:
        groups.setdefault((r[1], r[3], r[4]), []).append(r)

    todo = list(groups.items())
    if args.limit:
        todo = todo[: args.limit]
    log.info("%d Angebote in %d Abruf-Gruppen", len(rows), len(todo))

    done = failed = 0

    def load(item):
        (product_url, condition, language), listings = item
        try:
            return item, bd_fetch(build_url(product_url, condition, language), api_key, zone), None
        except Exception as e:
            return item, None, e

    with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
        for i, (item, html, err) in enumerate(pool.map(load, todo), 1):
            (product_url, condition, language), listings = item
            name = listings[0][2][:38]

            if err is not None:
                log.error("  [%d/%d] %s — Abruf fehlgeschlagen: %s", i, len(todo), name, err)
                failed += 1
                continue

            market = extract_prices(html) or {}
            competitors = parse_competitors(html)
            if not competitors:
                log.warning("  [%d/%d] %s — keine Angebote geparst", i, len(todo), name)
                failed += 1
                continue

            for listing_id, _u, pname, _c, _l, my_price in listings:
                res = rank_for(my_price, competitors, me, condition)
                rank_txt = f"Rang {res['rank']}" if res["rank"] else f">{SHOWN_LIMIT}"
                log.info("  [%d/%d] %-36s %8.2f € | %-8s von %2d | best %s",
                         i, len(todo), pname[:36], my_price, rank_txt,
                         res["competitors_total"],
                         f"{res['best_price']:.2f} €" if res["best_price"] else "—")

                if not args.dry_run:
                    db.execute(
                        """UPDATE listing_snapshots
                           SET rank = ?, rank_capped = ?, competitors_below = ?,
                               competitors_total = ?, best_price = ?,
                               best_same = ?, competitors_same = ?,
                               market_trend = ?, market_avg7 = ?, market_avg30 = ?,
                               market_avg1 = ?, market_available = ?
                           WHERE listing_id = ? AND captured_at = (
                               SELECT MAX(captured_at) FROM listing_snapshots
                               WHERE listing_id = ?)""",
                        (res["rank"], res["rank_capped"], res["competitors_below"],
                         res["competitors_total"], res["best_price"],
                         res["best_same"], res["competitors_same"],
                         market.get("trend"), market.get("avg7"), market.get("avg30"),
                         market.get("avg1"), market.get("available_items"),
                         listing_id, listing_id),
                    )
                done += 1

            if not args.dry_run:
                db.commit()

    log.info("Fertig: %d Angebote bewertet, %d Gruppen fehlgeschlagen", done, failed)
    return 1 if failed and not done else 0


if __name__ == "__main__":
    sys.exit(main())
