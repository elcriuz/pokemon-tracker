/**
 * One-time migration: Import portfolio.csv + historical price CSVs into SQLite.
 * Safe to run multiple times (uses INSERT OR IGNORE).
 */
import { getDb } from "./db"
import fs from "fs"
import path from "path"
import { fileURLToPath } from "url"

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)
const BASE = path.join(__dirname, "..")
const PORTFOLIO_CSV = path.join(BASE, "portfolio.csv")
const PRICES_DIR = path.join(BASE, "prices")

function parseCsvLine(line: string): string[] {
  const result: string[] = []
  let current = ""
  let inQuotes = false
  for (const ch of line) {
    if (ch === '"') { inQuotes = !inQuotes; continue }
    if (ch === "," && !inQuotes) { result.push(current.trim()); current = ""; continue }
    current += ch
  }
  result.push(current.trim())
  return result
}

function parseCsv(filepath: string): Record<string, string>[] {
  const content = fs.readFileSync(filepath, "utf-8")
  const lines = content.split("\n").filter((l) => l.trim())
  if (lines.length < 2) return []
  const headers = parseCsvLine(lines[0])
  return lines.slice(1).map((line) => {
    const values = parseCsvLine(line)
    const row: Record<string, string> = {}
    headers.forEach((h, i) => (row[h] = values[i] || ""))
    return row
  })
}

function migrate() {
  const db = getDb()
  console.log("Starting migration...")

  // Step 1: Import cards from portfolio.csv
  const cards = parseCsv(PORTFOLIO_CSV)
  const insertCard = db.prepare(
    "INSERT OR IGNORE INTO cards (url, name, grade, notes) VALUES (?, ?, ?, ?)"
  )
  let cardCount = 0
  for (const card of cards) {
    const result = insertCard.run(card.url, card.name || "", card.grade || "", card.notes || "")
    if (result.changes > 0) cardCount++
  }
  console.log(`Imported ${cardCount} cards (${cards.length} in CSV)`)

  // Step 2: Import price history from CSVs
  const csvFiles = fs
    .readdirSync(PRICES_DIR)
    .filter((f) => f.endsWith(".csv"))
    .sort()

  // Also import from latest.json
  const latestPath = path.join(PRICES_DIR, "latest.json")

  const cardLookup = db
    .prepare("SELECT id, url FROM cards")
    .all() as { id: number; url: string }[]
  const urlToId = new Map(cardLookup.map((c) => [c.url, c.id]))

  const insertPrice = db.prepare(`
    INSERT OR IGNORE INTO prices (card_id, scraped_at, value, trend, avg7, avg30, avg1, from_price, available_items, psa10_low, psa9_low, cgc10_low, bgs10_low, error)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `)

  let priceCount = 0

  // Import CSVs
  for (const csvFile of csvFiles) {
    const rows = parseCsv(path.join(PRICES_DIR, csvFile))
    for (const row of rows) {
      const cardId = urlToId.get(row.url)
      if (!cardId) continue
      const result = insertPrice.run(
        cardId,
        row.timestamp || "",
        parseFloat(row.value) || null,
        parseFloat(row.trend) || null,
        parseFloat(row.avg7) || null,
        parseFloat(row.avg30) || null,
        parseFloat(row.avg1) || null,
        parseFloat(row.from) || null,
        parseInt(row.available_items) || null,
        parseFloat(row.psa10_low) || null,
        parseFloat(row.psa9_low) || null,
        parseFloat(row.cgc10_low) || null,
        parseFloat(row.bgs10_low) || null,
        row.error || null
      )
      if (result.changes > 0) priceCount++
    }
  }

  // Import latest.json
  if (fs.existsSync(latestPath)) {
    const latest = JSON.parse(fs.readFileSync(latestPath, "utf-8")) as any[]
    for (const row of latest) {
      const cardId = urlToId.get(row.url)
      if (!cardId) continue
      insertPrice.run(
        cardId,
        row.timestamp || "",
        row.value ?? null,
        row.trend ?? null,
        row.avg7 ?? null,
        row.avg30 ?? null,
        row.avg1 ?? null,
        row.from ?? null,
        row.available_items ?? null,
        row.psa10_low ?? null,
        row.psa9_low ?? null,
        row.cgc10_low ?? null,
        row.bgs10_low ?? null,
        row.error ?? null
      )
      priceCount++
    }
  }

  console.log(`Imported ${priceCount} price records from ${csvFiles.length} CSVs + latest.json`)
  console.log("Migration complete!")
}

migrate()
