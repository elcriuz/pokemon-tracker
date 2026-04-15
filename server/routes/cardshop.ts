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

// GET /api/cardshop
cardshopRouter.get("/", (req, res) => {
  const db = getDb()

  // Latest prices per card (same pattern as dashboard.ts)
  const cards = db.prepare(`
    SELECT
      c.id, c.name, c.grade, c.image, c.url, c.quantity, c.purchase_price,
      p.from_price, p.trend, p.avg7, p.avg30, p.value, p.scraped_at,
      p.psa10_low, p.psa9_low,
      prev.from_price AS prev_from
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

  let totalShopValue = 0
  let totalMarketValue = 0
  let sellNowCount = 0

  const result = cards.map((c) => {
    const from_price = c.from_price || c.value || null
    const shop_price = from_price ? Math.round(from_price * SHOP_RATE * 100) / 100 : null
    const avg7 = c.avg7 || null
    const avg30 = c.avg30 || null
    const ratio_7d = from_price && avg7 ? Math.round((from_price / avg7) * 1000) / 1000 : null
    const ratio_30d = from_price && avg30 ? Math.round((from_price / avg30) * 1000) / 1000 : null
    const recommendation = getRecommendation(from_price, avg7, avg30)
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
      avg7,
      avg30,
      prev_from: c.prev_from || null,
      ratio_7d,
      ratio_30d,
      recommendation,
      purchase_price: c.purchase_price || null,
      profit_if_sold,
      scraped_at: c.scraped_at || null,
    }
  })

  // Sort: SELL_NOW first, then by shop_price desc
  result.sort((a, b) => {
    const ra = REC_ORDER[a.recommendation || ""] ?? 99
    const rb = REC_ORDER[b.recommendation || ""] ?? 99
    if (ra !== rb) return ra - rb
    return (b.shop_price || 0) - (a.shop_price || 0)
  })

  res.json({
    summary: {
      totalShopValue: Math.round(totalShopValue * 100) / 100,
      totalMarketValue: Math.round(totalMarketValue * 100) / 100,
      cardCount: cards.length,
      sellNowCount,
    },
    cards: result,
  })
})
