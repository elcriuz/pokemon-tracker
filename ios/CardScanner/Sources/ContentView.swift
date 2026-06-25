import SwiftUI
import VisionKit

struct ContentView: View {
    @StateObject private var model = ScannerViewModel()
    @StateObject private var queue = ScanQueue()

    private var scannerAvailable: Bool {
        DataScannerViewController.isSupported && DataScannerViewController.isAvailable
    }

    var body: some View {
        VStack(spacing: 0) {
            ZStack(alignment: .bottom) {
                if scannerAvailable {
                    DataScannerView(model: model).ignoresSafeArea(edges: .top)
                    shutterBar
                } else {
                    SimulatorDemoView(queue: queue)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)

            batchPanel
        }
    }

    // Live capture: each tap fires a job and returns instantly — keep shooting.
    private var shutterBar: some View {
        HStack(alignment: .center) {
            VStack(alignment: .leading, spacing: 2) {
                Text(model.live.name ?? "—").font(.subheadline).bold()
                Text([model.live.collectorNumber, model.live.grade == "raw" ? nil : model.live.grade]
                        .compactMap { $0 }.joined(separator: " · "))
                    .font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            Button {
                Task {
                    let image = await model.capturePhoto?()
                    queue.enqueue(hints: model.live, image: image)
                }
            } label: {
                Image(systemName: "camera.circle.fill")
                    .font(.system(size: 60))
                    .symbolRenderingMode(.hierarchical)
            }
            .disabled(!model.live.hasUsableHints)
        }
        .padding()
        .background(.ultraThinMaterial, in: Capsule())
        .padding()
    }

    private var batchPanel: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("Batch").font(.headline)
                Spacer()
                Text("\(queue.doneCount) fertig · \(queue.inFlight) in Arbeit")
                    .font(.caption).foregroundStyle(.secondary)
                if !queue.jobs.isEmpty {
                    Button("Leeren") { queue.clear() }.font(.caption)
                }
            }
            if queue.jobs.isEmpty {
                Text("Karten abfeuern → Reports erscheinen hier, sobald sie fertig sind.")
                    .font(.footnote).foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            ScrollView {
                LazyVStack(spacing: 8) {
                    ForEach(queue.jobs) { JobRow(job: $0) }
                }
            }
        }
        .padding()
        .frame(height: 300)
        .background(Color(.secondarySystemBackground))
    }
}

private struct JobRow: View {
    let job: ScanJob

    var body: some View {
        HStack(spacing: 12) {
            Text("#\(job.index)").font(.caption).monospaced().foregroundStyle(.secondary)
            statusDot
            VStack(alignment: .leading, spacing: 2) {
                Text(job.result?.name ?? job.hints.name ?? "wird erkannt…").bold()
                if let r = job.result {
                    Text([r.number.map { "#\($0)" }, r.grade == "raw" ? nil : r.grade,
                          r.marketEur.map { String(format: "%.2f €", $0) }]
                            .compactMap { $0 }.joined(separator: " · "))
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            Spacer()
            if job.status == .done, let r = job.result {
                Text(r.via).font(.caption2).foregroundStyle(.secondary)
            } else {
                Text(job.status.rawValue).font(.caption2).foregroundStyle(.secondary)
                if job.status == .processing { ProgressView().scaleEffect(0.7) }
            }
        }
        .padding(10)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 12))
    }

    private var statusDot: some View {
        Circle()
            .fill(job.status == .done ? Color.green : job.status == .processing ? .orange : .gray)
            .frame(width: 8, height: 8)
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
                for s in samples {   // foto-foto-foto: alle sofort einreihen
                    queue.enqueue(hints: CardRecognizer.recognize(texts: s.texts, barcodes: s.barcodes), image: nil)
                }
            } label: {
                Label("Rapid-Fire: \(samples.count) Karten abfeuern", systemImage: "bolt.fill")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)

            Button("+1 Karte") {
                let s = samples.randomElement()!
                queue.enqueue(hints: CardRecognizer.recognize(texts: s.texts, barcodes: s.barcodes), image: nil)
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
                    queue.enqueue(hints: CardRecognizer.recognize(texts: s.texts, barcodes: s.barcodes), image: nil)
                }
            }
        }
    }
}
