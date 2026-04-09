import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { useState, useEffect } from "react"
import { Send, Check, AlertCircle, Plus, Pencil, Trash2 } from "lucide-react"

const BINDER_COLORS = [
  "#3b82f6", "#ef4444", "#22c55e", "#eab308", "#a855f7", "#f97316", "#ec4899", "#06b6d4",
]

export function Settings() {
  const { data: settings, isLoading } = useQuery({ queryKey: ["settings"], queryFn: api.getSettings })
  const { data: binders } = useQuery({ queryKey: ["binders"], queryFn: api.getBinders })
  const { data: scrapeHistory } = useQuery({ queryKey: ["scrapeHistory"], queryFn: () => api.getScrapeStatus() })
  const queryClient = useQueryClient()

  const [form, setForm] = useState({ alert_threshold_pct: "5", telegram_bot_token: "", telegram_chat_id: "" })
  const [saved, setSaved] = useState(false)
  const [testResult, setTestResult] = useState<boolean | null>(null)
  const [newBinder, setNewBinder] = useState("")
  const [newBinderColor, setNewBinderColor] = useState(BINDER_COLORS[0])
  const [editingBinder, setEditingBinder] = useState<any>(null)

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
  const addBinderMutation = useMutation({
    mutationFn: api.addBinder,
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["binders"] }); setNewBinder("") },
  })
  const updateBinderMutation = useMutation({
    mutationFn: ({ id, ...data }: any) => api.updateBinder(id, data),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["binders"] }); setEditingBinder(null) },
  })
  const deleteBinderMutation = useMutation({
    mutationFn: api.deleteBinder,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["binders"] }),
  })

  if (isLoading) return <div className="text-muted-foreground">Laden...</div>

  return (
    <div className="max-w-2xl mx-auto space-y-8">
      <h1 className="text-2xl font-bold">Einstellungen</h1>

      {/* Binder Management */}
      <section className="space-y-4">
        <h2 className="text-lg font-semibold">Binder</h2>
        <div className="p-4 rounded-lg bg-card border border-border space-y-3">
          {/* Existing Binders */}
          {binders?.map((b: any) => (
            <div key={b.id} className="flex items-center gap-3">
              {editingBinder?.id === b.id ? (
                <>
                  <div className="flex gap-1">
                    {BINDER_COLORS.map((c) => (
                      <button
                        key={c}
                        onClick={() => setEditingBinder({ ...editingBinder, color: c })}
                        className={`w-6 h-6 rounded-full border-2 ${editingBinder.color === c ? "border-white" : "border-transparent"}`}
                        style={{ backgroundColor: c }}
                      />
                    ))}
                  </div>
                  <input
                    value={editingBinder.name}
                    onChange={(e) => setEditingBinder({ ...editingBinder, name: e.target.value })}
                    className="flex-1 px-2 py-1 rounded bg-secondary border border-border text-foreground text-sm"
                  />
                  <button
                    onClick={() => updateBinderMutation.mutate(editingBinder)}
                    className="px-2 py-1 text-xs rounded bg-ring text-primary-foreground"
                  >
                    OK
                  </button>
                  <button onClick={() => setEditingBinder(null)} className="px-2 py-1 text-xs rounded bg-secondary">X</button>
                </>
              ) : (
                <>
                  <span className="w-4 h-4 rounded-full flex-shrink-0" style={{ backgroundColor: b.color }} />
                  <span className="flex-1 text-sm">{b.name}</span>
                  <span className="text-xs text-muted-foreground">{b.card_count} Karten</span>
                  <button onClick={() => setEditingBinder({ id: b.id, name: b.name, color: b.color })} className="p-1 rounded hover:bg-secondary">
                    <Pencil className="w-3.5 h-3.5 text-muted-foreground" />
                  </button>
                  <button
                    onClick={() => { if (confirm(`Binder "${b.name}" loeschen? Karten werden "Unsortiert".`)) deleteBinderMutation.mutate(b.id) }}
                    className="p-1 rounded hover:bg-destructive/20"
                  >
                    <Trash2 className="w-3.5 h-3.5 text-destructive" />
                  </button>
                </>
              )}
            </div>
          ))}

          {/* Add Binder */}
          <div className="flex items-center gap-2 pt-2 border-t border-border">
            <div className="flex gap-1">
              {BINDER_COLORS.map((c) => (
                <button
                  key={c}
                  onClick={() => setNewBinderColor(c)}
                  className={`w-5 h-5 rounded-full border-2 ${newBinderColor === c ? "border-white" : "border-transparent"}`}
                  style={{ backgroundColor: c }}
                />
              ))}
            </div>
            <input
              value={newBinder}
              onChange={(e) => setNewBinder(e.target.value)}
              placeholder="Neuer Binder..."
              className="flex-1 px-2 py-1 rounded bg-secondary border border-border text-foreground text-sm"
              onKeyDown={(e) => { if (e.key === "Enter" && newBinder) addBinderMutation.mutate({ name: newBinder, color: newBinderColor }) }}
            />
            <button
              onClick={() => { if (newBinder) addBinderMutation.mutate({ name: newBinder, color: newBinderColor }) }}
              disabled={!newBinder}
              className="flex items-center gap-1 px-2 py-1 text-sm rounded bg-ring text-primary-foreground disabled:opacity-50"
            >
              <Plus className="w-3.5 h-3.5" /> Hinzufuegen
            </button>
          </div>
        </div>
      </section>

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
