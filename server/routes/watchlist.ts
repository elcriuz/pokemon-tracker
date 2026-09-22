import { Router } from "express"
import { spawn } from "child_process"
import path from "path"
import { fileURLToPath } from "url"
import { getDb } from "../db"

export const watchlistRouter = Router()

const BASE = path.join(path.dirname(fileURLToPath(import.meta.url)), "../..")

// Preise fuer frisch angelegte Eintraege sofort holen statt beim naechsten Cron —
// eine Zeile ohne Zahl sagt nichts. Laeuft im Hintergrund, die Antwort wartet
// nicht darauf; das Deploy-Script sieht den Lauf und verschiebt Neustarts.
function fetchNow(ids: number[]) {
  try {
    const proc = spawn("python3", ["watchlist.py", "--only", ...ids.map(String)],
      { cwd: BASE, stdio: "ignore" })
    proc.on("error", (e) => console.error("watchlist.py --only:", e))
  } catch (e) {
    console.error("watchlist.py --only:", e)
  }
}

const CONDITIONS = ["MT", "NM", "EX", "GD", "LP", "PL", "PO"]
const LANGUAGES = ["de", "en", "fr", "es", "it", "ja", "zh", "pt", "ru", "ko"]

// Cardmarkets Filter-IDs, dieselben wie in cardmarket_public.py (dort verifiziert).
// Ohne Filter zeigt die Produktseite die 50 billigsten Angebote ueber alle
// Sprachen — bei einer Box also erst mal Italienisch. Der Link aus der Liste
// soll genau die Seite oeffnen, die der Tracker vergleicht.
const LANGUAGE_IDS: Record<string, number> = {
  en: 1, fr: 2, de: 3, es: 4, it: 5, zh: 6, ja: 7, pt: 8, ru: 9, ko: 10,
}
const CONDITION_IDS: Record<string, number> = { MT: 1, NM: 2, EX: 3, GD: 4, LP: 5, PL: 6, PO: 7 }

function offerUrl(productUrl: string, condition: string, language: string) {
  const params: string[] = []
  if (LANGUAGE_IDS[language]) params.push(`language=${LANGUAGE_IDS[language]}`)
  if (CONDITION_IDS[condition]) params.push(`minCondition=${CONDITION_IDS[condition]}`)
  return params.length ? `${productUrl}?${params.join("&")}` : productUrl
}

/** Aus einer Cardmarket-URL Spiel und Kartenname ableiten. */
/**
 * Zwei URL-Formen:
 *   Singles:  /Products/Singles/<Set>/<Karte>        (drei Segmente)
 *   Sealed:   /Products/Booster-Boxes/<Produkt>       (zwei Segmente)
 * Displays, Booster, ETBs haben keinen Zustand — nur Sprache und Preis.
 */
