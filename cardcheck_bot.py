#!/usr/bin/env python3
"""
Pokemon Card Check Bot — Foto schicken, sofort Preischeck bekommen.
Laeuft als Telegram Bot, nutzt GPT Vision + Bright Data + Cardmarket/eBay.
"""

import asyncio
import json
import logging
import re
import time
from io import BytesIO

import requests
import aiohttp
from openai import AsyncOpenAI
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

# ─── Config ──────────────────────────────────────────────────

DB_PATH = "/opt/pokemon-tracker/data/tracker.db"

def load_allowed_users():
    """Laedt erlaubte User IDs aus DB (Tabelle: allowed_users) + Fallback hardcoded."""
    import sqlite3, os
    db = os.environ.get("CARDCHECK_DB", DB_PATH)
    users = set()
    try:
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE IF NOT EXISTS allowed_users (telegram_id INTEGER PRIMARY KEY, name TEXT)")
        rows = conn.execute("SELECT telegram_id FROM allowed_users").fetchall()
        users = {r[0] for r in rows}
        if not users:
            # Seed with Christoph if table is empty
            conn.execute("INSERT OR IGNORE INTO allowed_users (telegram_id, name) VALUES (8416445370, 'Christoph')")
            conn.commit()
            users = {8416445370}
        conn.close()
    except Exception:
        users = {8416445370}
    return users

ALLOWED_USERS = load_allowed_users()

def load_config():
    """Laedt Credentials aus DB settings."""
    import sqlite3, os
    db = os.environ.get("CARDCHECK_DB", DB_PATH)
    cfg = {}
    try:
        conn = sqlite3.connect(db)
        rows = conn.execute("SELECT key, value FROM settings WHERE key IN ('telegram_bot_token','telegram_chat_id','openai_api_key','brightdata_api_key','brightdata_zone','ximilar_api_key')").fetchall()
        cfg = dict(rows)
        conn.close()
    except Exception:
        pass
    return {
        "telegram_token": cfg.get("telegram_bot_token", os.environ.get("TELEGRAM_BOT_TOKEN", "")),
        "openai_key": cfg.get("openai_api_key", os.environ.get("OPENAI_API_KEY", "")),
        "bd_key": cfg.get("brightdata_api_key", os.environ.get("BRIGHTDATA_API_KEY", "")),
        "bd_zone": cfg.get("brightdata_zone", os.environ.get("BRIGHTDATA_ZONE", "cardmarket")),
        "ximilar_key": cfg.get("ximilar_api_key", os.environ.get("XIMILAR_API_KEY", "")),
    }

CONFIG = load_config()
TELEGRAM_TOKEN = CONFIG["telegram_token"]
BD_KEY = CONFIG["bd_key"]
BD_ZONE = CONFIG["bd_zone"]
XIMILAR_KEY = CONFIG["ximilar_key"]

LANG_MAP = {"jp": 7, "en": 1, "de": 3, "fr": 2, "es": 4, "it": 5, "kr": 9, "cn": 10}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("cardcheck")

openai_client = AsyncOpenAI(api_key=CONFIG["openai_key"])

# Ximilar toggle — remove ximilar_api_key from DB to disable (falls back to Vision+TCG+gpt-4o)
if XIMILAR_KEY:
    log.info("Ximilar enabled — hybrid identification (Vision + Ximilar)")
else:
    log.info("Ximilar disabled — using Vision + TCG API + gpt-4o pipeline")

# ─── Currency ────────────────────────────────────────────────

_eur_rates = {"ts": 0, "rates": {}}

def get_eur_rate(currency="JPY"):
    if time.time() - _eur_rates["ts"] > 3600:
        try:
            resp = requests.get("https://api.exchangerate.host/latest?base=EUR", timeout=10)
            data = resp.json()
            if data.get("success") or data.get("rates"):
                _eur_rates["rates"] = data["rates"]
                _eur_rates["ts"] = time.time()
        except Exception as e:
            log.warning(f"Exchange rate fetch failed: {e}")
    rate = _eur_rates["rates"].get(currency, 163.0 if currency == "JPY" else 1.1)
    return rate

def to_eur(amount, currency="JPY"):
    if currency == "EUR":
        return amount
    rate = get_eur_rate(currency)
    return round(amount / rate, 2)

def fmt_eur(val):
    if val is None:
        return "-"
    if val >= 1000:
        return f"{val:,.0f}\u20ac".replace(",", ".")
    return f"{val:.2f}\u20ac"

# ─── Vision: Card Identification ─────────────────────────────

