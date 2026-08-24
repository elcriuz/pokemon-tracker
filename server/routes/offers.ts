import { Router } from "express"
import { execFile } from "child_process"
import path from "path"
import { fileURLToPath } from "url"
import { getDb } from "../db"

const __dirname_ = path.dirname(fileURLToPath(import.meta.url))
const REPRICE = path.join(__dirname_, "..", "..", "reprice.py")

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
           s.competitors_total, s.best_price, s.best_same, s.median_same, s.competitors_same,
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
    // Gemessen wird gegen den MEDIAN der zustandsgleichen Angebote — dieselbe
    // Bezugsgroesse wie in den Signalen. Gegen das guenstigste Angebot zu messen
    // waere irrefuehrend: ein einzelner Ausreisser nach unten laesst einen
    // marktgerechten Preis dann wie Wucher aussehen (Flamara: +323% gegen das
    // billigste Angebot, aber nur +31% gegen das Mittelfeld).
    const vsMedian = r.median_same && r.price ? r.price / r.median_same - 1 : null
    return { ...r, signals, days_listed: daysListed, vs_median: vsMedian }
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


/**
 * Preis eines eigenen Angebots aendern.
 *
 * Die eigentliche Arbeit macht reprice.py ueber die angemeldete Browser-Sitzung;
 * hier wird nur geprueft, angestossen und das Ergebnis zurueckgegeben. Jeder
 * Aufruf entspricht einem bewussten Klick — es gibt bewusst keinen Weg, das
 * fuer viele Angebote auf einmal auszuloesen.
 */
offersRouter.post("/:id/price", (req, res) => {
  const db = getDb()
  const price = Number(req.body?.price)
  const signalId = req.body?.signal_id ? Number(req.body.signal_id) : null

  if (!Number.isFinite(price) || price <= 0 || price > 100_000) {
    return res.status(400).json({ error: "Kein gültiger Preis" })
  }

  const listing = db.prepare(
    "SELECT cm_article_id, product_name, price, game FROM listings WHERE id = ? AND active = 1"
  ).get(req.params.id) as any
  if (!listing?.cm_article_id) {
    return res.status(404).json({ error: "Angebot nicht gefunden" })
  }

  execFile("python3", [REPRICE, "--article", String(listing.cm_article_id),
                       "--price", price.toFixed(2), "--game", listing.game],
    { timeout: 180_000 }, (err, stdout, stderr) => {
      const out = `${stdout}\n${stderr}`.trim()
      if (err) {
        // Der Skript-Text ist fuer Menschen geschrieben — direkt durchreichen,
        // statt ihn hinter einer generischen Fehlermeldung zu verstecken.
        // Die ERSTE Fehlerzeile nennt die Ursache; spaetere sind nur Hinweise.
        const line = out.split("\n").filter((l) => l.includes("ERROR"))[0]
        return res.status(422).json({
          error: line?.replace(/^.*ERROR\s+/, "") || "Preisänderung fehlgeschlagen",
          detail: out.slice(-800),
        })
      }
      if (signalId) {
        db.prepare("UPDATE signals SET applied_at = datetime('now') WHERE id = ?").run(signalId)
      }
      const now = db.prepare("SELECT price FROM listings WHERE id = ?").get(req.params.id) as any
      res.json({ ok: true, price: now?.price ?? price, was: listing.price })
    })
})
