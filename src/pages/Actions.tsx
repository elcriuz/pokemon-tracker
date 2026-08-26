import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { api } from "@/lib/api"
import {
  TrendingUp, TrendingDown, Flame, Swords, Banknote, Snowflake,
  ShoppingCart, ExternalLink, X, Check, Loader2, Play,
} from "lucide-react"

const KIND: Record<string, { icon: any; color: string; bg: string }> = {
  raise:       { icon: TrendingUp,   color: "text-emerald-400", bg: "bg-emerald-500/15" },
  underpriced: { icon: Banknote,     color: "text-sky-400",     bg: "bg-sky-500/15" },
  sell_now:    { icon: Flame,        color: "text-orange-400",  bg: "bg-orange-500/20" },
  lower:       { icon: TrendingDown, color: "text-amber-400",   bg: "bg-amber-500/15" },
  overpriced:  { icon: Snowflake,    color: "text-violet-400",  bg: "bg-violet-500/15" },
  undercut:    { icon: Swords,       color: "text-red-400",     bg: "bg-red-500/15" },
  buy:         { icon: ShoppingCart, color: "text-emerald-400", bg: "bg-emerald-500/15" },
}

function eur(v: number | null | undefined, digits = 2) {
  if (v == null) return "–"
  return v >= 1000
    ? `${v.toLocaleString("de-DE", { maximumFractionDigits: 0 })}€`
    : `${v.toFixed(digits)}€`
}

