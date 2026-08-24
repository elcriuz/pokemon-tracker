import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { api } from "@/lib/api"
import { ShoppingCart, ExternalLink, Trash2, Plus, X } from "lucide-react"

function formatEur(val: number | null) {
  if (val == null) return "–"
  return val >= 1000
    ? `${val.toLocaleString("de-DE", { maximumFractionDigits: 0 })}€`
    : `${val.toFixed(2)}€`
}

/**
 * Wo steht der Preis zwischen dem tiefsten und höchsten Wert, den wir seit dem
 * Eintragen gesehen haben? Genau das kann eine Wantlist nicht beantworten.
 */
function PriceRange({ low, high, current, points }: {
  low: number | null; high: number | null; current: number | null; points: number
}) {
  // Solange sich der Preis nicht bewegt hat, waere ein Balken irrefuehrend:
  // er suggeriert eine Spanne, die es noch gar nicht gibt.
  if (low == null || high == null || current == null || points < 2 || high === low) {
    return (
      <span className="text-xs text-muted-foreground">
        {points < 2 ? "erster Preisstand" : `stabil bei ${formatEur(low)}`}
      </span>
    )
  }
  const pos = high > low ? ((current - low) / (high - low)) * 100 : 50
  const color = pos <= 20 ? "bg-emerald-400" : pos >= 80 ? "bg-red-400" : "bg-amber-400"
  return (
    <div className="w-32">
      <div className="relative h-1.5 rounded-full bg-muted">
        <div className={`absolute w-1.5 h-1.5 rounded-full ${color} -translate-x-1/2`}
             style={{ left: `${Math.min(100, Math.max(0, pos))}%` }} />
      </div>
      <div className="flex justify-between text-[10px] text-muted-foreground mt-1 tabular-nums">
        <span>{formatEur(low)}</span>
        <span>{formatEur(high)}</span>
      </div>
    </div>
  )
}

