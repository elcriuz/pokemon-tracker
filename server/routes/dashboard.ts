import { Router } from "express"
import { getDb } from "../db"

export const dashboardRouter = Router()

// GET /api/dashboard
dashboardRouter.get("/", (req, res) => {
  const db = getDb()
  const binderId = req.query.binder_id as string | undefined

  // Latest prices per card
  let cardQuery = `
    SELECT c.id, c.name, c.grade, c.purchase_price, c.quantity, c.binder_id, p.value, p.trend, p.scraped_at
    FROM cards c
    LEFT JOIN prices p ON p.card_id = c.id
      AND p.scraped_at = (SELECT MAX(p2.scraped_at) FROM prices p2 WHERE p2.card_id = c.id AND p2.value IS NOT NULL)
  `
  const params: any[] = []
  if (binderId) {
    cardQuery += binderId === "none" ? " WHERE c.binder_id IS NULL" : " WHERE c.binder_id = ?"
    if (binderId !== "none") params.push(binderId)
  }

  const cards = db.prepare(cardQuery).all(...params) as any[]

  // Previous prices per card
  const previousPrices = db
    .prepare(`
      SELECT p.card_id, p.value
      FROM prices p
      WHERE p.scraped_at = (
        SELECT MAX(p2.scraped_at) FROM prices p2
        WHERE p2.card_id = p.card_id AND p2.value IS NOT NULL
          AND p2.scraped_at < (SELECT MAX(p3.scraped_at) FROM prices p3 WHERE p3.card_id = p.card_id AND p3.value IS NOT NULL)
      )
    `)
    .all() as any[]

  const prevMap = new Map(previousPrices.map((p) => [p.card_id, p.value]))

  // Quantity-aware totals
  const totalValue = cards.reduce((sum, c) => sum + (c.value || 0) * (c.quantity || 1), 0)
  const previousTotal = cards.reduce((sum, c) => sum + (prevMap.get(c.id) || c.value || 0) * (c.quantity || 1), 0)
  const changePercent = previousTotal ? ((totalValue - previousTotal) / previousTotal) * 100 : 0

  const totalCards = cards.reduce((sum, c) => sum + (c.quantity || 1), 0)

  // Top movers
  const movers = cards
    .map((c) => {
      const prev = prevMap.get(c.id)
      const changePct = prev ? ((c.value - prev) / prev) * 100 : 0
      return { ...c, previousValue: prev || c.value, changePct }
    })
    .sort((a, b) => Math.abs(b.changePct) - Math.abs(a.changePct))

  // Last scrape
  const lastRun = db
    .prepare("SELECT * FROM scrape_runs ORDER BY started_at DESC LIMIT 1")
    .get() as any

  // Portfolio history (quantity-aware)
  const history = db
    .prepare(`
      SELECT date, SUM(value * qty) as totalValue
      FROM (
        SELECT DATE(p.scraped_at) as date, p.card_id, p.value, c.quantity as qty,
               ROW_NUMBER() OVER (PARTITION BY p.card_id, DATE(p.scraped_at) ORDER BY p.scraped_at DESC) as rn
        FROM prices p
        JOIN cards c ON c.id = p.card_id
        WHERE p.value IS NOT NULL
      )
      WHERE rn = 1
      GROUP BY date
      ORDER BY date ASC
    `)
    .all()

  // Quantity-aware purchase totals
  const cardsWithPurchase = cards.filter((c) => c.purchase_price)
  const totalPurchase = cardsWithPurchase.reduce((sum, c) => sum + c.purchase_price * (c.quantity || 1), 0)
  const purchaseValue = cardsWithPurchase.reduce((sum, c) => sum + (c.value || 0) * (c.quantity || 1), 0)
  const totalProfit = totalPurchase > 0 ? purchaseValue - totalPurchase : 0
  const totalProfitPct = totalPurchase > 0 ? (totalProfit / totalPurchase) * 100 : 0

  res.json({
    totalValue,
    totalPurchase,
    purchaseCount: cardsWithPurchase.length,
    totalProfit,
    totalProfitPct,
    previousTotalValue: previousTotal,
    changePercent,
    cardCount: totalCards,
    uniqueCardCount: cards.length,
    gradedCount: cards.filter((c) => c.grade).length,
    lastScrapeAt: lastRun?.finished_at || cards[0]?.scraped_at,
    topMovers: movers.slice(0, 6),
    portfolioHistory: history,
  })
})
