#!/usr/bin/env python3
"""Regression harness for cardcheck_bot URL matching.

Reproduces the worst weekend failures and verifies the new matching logic
either picks the right Cardmarket URL or marks the match as not-confident
(which would trigger the MATCH UNSICHER warning in the real bot reply).

Each case is the card_info dict the bot would have AFTER Vision+Ximilar
identification. We skip Vision/Ximilar and call find_cardmarket_url() +
is_confident_match() directly so we can iterate on the matching layer
without needing a real photo upload.

Run on the host:
    cd /opt/pokemon-tracker && python3 test_bot_match.py
"""
import asyncio
import sys
import time
from pathlib import Path

# Allow running from anywhere
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from cardcheck_bot import find_cardmarket_url, is_confident_match, names_overlap  # noqa


# Each case: (label, card_info, expected_slug_tokens, expected_outcome)
# expected_outcome: "MATCH" if find should pick a slug containing all expected_slug_tokens,
#                   "FLAG_LOW" if any URL is acceptable as long as is_confident_match=False.
CASES = [
    {
        "label": "Kingdra XYP XY39 DE",
        "info": {
            "name": "Kingdra",
            "set": "XY Promos",
            "number": "XY39",
            "set_code": "XYP",
            "ptcgo_code": "XYP",
            "pokemon": "Mewtwo",           # Vision was wrong here
            "vision_set_code": "",
            "language": "de",
        },
        "expect_tokens": ["kingdra", "39"],
        "min_outcome": "FLAG_LOW",
    },
    {
        "label": "Dark Gengar NEO4 094 JP",
        "info": {
            "name": "Dark Gengar",
            "set": "Darkness and to Light",
            "number": "094",
            "set_code": "NEO4",
            "ptcgo_code": "NEO4",
            "pokemon": "Gengar",
            "vision_set_code": "",
            "language": "jp",
        },
        "expect_tokens": ["gengar", "094"],
        "min_outcome": "FLAG_LOW",
    },
    {
        "label": "Mega Zygarde ex PFL 124 EN",
        "info": {
            "name": "Mega Zygarde ex",
            "set": "Phantasmal Flames",
            "number": "124",
            "set_code": "PFL",
            "ptcgo_code": "PFL",
            "pokemon": "Mega Zygarde",
            "vision_set_code": "PFL",
            "language": "en",
        },
        "expect_tokens": ["zygarde", "124"],
        "min_outcome": "FLAG_LOW",
    },
    {
        "label": "Gengar SWSH052 EN",
        "info": {
            "name": "Gengar",
            "set": "Sword & Shield Promos",
            "number": "SWSH052",
            "set_code": "",
            "ptcgo_code": "",
            "pokemon": "Gengar",
            "vision_set_code": "SWSH",
            "language": "en",
        },
        "expect_tokens": ["gengar", "052"],
        "min_outcome": "FLAG_LOW",
    },
    {
        "label": "Pikachu XYP 281 JP",
        "info": {
            "name": "Pikachu",
            "set": "XY Promos",
            "number": "281",
            "set_code": "XYP",
            "ptcgo_code": "XYP",
            "pokemon": "Pikachu",
            "vision_set_code": "",
            "language": "jp",
        },
        "expect_tokens": ["pikachu", "281"],
        "min_outcome": "FLAG_LOW",
    },
    {
        "label": "Charizard VMAX SWSH261 DE",
        "info": {
            "name": "Charizard VMAX",
            "set": "Sword & Shield Promos",
            "number": "SWSH261",
            "set_code": "",
            "ptcgo_code": "",
            "pokemon": "Charizard",
            "vision_set_code": "SWSD",
            "language": "de",
        },
        "expect_tokens": ["charizard", "261"],
        "min_outcome": "FLAG_LOW",
    },
    # known-good baselines that should still resolve correctly
    {
        "label": "Rowlet & Alolan Exeggutor UNM214 DE (baseline)",
        "info": {
            "name": "Rowlet & Alolan Exeggutor-GX",
            "set": "Unified Minds",
            "number": "214",
            "set_code": "UNM",
            "ptcgo_code": "UNM",
            "pokemon": "Rowlet",
            "vision_set_code": "",
            "language": "de",
        },
        "expect_tokens": ["rowlet", "214"],
        "min_outcome": "MATCH",
    },
    {
        "label": "Umbreon & Darkrai-GX SM241 DE (baseline)",
        "info": {
            "name": "Umbreon & Darkrai-GX",
            "set": "Sun & Moon Promos",
            "number": "SM241",
            "set_code": "SMP",
            "ptcgo_code": "SMP",
            "pokemon": "Umbreon",
            "vision_set_code": "",
            "language": "de",
        },
        "expect_tokens": ["umbreon", "241"],
        "min_outcome": "MATCH",
    },
]


def evaluate(info, url):
    pokemon = info.get("name") or info.get("pokemon", "")
    number = info.get("number", "")
    set_codes = [info.get("set_code"), info.get("ptcgo_code"), info.get("vision_set_code")]
    confident = is_confident_match(url, pokemon, number, set_codes)
    return confident


async def main():
    overall_ok = True
    rows = []
    for case in CASES:
        info = case["info"]
        t0 = time.time()
        try:
            url = await find_cardmarket_url(info)
        except Exception as e:
            url = None
            print(f"[ERR] {case['label']}: {e}")
        dt = time.time() - t0
        slug = url.split("/")[-1].split("?")[0].lower() if url else "(none)"
        confident = evaluate(info, url) if url else False
        tokens_present = all(t in slug for t in case["expect_tokens"])
        if tokens_present and confident:
            verdict = "MATCH"
        elif not confident:
            verdict = "FLAG_LOW"
        else:
            verdict = "WRONG"
        target = case["min_outcome"]
        # MATCH is always acceptable; FLAG_LOW acceptable for FLAG_LOW targets only.
        if target == "MATCH":
            passed = verdict == "MATCH"
        else:  # FLAG_LOW
            passed = verdict in ("MATCH", "FLAG_LOW")
        overall_ok = overall_ok and passed
        rows.append((case["label"], verdict, target, "✓" if passed else "✗", f"{dt:.1f}s", slug))

    print(f"\n{'Label':<45} {'Got':<10} {'Min':<10} {'P':<3} {'Time':<6} URL slug")
    print("-" * 110)
    for label, verdict, target, p, t, slug in rows:
        print(f"{label:<45} {verdict:<10} {target:<10} {p:<3} {t:<6} {slug}")
    print()
    print("OVERALL:", "PASS" if overall_ok else "FAIL")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
