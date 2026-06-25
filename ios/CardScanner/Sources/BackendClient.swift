import Foundation
import UIKit

/// Talks to the recognition/price backend (`cardcheck_api.py`). Endpoint is read from AppConfig
/// at call time. On any failure it returns an on-device result with a distinct `via` so the queue
/// can show whether the card was resolved, fell back, or the server was unreachable.
///
/// Backend contract (POST JSON):
///   request : { name?, number?, setCode?, grade, certBarcode?, language?, imageBase64? }
///   response: { name, set?, number?, grade?, language?, marketEur?, cmUrl?, confidence, via }
struct BackendClient {
    /// Dedicated session: short connect/first-byte timeout (fail fast when the host is unreachable,
    /// e.g. phone not on the same WLAN/Tailscale) but a long total budget for slow Cardmarket scrapes.
    private static let session: URLSession = {
        let cfg = URLSessionConfiguration.default
        cfg.timeoutIntervalForRequest = 8
        cfg.timeoutIntervalForResource = 60
        cfg.waitsForConnectivity = false
        return URLSession(configuration: cfg)
    }()

    func identify(card: RecognizedCard, imageData: Data?) async -> IdentifiedCard {
        guard let endpoint = AppConfig.backendURL else {
            // No backend configured → simulate latency so the batch queue still visibly streams.
            try? await Task.sleep(nanoseconds: UInt64.random(in: 400_000_000...1_800_000_000))
            return onDeviceFallback(card, via: "on-device")
        }
        do {
            return try await callBackend(endpoint, card: card, imageData: imageData)
        } catch {
            #if DEBUG
            print("[BackendClient] identify failed: \(error)")
            #endif
            return onDeviceFallback(card, via: "unreachable")
        }
    }

    private func callBackend(_ url: URL, card: RecognizedCard, imageData: Data?) async throws -> IdentifiedCard {
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")

        var body: [String: Any] = ["grade": card.grade]
        body["name"] = card.name
        body["number"] = card.collectorNumber
        body["setCode"] = card.setCode
        body["certBarcode"] = card.certBarcode
        body["language"] = card.language
        if let imageData { body["imageBase64"] = imageData.base64EncodedString() }
        req.httpBody = try JSONSerialization.data(withJSONObject: body)

        let (data, resp) = try await BackendClient.session.data(for: req)
        guard (resp as? HTTPURLResponse)?.statusCode == 200 else { throw URLError(.badServerResponse) }
        return try JSONDecoder().decode(IdentifiedCard.self, from: data)
    }

    private func onDeviceFallback(_ card: RecognizedCard, via: String) -> IdentifiedCard {
        IdentifiedCard(
            name: card.name ?? "Unbekannte Karte",
            set: nil,
            number: card.collectorNumber,
            grade: card.grade,
            language: card.language,
            marketEur: nil,
            cmUrl: nil,
            confidence: card.fieldConfidence > 0.6 ? "HIGH" : "LOW",
            via: via)
    }
}
