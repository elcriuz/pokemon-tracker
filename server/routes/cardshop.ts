import { Router } from "express"
import { getDb } from "../db"

export const cardshopRouter = Router()

const SHOP_RATE = 0.80 // Shop pays 80% of CM lowest price

type Recommendation = "SELL_NOW" | "GOOD" | "HOLD" | "AVOID" | null

function getRecommendation(from_price: number | null, avg7: number | null, avg30: number | null): Recommendation {
  if (!from_price || (!avg7 && !avg30)) return null
  const r7 = avg7 ? from_price / avg7 : null
  const r30 = avg30 ? from_price / avg30 : null

  // SELL NOW: >5% above both averages
  if (r7 && r30 && r7 > 1.05 && r30 > 1.05) return "SELL_NOW"
  // GOOD: above at least one average
  if ((r7 && r7 > 1.00) || (r30 && r30 > 1.00)) return "GOOD"
  // AVOID: >5% below either average
  if ((r7 && r7 < 0.95) || (r30 && r30 < 0.95)) return "AVOID"
  // HOLD: everything in between
  return "HOLD"
}

const REC_ORDER: Record<string, number> = { SELL_NOW: 0, GOOD: 1, HOLD: 2, AVOID: 3 }

// POST /api/cardshop/:id/stash — toggle stash status
cardshopRouter.post("/:id/stash", (req, res) => {
  const db = getDb()
  const { id } = req.params
  const card = db.prepare("SELECT stash FROM cards WHERE id = ?").get(id) as any
  if (!card) return res.status(404).json({ error: "Card not found" })
  const newStash = card.stash ? 0 : 1
  db.prepare("UPDATE cards SET stash = ? WHERE id = ?").run(newStash, id)
  res.json({ id: Number(id), stash: newStash })
})

