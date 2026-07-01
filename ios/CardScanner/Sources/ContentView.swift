import SwiftUI
import VisionKit
import AVFoundation

struct ContentView: View {
    @StateObject private var model = ScannerViewModel()
    @StateObject private var queue = ScanQueue()
    @State private var activeSheet: ActiveSheet?
    @State private var shotCount = 0

    var body: some View {
        VStack(spacing: 0) {
            ZStack(alignment: .bottom) {
                scannerLayer
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)

            batchPanel
        }
        .sheet(item: $activeSheet) { sheet in
            switch sheet {
            case .settings: SettingsView()
            case .safari(let url): SafariView(url: url).ignoresSafeArea()
            }
        }
    }

    // MARK: - Scanner / camera gating

    @ViewBuilder private var scannerLayer: some View {
        if !DataScannerViewController.isSupported {
            SimulatorDemoView(queue: queue)                                   // Simulator: kein Kamera-HW
        } else {
            switch model.cameraStatus {
            case .authorized:
                if DataScannerViewController.isAvailable {
                    DataScannerView(model: model).ignoresSafeArea(edges: .top)
                    shutterBar
                } else {
                    infoView("Scanner nicht verfügbar", "Dieses Gerät unterstützt den Live-Scanner nicht.")
                }
            case .notDetermined:
                infoView("Kamera wird gestartet…", "Bitte den Kamerazugriff erlauben.")
                    .onAppear { model.requestCameraAccessIfNeeded() }
            default:
                cameraDeniedView
            }
        }
    }

    private var shutterBar: some View {
        HStack(alignment: .center) {
            VStack(alignment: .leading, spacing: 2) {
                Text(model.live.name ?? "Karte anvisieren…").font(.subheadline).bold()
                Text(liveDetail).font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            if shotCount > 0 {
                Text("\(shotCount)").font(.caption).monospaced().foregroundStyle(.secondary)
            }
            Button {
                UIImpactFeedbackGenerator(style: .medium).impactOccurred()   // sofortiges Feedback
                Task {
                    let data = await model.captureData()
                    guard model.live.hasUsableHints || data != nil else { return }
                    shotCount += 1
                    queue.enqueue(hints: model.live, imageData: data)
                }
            } label: {
                Image(systemName: "camera.circle.fill")
                    .font(.system(size: 60))
                    .symbolRenderingMode(.hierarchical)
            }
        }
        .padding()
        .background(.ultraThinMaterial, in: Capsule())
        .padding()
    }

    private var liveDetail: String {
        [model.live.collectorNumber, model.live.grade == "raw" ? nil : model.live.grade]
            .compactMap { $0 }.joined(separator: " · ")
    }

    private func infoView(_ title: String, _ subtitle: String) -> some View {
        VStack(spacing: 12) {
            Image(systemName: "camera.viewfinder").font(.system(size: 40)).foregroundStyle(.secondary)
            Text(title).font(.headline)
            Text(subtitle).font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center)
        }
        .padding().frame(maxWidth: .infinity, maxHeight: .infinity).background(Color(.systemBackground))
    }

    private var cameraDeniedView: some View {
        VStack(spacing: 14) {
            Image(systemName: "camera.fill").font(.system(size: 40)).foregroundStyle(.secondary)
            Text("Kamerazugriff nötig").font(.headline)
            Text("Erlaube den Kamerazugriff in den Einstellungen, um Karten zu scannen.")
                .font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center)
            Button("Einstellungen öffnen") {
                if let url = URL(string: UIApplication.openSettingsURLString) { UIApplication.shared.open(url) }
            }
            .buttonStyle(.borderedProminent)
        }
        .padding().frame(maxWidth: .infinity, maxHeight: .infinity).background(Color(.systemBackground))
    }

    // MARK: - Batch list

    private var batchPanel: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 12) {
                Text("Batch").font(.headline)
                Spacer()
                Text("\(queue.doneCount) fertig · \(queue.inFlight) in Arbeit")
                    .font(.caption).foregroundStyle(.secondary)
                if !queue.jobs.isEmpty {
                    Button("Leeren") { queue.clear() }.font(.caption)
                }
                Button { activeSheet = .settings } label: { Image(systemName: "gearshape") }
            }
            if queue.jobs.isEmpty {
                Text("Karten abfeuern → Reports erscheinen hier, sobald sie fertig sind.")
                    .font(.footnote).foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            ScrollView {
                LazyVStack(spacing: 8) {
                    ForEach(queue.jobs) { job in
                        JobRow(job: job) { activeSheet = .safari($0) }
                    }
                }
            }
        }
        .padding()
        .frame(height: 300)
        .background(Color(.secondarySystemBackground))
    }
}

private enum ActiveSheet: Identifiable {
    case settings
    case safari(URL)
    var id: String {
        switch self {
        case .settings: return "settings"
        case .safari(let u): return u.absoluteString
        }
    }
}

private struct JobRow: View {
    let job: ScanJob
    var onOpenLink: (URL) -> Void

