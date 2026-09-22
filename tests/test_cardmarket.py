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
import scrape_brightdata as sb
import versandkosten as vk
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
    # Der Standort entscheidet ueber den Versand — ohne ihn gilt Deutschland.
    orte = {c["origin"] for c in de}
    check("Standort je Angebot gelesen", all(c["origin"] for c in de), f"{orte}")
    check("Standorte sind bekannte Laender", all(vk.land_id(o) for o in orte), f"{orte}")

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

    # Gerechnet wird mit dem Gesamtpreis (Karte plus Versand).
    item = {"id": 1, "name": "X", "target_price": 30.0}
    snap = {"best_total": 28.0, "median_total": 40.0, "best_shipping": 7.61, "best_origin": "Österreich"}
    sig = wl.evaluate_buy(item, snap, None, 12)
    check("Zielpreis erreicht -> kaufen", sig is not None and "Zielpreis" in sig["detail"])
    check("Signal nennt den Versand", sig is not None and "7.61 € aus Österreich" in sig["detail"],
          sig["detail"] if sig else "")

    sig2 = wl.evaluate_buy(item, {"best_total": 35.0, "median_total": 36.0}, None, 12)
    check("über Zielpreis und nah am Mittelfeld -> kein Signal", sig2 is None)

    # Ohne Zielpreis zaehlt allein der Abstand zum Mittelfeld.
    frei = {"id": 2, "name": "Y", "target_price": None}
    sig3 = wl.evaluate_buy(frei, {"best_total": 30.0, "median_total": 40.0}, None, 12)
    check("ohne Zielpreis: deutlich unter Mittelfeld -> kaufen", sig3 is not None)
    sig4 = wl.evaluate_buy(frei, {"best_total": 38.0, "median_total": 40.0}, None, 12)
    check("ohne Zielpreis: nah am Mittelfeld -> kein Signal", sig4 is None)

    sig5 = wl.evaluate_buy(frei, {"best_total": 30.0, "median_total": 40.0},
                           {"best_total": 36.0}, 12)
    check("Preisrutsch seit dem letzten Blick wird erwähnt",
          sig5 is not None and "günstiger" in sig5["detail"])

    check("ohne Angebote kein Signal",
          wl.evaluate_buy(item, {"best_total": None, "median_total": None}, None, 12) is None)


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

    # Der Watchlist-Filter darf bei leerem Zustand nicht alles wegwerfen — und
    # ein Angebot ohne lesbare Sprache darf nicht durchrutschen.
    item = {"condition": "", "language": "en"}
    filt = lambda cs: [c for c in cs
                       if (not item["condition"] or c["condition"] == item["condition"])
                       and (not item["language"] or c.get("language") == item["language"])]
    check("Sealed-Filter behaelt alle passenden Angebote", len(filt(comp)) == len(comp))
    check("Angebot ohne Sprache faellt raus", filt([{"condition": "", "language": ""}]) == [])
    check("Median liegt in der Preisspanne",
          min(c["price"] for c in comp) <= wl.median([c["price"] for c in comp])
          <= max(c["price"] for c in comp))

    # Englische Seiten (/en/-Links) tragen englische Labels.
    en_block = ('<div id="articleRow1" class="row"><a href="/en/Users/x">x</a>'
                '<span class="article-condition condition-nm"></span>'
                '<span aria-label="German"></span>'
                '<span class="color-primary small text-end text-nowrap fw-bold">12,00 €</span></div>')
    en = cm.parse_competitors(en_block)
    check("englische Sprach-Labels werden gelesen", len(en) == 1 and en[0]["language"] == "de",
          str(en))
    # … und Markup-Varianten ohne aria-label.
    tt = cm.parse_competitors(en_block.replace('aria-label="German"', 'data-original-title="Englisch"'))
    mb = cm.parse_competitors(en_block.replace('<span aria-label="German"></span>',
                                               '<span onmouseover="showMsgBox(this,`Japanisch`)"></span>'))
    check("Sprache aus dem Tooltip", tt and tt[0]["language"] == "en", str(tt))
    check("Sprache aus dem onmouseover", mb and mb[0]["language"] == "ja", str(mb))
    ohne = cm.parse_competitors(en_block.replace('<span aria-label="German"></span>', ""))
    check("ohne Label bleibt die Sprache leer", ohne and ohne[0]["language"] == "", str(ohne))

    # Singles-Fixture: Sprache muss dort weiterhin stimmen (de-Filter)
    de = cm.parse_competitors(fixture("product_de_nm"))
    check("Singles: Sprache ebenfalls gelesen", {c["language"] for c in de} == {"de"},
          f"{ {c['language'] for c in de} }")


