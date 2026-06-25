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
    private let maxConcurrent = 4

    private var running = 0
    private var pending: [(id: UUID, hints: RecognizedCard, image: Data?)] = []
    private var counter = 0
    private var generation = 0   // bumped on clear() so orphaned in-flight Tasks no-op

    var doneCount: Int { jobs.lazy.filter { $0.status == .done }.count }
    var inFlight: Int { jobs.lazy.filter { $0.status == .queued || $0.status == .processing }.count }

    /// Fire-and-forget — never blocks the shutter.
    func enqueue(hints: RecognizedCard, imageData: Data?) {
        counter += 1
        let job = ScanJob(index: counter, hints: hints)
        jobs.insert(job, at: 0)               // newest on top
        pending.append((job.id, hints, imageData))
        drain()
    }

    func clear() {
        generation += 1
        jobs.removeAll()
        pending.removeAll()
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
