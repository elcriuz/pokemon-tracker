#!/usr/bin/env python3
"""
Pokemon Card Portfolio Tracker - Cardmarket Scraper
Scrapt Preise von Cardmarket via undetected_chromedriver (Cloudflare-Bypass).

Usage:
    python3 scrape.py                  # Scrapt alle Karten aus portfolio.csv
    python3 scrape.py --dry-run        # Zeigt nur was gescrapt wuerde
    python3 scrape.py --single "URL"   # Scrapt eine einzelne URL

Env:
    TELEGRAM_BOT_TOKEN   - Telegram Bot Token fuer Alerts
    TELEGRAM_CHAT_ID     - Telegram Chat ID fuer Alerts
    CF_WAIT_TIMEOUT      - Sekunden warten auf menschliche Hilfe bei CF (default: 300)
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
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Timing
MIN_DELAY = 12
MAX_DELAY = 20
INITIAL_WAIT = 12
CF_WAIT_TIMEOUT = int(os.environ.get("CF_WAIT_TIMEOUT", "300"))  # 5 Minuten default

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
ERR_TIMEOUT = "cloudflare_timeout"
STATUS_OK = "ok"


# ─── Telegram ───────────────────────────────────────────────

def load_telegram_config():
    """Laedt Telegram-Config aus env oder DB settings."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        # Fallback: aus SQLite settings lesen
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
    """Sendet Telegram-Nachricht. Gibt True/False zurueck."""
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


# ─── Cloudflare Detection ───────────────────────────────────

def is_cloudflare_challenge(content):
    """Erkennt ob die Seite eine Cloudflare Challenge zeigt."""
    if len(content) > 10000:
        return False
    indicators = ["challenge", "just a moment", "checking your browser", "cloudflare"]
    lower = content.lower()
    return any(ind in lower for ind in indicators)


def try_solve_turnstile(driver):
    """Versucht den Turnstile-Checkbox automatisch zu klicken."""
    try:
        frames = driver.find_elements("tag name", "iframe")
        for frame in frames:
            src = frame.get_attribute("src") or ""
            if "challenge" in src or "turnstile" in src:
                driver.switch_to.frame(frame)
                checkboxes = driver.find_elements("css selector", "input[type='checkbox'], .cb-lb, #challenge-stage")
                if checkboxes:
                    checkboxes[0].click()
                    log.info("  Turnstile Checkbox geklickt")
                driver.switch_to.default_content()
                return True
    except Exception:
        try:
            driver.switch_to.default_content()
        except Exception:
            pass
    return False


def wait_for_cloudflare(driver, card_name):
    """
    Wartet auf Cloudflare-Bypass. Strategie:
    1. Auto-Solve versuchen (3x)
    2. Falls das nicht klappt: Telegram-Alert → warten auf menschliche Hilfe
    3. Timeout nach CF_WAIT_TIMEOUT Sekunden

    Returns: (content, status) - status ist STATUS_OK oder ERR_CLOUDFLARE/ERR_TIMEOUT
    """
    content = driver.page_source

    if not is_cloudflare_challenge(content):
        return content, STATUS_OK

    # Phase 1: Auto-Solve (3 Versuche, je 15s)
    log.warning(f"  Cloudflare Challenge erkannt fuer: {card_name}")
    for attempt in range(3):
        log.info(f"  Auto-Solve Versuch {attempt+1}/3...")
        try_solve_turnstile(driver)
        time.sleep(15)
        content = driver.page_source
        if not is_cloudflare_challenge(content):
            log.info("  Cloudflare automatisch geloest!")
            return content, STATUS_OK

    # Phase 2: Menschliche Hilfe anfordern
    log.warning(f"  Auto-Solve fehlgeschlagen. Warte auf menschliche Hilfe ({CF_WAIT_TIMEOUT}s)...")
    send_telegram(
        f"⚠️ <b>Cloudflare Challenge</b>\n\n"
        f"Karte: {card_name}\n"
        f"Bitte via VNC/Browser eingreifen!\n"
        f"Timeout in {CF_WAIT_TIMEOUT // 60} Minuten."
    )

    start = time.time()
    while time.time() - start < CF_WAIT_TIMEOUT:
        time.sleep(10)
        content = driver.page_source
        if not is_cloudflare_challenge(content):
            elapsed = int(time.time() - start)
            log.info(f"  Cloudflare geloest (nach {elapsed}s menschlicher Hilfe)")
            send_telegram(f"✅ Cloudflare geloest! Scrape wird fortgesetzt.")
            return content, STATUS_OK

    # Phase 3: Timeout
    log.error(f"  Cloudflare Timeout nach {CF_WAIT_TIMEOUT}s")
    send_telegram(f"❌ Cloudflare Timeout fuer: {card_name}. Karte wird uebersprungen.")
    return content, ERR_TIMEOUT


