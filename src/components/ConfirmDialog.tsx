import { AlertTriangle, X } from "lucide-react"

/**
 * Bestaetigung als echtes Overlay statt window.confirm(): Chrome kann native
 * Dialoge für eine Seite dauerhaft sperren ("Verhindern, dass diese Seite
 * weitere Dialoge erzeugt") — dann liefert confirm() stumm false und der
 * Klick tut scheinbar nichts.
 */
export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = "Löschen",
  busy = false,
  error,
  onConfirm,
  onClose,
}: {
  open: boolean
  title: string
  message: React.ReactNode
  confirmLabel?: string
  busy?: boolean
  error?: string | null
  onConfirm: () => void
  onClose: () => void
}) {
  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div
        className="w-full max-w-sm p-6 rounded-xl bg-card border border-border space-y-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-center gap-2">
            <AlertTriangle className="w-5 h-5 text-destructive" />
            <h2 className="font-semibold">{title}</h2>
          </div>
          <button onClick={onClose} className="p-1 rounded hover:bg-secondary text-muted-foreground">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="text-sm text-muted-foreground">{message}</div>

        {error && (
          <div className="text-sm px-3 py-2 rounded-lg bg-destructive/15 text-destructive border border-destructive/30">
            {error}
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
            onClick={onConfirm}
            disabled={busy}
            autoFocus
            className="px-3 py-2 text-sm rounded-lg bg-destructive text-white hover:bg-destructive/80 disabled:opacity-50 transition-colors"
          >
            {busy ? "Läuft …" : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
