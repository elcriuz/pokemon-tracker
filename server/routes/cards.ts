import { Router } from "express"
import fs from "fs"
import path from "path"
import { fileURLToPath } from "url"
import { getDb } from "../db"

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)

export const cardsRouter = Router()

// GET /api/cards - all cards with latest price
cardsRouter.get("/", (req, res) => {
  const db = getDb()
  const binderId = req.query.binder_id as string | undefined
  let query = `
    SELECT c.*, b.name as binder_name, b.color as binder_color,
           p.value, p.trend, p.avg7, p.avg30, p.avg1, p.from_price,
           p.available_items, p.psa10_low, p.psa9_low, p.cgc10_low, p.bgs10_low,
           p.scraped_at, p.error, p.stale_grade,
           prev.value as prev_value
    FROM cards c
    LEFT JOIN binders b ON b.id = c.binder_id
    LEFT JOIN prices p ON p.card_id = c.id
      AND p.scraped_at = (SELECT MAX(p2.scraped_at) FROM prices p2 WHERE p2.card_id = c.id AND p2.value IS NOT NULL)
    LEFT JOIN prices prev ON prev.card_id = c.id
      AND prev.scraped_at = (
        SELECT MAX(p3.scraped_at) FROM prices p3
        WHERE p3.card_id = c.id AND p3.value IS NOT NULL
          AND p3.scraped_at < (SELECT MAX(p4.scraped_at) FROM prices p4 WHERE p4.card_id = c.id AND p4.value IS NOT NULL)
      )
  `
  const params: any[] = []
  const where: string[] = []
  if (binderId) {
    where.push(binderId === "none" ? "c.binder_id IS NULL" : "c.binder_id = ?")
    if (binderId !== "none") params.push(binderId)
  }
  // Verkaufte Karten gehoeren nicht mehr ins Portfolio: ?sold=1 zeigt nur sie, ?sold=all alles
  const soldFilter = req.query.sold as string | undefined
  if (soldFilter === "1") where.push("c.sold_at IS NOT NULL")
  else if (soldFilter !== "all") where.push("c.sold_at IS NULL")
  if (where.length) query += " WHERE " + where.join(" AND ")
  query += soldFilter === "1" ? " ORDER BY c.sold_at DESC" : " ORDER BY c.name"
  const cards = db.prepare(query).all(...params)
  res.json(cards)
})

// GET /api/cards/:id - single card with latest price
cardsRouter.get("/:id", (req, res) => {
  const db = getDb()
  const card = db
    .prepare(`
      SELECT c.*, b.name as binder_name, b.color as binder_color,
             p.value, p.trend, p.avg7, p.avg30, p.avg1, p.from_price,
             p.available_items, p.psa10_low, p.psa9_low, p.cgc10_low, p.bgs10_low,
             p.scraped_at, p.error, p.stale_grade
      FROM cards c
      LEFT JOIN binders b ON b.id = c.binder_id
      LEFT JOIN prices p ON p.card_id = c.id
        AND p.scraped_at = (SELECT MAX(p2.scraped_at) FROM prices p2 WHERE p2.card_id = c.id AND p2.value IS NOT NULL)
      WHERE c.id = ?
    `)
    .get(req.params.id)
  if (!card) return res.status(404).json({ error: "Card not found" })
  res.json(card)
})

// GET /api/cards/:id/prices - price history
cardsRouter.get("/:id/prices", (req, res) => {
  const db = getDb()
  const prices = db
    .prepare(`
      SELECT * FROM prices
      WHERE card_id = ?
      ORDER BY scraped_at ASC
    `)
    .all(req.params.id)
  res.json(prices)
})

// POST /api/cards - add new card
cardsRouter.post("/", (req, res) => {
  const db = getDb()
  const { url, name, grade, notes, purchase_price, purchase_date, quantity, binder_id } = req.body
  if (!url) return res.status(400).json({ error: "URL is required" })

  try {
    const result = db
      .prepare("INSERT INTO cards (url, name, grade, notes, purchase_price, purchase_date, quantity, binder_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)")
      .run(url, name || "", (grade || "").toUpperCase(), notes || "", purchase_price ?? null, purchase_date || null, quantity || 1, binder_id ?? null)
    const card = db.prepare("SELECT * FROM cards WHERE id = ?").get(result.lastInsertRowid)
    res.status(201).json(card)
  } catch (e: any) {
    if (e.message?.includes("UNIQUE")) {
      return res.status(409).json({ error: "Card with this URL already exists" })
    }
    throw e
  }
})

