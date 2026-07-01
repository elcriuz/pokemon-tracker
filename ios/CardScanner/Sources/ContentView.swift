import SwiftUI
import AVFoundation

struct ContentView: View {
    @StateObject private var model = ScannerViewModel()
    @StateObject private var queue = ScanQueue()
    @State private var activeSheet: ActiveSheet?
    @State private var shotCount = 0
    @State private var panelHeight: CGFloat = 360    // Ankauf-Panel — per Handle aufziehbar (Bulk)
    @State private var dragStart: CGFloat? = nil

    private var hasCamera: Bool { AVCaptureDevice.default(for: .video) != nil }

    var body: some View {
        GeometryReader { geo in
            let maxPanel = max(320, geo.size.height - 220)   // Kamera nie ganz verdecken
            VStack(spacing: 0) {
                scannerLayer
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                dragHandle
                AnkaufSummaryView(
                    queue: queue,
                    onOpenSettings: { activeSheet = .settings },
                    onOpenLink: { activeSheet = .safari($0) })
                    .frame(height: min(panelHeight, maxPanel))
            }
        }
        .ignoresSafeArea(.keyboard)
        .sheet(item: $activeSheet) { sheet in
            switch sheet {
            case .settings: SettingsView()
            case .safari(let url): SafariView(url: url).ignoresSafeArea()
            }
        }
    }

    /// Griff zum Aufziehen des Ankauf-Panels (großer Stapel) — ziehen oder tippen zum Umschalten.
    private var dragHandle: some View {
        Capsule()
            .fill(Color.secondary.opacity(0.4))
            .frame(width: 40, height: 5)
            .frame(maxWidth: .infinity)
            .frame(height: 22)
            .background(Color(.secondarySystemBackground))
            .contentShape(Rectangle())
            .gesture(
                DragGesture()
                    .onChanged { v in
                        if dragStart == nil { dragStart = panelHeight }
                        panelHeight = min(max(300, (dragStart ?? panelHeight) - v.translation.height), 900)
                    }
                    .onEnded { _ in dragStart = nil }
            )
            .onTapGesture { withAnimation(.snappy) { panelHeight = panelHeight > 440 ? 360 : 640 } }
    }

    // MARK: - Live camera + shutter

    @ViewBuilder private var scannerLayer: some View {
        if !hasCamera {
            SimulatorDemoView(queue: queue)                                   // Simulator / kein Kamera-HW
        } else {
            switch model.cameraStatus {
            case .authorized:
                ZStack(alignment: .bottom) {
                    CameraScannerView(model: model).ignoresSafeArea(edges: .top)
                    shutterBar
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
            Text("\(shotCount) fotografiert")
                .font(.caption).foregroundStyle(.white)
                .padding(.horizontal, 10).padding(.vertical, 6)
                .background(.black.opacity(0.4), in: Capsule())
            Spacer()
            Button {
                UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                capture()
            } label: {
                Image(systemName: "camera.circle.fill")
                    .font(.system(size: 68)).symbolRenderingMode(.hierarchical)
                    .foregroundStyle(.white)
            }
            Spacer()
            Color.clear.frame(width: 60)   // balance the shot chip
        }
        .padding()
    }

    /// Fire-and-forget: capture the still, then OCR + enqueue in the background — the user keeps
    /// shooting while card 1 is already being scanned. Reports stream into the batch list below.
    private func capture() {
        shotCount += 1
        Task {
            guard let image = await model.capturePhoto?() else { return }
            Task.detached(priority: .userInitiated) {
                let hints = OnDeviceOCR.recognize(image)
                let data = image.downscaledJPEG(maxEdge: 1400, quality: 0.7)
                await MainActor.run { queue.enqueue(hints: hints, imageData: data) }
            }
        }
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
