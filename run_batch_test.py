#!/usr/bin/env python3
"""Batch-firing test: fire ALL test cards at the recognition engine at once and let the
reports stream back as each finishes — simulates 'foto-foto-foto, reports kommen nacheinander'.
Measures wall-clock vs. the sequential sum, and when each report lands."""
import asyncio, time, glob, os, sys, json

sys.path.insert(0, "/opt/pokemon-tracker")   # the DEPLOYED engine
import cardcheck_bot as bot

IMAGES = sorted(glob.glob("/tmp/cardtests/*.jpg"))
CONCURRENCY = int(os.environ.get("BATCH_CONCURRENCY", "0"))  # 0 = unbounded (fire all at once)
T0 = 0.0


async def process(idx, path, sem):
    name = os.path.basename(path)[-12:-4]
    async def _run():
        data = open(path, "rb").read()
        s = time.time()
        try:
            card = await bot.identify_card(data)
            who = (card.get("name") or card.get("pokemon") or "?").strip()
        except Exception as e:
            who = f"ERR {e!r}"
        dur = time.time() - s
        done = time.time() - T0
        print(f"  [report @ {done:5.1f}s]  card {idx:2d} ({name})  fertig in {dur:4.1f}s  ->  {who}", flush=True)
        return done, dur
    if sem:
        async with sem:
            return await _run()
    return await _run()


async def main():
    global T0
    n = len(IMAGES)
    sem = asyncio.Semaphore(CONCURRENCY) if CONCURRENCY else None
    label = f"max {CONCURRENCY} gleichzeitig" if CONCURRENCY else "ALLE gleichzeitig (unbounded)"
    print(f"BATCH: feuere {n} Karten ab — {label}\n", flush=True)
    T0 = time.time()
    results = await asyncio.gather(*[process(i, p, sem) for i, p in enumerate(IMAGES)])
    total = time.time() - T0
    durs = [d for _, d in results]
    dones = sorted(d for d, _ in results)
    seq = sum(durs)
    print(f"\n=== BATCH FERTIG ===")
    print(f"  {n} Karten · Wall-Clock {total:.1f}s · sequenziell wäre ~{seq:.0f}s · Speedup {seq/total:.1f}x")
    print(f"  erster Report @ {dones[0]:.1f}s · letzter @ {dones[-1]:.1f}s · pro-Karte Ø {seq/n:.1f}s")


if __name__ == "__main__":
    asyncio.run(main())
