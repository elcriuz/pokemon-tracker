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
  triggerScrape: () => fetchJSON<any>("/scrape", { method: "POST" }),
  scrapeCards: (cardIds: number[]) =>
    fetchJSON<any>("/scrape/cards", { method: "POST", body: JSON.stringify({ cardIds }) }),
  stopScrape: () => fetchJSON<any>("/scrape/stop", { method: "POST" }),
  getScrapeStatus: () => fetchJSON<any>("/scrape/status"),
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
}
