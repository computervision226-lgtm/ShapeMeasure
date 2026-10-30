import SwiftUI

/// Options → zip → upload → wait for the server → results, for one recorded scan.
struct UploadView: View {
    let scan: ScanRecord
    @EnvironmentObject private var settings: ServerSettings
    @EnvironmentObject private var store: ScanStore
    @Environment(\.dismiss) private var dismiss
    @StateObject private var model = UploadModel()
    @State private var options = ScanOptions()
    @State private var shareItem: ShareFile?

    var body: some View {
        Form {
            Section("Scan") {
                LabeledContent("Recorded", value: scan.title)
                LabeledContent("Frames", value: "\(scan.frames) · \(String(format: "%.0f", scan.seconds)) s · \(scan.sizeText)")
                if let note = scan.note { Text(note).foregroundStyle(.orange).font(.footnote) }
            }
            if let job = model.job, job.isDone {
                ResultsSection(job: job, client: model.client)
            } else {
                optionsSection
                progressSection
            }
        }
        .navigationTitle(model.job?.isDone == true ? "Result" : "Upload & build")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarLeading) { Button("Close") { dismiss() } }
            ToolbarItem(placement: .topBarTrailing) {
                Button { shareScan() } label: { Image(systemName: "square.and.arrow.up") }
                    .disabled(model.phase == .zipping || model.phase == .uploading)
                    .accessibilityLabel("Share the raw scan")
            }
        }
        .sheet(item: $shareItem) { file in ActivityView(items: [file.url]) }
        .onAppear {
            model.configure(settings: settings, store: store)
            if let jobId = scan.jobId, scan.status != "recorded" {
                model.resume(scan: scan, jobId: jobId)
            }
        }
    }

    private var optionsSection: some View {
        Group {
            Section("Quality") {
                Picker("Preset", selection: $options.preset) {
                    ForEach(ScanOptions.presets, id: \.self) { Text($0) }
                }
            }
            Section("Calibration board") {
                Toggle("Part is on the printed PartScan board", isOn: $options.useBoard)
                if options.useBoard {
                    Picker("Board", selection: $options.boardType) {
                        ForEach(ScanOptions.boards, id: \.self) { Text($0) }
                    }
                    HStack {
                        Text("Print check (mm)")
                        Spacer()
                        TextField("100", text: $options.checkMM).keyboardType(.decimalPad)
                            .multilineTextAlignment(.trailing).frame(width: 110)
                    }
                    Picker("Measured on", selection: $options.checkOn) {
                        ForEach(ScanOptions.checks, id: \.self) { Text($0) }
                    }
                    Text("Printers scale by up to ~1 %: measure the printed 100 mm ruler once and enter it here.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            }
            Section("Caliper check (optional)") {
                Picker("Feature you measured", selection: $options.refName) {
                    ForEach(ScanOptions.references, id: \.self) { Text($0.isEmpty ? "none" : $0) }
                }
                if !options.refName.isEmpty {
                    HStack {
                        Text("Your measurement (mm)")
                        Spacer()
                        TextField("0", text: $options.refMM).keyboardType(.decimalPad)
                            .multilineTextAlignment(.trailing).frame(width: 110)
                    }
                    Toggle("Rescale the model to match", isOn: $options.refApply)
                }
            }
            Section("Material (for mass)") {
                Picker("Material", selection: $options.material) {
                    ForEach(ScanOptions.materials, id: \.self) { Text($0) }
                }
            }
        }
    }

    private var progressSection: some View {
        Section {
            switch model.phase {
            case .idle:
                if !settings.isConfigured {
                    Label("Set up the server first (Server tab).", systemImage: "exclamationmark.triangle")
                        .foregroundStyle(.orange)
                }
                Button {
                    model.start(scan: scan, options: options)
                } label: {
                    Label("Upload & build 3D model", systemImage: "arrow.up.circle.fill").bold()
                }
                .disabled(!settings.isConfigured)
            case .zipping:
                Label("Packing the scan…", systemImage: "doc.zipper")
            case .uploading:
                VStack(alignment: .leading) {
                    Label(String(format: "Uploading… %.0f %%", model.uploadProgress * 100), systemImage: "icloud.and.arrow.up")
                    ProgressView(value: model.uploadProgress)
                }
            case .processing:
                VStack(alignment: .leading, spacing: 6) {
                    Label(model.job?.message ?? "queued", systemImage: "cpu")
                    ProgressView(value: model.job?.progress ?? 0)
                    Text("About 10 minutes on a T4 GPU. You can leave the app; come back and it continues from here.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            case .failed:
                Label(model.errorText ?? "failed", systemImage: "xmark.octagon.fill").foregroundStyle(.red)
                Button("Try again") { model.reset() }
            case .done:
                EmptyView()
            }
        }
    }

    private func shareScan() {
        do {
            shareItem = ShareFile(url: try store.zip(scan))
        } catch {
            model.errorText = "Could not pack the scan: \(error.localizedDescription)"
            model.phase = .failed
        }
    }
}

struct ShareFile: Identifiable {
    let url: URL
    var id: String { url.absoluteString }
}

@MainActor
final class UploadModel: ObservableObject {
    enum Phase { case idle, zipping, uploading, processing, done, failed }

    @Published var phase: Phase = .idle
    @Published var uploadProgress = 0.0
    @Published var job: JobStatus?
    @Published var errorText: String?

    private(set) var client: APIClient!
    private var store: ScanStore?
    private var scan: ScanRecord?
    private var pollTask: Task<Void, Never>?

    func configure(settings: ServerSettings, store: ScanStore) {
        if client == nil { client = APIClient(settings: settings) }
        self.store = store
    }

    func reset() {
        pollTask?.cancel()
        phase = .idle
        errorText = nil
        uploadProgress = 0
        job = nil
    }

    func start(scan: ScanRecord, options: ScanOptions) {
        self.scan = scan
        phase = .zipping
        errorText = nil
        Task {
            do {
                guard let store else { throw APIError.noServer }
                let zip = try await Task.detached(priority: .userInitiated) { try store.zip(scan) }.value
                phase = .uploading
                var record = scan
                record.status = "uploading"
                store.update(record)
                let job = try await client.upload(zip: zip, options: options) { [weak self] p in
                    Task { @MainActor in self?.uploadProgress = p }
                }
                try? FileManager.default.removeItem(at: zip)
                self.job = job
                record.status = "processing"
                record.jobId = job.jobId
                record.serverURL = client.serverDescription
                store.update(record)
                phase = .processing
                poll(jobId: job.jobId)
            } catch {
                fail(error.localizedDescription)
            }
        }
    }

    func resume(scan: ScanRecord, jobId: String) {
        self.scan = scan
        phase = .processing
        poll(jobId: jobId)
    }

    private func poll(jobId: String) {
        pollTask?.cancel()
        pollTask = Task {
            var failures = 0
            while !Task.isCancelled {
                do {
                    let status = try await client.job(jobId)
                    failures = 0
                    job = status
                    if status.isFinished {
                        finish(status)
                        return
                    }
                } catch {
                    failures += 1
                    if failures >= 5 {
                        fail("Lost the server while waiting: \(error.localizedDescription). Open this scan again later to continue.")
                        return
                    }
                }
                try? await Task.sleep(for: .seconds(4))
            }
        }
    }

    private func finish(_ status: JobStatus) {
        guard var record = scan else { return }
        if status.isDone {
            phase = .done
            record.status = "done"
            record.summaryText = status.summary?.text
        } else {
            phase = .failed
            errorText = status.error ?? "The server could not build this scan."
            record.status = "error"
            record.note = errorText
        }
        store?.update(record)
    }

    private func fail(_ text: String) {
        errorText = text
        phase = .failed
        if var record = scan {
            record.status = record.jobId == nil ? "recorded" : "processing"
            store?.update(record)
        }
    }
}

extension APIClient {
    var serverDescription: String { (try? url("api").absoluteString) ?? "" }
}
