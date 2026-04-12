import { useQuery } from "@tanstack/react-query"
import { useEffect, useRef } from "react"
import { api } from "@/lib/api"
import { Terminal, X } from "lucide-react"

export function LogPanel({ onClose }: { onClose: () => void }) {
  const { data } = useQuery({
    queryKey: ["scrapeLogs"],
    queryFn: api.getScrapeLogs,
    refetchInterval: 2500,
  })
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [data?.lines?.length])

  return (
    <>
      <div className="flex items-center justify-between px-4 py-3 border-b border-border flex-shrink-0">
        <div className="flex items-center gap-2 text-sm font-medium">
          <Terminal className="w-4 h-4 text-yellow-400" />
          <span className="hidden sm:inline">Scraper Log</span>
          {data?.lines && data.lines.length > 0 && (
            <span className="flex items-center gap-1.5 ml-2">
              <span className="w-2 h-2 rounded-full bg-positive animate-pulse" />
              <span className="text-xs text-muted-foreground">Live</span>
            </span>
          )}
        </div>
        <button
          onClick={onClose}
          className="p-1.5 rounded-lg hover:bg-secondary text-muted-foreground hover:text-foreground"
        >
          <X className="w-4 h-4" />
        </button>
      </div>
      <div className="flex-1 overflow-y-auto p-4 font-mono text-xs leading-relaxed text-muted-foreground">
        {data?.lines?.length ? (
          data.lines.map((line, i) => (
            <div key={i} className={line.includes("FEHLER") || line.includes("ERROR") ? "text-negative" : line.includes("EUR") ? "text-foreground" : ""}>
              {line}
            </div>
          ))
        ) : (
          <div className="text-muted-foreground/50">Warte auf Log-Output...</div>
        )}
        <div ref={bottomRef} />
      </div>
    </>
  )
}
