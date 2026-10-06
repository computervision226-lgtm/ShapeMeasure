import SwiftUI
import SceneKit
import simd

struct MeshPreview: UIViewRepresentable {
    var mesh: MeshData
    var picks: [Point3]
    var measurements: [SavedMeasurement]
    var onPick: (Point3) -> Void

    func makeCoordinator() -> Coordinator { Coordinator(onPick: onPick) }
    func makeUIView(context: Context) -> SCNView {
        let view = SCNView()
        view.backgroundColor = UIColor(red: 0.055, green: 0.09, blue: 0.1, alpha: 1)
        view.antialiasingMode = .multisampling4X
        view.allowsCameraControl = true
        view.autoenablesDefaultLighting = true
        view.defaultCameraController.inertiaEnabled = true
        let scene = SCNScene()
        let positions = mesh.vertices.map(\.vector)
        var normals = [SIMD3<Float>](repeating: .zero, count: positions.count)
        let indices = mesh.triangles.flatMap { [$0.a, $0.b, $0.c] }
        for t in mesh.triangles {
            let a = positions[Int(t.a)], b = positions[Int(t.b)], c = positions[Int(t.c)]
            let ab = SIMD3<Float>(b.x-a.x,b.y-a.y,b.z-a.z), ac = SIMD3<Float>(c.x-a.x,c.y-a.y,c.z-a.z)
            let n = simd_cross(ab, ac)
            normals[Int(t.a)] += n; normals[Int(t.b)] += n; normals[Int(t.c)] += n
        }
        let normalSource = SCNGeometrySource(normals: normals.map {
            let n = simd_length($0) > 1e-12 ? simd_normalize($0) : SIMD3<Float>(0,1,0)
            return SCNVector3(n.x,n.y,n.z)
        })
        let geometry = SCNGeometry(sources: [SCNGeometrySource(vertices: positions), normalSource],
                                   elements: [SCNGeometryElement(indices: indices, primitiveType: .triangles)])
        let material = SCNMaterial()
        material.diffuse.contents = UIColor(red: 0.37, green: 0.78, blue: 0.69, alpha: 1)
        material.roughness.contents = 0.65
        material.metalness.contents = 0.05
        material.lightingModel = .physicallyBased
        material.isDoubleSided = true
        geometry.materials = [material]
        let model = SCNNode(geometry: geometry); model.name = "objectMesh"
        scene.rootNode.addChildNode(model)
        let minimum = Point3(mesh.vertices.map(\.x).min()!,mesh.vertices.map(\.y).min()!,mesh.vertices.map(\.z).min()!)
        let maximum = Point3(mesh.vertices.map(\.x).max()!,mesh.vertices.map(\.y).max()!,mesh.vertices.map(\.z).max()!)
        let center = SCNVector3(Float((minimum.x+maximum.x)/2),Float((minimum.y+maximum.y)/2),Float((minimum.z+maximum.z)/2))
        let radius = max(Float(maximum.distance(to: minimum))/2, 0.00001)
        let camera = SCNNode(); camera.camera = SCNCamera()
        camera.camera?.zNear = Double(radius)/1000
        camera.camera?.zFar = Double(radius)*100
        camera.position = SCNVector3(center.x+radius*1.6,center.y+radius*0.8,center.z+radius*2.8)
        camera.look(at: center); scene.rootNode.addChildNode(camera)
        view.scene = scene; view.pointOfView = camera
        view.defaultCameraController.target = center
        context.coordinator.radius = CGFloat(radius)
        let tap = UITapGestureRecognizer(target: context.coordinator, action: #selector(Coordinator.tap(_:)))
        view.addGestureRecognizer(tap)
        view.accessibilityLabel = "3D object preview. Drag to rotate, pinch to zoom, and tap two surface points to measure."
        return view
    }
    func updateUIView(_ view: SCNView, context: Context) {
        context.coordinator.onPick = onPick
        guard let scene = view.scene else { return }
        scene.rootNode.childNode(withName: "markers", recursively: false)?.removeFromParentNode()
        let markers = SCNNode(); markers.name = "markers"
        let radius = context.coordinator.radius
        func sphere(_ p: Point3, color: UIColor) {
            let shape = SCNSphere(radius: radius * 0.018)
            shape.firstMaterial?.diffuse.contents = color; shape.firstMaterial?.lightingModel = .constant
            let node = SCNNode(geometry: shape); node.position = p.vector; markers.addChildNode(node)
        }
        func line(_ a: Point3, _ b: Point3, color: UIColor) {
            let distance = CGFloat(a.distance(to: b)); guard distance > 0 else { return }
            let geometry = SCNCylinder(radius: radius * 0.005, height: distance)
            geometry.firstMaterial?.diffuse.contents = color; geometry.firstMaterial?.lightingModel = .constant
            let node = SCNNode(geometry: geometry)
            node.position = SCNVector3(Float((a.x+b.x)/2),Float((a.y+b.y)/2),Float((a.z+b.z)/2))
            let direction = simd_normalize(SIMD3<Float>(Float(b.x-a.x),Float(b.y-a.y),Float(b.z-a.z)))
            node.simdOrientation = simd_quatf(from: SIMD3<Float>(0,1,0), to: direction)
            markers.addChildNode(node)
        }
        for m in measurements { line(m.a,m.b,color: .systemTeal) }
        for p in picks { sphere(p,color: .systemYellow) }
        if picks.count == 2 { line(picks[0],picks[1],color: .systemYellow) }
        scene.rootNode.addChildNode(markers)
    }
    final class Coordinator: NSObject {
        var onPick: (Point3) -> Void
        var radius: CGFloat = 0.1
        init(onPick: @escaping (Point3) -> Void) { self.onPick = onPick }
        @objc func tap(_ recognizer: UITapGestureRecognizer) {
            guard let view = recognizer.view as? SCNView else { return }
            let hits = view.hitTest(recognizer.location(in: view), options: [.searchMode: SCNHitTestSearchMode.all.rawValue])
            if let hit = hits.first(where: { $0.node.name == "objectMesh" }) { onPick(Point3(hit.localCoordinates)) }
        }
    }
}
