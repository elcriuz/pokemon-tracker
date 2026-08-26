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
 * Preisaenderung vormerken statt sofort ausfuehren.
 *
 * Jede einzeln ausgefuehrte Aenderung blaettert den Bestand komplett durch —
 * bei zehn Karten also bis zu vierzig Seitenaufrufe, und genau das loest
 * Cardmarkets Bot-Pruefung aus. Gesammelt wird jede Bestandsseite einmal
 * geladen, egal wie viele Karten darauf liegen.
 */
offersRouter.post("/:id/queue", (req, res) => {
  const db = getDb()
  const price = Number(req.body?.price)
  const signalId = req.body?.signal_id ? Number(req.body.signal_id) : null

  if (!Number.isFinite(price) || price <= 0 || price > 100_000) {
    return res.status(400).json({ error: "Kein gültiger Preis" })
  }
  const listing = db.prepare(
    "SELECT id, price, product_name FROM listings WHERE id = ? AND active = 1"
  ).get(req.params.id) as any
  if (!listing) return res.status(404).json({ error: "Angebot nicht gefunden" })

  // Faktor-5-Regel schon hier, damit ein Zahlendreher gar nicht erst in die
  // Liste kommt und dort bis zum naechsten Lauf unbemerkt liegt.
  if (listing.price && (price > listing.price * 5 || price < listing.price / 5)) {
    return res.status(422).json({
      error: `Sprung von ${listing.price.toFixed(2)} € auf ${price.toFixed(2)} € ` +
             `sieht nach Zahlendreher aus`,
    })
  }

  db.prepare(`
    INSERT INTO reprice_queue (listing_id, signal_id, target_price, queued_at)
    VALUES (?, ?, ?, datetime('now'))
    ON CONFLICT(listing_id) DO UPDATE SET
      target_price = excluded.target_price, signal_id = excluded.signal_id,
      queued_at = excluded.queued_at, done_at = NULL, error = NULL
  `).run(listing.id, signalId, price)

  const open = db.prepare("SELECT COUNT(*) AS n FROM reprice_queue WHERE done_at IS NULL").get() as any
  res.json({ ok: true, queued: open.n })
})

offersRouter.delete("/queue/:id", (req, res) => {
  const db = getDb()
  db.prepare("DELETE FROM reprice_queue WHERE listing_id = ? AND done_at IS NULL")
    .run(req.params.id)
  const open = db.prepare("SELECT COUNT(*) AS n FROM reprice_queue WHERE done_at IS NULL").get() as any
  res.json({ ok: true, queued: open.n })
})

offersRouter.get("/queue", (_req, res) => {
  const db = getDb()
  res.json({
    items: db.prepare(`
      SELECT q.*, l.product_name, l.game, l.price AS current_price
      FROM reprice_queue q JOIN listings l ON l.id = q.listing_id
      WHERE q.done_at IS NULL ORDER BY q.queued_at
    `).all(),
  })
})

/** Fuehrt alle vorgemerkten Aenderungen in einem Durchgang aus. */
offersRouter.post("/queue/run", (_req, res) => {
  const db = getDb()
  const open = db.prepare("SELECT COUNT(*) AS n FROM reprice_queue WHERE done_at IS NULL").get() as any
  if (!open.n) return res.status(400).json({ error: "Nichts vorgemerkt" })

  execFile("python3", [REPRICE, "--batch"], { timeout: 600_000 }, (err, stdout, stderr) => {
    const out = `${stdout}\n${stderr}`.trim()
    const done = db.prepare(
      "SELECT COUNT(*) AS n FROM reprice_queue WHERE done_at IS NOT NULL AND done_at > datetime('now','-15 minutes')"
    ).get() as any
    const left = db.prepare("SELECT COUNT(*) AS n FROM reprice_queue WHERE done_at IS NULL").get() as any
    // Exit 3 = Bot-Pruefung. Der Rest bleibt vorgemerkt, das ist kein Datenverlust.
    const blocked = /Bot-Pruefung|Cloudflare/i.test(out)
    res.json({
      ok: !err || blocked,
      changed: done.n,
      remaining: left.n,
      blocked,
      message: blocked
        ? "Cloudflare verlangt eine Bestätigung — der Rest bleibt vorgemerkt."
        : undefined,
      detail: err ? out.slice(-800) : undefined,
    })
  })
})
