#!/usr/bin/env python3
"""
Pokemon Card Portfolio Tracker - Decodo Auto-Scraper
Scrapt Preise von Cardmarket via Decodo Web Scraping API (kein Browser noetig).

Usage:
    python3 scrape_decodo.py                  # Scrapt alle Karten aus portfolio.csv
    python3 scrape_decodo.py --dry-run        # Zeigt nur was gescrapt wuerde
    python3 scrape_decodo.py --single "URL"   # Scrapt eine einzelne URL

Env:
    DECODO_API_TOKEN     - Decodo API Token (alternativ aus DB settings)
    TELEGRAM_BOT_TOKEN   - Telegram Bot Token fuer Alerts
    TELEGRAM_CHAT_ID     - Telegram Chat ID fuer Alerts
"""

import csv
import json
import os
import re
import sys
import time
import hashlib
import logging
from datetime import datetime
from pathlib import Path

import requests

BASE_DIR = Path(__file__).parent
PORTFOLIO_FILE = BASE_DIR / "portfolio.csv"
PRICES_DIR = BASE_DIR / "prices"
LATEST_FILE = PRICES_DIR / "latest.json"
LOG_DIR = BASE_DIR / "data" / "logs"
RESUME_FILE = BASE_DIR / "data" / "scrape_resume.json"
LOG_DIR.mkdir(parents=True, exist_ok=True)

DECODO_ENDPOINT = "https://scraper-api.decodo.com/v2/scrape"

# Logging — stdout goes to live log panel in frontend
log_file = LOG_DIR / f"scrape_decodo_{datetime.now().strftime('%Y-%m-%d_%H%M')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("decodo")

# Error categories
ERR_CLOUDFLARE = "cloudflare_blocked"
ERR_NO_PRICES = "no_prices_extracted"
ERR_TIMEOUT = "timeout"
ERR_API_ERROR = "api_error"
ERR_CRASH = "exception"
STATUS_OK = "ok"


# ─── Config Loading ─────────────────────────────────────────

def load_decodo_token():
    token = os.environ.get("DECODO_API_TOKEN", "")
    if not token:
        try:
            db_path = BASE_DIR / "data" / "tracker.db"
            if db_path.exists():
                import sqlite3
                conn = sqlite3.connect(str(db_path))
                row = conn.execute("SELECT value FROM settings WHERE key = 'decodo_api_token'").fetchone()
                conn.close()
                if row and row[0]:
                    token = row[0]
        except Exception:
            pass
    return token


def load_telegram_config():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        try:
            db_path = BASE_DIR / "data" / "tracker.db"
            if db_path.exists():
                import sqlite3
                conn = sqlite3.connect(str(db_path))
                rows = conn.execute("SELECT key, value FROM settings WHERE key IN ('telegram_bot_token','telegram_chat_id')").fetchall()
                cfg = dict(rows)
                conn.close()
                token = token or cfg.get("telegram_bot_token", "")
                chat_id = chat_id or cfg.get("telegram_chat_id", "")
        except Exception:
            pass
    return token, chat_id


