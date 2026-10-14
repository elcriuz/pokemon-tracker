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

function setSetting(key: string, value: string) {
  getDb().prepare(`INSERT INTO settings (key, value) VALUES (?, ?)
                   ON CONFLICT(key) DO UPDATE SET value = excluded.value`).run(key, value)
}

function getSetting(key: string): string | null {
  return (getDb().prepare("SELECT value FROM settings WHERE key = ?").get(key) as any)?.value ?? null
}

function logEvent(kind: string, text: string, opts: { signal_id?: number | null; watchlist_id?: number | null; data?: any } = {}) {
  getDb().prepare(`INSERT INTO sniper_events (ts, source, kind, watchlist_id, signal_id, text, data)
                   VALUES (${NOW}, 'helfer', ?, ?, ?, ?, ?)`)
    .run(kind, opts.watchlist_id ?? null, opts.signal_id ?? null, text.slice(0, 500),
         opts.data == null ? null : JSON.stringify(opts.data).slice(0, 4000))
}

const RESULT_TEXT: Record<string, string> = {
  carted: "🛒 Im Warenkorb",
  failed: "⚠ Nicht eingelegt — Alarm „selbst einlegen\"",
  blocked: "⛔ Verschickt nicht nach AT — verworfen, Verkäufer gesperrt",
  gone: "Angebot schon weg",
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
sniperRouter.get("/pending", (req, res) => {
  const db = getDb()
  db.prepare(`INSERT INTO settings (key, value) VALUES ('sniper_helper_seen', ${NOW})
              ON CONFLICT(key) DO UPDATE SET value = excluded.value`).run()
  if (typeof req.query.v === "string") setSetting("sniper_helper_version", req.query.v.slice(0, 20))
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
  const sig = db.prepare("SELECT id, watchlist_id, offer_key, check_detail FROM signals WHERE id = ?").get(req.params.id) as any
  if (!sig) return res.status(404).json({ error: "Signal nicht gefunden" })
  const dry = sig.check_detail === "dry"
  logEvent("ergebnis", `${dry ? "Testlauf: " : ""}${RESULT_TEXT[status]}${detail ? ` — ${detail}` : ""}`,
           { signal_id: sig.id, watchlist_id: sig.watchlist_id, data: { status, dry } })
  if (status === "failed" && /ausgeloggt/i.test(detail ?? "")) {
    setSetting("sniper_helper_login", JSON.stringify({ state: "out", at: new Date().toISOString(), detail }))
  }
  // Testlauf: Ergebnis festhalten, aber nie Alarm und nie Sperrliste.
  if (dry) {
    db.prepare(`UPDATE signals SET check_status = ?, check_detail = ?, notified_at = ${NOW},
                dismissed_at = ${NOW} WHERE id = ?`).run(`dry-${status}`, detail ?? null, sig.id)
    return res.json({ ok: true, dry: true })
  }

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

// Schritte, die der Helfer meldet. kind = login aktualisiert nur den Login-Stand
// und landet nur im Protokoll, wenn er sich geaendert hat.
sniperRouter.post("/event", (req, res) => {
  const { kind, text, data, signal_id } = req.body ?? {}
  if (typeof kind !== "string" || typeof text !== "string") {
    return res.status(400).json({ error: "kind und text noetig" })
  }
  if (kind === "login") {
    const prev = JSON.parse(getSetting("sniper_helper_login") || "{}")
    setSetting("sniper_helper_login", JSON.stringify({ state: data?.state, at: new Date().toISOString(), detail: text }))
    if (prev.state === data?.state) return res.json({ ok: true })
  }
  const wl = signal_id
    ? (getDb().prepare("SELECT watchlist_id FROM signals WHERE id = ?").get(signal_id) as any)?.watchlist_id
    : null
  logEvent(kind.slice(0, 20), text, { signal_id: signal_id ?? null, watchlist_id: wl, data })
  res.json({ ok: true })
})

// Alles fuer die Sniper-Seite in einem Aufruf.
sniperRouter.get("/status", (req, res) => {
  const db = getDb()
  const limit = Math.min(500, Number(req.query.limit) || 150)
  const seen = getSetting("sniper_helper_seen")
  const online = !!seen && Date.now() - new Date(seen).getTime() < 90_000
  const items = db.prepare(`SELECT id, name, language, product_url, image, kind FROM watchlist
                            WHERE active = 1 AND sniper = 1 ORDER BY name`).all() as any[]
  const lastRun = db.prepare(`SELECT * FROM sniper_events WHERE source = 'lauf' AND watchlist_id = ?
                              ORDER BY ts DESC, id DESC LIMIT 1`)
  const events = db.prepare(`SELECT e.*, w.name FROM sniper_events e LEFT JOIN watchlist w ON w.id = e.watchlist_id
                             ORDER BY e.ts DESC, e.id DESC LIMIT ?`).all(limit) as any[]
  const parse = (e: any) => e && ({ ...e, data: e.data ? JSON.parse(e.data) : null })
  res.json({
    helper: {
      seen, online,
      version: getSetting("sniper_helper_version"),
      login: JSON.parse(getSetting("sniper_helper_login") || "null"),
    },
    last_run: (db.prepare("SELECT MAX(ts) AS ts FROM sniper_events WHERE source = 'lauf'").get() as any)?.ts ?? null,
    items: items.map((i) => ({ ...i, last: parse(lastRun.get(i.id)) })),
    events: events.map(parse),
    signals: db.prepare(`SELECT s.id, s.created_at, s.suggested_price, s.offer_key, s.check_status, s.check_detail,
                                s.detail, w.name FROM signals s JOIN watchlist w ON w.id = s.watchlist_id
                         WHERE s.check_status IS NOT NULL ORDER BY s.created_at DESC LIMIT 20`).all(),
    blocked: db.prepare("SELECT * FROM blocked_sellers ORDER BY created_at DESC").all(),
  })
})
