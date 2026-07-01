import Foundation

/// App configuration. The backend URL is editable at runtime (Einstellungen) and persisted in
/// UserDefaults, so you can point the app at whatever host your iPhone can reach (same WLAN
/// or Tailscale). Empty = on-device-only (no price).
enum AppConfig {
    static let defaultBackend = "http://192.168.1.91:8088/api/identify"
    private static let urlKey = "backendURL"
    private static let tokenKey = "apiToken"

    static var backendURLString: String {
        get { UserDefaults.standard.string(forKey: urlKey) ?? defaultBackend }
        set { UserDefaults.standard.set(newValue, forKey: urlKey) }
    }

    static var backendURL: URL? {
        let s = backendURLString.trimmingCharacters(in: .whitespacesAndNewlines)
        return s.isEmpty ? nil : URL(string: s)
    }

    /// Bearer token for the public endpoint (Cloudflare Tunnel). Empty = no auth header (LAN use).
    /// Stored in UserDefaults only — never committed.
    static var apiToken: String {
        get { (UserDefaults.standard.string(forKey: tokenKey) ?? "").trimmingCharacters(in: .whitespacesAndNewlines) }
        set { UserDefaults.standard.set(newValue.trimmingCharacters(in: .whitespacesAndNewlines), forKey: tokenKey) }
    }
}
