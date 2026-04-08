import { Router } from "express"
import { getDb } from "../db"

export const dashboardRouter = Router()

// GET /api/dashboard
dashboardRouter.get("/", (_req, res) => {
  const db = getDb()

  // Latest prices per card
  const cards = db
    .prepare(`
      SELECT c.id, c.name, c.grade, c.purchase_price, p.value, p.trend, p.scraped_at
      FROM cards c
      LEFT JOIN prices p ON p.card_id = c.id
        AND p.scraped_at = (SELECT MAX(p2.scraped_at) FROM prices p2 WHERE p2.card_id = c.id)
    `)
    .all() as any[]

  // Previous prices per card (second most recent)
  const previousPrices = db
    .prepare(`
      SELECT p.card_id, p.value
      FROM prices p
      WHERE p.scraped_at = (
        SELECT MAX(p2.scraped_at) FROM prices p2
        WHERE p2.card_id = p.card_id
          AND p2.scraped_at < (SELECT MAX(p3.scraped_at) FROM prices p3 WHERE p3.card_id = p.card_id)
      )
    `)
    .all() as any[]

  const prevMap = new Map(previousPrices.map((p) => [p.card_id, p.value]))

  const totalValue = cards.reduce((sum, c) => sum + (c.value || 0), 0)
  const previousTotal = cards.reduce((sum, c) => sum + (prevMap.get(c.id) || c.value || 0), 0)
  const changePercent = previousTotal ? ((totalValue - previousTotal) / previousTotal) * 100 : 0

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

  // Portfolio history (per card: last price of the day, then sum)
  const history = db
    .prepare(`
      SELECT date, SUM(value) as totalValue
      FROM (
        SELECT DATE(scraped_at) as date, card_id, value,
               ROW_NUMBER() OVER (PARTITION BY card_id, DATE(scraped_at) ORDER BY scraped_at DESC) as rn
        FROM prices
        WHERE value IS NOT NULL
      )
      WHERE rn = 1
      GROUP BY date
      ORDER BY date ASC
    `)
    .all()

  const cardsWithPurchase = cards.filter((c) => c.purchase_price)
  const totalPurchase = cardsWithPurchase.reduce((sum, c) => sum + c.purchase_price, 0)
  const purchaseValue = cardsWithPurchase.reduce((sum, c) => sum + (c.value || 0), 0)
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
    cardCount: cards.length,
    gradedCount: cards.filter((c) => c.grade).length,
    lastScrapeAt: lastRun?.finished_at || cards[0]?.scraped_at,
    topMovers: movers.slice(0, 6),
    portfolioHistory: history,
  })
})
