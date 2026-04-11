#!/usr/bin/env python3
"""
Pokemon Card Portfolio Tracker - HTTP Cookie Scraper (v2)
Holt Preise via HTTP-Requests mit CF-Cookies statt Browser-Navigation.

Flow:
1. Cookies aus data/cf_cookies.json laden
2. Falls abgelaufen/fehlend: Patchright oeffnet Cardmarket, holt neue Cookies
3. HTTP-Requests fuer jede Karte (~0.5s statt ~6s)

Usage:
    python3 scrape_http.py                  # Scrapt alle Karten
    python3 scrape_http.py --single "URL"   # Einzelne Karte
    python3 scrape_http.py --refresh-cookies # Nur Cookies erneuern
"""

import csv
import json
import os
import re
import sys
import time
import random
import logging
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

BASE_DIR = Path(__file__).parent
PORTFOLIO_FILE = BASE_DIR / "portfolio.csv"
PRICES_DIR = BASE_DIR / "prices"
LATEST_FILE = PRICES_DIR / "latest.json"
COOKIE_FILE = BASE_DIR / "data" / "cf_cookies.json"
LOG_DIR = BASE_DIR / "data" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "50"))
REQUEST_DELAY_MIN = 0.5
REQUEST_DELAY_MAX = 1.5
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"

log_file = LOG_DIR / f"scrape_http_{datetime.now().strftime('%Y-%m-%d_%H%M')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("scraper_http")


# ─── Cookie Management ──────────────────────────────────────

def load_cookies():
    """Laedt gespeicherte Cookies von Disk."""
    if COOKIE_FILE.exists():
        try:
            data = json.loads(COOKIE_FILE.read_text())
            age = time.time() - data.get("timestamp", 0)
            if age < 7200:  # Max 2 Stunden
                log.info(f"Cookies geladen (Alter: {int(age/60)}min)")
                return data["cookies"]
            log.info("Cookies abgelaufen")
        except Exception:
            pass
    return None


def save_cookies(cookies):
    """Speichert Cookies auf Disk."""
    COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
    COOKIE_FILE.write_text(json.dumps({
        "cookies": cookies,
        "timestamp": time.time(),
    }))
    log.info(f"Cookies gespeichert ({len(cookies)} Cookies)")


