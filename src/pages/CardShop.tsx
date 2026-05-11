import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { api } from "@/lib/api"
import { Link } from "react-router-dom"
import { Lock, Unlock, TrendingDown } from "lucide-react"

const REC_CONFIG: Record<string, { label: string; color: string; bg: string }> = {
  SELL_NOW: { label: "SELL NOW", color: "text-green-400", bg: "bg-green-500/20" },
  GOOD: { label: "GOOD", color: "text-emerald-400", bg: "bg-emerald-500/15" },
  HOLD: { label: "HOLD", color: "text-gray-400", bg: "bg-gray-500/15" },
  AVOID: { label: "AVOID", color: "text-red-400", bg: "bg-red-500/20" },
}

function formatEur(val: number | null) {
  if (val == null) return "-"
  return val >= 1000
    ? `${val.toLocaleString("de-DE", { maximumFractionDigits: 0 })}\u20ac`
    : `${val.toFixed(2)}\u20ac`
}

function TrendBadge({ ratio, label }: { ratio: number | null; label: string }) {
  if (ratio == null) return <span className="text-muted-foreground text-xs">-</span>
  const pct = ((ratio - 1) * 100)
  const isUp = pct > 0
  const color = pct > 5 ? "text-green-400" : pct > 0 ? "text-emerald-400" : pct > -5 ? "text-gray-400" : "text-red-400"
  return (
    <div className="text-right">
      <span className={`text-sm font-medium ${color}`}>
        {isUp ? "\u2191" : "\u2193"}{Math.abs(pct).toFixed(1)}%
      </span>
      <div className="text-[10px] text-muted-foreground">{label}</div>
    </div>
  )
}

type FilterType = "ALL" | "SELL_NOW" | "GOOD" | "HOLD" | "AVOID"

