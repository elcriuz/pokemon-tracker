#!/usr/bin/env python3
"""Standalone recognition benchmark — runs ONLY identify_card() on test images,
measures wall-clock + which pipeline path was taken. No DB writes, no price scrape."""
import asyncio, base64, json, time, glob, sys, os

# Prefer a patched copy in /tmp/cardtests if present, else the deployed prod module.
sys.path.insert(0, "/opt/pokemon-tracker")
sys.path.insert(0, "/tmp/cardtests")
import cardcheck_bot as bot
sys.stderr.write(f"\n[harness] cardcheck_bot loaded from: {bot.__file__}\n")

IMAGES = sorted(glob.glob("/tmp/cardtests/*.jpg"))


def classify_via(card):
    if card.get("ximilar_id"):
        return "ximilar"
    if card.get("cm_url_override"):
        return "quick_cm"
    if card.get("cm_product_url"):
        return "ximilar_product"
    if card.get("tcg_id"):
        return "tcg_api"
    return "vision_only/none"


async def run_one(path):
    with open(path, "rb") as f:
        data = f.read()
    t0 = time.time()
    err = None
    card = {}
    try:
        card = await bot.identify_card(data)
    except Exception as e:
        err = repr(e)
    dt = round(time.time() - t0, 2)
    return {
        "file": os.path.basename(path),
        "elapsed_s": dt,
        "via": classify_via(card),
        "error": err,
        "name": card.get("name"),
        "set": card.get("set"),
        "set_code": card.get("set_code"),
        "number": card.get("number"),
        "language": card.get("language"),
        "grade": card.get("grade"),
        "vision_pokemon": card.get("pokemon"),
        "vision_set_code": card.get("vision_set_code"),
        "cm_product_url": card.get("cm_product_url"),
        "cm_url_override": card.get("cm_url_override"),
    }


async def main():
    results = []
    for p in IMAGES:
        sys.stderr.write(f"\n========== {os.path.basename(p)} ==========\n")
        sys.stderr.flush()
        r = await run_one(p)
        results.append(r)
        print("RESULT " + json.dumps(r, ensure_ascii=False), flush=True)
    print("\n===JSON_ARRAY===", flush=True)
    print(json.dumps(results, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
