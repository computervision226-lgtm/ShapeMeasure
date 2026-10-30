import Combine
import Foundation

/// Local scans: one folder each under Documents/Scans (visible in the Files app as "PartScan"), plus a
/// small index with upload status and results summary.
final class ScanStore: ObservableObject {
    @Published private(set) var scans: [ScanRecord] = []

    static var scansDirectory: URL {
        let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        return docs.appendingPathComponent("Scans", isDirectory: true)
    }

    private var indexURL: URL { Self.scansDirectory.appendingPathComponent("index.json") }

    init() {
        try? FileManager.default.createDirectory(at: Self.scansDirectory, withIntermediateDirectories: true)
        load()
    }

    func folder(for scan: ScanRecord) -> URL {
        Self.scansDirectory.appendingPathComponent(scan.id, isDirectory: true)
    }

    func add(_ scan: ScanRecord) {
        scans.removeAll { $0.id == scan.id }
        scans.insert(scan, at: 0)
        save()
    }

    func update(_ scan: ScanRecord) {
        if let i = scans.firstIndex(where: { $0.id == scan.id }) {
            scans[i] = scan
        } else {
            scans.insert(scan, at: 0)
        }
        save()
    }

    func delete(_ scan: ScanRecord) {
        try? FileManager.default.removeItem(at: folder(for: scan))
        try? FileManager.default.removeItem(at: zipURL(for: scan))
        scans.removeAll { $0.id == scan.id }
        save()
    }

    func zipURL(for scan: ScanRecord) -> URL {
        FileManager.default.temporaryDirectory.appendingPathComponent("\(scan.id).zip")
    }

    /// Zips the scan folder (NSFileCoordinator does this without any third-party code).
    func zip(_ scan: ScanRecord) throws -> URL {
        let source = folder(for: scan)
        let destination = zipURL(for: scan)
        let fm = FileManager.default
        try? fm.removeItem(at: destination)
        var coordinatorError: NSError?
        var copyError: Error?
        NSFileCoordinator().coordinate(readingItemAt: source, options: [.forUploading], error: &coordinatorError) { zipped in
            do {
                try fm.copyItem(at: zipped, to: destination)
            } catch {
                copyError = error
            }
        }
        if let coordinatorError { throw coordinatorError }
        if let copyError { throw copyError }
        return destination
    }

    static func folderSize(_ url: URL) -> Int64 {
        let fm = FileManager.default
        guard let e = fm.enumerator(at: url, includingPropertiesForKeys: [.fileSizeKey]) else { return 0 }
        var total: Int64 = 0
        for case let file as URL in e {
            if let size = try? file.resourceValues(forKeys: [.fileSizeKey]).fileSize {
                total += Int64(size)
            }
        }
        return total
    }

    // MARK: persistence

    private func load() {
        guard let data = try? Data(contentsOf: indexURL),
              let list = try? JSONDecoder().decode([ScanRecord].self, from: data) else { return }
        let fm = FileManager.default
        scans = list.filter { fm.fileExists(atPath: Self.scansDirectory.appendingPathComponent($0.id).path) }
    }

    private func save() {
        if let data = try? JSONEncoder().encode(scans) {
            try? data.write(to: indexURL, options: .atomic)
        }
    }
}
