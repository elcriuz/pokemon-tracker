import SwiftUI
import AVFoundation
import Vision
import UIKit

/// Live camera with card-outline detection (VNDetectRectangles) drawn as a frame overlay.
/// The shutter captures a still and hands it back via model.capturePhoto — the caller enqueues
/// it immediately, so card 1 is scanned in the background WHILE you photograph card 2.
struct CameraScannerView: UIViewControllerRepresentable {
    @ObservedObject var model: ScannerViewModel

    func makeUIViewController(context: Context) -> CardCameraController {
        let vc = CardCameraController()
        model.capturePhoto = { [weak vc] in await vc?.capturePhoto() }
        return vc
    }
    func updateUIViewController(_ vc: CardCameraController, context: Context) {}
}

final class CardCameraController: UIViewController, AVCaptureVideoDataOutputSampleBufferDelegate {
    private let session = AVCaptureSession()
    private let videoOutput = AVCaptureVideoDataOutput()
    private let photoOutput = AVCapturePhotoOutput()
    private var previewLayer: AVCaptureVideoPreviewLayer!
    private let outlineLayer = CAShapeLayer()
    private let sessionQueue = DispatchQueue(label: "cam.session")
    private var photoContinuation: CheckedContinuation<UIImage?, Never>?
    private var frameCount = 0
    private var smoothed: [CGPoint]?   // last corners (normalized, bottom-left origin) for jitter smoothing

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .black
        previewLayer = AVCaptureVideoPreviewLayer(session: session)
        previewLayer.videoGravity = .resizeAspectFill
        view.layer.addSublayer(previewLayer)

        outlineLayer.fillColor = UIColor.systemYellow.withAlphaComponent(0.10).cgColor
        outlineLayer.strokeColor = UIColor.systemYellow.cgColor
        outlineLayer.lineWidth = 3
        outlineLayer.lineJoin = .round
        view.layer.addSublayer(outlineLayer)

        sessionQueue.async { [weak self] in
            self?.configure()
            self?.session.startRunning()
        }
    }

    override func viewDidLayoutSubviews() {
        super.viewDidLayoutSubviews()
        previewLayer.frame = view.bounds
        outlineLayer.frame = view.bounds
    }

    override func viewWillDisappear(_ animated: Bool) {
        super.viewWillDisappear(animated)
        sessionQueue.async { [weak self] in self?.session.stopRunning() }
    }

    private func configure() {
        session.beginConfiguration()
        session.sessionPreset = .photo
        if let cam = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back),
           let input = try? AVCaptureDeviceInput(device: cam), session.canAddInput(input) {
            session.addInput(input)
        }
        videoOutput.alwaysDiscardsLateVideoFrames = true
        videoOutput.setSampleBufferDelegate(self, queue: DispatchQueue(label: "cam.video"))
        if session.canAddOutput(videoOutput) { session.addOutput(videoOutput) }
        if session.canAddOutput(photoOutput) { session.addOutput(photoOutput) }
        session.commitConfiguration()
        DispatchQueue.main.async {
            if let c = self.previewLayer.connection, c.isVideoRotationAngleSupported(90) {
                c.videoRotationAngle = 90   // portrait
            }
        }
    }

    // MARK: - Shutter

    func capturePhoto() async -> UIImage? {
        await withCheckedContinuation { cont in
            DispatchQueue.main.async {
                self.photoContinuation = cont
                self.photoOutput.capturePhoto(with: AVCapturePhotoSettings(), delegate: self)
            }
        }
    }

    // MARK: - Live card-outline detection

    func captureOutput(_ output: AVCaptureOutput, didOutput sampleBuffer: CMSampleBuffer,
                       from connection: AVCaptureConnection) {
        guard let buffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        frameCount += 1
        if frameCount % 3 != 0 { return }   // ~10fps: genug fuers Overlay, weniger Zappeln/Last
        // Document Segmentation = ML-basiert, findet gezielt die Karte (kein Hintergrund-Rechteck).
        let request = VNDetectDocumentSegmentationRequest { [weak self] req, _ in
            let obs = (req.results as? [VNRectangleObservation])?.first
            DispatchQueue.main.async { self?.update(obs) }
        }
        try? VNImageRequestHandler(cvPixelBuffer: buffer, orientation: .right, options: [:]).perform([request])
    }

    private func update(_ obs: VNRectangleObservation?) {
        guard let obs, obs.confidence > 0.5 else {
            smoothed = nil
            outlineLayer.path = nil
            return
        }
        let target = [obs.topLeft, obs.topRight, obs.bottomRight, obs.bottomLeft]
        if let s = smoothed, s.count == 4 {
            // exponentielle Glaettung gegen Zittern
            smoothed = zip(s, target).map { CGPoint(x: $0.x * 0.55 + $1.x * 0.45, y: $0.y * 0.55 + $1.y * 0.45) }
        } else {
            smoothed = target
        }
        drawOutline(smoothed!)
    }

    private func drawOutline(_ corners: [CGPoint]) {
        guard let pl = previewLayer, corners.count == 4 else { return }
        func pt(_ p: CGPoint) -> CGPoint {
            pl.layerPointConverted(fromCaptureDevicePoint: CGPoint(x: p.x, y: 1 - p.y))
        }
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        let path = UIBezierPath()
        path.move(to: pt(corners[0]))
        for c in corners.dropFirst() { path.addLine(to: pt(c)) }
        path.close()
        outlineLayer.path = path.cgPath
        CATransaction.commit()
    }
}

extension CardCameraController: AVCapturePhotoCaptureDelegate {
    func photoOutput(_ output: AVCapturePhotoOutput, didFinishProcessingPhoto photo: AVCapturePhoto,
                     error: Error?) {
        let image = photo.fileDataRepresentation().flatMap { UIImage(data: $0) }
        DispatchQueue.main.async {
            self.photoContinuation?.resume(returning: image)
            self.photoContinuation = nil
        }
    }
}

/// On-device OCR on a captured card image → recognition hints (number/set/name/grade).
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
