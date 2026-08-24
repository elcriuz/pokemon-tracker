#!/usr/bin/env python3
"""Tests fuer das Cardmarket-Modul.

Laeuft komplett offline gegen gespeicherte Seiten in tests/fixtures/ — kostet also
keine Bright-Data-Abrufe und bleibt auch dann gruen, wenn Cardmarket gerade zickt.
Bricht das Layout, schlagen die Parser-Tests fehl, statt still falsche Zahlen zu
schreiben.

  python3 tests/test_cardmarket.py
"""
from __future__ import annotations

import gzip
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FIXTURES = Path(__file__).resolve().parent / "fixtures"

import scrape_offers as so
import scrape_competition as sc
import signals as sg
import watchlist as wl

_failures: list[str] = []
_passed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _passed
    if cond:
        _passed += 1
        print(f"  \033[32m✓\033[0m {name}")
    else:
        _failures.append(name)
        print(f"  \033[31m✗\033[0m {name}" + (f" — {detail}" if detail else ""))


def fixture(name: str) -> str:
    with gzip.open(FIXTURES / f"{name}.html.gz", "rt", encoding="utf-8") as f:
        return f.read()


# --------------------------------------------------------------- Angebote

def test_offers_parsing():
    print("\nAngebots-Parser")
    pk = so.parse_offers(fixture("offers_pokemon"), "single", "Pokemon")
    mg = so.parse_offers(fixture("offers_magic"), "single", "Magic")

    check("Pokemon-Seite liefert Angebote", len(pk) >= 15, f"{len(pk)}")
    check("Magic-Seite liefert Angebote", len(mg) >= 15, f"{len(mg)}")
    check("jedes Angebot hat einen Preis", all(o["price"] for o in pk + mg))
    check("jedes Angebot hat einen Zustand", all(o["condition"] for o in pk + mg))
    check("jedes Angebot hat eine Sprache", all(o["language"] for o in pk + mg))
    check("Artikel-IDs sind eindeutig",
          len({o["cm_article_id"] for o in pk}) == len(pk))
    check("Spiel wird aus dem Pfad gelesen",
          {o["game"] for o in pk} == {"Pokemon"} and {o["game"] for o in mg} == {"Magic"})
    check("Magic-Foils werden erkannt", sum(o["is_foil"] for o in mg) > 0,
          "kein einziges Foil — aria-label geaendert?")
    check("HTML-Entities sind dekodiert",
          not any("&amp;" in o["product_name"] + o["expansion"] for o in pk + mg))
    check("Preise sind plausibel",
          all(0.01 <= o["price"] <= 100_000 for o in pk + mg))


def test_offers_persistence():
    """Der gefaehrlichste Teil: Was passiert mit Angeboten, die nicht mehr auftauchen?"""
    print("\nSpeicher-Logik")
    db = sqlite3.connect(":memory:")
    db.executescript("""
        CREATE TABLE cards (id INTEGER PRIMARY KEY, url TEXT);
        CREATE TABLE listings (
          id INTEGER PRIMARY KEY AUTOINCREMENT, card_id INTEGER, cm_article_id TEXT UNIQUE,
          game TEXT DEFAULT 'Pokemon', product_url TEXT, product_name TEXT, expansion TEXT,
          kind TEXT, condition TEXT, language TEXT, is_foil INT DEFAULT 0,
          is_signed INT DEFAULT 0, is_playset INT DEFAULT 0, price REAL,
          quantity INT DEFAULT 1, comment TEXT DEFAULT '', first_seen TEXT, last_seen TEXT,
          active INT DEFAULT 1, gone_at TEXT);
        CREATE TABLE listing_snapshots (
          id INTEGER PRIMARY KEY AUTOINCREMENT, listing_id INT, captured_at TEXT,
          my_price REAL, rank INT, competitors_below INT, competitors_total INT,
          best_price REAL, rank_capped INT DEFAULT 0, market_trend REAL, market_avg7 REAL,
          market_avg30 REAL, market_avg1 REAL, market_available INT,
          UNIQUE(listing_id, captured_at));
    """)
    pk = so.parse_offers(fixture("offers_pokemon"), "single", "Pokemon")
    mg = so.parse_offers(fixture("offers_magic"), "single", "Magic")
    both = {("Pokemon", "single"), ("Magic", "single")}

    s1 = so.save_offers(db, pk + mg, both)
    check("Erstimport legt alle an", s1["neu"] == len(pk) + len(mg))

    s2 = so.save_offers(db, pk + mg, both)
    check("zweiter Lauf legt nichts doppelt an", s2["neu"] == 0)
    check("zweiter Lauf aktualisiert", s2["aktualisiert"] == len(pk) + len(mg))

    s3 = so.save_offers(db, pk[1:] + mg, both)
    check("verkauftes Angebot wird deaktiviert", s3["verschwunden"] == 1)
    gone = db.execute("SELECT active, gone_at FROM listings WHERE cm_article_id = ?",
                      (pk[0]["cm_article_id"],)).fetchone()
    check("deaktiviert statt geloescht", gone is not None and gone[0] == 0 and gone[1])

    # Der kritische Fall: Magic-Abruf schlaegt fehl, nur Pokemon wurde gescannt.
    s4 = so.save_offers(db, pk, {("Pokemon", "single")})
    still = db.execute("SELECT COUNT(*) FROM listings WHERE game='Magic' AND active=1").fetchone()[0]
    check("fehlender Magic-Lauf loescht keine Magic-Angebote",
          still == len(mg) and s4["verschwunden"] == 0,
          f"{still} von {len(mg)} noch aktiv")

    changed = [dict(o) for o in pk]
    changed[0] = {**changed[0], "price": changed[0]["price"] + 5}
    s5 = so.save_offers(db, changed + mg, both)
    check("Preisaenderung wird erkannt", s5["preis_geaendert"] == 1)


