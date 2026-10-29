import ARKit
import SceneKit
import SwiftUI

struct ScanView: View {
    @EnvironmentObject private var settings: ServerSettings
    @EnvironmentObject private var store: ScanStore
    @StateObject private var recorder = ScanRecorder()
    @State private var pendingScan: ScanRecord?
    @State private var showGuide = false

    var body: some View {
        ZStack {
            if ScanRecorder.lidarSupported {
                ARViewContainer(session: recorder.session)
                    .ignoresSafeArea()
            } else {
                Color.black.ignoresSafeArea()
            }
            VStack(spacing: 12) {
                statusBar
                Spacer()
                if recorder.isRecording {
                    coveragePanel
                }
                controls
            }
            .padding()
        }
        .navigationTitle("Scan")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button { showGuide = true } label: { Image(systemName: "questionmark.circle") }
            }
        }
        .onAppear {
            recorder.frameStride = settings.frameStride
            recorder.startSession()
        }
        .onDisappear { recorder.pauseSession() }
        .onChange(of: recorder.finishedScan) { _, scan in
            guard let scan else { return }
            store.add(scan)
            pendingScan = scan
        }
        .sheet(item: $pendingScan) { scan in
            NavigationStack { UploadView(scan: scan) }
        }
        .sheet(isPresented: $showGuide) { GuideSheet() }
    }

    private var statusBar: some View {
        VStack(alignment: .leading, spacing: 4) {
            if let err = recorder.lastError {
                Label(err, systemImage: "exclamationmark.triangle.fill")
                    .foregroundStyle(.orange)
            } else {
                HStack {
                    Label(recorder.trackingText, systemImage: "scope")
                    Spacer()
                    Label(recorder.depthAvailable ? "LiDAR ✓" : "no LiDAR depth",
                          systemImage: recorder.depthAvailable ? "checkmark.circle.fill" : "xmark.circle")
                        .foregroundStyle(recorder.depthAvailable ? .green : .orange)
                }
            }
            if recorder.isRecording {
                HStack {
                    Text(String(format: "● %.0f s", recorder.elapsed)).foregroundStyle(.red).bold()
                    Text("\(recorder.frameCount) frames")
                    if recorder.droppedFrames > 0 { Text("(\(recorder.droppedFrames) skipped)").foregroundStyle(.secondary) }
                    Spacer()
                    Text(String(format: "%.0f° · %@", recorder.elevationDeg,
                                CoverageTracker.bandName(forElevation: recorder.elevationDeg)))
                }
                .font(.footnote)
            }
        }
        .font(.subheadline)
        .padding(10)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 12))
    }

    private var coveragePanel: some View {
        VStack(alignment: .leading, spacing: 6) {
            CoverageRow(name: "Low ring (≈20°)", value: recorder.coverage.low)
            CoverageRow(name: "Middle ring (≈45°)", value: recorder.coverage.mid)
            CoverageRow(name: "High ring (≈70°)", value: recorder.coverage.high)
            CoverageRow(name: "Top pass (straight down)", value: recorder.coverage.top)
            Text("Keep 30–40 cm away, move slowly, keep the board in view. Holes in the sides? Finish with a few views straight into them.")
                .font(.caption2).foregroundStyle(.secondary)
        }
        .padding(10)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 12))
    }

    private var controls: some View {
        HStack {
            Spacer()
            Button {
                if recorder.isRecording { recorder.stopRecording() } else { recorder.startRecording() }
            } label: {
                ZStack {
                    Circle().strokeBorder(.white, lineWidth: 4).frame(width: 78, height: 78)
                    if recorder.isRecording {
                        RoundedRectangle(cornerRadius: 6).fill(.red).frame(width: 32, height: 32)
                    } else {
                        Circle().fill(.red).frame(width: 64, height: 64)
                    }
                }
            }
            .disabled(!recorder.isSessionRunning)
            .accessibilityLabel(recorder.isRecording ? "Stop recording" : "Start recording")
            Spacer()
        }
        .padding(.bottom, 8)
    }
}

struct CoverageRow: View {
    let name: String
    let value: Double

    var body: some View {
        HStack {
            Text(name).font(.caption).frame(width: 150, alignment: .leading)
            ProgressView(value: min(1, max(0, value)))
                .tint(value >= 0.9 ? .green : .orange)
            Text("\(Int((value * 100).rounded()))%").font(.caption.monospacedDigit()).frame(width: 40, alignment: .trailing)
        }
    }
}

struct ARViewContainer: UIViewRepresentable {
    let session: ARSession

    func makeUIView(context: Context) -> ARSCNView {
        let view = ARSCNView(frame: .zero)
        view.session = session
        view.scene = SCNScene()
        view.automaticallyUpdatesLighting = false
        view.rendersContinuously = true
        return view
    }

    func updateUIView(_ uiView: ARSCNView, context: Context) {}
}

struct GuideSheet: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    Group {
                        Text("1 · Prepare").font(.headline)
                        Text("Print the PartScan board at 100 %, glue it flat, and measure its printed 100 mm ruler (enter that value when uploading). Put the part near the middle of the board on its most stable face. Shiny, black or clear parts: dust them with scanning spray. Soft, even light; no glare on the paper.")
                        Text("2 · Record (40–90 s)").font(.headline)
                        Text("Hold the phone 30–40 cm from the part. Go all the way around at a low angle (≈20°), again at ≈45°, again at ≈70°, then a slow pass right over the top looking straight down. Holes in the sides: finish with a few views looking straight into each one. Move slowly — sharp frames matter more than many frames.")
                        Text("3 · Upload").font(.headline)
                        Text("Stop, choose the options, and upload. The server (a Colab notebook or a Hugging Face Space with a GPU, set up in the Server tab) builds the 3D model in roughly 10 minutes. You get the dimensions, holes, wall thicknesses, drawings, the STL/OBJ files and an interactive 3D viewer.")
                        Text("4 · Check").font(.headline)
                        Text("Always compare one dimension with a caliper. The caliper check in the upload options shows the real error of that scan.")
                    }
                    .font(.subheadline)
                }
                .padding()
            }
            .navigationTitle("How to scan")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { dismiss() } } }
        }
    }
}
