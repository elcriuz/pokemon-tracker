#!/usr/bin/env python3
"""
Pokemon Card Check Bot — Foto schicken, sofort Preischeck bekommen.
Laeuft als Telegram Bot, nutzt GPT Vision + Bright Data + Cardmarket/eBay.
"""

import asyncio
import json
import logging
import os
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

# Cardvision — self-hosted MobileCLIP image-retrieval service (replaces Ximilar's image match).
# When Ximilar is off, _ximilar_identify() delegates to the local cardvision service, which
# returns catalog candidates that feed the SAME downstream (number cross-ref, CM search).
CARDVISION_URL = os.environ.get("CARDVISION_URL", "http://127.0.0.1:8099")
USE_CARDVISION = os.environ.get("USE_CARDVISION", "1") == "1"
if not XIMILAR_KEY and USE_CARDVISION:
    log.info(f"Cardvision enabled — image retrieval via {CARDVISION_URL}")

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

# ─── Match Quality Helpers ─────────────────────────────────

# Words that frame a Pokemon's name but aren't the species: card type suffixes,
# region/form prefixes, "Team Rocket's", "Dark X", etc. Used by pokemon_tokens() to
# strip everything down to the actual species so two names can be compared meaningfully.
_NAME_NOISE = {
    "ex", "gx", "v", "vmax", "vstar", "vunion", "tag", "team",
    "mega", "shining", "crystal", "shiny", "radiant", "dark", "light",
    "alolan", "galarian", "hisuian", "paldean", "primal",
    "the", "and", "card", "ultra", "rare", "promo", "rocket", "rockets",
}


def pokemon_tokens(name):
    """Return the set of meaningful tokens in a Pokemon card name.
    "Mewtwo ex" -> {"mewtwo"}, "Pikachu & Zekrom GX" -> {"pikachu", "zekrom"},
    "Dark Gengar" -> {"gengar"}, "Team Rocket's Mewtwo ex" -> {"mewtwo"}."""
    if not name:
        return set()
    s = re.sub(r"[^a-zA-Z\s]", " ", name.lower())
    return {w for w in s.split() if len(w) >= 3 and w not in _NAME_NOISE}


def names_overlap(a, b):
    """True if two card names share at least one species token."""
    ta, tb = pokemon_tokens(a), pokemon_tokens(b)
    return bool(ta & tb)


def _normalize_set_code(sc):
    if not sc:
        return ""
    return re.sub(r"[^a-z0-9]", "", sc.lower())


def validate_url_match(url, pokemon, number, set_codes=None):
    """Return (name_ok, num_ok, set_ok) for a Cardmarket URL.

    Used to spot wrong-card matches like Vision-said-Kingdra-but-bot-picked-Mewtwo
    or expected-set-NEO4-but-picked-MEW094. set_codes is a list of acceptable codes
    (e.g. [vision_set_code, ptcgo_code]); set_ok is True if ANY appears in slug."""
    slug = url.split("/")[-1].split("?")[0].lower()
    full_path = url.split("?")[0].lower()
    slug_tokens = {t for t in re.findall(r"[a-z]{3,}", slug)}
    name_ok = bool(pokemon_tokens(pokemon) & slug_tokens)
    num_ok = True
    if number:
        nstr = re.sub(r"[^0-9]", "", number)
        if nstr:
            cands = {nstr, nstr.lstrip("0") or "0"}
            num_ok = False
            for cand in cands:
                if slug.endswith(cand):
                    pos = len(slug) - len(cand)
                    if pos == 0 or not slug[pos - 1].isdigit():
                        num_ok = True
                        break
                    # preceding char is a digit — must be guarded by a known set-code prefix
                    for sc in (set_codes or []):
                        scn = _normalize_set_code(sc)
                        if scn and slug.endswith(scn + cand):
                            num_ok = True
                            break
                    if num_ok:
                        break
    set_ok = True
    if set_codes:
        norm_codes = [_normalize_set_code(sc) for sc in set_codes if sc]
        norm_codes = [c for c in norm_codes if len(c) >= 2]
        if norm_codes:
            set_ok = any(c in slug or c in full_path for c in norm_codes)
    return name_ok, num_ok, set_ok


def is_confident_match(url, pokemon, number, set_codes=None):
    """Coarse-grained confidence check: name must appear in slug, number must match.
    Set-code is informational only — too many CM URLs use a different abbreviation
    than the PTCGO code (e.g. MEW vs the 151 set) to make it a hard requirement."""
    if not url:
        return False
    name_ok, num_ok, _set_ok = validate_url_match(url, pokemon, number, set_codes)
    if not name_ok:
        return False
    if number and not num_ok:
        return False
    return True


# ─── Vision: Card Identification ─────────────────────────────

VISION_PROMPT = """Antworte NUR als JSON (kein Markdown):
{
  "pokemon": "Pokemon-Spezies auf Englisch (z.B. Charizard, Umbreon, Giratina)",
  "card_suffix": "ex|EX|V|GX|VMAX|VSTAR|VUNION|none",
  "set_code": "Set-Code von der Karte unten links oder PSA Label (z.B. PRE, WHT, BLK, s11, PFL, m2)",
  "number": "Kartennummer von unten links (z.B. 161/131, 7/102, 94/97) oder null",
  "language": "jp|en|de",
  "grade": "raw ODER exakter Grade vom Slab-Label inkl. Nachkommastelle (z.B. PSA10, PSA9, PSA8, CGC10, CGC9.5, CGC9, CGC8.5, BGS10, BGS9.5)",
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
- grade: PSA/CGC/BGS Label EXAKT lesen, inkl. Nachkommastelle. GEM MT 10=PSA10/CGC10, MINT 9=PSA9, "NM-MT+ 8.5"=CGC8.5, "9.5"=CGC9.5/BGS9.5. NICHT auf 10 aufrunden! Kein Slab=raw
- shop_price: Preistag lesen. Punkte=Tausender (¥130.000=130000). null wenn nicht sichtbar
- shop_currency: NUR JPY wenn ¥-Symbol oder japanischer Text sichtbar. Sonst EUR als Default."""

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


