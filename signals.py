#!/usr/bin/env python3
"""Leitet aus Angebots- und Marktdaten Handlungssignale ab.

Beantwortet die zwei teuren Fragen: Verkaufe ich gerade unter Wert, weil der Markt
gestiegen ist? Und liegt etwas wie Blei, weil der Markt unter meinen Preis gefallen ist?

  python3 signals.py --dry-run     # nur zeigen
  python3 signals.py --notify      # neue Signale per Telegram melden
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "tracker.db"

log = logging.getLogger("signals")

# Unter so vielen zustandsgleichen Angeboten ist ein Preisvergleich nicht belastbar.
MIN_COMPARABLES = 2

RAISE, LOWER, SELL_NOW, UNDERCUT = "raise", "lower", "sell_now", "undercut"
UNDERPRICED = "underpriced"
OVERPRICED = "overpriced"

LABELS = {
    RAISE:    ("📈", "Preis anheben"),
    LOWER:    ("📉", "Preis senken"),
    SELL_NOW: ("🔥", "Jetzt verkaufen"),
    UNDERCUT: ("⚔️", "Unterboten"),
    UNDERPRICED: ("💸", "Zu günstig"),
    OVERPRICED: ("🧊", "Zu teuer"),
    "buy":       ("🛒", "Kaufen"),
}


def get(db, key, default):
    row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    try:
        return float(row[0]) if row and row[0] != "" else float(default)
    except (TypeError, ValueError):
        return float(default)


def latest_snapshots(db) -> list[dict]:
    """Je aktivem Angebot der neueste Snapshot plus der davor (fuer Veraenderungen)."""
    rows = db.execute("""
        SELECT l.id, l.product_name, l.game, l.condition, l.language, l.price,
               l.first_seen, l.product_url,
               s.captured_at, s.rank, s.rank_capped, s.competitors_total, s.best_price,
               s.best_same, s.median_same, s.competitors_same,
               s.market_trend, s.market_avg7, s.market_avg30, s.market_avg1,
               s.market_available
        FROM listings l
        JOIN listing_snapshots s ON s.listing_id = l.id
        WHERE l.active = 1
          AND s.captured_at = (SELECT MAX(captured_at) FROM listing_snapshots
                               WHERE listing_id = l.id)
    """).fetchall()
    cols = ["id", "name", "game", "condition", "language", "price", "first_seen",
            "url", "captured_at", "rank", "rank_capped", "competitors_total",
            "best_price", "best_same", "median_same", "competitors_same",
            "trend", "avg7", "avg30", "avg1", "available"]
    out = []
    for r in rows:
        d = dict(zip(cols, r))
        prev = db.execute("""
            SELECT rank, market_available FROM listing_snapshots
            WHERE listing_id = ? AND captured_at < ?
            ORDER BY captured_at DESC LIMIT 1""", (d["id"], d["captured_at"])).fetchone()
        d["prev_rank"] = prev[0] if prev else None
        d["prev_available"] = prev[1] if prev else None
        out.append(d)
    return out


def rank_text(d: dict) -> str:
    """'Rang 7 von 40' — oder nur 'Rang 7', wenn die Gesamtzahl fehlt."""
    if d["competitors_total"]:
        return f"Rang {d['rank']} von {d['competitors_total']}"
    return f"Rang {d['rank']}"


def days_since(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return (datetime.now() - datetime.fromisoformat(iso)).total_seconds() / 86400
    except ValueError:
        return None


def evaluate(d: dict, cfg: dict) -> list[dict]:
    """Prueft alle Regeln fuer ein Angebot. Fehlende Marktdaten = kein Signal."""
    out = []
    price, trend, avg7, avg30, avg1 = (d["price"], d["trend"], d["avg7"],
                                       d["avg30"], d["avg1"])
    if price is None or price < cfg["min_price"]:
        return out

    # 1) Markt zieht an und ich unterbiete den vergleichbaren Markt deutlich.
    #
    # Verglichen wird gegen best_price, NICHT gegen den Produkt-Trend: der Trend
    # gilt fuer alle Zustaende gemeinsam und wird von NM-Ware dominiert. Eine
    # GD-Karte daran zu messen wuerde absurde Zielpreise liefern (echter Fall:
    # 680-€-Angebot, Trend 2075 €, vergleichbarer Markt aber 650 €).
    # Preisvergleiche laufen ueber best_same (gleicher Zustand), nicht best_price
    # (= "mindestens so gut"). Unter zwei Vergleichsangeboten ist die Datenlage zu
    # duenn fuer eine Empfehlung — dann lieber schweigen als falsch raten.
    best = d.get("best_same") if (d.get("competitors_same") or 0) >= MIN_COMPARABLES else None
    if (best and avg7 is not None and avg30 is not None and avg30 > 0):
        uptrend = avg7 > avg30 * (1 + cfg["raise_uptrend"] / 100)
        undercutting = price < best * (1 - cfg["raise_below"] / 100)
        if uptrend and undercutting:
            out.append({
                "kind": RAISE,
                "suggested": round(best * 0.99, 2),
                "detail": (f"Markt +{(avg7/avg30-1)*100:.0f}% (7T über 30T), "
                           f"du liegst {(1-price/best)*100:.0f}% unter dem günstigsten "
                           f"{d['condition']}-Angebot ({best:.2f} €, "
                           f"{d['competitors_same']} Vergleichsangebote)"),
            })

    # 2) Deutlich unter dem vergleichbaren Markt — unabhaengig von der Marktrichtung.
    #
    # Das ist der haeufigste Fall: Angebote werden guenstig reingestellt und bleiben
    # dann stehen, waehrend niemand mitbekommt, dass der vergleichbare Markt viel
    # hoeher liegt. Braucht keinen Aufwaertstrend, der Abstand allein genuegt.
    if best and price < best * (1 - cfg["underpriced"] / 100):
        out.append({
            "kind": UNDERPRICED,
            "suggested": round(best * 0.99, 2),
            "detail": (f"{(1-price/best)*100:.0f}% unter dem günstigsten "
                       f"{d['condition']}-Angebot ({best:.2f} €, "
                       f"{d['competitors_same']} Vergleichsangebote), {rank_text(d)}"),
        })

    # 3) Deutlich ueber dem zustandsgleichen Markt -> wird so nie verkauft.
    #
    # Verglichen wird gegen den MEDIAN, nicht gegen das guenstigste Angebot:
    # ueber dem Billigsten zu liegen ist voellig normal, deutlich ueber der
    # Mitte des Feldes dagegen heisst, dass zuerst alle anderen wegverkauft
    # werden. Braucht keine Standzeit — der Abstand steht ab Tag eins fest.
    med = d.get("median_same") if (d.get("competitors_same") or 0) >= MIN_COMPARABLES else None
    if med and price > med * (1 + cfg["overpriced"] / 100):
        out.append({
            "kind": OVERPRICED,
            "suggested": round(med, 2),
            "detail": (f"{(price/med-1)*100:.0f}% über dem Mittelfeld der "
                       f"{d['condition']}-Angebote ({med:.2f} €, "
                       f"{d['competitors_same']} Vergleichsangebote), {rank_text(d)}"),
        })

    # 4) Ladenhueter im fallenden Markt -> runter, bevor es schlimmer wird.
    age = days_since(d["first_seen"])
    if (age is not None and age > cfg["lower_days"]
            and d["rank"] is not None and d["rank"] > cfg["lower_rank"]
            and avg7 is not None and avg30 is not None and avg7 < avg30):
        best = d["best_price"]
        out.append({
            "kind": LOWER,
            "suggested": round(best - 0.01, 2) if best else None,
            "detail": (f"{age:.0f} Tage gelistet, "
                       + rank_text(d)
                       + f", Markt fällt ({avg7:.2f} € < {avg30:.2f} €)"),
        })

    # 5) Kurzfristiger Ausschlag nach oben bei sinkendem Angebot -> Spitze mitnehmen.
    if avg1 is not None and avg30 is not None and avg30 > 0:
        spike = avg1 > avg30 * (1 + cfg["sellnow_spike"] / 100)
        supply_down = (d["available"] is not None and d["prev_available"] is not None
                       and d["available"] < d["prev_available"])
        if spike and supply_down:
            # Zielpreis wieder aus dem vergleichbaren Markt, nicht aus avg1 —
            # avg1 mischt alle Zustaende.
            best_now = best
            out.append({
                "kind": SELL_NOW,
                "suggested": round(best_now * 0.99, 2) if best_now else None,
                "detail": (f"Tagesschnitt {avg1:.2f} € liegt {(avg1/avg30-1)*100:.0f}% "
                           f"über dem 30-Tage-Schnitt, Angebot schrumpft "
                           f"({d['prev_available']} → {d['available']})"),
            })

    # 6) Jemand hat mich unterboten -> ich bin nicht mehr vorne.
    if (d["rank"] is not None and d["prev_rank"] is not None
            and d["prev_rank"] <= 3 and d["rank"] > d["prev_rank"]):
        out.append({
            "kind": UNDERCUT,
            "suggested": round(d["best_price"] - 0.01, 2) if d["best_price"] else None,
            "detail": (f"Rang {d['prev_rank']} → {d['rank']}"
                       + (f", günstigster jetzt {d['best_price']:.2f} €"
                          if d["best_price"] else "")),
        })

    return out


def store(db, listing_id: int, sig: dict, my_price: float, repeat_days: float) -> bool:
    """Legt ein Signal an, sofern nicht kuerzlich dasselbe schon gemeldet wurde."""
    cutoff = (datetime.now() - timedelta(days=repeat_days)).isoformat(timespec="seconds")
    exists = db.execute("""
        SELECT 1 FROM signals
        WHERE listing_id = ? AND kind = ? AND created_at > ? AND dismissed_at IS NULL
        LIMIT 1""", (listing_id, sig["kind"], cutoff)).fetchone()
    if exists:
        return False
    db.execute("""
        INSERT INTO signals (listing_id, kind, created_at, my_price, suggested_price, detail)
        VALUES (?,?,?,?,?,?)""",
        (listing_id, sig["kind"], datetime.now().isoformat(timespec="seconds"),
         my_price, sig.get("suggested"), sig["detail"]))
    return True


def notify(db, limit: int = 12) -> int:
    """Schickt noch nicht gemeldete Signale gebuendelt per Telegram."""
    try:
        from scrape_brightdata import send_telegram
    except Exception as e:
        log.error("Telegram nicht verfuegbar: %s", e)
        return 0

    # LEFT JOIN auf beide Quellen: Verkaufssignale haengen an einem eigenen
    # Angebot, Kaufsignale an einem Wunschlisten-Eintrag.
    rows = db.execute("""
        SELECT s.id, s.kind, s.my_price, s.suggested_price, s.detail,
               COALESCE(l.product_name, w.name)   AS name,
               COALESCE(l.game, w.game)           AS game,
               COALESCE(l.condition, w.condition) AS cond,
               COALESCE(l.language, w.language)   AS lang,
               COALESCE(l.product_url, w.product_url) AS url
        FROM signals s
        LEFT JOIN listings l  ON l.id = s.listing_id
        LEFT JOIN watchlist w ON w.id = s.watchlist_id
        WHERE s.notified_at IS NULL AND s.dismissed_at IS NULL
          AND COALESCE(l.product_name, w.name) IS NOT NULL
        ORDER BY CASE s.kind WHEN 'buy' THEN 0 WHEN 'sell_now' THEN 1
                             WHEN 'underpriced' THEN 2 WHEN 'raise' THEN 3
                             WHEN 'undercut' THEN 4 ELSE 5 END,
                 COALESCE(s.my_price, s.suggested_price) DESC""").fetchall()
    if not rows:
        log.info("Keine neuen Signale zu melden")
        return 0

    lines = [f"<b>Cardmarket — {len(rows)} neue Signale</b>", ""]
    for (_id, kind, price, sugg, detail, name, game, cond, lang, url) in rows[:limit]:
        icon, label = LABELS.get(kind, ("•", kind))
        lines.append(f'{icon} <a href="{url}">{name[:44]}</a> <i>{game[:3]} {cond}/{lang}</i>')
        if price is None:
            # Kaufsignal: es gibt keinen "eigenen" Preis, nur den am Markt.
            lines.append(f"   <b>{sugg:.2f} €</b> — {detail}" if sugg else f"   {detail}")
        else:
            target = f" → <b>{sugg:.2f} €</b>" if sugg else ""
            lines.append(f"   {price:.2f} €{target} — {detail}")
    if len(rows) > limit:
        lines.append(f"\n… und {len(rows) - limit} weitere im Dashboard")

    send_telegram("\n".join(lines))
    now = datetime.now().isoformat(timespec="seconds")
    db.executemany("UPDATE signals SET notified_at = ? WHERE id = ?",
                   [(now, r[0]) for r in rows])
    db.commit()
    return len(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="nichts speichern")
    ap.add_argument("--notify", action="store_true", help="neue Signale per Telegram melden")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s",
                        datefmt="%H:%M:%S")

    db = sqlite3.connect(DB_PATH)
    cfg = {
        "raise_uptrend": get(db, "sig_raise_uptrend_pct", 5),
        "raise_below": get(db, "sig_raise_below_trend_pct", 10),
        "lower_days": get(db, "sig_lower_days", 30),
        "lower_rank": get(db, "sig_lower_rank", 5),
        "sellnow_spike": get(db, "sig_sellnow_spike_pct", 20),
        "min_price": get(db, "sig_min_price_eur", 2),
        "underpriced": get(db, "sig_underpriced_pct", 15),
        "overpriced": get(db, "sig_overpriced_pct", 60),
        "repeat_days": get(db, "sig_repeat_days", 14),
    }

    listings = latest_snapshots(db)
    log.info("%d aktive Angebote mit Snapshot", len(listings))

    found = new = 0
    for d in listings:
        for sig in evaluate(d, cfg):
            found += 1
            icon, label = LABELS[sig["kind"]]
            tgt = f" → {sig['suggested']:.2f} €" if sig.get("suggested") else ""
            log.info("  %s %-34s %7.2f €%s | %s", icon, d["name"][:34], d["price"], tgt,
                     sig["detail"])
            if not args.dry_run and store(db, d["id"], sig, d["price"], cfg["repeat_days"]):
                new += 1

    if not args.dry_run:
        db.commit()
        log.info("%d Signale erkannt, %d neu gespeichert", found, new)
        if args.notify:
            log.info("%d per Telegram gemeldet", notify(db))
    else:
        log.info("%d Signale erkannt (dry-run)", found)
    return 0


if __name__ == "__main__":
    sys.exit(main())
