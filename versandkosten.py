#!/usr/bin/env python3
"""Was der Versand einer Cardmarket-Bestellung nach Hause kostet.

Die Produktseite zeigt keine Versandkosten, nur den Artikelstandort. Die
Versandarten samt Preisen legt aber Cardmarket selbst je Länderpaar fest
(help.cardmarket.com/de/ShippingCosts) — der Verkäufer wählt nur daraus.
Zwei Regeln bestimmen, was an der Kasse überhaupt zur Wahl steht:

  - ab 25 € Bestellwert ist Versand mit Sendungsverfolgung Pflicht
  - eine Versandart gilt nur bis zu ihrem Höchstwert („Max. Bestellwert")

Geschätzt wird darum die günstigste Versandart, die den Warenwert abdeckt.
Ob der Verkäufer genau die anbietet, weiß nur der Warenkorb — näher kommt
man ohne Anmeldung nicht. Von Deutschland nach Österreich heißt das etwa:
bis 500 € DHL-Paket 15,49 €, bis 1.000 € Wertpaket 32,49 €, aus Österreich
selbst 7,61 €.

Die Tabelle kommt von help.cardmarket.com und liegt 30 Tage in data/.
Ohne Netz gilt die eingefrorene Kopie unter tests/fixtures/.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CACHE_DIR = ROOT / "data"
FIXTURE = ROOT / "tests" / "fixtures" / "versandkosten_at.json"

log = logging.getLogger("versand")

# Länder-IDs von Cardmarket (Auswahlliste auf help.cardmarket.com, 22.09.2026).
# Die Produktseite nennt Großbritannien „Vereinigtes Königreich"; englische
# Seiten (/en/) tragen englische Namen — alles führt zur selben ID.
LAENDER = {
    "Österreich": 1, "Austria": 1,
    "Belgien": 2, "Belgium": 2,
    "Bulgarien": 3, "Bulgaria": 3,
    "Schweiz": 4, "Switzerland": 4,
    "Zypern": 5, "Cyprus": 5,
    "Tschechien": 6, "Tschechische Republik": 6, "Czech Republic": 6, "Czechia": 6,
    "Deutschland": 7, "Germany": 7,
    "Dänemark": 8, "Denmark": 8,
    "Estland": 9, "Estonia": 9,
    "Spanien": 10, "Spain": 10,
    "Finnland": 11, "Finland": 11,
    "Frankreich": 12, "France": 12,
    "Großbritannien": 13, "Vereinigtes Königreich": 13, "United Kingdom": 13, "Great Britain": 13,
    "Griechenland": 14, "Greece": 14,
    "Ungarn": 15, "Hungary": 15,
    "Irland": 16, "Ireland": 16,
    "Italien": 17, "Italy": 17,
    "Liechtenstein": 18,
    "Litauen": 19, "Lithuania": 19,
    "Luxemburg": 20, "Luxembourg": 20,
    "Lettland": 21, "Latvia": 21,
    "Malta": 22,
    "Niederlande": 23, "Netherlands": 23,
    "Norwegen": 24, "Norway": 24,
    "Polen": 25, "Poland": 25,
    "Portugal": 26,
    "Rumänien": 27, "Romania": 27,
    "Schweden": 28, "Sweden": 28,
    "Singapur": 29, "Singapore": 29,
    "Slowenien": 30, "Slovenia": 30,
    "Slowakei": 31, "Slovakia": 31,
    "Kroatien": 35, "Croatia": 35,
    "Japan": 36,
    "Island": 37, "Iceland": 37,
}
# Außerhalb der EU-Zollunion: dort kommt über 150 € Warenwert die
# Einfuhrumsatzsteuer dazu (siehe scrape_brightdata._apply_import_uplift).
NICHT_EU = {4, 13, 18, 24, 29, 36, 37}

# Standort eines Angebots auf der Produktseite (aria-label bzw. Tooltip).
STANDORT_RE = re.compile(r'(?:Artikelstandort|Item location):\s*([^"<]+?)\s*"')

# Was so eine Sendung wiegt. Cardmarket rechnet für Briefe bis 20 g mit vier
# Karten — eine Karte im Toploader bleibt darunter. Ein Display im Karton
# liegt unter einem Kilo.
GEWICHT_G = {"single": 20, "sealed": 1000}
TRACKING_AB_EUR = 25.0
FALLBACK_LAND = 7  # Deutschland: das häufigste Herkunftsland, wenn der Standort fehlt

# Ohne Browser-Kennung antwortet help.cardmarket.com mit 403.
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def parse_eur(s: str) -> float:
    return float(s.replace("€", "").replace(".", "").replace(",", ".").strip())


def land_id(standort: str | None) -> int | None:
    return LAENDER.get((standort or "").strip())


def ist_nicht_eu(standort: str | None) -> bool:
    return land_id(standort) in NICHT_EU


def _hole(von: int, nach: int) -> list[dict]:
    url = (f"https://help.cardmarket.com/api/shippingCosts?locale=de"
           f"&fromCountry={von}&toCountry={nach}&preview=false")
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        rows = json.load(r)
    # „Virtual Delivery" ist kein Versand; Cardmarket führt ihn mit 0 g.
    return [{"name": m["name"], "tracked": bool(m["isTracked"]),
             "max_wert": parse_eur(m["maxValue"]), "max_gewicht": int(m["maxWeight"]),
             "preis": parse_eur(m["price"])}
            for m in rows if not m.get("isVirtual") and int(m["maxWeight"]) > 0]


def _laender(daten: dict) -> dict[int, list[dict]]:
    return {int(k): v for k, v in daten["laender"].items()}


def lade_tabelle(ziel: int = 1, max_alter_tage: int = 30) -> dict[int, list[dict]]:
    """Versandarten aller Herkunftsländer ins Zielland, mit Zwischenspeicher."""
    cache = CACHE_DIR / f"versandkosten_{ziel}.json"
    alt = None
    if cache.exists():
        try:
            alt = json.loads(cache.read_text(encoding="utf-8"))
            stand = datetime.fromisoformat(alt["stand"])
            if datetime.now() - stand < timedelta(days=max_alter_tage):
                return _laender(alt)
        except Exception as e:
            log.warning("Versand-Cache unlesbar: %s", e)
            alt = None

    try:
        laender = {lid: _hole(lid, ziel) for lid in sorted(set(LAENDER.values()))}
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"stand": datetime.now().isoformat(timespec="seconds"),
                                     "ziel": ziel, "laender": laender}, ensure_ascii=False),
                         encoding="utf-8")
        log.info("Versandtabelle von Cardmarket geholt (%d Länder → Ziel %d)", len(laender), ziel)
        return laender
    except Exception as e:
        log.warning("Versandtabelle nicht abrufbar (%s) — nehme die letzte bekannte", e)

    if alt:
        return _laender(alt)
    fest = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if fest.get("ziel") != ziel:
        log.warning("Eingefrorene Tabelle gilt für Ziel %s, nicht %s", fest.get("ziel"), ziel)
    return _laender(fest)


class Versandtabelle:
    def __init__(self, laender: dict[int, list[dict]]):
        self.laender = laender

    def kosten(self, standort: str | None, wert: float, art: str = "single") -> dict:
        """Günstigste Versandart, die den Warenwert abdeckt.

        Liefert preis, methode, land_id und geschaetzt — True, wenn der Standort
        fehlte (dann gilt Deutschland) oder keine Versandart den Wert abdeckt
        (dann der höchste Eintrag des Landes).
        """
        lid = land_id(standort)
        geschaetzt = lid is None or lid not in self.laender
        if geschaetzt:
            lid = FALLBACK_LAND
        alle = self.laender.get(lid, [])
        if not alle:
            return {"preis": 0.0, "methode": "unbekannt", "land_id": lid, "geschaetzt": True}

        gewicht = GEWICHT_G.get(art, GEWICHT_G["single"])
        passend = [m for m in alle
                   if m["max_wert"] >= wert and m["max_gewicht"] >= gewicht
                   and (m["tracked"] or wert <= TRACKING_AB_EUR)]
        if passend:
            best = min(passend, key=lambda m: m["preis"])
            return {"preis": best["preis"], "methode": best["name"], "land_id": lid,
                    "geschaetzt": geschaetzt}
        top = max(alle, key=lambda m: (m["max_wert"], -m["preis"]))
        return {"preis": top["preis"], "methode": top["name"], "land_id": lid, "geschaetzt": True}
