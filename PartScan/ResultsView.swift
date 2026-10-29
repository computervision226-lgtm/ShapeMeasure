import SwiftUI
import UIKit

/// The finished job: dimensions, warnings, drawing, measurement table, quality checks, viewer, downloads.
struct ResultsSection: View {
    let job: JobStatus
    let client: APIClient
    @State private var drawing: UIImage?
    @State private var web: WebTarget?
    @State private var share: ShareFile?
    @State private var busy = false
    @State private var errorText: String?

    struct WebTarget: Identifiable {
        let title: String
        let request: URLRequest
        var id: String { title }
    }

    var body: some View {
        Section {
            if let s = job.summary {
                VStack(alignment: .leading, spacing: 4) {
                    Text(s.text).font(.title2.bold().monospacedDigit())
                    Text("length × width × height (\(s.how ?? "")) · \(s.holes ?? 0) hole(s)\(roughText(s)) · scale from \(s.scaleReference ?? "?")")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            }
            ForEach(Array((job.warnings ?? []).enumerated()), id: \.offset) { _, w in
                Label(w, systemImage: "exclamationmark.triangle.fill").font(.footnote).foregroundStyle(.orange)
            }
            if let errorText { Text(errorText).font(.footnote).foregroundStyle(.red) }
        } header: {
            Text("Result")
        }

        Section("Dimensioned views") {
            if let drawing {
                Image(uiImage: drawing).resizable().scaledToFit()
            } else {
                ProgressView().task { await loadDrawing() }
            }
        }

        Section("Open") {
            Button {
                open(title: "3D viewer", key: "viewer")
            } label: { Label("Interactive 3D viewer (tap two points to measure)", systemImage: "cube") }
            Button {
                openRaw(title: "Report", name: "report.html")
            } label: { Label("Full report", systemImage: "doc.text") }
            Button {
                download(key: "zip")
            } label: { Label(busy ? "Downloading…" : "Save / share all files (zip with STL, OBJ, PLY, GLB…)", systemImage: "square.and.arrow.down") }
            .disabled(busy)
            Button {
                download(key: "stl")
            } label: { Label("Save / share the STL", systemImage: "square.and.arrow.down") }
            .disabled(busy)
        }

        Section("Measurements (mm)") {
            ForEach(Array((job.rows ?? []).enumerated()), id: \.offset) { _, row in
                VStack(alignment: .leading, spacing: 2) {
                    HStack(alignment: .firstTextBaseline) {
                        Text(row.count > 0 ? row[0] : "").font(.subheadline)
                        Spacer()
                        Text(row.count > 1 ? row[1] : "").font(.subheadline.monospacedDigit().bold())
                    }
                    if row.count > 2, !row[2].isEmpty {
                        Text(row[2]).font(.caption2).foregroundStyle(.secondary)
                    }
                }
            }
        }

        Section("Capture quality") {
            ForEach(Array((job.quality ?? []).enumerated()), id: \.offset) { _, row in
                HStack {
                    Circle().fill(color(for: row.count > 2 ? row[2] : "info")).frame(width: 9, height: 9)
                    Text(row.count > 0 ? row[0] : "").font(.subheadline)
                    Spacer()
                    Text(row.count > 1 ? row[1] : "").font(.subheadline.monospacedDigit())
                }
            }
        }
        .sheet(item: $web) { target in WebSheet(title: target.title, request: target.request) }
        .sheet(item: $share) { file in ActivityView(items: [file.url]) }
    }

    private func roughText(_ s: JobSummary) -> String {
        let n = s.roughHoles ?? 0
        return n > 0 ? " (\(n) rough)" : ""
    }

    private func color(for status: String) -> Color {
        switch status {
        case "good": return .green
        case "ok": return .orange
        case "poor": return .red
        default: return .gray
        }
    }

    private func loadDrawing() async {
        guard job.files?["drawing"] != nil else { return }
        do {
            let dest = FileManager.default.temporaryDirectory.appendingPathComponent("\(job.jobId)-drawing.png")
            let url = try await client.download(jobId: job.jobId, key: "drawing", to: dest)
            if let data = try? Data(contentsOf: url) { drawing = UIImage(data: data) }
        } catch {
            errorText = "Drawing: \(error.localizedDescription)"
        }
    }

    private func open(title: String, key: String) {
        do {
            web = WebTarget(title: title, request: try client.fileRequest(jobId: job.jobId, key: key))
        } catch {
            errorText = error.localizedDescription
        }
    }

    private func openRaw(title: String, name: String) {
        do {
            web = WebTarget(title: title, request: try client.fileRequest(jobId: job.jobId, raw: name))
        } catch {
            errorText = error.localizedDescription
        }
    }

    private func download(key: String) {
        guard let name = job.files?[key] else { errorText = "That file is not available for this scan."; return }
        busy = true
        Task {
            do {
                let dest = FileManager.default.temporaryDirectory.appendingPathComponent("\(job.jobId)-\(name)")
                let url = try await client.download(jobId: job.jobId, key: key, to: dest)
                share = ShareFile(url: url)
            } catch {
                errorText = error.localizedDescription
            }
            busy = false
        }
    }
}

/// Loads a finished job from the server (for the Scans list) and shows it.
struct JobResultView: View {
    let scan: ScanRecord
    @EnvironmentObject private var settings: ServerSettings
    @State private var job: JobStatus?
    @State private var errorText: String?
    @State private var client: APIClient?

    var body: some View {
        Form {
            Section("Scan") {
                LabeledContent("Recorded", value: scan.title)
                LabeledContent("Frames", value: "\(scan.frames) · \(String(format: "%.0f", scan.seconds)) s")
            }
            if let job, let client {
                if job.isDone {
                    ResultsSection(job: job, client: client)
                } else {
                    Section { Label(job.error ?? job.message ?? job.status, systemImage: "info.circle") }
                }
            } else if let errorText {
                Section { Label(errorText, systemImage: "xmark.octagon").foregroundStyle(.red) }
            } else {
                Section { ProgressView("Loading the result from the server…") }
            }
        }
        .navigationTitle("Result")
        .navigationBarTitleDisplayMode(.inline)
        .task {
            let c = APIClient(settings: settings)
            client = c
            guard let jobId = scan.jobId else { errorText = "This scan was never uploaded."; return }
            do {
                job = try await c.job(jobId)
            } catch {
                errorText = "\(error.localizedDescription) The results live on the server; if the notebook or Space was restarted, they may be gone — upload the scan again."
            }
        }
    }
}
