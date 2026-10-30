import ARKit
import AVFoundation
import Combine
import Foundation
import UIKit
import simd

/// Records an ARKit session with LiDAR depth in the Stray Scanner dataset layout that the PartScan
/// server reads:  rgb.mp4 (HEVC, sensor orientation) · depth/NNNNNN.png (16-bit, millimetres) ·
/// confidence/NNNNNN.png (0/1/2) · odometry.csv (camera-to-world, OpenCV camera axes, per-frame
/// intrinsics) · camera_matrix.csv · imu.csv (header only).
final class ScanRecorder: NSObject, ObservableObject, ARSessionDelegate {
    let session = ARSession()

    @Published var isSessionRunning = false
    @Published var isRecording = false
    @Published var frameCount = 0
    @Published var droppedFrames = 0
    @Published var elapsed: Double = 0
    @Published var depthAvailable = false
    @Published var trackingText = "starting camera…"
    @Published var elevationDeg: Double = 0
    @Published var coverage = CoverageTracker.Snapshot()
    @Published var lastError: String?
    @Published var finishedScan: ScanRecord?

    /// Record every n-th camera frame (2 → 30 fps; plenty for the reconstruction, half the upload).
    var frameStride = 2
    var scansRoot: URL = ScanStore.scansDirectory

    static var lidarSupported: Bool {
        ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth)
    }

    // Everything below is touched only on `queue` (the ARSession delegate queue).
    private let queue = DispatchQueue(label: "partscan.recorder")
    private let writeQueue = DispatchQueue(label: "partscan.png", qos: .userInitiated)
    private var recording = false
    private var writer: AVAssetWriter?
    private var input: AVAssetWriterInput?
    private var adaptor: AVAssetWriterInputPixelBufferAdaptor?
    private var scanDir: URL?
    private var scanID = ""
    private var startTimestamp: TimeInterval?
    private var lastTimestamp: TimeInterval = 0
    private var rows: [String] = []
    private var lastIntrinsics: simd_float3x3?
    private var written = 0
    private var dropped = 0
    private var seen = 0
    private var uiTick = 0
    private var tracker = CoverageTracker()
    private var videoSize = CGSize(width: 1920, height: 1440)
    private var deviceModel = ""
    private var systemVersion = ""
    private var modelId = ""
    private var appVersion = ""

    // MARK: session

    func startSession() {
        if deviceModel.isEmpty {                      // UIDevice is main-thread only; startSession runs on main
            deviceModel = UIDevice.current.model
            systemVersion = UIDevice.current.systemVersion
            modelId = DeviceInfo.modelId
            appVersion = DeviceInfo.appVersion
        }
        guard Self.lidarSupported else {
            DispatchQueue.main.async {
                self.lastError = "This iPhone has no LiDAR scanner. PartScan needs an iPhone Pro (12 Pro or later)."
            }
            return
        }
        let config = ARWorldTrackingConfiguration()
        config.frameSemantics = [.sceneDepth]
        config.worldAlignment = .gravity
        config.isAutoFocusEnabled = true
        if let format = ARWorldTrackingConfiguration.supportedVideoFormats.first(where: {
            $0.imageResolution.width == 1920 && $0.imageResolution.height == 1440
        }) {
            config.videoFormat = format
        }
        videoSize = config.videoFormat.imageResolution
        session.delegate = self
        session.delegateQueue = queue
        session.run(config, options: [.resetTracking, .removeExistingAnchors])
        DispatchQueue.main.async { self.isSessionRunning = true }
    }

    func pauseSession() {
        if isRecording { stopRecording() }
        session.pause()
        isSessionRunning = false
    }

    // MARK: recording

    func startRecording() {
        queue.async {
            guard !self.recording else { return }
            do {
                try self.beginRecording()
                DispatchQueue.main.async {
                    self.isRecording = true
                    self.frameCount = 0
                    self.droppedFrames = 0
                    self.elapsed = 0
                    self.finishedScan = nil
                    self.lastError = nil
                }
            } catch {
                DispatchQueue.main.async { self.lastError = "Could not start recording: \(error.localizedDescription)" }
            }
        }
    }

    private func beginRecording() throws {
        let fm = FileManager.default
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyyMMdd-HHmmss"
        scanID = formatter.string(from: Date())
        let dir = scansRoot.appendingPathComponent(scanID)
        try fm.createDirectory(at: dir.appendingPathComponent("depth"), withIntermediateDirectories: true)
        try fm.createDirectory(at: dir.appendingPathComponent("confidence"), withIntermediateDirectories: true)

        let writer = try AVAssetWriter(outputURL: dir.appendingPathComponent("rgb.mp4"), fileType: .mp4)
        let w = Int(videoSize.width), h = Int(videoSize.height)
        let settings: [String: Any] = [
            AVVideoCodecKey: AVVideoCodecType.hevc,
            AVVideoWidthKey: w,
            AVVideoHeightKey: h,
            AVVideoCompressionPropertiesKey: [AVVideoAverageBitRateKey: 24_000_000,
                                              AVVideoMaxKeyFrameIntervalKey: 30]
        ]
        let input = AVAssetWriterInput(mediaType: .video, outputSettings: settings)
        input.expectsMediaDataInRealTime = true
        let attributes: [String: Any] = [
            kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_420YpCbCr8BiPlanarFullRange,
            kCVPixelBufferWidthKey as String: w,
            kCVPixelBufferHeightKey as String: h
        ]
        let adaptor = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: input,
                                                           sourcePixelBufferAttributes: attributes)
        guard writer.canAdd(input) else { throw NSError(domain: "PartScan", code: 1, userInfo: [NSLocalizedDescriptionKey: "video writer refused the input"]) }
        writer.add(input)
        guard writer.startWriting() else {
            throw writer.error ?? NSError(domain: "PartScan", code: 2, userInfo: [NSLocalizedDescriptionKey: "could not start the video file"])
        }
        self.writer = writer
        self.input = input
        self.adaptor = adaptor
        self.scanDir = dir
        self.startTimestamp = nil
        self.rows = []
        self.lastIntrinsics = nil
        self.written = 0
        self.dropped = 0
        self.seen = 0
        self.tracker = CoverageTracker()
        self.recording = true
    }

    func stopRecording() {
        queue.async {
            guard self.recording else { return }
            self.recording = false
            let writer = self.writer
            let input = self.input
            let dir = self.scanDir
            let rows = self.rows
            let intrinsics = self.lastIntrinsics
            let frames = self.written
            let dropped = self.dropped
            let seconds = max(0, self.lastTimestamp - (self.startTimestamp ?? self.lastTimestamp))
            let size = self.videoSize
            let id = self.scanID
            let coverage = self.tracker.snapshot()
            self.writer = nil
            self.input = nil
            self.adaptor = nil
            self.scanDir = nil
            DispatchQueue.main.async { self.isRecording = false }

            guard let writer, let input, let dir else { return }
            guard frames > 0 else {
                writer.cancelWriting()
                try? FileManager.default.removeItem(at: dir)
                DispatchQueue.main.async {
                    self.lastError = "No frames with LiDAR depth were recorded. Point the camera at the part and try again."
                }
                return
            }
            input.markAsFinished()
            writer.finishWriting { [weak self] in
                guard let self else { return }
                let ok = writer.status == .completed
                let err = writer.error?.localizedDescription
                // the PNG queue is serial: this runs after the last depth image has been written
                self.writeQueue.async {
                    self.finishFiles(dir: dir, id: id, rows: rows, intrinsics: intrinsics, frames: frames,
                                     dropped: dropped, seconds: seconds, size: size, coverage: coverage,
                                     videoOK: ok, videoError: err)
                }
            }
        }
    }

    private func finishFiles(dir: URL, id: String, rows: [String], intrinsics: simd_float3x3?, frames: Int,
                             dropped: Int, seconds: Double, size: CGSize, coverage: CoverageTracker.Snapshot,
                             videoOK: Bool, videoError: String?) {
        var csv = "timestamp, frame, x, y, z, qx, qy, qz, qw, fx, fy, cx, cy, distortion_center_x, distortion_center_y\n"
        csv += rows.joined(separator: "\n") + "\n"
        var note: String?
        do {
            try csv.write(to: dir.appendingPathComponent("odometry.csv"), atomically: true, encoding: .utf8)
            if let k = intrinsics {
                let cm = String(format: "%.6f,0,%.6f\n0,%.6f,%.6f\n0,0,1\n",
                                Double(k.columns.0.x), Double(k.columns.2.x), Double(k.columns.1.y), Double(k.columns.2.y))
                try cm.write(to: dir.appendingPathComponent("camera_matrix.csv"), atomically: true, encoding: .utf8)
            }
            try "timestamp, a_x, a_y, a_z, alpha_x, alpha_y, alpha_z\n"
                .write(to: dir.appendingPathComponent("imu.csv"), atomically: true, encoding: .utf8)
            let meta: [String: Any] = ["app": "PartScan iOS", "appVersion": appVersion, "format": "stray-scanner-1.4",
                                       "frames": frames, "dropped": dropped, "seconds": seconds, "stride": frameStride,
                                       "video": [Int(size.width), Int(size.height)],
                                       "device": deviceModel, "modelId": modelId, "system": systemVersion,
                                       "coverage": ["low": coverage.low, "mid": coverage.mid,
                                                    "high": coverage.high, "top": coverage.top]]
            let data = try JSONSerialization.data(withJSONObject: meta, options: [.prettyPrinted])
            try data.write(to: dir.appendingPathComponent("partscan.json"))
        } catch {
            note = "could not write the pose file: \(error.localizedDescription)"
        }
        if !videoOK { note = "video error: \(videoError ?? "unknown")" }
        let bytes = ScanStore.folderSize(dir)
        let record = ScanRecord(id: id, createdAt: Date(), frames: frames, seconds: seconds, bytes: bytes,
                                videoWidth: Int(size.width), videoHeight: Int(size.height),
                                status: note == nil ? "recorded" : "error", jobId: nil, serverURL: nil,
                                summaryText: nil, note: note)
        DispatchQueue.main.async {
            self.finishedScan = record
            if let note { self.lastError = note }
        }
    }

    // MARK: ARSessionDelegate (called on `queue`)

    func session(_ session: ARSession, didUpdate frame: ARFrame) {
        let camera = frame.camera
        let t = camera.transform
        let look = -SIMD3<Float>(t.columns.2.x, t.columns.2.y, t.columns.2.z)   // ARKit cameras look down -Z
        let horizontal = max(1e-6, (look.x * look.x + look.z * look.z).squareRoot())
        let elevation = Double(atan2(-look.y, horizontal)) * 180 / .pi          // + = looking downwards
        let azimuth = Double(atan2(look.x, look.z)) * 180 / .pi
        let hasDepth = frame.sceneDepth != nil
        uiTick += 1
        if recording {
            tracker.add(elevationDeg: elevation, azimuthDeg: azimuth)
            lastTimestamp = frame.timestamp
        }
        if uiTick % 6 == 0 {
            let snap = tracker.snapshot()
            let frames = written, drops = dropped
            let secs: Double? = recording ? max(0, frame.timestamp - (startTimestamp ?? frame.timestamp)) : nil
            let text = Self.describe(camera.trackingState)
            DispatchQueue.main.async {
                self.elevationDeg = elevation
                self.depthAvailable = hasDepth
                self.coverage = snap
                self.frameCount = frames
                self.droppedFrames = drops
                if let secs { self.elapsed = secs }
                self.trackingText = text
            }
        }
        guard recording, let writer, let input, let adaptor, let dir = scanDir else { return }
        seen += 1
        if seen % max(1, frameStride) != 0 { return }
        guard let depth = frame.sceneDepth else { dropped += 1; return }
        guard writer.status == .writing, input.isReadyForMoreMediaData else { dropped += 1; return }
        if startTimestamp == nil {
            startTimestamp = frame.timestamp
            writer.startSession(atSourceTime: .zero)
        }
        let seconds = frame.timestamp - (startTimestamp ?? frame.timestamp)
        let pts = CMTime(seconds: seconds, preferredTimescale: 600)
        guard adaptor.append(frame.capturedImage, withPresentationTime: pts) else { dropped += 1; return }
        let index = written
        written += 1
        rows.append(Self.odometryRow(index: index, timestamp: frame.timestamp, transform: t,
                                     intrinsics: camera.intrinsics))
        lastIntrinsics = camera.intrinsics
        let depthImage = Self.millimetres(from: depth.depthMap)
        let confidence = depth.confidenceMap.map { Self.bytes(from: $0) }
        let name = String(format: "%06d.png", index)
        writeQueue.async {
            PNGWriter.writeGray16(depthImage.pixels, width: depthImage.width, height: depthImage.height,
                                  to: dir.appendingPathComponent("depth").appendingPathComponent(name))
            if let confidence {
                PNGWriter.writeGray8(confidence.pixels, width: confidence.width, height: confidence.height,
                                     to: dir.appendingPathComponent("confidence").appendingPathComponent(name))
            }
        }
    }

    func session(_ session: ARSession, didFailWithError error: Error) {
        DispatchQueue.main.async {
            self.lastError = error.localizedDescription
            self.isSessionRunning = false
        }
    }

    func sessionWasInterrupted(_ session: ARSession) {
        DispatchQueue.main.async { self.trackingText = "camera interrupted" }
    }

    func sessionInterruptionEnded(_ session: ARSession) {
        DispatchQueue.main.async { self.trackingText = "camera back" }
    }

    // MARK: helpers

    private static func describe(_ state: ARCamera.TrackingState) -> String {
        switch state {
        case .normal: return "tracking OK"
        case .notAvailable: return "no tracking yet"
        case .limited(let reason):
            switch reason {
            case .initializing: return "initialising — move the phone slowly"
            case .excessiveMotion: return "too fast — slow down"
            case .insufficientFeatures: return "needs more detail in view"
            case .relocalizing: return "relocalising"
            @unknown default: return "limited tracking"
            }
        }
    }

    struct Gray16 { let pixels: [UInt16]; let width: Int; let height: Int }
    struct Gray8 { let pixels: [UInt8]; let width: Int; let height: Int }

    /// Float32 metres → UInt16 millimetres (0 = invalid).
    static func millimetres(from buffer: CVPixelBuffer) -> Gray16 {
        CVPixelBufferLockBaseAddress(buffer, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(buffer, .readOnly) }
        let w = CVPixelBufferGetWidth(buffer), h = CVPixelBufferGetHeight(buffer)
        let stride = CVPixelBufferGetBytesPerRow(buffer)
        var out = [UInt16](repeating: 0, count: w * h)
        guard let base = CVPixelBufferGetBaseAddress(buffer) else { return Gray16(pixels: out, width: w, height: h) }
        for y in 0..<h {
            let row = base.advanced(by: y * stride).assumingMemoryBound(to: Float32.self)
            for x in 0..<w {
                let d = row[x]
                if d.isFinite && d > 0 {
                    out[y * w + x] = UInt16(min(65535, max(0, (d * 1000).rounded())))
                }
            }
        }
        return Gray16(pixels: out, width: w, height: h)
    }

    static func bytes(from buffer: CVPixelBuffer) -> Gray8 {
        CVPixelBufferLockBaseAddress(buffer, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(buffer, .readOnly) }
        let w = CVPixelBufferGetWidth(buffer), h = CVPixelBufferGetHeight(buffer)
        let stride = CVPixelBufferGetBytesPerRow(buffer)
        var out = [UInt8](repeating: 0, count: w * h)
        guard let base = CVPixelBufferGetBaseAddress(buffer) else { return Gray8(pixels: out, width: w, height: h) }
        for y in 0..<h {
            let row = base.advanced(by: y * stride).assumingMemoryBound(to: UInt8.self)
            for x in 0..<w {
                out[y * w + x] = row[x]
            }
        }
        return Gray8(pixels: out, width: w, height: h)
    }

    /// One odometry.csv line. ARKit's camera axes (x right, y up, z backwards) become OpenCV's
    /// (x right, y down, z forward) by negating the second and third rotation columns; the quaternion is
    /// scalar-last, as Stray Scanner writes it.
    static func odometryRow(index: Int, timestamp: TimeInterval, transform t: simd_float4x4,
                            intrinsics k: simd_float3x3) -> String {
        let c0 = SIMD3<Float>(t.columns.0.x, t.columns.0.y, t.columns.0.z)
        let c1 = SIMD3<Float>(t.columns.1.x, t.columns.1.y, t.columns.1.z)
        let c2 = SIMD3<Float>(t.columns.2.x, t.columns.2.y, t.columns.2.z)
        let rotation = simd_float3x3(columns: (c0, -c1, -c2))
        let q = simd_quatf(rotation)
        let p = t.columns.3
        return String(format: "%.6f, %06ld, %.7f, %.7f, %.7f, %.8f, %.8f, %.8f, %.8f, %.4f, %.4f, %.4f, %.4f, , ",
                      timestamp, index, Double(p.x), Double(p.y), Double(p.z),
                      Double(q.imag.x), Double(q.imag.y), Double(q.imag.z), Double(q.real),
                      Double(k.columns.0.x), Double(k.columns.1.y), Double(k.columns.2.x), Double(k.columns.2.y))
    }
}

