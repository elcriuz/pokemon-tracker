import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts"
import { formatEUR } from "@/lib/utils"

export function PortfolioChart({ data }: { data: { date: string; totalValue: number }[] }) {
  return (
    <div className="p-4 rounded-lg bg-card border border-border">
      <h2 className="text-sm font-semibold text-muted-foreground mb-4">Portfolio-Wert</h2>
      <ResponsiveContainer width="100%" height={200}>
        <AreaChart data={data}>
          <defs>
            <linearGradient id="valueGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="hsl(212, 100%, 48%)" stopOpacity={0.3} />
              <stop offset="95%" stopColor="hsl(212, 100%, 48%)" stopOpacity={0} />
            </linearGradient>
          </defs>
          <XAxis dataKey="date" tick={{ fill: "hsl(215, 20%, 55%)", fontSize: 12 }} tickLine={false} axisLine={false} />
          <YAxis tick={{ fill: "hsl(215, 20%, 55%)", fontSize: 12 }} tickLine={false} axisLine={false} tickFormatter={(v) => `${(v / 1000).toFixed(1)}k`} />
          <Tooltip
            contentStyle={{ background: "hsl(222, 47%, 8%)", border: "1px solid hsl(217, 33%, 17%)", borderRadius: 8, color: "white" }}
            formatter={(value: number) => [formatEUR(value), "Gesamt"]}
            labelFormatter={(label) => new Date(label).toLocaleDateString("de-DE")}
          />
          <Area type="monotone" dataKey="totalValue" stroke="hsl(212, 100%, 48%)" fill="url(#valueGrad)" strokeWidth={2} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}
