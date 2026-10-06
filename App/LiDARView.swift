import ARKit
import SceneKit
import SwiftUI
import UIKit

@MainActor
struct LiDARView: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(\.scenePhase) private var scenePhase
    @StateObject private var scanner = LiDARScanner()
    @State private var options = CloudOptions()
    @State private var showPoints = false
    @State private var fitRevision = 0
    @State private var confirmReset = false
    @State private var confirmClose = false
    @State private var share: CloudShare?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    Text("Read the rear LiDAR sensor, then save the measured points.")
                        .foregroundStyle(.secondary)
                    HStack {
                        Picker("Preview", selection: $showPoints) {
                            Text("Camera").tag(false)
                            Text("Point cloud").tag(true)
                        }.pickerStyle(.segmented)
                        if showPoints {
                            Button("Fit") { fitRevision += 1 }
                                .disabled(scanner.preview.isEmpty)
                        }
                    }
                    ZStack {
                        if scanner.cameraReady {
                            LiDARCameraView(scanner: scanner)
                                .opacity(showPoints ? 0 : 1)
                                .allowsHitTesting(!showPoints)
                        } else {
                            Color.black
                            Image(systemName: "camera.viewfinder").font(.largeTitle).foregroundStyle(.secondary)
                        }
                        if showPoints {
                            CloudPreview(points: scanner.preview, revision: scanner.previewRevision, fitRevision: fitRevision)
                            if scanner.preview.isEmpty {
                                Text("Your captured points appear here.").foregroundStyle(.secondary)
                            }
                        }
                    }.frame(height: 340).clipShape(RoundedRectangle(cornerRadius: 18))
                    Panel {
                        HStack {
                            Label(scanner.isScanning ? "Recording" : "Point cloud", systemImage: scanner.isScanning ? "record.circle" : "cube.transparent")
                                .foregroundStyle(Palette.mint)
                            Spacer()
                            Text(scanner.pointCount.formatted() + " points").monospacedDigit()
                        }.font(.headline)
                        Text(scanner.status).font(.subheadline).foregroundStyle(.secondary)
                        HStack {
                            if scanner.isScanning {
                                Button { scanner.stop() } label: { Label("Stop", systemImage: "stop.fill") }
                                    .buttonStyle(FilledButton())
                            } else {
                                Button { scanner.start(options: options) } label: {
                                    Label(scanner.pointCount > 0 ? "Resume" : "Start", systemImage: "record.circle")
                                }.buttonStyle(FilledButton()).disabled(!scanner.canStart || scanner.isExporting)
                            }
                            Button("Reset", role: .destructive) {
                                if scanner.hasUnsavedPoints { confirmReset = true }
                                else { scanner.reset(); fitRevision += 1 }
                            }.disabled(!scanner.cameraReady || scanner.isExporting)
                        }
                        Button { scanner.save() } label: {
                            if scanner.isExporting { ProgressView().frame(maxWidth: .infinity) }
                            else { Label("Save PLY + XYZ", systemImage: "square.and.arrow.down").frame(maxWidth: .infinity) }
                        }.buttonStyle(.borderedProminent)
                            .disabled(scanner.pointCount == 0 || scanner.isScanning || scanner.isExporting)
                        Text("Stop, save, then share a saved cloud below. Exports contain every retained point; the preview displays up to 25,000.")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                    Panel {
                        Text("Capture settings").font(.headline)
                        Picker("Point spacing", selection: $options.voxelMetres) {
                            Text("5 mm").tag(Float(0.005))
                            Text("1 cm").tag(Float(0.01))
                            Text("2 cm").tag(Float(0.02))
                        }.pickerStyle(.segmented)
                        Picker("Depth confidence", selection: $options.minimumConfidence) {
                            Text("High").tag(UInt8(2))
                            Text("Medium + high").tag(UInt8(1))
                        }.pickerStyle(.segmented)
                        HStack {
                            Text("Maximum depth")
                            Spacer()
                            Text(String(format: "%.1f m", options.maximumDepthMetres)).monospacedDigit()
                        }
                        Slider(value: $options.maximumDepthMetres, in: 0.5...5, step: 0.5)
                        Text(scanner.pointCount > 0 ? "Reset to change settings for a new cloud." : "Spacing controls how densely points are stored. It does not specify measurement accuracy.")
                            .font(.footnote).foregroundStyle(.secondary)
                    }.disabled(scanner.pointCount > 0 || scanner.isScanning || scanner.isExporting)
                    if !scanner.savedClouds.isEmpty {
                        Text("Saved point clouds").font(.title3.weight(.semibold))
                        ForEach(scanner.savedClouds) { cloud in
                            Panel {
                                HStack {
                                    VStack(alignment: .leading, spacing: 4) {
                                        Text(cloud.createdAt, format: .dateTime.month().day().hour().minute()).font(.headline)
                                        Text("\(cloud.pointCount.formatted()) points · metres").font(.caption).foregroundStyle(.secondary)
                                    }
                                    Spacer()
                                    Button("Share files") { share = CloudShare(files: cloud.files) }
                                }
                                HStack {
                                    Button("PLY") { share = CloudShare(files: [cloud.ply]) }
                                    Button("XYZ") { share = CloudShare(files: [cloud.stem.appendingPathExtension("xyz")]) }
                                    Button("Details") { share = CloudShare(files: [cloud.stem.appendingPathExtension("json")]) }
                                }.buttonStyle(.bordered)
                            }.disabled(scanner.isScanning || scanner.isExporting)
                        }
                    }
                    Text("Move slowly, keep the scene still, and scan from several angles. Points are local ARKit coordinates with Y up. Background surfaces are included. Check dimensions against a ruler; shiny or transparent surfaces can leave gaps.")
                        .font(.footnote).foregroundStyle(.secondary)
                }.padding(20)
            }.background(Palette.background).navigationTitle("LiDAR Point Cloud")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .topBarLeading) {
                        Button("Close") {
                            if scanner.hasUnsavedPoints { confirmClose = true }
                            else { dismiss() }
                        }.disabled(scanner.isExporting)
                    }
                }
        }
        .interactiveDismissDisabled(scanner.hasUnsavedPoints || scanner.isExporting)
        .onAppear { scanner.prepare() }
        .onDisappear { scanner.close() }
        .onChange(of: scenePhase) { _, phase in if phase != .active { scanner.suspend() } }
        .sheet(item: $share) { CloudShareSheet(files: $0.files) }
        .confirmationDialog("Discard the current point cloud and reset tracking?", isPresented: $confirmReset, titleVisibility: .visible) {
            Button("Discard and reset", role: .destructive) { scanner.reset(); fitRevision += 1 }
            Button("Keep scanning", role: .cancel) { }
        }
        .confirmationDialog("The current cloud has unsaved points.", isPresented: $confirmClose, titleVisibility: .visible) {
            Button("Discard unsaved points and close", role: .destructive) { dismiss() }
            Button("Keep open", role: .cancel) { }
        }
        .alert("Point cloud", isPresented: Binding(get: { scanner.notice != nil }, set: { if !$0 { scanner.notice = nil } })) {
            Button("OK", role: .cancel) { scanner.notice = nil }
        } message: { Text(scanner.notice ?? "") }
    }
}

