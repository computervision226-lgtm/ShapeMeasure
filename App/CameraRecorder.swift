import SwiftUI
import AVFoundation
import UniformTypeIdentifiers
import UIKit

struct CameraRecorder: UIViewControllerRepresentable {
    var onVideo: (URL) -> Void
    var onError: (String) -> Void
    @Environment(\.dismiss) private var dismiss
    func makeCoordinator() -> Coordinator { Coordinator(parent: self) }
    func makeUIViewController(context: Context) -> UIImagePickerController {
        let camera = UIImagePickerController()
        camera.delegate = context.coordinator
        camera.sourceType = .camera
        camera.mediaTypes = [UTType.movie.identifier]
        camera.cameraDevice = .rear
        camera.cameraCaptureMode = .video
        camera.videoQuality = .typeHigh
        camera.videoMaximumDuration = 90
        return camera
    }
    func updateUIViewController(_ controller: UIImagePickerController, context: Context) {}
    final class Coordinator: NSObject, UINavigationControllerDelegate, UIImagePickerControllerDelegate {
        var parent: CameraRecorder
        init(parent: CameraRecorder) { self.parent = parent }
        func imagePickerController(_ picker: UIImagePickerController, didFinishPickingMediaWithInfo info: [UIImagePickerController.InfoKey: Any]) {
            guard let url = info[.mediaURL] as? URL else { parent.dismiss(); return }
            // Copy out of the camera controller's temporary ownership before dismissal.
            let destination = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".mov")
            do {
                try FileManager.default.copyItem(at: url, to: destination)
                parent.dismiss()
                let action = parent.onVideo
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { action(destination) }
            } catch { parent.dismiss(); parent.onError(error.localizedDescription) }
        }
        func imagePickerControllerDidCancel(_ picker: UIImagePickerController) { parent.dismiss() }
    }
}

struct ArchiveShareView: UIViewControllerRepresentable {
    var url: URL
    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: [url], applicationActivities: nil)
    }
    func updateUIViewController(_ controller: UIActivityViewController, context: Context) {}
}
