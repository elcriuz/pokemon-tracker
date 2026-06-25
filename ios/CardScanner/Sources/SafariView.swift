import SwiftUI
import SafariServices

/// In-app browser. Opening a Cardmarket link with SFSafariViewController keeps the app in the
/// FOREGROUND — so the still-running batch scans (Bright Data, 10-40s) are NOT suspended/cancelled
/// the way they are when tapping out to the external Safari app.
struct SafariView: UIViewControllerRepresentable {
    let url: URL
    func makeUIViewController(context: Context) -> SFSafariViewController {
        SFSafariViewController(url: url)
    }
    func updateUIViewController(_ vc: SFSafariViewController, context: Context) {}
}
