const BASE = "/api"

async function fetchJSON<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }))
    throw new Error(err.error || res.statusText)
  }
  return res.json()
}

export const api = {
  getDashboard: () => fetchJSON<any>("/dashboard"),
  getCards: () => fetchJSON<any[]>("/cards"),
  getCard: (id: number) => fetchJSON<any>(`/cards/${id}`),
  getCardPrices: (id: number) => fetchJSON<any[]>(`/cards/${id}/prices`),
  addCard: (data: { url: string; name?: string; grade?: string; notes?: string }) =>
    fetchJSON<any>("/cards", { method: "POST", body: JSON.stringify(data) }),
  updateCard: (id: number, data: { name?: string; grade?: string; notes?: string }) =>
    fetchJSON<any>(`/cards/${id}`, { method: "PUT", body: JSON.stringify(data) }),
  deleteCard: (id: number) =>
    fetchJSON<any>(`/cards/${id}`, { method: "DELETE" }),
  triggerScrape: () => fetchJSON<any>("/scrape", { method: "POST" }),
  scrapeCards: (cardIds: number[]) =>
    fetchJSON<any>("/scrape/cards", { method: "POST", body: JSON.stringify({ cardIds }) }),
  getScrapeStatus: () => fetchJSON<any>("/scrape/status"),
  getPortfolioHistory: () => fetchJSON<any[]>("/portfolio/history"),
  getSettings: () => fetchJSON<Record<string, string>>("/settings"),
  updateSettings: (data: Record<string, string>) =>
    fetchJSON<any>("/settings", { method: "PUT", body: JSON.stringify(data) }),
  testTelegram: () => fetchJSON<any>("/telegram/test", { method: "POST" }),
}
