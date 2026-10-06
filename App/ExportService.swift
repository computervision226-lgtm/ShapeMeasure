import Foundation

enum ExportService {
    static func analyze(_ mesh: MeshData) throws -> MeasurementSummary {
        let points = mesh.vertices.map(\.cPoint), triangles = mesh.triangles.map(\.cTriangle)
        var result = SCMetrics(), error = [CChar](repeating: 0, count: 512)
        let ok = points.withUnsafeBufferPointer { p in
            triangles.withUnsafeBufferPointer { t in
                sc_analyze(p.baseAddress, Int32(p.count), t.baseAddress, Int32(t.count), 1, &result, &error, 512)
            }
        }
        guard ok != 0 else { throw ScanFailure.message(String(cString: error)) }
        return MeasurementSummary(result)
    }

    static func export(mesh: MeshData, record: ScanRecord, directory: URL) throws -> URL {
        guard record.canExport, let scale = record.scale else { throw ScanFailure.message("Set or confirm the scale before exporting.") }
        let fm = FileManager.default
        let staging = directory.appendingPathComponent("Export-\(UUID().uuidString)", isDirectory: true)
        try fm.createDirectory(at: staging, withIntermediateDirectories: true)
        defer { try? fm.removeItem(at: staging) }
        let safeName = record.name.unicodeScalars.map { CharacterSet.alphanumerics.contains($0) ? String($0) : "_" }.joined()
        let filename = String(safeName.prefix(48)) + "_" + String(UUID().uuidString.prefix(8)) + ".zip"
        let archive = directory.appendingPathComponent(filename)
        let points = mesh.vertices.map(\.cPoint), triangles = mesh.triangles.map(\.cTriangle)
        let lines = record.measurements.map { measurement -> SCLine in
            var line = SCLine(); line.a = measurement.a.cPoint; line.b = measurement.b.cPoint
            // The DBF label field is ASCII. Full scan names remain UTF-8 in report.json.
            let text = measurement.label.unicodeScalars.map { $0.value < 128 ? String($0) : "?" }.joined()
            let bytes = Array(text.utf8.prefix(47))
            withUnsafeMutableBytes(of: &line.label) { target in
                for i in 0..<target.count { target[i] = i < bytes.count ? bytes[i] : 0 }
            }
            return line
        }
        var error = [CChar](repeating: 0, count: 512)
        let ok = points.withUnsafeBufferPointer { p in
            triangles.withUnsafeBufferPointer { t in
                lines.withUnsafeBufferPointer { l in
                    staging.path.withCString { path in
                        record.scaleSource.exportValue.withCString { source in
                            record.name.withCString { name in
                                sc_export_bundle(path, p.baseAddress, Int32(p.count), t.baseAddress, Int32(t.count),
                                                 l.baseAddress, Int32(l.count), scale, source, name, &error, 512)
                            }
                        }
                    }
                }
            }
        }
        guard ok != 0 else { throw ScanFailure.message(String(cString: error)) }
        let zipped = staging.path.withCString { path in
            archive.path.withCString { zip in sc_zip_bundle(path, zip, &error, 512) }
        }
        guard zipped != 0 else { throw ScanFailure.message(String(cString: error)) }
        return archive
    }
}
