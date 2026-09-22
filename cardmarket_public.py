#!/usr/bin/env python3
"""Gemeinsame Bausteine fuer oeffentliche Cardmarket-Abrufe ueber Bright Data.

Bis 17.09.2026 hiess die Datei scrape_competition.py und bewertete zusaetzlich
die eigene Wettbewerbsposition. Der Verkaeuferbereich liegt inzwischen bei
TCG PowerTools, das als offizieller Cardmarket-API-Partner arbeitet — geblieben
ist die Kaufseite: die Wunschliste holt sich hierueber ihre Produktseiten.

Der Kern ist der Filter: Eine Produktseite ohne Filter zeigt die 50 guenstigsten
Angebote ueber ALLE Sprachen und Zustaende. Bei einer Karte mit 300 Angeboten waren
das 50x italienisch — ein Vergleich dagegen ist wertlos, wenn man selbst eine
deutsche NM-Karte sucht. Erst mit ?language=&minCondition= wird er aussagekraeftig.

Kein eigener Einstiegspunkt: importiert wird das hier von watchlist.py.
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "tracker.db"

# Ein Abruf dauert ueber Bright Data rund eine Minute — deshalb parallel.
# Die DB bleibt einthreadig, es werden nur die Seiten nebenlaeufig geholt.
MAX_PARALLEL = 8

log = logging.getLogger("cardmarket")

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
# Sprache des Angebots (Sprach-Icon). Bei Sealed gibt es keinen Zustand — dort ist
# die Sprache das einzige Merkmal, nach dem verglichen werden kann. Auf /en/-Seiten
# heissen die Labels englisch — ohne sie blieb die Sprache dort leer, und der
# Sprachfilter liess alles durch.
LANG_LABELS = {"Deutsch": "de", "Englisch": "en", "Französisch": "fr", "Spanisch": "es",
               "Italienisch": "it", "Japanisch": "ja", "Chinesisch": "zh",
               "Portugiesisch": "pt", "Russisch": "ru", "Koreanisch": "ko",
               "German": "de", "English": "en", "French": "fr", "Spanish": "es",
               "Italian": "it", "Japanese": "ja", "S-Chinese": "zh", "T-Chinese": "zh-t",
               "Portuguese": "pt", "Russian": "ru", "Korean": "ko"}
# Das Sprach-Icon traegt sein Label mal im aria-label, mal nur im Tooltip oder im
# onmouseover — Cardmarket liefert nicht bei jedem Abruf dasselbe Markup. Am 19.09.
# und 22.09.2026 blieb die Sprache so bei einzelnen Abrufen leer.
_LANG_ALT = "|".join(map(re.escape, LANG_LABELS))
LANG_RE = re.compile(r'(?:aria-label|data-original-title|data-bs-original-title)="(' + _LANG_ALT + r')"'
                     r'|showMsgBox\(this,`(' + _LANG_ALT + r')`\)')
COMMENT_RE = re.compile(r'fst-italic small">([^<]+)</span>')
# Verkaufszahl des Anbieters (Badge „159 Verkäufe"). Ein frisches Konto mit einem
# Preis weit unter dem Markt ist auf Cardmarket fast immer Betrug.
SALES_RE = re.compile(r'sell-count"[^>]*>\s*(\d+)\s*<')
ROW_SPLIT_RE = re.compile(r'<div id="articleRow\d+"')
# Herkunftsland des Angebots — davon haengt ab, was der Versand nach Hause kostet.
from versandkosten import STANDORT_RE  # noqa: E402

# Die aufwendig gepflegten Filter des Preis-Scrapers mitbenutzen statt neu bauen:
# "nur Huelle", "ohne Karte", graded-Kommentare und UK-Einfuhraufschlag.
try:
    from scrape_brightdata import (BAD_LISTING_RE, _apply_import_uplift,
                                   _comment_is_graded, extract_prices)
except Exception:  # pragma: no cover - Fallback, falls sich das Modul aendert
    BAD_LISTING_RE = None
    _comment_is_graded = lambda c: False
    _apply_import_uplift = lambda p, b: (p, False)
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

        price, _ = _apply_import_uplift(price, block)
        sm = SELLER_RE.search(block)
        cond = COND_RE.search(block)
        lm = LANG_RE.search(block)
        om = STANDORT_RE.search(block)
        vm = SALES_RE.search(block)
        out.append({
            "price": price,
            "seller": sm.group(1) if sm else "",
            "condition": cond.group(1).upper() if cond else "",
            "language": LANG_LABELS.get(lm.group(1) or lm.group(2), "") if lm else "",
            "origin": om.group(1).strip() if om else "",
            "sales": int(vm.group(1)) if vm else None,
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