def refresh_cookies():
    """Oeffnet Patchright Browser, laedt Cardmarket, extrahiert Cookies."""
    from patchright.sync_api import sync_playwright

    log.info("Hole neue Cookies via Patchright...")
    display = os.environ.get("DISPLAY", ":99")
    os.environ["DISPLAY"] = display

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(BASE_DIR / "data" / "patchright-profile"),
            channel="chrome", headless=False, no_viewport=True,
            args=["--window-size=1280,900", "--window-position=0,0", "--no-first-run",
                  "--disable-session-crashed-bubble", "--disable-dev-shm-usage"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://www.cardmarket.com/en/Pokemon", wait_until="domcontentloaded")
        time.sleep(8)

        # Wait for CF to resolve
        for _ in range(20):
            title = page.title()
            if "moment" not in title.lower():
                break
            time.sleep(3)

        cookies_raw = ctx.cookies()
        cookies = {c["name"]: c["value"] for c in cookies_raw if "cardmarket" in c.get("domain", "")}
        ctx.close()

    if "cf_clearance" in cookies:
        save_cookies(cookies)
        log.info("Cookies erfolgreich geholt!")
        return cookies
    else:
        log.error("Keine cf_clearance Cookie erhalten!")
        return None


def get_cookie_string(cookies):
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


# ─── HTTP Fetching ───────────────────────────────────────────

def fetch_page(url, cookies):
    """Holt eine Cardmarket-Seite via HTTP mit Cookies."""
    req = Request(url, headers={
        "Cookie": get_cookie_string(cookies),
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,de;q=0.8",
    })
    resp = urlopen(req, timeout=15)
    return resp.read().decode("utf-8")


# ─── Price Extraction (same as scrape.py) ─────────────────────

def parse_de_price(price_str):
    if not price_str: return None
    cleaned = price_str.replace(".", "").replace(",", ".")
    try: return float(cleaned)
    except ValueError: return None


def extract_prices(content):
    prices = {}
    for key, pattern in {
        "trend": r"(?:Price Trend|Preis-Trend)[^€]*?([\d.,]+)\s*€",
        "avg30": r"(?:30-days average|30-Tages-Durchschnitt)[^€]*?([\d.,]+)\s*€",
        "avg7": r"(?:7-days average|7-Tages-Durchschnitt)[^€]*?([\d.,]+)\s*€",
        "avg1": r"(?:1-day average|1-Tages-Durchschnitt)[^€]*?([\d.,]+)\s*€",
        "from": r"(?:From|ab)[^€]*?([\d.,]+)\s*€",
    }.items():
        m = re.search(pattern, content, re.IGNORECASE)
        if m: prices[key] = parse_de_price(m.group(1))

    m = re.search(r"(\d+)\s*(?:items|Artikel)", content)
    if m: prices["available_items"] = int(m.group(1))

    for gk, gp in [("psa10_low", r"PSA\s*10[^€]*?([\d.,]+)\s*€"), ("psa9_low", r"PSA\s*9(?!\d)[^€]*?([\d.,]+)\s*€"),
                    ("cgc10_low", r"CGC\s*10[^€]*?([\d.,]+)\s*€"), ("bgs10_low", r"BGS\s*10[^€]*?([\d.,]+)\s*€")]:
        m = re.search(gp, content, re.IGNORECASE)
        if m: prices[gk] = parse_de_price(m.group(1))
    return prices


def extract_card_info(content):
    info = {}
    m = re.search(r"<title>(.*?)</title>", content)
    if m:
        title = m.group(1).replace(" | Cardmarket", "")
        if not any(x in title.lower() for x in ["just a moment", "cloudflare"]): info["page_title"] = title
    m = re.search(r'og:image.*?content=["\']([^"\']+product-images[^"\']+)', content)
    if m: info["image_url"] = m.group(1)
    return info


def extract_set_from_url(url):
    m = re.search(r"/Singles/([^/]+)/", url)
    if m: return m.group(1).replace("-", " ")
    m = re.search(r"/(?:Elite-Trainer-Boxes|Booster-Boxes|Booster-Packs|Tins|Collections|Special-Products)/([^/?]+)", url)
    if m: return m.group(1).replace("-", " ")
    return None


# ─── Portfolio ───────────────────────────────────────────────

def load_portfolio():
    cards = []
    with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            url = row.get("url", "").strip()
            if url:
                cards.append({"url": url, "name": row.get("name", "").strip(),
                              "grade": row.get("grade", "").strip().upper(), "notes": row.get("notes", "").strip()})
    return cards


# ─── Main ────────────────────────────────────────────────────

def scrape_all(cards, cookies):
    results = []
    stats = {"ok": 0, "cf": 0, "errors": 0}
    total = len(cards)
    timestamp = datetime.now().isoformat()

    for i, card in enumerate(cards):
        card_name = card["name"] or card["url"].split("/")[-1].split("?")[0]
        log.info(f"[{i+1}/{total}] {card_name}")

        try:
            html = fetch_page(card["url"], cookies)

            # CF check
            if "Just a moment" in html or len(html) < 5000:
                log.warning(f"  CF blockiert! Cookies abgelaufen?")
                stats["cf"] += 1
                # Try refreshing cookies once
                if stats["cf"] == 1:
                    log.info("  Versuche Cookies zu erneuern...")
                    new_cookies = refresh_cookies()
                    if new_cookies:
                        cookies = new_cookies
                        html = fetch_page(card["url"], cookies)
                        if "Just a moment" in html:
                            results.append({"url": card["url"], "name": card["name"], "timestamp": timestamp, "error": "cloudflare"})
                            continue
                    else:
                        results.append({"url": card["url"], "name": card["name"], "timestamp": timestamp, "error": "cloudflare"})
                        continue
                else:
                    results.append({"url": card["url"], "name": card["name"], "timestamp": timestamp, "error": "cloudflare"})
                    continue

            prices = extract_prices(html)
            info = extract_card_info(html)
            grade = card.get("grade", "")
            grade_map = {"PSA10": "psa10_low", "PSA9": "psa9_low", "CGC10": "cgc10_low", "BGS10": "bgs10_low"}
            grade_key = grade_map.get(grade.replace(" ", ""))
            grade_value = prices.get(grade_key) if grade_key else None

            if not prices or (not prices.get("trend") and not prices.get("from")):
                log.warning(f"  Keine Preise!")
                stats["errors"] += 1
                error = "no_prices"
            else:
                stats["ok"] += 1
                error = None
                # Smart value (same logic as scrape.py)
                if grade_value:
                    value = grade_value
                elif prices.get("from"):
                    from_price = prices["from"]
                    psa_prices = [p for p in [prices.get("psa10_low"), prices.get("psa9_low")] if p]
                    if not grade and psa_prices and from_price >= min(psa_prices):
                        value = prices.get("trend") or from_price
                    else:
                        value = from_price
                else:
                    value = prices.get("trend")
                log.info(f"  Value: EUR {value}, Trend: EUR {prices.get('trend')}, Low: EUR {prices.get('from')}")

            result = {
                "url": card["url"],
                "name": card["name"] or info.get("page_title", ""),
                "set_name": extract_set_from_url(card["url"]),
                "grade": grade, "value": value if error is None else None,
                "notes": card["notes"], "timestamp": timestamp, "error": error, **prices,
            }
            results.append(result)

            # Write latest.json after each card (live updates)
            PRICES_DIR.mkdir(exist_ok=True)
            with open(LATEST_FILE, "w", encoding="utf-8") as f:
                json.dump(results, f, indent=2, ensure_ascii=False)

        except HTTPError as e:
            if e.code == 403:
                log.warning(f"  403 Forbidden — Cookies abgelaufen")
                stats["cf"] += 1
                if stats["cf"] == 1:
                    cookies = refresh_cookies()
                    if cookies: continue
            else:
                log.error(f"  HTTP {e.code}: {e.reason}")
                stats["errors"] += 1
            results.append({"url": card["url"], "name": card["name"], "timestamp": timestamp, "error": str(e)})
        except Exception as e:
            log.error(f"  FEHLER: {e}")
            stats["errors"] += 1
            results.append({"url": card["url"], "name": card["name"], "timestamp": timestamp, "error": str(e)})

        # Delay between requests
        time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))

    return results, stats, cookies


