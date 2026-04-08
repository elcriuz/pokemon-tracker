import { useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { X } from "lucide-react"

export function AddCardDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [url, setUrl] = useState("")
  const [grade, setGrade] = useState("")
  const [notes, setNotes] = useState("")
  const [error, setError] = useState("")
  const queryClient = useQueryClient()

  const mutation = useMutation({
    mutationFn: api.addCard,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["cards"] })
      queryClient.invalidateQueries({ queryKey: ["dashboard"] })
      setUrl(""); setGrade(""); setNotes(""); setError("")
      onClose()
    },
    onError: (e: Error) => setError(e.message),
  })

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div className="w-full max-w-md p-6 rounded-xl bg-card border border-border space-y-4" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">Karte hinzufuegen</h2>
          <button onClick={onClose} className="p-1 rounded hover:bg-secondary"><X className="w-4 h-4" /></button>
        </div>

        <label className="block">
          <span className="text-sm text-muted-foreground">Cardmarket URL</span>
          <input
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://www.cardmarket.com/..."
            className="mt-1 w-full px-3 py-2 rounded-lg bg-secondary border border-border text-foreground text-sm"
            autoFocus
          />
        </label>

        <label className="block">
          <span className="text-sm text-muted-foreground">Grade (optional)</span>
          <select
            value={grade}
            onChange={(e) => setGrade(e.target.value)}
            className="mt-1 w-full px-3 py-2 rounded-lg bg-secondary border border-border text-foreground text-sm"
          >
            <option value="">Ungraded</option>
            <option value="PSA10">PSA 10</option>
            <option value="PSA9">PSA 9</option>
            <option value="CGC10">CGC 10</option>
            <option value="BGS10">BGS 10</option>
          </select>
        </label>

        <label className="block">
          <span className="text-sm text-muted-foreground">Notizen (optional)</span>
          <input
            type="text"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="z.B. Japanese, German..."
            className="mt-1 w-full px-3 py-2 rounded-lg bg-secondary border border-border text-foreground text-sm"
          />
        </label>

        {error && <p className="text-sm text-negative">{error}</p>}

        <div className="flex justify-end gap-2">
          <button onClick={onClose} className="px-3 py-2 text-sm rounded-lg hover:bg-secondary">Abbrechen</button>
          <button
            onClick={() => mutation.mutate({ url, grade, notes })}
            disabled={!url || mutation.isPending}
            className="px-4 py-2 text-sm rounded-lg bg-ring text-primary-foreground hover:bg-ring/80 disabled:opacity-50"
          >
            {mutation.isPending ? "Wird hinzugefuegt..." : "Hinzufuegen"}
          </button>
        </div>
      </div>
    </div>
  )
}