export function CardShop() {
  const queryClient = useQueryClient()
  const [showStash, setShowStash] = useState(false)
  const [showLosers, setShowLosers] = useState(false)
  const { data, isLoading } = useQuery({
    queryKey: ["cardshop", showStash, showLosers],
    queryFn: () => api.getCardShop(showStash, showLosers),
    refetchInterval: 60_000,
  })
  const [filter, setFilter] = useState<FilterType>("ALL")
  const stashMutation = useMutation({
    mutationFn: (id: number) => api.toggleStash(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["cardshop"] }),
  })

  const summary = data?.summary || {}
  const allCards = data?.cards || []
  const cards = filter === "ALL" ? allCards : allCards.filter((c: any) => c.recommendation === filter)

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-6">
      <h1 className="text-2xl font-bold">Card Shop</h1>
      <p className="text-sm text-muted-foreground">Shop zahlt 80% vom CM-Tiefstpreis. Verkaufe wenn die Karte gerade oben ist.</p>

      {/* Summary */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <div className="bg-card border border-border rounded-lg p-4 text-center">
          <div className="text-2xl font-bold">{formatEur(summary.totalShopValue)}</div>
          <div className="text-xs text-muted-foreground">Shop Wert (80%)</div>
        </div>
        <div className="bg-card border border-border rounded-lg p-4 text-center">
          <div className="text-2xl font-bold">{formatEur(summary.totalMarketValue)}</div>
          <div className="text-xs text-muted-foreground">Marktwert</div>
        </div>
        <div className="bg-card border border-border rounded-lg p-4 text-center">
          <div className={`text-2xl font-bold ${summary.totalProfit > 0 ? "text-green-400" : summary.totalProfit < 0 ? "text-red-400" : ""}`}>
            {summary.totalProfit != null ? (summary.totalProfit >= 0 ? "+" : "") + formatEur(summary.totalProfit) : "-"}
          </div>
          <div className="text-xs text-muted-foreground">Gewinn ggü. Kauf</div>
        </div>
        <div className="bg-card border border-border rounded-lg p-4 text-center">
          <div className="text-2xl font-bold">{summary.cardCount || 0}</div>
          <div className="text-xs text-muted-foreground">Karten</div>
        </div>
        <div className="bg-card border border-border rounded-lg p-4 text-center">
          <div className={`text-2xl font-bold ${summary.sellNowCount > 0 ? "text-green-400" : ""}`}>
            {summary.sellNowCount || 0}
          </div>
          <div className="text-xs text-muted-foreground">SELL NOW</div>
        </div>
      </div>

      {/* Filter */}
      <div className="flex gap-2 items-center">
        {(["ALL", "SELL_NOW", "GOOD", "HOLD", "AVOID"] as FilterType[]).map((f) => {
          const count = f === "ALL" ? allCards.length : allCards.filter((c: any) => c.recommendation === f).length
          const active = filter === f
          const cfg = f !== "ALL" ? REC_CONFIG[f] : null
          return (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                active ? "bg-secondary text-foreground" : "text-muted-foreground hover:text-foreground hover:bg-secondary/50"
              }`}
            >
              {f === "ALL" ? "Alle" : cfg?.label} ({count})
            </button>
          )
        })}
        <div className="ml-auto flex gap-2">
          <button
            onClick={() => setShowLosers((v) => !v)}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors flex items-center gap-1 ${
              showLosers ? "bg-secondary text-foreground" : "text-muted-foreground hover:text-foreground hover:bg-secondary/50"
            }`}
            title={showLosers ? "Auch Karten mit Verlust werden gezeigt" : `${summary.hiddenLosers || 0} Karten mit Verlust ausgeblendet`}
          >
            <TrendingDown className="w-3 h-3" /> Verlust {showLosers ? "an" : "aus"}
            {!showLosers && summary.hiddenLosers > 0 && (
              <span className="ml-1 text-muted-foreground">({summary.hiddenLosers})</span>
            )}
          </button>
          <button
            onClick={() => setShowStash((v) => !v)}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors flex items-center gap-1 ${
              showStash ? "bg-secondary text-foreground" : "text-muted-foreground hover:text-foreground hover:bg-secondary/50"
            }`}
          >
            <Lock className="w-3 h-3" /> Stash {showStash ? "an" : "aus"}
          </button>
        </div>
      </div>

      {/* Table */}
      {isLoading ? (
        <div className="text-muted-foreground">Laden...</div>
      ) : cards.length === 0 ? (
        <div className="text-muted-foreground">Keine Karten{filter !== "ALL" ? ` mit ${filter}` : ""}.</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-muted-foreground">
                <th className="py-2 pr-3">Karte</th>
                <th className="py-2 pr-3 text-right">CM Preis</th>
                <th className="py-2 pr-3 text-right">Shop (80%)</th>
                <th className="py-2 pr-3 text-right">7d</th>
                <th className="py-2 pr-3 text-right">30d</th>
                <th className="py-2 pr-3 text-center">Signal</th>
                <th className="py-2 pr-3 text-right">Gewinn</th>
                <th className="py-2 w-8"></th>
              </tr>
            </thead>
            <tbody>
              {cards.map((c: any) => {
                const rec = c.recommendation ? REC_CONFIG[c.recommendation] : null
                return (
                  <tr key={c.id} className="border-b border-border/50 hover:bg-secondary/30">
                    <td className="py-2.5 pr-3">
                      <Link to={`/cards/${c.id}`} className="flex items-center gap-2 hover:text-blue-400 transition-colors">
                        {c.image && (
                          <img src={`/images/${c.image}`} alt="" className="w-8 h-11 rounded object-cover" />
                        )}
                        <div>
                          <div className="font-medium">{c.name}{c.quantity > 1 ? ` (${c.quantity}x)` : ""}</div>
                          {c.grade && <div className="text-[10px] text-muted-foreground">{c.grade}</div>}
                        </div>
                      </Link>
                    </td>
                    <td className="py-2.5 pr-3 text-right">{formatEur(c.from_price)}</td>
                    <td className="py-2.5 pr-3 text-right font-medium">{formatEur(c.shop_price)}</td>
                    <td className="py-2.5 pr-3"><TrendBadge ratio={c.ratio_7d} label={c.isGraded ? "vs prev" : "vs 7d"} /></td>
                    <td className="py-2.5 pr-3"><TrendBadge ratio={c.ratio_30d} label="vs 30d" /></td>
                    <td className="py-2.5 pr-3 text-center">
                      {rec ? (
                        <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${rec.bg} ${rec.color}`}>
                          {rec.label}
                        </span>
                      ) : (
                        <span className="text-xs text-muted-foreground">-</span>
                      )}
                    </td>
                    <td className="py-2.5 pr-3 text-right">
                      {c.profit_if_sold != null ? (
                        <span className={c.profit_if_sold >= 0 ? "text-green-400" : "text-red-400"}>
                          {c.profit_if_sold >= 0 ? "+" : ""}{formatEur(c.profit_if_sold)}
                        </span>
                      ) : (
                        <span className="text-muted-foreground">-</span>
                      )}
                    </td>
                    <td className="py-2.5">
                      <button
                        onClick={() => stashMutation.mutate(c.id)}
                        title={c.stash ? "Aus Stash entfernen" : "In Stash (nicht verkaufen)"}
                        className="text-muted-foreground hover:text-foreground transition-colors"
                      >
                        {c.stash ? <Lock className="w-3.5 h-3.5" /> : <Unlock className="w-3.5 h-3.5 opacity-30 hover:opacity-100" />}
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
