import { useEffect, useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { formatEUR } from "@/lib/utils"
import { X, Tag } from "lucide-react"

type SoldCard = {
  id: number
  name: string
  value?: number | null
  quantity?: number | null
  purchase_price?: number | null
  image?: string | null
}

const heute = () => new Date().toISOString().slice(0, 10)

/**
 * Markiert eine oder mehrere Karten als verkauft. Der Erlös wird je Karte
 * erfasst — vorbelegt wird nichts, damit kein Marktwert als Verkaufspreis
 * ins Ergebnis rutscht; "Marktwerte übernehmen" fuellt auf Wunsch alles.
 */
export function MarkSoldDialog({
  cards,
  open,
  onClose,
  onDone,
}: {
  cards: SoldCard[]
  open: boolean
  onClose: () => void
  onDone?: () => void
}) {
  const queryClient = useQueryClient()
  const [soldAt, setSoldAt] = useState(heute())
  const [preise, setPreise] = useState<Record<number, string>>({})
  const [fehler, setFehler] = useState<string | null>(null)

  useEffect(() => {
    if (open) {
      setSoldAt(heute())
      setPreise({})
      setFehler(null)
    }
  }, [open, cards.length])

  const mutation = useMutation({
    mutationFn: () =>
      api.markSoldBulk({
        sold_at: soldAt,
        items: cards.map((c) => ({
          id: c.id,
          sold_price: preise[c.id]?.trim() ? Number(preise[c.id].replace(",", ".")) : null,
        })),
      }),
    onSuccess: () => {
      for (const key of ["cards", "dashboard", "binders", "cardshop", "actions"]) {
        queryClient.invalidateQueries({ queryKey: [key] })
      }
      for (const c of cards) queryClient.invalidateQueries({ queryKey: ["card", c.id] })
      onDone?.()
      onClose()
    },
    onError: (e: any) => setFehler(e?.message || "Markieren fehlgeschlagen"),
  })

  if (!open) return null

  const marktwert = (c: SoldCard) => (c.value || 0) * (c.quantity || 1)
  const erloes = cards.reduce((s, c) => {
    const v = preise[c.id]?.trim() ? Number(preise[c.id].replace(",", ".")) : NaN
    return s + (Number.isFinite(v) ? v : 0)
  }, 0)
  const einkauf = cards.reduce((s, c) => s + (c.purchase_price || 0) * (c.quantity || 1), 0)
  const ergebnis = erloes - einkauf
  const ungueltig = cards.some((c) => {
    const raw = preise[c.id]?.trim()
    return !!raw && !Number.isFinite(Number(raw.replace(",", ".")))
  })

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div
        className="w-full max-w-md max-h-[85vh] flex flex-col p-6 rounded-xl bg-card border border-border space-y-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Tag className="w-5 h-5 text-ring" />
            <h2 className="font-semibold">
              {cards.length === 1 ? "Als verkauft markieren" : `${cards.length} Karten als verkauft markieren`}
            </h2>
          </div>
          <button onClick={onClose} className="p-1 rounded hover:bg-secondary text-muted-foreground">
            <X className="w-4 h-4" />
          </button>
        </div>

        <p className="text-xs text-muted-foreground">
          Die Karten verschwinden aus dem Portfolio und werden nicht mehr gescrapt. Preisverlauf und
          Kaufpreis bleiben erhalten — zu sehen unter „Verkauft" im Portfolio.
        </p>

        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">Verkaufsdatum</label>
          <input
            type="date"
            value={soldAt}
            onChange={(e) => setSoldAt(e.target.value)}
            className="w-full px-3 py-2 text-sm rounded-lg bg-secondary border border-border focus:outline-none focus:border-ring"
          />
        </div>

        <div className="flex items-center justify-between">
          <label className="text-xs text-muted-foreground">Erlös je Karte (optional)</label>
          <button
            onClick={() =>
              setPreise(Object.fromEntries(cards.map((c) => [c.id, marktwert(c) ? String(marktwert(c)) : ""])))
            }
            className="text-xs px-2 py-1 rounded bg-secondary hover:bg-secondary/80 text-muted-foreground hover:text-foreground transition-colors"
          >
            Marktwerte übernehmen
          </button>
        </div>

        <div className="flex-1 overflow-y-auto space-y-2 -mx-1 px-1">
          {cards.map((c) => (
            <div key={c.id} className="flex items-center gap-2">
              {c.image ? (
                <img src={`/images/${c.image}`} alt="" className="w-7 h-10 object-cover rounded flex-shrink-0" />
              ) : (
                <div className="w-7 h-10 rounded bg-secondary flex-shrink-0" />
              )}
              <div className="min-w-0 flex-1">
                <div className="text-sm truncate" title={c.name}>
                  {c.name}
                  {(c.quantity || 1) > 1 && (
                    <span className="ml-1 px-1 py-0.5 rounded bg-ring/20 text-ring text-[10px] font-bold">
                      x{c.quantity}
                    </span>
                  )}
                </div>
                <div className="text-[11px] text-muted-foreground">
                  Marktwert {formatEUR(marktwert(c))}
                  {c.purchase_price ? ` · Kauf ${formatEUR(c.purchase_price * (c.quantity || 1))}` : ""}
                </div>
              </div>
              <div className="relative flex-shrink-0">
                <input
                  type="text"
                  inputMode="decimal"
                  value={preise[c.id] ?? ""}
                  onChange={(e) => setPreise((p) => ({ ...p, [c.id]: e.target.value }))}
                  placeholder="—"
                  className="w-24 pl-2 pr-6 py-1.5 text-sm text-right tabular-nums rounded-lg bg-secondary border border-border focus:outline-none focus:border-ring"
                />
                <span className="absolute right-2 top-1/2 -translate-y-1/2 text-xs text-muted-foreground">
                  &euro;
                </span>
              </div>
            </div>
          ))}
        </div>

        {erloes > 0 && (
          <div className="text-xs space-y-0.5 pt-2 border-t border-border">
            <div className="flex justify-between">
              <span className="text-muted-foreground">Erlös</span>
              <span className="tabular-nums font-medium">{formatEUR(erloes)}</span>
            </div>
            {einkauf > 0 && (
              <>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Kaufpreis</span>
                  <span className="tabular-nums">{formatEUR(einkauf)}</span>
                </div>
                <div className="flex justify-between font-medium">
                  <span>Ergebnis</span>
                  <span className={`tabular-nums ${ergebnis >= 0 ? "text-positive" : "text-negative"}`}>
                    {ergebnis >= 0 ? "+" : ""}
                    {formatEUR(ergebnis)}
                  </span>
                </div>
              </>
            )}
          </div>
        )}

        {(cards.some((c) => (c.quantity || 1) > 1)) && (
          <p className="text-[11px] text-muted-foreground">
            Bei Karten mit Stückzahl gilt der ganze Posten als verkauft. Hast du nur einen Teil
            verkauft, vorher die Menge im Bearbeiten-Dialog verringern.
          </p>
        )}

        {fehler && (
          <div className="text-sm px-3 py-2 rounded-lg bg-destructive/15 text-destructive border border-destructive/30">
            {fehler}
          </div>
        )}

        <div className="flex justify-end gap-2">
          <button
            onClick={onClose}
            className="px-3 py-2 text-sm rounded-lg bg-secondary hover:bg-secondary/80 transition-colors"
          >
            Abbrechen
          </button>
          <button
            onClick={() => mutation.mutate()}
            disabled={mutation.isPending || ungueltig || !soldAt}
            className="px-3 py-2 text-sm rounded-lg bg-ring text-primary-foreground hover:bg-ring/80 disabled:opacity-50 transition-colors"
          >
            {mutation.isPending ? "Speichern..." : "Verkauft"}
          </button>
        </div>
      </div>
    </div>
  )
}
