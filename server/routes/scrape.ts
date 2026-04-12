import { Router } from "express"
import { spawn } from "child_process"
import fs from "fs"
import path from "path"
import { fileURLToPath } from "url"
import { getDb } from "../db"

export const scrapeRouter = Router()

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)
const BASE = path.join(__dirname, "../..")
type ScrapeEngine = "patchright" | "decodo"
let isRunning = false
let currentRunId: number | null = null
let currentProcess: ReturnType<typeof spawn> | null = null
let currentEngine: ScrapeEngine | null = null
const logBuffer: string[] = []
const LOG_BUFFER_MAX = 200

function regeneratePortfolioCsv() {
  const db = getDb()
  const cards = db.prepare("SELECT url, name, grade, notes FROM cards").all() as any[]
  const header = "url,name,grade,notes\n"
  const rows = cards.map((c) => `${c.url},${c.name},${c.grade},${c.notes}`).join("\n")
  fs.writeFileSync(path.join(BASE, "portfolio.csv"), header + rows + "\n")
}

function importResults() {
  const db = getDb()
  const latestPath = path.join(BASE, "prices", "latest.json")
  if (!fs.existsSync(latestPath)) return

  const results = JSON.parse(fs.readFileSync(latestPath, "utf-8")) as any[]
  const cardLookup = db.prepare("SELECT id, url FROM cards").all() as any[]
  const urlToId = new Map(cardLookup.map((c: any) => [c.url, c.id]))

  const insert = db.prepare(`
    INSERT OR IGNORE INTO prices (card_id, scraped_at, value, trend, avg7, avg30, avg1, from_price, available_items, psa10_low, psa9_low, cgc10_low, bgs10_low, error)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `)

  const updateImage = db.prepare("UPDATE cards SET image = ? WHERE id = ? AND (image = '' OR image IS NULL)")
  const updateName = db.prepare("UPDATE cards SET name = ? WHERE id = ? AND (name = '' OR name IS NULL)")

  for (const r of results) {
    const cardId = urlToId.get(r.url)
    if (!cardId) continue
    insert.run(
      cardId, r.timestamp ?? "", r.value ?? null, r.trend ?? null,
      r.avg7 ?? null, r.avg30 ?? null, r.avg1 ?? null, r.from ?? null,
      r.available_items ?? null, r.psa10_low ?? null, r.psa9_low ?? null,
      r.cgc10_low ?? null, r.bgs10_low ?? null, r.error ?? null
    )
    // Save image + name to card if scraped and missing
    if (r.image) updateImage.run(r.image, cardId)
    if (r.name && !r.name.toLowerCase().includes("just a moment") && !r.name.toLowerCase().includes("cloudflare")) updateName.run(r.name, cardId)
    if (r.set_name) db.prepare("UPDATE cards SET set_name = ? WHERE id = ? AND (set_name = '' OR set_name IS NULL)").run(r.set_name, cardId)
  }
}

function captureStdout(proc: ReturnType<typeof spawn>) {
  proc.stdout?.on("data", (chunk) => {
    const lines = chunk.toString().split("\n").filter(Boolean)
    for (const line of lines) {
      logBuffer.push(line)
      if (logBuffer.length > LOG_BUFFER_MAX) logBuffer.shift()
    }
  })
}

// POST /api/scrape - trigger full scrape
scrapeRouter.post("/", (req, res) => {
  if (isRunning) return res.status(409).json({ error: "Scrape already running", runId: currentRunId })

  const engine: ScrapeEngine = req.body?.engine === "decodo" ? "decodo" : "patchright"
  const script = engine === "decodo" ? "scrape_decodo.py" : "scrape.py"
  const env = engine === "decodo" ? { ...process.env } : { ...process.env, DISPLAY: ":99" }

  const db = getDb()
  regeneratePortfolioCsv()
  // Clear resume state so scraper doesn't skip cards
  try { fs.unlinkSync(path.join(BASE, "data", "scrape_resume.json")) } catch {}

  const run = db.prepare(
    "INSERT INTO scrape_runs (started_at, status, card_count, engine) VALUES (datetime('now'), 'running', (SELECT COUNT(*) FROM cards), ?)"
  ).run(engine)
  currentRunId = run.lastInsertRowid as number
  currentEngine = engine
  isRunning = true
  logBuffer.length = 0

  const proc = spawn("python3", [script], { cwd: BASE, stdio: ["ignore", "pipe", "pipe"], env })
  currentProcess = proc

  let stderr = ""
  proc.stderr.on("data", (d) => (stderr += d.toString()))
  captureStdout(proc)

  const liveImport = setInterval(() => {
    try { importResults(); db.pragma("wal_checkpoint(TRUNCATE)") } catch {}
  }, 10_000)

  proc.on("close", (code) => {
    clearInterval(liveImport)
    const wasStopped = code === null || code === 137 || code === 15
    const status = wasStopped ? "stopped" : code === 0 ? "completed" : "failed"
    try { importResults() } catch (e) { console.error("Import error:", e) }
    db.pragma("wal_checkpoint(TRUNCATE)")
    db.prepare(
      "UPDATE scrape_runs SET finished_at = datetime('now'), status = ?, duration_s = (julianday(datetime('now')) - julianday(started_at)) * 86400, error = ? WHERE id = ?"
    ).run(status, wasStopped ? "Manually stopped" : code !== 0 ? stderr.slice(0, 500) : null, currentRunId)
    isRunning = false
    currentProcess = null
    currentRunId = null
    currentEngine = null
  })

  res.json({ runId: currentRunId, status: "running", engine })
})

