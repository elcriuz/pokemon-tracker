import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { api } from "@/lib/api"
import { TrendingUp, TrendingDown, Flame, Swords, Banknote, Snowflake, ExternalLink, X } from "lucide-react"

/** Die vier Handlungssignale. Farbe kodiert Richtung, nicht Wichtigkeit. */
const SIGNAL_CONFIG: Record<string, { label: string; icon: any; color: string; bg: string }> = {
  raise:    { label: "Anheben",       icon: TrendingUp,   color: "text-emerald-400", bg: "bg-emerald-500/15" },
  lower:    { label: "Senken",        icon: TrendingDown, color: "text-amber-400",   bg: "bg-amber-500/15" },
  sell_now: { label: "Jetzt raus",    icon: Flame,        color: "text-orange-400",  bg: "bg-orange-500/20" },
  undercut: { label: "Unterboten",    icon: Swords,       color: "text-red-400",     bg: "bg-red-500/15" },
  underpriced: { label: "Zu günstig", icon: Banknote,     color: "text-sky-400",     bg: "bg-sky-500/15" },
  overpriced:  { label: "Zu teuer",   icon: Snowflake,    color: "text-violet-400",  bg: "bg-violet-500/15" },
}

function formatEur(val: number | null) {
  if (val == null) return "–"
  return val >= 1000
    ? `${val.toLocaleString("de-DE", { maximumFractionDigits: 0 })}€`
    : `${val.toFixed(2)}€`
}

/** Rang unter vergleichbaren Angeboten — Platz 1 ist nicht automatisch gut. */
function RankBadge({ rank, capped, total }: { rank: number | null; capped: number; total: number | null }) {
  if (capped) {
    return <span className="text-xs text-muted-foreground" title="Nicht unter den 50 günstigsten vergleichbaren Angeboten">
      &gt;50
    </span>
  }
  if (rank == null) return <span className="text-xs text-muted-foreground">–</span>
  const color = rank === 1 ? "text-amber-400" : rank <= 3 ? "text-emerald-400" : "text-muted-foreground"
  return (
    <span className={`text-sm font-medium ${color}`} title={rank === 1 ? "Günstigster – prüfen, ob zu billig" : ""}>
      {rank}{total ? <span className="text-muted-foreground text-xs">/{total}</span> : null}
    </span>
  )
}

