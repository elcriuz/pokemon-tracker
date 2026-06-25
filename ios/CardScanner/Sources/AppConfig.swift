import Foundation

enum AppConfig {
    /// Recognition/price backend (`cardcheck_api.py`). Set to nil for on-device-only demo.
    /// The iOS Simulator shares the Mac's network, so the LAN/Tailscale IP is reachable.
    static let backendURL = URL(string: "http://192.168.1.91:8088/api/identify")
}
