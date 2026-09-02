#!/usr/bin/env python3
"""Liest die eigenen Cardmarket-Verkaeufe samt Positionen.

Startet fuer den Lauf einen eigenen Chrome auf dem angemeldeten Profil (der
noVNC-Dienst wird solange gestoppt) — ohne Fernsteuerungs-Port, den Cloudflare
als Automatik erkennt.

  python3 scrape_sales.py            # neue und noch offene Bestellungen holen
  python3 scrape_sales.py --all      # auch bereits abgeschlossene neu einlesen
"""
from __future__ import annotations

import argparse
import html as html_mod
import logging
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
DB_PATH = ROOT / "data" / "tracker.db"

BASE = "https://www.cardmarket.com"

# Reihenfolge = Lebenslauf einer Bestellung. "Arrived" ist der Endzustand;
# was dort steht, aendert sich nicht mehr und wird nur einmal geholt.
STATES = ["Paid", "Sent", "Arrived"]
FINAL_STATES = {"Arrived"}

log = logging.getLogger("sales")

from cardmarket_guard import (Gesperrt, Challenge, NichtAngemeldet, Takt,
                              seite_pruefen, sperre_pruefen, sperre_aufheben)

takt = Takt()

# Zahlenschluessel wie in scrape_competition — Cardmarket nutzt sie ueberall gleich.
CONDITIONS = {1: "MT", 2: "NM", 3: "EX", 4: "GD", 5: "LP", 6: "PL", 7: "PO"}
LANGUAGES = {1: "en", 2: "fr", 3: "de", 4: "es", 5: "it",
             6: "zh", 7: "ja", 8: "pt", 9: "ru", 10: "ko", 11: "zh-t"}

ROW_RE = re.compile(r"<tr\s+data-article-id=[^>]*>")
ATTR_RE = re.compile(r'data-([\w-]+)="([^"]*)"')
ORDER_LINK_RE = re.compile(r"/de/(\w+)/Orders/(\d+)")
PRODUCT_RE = re.compile(r'<a href="(/[a-z]{2}/\w+/Products/[^"?]+)')
PAGES_RE = re.compile(r"Seite\s+\d+\s+von\s+(\d+)")

# Betraege aus der Zusammenfassung. Die Labels stehen als Text neben dem Wert.
SUMMARY_LABELS = {
    "item_value": r"Artikelwert",
    "shipping": r"Versandkosten",
    "total": r"Gesamtsumme",
}
DATE_LABELS = {"paid_at": "Bezahlt", "sent_at": "Versandt", "arrived_at": "Angekommen"}


def parse_de_price(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return float(s.replace(".", "").replace(",", "."))
    except ValueError:
        return None


def parse_de_date(s: str) -> str | None:
    """'24.08.202619:44' -> ISO. Cardmarket klebt Datum und Uhrzeit zusammen."""
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})\s*(\d{2}):(\d{2})", s)
    if not m:
        return None
    d, mo, y, hh, mm = m.groups()
    return f"{y}-{mo}-{d}T{hh}:{mm}:00"


def parse_order_items(page_html: str) -> list[dict]:
    """Positionen aus den data-Attributen lesen — stabiler als HTML-Struktur."""
    items = []
    for m in ROW_RE.finditer(page_html):
        attrs = dict(ATTR_RE.findall(m.group(0)))
        if "article-id" not in attrs:
            continue
        # Der Produktlink steht kurz nach der Zeile.
        tail = page_html[m.end():m.end() + 2000]
        prod = PRODUCT_RE.search(tail)
        try:
            cond = CONDITIONS.get(int(attrs.get("condition") or 0), "")
            lang = LANGUAGES.get(int(attrs.get("language") or 0), "")
        except ValueError:
            cond = lang = ""
        items.append({
            "cm_article_id": attrs.get("article-id", ""),
            "product_url": BASE + prod.group(1) if prod else "",
            "name": html_mod.unescape(attrs.get("name", "")),
            "expansion": html_mod.unescape(attrs.get("expansion-name", "")),
            "number": attrs.get("number", ""),
            "condition": cond,
            "language": lang,
            "price": parse_de_price(attrs.get("price", "").replace(".", ",")),
            "amount": int(attrs.get("amount") or 1),
            "comment": html_mod.unescape(attrs.get("comment", "")),
        })
    return items


