import { Router } from "express"
import { getDb } from "../db"

export const bindersRouter = Router()

// GET /api/binders
bindersRouter.get("/", (_req, res) => {
  const db = getDb()
  const binders = db.prepare(`
    SELECT b.*, COUNT(c.id) as card_count
    FROM binders b
    LEFT JOIN cards c ON c.binder_id = b.id
    GROUP BY b.id
    ORDER BY b.sort_order, b.name
  `).all()
  res.json(binders)
})

// POST /api/binders
bindersRouter.post("/", (req, res) => {
  const db = getDb()
  const { name, color } = req.body
  if (!name) return res.status(400).json({ error: "Name is required" })
  const result = db.prepare("INSERT INTO binders (name, color) VALUES (?, ?)").run(name, color || "#3b82f6")
  const binder = db.prepare("SELECT * FROM binders WHERE id = ?").get(result.lastInsertRowid)
  res.status(201).json(binder)
})

// PUT /api/binders/:id
bindersRouter.put("/:id", (req, res) => {
  const db = getDb()
  const existing = db.prepare("SELECT * FROM binders WHERE id = ?").get(req.params.id) as any
  if (!existing) return res.status(404).json({ error: "Binder not found" })
  const { name, color, sort_order } = req.body
  db.prepare("UPDATE binders SET name = ?, color = ?, sort_order = ? WHERE id = ?").run(
    name ?? existing.name, color ?? existing.color, sort_order ?? existing.sort_order, req.params.id
  )
  const binder = db.prepare("SELECT * FROM binders WHERE id = ?").get(req.params.id)
  res.json(binder)
})

// DELETE /api/binders/:id
bindersRouter.delete("/:id", (req, res) => {
  const db = getDb()
  const result = db.prepare("DELETE FROM binders WHERE id = ?").run(req.params.id)
  if (result.changes === 0) return res.status(404).json({ error: "Binder not found" })
  res.json({ ok: true })
})
