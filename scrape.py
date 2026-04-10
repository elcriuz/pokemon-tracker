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
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).parent
PORTFOLIO_FILE = BASE_DIR / "portfolio.csv"
PRICES_DIR = BASE_DIR / "prices"
LATEST_FILE = PRICES_DIR / "latest.json"
LOG_DIR = BASE_DIR / "data" / "logs"
RESUME_FILE = BASE_DIR / "data" / "scrape_resume.json"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Timing — schneller nach dem ersten erfolgreichen Load
FIRST_WAIT = 12       # Erste Seite (Cloudflare-Bypass)
MIN_DELAY = 8         # Minimum zwischen Karten
MAX_DELAY = 14        # Maximum zwischen Karten
CF_WAIT_TIMEOUT = int(os.environ.get("CF_WAIT_TIMEOUT", "300"))
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
ERR_TIMEOUT = "cloudflare_timeout"
ERR_SKIPPED = "skipped_recent"
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


# ─── Cloudflare Detection ───────────────────────────────────

def is_cloudflare_challenge(content):
    lower = content.lower()
    # Short page with CF indicators = definite challenge
    if len(content) < 10000:
        indicators = ["challenge", "just a moment", "checking your browser"]
        if any(ind in lower for ind in indicators):
            return True
    # Turnstile iframe can also appear on larger pages
    if "challenges.cloudflare.com" in lower or "turnstile" in lower:
        return True
    return False


