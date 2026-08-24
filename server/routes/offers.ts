import { Router } from "express"
import { getDb } from "../db"

export const offersRouter = Router()

/**
 * Eigene Cardmarket-Angebote mit Wettbewerbsposition.
 *
 * rank/best_price beziehen sich immer auf VERGLEICHBARE Angebote (gleiche Sprache,
 * mindestens gleicher Zustand). Ein Rang ueber alle Sprachen waere wertlos — bei
 * vielen Karten sind die guenstigsten Angebote italienisch oder japanisch.
 */
offersRouter.get("/", (req, res) => {
  const db = getDb()
  const { game, kind, signal } = req.query as Record<string, string | undefined>

  const where: string[] = ["l.active = 1"]
  const params: any[] = []
  if (game) { where.push("l.game = ?"); params.push(game) }
  if (kind) { where.push("l.kind = ?"); params.push(kind) }

  const rows = db.prepare(`
    SELECT l.id, l.game, l.product_name, l.expansion, l.product_url, l.kind,
           l.condition, l.language, l.is_foil, l.price, l.quantity, l.comment,
           l.first_seen, l.card_id,
           s.captured_at, s.rank, s.rank_capped, s.competitors_below,
           s.competitors_total, s.best_price, s.best_same, s.competitors_same,
           s.market_trend, s.market_avg7, s.market_avg30, s.market_avg1,
           s.market_available
    FROM listings l
    LEFT JOIN listing_snapshots s
      ON s.listing_id = l.id
     AND s.captured_at = (SELECT MAX(captured_at) FROM listing_snapshots
                          WHERE listing_id = l.id)
    WHERE ${where.join(" AND ")}
    ORDER BY l.price DESC
  `).all(...params) as any[]

  // Offene Signale je Angebot dazu — daran haengt die Handlungsempfehlung.
  const sigs = db.prepare(`
    SELECT id, listing_id, kind, suggested_price, detail, created_at
    FROM signals
    WHERE dismissed_at IS NULL AND applied_at IS NULL
    ORDER BY created_at DESC
  `).all() as any[]
  const byListing = new Map<number, any[]>()
  for (const s of sigs) {
    if (!byListing.has(s.listing_id)) byListing.set(s.listing_id, [])
    byListing.get(s.listing_id)!.push(s)
  }

  const items = rows.map((r) => {
    const signals = byListing.get(r.id) ?? []
    const daysListed = r.first_seen
      ? Math.floor((Date.now() - new Date(r.first_seen).getTime()) / 86_400_000)
      : null
    // Abstand zum guenstigsten Angebot im GLEICHEN Zustand. Der Produkt-Trend
    // taugt dafuer nicht: er mischt alle Zustaende und laesst gespielte Karten
    // grundsaetzlich "zu guenstig" aussehen.
    const vsBest = r.best_same && r.price ? r.price / r.best_same - 1 : null
    return { ...r, signals, days_listed: daysListed, vs_best: vsBest }
  })

  const filtered = signal
    ? items.filter((i) => i.signals.some((s: any) => s.kind === signal))
    : items

  res.json({
    items: filtered,
    summary: {
      count: items.length,
      value: items.reduce((a, i) => a + (i.price ?? 0) * (i.quantity ?? 1), 0),
      with_signal: items.filter((i) => i.signals.length > 0).length,
      games: [...new Set(items.map((i) => i.game))],
      last_run: rows[0]?.captured_at ?? null,
    },
  })
})

// Signal abhaken, ohne es umzusetzen — verschwindet aus der Liste und aus Telegram.
offersRouter.post("/signals/:id/dismiss", (req, res) => {
  const db = getDb()
  const info = db.prepare(
    "UPDATE signals SET dismissed_at = datetime('now') WHERE id = ? AND dismissed_at IS NULL"
  ).run(req.params.id)
  if (!info.changes) return res.status(404).json({ error: "Signal nicht gefunden" })
  res.json({ ok: true })
})

// GET /api/offers/history/:id — Preis- und Rangverlauf eines Angebots
offersRouter.get("/history/:id", (req, res) => {
  const db = getDb()
  const rows = db.prepare(`
    SELECT captured_at, my_price, rank, best_price, market_trend, market_avg7, market_avg30
    FROM listing_snapshots WHERE listing_id = ? ORDER BY captured_at
  `).all(req.params.id)
  res.json({ items: rows })
})
