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
    """Send message to all allowed_users."""
    token, _ = load_telegram_config()
    if not token:
        return False
    # Get digest subscribers from allowed_users table
    chat_ids = []
    try:
        db_path = BASE_DIR / "data" / "tracker.db"
        if db_path.exists():
            import sqlite3
            conn = sqlite3.connect(str(db_path))
            conn.execute("CREATE TABLE IF NOT EXISTS allowed_users (telegram_id INTEGER PRIMARY KEY, name TEXT, receives_digest INTEGER NOT NULL DEFAULT 0)")
            # Add column if missing (for existing DBs)
            try:
                conn.execute("ALTER TABLE allowed_users ADD COLUMN receives_digest INTEGER NOT NULL DEFAULT 0")
            except Exception:
                pass
            rows = conn.execute("SELECT telegram_id FROM allowed_users WHERE receives_digest = 1").fetchall()
            chat_ids = [r[0] for r in rows]
            conn.close()
    except Exception:
        pass
    # Fallback to settings chat_id if no allowed_users
    if not chat_ids:
        _, chat_id = load_telegram_config()
        if chat_id:
            chat_ids = [int(chat_id)]
    sent = 0
    for cid in chat_ids:
        try:
            import urllib.request
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            data = json.dumps({"chat_id": cid, "text": message, "parse_mode": "HTML"}).encode()
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10)
            sent += 1
        except Exception as e:
            log.warning(f"Telegram-Fehler (user {cid}): {e}")
    log.info(f"Telegram: sent to {sent}/{len(chat_ids)} users")
    return sent > 0


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
    }
    for key, pattern in patterns.items():
        m = re.search(pattern, content, re.IGNORECASE)
        if m:
            prices[key] = parse_de_price(m.group(1))

    m = re.search(r"(\d+)\s*(?:items|Artikel)", content)
    if m:
        prices["available_items"] = int(m.group(1))

    listing_min = extract_min_listing_price(content)
    if listing_min is not None:
        prices["from"] = listing_min

    prices.update(extract_grade_lows(content))

    return prices


# Listing-row prices use a dedicated bold-primary span; trend/averages use other markup.
# Anchoring on this exact class combo avoids matching unrelated Euro values on the page.
LISTING_PRICE_RE = re.compile(
    r'<span class="color-primary[^"]*fw-bold[^"]*">\s*([\d.,]+)\s*€\s*</span>'
)


def extract_min_listing_price(content):
    prices = []
    for m in LISTING_PRICE_RE.finditer(content):
        p = parse_de_price(m.group(1))
        if p is not None:
            prices.append(p)
    return min(prices) if prices else None


# Cardmarket offer-listing structure: each offer has a comment span (fst-italic small)
# that contains either a pure grade label ("PSA 10", "CGC Pristine 10") or free-form
# seller text ("Perfect condition. Definitely a PSA10 contender."). The grade-low
# extractor must only accept comments that START with a grade label, not text that
# merely mentions a grade somewhere inside a sentence.
LISTING_RE = re.compile(
    r'fst-italic small">(?P<comment>[^<]+)</span>.*?color-primary[^>]*>\s*(?P<price>[\d.,]+)\s*€',
    re.DOTALL,
)
_GRADE_LABEL_PATTERNS = {
    "psa10_low": re.compile(r"^\s*PSA\s*10\b(?!\s*(?:contender|candidate|potential|worthy|ready))", re.IGNORECASE),
    "psa9_low":  re.compile(r"^\s*PSA\s*9\b(?!\d)", re.IGNORECASE),
    "cgc10_low": re.compile(r"^\s*CGC\s*(?:Pristine\s*|Black\s*Label\s*)?10\b", re.IGNORECASE),
    "bgs10_low": re.compile(r"^\s*BGS\s*(?:Pristine\s*|Black\s*Label\s*)?10\b", re.IGNORECASE),
}


def extract_grade_lows(content):
    lows = {}
    for m in LISTING_RE.finditer(content):
        comment = m.group("comment").strip()
        price = parse_de_price(m.group("price"))
        if price is None:
            continue
        for key, pat in _GRADE_LABEL_PATTERNS.items():
            if pat.match(comment):
                if key not in lows or price < lows[key]:
                    lows[key] = price
                break
    return lows


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

