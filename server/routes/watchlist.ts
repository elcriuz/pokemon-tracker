import { Router } from "express"
import { getDb } from "../db"

export const watchlistRouter = Router()

const CONDITIONS = ["MT", "NM", "EX", "GD", "LP", "PL", "PO"]
const LANGUAGES = ["de", "en", "fr", "es", "it", "ja", "zh", "pt", "ru", "ko"]

/** Aus einer Cardmarket-URL Spiel und Kartenname ableiten. */
function parseProductUrl(url: string) {
  const m = url.match(/cardmarket\.com\/[a-z]{2}\/(\w+)\/Products\/([^/]+)\/([^/?]+)\/([^/?]+)/)
  if (!m) return null
  // Aus "Umbreon-ex-V2-PRE161" wird "Umbreon ex". Die Varianten-Nummer und der
  // Set-Code am Ende sind Cardmarket-Interna und stehen so auf keiner Karte.
  // Beim ersten Preisabruf wird der Name ohnehin durch den echten ersetzt.
  const name = decodeURIComponent(m[4])
    .replace(/-V\d+(?=-|$)/g, "")
    .replace(/-[A-Z]{2,5}\d{1,4}[a-z]?$/, "")
    .replace(/-/g, " ")
    .trim()
  return {
    game: m[1],
    kind: /Singles/i.test(m[2]) ? "single" : "sealed",
    name,
    expansion: m[3].replace(/-/g, " "),
  }
}

watchlistRouter.get("/", (_req, res) => {
  const db = getDb()
  const rows = db.prepare(`
    SELECT w.*, s.captured_at, s.best_price, s.median_price, s.offers_count,
           s.market_trend, s.market_avg7, s.market_avg30
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
      SELECT best_price FROM watchlist_snapshots
      WHERE watchlist_id = ? AND best_price IS NOT NULL ORDER BY captured_at
    `).all(r.id) as any[]
    const prices = hist.map((h) => h.best_price)
    const low = prices.length ? Math.min(...prices) : null
    const high = prices.length ? Math.max(...prices) : null
    return {
      ...r,
      signals: byItem.get(r.id) ?? [],
      history_low: low,
      history_high: high,
      history_points: prices.length,
      problem: r.last_error ?? null,
      // 0 = so guenstig wie nie beobachtet, 1 = Hoechststand
      position: low != null && high != null && high > low && r.best_price != null
        ? (r.best_price - low) / (high - low)
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
  const { url, condition, language, target_price, note } = req.body ?? {}
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
  const cond = CONDITIONS.includes(condition) ? condition : "NM"
  const lang = LANGUAGES.includes(language) ? language : "de"

  try {
    const info = db.prepare(`
      INSERT INTO watchlist (product_url, name, game, kind, condition, language,
                             target_price, note)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    `).run(url.split("?")[0], parsed.name, parsed.game, parsed.kind, cond, lang,
           target_price ? Number(target_price) : null, note ?? "")
    res.json({ id: info.lastInsertRowid, ...parsed, condition: cond, language: lang })
  } catch (e: any) {
    if (String(e).includes("UNIQUE")) {
      return res.status(409).json({ error: "Steht in dieser Ausführung schon auf der Liste" })
    }
    throw e
  }
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
      SELECT captured_at, best_price, median_price, offers_count, market_trend
      FROM watchlist_snapshots WHERE watchlist_id = ? ORDER BY captured_at
    `).all(req.params.id),
  })
})
