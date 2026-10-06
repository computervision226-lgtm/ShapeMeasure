import ARKit
import AVFoundation
import Combine
import CoreVideo
import Foundation
import UIKit

// ARSession callbacks, sample processing, and disk export use one serial queue.
// Published values are changed only on the main queue. ARFrames are processed
// immediately, never retained in a growing backlog.
final class LiDARScanner: NSObject, ObservableObject, ARSessionDelegate {
    static var supported: Bool {
        ARWorldTrackingConfiguration.isSupported &&
        ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth)
    }
    let session = ARSession()
    @Published private(set) var cameraReady = false
    @Published private(set) var canStart = false
    @Published private(set) var isScanning = false
    @Published private(set) var isExporting = false
    @Published private(set) var needsReset = false
    @Published private(set) var pointCount = 0
    @Published private(set) var hasUnsavedPoints = false
    @Published private(set) var status = "Preparing the LiDAR camera…"
    @Published private(set) var preview: [SCCloudPoint] = []
    @Published private(set) var previewRevision = 0
    @Published private(set) var savedClouds: [SavedCloud] = []
    @Published var notice: String?

    private let queue = DispatchQueue(label: "com.maged.shapemeasure.lidar", qos: .userInitiated)
    private var prepared = false // main queue only
    private var closed = false // main queue only
    private var previousIdleTimer = false // main queue only
    private var ownsIdleTimer = false // main queue only
    private var options = CloudOptions() // subsequent fields: worker queue only
    private var points: [SCCloudPoint] = []
    private struct Voxel: Hashable { var x: Int32; var y: Int32; var z: Int32 }
    private var occupied: Set<Voxel> = []
    private var running = false
    private var capturing = false
    private var blocked = false
    private var trackingNormal = false
    private var hasDepth = false
    private var lastSample = -Double.infinity
    private var lastPublish = -Double.infinity
    private var frames = 0
    private var colourUnavailableFrames = 0
    private var recordedSeconds: Double = 0
    private var depthResolution: [Int] = []
    private var intrinsics: [Float] = []
    private var pauseReason: String?
    private var savedPointCount = 0
    private var lastSaved: SavedCloud?

    override init() {
        super.init()
        session.delegate = self
        session.delegateQueue = queue
    }
    @MainActor func prepare() {
        guard !prepared else { return }
        prepared = true
        queue.async {
            let files = LiDARFiles.savedClouds()
            DispatchQueue.main.async { self.savedClouds = files }
        }
        guard Self.supported else {
            status = "This device does not support rear LiDAR scene depth. Use a LiDAR-equipped iPhone Pro."
            return
        }
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized: enableCamera()
        case .notDetermined:
            AVCaptureDevice.requestAccess(for: .video) { allowed in
                DispatchQueue.main.async {
                    guard !self.closed else { return }
                    if allowed { self.enableCamera() }
                    else { self.status = "Enable Camera access in Settings, then reopen LiDAR Point Cloud." }
                }
            }
        default: status = "Enable Camera access in Settings, then reopen LiDAR Point Cloud."
        }
    }
    @MainActor private func enableCamera() {
        guard !closed else { return }
        cameraReady = true
        previousIdleTimer = UIApplication.shared.isIdleTimerDisabled
        UIApplication.shared.isIdleTimerDisabled = true
        ownsIdleTimer = true
        queue.async { self.runSession(reset: true) }
    }
    private func runSession(reset: Bool) {
        let config = ARWorldTrackingConfiguration()
        config.worldAlignment = .gravity
        config.frameSemantics = .sceneDepth
        running = true
        hasDepth = false
        trackingNormal = false
        let runOptions: ARSession.RunOptions = reset ? [.resetTracking, .removeExistingAnchors] : []
        session.run(config, options: runOptions)
        publish()
    }
    @MainActor func start(options selected: CloudOptions) {
        guard canStart, !isExporting else { return }
        isScanning = true
        canStart = false
        queue.async {
            guard self.running, !self.blocked, self.trackingNormal, self.hasDepth else { self.publish(); return }
            if self.points.isEmpty { self.options = selected }
            self.capturing = true
            self.lastSample = -Double.infinity
            self.publish()
        }
    }
    @MainActor func stop() {
        isScanning = false
        queue.async { self.capturing = false; self.publish(includePreview: true) }
    }
    @MainActor func reset() {
        guard cameraReady, !isExporting else { return }
        canStart = false
        isScanning = false
        queue.async {
            self.capturing = false
            self.points.removeAll(keepingCapacity: false)
            self.occupied.removeAll(keepingCapacity: false)
            self.frames = 0
            self.colourUnavailableFrames = 0
            self.recordedSeconds = 0
            self.lastSample = -Double.infinity
            self.lastPublish = -Double.infinity
            self.blocked = false
            self.pauseReason = nil
            self.savedPointCount = 0
            self.lastSaved = nil
            self.depthResolution = []
            self.intrinsics = []
            self.runSession(reset: true)
            self.publish(includePreview: true)
        }
    }
    @MainActor func suspend() {
        queue.async {
            guard self.running else { return }
            self.running = false
            self.capturing = false
            self.blocked = true
            self.pauseReason = "The app left the foreground. Reset before another capture to avoid mixing coordinate frames."
            self.session.pause()
            self.publish(includePreview: true)
        }
    }
    @MainActor func close() {
        closed = true
        if ownsIdleTimer {
            UIApplication.shared.isIdleTimerDisabled = previousIdleTimer
            ownsIdleTimer = false
        }
        suspend()
    }
    @MainActor func save() {
        guard pointCount > 0, !isScanning, !isExporting else { return }
        isExporting = true
        let device = UIDevice.current.model
        let operatingSystem = UIDevice.current.systemVersion
        queue.async {
            do {
                if self.lastSaved == nil || self.savedPointCount != self.points.count {
                    let report = CloudReport(id: UUID(), createdAt: Date(), pointCount: self.points.count,
                        processedDepthFrames: self.frames, colourUnavailableFrames: self.colourUnavailableFrames,
                        recordedSeconds: self.recordedSeconds, settings: self.options,
                        bounds: CloudBounds(points: self.points), depthResolutionPixels: self.depthResolution,
                        lastDepthIntrinsicsFXFYCXCY: self.intrinsics, pauseReason: self.pauseReason,
                        device: device, operatingSystem: operatingSystem)
                    self.lastSaved = try LiDARFiles.save(points: self.points, report: report)
                    self.savedPointCount = self.points.count
                }
                let files = LiDARFiles.savedClouds()
                DispatchQueue.main.async { self.savedClouds = files; self.isExporting = false }
                self.publish()
            } catch {
                DispatchQueue.main.async { self.notice = error.localizedDescription; self.isExporting = false }
            }
        }
    }
    func session(_ session: ARSession, didUpdate frame: ARFrame) {
        guard running, !blocked else { return }
        if case .normal = frame.camera.trackingState { trackingNormal = true }
        else { trackingNormal = false }
        if case .limited(.relocalizing) = frame.camera.trackingState, !points.isEmpty {
            halt("Tracking is relocalizing. Save this cloud and reset before recording more.")
            return
        }
        hasDepth = frame.sceneDepth?.confidenceMap != nil
        if capturing, trackingNormal, let depth = frame.sceneDepth,
           frame.timestamp - lastSample >= options.frameIntervalSeconds {
            if lastSample.isFinite { recordedSeconds += frame.timestamp - lastSample }
            lastSample = frame.timestamp
            append(frame: frame, depth: depth)
        }
        if frame.timestamp - lastPublish >= 0.5 {
            lastPublish = frame.timestamp
            publish(includePreview: capturing)
        }
    }
    private func append(frame: ARFrame, depth: ARDepthData) {
        let map = depth.depthMap
        guard CVPixelBufferGetPixelFormatType(map) == kCVPixelFormatType_DepthFloat32,
              let confidence = depth.confidenceMap,
              CVPixelBufferGetPixelFormatType(confidence) == kCVPixelFormatType_OneComponent8 else { return }
        let width = CVPixelBufferGetWidth(map), height = CVPixelBufferGetHeight(map)
        guard width > 0, height > 0,
              CVPixelBufferGetWidth(confidence) == width, CVPixelBufferGetHeight(confidence) == height else { return }
        guard CVPixelBufferLockBaseAddress(map, .readOnly) == kCVReturnSuccess else { return }
        defer { CVPixelBufferUnlockBaseAddress(map, .readOnly) }
        guard CVPixelBufferLockBaseAddress(confidence, .readOnly) == kCVReturnSuccess else { return }
        defer { CVPixelBufferUnlockBaseAddress(confidence, .readOnly) }
        guard let depthBase = CVPixelBufferGetBaseAddress(map),
              let confidenceBase = CVPixelBufferGetBaseAddress(confidence) else { return }
        let depthStride = CVPixelBufferGetBytesPerRow(map)
        let confidenceStride = CVPixelBufferGetBytesPerRow(confidence)
        let image = frame.capturedImage
        let imageLocked = CVPixelBufferLockBaseAddress(image, .readOnly) == kCVReturnSuccess
        defer { if imageLocked { CVPixelBufferUnlockBaseAddress(image, .readOnly) } }
        let format = CVPixelBufferGetPixelFormatType(image)
        let matrixName = CVBufferCopyAttachment(image, kCVImageBufferYCbCrMatrixKey, nil) as? String
        let bt709 = matrixName == (kCVImageBufferYCbCrMatrix_ITU_R_709_2 as String)
        let matrixSupported = matrixName == nil || bt709 || matrixName == (kCVImageBufferYCbCrMatrix_ITU_R_601_4 as String)
        let colourSupported = imageLocked && matrixSupported && CVPixelBufferGetPlaneCount(image) == 2 &&
            (format == kCVPixelFormatType_420YpCbCr8BiPlanarFullRange || format == kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange)
        let yBase = colourSupported ? CVPixelBufferGetBaseAddressOfPlane(image, 0) : nil
        let uvBase = colourSupported ? CVPixelBufferGetBaseAddressOfPlane(image, 1) : nil
        let yStride = colourSupported ? CVPixelBufferGetBytesPerRowOfPlane(image, 0) : 0
        let uvStride = colourSupported ? CVPixelBufferGetBytesPerRowOfPlane(image, 1) : 0
        let colourWidth = colourSupported ? CVPixelBufferGetWidthOfPlane(image, 0) : 0
        let colourHeight = colourSupported ? CVPixelBufferGetHeightOfPlane(image, 0) : 0
        let uvWidth = colourSupported ? CVPixelBufferGetWidthOfPlane(image, 1) : 0
        let uvHeight = colourSupported ? CVPixelBufferGetHeightOfPlane(image, 1) : 0
        let fullRange = format == kCVPixelFormatType_420YpCbCr8BiPlanarFullRange
        if yBase == nil || uvBase == nil { colourUnavailableFrames += 1 }

        let resolution = frame.camera.imageResolution
        guard resolution.width > 0, resolution.height > 0 else { return }
        let k = frame.camera.intrinsics
        let sx = Float(width) / Float(resolution.width), sy = Float(height) / Float(resolution.height)
        let fx = k.columns.0.x * sx, fy = k.columns.1.y * sy
        let cx = k.columns.2.x * sx, cy = k.columns.2.y * sy
        let t = frame.camera.transform
        let transform = [t.columns.0.x, t.columns.0.y, t.columns.0.z, t.columns.0.w,
                         t.columns.1.x, t.columns.1.y, t.columns.1.z, t.columns.1.w,
                         t.columns.2.x, t.columns.2.y, t.columns.2.z, t.columns.2.w,
                         t.columns.3.x, t.columns.3.y, t.columns.3.z, t.columns.3.w]
        depthResolution = [width, height]
        intrinsics = [fx, fy, cx, cy]
        frames += 1
        transform.withUnsafeBufferPointer { pose in
            rows: for y in stride(from: 0, to: height, by: options.sampleStep) {
                let depths = depthBase.advanced(by: y * depthStride).assumingMemoryBound(to: Float.self)
                let confidences = confidenceBase.advanced(by: y * confidenceStride).assumingMemoryBound(to: UInt8.self)
                for x in stride(from: 0, to: width, by: options.sampleStep) {
                    let d = depths[x], c = confidences[x]
                    guard d.isFinite, d >= options.minimumDepthMetres, d <= options.maximumDepthMetres,
                          c >= options.minimumConfidence, c <= 2 else { continue }
                    var world = SCPoint3()
                    guard sc_depth_unproject(Float(x), Float(y), d, fx, fy, cx, cy, pose.baseAddress, &world) != 0 else { continue }
                    let size = Double(options.voxelMetres)
                    let voxel = Voxel(x: Int32(floor(world.x / size)), y: Int32(floor(world.y / size)), z: Int32(floor(world.z / size)))
                    guard !occupied.contains(voxel) else { continue }
                    var rgb: [UInt8] = [180, 180, 180]
                    if let yBase, let uvBase, colourWidth > 0, colourHeight > 0, uvWidth > 0, uvHeight > 0 {
                        let px = min(colourWidth - 1, x * colourWidth / width)
                        let py = min(colourHeight - 1, y * colourHeight / height)
                        let uvX = min(uvWidth - 1, px / 2), uvY = min(uvHeight - 1, py / 2)
                        let luma = yBase.advanced(by: py * yStride).assumingMemoryBound(to: UInt8.self)[px]
                        let chroma = uvBase.advanced(by: uvY * uvStride).assumingMemoryBound(to: UInt8.self)
                        sc_camera_rgb(luma, chroma[uvX * 2], chroma[uvX * 2 + 1], fullRange ? 1 : 0, bt709 ? 1 : 0, &rgb)
                    }
                    occupied.insert(voxel)
                    points.append(SCCloudPoint(x: Float(world.x), y: Float(world.y), z: Float(world.z),
                                              red: rgb[0], green: rgb[1], blue: rgb[2], confidence: c))
                    if points.count >= options.pointLimit {
                        capturing = false
                        blocked = true
                        pauseReason = "The 500,000 point limit was reached. Save this cloud, then reset for another scan."
                        break rows
                    }
                }
            }
        }
        if blocked { publish(includePreview: true) }
    }
    private func halt(_ reason: String) {
        capturing = false
        blocked = true
        pauseReason = reason
        publish(includePreview: true)
    }
    func sessionWasInterrupted(_ session: ARSession) {
        halt("The AR session was interrupted. Save this cloud, then reset before recording more.")
    }
    func sessionInterruptionEnded(_ session: ARSession) { publish() }
    func session(_ session: ARSession, didFailWithError error: Error) {
        halt("LiDAR session stopped: \(error.localizedDescription). Reset to try again.")
    }
    private func publish(includePreview: Bool = false) {
        let count = points.count, scanning = capturing, reset = blocked
        let start = running && !blocked && trackingNormal && hasDepth && !capturing
        let unsaved = count != savedPointCount
        let text: String
        if let pauseReason { text = pauseReason }
        else if !trackingNormal { text = "Move the camera slowly in a well-lit space to establish tracking." }
        else if !hasDepth { text = "Waiting for rear LiDAR depth and confidence data…" }
        else if capturing { text = "Recording measured depth points. Move slowly and keep the scene still." }
        else if count > 0 { text = "Stopped. Inspect the points, resume, or save the cloud." }
        else { text = "Ready. Tap Start and move around the object or room." }
        let sample: [SCCloudPoint]?
        if includePreview {
            let step = max(1, (count + 24_999) / 25_000)
            sample = stride(from: 0, to: count, by: step).map { points[$0] }
        } else { sample = nil }
        DispatchQueue.main.async {
            self.pointCount = count
            self.isScanning = scanning
            self.canStart = start
            self.needsReset = reset
            self.hasUnsavedPoints = unsaved
            self.status = text
            if let sample { self.preview = sample; self.previewRevision += 1 }
        }
    }
}
