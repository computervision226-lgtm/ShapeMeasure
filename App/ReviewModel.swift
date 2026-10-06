import SwiftUI
import Combine

struct SharedArchive: Identifiable { var id = UUID(); var url: URL }

@MainActor
final class ReviewModel: ObservableObject {
    let store: ScanStore
    let id: UUID
    @Published var record: ScanRecord?
    @Published var mesh: MeshData?
    @Published var summary: MeasurementSummary?
    @Published var picks: [Point3] = []
    @Published var unit: DisplayUnit = .cm
    @Published var calibrationUnit: DisplayUnit = .cm
    @Published var knownLength = ""
    @Published var measurementLabel = ""
    @Published var share: SharedArchive?
    @Published var exporting = false
    @Published var errorMessage: String?
    init(store: ScanStore, id: UUID) { self.store = store; self.id = id; record = store.record(id) }

    func load() async {
        guard mesh == nil else { return }
        do {
            let directory = ScanFiles.directory(id)
            let result = try await Task.detached(priority: .userInitiated) {
                let mesh = try ScanFiles.read(MeshData.self, from: directory.appendingPathComponent("mesh.json"))
                return (mesh, try ExportService.analyze(mesh))
            }.value
            mesh = result.0; summary = result.1
        } catch { errorMessage = error.localizedDescription }
    }
    func picked(_ p: Point3) { if picks.count == 2 { picks = [] }; picks.append(p) }
    var rawDistance: Double? { picks.count == 2 ? picks[0].distance(to: picks[1]) : nil }
    var selectedLength: String {
        guard let raw = rawDistance else { return "Tap two points on the object" }
        guard let scale = record?.scale else { return "Two points selected · set a known length" }
        return unit.format(metres: raw * scale)
    }
    func rename(_ name: String) {
        guard var current = record else { return }
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        current.name = String(trimmed.prefix(100)); commit(current)
    }
    func calibrate() {
        guard var current = record, let distance = rawDistance, distance > 1e-9,
              let amount = Double(knownLength.replacingOccurrences(of: ",", with: ".")), amount.isFinite, amount > 0 else {
            errorMessage = "Tap two distinct object points, then enter their real distance."; return
        }
        let factor = (amount / calibrationUnit.factor) / distance
        guard factor.isFinite, factor > 0, factor <= 1e9 else { errorMessage = "That scale is outside the supported range."; return }
        current.scale = factor; current.scaleSource = .reference; current.scaleConfirmed = true
        commit(current); knownLength = ""
    }
    func confirmLiDAR() {
        guard var current = record, current.scaleSource == .lidar else { return }
        current.scaleConfirmed = true; commit(current)
    }
    func saveMeasurement() {
        guard var current = record, current.scale != nil, picks.count == 2, let raw = rawDistance, raw > 1e-9 else { return }
        guard current.measurements.count < 200 else { errorMessage = "This scan supports up to 200 saved measurements."; return }
        let title = measurementLabel.trimmingCharacters(in: .whitespacesAndNewlines)
        current.measurements.append(SavedMeasurement(label: title.isEmpty ? "Distance \(current.measurements.count + 1)" : String(title.prefix(47)), a: picks[0], b: picks[1]))
        commit(current); picks = []; measurementLabel = ""
    }
    func deleteMeasurement(_ id: UUID) {
        guard var current = record else { return }
        current.measurements.removeAll { $0.id == id }; commit(current)
    }
    private func commit(_ current: ScanRecord) {
        do { try store.save(current); record = current }
        catch { errorMessage = error.localizedDescription }
    }
    func export() {
        guard let mesh, let record, record.canExport, !exporting else { return }
        exporting = true
        Task {
            defer { exporting = false }
            do {
                let directory = ScanFiles.directory(record.id)
                let archive = try await Task.detached(priority: .userInitiated) {
                    try ExportService.export(mesh: mesh, record: record, directory: directory)
                }.value
                share = SharedArchive(url: archive)
            } catch { errorMessage = error.localizedDescription }
        }
    }
}
