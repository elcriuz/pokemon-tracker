import { Router } from "express"
import { getDb } from "../db"

export const brightdataRouter = Router()

// Guthaben und Verbrauch der Bright-Data-Zone. Bright Data laedt nicht automatisch
// nach — ein leeres Konto merkt man sonst erst daran, dass keine Preise mehr kommen.
// Die Zahlen aendern sich nur mit jedem Lauf, zehn Minuten Cache reichen.
let cache: { at: number; data: any } | null = null
const TTL_MS = 10 * 60 * 1000

async function bd(path: string, key: string) {
  const r = await fetch(`https://api.brightdata.com/${path}`, {
    headers: { Authorization: `Bearer ${key}` },
    signal: AbortSignal.timeout(15_000),
  })
  if (!r.ok) throw new Error(`${path.split("?")[0]}: HTTP ${r.status}`)
  return r.json()
}

// GET /api/brightdata/usage
brightdataRouter.get("/usage", async (_req, res) => {
  if (cache && Date.now() - cache.at < TTL_MS) return res.json(cache.data)

  const db = getDb()
  const rows = db.prepare(
    "SELECT key, value FROM settings WHERE key IN ('brightdata_api_key', 'brightdata_zone')"
  ).all() as any[]
  const s = Object.fromEntries(rows.map((r) => [r.key, r.value]))
  const key = s.brightdata_api_key
  const zone = s.brightdata_zone || "cardmarket"
  if (!key) return res.json({ error: "Kein Bright-Data-Key hinterlegt" })

  try {
    const [bal, cost] = await Promise.all([
      bd("customer/balance", key),
      bd(`zone/cost?zone=${encodeURIComponent(zone)}`, key),
    ])
    // zone/cost antwortet mit {<customer_id>: {back_d0: heute, back_d1: gestern, …,
    // back_m0: dieser Monat}}; reqs_unblocker sind die abgerechneten Seitenabrufe.
    const c = (Object.values(cost)[0] as any) ?? {}
    const slot = (k: string) => ({ cost: c[k]?.cost ?? 0, reqs: c[k]?.reqs_unblocker ?? 0 })
    const today = slot("back_d0")
    const month = slot("back_m0")
    // Reichweite aus den zwei letzten vollen Tagen — heute ist noch nicht vorbei.
    const perDay = (slot("back_d1").cost + slot("back_d2").cost) / 2
    const net = (bal.balance ?? 0) - (bal.pending_costs ?? 0)
    const data = {
      zone,
      balance: bal.balance ?? 0,
      pending: bal.pending_costs ?? 0,
      net,
      today,
      month,
      per_request: month.reqs ? month.cost / month.reqs : null,
      per_day: perDay,
      days_left: perDay > 0 ? Math.floor(net / perDay) : null,
      fetched_at: new Date().toISOString(),
    }
    cache = { at: Date.now(), data }
    res.json(data)
  } catch (e: any) {
    res.json({ error: String(e?.message ?? e) })
  }
})
