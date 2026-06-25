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
/// Decoding is TOLERANT: a partial or slightly-mistyped backend response (e.g. price as a
/// "12,50" string, or a missing field) still yields a usable card instead of throwing and
/// silently discarding the whole correct identification.
struct IdentifiedCard: Equatable {
    var name: String
    var set: String?
    var number: String?
    var grade: String?
    var language: String?
    var marketEur: Double?
    var cmUrl: String?
    var confidence: String      // "HIGH" | "LOW"
    var via: String             // "on-device" | "unreachable" | "ximilar" | "cloud" ...

    init(name: String, set: String?, number: String?, grade: String?, language: String?,
         marketEur: Double?, cmUrl: String?, confidence: String, via: String) {
        self.name = name; self.set = set; self.number = number; self.grade = grade
        self.language = language; self.marketEur = marketEur; self.cmUrl = cmUrl
        self.confidence = confidence; self.via = via
    }
}

extension IdentifiedCard: Decodable {
    enum CodingKeys: String, CodingKey {
        case name, set, number, grade, language, marketEur, cmUrl, confidence, via
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = (try? c.decode(String.self, forKey: .name)) ?? "Unbekannte Karte"
        set = try? c.decode(String.self, forKey: .set)
        number = try? c.decode(String.self, forKey: .number)
        grade = try? c.decode(String.self, forKey: .grade)
        language = try? c.decode(String.self, forKey: .language)
        if let n = try? c.decode(Double.self, forKey: .marketEur) {
            marketEur = n
        } else if let s = try? c.decode(String.self, forKey: .marketEur) {
            marketEur = Double(s.replacingOccurrences(of: ",", with: "."))
        } else {
            marketEur = nil
        }
        cmUrl = try? c.decode(String.self, forKey: .cmUrl)
        confidence = (try? c.decode(String.self, forKey: .confidence)) ?? "LOW"
        via = (try? c.decode(String.self, forKey: .via)) ?? "cloud"
    }
}