// POST /api/scrape/cards - scrape specific card IDs
scrapeRouter.post("/cards", (req, res) => {
  if (isRunning) return res.status(409).json({ error: "Scrape already running", runId: currentRunId })

  const { cardIds, engine: reqEngine } = req.body as { cardIds: number[]; engine?: string }
  if (!cardIds?.length) return res.status(400).json({ error: "cardIds required" })

  const engine: ScrapeEngine = reqEngine === "decodo" ? "decodo" : "patchright"
  const script = engine === "decodo" ? "scrape_decodo.py" : "scrape.py"
  const env = engine === "decodo" ? { ...process.env } : { ...process.env, DISPLAY: ":99" }

  const db = getDb()
  const cards = db.prepare(`SELECT url, name, grade, notes FROM cards WHERE id IN (${cardIds.map(() => "?").join(",")})`)
    .all(...cardIds) as any[]

  if (!cards.length) return res.status(404).json({ error: "No cards found" })

  // Clear resume state
  try { fs.unlinkSync(path.join(BASE, "data", "scrape_resume.json")) } catch {}

  // Write a temp CSV with just the selected cards
  const header = "url,name,grade,notes\n"
  const rows = cards.map((c) => `${c.url},${c.name},${c.grade},${c.notes}`).join("\n")
  fs.writeFileSync(path.join(BASE, "portfolio.csv"), header + rows + "\n")

  const run = db.prepare(
    "INSERT INTO scrape_runs (started_at, status, card_count, engine) VALUES (datetime('now'), 'running', ?, ?)"
  ).run(cards.length, engine)
  currentRunId = run.lastInsertRowid as number
  currentEngine = engine
  isRunning = true
  logBuffer.length = 0

  const proc = spawn("python3", [script], { cwd: BASE, stdio: ["ignore", "pipe", "pipe"], env })
  currentProcess = proc

  let stderr = ""
  proc.stderr.on("data", (d) => (stderr += d.toString()))
  captureStdout(proc)

  const liveImport = setInterval(() => {
    try { importResults(); db.pragma("wal_checkpoint(TRUNCATE)") } catch {}
  }, 10_000)

  proc.on("close", (code) => {
    clearInterval(liveImport)
    const wasStopped = code === null || code === 137 || code === 15
    const status = wasStopped ? "stopped" : code === 0 ? "completed" : "failed"
    try { importResults() } catch (e) { console.error("Import error:", e) }
    db.pragma("wal_checkpoint(TRUNCATE)")
    db.prepare(
      "UPDATE scrape_runs SET finished_at = datetime('now'), status = ?, duration_s = (julianday(datetime('now')) - julianday(started_at)) * 86400, error = ? WHERE id = ?"
    ).run(status, wasStopped ? "Manually stopped" : code !== 0 ? stderr.slice(0, 500) : null, currentRunId)
    regeneratePortfolioCsv()
    isRunning = false
    currentRunId = null
    currentEngine = null
  })

  res.json({ runId: currentRunId, status: "running", cardCount: cards.length, engine })
})

// GET /api/scrape/status
// POST /api/scrape/stop - stop running scrape
scrapeRouter.post("/stop", (_req, res) => {
  if (!isRunning || !currentProcess) {
    return res.status(400).json({ error: "No scrape running" })
  }
  currentProcess.kill("SIGTERM")
  // Clean up Chrome crash state so next start doesn't show Google/restore page
  setTimeout(() => {
    try {
      const profileDir = path.join(BASE, "data", "chrome-profile")
      const defaultDir = path.join(profileDir, "Default")
      const files = ["Preferences", "Secure Preferences"]
      for (const f of files) {
        const fp = path.join(defaultDir, f)
        if (fs.existsSync(fp)) {
          const content = fs.readFileSync(fp, "utf-8")
          // Remove exit_type: Crashed so Chrome doesn't show restore bubble
          const fixed = content.replace(/"exit_type"\s*:\s*"Crashed"/g, '"exit_type":"Normal"')
          fs.writeFileSync(fp, fixed)
        }
      }
    } catch {}
  }, 2000)
  res.json({ ok: true, message: "Scrape stopping..." })
})

scrapeRouter.get("/status", (_req, res) => {
  const db = getDb()
  const latest = db.prepare("SELECT * FROM scrape_runs ORDER BY id DESC LIMIT 1").get() as any

  // Progress: count results in latest.json while scraping
  let progress = 0
  if (isRunning) {
    try {
      const latestPath = path.join(BASE, "prices", "latest.json")
      const data = JSON.parse(fs.readFileSync(latestPath, "utf-8"))
      progress = Array.isArray(data) ? data.length : 0
    } catch {}
  }

  res.json({ isRunning, currentRunId, latest, progress, total: latest?.card_count || 0, engine: currentEngine })
})

// GET /api/scrape/logs - live log lines from current scrape
scrapeRouter.get("/logs", (_req, res) => {
  res.json({ engine: currentEngine, lines: [...logBuffer] })
})

// GET /api/scrape/history
scrapeRouter.get("/history", (_req, res) => {
  const db = getDb()
  const runs = db.prepare("SELECT * FROM scrape_runs ORDER BY started_at DESC LIMIT 20").all()
  res.json(runs)
})
