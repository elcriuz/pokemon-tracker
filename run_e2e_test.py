#!/usr/bin/env python3
"""End-to-end test: identify_card() + scrape_cardmarket_prices() — does the card
resolve to a real Cardmarket URL + price? Reports timing split and confidence."""
import asyncio, time, sys, json

sys.path.insert(0, "/tmp/cardtests")
import cardcheck_bot as bot

DIR = "/tmp/cardtests"
# Default: the hard cards the Ximilar guard now distrusts (+ a couple controls).
FILES = sys.argv[1:] or [
    "2026-06-25 10.28.23.jpg",  # Team Rocket's Mewtwo ex (M2a) — Ximilar gave Sacred Charm
    "2026-06-25 10.28.34.jpg",  # Pikachu 151 (zh) — Ximilar gave Darumaka
    "2026-06-25 10.28.52.jpg",  # Mega Zygarde ex gold — Ximilar gave Heatran VMAX
    "2026-06-25 10.29.03.jpg",  # Mewtu-GX rainbow — Ximilar gave Kingdra
    "2026-06-25 09.55.37.jpg",  # control: Mega Lucario ex (fast-path, should still work)
]


async def run_one(name):
    path = f"{DIR}/{name}"
    data = open(path, "rb").read()
    t0 = time.time()
    card = await bot.identify_card(data)
    t1 = time.time()
    try:
        url, prices, full_url, conf = await bot.scrape_cardmarket_prices(card)
    except Exception as e:
        url, prices, full_url, conf = None, None, None, f"ERR:{e!r}"
    t2 = time.time()
    prices = prices or {}
    return {
        "file": name,
        "identify_s": round(t1 - t0, 1),
        "scrape_s": round(t2 - t1, 1),
        "total_s": round(t2 - t0, 1),
        "name": card.get("name"),
        "pokemon": card.get("pokemon"),
        "number": card.get("number"),
        "disagreement": card.get("disagreement", False),
        "cm_url": (url or "").split("/")[-1] if url else None,
        "confidence": conf,
        "from_eur": prices.get("from"),
        "trend_eur": prices.get("trend"),
    }


async def main():
    for f in FILES:
        sys.stderr.write(f"\n========== {f} ==========\n"); sys.stderr.flush()
        r = await run_one(f)
        print("E2E " + json.dumps(r, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
