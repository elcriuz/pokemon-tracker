import { Router } from "express"
import { getDb } from "../db"

export const scansRouter = Router()

// GET /api/scans — scan activity log
scansRouter.get("/", (req, res) => {
  const db = getDb()
  const limit = Math.min(parseInt(req.query.limit as string) || 100, 500)

  // Ensure table exists (created by cardcheck_bot.py, but just in case)
  db.exec(`CREATE TABLE IF NOT EXISTS scan_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scanned_at TEXT DEFAULT (datetime('now')),
    user_id INTEGER, user_name TEXT,
    card_name TEXT, set_name TEXT, number TEXT,
    language TEXT, grade TEXT,
    market_eur REAL, cm_url TEXT,
    duration_sec INTEGER, via TEXT
  )`)

  const scans = db.prepare(`
    SELECT id, scanned_at, user_id, user_name, card_name, set_name, number,
           language, grade, market_eur, cm_url, duration_sec, via
    FROM scan_log ORDER BY id DESC LIMIT ?
  `).all(limit)

  // Stats
  const stats = db.prepare(`
    SELECT
      COUNT(*) as total,
      COUNT(DISTINCT user_id) as users,
      ROUND(AVG(duration_sec), 1) as avg_duration,
      SUM(CASE WHEN via = 'quick_cm' THEN 1 ELSE 0 END) as quick_cm,
      SUM(CASE WHEN via = 'ximilar' THEN 1 ELSE 0 END) as ximilar,
      SUM(CASE WHEN via = 'tcg_api' THEN 1 ELSE 0 END) as tcg_api
    FROM scan_log
  `).get()

  res.json({ scans, stats })
})

// GET /api/scans/users — allowed users
scansRouter.get("/users", (req, res) => {
  const db = getDb()
  db.exec(`CREATE TABLE IF NOT EXISTS allowed_users (telegram_id INTEGER PRIMARY KEY, name TEXT)`)
  const users = db.prepare("SELECT telegram_id, name FROM allowed_users").all()
  res.json(users)
})
