import { useMemo, useState } from "react"
import { Filter, RefreshCw, CheckSquare, Clock, Euro, Layers } from "lucide-react"

const AGE_OPTIONS = [
  { label: "Alle", hours: 0 },
  { label: "Nie gescrapt", hours: -1 },
  { label: "> 24h", hours: 24 },
  { label: "> 12h", hours: 12 },
  { label: "> 6h", hours: 6 },
  { label: "> 1h", hours: 1 },
]

const VALUE_OPTIONS = [
  { label: "Alle", min: 0 },
  { label: "> 500", min: 500 },
  { label: "> 200", min: 200 },
  { label: "> 100", min: 100 },
  { label: "> 50", min: 50 },
  { label: "< 50", min: -50 },
]

export function ScrapeFilter({
  open,
  onClose,
  cards,
  binders,
  selected,
  setSelected,
  isAnyScraping,
  onScrape,
}: {
  open: boolean
  onClose: () => void
  cards: any[]
  binders: any[]
  selected: Set<number>
  setSelected: (s: Set<number>) => void
  isAnyScraping: boolean
  onScrape: (ids: number[]) => void
}) {
  const [ageHours, setAgeHours] = useState(0)
  const [minValue, setMinValue] = useState(0)
  const [filterBinder, setFilterBinder] = useState<string>("")

  const filtered = useMemo(() => {
    if (!cards) return []
    return cards.filter((c) => {
      // Age filter (AND)
      if (ageHours !== 0) {
        if (ageHours === -1) { if (c.scraped_at) return false }
        else {
          const scraped = c.scraped_at ? new Date(c.scraped_at.endsWith("Z") ? c.scraped_at : c.scraped_at + "Z").getTime() : 0
          const hoursAgo = (Date.now() - scraped) / 3600000
          if (hoursAgo < ageHours && c.scraped_at) return false
        }
      }
      // Value filter (AND)
      if (minValue !== 0) {
        const val = c.value || 0
        if (minValue === -50) { if (val >= 50) return false }
        else if (val < minValue) return false
      }
      // Binder filter (AND)
      if (filterBinder) {
        if (filterBinder === "none" && c.binder_id != null) return false
        if (filterBinder !== "none" && String(c.binder_id) !== filterBinder) return false
      }
      return true
    })
  }, [cards, ageHours, minValue, filterBinder])

  function applyFilter() {
    setSelected(new Set(filtered.map((c) => c.id)))
  }

  if (!open) return null

  return (
    <div className="p-4 rounded-lg bg-card border border-border space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold flex items-center gap-2">
          <Filter className="w-4 h-4" />
          Smart Scrape
        </h3>
        <button onClick={() => { onClose(); setSelected(new Set()) }} className="text-xs text-muted-foreground hover:text-foreground">
          Schliessen
        </button>
      </div>

      {/* All filters in one row */}
      <div className="flex flex-wrap gap-4">
        {/* Age */}
        <div className="space-y-1">
          <div className="flex items-center gap-1 text-[10px] text-muted-foreground font-medium"><Clock className="w-3 h-3" /> Alter</div>
          <div className="flex gap-1">
            {AGE_OPTIONS.map((opt) => (
              <button key={opt.hours} onClick={() => setAgeHours(opt.hours)}
                className={`px-2 py-1 text-xs rounded transition-colors ${ageHours === opt.hours ? "bg-ring text-primary-foreground" : "bg-secondary/50 text-muted-foreground hover:bg-secondary"}`}>
                {opt.label}
              </button>
            ))}
          </div>
        </div>

        {/* Value */}
        <div className="space-y-1">
          <div className="flex items-center gap-1 text-[10px] text-muted-foreground font-medium"><Euro className="w-3 h-3" /> Wert</div>
          <div className="flex gap-1">
            {VALUE_OPTIONS.map((opt) => (
              <button key={opt.min} onClick={() => setMinValue(opt.min)}
                className={`px-2 py-1 text-xs rounded transition-colors ${minValue === opt.min ? "bg-ring text-primary-foreground" : "bg-secondary/50 text-muted-foreground hover:bg-secondary"}`}>
                {opt.label}
              </button>
            ))}
          </div>
        </div>

        {/* Binder */}
        {binders && binders.length > 0 && (
          <div className="space-y-1">
            <div className="flex items-center gap-1 text-[10px] text-muted-foreground font-medium"><Layers className="w-3 h-3" /> Binder</div>
            <div className="flex gap-1">
              <button onClick={() => setFilterBinder("")}
                className={`px-2 py-1 text-xs rounded transition-colors ${!filterBinder ? "bg-ring text-primary-foreground" : "bg-secondary/50 text-muted-foreground"}`}>
                Alle
              </button>
              {binders.map((b: any) => (
                <button key={b.id} onClick={() => setFilterBinder(String(b.id))}
                  className={`flex items-center gap-1 px-2 py-1 text-xs rounded transition-colors ${filterBinder === String(b.id) ? "text-white" : "bg-secondary/50 text-muted-foreground"}`}
                  style={filterBinder === String(b.id) ? { backgroundColor: b.color } : undefined}>
                  <span className="w-2 h-2 rounded-full" style={{ backgroundColor: b.color }} />
                  {b.name}
                </button>
              ))}
              <button onClick={() => setFilterBinder("none")}
                className={`px-2 py-1 text-xs rounded transition-colors ${filterBinder === "none" ? "bg-ring text-primary-foreground" : "bg-secondary/50 text-muted-foreground"}`}>
                Unsortiert
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Result + Actions */}
      <div className="flex items-center justify-between pt-2 border-t border-border">
        <div className="text-sm text-muted-foreground">
          <span className="text-foreground font-medium">{filtered.length}</span> von {cards.length} Karten
        </div>
        <div className="flex gap-2">
          <button onClick={applyFilter}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg bg-secondary hover:bg-secondary/80 transition-colors">
            <CheckSquare className="w-3.5 h-3.5" />
            {filtered.length} auswaehlen
          </button>
          <button onClick={() => setSelected(new Set(cards.map((c: any) => c.id)))}
            className="px-3 py-1.5 text-xs rounded-lg bg-secondary hover:bg-secondary/80 transition-colors">
            Alle
          </button>
          <button onClick={() => { applyFilter(); onScrape(filtered.map((c) => c.id)) }}
            disabled={filtered.length === 0 || isAnyScraping}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg bg-ring text-primary-foreground hover:bg-ring/80 disabled:opacity-50 transition-colors">
            <RefreshCw className={`w-3.5 h-3.5 ${isAnyScraping ? "animate-spin" : ""}`} />
            {isAnyScraping ? "Scraping..." : `${filtered.length} scrapen`}
          </button>
        </div>
      </div>
    </div>
  )
}
