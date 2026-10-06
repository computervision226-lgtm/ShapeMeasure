import Foundation
import AVFoundation
import CoreTransferable
import ImageIO
import UniformTypeIdentifiers

struct ImportedMovie: Transferable {
    var url: URL
    static var transferRepresentation: some TransferRepresentation {
        FileRepresentation(importedContentType: .movie) { incoming in
            let directory = FileManager.default.temporaryDirectory.appendingPathComponent("ImportedMovies", isDirectory: true)
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            let suffix = incoming.file.pathExtension.isEmpty ? "mov" : incoming.file.pathExtension
            let destination = directory.appendingPathComponent(UUID().uuidString + "." + suffix)
            try FileManager.default.copyItem(at: incoming.file, to: destination)
            return Self(url: destination)
        }
    }
}

enum MediaImporter {
    static func saveJPEG(_ data: Data, to url: URL) throws {
        guard let source = CGImageSourceCreateWithData(data as CFData, nil),
              let image = CGImageSourceCreateThumbnailAtIndex(source, 0, [
                kCGImageSourceCreateThumbnailFromImageAlways: true,
                kCGImageSourceCreateThumbnailWithTransform: true,
                kCGImageSourceThumbnailMaxPixelSize: 2400
              ] as CFDictionary) else { throw ScanFailure.message("One of the selected photos could not be decoded.") }
        try saveJPEG(image, to: url)
    }
    static func saveJPEG(_ image: CGImage, to url: URL) throws {
        guard let output = CGImageDestinationCreateWithURL(url as CFURL, UTType.jpeg.identifier as CFString, 1, nil) else {
            throw ScanFailure.message("Cannot write the capture image.")
        }
        CGImageDestinationAddImage(output, image, [kCGImageDestinationLossyCompressionQuality: 0.95] as CFDictionary)
        guard CGImageDestinationFinalize(output) else { throw ScanFailure.message("Not enough storage to save images.") }
    }
    static func extractVideo(_ url: URL, into directory: URL,
                             progress: @escaping @Sendable (Double) async -> Void) async throws -> Int {
        let asset = AVURLAsset(url: url)
        let duration = try await asset.load(.duration).seconds
        guard duration.isFinite, duration >= 10, duration <= 120 else {
            throw ScanFailure.message("Choose a 10–120 second video that circles a stationary object.")
        }
        let frames = min(100, max(20, Int(duration * 2)))
        let generator = AVAssetImageGenerator(asset: asset)
        generator.appliesPreferredTrackTransform = true
        generator.maximumSize = CGSize(width: 2400, height: 2400)
        generator.requestedTimeToleranceBefore = CMTime(seconds: 0.03, preferredTimescale: 600)
        generator.requestedTimeToleranceAfter = CMTime(seconds: 0.03, preferredTimescale: 600)
        for index in 0..<frames {
            try Task.checkCancellation()
            let seconds = (duration - 0.25) * Double(index) / Double(frames - 1)
            let result = try await generator.image(at: CMTime(seconds: seconds, preferredTimescale: 600))
            let output = directory.appendingPathComponent(String(format: "frame_%04d.jpg", index))
            try saveJPEG(result.image, to: output)
            await progress(Double(index + 1) / Double(frames))
        }
        return frames
    }
}