def send_telegram(message):
    token, chat_id = load_telegram_config()
    if not token or not chat_id:
        return False
    try:
        import urllib.request
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = json.dumps({"chat_id": chat_id, "text": message, "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception as e:
        log.warning(f"Telegram-Fehler: {e}")
        return False


# ─── Price Extraction (same as scrape.py) ──��────────────────

def parse_de_price(price_str):
    if not price_str:
        return None
    cleaned = price_str.replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def extract_prices(content):
    prices = {}
    patterns = {
        "trend": r"(?:Price Trend|Preis-Trend)[^€]*?([\d.,]+)\s*€",
        "avg30": r"(?:30-days average|30-Tages-Durchschnitt)[^€]*?([\d.,]+)\s*€",
        "avg7": r"(?:7-days average|7-Tages-Durchschnitt)[^€]*?([\d.,]+)\s*€",
        "avg1": r"(?:1-day average|1-Tages-Durchschnitt)[^€]*?([\d.,]+)\s*€",
        "from": r"(?:From|ab)[^€]*?([\d.,]+)\s*€",
    }
    for key, pattern in patterns.items():
        m = re.search(pattern, content, re.IGNORECASE)
        if m:
            prices[key] = parse_de_price(m.group(1))

    m = re.search(r"(\d+)\s*(?:items|Artikel)", content)
    if m:
        prices["available_items"] = int(m.group(1))

    for grade_key, grade_pattern in [
        ("psa10_low", r"PSA\s*10[^€]*?([\d.,]+)\s*€"),
        ("psa9_low", r"PSA\s*9(?!\d)[^€]*?([\d.,]+)\s*€"),
        ("cgc10_low", r"CGC\s*10[^€]*?([\d.,]+)\s*€"),
        ("bgs10_low", r"BGS\s*10[^€]*?([\d.,]+)\s*€"),
    ]:
        m = re.search(grade_pattern, content, re.IGNORECASE)
        if m:
            prices[grade_key] = parse_de_price(m.group(1))

    return prices


def extract_set_from_url(url):
    m = re.search(r"/Singles/([^/]+)/", url)
    if m:
        return m.group(1).replace("-", " ")
    m = re.search(r"/(?:Elite-Trainer-Boxes|Booster-Boxes|Booster-Packs|Tins|Collections|Special-Products)/([^/?]+)", url)
    if m:
        return m.group(1).replace("-", " ")
    return None


def extract_card_info(content):
    info = {}
    title_m = re.search(r"<title>(.*?)</title>", content)
    if title_m:
        title = title_m.group(1).replace(" | Cardmarket", "")
        if not any(x in title.lower() for x in ["just a moment", "cloudflare", "checking"]):
            info["page_title"] = title
    og_m = re.search(r'<meta[^>]+(?:property|name)=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', content)
    if not og_m:
        og_m = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', content)
    if og_m:
        img_url = og_m.group(1)
        if "product-images" in img_url.lower():
            info["image_url"] = img_url
    return info


# ─── Image Download (HTTP, no browser needed) ───────────────

def download_image(image_url, card_url):
    if not image_url:
        return None
    images_dir = BASE_DIR / "data" / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    url_hash = hashlib.md5(card_url.encode()).hexdigest()[:12]
    ext = ".png"
    filepath = images_dir / f"{url_hash}{ext}"

    if filepath.exists():
        return url_hash + ext

    try:
        resp = requests.get(image_url, timeout=30)
        if resp.status_code == 200 and len(resp.content) > 1000:
            filepath.write_bytes(resp.content)
            log.info(f"  Bild gespeichert: {filepath.name}")
            return url_hash + ext
        else:
            log.warning(f"  Bild-Download fehlgeschlagen: HTTP {resp.status_code}")
    except Exception as e:
        log.warning(f"  Bild-Download fehlgeschlagen: {e}")
    return None


# ─── Portfolio Loading ───────────────────────────────────────

def load_portfolio():
    cards = []
    with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            url = row.get("url", "").strip()
            if url:
                cards.append({
                    "url": url,
                    "name": row.get("name", "").strip(),
                    "grade": row.get("grade", "").strip().upper(),
                    "notes": row.get("notes", "").strip(),
                })
    return cards


# ─── Resume Support ──────────────────────────────────────────

def load_resume_state():
    if RESUME_FILE.exists():
        try:
            state = json.loads(RESUME_FILE.read_text())
            if state.get("date") == datetime.now().strftime("%Y-%m-%d"):
                return state
        except Exception:
            pass
    return None


def save_resume_state(completed_urls, results):
    state = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "completed_urls": list(completed_urls),
        "results_count": len(results),
        "timestamp": datetime.now().isoformat(),
    }
    RESUME_FILE.write_text(json.dumps(state))


def clear_resume_state():
    if RESUME_FILE.exists():
        RESUME_FILE.unlink()


# ─── Decodo API Call ─────────────────────────────────────────

def decodo_scrape(url, token, retries=3):
    """Scrapt eine URL via Decodo API mit Retry-Logik."""
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Basic {token}",
    }
    payload = {"url": url, "headless": "html", "geo": "de"}

    for attempt in range(retries):
        try:
            start = time.time()
            resp = requests.post(DECODO_ENDPOINT, headers=headers, json=payload, timeout=120)
            elapsed = time.time() - start

            if resp.status_code == 200:
                data = resp.json()
                results = data.get("results", [data])
                if results:
                    content = results[0].get("content", "")
                    inner_status = results[0].get("status_code", 200)
                    return content, inner_status, elapsed

            if resp.status_code == 429:
                wait = (2 ** attempt) * 5
                log.warning(f"  Rate limit (429), warte {wait}s... (Versuch {attempt + 1}/{retries})")
                time.sleep(wait)
                continue

            if resp.status_code >= 500:
                log.warning(f"  Server-Fehler ({resp.status_code}), retry in 5s...")
                time.sleep(5)
                continue

            log.error(f"  API-Fehler: HTTP {resp.status_code} — {resp.text[:200]}")
            return None, resp.status_code, time.time() - start

        except requests.Timeout:
            log.warning(f"  Timeout nach 120s (Versuch {attempt + 1}/{retries})")
            if attempt < retries - 1:
                time.sleep(3)
                continue
            return None, 0, 120.0

        except Exception as e:
            log.error(f"  Request-Fehler: {e}")
            return None, 0, 0.0

    return None, 0, 0.0


