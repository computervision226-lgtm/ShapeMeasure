import Foundation

// MARK: - Server API (partscan/api.py)

struct JobStatus: Codable, Identifiable {
    let jobId: String
    let status: String            // queued | running | done | error
    let progress: Double
    let message: String?
    let error: String?
    let createdAt: String?
    let summary: JobSummary?
    let rows: [[String]]?
    let quality: [[String]]?
    let warnings: [String]?
    let files: [String: String]?

    var id: String { jobId }
    var isFinished: Bool { status == "done" || status == "error" }
    var isDone: Bool { status == "done" }
}

struct JobSummary: Codable {
    let length: Double
    let width: Double
    let height: Double
    let how: String?
    let holes: Int?
    let roughHoles: Int?
    let scaleReference: String?
    let keyframes: Int?
    let seconds: Double?

    var text: String { String(format: "%.2f × %.2f × %.2f mm", length, width, height) }
}

struct JobList: Codable {
    let jobs: [JobStatus]
}

struct PingResponse: Codable {
    let ok: Bool
    let app: String?
    let version: String?
    let auth: Bool?
    let device: String?
    let jobsQueued: Int?
}

// MARK: - Local scans

struct ScanRecord: Codable, Identifiable, Equatable {
    var id: String                 // folder name under Documents/Scans
    var createdAt: Date
    var frames: Int
    var seconds: Double
    var bytes: Int64
    var videoWidth: Int
    var videoHeight: Int
    var status: String             // recorded | uploading | processing | done | error
    var jobId: String?
    var serverURL: String?
    var summaryText: String?
    var note: String?

    var title: String {
        let f = DateFormatter()
        f.dateStyle = .medium
        f.timeStyle = .short
        return f.string(from: createdAt)
    }

    var sizeText: String { ByteCountFormatter.string(fromByteCount: bytes, countStyle: .file) }
}

struct ScanOptions {
    var preset = "Accurate (recommended)"
    var useBoard = true
    var boardType = "Standard 8x6 (Letter / A4)"
    var checkMM = "100"
    var checkOn = "printed 100 mm ruler"
    var material = "Steel"
    var refName = ""
    var refMM = ""
    var refApply = false

    static let presets = ["Fast (fewer frames)", "Accurate (recommended)", "Max detail (slow)"]
    static let boards = ["Standard 8x6 (Letter / A4)", "Large 11x7 (A3 / Tabloid)"]
    static let checks = ["printed 100 mm ruler", "6 squares between inner corners"]
    static let materials = ["Steel", "Stainless steel", "Aluminium 6061", "Titanium Ti-6Al-4V", "Brass", "Copper",
                            "Cast iron", "PLA", "ABS", "Nylon (PA12)", "POM / Delrin", "None"]
    static let references = ["", "Length (X)", "Width (Y)", "Height (Z)", "Largest hole diameter",
                             "Smallest hole diameter"]

    var formFields: [(String, String)] {
        [("preset", preset), ("use_board", useBoard ? "1" : "0"), ("board_type", boardType),
         ("check_mm", checkMM), ("check_on", checkOn), ("material", material), ("ref_name", refName),
         ("ref_mm", refMM.isEmpty ? "0" : refMM), ("ref_apply", refApply ? "1" : "0"), ("client", DeviceInfo.clientString)]
    }
}
