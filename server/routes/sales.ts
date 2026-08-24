import { Router } from "express"
import { getDb } from "../db"

export const salesRouter = Router()

/** Cardmarket behaelt eine Provision auf den Artikelwert ein. */
function commissionPct(db: any): number {
  const row = db.prepare("SELECT value FROM settings WHERE key = 'cm_commission_pct'").get() as any
  const v = Number(row?.value)
  return Number.isFinite(v) && v > 0 ? v : 5
}

salesRouter.get("/", (req, res) => {
  const db = getDb()
  const { game, months } = req.query as Record<string, string | undefined>
  const pct = commissionPct(db)

  const where: string[] = ["1=1"]
  const params: any[] = []
  if (game) { where.push("o.game = ?"); params.push(game) }

  const orders = db.prepare(`
    SELECT o.*,
           COALESCE(o.arrived_at, o.sent_at, o.paid_at) AS sold_at,
           (SELECT COUNT(*) FROM order_items WHERE order_id = o.id) AS positions,
           (SELECT SUM(amount) FROM order_items WHERE order_id = o.id) AS cards
    FROM orders o
    WHERE ${where.join(" AND ")}
    ORDER BY COALESCE(o.arrived_at, o.sent_at, o.paid_at) DESC
  `).all(...params) as any[]

  const withFees = orders.map((o) => {
    const commission = o.item_value ? (o.item_value * pct) / 100 : 0
    return { ...o, commission, net: (o.item_value ?? 0) - commission }
  })

  // Monatsreihe fuer den Verlauf. Bestellungen ohne jedes Datum landen in "unbekannt"
  // statt still zu verschwinden.
  const byMonth = new Map<string, { revenue: number; net: number; orders: number; cards: number }>()
  for (const o of withFees) {
    const key = o.sold_at ? String(o.sold_at).slice(0, 7) : "unbekannt"
    const e = byMonth.get(key) ?? { revenue: 0, net: 0, orders: 0, cards: 0 }
    e.revenue += o.item_value ?? 0
    e.net += o.net
    e.orders += 1
    e.cards += o.cards ?? 0
    byMonth.set(key, e)
  }

  const revenue = withFees.reduce((a, o) => a + (o.item_value ?? 0), 0)
  const commission = withFees.reduce((a, o) => a + o.commission, 0)

  // Einkaufspreise kennen wir nur fuer Karten, die im Portfolio gefuehrt sind —
  // das meiste Verkaufte sind Doppelte, die dort nie standen.
  const cost = db.prepare(`
    SELECT COUNT(*) AS n, SUM(c.purchase_price * oi.amount) AS total
    FROM order_items oi JOIN cards c ON c.id = oi.card_id
    WHERE c.purchase_price IS NOT NULL
  `).get() as any
  const totalItems = (db.prepare("SELECT COUNT(*) AS n FROM order_items").get() as any).n

  res.json({
    orders: months ? withFees.slice(0, 200) : withFees,
    by_month: [...byMonth.entries()]
      .map(([month, v]) => ({ month, ...v }))
      .sort((a, b) => a.month.localeCompare(b.month)),
    summary: {
      orders: withFees.length,
      revenue,
      shipping: withFees.reduce((a, o) => a + (o.shipping ?? 0), 0),
      commission,
      net: revenue - commission,
      commission_pct: pct,
      cards: withFees.reduce((a, o) => a + (o.cards ?? 0), 0),
      games: [...new Set(orders.map((o) => o.game).filter(Boolean))],
      known_cost_items: cost?.n ?? 0,
      known_cost_total: cost?.total ?? 0,
      total_items: totalItems,
    },
  })
})

salesRouter.get("/:id/items", (req, res) => {
  const db = getDb()
  res.json({
    items: db.prepare(`
      SELECT oi.*, c.purchase_price
      FROM order_items oi LEFT JOIN cards c ON c.id = oi.card_id
      WHERE oi.order_id = ? ORDER BY oi.price DESC
    `).all(req.params.id),
  })
})
