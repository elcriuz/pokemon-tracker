import SwiftUI
import VisionKit

/// Wraps VisionKit's live `DataScannerViewController`. Streams the full set of recognized
/// text + barcodes to the view model on every frame update — that is the on-device fast path.
struct DataScannerView: UIViewControllerRepresentable {
    @ObservedObject var model: ScannerViewModel

    func makeUIViewController(context: Context) -> DataScannerViewController {
        let scanner = DataScannerViewController(
            recognizedDataTypes: [.text(), .barcode()],
            qualityLevel: .balanced,
            recognizesMultipleItems: true,
            isHighFrameRateTrackingEnabled: true,
            isHighlightingEnabled: true)
        scanner.delegate = context.coordinator

        // Expose a high-res capture for the "Identifizieren" action.
        model.capturePhoto = { [weak scanner] in
            guard let scanner else { return nil }
            return try? await scanner.capturePhoto()
        }

        try? scanner.startScanning()
        return scanner
    }

    func updateUIViewController(_ uiViewController: DataScannerViewController, context: Context) {}

    func makeCoordinator() -> Coordinator { Coordinator(model: model) }

    final class Coordinator: NSObject, DataScannerViewControllerDelegate {
        let model: ScannerViewModel
        init(model: ScannerViewModel) { self.model = model }

        func dataScanner(_ ds: DataScannerViewController, didAdd added: [RecognizedItem], allItems: [RecognizedItem]) {
            push(allItems)
        }
        func dataScanner(_ ds: DataScannerViewController, didUpdate updated: [RecognizedItem], allItems: [RecognizedItem]) {
            push(allItems)
        }
        func dataScanner(_ ds: DataScannerViewController, didRemove removed: [RecognizedItem], allItems: [RecognizedItem]) {
            push(allItems)
        }

        private func push(_ items: [RecognizedItem]) {
            var texts: [String] = []
            var barcodes: [String] = []
            for item in items {
                switch item {
                case .text(let t): texts.append(t.transcript)
                case .barcode(let b): if let s = b.payloadStringValue { barcodes.append(s) }
                @unknown default: break
                }
            }
            Task { @MainActor in
                self.model.setAll(texts: texts, barcodes: barcodes)
            }
        }
    }
}