// PUT /api/cards/:id - update card
cardsRouter.put("/:id", (req, res) => {
  const db = getDb()
  const { name, grade, notes, purchase_price, purchase_date, quantity, binder_id } = req.body
  const existing = db.prepare("SELECT * FROM cards WHERE id = ?").get(req.params.id) as any
  if (!existing) return res.status(404).json({ error: "Card not found" })

  db.prepare(`
    UPDATE cards SET name = ?, grade = ?, notes = ?, purchase_price = ?, purchase_date = ?, quantity = ?, binder_id = ?, updated_at = datetime('now')
    WHERE id = ?
  `).run(
    name ?? existing.name,
    grade !== undefined ? grade.toUpperCase() : existing.grade,
    notes ?? existing.notes,
    purchase_price !== undefined ? purchase_price : existing.purchase_price,
    purchase_date !== undefined ? purchase_date : existing.purchase_date,
    quantity !== undefined ? quantity : existing.quantity,
    binder_id !== undefined ? binder_id : existing.binder_id,
    req.params.id
  )
  const card = db.prepare("SELECT * FROM cards WHERE id = ?").get(req.params.id)
  res.json(card)
})

// POST /api/cards/:id/watch - toggle watch flag (always-scrape)
cardsRouter.post("/:id/watch", (req, res) => {
  const db = getDb()
  const card = db.prepare("SELECT watch FROM cards WHERE id = ?").get(req.params.id) as any
  if (!card) return res.status(404).json({ error: "Card not found" })
  const newWatch = card.watch ? 0 : 1
  db.prepare("UPDATE cards SET watch = ? WHERE id = ?").run(newWatch, req.params.id)
  res.json({ id: Number(req.params.id), watch: newWatch })
})

// --- Verkauf ---------------------------------------------------------------
// Verkaufte Karten bleiben mit Preisverlauf in der Datenbank, zaehlen aber nicht
// mehr zum Portfolio. Die /bulk-Routen muessen vor den /:id-Routen stehen,
// sonst greift Express "bulk" als :id ab.

function parseIds(body: any): number[] | null {
  const ids = Array.isArray(body?.ids) ? body.ids.map(Number) : null
  if (!ids || !ids.length || ids.some((n: number) => !Number.isInteger(n) || n <= 0)) return null
  return ids
}

// Preis kann als Zahl oder als String mit deutschem Komma kommen.
// undefined = nicht angegeben, NaN = ungueltig, null = ausdruecklich leer.
function parsePrice(raw: any): number | null | undefined {
  if (raw === undefined) return undefined
  if (raw === null || raw === "") return null
  const n = typeof raw === "string" ? Number(raw.trim().replace(",", ".")) : Number(raw)
  return Number.isFinite(n) ? n : NaN
}

// POST /api/cards/bulk/sold - mehrere Karten als verkauft markieren.
// Entweder items: [{id, sold_price}] (Erloes je Karte) oder ids + gemeinsamer sold_price.
cardsRouter.post("/bulk/sold", (req, res) => {
  const db = getDb()
  const soldAt = req.body?.sold_at || new Date().toISOString().slice(0, 10)

  let items: { id: number; sold_price: number | null }[]
  if (Array.isArray(req.body?.items)) {
    items = req.body.items.map((it: any) => ({
      id: Number(it?.id),
      sold_price: parsePrice(it?.sold_price) ?? null,
    }))
    if (!items.length || items.some((it) => !Number.isInteger(it.id) || it.id <= 0)) {
      return res.status(400).json({ error: "items enthält ungültige Karten-IDs" })
    }
    if (items.some((it) => it.sold_price != null && !Number.isFinite(it.sold_price))) {
      return res.status(400).json({ error: "Verkaufspreis ist keine Zahl" })
    }
  } else {
    const ids = parseIds(req.body)
    if (!ids) return res.status(400).json({ error: "ids oder items fehlt" })
    const shared = parsePrice(req.body.sold_price) ?? null
    if (shared != null && !Number.isFinite(shared)) return res.status(400).json({ error: "Verkaufspreis ist keine Zahl" })
    items = ids.map((id) => ({ id, sold_price: shared }))
  }

  const stmt = db.prepare("UPDATE cards SET sold_at = ?, sold_price = ?, updated_at = datetime('now') WHERE id = ?")
  const run = db.transaction((list: typeof items) =>
    list.reduce((n, it) => n + stmt.run(soldAt, it.sold_price, it.id).changes, 0)
  )
  const changed = run(items)
  res.json({ ok: true, changed, sold_at: soldAt })
})

