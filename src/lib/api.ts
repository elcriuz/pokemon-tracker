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
  getDashboard: (binderId?: string) =>
    fetchJSON<any>(`/dashboard${binderId ? `?binder_id=${binderId}` : ""}`),
  getCards: (binderId?: string) =>
    fetchJSON<any[]>(`/cards${binderId ? `?binder_id=${binderId}` : ""}`),
  getCard: (id: number) => fetchJSON<any>(`/cards/${id}`),
  getCardPrices: (id: number) => fetchJSON<any[]>(`/cards/${id}/prices`),
  addCard: (data: any) =>
    fetchJSON<any>("/cards", { method: "POST", body: JSON.stringify(data) }),
  updateCard: (id: number, data: any) =>
    fetchJSON<any>(`/cards/${id}`, { method: "PUT", body: JSON.stringify(data) }),
  deleteCard: (id: number) =>
    fetchJSON<any>(`/cards/${id}`, { method: "DELETE" }),
  deleteImage: (id: number) =>
    fetchJSON<any>(`/cards/${id}/image`, { method: "DELETE" }),
  toggleWatch: (id: number) =>
    fetchJSON<any>(`/cards/${id}/watch`, { method: "POST" }),
  triggerScrape: (engine?: string) =>
    fetchJSON<any>("/scrape", { method: "POST", body: JSON.stringify({ engine }) }),
  scrapeCards: (cardIds: number[], engine?: string) =>
    fetchJSON<any>("/scrape/cards", { method: "POST", body: JSON.stringify({ cardIds, engine }) }),
  stopScrape: () => fetchJSON<any>("/scrape/stop", { method: "POST" }),
  getScrapeStatus: () => fetchJSON<any>("/scrape/status"),
  getScrapeLogs: () => fetchJSON<{ engine: string | null; lines: string[] }>("/scrape/logs"),
  getPortfolioHistory: () => fetchJSON<any[]>("/portfolio/history"),
  getSettings: () => fetchJSON<Record<string, string>>("/settings"),
  updateSettings: (data: Record<string, string>) =>
    fetchJSON<any>("/settings", { method: "PUT", body: JSON.stringify(data) }),
  testTelegram: () => fetchJSON<any>("/telegram/test", { method: "POST" }),
  getBinders: () => fetchJSON<any[]>("/binders"),
  addBinder: (data: { name: string; color: string }) =>
    fetchJSON<any>("/binders", { method: "POST", body: JSON.stringify(data) }),
  updateBinder: (id: number, data: any) =>
    fetchJSON<any>(`/binders/${id}`, { method: "PUT", body: JSON.stringify(data) }),
  deleteBinder: (id: number) =>
    fetchJSON<any>(`/binders/${id}`, { method: "DELETE" }),
  getSales: (game = "") =>
    fetchJSON<any>(`/sales${game ? `?game=${encodeURIComponent(game)}` : ""}`),

  getSaleItems: (id: number) => fetchJSON<any>(`/sales/${id}/items`),

  getWatchlist: () => fetchJSON<any>("/watchlist"),

  addWatchlistItem: (body: any) =>
    fetchJSON<any>("/watchlist", { method: "POST", body: JSON.stringify(body) }),

  removeWatchlistItem: (id: number) =>
    fetchJSON<any>(`/watchlist/${id}`, { method: "DELETE" }),

  getOffers: (game = "") => {
    const qs = game ? `?game=${encodeURIComponent(game)}` : ""
    return fetchJSON<any>(`/offers${qs}`)
  },

  queuePrice: (listingId: number, price: number, signalId?: number) =>
    fetchJSON<any>(`/offers/${listingId}/queue`, {
      method: "POST",
      body: JSON.stringify({ price, signal_id: signalId }),
    }),

  unqueuePrice: (listingId: number) =>
    fetchJSON<any>(`/offers/queue/${listingId}`, { method: "DELETE" }),

  getActions: (kind = "", game = "") => {
    const q = new URLSearchParams()
    if (kind) q.set("kind", kind)
    if (game) q.set("game", game)
    const qs = q.toString()
    return fetchJSON<any>(`/actions${qs ? `?${qs}` : ""}`)
  },

  getRepriceQueue: () => fetchJSON<any>("/offers/queue"),

  runRepriceQueue: () => fetchJSON<any>("/offers/queue/run", { method: "POST" }),

  dismissSignal: (id: number) =>
    fetchJSON<any>(`/offers/signals/${id}/dismiss`, { method: "POST" }),

  getCardShop: (showStash = false, showLosers = false) => {
    const params = new URLSearchParams()
    if (showStash) params.set("stash", "1")
    if (showLosers) params.set("losers", "1")
    const qs = params.toString()
    return fetchJSON<any>(`/cardshop${qs ? `?${qs}` : ""}`)
  },
  toggleStash: (id: number) => fetchJSON<any>(`/cardshop/${id}/stash`, { method: "POST" }),
  getScans: (limit = 100) => fetchJSON<any>(`/scans?limit=${limit}`),
  getScanUsers: () => fetchJSON<any[]>("/scans/users"),
}