def lookup_last_grade_low(card_url, grade_key):
    """Sucht letzten nicht-null Wert von <grade_key> aus prices fuer diese Karte.
    Nutzt URL → card_id Lookup. Gibt None zurueck wenn DB nicht da oder kein Treffer."""
    try:
        import sqlite3
        db_path = BASE_DIR / "data" / "tracker.db"
        if not db_path.exists():
            return None
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            f"SELECT {grade_key} FROM prices p JOIN cards c ON p.card_id = c.id "
            f"WHERE c.url = ? AND {grade_key} IS NOT NULL "
            f"ORDER BY p.scraped_at DESC LIMIT 1",
            (card_url,)
        ).fetchone()
        conn.close()
        return row[0] if row else None
    except Exception as e:
        log.warning(f"  lookup_last_grade_low failed: {e}")
        return None


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
    stale_grade = 0
    if grade_value:
        value = grade_value
    elif grade_key:
        # Graded Karte aber kein matching Listing heute → letzten bekannten grade_low aus DB
        last_known = lookup_last_grade_low(card["url"], grade_key)
        if last_known:
            value = last_known
            stale_grade = 1
            log.info(f"  {grade} heute nicht gelistet → letzten bekannten {grade_key}={last_known} EUR (stale)")
        else:
            value = prices.get("trend") or prices.get("from")
            stale_grade = 1
            log.info(f"  {grade} heute nicht gelistet, kein History-Wert → Fallback Trend={value} (stale)")
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
        "grade": grade, "value": value, "stale_grade": stale_grade, "notes": card["notes"],
        "image": image_file, "timestamp": timestamp, "error": None,
        **prices,
    }

    if grade_value:
        log.info(f"  {grade} Low: EUR {grade_value} | Trend: EUR {prices.get('trend')}")
    elif not stale_grade:
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

    # Load previous prices from DB for change detection
    prev_prices = {}
    alert_pct = 10.0
    alert_eur = 35.0
    try:
        db_path = BASE_DIR / "data" / "tracker.db"
        if db_path.exists():
            import sqlite3
            conn = sqlite3.connect(str(db_path))
            # Get most recent value per card
            rows = conn.execute("""
                SELECT c.url, p.value FROM cards c
                JOIN prices p ON p.card_id = c.id
                WHERE p.value IS NOT NULL
                  AND p.scraped_at = (SELECT MAX(p2.scraped_at) FROM prices p2 WHERE p2.card_id = c.id AND p2.value IS NOT NULL)
            """).fetchall()
            prev_prices = {r[0]: r[1] for r in rows}
            # Get alert thresholds from settings
            thresh = dict(conn.execute("SELECT key, value FROM settings WHERE key IN ('alert_threshold_pct','alert_threshold_eur')").fetchall())
            if thresh.get("alert_threshold_pct"):
                alert_pct = float(thresh["alert_threshold_pct"])
            if thresh.get("alert_threshold_eur"):
                alert_eur = float(thresh["alert_threshold_eur"])
            conn.close()
    except Exception as e:
        log.warning(f"Vorherige Preise laden fehlgeschlagen: {e}")

    log.info(f"Alert-Schwellen: >{alert_eur:.0f} EUR oder >{alert_pct:.0f}%")
    movers = []
    for r in results:
        if r.get("error") or not r.get("value"):
            continue
        url = r["url"]
        new_val = r["value"]
        old_val = prev_prices.get(url)
        if old_val is None or old_val == 0:
            continue
        diff = new_val - old_val
        pct = (diff / old_val) * 100
        if abs(diff) >= alert_eur or abs(pct) >= alert_pct:
            arrow = "\U0001f4c8" if diff > 0 else "\U0001f4c9"
            name = r.get("name") or r["url"].split("/")[-1].split("?")[0]
            sign = "+" if diff > 0 else ""
            movers.append((abs(diff), diff, f"{name}: {old_val:.2f} \u2192 {new_val:.2f}\u20ac ({sign}{diff:.2f}\u20ac / {sign}{pct:.1f}%)"))

    # Split into winners/losers
    winners = [(d, line) for d, diff_val, line in movers if diff_val > 0]
    losers = [(d, line) for d, diff_val, line in movers if diff_val < 0]
    winners.sort(reverse=True)
    losers.sort(reverse=True)

    # Build Telegram message
    msg_parts = [f"\U0001f4ca <b>Scrape Report</b> ({now.strftime('%d.%m.%Y %H:%M')})\n"]
    msg_parts.append(f"\u2705 {stats['ok']}/{len(results)} Karten | Portfolio: EUR {total_value:,.2f}")

    if stats["cloudflare"] > 0 or stats["errors"] > 0 or stats["no_prices"] > 0:
        problems = []
        if stats["cloudflare"]: problems.append(f"\u2601\ufe0f {stats['cloudflare']}x CF")
        if stats["no_prices"]: problems.append(f"\u26a0\ufe0f {stats['no_prices']}x keine Preise")
        if stats["errors"]: problems.append(f"\u274c {stats['errors']}x Fehler")
        msg_parts.append(" | ".join(problems))

    if winners:
        msg_parts.append(f"\n\U0001f4c8 <b>Gewinner ({len(winners)}):</b>")
        for _, line in winners[:8]:
            msg_parts.append(line)
    if losers:
        msg_parts.append(f"\n\U0001f4c9 <b>Verlierer ({len(losers)}):</b>")
        for _, line in losers[:8]:
            msg_parts.append(line)
    if not winners and not losers:
        msg_parts.append("\nKeine grossen Preisbewegungen.")

    send_telegram("\n".join(msg_parts))

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
                "INSERT OR IGNORE INTO prices (card_id, scraped_at, value, trend, avg7, avg30, avg1, from_price, available_items, psa10_low, psa9_low, cgc10_low, bgs10_low, error, stale_grade) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (card_id, r.get("timestamp", ""), r.get("value"), r.get("trend"), r.get("avg7"), r.get("avg30"), r.get("avg1"), r.get("from"), r.get("available_items"), r.get("psa10_low"), r.get("psa9_low"), r.get("cgc10_low"), r.get("bgs10_low"), r.get("error"), r.get("stale_grade", 0))
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