function parseProductUrl(url: string) {
  const m = url.match(/cardmarket\.com\/[a-z]{2}\/(\w+)\/Products\/([^?#]+)/)
  if (!m) return null
  const seg = m[2].split("/").filter(Boolean)
  if (seg.length < 2) return null
  const category = seg[0]
  const kind = /^Singles$/i.test(category) ? "single" : "sealed"
  if (kind === "single" && seg.length < 3) return null
  const slug = seg[seg.length - 1]
  // Aus "Umbreon-ex-V2-PRE161" wird "Umbreon ex". Die Varianten-Nummer und der
  // Set-Code am Ende sind Cardmarket-Interna und stehen so auf keiner Karte.
  // Beim ersten Preisabruf wird der Name ohnehin durch den echten ersetzt.
  const name = decodeURIComponent(slug)
    .replace(/-V\d+(?=-|$)/g, "")
    .replace(/-[A-Z]{2,5}\d{1,4}[a-z]?$/, "")
    .replace(/-/g, " ")
    .trim()
  return {
    game: m[1],
    kind,
    name,
    expansion: (seg.length >= 3 ? seg[1] : category).replace(/-/g, " "),
  }
}

watchlistRouter.get("/", (_req, res) => {
  const db = getDb()
  const rows = db.prepare(`
    SELECT w.*, s.captured_at, s.best_price, s.median_price, s.offers_count,
           s.market_trend, s.market_avg7, s.market_avg30,
           s.best_total, s.best_shipping, s.best_origin, s.median_total
    FROM watchlist w
    LEFT JOIN watchlist_snapshots s
      ON s.watchlist_id = w.id
     AND s.captured_at = (SELECT MAX(captured_at) FROM watchlist_snapshots
                          WHERE watchlist_id = w.id)
    WHERE w.active = 1
    ORDER BY w.created_at DESC
  `).all() as any[]

  const sigs = db.prepare(`
    SELECT id, watchlist_id, suggested_price, detail, created_at FROM signals
    WHERE kind = 'buy' AND watchlist_id IS NOT NULL
      AND dismissed_at IS NULL ORDER BY created_at DESC
  `).all() as any[]
  const byItem = new Map<number, any[]>()
  for (const s of sigs) {
    if (!byItem.has(s.watchlist_id)) byItem.set(s.watchlist_id, [])
    byItem.get(s.watchlist_id)!.push(s)
  }

  const items = rows.map((r) => {
    // Der eigentliche Mehrwert gegenueber einer Wantlist: Wo steht der Preis
    // gemessen an dem, was wir seit dem Eintragen gesehen haben?
    const hist = db.prepare(`
      SELECT best_price, best_total FROM watchlist_snapshots
      WHERE watchlist_id = ? AND best_price IS NOT NULL ORDER BY captured_at
    `).all(r.id) as any[]
    // Preisstaende vor dem 22.09.2026 kennen keinen Versand. Damit die Spanne zum
    // heutigen Gesamtpreis passt, bekommen sie den heutigen Versand dazu — der
    // haengt am Herkunftsland und der Wertstufe und aendert sich kaum.
    const prices = hist.map((h) => h.best_total ?? h.best_price + (r.best_shipping ?? 0))
    const current = r.best_total ?? r.best_price
    const low = prices.length ? Math.min(...prices) : null
    const high = prices.length ? Math.max(...prices) : null
    return {
      ...r,
      offer_url: offerUrl(r.product_url, r.condition, r.language),
      signals: byItem.get(r.id) ?? [],
      history_low: low,
      history_high: high,
      history_points: prices.length,
      problem: r.last_error ?? null,
      // 0 = so guenstig wie nie beobachtet, 1 = Hoechststand
      position: low != null && high != null && high > low && current != null
        ? (current - low) / (high - low)
        : null,
    }
  })

  res.json({
    items,
    summary: {
      count: items.length,
      with_signal: items.filter((i) => i.signals.length > 0).length,
      total_target: items.reduce((a, i) => a + (i.target_price ?? 0), 0),
    },
    options: { conditions: CONDITIONS, languages: LANGUAGES },
  })
})

watchlistRouter.post("/", (req, res) => {
  const db = getDb()
  const { url, condition, language, languages, target_price, note } = req.body ?? {}
  if (!url || typeof url !== "string") {
    return res.status(400).json({ error: "Cardmarket-Link fehlt" })
  }
  const parsed = parseProductUrl(url)
  if (!parsed) {
    return res.status(400).json({
      error: "Das sieht nicht nach einem Cardmarket-Produktlink aus. " +
             "Erwartet wird z.B. cardmarket.com/de/Pokemon/Products/Singles/<Set>/<Karte>",
    })
  }
  // Sealed hat keinen Zustand — leer speichern, sonst filtert der Abruf ins Leere.
  const cond = parsed.kind === "sealed" ? "" : (CONDITIONS.includes(condition) ? condition : "NM")
  // Mehrere Sprachen auf einmal: je Sprache ein Eintrag, denn jede ist ihr eigener
  // Markt mit eigenem Verlauf und Signal. Die Liste zeigt sie gruppiert.
  const wanted: string[] = (Array.isArray(languages) && languages.length ? languages : [language])
    .filter((l: any) => LANGUAGES.includes(l))
  if (!wanted.length) wanted.push("de")

  const insert = db.prepare(`
    INSERT INTO watchlist (product_url, name, game, kind, condition, language,
                           target_price, note)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
  `)
  const ids: number[] = []
  const created: string[] = []
  for (const lang of wanted) {
    try {
      const info = insert.run(url.split("?")[0], parsed.name, parsed.game, parsed.kind, cond, lang,
                              target_price ? Number(target_price) : null, note ?? "")
      ids.push(Number(info.lastInsertRowid))
      created.push(lang)
    } catch (e: any) {
      if (!String(e).includes("UNIQUE")) throw e
    }
  }
  if (!ids.length) {
    return res.status(409).json({ error: "Steht in dieser Ausführung schon auf der Liste" })
  }
  fetchNow(ids)
  res.json({ ids, id: ids[0], ...parsed, condition: cond, languages: created })
})

watchlistRouter.patch("/:id", (req, res) => {
  const db = getDb()
  const { target_price, note } = req.body ?? {}
  const info = db.prepare(
    "UPDATE watchlist SET target_price = COALESCE(?, target_price), note = COALESCE(?, note) WHERE id = ?"
  ).run(target_price ?? null, note ?? null, req.params.id)
  if (!info.changes) return res.status(404).json({ error: "Eintrag nicht gefunden" })
  res.json({ ok: true })
})

watchlistRouter.delete("/:id", (req, res) => {
  const db = getDb()
  // Deaktivieren statt loeschen — der Preisverlauf bleibt erhalten, falls die
  // Karte spaeter wieder interessant wird.
  const info = db.prepare("UPDATE watchlist SET active = 0 WHERE id = ?").run(req.params.id)
  if (!info.changes) return res.status(404).json({ error: "Eintrag nicht gefunden" })
  res.json({ ok: true })
})

watchlistRouter.get("/history/:id", (req, res) => {
  const db = getDb()
  res.json({
    items: db.prepare(`
      SELECT captured_at, best_price, median_price, offers_count, market_trend,
             best_total, best_shipping, best_origin, median_total
      FROM watchlist_snapshots WHERE watchlist_id = ? ORDER BY captured_at
    `).all(req.params.id),
  })
})