def parse_order_detail(text: str, page_html: str) -> dict:
    """Betraege und Zeitstempel aus der Zusammenfassung."""
    out: dict = {}
    for key, label in SUMMARY_LABELS.items():
        m = re.search(label + r"\s*([\d.]+,\d{2})\s*€", text)
        out[key] = parse_de_price(m.group(1)) if m else None
    for key, label in DATE_LABELS.items():
        m = re.search(label + r":?\s*(\d{2}\.\d{2}\.\d{4}\s*\d{2}:\d{2})", text)
        out[key] = parse_de_date(m.group(1)) if m else None
    out["items"] = parse_order_items(page_html)
    return out


def parse_order_list(page_html: str) -> list[dict]:
    """Bestell-IDs samt Spiel aus einer Uebersichtsseite."""
    seen = {}
    for m in ORDER_LINK_RE.finditer(page_html):
        game, oid = m.group(1), m.group(2)
        seen.setdefault(oid, game)
    return [{"cm_order_id": k, "game": v} for k, v in seen.items()]


def strip_query(url: str) -> str:
    """Portfolio-URLs tragen Filter-Parameter (?language=1&minCondition=3),
    Bestell-URLs nicht. Ohne Abschneiden findet die Zuordnung nie etwas."""
    return url.split("?", 1)[0].rstrip("/")


def card_index(db: sqlite3.Connection) -> dict[str, int]:
    return {strip_query(u): i for i, u in db.execute("SELECT id, url FROM cards")}


def game_from_items(items: list[dict], fallback: str) -> str:
    """Das Spiel steht im Produktpfad. Die Bestell-URL sagt es nicht — sie
    lautet immer /de/Pokemon/Orders/..., egal was verkauft wurde."""
    games = [it["product_url"].split("/")[4] for it in items
             if it["product_url"].count("/") >= 5]
    if not games:
        return fallback
    return max(set(games), key=games.count)


