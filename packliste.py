#!/usr/bin/env python3
"""Packliste fuer den Versand: was muss morgen wohin, und was darf das Porto kosten.

Erzeugt ein PDF mit einer Seite je Sendung — Lieferanschrift, Kartenbilder,
gewaehlte Versandart und das Portobudget (das, was der Kaeufer bezahlt hat).

Bewusst sparsam mit Aufrufen: eine Uebersichtsseite plus eine Seite je
Bestellung. Cardmarket sperrt bei zu vielen Zugriffen (Error 1015).

  python3 packliste.py                    # bezahlte, noch nicht versandte
  python3 packliste.py --out /tmp/x.pdf
"""
from __future__ import annotations

import argparse
import base64
import html as html_mod
import logging
import re
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

CDP_URL = "http://localhost:9222"
BASE = "https://www.cardmarket.com"
PAGE_DELAY_MS = 2500

log = logging.getLogger("packliste")

CONDITIONS = {1: "MT", 2: "NM", 3: "EX", 4: "GD", 5: "LP", 6: "PL", 7: "PO"}
LANGUAGES = {1: "EN", 2: "FR", 3: "DE", 4: "ES", 5: "IT",
             6: "ZH", 7: "JA", 8: "PT", 9: "RU", 10: "KO", 11: "ZH-T"}

ROW_RE = re.compile(r"<tr\s+data-article-id=[^>]*>")
ATTR_RE = re.compile(r'data-([\w-]+)="([^"]*)"')
IMG_RE = re.compile(r"https://product-images\.s3\.cardmarket\.com/[\w/]+\.jpg")


def check_access(page) -> None:
    """Unterscheidet die drei Zustaende, die alle wie eine leere Seite aussehen."""
    title = (page.title() or "").lower()
    body = page.inner_text("body")[:400].lower()
    if "error 1015" in body or "rate limited" in body:
        raise RuntimeError(
            "Cardmarket hat uns wegen zu vieler Zugriffe voruebergehend gesperrt "
            "(Error 1015). Das laeuft von selbst aus — spaeter erneut versuchen.")
    if any(m in title or m in body for m in
           ("just a moment", "cloudflare", "security verification")):
        raise RuntimeError("Cloudflare verlangt eine Bestaetigung — einmal unter "
                           "http://192.168.1.91:6080/vnc.html klicken")
    if "anmeldung" in title or "Account/Login" in page.url:
        raise RuntimeError("Nicht angemeldet — bitte ueber noVNC einloggen")


