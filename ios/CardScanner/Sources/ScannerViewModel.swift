import SwiftUI
import UIKit
import AVFoundation

@MainActor
final class ScannerViewModel: ObservableObject {
    @Published var cameraStatus: AVAuthorizationStatus = AVCaptureDevice.authorizationStatus(for: .video)

    /// Set by CameraScannerView so the shutter can grab the current still.
    var capturePhoto: (() async -> UIImage?)?

    func requestCameraAccessIfNeeded() {
        guard cameraStatus == .notDetermined else { return }
        AVCaptureDevice.requestAccess(for: .video) { granted in
            Task { @MainActor in
                self.cameraStatus = granted ? .authorized : .denied
            }
        }
    }
}

extension UIImage {
    /// Downscaled JPEG — never send full-sensor stills; caps upload size + memory for rapid batches.
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