/// How much of the capture pattern (three rings + the top pass) has been covered, from where the camera
/// has been looking: the elevation of the viewing direction and its compass direction.
struct CoverageTracker {
    struct Snapshot: Equatable {
        var low = 0.0, mid = 0.0, high = 0.0, top = 0.0
        var total: Double { (low + mid + high + top) / 4 }
    }

    static let sectors = 12
    private var counts = [[Int]](repeating: [Int](repeating: 0, count: CoverageTracker.sectors), count: 3)
    private var topCount = 0

    mutating func add(elevationDeg e: Double, azimuthDeg a: Double) {
        if e >= 78 { topCount += 1; return }
        let band: Int
        if e < 8 { return } else if e < 32 { band = 0 } else if e < 58 { band = 1 } else { band = 2 }
        let wrapped = (a + 360).truncatingRemainder(dividingBy: 360)
        let sector = min(max(Int(wrapped / (360 / Double(Self.sectors))), 0), Self.sectors - 1)
        counts[band][sector] += 1
    }

    func snapshot() -> Snapshot {
        func fraction(_ band: Int) -> Double {
            Double(counts[band].filter { $0 >= 4 }.count) / Double(Self.sectors)
        }
        return Snapshot(low: fraction(0), mid: fraction(1), high: fraction(2), top: min(1, Double(topCount) / 60))
    }

    static func bandName(forElevation e: Double) -> String {
        if e >= 78 { return "top pass" }
        if e < 8 { return "too low — aim down at the part" }
        if e < 32 { return "low ring" }
        if e < 58 { return "middle ring" }
        return "high ring"
    }
}