async def _visual_index_identify(b64, k=6):
    """Query the local cardvision MobileCLIP service and return a Ximilar-shaped result
    (best/alternatives/prob) so the existing downstream logic works unchanged.
    Cosine score is calibrated to a pseudo-prob for the >0.75/0.80 trust gates."""
    try:
        payload = json.dumps({"image_b64": b64, "k": k}).encode()
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{CARDVISION_URL}/candidates", data=payload,
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=12)) as resp:
                if resp.status != 200:
                    log.warning(f"  CARDVISION: HTTP {resp.status}")
                    return None
                data = await resp.json()
    except Exception as e:
        log.warning(f"  CARDVISION: {e}")
        return None
    cands = data.get("candidates", [])
    if not cands:
        return None

    def _mk(c):
        sid = c.get("set_id", "")
        return {"name": c.get("name", ""), "set": sid, "set_code": sid,
                "card_number": str(c.get("number", "")), "lang": c.get("lang", ""),
                "full_name": f"{c.get('name','')} {sid}#{c.get('number','')}", "links": {}}

    best = cands[0]
    cos = float(best.get("score", 0.0))
    prob = max(0.0, min(1.0, (cos - 0.55) / 0.30))   # cos 0.55->0, 0.85->1.0
    log.info(f"  CARDVISION: top1={_mk(best)['full_name']} cos={cos:.3f} prob={prob:.2f} [{data.get('crop_mode')}]")
    return {"best": _mk(best), "alternatives": [_mk(c) for c in cands[1:]],
            "prob": prob, "cm_link": "", "tags": {}, "cardvision_cos": cos}