def ensure_schema(db: sqlite3.Connection) -> None:
    """Die Tabellen legt sonst der Node-Server an; hier fuer den Cron-Fall."""
    db.executescript("""
        CREATE TABLE IF NOT EXISTS orders (
          id           INTEGER PRIMARY KEY AUTOINCREMENT,
          cm_order_id  TEXT    NOT NULL UNIQUE,
          game         TEXT    NOT NULL DEFAULT '',
          buyer        TEXT    NOT NULL DEFAULT '',
          state        TEXT    NOT NULL DEFAULT '',
          item_value   REAL,
          shipping     REAL,
          total        REAL,
          paid_at      TEXT,
          sent_at      TEXT,
          arrived_at   TEXT,
          fetched_at   TEXT    NOT NULL
        );
        CREATE TABLE IF NOT EXISTS order_items (
          id            INTEGER PRIMARY KEY AUTOINCREMENT,
          order_id      INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
          card_id       INTEGER REFERENCES cards(id) ON DELETE SET NULL,
          cm_article_id TEXT,
          product_url   TEXT    NOT NULL DEFAULT '',
          name          TEXT    NOT NULL DEFAULT '',
          expansion     TEXT    NOT NULL DEFAULT '',
          number        TEXT    NOT NULL DEFAULT '',
          condition     TEXT    NOT NULL DEFAULT '',
          language      TEXT    NOT NULL DEFAULT '',
          price         REAL,
          amount        INTEGER NOT NULL DEFAULT 1,
          comment       TEXT    NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_order_items_order ON order_items(order_id);
        CREATE INDEX IF NOT EXISTS idx_orders_state ON orders(state);
    """)
    db.commit()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true",
                    help="auch abgeschlossene Bestellungen neu einlesen")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s",
                        datefmt="%H:%M:%S")

    db = sqlite3.connect(DB_PATH)
    ensure_schema(db)

    # Erst pruefen, dann Browser: bei laufender Sperre den noVNC-Browser gar
    # nicht anfassen.
    try:
        sperre_pruefen()
    except Gesperrt as e:
        log.error("%s", e)
        return 3

    from cardmarket_browser import eigener_browser

    with eigener_browser() as (_ctx, page):

        # 1) Uebersichten einsammeln
        found: dict[str, dict] = {}
        for state in STATES:
            site = 1
            pages = 1
            while site <= pages:
                url = f"{BASE}/de/Pokemon/Orders/Sales/{state}"
                if site > 1:
                    url += f"?site={site}"
                takt.warten()
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                seite_pruefen(page)
                h = page.content()
                if "Account/Login" in page.url:
                    log.error("Nicht angemeldet — bitte ueber noVNC einloggen")
                    return 2
                if site == 1:
                    m = PAGES_RE.search(re.sub(r"\s+", " ", page.inner_text("body")))
                    pages = int(m.group(1)) if m else 1
                for o in parse_order_list(h):
                    found.setdefault(o["cm_order_id"], {**o, "state": state})
                site += 1
            log.info("  %-8s bis hier %d Bestellungen", state, len(found))

        # 2) Details nur fuer Neue oder noch nicht abgeschlossene
        known = {r[0]: r[1] for r in db.execute("SELECT cm_order_id, state FROM orders")}
        todo = [o for o in found.values()
                if args.all or o["cm_order_id"] not in known
                or known[o["cm_order_id"]] not in FINAL_STATES]
        if args.limit:
            todo = todo[: args.limit]
        log.info("%d Bestellungen gesamt, %d zu holen", len(found), len(todo))

        now = datetime.now().isoformat(timespec="seconds")
        cards = card_index(db)
        neu = akt = 0
        for i, o in enumerate(todo, 1):
            takt.warten()
            try:
                page.goto(f"{BASE}/de/{o['game']}/Orders/{o['cm_order_id']}",
                          wait_until="domcontentloaded", timeout=60000)
                seite_pruefen(page)
            except (Gesperrt, Challenge, NichtAngemeldet) as e:
                # Abbrechen statt weiterklopfen — jeder weitere Versuch
                # verlaengert eine laufende Sperre.
                log.error("Abbruch nach %d von %d: %s", i - 1, len(todo), e)
                db.commit()
                page.close()
                return 3
            h = page.content()
            text = re.sub(r"[ \t]+", " ", page.inner_text("body"))
            d = parse_order_detail(text, h)
            # Die Ueberschrift "Verkauf #<id>" steht zweimal untereinander, der
            # Kaeufername erst danach — sonst faengt man die Ueberschrift selbst.
            buyer_m = re.search(r"Verkauf #(\d+)\s*\n\s*Verkauf #\1\s*\n\s*([^\n]+)", text)
            buyer = buyer_m.group(2).strip() if buyer_m else ""

            row = db.execute("SELECT id FROM orders WHERE cm_order_id = ?",
                             (o["cm_order_id"],)).fetchone()
            game = game_from_items(d["items"], o["game"])
            vals = (game, buyer, o["state"], d["item_value"], d["shipping"],
                    d["total"], d["paid_at"], d["sent_at"], d["arrived_at"], now)
            if row:
                db.execute("""UPDATE orders SET game=?, buyer=?, state=?, item_value=?,
                    shipping=?, total=?, paid_at=?, sent_at=?, arrived_at=?, fetched_at=?
                    WHERE id=?""", (*vals, row[0]))
                order_id = row[0]
                akt += 1
            else:
                cur = db.execute("""INSERT INTO orders (game, buyer, state, item_value,
                    shipping, total, paid_at, sent_at, arrived_at, fetched_at, cm_order_id)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (*vals, o["cm_order_id"]))
                order_id = cur.lastrowid
                neu += 1

            # Positionen immer frisch — sonst verdoppeln sie sich bei Updates.
            db.execute("DELETE FROM order_items WHERE order_id = ?", (order_id,))
            for it in d["items"]:
                card_id = cards.get(strip_query(it["product_url"]))
                db.execute("""INSERT INTO order_items (order_id, card_id, cm_article_id,
                    product_url, name, expansion, number, condition, language, price,
                    amount, comment) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (order_id, card_id, it["cm_article_id"],
                     it["product_url"], it["name"], it["expansion"], it["number"],
                     it["condition"], it["language"], it["price"], it["amount"],
                     it["comment"]))
            db.commit()
            log.info("  [%d/%d] #%s %-14s %s € (%d Positionen)", i, len(todo),
                     o["cm_order_id"], buyer[:14],
                     f"{d['total']:.2f}" if d["total"] else "—", len(d["items"]))

        page.close()

    sperre_aufheben()
    log.info("Fertig: %d neu, %d aktualisiert", neu, akt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