/** Wie viel Geld bei dieser Bewegung im Spiel ist, relativ zur größten der Liste. */
function StakeBar({ value, max, direction }: { value: number; max: number; direction: string }) {
  const pct = max > 0 ? Math.max(2, (value / max) * 100) : 0
  const color = direction === "down" ? "bg-amber-400/70"
    : direction === "buy" ? "bg-emerald-400/70" : "bg-sky-400/70"
  return (
    <div className="flex items-center gap-2">
      <div className="w-16 h-1 rounded-full bg-muted overflow-hidden">
        <div className={`h-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="tabular-nums text-xs w-14 text-right">{eur(value)}</span>
    </div>
  )
}

export function Actions() {
  const queryClient = useQueryClient()
  const [kind, setKind] = useState("")
  const [msg, setMsg] = useState("")
  const [error, setError] = useState("")

  const { data, isLoading } = useQuery({
    queryKey: ["actions", kind],
    queryFn: () => api.getActions(kind),
    refetchInterval: 60_000,
  })

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["actions"] })
    queryClient.invalidateQueries({ queryKey: ["offers"] })
  }

  const enqueue = useMutation({
    mutationFn: (v: { listingId: number; price: number; signalId: number }) =>
      api.queuePrice(v.listingId, v.price, v.signalId),
    onSuccess: () => { setError(""); refresh() },
    onError: (e: any) => setError(e?.message ?? "Konnte nicht vorgemerkt werden"),
  })
  const dequeue = useMutation({
    mutationFn: (listingId: number) => api.unqueuePrice(listingId),
    onSuccess: refresh,
  })
  const dismiss = useMutation({
    mutationFn: (id: number) => api.dismissSignal(id),
    onSuccess: refresh,
  })
  const runQueue = useMutation({
    mutationFn: () => api.runRepriceQueue(),
    onSuccess: (r: any) => {
      setError(r.blocked ? r.message : "")
      setMsg(r.blocked ? `${r.changed} geändert, ${r.remaining} warten noch.`
                       : `${r.changed} Preise geändert.`)
      refresh()
    },
    onError: (e: any) => setError(e?.message ?? "Durchlauf fehlgeschlagen"),
  })

  if (isLoading) return <div className="p-6 text-muted-foreground">Lade Empfehlungen…</div>

  const items = data?.items ?? []
  const s = data?.summary
  const max = Math.max(1, ...items.map((i: any) => i.stake))

  return (
    <div className="p-6 space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">Was zu tun ist</h1>
          <p className="text-sm text-muted-foreground mt-1 max-w-2xl">
            Alle Empfehlungen zusammen, sortiert nach dem Betrag, um den es geht —
            nicht nach Signalart. <strong>+106 % auf eine 40-€-Karte</strong> wiegt
            schwerer als dasselbe auf eine für zwei Euro.
          </p>
        </div>
        <div className="flex gap-6 text-right">
          <div>
            <div className="text-2xl font-semibold tabular-nums text-sky-400">
              {eur(s?.stake_up, 0)}
            </div>
            <div className="text-xs text-muted-foreground">liegen brach</div>
          </div>
          <div>
            <div className="text-2xl font-semibold tabular-nums text-amber-400">
              {eur(s?.stake_down, 0)}
            </div>
            <div className="text-xs text-muted-foreground">blockiert Verkauf</div>
          </div>
          <div>
            <div className="text-2xl font-semibold tabular-nums">{s?.count ?? 0}</div>
            <div className="text-xs text-muted-foreground">Empfehlungen</div>
          </div>
        </div>
      </div>

      {error && <div className="text-sm text-amber-400 border-l-2 border-amber-400/60 pl-3">{error}</div>}
      {msg && !error && <div className="text-sm text-emerald-400 border-l-2 border-emerald-400/60 pl-3">{msg}</div>}

      {(s?.queued ?? 0) > 0 && (
        <div className="border border-emerald-500/30 bg-emerald-500/5 rounded p-3 flex flex-wrap items-center gap-3">
          <span className="text-sm">
            <strong>{s.queued}</strong>{" "}
            {s.queued === 1 ? "Preisänderung vorgemerkt" : "Preisänderungen vorgemerkt"}
          </span>
          <button onClick={() => runQueue.mutate()} disabled={runQueue.isPending}
            className="ml-auto px-3 py-1.5 rounded bg-emerald-600 text-white text-sm inline-flex items-center gap-1.5 disabled:opacity-50">
            {runQueue.isPending
              ? <><Loader2 className="w-3.5 h-3.5 animate-spin" /> läuft…</>
              : <><Play className="w-3.5 h-3.5" /> Alle ausführen</>}
          </button>
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        <button onClick={() => setKind("")}
          className={`px-3 py-1.5 rounded text-sm ${!kind ? "bg-primary text-primary-foreground" : "bg-muted"}`}>
          Alle ({s?.count ?? 0})
        </button>
        {(s?.kinds ?? []).map((k: string) => {
          const cfg = KIND[k]
          const Icon = cfg?.icon ?? Flame
          const n = items.filter((i: any) => i.kind === k).length
          return (
            <button key={k} onClick={() => setKind(kind === k ? "" : k)}
              className={`px-3 py-1.5 rounded text-sm inline-flex items-center gap-1.5 ${
                kind === k ? "bg-primary text-primary-foreground" : "bg-muted"}`}>
              <Icon className="w-3.5 h-3.5" />
              {items.find((i: any) => i.kind === k)?.label ?? k}
              {!kind && <span className="opacity-60">{n}</span>}
            </button>
          )
        })}
      </div>

      <div className="space-y-1.5">
        {items.map((i: any) => {
          const cfg = KIND[i.kind] ?? { icon: Flame, color: "", bg: "bg-muted" }
          const Icon = cfg.icon
          return (
            <div key={`${i.type}-${i.signal_id}`}
              className="border border-border rounded px-3 py-2.5 hover:bg-muted/20 flex flex-wrap items-center gap-x-4 gap-y-2">
              <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] shrink-0 ${cfg.bg} ${cfg.color}`}>
                <Icon className="w-3 h-3" /> {i.label}
              </span>

              <div className="min-w-[220px] flex-1">
                <a href={i.url} target="_blank" rel="noreferrer"
                   className="hover:underline inline-flex items-center gap-1 text-sm">
                  {i.name}
                  <ExternalLink className="w-3 h-3 opacity-40" />
                </a>
                <div className="text-[11px] text-muted-foreground">{i.subtitle}</div>
              </div>

              <div className="text-sm tabular-nums whitespace-nowrap">
                {eur(i.current)}
                {i.suggested != null && (
                  <>
                    <span className="text-muted-foreground mx-1">→</span>
                    <strong className={i.direction === "down" ? "text-amber-400" : "text-emerald-400"}>
                      {eur(i.suggested)}
                    </strong>
                  </>
                )}
                {i.quantity > 1 && <span className="text-muted-foreground text-xs"> ×{i.quantity}</span>}
              </div>

              <StakeBar value={i.stake} max={max} direction={i.direction} />

              <div className="text-[11px] text-muted-foreground basis-full md:basis-auto md:flex-1 md:min-w-[240px]">
                {i.detail}
              </div>

              <div className="flex items-center gap-2 shrink-0">
                {i.type === "sell" && i.suggested != null && (
                  <button
                    onClick={() => i.queued
                      ? dequeue.mutate(i.listing_id)
                      : enqueue.mutate({ listingId: i.listing_id, price: i.suggested, signalId: i.signal_id })}
                    className={`text-xs px-2 py-1 rounded inline-flex items-center gap-1 ${
                      i.queued ? "bg-emerald-500/20 text-emerald-400" : "bg-muted hover:bg-muted/70"}`}
                    title={i.queued ? "Vormerkung zurücknehmen" : "Für den nächsten Durchlauf vormerken"}>
                    <Check className="w-3 h-3" /> {i.queued ? "vorgemerkt" : "vormerken"}
                  </button>
                )}
                <button onClick={() => dismiss.mutate(i.signal_id)}
                  className="opacity-30 hover:opacity-100" title="Erledigt / ignorieren">
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
          )
        })}
      </div>

      {items.length === 0 && (
        <div className="py-12 text-center text-muted-foreground text-sm">
          Nichts zu tun — keine offenen Empfehlungen.
        </div>
      )}
    </div>
  )
}
