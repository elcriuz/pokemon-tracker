import { useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api"

const VIA_LABELS: Record<string, string> = {
  quick_cm: "Quick CM",
  ximilar: "Ximilar",
  tcg_api: "TCG+GPT4o",
}

const VIA_COLORS: Record<string, string> = {
  quick_cm: "bg-green-500/20 text-green-400",
  ximilar: "bg-blue-500/20 text-blue-400",
  tcg_api: "bg-amber-500/20 text-amber-400",
}

function formatTime(iso: string) {
  if (!iso) return "-"
  const d = new Date(iso + "Z")
  return d.toLocaleString("de-AT", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })
}

function formatEur(val: number | null) {
  if (val == null) return "-"
  return val >= 1000 ? `${val.toLocaleString("de-DE", { maximumFractionDigits: 0 })}` : val.toFixed(2) + "\u20ac"
}

export function ScanLog() {
  const { data, isLoading } = useQuery({ queryKey: ["scans"], queryFn: () => api.getScans(200) })
  const scans = data?.scans || []
  const stats = data?.stats || {}

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-6">
      <h1 className="text-2xl font-bold">Scan Activity</h1>

      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        {[
          { label: "Total Scans", value: stats.total || 0 },
          { label: "Users", value: stats.users || 0 },
          { label: "Avg Duration", value: stats.avg_duration ? `${stats.avg_duration}s` : "-" },
          { label: "Quick CM", value: stats.quick_cm || 0 },
          { label: "Ximilar", value: stats.ximilar || 0 },
        ].map(({ label, value }) => (
          <div key={label} className="bg-card border border-border rounded-lg p-3 text-center">
            <div className="text-2xl font-bold">{value}</div>
            <div className="text-xs text-muted-foreground">{label}</div>
          </div>
        ))}
      </div>

      {/* Table */}
      {isLoading ? (
        <div className="text-muted-foreground">Laden...</div>
      ) : scans.length === 0 ? (
        <div className="text-muted-foreground">Noch keine Scans.</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-muted-foreground">
                <th className="py-2 pr-3">Zeit</th>
                <th className="py-2 pr-3">User</th>
                <th className="py-2 pr-3">Karte</th>
                <th className="py-2 pr-3">Set</th>
                <th className="py-2 pr-3 text-right">Preis</th>
                <th className="py-2 pr-3 text-right">Dauer</th>
                <th className="py-2">Via</th>
              </tr>
            </thead>
            <tbody>
              {scans.map((s: any) => (
                <tr key={s.id} className="border-b border-border/50 hover:bg-secondary/30">
                  <td className="py-2 pr-3 text-muted-foreground whitespace-nowrap">{formatTime(s.scanned_at)}</td>
                  <td className="py-2 pr-3">{s.user_name}</td>
                  <td className="py-2 pr-3 font-medium">
                    {s.cm_url ? (
                      <a href={s.cm_url} target="_blank" rel="noopener" className="hover:text-blue-400 transition-colors">
                        {s.card_name || "?"} {s.number ? `#${s.number}` : ""}
                      </a>
                    ) : (
                      <>{s.card_name || "?"} {s.number ? `#${s.number}` : ""}</>
                    )}
                  </td>
                  <td className="py-2 pr-3 text-muted-foreground">{s.set_name || "-"}</td>
                  <td className="py-2 pr-3 text-right">{formatEur(s.market_eur)}</td>
                  <td className="py-2 pr-3 text-right text-muted-foreground">{s.duration_sec ? `${s.duration_sec}s` : "-"}</td>
                  <td className="py-2">
                    <span className={`text-xs px-2 py-0.5 rounded-full ${VIA_COLORS[s.via] || "bg-secondary text-muted-foreground"}`}>
                      {VIA_LABELS[s.via] || s.via || "?"}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
