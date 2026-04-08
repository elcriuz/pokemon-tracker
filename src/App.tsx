import { BrowserRouter, Routes, Route } from "react-router-dom"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { AppShell } from "./components/layout/AppShell"
import { Dashboard } from "./pages/Dashboard"
import { CardDetail } from "./pages/CardDetail"
import { Settings } from "./pages/Settings"

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
            <Route path="/settings" element={<Settings />} />
          </Routes>
        </AppShell>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