    var body: some View {
        HStack(spacing: 12) {
            Text("#\(job.index)").font(.caption).monospaced().foregroundStyle(.secondary)
            statusDot
            VStack(alignment: .leading, spacing: 2) {
                Text(job.result?.name ?? job.hints.name ?? "wird erkannt…").bold()
                if let r = job.result {
                    Text(detailLine(r)).font(.caption).foregroundStyle(.secondary)
                }
            }
            Spacer()
            trailing
        }
        .padding(10)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 12))
    }

    @ViewBuilder private var trailing: some View {
        if job.status == .failed {
            Text("Server nicht erreichbar").font(.caption2).foregroundStyle(.red)
        } else if let r = job.result, let cm = r.cmUrl, let url = URL(string: cm) {
            Button { onOpenLink(url) } label: { Image(systemName: "arrow.up.forward.square") }
        } else if job.status == .processing {
            ProgressView().scaleEffect(0.7)
        } else {
            Text(job.status.rawValue).font(.caption2).foregroundStyle(.secondary)
        }
    }

    private func detailLine(_ r: IdentifiedCard) -> String {
        var parts: [String] = []
        if let n = r.number { parts.append("#\(n)") }
        if let g = r.grade, g != "raw" { parts.append(g) }
        if let p = r.marketEur { parts.append(String(format: "%.2f €", p)) }
        else if job.status == .done { parts.append("kein Preis") }
        if r.via.contains("on-device") { parts.append("nur On-Device") }
        return parts.joined(separator: " · ")
    }

    private var statusDot: some View {
        Circle()
            .fill(job.status == .done ? Color.green
                  : job.status == .failed ? .red
                  : job.status == .processing ? .orange : .gray)
            .frame(width: 8, height: 8)
    }
}

/// Backend-URL zur Laufzeit einstellbar — damit das iPhone den erreichbaren Host nutzen kann.
private struct SettingsView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var url = AppConfig.backendURLString
    @State private var token = AppConfig.apiToken
    @State private var testResult: String?
    @State private var testing = false

    private var trimmed: String { url.trimmingCharacters(in: .whitespacesAndNewlines) }

    private var isValid: Bool {
        if trimmed.isEmpty { return true }   // leer = nur On-Device
        guard let u = URL(string: trimmed), let scheme = u.scheme, !scheme.isEmpty, u.host != nil else { return false }
        return true
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Backend") {
                    TextField("http://192.168.1.91:8088/api/identify", text: $url)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                    if !isValid {
                        Text("Ungültige URL (Schema + Host nötig).").font(.caption).foregroundStyle(.red)
                    }
                    Text("Das iPhone muss den Host erreichen — gleiches WLAN oder Tailscale. Leer lassen = nur On-Device (kein Preis).")
                        .font(.caption).foregroundStyle(.secondary)
                    Button(testing ? "Teste…" : "Verbindung testen") { Task { await testConnection() } }
                        .disabled(testing || trimmed.isEmpty || !isValid)
                    if let t = testResult { Text(t).font(.caption) }
                }
                Section("Auth-Token") {
                    TextField("Bearer-Token (für öffentlichen Zugang)", text: $token)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    Text("Wird als Authorization-Bearer-Header gesendet. Leer lassen = ohne Token (nur LAN).")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Section {
                    Button("Auf Standard zurücksetzen") { url = AppConfig.defaultBackend }
                }
            }
            .navigationTitle("Einstellungen")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Fertig") {
                        AppConfig.backendURLString = trimmed
                        AppConfig.apiToken = token
                        dismiss()
                    }
                    .disabled(!isValid)
                }
            }
        }
    }

    private func testConnection() async {
        testing = true
        testResult = nil
        defer { testing = false }
        guard let u = URL(string: trimmed) else { testResult = "Ungültige URL"; return }
        // /api/identify → /health am selben Host
        let healthURL = u.deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("health")
        var req = URLRequest(url: healthURL)
        req.timeoutInterval = 8
        do {
            let (_, resp) = try await URLSession.shared.data(for: req)
            let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
            testResult = code == 200 ? "✓ Erreichbar" : "Antwort \(code)"
        } catch {
            testResult = "✗ Nicht erreichbar"
        }
    }
}

/// No live camera (Simulator) → demonstrate rapid-fire batching with real sample OCR.
private struct SimulatorDemoView: View {
    @ObservedObject var queue: ScanQueue

    private let samples: [(texts: [String], barcodes: [String])] = [
        (["Pikachu", "HP 60", "60/64", "Jungle"], []),
        (["CGC", "8.5", "NM/MINT+", "ナツメのゲンガー", "Challenge from the Darkness"], ["6118284090"]),
        (["メガルカリオ ex", "HP 330", "092/063", "MUR"], []),
        (["Team Rockets Mewtu ex", "Basis", "KP 280", "281/217"], []),
        (["Gengar", "HP 120", "SWSH052"], []),
        (["Victini", "KP 80", "172/086", "V-Stärke"], []),
        (["Mewtu GX", "KP 190", "78/73", "Psychostoß"], []),
        (["Mew ex", "232/091", "Paldean Fates"], []),
    ]

    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: "camera.viewfinder").font(.system(size: 40)).foregroundStyle(.secondary)
            Text("Simulator – kein Live-Scanner").font(.headline)
            Text("DataScannerViewController braucht eine echte Kamera. Hier feuerst du die \(samples.count) Beispiel-Karten als Batch ab — beobachte unten, wie die Reports nacheinander einlaufen.")
                .font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center)

            Button {
                for s in samples {
                    queue.enqueue(hints: CardRecognizer.recognize(texts: s.texts, barcodes: s.barcodes), imageData: nil)
                }
            } label: {
                Label("Rapid-Fire: \(samples.count) Karten abfeuern", systemImage: "bolt.fill")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)

            Button("+1 Karte") {
                let s = samples.randomElement()!
                queue.enqueue(hints: CardRecognizer.recognize(texts: s.texts, barcodes: s.barcodes), imageData: nil)
            }
            .buttonStyle(.bordered)
            Spacer()
        }
        .padding()
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(.systemBackground))
        .onAppear {
            if queue.jobs.isEmpty {
                for s in samples {
                    queue.enqueue(hints: CardRecognizer.recognize(texts: s.texts, barcodes: s.barcodes), imageData: nil)
                }
            }
        }
    }
}
