import Foundation
import SceneKit
import simd

struct Point3: Codable, Hashable {
    var x: Double
    var y: Double
    var z: Double
    init(_ x: Double, _ y: Double, _ z: Double) { self.x = x; self.y = y; self.z = z }
    init(_ p: SCNVector3) { self.init(Double(p.x), Double(p.y), Double(p.z)) }
    var vector: SCNVector3 { SCNVector3(Float(x), Float(y), Float(z)) }
    var cPoint: SCPoint3 { SCPoint3(x: x, y: y, z: z) }
    func distance(to other: Self) -> Double {
        sqrt(pow(x - other.x, 2) + pow(y - other.y, 2) + pow(z - other.z, 2))
    }
}

struct Triangle: Codable {
    var a: Int32
    var b: Int32
    var c: Int32
    var cTriangle: SCTriangle { SCTriangle(a: a, b: b, c: c) }
}

struct MeshData: Codable {
    var vertices: [Point3]
    var triangles: [Triangle]

    static var demo: Self {
        // Synthetic 20 x 10 x 5 cm closed box; model Y is up.
        let v = [Point3(0, 0, 0), Point3(0.2, 0, 0), Point3(0.2, 0, 0.1), Point3(0, 0, 0.1),
                 Point3(0, 0.05, 0), Point3(0.2, 0.05, 0), Point3(0.2, 0.05, 0.1), Point3(0, 0.05, 0.1)]
        let faces: [[Int32]] = [[0,1,2],[0,2,3],[4,6,5],[4,7,6],
                               [0,4,5],[0,5,1],[1,5,6],[1,6,2],
                               [2,6,7],[2,7,3],[3,7,4],[3,4,0]]
        return Self(vertices: v, triangles: faces.map { Triangle(a: $0[0], b: $0[1], c: $0[2]) })
    }
}

enum CaptureKind: String, Codable {
    case guided, photos, video, demo
    var title: String {
        switch self { case .guided: return "Guided LiDAR capture"
        case .photos: return "Imported photos"
        case .video: return "Video frames"
        case .demo: return "Synthetic demo" }
    }
}

enum ScaleSource: String, Codable {
    case unverified, lidar, reference, demo
    var exportValue: String {
        switch self { case .lidar: return "LIDAR"; case .reference: return "REFERENCE"
        case .demo: return "DEMO"; case .unverified: return "UNVERIFIED" }
    }
    var title: String {
        switch self { case .unverified: return "Scale required"
        case .lidar: return "LiDAR scale estimate"
        case .reference: return "Known-length calibration"
        case .demo: return "Synthetic demo dimensions" }
    }
}

struct SavedMeasurement: Codable, Identifiable {
    var id = UUID()
    var label: String
    var a: Point3
    var b: Point3
}

struct ScanRecord: Codable, Identifiable {
    var id = UUID()
    var name: String
    var createdAt = Date()
    var kind: CaptureKind
    var imageCount: Int
    var scale: Double?
    var scaleSource: ScaleSource
    var scaleConfirmed: Bool
    var measurements: [SavedMeasurement] = []
    var warnings: [String] = []
    var canExport: Bool { scale != nil && scaleConfirmed && scaleSource != .unverified }
}

struct ScanDraft: Identifiable {
    var id: UUID
    var name: String
    var kind: CaptureKind
    var directory: URL
    var images: URL { directory.appendingPathComponent("Images", isDirectory: true) }
    var checkpoints: URL { directory.appendingPathComponent("Checkpoints", isDirectory: true) }
    var model: URL { directory.appendingPathComponent("model.usdz") }
}

struct AppNotice: Identifiable {
    var id = UUID()
    var message: String
}

struct MeasurementSummary {
    var width: Double
    var depth: Double
    var height: Double
    var surface: Double
    var footprint: Double
    var volume: Double?
    var closed: Bool
    var boundaryEdges: Int
    var components: Int
    var vertices: Int
    var triangles: Int
    init(_ m: SCMetrics) {
        width = m.width_m; depth = m.depth_m; height = m.height_m
        surface = m.surface_m2; footprint = m.footprint_m2
        volume = m.volume_valid != 0 ? m.volume_m3 : nil
        closed = m.watertight != 0; boundaryEdges = Int(m.boundary_edges)
        components = Int(m.components); vertices = Int(m.vertex_count); triangles = Int(m.triangle_count)
    }
}

enum ScanFailure: LocalizedError {
    case message(String)
    var errorDescription: String? { if case .message(let text) = self { return text }; return nil }
}

enum DisplayUnit: String, CaseIterable, Identifiable {
    case cm, mm, inches
    var id: String { rawValue }
    var factor: Double { switch self { case .cm: return 100; case .mm: return 1000; case .inches: return 1 / 0.0254 } }
    var symbol: String { self == .inches ? "in" : rawValue }
    func format(metres: Double) -> String { String(format: "%.2f %@", metres * factor, symbol) }
}

enum ScanFiles {
    static var root: URL {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Scans", isDirectory: true)
    }
    static func directory(_ id: UUID) -> URL { root.appendingPathComponent(id.uuidString, isDirectory: true) }
    static func write<T: Encodable>(_ value: T, to url: URL) throws {
        let encoder = JSONEncoder(); encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        encoder.dateEncodingStrategy = .iso8601
        try encoder.encode(value).write(to: url, options: .atomic)
    }
    static func read<T: Decodable>(_ type: T.Type, from url: URL) throws -> T {
        let decoder = JSONDecoder(); decoder.dateDecodingStrategy = .iso8601
        return try decoder.decode(type, from: Data(contentsOf: url))
    }
}