async def _ximilar_identify(b64, set_code_hint=""):
    """Identify card via Ximilar API — or, when Ximilar is off, via the local cardvision
    image-retrieval service (set_code_hint is Ximilar-only and ignored by cardvision)."""
    if not XIMILAR_KEY:
        if USE_CARDVISION and not set_code_hint:
            return await _visual_index_identify(b64)
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

    # ─── Step 2: Ximilar product URL (fastest: 1 BD request, skips CM search) ───
    # If Ximilar identified the card AND has a CM product URL → use it directly
    if ximilar_result and ximilar_result["prob"] > 0.80 and ximilar_result.get("cm_link"):
        xbest = ximilar_result["best"]
        xset = (xbest.get("set_code") or "").upper().replace("-", "")
        vision_sc_norm = vision_sc.upper().replace("-", "")
        # Trust Ximilar when: set_codes match (or no Vision set_code) AND numbers match
        xnum_ok = not vision_num or str(xbest.get("card_number")).lstrip("0") == vision_num.lstrip("0")
        # Don't fast-path when ambiguous alts exist (same name, different set) without a number to disambiguate
        has_ambiguous_alts = not vision_num and any(
            a.get("name") == xbest.get("name") and a.get("set_code") != xbest.get("set_code")
            for a in ximilar_result.get("alternatives", [])[:3])
        if (not vision_sc or xset == vision_sc_norm) and xnum_ok and not has_ambiguous_alts:
            log.info(f"  XIMILAR_FAST: {xbest.get('full_name')} → product URL (skipping CM search)")
            card["name"] = xbest.get("name", "")
            card["set"] = xbest.get("set", "")
            card["set_code"] = xbest.get("set_code", "")
            card["number"] = str(xbest.get("card_number", ""))
            card["ximilar_id"] = True
            card["ptcgo_code"] = xbest.get("set_code", "")
            card["cm_product_url"] = ximilar_result["cm_link"]
            return card
        # Vision set_code differs → retry Ximilar with hint (only if we have a real set_code)
        if not vision_sc:
            log.info(f"  XIMILAR_FAST: skipped (no set_code, number mismatch or ambiguous)")
        else:
            log.info(f"  XIMILAR: set mismatch ({xset} vs {vision_sc}) — retrying with hint...")
        ximilar_hint = await _ximilar_identify(b64, set_code_hint=vision_sc) if vision_sc else None
        if ximilar_hint and ximilar_hint["prob"] > 0.80 and ximilar_hint.get("cm_link"):
            hbest = ximilar_hint["best"]
            log.info(f"  XIMILAR_FAST (hinted): {hbest.get('full_name')} → product URL")
            card["name"] = hbest.get("name", "")
            card["set"] = hbest.get("set", "")
            card["set_code"] = hbest.get("set_code", "")
            card["number"] = str(hbest.get("card_number", ""))
            card["ximilar_id"] = True
            card["ptcgo_code"] = hbest.get("set_code", "")
            card["cm_product_url"] = ximilar_hint["cm_link"]
            return card

    # If Ximilar returned a usable match (prob>0.75 — the same bar the accept + disagreement
    # guard below use), the BD-heavy QUICK_CM + hint-retry are pure latency: the pipeline will
    # either accept this Ximilar match or the guard will veto it. QUICK_CM only earns its
    # 13-25s when Ximilar actually missed (prob<=0.75 / no result). This turns the slow JP/new-
    # set scans (Gengar SWSH, Mega Zygarde, Team Rocket's Mewtwo ex) from 25-60s into ~2-5s.
    ximilar_confident = bool(ximilar_result and ximilar_result["prob"] > 0.75)

    # ─── Step 3: Quick CM (fallback when Ximilar has no product URL — JP sets etc.) ───
    if vision_sc and vision_num and len(vision_sc) <= 6 and not ximilar_confident:
        search_queries = [f"{pokemon_name} {vision_sc}", f"{vision_sc}{vision_num}"]
        log.info(f"  QUICK_CM: trying {search_queries} in parallel...")
        results_per_query = await asyncio.gather(*[search_cardmarket(q) for q in search_queries])
        name_slug = pokemon_name.lower().split()[0]
        for search_q, quick_results in zip(search_queries, results_per_query):
            for url in quick_results:
                slug = url.split("/")[-1]
                if slug.endswith(vision_num) and name_slug in slug.lower():
                    log.info(f"  QUICK_CM: FOUND {slug} (via '{search_q}')")
                    card["name"] = re.sub(r"-V\d.*", "", slug).replace("-", " ")
                    card["set"] = url.split("/Singles/")[-1].split("/")[0].replace("-", " ") if "/Singles/" in url else ""
                    card["number"] = vision_num
                    card["cm_url_override"] = url
                    return card

    # ─── Step 3: Ximilar result → use if available ───
    # If Vision had a set_code, retry Ximilar with hint for better accuracy — but KEEP the
    # original match as fallback. Otherwise a garbage hint (e.g. Vision misreads "BLW" as "BLK"
    # which isn't a real PTCGO set) silently destroys a perfectly good Ximilar identification.
    if ximilar_result and vision_sc and not ximilar_confident and ximilar_result["best"].get("set_code") != vision_sc:
        log.info(f"  XIMILAR: retrying with set_code hint '{vision_sc}'...")
        hinted = await _ximilar_identify(b64, set_code_hint=vision_sc)
        if hinted and hinted["prob"] > 0.75:
            ximilar_result = hinted
        else:
            log.info(f"  XIMILAR: hinted retry empty/low-conf — keeping original match ({ximilar_result['best'].get('set_code')})")

    # ─── Vision/Ximilar disagreement guard (gpt-4o tiebreak) ───
    # When Vision read a real species and Ximilar's name shares NO token with it, the
    # prob>0.75 trust below has historically steamrolled a CORRECT Vision read with a
    # wrong high-confidence Ximilar match (Mega Zygarde ex→Heatran VMAX, Mewtwo GX→Kingdra).
    # Break the tie with gpt-4o: only distrust Ximilar when gpt-4o does NOT back it — so the
    # common "Vision wrong, Ximilar right" case (e.g. Mega Lucario ex) is preserved.
    if ximilar_result and ximilar_result["prob"] > 0.75:
        _xbest_name = ximilar_result["best"].get("name", "")
        _vis_tokens = pokemon_tokens(card.get("pokemon", ""))
        if _vis_tokens and not (_vis_tokens & pokemon_tokens(_xbest_name)):
            log.info(f"  DISAGREE: Vision='{card.get('pokemon')}' vs Ximilar='{_xbest_name}' — gpt-4o tiebreak...")
            try:
                tb = await openai_client.chat.completions.create(
                    model="gpt-4o",
                    messages=[{"role": "user", "content": [
                        {"type": "text", "text": f"This is a Pokemon card. Is the main Pokemon/character '{card.get('pokemon')}' or '{_xbest_name}'? Reply ONLY with the correct full card name in English (include ex/GX/V/VMAX/VSTAR/Mega if shown). One line."},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"}},
                    ]}],
                    max_completion_tokens=20, temperature=0)
                tb_name = tb.choices[0].message.content.strip().split("\n")[0].strip()
                log.info(f"  DISAGREE: gpt-4o says '{tb_name}'")
            except Exception as e:
                tb_name = ""
                log.warning(f"  DISAGREE: tiebreak failed: {e}")
            if tb_name and not (pokemon_tokens(tb_name) & pokemon_tokens(_xbest_name)):
                # gpt-4o does NOT confirm Ximilar → distrust it. Don't grind through the slow
                # TCG-API/QUICK_CM fallback again (it already failed and TCG-API rarely has
                # these brand-new/secret/foreign cards) — return the clean tiebreak species so
                # the downstream Cardmarket search (find_cardmarket_url uses name/pokemon) can
                # resolve it. Fast + honest instead of a >60s timeout or a wrong high-conf match.
                tb_name = re.sub(r"\s*\(.*?\)", "", tb_name).strip(" .,;:!\"'")  # drop "(Illustration Rare)" + stray punctuation
                log.info(f"  DISAGREE: distrusting Ximilar — returning species '{tb_name}' for CM search")
                card["pokemon"] = tb_name
                card["name"] = tb_name
                card["disagreement"] = True
                return card

    if ximilar_result and ximilar_result["prob"] > 0.75:
        best = ximilar_result["best"]
        log.info(f"  XIMILAR: {best.get('full_name')} prob={ximilar_result['prob']:.2f}")

        # Cross-reference with Vision's number to pick right alternative
        # Only if Vision read X/Y format AND the name matches (avoid picking wrong Pokemon)
        vision_number_raw = card.get("number") or ""
        picked = best
        # Cross-reference Vision's number with Ximilar (supports "X/Y" and plain "X")
        v_num = ""
        if "/" in vision_number_raw:
            v_num = re.sub(r"[^\d]", "", vision_number_raw.split("/")[0])
        elif vision_number_raw:
            v_num = re.sub(r"[^\d]", "", vision_number_raw)
        if v_num and str(best.get("card_number")) != v_num:
            best_name_lower = (best.get("name") or "").lower()
            for alt in ximilar_result["alternatives"]:
                alt_name_lower = (alt.get("name") or "").lower()
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
        # Only use CM product URL for best_match when NOT ambiguous
        ambiguous = any(a.get("name") == best.get("name") and a.get("set_code") != best.get("set_code")
                        for a in ximilar_result["alternatives"][:3])
        if picked is best and not ambiguous:
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
        retry_name_slug = pokemon_name.lower().split()[0]
        retry_queries = [f"{pokemon_name} {vision_sc}", f"{pokemon_name} ex {vision_sc}"]
        log.info(f"  QUICK_CM retry: trying {retry_queries} in parallel...")
        retry_results_per_query = await asyncio.gather(*[search_cardmarket(q) for q in retry_queries])
        for search_q, quick_results in zip(retry_queries, retry_results_per_query):
            for url in quick_results:
                slug = url.split("/")[-1]
                if slug.endswith(vision_num) and retry_name_slug in slug.lower():
                    log.info(f"  QUICK_CM retry: FOUND {slug} (via '{search_q}')")
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
_bd_semaphore = asyncio.Semaphore(20)
_bd_session = None