def parse_de_price(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return float(s.replace(".", "").replace(",", "."))
    except ValueError:
        return None


def parse_address(text: str) -> list[str]:
    """Die Anschrift steht als Block zwischen 'Lieferanschrift' und 'Versandmethode'."""
    m = re.search(r"Lieferanschrift\s*\n(.*?)(?:Versandmethode|Bewertung|Kommentar)",
                  text, re.S)
    if not m:
        return []
    lines = [l.strip() for l in m.group(1).split("\n") if l.strip()]
    return lines[:6]


def parse_order(page, order_id: str, game: str) -> dict:
    page.goto(f"{BASE}/de/{game}/Orders/{order_id}", wait_until="domcontentloaded",
              timeout=60000)
    page.wait_for_timeout(PAGE_DELAY_MS)
    check_access(page)

    html = page.content()
    text = re.sub(r"[ \t]+", " ", page.inner_text("body"))

    def money(label: str) -> float | None:
        m = re.search(label + r"\s*([\d.]+,\d{2})\s*€", text)
        return parse_de_price(m.group(1)) if m else None

    buyer = ""
    bm = re.search(r"Verkauf #(\d+)\s*\n\s*Verkauf #\1\s*\n\s*([^\n]+)", text)
    if bm:
        buyer = bm.group(2).strip()

    method = ""
    mm = re.search(r"Versandmethode:\s*\n?\s*([^\n]+)", text)
    if mm:
        method = mm.group(1).strip()

    items = []
    for m in ROW_RE.finditer(html):
        attrs = dict(ATTR_RE.findall(m.group(0)))
        if "article-id" not in attrs:
            continue
        tail = html[m.end():m.end() + 2500]
        img = IMG_RE.search(tail)
        try:
            cond = CONDITIONS.get(int(attrs.get("condition") or 0), "")
            lang = LANGUAGES.get(int(attrs.get("language") or 0), "")
        except ValueError:
            cond = lang = ""
        items.append({
            "name": html_mod.unescape(attrs.get("name", "")),
            "expansion": html_mod.unescape(attrs.get("expansion-name", "")),
            "number": attrs.get("number", ""),
            "condition": cond,
            "language": lang,
            "amount": int(attrs.get("amount") or 1),
            "price": parse_de_price((attrs.get("price") or "").replace(".", ",")),
            "comment": html_mod.unescape(attrs.get("comment", "")),
            "image": img.group(0) if img else None,
        })

    return {
        "id": order_id,
        "game": game,
        "buyer": buyer,
        "address": parse_address(text),
        "method": method,
        "item_value": money("Artikelwert"),
        "shipping": money("Versandkosten"),
        "total": money("Gesamtsumme"),
        "items": items,
    }


def fetch_image(url: str) -> str | None:
    """Bild als data-URI, damit das PDF ohne Netz vollstaendig ist."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return "data:image/jpeg;base64," + base64.b64encode(r.read()).decode()
    except Exception as e:
        log.warning("  Bild nicht geladen (%s): %s", url[-24:], e)
        return None


# ---------------------------------------------------------------- PDF

CSS = """
@page { size: A4; margin: 14mm 12mm; }
* { box-sizing: border-box; }
body { font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
       font-size: 10.5pt; color: #16191c; margin: 0; }
.sendung { page-break-after: always; }
.sendung:last-child { page-break-after: auto; }
.kopf { display: flex; justify-content: space-between; align-items: flex-start;
        border-bottom: 2px solid #16191c; padding-bottom: 6px; margin-bottom: 12px; }
.kopf h1 { font-size: 17pt; margin: 0 0 2px; }
.kopf .meta { font-size: 9pt; color: #666; }
.nr { font-family: ui-monospace, Menlo, monospace; font-size: 9pt; color: #666;
      text-align: right; white-space: nowrap; }
.zeile { display: flex; gap: 12px; margin-bottom: 12px; width: 100%; }
.box { border: 1px solid #d8d5cf; border-radius: 4px; padding: 9px 11px;
       min-width: 0; overflow-wrap: break-word; }
.adresse { flex: 1.15 1 0; }
.adresse .label, .versand .label { font-size: 7.5pt; letter-spacing: .09em;
      text-transform: uppercase; color: #888; margin-bottom: 5px; }
.adresse .an { font-size: 12pt; line-height: 1.45; }
.versand { flex: 1 1 0; }
.versand .art { font-size: 11.5pt; font-weight: 600; margin-bottom: 8px; }
.budget { background: #fdf6e3; border: 1px solid #e8d9a8; border-radius: 4px;
          padding: 7px 10px; margin-top: 6px; }
.budget .betrag { font-size: 16pt; font-weight: 700; color: #8a6d1f; }
.budget .hinweis { font-size: 8pt; color: #8a7a4f; margin-top: 1px; }
table { width: 100%; border-collapse: collapse; }
th { font-size: 7.5pt; letter-spacing: .08em; text-transform: uppercase;
     color: #888; text-align: left; border-bottom: 1.5px solid #d8d5cf;
     padding: 4px 6px; font-weight: 500; }
td { border-bottom: 1px solid #eceae5; padding: 9px 6px; vertical-align: middle; }
/* Die Bilder sind zum Abgleichen da, nicht zur Zierde — Stefan muss die Karte
   in der Hand mit der Abbildung vergleichen koennen. */
td.bild { width: 112px; }
td.bild img { width: 104px; border: 1px solid #ddd9d2; border-radius: 5px;
              display: block; }
td.anz { width: 38px; font-weight: 700; font-size: 14pt; }
.karte { font-weight: 600; font-size: 12.5pt; }
.set { font-size: 9.5pt; color: #777; margin-top: 2px; }
.zweitname { font-size: 9pt; color: #999; margin-top: 1px; }
  .zustand { font-family: ui-monospace, Menlo, monospace; font-size: 11pt;
           background: #f0eeea; border-radius: 3px; padding: 3px 8px; }
.komm { font-size: 9pt; color: #777; font-style: italic; margin-top: 3px; }
td.preis { text-align: right; font-variant-numeric: tabular-nums;
           white-space: nowrap; color: #555; font-size: 11.5pt; }
.haken { width: 22px; height: 22px; border: 2px solid #999; border-radius: 4px; }
.fuss { margin-top: 10px; font-size: 8.5pt; color: #777;
        display: flex; justify-content: space-between; }
.titel { page-break-after: always; padding-top: 40mm; text-align: center; }
.titel h1 { font-size: 26pt; margin: 0 0 6px; }
.titel .datum { font-size: 13pt; color: #666; }
.titel .zahlen { margin-top: 26px; display: flex; justify-content: center; gap: 34px; }
.titel .zahl { font-size: 22pt; font-weight: 700; }
.titel .bez { font-size: 9pt; color: #777; text-transform: uppercase;
              letter-spacing: .08em; }
.titel .liste { margin: 30px auto 0; max-width: 130mm; text-align: left;
                font-size: 10pt; }
.titel .liste div { display: flex; justify-content: space-between;
                    border-bottom: 1px solid #eceae5; padding: 5px 0; }
"""


def name_aus_slug(slug: str) -> str:
    """Aus 'Sacred-Foundry' wird 'Sacred Foundry'.

    Cardmarket liefert im data-name immer den deutschen Namen, weil wir die
    deutsche Seite lesen. Auf einer englischen Karte steht aber der englische —
    und danach sucht Stefan beim Abgleichen. Der englische Name steckt in der
    Produkt-URL. Satzzeichen gehen dabei verloren ('Rakdos, Patron of Chaos'
    wird zu 'Rakdos Patron of Chaos'); zum Wiedererkennen reicht das.
    """
    if not slug:
        return ""
    s = re.sub(r"-V\d+(?=-|$)", "", slug)
    s = re.sub(r"-[A-Z]{2,5}\d{1,4}[a-z]?$", "", s)
    return s.replace("-", " ").strip()


def karten_text(n: int) -> str:
    return f"{n} Karte" if n == 1 else f"{n} Karten"


def build_html(orders: list[dict], fuer: str, tag: str) -> str:
    gesamt_porto = sum(o["shipping"] or 0 for o in orders)
    gesamt_karten = sum(sum(i["amount"] for i in o["items"]) for o in orders)

    teile = [f"<style>{CSS}</style>"]

    # Deckblatt: der Ueberblick, bevor es an die einzelnen Sendungen geht.
    zeilen = "".join(
        f'<div><span>{html_mod.escape(o["buyer"] or o["id"])}'
        f' <span style="color:#888">· {karten_text(sum(i["amount"] for i in o["items"]))}</span></span>'
        f'<span>{(o["shipping"] or 0):.2f} € Porto</span></div>'
        for o in orders)
    teile.append(f"""
    <div class="titel">
      <h1>Versand {html_mod.escape(tag)}</h1>
      <div class="datum">für {html_mod.escape(fuer)}</div>
      <div class="zahlen">
        <div><div class="zahl">{len(orders)}</div><div class="bez">Sendungen</div></div>
        <div><div class="zahl">{gesamt_karten}</div><div class="bez">{"Karte" if gesamt_karten == 1 else "Karten"}</div></div>
        <div><div class="zahl">{gesamt_porto:.2f} €</div><div class="bez">Porto gesamt</div></div>
      </div>
      <div class="liste">{zeilen}</div>
    </div>""")

    for o in orders:
        anschrift = "<br>".join(html_mod.escape(z) for z in o["address"]) or \
                    '<span style="color:#b00">Keine Anschrift gefunden</span>'
        reihen = []
        for it in o["items"]:
            bild = (f'<img src="{it["image_data"]}" alt="">'
                    if it.get("image_data") else
                    '<div style="width:104px;height:145px;background:#f0eeea;border-radius:5px"></div>')
            komm = (f'<div class="komm">{html_mod.escape(it["comment"])}</div>'
                    if it["comment"] else "")
            nummer = f' #{html_mod.escape(it["number"])}' if it["number"] else ""

            # Bei englischen Karten steht der englische Name gross, der deutsche
            # klein darunter — so passt die Zeile zur Karte UND zu Cardmarket.
            engl = name_aus_slug(it.get("slug", "")) if it["language"] == "EN" else ""
            if engl and engl.lower() != it["name"].lower():
                titel = html_mod.escape(engl)
                zweitname = f'<div class="zweitname">dt. {html_mod.escape(it["name"])}</div>'
            else:
                titel = html_mod.escape(it["name"])
                zweitname = ""
            reihen.append(f"""
              <tr>
                <td class="bild">{bild}</td>
                <td class="anz">{it["amount"]}×</td>
                <td>
                  <div class="karte">{titel}</div>
                  {zweitname}
                  <div class="set">{html_mod.escape(it["expansion"])}{nummer}</div>
                  {komm}
                </td>
                <td><span class="zustand">{it["condition"]}/{it["language"]}</span></td>
                <td class="preis">{(it["price"] or 0):.2f} €</td>
                <td style="width:22px"><div class="haken"></div></td>
              </tr>""")

        teile.append(f"""
        <div class="sendung">
          <div class="kopf">
            <div>
              <h1>{html_mod.escape(o["buyer"] or "—")}</h1>
              <div class="meta">{karten_text(sum(i["amount"] for i in o["items"]))} ·
                Warenwert {(o["item_value"] or 0):.2f} €</div>
            </div>
            <div class="nr">Bestellung #{o["id"]}<br>{html_mod.escape(o["game"])}</div>
          </div>

          <div class="zeile">
            <div class="box adresse">
              <div class="label">Lieferanschrift</div>
              <div class="an">{anschrift}</div>
            </div>
            <div class="box versand">
              <div class="label">Versandart (vom Käufer gewählt)</div>
              <div class="art">{html_mod.escape(o["method"] or "—")}</div>
              <div class="budget">
                <div class="betrag">{(o["shipping"] or 0):.2f} €</div>
                <div class="hinweis">Portobudget — mehr zahlen wir drauf</div>
              </div>
            </div>
          </div>

          <table>
            <thead><tr>
              <th></th><th>Anz.</th><th>Karte</th><th>Zustand</th>
              <th style="text-align:right">Wert</th><th></th>
            </tr></thead>
            <tbody>{"".join(reihen)}</tbody>
          </table>

          <div class="fuss">
            <span>Gesamtsumme der Bestellung: {(o["total"] or 0):.2f} €</span>
            <span>Kästchen abhaken beim Einpacken</span>
          </div>
        </div>""")

    return "".join(teile)


def html_to_pdf(page, html: str, out: Path) -> None:
    """Ueber CDP drucken — page.pdf() gaebe es nur im Headless-Modus."""
    page.set_content(html, wait_until="load")
    page.wait_for_timeout(1200)
    cdp = page.context.new_cdp_session(page)
    res = cdp.send("Page.printToPDF", {
        "printBackground": True,
        "paperWidth": 8.27, "paperHeight": 11.69,
        "marginTop": 0, "marginBottom": 0, "marginLeft": 0, "marginRight": 0,
        "preferCSSPageSize": True,
    })
    out.write_bytes(base64.b64decode(res["data"]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    ap.add_argument("--fuer", default="Stefan")
    ap.add_argument("--state", default="Paid",
                    help="Paid = bezahlt und noch nicht versandt")
    ap.add_argument("--no-images", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s",
                        datefmt="%H:%M:%S")

    morgen = (datetime.now() + timedelta(days=1)).strftime("%d.%m.%Y")
    out = Path(args.out) if args.out else ROOT / "data" / f"packliste_{morgen.replace('.', '-')}.pdf"
    out.parent.mkdir(parents=True, exist_ok=True)

    from patchright.sync_api import sync_playwright
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP_URL)
        except Exception as e:
            log.error("Kein angemeldeter Browser (%s): %s", CDP_URL, e)
            return 2
        page = browser.contexts[0].new_page()
        try:
            page.goto(f"{BASE}/de/Pokemon/Orders/Sales/{args.state}",
                      wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(PAGE_DELAY_MS)
            check_access(page)

            html = page.content()
            gefunden = {}
            for m in re.finditer(r"/de/(\w+)/Orders/(\d+)", html):
                gefunden.setdefault(m.group(2), m.group(1))
            log.info("%d Sendungen im Zustand '%s'", len(gefunden), args.state)
            if not gefunden:
                log.info("Nichts zu verschicken — kein PDF erzeugt.")
                return 0

            orders = []
            for i, (oid, game) in enumerate(gefunden.items(), 1):
                o = parse_order(page, oid, game)
                log.info("  [%d/%d] #%s %-16s %d Karten, Porto %.2f €", i, len(gefunden),
                         oid, o["buyer"][:16], sum(x["amount"] for x in o["items"]),
                         o["shipping"] or 0)
                orders.append(o)

            if not args.no_images:
                bilder = {it["image"] for o in orders for it in o["items"] if it["image"]}
                log.info("Lade %d Kartenbilder ...", len(bilder))
                cache = {u: fetch_image(u) for u in bilder}
                for o in orders:
                    for it in o["items"]:
                        it["image_data"] = cache.get(it["image"])

            html_to_pdf(page, build_html(orders, args.fuer, morgen), out)
            log.info("PDF: %s (%d Sendungen, %.1f KB)", out, len(orders),
                     out.stat().st_size / 1024)
        except RuntimeError as e:
            log.error("%s", e)
            return 1
        finally:
            page.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
