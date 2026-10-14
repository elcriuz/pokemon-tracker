#!/usr/bin/env python3
"""Beobachtet Wunschkarten und meldet, wann sich der Zugriff lohnt.

Der Unterschied zu Cardmarkets Wantlist: dort steht nur, dass man etwas sucht.
Hier kommt dazu, was es kosten darf — und vor allem ein Preisverlauf, an dem
sich erkennen laesst, ob 34 Euro heute guenstig sind oder ob es letzte Woche
28 waren.

  python3 watchlist.py              # Preise holen, Kaufsignale schreiben
  python3 watchlist.py --notify     # und die neuen per Telegram melden
  python3 watchlist.py --dry-run
  python3 watchlist.py --only 14 15  # nur diese Eintraege, z. B. frisch angelegte
  python3 watchlist.py --sniper --notify  # nur die Sniper-Eintraege, Cron alle 10 Min.
  python3 watchlist.py --alert 1234       # Sniper-Meldung nach dem Warenkorb-Check (ruft der Server)

Sniper-Ablauf seit 14.10.2026: Ein Schnapper wird nicht sofort gemeldet, sondern
als `check_status = 'pending'` abgelegt. Der Sniper-Helfer (Chrome-Erweiterung in
Christophs eingeloggtem Browser, ~/Desktop/PackGehabt/Sniper-Helper/) holt ihn
ueber /api/sniper/pending, prueft, ob das Angebot ueberhaupt in den Warenkorb darf
(viele Lockvogel-Angebote verschicken in kein Land), legt es hinein und meldet
das Ergebnis. Erst dann geht der Alarm raus. Ist der Helfer offline, meldet
notify() ungeprueft.
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
DB_PATH = ROOT / "data" / "tracker.db"

from scrape_brightdata import download_image, extract_card_info
from cardmarket_public import (MAX_PARALLEL, bd_fetch, build_url, extract_prices,
                               parse_competitors)
from versandkosten import Versandtabelle, lade_tabelle, land_name

log = logging.getLogger("watchlist")

BUY = "buy"


def get(db, key, default):
    row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    try:
        return float(row[0]) if row and row[0] != "" else float(default)
    except (TypeError, ValueError):
        return float(default)


def median(values: list[float]) -> float | None:
    if not values:
        return None
    v = sorted(values)
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


# Unter diesem Anteil des Mittelfelds gilt ein Angebot als Betrug, egal wer anbietet.
SCAM_HARD_PCT = 35.0


def is_suspicious(total: float | None, median_total: float | None, sales: int | None,
                  max_pct: float = 55.0, max_sales: int = 50) -> bool:
    """Betrugsverdacht: weit unter dem Markt, und der Anbieter hat kaum Verkaeufe.

    Auf Cardmarket tauchen bei teurem Sealed regelmaessig frische Konten mit
    Preisen bei 20-45 % des Marktes auf und verschwinden nach ein, zwei Tagen
    wieder — beim Hobbit-Display 258 € statt 600 € (12.09.2026) und 142 € statt
    670 € (19.09.2026), beide Male mit Kaufsignal per Telegram. Als Signal sind
    sie wertlos, im Verlauf verfaelschen sie die Spanne. Unbekannte Verkaufszahl
    (Markup geaendert?) zaehlt wie ein frisches Konto.
    """
    if not total or not median_total:
        return False
    anteil = total / median_total * 100
    if anteil < SCAM_HARD_PCT:
        return True
    return anteil < max_pct and (sales is None or sales < max_sales)


def evaluate_buy(item: dict, snap: dict, prev: dict | None, below_pct: float) -> dict | None:
    """Kaufsignal: Zielpreis erreicht, oder deutlich unter dem ueblichen Niveau.

    Gerechnet wird mit dem, was die Karte zu Hause kostet: Preis plus Versand.
    """
    best = snap.get("best_total")
    if not best:
        return None
    versand = ""
    if snap.get("best_shipping") is not None:
        versand = f" · Versand {snap['best_shipping']:.2f} € aus {snap.get('best_origin') or '?'}"

    target = item.get("target_price")
    if target and best <= target:
        return {
            "kind": BUY,
            "price": best,
            "detail": f"Zielpreis erreicht: {best:.2f} € inkl. Versand (Ziel {target:.2f} €){versand}",
        }

    # Ohne Zielpreis gilt der eigene Verlauf als Massstab: deutlich unter dem
    # Mittelfeld heisst, dass gerade jemand billiger raus will.
    med = snap.get("median_total")
    if med and best < med * (1 - below_pct / 100):
        drop = ""
        if prev and prev.get("best_total"):
            delta = best / prev["best_total"] - 1
            if delta < -0.05:
                drop = f", {abs(delta)*100:.0f}% günstiger als beim letzten Blick"
        return {
            "kind": BUY,
            "price": best,
            "detail": (f"{best:.2f} € inkl. Versand liegt {(1-best/med)*100:.0f}% unter dem "
                       f"Mittelfeld ({med:.2f} €){drop}{versand}"),
        }
    return None


def boden_7d(db, watchlist_id: int, vor: str) -> float | None:
    """Was das Produkt in den letzten 7 Tagen ueblicherweise mindestens gekostet hat.

    Median der jeweils guenstigsten Angebote aller Abrufe — ein einzelner
    Ausreisser (auch ein frueherer Schnapper) zieht ihn kaum. Unter vier
    Abrufen gibt es noch keinen Boden.
    """
    ab = (datetime.fromisoformat(vor) - timedelta(days=7)).isoformat(timespec="seconds")
    werte = [r[0] for r in db.execute(
        """SELECT best_total FROM watchlist_snapshots WHERE watchlist_id = ?
           AND captured_at >= ? AND captured_at < ? AND best_total IS NOT NULL""",
        (watchlist_id, ab, vor))]
    return median(werte) if len(werte) >= 4 else None


def evaluate_snipe(offers: list[dict], boden: float | None, min_pct: float = 3.0,
                   pruef_pct: float = 25.0, target: float | None = None,
                   min_eur: float = 10.0) -> dict | None:
    """Schnapper: das guenstigste Angebot liegt klar unter ALLEM anderen.

    Gemessen wird gegen zwei Dinge zugleich — das naechstguenstige Angebot von
    jetzt und den Boden der letzten 7 Tage — und gilt der niedrigere. Faellt der
    ganze Markt langsam (Reality Fracture: 376 → 342 € in zwei Wochen), sinkt das
    zweite Angebot mit, und es meldet sich nichts. Steht dagegen einer allein
    deutlich darunter, wird er gleich weg sein. Genau dann zuschlagen.

    `offers` sind die vergleichbaren Angebote (Sprache passt, kein Betrugs-
    verdacht) mit `total` inkl. Versand.
    """
    if not offers:
        return None
    reihe = sorted(offers, key=lambda c: c["total"])
    best = reihe[0]
    # Derselbe Verkaeufer zweimal zum selben Preis ist kein Konkurrent.
    zweit = next((c["total"] for c in reihe[1:]
                  if c["seller"] != best["seller"] or c["total"] != best["total"]), None)
    refs = [x for x in (zweit, boden) if x]
    ziel_ok = bool(target and best["total"] <= target)
    if not refs and not ziel_ok:
        return None
    ref = min(refs) if refs else None
    unter = (1 - best["total"] / ref) * 100 if ref else 0.0
    # Beides muss stimmen: 3 % von 340 € sind 10 €, von einer 140-€-Box nur 4 € —
    # das ist Rauschen, kein Schnapper.
    abstand = ref - best["total"] if ref else 0.0
    if (unter < min_pct or abstand < min_eur) and not ziel_ok:
        return None

    verkaeufer = best["seller"] or "?"
    if best.get("sales") is not None:
        verkaeufer += f" ({best['sales']} Verkäufe)"
    vergleich = []
    if zweit:
        vergleich.append(f"nächstes Angebot {zweit:.2f} €")
    if boden:
        vergleich.append(f"7-Tage-Boden {boden:.2f} €")
    teile = [f"{best['total']:.2f} € inkl. Versand ({best['price']:.2f} € + "
             f"{best['shipping']:.2f} € aus {land_name(best.get('origin')) or '?'})",
             f"von {verkaeufer}"]
    if ref and unter > 0:
        teile.append(f"{unter:.0f} % / {ref - best['total']:.0f} € unter allem anderen "
                     f"({', '.join(vergleich)})")
    if ziel_ok:
        teile.append(f"Zielpreis {target:.2f} € erreicht")
    if unter >= pruef_pct:
        teile.append("⚠ ungewöhnlich weit unter Markt — Verkäuferkonto prüfen")
    return {
        "kind": BUY,
        "price": best["total"],
        "detail": " · ".join(teile),
        "offer_key": f"{best['seller']}|{best['total']:.2f}",
        "article_id": best.get("article_id"),
    }


def gesperrte_verkaeufer(db) -> set[str]:
    """Verkaeufer, die laut Sniper-Helfer nicht nach Oesterreich verschicken.

    Das sieht man nur eingeloggt (grauer Warenkorb-Knopf). Wer einmal so
    aufgefallen ist, zaehlt fuer keinen Eintrag mehr als Angebot.
    """
    return {r[0].lower() for r in db.execute("SELECT seller FROM blocked_sellers")}


def helfer_online(db, sekunden: int = 90) -> bool:
    seen = get_setting(db, "sniper_helper_seen")
    try:
        return (datetime.now() - datetime.fromisoformat(seen)).total_seconds() < sekunden
    except (TypeError, ValueError):
        return False


CART_URL = "https://www.cardmarket.com/de/{game}/ShoppingCart"


def send_sniper_alert(db, signal_id: int) -> bool:
    """Eine Sniper-Meldung, abhaengig davon, was der Warenkorb-Check ergeben hat."""
    from scrape_brightdata import send_telegram
    r = db.execute("""
        SELECT s.suggested_price, s.detail, s.check_status, s.check_detail, w.name, w.game,
               w.condition, w.language, w.product_url
        FROM signals s JOIN watchlist w ON w.id = s.watchlist_id WHERE s.id = ?""",
        (signal_id,)).fetchone()
    if not r:
        return False
    preis, detail, status, check, name, game, cond, lang, url = r
    angebot = build_url(url, cond, lang)
    if status == "carted":
        titel = f"🛒 Im Warenkorb: {preis:.0f} €"
        kopf = "🛒 <b>SCHNAPPER IM WARENKORB</b> — jetzt bezahlen"
        link = CART_URL.format(game=game or "Magic")
    elif status == "failed":
        titel = f"🎯 Schnapper {preis:.0f} € — selbst einlegen"
        kopf = f"🎯 <b>SCHNAPPER</b> — Warenkorb hat nicht geklappt ({check or '?'}), schnell selbst"
        link = angebot
    else:
        titel = f"🎯 Schnapper {preis:.0f} € (ungeprüft)"
        kopf = "🎯 <b>SCHNAPPER</b> — ungeprüft, Chrome-Helfer offline: Warenkorb-Knopf checken"
        link = angebot
    send_telegram(f'{kopf}\n<a href="{angebot}">{name[:60]}</a> <i>{lang}</i>\n'
                  f'<b>{preis:.2f} \u20ac</b> — {detail}'
                  + (f'\n<a href="{link}">Zum Warenkorb</a>' if status == "carted" else ""))
    ha_push(get_setting(db, "sniper_ha_webhook"), titel, f"{name[:60]} ({lang}) — {detail}", link)
    db.execute("UPDATE signals SET notified_at = ? WHERE id = ?",
               (datetime.now().isoformat(timespec="seconds"), signal_id))
    db.commit()
    return True


def ha_push(url: str, titel: str, text: str, link: str) -> bool:
    """Critical Push aufs iPhone ueber einen Webhook-Trigger in HA 7010.

    Telegram landet gern in der iOS-Mitteilungszusammenfassung; ein Schnapper
    ist nach Minuten weg. Die Automation in HA schickt den Push mit
    interruption-level critical, der Tap oeffnet das Angebot.
    """
    if not url:
        return False
    daten = json.dumps({"title": titel, "message": text, "url": link}).encode()
    try:
        req = urllib.request.Request(url, data=daten, method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            r.read()
        return True
    except Exception as e:
        log.error("HA-Push fehlgeschlagen: %s", e)
        return False


def notify(db, limit: int = 12) -> int:
    """Schickt noch nicht gemeldete Kaufsignale gebuendelt per Telegram.

    Stand bis 17.09.2026 in signals.py und meldete auch Verkaufssignale. Seit der
    Verkaeuferbereich bei TCG PowerTools liegt, gibt es nur noch die Kaufseite —
    und damit keinen Grund mehr, das in einem eigenen Lauf zu tun.
    """
    try:
        from scrape_brightdata import send_telegram
    except Exception as e:
        log.error("Telegram nicht verfuegbar: %s", e)
        return 0

    rows = db.execute("""
        SELECT s.id, s.suggested_price, s.detail, w.name, w.game, w.condition,
               w.language, w.product_url, w.sniper, s.check_status,
               COALESCE(s.claimed_at, s.created_at)
        FROM signals s
        JOIN watchlist w ON w.id = s.watchlist_id
        WHERE s.notified_at IS NULL AND s.dismissed_at IS NULL AND s.kind = ?
        ORDER BY s.suggested_price DESC""", (BUY,)).fetchall()
    if not rows:
        log.info("Keine neuen Kaufsignale zu melden")
        return 0

    # Schnapper einzeln und sofort, mit Critical Push — nicht im Sammelbericht.
    # Solange der Sniper-Helfer im Browser laeuft, prueft er zuerst den Warenkorb
    # und der Server meldet danach (--alert). Haengt er oder ist er offline,
    # geht die Meldung ungeprueft raus — lieber ein Fehlalarm als ein verpasster.
    online = helfer_online(db)
    grenze = (datetime.now() - timedelta(minutes=5)).isoformat(timespec="seconds")
    for r in [r for r in rows if r[8]]:
        status, seit = r[9], r[10]
        if status in ("pending", "claimed") and online and seit > grenze:
            log.info("Schnapper %s wartet auf den Warenkorb-Check", r[0])
            continue
        if status in ("pending", "claimed", None):
            db.execute("UPDATE signals SET check_status = 'unchecked' WHERE id = ?", (r[0],))
        send_sniper_alert(db, r[0])
    rows = [r[:8] for r in rows if not r[8]]
    jetzt = datetime.now().isoformat(timespec="seconds")
    if not rows:
        return 0

    zeilen = [f"<b>Wunschliste — {len(rows)} neue Kaufsignale</b>", ""]
    for (_id, preis, detail, name, game, cond, lang, url) in rows[:limit]:
        zustand = f"{cond}/{lang}" if cond else (lang or "")
        # Gefilterter Link — ungefiltert zeigt Cardmarket erst mal andere Sprachen.
        zeilen.append(f'\U0001f6d2 <a href="{build_url(url, cond, lang)}">{name[:44]}</a> '
                      f'<i>{game[:3]} {zustand}</i>')
        zeilen.append(f"   <b>{preis:.2f} \u20ac</b> — {detail}" if preis else f"   {detail}")
    if len(rows) > limit:
        zeilen.append(f"\n… und {len(rows) - limit} weitere in der Wunschliste")

    send_telegram("\n".join(zeilen))
    db.executemany("UPDATE signals SET notified_at = ? WHERE id = ?",
                   [(jetzt, r[0]) for r in rows])
    db.commit()
    return len(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--notify", action="store_true", help="neue Kaufsignale per Telegram melden")
    ap.add_argument("--only", type=int, nargs="+", metavar="ID",
                    help="nur diese Eintraege holen — der Server ruft das beim Anlegen auf")
    ap.add_argument("--sniper", action="store_true",
                    help="nur Eintraege mit Sniper-Haken — engmaschiger Cron, Schnapper-Logik")
    ap.add_argument("--alert", type=int, metavar="SIGNAL",
                    help="Sniper-Meldung fuer dieses Signal schicken (nach dem Warenkorb-Check)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")

    db = sqlite3.connect(DB_PATH)
    ensure_schema(db)
    if args.alert:
        return 0 if send_sniper_alert(db, args.alert) else 1
    api_key = get_setting(db, "brightdata_api_key")
    zone = get_setting(db, "brightdata_zone", "cardmarket")
    me = get_setting(db, "cardmarket_user")
    below_pct = get(db, "sig_buy_below_median_pct", 12)
    scam_pct = get(db, "scam_max_pct_of_median", 55)
    scam_sales = int(get(db, "scam_max_sales", 50))
    snipe_pct = get(db, "sniper_min_pct", 3)
    snipe_eur = get(db, "sniper_min_eur", 10)
    snipe_pruef = get(db, "sniper_check_pct", 25)
    if not api_key:
        log.error("Kein Bright-Data-Key in settings")
        return 2

    sql = """SELECT id, product_url, name, condition, language, target_price, max_price,
                    image, kind, sniper FROM watchlist WHERE active = 1"""
    params: tuple = ()
    if args.only:
        sql += f" AND id IN ({','.join('?' * len(args.only))})"
        params = tuple(args.only)
    if args.sniper:
        sql += " AND sniper = 1"
    items = [dict(zip(["id", "product_url", "name", "condition", "language",
                       "target_price", "max_price", "image", "kind", "sniper"], r))
             for r in db.execute(sql, params)]
    if not items:
        log.info("Watchlist ist leer")
        return 0
    log.info("%d Wunschkarten", len(items))
    gesperrt = gesperrte_verkaeufer(db)
    versand = Versandtabelle(lade_tabelle(ziel=int(get(db, "versand_ziel_id", 1))))

    def load(it):
        try:
            return it, bd_fetch(build_url(it["product_url"], it["condition"], it["language"]),
                                api_key, zone), None
        except Exception as e:
            return it, None, e

    now = datetime.now().isoformat(timespec="seconds")
    hits = failed = 0

    with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
        for it, html, err in pool.map(load, items):
            if err is not None:
                log.error("  %s — Abruf fehlgeschlagen: %s", it["name"][:34], err)
                failed += 1
                continue

            # Eigene Angebote gehoeren nicht in den Kaufpreis-Vergleich.
            # Sealed hat keinen Zustand (condition leer) — dann zaehlt nur die
            # Sprache. Die wird zusaetzlich clientseitig geprueft, weil der
            # ?language=-Filter nicht sicher greift: die /de/-Seite von „Der Eine
            # Ring" liefert mit ?language=3 lauter englische Angebote. Ein Angebot
            # ohne lesbare Sprache ist darum nicht vergleichbar und faellt raus.
            alle = [c for c in parse_competitors(html)
                    if c["seller"].lower() != (me or "").lower()
                    and c["seller"].lower() not in gesperrt]
            offers = [c for c in alle
                      if (not it["condition"] or c["condition"] == it["condition"])
                      and (not it["language"] or c.get("language") == it["language"])]
            if alle and not any(c.get("language") for c in alle):
                log.warning("  %-34s Sprache der Angebote nicht lesbar — Markup geaendert?",
                            it["name"][:34])
            market = extract_prices(html) or {}

            # Der aus der URL geratene Name wird durch den echten von der Seite
            # ersetzt, sobald wir sie einmal geholt haben.
            info = extract_card_info(html) or {}
            title = info.get("page_title")
            if title and title.strip() and title.strip() != it["name"]:
                db.execute("UPDATE watchlist SET name = ? WHERE id = ?",
                           (title.strip(), it["id"]))
                it["name"] = title.strip()

            # Vorschaubild für die Liste. download_image lädt nur, wenn die Datei
            # noch fehlt — an normalen Tagen kostet der Aufruf also nichts.
            if not args.dry_run:
                image = download_image(info.get("image_url"), it["product_url"])
                if image and image != it["image"]:
                    db.execute("UPDATE watchlist SET image = ? WHERE id = ?",
                               (image, it["id"]))
            # Was das Angebot zu Hause kostet: Cardmarkets Versandtabelle fuer das
            # Herkunftsland, darin die guenstigste Versandart, die den Wert abdeckt.
            # Das guenstigste Angebot ist das mit dem niedrigsten Gesamtpreis.
            for c in offers:
                v = versand.kosten(c.get("origin"), c["price"], it["kind"])
                c["shipping"], c["total"] = v["preis"], round(c["price"] + v["preis"], 2)
            # Betrugsverdacht aussortieren, bevor das Guenstigste bestimmt wird —
            # gemessen am Mittelfeld aller passenden Angebote.
            med_alle = median([c["total"] for c in offers])
            verdaechtig = [c for c in offers
                           if is_suspicious(c["total"], med_alle, c.get("sales"), scam_pct, scam_sales)]
            for c in verdaechtig:
                log.info("  Verdaechtig, ignoriert: %.2f € von %s (%s Verkaeufe) bei Mittelfeld %.2f €",
                         c["total"], c["seller"], c.get("sales", "?"), med_alle)
            raus = {id(c) for c in verdaechtig}
            offers = [c for c in offers if id(c) not in raus]
            best = min(offers, key=lambda c: c["total"]) if offers else None
            prices = [c["price"] for c in offers]
            snap = {
                "best_price": best["price"] if best else None,
                "best_total": best["total"] if best else None,
                "best_shipping": best["shipping"] if best else None,
                "best_origin": land_name(best.get("origin")) if best else None,
                "median_price": median(prices),
                "median_total": median([c["total"] for c in offers]),
                "offers_count": len(prices),
                "suspicious": len(verdaechtig),
                "market_trend": market.get("trend"),
                "market_avg7": market.get("avg7"),
                "market_avg30": market.get("avg30"),
            }

            prev = db.execute("""SELECT best_total, median_total FROM watchlist_snapshots
                                 WHERE watchlist_id = ? ORDER BY captured_at DESC LIMIT 1""",
                              (it["id"],)).fetchone()
            prev_d = {"best_total": prev[0], "median_total": prev[1]} if prev else None

            # Ein Link, der auf eine Set-Uebersicht statt auf eine Karte zeigt,
            # liefert dauerhaft null Angebote. Ohne Hinweis liegt so ein Eintrag
            # still auf der Liste und man wartet auf ein Signal, das nie kommt.
            problem = None
            if "articleRow" not in html:
                problem = "Der Link führt nicht auf eine Kartenseite"
            elif not offers and verdaechtig:
                problem = f"Nur Angebote mit Betrugsverdacht ({len(verdaechtig)})"
            elif not offers:
                problem = (f"Kein Angebot in {it['condition']}/{it['language']}" if it["condition"]
                           else f"Kein Angebot in Sprache {it['language']}")

            if not args.dry_run:
                db.execute("UPDATE watchlist SET last_error = ? WHERE id = ?",
                           (problem, it["id"]))
            if problem:
                log.warning("  %-34s %s", it["name"][:34], problem)

            # Sniper-Eintraege messen das Angebot am Rest des Marktes, nicht am Median.
            if it["sniper"]:
                sig = evaluate_snipe(offers, boden_7d(db, it["id"], now), snipe_pct,
                                     snipe_pruef, it["target_price"], snipe_eur)
            else:
                sig = evaluate_buy(it, snap, prev_d, below_pct)
            mark = "  ← KAUFEN" if sig else ""
            log.info("  %-34s %s € inkl. %s € Versand (Median %s, %d Angebote)%s",
                     it["name"][:34],
                     f"{snap['best_total']:.2f}" if snap["best_total"] else "—",
                     f"{snap['best_shipping']:.2f}" if snap["best_shipping"] is not None else "—",
                     f"{snap['median_total']:.2f}" if snap["median_total"] else "—",
                     snap["offers_count"], mark)

            if args.dry_run:
                continue

            db.execute("""INSERT OR IGNORE INTO watchlist_snapshots
                (watchlist_id, captured_at, best_price, median_price, offers_count,
                 market_trend, market_avg7, market_avg30,
                 best_total, best_shipping, best_origin, median_total, suspicious)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (it["id"], now, snap["best_price"], snap["median_price"],
                 snap["offers_count"], snap["market_trend"], snap["market_avg7"],
                 snap["market_avg30"], snap["best_total"], snap["best_shipping"],
                 snap["best_origin"], snap["median_total"], snap["suspicious"]))

            if sig and not schon_gemeldet(db, it["id"], sig):
                hits += 1
                db.execute("""INSERT INTO signals
                    (watchlist_id, kind, created_at, my_price, suggested_price, detail, offer_key,
                     article_id, check_status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (it["id"], BUY, now, None, sig["price"], sig["detail"], sig.get("offer_key"),
                     sig.get("article_id"), "pending" if it["sniper"] else None))
            db.commit()

    log.info("Fertig: %d Kaufsignale, %d Fehler", hits, failed)
    if args.notify and not args.dry_run:
        log.info("%d per Telegram gemeldet", notify(db))
    return 0


def schon_gemeldet(db, watchlist_id: int, sig: dict) -> bool:
    """Wiedervorlagesperre, damit ein dauerhaft guenstiges Angebot nicht jeden Lauf meldet.

    Bis 14.10.2026 sperrte jedes Signal das Produkt pauschal 7 Tage. Dadurch ging
    Reality Fracture am 03.10. fuer 203 € (statt ~370 €) ungemeldet durch, weil am
    27.09. schon eins fuer 241 € gekommen war. Jetzt gilt: Ein Angebot, das
    mindestens 3 % unter der letzten Meldung liegt, kommt immer durch. Schnapper
    sperren zusaetzlich nur dasselbe Angebot (Verkaeufer + Preis).
    """
    cutoff = (datetime.now() - timedelta(days=7)).isoformat(timespec="seconds")
    if sig.get("offer_key"):
        if db.execute("""SELECT 1 FROM signals WHERE watchlist_id = ? AND kind = ?
                         AND offer_key = ? AND created_at > ? LIMIT 1""",
                      (watchlist_id, BUY, sig["offer_key"], cutoff)).fetchone():
            return True
        # Ein anderes Angebot zum (fast) selben Preis wie gerade eben gemeldet:
        # nicht nochmal klingeln, solange die letzte Meldung juenger als 6 h ist.
        kurz = (datetime.now() - timedelta(hours=6)).isoformat(timespec="seconds")
        return db.execute("""SELECT 1 FROM signals WHERE watchlist_id = ? AND kind = ?
                             AND created_at > ? AND suggested_price <= ? LIMIT 1""",
                          (watchlist_id, BUY, kurz, sig["price"] * 1.03)).fetchone() is not None
    return db.execute("""SELECT 1 FROM signals WHERE watchlist_id = ? AND kind = ?
                         AND created_at > ? AND dismissed_at IS NULL
                         AND suggested_price <= ? LIMIT 1""",
                      (watchlist_id, BUY, cutoff, sig["price"] * 1.03)).fetchone() is not None


def ensure_schema(db) -> None:
    """Spalten, die der Server (server/db.ts) auch anlegt — falls das Script zuerst laeuft."""
    for stmt in ("ALTER TABLE watchlist ADD COLUMN sniper INTEGER NOT NULL DEFAULT 0",
                 "ALTER TABLE signals ADD COLUMN offer_key TEXT",
                 "ALTER TABLE signals ADD COLUMN article_id INTEGER",
                 "ALTER TABLE signals ADD COLUMN check_status TEXT",
                 "ALTER TABLE signals ADD COLUMN check_detail TEXT",
                 "ALTER TABLE signals ADD COLUMN claimed_at TEXT",
                 """CREATE TABLE IF NOT EXISTS blocked_sellers (
                      seller TEXT PRIMARY KEY, reason TEXT,
                      created_at TEXT NOT NULL DEFAULT (datetime('now')))"""):
        try:
            db.execute(stmt)
        except sqlite3.OperationalError:
            pass


def get_setting(db, key, default=""):
    row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row and row[0] else default


if __name__ == "__main__":
    sys.exit(main())
