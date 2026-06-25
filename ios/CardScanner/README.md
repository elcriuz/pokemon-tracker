# CardScanner — iOS-Prototyp (On-Device-Erkennung + Rapid-Fire-Batch)

Prototyp, der die Karten-Scanner-Engine auf iOS bringt und zwei Dinge demonstriert:

1. **On-Device-Beschleunigung** — Nummer / Set / Name / Grade werden lokal per VisionKit + Vision
   in ~100–300 ms gelesen, statt auf zwei Cloud-Roundtrips (OpenAI Vision + Ximilar) zu warten.
2. **Rapid-Fire-Batch** — „foto-foto-foto": jeder Auslöser reiht sofort einen Job ein (kein Warten),
   bis zu 4 Jobs werden im Hintergrund parallel aufgelöst, Reports streamen in die Liste ein.

## Status
- ✅ Kompiliert sauber (Xcode 26, Swift 5-Modus, iOS 17+), Device-Signing = Automatic.
- ✅ Läuft im Simulator (Demo-Modus mit echten Beispiel-OCR-Daten, da der Simulator keine Kamera hat).
- ✅ On-Device-Parser verifiziert: Pikachu 60/64, **CGC 8.5 + Slab-Cert**, Mega Lucario 092/063, Mewtu 281/217, Gengar SWSH052.
- ✅ **Live-Backend angebunden** (`cardcheck_api.py`, systemd `cardcheck-api` auf `:8088`) — echte Cardmarket-Preise verifiziert (Pikachu 1,99 €, Victini 524,99 €, via `cloud+ondevice`).
- ✅ Backend-URL **zur Laufzeit einstellbar** (Zahnrad → Einstellungen), persistiert in UserDefaults.

## Architektur (`Sources/`)
| Datei | Aufgabe |
|---|---|
| `CardRecognizer.swift` | **Reine, netzfreie Parsing-Logik**: OCR-Zeilen + Barcodes → `RecognizedCard` (Nummer/Set/Name/Grade/Sprache/Cert). Testbar, läuft auch standalone. |
| `DataScannerView.swift` | VisionKit `DataScannerViewController` (Live-Text + Barcode), streamt jeden Frame an das ViewModel. |
| `ScannerViewModel.swift` | Hält das live erkannte Feld-Set + `capturePhoto` (High-Res-Still für den Backend-Call). |
| `ScanQueue.swift` | **Rapid-Fire-Queue**: `enqueue` kehrt sofort zurück, max. 4 Jobs parallel, Reports streamen in `jobs`. |
| `BackendClient.swift` | POST an dein Backend (`/api/identify`) mit On-Device-Hints; Fallback = reines On-Device-Ergebnis. |
| `ContentView.swift` | Live-Scanner + Auslöser oben, streamende Batch-Liste unten (Simulator: Rapid-Fire-Demo). |

## Warum das schnell ist
Die Engine ist heute **netzwerk-gebunden** (Vision ~2–5 s, Ximilar ~2–6 s, Preis-Scrape ~10–30 s).
- On-Device-OCR/-Barcode killt die beiden ID-Roundtrips für den Normalfall (lokal, ~ms).
- **Slab-Barcode** = deterministische Karte + exakter Grade (löst CGC-8.5-Problem strukturell).
- **Batch**: Server verarbeitet schon fire-and-forget. Messung mit 25 Testkarten:
  **alle gleichzeitig → 8,2 s Wall-Clock statt ~133 s sequenziell (16×), Reports streamen ab 3,0 s.**
- Bleibt netzwerk-gebunden: der Cardmarket-**Preis**. Den löst nur eine API / ein Preis-Cache, nicht VisionKit.

## Auf dem iPhone testen (Xcode)
```bash
cd ios/CardScanner
xcodegen generate            # erzeugt CardScanner.xcodeproj aus project.yml
open CardScanner.xcodeproj
```
1. In Xcode den Ziel-**CardScanner** wählen → Tab **Signing & Capabilities** → **dein Team** auswählen
   (Automatic Signing). Bundle-ID `at.mxr.cardscanner` ggf. eindeutig machen, falls vergeben.
2. iPhone per Kabel verbinden, oben als Run-Ziel wählen, **▶︎ Run**. Beim Start Kamera-Zugriff erlauben.
3. **Erreichbarkeit zum Backend:** das iPhone muss `192.168.1.91:8088` erreichen — entweder im **gleichen WLAN**
   wie der Host, oder **Tailscale** auf dem iPhone aktiv. Sonst im Zahnrad → **Einstellungen** die passende
   Backend-URL eintragen (oder leer lassen für reinen On-Device-Modus ohne Preis).
4. Karte ins Bild halten → die On-Device-Felder (Nummer/Set/Name/Grade) erscheinen live → **Auslöser** drücken.
   Mehrfach schnell hintereinander drücken → die Reports streamen unten in die Batch-Liste.

Simulator (ohne Kamera, Demo-Modus):
```bash
xcodebuild -project CardScanner.xcodeproj -scheme CardScanner \
  -sdk iphonesimulator -destination 'generic/platform=iOS Simulator' build CODE_SIGNING_ALLOWED=NO
```
`CardScanner.xcodeproj` ist generiert — Quelle der Wahrheit ist `project.yml`.

## Backend-Contract (zum Anbinden an die Python-Engine)
`POST /api/identify`
```jsonc
// request
{ "name": "Mewtu", "number": "281/217", "setCode": null,
  "grade": "raw", "certBarcode": null, "language": "de", "imageBase64": "..." }
// response
{ "name": "Team Rocket's Mewtwo ex", "set": "Ascended Heroes", "number": "281",
  "grade": "raw", "language": "de", "marketEur": 137.99, "cmUrl": "...",
  "confidence": "HIGH", "via": "ximilar" }
```
Das ist bereits implementiert in **`/opt/pokemon-tracker/cardcheck_api.py`** (Repo-Root: `cardcheck_api.py`)
und läuft als systemd-Service `cardcheck-api` auf `:8088`. Modi: `imageBase64` (volles `identify_card`)
ODER On-Device-Hints (nur Preis-Scrape).

## Nächste Schritte
- HTTPS + Auth fürs Backend (statt `NSAllowsArbitraryLoads`) für Nutzung außerhalb des LAN.
- On-Device-FeaturePrint-Index (lokales Bild-Matching) als erste ID-Stufe, Cloud nur als Fallback.
- Echtes Hintergrund-Hochladen via `URLSession` background uploads, damit der Batch auch bei gesperrtem Display weiterläuft.