VISION_PROMPT = """Antworte NUR als JSON (kein Markdown):
{
  "pokemon": "Pokemon-Spezies auf Englisch (z.B. Charizard, Umbreon, Giratina)",
  "card_suffix": "ex|EX|V|GX|VMAX|VSTAR|VUNION|none",
  "set_code": "Set-Code von der Karte unten links oder PSA Label (z.B. PRE, WHT, BLK, s11, PFL, m2)",
  "number": "Kartennummer von unten links (z.B. 161/131, 7/102, 94/97) oder null",
  "language": "jp|en|de",
  "grade": "raw|PSA10|PSA9|PSA8|CGC10|BGS10",
  "is_first_edition": false,
  "shop_price": 130000,
  "shop_currency": "JPY|EUR|USD"
}

REGELN:
- pokemon: LIES den Pokemon-Namen DIREKT von der Karte (steht oben). NICHT raten! Nockchan=Hitmonchan, Nachtara=Umbreon, Glurak=Charizard, Mega-Glurak=Mega Charizard, Latios=Latios, Latias=Latias. Bei alten Karten (1999-2007) steht der Name ebenfalls oben.
- card_suffix: Steht "ex", "EX", "V", "GX", "VMAX", "VSTAR" im Kartennamen? Wenn ja → diesen Suffix. Wenn NICHT → "none". WICHTIG: "Victini" ohne Suffix = "none", "Victini ex" = "ex"
- number: IMMER mit Total angeben falls lesbar (z.B. "7/102", "94/97", "161/131"). Steht unten rechts oder links auf der Karte.
- set_code: Steht unten links auf der Karte vor der Nummer (z.B. "PRE 161/131" → PRE) oder auf dem PSA Label. Alte WotC/EX-era Karten (1999-2007) haben KEINEN Text-Code → null
- is_first_edition: true wenn links unter dem Kartenbild ein "1" in einem Kreis steht, oder "1st Edition"/"1. Edition" auf der Karte steht
- grade: PSA/CGC/BGS Label lesen. GEM MT 10=PSA10, MINT 9=PSA9. Kein Slab=raw
- shop_price: Preistag lesen. Punkte=Tausender (¥130.000=130000). null wenn nicht sichtbar"""

TCG_API = "https://api.pokemontcg.io/v2"

def search_tcg_api(pokemon_name):
    """Sucht alle Karten eines Pokemon in der TCG API."""
    try:
        resp = requests.get(
            f"{TCG_API}/cards",
            params={"q": f"name:{pokemon_name}", "select": "id,name,number,set,rarity,images", "pageSize": 50},
            timeout=10
        )
        if resp.status_code == 200:
            return resp.json().get("data", [])
    except Exception as e:
        log.warning(f"  TCG API failed: {e}")
    return []

MATCH_PROMPT = """Hier ist ein Foto einer Pokemon-Karte. Darunter {count} Kandidaten-Bilder.

Welches Bild hat EXAKT dasselbe Artwork UND denselben Kartenrahmen?
- Alte Karten (1999-2007): silber/grauer Rahmen, WotC/EX-era Design
- Moderne Karten (2008+): schwarzer/farbiger Rahmen
- Illustration Rare: Artwork bedeckt die GANZE Karte

{candidates}

Antworte NUR mit der Nummer."""

async def _attempt_tcg_match(tcg_cards, card, b64, vision_number_raw):
    """Try number+total match, then visual match. Modifies card in place. Returns True if matched."""
    if not tcg_cards:
        return False

    # ─── Number+Total deterministic match ───
    # e.g. "7/102" → only one set with 102 printed cards has this Pokemon as #7
    if vision_number_raw and "/" in vision_number_raw:
        parts = vision_number_raw.split("/")
        num_str = re.sub(r"[^\d]", "", parts[0])
        total_str = re.sub(r"[^\d]", "", parts[1])
        if num_str and total_str:
            total_int = int(total_str)
            num_matches = [c for c in tcg_cards if c.get("number") == num_str and c["set"].get("printedTotal") == total_int]
            if len(num_matches) == 1:
                m = num_matches[0]
                log.info(f"  NUMBER_MATCH: {m['name']} #{num_str}/{total_int} from {m['set']['name']}")
                card["name"] = m.get("name", "")
                card["set"] = m["set"]["name"]
                card["set_code"] = m["set"]["id"]
                card["number"] = m.get("number", "")
                card["tcg_id"] = m["id"]
                card["ptcgo_code"] = m["set"].get("ptcgoCode", "")
                return True
            elif num_matches:
                log.info(f"  NUMBER_MATCH: {len(num_matches)} ambiguous for #{num_str}/{total_int} — narrowing candidates")
                tcg_cards = num_matches

    # ─── Pre-filter + gpt-4o visual match ───
    special, regular = [], []
    for c in tcg_cards:
        img = c.get("images", {}).get("large", c.get("images", {}).get("small", ""))
        if not img:
            continue
        year = c["set"].get("releaseDate", "")[:4]
        entry = {
            "id": c["id"], "name": c.get("name", ""), "number": c.get("number", ""),
            "set_name": c["set"]["name"], "set_id": c["set"]["id"],
            "ptcgo_code": c["set"].get("ptcgoCode", ""),
            "rarity": c.get("rarity", ""), "img": img, "year": year,
        }
        try:
            num = int(re.match(r"\d+", c.get("number", "0")).group())
        except (ValueError, AttributeError):
            num = 0
        total = c["set"].get("printedTotal", 999)
        rarity = c.get("rarity", "").lower()
        is_special = num > total or any(r in rarity for r in ["ultra", "rainbow", "illustration", "secret", "prism", "full art", "double", "black white", "hyper", "holo ex", "holo gx", "holo star"])
        (special if is_special else regular).append(entry)

    candidates = special + regular[:max(5, 15 - len(special))]

    # ─── Filter by card_suffix from Vision (e.g. "none" → exclude "ex" cards) ───
    vision_suffix = (card.get("card_suffix") or "none").lower()
    if vision_suffix != "none" and len(candidates) > 3:
        # Vision detected a suffix (ex, V, GX) → keep only cards with that suffix in name
        suffix_map = {"ex": [" ex", "-EX"], "v": [" V", "-V"], "gx": [" GX", "-GX"],
                       "vmax": ["VMAX"], "vstar": ["VSTAR"]}
        patterns = suffix_map.get(vision_suffix, [vision_suffix])
        filtered = [c for c in candidates if any(p.lower() in c["name"].lower() for p in patterns)]
        if filtered:
            log.info(f"  SUFFIX_FILTER: '{vision_suffix}' → {len(candidates)} → {len(filtered)} candidates")
            candidates = filtered
    elif vision_suffix == "none" and len(candidates) > 3:
        # Vision says NO suffix → prefer cards without ex/V/GX in name
        suffix_words = [" ex", "-EX", " V", "-V", " GX", "-GX", "VMAX", "VSTAR"]
        no_suffix = [c for c in candidates if not any(s.lower() in c["name"].lower() for s in suffix_words)]
        if no_suffix and len(no_suffix) >= 3:
            log.info(f"  SUFFIX_FILTER: 'none' → {len(candidates)} → {len(no_suffix)} candidates (removed ex/V/GX)")
            candidates = no_suffix

    for i, c in enumerate(candidates):
        c["idx"] = i + 1

    log.info(f"  CANDIDATES: {len(special)} special + {len(regular)} regular → {len(candidates)} for matching")

    if not candidates:
        return False

    cand_text = "\n".join(f"{c['idx']}. {c['name']} #{c['number']} ({c['set_name']}, {c.get('year','?')}) [{c['rarity']}]" for c in candidates)
    cand_images = [{"type": "image_url", "image_url": {"url": c["img"], "detail": "low"}} for c in candidates]

    log.info(f"  MATCH: sending {len(cand_images)} thumbnails to gpt-4o...")
    match_resp = await openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": [
            {"type": "text", "text": MATCH_PROMPT.format(count=len(candidates), candidates=cand_text)},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"}},
        ] + cand_images}],
        max_completion_tokens=20, temperature=0,
    )
    answer = match_resp.choices[0].message.content.strip()
    log.info(f"  MATCH: gpt-4o says '{answer}'")

    num_match = re.search(r"\d+", answer)
    if num_match:
        idx = int(num_match.group()) - 1
        if 0 <= idx < len(candidates):
            matched = candidates[idx]
            log.info(f"  IDENTIFIED: {matched['name']} #{matched['number']} from {matched['set_name']} ({matched['set_id']})")
            card["name"] = matched["name"]
            card["set"] = matched["set_name"]
            card["set_code"] = matched["set_id"]
            card["number"] = matched["number"]
            card["tcg_id"] = matched["id"]
            card["ptcgo_code"] = matched.get("ptcgo_code", "")
            return True

    log.warning(f"  MATCH: no valid match from gpt-4o: '{answer}'")
    return False


