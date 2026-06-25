import Foundation

/// Best-effort card guess fused entirely on-device from OCR text lines + barcode payloads.
/// This is what makes the iOS path fast: number/set/name/grade are read locally in ~100-300ms,
/// so the two cloud round-trips (OpenAI Vision + Ximilar) can be skipped for the common case.
struct RecognizedCard: Equatable {
    var name: String?
    var collectorNumber: String?      // "60/64", "SWSH052", "172/151"
    var setCode: String?
    var grade: String = "raw"         // "raw" or e.g. "CGC 8.5", "PSA 10"
    var certBarcode: String?          // slab cert barcode payload (deterministic ID for graded cards)
    var language: String?             // "en" | "de" | "jp" | "zh" (heuristic)
    var rawTexts: [String] = []
    var fieldConfidence: Double = 0   // 0..1 rough on-device confidence

    var isGraded: Bool { grade != "raw" }
    var hasUsableHints: Bool { collectorNumber != nil || certBarcode != nil || name != nil }
}

/// Final identification — returned by the backend (cloud fallback + price) or the on-device stub.
struct IdentifiedCard: Decodable, Equatable {
    var name: String
    var set: String?
    var number: String?
    var grade: String?
    var language: String?
    var marketEur: Double?
    var cmUrl: String?
    var confidence: String      // "HIGH" | "LOW"
    var via: String             // "on-device" | "ximilar" | "cloud-fallback" ...
}
