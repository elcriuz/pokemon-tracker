#!/usr/bin/env python3
"""
Pokemon Card Portfolio Tracker - Bright Data Auto-Scraper
Scrapt Preise von Cardmarket via Bright Data Web Unlocker API (async parallel).

Usage:
    python3 scrape_brightdata.py                  # Scrapt alle Karten aus portfolio.csv
    python3 scrape_brightdata.py --dry-run        # Zeigt nur was gescrapt wuerde
    python3 scrape_brightdata.py --single "URL"   # Scrapt eine einzelne URL

Env:
    BRIGHTDATA_API_KEY   - Bright Data API Key (alternativ aus DB settings)
    BRIGHTDATA_ZONE      - Bright Data Zone Name (default: cardmarket)
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

BD_BASE = "https://api.brightdata.com"
POLL_INTERVAL = 5
MAX_POLL_ATTEMPTS = 60  # 5s * 60 = 5min max
MAX_PARALLEL = 50

# Logging
log_file = LOG_DIR / f"scrape_brightdata_{datetime.now().strftime('%Y-%m-%d_%H%M')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("brightdata")

ERR_CLOUDFLARE = "cloudflare_blocked"
ERR_NO_PRICES = "no_prices_extracted"
ERR_TIMEOUT = "timeout"
ERR_API_ERROR = "api_error"
ERR_CRASH = "exception"


# ─── Config Loading ─────────────────────────────────────────

def load_brightdata_config():
    api_key = os.environ.get("BRIGHTDATA_API_KEY", "")
    zone = os.environ.get("BRIGHTDATA_ZONE", "")
    if not api_key or not zone:
        try:
            db_path = BASE_DIR / "data" / "tracker.db"
            if db_path.exists():
                import sqlite3
                conn = sqlite3.connect(str(db_path))
                rows = conn.execute("SELECT key, value FROM settings WHERE key IN ('brightdata_api_key','brightdata_zone')").fetchall()
                cfg = dict(rows)
                conn.close()
                api_key = api_key or cfg.get("brightdata_api_key", "")
                zone = zone or cfg.get("brightdata_zone", "cardmarket")
        except Exception:
            pass
    return api_key, zone or "cardmarket"


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


# ─── Price Extraction (same as scrape.py) ────────────────────

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


# ─── Image Download ──────────────────────────────────────────

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
    except Exception as e:
        log.warning(f"  Bild-Download fehlgeschlagen: {e}")
    return None


# ─── Portfolio & Resume ──────────────────────────────────────

def load_portfolio():
    """Laedt Karten aus DB (bevorzugt) oder portfolio.csv als Fallback."""
    db_path = BASE_DIR / "data" / "tracker.db"
    if db_path.exists():
        try:
            import sqlite3
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT url, name, grade, notes FROM cards").fetchall()
            conn.close()
            if rows:
                log.info(f"  {len(rows)} Karten aus DB geladen")
                return [{"url": r["url"], "name": r["name"] or "", "grade": (r["grade"] or "").upper(), "notes": r["notes"] or ""} for r in rows]
        except Exception as e:
            log.warning(f"  DB-Ladevorgang fehlgeschlagen, Fallback auf CSV: {e}")

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


# ─── Result Processing ──────────────────────────────────────

def process_result(content, card, timestamp):
    """Verarbeitet HTML-Content einer Karte und gibt Result-Dict zurueck."""
    if "cf-challenge" in content.lower() or ("just a moment" in content.lower() and len(content) < 5000):
        return {
            "url": card["url"], "name": card["name"], "notes": card["notes"],
            "timestamp": timestamp, "error": ERR_CLOUDFLARE,
        }, "cloudflare"

    prices = extract_prices(content)
    info = extract_card_info(content)

    grade = card.get("grade", "")
    grade_map = {"PSA10": "psa10_low", "PSA9": "psa9_low", "CGC10": "cgc10_low", "BGS10": "bgs10_low"}
    grade_key = grade_map.get(grade.replace(" ", ""))
    grade_value = prices.get(grade_key) if grade_key else None

    image_file = download_image(info.get("image_url", ""), card["url"])

    if not prices or (not prices.get("trend") and not prices.get("from")):
        return {
            "url": card["url"], "name": card["name"] or info.get("page_title", ""),
            "set_name": extract_set_from_url(card["url"]),
            "grade": grade, "notes": card["notes"], "image": image_file,
            "timestamp": timestamp, "error": ERR_NO_PRICES, **prices,
        }, "no_prices"

    # Value-Bestimmung
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
        "grade": grade, "value": value, "notes": card["notes"],
        "image": image_file, "timestamp": timestamp, "error": None,
        **prices,
    }

    if grade_value:
        log.info(f"  {grade} Low: EUR {grade_value} | Trend: EUR {prices.get('trend')}")
    else:
        log.info(f"  Low: EUR {prices.get('from')} | Trend: EUR {prices.get('trend')} | 7d: EUR {prices.get('avg7')}")

    return result, "ok"


# ─── Async Parallel Scraping ────────────────────────────────

def scrape_cards(cards, api_key, zone):
    """Scrapt alle Karten via Bright Data async parallel."""
    results = []
    stats = {"ok": 0, "cloudflare": 0, "no_prices": 0, "errors": 0, "skipped": 0}
    total = len(cards)
    timestamp = datetime.now().isoformat()
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}

    # Resume
    resume_state = load_resume_state()
    completed_urls = set()
    if resume_state:
        completed_urls = set(resume_state.get("completed_urls", []))
        if completed_urls:
            log.info(f"Resume: {len(completed_urls)} Karten vom letzten Run ueberspringen")
            stats["skipped"] = len(completed_urls)

    remaining = [c for c in cards if c["url"] not in completed_urls]
    if not remaining:
        log.info("Alle Karten bereits gescrapt (Resume). Nichts zu tun.")
        return results, stats

    log.info(f"{len(remaining)} Karten zu scrapen (von {total} gesamt)")

    # Process in batches of MAX_PARALLEL
    batches = [remaining[i:i+MAX_PARALLEL] for i in range(0, len(remaining), MAX_PARALLEL)]
    log.info(f"{len(batches)} Batch(es) a max. {MAX_PARALLEL} Karten")

    for batch_num, batch in enumerate(batches):
        # Phase 1: Submit batch async
        log.info(f"\n{'='*50}")
        log.info(f"Batch {batch_num+1}/{len(batches)}: {len(batch)} Requests abschicken...")
        log.info(f"{'='*50}")

        pending = {}  # response_id → card

        for i, card in enumerate(batch):
            card_name = card["name"] or card["url"].split("/")[-1].split("?")[0]
            global_idx = batch_num * MAX_PARALLEL + i + stats["skipped"] + 1
            try:
                resp = requests.post(
                    f"{BD_BASE}/unblocker/req?zone={zone}",
                    headers=headers,
                    json={"url": card["url"], "country": "de"},
                    timeout=15
                )
                if resp.status_code == 200:
                    rid = resp.json().get("response_id", "")
                    pending[rid] = card
                    log.info(f"  [{global_idx}/{total}] {card_name} → {rid[:20]}...")
                else:
                    log.error(f"  [{global_idx}/{total}] {card_name} → HTTP {resp.status_code}: {resp.text[:100]}")
                    results.append({
                        "url": card["url"], "name": card["name"], "notes": card["notes"],
                        "timestamp": timestamp, "error": ERR_API_ERROR,
                    })
                    stats["errors"] += 1
            except Exception as e:
                log.error(f"  [{global_idx}/{total}] {card_name} → Fehler: {e}")
                results.append({
                    "url": card["url"], "name": card["name"], "notes": card["notes"],
                    "timestamp": timestamp, "error": ERR_CRASH, "error_detail": str(e),
                })
                stats["errors"] += 1

        log.info(f"\n{len(pending)} submitted")

        # Phase 2: Poll for batch results
        log.info(f"Ergebnisse pollen ({POLL_INTERVAL}s Intervall)...")

        done_ids = set()
        for attempt in range(MAX_POLL_ATTEMPTS):
            if not pending or len(done_ids) == len(pending):
                break

            time.sleep(POLL_INTERVAL)
            still_pending = len(pending) - len(done_ids)
            log.info(f"\n--- Poll #{attempt+1} | {len(done_ids)}/{len(pending)} fertig, {still_pending} ausstehend ---")

            for rid, card in pending.items():
                if rid in done_ids:
                    continue
                try:
                    resp = requests.get(
                        f"{BD_BASE}/unblocker/get_result?zone={zone}&response_id={rid}",
                        headers={"Authorization": f"Bearer {api_key}"},
                        timeout=15
                    )
                    if resp.status_code == 200:
                        card_name = card["name"] or card["url"].split("/")[-1].split("?")[0]
                        content = resp.text
                        result, status = process_result(content, card, timestamp)
                        results.append(result)
                        stats[status if status in stats else "errors"] += 1
                        completed_urls.add(card["url"])
                        done_ids.add(rid)
                        log.info(f"  [{len(results)}/{total}] {card_name} — {status}")

                        # Live update
                        save_resume_state(completed_urls, results)
                        PRICES_DIR.mkdir(exist_ok=True)
                        with open(LATEST_FILE, "w", encoding="utf-8") as f:
                            json.dump(results, f, indent=2, ensure_ascii=False)

                    elif resp.status_code == 202:
                        pass  # still processing
                    else:
                        card_name = card["name"] or card["url"].split("/")[-1].split("?")[0]
                        log.warning(f"  {card_name}: HTTP {resp.status_code}")

                except Exception as e:
                    log.warning(f"  Poll-Fehler fuer {rid[:20]}: {e}")

        # Handle timed-out requests in this batch
        for rid, card in pending.items():
            if rid not in done_ids:
                card_name = card["name"] or card["url"].split("/")[-1].split("?")[0]
                log.warning(f"  TIMEOUT: {card_name}")
                results.append({
                    "url": card["url"], "name": card["name"], "notes": card["notes"],
                    "timestamp": timestamp, "error": ERR_TIMEOUT,
                })
                stats["errors"] += 1

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

    total_value = sum(r.get("value", 0) or 0 for r in results)

    summary = f"""
{'='*50}
BRIGHT DATA SCRAPE ZUSAMMENFASSUNG ({now.strftime('%d.%m.%Y %H:%M')})
{'='*50}
Karten gesamt:   {len(results)} (+ {stats['skipped']} uebersprungen)
  Erfolgreich:   {stats['ok']}
  Keine Preise:  {stats['no_prices']}
  Cloudflare:    {stats['cloudflare']}
  Fehler:        {stats['errors']}
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
            f"\U0001f4ca <b>Bright Data Scrape Report</b>\n\n"
            f"\u2705 {stats['ok']}/{len(results)} erfolgreich\n"
            f"{chr(10).join(problems)}\n\n"
            f"<b>Betroffene Karten:</b>\n"
            f"{chr(10).join(chr(8226) + ' ' + c for c in failed_cards[:10])}\n\n"
            f"Portfolio: EUR {total_value:,.2f}"
        )
    else:
        send_telegram(
            f"\u2705 <b>Bright Data Scrape OK</b> \u2014 {stats['ok']}/{len(results)} Karten\n"
            f"Portfolio: EUR {total_value:,.2f}"
        )

    return csv_file


