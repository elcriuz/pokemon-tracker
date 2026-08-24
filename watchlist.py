#!/usr/bin/env python3
"""Beobachtet Wunschkarten und meldet, wann sich der Zugriff lohnt.

Der Unterschied zu Cardmarkets Wantlist: dort steht nur, dass man etwas sucht.
Hier kommt dazu, was es kosten darf — und vor allem ein Preisverlauf, an dem
sich erkennen laesst, ob 34 Euro heute guenstig sind oder ob es letzte Woche
28 waren.

  python3 watchlist.py              # Preise holen, Kaufsignale schreiben
  python3 watchlist.py --dry-run
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
DB_PATH = ROOT / "data" / "tracker.db"

from scrape_brightdata import extract_card_info
from scrape_competition import (MAX_PARALLEL, bd_fetch, build_url, extract_prices,
                                parse_competitors)

log = logging.getLogger("watchlist")

BUY = "buy"


def get(db, key, default):
    row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    try:
        return float(row[0]) if row and row[0] != "" else float(default)
    except (TypeError, ValueError):
        return float(default)


def median(values: list[float]) -> float | None:
    if not values:
        return None
    v = sorted(values)
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def evaluate_buy(item: dict, snap: dict, prev: dict | None, below_pct: float) -> dict | None:
    """Kaufsignal: Zielpreis erreicht, oder deutlich unter dem ueblichen Niveau."""
    best = snap.get("best_price")
    if not best:
        return None

    target = item.get("target_price")
    if target and best <= target:
        return {
            "kind": BUY,
            "price": best,
            "detail": f"Zielpreis erreicht: {best:.2f} € (Ziel {target:.2f} €)",
        }

    # Ohne Zielpreis gilt der eigene Verlauf als Massstab: deutlich unter dem
    # Mittelfeld heisst, dass gerade jemand billiger raus will.
    med = snap.get("median_price")
    if med and best < med * (1 - below_pct / 100):
        drop = ""
        if prev and prev.get("best_price"):
            delta = best / prev["best_price"] - 1
            if delta < -0.05:
                drop = f", {abs(delta)*100:.0f}% günstiger als beim letzten Blick"
        return {
            "kind": BUY,
            "price": best,
            "detail": (f"{best:.2f} € liegt {(1-best/med)*100:.0f}% unter dem "
                       f"Mittelfeld ({med:.2f} €){drop}"),
        }
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")

    db = sqlite3.connect(DB_PATH)
    api_key = get_setting(db, "brightdata_api_key")
    zone = get_setting(db, "brightdata_zone", "cardmarket")
    me = get_setting(db, "cardmarket_user")
    below_pct = get(db, "sig_buy_below_median_pct", 12)
    if not api_key:
        log.error("Kein Bright-Data-Key in settings")
        return 2

    items = [dict(zip(["id", "product_url", "name", "condition", "language",
                       "target_price", "max_price"], r))
             for r in db.execute("""SELECT id, product_url, name, condition, language,
                                           target_price, max_price
                                    FROM watchlist WHERE active = 1""")]
    if not items:
        log.info("Watchlist ist leer")
        return 0
    log.info("%d Wunschkarten", len(items))

    def load(it):
        try:
            return it, bd_fetch(build_url(it["product_url"], it["condition"], it["language"]),
                                api_key, zone), None
        except Exception as e:
            return it, None, e

    now = datetime.now().isoformat(timespec="seconds")
    hits = failed = 0

    with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
        for it, html, err in pool.map(load, items):
            if err is not None:
                log.error("  %s — Abruf fehlgeschlagen: %s", it["name"][:34], err)
                failed += 1
                continue

            # Eigene Angebote gehoeren nicht in den Kaufpreis-Vergleich.
            offers = [c for c in parse_competitors(html)
                      if c["seller"].lower() != (me or "").lower()
                      and c["condition"] == it["condition"]]
            market = extract_prices(html) or {}

            # Der aus der URL geratene Name wird durch den echten von der Seite
            # ersetzt, sobald wir sie einmal geholt haben.
            title = (extract_card_info(html) or {}).get("page_title")
            if title and title.strip() and title.strip() != it["name"]:
                db.execute("UPDATE watchlist SET name = ? WHERE id = ?",
                           (title.strip(), it["id"]))
                it["name"] = title.strip()
            prices = [c["price"] for c in offers]
            snap = {
                "best_price": min(prices) if prices else None,
                "median_price": median(prices),
                "offers_count": len(prices),
                "market_trend": market.get("trend"),
                "market_avg7": market.get("avg7"),
                "market_avg30": market.get("avg30"),
            }

            prev = db.execute("""SELECT best_price, median_price FROM watchlist_snapshots
                                 WHERE watchlist_id = ? ORDER BY captured_at DESC LIMIT 1""",
                              (it["id"],)).fetchone()
            prev_d = {"best_price": prev[0], "median_price": prev[1]} if prev else None

            # Ein Link, der auf eine Set-Uebersicht statt auf eine Karte zeigt,
            # liefert dauerhaft null Angebote. Ohne Hinweis liegt so ein Eintrag
            # still auf der Liste und man wartet auf ein Signal, das nie kommt.
            problem = None
            if "articleRow" not in html:
                problem = "Der Link führt nicht auf eine Kartenseite"
            elif not offers:
                problem = f"Kein Angebot in {it['condition']}/{it['language']}"

            if not args.dry_run:
                db.execute("UPDATE watchlist SET last_error = ? WHERE id = ?",
                           (problem, it["id"]))
            if problem:
                log.warning("  %-34s %s", it["name"][:34], problem)

            sig = evaluate_buy(it, snap, prev_d, below_pct)
            mark = "  ← KAUFEN" if sig else ""
            log.info("  %-34s %s € (Median %s, %d Angebote)%s", it["name"][:34],
                     f"{snap['best_price']:.2f}" if snap["best_price"] else "—",
                     f"{snap['median_price']:.2f}" if snap["median_price"] else "—",
                     snap["offers_count"], mark)

            if args.dry_run:
                continue

            db.execute("""INSERT OR IGNORE INTO watchlist_snapshots
                (watchlist_id, captured_at, best_price, median_price, offers_count,
                 market_trend, market_avg7, market_avg30) VALUES (?,?,?,?,?,?,?,?)""",
                (it["id"], now, snap["best_price"], snap["median_price"],
                 snap["offers_count"], snap["market_trend"], snap["market_avg7"],
                 snap["market_avg30"]))

            if sig:
                hits += 1
                # Dieselbe Wiedervorlagesperre wie bei den Verkaufssignalen, damit
                # eine dauerhaft guenstige Karte nicht jeden Tag meldet.
                cutoff = (datetime.now() - timedelta(days=7)).isoformat(timespec="seconds")
                exists = db.execute("""SELECT 1 FROM signals WHERE watchlist_id = ? AND kind = ?
                    AND created_at > ? AND dismissed_at IS NULL LIMIT 1""",
                    (it["id"], BUY, cutoff)).fetchone()
                if not exists:
                    db.execute("""INSERT INTO signals
                        (watchlist_id, kind, created_at, my_price, suggested_price, detail)
                        VALUES (?, ?, ?, ?, ?, ?)""",
                        (it["id"], BUY, now, None, sig["price"], sig["detail"]))
            db.commit()

    log.info("Fertig: %d Kaufsignale, %d Fehler", hits, failed)
    return 0


def get_setting(db, key, default=""):
    row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row and row[0] else default


if __name__ == "__main__":
    sys.exit(main())
