import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { useState, useEffect } from "react"
import { Send, Check, AlertCircle } from "lucide-react"

export function Settings() {
  const { data: settings, isLoading } = useQuery({ queryKey: ["settings"], queryFn: api.getSettings })
  const { data: scrapeHistory } = useQuery({ queryKey: ["scrapeHistory"], queryFn: () => api.getScrapeStatus() })
  const queryClient = useQueryClient()

  const [form, setForm] = useState({ alert_threshold_pct: "5", telegram_bot_token: "", telegram_chat_id: "" })
  const [saved, setSaved] = useState(false)
  const [testResult, setTestResult] = useState<boolean | null>(null)

  useEffect(() => {
    if (settings) setForm((f) => ({ ...f, ...settings }))
  }, [settings])

  const saveMutation = useMutation({
    mutationFn: (data: Record<string, string>) => api.updateSettings(data),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["settings"] }); setSaved(true); setTimeout(() => setSaved(false), 2000) },
  })

  const testMutation = useMutation({
    mutationFn: api.testTelegram,
    onSuccess: (data) => setTestResult(data.ok),
    onError: () => setTestResult(false),
  })

  if (isLoading) return <div className="text-muted-foreground">Laden...</div>

  return (
    <div className="max-w-2xl mx-auto space-y-8">
      <h1 className="text-2xl font-bold">Einstellungen</h1>

      {/* Alert Settings */}
      <section className="space-y-4">
        <h2 className="text-lg font-semibold">Preis-Alerts</h2>
        <div className="p-4 rounded-lg bg-card border border-border space-y-3">
          <label className="block">
            <span className="text-sm text-muted-foreground">Schwellenwert fuer Alert (%)</span>
            <input
              type="number"
              value={form.alert_threshold_pct}
              onChange={(e) => setForm({ ...form, alert_threshold_pct: e.target.value })}
              className="mt-1 w-full px-3 py-2 rounded-lg bg-secondary border border-border text-foreground"
            />
          </label>
        </div>
      </section>

      {/* Telegram Settings */}
      <section className="space-y-4">
        <h2 className="text-lg font-semibold">Telegram</h2>
        <div className="p-4 rounded-lg bg-card border border-border space-y-3">
          <label className="block">
            <span className="text-sm text-muted-foreground">Bot Token</span>
            <input
              type="password"
              value={form.telegram_bot_token}
              onChange={(e) => setForm({ ...form, telegram_bot_token: e.target.value })}
              placeholder="123456:ABC-DEF..."
              className="mt-1 w-full px-3 py-2 rounded-lg bg-secondary border border-border text-foreground"
            />
          </label>
          <label className="block">
            <span className="text-sm text-muted-foreground">Chat ID</span>
            <input
              type="text"
              value={form.telegram_chat_id}
              onChange={(e) => setForm({ ...form, telegram_chat_id: e.target.value })}
              placeholder="123456789"
              className="mt-1 w-full px-3 py-2 rounded-lg bg-secondary border border-border text-foreground"
            />
          </label>
          <button
            onClick={() => testMutation.mutate()}
            disabled={!form.telegram_bot_token || !form.telegram_chat_id}
            className="flex items-center gap-2 px-3 py-2 text-sm rounded-lg bg-secondary hover:bg-secondary/80 disabled:opacity-50"
          >
            <Send className="w-4 h-4" />
            Test senden
          </button>
          {testResult === true && <p className="text-sm text-positive flex items-center gap-1"><Check className="w-4 h-4" /> Nachricht gesendet!</p>}
          {testResult === false && <p className="text-sm text-negative flex items-center gap-1"><AlertCircle className="w-4 h-4" /> Fehler beim Senden</p>}
        </div>
      </section>

      {/* Save */}
      <button
        onClick={() => saveMutation.mutate(form)}
        className="flex items-center gap-2 px-4 py-2 rounded-lg bg-ring text-primary-foreground hover:bg-ring/80 transition-colors"
      >
        {saved ? <><Check className="w-4 h-4" /> Gespeichert</> : "Speichern"}
      </button>

      {/* Scrape Status */}
      <section className="space-y-4">
        <h2 className="text-lg font-semibold">Letzter Scrape</h2>
        {scrapeHistory?.latest ? (
          <div className="p-4 rounded-lg bg-card border border-border text-sm space-y-1">
            <p>Status: <span className={scrapeHistory.latest.status === "completed" ? "text-positive" : "text-negative"}>{scrapeHistory.latest.status}</span></p>
            <p>Gestartet: {new Date(scrapeHistory.latest.started_at).toLocaleString("de-DE")}</p>
            {scrapeHistory.latest.finished_at && <p>Beendet: {new Date(scrapeHistory.latest.finished_at).toLocaleString("de-DE")}</p>}
            {scrapeHistory.latest.duration_s && <p>Dauer: {Math.round(scrapeHistory.latest.duration_s)}s</p>}
            {scrapeHistory.latest.card_count && <p>Karten: {scrapeHistory.latest.card_count}</p>}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">Noch kein Scrape durchgefuehrt</p>
        )}
      </section>
    </div>
  )
}
