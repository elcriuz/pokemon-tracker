import SwiftUI
import Foundation
import UIKit

/// One queued scan. Fired instantly on capture; resolved in the background.
struct ScanJob: Identifiable {
    let id = UUID()
    let index: Int
    var hints: RecognizedCard
    var status: Status = .queued
    var result: IdentifiedCard?

    enum Status: String {
        case queued = "Warteschlange"
        case processing = "läuft…"
        case done = "fertig"
        case failed = "Fehler"
    }
}

/// Rapid-fire batch queue. `enqueue` returns immediately (the user keeps shooting), and up to
/// `maxConcurrent` jobs are resolved in the background — reports stream into `jobs` as they finish.
/// Mirrors the server's proven fire-and-forget behaviour (25 cards → 8.2s wall-clock in testing).
@MainActor
final class ScanQueue: ObservableObject {
    @Published private(set) var jobs: [ScanJob] = []

    var backend = BackendClient()
    // Hoch, damit ein ganzer Stapel (z.B. 20 Karten) quasi gleichzeitig laeuft: Wall-Clock ~=
    // Dauer EINER Karte statt Summe. Backend/Bright-Data drosseln serverseitig (BD-Semaphore).
    private let maxConcurrent = 16

    /// Ab diesem Marktwert (€) gilt eine Karte als "High" — für den High-Filter am Tresen.
    static let highValueThreshold = 20.0

    private var running = 0
    private var pending: [(id: UUID, hints: RecognizedCard, image: Data?)] = []
    private var images: [UUID: Data] = [:]   // fürs erneute Preis-Suchen (Retry) aufbewahrt
    private var counter = 0
    private var generation = 0   // bumped on clear() so orphaned in-flight Tasks no-op

    var doneCount: Int { jobs.lazy.filter { $0.status == .done }.count }
    var inFlight: Int { jobs.lazy.filter { $0.status == .queued || $0.status == .processing }.count }

    // MARK: - Ankauf-Summary (Cardshop-Tresen)

    /// Karten mit gültigem Marktpreis (>0) — zählen in die Summe.
    var pricedJobs: [ScanJob] { jobs.filter { ($0.result?.marketEur ?? 0) > 0 } }
    /// Gesamt-Cardmarket-Marktwert aller bepreisten Karten.
    var totalMarketEur: Double { jobs.reduce(0) { $0 + ($1.result?.marketEur ?? 0) } }
    /// Ankaufswert = Marktwert × Satz (was der Shop auszahlt).
    func totalBuyEur(ratePercent: Int) -> Double { totalMarketEur * Double(ratePercent) / 100 }
    /// Fertig verarbeitet (erkannt oder Fehler) — für den Fortschrittsbalken.
    var processedCount: Int { jobs.lazy.filter { $0.status == .done || $0.status == .failed }.count }
    /// Verarbeitet, aber ohne Preis (kein CM-Treffer) oder Serverfehler — braucht Nacharbeit.
    var noPriceCount: Int {
        jobs.lazy.filter { ($0.status == .done || $0.status == .failed) && ($0.result?.marketEur ?? 0) <= 0 }.count
    }
    var highValueCount: Int { jobs.lazy.filter { ($0.result?.marketEur ?? 0) >= ScanQueue.highValueThreshold }.count }

    /// Fire-and-forget — never blocks the shutter.
    func enqueue(hints: RecognizedCard, imageData: Data?) {
        counter += 1
        let job = ScanJob(index: counter, hints: hints)
        jobs.insert(job, at: 0)               // newest on top
        images[job.id] = imageData
        pending.append((job.id, hints, imageData))
        drain()
    }

    /// Erneuter Backend-Versuch für eine Karte ohne Preis/Fehler — nutzt das aufbewahrte Bild.
    func retry(_ id: UUID) {
        guard let job = jobs.first(where: { $0.id == id }) else { return }
        update(id) { $0.status = .queued; $0.result = nil }
        pending.append((id, job.hints, images[id]))
        drain()
    }

    /// Manuell eingetragener Preis (€) — z.B. wenn Cardmarket keinen Treffer hatte. Zählt in die Summe.
    func setManualPrice(_ id: UUID, eur: Double) {
        update(id) { job in
            let base = job.result
            job.result = IdentifiedCard(
                name: base?.name ?? job.hints.name ?? "Karte",
                set: base?.set,
                number: base?.number ?? job.hints.collectorNumber,
                grade: base?.grade ?? (job.hints.isGraded ? job.hints.grade : nil),
                language: base?.language ?? job.hints.language,
                marketEur: eur,
                cmUrl: base?.cmUrl,
                confidence: base?.confidence ?? "HIGH",
                via: "manual")
            job.status = .done
        }
    }

    func clear() {
        generation += 1
        jobs.removeAll()
        pending.removeAll()
        images.removeAll()
        counter = 0
        running = 0
    }

    private func drain() {
        while running < maxConcurrent, !pending.isEmpty {
            let next = pending.removeFirst()
            running += 1
            update(next.id) { $0.status = .processing }
            let gen = generation
            Task { [weak self] in
                guard let self else { return }
                // Keep the request alive for ~30s if the app gets backgrounded (e.g. user tapped
                // away) so in-flight Bright-Data scrapes aren't cut. (Tapping a Cardmarket link
                // now opens in-app via SFSafariViewController, so the app stays foregrounded anyway.)
                let bg = UIApplication.shared.beginBackgroundTask(withName: "scan")
                defer { UIApplication.shared.endBackgroundTask(bg) }
                let result = await self.backend.identify(card: next.hints, imageData: next.image)
                guard gen == self.generation else { return }   // batch was cleared → drop stale result
                self.update(next.id) {
                    $0.result = result
                    $0.status = (result.via == "unreachable") ? .failed : .done
                }
                self.running -= 1
                self.drain()
            }
        }
    }

    private func update(_ id: UUID, _ change: (inout ScanJob) -> Void) {
        if let i = jobs.firstIndex(where: { $0.id == id }) { change(&jobs[i]) }
    }
}