async def _ximilar_identify(b64, set_code_hint=""):
    """Identify card via Ximilar API. Returns best match dict or None."""
    if not XIMILAR_KEY:
        return None
    try:
        body = {"records": [{"_base64": b64}], "lang": True, "slab_grade": True}
        if set_code_hint:
            body["records"][0]["set_code"] = set_code_hint
        async with aiohttp.ClientSession() as session:
            async with session.post("https://api.ximilar.com/collectibles/v2/tcg_id",
                headers={"Content-Type": "application/json", "Authorization": f"Token {XIMILAR_KEY}"},
                json=body, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    log.warning(f"  XIMILAR: HTTP {resp.status}")
                    return None
                data = await resp.json()
        for rec in data.get("records", []):
            for obj in rec.get("_objects", []):
                ident = obj.get("_identification", {})
                best = ident.get("best_match")
                if best and best.get("name"):
                    return {
                        "best": best,
                        "alternatives": ident.get("alternatives", []),
                        "prob": obj.get("prob", 0),
                        "tags": obj.get("_tags", {}),
                        "cm_link": best.get("links", {}).get("cardmarket.com", ""),
                    }
    except Exception as e:
        log.warning(f"  XIMILAR: {e}")
    return None


async def identify_card(photo_bytes):
    import base64
    b64 = base64.b64encode(photo_bytes).decode()

    # ─── Step 1: Vision + Ximilar in PARALLEL ───
    async def _vision():
        r = await openai_client.chat.completions.create(
            model="gpt-5.4-mini",
            messages=[{"role": "user", "content": [
                {"type": "text", "text": VISION_PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"}}
            ]}],
            max_completion_tokens=300, temperature=0,
        )
        text = re.sub(r"^```json?\s*|\s*```$", "", r.choices[0].message.content.strip())
        return json.loads(text)

    # First Ximilar call without hint (parallel with Vision)
    vision_task = asyncio.ensure_future(_vision())
    ximilar_task = asyncio.ensure_future(_ximilar_identify(b64))
    card, ximilar_result = await asyncio.gather(vision_task, ximilar_task)

    pokemon_name = card.get("pokemon", card.get("name", "")).strip()
    card["vision_set_code"] = card.get("set_code", "")
    vision_sc = card.get("vision_set_code") or ""
    if vision_sc.lower() in ("none", "null", "basis", "basic", "stage", "stufe"):
        vision_sc = ""
        card["vision_set_code"] = ""
    vision_num = re.sub(r"[^\d]", "", card.get("number", "").split("/")[0]) if card.get("number") else ""

    log.info(f"  VISION: pokemon='{pokemon_name}' set_code='{vision_sc}' num='{vision_num}' lang={card.get('language')} grade={card.get('grade')}")

    if not pokemon_name:
        return card

    # ─── Step 2: If Vision has set_code → Quick CM (fastest path, handles JP sets) ───
    if vision_sc and vision_num and len(vision_sc) <= 6:
        for search_q in [f"{pokemon_name} {vision_sc}", f"{vision_sc}{vision_num}"]:
            log.info(f"  QUICK_CM: trying '{search_q}'...")
            quick_results = await search_cardmarket(search_q)
            for url in quick_results:
                slug = url.split("/")[-1]
                # Match: slug ends with vision_num (exact match, no digit-predecessor check
                # because set codes like "s11" + number "111" → "s11111" are valid)
                if slug.endswith(vision_num):
                    log.info(f"  QUICK_CM: FOUND {slug}")
                    card["name"] = re.sub(r"-V\d.*", "", slug).replace("-", " ")
                    card["set"] = url.split("/Singles/")[-1].split("/")[0].replace("-", " ") if "/Singles/" in url else ""
                    card["number"] = vision_num
                    card["cm_url_override"] = url
                    return card

    # ─── Step 3: Ximilar result → use if available ───
    # If Vision had a set_code, retry Ximilar with hint for better accuracy
    if ximilar_result and vision_sc and ximilar_result["best"].get("set_code") != vision_sc:
        log.info(f"  XIMILAR: retrying with set_code hint '{vision_sc}'...")
        ximilar_result = await _ximilar_identify(b64, set_code_hint=vision_sc)

    if ximilar_result and ximilar_result["prob"] > 0.75:
        best = ximilar_result["best"]
        log.info(f"  XIMILAR: {best.get('full_name')} prob={ximilar_result['prob']:.2f}")

        # Cross-reference with Vision's number to pick right alternative
        # Only if Vision read X/Y format AND the name matches (avoid picking wrong Pokemon)
        vision_number_raw = card.get("number") or ""
        picked = best
        if "/" in vision_number_raw:
            v_num = re.sub(r"[^\d]", "", vision_number_raw.split("/")[0])
            best_name_lower = (best.get("name") or "").lower()
            if v_num and str(best.get("card_number")) != v_num:
                for alt in ximilar_result["alternatives"]:
                    alt_name_lower = (alt.get("name") or "").lower()
                    # Only pick alt if same Pokemon species AND number matches
                    if str(alt.get("card_number")) == v_num and best_name_lower.split()[0] in alt_name_lower:
                        log.info(f"  XIMILAR: alt match by number #{v_num}: {alt.get('full_name')}")
                        picked = alt
                        break

        card["name"] = picked.get("name", "")
        card["set"] = picked.get("set", "")
        card["set_code"] = picked.get("set_code", "")
        card["number"] = str(picked.get("card_number", ""))
        card["ximilar_id"] = True
        card["ptcgo_code"] = picked.get("set_code", "")
        # Only use CM product URL for best_match (alt's product ID may point to wrong variant)
        if picked is best:
            cm_link = picked.get("links", {}).get("cardmarket.com", "")
            if cm_link and "idProduct=" in cm_link:
                card["cm_product_url"] = cm_link
        # Save partner-set alternatives for CM search fallback (WHT/BLK etc.)
        partner_alts = [a for a in ximilar_result["alternatives"]
                        if a.get("name") == picked.get("name") and str(a.get("card_number")) == str(picked.get("card_number"))]
        if partner_alts:
            card["ximilar_partner_sets"] = [a.get("set_code", "") for a in partner_alts]
            log.info(f"  XIMILAR: partner sets: {card['ximilar_partner_sets']}")
        return card
    else:
        log.info(f"  XIMILAR: no result or low confidence — falling back to TCG API pipeline")

    # ─── Step 4: Fallback — Quick CM with gpt-4o reident (for JP cards Ximilar missed) ───
    if vision_sc and vision_num:
        log.info(f"  QUICK_CM failed + XIMILAR missed — trying gpt-4o re-identification...")
        retry_resp = await openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": [
                {"type": "text", "text": "What Pokemon is on this card? Give the FULL card name in English, including Mega, VSTAR, ex etc. One line only."},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"}}
            ]}],
            max_completion_tokens=20, temperature=0)
        new_name = retry_resp.choices[0].message.content.strip().split("\n")[0].strip()
        log.info(f"  GPT4O_REIDENT: '{new_name}' (was '{pokemon_name}')")
        if new_name.lower() != pokemon_name.lower():
            pokemon_name = new_name
            card["pokemon"] = pokemon_name
        for search_q in [f"{pokemon_name} {vision_sc}", f"{pokemon_name} ex {vision_sc}"]:
            log.info(f"  QUICK_CM retry: '{search_q}'...")
            quick_results = await search_cardmarket(search_q)
            for url in quick_results:
                slug = url.split("/")[-1]
                if slug.endswith(vision_num):
                    log.info(f"  QUICK_CM retry: FOUND {slug}")
                    card["name"] = re.sub(r"-V\d.*", "", slug).replace("-", " ")
                    card["set"] = url.split("/Singles/")[-1].split("/")[0].replace("-", " ") if "/Singles/" in url else ""
                    card["number"] = vision_num
                    card["cm_url_override"] = url
                    return card

    # ─── Step 5: Last resort — TCG API + Number/Visual match ───
    vision_number_raw = card.get("number") or ""
    tcg_cards = await asyncio.get_event_loop().run_in_executor(None, search_tcg_api, pokemon_name)
    if tcg_cards:
        matched = await _attempt_tcg_match(tcg_cards, card, b64, vision_number_raw)
        if not matched:
            # Reident + retry
            retry_resp = await openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": [
                    {"type": "text", "text": "What Pokemon species is on this card? English name only. One word."},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"}}
                ]}],
                max_completion_tokens=20, temperature=0)
            reident_name = retry_resp.choices[0].message.content.strip().split()[0].strip(".,!\"'")
            if reident_name.lower() != pokemon_name.lower():
                log.info(f"  REIDENT_RETRY: '{reident_name}' (was '{pokemon_name}')")
                card["pokemon"] = reident_name
                retry_tcg = await asyncio.get_event_loop().run_in_executor(None, search_tcg_api, reident_name)
                if retry_tcg:
                    await _attempt_tcg_match(retry_tcg, card, b64, vision_number_raw)

    return card