def import_to_db(results):
    """Importiert Ergebnisse direkt in die DB (fuer Cron-Jobs ohne Express-Server)."""
    db_path = BASE_DIR / "data" / "tracker.db"
    if not db_path.exists():
        return
    try:
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        # Build URL → card_id lookup
        card_rows = conn.execute("SELECT id, url FROM cards").fetchall()
        url_to_id = {r[1]: r[0] for r in card_rows}

        for r in results:
            card_id = url_to_id.get(r.get("url"))
            if not card_id:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO prices (card_id, scraped_at, value, trend, avg7, avg30, avg1, from_price, available_items, psa10_low, psa9_low, cgc10_low, bgs10_low, error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (card_id, r.get("timestamp", ""), r.get("value"), r.get("trend"), r.get("avg7"), r.get("avg30"), r.get("avg1"), r.get("from"), r.get("available_items"), r.get("psa10_low"), r.get("psa9_low"), r.get("cgc10_low"), r.get("bgs10_low"), r.get("error"))
            )
            if r.get("image"):
                conn.execute("UPDATE cards SET image = ? WHERE id = ? AND (image = '' OR image IS NULL)", (r["image"], card_id))
            name = r.get("name", "")
            if name and "just a moment" not in name.lower() and "cloudflare" not in name.lower():
                conn.execute("UPDATE cards SET name = ? WHERE id = ? AND (name = '' OR name IS NULL)", (name, card_id))
            if r.get("set_name"):
                conn.execute("UPDATE cards SET set_name = ? WHERE id = ? AND (set_name = '' OR set_name IS NULL)", (r["set_name"], card_id))
        conn.commit()
        conn.close()
        log.info(f"DB-Import: {len(results)} Ergebnisse importiert")
    except Exception as e:
        log.error(f"DB-Import fehlgeschlagen: {e}")


def main():
    args = sys.argv[1:]

    if "--dry-run" in args:
        cards = load_portfolio()
        print(f"{len(cards)} Karten in portfolio.csv:")
        for c in cards:
            print(f"  - {c['name'] or c['url']}")
        print(f"\nGeschaetzte Dauer: ~30-60s (Bright Data async parallel)")
        return

    api_key, zone = load_brightdata_config()
    if not api_key:
        log.error("Kein Bright Data API Key! Bitte in Settings oder BRIGHTDATA_API_KEY env setzen.")
        sys.exit(2)

    log.info(f"Bright Data Zone: {zone}")

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

    log.info(f"\n{len(cards)} Karten zu scrapen (Bright Data async parallel)...")

    results, stats = scrape_cards(cards, api_key, zone)
    save_results(results, stats)
    import_to_db(results)

    if stats["ok"] == 0 and len(cards) > 0:
        sys.exit(2)
    elif stats["cloudflare"] > 0 or stats["errors"] > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
