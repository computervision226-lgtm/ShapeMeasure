import SwiftUI
import RealityKit
import UIKit

@MainActor
struct CaptureView: View {
    @EnvironmentObject private var store: ScanStore
    var draft: ScanDraft
    var session: ObjectCaptureSession
    @State private var passes = 1
    var body: some View {
        ZStack(alignment: .bottom) {
            ObjectCaptureView(session: session).ignoresSafeArea()
            VStack(alignment: .leading, spacing: 14) {
                HStack { Text("Capture your object").font(.headline); Spacer()
                    Button("Cancel") { store.cancel() }.foregroundStyle(.white) }
                Text(instruction).font(.subheadline)
                controls
            }.padding(22).background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 24))
                .padding(14)
        }.onChange(of: session.state) { _, state in
            switch state {
            case .completed: store.finishCapture(draft)
            case .failed(let error): store.handle(error)
            default: break
            }
        }
    }
    private var instruction: String {
        switch session.state {
        case .initializing: return "Preparing your camera…"
        case .ready: return "Center the object in the frame, then select it."
        case .detecting: return "Adjust the bounding box to include only your object."
        case .capturing:
            return session.userCompletedScanPass ? "Pass \(passes) complete. Add another height for more coverage, or finish." : "Pass \(passes): circle slowly and fill the capture dial."
        case .finishing: return "Saving images and depth. Keep this screen open."
        case .completed: return "Capture saved. Preparing reconstruction…"
        case .failed: return "Capture stopped."
        @unknown default: return "Follow the on-screen capture guide."
        }
    }
    @ViewBuilder private var controls: some View {
        switch session.state {
        case .ready:
            Button("Select object") { _ = session.startDetecting() }.buttonStyle(FilledButton())
        case .detecting:
            Button("Start capture") { session.startCapturing() }.buttonStyle(FilledButton())
            Button("Reset selection") { _ = session.resetDetection() }.frame(maxWidth: .infinity)
        case .capturing:
            HStack {
                Label("\(session.numberOfShotsTaken) images", systemImage: "photo.stack")
                Spacer(); Text("Aim for 3 passes").foregroundStyle(.secondary)
            }.font(.caption)
            if session.userCompletedScanPass {
                Button("Another pass at a different height") { passes += 1; session.beginNewScanPass() }.buttonStyle(.bordered)
                Button("Build 3D shape") { session.finish() }.buttonStyle(FilledButton()).disabled(session.numberOfShotsTaken < 20)
            } else {
                Button("Finish and build") { session.finish() }.buttonStyle(.bordered).disabled(session.numberOfShotsTaken < 20)
            }
        default: ProgressView().frame(maxWidth: .infinity)
        }
    }
}

@MainActor
struct ProcessingView: View {
    @EnvironmentObject private var store: ScanStore
    var body: some View {
        VStack(spacing: 28) {
            Spacer()
            Image(systemName: "cube.transparent").font(.system(size: 74)).foregroundStyle(Palette.mint)
            Text("Building your shape").font(.title.bold())
            Text(store.status).multilineTextAlignment(.center).foregroundStyle(.secondary)
            ProgressView(value: store.progress).tint(Palette.mint)
            Text("Keep ShapeMeasure open. Reconstruction can take several minutes.").font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center)
            if !store.warnings.isEmpty { Text("\(store.warnings.count) capture notes will be saved with this object.").font(.caption).foregroundStyle(.secondary) }
            Spacer()
            Button("Cancel") { store.cancel() }.buttonStyle(.bordered).padding(.bottom, 24)
        }.padding(32).frame(maxWidth: .infinity).background(Palette.background)
    }
}