async def _get_bd_session():
    global _bd_session
    if _bd_session is None or _bd_session.closed:
        _bd_session = aiohttp.ClientSession(
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {BD_KEY}"},
            # Bright Data's Web Unblocker on Cardmarket routinely needs ~20-25s per page; 25s
            # timed out on the edge and dropped valid pages (no price). 40s covers the real latency.
            timeout=aiohttp.ClientTimeout(total=40),
        )
    return _bd_session

async def bd_scrape(url):
    # Bright Data on Cardmarket is slow + flaky (intermittent timeouts / block pages). Retry once
    # on failure so a single bad response doesn't drop a valid, priced page.
    async with _bd_semaphore:
        for attempt in range(2):
            try:
                session = await _get_bd_session()
                async with session.post("https://api.brightdata.com/request",
                    json={"zone": BD_ZONE, "url": url, "format": "raw"}) as resp:
                    if resp.status == 200:
                        html = await resp.text()
                        if html and len(html) > 2000:   # sanity: not an empty/block page
                            return html
                        log.warning(f"  BD attempt {attempt+1}: 200 but short body ({len(html or '')})")
                    else:
                        log.warning(f"  BD attempt {attempt+1}: status {resp.status}")
            except asyncio.TimeoutError:
                log.warning(f"  BD timeout attempt {attempt+1}: {url[:80]}")
            except Exception as e:
                log.warning(f"  BD error attempt {attempt+1}: {e}")
        return None

def bd_scrape_sync(url):
    """Sync version for use in run_in_executor contexts."""
    resp = requests.post("https://api.brightdata.com/request",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {BD_KEY}"},
        json={"zone": BD_ZONE, "url": url, "format": "raw"},
        timeout=90)
    if resp.status_code != 200:
        return None
    return resp.text

def parse_de_price(s):
    if not s:
        return None
    return float(s.replace(".", "").replace(",", "."))


# Cardmarket renders each offer twice (mobile + desktop) inside the same article-row
# container; splitting on <div id="articleRow..."> gives one block per real offer so we
# can read each listing's seller country and seller comment alongside its price.
_BOT_OFFER_SPLIT_RE = re.compile(r'<div id="articleRow\d+"')
_BOT_LISTING_PRICE_RE = re.compile(
    r'<span class="color-primary[^"]*fw-bold[^"]*">\s*([\d.,]+)\s*€\s*</span>'
)
_BOT_OFFER_COMMENT_RE = re.compile(r'fst-italic small">([^<]+)</span>')
_BOT_OFFER_LOCATION_RE = re.compile(r'title="Item location:\s*([^"]+)"', re.IGNORECASE)

# Same blacklist as the daily scraper — seller listed only the insert/sleeve/case, not
# the card itself. Drop those before they poison the from-price.
_BOT_BAD_LISTING_RE = re.compile(
    r"(?:"
    r"\binsert\s*!"
    r"|\b(?:just\s+(?:the\s+)?|only\s+the\s+)?insert\s+only\b"
    r"|\bnur\s+(?:der\s+|das\s+|die\s+)?insert\b"
    r"|\bcase\s+only\b"
    r"|\bsleeve\s+only\b"
    r"|\bbox\s+only\b"
    r"|\bempty\s+(?:case|sleeve|box|holder|capsule)\b"
    r"|\bohne\s+karte\b"
    r"|\bleer(?:h[üu]lle)?\b"
    r"|\bnur\s+(?:h[üu]lle|sleeve|etui|verpackung|umverpackung|case|box|kapsel|magnet(?:halter)?)\b"
    r"|\bwithout\s+(?:the\s+)?card\b"
    r"|\bno\s+card\b"
    r"|\bnot\s+the\s+(?:\w+\s+)?card\b"
    r"|\bcarte\s+manquante\b"
    r")",
    re.IGNORECASE,
)

# Grade label patterns. Must match the START of the seller comment so that an offhand
# mention like "PSA10 contender" doesn't get treated as an actual PSA10 listing.
_BOT_GRADE_LABEL_PATTERNS = {
    "psa10": re.compile(r"^\s*PSA\s*10\b(?!\s*(?:contender|candidate|potential|worthy|ready))", re.IGNORECASE),
    "psa9":  re.compile(r"^\s*PSA\s*9\b(?!\d)", re.IGNORECASE),
    "cgc10": re.compile(r"^\s*CGC\s*(?:Pristine\s*|Black\s*Label\s*)?10\b", re.IGNORECASE),
    "bgs10": re.compile(r"^\s*BGS\s*(?:Pristine\s*|Black\s*Label\s*)?10\b", re.IGNORECASE),
}

