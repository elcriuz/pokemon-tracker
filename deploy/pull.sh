#!/bin/bash
#
# Deploy-Schleife für Container 103. Läuft per Cron jede Minute:
#   * * * * * /opt/pokemon-tracker/deploy/pull.sh
#
# Grundsatz: Ein fehlgeschlagener Build darf den laufenden Stand nicht
# anfassen. Erst wenn pnpm install UND vite build durch sind, wird der
# Dienst neu gestartet. Jeder Fehlschlag steht vollständig im Log und geht
# zusätzlich per Telegram raus.
#
# Die ganze Logik steckt in main(), weil `git reset --hard` dieses Script
# mitten im Lauf ersetzt — Bash liest längere Dateien nachladend und würde
# sonst in der neuen Version weiterspringen. Mit main() ist alles geparst,
# bevor die erste Zeile ausgefuehrt wird.

set -uo pipefail

PROJECT_DIR=/opt/pokemon-tracker
LOG="$PROJECT_DIR/data/logs/deploy.log"
PENDING="$PROJECT_DIR/data/deploy_restart_pending"
LOCK=/var/lock/pokemon-deploy.lock

main() {
  local log_line
  ts() { date "+%Y-%m-%d %H:%M:%S"; }
  log() { echo "$(ts) $*" >> "$LOG"; }

  # Telegram-Zugang steht in der Tracker-Datenbank, nicht in einer Env-Datei.
  notify() {
    local text="$1"
    python3 - "$text" <<'PYEOF' >> "$LOG" 2>&1 || log "Telegram-Hinweis fehlgeschlagen"
import sqlite3, sys, urllib.parse, urllib.request
text = sys.argv[1]
db = "/opt/pokemon-tracker/data/tracker.db"
conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
werte = dict(conn.execute(
    "SELECT key, value FROM settings WHERE key IN ('telegram_bot_token','telegram_chat_id')"
))
token, chat = werte.get("telegram_bot_token", ""), werte.get("telegram_chat_id", "")
if not token or not chat:
    raise SystemExit("Telegram nicht konfiguriert, kein Hinweis verschickt")
daten = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
with urllib.request.urlopen(
    f"https://api.telegram.org/bot{token}/sendMessage", data=daten, timeout=15
) as antwort:
    antwort.read()
PYEOF
  }

  # Ein Scrape darf nicht mitten im Lauf den Server unter sich verlieren.
  scrape_laeuft() {
    pgrep -f "python3 .*scrape" > /dev/null || pgrep -f "python3 .*watchlist" > /dev/null
  }

  neustart() {
    local grund="$1"
    if scrape_laeuft; then
      git -C "$PROJECT_DIR" rev-parse --short HEAD > "$PENDING"
      log "Scrape läuft, Neustart vorgemerkt ($grund)"
      notify "Tracker-Deploy $grund: Neustart übersprungen, weil gerade ein Scrape läuft. Wird automatisch nachgeholt, sobald der Scrape durch ist."
      return 0
    fi
    if systemctl restart pokemon-tracker; then
      rm -f "$PENDING"
      log "Dienst neu gestartet ($grund)"
      return 0
    fi
    log "FEHLER: systemctl restart pokemon-tracker fehlgeschlagen"
    notify "Tracker-Deploy: Neustart fehlgeschlagen. Der Stand liegt gebaut bereit, der Dienst läuft auf altem Code."
    return 1
  }

  mkdir -p "$(dirname "$LOG")"
  cd "$PROJECT_DIR" || { log "FEHLER: $PROJECT_DIR nicht erreichbar"; return 1; }

  # Vorgemerkten Neustart aus einem früheren Lauf nachholen
  if [ -f "$PENDING" ] && ! scrape_laeuft; then
    neustart "nachgeholt für $(cat "$PENDING")"
  fi

  if ! git fetch -q origin main 2>>"$LOG"; then
    log "FEHLER: git fetch fehlgeschlagen"
    return 1
  fi

  local lokal fern
  lokal=$(git rev-parse HEAD)
  fern=$(git rev-parse origin/main)
  [ "$lokal" = "$fern" ] && return 0

  local kurz betreff start
  kurz=$(git rev-parse --short "$fern")
  betreff=$(git log -1 --format=%s "$fern")
  start=$SECONDS
  log "--- Neuer Stand $kurz: $betreff"

  if ! git reset -q --hard "$fern" 2>>"$LOG"; then
    log "FEHLER: git reset fehlgeschlagen, Stand unverändert"
    notify "Tracker-Deploy $kurz fehlgeschlagen: git reset ging nicht durch."
    return 1
  fi

  # Ab hier: Fehler bedeuten "nicht neu starten". Der Dienst läuft
  # weiter auf dem alten dist, bis ein Lauf vollständig durchkommt.
  local ausgabe
  ausgabe=$(mktemp)

  if ! pnpm install --frozen-lockfile > "$ausgabe" 2>&1; then
    log "FEHLER bei pnpm install:"
    sed 's/^/    /' "$ausgabe" >> "$LOG"
    rm -f "$ausgabe"
    notify "Tracker-Deploy $kurz abgebrochen: pnpm install fehlgeschlagen. Der Server läuft unverändert weiter. Siehe deploy.log."
    return 1
  fi

  if ! npx vite build > "$ausgabe" 2>&1; then
    log "FEHLER beim Build:"
    sed 's/^/    /' "$ausgabe" >> "$LOG"
    rm -f "$ausgabe"
    notify "Tracker-Deploy $kurz abgebrochen: Build fehlgeschlagen. Der Server läuft mit dem alten Frontend weiter. Siehe deploy.log."
    return 1
  fi

  log_line=$(grep -E "built in|dist/assets/.*\.js" "$ausgabe" | tail -2 | tr '\n' ' ')
  rm -f "$ausgabe"
  log "Build ok: ${log_line:-keine Build-Ausgabe}"

  neustart "$kurz" || return 1
  log "Deploy $kurz durch in $((SECONDS - start))s"
}

# Nur ein Lauf gleichzeitig — sonst überholen sich Minutentakt und ein
# längerer Build gegenseitig im selben Arbeitsverzeichnis.
exec 200>"$LOCK"
flock -n 200 || exit 0
main
