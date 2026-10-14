import { useQuery } from "@tanstack/react-query"
import { useState } from "react"
import { api } from "@/lib/api"
import { Crosshair, Globe, KeyRound, Timer, ExternalLink, Ban } from "lucide-react"

/**
 * Was der Sniper gerade tut: Lebt der Helfer in Chrome, ist er bei Cardmarket
 * eingeloggt, was haben die Preislaeufe gelesen und was hat der Helfer mit
 * einem Schnapper gemacht. Laedt alle 15 s neu.
 */

function seit(iso: string | null | undefined): string {
  if (!iso) return "noch nie"
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000))
  if (s < 60) return `vor ${s} s`
  const m = Math.round(s / 60)
  if (m < 60) return `vor ${m} Min.`
  const h = Math.round(m / 60)
  return h < 24 ? `vor ${h} Std.` : `vor ${Math.round(h / 24)} Tagen`
}

const zeit = (iso: string) =>
  new Date(iso).toLocaleString("de-AT", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" })

const eur = (v: number | null | undefined) => v == null ? "–" : `${v.toFixed(2).replace(".", ",")} €`

/** Der Cron laeuft um :05, :15, :25 … */
function naechsterLauf(): Date {
  const d = new Date()
  const m = d.getMinutes()
  const next = Math.floor((m - 5) / 10) * 10 + 15
  d.setSeconds(0, 0)
  d.setMinutes(next)
  return d
}

function Tile({ icon: Icon, label, value, sub, tone }: {
  icon: any; label: string; value: string; sub?: string; tone: "ok" | "warn" | "bad" | "neutral"
}) {
  const dot = { ok: "bg-emerald-400", warn: "bg-amber-400", bad: "bg-red-500", neutral: "bg-muted-foreground" }[tone]
  return (
    <div className="border border-border rounded-lg p-4 bg-muted/10">
      <div className="flex items-center gap-1.5 text-xs text-muted-foreground uppercase tracking-wide">
        <Icon className="w-3.5 h-3.5" /> {label}
      </div>
      <div className="mt-2 flex items-center gap-2 text-lg font-semibold">
        <span className={`w-2.5 h-2.5 rounded-full ${dot} ${tone === "ok" ? "animate-pulse" : ""}`} />
        {value}
      </div>
      {sub && <div className="text-xs text-muted-foreground mt-1">{sub}</div>}
    </div>
  )
}

const KIND: Record<string, { label: string; cls: string }> = {
  lauf: { label: "Preislauf", cls: "bg-muted/50 text-muted-foreground" },
  schnapper: { label: "Schnapper", cls: "bg-orange-500/15 text-orange-400" },
  pruefung: { label: "Helfer", cls: "bg-sky-500/15 text-sky-400" },
  ergebnis: { label: "Ergebnis", cls: "bg-emerald-500/15 text-emerald-400" },
  alarm: { label: "Alarm", cls: "bg-red-500/15 text-red-400" },
  login: { label: "Login", cls: "bg-violet-500/15 text-violet-400" },
  start: { label: "Helfer", cls: "bg-sky-500/15 text-sky-400" },
  warnung: { label: "Warnung", cls: "bg-amber-500/15 text-amber-400" },
  fehler: { label: "Fehler", cls: "bg-red-500/15 text-red-400" },
  info: { label: "Info", cls: "bg-muted/50 text-muted-foreground" },
}

const STATUS: Record<string, string> = {
  carted: "🛒 im Warenkorb", failed: "⚠ selbst einlegen", blocked: "⛔ kein Versand nach AT",
  gone: "schon weg", unchecked: "ungeprüft gemeldet", pending: "wartet auf Helfer", claimed: "Helfer prüft …",
}

export function Sniper() {
  const [filter, setFilter] = useState<"alle" | "helfer" | "laeufe">("alle")
  const { data, isLoading } = useQuery({
    queryKey: ["sniperStatus"],
    queryFn: () => api.getSniperStatus(),
    refetchInterval: 15_000,
  })
  if (isLoading || !data) return <div className="p-6 text-muted-foreground">Lade Sniper …</div>

  const h = data.helper
  const login = h.login
  const loginTone = !login ? "neutral" : login.state === "in" ? "ok" : login.state === "out" ? "bad" : "warn"
  const loginText = !login ? "noch nicht geprüft"
    : login.state === "in" ? "eingeloggt" : login.state === "out" ? "AUSGELOGGT" : "unklar"
  const runAlt = data.last_run && Date.now() - new Date(data.last_run).getTime() > 25 * 60_000

  const events = data.events.filter((e: any) =>
    filter === "alle" ? true : filter === "helfer" ? e.kind !== "lauf" : e.kind === "lauf")

  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-semibold inline-flex items-center gap-2">
          <Crosshair className="w-6 h-6 text-orange-400" /> Sniper
        </h1>
        <p className="text-sm text-muted-foreground mt-1">
          Alle 10 Minuten holt der Tracker die Sniper-Artikel. Ein Schnapper geht an den Helfer in Chrome:
          Der prüft eingeloggt, ob der Verkäufer nach AT verschickt, legt ein und erst dann klingelt’s.
        </p>
      </div>

      <div className="grid gap-3 sm:grid-cols-3">
        <Tile icon={Globe} label="Helfer in Chrome"
          value={h.online ? "live" : "offline"} tone={h.online ? "ok" : "bad"}
          sub={`${h.version ? `v${h.version} · ` : ""}zuletzt gemeldet ${seit(h.seen)}${h.online ? "" : " — Schnapper gehen ungeprüft raus"}`} />
        <Tile icon={KeyRound} label="Cardmarket-Login" value={loginText} tone={loginTone}
          sub={login ? `geprüft ${seit(login.at)}${login.state === "out" ? " — in Chrome einloggen!" : ""}${login.state === "unknown" ? ` · ${login.detail}` : ""}` : "Helfer prüft alle 20 Min."} />
        <Tile icon={Timer} label="Preislauf"
          value={data.last_run ? seit(data.last_run) : "noch keiner"} tone={!data.last_run ? "neutral" : runAlt ? "bad" : "ok"}
          sub={`nächster um ${naechsterLauf().toLocaleTimeString("de-AT", { hour: "2-digit", minute: "2-digit" })}${runAlt ? " · letzter Lauf überfällig!" : ""}`} />
      </div>

      <section>
        <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground mb-2">Überwacht</h2>
        <div className="grid gap-3 md:grid-cols-2">
          {data.items.map((i: any) => {
            const l = i.last?.data
            const abstand = l?.best_total != null && l?.schwelle != null ? l.best_total - l.schwelle : null
            return (
              <div key={i.id} className="border border-border rounded-lg p-4 flex gap-4">
                {i.image ? (
                  <img src={`/images/${i.image}`} alt="" className="w-14 h-20 object-contain rounded flex-shrink-0" />
                ) : <div className="w-14 h-20 rounded bg-muted/40 flex-shrink-0" />}
                <div className="min-w-0 flex-1 text-sm">
                  <a href={i.product_url} target="_blank" rel="noreferrer" className="font-medium hover:underline inline-flex items-center gap-1">
                    {i.name} <ExternalLink className="w-3 h-3 opacity-40" />
                  </a>
                  <span className="ml-1.5 text-[10px] uppercase px-1.5 py-0.5 rounded bg-muted/50">{i.language}</span>
                  {!l ? (
                    <div className="text-xs text-muted-foreground mt-2">Noch kein Lauf mit Protokoll.</div>
                  ) : (
                    <>
                      <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 tabular-nums text-xs">
                        <span className="text-muted-foreground">Bestes inkl. Versand</span>
                        <span className="text-right font-semibold text-sm">{eur(l.best_total)}</span>
                        <span className="text-muted-foreground">Verkäufer</span>
                        <span className="text-right truncate">{l.seller ?? "–"}{l.origin ? ` · ${l.origin}` : ""}</span>
                        <span className="text-muted-foreground">Nächstes Angebot</span>
                        <span className="text-right">{eur(l.zweit)}</span>
                        <span className="text-muted-foreground">7-Tage-Boden</span>
                        <span className="text-right">{eur(l.boden)}</span>
                        <span className="text-orange-400">Schnapper ab</span>
                        <span className="text-right text-orange-400 font-medium">≤ {eur(l.schwelle)}</span>
                      </div>
                      <div className="text-[11px] text-muted-foreground mt-2">
                        {abstand != null && abstand > 0 ? `noch ${eur(abstand)} bis zum Alarm · ` : abstand != null ? "🎯 unter der Schwelle · " : ""}
                        {l.offers} Angebote{l.verdaechtig ? ` · ${l.verdaechtig} verdächtig` : ""}{l.gesperrt ? ` · ${l.gesperrt} gesperrt` : ""}
                        {" · "}gelesen {seit(i.last.ts)}
                      </div>
                    </>
                  )}
                </div>
              </div>
            )
          })}
          {data.items.length === 0 && (
            <div className="text-sm text-muted-foreground">Kein Artikel mit Fadenkreuz in der Wunschliste.</div>
          )}
        </div>
      </section>

      {data.signals.length > 0 && (
        <section>
          <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground mb-2">Letzte Schnapper</h2>
          <div className="border border-border rounded-lg divide-y divide-border/50 text-sm">
            {data.signals.map((s: any) => {
              const dry = String(s.check_status).startsWith("dry-")
              const st = dry ? s.check_status.slice(4) : s.check_status
              return (
                <div key={s.id} className="px-4 py-2.5 flex flex-wrap items-baseline gap-x-3">
                  <span className="text-xs text-muted-foreground tabular-nums w-28">{zeit(s.created_at)}</span>
                  <span className="font-medium tabular-nums">{eur(s.suggested_price)}</span>
                  <span>{String(s.offer_key ?? "").split("|")[0]}</span>
                  <span className="text-muted-foreground truncate">{s.name}</span>
                  <span className="ml-auto text-xs">{dry ? "Testlauf · " : ""}{STATUS[st] ?? st}</span>
                  {s.check_detail && s.check_detail !== "dry" && (
                    <div className="w-full text-[11px] text-muted-foreground pl-[7.75rem]">{s.check_detail}</div>
                  )}
                </div>
              )
            })}
          </div>
        </section>
      )}

      <section>
        <div className="flex items-center justify-between mb-2">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">Protokoll</h2>
          <div className="flex gap-1 text-xs">
            {(["alle", "helfer", "laeufe"] as const).map((f) => (
              <button key={f} onClick={() => setFilter(f)}
                className={`px-2 py-1 rounded border ${filter === f ? "bg-primary text-primary-foreground border-primary" : "border-border text-muted-foreground"}`}>
                {{ alle: "Alles", helfer: "Ohne Preisläufe", laeufe: "Nur Preisläufe" }[f]}
              </button>
            ))}
          </div>
        </div>
        <div className="border border-border rounded-lg divide-y divide-border/40 text-sm">
          {events.map((e: any) => {
            const k = KIND[e.kind] ?? KIND.info
            return (
              <div key={e.id} className="px-4 py-2 flex gap-3 items-baseline">
                <span className="text-xs text-muted-foreground tabular-nums w-28 flex-shrink-0">{zeit(e.ts)}</span>
                <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium flex-shrink-0 w-20 text-center ${k.cls}`}>{k.label}</span>
                <span className="min-w-0">
                  {e.name && <span className="text-muted-foreground">{e.name.slice(0, 40)} · </span>}
                  {e.text}
                </span>
              </div>
            )
          })}
          {events.length === 0 && <div className="px-4 py-6 text-center text-muted-foreground">Noch nichts protokolliert.</div>}
        </div>
      </section>

      {data.blocked.length > 0 && (
        <section>
          <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground mb-2 inline-flex items-center gap-1.5">
            <Ban className="w-3.5 h-3.5" /> Gesperrte Verkäufer
          </h2>
          <div className="border border-border rounded-lg divide-y divide-border/40 text-sm">
            {data.blocked.map((b: any) => (
              <div key={b.seller} className="px-4 py-2 flex gap-3">
                <span className="font-medium">{b.seller}</span>
                <span className="text-muted-foreground text-xs truncate">{b.reason}</span>
                <span className="ml-auto text-xs text-muted-foreground">{b.created_at?.slice(0, 10)}</span>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}
