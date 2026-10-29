import SwiftUI

struct ScansListView: View {
    @EnvironmentObject private var store: ScanStore
    @State private var share: ShareFile?
    @State private var errorText: String?

    var body: some View {
        Group {
            if store.scans.isEmpty {
                ContentUnavailableView("No scans yet", systemImage: "cube.transparent",
                                       description: Text("Record a part in the Scan tab. Scans are also visible in the Files app under PartScan, so you can move one to Google Drive for the notebook."))
            } else {
                List {
                    ForEach(store.scans) { scan in
                        NavigationLink {
                            if scan.status == "done" {
                                JobResultView(scan: scan)
                            } else {
                                UploadView(scan: scan)
                            }
                        } label: {
                            ScanRow(scan: scan)
                        }
                        .swipeActions(edge: .trailing) {
                            Button(role: .destructive) { store.delete(scan) } label: { Label("Delete", systemImage: "trash") }
                            Button { shareScan(scan) } label: { Label("Share", systemImage: "square.and.arrow.up") }.tint(.blue)
                        }
                    }
                }
            }
        }
        .navigationTitle("Scans")
        .sheet(item: $share) { file in ActivityView(items: [file.url]) }
        .alert("Could not share", isPresented: Binding(get: { errorText != nil }, set: { if !$0 { errorText = nil } })) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(errorText ?? "")
        }
    }

    private func shareScan(_ scan: ScanRecord) {
        do {
            share = ShareFile(url: try store.zip(scan))
        } catch {
            errorText = error.localizedDescription
        }
    }
}

struct ScanRow: View {
    let scan: ScanRecord

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack {
                Text(scan.title).font(.headline)
                Spacer()
                statusBadge
            }
            if let summary = scan.summaryText {
                Text(summary).font(.subheadline.monospacedDigit())
            }
            Text("\(scan.frames) frames · \(String(format: "%.0f", scan.seconds)) s · \(scan.sizeText)")
                .font(.caption).foregroundStyle(.secondary)
            if let note = scan.note, scan.status == "error" {
                Text(note).font(.caption2).foregroundStyle(.orange)
            }
        }
    }

    private var statusBadge: some View {
        let (text, color): (String, Color) = {
            switch scan.status {
            case "done": return ("done", .green)
            case "processing": return ("processing", .orange)
            case "uploading": return ("uploading", .orange)
            case "error": return ("error", .red)
            default: return ("not uploaded", .gray)
            }
        }()
        return Text(text).font(.caption2.bold()).padding(.horizontal, 7).padding(.vertical, 3)
            .background(color.opacity(0.18), in: Capsule()).foregroundStyle(color)
    }
}
