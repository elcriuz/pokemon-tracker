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
  const [languages, setLanguages] = useState<string[]>(["de"])
  const [target, setTarget] = useState("")
  const [error, setError] = useState("")

  const { data, isLoading } = useQuery({
    queryKey: ["watchlist"],
    queryFn: () => api.getWatchlist(),
    refetchInterval: 60_000,
  })

  // Alles ausser /Products/Singles/ ist Sealed — Displays, Booster, ETBs. Die
  // haben keinen Zustand, nur eine Sprache.
  const isSealed = /\/Products\/(?!Singles\/)/i.test(url)

  const toggleLanguage = (l: string) =>
    setLanguages((cur) => cur.includes(l) ? cur.filter((x) => x !== l) : [...cur, l])

  const add = useMutation({
    mutationFn: () => api.addWatchlistItem({
      url, condition: isSealed ? "" : condition, languages,
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

  // Eine weitere Sprache zu einem Produkt, das schon auf der Liste steht.
  const addLanguage = useMutation({
    mutationFn: (b: { url: string; condition: string; language: string }) =>
      api.addWatchlistItem({ url: b.url, condition: b.condition, languages: [b.language] }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["watchlist"] }),
  })

  if (isLoading) return <div className="p-6 text-muted-foreground">Lade Wunschliste…</div>

  const items = data?.items ?? []
  const s = data?.summary
  const opts = data?.options ?? { conditions: ["NM"], languages: ["de"] }

  // Ein Produkt, mehrere Sprachen: jede Sprache ist ihr eigener Markt mit eigenem
  // Verlauf und Signal, in der Liste gehoeren sie aber zusammen.
  const groups: { key: string; items: any[] }[] = []
  for (const i of items) {
    const key = `${i.product_url}|${i.condition}`
    let g = groups.find((x) => x.key === key)
    if (!g) { g = { key, items: [] }; groups.push(g) }
    g.items.push(i)
  }
  for (const g of groups) g.items.sort((a, b) => a.language.localeCompare(b.language))

  return (
    <div className="p-6 space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">Wunschliste</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Nicht nur „ich suche das“, sondern <strong>was es kosten darf</strong> — inklusive
            Versand, und wie sich der Preis seither entwickelt hat.
          </p>
        </div>
        <div className="flex items-end gap-6">
          <div className="text-right">
            <div className="text-2xl font-semibold tabular-nums">{groups.length}</div>
            <div className="text-xs text-muted-foreground">
              {s?.count && s.count !== groups.length ? `Produkte · ${s.count} Ausführungen` : "Produkte"}
            </div>
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
            {!isSealed && (
              <div>
                <label className="text-xs text-muted-foreground block mb-1">Zustand</label>
                <select value={condition} onChange={(e) => setCondition(e.target.value)}
                  className="px-2.5 py-1.5 rounded bg-background border border-border text-sm">
                  {opts.conditions.map((c: string) => <option key={c} value={c}>{c}</option>)}
                </select>
              </div>
            )}
            {isSealed && url && (
              <div className="self-center text-xs text-muted-foreground pt-4">Sealed — kein Zustand</div>
            )}
            <div>
              <label className="text-xs text-muted-foreground block mb-1">Sprachen</label>
              <div className="flex flex-wrap gap-1">
                {opts.languages.map((l: string) => (
                  <button key={l} type="button" onClick={() => toggleLanguage(l)}
                    className={`px-2 py-1 rounded border text-xs uppercase ${languages.includes(l)
                      ? "bg-primary text-primary-foreground border-primary"
                      : "border-border text-muted-foreground hover:text-foreground"}`}>
                    {l}
                  </button>
                ))}
              </div>
            </div>
            <div>
              <label className="text-xs text-muted-foreground block mb-1">Zielpreis inkl. Versand (optional)</label>
              <input value={target} onChange={(e) => setTarget(e.target.value)} placeholder="34,00"
                className="w-24 px-2.5 py-1.5 rounded bg-background border border-border text-sm" />
            </div>
            <button onClick={() => add.mutate()} disabled={!url || !languages.length || add.isPending}
              className="px-3 py-1.5 rounded bg-primary text-primary-foreground text-sm disabled:opacity-40">
              {add.isPending ? "…" : "Aufnehmen"}
            </button>
          </div>
          <p className="text-xs text-muted-foreground">
            Ohne Zielpreis meldet sich das System, sobald ein Angebot deutlich unter dem
            üblichen Niveau liegt. Mehrere Sprachen werden getrennt beobachtet — jede ist
            ihr eigener Markt. Die Preise kommen ein, zwei Minuten nach dem Anlegen.
          </p>
          {error && <p className="text-xs text-red-400">{error}</p>}
        </div>
      )}

      <div className="overflow-x-auto">
        <table className="w-full text-sm min-w-[900px]">
          <thead className="text-xs uppercase tracking-wide text-muted-foreground">
            <tr className="border-b border-border">
              <th className="text-left py-2 pr-3 font-medium">Karte</th>
              <th className="text-left py-2 px-3 font-medium">Sprache</th>
              <th className="text-right py-2 px-3 font-medium">
                Günstigstes
                <div className="normal-case tracking-normal text-[10px] opacity-60">inkl. Versand</div>
              </th>
              <th className="text-right py-2 px-3 font-medium">
                Mittelfeld
                <div className="normal-case tracking-normal text-[10px] opacity-60">inkl. Versand</div>
              </th>
              <th className="text-right py-2 px-3 font-medium">Ziel</th>
              <th className="text-left py-2 px-3 font-medium">Beobachtete Spanne</th>
              <th className="text-left py-2 pl-3 font-medium"></th>
            </tr>
          </thead>
          <tbody>
            {groups.map((g) => g.items.map((i: any, idx: number) => {
              const last = idx === g.items.length - 1
              const missing = opts.languages.filter((l: string) => !g.items.some((x: any) => x.language === l))
              return (
              <tr key={i.id} className={`hover:bg-muted/30 border-b ${last ? "border-border/50" : "border-border/15"}`}>
                {/* Die Karte steht einmal je Produkt und spannt ueber alle Sprachen. */}
                {idx === 0 && (
                <td className="py-2.5 pr-3 align-top" rowSpan={g.items.length}>
                  <div className="flex items-start gap-3">
                    {/* Ohne Bild bleibt der Platz stehen, damit die Namen bündig bleiben. */}
                    {i.image ? (
                      <img src={`/images/${i.image}`} alt="" loading="lazy"
                        className={`w-12 h-[68px] rounded-md flex-shrink-0 ${i.kind === "sealed" ? "object-contain" : "object-cover"}`} />
                    ) : (
                      <div className="w-12 h-[68px] rounded-md flex-shrink-0 bg-muted/40" />
                    )}
                    <div className="min-w-0">
                      {/* Gefilterter Link: dieselbe Seite, die der Tracker vergleicht. */}
                      <a href={i.offer_url ?? i.product_url} target="_blank" rel="noreferrer"
                         className="hover:underline inline-flex items-center gap-1">
                        {i.name}
                        <ExternalLink className="w-3 h-3 opacity-40" />
                      </a>
                      <div className="text-[11px] text-muted-foreground">
                        {i.game} · {i.kind === "sealed" ? "Sealed" : i.condition}
                      </div>
                      {missing.length > 0 && (
                        <select value="" title="Weitere Sprache beobachten"
                          onChange={(e) => e.target.value && addLanguage.mutate({
                            url: i.product_url, condition: i.condition, language: e.target.value })}
                          className="mt-1.5 text-[11px] bg-transparent text-muted-foreground hover:text-foreground border border-border/60 rounded px-1 py-0.5 cursor-pointer">
                          <option value="">+ Sprache</option>
                          {missing.map((l: string) => <option key={l} value={l}>{l}</option>)}
                        </select>
                      )}
                    </div>
                  </div>
                </td>
                )}
                <td className="py-2.5 px-3 whitespace-nowrap">
                  <span className="inline-block px-1.5 py-0.5 rounded bg-muted/50 text-xs font-medium uppercase">
                    {i.language}
                  </span>
                  <div className="text-[11px] text-muted-foreground mt-1">
                    {i.offers_count != null ? `${i.offers_count} Angebote` : "wird geholt …"}
                  </div>
                  {i.problem && (
                    <div className="text-[11px] text-amber-400 mt-0.5">⚠ {i.problem}</div>
                  )}
                </td>
                <td className="py-2.5 px-3 text-right tabular-nums">
                  <div className="font-medium">{formatEur(i.best_total ?? i.best_price)}</div>
                  {/* Bis der erste Abruf mit Versand durch ist, steht hier der reine Kartenpreis. */}
                  <div className="text-[10px] text-muted-foreground whitespace-nowrap">
                    {i.best_shipping != null
                      ? `${formatEur(i.best_price)} + ${formatEur(i.best_shipping)} · ${i.best_origin ?? "?"}`
                      : i.best_price != null ? "ohne Versand" : ""}
                  </div>
                </td>
                <td className="py-2.5 px-3 text-right tabular-nums text-muted-foreground">
                  {formatEur(i.median_total ?? i.median_price)}
                </td>
                <td className="py-2.5 px-3 text-right tabular-nums text-muted-foreground">
                  {formatEur(i.target_price)}
                </td>
                <td className="py-2.5 px-3">
                  <PriceRange low={i.history_low} high={i.history_high}
                              current={i.best_total ?? i.best_price} points={i.history_points} />
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
                      className="opacity-30 hover:opacity-100" title="Diese Sprache von der Liste nehmen">
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </td>
              </tr>
              )
            }))}
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
