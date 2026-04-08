import { Router } from "express"
import { getDb } from "../db"

export const pricesRouter = Router()

// GET /api/portfolio/history
pricesRouter.get("/history", (req, res) => {
  const db = getDb()
  const from = req.query.from as string | undefined
  const to = req.query.to as string | undefined

  // Pro Karte nur den letzten Preis des Tages nehmen, dann summieren
  let query = `
    SELECT date, SUM(value) as totalValue, COUNT(*) as cardCount
    FROM (
      SELECT DATE(scraped_at) as date, card_id, value,
             ROW_NUMBER() OVER (PARTITION BY card_id, DATE(scraped_at) ORDER BY scraped_at DESC) as rn
      FROM prices
      WHERE value IS NOT NULL
    )
    WHERE rn = 1
  `
  const params: string[] = []
  if (from) { query += " AND date >= ?"; params.push(from) }
  if (to) { query += " AND date <= ?"; params.push(to) }
  query += " GROUP BY date ORDER BY date ASC"

  const history = db.prepare(query).all(...params)
  res.json(history)
})