# ─── Main Scraper ────────────────────────────────────────────

def scrape_single_card(card, token, timestamp):
    """Scrapt eine einzelne Karte via Decodo API."""
    card_name = card["name"] or card["url"].split("/")[-1].split("?")[0]

    content, status_code, elapsed = decodo_scrape(card["url"], token)

    if content is None:
        log.error(f"  API fehlgeschlagen (status={status_code}, {elapsed:.1f}s)")
        return {
            "url": card["url"], "name": card["name"], "notes": card["notes"],
            "timestamp": timestamp, "error": ERR_API_ERROR,
        }, "errors"

    # Check for Cloudflare challenge in content
    if "cf-challenge" in content.lower() or ("just a moment" in content.lower() and len(content) < 5000):
        log.warning(f"  Cloudflare Challenge trotz Decodo! ({elapsed:.1f}s)")
        return {
            "url": card["url"], "name": card["name"], "notes": card["notes"],
            "timestamp": timestamp, "error": ERR_CLOUDFLARE,
        }, "cloudflare"

    log.info(f"  Decodo API: {status_code} ({elapsed:.1f}s)")

    prices = extract_prices(content)
    info = extract_card_info(content)

    grade = card.get("grade", "")
    grade_map = {"PSA10": "psa10_low", "PSA9": "psa9_low", "CGC10": "cgc10_low", "BGS10": "bgs10_low"}
    grade_key = grade_map.get(grade.replace(" ", ""))
    grade_value = prices.get(grade_key) if grade_key else None

    # Bild herunterladen (HTTP direkt, kein Browser noetig)
    image_file = download_image(info.get("image_url", ""), card["url"])

    if not prices or (not prices.get("trend") and not prices.get("from")):
        log.warning(f"  Keine Preise extrahiert! len={len(content)}")
        error = ERR_NO_PRICES
        status = "no_prices"
    else:
        error = None
        status = "ok"
        if grade_value:
            log.info(f"  {grade} Low: EUR {grade_value} | Trend: EUR {prices.get('trend')}")
        else:
            log.info(f"  Low: EUR {prices.get('from')} | Trend: EUR {prices.get('trend')} | 7d: EUR {prices.get('avg7')}")

    # Value-Bestimmung (gleiche Logik wie scrape.py)
    if grade_value:
        value = grade_value
    elif prices.get("from"):
        from_price = prices["from"]
        psa_prices = [p for p in [prices.get("psa10_low"), prices.get("psa9_low")] if p]
        if not grade and psa_prices and from_price >= min(psa_prices):
            log.info(f"  Nur graded Angebote (from={from_price} >= PSA low={min(psa_prices)}), nutze Trend")
            value = prices.get("trend") or from_price
        else:
            value = from_price
    else:
        value = prices.get("trend")

    result = {
        "url": card["url"],
        "name": card["name"] or info.get("page_title", ""),
        "set_name": extract_set_from_url(card["url"]),
        "grade": grade,
        "value": value,
        "notes": card["notes"],
        "image": image_file,
        "timestamp": timestamp,
        "error": error,
        **prices,
    }
    return result, status


def scrape_cards(cards, token):
    """Scrapt Preise fuer alle Karten mit Decodo API."""
    results = []
    stats = {"ok": 0, "cloudflare": 0, "no_prices": 0, "errors": 0, "skipped": 0}
    total = len(cards)
    timestamp = datetime.now().isoformat()

    # Resume
    resume_state = load_resume_state()
    completed_urls = set()
    if resume_state:
        completed_urls = set(resume_state.get("completed_urls", []))
        if completed_urls:
            log.info(f"Resume: {len(completed_urls)} Karten vom letzten Run ueberspringen")
            stats["skipped"] = len(completed_urls)

    remaining_cards = [c for c in cards if c["url"] not in completed_urls]
    if not remaining_cards:
        log.info("Alle Karten bereits gescrapt (Resume). Nichts zu tun.")
        return results, stats

    log.info(f"{len(remaining_cards)} Karten zu scrapen (von {total} gesamt)")

    for i, card in enumerate(remaining_cards):
        card_name = card["name"] or card["url"].split("/")[-1].split("?")[0]
        log.info(f"\n[{i + stats['skipped'] + 1}/{total}] {card_name}")

        try:
            result, status = scrape_single_card(card, token, timestamp)
            results.append(result)
            stats[status if status in stats else "errors"] += 1
            completed_urls.add(card["url"])

            save_resume_state(completed_urls, results)
            PRICES_DIR.mkdir(exist_ok=True)
            with open(LATEST_FILE, "w", encoding="utf-8") as f:
                json.dump(results, f, indent=2, ensure_ascii=False)

        except Exception as e:
            log.error(f"  FEHLER: {e}", exc_info=True)
            stats["errors"] += 1
            results.append({
                "url": card["url"], "name": card["name"], "notes": card["notes"],
                "timestamp": timestamp, "error": ERR_CRASH, "error_detail": str(e),
            })

    clear_resume_state()
    return results, stats