def test_versand():
    """Versand nach Hause aus Cardmarkets eigener Tabelle — Zielland Österreich."""
    print("\nVersandkosten")
    import json
    fest = json.loads(vk.FIXTURE.read_text(encoding="utf-8"))
    t = vk.Versandtabelle({int(k): v for k, v in fest["laender"].items()})

    k = lambda *a: t.kosten(*a)["preis"]
    # Ab 25 € ist Tracking Pflicht, und jede Versandart gilt nur bis zu ihrem
    # Hoechstwert — aus Deutschland heisst das Paket, keine Briefmarke.
    check("DE, 20 € Karte: Brief reicht", k("Deutschland", 20, "single") == 1.55, str(k("Deutschland", 20)))
    check("DE, 300 € Karte: DHL-Paket 15,49", k("Deutschland", 300, "single") == 15.49, str(k("Deutschland", 300)))
    check("DE, 849 € Karte: Wertpaket 32,49", k("Deutschland", 849, "single") == 32.49, str(k("Deutschland", 849)))
    check("AT, 300 € Karte: Paket 7,61", k("Österreich", 300, "single") == 7.61, str(k("Österreich", 300)))
    check("AT, 300 € Display: gleicher Paketpreis", k("Österreich", 300, "sealed") == 7.61,
          str(k("Österreich", 300, "sealed")))
    check("CH, 300 € Karte: 40,43", k("Schweiz", 300, "single") == 40.43, str(k("Schweiz", 300)))
    check("englischer Standort wird erkannt", k("Germany", 300, "single") == 15.49)
    check("englischer Standort wird deutsch benannt", vk.land_name("Germany") == "Deutschland"
          and vk.land_name("Vereinigtes Königreich") == "Großbritannien" and vk.land_name("") == "")
    ohne = t.kosten("", 300, "single")
    check("ohne Standort: Deutschland, als Schaetzung markiert",
          ohne["geschaetzt"] and ohne["land_id"] == 7 and ohne["preis"] == 15.49, str(ohne))
    # Cardmarket deckt bis 1.000.000 € ab (Kurier mit Vollversicherung) — erst
    # darueber bleibt nur der hoechste Eintrag als Naeherung.
    check("40.000 € Karte: Kurier, kein Schaetzwert",
          not t.kosten("Deutschland", 40000, "single")["geschaetzt"])
    hoch = t.kosten("Deutschland", 2_000_000, "single")
    check("ueber jeder Wertstufe: hoechster Eintrag, geschaetzt",
          hoch["geschaetzt"] and hoch["preis"] > 1000, str(hoch))
    check("Versandart hat Tracking", t.kosten("Deutschland", 300)["methode"].startswith("Registered"))

    # Einfuhrabgaben: bis 22.09.2026 suchte der Aufschlag nach title="Item location:",
    # das die Seite nicht mehr hat — jetzt gilt das aria-label, fuer alle Nicht-EU-Laender.
    ch = 'aria-label="Artikelstandort: Schweiz" data-bs-original-title="Artikelstandort: Schweiz"'
    de = 'aria-label="Artikelstandort: Deutschland"'
    uk = 'aria-label="Item location: United Kingdom"'
    check("Schweiz ueber 150 €: +22 %", sb._apply_import_uplift(300.0, ch) == (366.0, True))
    check("Schweiz unter 150 €: nichts (IOSS)", sb._apply_import_uplift(100.0, ch) == (100.0, False))
    check("Deutschland: kein Aufschlag", sb._apply_import_uplift(300.0, de) == (300.0, False))
    check("UK auf englischer Seite: +22 %", sb._apply_import_uplift(300.0, uk) == (366.0, True))
    check("Standort wird aus dem Block gelesen", sb.offer_location(ch) == "Schweiz")


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
    test_versand()
    test_vorschaubild()

    total = _passed + len(_failures)
    print(f"\n{'─' * 46}")
    if _failures:
        print(f"\033[31m{len(_failures)} von {total} fehlgeschlagen:\033[0m")
        for f in _failures:
            print(f"  · {f}")
        sys.exit(1)
    print(f"\033[32mAlle {total} Tests bestanden\033[0m")
