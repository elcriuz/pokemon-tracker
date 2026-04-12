import Database from "better-sqlite3"
import path from "path"
import fs from "fs"
import { fileURLToPath } from "url"

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)

const DB_PATH = path.join(__dirname, "..", "data", "tracker.db")

let db: Database.Database

export function getDb(): Database.Database {
  if (!db) {
    const dir = path.dirname(DB_PATH)
    if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true })

    db = new Database(DB_PATH)
    db.pragma("journal_mode = WAL")
    db.pragma("foreign_keys = ON")
    initSchema(db)
  }
  return db
}

function initSchema(db: Database.Database) {
  db.exec(`
    CREATE TABLE IF NOT EXISTS cards (
      id          INTEGER PRIMARY KEY AUTOINCREMENT,
      url         TEXT    NOT NULL UNIQUE,
      name        TEXT    NOT NULL DEFAULT '',
      grade       TEXT    NOT NULL DEFAULT '',
      notes       TEXT    NOT NULL DEFAULT '',
      created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
      updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS prices (
      id              INTEGER PRIMARY KEY AUTOINCREMENT,
      card_id         INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
      scraped_at      TEXT    NOT NULL,
      value           REAL,
      trend           REAL,
      avg7            REAL,
      avg30           REAL,
      avg1            REAL,
      from_price      REAL,
      available_items INTEGER,
      psa10_low       REAL,
      psa9_low        REAL,
      cgc10_low       REAL,
      bgs10_low       REAL,
      error           TEXT,
      UNIQUE(card_id, scraped_at)
    );

    CREATE INDEX IF NOT EXISTS idx_prices_card_id ON prices(card_id);
    CREATE INDEX IF NOT EXISTS idx_prices_scraped_at ON prices(scraped_at);
    CREATE INDEX IF NOT EXISTS idx_prices_card_scraped ON prices(card_id, scraped_at);

    CREATE TABLE IF NOT EXISTS settings (
      key   TEXT PRIMARY KEY,
      value TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS scrape_runs (
      id          INTEGER PRIMARY KEY AUTOINCREMENT,
      started_at  TEXT    NOT NULL,
      finished_at TEXT,
      status      TEXT    NOT NULL DEFAULT 'running',
      card_count  INTEGER,
      error       TEXT,
      duration_s  REAL
    );
  `)

  // Binders table
  db.exec(`
    CREATE TABLE IF NOT EXISTS binders (
      id         INTEGER PRIMARY KEY AUTOINCREMENT,
      name       TEXT NOT NULL,
      color      TEXT NOT NULL DEFAULT '#3b82f6',
      sort_order INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
  `)

  // Add columns if missing (migration for existing DBs)
  try { db.exec("ALTER TABLE cards ADD COLUMN image TEXT NOT NULL DEFAULT ''") } catch {}
  try { db.exec("ALTER TABLE cards ADD COLUMN purchase_price REAL") } catch {}
  try { db.exec("ALTER TABLE cards ADD COLUMN purchase_date TEXT") } catch {}
  try { db.exec("ALTER TABLE cards ADD COLUMN quantity INTEGER NOT NULL DEFAULT 1") } catch {}
  try { db.exec("ALTER TABLE cards ADD COLUMN binder_id INTEGER REFERENCES binders(id) ON DELETE SET NULL") } catch {}
  try { db.exec("ALTER TABLE cards ADD COLUMN set_name TEXT NOT NULL DEFAULT ''") } catch {}
  try { db.exec("ALTER TABLE scrape_runs ADD COLUMN engine TEXT NOT NULL DEFAULT 'patchright'") } catch {}

  // Default settings
  const insert = db.prepare("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)")
  insert.run("alert_threshold_pct", "5")
  insert.run("telegram_chat_id", "")
  insert.run("telegram_bot_token", "")
  insert.run("decodo_api_token", "")
}
