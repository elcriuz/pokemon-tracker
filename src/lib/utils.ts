import { type ClassValue, clsx } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function formatEUR(value: number | null | undefined): string {
  if (value == null) return "—"
  return new Intl.NumberFormat("de-DE", {
    style: "currency",
    currency: "EUR",
  }).format(value)
}

export function formatPct(value: number | null | undefined): string {
  if (value == null) return "—"
  const sign = value >= 0 ? "+" : ""
  return `${sign}${value.toFixed(1)}%`
}

// Cardmarket language parameter → flag emoji
const LANG_ID_FLAG: Record<string, string> = {
  "1": "\u{1F1EC}\u{1F1E7}",  // English
  "2": "\u{1F1EB}\u{1F1F7}",  // French
  "3": "\u{1F1E9}\u{1F1EA}",  // German
  "4": "\u{1F1EA}\u{1F1F8}",  // Spanish
  "5": "\u{1F1EE}\u{1F1F9}",  // Italian
  "6": "\u{1F1F3}\u{1F1F1}",  // Dutch
  "7": "\u{1F1EF}\u{1F1F5}",  // Japanese
  "8": "\u{1F1F5}\u{1F1F9}",  // Portuguese
  "9": "\u{1F1F7}\u{1F1FA}",  // Russian
  "10": "\u{1F1F0}\u{1F1F7}", // Korean
  "11": "\u{1F1E8}\u{1F1F3}", // Chinese
}

export function urlToFlag(url: string | null | undefined): string {
  if (!url) return ""
  try {
    const params = new URL(url).searchParams
    const lang = params.get("language")
    if (lang && LANG_ID_FLAG[lang]) return LANG_ID_FLAG[lang]
  } catch {}
  return ""
}

// Ab so vielen Tagen ohne passendes Angebot zeigen wir den gehaltenen Wert nicht mehr
// als Marktwert an. Der Scraper laeuft woechentlich — 7 Tage = ein verpasster Lauf.
export const STALE_PRICE_DAYS = 7

/** Tage seit dem letzten echten Preis, wenn der aktuelle Wert nur gehalten ist und das
 *  laenger als STALE_PRICE_DAYS — sonst null. */
export function expiredPriceDays(card: {
  stale_grade?: number | null
  last_fresh_at?: string | null
  scraped_at?: string | null
}): number | null {
  if (!card.stale_grade || !card.scraped_at) return null
  const ref = card.last_fresh_at ?? null
  if (!ref) return Infinity
  const days = Math.floor((Date.now() - new Date(ref + "Z").getTime()) / 86400000)
  return days > STALE_PRICE_DAYS ? days : null
}

export function timeAgo(dateStr: string | null | undefined): string {
  if (!dateStr) return ""
  const now = Date.now()
  // Timestamps from scraper have no timezone suffix — they are UTC (container runs UTC)
  const normalized = dateStr.endsWith("Z") ? dateStr : dateStr + "Z"
  const then = new Date(normalized).getTime()
  const diff = now - then
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return "gerade eben"
  if (mins < 60) return `vor ${mins}m`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `vor ${hours}h`
  const days = Math.floor(hours / 24)
  if (days === 1) return "gestern"
  return `vor ${days}d`
}
