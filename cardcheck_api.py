#!/usr/bin/env python3
"""
HTTP-Service fuer den iOS-CardScanner-Prototyp.

POST /api/identify
  Body (JSON), zwei Modi:
    a) { "imageBase64": "..." }                  -> volles identify_card(image) + Preis
    b) { "name", "number", "setCode", "grade",   -> On-Device hat schon erkannt,
         "certBarcode", "language" }                Backend liefert nur den Cardmarket-Preis
  Response:
    { name, set, number, grade, language, marketEur, cmUrl, confidence, via }

Nutzt dieselbe Engine wie der Telegram-Bot (cardcheck_bot.identify_card + scrape_cardmarket_prices),
laeuft auf demselben Host, liest Keys aus derselben DB. Nur aiohttp (schon installiert) — keine neue Dependency.
"""
import base64
import logging
import os
import sqlite3

from aiohttp import web

import cardcheck_bot as bot

log = logging.getLogger("cardcheck-api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def load_api_token():
    """Shared secret for the public endpoint — stored in DB settings (key: cardcheck_api_token),
    never in the repo. If empty, auth is disabled (LAN-only use)."""
    db = os.environ.get("CARDCHECK_DB", "/opt/pokemon-tracker/data/tracker.db")
    try:
        conn = sqlite3.connect(db)
        row = conn.execute("SELECT value FROM settings WHERE key='cardcheck_api_token'").fetchone()
        conn.close()
        return (row[0] or "").strip() if row else ""
    except Exception:
        return ""


API_TOKEN = load_api_token()
if API_TOKEN:
    log.info("API auth enabled (token required)")
else:
    log.info("API auth DISABLED (no cardcheck_api_token in DB) — do not expose publicly")


def _authorized(request: web.Request) -> bool:
    if not API_TOKEN:
        return True
    auth = request.headers.get("Authorization", "")
    token = auth[7:].strip() if auth.startswith("Bearer ") else request.headers.get("X-Auth-Token", "").strip()
    return token == API_TOKEN


def _market_price(card, prices):
    """Marktpreis je nach Grade auswaehlen — gespiegelt aus dem Bot-Verdict."""
    grade = card.get("grade", "raw")
    table = {"PSA10": "psa10", "PSA9": "psa9", "CGC10": "cgc10", "BGS10": "bgs10"}
    key = table.get(grade)
    if key and prices.get(key) is not None:
        return prices[key]
    if prices.get("from") is not None:
        return prices["from"]
    return prices.get("trend")


def _via(card):
    if card.get("ximilar_id"):
        return "ximilar"
    if card.get("cm_url_override"):
        return "quick_cm"
    if card.get("tcg_id"):
        return "tcg_api"
    return "cloud"


async def identify_handler(request: web.Request) -> web.Response:
    if not _authorized(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)

    img_b64 = data.get("imageBase64")
    if img_b64:
        # Voller Pfad: Engine erkennt die Karte vom Foto.
        try:
            photo = base64.b64decode(img_b64)
        except Exception:
            return web.json_response({"error": "bad base64"}, status=400)
        card = await bot.identify_card(photo)
        source = "image"
    else:
        # Fast-Path: On-Device hat schon erkannt -> nur noch bepreisen.
        name = (data.get("name") or "").strip()
        if not name and not data.get("number"):
            return web.json_response({"error": "need imageBase64 or name/number hints"}, status=400)
        set_code = (data.get("setCode") or "").strip()
        card = {
            "name": name,
            "pokemon": name,
            "number": str(data.get("number") or ""),
            "set_code": set_code,
            "vision_set_code": set_code,
            "ptcgo_code": set_code,
            "grade": data.get("grade") or "raw",
            "language": data.get("language") or "en",
        }
        source = "hints"

    # Optionale Caption-artige Overrides vom Geraet
    if data.get("language"):
        card["language"] = data["language"]
    if data.get("grade") and data["grade"] != "raw":
        card["grade"] = data["grade"]

    cm_url, prices, full_url, conf = await bot.scrape_cardmarket_prices(card)
    prices = prices or {}

    name_out = (card.get("name") or card.get("pokemon") or "Unbekannte Karte").strip()
    resp = {
        "name": name_out,
        "set": card.get("set"),
        "number": str(card.get("number") or "") or None,
        "grade": card.get("grade", "raw"),
        "language": card.get("language"),
        "marketEur": _market_price(card, prices),
        "cmUrl": full_url or cm_url,
        "confidence": "LOW" if conf == "LOW" else "HIGH",
        "via": _via(card) + ("+ondevice" if source == "hints" else ""),
    }
    log.info(f"identify[{source}] -> {resp['name']} #{resp['number']} {resp['grade']} "
             f"market={resp['marketEur']} conf={resp['confidence']} via={resp['via']}")
    return web.json_response(resp)


async def health(_request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


def make_app() -> web.Application:
    app = web.Application(client_max_size=25 * 1024 * 1024)  # erlaube ~25MB Foto-Uploads
    app.add_routes([
        web.post("/api/identify", identify_handler),
        web.get("/health", health),
    ])
    return app


if __name__ == "__main__":
    log.info("Card Check API startet auf :8088 ...")
    web.run_app(make_app(), host="0.0.0.0", port=8088)
