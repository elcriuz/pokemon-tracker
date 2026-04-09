import { useState, useEffect } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { X } from "lucide-react"

export function EditCardDialog({ card, open, onClose }: { card: any; open: boolean; onClose: () => void }) {
  const [name, setName] = useState("")
  const [grade, setGrade] = useState("")
  const [notes, setNotes] = useState("")
  const [purchasePrice, setPurchasePrice] = useState("")
  const [purchaseDate, setPurchaseDate] = useState("")
  const [quantity, setQuantity] = useState("1")
  const [binderId, setBinderId] = useState("")
  const queryClient = useQueryClient()
  const { data: binders } = useQuery({ queryKey: ["binders"], queryFn: api.getBinders })

  useEffect(() => {
    if (card) {
      setName(card.name)
      setGrade(card.grade)
      setNotes(card.notes || "")
      setPurchasePrice(card.purchase_price != null ? String(card.purchase_price) : "")
      setPurchaseDate(card.purchase_date || "")
      setQuantity(String(card.quantity || 1))
      setBinderId(card.binder_id != null ? String(card.binder_id) : "")
    }
  }, [card])

  const mutation = useMutation({
    mutationFn: (data: any) => api.updateCard(card.id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["card", card.id] })
      queryClient.invalidateQueries({ queryKey: ["cards"] })
      queryClient.invalidateQueries({ queryKey: ["dashboard"] })
      queryClient.invalidateQueries({ queryKey: ["binders"] })
      onClose()
    },
  })

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div className="w-full max-w-md p-6 rounded-xl bg-card border border-border space-y-4" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">Karte bearbeiten</h2>
          <button onClick={onClose} className="p-1 rounded hover:bg-secondary"><X className="w-4 h-4" /></button>
        </div>

        <label className="block">
          <span className="text-sm text-muted-foreground">Name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} className="mt-1 w-full px-3 py-2 rounded-lg bg-secondary border border-border text-foreground text-sm" />
        </label>

        <div className="grid grid-cols-2 gap-3">
          <label className="block">
            <span className="text-sm text-muted-foreground">Grade</span>
            <select value={grade} onChange={(e) => setGrade(e.target.value)} className="mt-1 w-full px-3 py-2 rounded-lg bg-secondary border border-border text-foreground text-sm">
              <option value="">Ungraded</option>
              <option value="PSA10">PSA 10</option>
              <option value="PSA9">PSA 9</option>
              <option value="CGC10">CGC 10</option>
              <option value="BGS10">BGS 10</option>
            </select>
          </label>
          <label className="block">
            <span className="text-sm text-muted-foreground">Anzahl</span>
            <input
              type="number"
              min="1"
              value={quantity}
              onChange={(e) => setQuantity(e.target.value)}
              className="mt-1 w-full px-3 py-2 rounded-lg bg-secondary border border-border text-foreground text-sm"
            />
          </label>
        </div>

        <label className="block">
          <span className="text-sm text-muted-foreground">Binder</span>
          <select value={binderId} onChange={(e) => setBinderId(e.target.value)} className="mt-1 w-full px-3 py-2 rounded-lg bg-secondary border border-border text-foreground text-sm">
            <option value="">Kein Binder</option>
            {binders?.map((b: any) => (
              <option key={b.id} value={b.id}>{b.name}</option>
            ))}
          </select>
        </label>

        <div className="grid grid-cols-2 gap-3">
          <label className="block">
            <span className="text-sm text-muted-foreground">Kaufpreis (EUR)</span>
            <input
              type="number"
              step="0.01"
              value={purchasePrice}
              onChange={(e) => setPurchasePrice(e.target.value)}
              placeholder="0.00"
              className="mt-1 w-full px-3 py-2 rounded-lg bg-secondary border border-border text-foreground text-sm"
            />
          </label>
          <label className="block">
            <span className="text-sm text-muted-foreground">Kaufdatum</span>
            <input
              type="date"
              value={purchaseDate}
              onChange={(e) => setPurchaseDate(e.target.value)}
              className="mt-1 w-full px-3 py-2 rounded-lg bg-secondary border border-border text-foreground text-sm"
            />
          </label>
        </div>

        <label className="block">
          <span className="text-sm text-muted-foreground">Notizen</span>
          <input value={notes} onChange={(e) => setNotes(e.target.value)} className="mt-1 w-full px-3 py-2 rounded-lg bg-secondary border border-border text-foreground text-sm" />
        </label>

        <div className="flex justify-end gap-2">
          <button onClick={onClose} className="px-3 py-2 text-sm rounded-lg hover:bg-secondary">Abbrechen</button>
          <button
            onClick={() => mutation.mutate({
              name, grade, notes,
              purchase_price: purchasePrice ? parseFloat(purchasePrice) : null,
              purchase_date: purchaseDate || null,
              quantity: parseInt(quantity) || 1,
              binder_id: binderId ? parseInt(binderId) : null,
            })}
            disabled={mutation.isPending}
            className="px-4 py-2 text-sm rounded-lg bg-ring text-primary-foreground hover:bg-ring/80 disabled:opacity-50"
          >
            Speichern
          </button>
        </div>
      </div>
    </div>
  )
}
