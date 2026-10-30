import Combine
import Foundation

/// Where the reconstruction server is (a Colab notebook's public link or a Hugging Face Space) and how to
/// authenticate. Persisted in UserDefaults.
final class ServerSettings: ObservableObject {
    @Published var baseURL: String { didSet { defaults.set(baseURL, forKey: "serverURL") } }
    @Published var accessKey: String { didSet { defaults.set(accessKey, forKey: "accessKey") } }
    @Published var hfToken: String { didSet { defaults.set(hfToken, forKey: "hfToken") } }
    @Published var frameStride: Int { didSet { defaults.set(frameStride, forKey: "frameStride") } }

    private let defaults = UserDefaults.standard

    init() {
        baseURL = defaults.string(forKey: "serverURL") ?? ""
        accessKey = defaults.string(forKey: "accessKey") ?? ""
        hfToken = defaults.string(forKey: "hfToken") ?? ""
        let stride = defaults.integer(forKey: "frameStride")
        frameStride = stride == 0 ? 2 : stride
    }

    var trimmedURL: String { baseURL.trimmingCharacters(in: .whitespacesAndNewlines) }

    var isConfigured: Bool {
        guard let url = URL(string: trimmedURL), let scheme = url.scheme, url.host != nil else { return false }
        return scheme == "http" || scheme == "https"
    }

    /// Reads the QR code shown by the notebook (`https://xxx.gradio.live/?k=KEY`) or any plain server URL.
    @discardableResult
    func apply(scanned text: String) -> Bool {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard var comps = URLComponents(string: trimmed), let scheme = comps.scheme,
              scheme == "http" || scheme == "https", comps.host != nil else { return false }
        if let key = comps.queryItems?.first(where: { $0.name == "k" })?.value, !key.isEmpty {
            accessKey = key
        }
        comps.queryItems = nil
        comps.fragment = nil
        var path = comps.path
        while path.hasSuffix("/") { path.removeLast() }
        comps.path = path
        baseURL = comps.string ?? trimmed
        return true
    }
}