# ─── Bright Data Scraping ────────────────────────────────────

# Limit concurrent BD requests to avoid throttling when processing multiple cards
_bd_semaphore = asyncio.Semaphore(5)
_bd_session = None

async def _get_bd_session():
    global _bd_session
    if _bd_session is None or _bd_session.closed:
        _bd_session = aiohttp.ClientSession(
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {BD_KEY}"},
            timeout=aiohttp.ClientTimeout(total=90),
        )
    return _bd_session

async def bd_scrape(url):
    async with _bd_semaphore:
        try:
            session = await _get_bd_session()
            async with session.post("https://api.brightdata.com/request",
                json={"zone": BD_ZONE, "url": url, "format": "raw", "country": "de"}) as resp:
                if resp.status != 200:
                    return None
                return await resp.text()
        except asyncio.TimeoutError:
            log.warning(f"  BD timeout: {url[:80]}")
            return None
        except Exception as e:
            log.warning(f"  BD error: {e}")
            return None

def bd_scrape_sync(url):
    """Sync version for use in run_in_executor contexts."""
    resp = requests.post("https://api.brightdata.com/request",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {BD_KEY}"},
        json={"zone": BD_ZONE, "url": url, "format": "raw", "country": "de"},
        timeout=90)
    if resp.status_code != 200:
        return None
    return resp.text

