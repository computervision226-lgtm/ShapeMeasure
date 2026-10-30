import SwiftUI
import PhotosUI
import AVFoundation
import UIKit

@MainActor
struct HomeView: View {
    @EnvironmentObject private var store: ScanStore
    @State private var photos: [PhotosPickerItem] = []
    @State private var movie: PhotosPickerItem?
    @State private var showRecorder = false
    @State private var showLiDAR = false
    @State private var pendingDelete: ScanRecord?
    var body: some View {
        NavigationStack(path: $store.path) {
            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    VStack(alignment: .leading, spacing: 10) {
                        Label("SHAPEMEASURE", systemImage: "viewfinder").font(.caption.weight(.bold)).tracking(2).foregroundStyle(Palette.mint)
                        Text("From object\nto measured shape.").font(.system(size: 34, weight: .semibold, design: .rounded))
                        Text("Capture every side. Measure in 3D. Export your GIS files.").foregroundStyle(.secondary)
                    }.padding(.top, 8)
                    Panel {
                        Label("Direct iPhone LiDAR", systemImage: "sensor.fill").font(.headline).foregroundStyle(Palette.mint)
                        Button { showLiDAR = true } label: { Label("LiDAR Point Cloud", systemImage: "cube.transparent") }
                            .buttonStyle(FilledButton())
                        Text(LiDARScanner.supported ? "Read rear depth frames directly. Preview and save measured XYZ points as PLY and XYZ files." : "Capture needs a LiDAR-equipped iPhone Pro. Open to share any previously saved clouds.")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                    Panel {
                        HStack { Image(systemName: store.supported ? "checkmark.circle.fill" : "info.circle").foregroundStyle(Palette.mint)
                            Text(store.supported ? "Guided Object Capture available" : "Object Capture requires a supported iPhone").font(.subheadline) }
                        Button { store.startGuided() } label: { Label("Scan an object", systemImage: "camera.viewfinder") }
                            .buttonStyle(FilledButton()).disabled(!store.supported)
                        Text("Best for small, rigid objects. Circle the object slowly at several heights.").font(.footnote).foregroundStyle(.secondary)
                    }
                    Panel {
                        Text("Already have photos or video?").font(.headline)
                        PhotosPicker(selection: $photos, maxSelectionCount: 120, matching: .images) {
                            Label("Import overlapping photos", systemImage: "photo.on.rectangle.angled").frame(maxWidth: .infinity, alignment: .leading)
                        }.padding(.vertical, 5).disabled(!store.supported)
                        PhotosPicker(selection: $movie, matching: .videos) {
                            Label("Import an orbit video", systemImage: "film").frame(maxWidth: .infinity, alignment: .leading)
                        }.padding(.vertical, 5).disabled(!store.supported)
                        Button { prepareRecorder() } label: {
                            Label("Record an orbit video", systemImage: "video").frame(maxWidth: .infinity, alignment: .leading)
                        }.padding(.vertical, 5).disabled(!store.supported)
                        Text("Use 20–120 photos or a 10–120 second video. These imports need a known-length scale check.")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                    HStack { Text("Your objects").font(.title3.weight(.semibold)); Spacer()
                        Button("Try demo") { store.addDemo() }.font(.subheadline) }
                    if store.scans.isEmpty {
                        Text("Your scans will appear here. Try the synthetic box to explore measurements and exports.")
                            .foregroundStyle(.secondary).padding(.bottom, 20)
                    }
                    ForEach(store.scans) { record in
                        Panel {
                            NavigationLink(value: record.id) {
                                HStack(spacing: 14) {
                                    Image(systemName: "cube.transparent.fill").font(.title).foregroundStyle(Palette.mint)
                                    VStack(alignment: .leading, spacing: 5) {
                                        Text(record.name).font(.headline).foregroundStyle(.white)
                                        Text(record.scaleSource.title).font(.caption).foregroundStyle(.secondary)
                                        Text(record.createdAt, style: .date).font(.caption2).foregroundStyle(.secondary)
                                    }; Spacer(); Image(systemName: "chevron.right").foregroundStyle(.secondary)
                                }
                            }.buttonStyle(.plain)
                            HStack {
                                Text(record.kind.title).font(.caption).foregroundStyle(.secondary)
                                Spacer()
                                Button(role: .destructive) { pendingDelete = record } label: { Image(systemName: "trash") }
                                    .accessibilityLabel("Delete \(record.name)")
                            }
                        }
                    }
                    Text("All reconstruction stays on your iPhone. Export coordinates are local metres.").font(.footnote).foregroundStyle(.secondary)
                }.padding(22)
            }.background(Palette.background).navigationBarTitleDisplayMode(.inline)
                .navigationDestination(for: UUID.self) { id in ReviewView(store: store, id: id) }
        }
        .onChange(of: photos) { _, selected in if !selected.isEmpty { store.importPhotos(selected); photos = [] } }
        .onChange(of: movie) { _, selected in if let selected { store.importVideo(item: selected); movie = nil } }
        .onChange(of: store.workflow?.id) { _, value in UIApplication.shared.isIdleTimerDisabled = value != nil }
        .fullScreenCover(item: $store.workflow) { draft in
            if store.phase == .capture, let session = store.captureSession { CaptureView(draft: draft, session: session).environmentObject(store) }
            else { ProcessingView().environmentObject(store) }
        }
        .fullScreenCover(isPresented: $showRecorder) {
            CameraRecorder(onVideo: { store.importRecordedVideo($0) }, onError: { store.notice = AppNotice(message: $0) }).ignoresSafeArea()
        }
        .fullScreenCover(isPresented: $showLiDAR) { LiDARView() }
        .alert("ShapeMeasure", isPresented: Binding(get: { store.notice != nil }, set: { if !$0 { store.notice = nil } }), presenting: store.notice) { _ in
            Button("OK", role: .cancel) { store.notice = nil }
        } message: { notice in Text(notice.message) }
        .confirmationDialog("Delete this scan and its captured images?", isPresented: Binding(get: { pendingDelete != nil }, set: { if !$0 { pendingDelete = nil } }), titleVisibility: .visible) {
            Button("Delete scan", role: .destructive) { if let record = pendingDelete { store.delete(record) }; pendingDelete = nil }
        }
    }
    private func prepareRecorder() {
        Task {
            guard UIImagePickerController.isSourceTypeAvailable(.camera) else {
                store.notice = AppNotice(message: "Video recording needs a physical iPhone camera."); return
            }
            let camera = await AVCaptureDevice.requestAccess(for: .video)
            guard camera else { store.notice = AppNotice(message: "Enable Camera access in Settings to record video."); return }
            _ = await AVCaptureDevice.requestAccess(for: .audio)
            showRecorder = true
        }
    }
}
