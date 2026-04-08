import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend } from "recharts"
import { formatEUR } from "@/lib/utils"

export function PriceHistoryChart({ data }: { data: any[] }) {
  const formatted = data.map((d) => ({
    ...d,
    date: new Date(d.scraped_at).toLocaleDateString("de-DE"),
  }))

  return (
    <div className="p-4 rounded-lg bg-card border border-border">
      <h2 className="text-sm font-semibold text-muted-foreground mb-4">Preisverlauf</h2>
      <ResponsiveContainer width="100%" height={300}>
        <LineChart data={formatted}>
          <XAxis dataKey="date" tick={{ fill: "hsl(215, 20%, 55%)", fontSize: 12 }} tickLine={false} axisLine={false} />
          <YAxis tick={{ fill: "hsl(215, 20%, 55%)", fontSize: 12 }} tickLine={false} axisLine={false} tickFormatter={(v) => formatEUR(v)} />
          <Tooltip
            contentStyle={{ background: "hsl(222, 47%, 8%)", border: "1px solid hsl(217, 33%, 17%)", borderRadius: 8, color: "white" }}
            formatter={(value: number, name: string) => [formatEUR(value), name]}
          />
          <Legend />
          <Line type="monotone" dataKey="value" name="Wert" stroke="hsl(212, 100%, 48%)" strokeWidth={2} dot={{ r: 3 }} />
          <Line type="monotone" dataKey="trend" name="Trend" stroke="hsl(142, 71%, 45%)" strokeWidth={1.5} dot={{ r: 2 }} />
          <Line type="monotone" dataKey="from_price" name="Low" stroke="hsl(0, 84%, 60%)" strokeWidth={1.5} dot={{ r: 2 }} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
