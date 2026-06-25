import Foundation
import UIKit

/// Talks to your recognition/price backend. When `endpoint` is nil or the call fails, it
/// falls back to a purely on-device result so the prototype still demonstrates the full flow.
///
/// Backend contract (POST JSON) — wire this to a thin wrapper around the existing
/// Python `identify_card()` + `scrape_cardmarket_prices()`:
///   request : { name?, number?, setCode?, grade, certBarcode?, language?, imageBase64? }
///   response: { name, set?, number?, grade?, language?, marketEur?, cmUrl?, confidence, via }
struct BackendClient {
    var endpoint: URL?

    func identify(card: RecognizedCard, image: UIImage?) async -> IdentifiedCard {
        if let endpoint, let response = try? await callBackend(endpoint, card: card, image: image) {
            return response
        }
        if endpoint == nil {
            // Demo only: simulate per-card processing latency so the batch queue visibly streams.
            try? await Task.sleep(nanoseconds: UInt64.random(in: 400_000_000...1_800_000_000))
        }
        return onDeviceFallback(card)
    }

    private func callBackend(_ url: URL, card: RecognizedCard, image: UIImage?) async throws -> IdentifiedCard {
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 60   // Cardmarket scrapes can take 20-40s; don't fall back to on-device early

        var body: [String: Any] = ["grade": card.grade]
        body["name"] = card.name
        body["number"] = card.collectorNumber
        body["setCode"] = card.setCode
        body["certBarcode"] = card.certBarcode
        body["language"] = card.language
        if let data = image?.jpegData(compressionQuality: 0.7) {
            body["imageBase64"] = data.base64EncodedString()
        }
        req.httpBody = try JSONSerialization.data(withJSONObject: body)

        let (data, resp) = try await URLSession.shared.data(for: req)
        guard (resp as? HTTPURLResponse)?.statusCode == 200 else { throw URLError(.badServerResponse) }
        return try JSONDecoder().decode(IdentifiedCard.self, from: data)
    }

    private func onDeviceFallback(_ card: RecognizedCard) -> IdentifiedCard {
        IdentifiedCard(
            name: card.name ?? "Unbekannte Karte",
            set: nil,
            number: card.collectorNumber,
            grade: card.grade,
            language: card.language,
            marketEur: nil,
            cmUrl: nil,
            confidence: card.fieldConfidence > 0.6 ? "HIGH" : "LOW",
            via: "on-device")
    }
}
