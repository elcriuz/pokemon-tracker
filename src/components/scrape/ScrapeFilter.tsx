import { useMemo, useState } from "react"
import { Filter, RefreshCw, CheckSquare, Square, Clock, Euro, Layers } from "lucide-react"
import { timeAgo } from "@/lib/utils"

type FilterMode = "age" | "value" | "binder" | "all"

const AGE_OPTIONS = [
  { label: "Nie gescrapt", hours: -1 },
  { label: "> 24h", hours: 24 },
  { label: "> 12h", hours: 12 },
  { label: "> 6h", hours: 6 },
  { label: "> 1h", hours: 1 },
]

const VALUE_OPTIONS = [
  { label: "Alle Werte", min: 0 },
  { label: "> 500 EUR", min: 500 },
  { label: "> 200 EUR", min: 200 },
  { label: "> 100 EUR", min: 100 },
  { label: "> 50 EUR", min: 50 },
  { label: "< 50 EUR", min: -50 },
]

export function ScrapeFilter({
  cards,
  binders,
  selected,
  setSelected,
  isAnyScraping,
  onScrape,
}: {
  cards: any[]
  binders: any[]
  selected: Set<number>
  setSelected: (s: Set<number>) => void
  isAnyScraping: boolean
  onScrape: (ids: number[]) => void
}) {
  const [open, setOpen] = useState(false)
  const [mode, setMode] = useState<FilterMode>("age")
  const [ageHours, setAgeHours] = useState(24)
  const [minValue, setMinValue] = useState(0)
  const [filterBinder, setFilterBinder] = useState<string>("")

  const filtered = useMemo(() => {
    if (!cards) return []
    return cards.filter((c) => {
      // Age filter
      if (mode === "age" || mode === "all") {
        if (ageHours === -1) {
          if (c.scraped_at) return mode !== "age"
        } else {
          const scraped = c.scraped_at ? new Date(c.scraped_at.endsWith("Z") ? c.scraped_at : c.scraped_at + "Z").getTime() : 0
          const hoursAgo = (Date.now() - scraped) / 3600000
          if (mode === "age" && hoursAgo < ageHours && c.scraped_at) return false
        }
      }
      // Value filter
      if (mode === "value" || mode === "all") {
        const val = c.value || 0
        if (minValue === -50) {
          if (mode === "value" && val >= 50) return false
        } else if (mode === "value" && val < minValue) return false
      }
      // Binder filter
      if (mode === "binder" && filterBinder) {
        if (filterBinder === "none" && c.binder_id != null) return false
        if (filterBinder !== "none" && String(c.binder_id) !== filterBinder) return false
      }
      return true
    })
  }, [cards, mode, ageHours, minValue, filterBinder])

  function applyFilter() {
    setSelected(new Set(filtered.map((c) => c.id)))
  }

  function selectAll() {
    setSelected(new Set(cards.map((c: any) => c.id)))
  }

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="flex items-center gap-2 px-3 py-2 text-sm rounded-lg bg-secondary hover:bg-secondary/80 transition-colors"
      >
        <Filter className="w-4 h-4" />
        Smart Scrape
      </button>
    )
  }

  return (
    <div className="p-4 rounded-lg bg-card border border-border space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold flex items-center gap-2">
          <Filter className="w-4 h-4" />
          Smart Scrape Filter
        </h3>
        <button onClick={() => { setOpen(false); setSelected(new Set()) }} className="text-xs text-muted-foreground hover:text-foreground">
          Schliessen
        </button>
      </div>

      {/* Filter Mode Tabs */}
      <div className="flex gap-2">
        {[
          { key: "age" as FilterMode, icon: Clock, label: "Nach Alter" },
          { key: "value" as FilterMode, icon: Euro, label: "Nach Wert" },
          { key: "binder" as FilterMode, icon: Layers, label: "Nach Binder" },
        ].map(({ key, icon: Icon, label }) => (
          <button
            key={key}
            onClick={() => setMode(key)}
            className={`flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg transition-colors ${
              mode === key ? "bg-ring text-primary-foreground" : "bg-secondary text-muted-foreground hover:text-foreground"
            }`}
          >
            <Icon className="w-3.5 h-3.5" />
            {label}
          </button>
        ))}
      </div>

      {/* Filter Options */}
      <div className="flex flex-wrap gap-2">
        {mode === "age" && AGE_OPTIONS.map((opt) => (
          <button
            key={opt.hours}
            onClick={() => setAgeHours(opt.hours)}
            className={`px-3 py-1.5 text-xs rounded-lg transition-colors ${
              ageHours === opt.hours ? "bg-ring text-primary-foreground" : "bg-secondary/50 text-muted-foreground hover:bg-secondary"
            }`}
          >
            {opt.label}
          </button>
        ))}
        {mode === "value" && VALUE_OPTIONS.map((opt) => (
          <button
            key={opt.min}
            onClick={() => setMinValue(opt.min)}
            className={`px-3 py-1.5 text-xs rounded-lg transition-colors ${
              minValue === opt.min ? "bg-ring text-primary-foreground" : "bg-secondary/50 text-muted-foreground hover:bg-secondary"
            }`}
          >
            {opt.label}
          </button>
        ))}
        {mode === "binder" && (
          <>
            <button
              onClick={() => setFilterBinder("")}
              className={`px-3 py-1.5 text-xs rounded-lg transition-colors ${
                !filterBinder ? "bg-ring text-primary-foreground" : "bg-secondary/50 text-muted-foreground"
              }`}
            >
              Alle
            </button>
            {binders?.map((b: any) => (
              <button
                key={b.id}
                onClick={() => setFilterBinder(String(b.id))}
                className={`flex items-center gap-1 px-3 py-1.5 text-xs rounded-lg transition-colors ${
                  filterBinder === String(b.id) ? "text-white" : "bg-secondary/50 text-muted-foreground"
                }`}
                style={filterBinder === String(b.id) ? { backgroundColor: b.color } : undefined}
              >
                <span className="w-2 h-2 rounded-full" style={{ backgroundColor: b.color }} />
                {b.name}
              </button>
            ))}
            <button
              onClick={() => setFilterBinder("none")}
              className={`px-3 py-1.5 text-xs rounded-lg transition-colors ${
                filterBinder === "none" ? "bg-ring text-primary-foreground" : "bg-secondary/50 text-muted-foreground"
              }`}
            >
              Unsortiert
            </button>
          </>
        )}
      </div>

      {/* Result + Actions */}
      <div className="flex items-center justify-between pt-2 border-t border-border">
        <div className="text-sm text-muted-foreground">
          <span className="text-foreground font-medium">{filtered.length}</span> von {cards.length} Karten
          {selected.size > 0 && selected.size !== filtered.length && (
            <span> ({selected.size} ausgewaehlt)</span>
          )}
        </div>
        <div className="flex gap-2">
          <button
            onClick={applyFilter}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg bg-secondary hover:bg-secondary/80 transition-colors"
          >
            <CheckSquare className="w-3.5 h-3.5" />
            {filtered.length} auswaehlen
          </button>
          <button
            onClick={selectAll}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg bg-secondary hover:bg-secondary/80 transition-colors"
          >
            Alle
          </button>
          <button
            onClick={() => { applyFilter(); onScrape(filtered.map((c) => c.id)) }}
            disabled={filtered.length === 0 || isAnyScraping}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg bg-ring text-primary-foreground hover:bg-ring/80 disabled:opacity-50 transition-colors"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isAnyScraping ? "animate-spin" : ""}`} />
            {isAnyScraping ? "Scraping..." : `${filtered.length} scrapen`}
          </button>
        </div>
      </div>
    </div>
  )
}