def main():
    args = sys.argv[1:]

    if "--refresh-cookies" in args:
        refresh_cookies()
        return

    if "--single" in args:
        idx = args.index("--single")
        url = args[idx + 1] if idx + 1 < len(args) else None
        if not url: print("Fehler: --single braucht URL"); sys.exit(1)
        cards = [{"url": url, "name": "", "grade": "", "notes": ""}]
    else:
        cards = load_portfolio()
        if not cards: print("Keine Karten!"); sys.exit(1)

    log.info(f"\n{len(cards)} Karten via HTTP-Scraper...")
    log.info(f"Geschaetzte Dauer: ~{len(cards) * 1 / 60:.0f} Minuten")

    # Get cookies
    cookies = load_cookies()
    if not cookies:
        cookies = refresh_cookies()
        if not cookies:
            log.error("Konnte keine Cookies holen!")
            sys.exit(2)

    results, stats, cookies = scrape_all(cards, cookies)

    # Save
    PRICES_DIR.mkdir(exist_ok=True)
    now = datetime.now()
    csv_file = PRICES_DIR / f"{now.strftime('%Y-%m-%d_%H%M')}_http.csv"
    fieldnames = ["name", "grade", "value", "trend", "avg7", "avg30", "avg1", "from",
                   "psa10_low", "psa9_low", "cgc10_low", "bgs10_low",
                   "available_items", "notes", "set_name", "timestamp", "url", "error"]
    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)

    with open(LATEST_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    total_value = sum(r.get("value", 0) or 0 for r in results)
    log.info(f"\n{'='*50}")
    log.info(f"HTTP SCRAPE ({now.strftime('%d.%m.%Y %H:%M')})")
    log.info(f"{'='*50}")
    log.info(f"OK: {stats['ok']} | CF: {stats['cf']} | Fehler: {stats['errors']}")
    log.info(f"Portfolio: EUR {total_value:,.2f}")
    log.info(f"{'='*50}")


if __name__ == "__main__":
    main()