# UK listings: Cardmarket IOSS covers EU VAT up to 150€, above that the buyer pays
# import VAT (~20% AT) + ~6-12€ handling = ~22% effective surcharge. Mirror the daily
# scraper so the bot reflects the same realistic cost when the cheapest CM listing is
# from the UK.
_BOT_UK_UPLIFT_THRESHOLD_EUR = 150.0
_BOT_UK_UPLIFT_FACTOR = 1.22


def _bot_is_uk_listing(block):
    m = _BOT_OFFER_LOCATION_RE.search(block)
    return bool(m and "united kingdom" in m.group(1).lower())


def _bot_apply_uk_uplift(price, block):
    if price is None or price <= _BOT_UK_UPLIFT_THRESHOLD_EUR:
        return price, False
    if _bot_is_uk_listing(block):
        return round(price * _BOT_UK_UPLIFT_FACTOR, 2), True
    return price, False


def _bot_extract_listing_metrics(html):
    """Walk article-row blocks once, return (from_price, {grade_key: low}).
    Applies the bad-listing filter (insert/case-only) and the UK uplift the same way
    the daily scraper does. Returns (None, {}) if no usable listings found."""
    blocks = _BOT_OFFER_SPLIT_RE.split(html)[1:]
    if not blocks:
        return None, {}
    raw_min = None
    eff_min = None
    grade_lows = {}
    for block in blocks:
        price_m = _BOT_LISTING_PRICE_RE.search(block)
        if not price_m:
            continue
        price = parse_de_price(price_m.group(1))
        if price is None:
            continue
        comment_m = _BOT_OFFER_COMMENT_RE.search(block)
        comment = comment_m.group(1).strip() if comment_m else ""
        if comment and _BOT_BAD_LISTING_RE.search(comment):
            log.info(f"  Listing gefiltert (kein Karten-Listing): {price:.2f}€ — {comment[:60]!r}")
            continue
        grade_key = None
        if comment:
            for key, pat in _BOT_GRADE_LABEL_PATTERNS.items():
                if pat.match(comment):
                    grade_key = key
                    break
        adjusted, applied = _bot_apply_uk_uplift(price, block)
        if applied:
            log.info(f"  UK-Uplift: {price:.2f}€ → {adjusted:.2f}€ (+22% EUSt/Zoll)")
        if grade_key:
            if grade_key not in grade_lows or adjusted < grade_lows[grade_key]:
                grade_lows[grade_key] = adjusted
            continue
        # Raw listing: feeds the "from" price
        if raw_min is None or price < raw_min:
            raw_min = price
        if eff_min is None or adjusted < eff_min:
            eff_min = adjusted
    return eff_min, grade_lows


