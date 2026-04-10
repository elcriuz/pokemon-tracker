import { useParams, useNavigate, Link } from "react-router-dom"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { formatEUR, urlToFlag } from "@/lib/utils"
import { ArrowLeft, Pencil, Trash2, ExternalLink, RefreshCw } from "lucide-react"
import { PriceHistoryChart } from "@/components/charts/PriceHistoryChart"
import { useState } from "react"
import { EditCardDialog } from "@/components/cards/EditCardDialog"

export function CardDetail() {
  const { id } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const cardId = Number(id)

  const { data: card, isLoading } = useQuery({ queryKey: ["card", cardId], queryFn: () => api.getCard(cardId) })
  const { data: prices } = useQuery({ queryKey: ["cardPrices", cardId], queryFn: () => api.getCardPrices(cardId) })
  const [showEdit, setShowEdit] = useState(false)

  const deleteMutation = useMutation({
    mutationFn: () => api.deleteCard(cardId),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["cards"] }); navigate("/") },
  })

  const { data: scrapeStatus } = useQuery({
    queryKey: ["scrapeStatus"],
    queryFn: api.getScrapeStatus,
    refetchInterval: (query) => query.state.data?.isRunning ? 3000 : false,
  })
  const scrapeMutation = useMutation({
    mutationFn: () => api.scrapeCards([cardId]),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["scrapeStatus"] })
      // Refetch card data when scrape finishes
      const poll = setInterval(async () => {
        const status = await api.getScrapeStatus()
        if (!status.isRunning) {
          clearInterval(poll)
          queryClient.invalidateQueries({ queryKey: ["card", cardId] })
          queryClient.invalidateQueries({ queryKey: ["cardPrices", cardId] })
        }
      }, 3000)
    },
  })

  if (isLoading) return <div className="text-muted-foreground">Laden...</div>
  if (!card) return <div>Karte nicht gefunden</div>

  const qty = card.quantity || 1
  const profitPerCard = card.purchase_price && card.value ? card.value - card.purchase_price : null
  const profit = profitPerCard != null ? profitPerCard * qty : null
  const profitPct = card.purchase_price && card.value ? ((card.value - card.purchase_price) / card.purchase_price) * 100 : null
  const isProfitable = profit != null && profit >= 0

  const priceFields = [
    { label: "Wert", value: card.value, highlight: true },
    { label: "Kaufpreis", value: card.purchase_price },
    { label: "Trend", value: card.trend },
    { label: "7-Tage Avg", value: card.avg7 },
    { label: "30-Tage Avg", value: card.avg30 },
    { label: "Ab (Low)", value: card.from_price },
  ]

  const gradedFields = [
    card.psa10_low && { label: "PSA 10", value: card.psa10_low },
    card.psa9_low && { label: "PSA 9", value: card.psa9_low },
    card.cgc10_low && { label: "CGC 10", value: card.cgc10_low },
    card.bgs10_low && { label: "BGS 10", value: card.bgs10_low },
  ].filter(Boolean)

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Link to="/" className="p-2 rounded-lg hover:bg-secondary"><ArrowLeft className="w-5 h-5" /></Link>
          {card.image && <img src={`/images/${card.image}`} alt="" className="w-16 h-22 object-cover rounded-lg" />}
          <div>
            <h1 className="text-2xl font-bold">{card.name}</h1>
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              {card.set_name && <span className="px-1.5 py-0.5 rounded text-xs bg-secondary">{card.set_name}</span>}
              {card.grade && <span className="px-1.5 py-0.5 rounded text-xs bg-yellow-500/20 text-yellow-400">{card.grade}</span>}
              {(card.quantity || 1) > 1 && <span className="px-1.5 py-0.5 rounded text-xs bg-ring/20 text-ring">x{card.quantity}</span>}
              {card.binder_name && (
                <span className="flex items-center gap-1 px-1.5 py-0.5 rounded text-xs" style={{ backgroundColor: card.binder_color + "30", color: card.binder_color }}>
                  <span className="w-2 h-2 rounded-full" style={{ backgroundColor: card.binder_color }} />
                  {card.binder_name}
                </span>
              )}
              {urlToFlag(card.url) && <span>{urlToFlag(card.url)}</span>}
              {card.notes && <span>{card.notes}</span>}
            </div>
          </div>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => scrapeMutation.mutate()}
            disabled={scrapeStatus?.isRunning}
            className="flex items-center gap-2 px-3 py-2 text-sm rounded-lg bg-secondary hover:bg-secondary/80 disabled:opacity-50 transition-colors"
            title="Preis aktualisieren"
          >
            <RefreshCw className={`w-4 h-4 ${scrapeStatus?.isRunning ? "animate-spin" : ""}`} />
            {scrapeStatus?.isRunning ? "Scraping..." : "Aktualisieren"}
          </button>
          <a
            href={card.url}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-2 px-3 py-2 text-sm rounded-lg bg-secondary hover:bg-secondary/80 transition-colors"
            title="Auf Cardmarket oeffnen"
          >
            <ExternalLink className="w-4 h-4" /> Cardmarket
          </a>
          <button onClick={() => setShowEdit(true)} className="p-2 rounded-lg hover:bg-secondary" title="Bearbeiten"><Pencil className="w-4 h-4" /></button>
          <button
            onClick={() => { if (confirm("Karte wirklich loeschen?")) deleteMutation.mutate() }}
            className="p-2 rounded-lg hover:bg-destructive/20 text-destructive"
            title="Loeschen"
          >
            <Trash2 className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Price Grid */}
      <div className="grid grid-cols-3 lg:grid-cols-6 gap-3">
        {priceFields.map((f) => (
          <div key={f.label} className={`p-3 rounded-lg border border-border ${f.highlight ? "bg-ring/10 border-ring/30" : "bg-card"}`}>
            <div className="text-xs text-muted-foreground">{f.label}</div>
            <div className="text-lg font-bold tabular-nums">{formatEUR(f.value)}</div>
          </div>
        ))}
      </div>

      {/* Profit/Loss */}
      {profit != null && (
        <div className={`p-4 rounded-lg border ${isProfitable ? "bg-positive/10 border-positive/30" : "bg-negative/10 border-negative/30"}`}>
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium">{isProfitable ? "Gewinn" : "Verlust"}</span>
            <div className="text-right">
              <span className={`text-xl font-bold ${isProfitable ? "text-positive" : "text-negative"}`}>
                {isProfitable ? "+" : ""}{formatEUR(profit)}
              </span>
              <span className={`ml-2 text-sm ${isProfitable ? "text-positive" : "text-negative"}`}>
                ({isProfitable ? "+" : ""}{profitPct!.toFixed(1)}%)
              </span>
            </div>
          </div>
          {card.purchase_date && (
            <div className="text-xs text-muted-foreground mt-1">
              Gekauft am {new Date(card.purchase_date).toLocaleDateString("de-DE")}
            </div>
          )}
        </div>
      )}

      {/* Graded Prices */}
      {gradedFields.length > 0 && (
        <div>
          <h2 className="text-sm font-semibold text-muted-foreground mb-2">Graded Preise (guenstigster)</h2>
          <div className="flex gap-3">
            {gradedFields.map((f: any) => (
              <div key={f.label} className="px-4 py-2 rounded-lg bg-yellow-500/10 border border-yellow-500/20">
                <div className="text-xs text-yellow-400">{f.label}</div>
                <div className="font-bold tabular-nums">{formatEUR(f.value)}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Price History Chart */}
      {prices && prices.length > 0 && <PriceHistoryChart data={prices} />}

      {/* Last scraped */}
      <p className="text-xs text-muted-foreground">
        Zuletzt gescrapt: {card.scraped_at ? new Date(card.scraped_at).toLocaleString("de-DE") : "—"}
      </p>

      <EditCardDialog card={card} open={showEdit} onClose={() => setShowEdit(false)} />
    </div>
  )
}
