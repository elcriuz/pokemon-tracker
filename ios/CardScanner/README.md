# CardScanner — iOS-Prototyp (On-Device-Erkennung + Rapid-Fire-Batch)

Prototyp, der die Karten-Scanner-Engine auf iOS bringt und zwei Dinge demonstriert:

1. **On-Device-Beschleunigung** — Nummer / Set / Name / Grade werden lokal per VisionKit + Vision
   in ~100–300 ms gelesen, statt auf zwei Cloud-Roundtrips (OpenAI Vision + Ximilar) zu warten.
2. **Rapid-Fire-Batch** — „foto-foto-foto": jeder Auslöser reiht sofort einen Job ein (kein Warten),
   bis zu 4 Jobs werden im Hintergrund parallel aufgelöst, Reports streamen in die Liste ein.

## Status
- ✅ Kompiliert sauber (Xcode 26, Swift 5-Modus, iOS 17+) — mit `xcodebuild` gegen den Simulator verifiziert.
- ✅ Läuft im Simulator (Demo-Modus mit echten Beispiel-OCR-Daten, da der Simulator keine Kamera hat).
- ✅ On-Device-Parser verifiziert: Pikachu 60/64, **CGC 8.5 + Slab-Cert**, Mega Lucario 092/063, Mewtu 281/217, Gengar SWSH052.
- 🔌 Backend-Call ist ein Hook mit On-Device-Fallback — `BackendClient.endpoint` setzen, um Cloud-ID + echte Preise zu bekommen.

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

## Bauen & Starten
```bash
cd ios/CardScanner
xcodegen generate          # erzeugt CardScanner.xcodeproj aus project.yml
open CardScanner.xcodeproj # in Xcode auf ein echtes Gerät (Kamera!) bauen
# oder Simulator (Demo-Modus):
xcodebuild -project CardScanner.xcodeproj -scheme CardScanner \
  -sdk iphonesimulator -destination 'generic/platform=iOS Simulator' build CODE_SIGNING_ALLOWED=NO
```
`CardScanner.xcodeproj` wird generiert — Quelle der Wahrheit ist `project.yml`.

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
Dünner Wrapper um `cardcheck_bot.identify_card()` + `scrape_cardmarket_prices()` genügt — dann nutzt
die App On-Device-Hints für den Fast-Path und fällt nur bei niedriger Confidence auf die Cloud zurück.

## Nächste Schritte
- `/api/identify`-Endpoint im Node-Server (oder FastAPI) bereitstellen und `BackendClient.endpoint` setzen.
- On-Device-FeaturePrint-Index (lokales Bild-Matching) als erste ID-Stufe, Cloud nur als Fallback.
- Echtes Hintergrund-Hochladen via `URLSession` background uploads, damit der Batch auch bei gesperrtem Display weiterläuft.
