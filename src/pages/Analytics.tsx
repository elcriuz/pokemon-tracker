import { useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { formatEUR, formatPct } from "@/lib/utils"
import { TrendingUp, TrendingDown, CreditCard } from "lucide-react"
import { PortfolioChart } from "@/components/charts/PortfolioChart"

export function Analytics() {
  const { data: dashboard, isLoading } = useQuery({ queryKey: ["dashboard"], queryFn: () => api.getDashboard() })

  if (isLoading) return <div className="text-muted-foreground">Laden...</div>

  const d = dashboard!
  const isUp = d.changePercent >= 0

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      <h1 className="text-2xl font-bold">Analytics</h1>

      {/* Stat Cards */}
      <div className={`grid grid-cols-2 gap-4 ${d.purchaseCount > 0 ? "lg:grid-cols-4" : "lg:grid-cols-3"}`}>
        <StatCard label="Gesamt-Wert" value={formatEUR(d.totalValue)} sub={`${d.cardCount} Karten (${d.gradedCount} graded)`} positive={isUp} icon={isUp ? TrendingUp : TrendingDown} />
        {d.purchaseCount > 0 && (
          <StatCard
            label="Gewinn/Verlust"
            value={`${d.totalProfit >= 0 ? "+" : ""}${formatEUR(d.totalProfit)}`}
            sub={`${formatPct(d.totalProfitPct)} auf ${d.purchaseCount}/${d.uniqueCardCount} Karten`}
            positive={d.totalProfit >= 0}
            icon={d.totalProfit >= 0 ? TrendingUp : TrendingDown}
          />
        )}
        <StatCard label="Top Gewinner" value={d.topMovers[0]?.name?.split("(")[0]?.trim() || "\u2014"} sub={d.topMovers[0] ? formatPct(d.topMovers[0].changePct) : "\u2014"} positive icon={TrendingUp} />
        <StatCard label="Top Verlierer" value={d.topMovers[d.topMovers.length - 1]?.name?.split("(")[0]?.trim() || "\u2014"} sub={d.topMovers[d.topMovers.length - 1] ? formatPct(d.topMovers[d.topMovers.length - 1].changePct) : "\u2014"} positive={false} icon={TrendingDown} />
      </div>

      {/* Portfolio Chart */}
      {d.portfolioHistory?.length > 1 && <PortfolioChart data={d.portfolioHistory} />}

      {/* Top Movers */}
      {d.topMovers?.length > 0 && (
        <div>
          <h2 className="text-lg font-semibold mb-3">Top Movers</h2>
          <div className="grid gap-2">
            {d.topMovers.map((card: any) => {
              const change = card.changePct
              const isCardUp = change >= 0
              return (
                <div key={card.id} className="flex items-center justify-between p-3 rounded-lg bg-card border border-border">
                  <div>
                    <span className="font-medium text-sm">{card.name}</span>
                    {card.grade && <span className="ml-2 px-1 py-0.5 rounded text-[10px] bg-yellow-500/20 text-yellow-400">{card.grade}</span>}
                  </div>
                  <div className="flex items-center gap-3">
                    <span className="font-semibold tabular-nums">{formatEUR(card.value)}</span>
                    <span className={`text-sm font-medium ${isCardUp ? "text-positive" : "text-negative"}`}>
                      {formatPct(change)}
                    </span>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

function StatCard({ label, value, sub, positive, icon: Icon }: {
  label: string; value: string; sub?: string; positive?: boolean; icon: any
}) {
  return (
    <div className="p-4 rounded-lg bg-card border border-border">
      <div className="flex items-center justify-between mb-2">
        <span className="text-sm text-muted-foreground">{label}</span>
        <Icon className={`w-4 h-4 ${positive === true ? "text-positive" : positive === false ? "text-negative" : "text-muted-foreground"}`} />
      </div>
      <div className="text-xl font-bold truncate">{value}</div>
      {sub && <div className={`text-sm ${positive === true ? "text-positive" : positive === false ? "text-negative" : "text-muted-foreground"}`}>{sub}</div>}
    </div>
  )
}
