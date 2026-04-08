import { Router } from "express"
import { getDb } from "../db"

export const cardsRouter = Router()

// GET /api/cards - all cards with latest price
cardsRouter.get("/", (_req, res) => {
  const db = getDb()
  const cards = db
    .prepare(`
      SELECT c.*, p.value, p.trend, p.avg7, p.avg30, p.avg1, p.from_price,
             p.available_items, p.psa10_low, p.psa9_low, p.cgc10_low, p.bgs10_low,
             p.scraped_at, p.error
      FROM cards c
      LEFT JOIN prices p ON p.card_id = c.id
        AND p.scraped_at = (SELECT MAX(p2.scraped_at) FROM prices p2 WHERE p2.card_id = c.id AND p2.value IS NOT NULL)
      ORDER BY c.name
    `)
    .all()
  res.json(cards)
})

// GET /api/cards/:id - single card with latest price
cardsRouter.get("/:id", (req, res) => {
  const db = getDb()
  const card = db
    .prepare(`
      SELECT c.*, p.value, p.trend, p.avg7, p.avg30, p.avg1, p.from_price,
             p.available_items, p.psa10_low, p.psa9_low, p.cgc10_low, p.bgs10_low,
             p.scraped_at, p.error
      FROM cards c
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
  const { url, name, grade, notes, purchase_price, purchase_date } = req.body
  if (!url) return res.status(400).json({ error: "URL is required" })

  try {
    const result = db
      .prepare("INSERT INTO cards (url, name, grade, notes, purchase_price, purchase_date) VALUES (?, ?, ?, ?, ?, ?)")
      .run(url, name || "", (grade || "").toUpperCase(), notes || "", purchase_price ?? null, purchase_date || null)
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
  const { name, grade, notes, purchase_price, purchase_date } = req.body
  const existing = db.prepare("SELECT * FROM cards WHERE id = ?").get(req.params.id) as any
  if (!existing) return res.status(404).json({ error: "Card not found" })

  db.prepare(`
    UPDATE cards SET name = ?, grade = ?, notes = ?, purchase_price = ?, purchase_date = ?, updated_at = datetime('now')
    WHERE id = ?
  `).run(
    name ?? existing.name,
    grade !== undefined ? grade.toUpperCase() : existing.grade,
    notes ?? existing.notes,
    purchase_price !== undefined ? purchase_price : existing.purchase_price,
    purchase_date !== undefined ? purchase_date : existing.purchase_date,
    req.params.id
  )
  const card = db.prepare("SELECT * FROM cards WHERE id = ?").get(req.params.id)
  res.json(card)
})

// DELETE /api/cards/:id
cardsRouter.delete("/:id", (req, res) => {
  const db = getDb()
  const result = db.prepare("DELETE FROM cards WHERE id = ?").run(req.params.id)
  if (result.changes === 0) return res.status(404).json({ error: "Card not found" })
  res.json({ ok: true })
})
