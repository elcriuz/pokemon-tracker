#!/usr/bin/env python3
"""Tests fuer die Kaufseite: Produktseiten lesen und Kaufsignale ableiten.

Laeuft komplett offline gegen gespeicherte Seiten in tests/fixtures/ — kostet also
keine Bright-Data-Abrufe und bleibt auch dann gruen, wenn Cardmarket gerade zickt.
Bricht das Layout, schlagen die Parser-Tests fehl, statt still falsche Zahlen zu
schreiben.

Bis 17.09.2026 standen hier zusaetzlich die Verkaeuferseite (eigene Angebote,
Wettbewerbsrang, Umpreisen) und der eingeloggte Bereich. Beides liegt jetzt bei
TCG PowerTools; die zugehoerigen Tests sind mit dem Code entfallen.

  python3 tests/test_cardmarket.py
"""
from __future__ import annotations

import gzip
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FIXTURES = Path(__file__).resolve().parent / "fixtures"

import cardmarket_public as cm
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


# ---------------------------------------------------------- Produktseiten

def test_produktseite():
    print("\nProduktseite lesen")
    de = cm.parse_competitors(fixture("product_de_nm"))

    check("gefilterte Seite liefert Angebote", len(de) >= 20, f"{len(de)}")
    check("Verkaeufer werden gelesen", all(c["seller"] for c in de))
    check("Preise sind Zahlen", all(isinstance(c["price"], float) for c in de))

    # Der Filter ist der Kern: ungefiltert zeigt eine Produktseite die 50
    # guenstigsten Angebote ueber ALLE Sprachen — bei einer gefragten Karte
    # sind das schnell 50x italienisch, und der Vergleich ist wertlos.
    check("Sprach-ID Deutsch", "language=3" in cm.build_url("u", "NM", "de"))
    check("Sprach-ID Japanisch", "language=7" in cm.build_url("u", "NM", "ja"))
    check("Zustand NM", "minCondition=2" in cm.build_url("u", "NM", "de"))
    check("ohne Sprache kein Sprachfilter", "language=" not in cm.build_url("u", "NM", ""))

    # Marktdaten muessen aus derselben Seite kommen, sonst kostet jede Karte
    # einen zweiten Abruf.
    m = cm.extract_prices(fixture("product_de_nm"))
    check("Marktdaten aus gefilterter Seite",
          all(m.get(k) for k in ("trend", "avg7", "avg30")), str(m)[:80])


# ------------------------------------------------------------- Wunschliste

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


def test_sealed():
    """Displays und Booster haben keinen Zustand — nur Sprache und Preis."""
    print("\nSealed-Produkte (Displays)")
    html = fixture("product_sealed_en")
    comp = cm.parse_competitors(html)
    check("Angebote werden geparst", len(comp) >= 20, f"{len(comp)}")
    check("kein Zustand bei Sealed", all(c["condition"] == "" for c in comp))
    langs = {c["language"] for c in comp}
    check("Sprache wird je Angebot gelesen", langs and "" not in langs, f"{langs}")
    check("?language=1 liefert nur englische Angebote", langs == {"en"}, f"{langs}")
    m = cm.extract_prices(html)
    check("Marktdaten auch bei Sealed", all(m.get(k) for k in ("trend", "avg7", "avg30")),
          str(m)[:80])

    # Der Watchlist-Filter darf bei leerem Zustand nicht alles wegwerfen.
    item = {"condition": "", "language": "en"}
    passend = [c for c in comp
               if (not item["condition"] or c["condition"] == item["condition"])
               and (not item["language"] or not c.get("language")
                    or c["language"] == item["language"])]
    check("Sealed-Filter behaelt alle passenden Angebote", len(passend) == len(comp))
    check("Median liegt in der Preisspanne",
          min(c["price"] for c in comp) <= wl.median([c["price"] for c in comp])
          <= max(c["price"] for c in comp))

    # Singles-Fixture: Sprache muss dort weiterhin stimmen (de-Filter)
    de = cm.parse_competitors(fixture("product_de_nm"))
    check("Singles: Sprache ebenfalls gelesen", {c["language"] for c in de} == {"de"},
          f"{ {c['language'] for c in de} }")


def test_vorschaubild():
    """Das Bild der Wunschliste muss zum Produkt gehören, nicht zum Nachbarn."""
    print("\nVorschaubilder")
    from scrape_brightdata import extract_card_info

    # Kartenseiten zeigen im Bildbereich eine Slideshow, die mit der *vorherigen*
    # Karte der Edition beginnt (869859). Richtig ist das og:image (869860).
    single = extract_card_info(fixture("product_de_nm")).get("image_url", "")
    check("Single: Bild der Karte selbst", single.endswith("/869860.jpg"), single)

    # Sealed trägt im og:image nur das Cardmarket-Logo.
    sealed = extract_card_info(fixture("product_sealed_en")).get("image_url", "")
    check("Sealed: Produktbild statt Logo", sealed.endswith("/885552.jpg"), sealed)


if __name__ == "__main__":
    print("\033[1mCardmarket — Kaufseite\033[0m")
    test_produktseite()
    test_watchlist()
    test_sealed()
    test_vorschaubild()

    total = _passed + len(_failures)
    print(f"\n{'─' * 46}")
    if _failures:
        print(f"\033[31m{len(_failures)} von {total} fehlgeschlagen:\033[0m")
        for f in _failures:
            print(f"  · {f}")
        sys.exit(1)
    print(f"\033[32mAlle {total} Tests bestanden\033[0m")