// GET /api/cardshop
cardshopRouter.get("/", (req, res) => {
  const db = getDb()
  const showStash = req.query.stash === "1"
  const showLosers = req.query.losers === "1"

  // Latest prices per card (same pattern as dashboard.ts)
  const cards = db.prepare(`
    SELECT
      c.id, c.name, c.grade, c.image, c.url, c.quantity, c.purchase_price, c.stash,
      p.from_price, p.trend, p.avg7, p.avg30, p.value, p.scraped_at,
      p.psa10_low, p.psa9_low,
      prev.from_price AS prev_from,
      prev.psa10_low AS prev_psa10,
      prev.psa9_low AS prev_psa9
    FROM cards c
    LEFT JOIN prices p ON p.card_id = c.id
      AND p.scraped_at = (SELECT MAX(p2.scraped_at) FROM prices p2 WHERE p2.card_id = c.id AND p2.value IS NOT NULL)
    LEFT JOIN prices prev ON prev.card_id = c.id
      AND prev.scraped_at = (
        SELECT MAX(p3.scraped_at) FROM prices p3
        WHERE p3.card_id = c.id AND p3.value IS NOT NULL
        AND p3.scraped_at < (SELECT MAX(p4.scraped_at) FROM prices p4 WHERE p4.card_id = c.id AND p4.value IS NOT NULL)
      )
  `).all() as any[]

  // Filter stash cards unless explicitly requested
  const filteredCards = showStash ? cards : cards.filter((c: any) => !c.stash)

  let totalShopValue = 0
  let totalMarketValue = 0
  let sellNowCount = 0

  const result = filteredCards.map((c: any) => {
    const grade = (c.grade || "").toUpperCase()
    const isGraded = grade.startsWith("PSA") || grade.startsWith("CGC") || grade.startsWith("BGS")

    // For graded cards: use the matching graded price, not the raw from_price
    let market_price: number | null = null
    if (isGraded) {
      if (grade.includes("10")) market_price = c.psa10_low || c.from_price || c.value || null
      else if (grade.includes("9")) market_price = c.psa9_low || c.from_price || c.value || null
      else market_price = c.from_price || c.value || null
    } else {
      market_price = c.from_price || c.value || null
    }

    const from_price = market_price
    const shop_price = from_price ? Math.round(from_price * SHOP_RATE * 100) / 100 : null

    // For graded cards: avg7/avg30 are raw-card averages → don't compare with graded price
    // Instead use previous scrape price for trend
    let ratio_7d: number | null = null
    let ratio_30d: number | null = null
    let recommendation: Recommendation = null

    if (isGraded) {
      // Graded: compare today's graded price vs previous scrape's GRADED price
      const prev = grade.includes("10") ? (c.prev_psa10 || null) : grade.includes("9") ? (c.prev_psa9 || null) : (c.prev_from || null)
      if (from_price && prev && prev > 0) {
        const change = from_price / prev
        ratio_7d = Math.round(change * 1000) / 1000
        ratio_30d = null // no 30d data for graded
        recommendation = change > 1.05 ? "SELL_NOW" : change > 1.0 ? "GOOD" : change > 0.95 ? "HOLD" : "AVOID"
      }
    } else {
      // Raw: compare from_price against CM avg7 and avg30
      const avg7 = c.avg7 || null
      const avg30 = c.avg30 || null
      ratio_7d = from_price && avg7 ? Math.round((from_price / avg7) * 1000) / 1000 : null
      ratio_30d = from_price && avg30 ? Math.round((from_price / avg30) * 1000) / 1000 : null
      recommendation = getRecommendation(from_price, avg7, avg30)
    }
    const profit_if_sold = shop_price && c.purchase_price ? Math.round((shop_price - c.purchase_price) * 100) / 100 : null

    if (shop_price) totalShopValue += shop_price * (c.quantity || 1)
    if (from_price) totalMarketValue += from_price * (c.quantity || 1)
    if (recommendation === "SELL_NOW") sellNowCount++

    return {
      id: c.id,
      name: c.name || "?",
      grade: c.grade || "",
      image: c.image || "",
      url: c.url || "",
      quantity: c.quantity || 1,
      from_price,
      shop_price,
      trend: c.trend || null,
      avg7: isGraded ? null : (c.avg7 || null),
      avg30: isGraded ? null : (c.avg30 || null),
      isGraded,
      prev_from: c.prev_from || null,
      ratio_7d,
      ratio_30d,
      recommendation,
      purchase_price: c.purchase_price || null,
      profit_if_sold,
      stash: !!c.stash,
      scraped_at: c.scraped_at || null,
    }
  })

  // Default: nur Karten mit Gewinn (oder ohne Kaufpreis = unbekannt). Loser ausblenden.
  const visible = showLosers
    ? result
    : result.filter((c) => c.profit_if_sold == null || c.profit_if_sold > 0)
  const hiddenLosers = result.length - visible.length

  // Sort: SELL_NOW first, then by shop_price desc
  visible.sort((a, b) => {
    const ra = REC_ORDER[a.recommendation || ""] ?? 99
    const rb = REC_ORDER[b.recommendation || ""] ?? 99
    if (ra !== rb) return ra - rb
    return (b.shop_price || 0) - (a.shop_price || 0)
  })

  // Summary spiegelt nur sichtbare Karten wider
  const visibleShopValue = visible.reduce((s, c) => s + (c.shop_price || 0) * (c.quantity || 1), 0)
  const visibleMarketValue = visible.reduce((s, c) => s + (c.from_price || 0) * (c.quantity || 1), 0)
  const visibleProfit = visible.reduce((s, c) => s + (c.profit_if_sold || 0) * (c.quantity || 1), 0)
  const visibleSellNow = visible.filter((c) => c.recommendation === "SELL_NOW").length

  res.json({
    summary: {
      totalShopValue: Math.round(visibleShopValue * 100) / 100,
      totalMarketValue: Math.round(visibleMarketValue * 100) / 100,
      totalProfit: Math.round(visibleProfit * 100) / 100,
      cardCount: visible.length,
      sellNowCount: visibleSellNow,
      hiddenLosers,
      // unverändert: Werte ohne Filter
      allShopValue: Math.round(totalShopValue * 100) / 100,
      allMarketValue: Math.round(totalMarketValue * 100) / 100,
      allSellNowCount: sellNowCount,
    },
    cards: visible,
  })
})
