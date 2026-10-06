import SwiftUI
import Combine
import RealityKit
import AVFoundation
import PhotosUI

@MainActor
final class ScanStore: ObservableObject {
    enum Phase { case capture, processing }
    @Published var scans: [ScanRecord] = []
    @Published var path: [UUID] = []
    @Published var workflow: ScanDraft?
    @Published var phase: Phase = .capture
    @Published var progress: Double = 0
    @Published var status = ""
    @Published var notice: AppNotice?
    @Published var captureSession: ObjectCaptureSession?
    @Published var warnings: [String] = []
    private var reconstruction: PhotogrammetrySession?
    private var operation: Task<Void, Never>?
    private var operationToken = UUID()
    var supported: Bool { ObjectCaptureSession.isSupported && PhotogrammetrySession.isSupported }

    init() { reload() }
    func reload() {
        do {
            try FileManager.default.createDirectory(at: ScanFiles.root, withIntermediateDirectories: true)
            let directories = try FileManager.default.contentsOfDirectory(at: ScanFiles.root, includingPropertiesForKeys: nil)
            scans = directories.compactMap { try? ScanFiles.read(ScanRecord.self, from: $0.appendingPathComponent("scan.json")) }
                .sorted { $0.createdAt > $1.createdAt }
        } catch { notice = AppNotice(message: error.localizedDescription) }
    }
    func record(_ id: UUID) -> ScanRecord? { scans.first { $0.id == id } }
    func save(_ record: ScanRecord) throws {
        try ScanFiles.write(record, to: ScanFiles.directory(record.id).appendingPathComponent("scan.json"))
        reload()
    }
    func delete(_ record: ScanRecord) {
        do { try FileManager.default.removeItem(at: ScanFiles.directory(record.id)); reload() }
        catch { notice = AppNotice(message: error.localizedDescription) }
    }
    private func makeDraft(kind: CaptureKind) throws -> ScanDraft {
        let id = UUID(), formatter = DateFormatter()
        formatter.dateFormat = "MMM d, HH:mm"
        let draft = ScanDraft(id: id, name: "Object · " + formatter.string(from: Date()), kind: kind, directory: ScanFiles.directory(id))
        for directory in [draft.images, draft.checkpoints] {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        }
        warnings = []; progress = 0
        return draft
    }
    func startGuided() {
        guard workflow == nil else { return }
        guard supported else { notice = AppNotice(message: "Guided capture requires a supported LiDAR iPhone and iOS 17 or later. The demo works without LiDAR."); return }
        let token = UUID(); operationToken = token
        operation = Task {
            let authorized = await AVCaptureDevice.requestAccess(for: .video)
            guard !Task.isCancelled, operationToken == token else { return }
            guard authorized else { notice = AppNotice(message: "Enable Camera access for ShapeMeasure in Settings to scan objects."); return }
            do {
                let draft = try makeDraft(kind: .guided)
                let session = ObjectCaptureSession()
                var configuration = ObjectCaptureSession.Configuration()
                configuration.checkpointDirectory = draft.checkpoints
                captureSession = session; phase = .capture; workflow = draft
                session.start(imagesDirectory: draft.images, configuration: configuration)
            } catch { handle(error, token: token) }
        }
    }
    func finishCapture(_ draft: ScanDraft) {
        guard workflow?.id == draft.id else { return }
        captureSession = nil; phase = .processing; status = "Building the 3D shape…"
        let token = UUID(); operationToken = token
        operation = Task { do { try await reconstruct(draft) } catch { handle(error, token: token) } }
    }
    func importPhotos(_ items: [PhotosPickerItem]) {
        guard workflow == nil else { return }
        guard items.count >= 20, items.count <= 120 else {
            notice = AppNotice(message: "Select 20–120 overlapping photos of the same stationary object, taken from different angles."); return
        }
        let token = UUID(); operationToken = token
        operation = Task {
            do {
                let draft = try makeDraft(kind: .photos); workflow = draft; phase = .processing
                status = "Preparing photos…"
                for (index, item) in items.enumerated() {
                    try Task.checkCancellation()
                    guard let data = try await item.loadTransferable(type: Data.self) else {
                        throw ScanFailure.message("A selected photo is unavailable. Download it to your phone and try again.")
                    }
                    try Task.checkCancellation()
                    let url = draft.images.appendingPathComponent(String(format: "photo_%04d.jpg", index))
                    try await Task.detached(priority: .userInitiated) { try MediaImporter.saveJPEG(data, to: url) }.value
                    try Task.checkCancellation()
                    progress = Double(index + 1) / Double(items.count)
                }
                try await reconstruct(draft)
            } catch { handle(error, token: token) }
        }
    }
    func importVideo(item: PhotosPickerItem) {
        guard workflow == nil else { return }
        let token = UUID(); operationToken = token
        operation = Task {
            do {
                let draft = try makeDraft(kind: .video); workflow = draft; phase = .processing
                status = "Loading video…"
                guard let movie = try await item.loadTransferable(type: ImportedMovie.self) else {
                    throw ScanFailure.message("The video could not be loaded.")
                }
                defer { try? FileManager.default.removeItem(at: movie.url) }
                try Task.checkCancellation()
                try await prepareVideo(movie.url, draft: draft)
                try await reconstruct(draft)
            } catch { handle(error, token: token) }
        }
    }
    func importRecordedVideo(_ url: URL) {
        guard workflow == nil else { return }
        let token = UUID(); operationToken = token
        operation = Task {
            do {
                defer { try? FileManager.default.removeItem(at: url) }
                let draft = try makeDraft(kind: .video); workflow = draft; phase = .processing
                try await prepareVideo(url, draft: draft)
                try await reconstruct(draft)
            } catch { handle(error, token: token) }
        }
    }
    private func prepareVideo(_ url: URL, draft: ScanDraft) async throws {
        status = "Extracting overlapping video frames…"; progress = 0
        _ = try await MediaImporter.extractVideo(url, into: draft.images) { [weak self] value in
            await MainActor.run { if self?.workflow?.id == draft.id { self?.progress = value } }
        }
        try Task.checkCancellation()
    }
    private func reconstruct(_ draft: ScanDraft) async throws {
        try Task.checkCancellation()
        guard PhotogrammetrySession.isSupported else {
            throw ScanFailure.message("This device cannot reconstruct objects. Use a supported LiDAR iPhone.")
        }
        let images = try FileManager.default.contentsOfDirectory(at: draft.images, includingPropertiesForKeys: nil)
            .filter { ["jpg","jpeg","heic","png"].contains($0.pathExtension.lowercased()) }
        guard images.count >= 20 else { throw ScanFailure.message("Capture at least 20 well-overlapping images before reconstruction.") }
        status = "Reconstructing on your iPhone…"; progress = 0
        var configuration = PhotogrammetrySession.Configuration()
        configuration.checkpointDirectory = draft.checkpoints
        configuration.isObjectMaskingEnabled = true
        let session = try PhotogrammetrySession(input: draft.images, configuration: configuration)
        reconstruction = session
        defer { if reconstruction === session { reconstruction = nil } }
        try session.process(requests: [.modelFile(url: draft.model, detail: .reduced)])
        var completed = false
        for try await output in session.outputs {
            try Task.checkCancellation()
            switch output {
            case .requestProgress(_, let fraction): progress = fraction
            case .requestError(_, let error): throw error
            case .requestComplete: completed = true
            case .invalidSample(_, let reason): warnings.append("An image was rejected: \(reason)")
            case .skippedSample: warnings.append("An image was skipped during reconstruction.")
            case .automaticDownsampling: warnings.append("Images were downsampled to fit device limits.")
            case .processingComplete: break
            default: break
            }
            if case .processingComplete = output { break }
        }
        guard completed, FileManager.default.fileExists(atPath: draft.model.path) else {
            throw ScanFailure.message("Reconstruction did not produce a model. Use more overlap and better lighting.")
        }
        status = "Preparing measurements and geometry…"; progress = 1
        let mesh = try await Task.detached(priority: .userInitiated) {
            let loaded = try MeshLoader.load(url: draft.model)
            _ = try ExportService.analyze(loaded)
            return loaded
        }.value
        try Task.checkCancellation()
        let guided = draft.kind == .guided
        let record = ScanRecord(id: draft.id, name: draft.name, kind: draft.kind, imageCount: images.count,
                                scale: guided ? 1 : nil, scaleSource: guided ? .lidar : .unverified,
                                scaleConfirmed: false, warnings: warnings)
        try ScanFiles.write(mesh, to: draft.directory.appendingPathComponent("mesh.json"))
        try save(record)
        workflow = nil; captureSession = nil; path.append(record.id)
    }
    func cancel() {
        operationToken = UUID()
        operation?.cancel(); reconstruction?.cancel(); captureSession?.cancel()
        let directory = workflow?.directory
        workflow = nil; captureSession = nil; reconstruction = nil
        // Let any in-flight image decode finish before deleting its draft directory.
        let previous = operation
        operation = nil
        Task { await previous?.value; if let directory, !FileManager.default.fileExists(atPath: directory.appendingPathComponent("scan.json").path) { try? FileManager.default.removeItem(at: directory) } }
    }
    func handle(_ error: Error, token: UUID? = nil) {
        if let token, token != operationToken { return }
        let cancelled = error is CancellationError || operation?.isCancelled == true
        let draft = workflow
        reconstruction?.cancel(); captureSession?.cancel(); reconstruction = nil; captureSession = nil; workflow = nil
        if let draft, !FileManager.default.fileExists(atPath: draft.directory.appendingPathComponent("scan.json").path) {
            try? FileManager.default.removeItem(at: draft.directory)
        }
        if !cancelled { notice = AppNotice(message: error.localizedDescription) }
    }
    func addDemo() {
        do {
            let draft = try makeDraft(kind: .demo)
            let record = ScanRecord(id: draft.id, name: "Demo · 20 × 10 × 5 cm", kind: .demo, imageCount: 0,
                                    scale: 1, scaleSource: .demo, scaleConfirmed: true)
            try ScanFiles.write(MeshData.demo, to: draft.directory.appendingPathComponent("mesh.json"))
            try save(record); path.append(record.id)
        } catch { handle(error) }
    }
}