def extract_prices(html):
    p = {}
    # Listing-derived numbers (from, PSA/CGC/BGS lows) come from a single article-row
    # walk so we get UK uplift + bad-listing filtering. Trend/avg averages are scraped
    # from the Cardmarket header section (label-anchored regex) — those are aggregates
    # CM publishes itself and don't apply per-listing logic.
    eff_from, grade_lows = _bot_extract_listing_metrics(html)
    if eff_from is not None:
        p["from"] = eff_from
    for key, low in grade_lows.items():
        p[key] = low
    for key, pat in [
        ("trend", r"(?:Price Trend|Preis-Trend)[^€]*?([\d.,]+)\s*€"),
        ("avg7", r"(?:7-days|7-Tages)[^€]*?([\d.,]+)\s*€"),
        ("avg30", r"(?:30-days|30-Tages)[^€]*?([\d.,]+)\s*€"),
    ]:
        m = re.search(pat, html, re.I)
        if m:
            p[key] = parse_de_price(m.group(1))
    # Fallback: if listing walk produced no from/grades (e.g. Cardmarket changed markup),
    # fall back to the old label-anchored regex so we don't regress on price reporting.
    if "from" not in p:
        m = re.search(r"(?:From|ab)[^€]*?([\d.,]+)\s*€", html, re.I)
        if m:
            p["from"] = parse_de_price(m.group(1))
    for key, pat in [
        ("psa10", r"PSA\s*10[^€]*?([\d.,]+)\s*€"),
        ("psa9", r"PSA\s*9(?!\d)[^€]*?([\d.,]+)\s*€"),
        ("cgc10", r"CGC\s*10[^€]*?([\d.,]+)\s*€"),
        ("bgs10", r"BGS\s*10[^€]*?([\d.,]+)\s*€"),
    ]:
        if key in p:
            continue
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
            # Stop at "?" so the query string (with HTML-escaped &amp;) doesn't end up in real_url
            og = re.search(r'property="og:url"[^>]*content="([^"?]+Singles/[^"?]+)"', html)
            if og:
                real_url = og.group(1)
                log.info(f"  CM_PRODUCT: resolved → {real_url.split('/')[-1]}")
                return real_url
    name = card_info.get("name", "")
    set_name = card_info.get("set", "")
    number = card_info.get("number", "").split("/")[0]
    # Use the canonical card name (from Ximilar/CM) as the species token when it's present.
    # Vision's `pokemon` field is just an OCR hint and is wrong often enough that trusting
    # it as the slug match key picks "Mewtwo" for a Kingdra photo.
    if name:
        pokemon = name.split(" ex")[0].split(" V")[0].split(" GX")[0].strip()
    else:
        pokemon = card_info.get("pokemon", "")

    # Fall back to Vision's pokemon name when Ximilar/QuickCM didn't identify the card,
    # otherwise queries become " BWP" / " " and CM returns random cards.
    search_name = name or pokemon

    # Search Cardmarket — use vision's set_code (e.g. PRE, WHT) which CM understands
    vision_set_code = card_info.get("vision_set_code", "")
    ptcgo_code = card_info.get("ptcgo_code", "")
    # If the card number itself encodes a set code (e.g. "SWSH261", "XY39"), use that
    # as a search hint when no ptcgo_code was supplied. Vision often gets the set wrong
    # ("SWSD" instead of "SWSH"), so this is more reliable.
    if number and not ptcgo_code:
        m = re.match(r"^([A-Za-z]+)\d", number)
        if m:
            ptcgo_code = m.group(1).upper()
    # Two queries cover almost every case at the card fair:
    #   1. Most specific  (name + setcode + number)  — to land directly on the slug
    #   2. Broad          (just the name)            — to catch cards filed under a
    #                                                  different CM set name than what
    #                                                  Vision/Ximilar reported
    # Each query costs ~10-25s on the Web Unblocker, so the old fallback-loop chain (5+
    # queries) was the main reason scans took 5+ minutes.
    # Cardmarket search returns 0 hits for slug-style queries like "Gengar-SWSH052".
    # Space-separated terms work: "Gengar SWSH" returns Gengar-SWSH052 + SWSH241,
    # while plain "Gengar" returns 29 popular Gengars but not the SWSH variants.
    queries = []
    if search_name and ptcgo_code:
        queries.append(f"{search_name} {ptcgo_code}")
    if search_name:
        queries.append(search_name)
    elif pokemon:
        queries.append(pokemon)
    # Deduplicate (case-insensitive) and cap at 2 queries.
    seen_q = set()
    deduped = []
    for q in queries:
        key = q.strip().lower()
        if key and key not in seen_q:
            seen_q.add(key)
            deduped.append(q.strip())
        if len(deduped) >= 2:
            break
    queries = deduped
    if not queries:
        log.info("  CM_SEARCH: no usable query (name/pokemon missing)")
        return None
    results = []
    seen = set()
    number_lower = (number or "").lower()
    log.info(f"  CM_SEARCH: running {len(queries)} queries in parallel: {queries}")
    parallel_results = await asyncio.gather(*[search_cardmarket(q) for q in queries])
    for q, urls in zip(queries, parallel_results):
        for url in urls:
            if url not in seen:
                seen.add(url)
                results.append(url)

    log.info(f"  CM_RESULTS: {len(results)} — {[u.split('/')[-1] for u in results[:8]]}")

    if not results:
        return None

    # Cardmarket slug: lowercase, drop apostrophes, non-alnum → dash.
    def _cm_slug(s):
        s = (s or "").strip().lower().replace("'", "").replace("’", "").replace("ʼ", "")
        return re.sub(r"[^a-z0-9]+", "-", s).strip("-")

    full_name_slug = _cm_slug(name)
    set_slug = _cm_slug(set_name)
    name_slug_short = pokemon.lower().split()[0] if pokemon else ""

    # Build candidate number suffixes: literal, digit-only, neighbors for typo tolerance.
    # ALL lowercase — slug.lower() is compared against these.
    number_l = (number or "").lower()
    digit_only = re.sub(r"[^0-9]", "", number) if number else ""
    try_numbers = []
    if number_l:
        try_numbers.append(number_l)
    if digit_only and digit_only != number_l:
        try_numbers.append(digit_only)
        stripped = digit_only.lstrip("0") or "0"
        if stripped != digit_only:
            try_numbers.append(stripped)
    if number and number.isdigit():
        n = int(number)
        for d in (-1, 1, -2, 2):
            try_numbers.append(str(n + d))
    # Dedupe while preserving order
    try_numbers = list(dict.fromkeys(try_numbers))

    sc_norm_list = [c for c in [
        (ptcgo_code or "").lower(), (vision_set_code or "").lower()
    ] if c and len(c) >= 2]

    def _num_match(slug, tn):
        if not tn or not slug.endswith(tn):
            return False
        pos = len(slug) - len(tn)
        if pos == 0 or not slug[pos - 1].isdigit():
            return True
        # Preceded by a digit — only OK when guarded by a known set-code prefix.
        return any(slug.endswith(sc + tn) for sc in sc_norm_list)

    # Priority 1: full-name + set path in URL. Catches multi-word/distinctive names like
    # "Sabrinas-Gengar-CFTD" — single-word names (just "gengar") need number/set to
    # disambiguate, so they're skipped here.
    # When a number IS available, only return a match when at least one URL in this
    # priority also matches the number — otherwise fall through. Without this we'd pick
    # Charizard-VMAX-S-P104 just because it shares "/Singles/Sword-Shield-Promos/" with
    # Charizard-VMAX-SWSH261 (the card we actually want).
    if full_name_slug and set_slug and "-" in full_name_slug:
        set_path = f"/singles/{set_slug}/"
        matches = [u for u in results if set_path in u.lower() and full_name_slug in u.split("/")[-1].lower()]
        if matches:
            if try_numbers:
                for tn in try_numbers:
                    for u in matches:
                        slug = u.split("/")[-1].lower()
                        if _num_match(slug, tn):
                            log.info(f"  CM_MATCH (full+set+num): {u.split('/')[-1]}")
                            return u
            else:
                log.info(f"  CM_MATCH (full+set): {matches[0].split('/')[-1]}")
                return matches[0]

    # Priority 2: full-name only — same fall-through rule when number is given.
    if full_name_slug and "-" in full_name_slug and full_name_slug != name_slug_short:
        matches = [u for u in results if full_name_slug in u.split("/")[-1].lower()]
        if matches:
            if try_numbers:
                for tn in try_numbers:
                    for u in matches:
                        slug = u.split("/")[-1].lower()
                        if _num_match(slug, tn):
                            log.info(f"  CM_MATCH (full name+num): {u.split('/')[-1]}")
                            return u
            else:
                log.info(f"  CM_MATCH (full name): {matches[0].split('/')[-1]}")
                return matches[0]

    # Priority 3: short-name + number. Picks "Gengar-SWSH052" over "Gengar-SWSH241" when
    # number is provided. Lower-case both sides — previously the literal SWSH052/XY39
    # number never matched the all-lowercase slug.
    name_slug = name_slug_short
    if name_slug and try_numbers:
        for tn in try_numbers:
            for url in results:
                slug = url.split("/")[-1].lower()
                if name_slug in slug and _num_match(slug, tn):
                    log.info(f"  CM_MATCH: '{name_slug}'+{tn} → {url.split('/')[-1]}")
                    return url

    # Try partner sets (e.g. WHT → BLK for twin JP sets)
    partner_sets = card_info.get("ximilar_partner_sets", [])
    if partner_sets and try_numbers:
        for ps in partner_sets:
            log.info(f"  CM_PARTNER: trying set_code '{ps}'...")
            ps_results = await search_cardmarket(f"{name} {ps}")
            for tn in try_numbers:
                for url in ps_results:
                    slug = url.split("/")[-1].lower()
                    if name_slug in slug and _num_match(slug, tn):
                        log.info(f"  CM_PARTNER: FOUND {url.split('/')[-1]}")
                        return url

    # Fallback: when no number was given (e.g. user only had a name), take the first
    # result that mentions the Pokemon. When a number IS given but nothing matched it,
    # prefer to return None — the bot will warn instead of silently presenting the
    # wrong card.
    if results and not number:
        for url in results:
            slug = url.split("/")[-1].lower()
            if name_slug and name_slug in slug:
                log.info(f"  CM_FALLBACK (no number): {url.split('/')[-1]}")
                return url
    if number:
        log.info(f"  CM_NOMATCH: number {number_l} did not match any of {len(results)} results")
    return None