def parse_de_price(s):
    if not s:
        return None
    return float(s.replace(".", "").replace(",", "."))

def extract_prices(html):
    p = {}
    for key, pat in [
        ("from", r"(?:From|ab)[^€]*?([\d.,]+)\s*€"),
        ("trend", r"(?:Price Trend|Preis-Trend)[^€]*?([\d.,]+)\s*€"),
        ("avg7", r"(?:7-days|7-Tages)[^€]*?([\d.,]+)\s*€"),
        ("avg30", r"(?:30-days|30-Tages)[^€]*?([\d.,]+)\s*€"),
        ("psa10", r"PSA\s*10[^€]*?([\d.,]+)\s*€"),
        ("psa9", r"PSA\s*9(?!\d)[^€]*?([\d.,]+)\s*€"),
        ("cgc10", r"CGC\s*10[^€]*?([\d.,]+)\s*€"),
        ("bgs10", r"BGS\s*10[^€]*?([\d.,]+)\s*€"),
    ]:
        m = re.search(pat, html, re.I)
        if m:
            p[key] = parse_de_price(m.group(1))
    items = re.search(r"(\d+)\s*(?:items|Artikel)", html)
    if items:
        p["items"] = int(items.group(1))
    title = re.search(r"<title>(.*?)</title>", html)
    if title:
        p["title"] = title.group(1).replace(" | Cardmarket", "")
    return p

# ─── Cardmarket Search + Scrape ──────────────────────────────

async def search_cardmarket(query):
    url = f"https://www.cardmarket.com/en/Pokemon/Products/Search?searchString={requests.utils.quote(query)}"
    html = await bd_scrape(url)
    if not html:
        return []
    links = re.findall(r'href="(/en/Pokemon/Products/Singles/[^"?]+)"', html)
    seen = []
    seen_set = set()
    for link in links:
        path = link.split("?")[0]
        if path in seen_set:
            continue
        seen_set.add(path)
        seen.append("https://www.cardmarket.com" + path)
    return seen

