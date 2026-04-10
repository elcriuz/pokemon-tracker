#!/usr/bin/env python3
"""
Pokemon Card Portfolio Tracker - Cardmarket Scraper
Scrapt Preise von Cardmarket via Patchright (Cloudflare-Bypass).

Usage:
    python3 scrape.py                  # Scrapt alle Karten aus portfolio.csv
    python3 scrape.py --dry-run        # Zeigt nur was gescrapt wuerde
    python3 scrape.py --single "URL"   # Scrapt eine einzelne URL

Env:
    TELEGRAM_BOT_TOKEN   - Telegram Bot Token fuer Alerts
    TELEGRAM_CHAT_ID     - Telegram Chat ID fuer Alerts
    BATCH_SIZE           - Chrome-Neustart alle N Karten (default: 50)
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

BASE_DIR = Path(__file__).parent
PORTFOLIO_FILE = BASE_DIR / "portfolio.csv"
PRICES_DIR = BASE_DIR / "prices"
LATEST_FILE = PRICES_DIR / "latest.json"
LOG_DIR = BASE_DIR / "data" / "logs"
RESUME_FILE = BASE_DIR / "data" / "scrape_resume.json"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Timing
FIRST_WAIT = 5
MIN_DELAY = 4
MAX_DELAY = 8
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "50"))

# Logging
log_file = LOG_DIR / f"scrape_{datetime.now().strftime('%Y-%m-%d_%H%M')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("scraper")

# Error categories
ERR_CLOUDFLARE = "cloudflare_blocked"
ERR_NO_PRICES = "no_prices_extracted"
ERR_CRASH = "exception"
STATUS_OK = "ok"


# ─── Telegram ───────────────────────────────────────────────

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


# ─── Price Extraction ────────────────────────────────────────

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
    """Extrahiert den Set-Namen aus der Cardmarket-URL."""
    # Singles: /Singles/Lost-Abyss/Giratina-V-V3-s11111
    # Sealed: /Elite-Trainer-Boxes/Ascended-Heroes-Elite-Trainer-Box
    m = re.search(r"/Singles/([^/]+)/", url)
    if m:
        return m.group(1).replace("-", " ")
    # Sealed products: extract from product name
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
        # Only use product images from S3, skip logos/placeholders
        if "product-images" in img_url.lower():
            info["image_url"] = img_url
    return info


# ─── Image Download ──────────────────────────────────────────

def download_image(image_url, card_url, page=None):
    import hashlib
    images_dir = BASE_DIR / "data" / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    url_hash = hashlib.md5(card_url.encode()).hexdigest()[:12]
    ext = ".png"
    filepath = images_dir / f"{url_hash}{ext}"

    if filepath.exists():
        return url_hash + ext
    if not page:
        return None

    try:
        # Strategy 1: Screenshot the product image on the current page (no navigation)
        product_img = page.query_selector("img[src*='product-images']")
        if product_img:
            product_img.screenshot(path=str(filepath))
            log.info(f"  Bild von Seite gespeichert: {filepath.name}")
            return url_hash + ext

        # Strategy 2: Navigate to the S3 image URL directly
        if not image_url:
            return None
        current_url = page.url
        page.goto(image_url, wait_until="load", timeout=15000)
        time.sleep(1)
        img = page.query_selector("img")
        if img:
            img.screenshot(path=str(filepath))
            log.info(f"  Bild gespeichert: {filepath.name}")
            page.goto(current_url, wait_until="domcontentloaded", timeout=15000)
            time.sleep(2)
            return url_hash + ext
        page.goto(current_url, wait_until="domcontentloaded", timeout=15000)
        time.sleep(2)
    except Exception as e:
        log.warning(f"  Bild-Download fehlgeschlagen: {e}")
        try:
            page.goto(current_url, wait_until="domcontentloaded", timeout=15000)
        except Exception:
            pass
    return None


# ─── Portfolio Loading ────────────────────────────────────────

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


# ─── Resume Support ───────────────────────────────────────────

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


# ─── Browser Management (Patchright) ─────────────────────────

def create_browser(playwright):
    """Erstellt einen Patchright-Browser mit persistentem Profil und echtem Chrome."""
    profile_dir = BASE_DIR / "data" / "patchright-profile"
    profile_dir.mkdir(parents=True, exist_ok=True)

    context = playwright.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        channel="chrome",
        headless=False,
        no_viewport=True,
        args=[
            "--window-size=1280,900",
            "--window-position=0,0",
            "--no-first-run",
            "--disable-session-crashed-bubble",
            "--disable-dev-shm-usage",
        ],
    )
    return context


# ─── Main Scraper ─────────────────────────────────────────────

def scrape_single_card(page, card, timestamp, is_first):
    """Scrapt eine einzelne Karte via Patchright."""
    card_name = card["name"] or card["url"].split("/")[-1].split("?")[0]

    page.goto(card["url"], wait_until="domcontentloaded", timeout=30000)
    wait = FIRST_WAIT if is_first else random.uniform(MIN_DELAY, MAX_DELAY)
    log.info(f"  Warte {wait:.0f}s...")
    time.sleep(wait)

    # Check if CF challenge
    title = page.title()
    if "moment" in title.lower() or "challenge" in title.lower():
        log.warning(f"  Cloudflare Challenge! Warte auf manuellen Click via noVNC...")
        send_telegram(
            f"\u26a0\ufe0f <b>Cloudflare Challenge</b>\n"
            f"Karte: {card_name}\n"
            f"Bitte im noVNC-Panel klicken!"
        )
        # Wait up to 5 min for manual resolution, poll every 3s
        for _ in range(100):
            time.sleep(3)
            title = page.title()
            if "moment" not in title.lower() and "challenge" not in title.lower():
                log.info("  Cloudflare geloest!")
                break
        else:
            log.warning(f"  CF Timeout: {title}")
            return {
                "url": card["url"], "name": card["name"], "notes": card["notes"],
                "timestamp": timestamp, "error": ERR_CLOUDFLARE,
            }, "cloudflare"

    content = page.content()
    prices = extract_prices(content)
    info = extract_card_info(content)

    grade = card.get("grade", "")
    grade_map = {"PSA10": "psa10_low", "PSA9": "psa9_low", "CGC10": "cgc10_low", "BGS10": "bgs10_low"}
    grade_key = grade_map.get(grade.replace(" ", ""))
    grade_value = prices.get(grade_key) if grade_key else None

    # Bild herunterladen (einmalig) — auch ohne og:image (Strategy 1 nutzt Seitenbild)
    image_file = download_image(info.get("image_url", ""), card["url"], page)

    if not prices or (not prices.get("trend") and not prices.get("from")):
        title = page.title()
        log.warning(f"  WARNUNG: Keine Preise extrahiert! title=\"{title}\", len={len(content)}")
        error = ERR_NO_PRICES
        status = "no_prices"
    else:
        error = None
        status = "ok"
        if grade_value:
            log.info(f"  {grade} Low: EUR {grade_value} (Trend: EUR {prices.get('trend')})")
        else:
            log.info(f"  Low: EUR {prices.get('from')}, Trend: EUR {prices.get('trend')}")

    value = grade_value or prices.get("from") or prices.get("trend")

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


def scrape_cards(cards):
    """Scrapt Preise fuer alle Karten mit Patchright, Batch-Support und Resume."""
    from patchright.sync_api import sync_playwright

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

    remaining_cards = [c for c in cards if c["url"] not in completed_urls]
    if not remaining_cards:
        log.info("Alle Karten bereits gescrapt (Resume). Nichts zu tun.")
        return results, stats

    log.info(f"{len(remaining_cards)} Karten zu scrapen (von {total} gesamt)")

    with sync_playwright() as playwright:
        context = None
        page = None
        batch_num = 0

        try:
            for i, card in enumerate(remaining_cards):
                # Neuen Browser bei Batch-Grenze oder am Anfang
                if context is None or (BATCH_SIZE > 0 and i > 0 and i % BATCH_SIZE == 0):
                    if context:
                        log.info(f"\n--- Batch {batch_num} abgeschlossen, Browser neustarten ---")
                        context.close()
                        time.sleep(3)
                    batch_num += 1
                    log.info(f"Chrome starten (Batch {batch_num}, Patchright)...")
                    context = create_browser(playwright)
                    page = context.pages[0] if context.pages else context.new_page()

                card_name = card["name"] or card["url"].split("/")[-1].split("?")[0]
                log.info(f"\n[{i + stats['skipped'] + 1}/{total}] {card_name}")

                try:
                    result, status = scrape_single_card(page, card, timestamp, is_first=(i == 0 and batch_num == 1))
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
                    try:
                        context.close()
                    except Exception:
                        pass
                    context = None
                    page = None
                    log.info("  Browser wird nach Fehler neugestartet...")

        finally:
            if context:
                context.close()

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
    graded = sum(1 for r in results if r.get("grade"))

    summary = f"""
{'='*50}
SCRAPE ZUSAMMENFASSUNG ({now.strftime('%d.%m.%Y %H:%M')})
{'='*50}
Karten gesamt:   {len(results)} (+ {stats['skipped']} uebersprungen)
  Erfolgreich:   {stats['ok']}
  Keine Preise:  {stats['no_prices']}
  Cloudflare:    {stats['cloudflare']}
  Fehler:        {stats['errors']}
  Graded:        {graded}
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
            f"\U0001f4ca <b>Scrape Report</b>\n\n"
            f"\u2705 {stats['ok']}/{len(results)} erfolgreich\n"
            f"{chr(10).join(problems)}\n\n"
            f"<b>Betroffene Karten:</b>\n"
            f"{chr(10).join(chr(8226) + ' ' + c for c in failed_cards[:10])}\n\n"
            f"Portfolio: EUR {total_value:,.2f}"
        )
    else:
        send_telegram(
            f"\u2705 <b>Scrape OK</b> \u2014 {stats['ok']}/{len(results)} Karten\n"
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
        est_min = len(cards) * 6 / 60
        print(f"\nGeschaetzte Dauer: ~{est_min:.0f} Minuten (Patchright, {BATCH_SIZE}er Batches)")
        return

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

    log.info(f"\n{len(cards)} Karten zu scrapen (Patchright + Chrome)...")
    log.info(f"Geschaetzte Dauer: ~{len(cards) * 6 / 60:.0f} Minuten")
    log.info(f"Batch-Groesse: {BATCH_SIZE}")

    results, stats = scrape_cards(cards)
    save_results(results, stats)

    if stats["ok"] == 0 and len(cards) > 0:
        sys.exit(2)
    elif stats["cloudflare"] > 0 or stats["errors"] > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