export function Watchlist() {
  const queryClient = useQueryClient()
  const [showAdd, setShowAdd] = useState(false)
  const [url, setUrl] = useState("")
  const [condition, setCondition] = useState("NM")
  const [language, setLanguage] = useState("de")
  const [target, setTarget] = useState("")
  const [error, setError] = useState("")

  const { data, isLoading } = useQuery({
    queryKey: ["watchlist"],
    queryFn: () => api.getWatchlist(),
    refetchInterval: 60_000,
  })

  const add = useMutation({
    mutationFn: () => api.addWatchlistItem({
      url, condition, language,
      target_price: target ? Number(target.replace(",", ".")) : null,
    }),
    onSuccess: () => {
      setUrl(""); setTarget(""); setError(""); setShowAdd(false)
      queryClient.invalidateQueries({ queryKey: ["watchlist"] })
    },
    onError: (e: any) => setError(e?.message ?? "Konnte nicht hinzugefügt werden"),
  })

  const remove = useMutation({
    mutationFn: (id: number) => api.removeWatchlistItem(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["watchlist"] }),
  })

  if (isLoading) return <div className="p-6 text-muted-foreground">Lade Wunschliste…</div>

  const items = data?.items ?? []
  const s = data?.summary
  const opts = data?.options ?? { conditions: ["NM"], languages: ["de"] }

  return (
    <div className="p-6 space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">Wunschliste</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Nicht nur „ich suche das“, sondern <strong>was es kosten darf</strong> — und wie sich
            der Preis seither entwickelt hat.
          </p>
        </div>
        <div className="flex items-end gap-6">
          <div className="text-right">
            <div className="text-2xl font-semibold tabular-nums">{s?.count ?? 0}</div>
            <div className="text-xs text-muted-foreground">Karten</div>
          </div>
          <div className="text-right">
            <div className={`text-2xl font-semibold tabular-nums ${s?.with_signal ? "text-emerald-400" : ""}`}>
              {s?.with_signal ?? 0}
            </div>
            <div className="text-xs text-muted-foreground">jetzt kaufen</div>
          </div>
          <button onClick={() => setShowAdd(!showAdd)}
            className="px-3 py-2 rounded bg-primary text-primary-foreground text-sm inline-flex items-center gap-1.5">
            <Plus className="w-4 h-4" /> Karte
          </button>
        </div>
      </div>

      {showAdd && (
        <div className="border border-border rounded p-4 space-y-3 bg-muted/20">
          <div className="flex flex-wrap gap-2 items-end">
            <div className="flex-1 min-w-[280px]">
              <label className="text-xs text-muted-foreground block mb-1">Cardmarket-Link</label>
              <input value={url} onChange={(e) => setUrl(e.target.value)}
                placeholder="https://www.cardmarket.com/de/Pokemon/Products/Singles/…"
                className="w-full px-2.5 py-1.5 rounded bg-background border border-border text-sm" />
            </div>
            <div>
              <label className="text-xs text-muted-foreground block mb-1">Zustand</label>
              <select value={condition} onChange={(e) => setCondition(e.target.value)}
                className="px-2.5 py-1.5 rounded bg-background border border-border text-sm">
                {opts.conditions.map((c: string) => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
            <div>
              <label className="text-xs text-muted-foreground block mb-1">Sprache</label>
              <select value={language} onChange={(e) => setLanguage(e.target.value)}
                className="px-2.5 py-1.5 rounded bg-background border border-border text-sm">
                {opts.languages.map((l: string) => <option key={l} value={l}>{l}</option>)}
              </select>
            </div>
            <div>
              <label className="text-xs text-muted-foreground block mb-1">Zielpreis (optional)</label>
              <input value={target} onChange={(e) => setTarget(e.target.value)} placeholder="34,00"
                className="w-24 px-2.5 py-1.5 rounded bg-background border border-border text-sm" />
            </div>
            <button onClick={() => add.mutate()} disabled={!url || add.isPending}
              className="px-3 py-1.5 rounded bg-primary text-primary-foreground text-sm disabled:opacity-40">
              {add.isPending ? "…" : "Aufnehmen"}
            </button>
          </div>
          <p className="text-xs text-muted-foreground">
            Ohne Zielpreis meldet sich das System, sobald ein Angebot deutlich unter dem
            üblichen Niveau liegt.
          </p>
          {error && <p className="text-xs text-red-400">{error}</p>}
        </div>
      )}

      <div className="overflow-x-auto">
        <table className="w-full text-sm min-w-[820px]">
          <thead className="text-xs uppercase tracking-wide text-muted-foreground">
            <tr className="border-b border-border">
              <th className="text-left py-2 pr-3 font-medium">Karte</th>
              <th className="text-right py-2 px-3 font-medium">Günstigstes</th>
              <th className="text-right py-2 px-3 font-medium">Mittelfeld</th>
              <th className="text-right py-2 px-3 font-medium">Ziel</th>
              <th className="text-left py-2 px-3 font-medium">Beobachtete Spanne</th>
              <th className="text-left py-2 pl-3 font-medium"></th>
            </tr>
          </thead>
          <tbody>
            {items.map((i: any) => (
              <tr key={i.id} className="border-b border-border/50 hover:bg-muted/30">
                <td className="py-2.5 pr-3">
                  <a href={i.product_url} target="_blank" rel="noreferrer"
                     className="hover:underline inline-flex items-center gap-1">
                    {i.name}
                    <ExternalLink className="w-3 h-3 opacity-40" />
                  </a>
                  <div className="text-[11px] text-muted-foreground">
                    {i.game} · {i.condition}/{i.language}
                    {i.offers_count != null ? ` · ${i.offers_count} Angebote` : ""}
                  </div>
                  {i.problem && (
                    <div className="text-[11px] text-amber-400 mt-0.5">⚠ {i.problem}</div>
                  )}
                </td>
                <td className="py-2.5 px-3 text-right tabular-nums font-medium">
                  {formatEur(i.best_price)}
                </td>
                <td className="py-2.5 px-3 text-right tabular-nums text-muted-foreground">
                  {formatEur(i.median_price)}
                </td>
                <td className="py-2.5 px-3 text-right tabular-nums text-muted-foreground">
                  {formatEur(i.target_price)}
                </td>
                <td className="py-2.5 px-3">
                  <PriceRange low={i.history_low} high={i.history_high}
                              current={i.best_price} points={i.history_points} />
                </td>
                <td className="py-2.5 pl-3">
                  <div className="flex items-center gap-2">
                    {i.signals.length > 0 && (
                      <span title={i.signals[0].detail}
                        className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] bg-emerald-500/15 text-emerald-400">
                        <ShoppingCart className="w-3 h-3" /> Kaufen
                      </span>
                    )}
                    <button onClick={() => remove.mutate(i.id)}
                      className="opacity-30 hover:opacity-100" title="Von der Liste nehmen">
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {items.length === 0 && (
          <div className="py-10 text-center text-muted-foreground text-sm">
            Noch nichts auf der Liste. Cardmarket-Link einfügen und Zielpreis setzen.
          </div>
        )}
      </div>
    </div>
  )
}
