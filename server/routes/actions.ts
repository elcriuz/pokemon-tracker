import { Router } from "express"
import { getDb } from "../db"

export const actionsRouter = Router()

/**
 * Alle offenen Empfehlungen an einem Ort — Verkaufs- und Kaufseite zusammen.
 *
 * Sortiert wird nach dem Betrag, um den es geht, nicht nach Signaltyp: +106 %
 * auf eine 40-€-Karte wiegt schwerer als +300 % auf eine für 2 €. Ohne dieses
 * Mass liest man die Liste von oben nach unten und arbeitet an den falschen
 * Karten.
 */
const DIRECTION: Record<string, "up" | "down" | "buy"> = {
  raise: "up", underpriced: "up", sell_now: "up",
  lower: "down", overpriced: "down", undercut: "down",
  buy: "buy",
}

const LABEL: Record<string, string> = {
  raise: "Anheben", underpriced: "Zu günstig", sell_now: "Jetzt raus",
  lower: "Senken", overpriced: "Zu teuer", undercut: "Unterboten", buy: "Kaufen",
}

actionsRouter.get("/", (req, res) => {
  const db = getDb()
  const { kind, game } = req.query as Record<string, string | undefined>

  // Verkaufsseite: offene Signale auf eigenen Angeboten
  const selling = db.prepare(`
    SELECT s.id AS signal_id, s.kind, s.suggested_price, s.detail, s.created_at,
           l.id AS listing_id, l.product_name, l.expansion, l.game, l.condition,
           l.language, l.price, l.quantity, l.product_url, l.first_seen,
           snap.rank, snap.competitors_same, snap.best_same, snap.median_same,
           q.target_price AS queued_price
    FROM signals s
    JOIN listings l ON l.id = s.listing_id
    LEFT JOIN listing_snapshots snap
      ON snap.listing_id = l.id
     AND snap.captured_at = (SELECT MAX(captured_at) FROM listing_snapshots
                             WHERE listing_id = l.id)
    LEFT JOIN reprice_queue q ON q.listing_id = l.id AND q.done_at IS NULL
    WHERE s.dismissed_at IS NULL AND s.applied_at IS NULL AND l.active = 1
  `).all() as any[]

  // Kaufseite: Wunschliste
  const buying = db.prepare(`
    SELECT s.id AS signal_id, s.kind, s.suggested_price, s.detail, s.created_at,
           w.id AS watchlist_id, w.name AS product_name, w.game, w.condition,
           w.language, w.target_price, w.product_url,
           ws.best_price, ws.median_price, ws.offers_count
    FROM signals s
    JOIN watchlist w ON w.id = s.watchlist_id
    LEFT JOIN watchlist_snapshots ws
      ON ws.watchlist_id = w.id
     AND ws.captured_at = (SELECT MAX(captured_at) FROM watchlist_snapshots
                           WHERE watchlist_id = w.id)
    WHERE s.dismissed_at IS NULL AND w.active = 1
  `).all() as any[]

  const items = [
    ...selling.map((r) => {
      const qty = r.quantity ?? 1
      const delta = r.suggested_price != null ? (r.suggested_price - r.price) : 0
      return {
        type: "sell" as const,
        signal_id: r.signal_id,
        listing_id: r.listing_id,
        kind: r.kind,
        label: LABEL[r.kind] ?? r.kind,
        direction: DIRECTION[r.kind] ?? "up",
        name: r.product_name,
        subtitle: `${r.game} · ${r.expansion} · ${r.condition}/${r.language}`,
        url: r.product_url,
        current: r.price,
        suggested: r.suggested_price,
        quantity: qty,
        // Um wie viel Geld es geht. Beim Senken ist es kein Verlust, sondern
        // der Abstand, der den Verkauf bisher verhindert.
        stake: Math.abs(delta) * qty,
        detail: r.detail,
        rank: r.rank,
        comparables: r.competitors_same,
        queued: r.queued_price != null,
        queued_price: r.queued_price,
        since: r.created_at,
      }
    }),
    ...buying.map((r) => ({
      type: "buy" as const,
      signal_id: r.signal_id,
      watchlist_id: r.watchlist_id,
      kind: "buy",
      label: LABEL.buy,
      direction: "buy" as const,
      name: r.product_name,
      subtitle: `${r.game} · ${r.condition}/${r.language}`,
      url: r.product_url,
      current: r.best_price,
      suggested: r.target_price ?? r.median_price,
      quantity: 1,
      // Ersparnis gegenueber dem Mittelfeld — das ist der Vorteil des Zugriffs.
      stake: r.median_price && r.best_price ? Math.max(0, r.median_price - r.best_price) : 0,
      detail: r.detail,
      comparables: r.offers_count,
      queued: false,
      since: r.created_at,
    })),
  ]

  const filtered = items
    .filter((i) => !kind || i.kind === kind)
    .filter((i) => !game || (i.subtitle ?? "").startsWith(game))
    .sort((a, b) => b.stake - a.stake)

  const open = db.prepare(
    "SELECT COUNT(*) AS n FROM reprice_queue WHERE done_at IS NULL"
  ).get() as any

  res.json({
    items: filtered,
    summary: {
      count: items.length,
      sell: items.filter((i) => i.type === "sell").length,
      buy: items.filter((i) => i.type === "buy").length,
      stake_up: items.filter((i) => i.direction === "up").reduce((a, i) => a + i.stake, 0),
      stake_down: items.filter((i) => i.direction === "down").reduce((a, i) => a + i.stake, 0),
      stake_buy: items.filter((i) => i.direction === "buy").reduce((a, i) => a + i.stake, 0),
      queued: open.n,
      kinds: [...new Set(items.map((i) => i.kind))],
      games: [...new Set(items.map((i) => (i.subtitle ?? "").split(" · ")[0]))],
    },
  })
})
