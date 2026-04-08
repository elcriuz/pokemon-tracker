import { Link, useLocation } from "react-router-dom"
import { LayoutDashboard, Settings, Zap } from "lucide-react"
import type { ReactNode } from "react"

const NAV = [
  { to: "/", icon: LayoutDashboard, label: "Dashboard" },
  { to: "/settings", icon: Settings, label: "Settings" },
]

export function AppShell({ children }: { children: ReactNode }) {
  const location = useLocation()

  return (
    <div className="flex h-screen bg-background text-foreground">
      <aside className="w-16 flex flex-col items-center py-6 gap-6 border-r border-border bg-card">
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
      </aside>
      <main className="flex-1 overflow-y-auto p-6">{children}</main>
    </div>
  )
}
