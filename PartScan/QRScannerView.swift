import AVFoundation
import SwiftUI
import UIKit
import Vision
import VisionKit

/// Scans the QR code the notebook prints (server link + access key) with VisionKit's data scanner.
struct QRScannerView: UIViewControllerRepresentable {
    let onFound: (String) -> Void

    static var isSupported: Bool {
        DataScannerViewController.isSupported && DataScannerViewController.isAvailable
    }

    func makeUIViewController(context: Context) -> ScannerHostController {
        ScannerHostController(onFound: onFound)
    }

    func updateUIViewController(_ uiViewController: ScannerHostController, context: Context) {}
}

final class ScannerHostController: UIViewController, DataScannerViewControllerDelegate {
    private let onFound: (String) -> Void
    private var scanner: DataScannerViewController?
    private var finished = false

    init(onFound: @escaping (String) -> Void) {
        self.onFound = onFound
        super.init(nibName: nil, bundle: nil)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .black
        let scanner = DataScannerViewController(recognizedDataTypes: [.barcode(symbologies: [.qr])],
                                                qualityLevel: .balanced, recognizesMultipleItems: false,
                                                isHighFrameRateTrackingEnabled: false, isHighlightingEnabled: true)
        scanner.delegate = self
        addChild(scanner)
        scanner.view.frame = view.bounds
        scanner.view.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        view.addSubview(scanner.view)
        scanner.didMove(toParent: self)
        self.scanner = scanner
    }

    override func viewDidAppear(_ animated: Bool) {
        super.viewDidAppear(animated)
        try? scanner?.startScanning()
    }

    override func viewWillDisappear(_ animated: Bool) {
        super.viewWillDisappear(animated)
        scanner?.stopScanning()
    }

    func dataScanner(_ dataScanner: DataScannerViewController, didAdd addedItems: [RecognizedItem],
                     allItems: [RecognizedItem]) {
        guard !finished else { return }
        for item in addedItems {
            if case .barcode(let barcode) = item, let text = barcode.payloadStringValue, !text.isEmpty {
                finished = true
                dataScanner.stopScanning()
                onFound(text)
                return
            }
        }
    }
}

struct QRScannerSheet: View {
    let onFound: (String) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var permission: Bool? = nil

    var body: some View {
        NavigationStack {
            Group {
                if permission == false {
                    ContentUnavailableView("Camera access needed", systemImage: "camera.fill",
                                           description: Text("Allow the camera in Settings → PartScan."))
                } else if !QRScannerView.isSupported {
                    ContentUnavailableView("QR scanning unavailable", systemImage: "qrcode",
                                           description: Text("Type the server link and key by hand instead."))
                } else if permission == true {
                    QRScannerView { text in
                        onFound(text)
                        dismiss()
                    }
                    .ignoresSafeArea(edges: .bottom)
                } else {
                    ProgressView()
                }
            }
            .navigationTitle("Scan the notebook's QR code")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) { Button("Cancel") { dismiss() } }
            }
            .task {
                permission = await AVCaptureDevice.requestAccess(for: .video)
            }
        }
    }
}