# --------------------------------------------------------------- Wettbewerb

def test_competition():
    print("\nWettbewerbsposition")
    de = sc.parse_competitors(fixture("product_de_nm"))
    un = sc.parse_competitors(fixture("product_unfiltered"))

    check("gefilterte Seite liefert Angebote", len(de) >= 20, f"{len(de)}")
    check("Verkaeufer werden gelesen", all(c["seller"] for c in de))

    r = sc.rank_for(4.00, de, "packgehabt")
    check("eigenes Angebot zaehlt nicht als Konkurrenz",
          r["competitors_total"] == len(de) - sum(1 for c in de if c["seller"] == "packgehabt"))
    check("Rang wird ermittelt", r["rank"] is not None and r["rank"] >= 1)

    # Ohne Filter ist die Seite voll fremdsprachiger Angebote — der Rang dort
    # waere irrefuehrend, deshalb muss er als "nicht ermittelbar" markiert werden.
    ru = sc.rank_for(4.00, un, "packgehabt")
    check("nicht gelistet + teurer als alle 50 = Rang unbekannt",
          ru["rank"] is None and ru["rank_capped"] == 1)

    fake = [{"price": 3.0, "seller": "A", "condition": "NM"},
            {"price": 3.5, "seller": "packgehabt", "condition": "NM"},
            {"price": 5.0, "seller": "B", "condition": "NM"}]
    r2 = sc.rank_for(4.00, fake, "packgehabt", "NM")
    check("Rang zaehlt nur fremde Angebote",
          r2["rank"] == 2 and r2["competitors_total"] == 2 and r2["best_price"] == 3.0)

    # Der Fall, der ein 630-Euro-Fehlsignal ausgeloest hat: die einzige EX-Karte
    # unter lauter NM-Ware ist nicht "zu guenstig", sie ist schlechter erhalten.
    mixed = [{"price": 799.0, "seller": "A", "condition": "MT"},
             {"price": 800.0, "seller": "B", "condition": "NM"},
             {"price": 840.0, "seller": "C", "condition": "NM"}]
    r3 = sc.rank_for(630.0, mixed, "packgehabt", "EX")
    check("bessere Zustaende zaehlen nicht als Preisvergleich",
          r3["best_same"] is None and r3["competitors_same"] == 0,
          f"best_same={r3['best_same']}")
    check("Rang beruecksichtigt sie trotzdem (Kaeufersicht)", r3["rank"] == 1)

    r4 = sc.rank_for(630.0, mixed + [{"price": 700.0, "seller": "D", "condition": "EX"}],
                     "packgehabt", "EX")
    check("gleicher Zustand wird als Vergleich erkannt",
          r4["best_same"] == 700.0 and r4["competitors_same"] == 1)

    ex3 = [{"price": p, "seller": f"S{p}", "condition": "EX"} for p in (10.0, 20.0, 60.0)]
    r5 = sc.rank_for(15.0, ex3, "packgehabt", "EX")
    check("Median statt Mittelwert (Ausreisser verzerren nicht)",
          r5["median_same"] == 20.0 and r5["best_same"] == 10.0,
          f"median={r5['median_same']}")

    check("Sprach-ID Deutsch", "language=3" in sc.build_url("u", "NM", "de"))
    check("Sprach-ID Japanisch", "language=7" in sc.build_url("u", "NM", "ja"))
    check("Zustand NM", "minCondition=2" in sc.build_url("u", "NM", "de"))
    check("ohne Sprache kein Sprachfilter", "language=" not in sc.build_url("u", "NM", ""))

    # Marktdaten muessen aus derselben Seite kommen, sonst kostet jede Karte
    # einen zweiten Abruf.
    m = sc.extract_prices(fixture("product_de_nm"))
    check("Marktdaten aus gefilterter Seite",
          all(m.get(k) for k in ("trend", "avg7", "avg30")), str(m)[:80])