private struct LiDARCameraView: UIViewRepresentable {
    let scanner: LiDARScanner
    func makeUIView(context: Context) -> ARSCNView {
        let view = ARSCNView()
        view.session = scanner.session
        view.scene = SCNScene()
        view.automaticallyUpdatesLighting = false
        view.preferredFramesPerSecond = 30
        view.accessibilityLabel = "Live rear camera. Move slowly to record LiDAR points."
        return view
    }
    func updateUIView(_ view: ARSCNView, context: Context) { }
}

private struct CloudPreview: UIViewRepresentable {
    var points: [SCCloudPoint]
    var revision: Int
    var fitRevision: Int
    func makeCoordinator() -> Coordinator { Coordinator() }
    func makeUIView(context: Context) -> SCNView {
        let view = SCNView()
        view.backgroundColor = UIColor(red: 0.035, green: 0.065, blue: 0.075, alpha: 1)
        view.allowsCameraControl = true
        view.defaultCameraController.inertiaEnabled = true
        let scene = SCNScene()
        let cloud = SCNNode(); cloud.name = "cloud"
        scene.rootNode.addChildNode(cloud)
        let camera = SCNNode(); camera.camera = SCNCamera()
        camera.camera?.zNear = 0.01; camera.camera?.zFar = 1000
        camera.position = SCNVector3(0, 0, 3)
        scene.rootNode.addChildNode(camera)
        view.scene = scene; view.pointOfView = camera
        view.accessibilityLabel = "3D LiDAR point cloud. Drag to rotate and pinch to zoom."
        return view
    }
    func updateUIView(_ view: SCNView, context: Context) {
        let state = context.coordinator
        guard let node = view.scene?.rootNode.childNode(withName: "cloud", recursively: false) else { return }
        if state.revision != revision {
            state.revision = revision
            if points.isEmpty { node.geometry = nil; state.fitted = false }
            else {
                let vertices = points.map { SCNVector3($0.x, $0.y, $0.z) }
                let colors = points.flatMap { [Float($0.red) / 255, Float($0.green) / 255, Float($0.blue) / 255, Float(1)] }
                let colorData = colors.withUnsafeBytes { Data($0) }
                let colorSource = SCNGeometrySource(data: colorData, semantic: .color,
                    vectorCount: points.count, usesFloatComponents: true, componentsPerVector: 4,
                    bytesPerComponent: 4, dataOffset: 0, dataStride: 16)
                let element = SCNGeometryElement(indices: points.indices.map { UInt32($0) }, primitiveType: .point)
                element.pointSize = 5; element.minimumPointScreenSpaceRadius = 1; element.maximumPointScreenSpaceRadius = 6
                let geometry = SCNGeometry(sources: [SCNGeometrySource(vertices: vertices), colorSource], elements: [element])
                let material = SCNMaterial(); material.lightingModel = .constant
                material.diffuse.contents = UIColor.white
                geometry.materials = [material]
                node.geometry = geometry
            }
        }
        if !points.isEmpty, !state.fitted || state.fitRevision != fitRevision {
            state.fitRevision = fitRevision
            state.fitted = true
            let bounds = CloudBounds(points: points)
            let a = bounds.minimumXYZMetres, b = bounds.maximumXYZMetres
            let center = SCNVector3(Float((a[0] + b[0]) / 2), Float((a[1] + b[1]) / 2), Float((a[2] + b[2]) / 2))
            let span = max(0.1, max(b[0] - a[0], max(b[1] - a[1], b[2] - a[2])))
            view.pointOfView?.position = SCNVector3(center.x + Float(span), center.y + Float(span * 0.5), center.z + Float(span * 1.8))
            view.pointOfView?.look(at: center)
            view.defaultCameraController.target = center
        }
    }
    final class Coordinator {
        var revision = -1
        var fitRevision = -1
        var fitted = false
    }
}

private struct CloudShare: Identifiable {
    let id = UUID()
    var files: [URL]
}
private struct CloudShareSheet: UIViewControllerRepresentable {
    var files: [URL]
    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: files, applicationActivities: nil)
    }
    func updateUIViewController(_ controller: UIActivityViewController, context: Context) { }
}
