import { Router } from "express"
import { spawn } from "child_process"
import path from "path"
import { fileURLToPath } from "url"
import { getDb } from "../db"

/**
 * Schnittstelle fuer den Sniper-Helfer (Chrome-Erweiterung in Christophs
 * eingeloggtem Browser, ~/Desktop/PackGehabt/Sniper-Helper/).
 *
 * Ohne Login sieht der Tracker nicht, ob ein Angebot in den Warenkorb darf —
 * Lockvogel-Angebote verschicken oft in kein einziges Land. Darum legt
 * watchlist.py einen Schnapper als check_status = 'pending' ab, der Helfer holt
 * ihn hier ab, prueft und legt ihn in den Warenkorb, und erst sein Ergebnis
 * loest den Alarm aus (watchlist.py --alert).
 */
export const sniperRouter = Router()

const BASE = path.join(path.dirname(fileURLToPath(import.meta.url)), "../..")
const LANGUAGE_IDS: Record<string, number> = {
  en: 1, fr: 2, de: 3, es: 4, it: 5, zh: 6, ja: 7, pt: 8, ru: 9, ko: 10,
}
const CONDITION_IDS: Record<string, number> = { MT: 1, NM: 2, EX: 3, GD: 4, LP: 5, PL: 6, PO: 7 }

// Dasselbe Format wie datetime.now().isoformat() in Python — sonst vergleicht
// watchlist.py die Zeitstempel als Strings falsch.
const NOW = "strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')"

function offerUrl(productUrl: string, condition: string, language: string) {
  const p: string[] = []
  if (LANGUAGE_IDS[language]) p.push(`language=${LANGUAGE_IDS[language]}`)
  if (CONDITION_IDS[condition]) p.push(`minCondition=${CONDITION_IDS[condition]}`)
  return p.length ? `${productUrl}?${p.join("&")}` : productUrl
}

function alert(id: number) {
  try {
    const proc = spawn("python3", ["watchlist.py", "--alert", String(id)], { cwd: BASE, stdio: "ignore" })
    proc.on("error", (e) => console.error("watchlist.py --alert:", e))
  } catch (e) {
    console.error("watchlist.py --alert:", e)
  }
}

// Der Helfer fragt alle 30 s. Jede Anfrage zaehlt als Lebenszeichen — daran
// erkennt watchlist.py, ob es auf den Check warten soll.
sniperRouter.get("/pending", (_req, res) => {
  const db = getDb()
  db.prepare(`INSERT INTO settings (key, value) VALUES ('sniper_helper_seen', ${NOW})
              ON CONFLICT(key) DO UPDATE SET value = excluded.value`).run()
  const rows = db.prepare(`
    SELECT s.id, s.article_id, s.offer_key, s.suggested_price, s.check_detail, w.name, w.product_url,
           w.condition, w.language
    FROM signals s JOIN watchlist w ON w.id = s.watchlist_id
    WHERE s.check_status = 'pending' AND s.notified_at IS NULL AND s.dismissed_at IS NULL
      AND s.article_id IS NOT NULL
      AND s.created_at > strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime', '-15 minutes')
    ORDER BY s.created_at`).all() as any[]
  const claim = db.prepare(`UPDATE signals SET check_status = 'claimed', claimed_at = ${NOW} WHERE id = ?`)
  for (const r of rows) claim.run(r.id)
  res.json({
    items: rows.map((r) => ({
      id: r.id,
      article_id: r.article_id,
      seller: String(r.offer_key ?? "").split("|")[0],
      price: r.suggested_price,
      name: r.name,
      url: offerUrl(r.product_url, r.condition, r.language),
      // Testlauf: pruefen, aber nicht einlegen (check_detail = 'dry')
      dry: r.check_detail === "dry",
    })),
  })
})

// carted  = liegt im Warenkorb → Alarm „jetzt bezahlen"
// failed  = darf rein, Einlegen ging aber nicht → Alarm „selbst einlegen"
// blocked = verschickt nicht nach AT → kein Alarm, Verkaeufer kuenftig ignorieren
// gone    = Angebot schon weg → kein Alarm
sniperRouter.post("/:id/result", (req, res) => {
  const db = getDb()
  const { status, detail } = req.body ?? {}
  if (!["carted", "failed", "blocked", "gone"].includes(status)) {
    return res.status(400).json({ error: "status muss carted, failed, blocked oder gone sein" })
  }
  const sig = db.prepare("SELECT id, offer_key FROM signals WHERE id = ?").get(req.params.id) as any
  if (!sig) return res.status(404).json({ error: "Signal nicht gefunden" })

  db.prepare("UPDATE signals SET check_status = ?, check_detail = ? WHERE id = ?")
    .run(status, detail ?? null, sig.id)
  if (status === "blocked" || status === "gone") {
    db.prepare(`UPDATE signals SET dismissed_at = ${NOW} WHERE id = ?`).run(sig.id)
    const seller = String(sig.offer_key ?? "").split("|")[0]
    if (status === "blocked" && seller) {
      db.prepare("INSERT OR IGNORE INTO blocked_sellers (seller, reason) VALUES (?, ?)")
        .run(seller, detail ?? "verschickt nicht nach AT")
    }
  } else {
    alert(sig.id)
  }
  res.json({ ok: true })
})

sniperRouter.get("/blocked", (_req, res) => {
  res.json({ items: getDb().prepare("SELECT * FROM blocked_sellers ORDER BY created_at DESC").all() })
})