# --------------------------------------------------------------- Signale

def base_listing(**kw):
    d = {"id": 1, "name": "Testkarte", "game": "Pokemon", "condition": "NM",
         "language": "de", "price": 10.0, "first_seen": datetime.now().isoformat(),
         "url": "http://x", "captured_at": datetime.now().isoformat(),
         "rank": 3, "rank_capped": 0, "competitors_total": 20, "best_price": 9.0,
         "best_same": 10.0, "median_same": 11.0, "competitors_same": 6,
         "trend": 10.0, "avg7": 10.0, "avg30": 10.0, "avg1": 10.0,
         "available": 100, "prev_rank": 3, "prev_available": 100}
    d.update(kw)
    return d


CFG = {"raise_uptrend": 5, "raise_below": 10, "lower_days": 30, "lower_rank": 5,
       "sellnow_spike": 20, "min_price": 2, "repeat_days": 14, "underpriced": 15,
       "overpriced": 60}


def kinds(d):
    return {s["kind"] for s in sg.evaluate(d, CFG)}


def test_signals():
    print("\nSignal-Regeln")
    check("ruhiger Markt loest nichts aus", kinds(base_listing()) == set())

    # Markt +10%, mein Preis 20% unter Trend
    check("steigender Markt + unter dem Vergleichsmarkt -> anheben",
          sg.RAISE in kinds(base_listing(avg7=11.0, avg30=10.0, price=8.0, best_same=10.0)))
    check("steigender Markt bei marktgerechtem Preis -> kein Signal",
          sg.RAISE not in kinds(base_listing(avg7=11.0, avg30=10.0, price=9.9, best_same=10.0)))

    check("deutlich über dem Mittelfeld -> zu teuer",
          sg.OVERPRICED in kinds(base_listing(price=20.0, median_same=11.0)))
    check("etwas über dem Mittelfeld -> kein Signal",
          sg.OVERPRICED not in kinds(base_listing(price=13.0, median_same=11.0)))
    check("teuer, aber zu wenig Vergleichsangebote -> kein Signal",
          sg.OVERPRICED not in kinds(base_listing(price=20.0, median_same=11.0, competitors_same=1)))
    check("deutlich unter Vergleichsmarkt -> zu günstig",
          sg.UNDERPRICED in kinds(base_listing(price=7.0, best_same=10.0)))
    check("zu wenig Vergleichsangebote -> kein Preissignal",
          kinds(base_listing(price=7.0, best_same=10.0, competitors_same=1)) == set())
    check("ohne zustandsgleichen Vergleich -> kein Preissignal",
          kinds(base_listing(price=7.0, best_same=None, competitors_same=0)) == set())

    old = (datetime.now() - timedelta(days=45)).isoformat()
    check("Ladenhueter im fallenden Markt -> senken",
          sg.LOWER in kinds(base_listing(first_seen=old, rank=12, avg7=9.0, avg30=10.0)))
    check("Ladenhueter aber vorne dabei -> kein Signal",
          sg.LOWER not in kinds(base_listing(first_seen=old, rank=2, avg7=9.0, avg30=10.0)))
    check("schlechter Rang im steigenden Markt -> kein Senken",
          sg.LOWER not in kinds(base_listing(first_seen=old, rank=12, avg7=11.0, avg30=10.0)))

    check("Preisspitze + schrumpfendes Angebot -> jetzt verkaufen",
          sg.SELL_NOW in kinds(base_listing(avg1=13.0, avg30=10.0, available=60, prev_available=100)))
    check("Cent-Regel greift vor allen Preissignalen",
          kinds(base_listing(price=1.0, best_same=10.0)) == set())
    check("Preisspitze bei wachsendem Angebot -> kein Signal",
          sg.SELL_NOW not in kinds(base_listing(avg1=13.0, avg30=10.0, available=140, prev_available=100)))

    check("vom Spitzenplatz verdraengt -> unterboten",
          sg.UNDERCUT in kinds(base_listing(prev_rank=1, rank=4)))
    check("Rang verbessert -> kein Signal",
          sg.UNDERCUT not in kinds(base_listing(prev_rank=4, rank=1)))
    check("hinten schon vorher -> kein Undercut-Alarm",
          sg.UNDERCUT not in kinds(base_listing(prev_rank=9, rank=14)))

    check("fehlende Marktdaten -> kein Signal",
          kinds(base_listing(trend=None, avg7=None, avg30=None, avg1=None,
                             best_same=None, competitors_same=0)) == set())


