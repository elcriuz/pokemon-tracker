import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import { api } from "@/lib/api"
import { formatEUR, urlToFlag, timeAgo } from "@/lib/utils"
import { Plus, RefreshCw, ExternalLink, Check, ArrowUpDown, Filter, Square, ImageOff } from "lucide-react"
import { useMemo, useState } from "react"
import { AddCardDialog } from "@/components/cards/AddCardDialog"
import { ScrapeFilter } from "@/components/scrape/ScrapeFilter"

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
  const [activeBinder, setActiveBinder] = useState<string | undefined>(undefined)
  const { data: binders } = useQuery({ queryKey: ["binders"], queryFn: api.getBinders })
  const { data: dashboard, isLoading } = useQuery({
    queryKey: ["dashboard", activeBinder],
    queryFn: () => api.getDashboard(activeBinder),
  })
  const [wasRunning, setWasRunning] = useState(false)
  const { data: scrapeStatus } = useQuery({
    queryKey: ["scrapeStatus"],
    queryFn: async () => {
      const status = await api.getScrapeStatus()
      // Detect transition: was running → now done
      if (wasRunning && !status.isRunning) {
        setTimeout(() => {
          queryClient.invalidateQueries({ queryKey: ["cards"] })
          queryClient.invalidateQueries({ queryKey: ["dashboard"] })
          queryClient.invalidateQueries({ queryKey: ["binders"] })
        }, 500)
      }
      setWasRunning(status.isRunning)
      return status
    },
    refetchInterval: (query) => query.state.data?.isRunning ? 3000 : 30_000,
  })
  const queryClient = useQueryClient()
  const { data: cards } = useQuery({
    queryKey: ["cards", activeBinder],
    queryFn: () => api.getCards(activeBinder),
    refetchInterval: scrapeStatus?.isRunning ? 5_000 : 60_000,
  })

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
  const stopMutation = useMutation({
    mutationFn: api.stopScrape,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["scrapeStatus"] }),
  })
  const [showAdd, setShowAdd] = useState(false)
  const [showScrapeFilter, setShowScrapeFilter] = useState(false)
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [sortKey, setSortKey] = useState<SortKey>("value")
  const [sortDir, setSortDir] = useState<SortDir>("desc")
  const [lastSelectedId, setLastSelectedId] = useState<number | null>(null)

  const sortedCards = useMemo(() => {
    if (!cards) return []
    return [...cards].sort((a, b) => {
      let av = a[sortKey]
      let bv = b[sortKey]
      if (av == null && bv == null) return 0
      if (av == null) return 1
      if (bv == null) return -1
      if (typeof av === "string" && typeof bv === "string") {
        return sortDir === "asc" ? av.localeCompare(bv) : bv.localeCompare(av)
      }
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

    if (e.shiftKey && lastSelectedId != null && sortedCards.length > 0) {
      // Shift+Click: select range
      const ids = sortedCards.map((c: any) => c.id)
      const fromIdx = ids.indexOf(lastSelectedId)
      const toIdx = ids.indexOf(cardId)
      if (fromIdx !== -1 && toIdx !== -1) {
        const start = Math.min(fromIdx, toIdx)
        const end = Math.max(fromIdx, toIdx)
        setSelected((prev) => {
          const next = new Set(prev)
          for (let i = start; i <= end; i++) next.add(ids[i])
          return next
        })
        setLastSelectedId(cardId)
        return
      }
    }

    setSelected((prev) => {
      const next = new Set(prev)
      next.has(cardId) ? next.delete(cardId) : next.add(cardId)
      return next
    })
    setLastSelectedId(cardId)
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
            {isAnyScraping ? (
              <span className="flex items-center gap-2">
                <RefreshCw className="w-3 h-3 animate-spin" />
                Scraping... {scrapeStatus?.progress || 0}/{scrapeStatus?.total || "?"} Karten
                <button
                  onClick={() => stopMutation.mutate()}
                  className="ml-1 px-1.5 py-0.5 rounded bg-negative/20 text-negative text-xs hover:bg-negative/30 flex items-center gap-1"
                >
                  <Square className="w-2.5 h-2.5 fill-current" /> Stop
                </button>
              </span>
            ) : (
              <>Letzte Aktualisierung: {d.lastScrapeAt ? new Date(d.lastScrapeAt).toLocaleString("de-DE") : "\u2014"}</>
            )}
          </p>
        </div>
        <div className="flex gap-2">
          {selected.size > 0 && (
            <>
              <button
                onClick={() => scrapeCardsMutation.mutate([...selected])}
                disabled={isAnyScraping}
                className="flex items-center gap-2 px-3 py-2 text-sm rounded-lg bg-ring text-primary-foreground hover:bg-ring/80 disabled:opacity-50 transition-colors"
              >
                <RefreshCw className={`w-4 h-4 ${isAnyScraping ? "animate-spin" : ""}`} />
                {isAnyScraping ? "Scraping..." : `${selected.size} scrapen`}
              </button>
              <button
                onClick={() => setSelected(new Set())}
                className="px-3 py-2 text-sm rounded-lg bg-secondary hover:bg-secondary/80 transition-colors"
              >
                Aufheben
              </button>
            </>
          )}
          <button
            onClick={() => setShowScrapeFilter((v) => !v)}
            className={`flex items-center gap-2 px-3 py-2 text-sm rounded-lg transition-colors ${
              showScrapeFilter ? "bg-yellow-500/20 text-yellow-400" : "bg-secondary hover:bg-secondary/80"
            }`}
          >
            <Filter className="w-4 h-4" />
            Smart Scrape
          </button>
          <button
            onClick={() => setShowAdd(true)}
            className="flex items-center gap-2 px-3 py-2 text-sm rounded-lg bg-ring text-primary-foreground hover:bg-ring/80 transition-colors"
          >
            <Plus className="w-4 h-4" />
            Karte
          </button>
        </div>
      </div>

      {/* Binder Filter */}
      {binders && binders.length > 0 && (
        <div className="flex gap-2 flex-wrap">
          <button
            onClick={() => setActiveBinder(undefined)}
            className={`px-3 py-1.5 text-sm rounded-full transition-colors ${
              !activeBinder ? "bg-ring text-primary-foreground" : "bg-secondary text-muted-foreground hover:text-foreground"
            }`}
          >
            Alle ({d.cardCount})
          </button>
          {binders.map((b: any) => (
            <button
              key={b.id}
              onClick={() => setActiveBinder(activeBinder === String(b.id) ? undefined : String(b.id))}
              className={`flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-full transition-colors ${
                activeBinder === String(b.id) ? "text-white" : "bg-secondary text-muted-foreground hover:text-foreground"
              }`}
              style={activeBinder === String(b.id) ? { backgroundColor: b.color } : undefined}
            >
              <span className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: b.color }} />
              {b.name} ({b.card_count})
            </button>
          ))}
          <button
            onClick={() => setActiveBinder(activeBinder === "none" ? undefined : "none")}
            className={`px-3 py-1.5 text-sm rounded-full transition-colors ${
              activeBinder === "none" ? "bg-ring text-primary-foreground" : "bg-secondary text-muted-foreground hover:text-foreground"
            }`}
          >
            Unsortiert
          </button>
        </div>
      )}

      {/* Smart Scrape Filter */}
      <ScrapeFilter
        open={showScrapeFilter}
        onClose={() => setShowScrapeFilter(false)}
        cards={cards || []}
        binders={binders || []}
        selected={selected}
        setSelected={setSelected}
        isAnyScraping={!!isAnyScraping}
        onScrape={(ids) => scrapeCardsMutation.mutate(ids)}
      />

      {/* Card Gallery Grid */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold">Karten ({cards?.length || 0})</h2>
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
          {sortedCards.map((card: any) => {
            const qty = card.quantity || 1
            const totalVal = (card.value || 0) * qty
            return (
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

                {/* Binder Color Dot */}
                {card.binder_color && (
                  <span
                    className="absolute bottom-[calc(100%-theme(spacing.1))] left-2 z-10 w-3 h-3 rounded-full border-2 border-card"
                    style={{ backgroundColor: card.binder_color, bottom: "auto", top: "auto" }}
                    title={card.binder_name}
                  />
                )}

                <Link
                  to={`/cards/${card.id}`}
                  className={`block rounded-xl bg-card border overflow-hidden transition-all hover:scale-[1.02] hover:shadow-lg hover:shadow-ring/10 ${
                    selected.has(card.id) ? "border-ring" : "border-border hover:border-ring/50"
                  }`}
                >
                  {/* Thumbnail */}
                  <div className="relative aspect-[5/7] bg-secondary overflow-hidden">
                    {card.image ? (
                      <img src={`/images/${card.image}`} alt={card.name} className="w-full h-full object-cover" />
                    ) : (
                      <div className="w-full h-full flex items-center justify-center text-muted-foreground text-2xl">?</div>
                    )}
                    {/* Binder strip at bottom of image */}
                    {card.binder_color && (
                      <div className="absolute bottom-0 left-0 right-0 h-1" style={{ backgroundColor: card.binder_color }} />
                    )}
                  </div>
                  {/* Info */}
                  <div className="p-3 space-y-1">
                    <div className="font-medium text-sm leading-tight truncate flex items-center gap-1" title={card.name}>
                      {card.name}
                      {qty > 1 && <span className="flex-shrink-0 px-1 py-0.5 rounded bg-ring/20 text-ring text-[10px] font-bold">x{qty}</span>}
                    </div>
                    <div className="flex items-center gap-1.5 text-xs text-muted-foreground overflow-hidden">
                      {urlToFlag(card.url) && <span className="flex-shrink-0">{urlToFlag(card.url)}</span>}
                      {card.set_name && <span className="truncate">{card.set_name}</span>}
                      {card.grade && <span className="flex-shrink-0 px-1 py-0.5 rounded bg-yellow-500/20 text-yellow-400 text-[10px] font-medium">{card.grade}</span>}
                    </div>
                    <div className="font-bold tabular-nums text-base flex items-center gap-1.5">
                      {formatEUR(totalVal)}
                      {card.prev_value != null && card.value != null && card.value !== card.prev_value && (() => {
                        const diff = card.value - card.prev_value
                        const pct = (diff / card.prev_value) * 100
                        const up = diff > 0
                        return (
                          <span className={`text-[10px] font-medium px-1 py-0.5 rounded ${up ? "bg-positive/20 text-positive" : "bg-negative/20 text-negative"}`}>
                            {up ? "+" : ""}{pct.toFixed(1)}%
                          </span>
                        )
                      })()}
                    </div>
                    <div className="text-xs text-muted-foreground flex justify-between">
                      <span>
                        {qty > 1
                          ? `${qty}x ${formatEUR(card.value)}`
                          : card.trend && card.trend !== card.value
                            ? `Trend: ${formatEUR(card.trend)}`
                            : ""
                        }
                      </span>
                      {card.scraped_at && <span title={new Date(card.scraped_at).toLocaleString("de-DE")}>{timeAgo(card.scraped_at)}</span>}
                    </div>
                  </div>
                </Link>
              </div>
            )
          })}
        </div>
      </div>

      <AddCardDialog open={showAdd} onClose={() => setShowAdd(false)} />
    </div>
  )
}
