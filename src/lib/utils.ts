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