def test_signal_dedup():
    print("\nSignal-Wiedervorlage")
    db = sqlite3.connect(":memory:")
    db.executescript("""
        CREATE TABLE signals (id INTEGER PRIMARY KEY AUTOINCREMENT, listing_id INT,
          kind TEXT, created_at TEXT, my_price REAL, suggested_price REAL,
          detail TEXT DEFAULT '', notified_at TEXT, dismissed_at TEXT, applied_at TEXT);
    """)
    sig = {"kind": sg.RAISE, "suggested": 12.0, "detail": "Test"}
    check("erstes Signal wird gespeichert", sg.store(db, 1, sig, 10.0, 14))
    check("gleiches Signal kommt nicht doppelt", not sg.store(db, 1, sig, 10.0, 14))
    check("anderes Signal derselben Karte kommt durch",
          sg.store(db, 1, {**sig, "kind": sg.LOWER}, 10.0, 14))
    db.execute("UPDATE signals SET dismissed_at = datetime('now') WHERE kind = 'raise'")
    check("abgehaktes Signal darf wieder auftauchen", sg.store(db, 1, sig, 10.0, 14))


def test_watchlist():
    print("\nWunschliste")
    check("Median bei ungerader Anzahl", wl.median([3.0, 1.0, 2.0]) == 2.0)
    check("Median bei gerader Anzahl", wl.median([1.0, 2.0, 3.0, 4.0]) == 2.5)
    check("Median einer leeren Liste", wl.median([]) is None)

    item = {"id": 1, "name": "X", "target_price": 30.0}
    snap = {"best_price": 28.0, "median_price": 40.0}
    sig = wl.evaluate_buy(item, snap, None, 12)
    check("Zielpreis erreicht -> kaufen", sig is not None and "Zielpreis" in sig["detail"])

    sig2 = wl.evaluate_buy(item, {"best_price": 35.0, "median_price": 36.0}, None, 12)
    check("über Zielpreis und nah am Mittelfeld -> kein Signal", sig2 is None)

    # Ohne Zielpreis zaehlt allein der Abstand zum Mittelfeld.
    frei = {"id": 2, "name": "Y", "target_price": None}
    sig3 = wl.evaluate_buy(frei, {"best_price": 30.0, "median_price": 40.0}, None, 12)
    check("ohne Zielpreis: deutlich unter Mittelfeld -> kaufen", sig3 is not None)
    sig4 = wl.evaluate_buy(frei, {"best_price": 38.0, "median_price": 40.0}, None, 12)
    check("ohne Zielpreis: nah am Mittelfeld -> kein Signal", sig4 is None)

    sig5 = wl.evaluate_buy(frei, {"best_price": 30.0, "median_price": 40.0},
                           {"best_price": 36.0}, 12)
    check("Preisrutsch seit dem letzten Blick wird erwähnt",
          sig5 is not None and "günstiger" in sig5["detail"])

    check("ohne Angebote kein Signal",
          wl.evaluate_buy(item, {"best_price": None, "median_price": None}, None, 12) is None)


def test_blocked_detection():
    print("\nSchutz gegen Fehldaten")
    check("Cloudflare-Seite wird erkannt",
          so.looks_blocked("<html><title>Just a moment...</title>") is not None)
    check("fremde Seite wird erkannt",
          so.looks_blocked("<html><title>Irgendwas anderes</title>") is not None)
    check("echte Seite gilt als in Ordnung",
          so.looks_blocked(fixture("offers_pokemon")) is None)


if __name__ == "__main__":
    print("\033[1mCardmarket-Modul — Tests\033[0m")
    test_offers_parsing()
    test_offers_persistence()
    test_competition()
    test_signals()
    test_signal_dedup()
    test_watchlist()
    test_blocked_detection()

    total = _passed + len(_failures)
    print(f"\n{'─' * 46}")
    if _failures:
        print(f"\033[31m{len(_failures)} von {total} fehlgeschlagen:\033[0m")
        for f in _failures:
            print(f"  · {f}")
        sys.exit(1)
    print(f"\033[32mAlle {total} Tests bestanden\033[0m")
