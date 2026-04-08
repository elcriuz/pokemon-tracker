import { Router } from "express"
import { getDb } from "../db"

export const telegramRouter = Router()

async function sendTelegram(botToken: string, chatId: string, text: string): Promise<boolean> {
  const url = `https://api.telegram.org/bot${botToken}/sendMessage`
  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, text, parse_mode: "HTML" }),
  })
  return resp.ok
}

// POST /api/telegram/test
telegramRouter.post("/test", async (_req, res) => {
  const db = getDb()
  const settings = db.prepare("SELECT key, value FROM settings").all() as any[]
  const cfg: Record<string, string> = {}
  for (const r of settings) cfg[r.key] = r.value

  if (!cfg.telegram_bot_token || !cfg.telegram_chat_id) {
    return res.status(400).json({ error: "Telegram bot token and chat ID required" })
  }

  const ok = await sendTelegram(
    cfg.telegram_bot_token,
    cfg.telegram_chat_id,
    "Pokemon Tracker: Test-Nachricht erfolgreich!"
  )
  res.json({ ok })
})

export { sendTelegram }
