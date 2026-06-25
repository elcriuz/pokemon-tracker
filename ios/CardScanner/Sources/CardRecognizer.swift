import Foundation

/// Pure, network-free parsing of on-device scan signals into a `RecognizedCard`.
/// Kept free of UIKit/VisionKit so the logic is testable and runs in the simulator demo.
enum CardRecognizer {

    // "60/64", "107/080", "172/151"
    private static let fractionNumber = try! NSRegularExpression(
        pattern: #"\b\d{1,3}\s?/\s?\d{1,3}\b"#)
    // promo / set-prefixed numbers: "SWSH052", "XY39", "SM241", "SVP 098"
    private static let promoNumber = try! NSRegularExpression(
        pattern: #"\b(SWSH|XY|SM|SVP|SV|BW|HGSS|DP|HS|PR)\s?-?\s?\d{1,3}\b"#,
        options: [.caseInsensitive])
    // grade label: "CGC 8.5", "PSA 10", "BGS 9.5". \s* (not \s?) so a slab whose company and
    // number land in separate OCR items ("CGC", "8.5") still matches once the lines are joined.
    private static let gradeLabel = try! NSRegularExpression(
        pattern: #"\b(PSA|CGC|BGS|SGC)\s*(10|9\.5|9|8\.5|8|7)\b"#,
        options: [.caseInsensitive])

    private static let nameStop: Set<String> = [
        "hp", "kp", "basis", "basic", "stage", "stufe", "trainer", "supporter", "item",
        "energy", "energie", "pokemon", "pokémon", "illus", "ex", "gx", "v", "vmax",
        "vstar", "weakness", "resistance", "schwäche", "resistenz", "rückzug", "retreat",
    ]

    static func recognize(texts: [String], barcodes: [String]) -> RecognizedCard {
        var card = RecognizedCard()
        card.rawTexts = texts
        let joined = texts.joined(separator: "  ")

        // Collector number — prefer the explicit promo/set-prefixed form, else the X/Y fraction.
        card.collectorNumber = firstMatch(promoNumber, in: joined)?.replacingOccurrences(of: " ", with: "")
            ?? firstMatch(fractionNumber, in: joined)?.replacingOccurrences(of: " ", with: "")

        // Grade label (on-device): reads the EXACT grade incl. half steps — fixes the CGC8.5→CGC10 problem.
        if let g = firstMatch(gradeLabel, in: joined) {
            card.grade = normalizeGrade(g)
        }

        // Slab cert barcode → deterministic identity + grade for graded cards.
        if let cert = barcodes.first(where: { $0.count >= 6 }) {
            card.certBarcode = cert
        }

        card.language = guessLanguage(joined)
        card.name = guessName(texts)
        card.fieldConfidence = score(card)
        return card
    }

    // MARK: - Helpers

    static func guessLanguage(_ s: String) -> String {
        let hasKana = s.unicodeScalars.contains { (0x3040...0x30FF).contains($0.value) }
        if hasKana { return "jp" }
        let hasCJK = s.unicodeScalars.contains { (0x4E00...0x9FFF).contains($0.value) }
        if hasCJK { return "zh" }   // kanji without kana → likely Chinese (heuristic; backend confirms)
        let lower = s.lowercased()
        let germanMarkers = ["basis", "entwicklung", "schwäche", "rückzug", "energie", " kp "]
        if lower.contains("ä") || lower.contains("ö") || lower.contains("ü")
            || germanMarkers.contains(where: { lower.contains($0) }) { return "de" }
        return "en"
    }

    private static let gradeWords: Set<String> = ["cgc", "psa", "bgs", "sgc", "gem", "mint", "nm", "mt"]

    static func guessName(_ texts: [String]) -> String? {
        // The card/character name sits at the TOP of the card, so the first plausible line wins —
        // picking the longest line wrongly grabbed the set name ("Challenge from the Darkness").
        for raw in texts {
            let t = raw.trimmingCharacters(in: .whitespaces)
            if t.contains("/") { continue }                              // numbers, "NM/MINT+"
            if t.rangeOfCharacter(from: .decimalDigits) != nil { continue }
            let letters = t.filter { $0.isLetter }
            guard letters.count >= 3, letters.count * 2 >= t.count else { continue }
            let low = t.lowercased()
            if nameStop.contains(low) || gradeWords.contains(low) { continue }
            return t
        }
        return nil
    }

    static func normalizeGrade(_ raw: String) -> String {
        let up = raw.uppercased()
        let company = ["PSA", "CGC", "BGS", "SGC"].first { up.contains($0) } ?? ""
        let number = up.drop { !$0.isNumber }
        return "\(company) \(number)".trimmingCharacters(in: .whitespaces)
    }

    static func score(_ c: RecognizedCard) -> Double {
        var s = 0.0
        if c.certBarcode != nil { s += 0.5 }   // slab cert is the strongest single signal
        if c.collectorNumber != nil { s += 0.3 }
        if c.name != nil { s += 0.2 }
        if c.grade != "raw" { s += 0.1 }
        return min(s, 1.0)
    }

    private static func firstMatch(_ re: NSRegularExpression, in s: String) -> String? {
        let range = NSRange(s.startIndex..., in: s)
        guard let m = re.firstMatch(in: s, range: range), let r = Range(m.range, in: s) else { return nil }
        return String(s[r])
    }
}
