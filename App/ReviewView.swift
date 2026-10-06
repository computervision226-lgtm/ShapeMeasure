import SwiftUI

@MainActor
struct ReviewView: View {
    @StateObject private var model: ReviewModel
    @State private var name = ""
    init(store: ScanStore, id: UUID) { _model = StateObject(wrappedValue: ReviewModel(store: store, id: id)) }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                TextField("Object name", text: $name).font(.title2.weight(.semibold))
                    .onSubmit { model.rename(name) }
                if let mesh = model.mesh {
                    MeshPreview(mesh: mesh, picks: model.picks, measurements: model.record?.measurements ?? [], onPick: { model.picked($0) })
                        .frame(height: 310).clipShape(RoundedRectangle(cornerRadius: 20))
                    HStack { Text("Drag to rotate · pinch to zoom · tap 2 points").font(.caption).foregroundStyle(.secondary); Spacer() }
                } else { ProgressView("Loading shape…").frame(maxWidth: .infinity, minHeight: 200) }
                if let record = model.record {
                    if record.kind == .demo {
                        Label("Synthetic demo: this is a known 20 × 10 × 5 cm box.", systemImage: "info.circle")
                            .font(.footnote).foregroundStyle(Palette.mint)
                    }
                    scalePanel(record)
                    if let summary = model.summary { metricsPanel(summary, record) }
                    measurePanel(record)
                    if !record.measurements.isEmpty { savedPanel(record) }
                    Panel {
                        Text("Export GIS package").font(.headline)
                        Text("3D shape, 2D footprint, dimensions, saved measurements, OBJ and reports in one ZIP.").font(.subheadline).foregroundStyle(.secondary)
                        Button { model.rename(name); model.export() } label: {
                            if model.exporting { ProgressView().tint(Palette.background).frame(maxWidth: .infinity) }
                            else { Label("Export shapefiles", systemImage: "square.and.arrow.up") }
                        }.buttonStyle(FilledButton()).disabled(!record.canExport || model.mesh == nil || model.exporting)
                        if !record.canExport { Text("Set a known length, or confirm the LiDAR estimate, before exporting.").font(.caption).foregroundStyle(.orange) }
                        Text("Local coordinates in metres. These files are not georeferenced.").font(.footnote).foregroundStyle(.secondary)
                    }
                    if !record.warnings.isEmpty {
                        Panel { Text("Capture notes").font(.headline)
                            ForEach(Array(record.warnings.enumerated()), id: \.offset) { _, warning in Text(warning).font(.footnote).foregroundStyle(.secondary) }
                        }
                    }
                }
            }.padding(20)
        }.background(Palette.background).navigationTitle("Measured object").navigationBarTitleDisplayMode(.inline)
            .task { name = model.record?.name ?? ""; await model.load() }
            .onDisappear { model.rename(name) }
            .sheet(item: $model.share) { archive in ArchiveShareView(url: archive.url) }
            .alert("ShapeMeasure", isPresented: Binding(get: { model.errorMessage != nil }, set: { if !$0 { model.errorMessage = nil } })) {
                Button("OK", role: .cancel) { model.errorMessage = nil }
            } message: { Text(model.errorMessage ?? "") }
    }
    private func scalePanel(_ record: ScanRecord) -> some View {
        Panel {
            HStack { Text("Scale").font(.headline); Spacer()
                Image(systemName: record.canExport ? "checkmark.seal.fill" : "ruler").foregroundStyle(record.canExport ? Palette.mint : .orange) }
            Text(record.scaleSource.title).font(.subheadline).foregroundStyle(.secondary)
            if record.scaleSource == .lidar && !record.scaleConfirmed {
                Text("Compare a dimension with a ruler. Calibrate below to correct it, or keep the LiDAR estimate.").font(.footnote)
                Button("Keep LiDAR estimate") { model.confirmLiDAR() }.buttonStyle(.bordered)
            }
            if record.kind != .demo {
                Text("To calibrate: tap two points whose real distance you know, then enter that distance.").font(.footnote).foregroundStyle(.secondary)
                HStack {
                    TextField("Known distance", text: $model.knownLength).keyboardType(.decimalPad).textFieldStyle(.roundedBorder)
                    Picker("Reference units", selection: $model.calibrationUnit) {
                        ForEach(DisplayUnit.allCases) { Text($0.symbol).tag($0) }
                    }.pickerStyle(.menu)
                }
                Button("Apply known length") { model.calibrate() }.buttonStyle(.bordered).disabled(model.picks.count != 2)
            }
        }
    }
    private func metricsPanel(_ summary: MeasurementSummary, _ record: ScanRecord) -> some View {
        Panel {
            HStack { Text("Object measurements").font(.headline); Spacer()
                Picker("Display units", selection: $model.unit) { ForEach(DisplayUnit.allCases) { Text($0.symbol).tag($0) } }.pickerStyle(.menu) }
            let scale = record.scale
            HStack(alignment: .top, spacing: 8) {
                metric("Width", value: scale.map { model.unit.format(metres: summary.width * $0) } ?? "—")
                metric("Depth", value: scale.map { model.unit.format(metres: summary.depth * $0) } ?? "—")
                metric("Height", value: scale.map { model.unit.format(metres: summary.height * $0) } ?? "—")
            }
            if let scale {
                LabeledContent("Mesh surface area", value: String(format: "%.2f cm²", summary.surface * scale * scale * 10_000))
                LabeledContent("Convex footprint area", value: String(format: "%.2f cm²", summary.footprint * scale * scale * 10_000))
                LabeledContent("Closed-mesh volume", value: summary.volume.map { String(format: "%.2f cm³", $0 * pow(scale,3) * 1_000_000) } ?? "Unavailable")
            }
            Text("Dimensions follow the model axes. Footprint is the convex outline; surface area follows the reconstructed mesh.").font(.caption).foregroundStyle(.secondary)
            if summary.volume == nil {
                Text("Volume needs one closed mesh with consistent face directions. This scan has \(summary.boundaryEdges) boundary edges and \(summary.components) component(s).")
                    .font(.caption).foregroundStyle(.orange)
            }
            Text("\(summary.vertices.formatted()) vertices · \(summary.triangles.formatted()) triangles").font(.caption2).foregroundStyle(.secondary)
            if record.scaleSource == .lidar { Text("LiDAR dimensions are estimates. Decimal places do not indicate accuracy.").font(.caption).foregroundStyle(.secondary) }
        }
    }
    private func metric(_ title: String, value: String) -> some View {
        VStack(alignment: .leading, spacing: 5) { Text(title).font(.caption).foregroundStyle(.secondary)
            Text(value).font(.headline).minimumScaleFactor(0.7).lineLimit(1) }.frame(maxWidth: .infinity, alignment: .leading)
    }
    private func measurePanel(_ record: ScanRecord) -> some View {
        Panel {
            HStack { Text("Point-to-point distance").font(.headline); Spacer(); Button("Clear") { model.picks = [] }.font(.caption) }
            Text(model.selectedLength).font(.title3.weight(.semibold)).foregroundStyle(Palette.mint)
            Text("Each tap selects a point on the visible object surface. Rotate to reach another side.").font(.footnote).foregroundStyle(.secondary)
            TextField("Measurement label (optional)", text: $model.measurementLabel).textFieldStyle(.roundedBorder)
            Button("Save distance") { model.saveMeasurement() }.buttonStyle(.bordered)
                .disabled(model.picks.count != 2 || record.scale == nil)
        }
    }
    private func savedPanel(_ record: ScanRecord) -> some View {
        Panel {
            Text("Saved distances").font(.headline)
            ForEach(record.measurements) { measurement in
                HStack {
                    VStack(alignment: .leading, spacing: 3) { Text(measurement.label).font(.subheadline)
                        Text(model.unit.format(metres: measurement.a.distance(to: measurement.b) * (record.scale ?? 1)))
                            .font(.caption).foregroundStyle(Palette.mint) }
                    Spacer()
                    Button(role: .destructive) { model.deleteMeasurement(measurement.id) } label: { Image(systemName: "trash") }
                        .accessibilityLabel("Delete \(measurement.label)")
                }
            }
        }
    }
}
