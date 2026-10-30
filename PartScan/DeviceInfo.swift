import Foundation
import UIKit

/// Identifiers sent with every scan so the server's scan log can tell phones and app builds apart.
enum DeviceInfo {
    /// Hardware model identifier such as "iPhone18,2" (UIDevice only says "iPhone").
    static var modelId: String {
        var sys = utsname()
        uname(&sys)
        let id = Mirror(reflecting: sys.machine).children.reduce(into: "") { acc, child in
            guard let value = child.value as? Int8, value != 0 else { return }
            acc.append(Character(UnicodeScalar(UInt8(bitPattern: value))))
        }
        return id.isEmpty ? "iPhone" : id
    }

    static var appVersion: String {
        let info = Bundle.main.infoDictionary ?? [:]
        let version = info["CFBundleShortVersionString"] as? String ?? "?"
        let build = info["CFBundleVersion"] as? String ?? "?"
        return "\(version) (\(build))"
    }

    static var system: String {
        "iOS " + UIDevice.current.systemVersion
    }

    static var clientString: String {
        "PartScan iOS \(appVersion) · \(modelId) · \(system)"
    }
}
