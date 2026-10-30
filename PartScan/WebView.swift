import SwiftUI
import UIKit
import WebKit

/// Shows a server page (the interactive 3D viewer or the HTML report) with the access key attached.
struct WebView: UIViewRepresentable {
    let request: URLRequest

    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.allowsInlineMediaPlayback = true
        let view = WKWebView(frame: .zero, configuration: config)
        view.isOpaque = false
        view.load(request)
        return view
    }

    func updateUIView(_ uiView: WKWebView, context: Context) {}
}

struct WebSheet: View {
    let title: String
    let request: URLRequest
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            WebView(request: request)
                .ignoresSafeArea(edges: .bottom)
                .navigationTitle(title)
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .topBarTrailing) {
                        Button("Done") { dismiss() }
                    }
                }
        }
    }
}

/// UIActivityViewController (share sheet) for files.
struct ActivityView: UIViewControllerRepresentable {
    let items: [Any]

    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: items, applicationActivities: nil)
    }

    func updateUIViewController(_ uiViewController: UIActivityViewController, context: Context) {}
}
