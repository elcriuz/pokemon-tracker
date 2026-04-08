import { Router } from "express"
import { getDb } from "../db"

export const settingsRouter = Router()

// GET /api/settings
settingsRouter.get("/", (_req, res) => {
  const db = getDb()
  const rows = db.prepare("SELECT key, value FROM settings").all() as any[]
  const obj: Record<string, string> = {}
  for (const r of rows) obj[r.key] = r.value
  res.json(obj)
})

// PUT /api/settings
settingsRouter.put("/", (req, res) => {
  const db = getDb()
  const upsert = db.prepare("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value")
  for (const [key, value] of Object.entries(req.body)) {
    upsert.run(key, String(value))
  }
  // Return updated settings
  const rows = db.prepare("SELECT key, value FROM settings").all() as any[]
  const obj: Record<string, string> = {}
  for (const r of rows) obj[r.key] = r.value
  res.json(obj)
})
