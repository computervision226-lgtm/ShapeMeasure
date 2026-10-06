import Foundation

struct CloudOptions: Equatable, Codable {
    var voxelMetres: Float = 0.01
    var maximumDepthMetres: Float = 4
    var minimumConfidence: UInt8 = 2
    private(set) var minimumDepthMetres: Float = 0.2
    private(set) var sampleStep: Int = 2
    private(set) var frameIntervalSeconds: Double = 0.1
    private(set) var pointLimit: Int = 500_000
}

struct CloudBounds: Codable {
    var minimumXYZMetres: [Double]
    var maximumXYZMetres: [Double]
    init(points: [SCCloudPoint]) {
        minimumXYZMetres = [Double.infinity, Double.infinity, Double.infinity]
        maximumXYZMetres = [-Double.infinity, -Double.infinity, -Double.infinity]
        for p in points {
            let values = [Double(p.x), Double(p.y), Double(p.z)]
            for i in 0..<3 {
                minimumXYZMetres[i] = min(minimumXYZMetres[i], values[i])
                maximumXYZMetres[i] = max(maximumXYZMetres[i], values[i])
            }
        }
    }
}

struct CloudReport: Codable {
    var schemaVersion = 1
    var id: UUID
    var createdAt: Date
    var source = "ARKit ARFrame.sceneDepth (rear LiDAR), not raw laser returns"
    var units = "metres"
    var coordinates = "Right-handed ARKit session world; Y up; X/Z horizontal; session origin; no geographic CRS"
    var sampling = "First accepted measured sample per voxel; no mesh generation, hole filling, or pose correction after insertion"
    var colours = "Camera NV12 YCbCr converted using BT.601 or BT.709; absent matrix assumes BT.601; unsupported colour format uses neutral grey"
    var accuracy = "ARKit depth and pose estimates. Voxel spacing is a sampling setting, not an accuracy guarantee. Validate against an independent reference."
    var confidenceLevels = ["0": "low", "1": "medium", "2": "high"]
    var pointCount: Int
    var processedDepthFrames: Int
    var colourUnavailableFrames: Int
    var recordedSeconds: Double
    var settings: CloudOptions
    var bounds: CloudBounds
    var depthResolutionPixels: [Int]
    var lastDepthIntrinsicsFXFYCXCY: [Float]
    var pauseReason: String?
    var device: String
    var operatingSystem: String
}

struct SavedCloud: Identifiable {
    var id: String { ply.lastPathComponent }
    var ply: URL
    var createdAt: Date
    var pointCount: Int
    var stem: URL { ply.deletingPathExtension() }
    var files: [URL] {
        [ply, stem.appendingPathExtension("xyz"), stem.appendingPathExtension("json")]
            .filter { FileManager.default.fileExists(atPath: $0.path) }
    }
}

enum LiDARFiles {
    static var root: URL {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("PointClouds", isDirectory: true)
    }
    static func savedClouds() -> [SavedCloud] {
        let urls = (try? FileManager.default.contentsOfDirectory(at: root, includingPropertiesForKeys: nil)) ?? []
        return urls.filter { $0.pathExtension == "ply" }.compactMap { url in
            let json = url.deletingPathExtension().appendingPathExtension("json")
            guard let report = try? ScanFiles.read(CloudReport.self, from: json) else { return nil }
            return SavedCloud(ply: url, createdAt: report.createdAt, pointCount: report.pointCount)
        }.sorted { $0.createdAt > $1.createdAt }
    }
    static func save(points: [SCCloudPoint], report: CloudReport) throws -> SavedCloud {
        guard !points.isEmpty else { throw ScanFailure.message("Record some LiDAR points first.") }
        let fm = FileManager.default
        try fm.createDirectory(at: root, withIntermediateDirectories: true)
        let stem = root.appendingPathComponent("LiDAR-\(report.id.uuidString)")
        let ply = stem.appendingPathExtension("ply")
        let xyz = stem.appendingPathExtension("xyz")
        let json = stem.appendingPathExtension("json")
        // Publish all three files only after successful export. A failed export
        // never appears in the saved list and cannot remove an older scan.
        let stage = root.appendingPathComponent("Export-\(UUID().uuidString)", isDirectory: true)
        try fm.createDirectory(at: stage, withIntermediateDirectories: true)
        defer { try? fm.removeItem(at: stage) }
        var error = [CChar](repeating: 0, count: 512)
        for (name, isPLY) in [("cloud.ply", true), ("cloud.xyz", false)] {
            let output = stage.appendingPathComponent(name)
            let ok = points.withUnsafeBufferPointer { buffer in
                output.path.withCString {
                    if isPLY { return sc_write_cloud_ply($0, buffer.baseAddress, Int32(buffer.count), &error, 512) }
                    return sc_write_cloud_xyz($0, buffer.baseAddress, Int32(buffer.count), &error, 512)
                }
            }
            guard ok != 0 else { throw ScanFailure.message(String(cString: error)) }
        }
        try ScanFiles.write(report, to: stage.appendingPathComponent("report.json"))
        var moved: [URL] = []
        do {
            for (name, destination) in [("cloud.ply", ply), ("cloud.xyz", xyz), ("report.json", json)] {
                try fm.moveItem(at: stage.appendingPathComponent(name), to: destination)
                moved.append(destination)
            }
        } catch {
            for url in moved { try? fm.removeItem(at: url) }
            throw error
        }
        return SavedCloud(ply: ply, createdAt: report.createdAt, pointCount: points.count)
    }
}