async def find_cardmarket_url(card_info):
    """Findet die richtige Cardmarket-URL basierend auf TCG API Daten."""
    if card_info.get("cm_url_override"):
        return card_info["cm_url_override"]

    # Ximilar CM product ID URL — fetch it to get the real Singles URL via og:url
    if card_info.get("cm_product_url"):
        log.info(f"  CM_PRODUCT: {card_info['cm_product_url']}")
        html = await bd_scrape(card_info["cm_product_url"])
        if html:
            og = re.search(r'property="og:url"[^>]*content="([^"]+Singles/[^"]+)"', html)
            if og:
                real_url = og.group(1)
                log.info(f"  CM_PRODUCT: resolved → {real_url.split('/')[-1]}")
                return real_url
    name = card_info.get("name", "")
    set_name = card_info.get("set", "")
    number = card_info.get("number", "").split("/")[0]
    pokemon = card_info.get("pokemon", name.split(" ex")[0].split(" V")[0].split(" GX")[0].strip())

    # Search Cardmarket — use vision's set_code (e.g. PRE, WHT) which CM understands
    vision_set_code = card_info.get("vision_set_code", "")
    ptcgo_code = card_info.get("ptcgo_code", "")
    queries = []
    # For TCG-matched cards with ptcgo_code: construct expected CM slug (e.g. "Latios-ex-DR94")
    if ptcgo_code and number:
        queries.append(f"{name.replace(' ', '-')}-{ptcgo_code}{number}")
    if vision_set_code:
        queries.append(f"{name} {vision_set_code}")
    if ptcgo_code and ptcgo_code != vision_set_code:
        queries.append(f"{name} {ptcgo_code}")
    queries += [f"{name} {set_name}", name, pokemon]
    results = []
    seen = set()
    for q in queries:
        log.info(f"  CM_SEARCH: '{q}'")
        for url in await search_cardmarket(q):
            if url not in seen:
                seen.add(url)
                results.append(url)
        # Stop early if we found a number match
        if number and any(number in url.split("/")[-1] for url in results):
            break

    log.info(f"  CM_RESULTS: {len(results)} — {[u.split('/')[-1] for u in results[:8]]}")

    if not results:
        return None

    # Match by card number + pokemon name in URL (try +/-2 for regional numbering)
    name_slug = pokemon.lower().split()[0]
    if number:
        try_numbers = [number] + ([str(int(number)+d) for d in [-1,1,-2,2]] if number.isdigit() else [])
        for tn in try_numbers:
            for url in results:
                slug = url.split("/")[-1].lower()
                if name_slug in slug and slug.endswith(tn):
                    # Ensure it's an exact number match, not a suffix (e.g. "7" in "107")
                    pos = len(slug) - len(tn)
                    if pos > 0 and slug[pos - 1].isdigit():
                        continue  # "MEW107" endswith "7" but it's actually 107
                    log.info(f"  CM_MATCH: '{name_slug}'+{tn} → {url.split('/')[-1]}")
                    return url

    # Direct URL construction for cards where search fails
    if ptcgo_code and number:
        name_slug_d = name.replace(" ", "-")
        set_slug = set_name.replace(" ", "-")
        # Try multiple slug variants (uppercase/lowercase set_code, with/without EX- prefix)
        pc_variants = list(set([ptcgo_code, ptcgo_code.lower(), ptcgo_code.upper()]))
        card_slugs = [f"{name_slug_d}-{pc}{number}" for pc in pc_variants]
        set_prefixes = [set_slug, f"EX-{set_slug}"]
        for card_s in card_slugs:
            for set_prefix in set_prefixes:
                direct_url = f"https://www.cardmarket.com/en/Pokemon/Products/Singles/{set_prefix}/{card_s}"
                log.info(f"  CM_DIRECT: {direct_url}")
                html = await bd_scrape(direct_url)
                # Strict check: page title must contain the card name (not a generic search page)
                if html and "404" not in html[:500]:
                    title_m = re.search(r"<title>(.*?)</title>", html)
                    if title_m and name.split()[0].lower() in title_m.group(1).lower() and "Singles |" not in title_m.group(1):
                        log.info(f"  CM_DIRECT: FOUND!")
                        return direct_url

    # Try partner sets (e.g. WHT → BLK for twin JP sets)
    partner_sets = card_info.get("ximilar_partner_sets", [])
    if partner_sets and number:
        for ps in partner_sets:
            log.info(f"  CM_PARTNER: trying set_code '{ps}'...")
            ps_results = await search_cardmarket(f"{name} {ps}")
            for url in ps_results:
                slug = url.split("/")[-1].lower()
                if name_slug in slug and slug.endswith(number):
                    pos = len(slug) - len(number)
                    if pos > 0 and slug[pos - 1].isdigit():
                        continue
                    log.info(f"  CM_PARTNER: FOUND {url.split('/')[-1]}")
                    return url

    # Fallback: first result
    if results:
        log.info(f"  CM_FALLBACK: using first result {results[0].split('/')[-1]}")
        return results[0]
    return None

async def scrape_cardmarket_prices(card_info):
    """Scrapt Cardmarket Preise fuer eine Karte."""
    url = await find_cardmarket_url(card_info)
    if not url:
        return None, None, None

    lang_code = LANG_MAP.get(card_info.get("language", "jp"), 7)
    grade = card_info.get("grade", "raw")

    # Build URL with filters
    params = f"language={lang_code}"
    is_graded = grade.startswith("PSA") or grade.startswith("CGC") or grade.startswith("BGS")
    if is_graded:
        params += "&minCondition=1&isGraded=Y"
    else:
        # Use caption condition if provided, otherwise default NM
        min_cond = card_info.get("min_condition", 2)
        params += f"&minCondition={min_cond}"
    if card_info.get("is_first_edition"):
        params += "&isFirstEd=Y"

    separator = "&" if "?" in url else "?"
    full_url = f"{url}{separator}{params}"

    log.info(f"  Scraping: {full_url}")
    html = await bd_scrape(full_url)
    if not html:
        return url, None, full_url

    prices = extract_prices(html)
    return url, prices, full_url

# ─── eBay Fallback ───────────────────────────────────────────

