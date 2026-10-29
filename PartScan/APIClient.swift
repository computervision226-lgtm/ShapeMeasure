import Foundation

enum APIError: LocalizedError {
    case noServer
    case http(Int, String)
    case badResponse

    var errorDescription: String? {
        switch self {
        case .noServer: return "No server configured. Open the Server tab and scan the notebook's QR code."
        case .http(let code, let detail):
            switch code {
            case 401: return "The server rejected the access key (401). Scan the QR code again."
            case 404: return "Not found on the server (404)."
            default: return "Server error \(code): \(detail)"
            }
        case .badResponse: return "Unexpected reply from the server."
        }
    }
}

/// Thin client for the REST API in partscan/api.py.
final class APIClient {
    private let settings: ServerSettings

    init(settings: ServerSettings) {
        self.settings = settings
    }

    private var base: URL? {
        guard settings.isConfigured else { return nil }
        return URL(string: settings.trimmedURL)
    }

    func url(_ path: String, query: [String: String] = [:]) throws -> URL {
        guard let base else { throw APIError.noServer }
        var url = base
        for part in path.split(separator: "/") {
            url.appendPathComponent(String(part))
        }
        guard !query.isEmpty, var comps = URLComponents(url: url, resolvingAgainstBaseURL: false) else { return url }
        comps.queryItems = query.map { URLQueryItem(name: $0.key, value: $0.value) }
        return comps.url ?? url
    }

    func request(_ path: String, method: String = "GET", query: [String: String] = [:],
                 timeout: TimeInterval = 60) throws -> URLRequest {
        var r = URLRequest(url: try url(path, query: query))
        r.httpMethod = method
        r.timeoutInterval = timeout
        addAuth(&r)
        return r
    }

    func addAuth(_ r: inout URLRequest) {
        let key = settings.accessKey.trimmingCharacters(in: .whitespacesAndNewlines)
        if !key.isEmpty { r.setValue(key, forHTTPHeaderField: "X-PartScan-Key") }
        let token = settings.hfToken.trimmingCharacters(in: .whitespacesAndNewlines)
        if !token.isEmpty { r.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
    }

    /// Request for a result file, usable by WKWebView: the key also travels as ?k= so the server can set
    /// its cookie and the HTML report's images load as well.
    func fileRequest(jobId: String, key: String? = nil, raw name: String? = nil) throws -> URLRequest {
        var query: [String: String] = [:]
        let k = settings.accessKey.trimmingCharacters(in: .whitespacesAndNewlines)
        if !k.isEmpty { query["k"] = k }
        let path: String
        if let name { path = "api/jobs/\(jobId)/raw/\(name)" } else { path = "api/jobs/\(jobId)/files/\(key ?? "zip")" }
        return try request(path, query: query, timeout: 120)
    }

    private func check(_ response: URLResponse, _ data: Data?) throws {
        guard let http = response as? HTTPURLResponse else { throw APIError.badResponse }
        guard (200..<300).contains(http.statusCode) else {
            var detail = ""
            if let data, let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                if let d = obj["detail"] as? String { detail = d } else if let d = obj["detail"] { detail = "\(d)" }
            } else if let data, let s = String(data: data, encoding: .utf8) {
                detail = String(s.prefix(200))
            }
            throw APIError.http(http.statusCode, detail)
        }
    }

    func ping() async throws -> PingResponse {
        let (data, response) = try await URLSession.shared.data(for: try request("api/ping", timeout: 20))
        try check(response, data)
        return try JSONDecoder().decode(PingResponse.self, from: data)
    }

    func job(_ id: String) async throws -> JobStatus {
        let (data, response) = try await URLSession.shared.data(for: try request("api/jobs/\(id)", timeout: 30))
        try check(response, data)
        return try JSONDecoder().decode(JobStatus.self, from: data)
    }

    func jobs() async throws -> [JobStatus] {
        let (data, response) = try await URLSession.shared.data(for: try request("api/jobs", timeout: 30))
        try check(response, data)
        return try JSONDecoder().decode(JobList.self, from: data).jobs
    }

    /// Uploads the scan zip as multipart/form-data (streamed from a temporary file, so a 500 MB scan does
    /// not have to fit in memory) and returns the queued job.
    func upload(zip: URL, options: ScanOptions, progress: @escaping (Double) -> Void) async throws -> JobStatus {
        var r = try request("api/scans", method: "POST", timeout: 600)
        let boundary = "PartScan-" + UUID().uuidString
        r.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        let body = try Self.multipartBody(boundary: boundary, fields: options.formFields, fileField: "file",
                                          fileURL: zip, mimeType: "application/zip")
        defer { try? FileManager.default.removeItem(at: body) }
        let delegate = UploadProgressDelegate(onProgress: progress)
        let (data, response) = try await URLSession.shared.upload(for: r, fromFile: body, delegate: delegate)
        try check(response, data)
        return try JSONDecoder().decode(JobStatus.self, from: data)
    }

    /// Downloads one result file to `destination` (replacing it).
    func download(jobId: String, key: String, to destination: URL) async throws -> URL {
        let (tmp, response) = try await URLSession.shared.download(for: try fileRequest(jobId: jobId, key: key))
        try check(response, nil)
        let fm = FileManager.default
        try? fm.removeItem(at: destination)
        try fm.createDirectory(at: destination.deletingLastPathComponent(), withIntermediateDirectories: true)
        try fm.moveItem(at: tmp, to: destination)
        return destination
    }

    func delete(jobId: String) async throws {
        let (data, response) = try await URLSession.shared.data(for: try request("api/jobs/\(jobId)", method: "DELETE"))
        try check(response, data)
    }

    // MARK: multipart

    static func multipartBody(boundary: String, fields: [(String, String)], fileField: String, fileURL: URL,
                              mimeType: String) throws -> URL {
        let out = FileManager.default.temporaryDirectory.appendingPathComponent("upload-\(UUID().uuidString).body")
        FileManager.default.createFile(atPath: out.path, contents: nil)
        let handle = try FileHandle(forWritingTo: out)
        defer { try? handle.close() }
        var head = ""
        for (name, value) in fields {
            head += "--\(boundary)\r\nContent-Disposition: form-data; name=\"\(name)\"\r\n\r\n\(value)\r\n"
        }
        head += "--\(boundary)\r\nContent-Disposition: form-data; name=\"\(fileField)\"; filename=\"\(fileURL.lastPathComponent)\"\r\n"
        head += "Content-Type: \(mimeType)\r\n\r\n"
        try handle.write(contentsOf: Data(head.utf8))
        let input = try FileHandle(forReadingFrom: fileURL)
        defer { try? input.close() }
        while true {
            let chunk = input.readData(ofLength: 4 << 20)
            if chunk.isEmpty { break }
            try handle.write(contentsOf: chunk)
        }
        try handle.write(contentsOf: Data("\r\n--\(boundary)--\r\n".utf8))
        return out
    }
}

final class UploadProgressDelegate: NSObject, URLSessionTaskDelegate {
    private let onProgress: (Double) -> Void

    init(onProgress: @escaping (Double) -> Void) {
        self.onProgress = onProgress
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didSendBodyData bytesSent: Int64,
                    totalBytesSent: Int64, totalBytesExpectedToSend: Int64) {
        guard totalBytesExpectedToSend > 0 else { return }
        onProgress(Double(totalBytesSent) / Double(totalBytesExpectedToSend))
    }
}
