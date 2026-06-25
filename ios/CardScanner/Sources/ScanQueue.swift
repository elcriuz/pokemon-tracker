import SwiftUI
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

    /// Backend for cloud IDs + real prices (see AppConfig); nil = on-device-only demo.
    var backend = BackendClient(endpoint: AppConfig.backendURL)
    private let maxConcurrent = 4

    private var running = 0
    private var pending: [(id: UUID, image: UIImage?)] = []
    private var counter = 0

    var doneCount: Int { jobs.lazy.filter { $0.status == .done }.count }
    var inFlight: Int { jobs.lazy.filter { $0.status == .queued || $0.status == .processing }.count }

    /// Fire-and-forget — never blocks the shutter.
    func enqueue(hints: RecognizedCard, image: UIImage?) {
        counter += 1
        let job = ScanJob(index: counter, hints: hints)
        jobs.insert(job, at: 0)               // newest on top
        pending.append((job.id, image))
        drain()
    }

    func clear() {
        jobs.removeAll()
        pending.removeAll()
        counter = 0
    }

    private func drain() {
        while running < maxConcurrent, !pending.isEmpty {
            let next = pending.removeFirst()
            running += 1
            update(next.id) { $0.status = .processing }
            let hints = jobs.first { $0.id == next.id }?.hints ?? RecognizedCard()
            Task {
                let result = await backend.identify(card: hints, image: next.image)
                update(next.id) {
                    $0.result = result
                    $0.status = .done
                }
                running -= 1
                drain()
            }
        }
    }

    private func update(_ id: UUID, _ change: (inout ScanJob) -> Void) {
        if let i = jobs.firstIndex(where: { $0.id == id }) { change(&jobs[i]) }
    }
}