async def scrape_cardmarket_prices(card_info):
    """Scrapt Cardmarket Preise fuer eine Karte."""
    lang_code = LANG_MAP.get(card_info.get("language", "jp"), 7)
    grade = card_info.get("grade", "raw")

    # Build filter params
    params = f"language={lang_code}"
    is_graded = grade.startswith("PSA") or grade.startswith("CGC") or grade.startswith("BGS")
    if is_graded:
        params += "&minCondition=1&isGraded=Y"
    else:
        min_cond = card_info.get("min_condition", 2)
        params += f"&minCondition={min_cond}"
    if card_info.get("is_first_edition"):
        params += "&isFirstEd=Y"

    def _confidence_for(url):
        if not url:
            return None
        pokemon = card_info.get("name") or card_info.get("pokemon", "")
        number = card_info.get("number", "")
        set_codes = [
            card_info.get("set_code"),
            card_info.get("ptcgo_code"),
            card_info.get("vision_set_code"),
        ]
        ok = is_confident_match(url, pokemon, number, set_codes)
        return "HIGH" if ok else "LOW"

    # Fast path: Ximilar CM product URL — scrape directly with filters (1 BD request instead of 2)
    if card_info.get("cm_product_url"):
        product_url = card_info["cm_product_url"] + "&" + params
        log.info(f"  Scraping (product URL): {product_url}")
        html = await bd_scrape(product_url)
        if html:
            prices = extract_prices(html)
            if prices.get("from") or prices.get("trend"):
                # Stop at "?" so the query string (with HTML-escaped &amp;) doesn't end up in real_url
                og = re.search(r'property="og:url"[^>]*content="([^"?]+Singles/[^"?]+)"', html)
                real_url = og.group(1) if og else card_info["cm_product_url"]
                full_url = f"{real_url}?{params}" if og else product_url
                return real_url, prices, full_url, _confidence_for(real_url)

    url = await find_cardmarket_url(card_info)
    if not url:
        return None, None, None, None

    confidence = _confidence_for(url)
    separator = "&" if "?" in url else "?"
    full_url = f"{url}{separator}{params}"

    log.info(f"  Scraping: {full_url} (confidence={confidence})")
    html = await bd_scrape(full_url)
    if not html:
        return url, None, full_url, confidence

    prices = extract_prices(html)
    return url, prices, full_url, confidence

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

# Wenn Shop- und Marktpreis um mehr als diesen Faktor auseinander liegen, war es fast
# immer ein Fehlmatch (Bot hat eine andere Karte auf Cardmarket gefunden). Beispiele aus
# den Logs: shop=350\u20ac vs market=0.10\u20ac \u2192 3500x, shop=480\u20ac vs market=4.94\u20ac \u2192 97x.
SCAN_MISMATCH_RATIO = 5.0


def calculate_verdict(shop_eur, market_eur):
    """Berechnet DEAL/FAIR/SKIP \u2014 oder MATCH UNSICHER bei extremer Diskrepanz."""
    if shop_eur is None or market_eur is None or market_eur == 0:
        return None, None
    diff_pct = ((shop_eur - market_eur) / market_eur) * 100
    if shop_eur > 0 and market_eur > 0:
        ratio = max(shop_eur / market_eur, market_eur / shop_eur)
        if ratio >= SCAN_MISMATCH_RATIO:
            return "MATCH UNSICHER \u26a0\ufe0f", diff_pct
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

# ─── Scan Logging ───────────────────────────────────────────