def try_solve_turnstile(driver):
    """Versucht Turnstile zu loesen: erst iframe-Position finden, dann OS-Level Click via xdotool."""
    import subprocess

    try:
        frames = driver.find_elements("tag name", "iframe")
        for frame in frames:
            src = frame.get_attribute("src") or ""
            if "challenge" in src or "turnstile" in src:
                # Get iframe position on screen
                loc = frame.location
                size = frame.size
                # Checkbox is roughly centered in the iframe, slightly left
                click_x = int(loc["x"]) + min(35, int(size["width"] // 2))
                click_y = int(loc["y"]) + int(size["height"] // 2)

                log.info(f"  Turnstile gefunden bei ({click_x}, {click_y}), versuche xdotool click...")

                jitter_x = random.randint(-3, 3)
                jitter_y = random.randint(-2, 2)
                target_x = click_x + jitter_x
                target_y = click_y + jitter_y

                try:
                    display = os.environ.get("DISPLAY", ":99")
                    env = {**os.environ, "DISPLAY": display}
                    # Start far away, move in 3-4 steps like a human
                    start_x = random.randint(200, 600)
                    start_y = random.randint(100, 400)
                    subprocess.run(["xdotool", "mousemove", "--", str(start_x), str(start_y)], env=env, timeout=5)
                    time.sleep(random.uniform(0.2, 0.5))
                    # Move towards target in steps
                    for step in range(3):
                        frac = (step + 1) / 4
                        sx = int(start_x + (target_x - start_x) * frac + random.randint(-10, 10))
                        sy = int(start_y + (target_y - start_y) * frac + random.randint(-8, 8))
                        subprocess.run(["xdotool", "mousemove", "--", str(sx), str(sy)], env=env, timeout=5)
                        time.sleep(random.uniform(0.05, 0.15))
                    time.sleep(random.uniform(0.1, 0.3))
                    subprocess.run(["xdotool", "mousemove", "--", str(target_x), str(target_y)], env=env, timeout=5)
                    time.sleep(random.uniform(0.3, 0.7))
                    subprocess.run(["xdotool", "click", "1"], env=env, timeout=5)
                    log.info(f"  xdotool click bei ({target_x}, {target_y})")
                    return True
                except Exception as e:
                    log.warning(f"  xdotool fehlgeschlagen: {e}, fallback auf Selenium click")
                    # Fallback: Selenium click
                    driver.switch_to.frame(frame)
                    checkboxes = driver.find_elements("css selector", "input[type='checkbox'], .cb-lb, #challenge-stage")
                    if checkboxes:
                        checkboxes[0].click()
                    driver.switch_to.default_content()
                    return True
    except Exception:
        try:
            driver.switch_to.default_content()
        except Exception:
            pass
    return False


def wait_for_cloudflare(driver, card_name):
    content = driver.page_source
    if not is_cloudflare_challenge(content):
        return content, STATUS_OK

    log.warning(f"  Cloudflare Challenge erkannt fuer: {card_name}")
    for attempt in range(3):
        log.info(f"  Auto-Solve Versuch {attempt+1}/3...")
        try_solve_turnstile(driver)
        time.sleep(15)
        content = driver.page_source
        if not is_cloudflare_challenge(content):
            log.info("  Cloudflare automatisch geloest!")
            return content, STATUS_OK

    log.warning(f"  Auto-Solve fehlgeschlagen. Warte auf menschliche Hilfe ({CF_WAIT_TIMEOUT}s)...")
    send_telegram(
        f"\u26a0\ufe0f <b>Cloudflare Challenge</b>\n\n"
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
            send_telegram(f"\u2705 Cloudflare geloest! Scrape wird fortgesetzt.")
            return content, STATUS_OK

    log.error(f"  Cloudflare Timeout nach {CF_WAIT_TIMEOUT}s")
    send_telegram(f"\u274c Cloudflare Timeout fuer: {card_name}. Karte wird uebersprungen.")
    return content, ERR_TIMEOUT


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


def extract_card_info(content):
    info = {}
    title_m = re.search(r"<title>(.*?)</title>", content)
    if title_m:
        title = title_m.group(1).replace(" | Cardmarket", "")
        # Cloudflare-Titel ignorieren
        if not any(x in title.lower() for x in ["just a moment", "cloudflare", "checking"]):
            info["page_title"] = title
    og_m = re.search(r'<meta[^>]+(?:property|name)=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', content)
    if not og_m:
        og_m = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', content)
    if og_m:
        info["image_url"] = og_m.group(1)
    return info


# ─── Image Download ──────────────────────────────────────────

def download_image(image_url, card_url, driver=None):
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
    """Laedt den Resume-State vom letzten abgebrochenen Run."""
    if RESUME_FILE.exists():
        try:
            state = json.loads(RESUME_FILE.read_text())
            # Nur wenn der letzte Run heute war
            if state.get("date") == datetime.now().strftime("%Y-%m-%d"):
                return state
        except Exception:
            pass
    return None


def save_resume_state(completed_urls, results):
    """Speichert den aktuellen Fortschritt fuer Resume."""
    state = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "completed_urls": list(completed_urls),
        "results_count": len(results),
        "timestamp": datetime.now().isoformat(),
    }
    RESUME_FILE.write_text(json.dumps(state))


def clear_resume_state():
    """Loescht den Resume-State nach erfolgreichem Durchlauf."""
    if RESUME_FILE.exists():
        RESUME_FILE.unlink()


# ─── Chrome Management ───────────────────────────────────────

def create_driver():
    """Erstellt einen neuen Chrome-Driver mit persistentem Profil."""
    import undetected_chromedriver as uc

    profile_dir = BASE_DIR / "data" / "chrome-profile"
    profile_dir.mkdir(parents=True, exist_ok=True)

    options = uc.ChromeOptions()
    options.add_argument("--window-size=1280,900")
    options.add_argument("--window-position=0,0")
    options.add_argument(f"--user-data-dir={profile_dir}")
    options.add_argument("--disable-dev-shm-usage")

    driver = uc.Chrome(options=options, version_main=146)
    return driver


# ─── Main Scraper ─────────────────────────────────────────────

def scrape_single_card(driver, card, timestamp, is_first):
    """Scrapt eine einzelne Karte. Returns (result_dict, status_str)."""
    card_name = card["name"] or card["url"].split("/")[-1].split("?")[0]

    driver.get(card["url"])
    wait = FIRST_WAIT if is_first else random.uniform(MIN_DELAY, MAX_DELAY)
    log.info(f"  Warte {wait:.0f}s...")
    time.sleep(wait)

    content, cf_status = wait_for_cloudflare(driver, card_name)

    if cf_status in (ERR_CLOUDFLARE, ERR_TIMEOUT):
        log.error(f"  UEBERSPRUNGEN: Cloudflare nicht geloest")
        return {
            "url": card["url"], "name": card["name"], "notes": card["notes"],
            "timestamp": timestamp, "error": cf_status,
            "error_detail": "Cloudflare Challenge konnte nicht geloest werden",
        }, "cloudflare"

    prices = extract_prices(content)
    info = extract_card_info(content)

    grade = card.get("grade", "")
    grade_map = {"PSA10": "psa10_low", "PSA9": "psa9_low", "CGC10": "cgc10_low", "BGS10": "bgs10_low"}
    grade_key = grade_map.get(grade.replace(" ", ""))
    grade_value = prices.get(grade_key) if grade_key else None

    image_file = None
    if info.get("image_url"):
        image_file = download_image(info["image_url"], card["url"], driver)

    if not prices or (not prices.get("trend") and not prices.get("from")):
        # Debug: warum keine Preise?
        title = driver.title or "?"
        has_cf = "challenge" in content.lower() or "turnstile" in content.lower()
        log.warning(f"  WARNUNG: Keine Preise extrahiert! title=\"{title}\", len={len(content)}, CF={has_cf}")
        error = ERR_NO_PRICES
        status = "no_prices"
    else:
        error = None
        status = "ok"
        if grade_value:
            log.info(f"  {grade} Low: EUR {grade_value} (Trend: EUR {prices.get('trend')})")
        else:
            log.info(f"  Low: EUR {prices.get('from')}, Trend: EUR {prices.get('trend')}")

    # Value-Bestimmung:
    # - Graded: guenstigster Preis fuer das jeweilige Grading
    # - Ungraded: "from" (guenstigster NM-Preis, gefiltert nach Sprache/Condition aus URL)
    # - Fallback: trend (falls from nicht verfuegbar)
    value = grade_value or prices.get("from") or prices.get("trend")

    result = {
        "url": card["url"],
        "name": card["name"] or info.get("page_title", ""),
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
    """Scrapt Preise fuer alle Karten mit Batch-Support und Resume."""
    results = []
    stats = {"ok": 0, "cloudflare": 0, "no_prices": 0, "errors": 0, "skipped": 0}
    total = len(cards)
    timestamp = datetime.now().isoformat()

    # Resume: bereits gescrapte Karten ueberspringen
    resume_state = load_resume_state()
    completed_urls = set()
    if resume_state:
        completed_urls = set(resume_state.get("completed_urls", []))
        if completed_urls:
            log.info(f"Resume: {len(completed_urls)} Karten vom letzten Run ueberspringen")

    # Karten filtern
    remaining_cards = []
    for card in cards:
        if card["url"] in completed_urls:
            stats["skipped"] += 1
            continue
        remaining_cards.append(card)

    if not remaining_cards:
        log.info("Alle Karten bereits gescrapt (Resume). Nichts zu tun.")
        return results, stats

    log.info(f"{len(remaining_cards)} Karten zu scrapen (von {total} gesamt, {stats['skipped']} uebersprungen)")

    # Batches verarbeiten
    driver = None
    batch_num = 0

    try:
        for i, card in enumerate(remaining_cards):
            # Neuen Chrome starten bei Batch-Grenze oder am Anfang
            if driver is None or (BATCH_SIZE > 0 and i > 0 and i % BATCH_SIZE == 0):
                if driver:
                    log.info(f"\n--- Batch {batch_num} abgeschlossen, Chrome neustarten ---")
                    driver.quit()
                    time.sleep(5)  # Kurze Pause zwischen Batches
                batch_num += 1
                log.info(f"Chrome starten (Batch {batch_num}, persistentes Profil)...")
                driver = create_driver()

            card_name = card["name"] or card["url"].split("/")[-1].split("?")[0]
            log.info(f"\n[{i + stats['skipped'] + 1}/{total}] {card_name}")

            try:
                result, status = scrape_single_card(driver, card, timestamp, is_first=(i == 0 and batch_num == 1))
                results.append(result)
                stats[status if status in stats else "errors"] += 1
                completed_urls.add(card["url"])

                # Resume-State + latest.json nach jeder Karte speichern (live updates)
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
                # Bei Crash: Chrome neustarten
                try:
                    driver.quit()
                except Exception:
                    pass
                driver = None
                log.info("  Chrome wird nach Fehler neugestartet...")

    finally:
        if driver:
            driver.quit()

    # Erfolgreich durchgelaufen: Resume-State loeschen
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
    counted = sum(1 for r in results if r.get("value"))
    graded = sum(1 for r in results if r.get("grade"))

    est_time = (stats["ok"] + stats["no_prices"]) * 12
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
Dauer:           ~{est_time // 60}m {est_time % 60}s
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
        est_min = len(cards) * 12 / 60
        print(f"\nGeschaetzte Dauer: ~{est_min:.0f} Minuten ({BATCH_SIZE}er Batches)")
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
    log.info(f"Geschaetzte Dauer: ~{len(cards) * 12 / 60:.0f} Minuten")
    log.info(f"Batch-Groesse: {BATCH_SIZE} | CF-Timeout: {CF_WAIT_TIMEOUT}s")

    results, stats = scrape_cards(cards)
    save_results(results, stats)

    if stats["ok"] == 0 and len(cards) > 0:
        sys.exit(2)
    elif stats["cloudflare"] > 0 or stats["errors"] > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
