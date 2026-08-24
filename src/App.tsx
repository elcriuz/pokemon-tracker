import { BrowserRouter, Routes, Route } from "react-router-dom"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { AppShell } from "./components/layout/AppShell"
import { Dashboard } from "./pages/Dashboard"
import { CardDetail } from "./pages/CardDetail"
import { Settings } from "./pages/Settings"
import { Analytics } from "./pages/Analytics"
import { ScanLog } from "./pages/ScanLog"
import { CardShop } from "./pages/CardShop"
import { Offers } from "./pages/Offers"
import { Watchlist } from "./pages/Watchlist"

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000 } },
})

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AppShell>
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/cards/:id" element={<CardDetail />} />
            <Route path="/analytics" element={<Analytics />} />
            <Route path="/scans" element={<ScanLog />} />
            <Route path="/cardshop" element={<CardShop />} />
            <Route path="/offers" element={<Offers />} />
            <Route path="/watchlist" element={<Watchlist />} />
            <Route path="/settings" element={<Settings />} />
          </Routes>
        </AppShell>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
