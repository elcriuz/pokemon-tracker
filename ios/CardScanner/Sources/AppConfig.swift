import Foundation

/// App configuration. The backend URL is editable at runtime (Einstellungen) and persisted in
/// UserDefaults, so you can point the app at whatever host your iPhone can reach (same WLAN
/// or Tailscale). Empty = on-device-only (no price).
enum AppConfig {
    static let defaultBackend = "http://192.168.1.91:8088/api/identify"
    private static let key = "backendURL"

    static var backendURLString: String {
        get { UserDefaults.standard.string(forKey: key) ?? defaultBackend }
        set { UserDefaults.standard.set(newValue, forKey: key) }
    }

    static var backendURL: URL? {
        let s = backendURLString.trimmingCharacters(in: .whitespacesAndNewlines)
        return s.isEmpty ? nil : URL(string: s)
    }
}
