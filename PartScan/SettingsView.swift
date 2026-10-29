import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var settings: ServerSettings
    @State private var showScanner = false
    @State private var testing = false
    @State private var testResult: String?
    @State private var testOK = false

    var body: some View {
        Form {
            Section {
                Button {
                    showScanner = true
                } label: {
                    Label("Scan the QR code from the notebook", systemImage: "qrcode.viewfinder").bold()
                }
                TextField("https://xxxx.gradio.live  or  https://name-partscan.hf.space", text: $settings.baseURL)
                    .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                TextField("Access key (from the notebook / Space secret)", text: $settings.accessKey)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
            } header: {
                Text("Server")
            } footer: {
                Text("The 3D reconstruction runs on a GPU server: the PartScan Colab notebook (set PUBLIC_LINK = True; it shows a QR code with the link and key) or a Hugging Face Space built from the Server folder of the repository.")
            }

            Section {
                SecureField("Hugging Face token (only for a private Space)", text: $settings.hfToken)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
            } footer: {
                Text("Leave empty for a Colab link or a public Space.")
            }

            Section {
                Button {
                    test()
                } label: {
                    HStack {
                        Label(testing ? "Testing…" : "Test connection", systemImage: "antenna.radiowaves.left.and.right")
                        Spacer()
                        if testing { ProgressView() }
                    }
                }
                .disabled(testing || !settings.isConfigured)
                if let testResult {
                    Label(testResult, systemImage: testOK ? "checkmark.circle.fill" : "xmark.octagon.fill")
                        .foregroundStyle(testOK ? .green : .red).font(.footnote)
                }
            }

            Section {
                Picker("Recording rate", selection: $settings.frameStride) {
                    Text("60 fps (biggest files)").tag(1)
                    Text("30 fps (recommended)").tag(2)
                    Text("20 fps").tag(3)
                    Text("15 fps (smallest)").tag(4)
                }
            } header: {
                Text("Capture")
            } footer: {
                Text("The server uses about 90 sharp frames spread over the whole capture; 30 fps for 60 s is plenty and keeps a scan around 200–400 MB.")
            }

            Section("About") {
                Text("PartScan records LiDAR + video in the Stray Scanner layout and sends it to the PartScan server, which builds a true-scale, measured 3D model (board scale, photogrammetry, Prompt Depth Anything, SAM 2.1, TSDF fusion). Always check one dimension with a caliper.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
        }
        .navigationTitle("Server")
        .sheet(isPresented: $showScanner) {
            QRScannerSheet { text in
                if settings.apply(scanned: text) {
                    testResult = nil
                    test()
                } else {
                    testResult = "That QR code is not a PartScan link."
                    testOK = false
                }
            }
        }
    }

    private func test() {
        testing = true
        testResult = nil
        Task {
            defer { testing = false }
            do {
                let p = try await APIClient(settings: settings).ping()
                let dev = p.device ?? "?"
                let name = p.app ?? "PartScan"
                let version = p.version ?? ""
                var text = "Connected: \(name) \(version) on \(dev)"
                if dev == "cpu" {
                    text += " — no GPU! choose a GPU runtime / hardware"
                }
                let needsKey = p.auth ?? false
                if needsKey && settings.accessKey.isEmpty {
                    text += " — this server needs an access key"
                }
                testOK = p.ok
                testResult = text
            } catch {
                testOK = false
                testResult = error.localizedDescription
            }
        }
    }
}