def _log_scan(user_id, user_name, card_name, set_name, number, language, grade, market_eur, cm_url, duration_sec, via, shop_eur=None, confidence=None):
    """Log scan to DB for activity tracking."""
    import sqlite3, os
    db = os.environ.get("CARDCHECK_DB", DB_PATH)
    try:
        conn = sqlite3.connect(db)
        conn.execute("""CREATE TABLE IF NOT EXISTS scan_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scanned_at TEXT DEFAULT (datetime('now')),
            user_id INTEGER, user_name TEXT,
            card_name TEXT, set_name TEXT, number TEXT,
            language TEXT, grade TEXT,
            market_eur REAL, cm_url TEXT,
            duration_sec INTEGER, via TEXT
        )""")
        # Migrations für neue Spalten (idempotent)
        for ddl in ("ALTER TABLE scan_log ADD COLUMN shop_eur REAL",
                    "ALTER TABLE scan_log ADD COLUMN confidence TEXT"):
            try: conn.execute(ddl)
            except sqlite3.OperationalError: pass
        conn.execute(
            "INSERT INTO scan_log (user_id, user_name, card_name, set_name, number, language, grade, market_eur, cm_url, duration_sec, via, shop_eur, confidence) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (user_id, user_name, card_name, set_name, number, language, grade, market_eur, cm_url, duration_sec, via, shop_eur, confidence))
        conn.commit()
        conn.close()
    except Exception as e:
        log.warning(f"Scan log failed: {e}")

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


# Hard ceiling on how long a single scan may take. At the card fair some scans ran
# 5+ minutes which is useless — better to bail out fast and let the user retry with
# a set-code caption than to keep the user staring at "scraping..." indefinitely.
SCAN_HARD_TIMEOUT_S = 60
# When elapsed time crosses this threshold, skip the eBay fallback. eBay adds another
# 15-30s on top of an already slow scan and rarely produces a usable answer.
SCAN_EBAY_DEADLINE_S = 35


async def _process_photo(msg, context):
    check_id = f"CHK-{int(time.time())}-{msg.message_id}"
    try:
        await asyncio.wait_for(_process_photo_inner(msg, context, check_id), timeout=SCAN_HARD_TIMEOUT_S)
    except asyncio.TimeoutError:
        log.warning(f"[{check_id}] TIMEOUT nach {SCAN_HARD_TIMEOUT_S}s")
        try:
            await msg.reply_text(
                f"⏱️ Scan zu langsam (>{SCAN_HARD_TIMEOUT_S}s). Schick das Foto bitte nochmal, "
                f"am besten mit Set-Code als Caption (z.B. 'SWSH', 'PFL', 'M2a').",
                reply_to_message_id=msg.message_id,
            )
        except Exception:
            pass


async def _process_photo_inner(msg, context, check_id):
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
        shop_currency = card.get("shop_currency", "EUR")
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
        cm_url, prices, cm_full_url, url_confidence = await scrape_cardmarket_prices(card)
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
            elapsed = time.time() - t0
            if elapsed > SCAN_EBAY_DEADLINE_S:
                log.info(f"[{check_id}] EBAY_SKIP: bereits {elapsed:.0f}s vergangen — eBay-Fallback ausgelassen")
                cm_line = "\nCM: keine Daten | eBay: ausgelassen (Scan zu langsam)"
            else:
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
                    cm_line = f"\nCM: keine Daten fuer {lang_label}"
                    log.info(f"[{check_id}] EBAY_RESULT: {ebay['count']} sold, median=${ebay['median_usd']}")
                else:
                    cm_line = f"\nCM: keine Daten | eBay: keine Daten"
                    log.info(f"[{check_id}] EBAY_RESULT: nothing found")

        # 5. Verdict
        verdict_line = ""
        verdict, diff_pct = calculate_verdict(shop_eur, market_eur)
        scan_confidence = "LOW" if url_confidence == "LOW" else "HIGH"
        if url_confidence == "LOW" and not (verdict and "UNSICHER" in (verdict or "")):
            # URL doesn't look like the photographed card (name/number mismatch in slug).
            # Tell the user before showing potentially misleading numbers.
            verdict_line = (
                f"\n\u26a0\ufe0f <b>MATCH UNSICHER</b> \u2014 gefundene Cardmarket-Seite passt nicht "
                f"eindeutig zur Karte (Nummer/Set stimmt nicht \u00fcberein)."
                f"\nLink pr\u00fcfen oder Foto mit Set-Code als Caption neu schicken "
                f"(z.B. 'SWSH', 'PFL', 'M2a')."
            )
        if verdict and "UNSICHER" in verdict:
            scan_confidence = "LOW"
            verdict_line = (
                f"\n\u26a0\ufe0f <b>MATCH UNSICHER</b> \u2014 Preise passen nicht zusammen"
                f"\nShop {fmt_eur(shop_eur)} vs Markt {fmt_eur(market_eur)} (Faktor "
                f"{max(shop_eur / market_eur, market_eur / shop_eur):.0f}x)"
                f"\nWahrscheinlich falsche Karte erkannt \u2014 Foto neu schicken oder Set-Code "
                f"als Caption mitgeben (z.B. 'SWSH', 'PFL', 'M2a')."
            )
        elif verdict and shop_eur:
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

        # 7. Log scan to DB
        _log_scan(msg.from_user.id, msg.from_user.first_name or "?", name, set_name, number,
                  language, grade, market_eur, link_url, total_sec,
                  "ximilar" if card.get("ximilar_id") else "quick_cm" if card.get("cm_url_override") else "tcg_api",
                  shop_eur=shop_eur, confidence=scan_confidence)

    except Exception as e:
        log.error(f"[{check_id}] ERROR: {e}", exc_info=True)
        await msg.reply_text(f"\u274c Fehler: {e}", reply_to_message_id=msg.message_id)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages — just acknowledge."""
    user_id = update.effective_user.id
    if user_id not in ALLOWED_USERS:
        user_name = update.effective_user.first_name or "?"
        log.info(f"BLOCKED: user_id={user_id} name='{user_name}' text='{(update.message.text or '')[:50]}'")
        await update.message.reply_text("Kein Zugang. Frag Christoph ob er dich freischaltet!")
        return
    text = (update.message.text or "").strip().lower()
    if text in ("hi", "hallo", "ping", "hey", "start"):
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
