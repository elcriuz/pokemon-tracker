import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import { api } from "@/lib/api"
import { formatEUR, formatPct, urlToFlag } from "@/lib/utils"
import { TrendingUp, TrendingDown, CreditCard, Plus, RefreshCw, ExternalLink, Check, ArrowUpDown } from "lucide-react"
import { useMemo, useState } from "react"
import { AddCardDialog } from "@/components/cards/AddCardDialog"
import { PortfolioChart } from "@/components/charts/PortfolioChart"

type SortKey = "name" | "value" | "trend" | "from_price" | "avg7" | "avg30" | "created_at" | "scraped_at"
type SortDir = "asc" | "desc"

const SORT_OPTIONS: { key: SortKey; label: string }[] = [
  { key: "value", label: "Wert" },
  { key: "name", label: "Name" },
  { key: "trend", label: "Trend" },
  { key: "from_price", label: "Low" },
  { key: "avg7", label: "7d Avg" },
  { key: "avg30", label: "30d Avg" },
  { key: "created_at", label: "Hinzugefuegt" },
  { key: "scraped_at", label: "Letztes Update" },
]

export function Dashboard() {
  const { data: dashboard, isLoading } = useQuery({ queryKey: ["dashboard"], queryFn: api.getDashboard })
  const { data: cards } = useQuery({ queryKey: ["cards"], queryFn: api.getCards })
  const { data: scrapeStatus } = useQuery({
    queryKey: ["scrapeStatus"],
    queryFn: api.getScrapeStatus,
    refetchInterval: (query) => query.state.data?.isRunning ? 3000 : false,
  })
  const queryClient = useQueryClient()
  const scrapeMutation = useMutation({
    mutationFn: api.triggerScrape,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["scrapeStatus"] }),
  })
  const scrapeCardsMutation = useMutation({
    mutationFn: (ids: number[]) => api.scrapeCards(ids),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["scrapeStatus"] })
      setSelected(new Set())
    },
  })
  const [showAdd, setShowAdd] = useState(false)
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [sortKey, setSortKey] = useState<SortKey>("value")
  const [sortDir, setSortDir] = useState<SortDir>("desc")

  const sortedCards = useMemo(() => {
    if (!cards) return []
    return [...cards].sort((a, b) => {
      let av = a[sortKey]
      let bv = b[sortKey]
      // Nulls to end
      if (av == null && bv == null) return 0
      if (av == null) return 1
      if (bv == null) return -1
      // String comparison for name/dates
      if (typeof av === "string" && typeof bv === "string") {
        return sortDir === "asc" ? av.localeCompare(bv) : bv.localeCompare(av)
      }
      // Number comparison
      return sortDir === "asc" ? av - bv : bv - av
    })
  }, [cards, sortKey, sortDir])

  if (isLoading) return <div className="text-muted-foreground">Laden...</div>

  const d = dashboard!
  const isUp = d.changePercent >= 0
  const isAnyScraping = scrapeStatus?.isRunning

  function toggleSelect(e: React.MouseEvent, cardId: number) {
    e.preventDefault()
    e.stopPropagation()
    setSelected((prev) => {
      const next = new Set(prev)
      next.has(cardId) ? next.delete(cardId) : next.add(cardId)
      return next
    })
  }

  function toggleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"))
    } else {
      setSortKey(key)
      setSortDir(key === "name" || key === "created_at" ? "asc" : "desc")
    }
  }

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Portfolio</h1>
          <p className="text-sm text-muted-foreground">
            Letzte Aktualisierung: {d.lastScrapeAt ? new Date(d.lastScrapeAt).toLocaleString("de-DE") : "\u2014"}
          </p>
        </div>
        <div className="flex gap-2">
          {selected.size > 0 ? (
            <button
              onClick={() => scrapeCardsMutation.mutate([...selected])}
              disabled={isAnyScraping}
              className="flex items-center gap-2 px-3 py-2 text-sm rounded-lg bg-ring text-primary-foreground hover:bg-ring/80 disabled:opacity-50 transition-colors"
            >
              <RefreshCw className={`w-4 h-4 ${isAnyScraping ? "animate-spin" : ""}`} />
              {isAnyScraping ? "Scraping..." : `${selected.size} Karten scrapen`}
            </button>
          ) : (
            <button
              onClick={() => scrapeMutation.mutate()}
              disabled={isAnyScraping}
              className="flex items-center gap-2 px-3 py-2 text-sm rounded-lg bg-secondary hover:bg-secondary/80 disabled:opacity-50 transition-colors"
            >
              <RefreshCw className={`w-4 h-4 ${isAnyScraping ? "animate-spin" : ""}`} />
              {isAnyScraping ? "Scraping..." : "Alle aktualisieren"}
            </button>
          )}
          {selected.size > 0 && (
            <button
              onClick={() => setSelected(new Set())}
              className="px-3 py-2 text-sm rounded-lg bg-secondary hover:bg-secondary/80 transition-colors"
            >
              Auswahl aufheben
            </button>
          )}
          <button
            onClick={() => setShowAdd(true)}
            className="flex items-center gap-2 px-3 py-2 text-sm rounded-lg bg-ring text-primary-foreground hover:bg-ring/80 transition-colors"
          >
            <Plus className="w-4 h-4" />
            Karte
          </button>
        </div>
      </div>

      {/* Card Gallery Grid */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold">Karten ({cards?.length || 0})</h2>
          {/* Sort Controls */}
          <div className="flex items-center gap-1 text-xs">
            <ArrowUpDown className="w-3.5 h-3.5 text-muted-foreground mr-1" />
            {SORT_OPTIONS.map((opt) => (
              <button
                key={opt.key}
                onClick={() => toggleSort(opt.key)}
                className={`px-2 py-1 rounded transition-colors ${
                  sortKey === opt.key
                    ? "bg-ring text-primary-foreground"
                    : "bg-secondary/50 text-muted-foreground hover:bg-secondary hover:text-foreground"
                }`}
              >
                {opt.label}
                {sortKey === opt.key && (sortDir === "asc" ? " \u2191" : " \u2193")}
              </button>
            ))}
          </div>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-4">
          {sortedCards.map((card: any) => (
            <div key={card.id} className="relative group">
              {/* Select Checkbox */}
              <button
                onClick={(e) => toggleSelect(e, card.id)}
                className={`absolute top-2 left-2 z-10 w-6 h-6 rounded-md border-2 flex items-center justify-center transition-all ${
                  selected.has(card.id)
                    ? "bg-ring border-ring text-primary-foreground"
                    : "border-white/30 bg-black/30 opacity-0 group-hover:opacity-100"
                }`}
              >
                {selected.has(card.id) && <Check className="w-4 h-4" />}
              </button>

              {/* Cardmarket Link */}
              <a
                href={card.url}
                target="_blank"
                rel="noopener noreferrer"
                onClick={(e) => e.stopPropagation()}
                className="absolute top-2 right-2 z-10 w-6 h-6 rounded-md bg-black/30 border border-white/20 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity hover:bg-black/60"
                title="Auf Cardmarket oeffnen"
              >
                <ExternalLink className="w-3 h-3 text-white" />
              </a>

              <Link
                to={`/cards/${card.id}`}
                className={`block rounded-xl bg-card border overflow-hidden transition-all hover:scale-[1.02] hover:shadow-lg hover:shadow-ring/10 ${
                  selected.has(card.id) ? "border-ring" : "border-border hover:border-ring/50"
                }`}
              >
                {/* Thumbnail */}
                <div className="aspect-[5/7] bg-secondary overflow-hidden">
                  {card.image ? (
                    <img src={`/images/${card.image}`} alt={card.name} className="w-full h-full object-cover" />
                  ) : (
                    <div className="w-full h-full flex items-center justify-center text-muted-foreground text-2xl">?</div>
                  )}
                </div>
                {/* Info */}
                <div className="p-3 space-y-1">
                  <div className="font-medium text-sm leading-tight truncate" title={card.name}>{card.name}</div>
                  <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                    {urlToFlag(card.url) && <span>{urlToFlag(card.url)}</span>}
                    {card.grade && <span className="px-1 py-0.5 rounded bg-yellow-500/20 text-yellow-400 text-[10px] font-medium">{card.grade}</span>}
                  </div>
                  <div className="font-bold tabular-nums text-base">{formatEUR(card.value)}</div>
                  <div className="text-xs text-muted-foreground">Low: {formatEUR(card.from_price)}</div>
                </div>
              </Link>
            </div>
          ))}
        </div>
      </div>

      {/* Stat Cards */}
      <div className={`grid grid-cols-2 gap-4 ${d.purchaseCount > 0 ? "lg:grid-cols-4" : "lg:grid-cols-3"}`}>
        <StatCard label="Gesamt-Wert" value={formatEUR(d.totalValue)} sub={`${d.cardCount} Karten (${d.gradedCount} graded)`} positive={isUp} icon={isUp ? TrendingUp : TrendingDown} />
        {d.purchaseCount > 0 && (
          <StatCard
            label="Gewinn/Verlust"
            value={`${d.totalProfit >= 0 ? "+" : ""}${formatEUR(d.totalProfit)}`}
            sub={`${formatPct(d.totalProfitPct)} auf ${d.purchaseCount}/${d.cardCount} Karten`}
            positive={d.totalProfit >= 0}
            icon={d.totalProfit >= 0 ? TrendingUp : TrendingDown}
          />
        )}
        <StatCard label="Top Gewinner" value={d.topMovers[0]?.name?.split("(")[0]?.trim() || "\u2014"} sub={d.topMovers[0] ? formatPct(d.topMovers[0].changePct) : "\u2014"} positive icon={TrendingUp} />
        <StatCard label="Top Verlierer" value={d.topMovers[d.topMovers.length - 1]?.name?.split("(")[0]?.trim() || "\u2014"} sub={d.topMovers[d.topMovers.length - 1] ? formatPct(d.topMovers[d.topMovers.length - 1].changePct) : "\u2014"} positive={false} icon={TrendingDown} />
      </div>

      {/* Portfolio Chart */}
      {d.portfolioHistory?.length > 1 && <PortfolioChart data={d.portfolioHistory} />}

      <AddCardDialog open={showAdd} onClose={() => setShowAdd(false)} />
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