# ─── Price Extraction ────────────────────────────────────────

def parse_de_price(price_str):
    """Konvertiert '1.124,38' -> 1124.38"""
    if not price_str:
        return None
    cleaned = price_str.replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def extract_prices(content):
    """Extrahiert alle Preise aus dem Seitenquelltext."""
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


def extract_card_info(content):
    """Extrahiert Karteninformationen aus dem Seitenquelltext."""
    info = {}

    title_m = re.search(r"<title>(.*?)</title>", content)
    if title_m:
        info["page_title"] = title_m.group(1).replace(" | Cardmarket", "")

    og_m = re.search(r'<meta[^>]+(?:property|name)=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', content)
    if not og_m:
        og_m = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', content)
    if og_m:
        info["image_url"] = og_m.group(1)

    return info


# ─── Image Download ──────────────────────────────────────────

def download_image(image_url, card_url, driver=None):
    """Laedt Kartenbild herunter via Browser-Navigation."""
    import hashlib

    images_dir = BASE_DIR / "data" / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    url_hash = hashlib.md5(card_url.encode()).hexdigest()[:12]
    ext = ".png"
    filepath = images_dir / f"{url_hash}{ext}"

    if filepath.exists():
        return url_hash + ext

    if not driver:
        return None

    try:
        current_url = driver.current_url
        driver.get(image_url)
        time.sleep(2)
        img_el = driver.find_element("tag name", "img")
        if img_el:
            img_el.screenshot(str(filepath))
            log.info(f"  Bild gespeichert: {filepath.name}")
            driver.get(current_url)
            time.sleep(3)
            return url_hash + ext
        else:
            driver.get(current_url)
            time.sleep(3)
    except Exception as e:
        log.warning(f"  Bild-Download fehlgeschlagen: {e}")
        try:
            driver.get(current_url)
            time.sleep(3)
        except Exception:
            pass
    return None


# ─── Portfolio Loading ────────────────────────────────────────

def load_portfolio():
    """Laedt die Kartenliste aus portfolio.csv."""
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


# ─── Main Scraper ─────────────────────────────────────────────

def scrape_cards(cards):
    """Scrapt Preise fuer alle Karten."""
    import undetected_chromedriver as uc

    profile_dir = BASE_DIR / "data" / "chrome-profile"
    profile_dir.mkdir(parents=True, exist_ok=True)

    options = uc.ChromeOptions()
    options.add_argument("--window-size=1280,900")
    options.add_argument(f"--user-data-dir={profile_dir}")

    log.info("Chrome starten (persistentes Profil)...")
    driver = uc.Chrome(options=options, version_main=146)

    results = []
    stats = {"ok": 0, "cloudflare": 0, "no_prices": 0, "errors": 0, "skipped": 0}
    total = len(cards)
    timestamp = datetime.now().isoformat()

    for i, card in enumerate(cards):
        card_name = card["name"] or card["url"].split("/")[-1].split("?")[0]
        log.info(f"\n[{i+1}/{total}] {card_name}")

        try:
            driver.get(card["url"])

            wait = INITIAL_WAIT if i == 0 else random.uniform(MIN_DELAY, MAX_DELAY)
            log.info(f"  Warte {wait:.0f}s...")
            time.sleep(wait)

            # Cloudflare-Handling mit Auto-Solve + Human Override
            content, cf_status = wait_for_cloudflare(driver, card_name)

            if cf_status in (ERR_CLOUDFLARE, ERR_TIMEOUT):
                log.error(f"  UEBERSPRUNGEN: Cloudflare nicht geloest")
                stats["cloudflare"] += 1
                results.append({
                    "url": card["url"],
                    "name": card["name"],
                    "notes": card["notes"],
                    "timestamp": timestamp,
                    "error": cf_status,
                    "error_detail": "Cloudflare Challenge konnte nicht geloest werden",
                })
                continue

            prices = extract_prices(content)
            info = extract_card_info(content)

            # Graded Preis bestimmen
            grade = card.get("grade", "")
            grade_map = {
                "PSA10": "psa10_low", "PSA9": "psa9_low",
                "CGC10": "cgc10_low", "BGS10": "bgs10_low",
            }
            grade_key = grade_map.get(grade.replace(" ", ""))
            grade_value = prices.get(grade_key) if grade_key else None

            # Bild herunterladen (einmalig)
            image_file = None
            if info.get("image_url"):
                image_file = download_image(info["image_url"], card["url"], driver)

            # Fehler-Check: Preise gefunden?
            if not prices or not prices.get("trend"):
                log.warning(f"  WARNUNG: Keine Preise extrahiert!")
                stats["no_prices"] += 1
                error = ERR_NO_PRICES
            else:
                stats["ok"] += 1
                error = None
                if grade_value:
                    log.info(f"  {grade} Low: EUR {grade_value} (Trend: EUR {prices.get('trend')})")
                else:
                    log.info(f"  Trend: EUR {prices.get('trend')}, Low: EUR {prices.get('from')}")

            result = {
                "url": card["url"],
                "name": card["name"] or info.get("page_title", ""),
                "grade": grade,
                "value": grade_value or prices.get("trend"),
                "notes": card["notes"],
                "image": image_file,
                "timestamp": timestamp,
                "error": error,
                **prices,
            }
            results.append(result)

        except Exception as e:
            log.error(f"  FEHLER: {e}", exc_info=True)
            stats["errors"] += 1
            results.append({
                "url": card["url"],
                "name": card["name"],
                "notes": card["notes"],
                "timestamp": timestamp,
                "error": ERR_CRASH,
                "error_detail": str(e),
            })

    driver.quit()
    return results, stats


