import Foundation
import SceneKit
import simd

enum MeshLoader {
    private struct WeldKey: Hashable { var x: Int64; var y: Int64; var z: Int64 }

    static func load(url: URL) throws -> MeshData {
        let scene = try SCNScene(url: url, options: [.convertToYUp: true, .convertUnitsToMeters: true])
        var vertices: [Point3] = []
        var triangles: [Triangle] = []
        var failure: Error?
        scene.rootNode.enumerateChildNodes { node, stop in
            do {
                guard let geometry = node.geometry,
                      let source = geometry.sources(for: .vertex).first else { return }
                guard source.usesFloatComponents, source.componentsPerVector >= 3,
                      [4,8].contains(source.bytesPerComponent) else {
                    throw ScanFailure.message("Unsupported model vertex format.")
                }
                let offset = Int32(vertices.count)
                let transform = node.simdWorldTransform
                let mirrored = simd_determinant(transform) < 0
                for i in 0..<source.vectorCount {
                    let start = source.dataOffset + i * source.dataStride
                    guard start + 3 * source.bytesPerComponent <= source.data.count else {
                        throw ScanFailure.message("Truncated model vertex buffer.")
                    }
                    let xyz: [Float] = (0..<3).map { component in
                        let position = start + component * source.bytesPerComponent
                        return source.data.withUnsafeBytes { raw in
                            if source.bytesPerComponent == 4 { return raw.loadUnaligned(fromByteOffset: position, as: Float.self) }
                            return Float(raw.loadUnaligned(fromByteOffset: position, as: Double.self))
                        }
                    }
                    let p = transform * SIMD4<Float>(xyz[0], xyz[1], xyz[2], 1)
                    guard p.x.isFinite, p.y.isFinite, p.z.isFinite, p.w.isFinite, abs(p.w) > 1e-8 else {
                        throw ScanFailure.message("Invalid reconstructed model coordinate.")
                    }
                    vertices.append(Point3(Double(p.x / p.w), Double(p.y / p.w), Double(p.z / p.w)))
                }
                for element in geometry.elements {
                    guard [.triangles,.triangleStrip].contains(element.primitiveType),
                          [1,2,4].contains(element.bytesPerIndex) else { continue }
                    func index(_ position: Int) throws -> Int32 {
                        let byte = position * element.bytesPerIndex
                        guard byte + element.bytesPerIndex <= element.data.count else {
                            throw ScanFailure.message("Truncated model index buffer.")
                        }
                        let value: UInt32 = element.data.withUnsafeBytes { raw in
                            switch element.bytesPerIndex {
                            case 1: return UInt32(raw.loadUnaligned(fromByteOffset: byte, as: UInt8.self))
                            case 2: return UInt32(raw.loadUnaligned(fromByteOffset: byte, as: UInt16.self))
                            default: return raw.loadUnaligned(fromByteOffset: byte, as: UInt32.self)
                            }
                        }
                        guard Int(value) < source.vectorCount else { throw ScanFailure.message("Invalid model triangle index.") }
                        return offset + Int32(value)
                    }
                    for face in 0..<element.primitiveCount {
                        let start = element.primitiveType == .triangles ? face * 3 : face
                        var a = try index(start), b = try index(start + 1), c = try index(start + 2)
                        if element.primitiveType == .triangleStrip && face % 2 == 1 { swap(&a, &b) }
                        if mirrored { swap(&b, &c) }
                        triangles.append(Triangle(a: a, b: b, c: c))
                    }
                }
                if vertices.count > 1_000_000 || triangles.count > 1_000_000 {
                    throw ScanFailure.message("Model is too large for this version. Use reduced detail.")
                }
            } catch { failure = error; stop.pointee = true }
        }
        if let failure { throw failure }
        guard !vertices.isEmpty, !triangles.isEmpty else { throw ScanFailure.message("No triangle mesh was reconstructed.") }
        return try clean(vertices: vertices, triangles: triangles)
    }

    static func clean(vertices: [Point3], triangles: [Triangle]) throws -> MeshData {
        // Weld texture seams with a small relative tolerance, then remove unused vertices.
        let minX = vertices.map(\.x).min()!, maxX = vertices.map(\.x).max()!
        let minY = vertices.map(\.y).min()!, maxY = vertices.map(\.y).max()!
        let minZ = vertices.map(\.z).min()!, maxZ = vertices.map(\.z).max()!
        let tolerance = max(max(maxX - minX, maxY - minY), maxZ - minZ) * 1e-7
        guard tolerance.isFinite, tolerance > 0 else { throw ScanFailure.message("The model has no usable dimensions.") }
        var keys: [WeldKey: Int32] = [:], welded: [Point3] = [], remap: [Int32] = []
        for p in vertices {
            let q = [(p.x-minX)/tolerance, (p.y-minY)/tolerance, (p.z-minZ)/tolerance]
            guard q.allSatisfy({ $0.isFinite && abs($0) < Double(Int64.max) / 2 }) else {
                throw ScanFailure.message("Model coordinates cannot be normalized.")
            }
            let key = WeldKey(x: Int64(q[0].rounded()), y: Int64(q[1].rounded()), z: Int64(q[2].rounded()))
            if let found = keys[key] { remap.append(found) }
            else { let i = Int32(welded.count); keys[key] = i; remap.append(i); welded.append(p) }
        }
        var faces: [Triangle] = []
        for t in triangles {
            let a = remap[Int(t.a)], b = remap[Int(t.b)], c = remap[Int(t.c)]
            guard a != b, b != c, c != a else { continue }
            let pa = welded[Int(a)], pb = welded[Int(b)], pc = welded[Int(c)]
            let ab = SIMD3<Double>(pb.x-pa.x, pb.y-pa.y, pb.z-pa.z)
            let ac = SIMD3<Double>(pc.x-pa.x, pc.y-pa.y, pc.z-pa.z)
            if simd_length(simd_cross(ab, ac)) > tolerance * tolerance { faces.append(Triangle(a: a, b: b, c: c)) }
        }
        var used: [Int32: Int32] = [:], compact: [Point3] = [], result: [Triangle] = []
        func compactIndex(_ index: Int32) -> Int32 {
            if let found = used[index] { return found }
            let new = Int32(compact.count); used[index] = new; compact.append(welded[Int(index)]); return new
        }
        for t in faces { result.append(Triangle(a: compactIndex(t.a), b: compactIndex(t.b), c: compactIndex(t.c))) }
        guard compact.count >= 3, !result.isEmpty else { throw ScanFailure.message("No nondegenerate faces were reconstructed.") }
        return MeshData(vertices: compact, triangles: result)
    }
}