// POST /api/cards/bulk/delete - mehrere Karten endgueltig loeschen
cardsRouter.post("/bulk/delete", (req, res) => {
  const db = getDb()
  const ids = parseIds(req.body)
  if (!ids) return res.status(400).json({ error: "ids (Array von Karten-IDs) fehlt" })

  const imgStmt = db.prepare("SELECT image FROM cards WHERE id = ?")
  const images = ids.map((id) => (imgStmt.get(id) as any)?.image).filter(Boolean)
  const delStmt = db.prepare("DELETE FROM cards WHERE id = ?")
  const run = db.transaction((list: number[]) => list.reduce((n, id) => n + delStmt.run(id).changes, 0))
  const deleted = run(ids)

  const imagesDir = path.join(__dirname, "../..", "data", "images")
  for (const img of images) {
    try { fs.unlinkSync(path.join(imagesDir, img)) } catch {}
  }
  res.json({ ok: true, deleted })
})

// POST /api/cards/:id/sold - eine Karte als verkauft markieren
cardsRouter.post("/:id/sold", (req, res) => {
  const db = getDb()
  const existing = db.prepare("SELECT id FROM cards WHERE id = ?").get(req.params.id)
  if (!existing) return res.status(404).json({ error: "Card not found" })
  const soldAt = req.body?.sold_at || new Date().toISOString().slice(0, 10)
  const soldPrice = parsePrice(req.body?.sold_price) ?? null
  if (soldPrice != null && !Number.isFinite(soldPrice)) return res.status(400).json({ error: "Verkaufspreis ist keine Zahl" })

  db.prepare("UPDATE cards SET sold_at = ?, sold_price = ?, updated_at = datetime('now') WHERE id = ?")
    .run(soldAt, soldPrice, req.params.id)
  res.json(db.prepare("SELECT * FROM cards WHERE id = ?").get(req.params.id))
})

// DELETE /api/cards/:id/sold - Verkauf zurueckholen
cardsRouter.delete("/:id/sold", (req, res) => {
  const db = getDb()
  const result = db.prepare("UPDATE cards SET sold_at = NULL, sold_price = NULL, updated_at = datetime('now') WHERE id = ?")
    .run(req.params.id)
  if (result.changes === 0) return res.status(404).json({ error: "Card not found" })
  res.json(db.prepare("SELECT * FROM cards WHERE id = ?").get(req.params.id))
})

// DELETE /api/cards/:id/image - delete image so it gets re-scraped
cardsRouter.delete("/:id/image", (req, res) => {
  const db = getDb()
  const card = db.prepare("SELECT image FROM cards WHERE id = ?").get(req.params.id) as any
  if (!card) return res.status(404).json({ error: "Card not found" })
  if (card.image) {
    const imagesDir = path.join(__dirname, "../..", "data", "images")
    try { fs.unlinkSync(path.join(imagesDir, card.image)) } catch {}
    db.prepare("UPDATE cards SET image = '' WHERE id = ?").run(req.params.id)
  }
  res.json({ ok: true })
})

// DELETE /api/cards/:id
cardsRouter.delete("/:id", (req, res) => {
  const db = getDb()
  const card = db.prepare("SELECT image FROM cards WHERE id = ?").get(req.params.id) as any
  const result = db.prepare("DELETE FROM cards WHERE id = ?").run(req.params.id)
  if (result.changes === 0) return res.status(404).json({ error: "Card not found" })
  if (card?.image) {
    try { fs.unlinkSync(path.join(__dirname, "../..", "data", "images", card.image)) } catch {}
  }
  res.json({ ok: true })
})
