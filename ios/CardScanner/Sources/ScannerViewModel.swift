import SwiftUI
import UIKit
import VisionKit

@MainActor
final class ScannerViewModel: ObservableObject {
    @Published var live = RecognizedCard()
    @Published var result: IdentifiedCard?
    @Published var isResolving = false
    @Published var lastImage: UIImage?

    /// Backend for cloud fallback + real prices (see AppConfig).
    var backend = BackendClient(endpoint: AppConfig.backendURL)

    /// Set by `DataScannerView` so the UI can grab a high-res still for the backend call.
    var capturePhoto: (() async -> UIImage?)?

    /// Called by the scanner delegate with the FULL current set of recognized items.
    func setAll(texts: [String], barcodes: [String]) {
        live = CardRecognizer.recognize(texts: texts, barcodes: barcodes)
    }

    func resolve() async {
        isResolving = true
        defer { isResolving = false }
        let image = await capturePhoto?()
        lastImage = image
        result = await backend.identify(card: live, image: image)
    }

    /// Simulator demo (no camera): feed sample OCR through the on-device recognizer.
    func runDemo(_ sample: [String], barcodes: [String] = []) {
        setAll(texts: sample, barcodes: barcodes)
        result = nil
    }
}
