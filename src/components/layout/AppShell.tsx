import { Link, useLocation } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import { LayoutDashboard, BarChart3, Settings, Zap, Monitor, Terminal, X, ScanSearch, Store, Tags, Eye, Receipt } from "lucide-react"
import { useState, useEffect } from "react"
import { api } from "@/lib/api"
import { LogPanel } from "@/components/scrape/LogPanel"
import type { ReactNode } from "react"

const NAV = [
  { to: "/", icon: LayoutDashboard, label: "Karten" },
  { to: "/analytics", icon: BarChart3, label: "Analytics" },
  { to: "/scans", icon: ScanSearch, label: "Scans" },
  { to: "/cardshop", icon: Store, label: "Card Shop" },
  { to: "/offers", icon: Tags, label: "Meine Angebote" },
  { to: "/watchlist", icon: Eye, label: "Wunschliste" },
  { to: "/sales", icon: Receipt, label: "Verkäufe" },
  { to: "/settings", icon: Settings, label: "Settings" },
]

export function AppShell({ children }: { children: ReactNode }) {
  const location = useLocation()
  const { data: scrapeStatus } = useQuery({
    queryKey: ["scrapeStatus"],
    queryFn: api.getScrapeStatus,
    refetchInterval: (query) => query.state.data?.isRunning ? 3000 : 30_000,
  })
  const [showPanel, setShowPanel] = useState(false)
  const [manualClose, setManualClose] = useState(false)
  const [panelEngine, setPanelEngine] = useState<string | null>(null)
  // noVNC only available on remote server (not localhost/Electron)
  const isLocal = typeof window !== "undefined" && (window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1")
  const hasVnc = !isLocal
  // Remember which engine opened the panel (so it doesn't flip to VNC when scrape ends and engine becomes null)
  const isAutoScraper = panelEngine === "brightdata"

  // Auto-open when scraping starts, only close when explicitly done (not on transient status changes)
  useEffect(() => {
    const engine = scrapeStatus?.engine
    const canShow = engine === "brightdata" || hasVnc
    if (scrapeStatus?.isRunning && !manualClose && canShow) {
      setPanelEngine(engine || "patchright")
      setShowPanel(true)
    }
    // Only close when latest run is explicitly completed/failed/stopped (not just isRunning=false)
    const latestStatus = scrapeStatus?.latest?.status
    if (!scrapeStatus?.isRunning && latestStatus && latestStatus !== "running" && showPanel && !manualClose) {
      const timer = setTimeout(() => { setShowPanel(false); setPanelEngine(null) }, 3000)
      return () => clearTimeout(timer)
    }
  }, [scrapeStatus?.isRunning, scrapeStatus?.latest?.status, scrapeStatus?.engine])

  const vncUrl = typeof window !== "undefined"
    ? `http://${window.location.hostname}:6080/vnc.html?autoconnect=true&resize=scale`
    : ""

  return (
    <div className="flex h-screen bg-background text-foreground">
      {/* Sidebar */}
      <aside className="w-16 flex-shrink-0 flex flex-col items-center py-6 gap-6 border-r border-border bg-card">
        <Zap className="w-7 h-7 text-yellow-400" />
        <nav className="flex flex-col gap-3 mt-4">
          {NAV.map(({ to, icon: Icon, label }) => {
            const active = location.pathname === to
            return (
              <Link
                key={to}
                to={to}
                title={label}
                className={`p-2.5 rounded-lg transition-colors ${
                  active
                    ? "bg-secondary text-foreground"
                    : "text-muted-foreground hover:text-foreground hover:bg-secondary/50"
                }`}
              >
                <Icon className="w-5 h-5" />
              </Link>
            )
          })}
        </nav>
        {/* Panel toggle (VNC for Patchright on remote, Log for Decodo everywhere) */}
        {((scrapeStatus?.engine === "brightdata") || hasVnc) && scrapeStatus?.isRunning && (
          <button
            onClick={() => { setShowPanel((v) => !v); setManualClose(false) }}
            title={scrapeStatus?.engine === "brightdata" ? "Scraper Log" : "Live View"}
            className={`mt-auto p-2.5 rounded-lg transition-colors ${
              showPanel ? "bg-yellow-500/20 text-yellow-400" : "text-muted-foreground hover:text-yellow-400"
            }`}
          >
            {scrapeStatus?.engine === "brightdata" ? <Terminal className="w-5 h-5" /> : <Monitor className="w-5 h-5" />}
          </button>
        )}
      </aside>

      {/* Main Content — shrinks when panel is open */}
      <main className={`flex-1 overflow-y-auto p-6 transition-all duration-300 ${showPanel ? "lg:mr-[50vw] md:mr-[40vw]" : ""}`}>
        {children}
      </main>

      {/* Side Panel — VNC for Patchright, Log for Decodo */}
      <div className={`fixed top-0 right-0 h-full bg-card border-l border-border flex flex-col transition-all duration-300 ${
        showPanel ? "w-full md:w-[40vw] lg:w-[50vw]" : "w-0"
      } overflow-hidden`}>
        {showPanel && (
          isAutoScraper ? (
            <LogPanel onClose={() => { setShowPanel(false); setManualClose(true); setPanelEngine(null) }} />
          ) : (
            <>
              <div className="flex items-center justify-between px-4 py-3 border-b border-border flex-shrink-0">
                <div className="flex items-center gap-2 text-sm font-medium">
                  <Monitor className="w-4 h-4 text-yellow-400" />
                  <span className="hidden sm:inline">Live View — Scraper</span>
                  {scrapeStatus?.isRunning && (
                    <span className="flex items-center gap-1.5 ml-2">
                      <span className="w-2 h-2 rounded-full bg-positive animate-pulse" />
                      <span className="text-xs text-muted-foreground">Scraping...</span>
                    </span>
                  )}
                </div>
                <button
                  onClick={() => { setShowPanel(false); setManualClose(true); setPanelEngine(null) }}
                  className="p-1.5 rounded-lg hover:bg-secondary text-muted-foreground hover:text-foreground"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>
              {hasVnc && (
                <iframe
                  src={vncUrl}
                  className="flex-1 w-full border-0"
                  allow="clipboard-read; clipboard-write"
                />
              )}
            </>
          )
        )}
      </div>
    </div>
  )
}
