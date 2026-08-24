import { useQuery } from "@tanstack/react-query"
import { useState } from "react"
import { api } from "@/lib/api"
import { ExternalLink, ChevronDown, ChevronRight } from "lucide-react"

function formatEur(val: number | null | undefined) {
  if (val == null) return "–"
  return val >= 1000
    ? `${val.toLocaleString("de-DE", { maximumFractionDigits: 0 })}€`
    : `${val.toFixed(2)}€`
}

function monthLabel(m: string) {
  if (m === "unbekannt") return "ohne Datum"
  const [y, mo] = m.split("-")
  const names = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]
  return `${names[Number(mo) - 1]} ${y.slice(2)}`
}

/** Monatsumsätze als Balken — der Verlauf sagt mehr als eine Gesamtsumme. */
function MonthBars({ data }: { data: any[] }) {
  if (!data.length) return null
  const max = Math.max(...data.map((d) => d.revenue))
  return (
    <div className="flex items-end gap-2 h-28">
      {data.map((d) => (
        <div key={d.month} className="flex-1 flex flex-col items-center gap-1 min-w-[38px]">
          <span className="text-[10px] tabular-nums text-muted-foreground">
            {formatEur(d.revenue)}
          </span>
          <div className="w-full rounded-t bg-primary/70 hover:bg-primary transition-colors"
               style={{ height: `${Math.max(3, (d.revenue / max) * 74)}px` }}
               title={`${d.orders} Bestellungen · ${d.cards} Karten · netto ${formatEur(d.net)}`} />
          <span className="text-[10px] text-muted-foreground">{monthLabel(d.month)}</span>
        </div>
      ))}
    </div>
  )
}

function OrderRow({ order }: { order: any }) {
  const [open, setOpen] = useState(false)
  const { data } = useQuery({
    queryKey: ["sale-items", order.id],
    queryFn: () => api.getSaleItems(order.id),
    enabled: open,
  })
  const sold = order.sold_at ? String(order.sold_at).slice(0, 10).split("-").reverse().join(".") : "–"

  return (
    <>
      <tr className="border-b border-border/50 hover:bg-muted/30 cursor-pointer"
          onClick={() => setOpen(!open)}>
        <td className="py-2.5 pr-2 w-6 text-muted-foreground">
          {open ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
        </td>
        <td className="py-2.5 pr-3">
          <span className="font-medium">{order.buyer || "–"}</span>
          <div className="text-[11px] text-muted-foreground">
            {order.game} · {order.positions} Pos. · {order.cards} Karten · {order.state}
          </div>
        </td>
        <td className="py-2.5 px-3 text-right tabular-nums text-muted-foreground">{sold}</td>
        <td className="py-2.5 px-3 text-right tabular-nums font-medium">{formatEur(order.item_value)}</td>
        <td className="py-2.5 px-3 text-right tabular-nums text-muted-foreground">{formatEur(order.shipping)}</td>
        <td className="py-2.5 px-3 text-right tabular-nums text-amber-400/80">−{formatEur(order.commission)}</td>
        <td className="py-2.5 pl-3 text-right tabular-nums font-medium text-emerald-400">{formatEur(order.net)}</td>
      </tr>
      {open && (
        <tr className="bg-muted/20">
          <td />
          <td colSpan={6} className="py-2 pr-3">
            {!data ? (
              <span className="text-xs text-muted-foreground">Lade Positionen…</span>
            ) : (
              <div className="space-y-1">
                {data.items.map((it: any) => (
                  <div key={it.id} className="flex items-baseline gap-2 text-[12px]">
                    <span className="text-muted-foreground tabular-nums w-8">{it.amount}×</span>
                    <a href={it.product_url} target="_blank" rel="noreferrer"
                       className="hover:underline inline-flex items-center gap-1">
                      {it.name}
                      <ExternalLink className="w-2.5 h-2.5 opacity-40" />
                    </a>
                    <span className="text-muted-foreground">
                      {it.expansion} · {it.condition}/{it.language}
                    </span>
                    {it.comment && (
                      <span className="text-muted-foreground italic truncate max-w-[220px]">
                        „{it.comment}“
                      </span>
                    )}
                    <span className="ml-auto tabular-nums">{formatEur(it.price)}</span>
                    {it.purchase_price != null && (
                      <span className="tabular-nums text-emerald-400 w-20 text-right">
                        {formatEur(it.price - it.purchase_price)} Marge
                      </span>
                    )}
                  </div>
                ))}
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  )
}

export function Sales() {
  const [game, setGame] = useState("")
  const { data, isLoading } = useQuery({
    queryKey: ["sales", game],
    queryFn: () => api.getSales(game),
  })

  if (isLoading) return <div className="p-6 text-muted-foreground">Lade Verkäufe…</div>
  const s = data?.summary
  const orders = data?.orders ?? []

  return (
    <div className="p-6 space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">Verkäufe</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Erlös nach Cardmarket-Provision ({s?.commission_pct ?? 5} % auf den Artikelwert).
            Versandkosten sind durchlaufend — was die Post kostet, steht nicht bei Cardmarket.
          </p>
        </div>
        <div className="flex gap-6 text-right">
          <div>
            <div className="text-2xl font-semibold tabular-nums">{s?.orders ?? 0}</div>
            <div className="text-xs text-muted-foreground">Bestellungen</div>
          </div>
          <div>
            <div className="text-2xl font-semibold tabular-nums">{formatEur(s?.revenue)}</div>
            <div className="text-xs text-muted-foreground">Artikelwert</div>
          </div>
          <div>
            <div className="text-2xl font-semibold tabular-nums text-emerald-400">
              {formatEur(s?.net)}
            </div>
            <div className="text-xs text-muted-foreground">nach Provision</div>
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
      </div>

      {data?.by_month?.length ? (
        <div className="border border-border rounded p-4">
          <div className="text-xs uppercase tracking-wide text-muted-foreground mb-3">
            Artikelwert je Monat
          </div>
          <MonthBars data={data.by_month} />
        </div>
      ) : null}

      {s && s.known_cost_items < s.total_items && (
        <p className="text-xs text-muted-foreground border-l-2 border-border pl-3">
          Echte Marge nur für {s.known_cost_items} von {s.total_items} Positionen bekannt —
          für die übrigen ist kein Einkaufspreis hinterlegt (meist Doppelte, die nie im
          Portfolio standen).
        </p>
      )}

      <div className="overflow-x-auto">
        <table className="w-full text-sm min-w-[760px]">
          <thead className="text-xs uppercase tracking-wide text-muted-foreground">
            <tr className="border-b border-border">
              <th />
              <th className="text-left py-2 pr-3 font-medium">Käufer</th>
              <th className="text-right py-2 px-3 font-medium">Verkauft</th>
              <th className="text-right py-2 px-3 font-medium">Artikelwert</th>
              <th className="text-right py-2 px-3 font-medium">Versand</th>
              <th className="text-right py-2 px-3 font-medium">Provision</th>
              <th className="text-right py-2 pl-3 font-medium">Netto</th>
            </tr>
          </thead>
          <tbody>
            {orders.map((o: any) => <OrderRow key={o.id} order={o} />)}
          </tbody>
        </table>
        {orders.length === 0 && (
          <div className="py-10 text-center text-muted-foreground text-sm">
            Noch keine Verkäufe eingelesen.
          </div>
        )}
      </div>
    </div>
  )
}
