import express from "express"
import cors from "cors"
import path from "path"
import { fileURLToPath } from "url"
import { getDb } from "./db"

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)
import { cardsRouter } from "./routes/cards"
import { dashboardRouter } from "./routes/dashboard"
import { pricesRouter } from "./routes/prices"
import { scrapeRouter } from "./routes/scrape"
import { settingsRouter } from "./routes/settings"
import { telegramRouter } from "./routes/telegram"
import { bindersRouter } from "./routes/binders"
import { scansRouter } from "./routes/scans"
import { cardshopRouter } from "./routes/cardshop"
import { offersRouter } from "./routes/offers"
import { watchlistRouter } from "./routes/watchlist"
import { salesRouter } from "./routes/sales"

const app = express()
const PORT = process.env.PORT || 3333

app.use(cors())
app.use(express.json())

// Init DB on startup
getDb()

// API routes
app.use("/api/cards", cardsRouter)
app.use("/api/dashboard", dashboardRouter)
app.use("/api/portfolio", pricesRouter)
app.use("/api/scrape", scrapeRouter)
app.use("/api/settings", settingsRouter)
app.use("/api/telegram", telegramRouter)
app.use("/api/binders", bindersRouter)
app.use("/api/scans", scansRouter)
app.use("/api/cardshop", cardshopRouter)
app.use("/api/offers", offersRouter)
app.use("/api/watchlist", watchlistRouter)
app.use("/api/sales", salesRouter)

// Serve card images
app.use("/images", express.static(path.join(__dirname, "..", "data", "images")))

// Serve static frontend in production
const distPath = path.join(__dirname, "..", "dist")
app.use(express.static(distPath))
app.get("/{*path}", (_req, res) => {
  res.sendFile(path.join(distPath, "index.html"))
})

app.listen(PORT, () => {
  console.log(`Server running on http://localhost:${PORT}`)
})