def save_results(results, stats):
    PRICES_DIR.mkdir(exist_ok=True)
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d_%H%M")

    csv_file = PRICES_DIR / f"{date_str}.csv"
    fieldnames = ["name", "grade", "value", "trend", "avg7", "avg30", "avg1", "from",
                   "psa10_low", "psa9_low", "cgc10_low", "bgs10_low",
                   "available_items", "notes", "image", "timestamp", "url", "error", "error_detail"]
    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)
    log.info(f"\nCSV gespeichert: {csv_file}")

    with open(LATEST_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    log.info(f"JSON gespeichert: {LATEST_FILE}")

    total_value = sum(r.get("value", 0) or 0 for r in results)

    summary = f"""
{'='*50}
DECODO SCRAPE ZUSAMMENFASSUNG ({now.strftime('%d.%m.%Y %H:%M')})
{'='*50}
Karten gesamt:   {len(results)} (+ {stats['skipped']} uebersprungen)
  Erfolgreich:   {stats['ok']}
  Keine Preise:  {stats['no_prices']}
  Cloudflare:    {stats['cloudflare']}
  API-Fehler:    {stats['errors']}
Portfolio-Wert:  EUR {total_value:,.2f}
Log-Datei:       {log_file}
{'='*50}"""
    log.info(summary)

    if stats["cloudflare"] > 0 or stats["errors"] > 0 or stats["no_prices"] > 0:
        problems = []
        if stats["cloudflare"]: problems.append(f"\u2601\ufe0f {stats['cloudflare']}x Cloudflare")
        if stats["no_prices"]: problems.append(f"\u26a0\ufe0f {stats['no_prices']}x keine Preise")
        if stats["errors"]: problems.append(f"\u274c {stats['errors']}x Fehler")
        failed_cards = [r["name"] or r["url"].split("/")[-1] for r in results if r.get("error")]
        send_telegram(
            f"\U0001f4ca <b>Decodo Scrape Report</b>\n\n"
            f"\u2705 {stats['ok']}/{len(results)} erfolgreich\n"
            f"{chr(10).join(problems)}\n\n"
            f"<b>Betroffene Karten:</b>\n"
            f"{chr(10).join(chr(8226) + ' ' + c for c in failed_cards[:10])}\n\n"
            f"Portfolio: EUR {total_value:,.2f}"
        )
    else:
        send_telegram(
            f"\u2705 <b>Decodo Scrape OK</b> \u2014 {stats['ok']}/{len(results)} Karten\n"
            f"Portfolio: EUR {total_value:,.2f}"
        )

    return csv_file


def main():
    args = sys.argv[1:]

    if "--dry-run" in args:
        cards = load_portfolio()
        print(f"{len(cards)} Karten in portfolio.csv:")
        for c in cards:
            print(f"  - {c['name'] or c['url']}")
        est_min = len(cards) * 12 / 60
        print(f"\nGeschaetzte Dauer: ~{est_min:.0f} Minuten (Decodo API, ~12s/Karte)")
        return

    token = load_decodo_token()
    if not token:
        log.error("Kein Decodo API Token gefunden! Bitte in Settings oder DECODO_API_TOKEN env setzen.")
        sys.exit(2)

    if "--single" in args:
        idx = args.index("--single")
        url = args[idx + 1] if idx + 1 < len(args) else None
        if not url:
            print("Fehler: --single braucht eine URL")
            sys.exit(1)
        cards = [{"url": url, "name": "", "grade": "", "notes": "single"}]
    else:
        cards = load_portfolio()
        if not cards:
            print("Keine Karten in portfolio.csv!")
            sys.exit(1)

    log.info(f"\n{len(cards)} Karten zu scrapen (Decodo API)...")
    log.info(f"Geschaetzte Dauer: ~{len(cards) * 12 / 60:.0f} Minuten")

    results, stats = scrape_cards(cards, token)
    save_results(results, stats)

    if stats["ok"] == 0 and len(cards) > 0:
        sys.exit(2)
    elif stats["cloudflare"] > 0 or stats["errors"] > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
