import SwiftUI
import UIKit
import AVFoundation
import VisionKit

@MainActor
final class ScannerViewModel: ObservableObject {
    @Published var live = RecognizedCard()
    @Published var cameraStatus: AVAuthorizationStatus = AVCaptureDevice.authorizationStatus(for: .video)

    /// Set by `DataScannerView` so the UI can grab a high-res still for the backend call.
    var capturePhoto: (() async -> UIImage?)?

    /// Called by the scanner delegate with the FULL current set of recognized items.
    func setAll(texts: [String], barcodes: [String]) {
        live = CardRecognizer.recognize(texts: texts, barcodes: barcodes)
    }

    func requestCameraAccessIfNeeded() {
        guard cameraStatus == .notDetermined else { return }
        AVCaptureDevice.requestAccess(for: .video) { granted in
            Task { @MainActor in
                self.cameraStatus = granted ? .authorized : .denied
            }
        }
    }

    /// Capture a DOWNSCALED JPEG — never keep full-sensor UIImages around, or a rapid-fire
    /// batch of 12MP stills (~30-50MB each) gets the app jetsam-killed.
    func captureData() async -> Data? {
        guard let image = await capturePhoto?() else { return nil }
        return image.downscaledJPEG(maxEdge: 1280, quality: 0.6)
    }
}

extension UIImage {
    func downscaledJPEG(maxEdge: CGFloat, quality: CGFloat) -> Data? {
        let longest = max(size.width, size.height)
        guard longest > 0 else { return jpegData(compressionQuality: quality) }
        let scale = longest > maxEdge ? maxEdge / longest : 1
        let newSize = CGSize(width: size.width * scale, height: size.height * scale)
        let renderer = UIGraphicsImageRenderer(size: newSize)
        let resized = renderer.image { _ in draw(in: CGRect(origin: .zero, size: newSize)) }
        return resized.jpegData(compressionQuality: quality)
    }
}