export function Offers() {
  const queryClient = useQueryClient()
  const [game, setGame] = useState<string>("")
  const [onlySignals, setOnlySignals] = useState(false)

  const { data, isLoading } = useQuery({
    queryKey: ["offers", game],
    queryFn: () => api.getOffers(game),
    refetchInterval: 60_000,
  })

  const dismiss = useMutation({
    mutationFn: (id: number) => api.dismissSignal(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["offers"] }),
  })

  if (isLoading) return <div className="p-6 text-muted-foreground">Lade Angebote…</div>

  const items = (data?.items ?? []).filter((i: any) => !onlySignals || i.signals.length > 0)
  const s = data?.summary

  return (
    <div className="p-6 space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">Meine Angebote</h1>
          <p className="text-sm text-muted-foreground mt-1">
            <strong>Rang</strong> = Käufersicht (gleiche Sprache, mindestens gleicher Zustand).
            <strong> Günstigster</strong> und <strong>vs. Vergleich</strong> zählen nur Angebote im
            <em> exakt gleichen</em> Zustand — sonst wirkt jede gespielte Karte zu billig.
          </p>
        </div>
        <div className="flex gap-6 text-right">
          <div>
            <div className="text-2xl font-semibold tabular-nums">{s?.count ?? 0}</div>
            <div className="text-xs text-muted-foreground">Angebote</div>
          </div>
          <div>
            <div className="text-2xl font-semibold tabular-nums">{formatEur(s?.value ?? 0)}</div>
            <div className="text-xs text-muted-foreground">Angebotswert</div>
          </div>
          <div>
            <div className={`text-2xl font-semibold tabular-nums ${s?.with_signal ? "text-amber-400" : ""}`}>
              {s?.with_signal ?? 0}
            </div>
            <div className="text-xs text-muted-foreground">mit Signal</div>
          </div>
        </div>
      </div>

      <div className="flex flex-wrap gap-2">
        <button onClick={() => setGame("")}
          className={`px-3 py-1.5 rounded text-sm ${!game ? "bg-primary text-primary-foreground" : "bg-muted"}`}>
          Alle
        </button>
        {(s?.games ?? []).map((g: string) => (
          <button key={g} onClick={() => setGame(g)}
            className={`px-3 py-1.5 rounded text-sm ${game === g ? "bg-primary text-primary-foreground" : "bg-muted"}`}>
            {g}
          </button>
        ))}
        <button onClick={() => setOnlySignals(!onlySignals)}
          className={`px-3 py-1.5 rounded text-sm ml-auto ${onlySignals ? "bg-amber-500/20 text-amber-400" : "bg-muted"}`}>
          Nur mit Signal
        </button>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-sm min-w-[880px]">
          <thead className="text-xs uppercase tracking-wide text-muted-foreground">
            <tr className="border-b border-border">
              <th className="text-left py-2 pr-3 font-medium">Karte</th>
              <th className="text-right py-2 px-3 font-medium">Mein Preis</th>
              <th className="text-right py-2 px-3 font-medium">Rang</th>
              <th className="text-right py-2 px-3 font-medium" title="Günstigstes Angebot im gleichen Zustand">
                Günstigster ({"gl. Zustand"})
              </th>
              <th className="text-right py-2 px-3 font-medium">Trend</th>
              <th className="text-right py-2 px-3 font-medium">vs. Vergleich</th>
              <th className="text-left py-2 pl-3 font-medium">Signal</th>
            </tr>
          </thead>
          <tbody>
            {items.map((i: any) => (
              <tr key={i.id} className="border-b border-border/50 hover:bg-muted/30">
                <td className="py-2.5 pr-3">
                  <a href={i.product_url} target="_blank" rel="noreferrer"
                     className="hover:underline inline-flex items-center gap-1">
                    {i.product_name}
                    <ExternalLink className="w-3 h-3 opacity-40" />
                  </a>
                  <div className="text-[11px] text-muted-foreground">
                    {i.game} · {i.expansion} · {i.condition}/{i.language}
                    {i.is_foil ? " · Foil" : ""}
                    {i.days_listed != null ? ` · ${i.days_listed} Tage` : ""}
                  </div>
                </td>
                <td className="py-2.5 px-3 text-right tabular-nums font-medium">
                  {formatEur(i.price)}
                  {i.quantity > 1 && <span className="text-muted-foreground text-xs"> ×{i.quantity}</span>}
                </td>
                <td className="py-2.5 px-3 text-right">
                  <RankBadge rank={i.rank} capped={i.rank_capped} total={i.competitors_total} />
                </td>
                <td className="py-2.5 px-3 text-right tabular-nums text-muted-foreground">
                  {i.competitors_same ? (
                    <>
                      {formatEur(i.best_same)}
                      <span className="text-[10px] ml-1 opacity-60">({i.competitors_same})</span>
                    </>
                  ) : (
                    <span title="Kein Angebot im gleichen Zustand – Preisvergleich nicht möglich">–</span>
                  )}
                </td>
                <td className="py-2.5 px-3 text-right tabular-nums text-muted-foreground">
                  {formatEur(i.market_trend)}
                </td>
                <td className="py-2.5 px-3 text-right tabular-nums">
                  {i.vs_best == null ? (
                    <span className="text-muted-foreground">–</span>
                  ) : (
                    <span className={i.vs_best < -0.1 ? "text-sky-400"
                                   : i.vs_best > 0.1 ? "text-amber-400" : "text-muted-foreground"}>
                      {i.vs_best > 0 ? "+" : ""}{(i.vs_best * 100).toFixed(0)}%
                    </span>
                  )}
                </td>
                <td className="py-2.5 pl-3">
                  <div className="flex flex-wrap gap-1">
                    {i.signals.map((sig: any, idx: number) => {
                      const cfg = SIGNAL_CONFIG[sig.kind] ?? { label: sig.kind, icon: Flame, color: "", bg: "bg-muted" }
                      const Icon = cfg.icon
                      return (
                        <span key={idx} title={sig.detail}
                          className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] ${cfg.bg} ${cfg.color}`}>
                          <Icon className="w-3 h-3" />
                          {cfg.label}
                          {sig.suggested_price ? ` → ${formatEur(sig.suggested_price)}` : ""}
                          <button onClick={() => dismiss.mutate(sig.id)}
                            className="opacity-40 hover:opacity-100 ml-0.5" title="Signal abhaken">
                            <X className="w-3 h-3" />
                          </button>
                        </span>
                      )
                    })}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {items.length === 0 && (
          <div className="py-10 text-center text-muted-foreground text-sm">
            Keine Angebote{onlySignals ? " mit offenem Signal" : ""}.
          </div>
        )}
      </div>
    </div>
  )
}