def save_results(results, stats):
    """Speichert Ergebnisse als CSV und JSON."""
    PRICES_DIR.mkdir(exist_ok=True)
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d_%H%M")

    # CSV mit Datum
    csv_file = PRICES_DIR / f"{date_str}.csv"
    fieldnames = ["name", "grade", "value", "trend", "avg7", "avg30", "avg1", "from",
                   "psa10_low", "psa9_low", "cgc10_low", "bgs10_low",
                   "available_items", "notes", "image", "timestamp", "url", "error", "error_detail"]
    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)
    log.info(f"\nCSV gespeichert: {csv_file}")

    # latest.json
    with open(LATEST_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    log.info(f"JSON gespeichert: {LATEST_FILE}")

    # Zusammenfassung
    total_value = sum(r.get("value", 0) or 0 for r in results)
    counted = sum(1 for r in results if r.get("value"))
    graded = sum(1 for r in results if r.get("grade"))

    summary = f"""
{'='*50}
SCRAPE ZUSAMMENFASSUNG ({now.strftime('%d.%m.%Y %H:%M')})
{'='*50}
Karten gesamt:   {len(results)}
  Erfolgreich:   {stats['ok']}
  Keine Preise:  {stats['no_prices']}
  Cloudflare:    {stats['cloudflare']}
  Fehler:        {stats['errors']}
  Graded:        {graded}
Portfolio-Wert:  EUR {total_value:,.2f}
Log-Datei:       {log_file}
{'='*50}"""
    log.info(summary)

    # Telegram-Summary bei Problemen
    if stats["cloudflare"] > 0 or stats["errors"] > 0 or stats["no_prices"] > 0:
        problems = []
        if stats["cloudflare"]: problems.append(f"☁️ {stats['cloudflare']}x Cloudflare")
        if stats["no_prices"]: problems.append(f"⚠️ {stats['no_prices']}x keine Preise")
        if stats["errors"]: problems.append(f"❌ {stats['errors']}x Fehler")

        # Betroffene Karten auflisten
        failed_cards = [r["name"] or r["url"].split("/")[-1] for r in results if r.get("error")]

        send_telegram(
            f"📊 <b>Scrape Report</b>\n\n"
            f"✅ {stats['ok']}/{len(results)} erfolgreich\n"
            f"{chr(10).join(problems)}\n\n"
            f"<b>Betroffene Karten:</b>\n"
            f"{chr(10).join('• ' + c for c in failed_cards[:10])}\n\n"
            f"Portfolio: EUR {total_value:,.2f}"
        )
    else:
        # Alles OK: kurze Erfolgsmeldung
        send_telegram(
            f"✅ <b>Scrape OK</b> — {stats['ok']}/{len(results)} Karten\n"
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
        est_min = len(cards) * 17 / 60
        print(f"\nGeschaetzte Dauer: ~{est_min:.0f} Minuten")
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

    log.info(f"\n{len(cards)} Karten zu scrapen...")
    log.info(f"Geschaetzte Dauer: ~{len(cards) * 17 / 60:.0f} Minuten")
    log.info(f"CF Human-Override Timeout: {CF_WAIT_TIMEOUT}s")

    results, stats = scrape_cards(cards)
    save_results(results, stats)

    # Exit-Code basierend auf Ergebnis
    if stats["ok"] == 0 and len(cards) > 0:
        sys.exit(2)  # Komplett fehlgeschlagen
    elif stats["cloudflare"] > 0 or stats["errors"] > 0:
        sys.exit(1)  # Teilweise fehlgeschlagen
    sys.exit(0)      # Alles OK


if __name__ == "__main__":
    main()
