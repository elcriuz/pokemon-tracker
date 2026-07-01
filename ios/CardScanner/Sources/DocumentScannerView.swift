import SwiftUI
import VisionKit
import Vision
import UIKit

/// Native document scanner (VNDocumentCameraViewController). Detects the card's edges/outline
/// live, shows the frame, auto-captures, perspective-corrects and crops — all built by Apple.
/// Scan several cards in one session; on "Sichern" every cropped page is returned via `onScans`.
struct DocumentScannerView: UIViewControllerRepresentable {
    var onScans: ([UIImage]) -> Void
    var onFinish: () -> Void

    func makeUIViewController(context: Context) -> VNDocumentCameraViewController {
        let vc = VNDocumentCameraViewController()
        vc.delegate = context.coordinator
        return vc
    }

    func updateUIViewController(_ vc: VNDocumentCameraViewController, context: Context) {}

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    final class Coordinator: NSObject, VNDocumentCameraViewControllerDelegate {
        let parent: DocumentScannerView
        init(_ parent: DocumentScannerView) { self.parent = parent }

        func documentCameraViewController(_ controller: VNDocumentCameraViewController,
                                          didFinishWith scan: VNDocumentCameraScan) {
            var images: [UIImage] = []
            for i in 0..<scan.pageCount { images.append(scan.imageOfPage(at: i)) }
            parent.onScans(images)
            parent.onFinish()
        }

        func documentCameraViewControllerDidCancel(_ controller: VNDocumentCameraViewController) {
            parent.onFinish()
        }

        func documentCameraViewController(_ controller: VNDocumentCameraViewController,
                                          didFailWithError error: Error) {
            parent.onFinish()
        }
    }
}

/// On-device OCR on a cropped card image → recognition hints (number/set/name/grade), so the
/// batch row shows something immediately while the backend resolves the price.
enum OnDeviceOCR {
    static func recognize(_ image: UIImage) -> RecognizedCard {
        guard let cg = image.cgImage else { return RecognizedCard() }
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.usesLanguageCorrection = false
        let handler = VNImageRequestHandler(cgImage: cg, orientation: .up, options: [:])
        try? handler.perform([request])
        let texts = (request.results as? [VNRecognizedTextObservation])?
            .compactMap { $0.topCandidates(1).first?.string } ?? []
        return CardRecognizer.recognize(texts: texts, barcodes: [])
    }
}
