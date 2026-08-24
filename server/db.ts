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
      stale_grade     INTEGER NOT NULL DEFAULT 0,
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

  // Cardmarket-Modul: eigene Angebote + Wettbewerbsposition
  //
  // listings sind bewusst NICHT an cards gekoppelt (card_id ist nullable):
  // Auf Cardmarket landen auch Doppelte und getradete Karten, die nie im
  // Portfolio waren. Verknuepft wird ueber product_url, wenn es passt.
  db.exec(`
    CREATE TABLE IF NOT EXISTS listings (
      id            INTEGER PRIMARY KEY AUTOINCREMENT,
      card_id       INTEGER REFERENCES cards(id) ON DELETE SET NULL,
      cm_article_id TEXT    UNIQUE,
      game          TEXT    NOT NULL DEFAULT 'Pokemon',
      product_url   TEXT    NOT NULL,
      product_name  TEXT    NOT NULL DEFAULT '',
      expansion     TEXT    NOT NULL DEFAULT '',
      kind          TEXT    NOT NULL DEFAULT 'single',
      condition     TEXT    NOT NULL DEFAULT '',
      language      TEXT    NOT NULL DEFAULT '',
      is_foil       INTEGER NOT NULL DEFAULT 0,
      is_signed     INTEGER NOT NULL DEFAULT 0,
      is_playset    INTEGER NOT NULL DEFAULT 0,
      price         REAL,
      quantity      INTEGER NOT NULL DEFAULT 1,
      comment       TEXT    NOT NULL DEFAULT '',
      first_seen    TEXT    NOT NULL DEFAULT (datetime('now')),
      last_seen     TEXT    NOT NULL DEFAULT (datetime('now')),
      active        INTEGER NOT NULL DEFAULT 1,
      gone_at       TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_listings_active ON listings(active);
    CREATE INDEX IF NOT EXISTS idx_listings_game ON listings(game);
    CREATE INDEX IF NOT EXISTS idx_listings_card ON listings(card_id);
    CREATE INDEX IF NOT EXISTS idx_listings_url ON listings(product_url);

    -- Ein Snapshot je Angebot und Lauf: eigener Preis plus Marktumfeld.
    -- best_price/rank beziehen sich immer auf VERGLEICHBARE Angebote
    -- (gleicher Zustand, gleiche Sprache) - sonst vergleicht man NM-DE
    -- gegen PO-EN und das Signal ist wertlos.
    CREATE TABLE IF NOT EXISTS listing_snapshots (
      id                INTEGER PRIMARY KEY AUTOINCREMENT,
      listing_id        INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
      captured_at       TEXT    NOT NULL,
      my_price          REAL,
      rank              INTEGER,
      competitors_below INTEGER,
      competitors_total INTEGER,
      best_price        REAL,
      rank_capped       INTEGER NOT NULL DEFAULT 0,
      best_same         REAL,
      median_same       REAL,
      competitors_same  INTEGER,
      market_trend      REAL,
      market_avg7       REAL,
      market_avg30      REAL,
      market_avg1       REAL,
      market_available  INTEGER,
      UNIQUE(listing_id, captured_at)
    );

    CREATE INDEX IF NOT EXISTS idx_lsnap_listing ON listing_snapshots(listing_id);
    CREATE INDEX IF NOT EXISTS idx_lsnap_captured ON listing_snapshots(captured_at);
  `)

  // Eigene Wunschliste. Cardmarkets Wantlist sagt nur "ich suche das" — hier
  // kommt dazu, was es kosten darf und wie sich der Preis seither entwickelt.
  db.exec(`
    CREATE TABLE IF NOT EXISTS watchlist (
      id           INTEGER PRIMARY KEY AUTOINCREMENT,
      product_url  TEXT    NOT NULL,
      name         TEXT    NOT NULL DEFAULT '',
      game         TEXT    NOT NULL DEFAULT 'Pokemon',
      kind         TEXT    NOT NULL DEFAULT 'single',
      condition    TEXT    NOT NULL DEFAULT 'NM',
      language     TEXT    NOT NULL DEFAULT 'de',
      target_price REAL,
      max_price    REAL,
      note         TEXT    NOT NULL DEFAULT '',
      active       INTEGER NOT NULL DEFAULT 1,
      last_error   TEXT,
      created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
      UNIQUE(product_url, condition, language)
    );

    -- Preisverlauf des Wunschzettels. Genau das kann Cardmarket nicht: sehen,
    -- ob 34 Euro heute guenstig sind oder ob es letzte Woche 28 waren.
    CREATE TABLE IF NOT EXISTS watchlist_snapshots (
      id           INTEGER PRIMARY KEY AUTOINCREMENT,
      watchlist_id INTEGER NOT NULL REFERENCES watchlist(id) ON DELETE CASCADE,
      captured_at  TEXT    NOT NULL,
      best_price   REAL,
      median_price REAL,
      offers_count INTEGER,
      market_trend REAL,
      market_avg7  REAL,
      market_avg30 REAL,
      UNIQUE(watchlist_id, captured_at)
    );

    CREATE INDEX IF NOT EXISTS idx_wsnap_item ON watchlist_snapshots(watchlist_id);
  `)

  // Verkaeufe aus dem eingeloggten Bereich (scrape_sales.py legt sie sonst selbst an).
  db.exec(`
    CREATE TABLE IF NOT EXISTS orders (
      id           INTEGER PRIMARY KEY AUTOINCREMENT,
      cm_order_id  TEXT    NOT NULL UNIQUE,
      game         TEXT    NOT NULL DEFAULT '',
      buyer        TEXT    NOT NULL DEFAULT '',
      state        TEXT    NOT NULL DEFAULT '',
      item_value   REAL,
      shipping     REAL,
      total        REAL,
      paid_at      TEXT,
      sent_at      TEXT,
      arrived_at   TEXT,
      fetched_at   TEXT    NOT NULL
    );
    CREATE TABLE IF NOT EXISTS order_items (
      id            INTEGER PRIMARY KEY AUTOINCREMENT,
      order_id      INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
      card_id       INTEGER REFERENCES cards(id) ON DELETE SET NULL,
      cm_article_id TEXT,
      product_url   TEXT    NOT NULL DEFAULT '',
      name          TEXT    NOT NULL DEFAULT '',
      expansion     TEXT    NOT NULL DEFAULT '',
      number        TEXT    NOT NULL DEFAULT '',
      condition     TEXT    NOT NULL DEFAULT '',
      language      TEXT    NOT NULL DEFAULT '',
      price         REAL,
      amount        INTEGER NOT NULL DEFAULT 1,
      comment       TEXT    NOT NULL DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_order_items_order ON order_items(order_id);
    CREATE INDEX IF NOT EXISTS idx_orders_state ON orders(state);
  `)

  // Erkannte Handlungssignale. Bewusst persistent statt nur berechnet: nur so
  // laesst sich unterscheiden, was Christoph schon gesehen hat — sonst meldet
  // Telegram jeden Tag dieselbe Karte.
  db.exec(`
    CREATE TABLE IF NOT EXISTS signals (
      id              INTEGER PRIMARY KEY AUTOINCREMENT,
      listing_id      INTEGER REFERENCES listings(id) ON DELETE CASCADE,
      watchlist_id    INTEGER REFERENCES watchlist(id) ON DELETE CASCADE,
      kind            TEXT    NOT NULL,
      created_at      TEXT    NOT NULL,
      my_price        REAL,
      suggested_price REAL,
      detail          TEXT    NOT NULL DEFAULT '',
      notified_at     TEXT,
      dismissed_at    TEXT,
      applied_at      TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_signals_listing ON signals(listing_id);
    CREATE INDEX IF NOT EXISTS idx_signals_open
      ON signals(kind, dismissed_at, created_at);
  `)

  // signals war zuerst nur fuer eigene Angebote gedacht (listing_id NOT NULL).
  // Kaufsignale der Watchlist haengen an keinem Angebot — die Spalte muss also
  // optional werden. SQLite kann NOT NULL nicht nachtraeglich loesen, deshalb
  // einmalig neu aufbauen. Die Tabelle ist jung, bestehende Zeilen wandern mit.
  try {
    const cols = db.prepare("PRAGMA table_info(signals)").all() as any[]
    if (cols.length && !cols.some((c) => c.name === "watchlist_id")) {
      db.exec(`
        ALTER TABLE signals RENAME TO signals_old;
        CREATE TABLE signals (
          id              INTEGER PRIMARY KEY AUTOINCREMENT,
          listing_id      INTEGER REFERENCES listings(id) ON DELETE CASCADE,
          watchlist_id    INTEGER REFERENCES watchlist(id) ON DELETE CASCADE,
          kind            TEXT    NOT NULL,
          created_at      TEXT    NOT NULL,
          my_price        REAL,
          suggested_price REAL,
          detail          TEXT    NOT NULL DEFAULT '',
          notified_at     TEXT,
          dismissed_at    TEXT,
          applied_at      TEXT
        );
        INSERT INTO signals (id, listing_id, kind, created_at, my_price,
                             suggested_price, detail, notified_at, dismissed_at, applied_at)
          SELECT id, listing_id, kind, created_at, my_price, suggested_price,
                 detail, notified_at, dismissed_at, applied_at FROM signals_old;
        DROP TABLE signals_old;
        CREATE INDEX IF NOT EXISTS idx_signals_listing ON signals(listing_id);
        CREATE INDEX IF NOT EXISTS idx_signals_watchlist ON signals(watchlist_id);
      `)
    }
  } catch (e) { console.error("signals-Migration:", e) }

  // Erst hier: bei Alt-Datenbanken gibt es die Spalte vor der Migration nicht.
  try { db.exec("CREATE INDEX IF NOT EXISTS idx_signals_watchlist ON signals(watchlist_id)") } catch {}

  // Add columns if missing (migration for existing DBs)
  try { db.exec("ALTER TABLE cards ADD COLUMN image TEXT NOT NULL DEFAULT ''") } catch {}
  try { db.exec("ALTER TABLE cards ADD COLUMN purchase_price REAL") } catch {}
  try { db.exec("ALTER TABLE cards ADD COLUMN purchase_date TEXT") } catch {}
  try { db.exec("ALTER TABLE cards ADD COLUMN quantity INTEGER NOT NULL DEFAULT 1") } catch {}
  try { db.exec("ALTER TABLE cards ADD COLUMN binder_id INTEGER REFERENCES binders(id) ON DELETE SET NULL") } catch {}
  try { db.exec("ALTER TABLE cards ADD COLUMN set_name TEXT NOT NULL DEFAULT ''") } catch {}
  try { db.exec("ALTER TABLE cards ADD COLUMN stash INTEGER NOT NULL DEFAULT 0") } catch {}
  try { db.exec("ALTER TABLE scrape_runs ADD COLUMN engine TEXT NOT NULL DEFAULT 'patchright'") } catch {}
  try { db.exec("ALTER TABLE prices ADD COLUMN stale_grade INTEGER NOT NULL DEFAULT 0") } catch {}
  try { db.exec("ALTER TABLE cards ADD COLUMN watch INTEGER NOT NULL DEFAULT 0") } catch {}
  try { db.exec("ALTER TABLE listing_snapshots ADD COLUMN rank_capped INTEGER NOT NULL DEFAULT 0") } catch {}
  try { db.exec("ALTER TABLE listing_snapshots ADD COLUMN best_same REAL") } catch {}
  try { db.exec("ALTER TABLE listing_snapshots ADD COLUMN median_same REAL") } catch {}
  try { db.exec("ALTER TABLE watchlist ADD COLUMN last_error TEXT") } catch {}
  try { db.exec("ALTER TABLE listing_snapshots ADD COLUMN competitors_same INTEGER") } catch {}
  try { db.exec("ALTER TABLE listing_snapshots ADD COLUMN market_avg1 REAL") } catch {}
  try { db.exec("ALTER TABLE listing_snapshots ADD COLUMN market_available INTEGER") } catch {}

  // Default settings
  const insert = db.prepare("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)")
  insert.run("alert_threshold_pct", "10")
  insert.run("alert_threshold_eur", "35")
  insert.run("telegram_chat_id", "")
  insert.run("telegram_bot_token", "")
  insert.run("brightdata_api_key", "")
  insert.run("brightdata_zone", "cardmarket")
  insert.run("openai_api_key", "")
  insert.run("scrape_threshold_hot", "50")
  insert.run("scrape_threshold_mid", "15")
  insert.run("scrape_interval_mid_days", "3")
  insert.run("scrape_interval_cold_days", "7")
  insert.run("cardmarket_user", "")
  insert.run("cardmarket_games", "Pokemon,Magic")
  insert.run("sig_raise_uptrend_pct", "5")     // avg7 muss avg30 um X% schlagen
  insert.run("sig_raise_below_trend_pct", "10") // ... und mein Preis X% unter Trend liegen
  insert.run("sig_lower_days", "30")            // ab wann ein Angebot als Ladenhueter gilt
  insert.run("sig_lower_rank", "5")             // ... und ab welchem Rang
  insert.run("sig_sellnow_spike_pct", "20")     // avg1 ueber avg30 = kurzfristiger Hype
  insert.run("sig_min_price_eur", "2")          // unter diesem Wert lohnt kein Alarm
  insert.run("sig_underpriced_pct", "15")       // unter dem guenstigsten Zustandsgleichen
  insert.run("sig_overpriced_pct", "60")        // ueber dem Median der Zustandsgleichen
  insert.run("sig_buy_below_median_pct", "12")  // Kaufsignal ohne gesetzten Zielpreis
  insert.run("cm_commission_pct", "5")          // Cardmarket-Provision auf den Artikelwert
  insert.run("sig_repeat_days", "14")           // gleiches Signal fruehestens wieder nach X Tagen
}