async def scrape_ebay_sold(query):
    """Scrapt eBay Sold Listings und berechnet Median."""
    terms = re.sub(r"[^\w\s]", " ", query).strip().replace(" ", "+")
    url = f"https://www.ebay.com/sch/i.html?_nkw={terms}+-psa+-cgc+-bgs+-graded&LH_Complete=1&LH_Sold=1&_sop=13"
    html = await bd_scrape(url)
    if not html:
        return None

    card_starts = [m.start() for m in re.finditer(r'class="s-card__link', html)]
    prices = []
    for i in range(len(card_starts)):
        start = card_starts[i]
        end = card_starts[i + 1] if i + 1 < len(card_starts) else start + 5000
        block = html[start:end]
        if "Shop on eBay" in block:
            continue
        price_m = re.search(r'\$([\d,]+\.\d{2})', block)
        if not price_m:
            continue
        price = float(price_m.group(1).replace(",", ""))
        if price >= 10:
            prices.append(price)

    if not prices:
        return None

    prices.sort()
    median = prices[len(prices) // 2]
    # Filter outliers
    filtered = [p for p in prices if median / 4 < p < median * 4]
    if not filtered:
        return None

    f_median = filtered[len(filtered) // 2]
    f_avg = sum(filtered) / len(filtered)

    return {
        "count": len(filtered),
        "median_usd": f_median,
        "avg_usd": round(f_avg, 2),
        "min_usd": min(filtered),
        "max_usd": max(filtered),
    }

# ─── Verdict ─────────────────────────────────────────────────

def calculate_verdict(shop_eur, market_eur):
    """Berechnet DEAL/FAIR/SKIP."""
    if shop_eur is None or market_eur is None or market_eur == 0:
        return None, None
    diff_pct = ((shop_eur - market_eur) / market_eur) * 100
    if diff_pct < -20:
        return "DEAL \u2705", diff_pct
    elif diff_pct <= 10:
        return "FAIR \U0001f7e1", diff_pct
    else:
        return "SKIP \u274c", diff_pct

# ─── Caption Parsing ────────────────────────────────────────

# Cardmarket condition codes → minCondition values
CONDITION_MAP = {
    "MT": 1, "MINT": 1,
    "NM": 2, "NEARMINT": 2,
    "EX": 3, "EXCELLENT": 3,
    "GD": 4, "GOOD": 4,
    "LP": 5, "LIGHTPLAYED": 5,
    "PL": 6, "PLAYED": 6,
    "PO": 7, "POOR": 7,
}

def parse_caption(caption):
    """Parse condition, 1st edition, and set code from photo caption.
    Examples: "LP", "1st", "LP 1st", "BLK", "PRE LP"
    """
    result = {}
    if not caption:
        return result
    upper = caption.upper().strip()
    words = upper.split()
    # Check for 1st Edition
    if any(x in upper for x in ["1ST", "FIRST ED", "1. ED"]):
        result["is_first_edition"] = True
    # Check for condition code
    for code, val in CONDITION_MAP.items():
        if code in words:
            result["min_condition"] = val
            result["condition_label"] = code
            break
    # Remaining words that aren't conditions or "1st" → treat as set_code
    skip = set(CONDITION_MAP.keys()) | {"1ST", "FIRST", "ED", "1.", "EDITION"}
    for w in words:
        if w not in skip and len(w) >= 2 and w.isalnum():
            result["set_code"] = w
            break
    return result

# ─── Main Handler ────────────────────────────────────────────

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ALLOWED_USERS:
        user_name = update.effective_user.first_name or "?"
        log.info(f"BLOCKED: user_id={user_id} name='{user_name}' — not in allowed_users")
        return

    msg = update.message
    if not msg or not msg.photo:
        return

    # Fire and forget — allows parallel processing of multiple photos
    asyncio.create_task(_process_photo(msg, context))

async def _process_photo(msg, context):
    check_id = f"CHK-{int(time.time())}-{msg.message_id}"
    try:
        # 1. Download photo
        photo = msg.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        bio = BytesIO()
        await file.download_to_memory(bio)
        photo_bytes = bio.getvalue()
        log.info(f"[{check_id}] Photo received ({len(photo_bytes)} bytes, {photo.width}x{photo.height})")

        # 1b. Parse caption for condition + 1st edition
        caption_opts = parse_caption(msg.caption)
        if caption_opts:
            log.info(f"[{check_id}] CAPTION: {caption_opts}")

        # 2. Identify card via Vision
        t0 = time.time()
        card = await identify_card(photo_bytes)
        # Apply caption overrides
        if caption_opts.get("is_first_edition"):
            card["is_first_edition"] = True
        if caption_opts.get("min_condition"):
            card["min_condition"] = caption_opts["min_condition"]
            card["condition_label"] = caption_opts.get("condition_label", "")
        if caption_opts.get("set_code"):
            card["vision_set_code"] = caption_opts["set_code"]
            log.info(f"[{check_id}] CAPTION set_code override: {caption_opts['set_code']}")
        vision_ms = int((time.time() - t0) * 1000)
        log.info(f"[{check_id}] VISION ({vision_ms}ms): {json.dumps(card, ensure_ascii=False)}")

        name = card.get("name", "?")
        version = card.get("version", "")
        set_name = card.get("set", "?")
        number = card.get("number", "")
        language = card.get("language", "jp")
        grade = card.get("grade", "raw")
        shop_price = card.get("shop_price")
        shop_currency = card.get("shop_currency", "JPY")
        set_code = card.get("set_code", "")

        log.info(f"[{check_id}] CARD: {name} {version} ({set_name}/{set_code}) #{number} {language} {grade} shop={shop_price}{shop_currency}")

        # Version label
        ver_label = f" {version.upper()}" if version and version != "regular" else ""
        grade_label = f" {grade.upper()}" if grade and grade != "raw" else ""
        lang_label = language.upper()

        cond_label = f" [{card.get('condition_label', '')}]" if card.get("condition_label") else ""
        first_ed_label = " 1st Ed." if card.get("is_first_edition") else ""
        header = f"<b>{name}{ver_label}</b> ({set_name}) {lang_label}{grade_label}{first_ed_label}{cond_label}"
        if number:
            header += f"\n#{number}"

        # Shop price in EUR
        shop_eur = None
        shop_line = ""
        if shop_price:
            shop_eur = to_eur(shop_price, shop_currency)
            if shop_currency != "EUR":
                shop_line = f"\nShop: {shop_currency} {shop_price:,.0f} ({fmt_eur(shop_eur)})"
            else:
                shop_line = f"\nShop: {fmt_eur(shop_eur)}"

        # 3. Scrape Cardmarket
        t1 = time.time()
        cm_url, prices, cm_full_url = await scrape_cardmarket_prices(card)
        scrape_ms = int((time.time() - t1) * 1000)
        log.info(f"[{check_id}] SCRAPE ({scrape_ms}ms): url={cm_full_url} prices={json.dumps({k:v for k,v in (prices or {}).items() if k != 'title'})}")

        cm_line = ""
        market_eur = None
        psa_line = ""

        if prices and (prices.get("from") or prices.get("trend")):
            from_p = prices.get("from")
            trend_p = prices.get("trend")
            cm_parts = []
            if from_p is not None:
                cm_parts.append(f"ab {fmt_eur(from_p)}")
            if trend_p is not None:
                cm_parts.append(f"Trend {fmt_eur(trend_p)}")
            cm_line = f"\nCM: {' | '.join(cm_parts)}"

            # PSA prices
            psa_parts = []
            if prices.get("psa10"):
                psa_parts.append(f"PSA10: {fmt_eur(prices['psa10'])}")
            if prices.get("psa9"):
                psa_parts.append(f"PSA9: {fmt_eur(prices['psa9'])}")
            if prices.get("cgc10"):
                psa_parts.append(f"CGC10: {fmt_eur(prices['cgc10'])}")
            if psa_parts:
                psa_line = f"\n{' | '.join(psa_parts)}"

            # Determine market price for verdict
            if grade in ("PSA10",) and prices.get("psa10"):
                market_eur = prices["psa10"]
            elif grade in ("PSA9",) and prices.get("psa9"):
                market_eur = prices["psa9"]
            elif grade in ("CGC10",) and prices.get("cgc10"):
                market_eur = prices["cgc10"]
            elif grade in ("BGS10",) and prices.get("bgs10"):
                market_eur = prices["bgs10"]
            elif from_p is not None:
                market_eur = from_p
            elif trend_p is not None:
                market_eur = trend_p

        # 4. eBay fallback if no CM prices
        ebay_line = ""
        if not prices or (not prices.get("from") and not prices.get("trend")):
            log.info(f"[{check_id}] EBAY_FALLBACK: no CM prices, searching eBay...")
            lang_word = {"jp": "japanese", "en": "english", "de": "german", "kr": "korean"}.get(language, "")
            ebay_query = f"{name} {number} {set_name} {lang_word}".strip()
            log.info(f"[{check_id}] EBAY_QUERY: {ebay_query}")
            ebay = await scrape_ebay_sold(ebay_query)
            if ebay:
                median_eur = to_eur(ebay["median_usd"], "USD")
                ebay_line = f"\neBay Sold ({ebay['count']}x): Median ${ebay['median_usd']:,.0f} ({fmt_eur(median_eur)})"
                if market_eur is None:
                    market_eur = median_eur
                cm_line = "\nCM: keine Daten fuer {lang_label}".format(lang_label=lang_label)
                log.info(f"[{check_id}] EBAY_RESULT: {ebay['count']} sold, median=${ebay['median_usd']}")
            else:
                cm_line = f"\nCM: keine Daten | eBay: keine Daten"
                log.info(f"[{check_id}] EBAY_RESULT: nothing found")

        # 5. Verdict
        verdict_line = ""
        verdict, diff_pct = calculate_verdict(shop_eur, market_eur)
        if verdict and shop_eur:
            sign = "+" if diff_pct > 0 else ""
            verdict_line = f"\n\u2192 <b>{verdict}</b> ({sign}{diff_pct:.0f}%)"
        elif not shop_price:
            if market_eur:
                verdict_line = f"\n\u2192 Marktpreis: {fmt_eur(market_eur)}"

        total_ms = int((time.time() - t0) * 1000)
        log.info(f"[{check_id}] VERDICT: shop={shop_eur}EUR market={market_eur}EUR verdict={verdict} diff={diff_pct}% total={total_ms}ms")

        # 6. Build reply
        total_sec = round(time.time() - t0)
        reply = f"{header}{shop_line}{cm_line}{psa_line}{ebay_line}{verdict_line}"

        link_url = cm_full_url or cm_url
        if link_url:
            reply += f'\n\n<a href="{link_url}">Cardmarket</a>'
        reply += f"  <i>({total_sec}s)</i>"

        await msg.reply_text(reply, parse_mode="HTML", disable_web_page_preview=True, reply_to_message_id=msg.message_id)

    except Exception as e:
        log.error(f"[{check_id}] ERROR: {e}", exc_info=True)
        await msg.reply_text(f"\u274c Fehler: {e}", reply_to_message_id=msg.message_id)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages — just acknowledge."""
    user_id = update.effective_user.id
    if user_id not in ALLOWED_USERS:
        return
    text = (update.message.text or "").strip().lower()
    if text in ("hi", "hallo", "ping"):
        await update.message.reply_text("\U0001f4f8 Schick mir ein Kartenfoto fuer einen Preischeck!")

# ─── Main ────────────────────────────────────────────────────

def main():
    log.info("Starting Card Check Bot...")
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    log.info("Bot ready. Waiting for photos...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
